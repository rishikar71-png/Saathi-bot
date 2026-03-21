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
from database import init_db, get_or_create_user

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", 8443))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/start", user_id)
    try:
        get_or_create_user(user_id)
        reply = "Namaste! Main Saathi hoon. 🙏"
        await update.message.reply_text(reply)
        logger.info("OUT | user_id=%s | type=text | content=%s", user_id, reply)
    except Exception as e:
        logger.error("ERR | user_id=%s | error=%s", user_id, e)
        raise


async def echo_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = update.message.text
    logger.info("IN  | user_id=%s | type=text | content=%s", user_id, text)
    try:
        get_or_create_user(user_id)
        reply = f"Saathi heard: {text}"
        await update.message.reply_text(reply)
        logger.info("OUT | user_id=%s | type=text | content=%s", user_id, reply)
    except Exception as e:
        logger.error("ERR | user_id=%s | error=%s", user_id, e)
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
        get_or_create_user(user_id)
        # File stored by Telegram; file_id used in Module 8 for Whisper transcription
        reply = "Aapki awaaz sun li. 🙏 (Voice support coming soon)"
        await update.message.reply_text(reply)
        logger.info("OUT | user_id=%s | type=text | content=%s", user_id, reply)
    except Exception as e:
        logger.error("ERR | user_id=%s | error=%s", user_id, e)
        raise


def main() -> None:
    init_db()
    logger.info("Database initialised")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_text))
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
