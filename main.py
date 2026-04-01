import io
import os
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv
from database import (
    init_db, run_startup_migrations, get_or_create_user, save_message_record,
    save_session_turn, get_session_messages, admin_reset_user,
)
from deepseek import call_deepseek, get_user_local_hour, get_time_of_day_label
from protocol1 import check_protocol1
from protocol3 import check_protocol3
from protocol4 import check_protocol4
from onboarding import (
    get_intro_message,
    get_opening_detection_question,
    get_resume_prompt,
    handle_onboarding_answer,
    handle_mode_detection,
    get_handoff_message,
    get_setup_child_name,
    is_confused_senior,
    get_confusion_response,
    detect_archetype_signal,
    get_archetype_adjustment_text,
)
from memory import extract_and_save_memories
from whisper import transcribe_voice
from tts import text_to_speech
from youtube import detect_music_request, find_music, build_music_message
from reminders import (
    check_and_send_reminders,
    is_acknowledgement,
    mark_reminder_acknowledged,
)
from rituals import check_and_send_rituals, record_first_message
from safety import (
    check_emergency_keywords,
    send_help_prompt,
    handle_help_command,
    handle_help_callback,
    check_inactivity,
)
from end_of_life import (
    find_senior_for_family_member,
    is_death_notification,
    handle_death_notification,
    is_eulogy_yes,
    build_eulogy_prompt,
)
from family import (
    get_or_create_linking_code,
    join_by_code,
    relay_message_to_senior,
    build_relay_confirmation,
    check_and_send_weekly_report,
)
from policy import POLICY_COMMAND_RESPONSE, USER_POLICY_DOCUMENT

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", 8443))

# Tracks how many times Protocol 1 has fired per user in this process lifetime.
# Resets on bot restart — sufficient for MVP.
_protocol1_session_counts: dict[int, int] = {}

# Archetype onboarding adjustments — First 7 Days only.
# In-memory: 'striver', 'quiet_one', or 'default'. No DB storage.
# Resets on bot restart — recalculates from messages if needed.
_archetype_cache: dict[int, str] = {}


def _get_archetype_adjustment(user_id: int, days_since_first_message: int) -> str | None:
    """
    Return archetype adjustment text for user_context, or None.

    - Only active during First 7 Days (days_since_first_message <= 7)
    - Calculates once after 3+ inbound messages, then caches in memory
    - Returns None for 'default' or after Day 7
    """
    if days_since_first_message > 7:
        return None

    if user_id in _archetype_cache:
        return get_archetype_adjustment_text(_archetype_cache[user_id])

    # Not yet detected — check how many messages the senior has sent
    try:
        from database import get_connection
        with get_connection() as conn:
            rows = conn.execute(
                """SELECT content FROM messages
                   WHERE user_id = ? AND direction = 'in'
                   ORDER BY created_at
                   LIMIT 5""",
                (user_id,),
            ).fetchall()
        if len(rows) >= 3:
            messages = [r["content"] for r in rows if r["content"]]
            label = detect_archetype_signal(messages)
            _archetype_cache[user_id] = label
            logger.info("ARCHETYPE | user_id=%s | detected=%s", user_id, label)
            return get_archetype_adjustment_text(label)
    except Exception as e:
        logger.warning("ARCHETYPE | lookup failed | user_id=%s | %s", user_id, e)

    return None


# ---------------------------------------------------------------------------
# Shared message pipeline — Protocol 1 → Protocol 3 → DeepSeek
# Called by both handle_text and receive_voice after text is available.
# ---------------------------------------------------------------------------

async def _run_pipeline(
    user_id: int,
    text: str,
    user_row,
    update: Update,
    input_type: str = "text",
    context: ContextTypes.DEFAULT_TYPE = None,
) -> None:
    """
    Run the full message pipeline for a single user turn.

    input_type is "text" or "voice" — used for message logging only.
    """
    save_message_record(user_id, "in", text, message_type=input_type)
    # Retrieve live session history AFTER saving the inbound message.
    # Passed to DeepSeek so it has full in-session conversation context.
    _session_history = get_session_messages(user_id)

    # --- End-of-life: death notification from a registered family member ---
    # Only check messages from registered family contacts — prevents abuse.
    # This runs before everything else so it can silence the normal pipeline.
    senior_id_for_family = find_senior_for_family_member(user_id)
    if senior_id_for_family is not None:
        from database import get_or_create_user as _get_senior
        senior_row = _get_senior(senior_id_for_family)
        senior_status = senior_row["account_status"] if senior_row else "active"

        if senior_status == "active" and is_death_notification(text):
            # Mark senior deceased, silence all proactive messages
            eulogy_offer = handle_death_notification(senior_id_for_family, user_id)
            if eulogy_offer:
                await update.message.reply_text(eulogy_offer)
                logger.info(
                    "EOL | death notification received | senior_id=%s | notifier=%s",
                    senior_id_for_family, user_id,
                )
            return

        if senior_status == "deceased":
            eulogy_delivered = senior_row["eulogy_delivered"] if senior_row else 1
            if not eulogy_delivered and is_eulogy_yes(text):
                # Family said yes to eulogy — generate and send
                try:
                    prompt = build_eulogy_prompt(senior_id_for_family)
                    if prompt:
                        eulogy_text = call_deepseek(prompt, {"language": "english"})
                        await update.message.reply_text(eulogy_text)
                        from database import update_user_fields
                        update_user_fields(senior_id_for_family, eulogy_delivered=1)
                        logger.info(
                            "EOL | eulogy delivered | senior_id=%s", senior_id_for_family
                        )
                except Exception as eol_err:
                    logger.error("EOL | eulogy generation failed: %s", eol_err)
                    await update.message.reply_text(
                        "I am so sorry — something went wrong and I wasn't able to send this right now. "
                        "Please try again in a little while."
                    )
            return  # Family messages beyond this point are not processed normally

        # --- Family bridge relay (active senior, registered family member) ---
        # Relay the message warmly to the senior.  One-way: family → senior.
        if senior_status == "active":
            sent = await relay_message_to_senior(
                senior_id_for_family, user_id, text, context.bot,
            )
            if sent:
                senior_name = senior_row["name"] if senior_row else "your family member"
                senior_lang = senior_row["language"] if senior_row else "hindi"
                confirm = build_relay_confirmation(senior_name, senior_lang)
                await update.message.reply_text(confirm, parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    "Something went wrong delivering your message. Please try again. 🙏"
                )
            return

    # --- Full policy request ---
    if text.strip().lower() in ("full policy", "full policy.", "puri policy"):
        await update.message.reply_text(USER_POLICY_DOCUMENT)
        logger.info("OUT | user_id=%s | type=policy_full", user_id)
        return

    # Track first message of the day for adaptive ritual scheduling
    record_first_message(user_id)

    # --- Medicine reminder acknowledgement ---
    # Checked before anything else so 👍 is never routed to DeepSeek.
    # mark_reminder_acknowledged only matches if there is a reminder sent
    # in the last 2 hours that is still unacknowledged.
    if user_row["onboarding_complete"] and is_acknowledgement(text):
        if mark_reminder_acknowledged(user_id):
            ack_reply = (
                "Shukriya! Dawai le li — bahut achha kiya. "
                "Apna khayal rakhein. 🙏"
            )
            await update.message.reply_text(ack_reply)
            logger.info("OUT | user_id=%s | type=reminder_ack", user_id)
            return

    # --- Onboarding gate ---
    if not user_row["onboarding_complete"]:
        setup_mode = user_row["setup_mode"] if "setup_mode" in user_row.keys() else None

        if setup_mode is None:
            # First contact — we haven't asked the opening question yet.
            # Send it now and set state to 'pending' so the NEXT message is parsed.
            # Never try to detect mode from an unsolicited first message.
            from database import update_user_fields as _uuf
            _uuf(user_id, setup_mode="pending")
            await update.message.reply_text(
                get_opening_detection_question(), parse_mode="Markdown"
            )
            logger.info("OUT | user_id=%s | type=opening_detection_question", user_id)
            return

        if setup_mode == "pending":
            # User is replying to the opening detection question — parse their answer.
            mode, next_msg = handle_mode_detection(user_id, text)
            await update.message.reply_text(next_msg, parse_mode="Markdown")
            logger.info("OUT | user_id=%s | type=mode_detection | mode=%s", user_id, mode)
            return

        reply = handle_onboarding_answer(user_id, user_row["onboarding_step"], text)
        await update.message.reply_text(reply, parse_mode="Markdown")
        logger.info(
            "OUT | user_id=%s | type=onboarding | step=%d",
            user_id, user_row["onboarding_step"],
        )
        return

    # --- Staged handoff (child-led mode only, handoff_step 0–3) ---
    setup_mode = user_row["setup_mode"] if "setup_mode" in user_row.keys() else None
    handoff_step = user_row["handoff_step"] if "handoff_step" in user_row.keys() else 4

    if setup_mode == "family" and handoff_step is not None and handoff_step < 4:
        child_name = get_setup_child_name(user_id)
        replies = []

        if handoff_step == 0:
            # Senior's very first message — confusion check first
            if is_confused_senior(text):
                confusion_msg = get_confusion_response(child_name)
                replies.append(confusion_msg)
                logger.info("OUT | user_id=%s | type=confusion_branch", user_id)

            msg1 = get_handoff_message(0, child_name)
            replies.append(msg1)
            from database import update_user_fields
            update_user_fields(user_id, handoff_step=1)
            logger.info("OUT | user_id=%s | type=handoff | step=0", user_id)

        elif handoff_step == 1:
            # Senior responded — ask their preferred name
            msg2 = get_handoff_message(1, child_name)
            replies.append(msg2)
            from database import update_user_fields
            update_user_fields(user_id, handoff_step=2)
            logger.info("OUT | user_id=%s | type=handoff | step=1", user_id)

        elif handoff_step == 2:
            # Senior gave their name — save it, ask what to call the bot
            name = text.strip().title()
            if name and len(name) < 50:
                from database import update_user_fields
                update_user_fields(user_id, name=name, handoff_step=3)
            else:
                from database import update_user_fields
                update_user_fields(user_id, handoff_step=3)
            msg3 = get_handoff_message(2, child_name)
            replies.append(msg3)
            logger.info("OUT | user_id=%s | type=handoff | step=2 | name=%s", user_id, name)

        elif handoff_step == 3:
            # Senior gave bot name — save it, send final welcome message
            bot_name = text.strip().title()
            if bot_name and len(bot_name) < 50 and bot_name.lower() not in ("no", "nahi"):
                from database import update_user_fields
                update_user_fields(user_id, bot_name=bot_name, handoff_step=4)
            else:
                from database import update_user_fields
                update_user_fields(user_id, handoff_step=4)
            msg4 = get_handoff_message(3, child_name)
            replies.append(msg4)
            logger.info("OUT | user_id=%s | type=handoff | step=3 | bot_name=%s", user_id, bot_name)

        for r in replies:
            await update.message.reply_text(r)
            save_message_record(user_id, "out", r)
        return

    # Build context dict from user profile.
    days_since_first = user_row["days_since_first_message"] or 1
    archetype_adjustment = _get_archetype_adjustment(user_id, days_since_first)

    _local_hour = get_user_local_hour(dict(user_row))
    _time_label = get_time_of_day_label(_local_hour)

    user_context = {
        "user_id":                user_id,
        "name":                   user_row["name"],
        "bot_name":               user_row["bot_name"],
        "persona":                user_row["persona"],
        "language":               user_row["language"],
        "city":                   user_row["city"],
        "spouse_name":            user_row["spouse_name"],
        "religion":               user_row["religion"],
        "health_sensitivities":   user_row["health_sensitivities"],
        "music_preferences":      user_row["music_preferences"],
        "favourite_topics":       user_row["favourite_topics"],
        "family_members":         None,  # TODO Module 7: inject from family_members table
        "archetype_adjustment":   archetype_adjustment,
        "local_hour":             _local_hour,
        "local_time_label":       _time_label,
    }

    # --- Meta-request: language switch ---
    # Must run before all protocols and DeepSeek.
    _LANGUAGE_SWITCH_TO_ENGLISH = [
        "in english", "in english please",
        "speak english", "english please",
        "reply in english", "respond in english",
    ]
    _LANGUAGE_SWITCH_TO_HINDI = [
        "hindi mein", "hindi mein baat karo",
        "hindi please", "hindi mein boliye",
        "in hindi", "in hindi please",
    ]

    msg_lower = text.lower().strip()

    if any(p in msg_lower for p in _LANGUAGE_SWITCH_TO_ENGLISH):
        from database import update_user_fields as _uuf_lang
        _uuf_lang(user_id, language="english")
        _lang_reply = "Of course."
        await update.message.reply_text(_lang_reply)
        save_message_record(user_id, "out", _lang_reply)
        save_session_turn(user_id, "user", text)
        save_session_turn(user_id, "assistant", _lang_reply)
        logger.info("OUT | user_id=%s | type=language_switch | lang=english", user_id)
        return

    if any(p in msg_lower for p in _LANGUAGE_SWITCH_TO_HINDI):
        from database import update_user_fields as _uuf_lang
        _uuf_lang(user_id, language="hindi")
        _lang_reply = "Bilkul."
        await update.message.reply_text(_lang_reply)
        save_message_record(user_id, "out", _lang_reply)
        save_session_turn(user_id, "user", text)
        save_session_turn(user_id, "assistant", _lang_reply)
        logger.info("OUT | user_id=%s | type=language_switch | lang=hindi", user_id)
        return

    # --- Greeting handler ---
    # User-initiated greetings get a time-aware response, not proactive check-in language.
    _GREETING_TRIGGERS = [
        "hello", "hi", "hey", "good morning", "good afternoon",
        "good evening", "good night", "namaste", "namaskar",
        "haan", "haan haan", "jai shri krishna", "sat sri akal",
        "salam", "adaab", "hola",
    ]

    def _get_time_aware_greeting(hour: int) -> str:
        if 5 <= hour < 12:
            return "Good morning. Good to hear from you."
        elif 12 <= hour < 17:
            return "Good afternoon."
        elif 17 <= hour < 21:
            return "Good evening. How has the day been?"
        else:
            return "Hello. Up late tonight?"

    if msg_lower in _GREETING_TRIGGERS or any(msg_lower.startswith(g) for g in _GREETING_TRIGGERS):
        _greet_reply = _get_time_aware_greeting(_local_hour)
        await update.message.reply_text(_greet_reply)
        save_message_record(user_id, "out", _greet_reply)
        save_session_turn(user_id, "user", text)
        save_session_turn(user_id, "assistant", _greet_reply)
        logger.info("OUT | user_id=%s | type=greeting | hour=%d", user_id, _local_hour)
        return

    # --- Emergency keyword check (runs BEFORE Protocol 1) ---
    # Detects physical safety signals ("I fell", "bachao", etc.) and presents
    # the /help inline keyboard. Mental health crisis is handled by Protocol 1.
    if check_emergency_keywords(text):
        await send_help_prompt(update)
        logger.info("OUT | user_id=%s | type=emergency_prompt", user_id)
        return

    # --- Protocol 1 check (runs BEFORE DeepSeek) ---
    session_count = _protocol1_session_counts.get(user_id, 0)
    protocol1_reply = check_protocol1(user_id, text, session_count)
    if protocol1_reply:
        _protocol1_session_counts[user_id] = session_count + 1
        await update.message.reply_text(protocol1_reply)
        logger.info(
            "OUT | user_id=%s | type=protocol1 | stage=%d",
            user_id, session_count + 1,
        )
        return

    # --- Protocol 4 check (runs AFTER Protocol 1, BEFORE Protocol 3) ---
    # Handles romantic or sexual signals with a gentle, non-shaming boundary.
    _p4_language = user_row["language"] or "english"
    protocol4_reply = check_protocol4(user_id, text, language=_p4_language)
    if protocol4_reply:
        await update.message.reply_text(protocol4_reply)
        save_message_record(user_id, "out", protocol4_reply)
        save_session_turn(user_id, "user", text)
        save_session_turn(user_id, "assistant", protocol4_reply)
        logger.info("OUT | user_id=%s | type=protocol4", user_id)
        return

    # --- Protocol 3 check (runs BEFORE DeepSeek, AFTER Protocol 1) ---
    #
    # Session expiry: clear protocol3_active if >60 min since last P3 trigger.
    # This resets the guard at the start of a new conversation session.
    _p3_active = user_row["protocol3_active"] if "protocol3_active" in user_row.keys() else 0
    _p3_triggered_at = user_row["protocol3_triggered_at"] if "protocol3_triggered_at" in user_row.keys() else None
    if _p3_active and _p3_triggered_at:
        try:
            from datetime import datetime, timezone, timedelta
            triggered = datetime.fromisoformat(_p3_triggered_at)
            if triggered.tzinfo is None:
                triggered = triggered.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - triggered > timedelta(minutes=60):
                from database import update_user_fields as _uuf_p3
                _uuf_p3(user_id, protocol3_active=0, protocol3_triggered_at=None)
                _p3_active = 0
        except Exception:
            pass  # on parse error, leave flag as-is

    # Inject P3 state into user_context so DeepSeek knows a financial topic
    # was raised and must not give financial advice on follow-up messages.
    user_context["protocol3_active"] = _p3_active

    user_language = user_row["language"] or "english"

    if not _p3_active:
        # Only run keyword detection when P3 hasn't already fired this session
        protocol3_reply = check_protocol3(user_id, text, language=user_language)
        if protocol3_reply:
            await update.message.reply_text(protocol3_reply)
            # Save both sides so DeepSeek has full context on the next message
            save_message_record(user_id, "out", protocol3_reply)
            save_session_turn(user_id, "user", text)
            save_session_turn(user_id, "assistant", protocol3_reply)
            # Mark P3 active — prevents re-fire loop on follow-up messages
            from database import update_user_fields as _uuf_p3
            from datetime import datetime, timezone
            _uuf_p3(
                user_id,
                protocol3_active=1,
                protocol3_triggered_at=datetime.now(timezone.utc).isoformat(),
            )
            logger.info("OUT | user_id=%s | type=protocol3", user_id)
            return
        # If no P3 trigger, fall through to DeepSeek normally

    # --- Music request check (runs BEFORE DeepSeek, AFTER protocols) ---
    music_query = detect_music_request(
        text, music_preferences=user_context.get("music_preferences") or ""
    )
    if music_query:
        try:
            title, url = find_music(music_query)
            reply = build_music_message(title, url)
            await update.message.reply_text(reply, parse_mode="Markdown")
            save_message_record(user_id, "out", reply)
            logger.info("OUT | user_id=%s | type=music | query=%r", user_id, music_query)
        except Exception as yt_err:
            logger.warning("YOUTUBE | user_id=%s | failed: %s", user_id, yt_err)
            await update.message.reply_text(
                "Koshish ki lekin abhi koi gaana nahi mil raha. "
                "Thodi der mein dobara try karein! 🙏"
            )
        return

    # --- DeepSeek ---
    # Pass _session_history so DeepSeek has full in-session conversation context.
    reply = call_deepseek(text, user_context, session_messages=_session_history)

    # Send text first — user gets the response immediately regardless of TTS
    await update.message.reply_text(reply)
    save_message_record(user_id, "out", reply)
    # Save this exchange to session buffer for the next DeepSeek call
    save_session_turn(user_id, "user", text)
    save_session_turn(user_id, "assistant", reply)
    logger.info("OUT | user_id=%s | type=%s | content=%s", user_id, input_type, reply[:80])

    # Send voice note — if TTS fails, text is already delivered so we never lose the response
    user_language = user_row["language"] or "english"
    try:
        audio_bytes = text_to_speech(reply, user_language=user_language)
        await update.message.reply_voice(voice=io.BytesIO(audio_bytes))
        logger.info("TTS | user_id=%s | voice note sent", user_id)
    except Exception as tts_err:
        logger.warning("TTS | user_id=%s | failed, text-only: %s", user_id, tts_err)

    # Extract and save memories (runs after reply is sent to user)
    extract_and_save_memories(user_id, text, reply)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_policy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/policy — sends the short privacy summary. Senior can request full policy by replying."""
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/policy", user_id)
    await update.message.reply_text(POLICY_COMMAND_RESPONSE, parse_mode="Markdown")
    logger.info("OUT | user_id=%s | type=policy_short", user_id)


async def handle_full_policy_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    If the senior replies 'full policy' after the /policy short response,
    send the complete policy document.
    Called from handle_text when the message is exactly 'full policy'.
    """
    user_id = update.effective_user.id
    await update.message.reply_text(USER_POLICY_DOCUMENT)
    logger.info("OUT | user_id=%s | type=policy_full", user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/start", user_id)
    try:
        user_row = get_or_create_user(user_id)

        if not user_row["onboarding_complete"]:
            setup_mode = user_row["setup_mode"] if "setup_mode" in user_row.keys() else None
            step = user_row["onboarding_step"]

            if setup_mode is None or setup_mode == "pending":
                # Ask the opening detection question and mark it as pending.
                # 'pending' = we've asked, waiting for the answer.
                from database import update_user_fields as _uuf
                _uuf(user_id, setup_mode="pending")
                reply = get_opening_detection_question()
            elif setup_mode == "family" and step == 0:
                reply = get_intro_message()
            else:
                reply = get_resume_prompt(user_id, step, setup_mode=setup_mode)
        else:
            if (user_row["language"] or "").lower() == "english":
                reply = "Hello. Good to hear from you."
            else:
                reply = "Namaste! Main yahan hoon. 🙏"

        await update.message.reply_text(reply, parse_mode="Markdown")
        logger.info("OUT | user_id=%s | type=text | content=%s", user_id, reply[:80])
    except Exception as e:
        logger.error("ERR | user_id=%s | error=%s", user_id, e)
        raise


async def handle_familycode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/familycode — senior requests a linking code to share with family."""
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/familycode", user_id)
    try:
        user_row = get_or_create_user(user_id)
        if not user_row["onboarding_complete"]:
            await update.message.reply_text(
                "Please complete setup first — then you can share a family code. 🙏"
            )
            return

        code = get_or_create_linking_code(user_id)
        senior_name = user_row["name"] or "aap"
        reply = (
            f"Your family code is:  *{code}*\n\n"
            f"Share this with your family member. They should message this bot "
            f"with:\n/join {code}\n\n"
            f"Once they join, they can send you messages through me, and they'll "
            f"receive a brief weekly update on how you're doing. 🙏"
        )
        await update.message.reply_text(reply, parse_mode="Markdown")
        logger.info("OUT | user_id=%s | type=familycode | code=%s", user_id, code)
    except Exception as e:
        logger.error("ERR | user_id=%s | /familycode error: %s", user_id, e)
        await update.message.reply_text("Something went wrong. Please try again. 🙏")


async def handle_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/join [CODE] — family member links to a senior's profile."""
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/join", user_id)
    try:
        args = context.args
        code = args[0] if args else ""
        success, reply = join_by_code(code, user_id)
        await update.message.reply_text(reply, parse_mode="Markdown")
        logger.info(
            "OUT | user_id=%s | type=join | code=%s | success=%s",
            user_id, code, success,
        )
    except Exception as e:
        logger.error("ERR | user_id=%s | /join error: %s", user_id, e)
        await update.message.reply_text("Something went wrong. Please try again. 🙏")


async def adminreset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != 8711370451:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /adminreset <telegram_id>")
        return
    try:
        target_telegram_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid telegram_id — must be a number.")
        return
    result = admin_reset_user(target_telegram_id)
    await update.message.reply_text(result)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = update.message.text
    logger.info("IN  | user_id=%s | type=text | content=%s", user_id, text)
    try:
        user_row = get_or_create_user(user_id)
        await _run_pipeline(user_id, text, user_row, update, input_type="text", context=context)
    except Exception as e:
        logger.error("ERR | user_id=%s | error=%s", user_id, e)
        await update.message.reply_text(
            "Maafi chahta hoon, abhi kuch takleef aa rahi hai. Thodi der mein dobara try karein. 🙏"
        )
        raise


async def receive_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    file_id = update.message.voice.file_id
    duration = update.message.voice.duration
    logger.info(
        "IN  | user_id=%s | type=voice | file_id=%s | duration_s=%s",
        user_id, file_id, duration,
    )
    try:
        user_row = get_or_create_user(user_id)

        # Download voice file from Telegram into memory (no disk writes)
        tg_file = await context.bot.get_file(file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())

        # Transcribe via Whisper — use the user's language as a hint
        user_language = (user_row["language"] or "hindi") if user_row else "hindi"
        try:
            text = transcribe_voice(file_bytes, user_language=user_language)
            logger.info(
                "WHISPER | user_id=%s | transcribed: %s",
                user_id, text[:80],
            )
        except Exception as whisper_err:
            logger.error("WHISPER | user_id=%s | failed: %s", user_id, whisper_err)
            await update.message.reply_text(
                "Sorry, I couldn't hear that clearly. Could you type it instead? 🙏"
            )
            return

        if not text:
            await update.message.reply_text(
                "I heard something but couldn't make it out. Could you type it? 🙏"
            )
            return

        # Pass transcribed text through the full pipeline — identical to text messages
        await _run_pipeline(user_id, text, user_row, update, input_type="voice", context=context)

    except Exception as e:
        logger.error("ERR | user_id=%s | error=%s", user_id, e)
        await update.message.reply_text(
            "Maafi chahta hoon, abhi kuch takleef aa rahi hai. Thodi der mein dobara try karein. 🙏"
        )
        raise


# ---------------------------------------------------------------------------
# Scheduler job — runs every 60 seconds via PTB JobQueue (APScheduler)
# ---------------------------------------------------------------------------

async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called every minute. Sends due reminders and escalates unacknowledged ones."""
    try:
        await check_and_send_reminders(context.bot)
    except Exception as e:
        logger.error("SCHEDULER | reminder_job failed: %s", e)


async def ritual_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called every minute. Sends morning/afternoon/evening rituals at user-set times."""
    try:
        await check_and_send_rituals(context.bot)
    except Exception as e:
        logger.error("SCHEDULER | ritual_job failed: %s", e)


async def safety_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called every minute. Runs the hourly inactivity check (self-gated to once/hour)."""
    try:
        await check_inactivity(context.bot)
    except Exception as e:
        logger.error("SCHEDULER | safety_job failed: %s", e)


async def weekly_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called every minute. Sends weekly family reports on Sundays 10am IST (self-gated)."""
    try:
        await check_and_send_weekly_report(context.bot)
    except Exception as e:
        logger.error("SCHEDULER | weekly_report_job failed: %s", e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    run_startup_migrations()
    init_db()
    logger.info("Database initialised")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", handle_help_command))
    app.add_handler(CommandHandler("policy", handle_policy_command))
    app.add_handler(CommandHandler("familycode", handle_familycode))
    app.add_handler(CommandHandler("join", handle_join))
    app.add_handler(CommandHandler("adminreset", adminreset_command))
    app.add_handler(CallbackQueryHandler(handle_help_callback, pattern="^help_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, receive_voice))

    # Register the reminder scheduler — fires every 60 seconds, first check after 10s
    app.job_queue.run_repeating(reminder_job, interval=60, first=10)
    logger.info("Reminder scheduler registered (interval=60s)")

    # Register the ritual scheduler — same interval, offset by 15s to spread load
    app.job_queue.run_repeating(ritual_job, interval=60, first=15)
    logger.info("Ritual scheduler registered (interval=60s)")

    # Register the safety scheduler — runs every minute, self-gated to hourly
    app.job_queue.run_repeating(safety_job, interval=60, first=30)
    logger.info("Safety scheduler registered (interval=60s, hourly inactivity check)")

    # Register the weekly family report scheduler — runs every minute, self-gated to Sunday 10am IST
    app.job_queue.run_repeating(weekly_report_job, interval=60, first=45)
    logger.info("Weekly report scheduler registered (interval=60s, Sunday 10am IST)")

    if WEBHOOK_URL:
        logger.info("Starting webhook mode on port %s", PORT)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="/webhook",
            webhook_url=f"{WEBHOOK_URL}/webhook",
        )
    else:
        logger.info("No WEBHOOK_URL set — starting in polling mode")
        app.run_polling()


if __name__ == "__main__":
    main()
