import io
import os
import re
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

# Language learning loop — tracks consecutive detections of a language
# different from the user's stored preference.
# Structure: {user_id: {"detected": "hindi", "streak": 3}}
# When streak reaches 5, users.language is updated in the DB.
_language_learning: dict[int, dict] = {}


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
# Language detection helpers
# ---------------------------------------------------------------------------

def _detect_message_language(text: str) -> str:
    """
    Detect the dominant language of a message.

    Returns: 'hindi', 'hinglish', or 'english'.

    Rules:
    - If the message contains significant Devanagari script → 'hindi'
    - If the message contains common Hindi/Urdu words in Roman script
      (at least 2 known words) → 'hinglish'
    - Otherwise → 'english'

    This is intentionally simple — false positives are fine.
    The goal is to catch clear cases, not edge cases.
    """
    import unicodedata

    # Count Devanagari characters
    devanagari_count = sum(
        1 for ch in text
        if '\u0900' <= ch <= '\u097F'  # Devanagari Unicode block
    )
    # If more than 3 Devanagari chars, it's Hindi
    if devanagari_count > 3:
        return "hindi"

    # Check for common Hindi/Urdu words written in Roman script
    _HINGLISH_MARKERS = [
        "hoon", "hun", "hai", "hain", "tha", "thi", "the",
        "kya", "nahi", "nhi", "acha", "achha", "theek", "thik",
        "bilkul", "haan", "naa", "bhi", "aur", "lekin", "par",
        "mujhe", "mera", "meri", "mere", "aap", "tum", "main",
        "kuch", "bahut", "thoda", "zyada", "bohot",
        "abhi", "aaj", "kal", "phir", "dobara",
        "ghar", "khana", "pani", "beta", "beti",
        "ji", "yaar", "bhai", "didi",
    ]
    text_lower = text.lower()
    # Word-boundary aware match: split on spaces and punctuation
    words = set(text_lower.replace(",", " ").replace(".", " ").split())
    hinglish_hits = sum(1 for w in _HINGLISH_MARKERS if w in words)
    if hinglish_hits >= 2:
        return "hinglish"

    return "english"


def _update_language_learning(user_id: int, stored_language: str, detected_language: str) -> str:
    """
    Maintain a rolling streak of detected language signals.
    If 5 consecutive messages are detected as a different language than stored,
    update the DB preference and return the new language.
    Returns the language that should be used for this response.
    """
    # Treat hindi and hinglish as compatible — don't flap between them
    def _family(lang: str) -> str:
        return "hindi_family" if lang in ("hindi", "hinglish") else "english"

    stored_family = _family(stored_language)
    detected_family = _family(detected_language)

    if detected_family == stored_family:
        # Consistent — reset streak
        _language_learning.pop(user_id, None)
        return stored_language

    # Different family detected — track streak
    entry = _language_learning.get(user_id, {"detected": detected_language, "streak": 0})
    if entry["detected"] != detected_language:
        # Detection changed mid-streak — reset
        entry = {"detected": detected_language, "streak": 0}
    entry["streak"] += 1
    _language_learning[user_id] = entry

    if entry["streak"] >= 5:
        # 5 consecutive messages in a different language — update DB preference
        try:
            from database import update_user_fields as _uuf_learn
            _uuf_learn(user_id, language=detected_language)
            logger.info(
                "LANG_LEARN | user_id=%s | updated preference: %s → %s",
                user_id, stored_language, detected_language,
            )
        except Exception as _le:
            logger.warning("LANG_LEARN | DB update failed | user_id=%s | %s", user_id, _le)
        _language_learning.pop(user_id, None)
        return detected_language

    # Use detected language for this response even before the DB update
    return detected_language


# ---------------------------------------------------------------------------
# Live data injection — news / cricket / weather queries in conversation
# ---------------------------------------------------------------------------

_NEWS_QUERY_SIGNALS = re.compile(
    r"\b(news|headlines?|current events?|khabar|khabaren|aaj ki khabar|"
    r"what.?s happening|kya ho raha|aaj kya hua|duniya mein kya|india mein kya|"
    r"latest (news|updates?)|tell me (the )?news|koi khabar|kuch naya)\b",
    re.IGNORECASE,
)
_CRICKET_QUERY_SIGNALS = re.compile(
    r"\b(cricket|score|match|india (won|lost|playing)|test match|"
    r"ipl|odi|t20|kya hua match mein|cricket mein kya)\b",
    re.IGNORECASE,
)
_WEATHER_QUERY_SIGNALS = re.compile(
    r"\b(weather|temperature|mausam|garmi|sardi|barish|kitni garmi|"
    r"how.?s the weather|aaj mausam|what.?s the temp)\b",
    re.IGNORECASE,
)


def _inject_live_data_if_needed(text: str, user_context: dict) -> str | None:
    """
    If the user's message is a news, cricket, or weather query, fetch live data
    and return an injection block for the DeepSeek system prompt.

    Returns None if the message is not a live-data query.
    Returns a string instruction block if it is (whether or not API data was available).
    The block either contains real data or explicitly tells DeepSeek not to hallucinate.
    """
    is_news    = bool(_NEWS_QUERY_SIGNALS.search(text))
    is_cricket = bool(_CRICKET_QUERY_SIGNALS.search(text))
    is_weather = bool(_WEATHER_QUERY_SIGNALS.search(text))

    if not (is_news or is_cricket or is_weather):
        return None

    try:
        from apis import fetch_news, fetch_cricket, fetch_weather
        from rituals import wrap_news, wrap_cricket, wrap_weather
    except ImportError:
        return None

    parts = []

    if is_weather or is_news or is_cricket:
        parts.append(
            "LIVE DATA CONTEXT — CRITICAL RULES:\n"
            "1. You MUST use ONLY the data provided below. Do NOT use training knowledge for weather, news, or cricket.\n"
            "2. If a section says 'No live data' — say exactly that to the user. Do NOT invent temperatures, conditions, scores, or headlines.\n"
            "3. Making up weather ('pleasant day', 'light cloud cover', 'shawl handy') when no data is provided is a serious error.\n"
            "4. The honest response when data is unavailable: 'I don't have live [weather/news/cricket] right now — my live updates aren't available at this moment.'"
        )

    profile_city = user_context.get("city") or ""

    # Always try to extract city from the message first — if the senior asks about
    # Delhi weather, use Delhi, even if their profile city is Mumbai.
    # Fall back to profile city only when no city is mentioned in the message.
    _TRAILING_NON_CITY = re.compile(
        r"\b(today|tonight|now|aaj|abhi|kal|tomorrow|this week|is waqt|"
        r"mein|ka|ki|ke|hai|hain|kya|please|batao|bata|do)\b.*$",
        re.IGNORECASE,
    )
    city = profile_city  # default
    if is_weather:
        _city_match = re.search(
            r"(?:weather|mausam|temperature)\s+(?:in|mein|of|ka|ki)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)",
            text, re.IGNORECASE
        ) or re.search(
            r"([A-Za-z]+(?:\s+[A-Za-z]+)?)\s+(?:mein|ka|ki)\s+(?:mausam|weather|temperature)",
            text, re.IGNORECASE
        )
        if _city_match:
            raw_city = _city_match.group(1).strip()
            raw_city = _TRAILING_NON_CITY.sub("", raw_city).strip()
            if raw_city:
                city = raw_city.title()

    if is_weather and city:
        try:
            raw = fetch_weather(city)
            if raw:
                parts.append(f"Weather ({city}): {wrap_weather(city, raw)}")
            else:
                parts.append(f"Weather: No live weather data available right now.")
        except Exception:
            parts.append("Weather: Not available right now.")
    elif is_weather and not city:
        parts.append("Weather: I don't know your city — ask me like 'what's the weather in Delhi?' and I'll check.")

    if is_cricket:
        try:
            raw = fetch_cricket()
            if raw:
                parts.append(f"Cricket: {wrap_cricket(raw)}")
            else:
                parts.append("Cricket: No live match data right now.")
        except Exception:
            parts.append("Cricket: Not available right now.")

    if is_news:
        news_interests = user_context.get("news_interests") or user_context.get("favourite_topics") or ""
        try:
            raw = fetch_news(news_interests)
            if raw:
                parts.append(f"News: {wrap_news(raw)}")
            else:
                parts.append("News: No live headlines available right now.")
        except Exception:
            parts.append("News: Not available right now.")

    if len(parts) <= 1:
        # Only the header, no actual data
        return (
            "LIVE DATA CONTEXT:\n"
            "The user is asking about live news or events. You do not have access to live data "
            "right now. Tell them honestly and warmly — do not guess, do not give cricket or "
            "sports results unless confirmed above, and do not repeat old information."
        )

    return "\n".join(parts)


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

    # --- Memory question response capture ---
    # If this user was sent a memory question and has not yet responded, capture
    # their next message as the response. Save it to the memories table with full
    # metadata (question_id, question_text, theme) so the memoir is properly linked.
    # This runs after reminder ack (so 👍 doesn't accidentally close a question)
    # but before onboarding gate and all other pipeline stages.
    if user_row["onboarding_complete"]:
        from memory_questions import get_pending_memory_question, save_memory_response
        _pending_qid, _pending_qtext, _pending_qtheme = get_pending_memory_question(user_row)
        if _pending_qid is not None:
            save_memory_response(
                user_id,
                response_text=text,
                question_id=_pending_qid,
                question_text=_pending_qtext,
                theme=_pending_qtheme,
            )
            logger.info(
                "MEMORY_Q | response captured | user_id=%s | question_id=%s | theme=%s",
                user_id, _pending_qid, _pending_qtheme,
            )
            # Do NOT return — let the message continue through the full pipeline
            # so DeepSeek can respond warmly to what the senior just shared.

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

    # --- Script-based language detection + learning loop ---
    # Runs after user_context is built. Detects the actual language of the current
    # message and adjusts user_context["language"] for this call.
    # If 5+ consecutive messages differ from the stored preference, the DB is updated.
    #
    # SHORT MESSAGE GUARD: Messages ≤3 words are unreliable language signals.
    # A single Hindi word ("Haan"), a voice note that transcribes to one word,
    # or a short English ack ("okay") should never override the stored preference.
    _stored_lang = user_context.get("language") or "english"
    _short_message = len(text.strip().split()) <= 3
    if _short_message:
        # Trust stored preference — don't learn from this, don't override for this call.
        _effective_lang = _stored_lang
        logger.debug("LANG | user_id=%s | short message, using stored=%s", user_id, _stored_lang)
    else:
        _detected_lang = _detect_message_language(text)
        _effective_lang = _update_language_learning(user_id, _stored_lang, _detected_lang)
        if _effective_lang != _stored_lang:
            logger.info(
                "LANG | user_id=%s | override: stored=%s detected=%s effective=%s",
                user_id, _stored_lang, _detected_lang, _effective_lang,
            )
    user_context["language"] = _effective_lang

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

    def _get_time_aware_greeting(hour: int, language: str, name: str) -> str:
        _name = (name or "").strip()
        _lang = (language or "english").lower()

        # Build a name suffix — used only in Hindi/Hinglish for natural flow
        _ji = f" {_name} ji" if _name else ""
        _eng_name = f", {_name} ji" if _name else ""

        if _lang in ("hindi", "hinglish"):
            if 5 <= hour < 12:
                return f"Namaste{_ji}. Sunkar achha laga. 🙏"
            elif 12 <= hour < 17:
                return f"Namaste{_ji}. Dopahar kaisi chal rahi hai?"
            elif 17 <= hour < 21:
                return f"Namaste{_ji}. Aaj ka din kaisa raha?"
            else:
                return f"Namaste{_ji}. Bahut raat ho gayi — sab theek hai?"
        else:
            if 5 <= hour < 12:
                return f"Good morning{_eng_name}. Good to hear from you."
            elif 12 <= hour < 17:
                return f"Good afternoon{_eng_name}."
            elif 17 <= hour < 21:
                return f"Good evening{_eng_name}. How has the day been?"
            else:
                return f"Hello{_eng_name}. Up late tonight?"

    # Only fire the greeting handler if we are NOT mid-session.
    # If there is substantial session history (>= 4 turns), the senior is returning
    # mid-conversation — let the message fall through to the mid-session intercept below.
    _is_fresh_greeting = (
        (msg_lower in _GREETING_TRIGGERS or any(msg_lower.startswith(g) for g in _GREETING_TRIGGERS))
        and len(_session_history) < 4
    )
    if _is_fresh_greeting:
        _greet_reply = _get_time_aware_greeting(
            _local_hour,
            language=user_context.get("language") or "english",
            name=user_context.get("name") or "",
        )
        await update.message.reply_text(_greet_reply)
        save_message_record(user_id, "out", _greet_reply)
        save_session_turn(user_id, "user", text)
        save_session_turn(user_id, "assistant", _greet_reply)
        logger.info("OUT | user_id=%s | type=greeting | hour=%d", user_id, _local_hour)
        return

    # --- Emergency keyword check (runs BEFORE Protocol 1) ---
    # Detects PHYSICAL safety signals only ("I fell", "bachao", chest pain, etc.).
    # Mental health / suicidal-ideation phrases go exclusively to Protocol 1.
    # send_help_prompt() sends an immediate text with 112 before any button press.
    # We also attempt a family alert here if the user has consented.
    if check_emergency_keywords(text):
        await send_help_prompt(update)
        # Attempt family alert immediately — don't wait for button press.
        # Only fires if escalation_opted_in = 1 AND contacts have telegram_user_id.
        try:
            from safety import alert_emergency_contacts
            await alert_emergency_contacts(context.bot, user_id, user_row)
        except Exception as _emg_err:
            logger.error("SAFETY | emergency alert failed | user_id=%s | %s", user_id, _emg_err)
        logger.info("OUT | user_id=%s | type=emergency_prompt", user_id)
        return

    # --- Protocol 1 check (runs BEFORE DeepSeek) ---
    session_count = _protocol1_session_counts.get(user_id, 0)
    protocol1_reply, is_escalation = check_protocol1(user_id, text, session_count)

    if is_escalation:
        # Imminent-risk signal. Attempt family alert if user has consented.
        # Build an honest response based on whether the alert actually went out.
        _protocol1_session_counts[user_id] = session_count + 1
        from safety import alert_emergency_contacts
        from protocol1 import _ESCALATION_RESPONSE_ALERT_SENT, _ESCALATION_RESPONSE_NO_ALERT
        _alert_sent = 0
        try:
            _alert_sent = await alert_emergency_contacts(context.bot, user_id, user_row)
        except Exception as _ae:
            logger.error("PROTOCOL1 | family alert failed | user_id=%s | %s", user_id, _ae)
        # Use the honest response variant — never claim alert sent if it wasn't
        _p1_escalation_text = (
            _ESCALATION_RESPONSE_ALERT_SENT if _alert_sent > 0
            else _ESCALATION_RESPONSE_NO_ALERT
        )
        await update.message.reply_text(_p1_escalation_text)
        logger.warning(
            "OUT | user_id=%s | type=protocol1_escalation | alert_sent=%d",
            user_id, _alert_sent,
        )
        return

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
            reply = build_music_message(title, url, language=user_context.get("language") or "english")
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

    # --- Live data injection for news / cricket / weather queries ---
    # The news/cricket/weather APIs are wired into the morning ritual but NOT into
    # ad-hoc conversation. Without this block, "tell me the news" goes to DeepSeek
    # which hallucinates stale content. Here we detect intent, call the APIs, and
    # inject real data (or an honest "I don't have live news") into the DeepSeek
    # system prompt as a context block.
    _live_data_injected = _inject_live_data_if_needed(text, user_context)
    if _live_data_injected:
        user_context["live_data_context"] = _live_data_injected
        logger.info("LIVE_DATA | user_id=%s | injected: %r", user_id, _live_data_injected[:80])

    # --- Mid-session return greeting intercept ---
    # When the senior returns with a bare greeting mid-session, build a targeted
    # prompt that explicitly includes the recent thread so DeepSeek references it
    # instead of defaulting to "Good evening. How has the day been?"
    _GREETING_WORDS = {
        "hello", "hello again", "hi", "hi again", "i'm back", "im back",
        "back again", "namaste", "haan", "haan ji",
    }
    _is_mid_session_greeting = (
        len(_session_history) >= 4
        and text.strip().lower().rstrip("!. ") in _GREETING_WORDS
    )
    _original_text = text  # preserve for session saving
    if _is_mid_session_greeting:
        _recent = _session_history[-6:]
        _ctx = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Saathi'}: {m['content']}"
            for m in _recent
        )
        text = (
            f"The user just returned mid-session with a greeting ('{_original_text.strip()}').\n\n"
            f"Recent conversation:\n{_ctx}\n\n"
            f"Respond in 1-2 warm sentences. If there is an unfinished story or topic above, "
            f"reference it warmly — e.g. 'Welcome back — you were just about to tell me about Bombay.' "
            f"Do NOT say 'Good evening. How has the day been?' or any generic time-of-day greeting."
        )
        _session_history = []  # History already embedded in prompt — don't double-inject

    # --- Vulnerability pre-processor ---
    # DeepSeek ignores system-prompt language-lock and no-excavation rules during
    # emotional content. Hard override: wrap the message with explicit instructions
    # before it reaches DeepSeek. _original_text is preserved so session history
    # is never polluted with the injected wrapper.
    # Grief signals — heavier disclosures that need more space than one sentence.
    # These get 2-3 sentences of warm presence, not the strict one-sentence limit.
    _GRIEF_SIGNALS = [
        # English
        "passed away", "he passed", "she passed", "they passed",
        "died", "lost my", "lost him", "lost her",
        "widowed", "widow", "widower",
        "miss him so much", "miss her so much", "miss them so much",
        "gone now", "not here anymore", "not with us anymore",
        "years ago he", "years ago she",
        # Hindi / Hinglish
        "woh nahi rahe", "woh nahi rahi", "woh chale gaye", "woh chali gayi",
        "unka intekaal", "unka nidhan", "guzar gaye", "guzar gayi",
        "bahut yaad aata hai", "bahut yaad aati hai",
        "akele ho gaye hain", "akele ho gayi hain",
        "bichhad gaye", "bichhad gayi",
    ]

    # Loneliness / invisibility signals — one-sentence hold, no excavation
    _VULNERABILITY_SIGNALS = [
        # English
        "nobody needs me", "no one needs me", "feel like nobody",
        "feel invisible", "nobody cares", "no one cares",
        "feel useless", "feel alone", "feel lonely", "feel like a burden",
        "nobody listens", "no one listens", "i am a burden", "i'm a burden",
        "nobody wants me", "no one wants me", "don't belong",
        "feel left out", "feel forgotten", "nobody remembers",
        # Hindi / Hinglish
        "koi zaroorat nahi", "koi nahi chahta", "akela feel",
        "akela hoon", "akele hoon", "koi nahi sunata", "koi nahi sunta",
        "kisi ko zaroorat nahi", "bekar lagta", "bekar lag raha",
        "bojh lag raha", "bojh lagta", "koi yaad nahi karta",
    ]

    _text_lower = text.lower()
    _is_grief = any(sig in _text_lower for sig in _GRIEF_SIGNALS)
    _is_vulnerability = (not _is_grief) and any(sig in _text_lower for sig in _VULNERABILITY_SIGNALS)

    if _is_grief:
        _original_text = text
        _lang = (user_context.get("language") or "english").lower()
        _lang_label = {"hindi": "Hindi", "hinglish": "Hinglish"}.get(_lang, "English")
        text = (
            f"[HARD OVERRIDE — apply before anything else:\n"
            f"1. Respond in {_lang_label} only. Do not switch language for any reason.\n"
            f"2. Two to three short sentences of warm presence. No more.\n"
            f"3. Stay with the grief — do not redirect, do not offer silver lining, do not look forward.\n"
            f"4. No questions. Do not ask what happened or how they are feeling now.\n"
            f"5. No therapy phrases ('it sounds like', 'I hear that'). Plain, warm language only.]\n\n"
            f"Senior's message: {_original_text}"
        )
        logger.info("PIPELINE | user_id=%s | grief_pre_processor triggered", user_id)

    elif _is_vulnerability:
        _original_text = text  # ensure original is preserved (may already be set above)
        _lang = (user_context.get("language") or "english").lower()
        _lang_label = {"hindi": "Hindi", "hinglish": "Hinglish"}.get(_lang, "English")
        text = (
            f"[HARD OVERRIDE — apply before anything else:\n"
            f"1. Respond in {_lang_label} only. Do not switch language for any reason.\n"
            f"2. One plain sentence of acknowledgement. Stop there.\n"
            f"3. Ask nothing. Not what happened. Not how long. Not anything.\n"
            f"4. Do not invite the user to say more.\n"
            f"The senior has shared something vulnerable. Hold the space. Do not fill it.]\n\n"
            f"Senior's message: {_original_text}"
        )
        logger.info("PIPELINE | user_id=%s | vulnerability_pre_processor triggered", user_id)

    # --- Identity / confusion intercept ---
    # If the senior asks who Saathi is or seems confused about what they're using,
    # return the hardcoded designed response without going to DeepSeek.
    # This is more reliable than hoping DeepSeek follows the identity rules.
    _IDENTITY_SIGNALS = [
        "who are you", "what are you", "are you a robot", "are you a bot",
        "are you human", "are you real", "is this a machine", "is this ai",
        "kya tum insaan ho", "kya aap insaan ho", "tum kaun ho", "aap kaun ho",
        "yeh kya hai", "kya ho tum", "kya hain aap",
        "samjha nahi", "samjhi nahi", "kya ho raha hai yahan",
        "i don't understand", "i'm confused", "i'm not sure what this is",
    ]
    # Always check the original message, not the wrapped version that grief/vulnerability may have set
    _msg_stripped = _original_text.strip().lower().rstrip("?.!")
    if any(sig in _msg_stripped for sig in _IDENTITY_SIGNALS):
        _id_lang = (user_context.get("language") or "english").lower()
        if _id_lang in ("hindi", "hinglish"):
            _identity_reply = "Bas baat karne ke liye hoon — aur kuch nahi. 🙏"
        else:
            _identity_reply = "Just someone to chat with — that's really all. 🙏"
        await update.message.reply_text(_identity_reply)
        save_message_record(user_id, "out", _identity_reply)
        save_session_turn(user_id, "user", _original_text)
        save_session_turn(user_id, "assistant", _identity_reply)
        logger.info("OUT | user_id=%s | type=identity_intercept", user_id)
        return

    # --- Short-reply disengagement detector ---
    # If the senior sends a very short reply (≤4 words, no question mark),
    # inject a HARD OVERRIDE so DeepSeek doesn't ask more questions.
    # This catches "Ok", "Hmm", "👍", "Theek hai", "Haan" etc.
    _word_count = len(_original_text.strip().split())  # use original, not possibly-wrapped text
    _is_short_disengaged = (
        _word_count <= 4
        and "?" not in _original_text
        and not any(sig in _original_text.lower() for sig in ["help", "bachao", "emergency"])
        and not _is_vulnerability  # let vulnerability pre-processor handle emotional shorts
        and not _is_grief          # let grief pre-processor handle grief shorts
    )
    if _is_short_disengaged and not _is_mid_session_greeting:
        _dis_lang = (user_context.get("language") or "english").lower()
        _dis_lang_label = {"hindi": "Hindi", "hinglish": "Hinglish"}.get(_dis_lang, "English")
        text = (
            f"[HARD OVERRIDE — apply before anything else:\n"
            f"1. Respond in {_dis_lang_label} only.\n"
            f"2. One short, warm, non-questioning sentence. Stop there.\n"
            f"3. Ask nothing. No follow-up. No observations about how they seem.\n"
            f"4. This is a disengaged reply — do not try to extend the conversation.\n"
            f"5. Correct: 'Theek hai.' or 'Alright.' — Incorrect: 'Interesting — what do you think about...?']\n\n"
            f"Senior's message: {_original_text}"
        )
        logger.info("PIPELINE | user_id=%s | short_reply_disengagement triggered", user_id)

    # --- DeepSeek ---
    # Pass _session_history so DeepSeek has full in-session conversation context.
    reply = call_deepseek(text, user_context, session_messages=_session_history)

    # Send text first — user gets the response immediately regardless of TTS
    await update.message.reply_text(reply)
    save_message_record(user_id, "out", reply)
    # Save this exchange to session buffer for the next DeepSeek call.
    # Use _original_text (not text) so the targeted prompt is never saved to session history.
    save_session_turn(user_id, "user", _original_text)
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


async def testapis_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dev-only command: tests weather/news/cricket APIs live and reports results."""
    if update.effective_user.id != 8711370451:
        return
    import os
    from apis import fetch_weather, fetch_cricket, fetch_news
    lines = ["*API Test Results*\n"]

    # env vars
    lines.append(f"WEATHER_API_KEY set: {'yes' if os.environ.get('WEATHER_API_KEY') else 'NO'}")
    lines.append(f"CRICKET_API_KEY set: {'yes' if os.environ.get('CRICKET_API_KEY') else 'NO'}")
    lines.append(f"NEWS_API_KEY set: {'yes' if os.environ.get('NEWS_API_KEY') else 'NO'}\n")

    # weather
    try:
        w = fetch_weather("Mumbai")
        lines.append(f"Weather (Mumbai): {str(w)[:120] if w else 'None returned'}")
    except Exception as e:
        lines.append(f"Weather error: {e}")

    # cricket
    try:
        c = fetch_cricket()
        lines.append(f"Cricket: {str(c)[:120] if c else 'None returned (no live match — expected)'}")
    except Exception as e:
        lines.append(f"Cricket error: {e}")

    # news
    try:
        n = fetch_news("")
        lines.append(f"News: {str(n)[:200] if n else 'None returned'}")
    except Exception as e:
        lines.append(f"News error: {e}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = update.message.text
    logger.info("IN  | user_id=%s | type=text | content=%s", user_id, text)
    try:
        user_row = get_or_create_user(user_id)
        # Show "typing..." and keep it alive for the full duration of the pipeline.
        # Telegram's typing action expires after ~5 seconds — we resend every 4s so the
        # senior always sees Saathi is thinking, never a silent gap.
        import asyncio
        chat_id = update.effective_chat.id

        async def _keep_typing(stop_event: asyncio.Event) -> None:
            while not stop_event.is_set():
                try:
                    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                except Exception:
                    pass
                # Sleep in 0.5s increments so we exit quickly when stop_event fires
                for _ in range(8):  # 8 × 0.5s = 4s
                    if stop_event.is_set():
                        return
                    await asyncio.sleep(0.5)

        _stop_event = asyncio.Event()
        _typing_task = asyncio.create_task(_keep_typing(_stop_event))
        try:
            await _run_pipeline(user_id, text, user_row, update, input_type="text", context=context)
        finally:
            _stop_event.set()
            await asyncio.sleep(0)  # yield so the task can exit cleanly
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

        # Show "typing..." while Whisper transcribes — voice upload can take 2–4 seconds
        # and a silent screen causes seniors to think something broke.
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )

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

        # Short transcription guard — if Whisper only caught 1–2 words, the recording
        # was likely unclear (not the user disengaging). Ask to resend rather than
        # sending a flat response like "Alright." via the disengagement path.
        if len(text.strip().split()) <= 2:
            user_lang = (user_row["language"] or "hindi") if user_row else "hindi"
            if user_lang in ("hindi", "hinglish"):
                await update.message.reply_text(
                    "Sahi se sun nahi paaya — zara dobara bhejein? 🙏"
                )
            else:
                await update.message.reply_text(
                    "Sorry, couldn't catch that clearly — could you send it again? 🙏"
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


async def handle_unsupported_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Catch-all handler for message types Saathi cannot process:
    photos, stickers, GIFs, documents, video, audio files.

    Without this, PTB silently drops the message and the senior gets no response —
    which looks like the bot broke. A warm, language-aware reply is far better.
    """
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=unsupported_media", user_id)
    try:
        user_row = get_or_create_user(user_id)
        language = (user_row["language"] or "hindi") if user_row else "hindi"
    except Exception:
        language = "hindi"

    if language in ("hindi", "hinglish"):
        reply = (
            "Yeh mujhe abhi samajh nahi aata — "
            "apni baat words mein bataiye na? 🙏"
        )
    else:
        reply = (
            "I can't quite see that yet — "
            "but I'd love to hear about it in your own words. 🙏"
        )

    await update.message.reply_text(reply)
    logger.info("OUT | user_id=%s | type=unsupported_media_reply", user_id)


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

def _start_health_server() -> None:
    """
    Bind to Railway's $PORT immediately so the platform considers the container
    healthy and does not kill it during slow DB initialisation.

    This is a minimal HTTP server (GET / → 200 OK).  In polling mode there is
    no PTB webhook listener, so this port is free.  In webhook mode this server
    is NOT started — PTB's run_webhook() owns the port instead.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):  # silence access logs
            pass

    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("HEALTH | server started on port %d", port)


def main() -> None:
    # Start health server FIRST — before any DB work — so Railway sees the
    # process as healthy and does not restart the container during slow init.
    # Only needed in polling mode; webhook mode lets PTB own the port.
    if not os.environ.get("WEBHOOK_URL", ""):
        _start_health_server()

    logger.info("STARTUP | run_startup_migrations")
    run_startup_migrations()
    logger.info("STARTUP | init_db")
    init_db()
    logger.info("Database initialised")

    # Seed the memory question bank — inserts 300+ questions if the table is empty.
    # Safe to call on every startup — silently skips if already seeded.
    try:
        from memory_questions import seed_memory_questions
        seed_memory_questions()
        logger.info("Memory question bank ready")
    except Exception as seed_err:
        logger.error("STARTUP | seed_memory_questions failed (non-fatal): %s", seed_err)

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", handle_help_command))
    app.add_handler(CommandHandler("policy", handle_policy_command))
    app.add_handler(CommandHandler("familycode", handle_familycode))
    app.add_handler(CommandHandler("join", handle_join))
    app.add_handler(CommandHandler("adminreset", adminreset_command))
    app.add_handler(CommandHandler("testapis", testapis_command))
    app.add_handler(CallbackQueryHandler(handle_help_callback, pattern="^help_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, receive_voice))
    # Catch photos, stickers, GIFs, documents, video, audio — return a warm
    # "I can't see that" message instead of silently dropping the message.
    app.add_handler(MessageHandler(
        filters.PHOTO
        | filters.Document.ALL   # catches GIFs/animations too (sent as Documents)
        | filters.Sticker.ALL
        | filters.VIDEO
        | filters.VIDEO_NOTE
        | filters.AUDIO,
        handle_unsupported_media,
    ))

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

    # DIAGNOSTIC: forced polling mode to rule out webhook misconfiguration.
    # Switch back to webhook once bot is confirmed live and responding.
    logger.info("Starting in POLLING mode (diagnostic)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
