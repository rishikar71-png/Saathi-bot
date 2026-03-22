import os
import logging
from telegram import Update
from telegram.ext import (
    Application,
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

    # --- DeepSeek ---
    reply = call_deepseek(text, user_context)
    await update.message.reply_text(reply)
    save_message_record(user_id, "out", reply)
    logger.info("OUT | user_id=%s | type=%s | content=%s", user_id, input_type, reply[:80])

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
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    init_db()
    logger.info("Database initialised")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, receive_voice))

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
