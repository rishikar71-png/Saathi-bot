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
from database import init_db, get_or_create_user, save_message_record
from deepseek import call_deepseek
from protocol1 import check_protocol1
from protocol3 import check_protocol3
from onboarding import (
    get_intro_message,
    get_resume_prompt,
    handle_onboarding_answer,
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
) -> None:
    """
    Run the full message pipeline for a single user turn.

    input_type is "text" or "voice" — used for message logging only.
    """
    save_message_record(user_id, "in", text, message_type=input_type)

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
        reply = handle_onboarding_answer(user_id, user_row["onboarding_step"], text)
        await update.message.reply_text(reply, parse_mode="Markdown")
        logger.info(
            "OUT | user_id=%s | type=onboarding | step=%d",
            user_id, user_row["onboarding_step"],
        )
        return

    # Build context dict from user profile.
    user_context = {
        "user_id":              user_id,
        "name":                 user_row["name"],
        "bot_name":             user_row["bot_name"],
        "persona":              user_row["persona"],
        "language":             user_row["language"],
        "city":                 user_row["city"],
        "spouse_name":          user_row["spouse_name"],
        "religion":             user_row["religion"],
        "health_sensitivities": user_row["health_sensitivities"],
        "music_preferences":    user_row["music_preferences"],
        "favourite_topics":     user_row["favourite_topics"],
        "family_members":       None,  # TODO Module 7: inject from family_members table
    }

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

    # --- Protocol 3 check (runs BEFORE DeepSeek, AFTER Protocol 1) ---
    protocol3_reply = check_protocol3(user_id, text)
    if protocol3_reply:
        await update.message.reply_text(protocol3_reply)
        logger.info("OUT | user_id=%s | type=protocol3", user_id)
        return

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
    reply = call_deepseek(text, user_context)

    # Send text first — user gets the response immediately regardless of TTS
    await update.message.reply_text(reply)
    save_message_record(user_id, "out", reply)
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/start", user_id)
    try:
        user_row = get_or_create_user(user_id)

        if not user_row["onboarding_complete"]:
            step = user_row["onboarding_step"]
            if step == 0:
                reply = get_intro_message()
            else:
                reply = get_resume_prompt(user_id, step)
        else:
            reply = "Namaste! Main yahan hoon. 🙏"

        await update.message.reply_text(reply, parse_mode="Markdown")
        logger.info("OUT | user_id=%s | type=text | content=%s", user_id, reply[:80])
    except Exception as e:
        logger.error("ERR | user_id=%s | error=%s", user_id, e)
        raise


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = update.message.text
    logger.info("IN  | user_id=%s | type=text | content=%s", user_id, text)
    try:
        user_row = get_or_create_user(user_id)
        await _run_pipeline(user_id, text, user_row, update, input_type="text")
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
        await _run_pipeline(user_id, text, user_row, update, input_type="voice")

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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    init_db()
    logger.info("Database initialised")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", handle_help_command))
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
