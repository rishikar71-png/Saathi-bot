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
from deepseek import call_deepseek
from protocol1 import check_protocol1
from protocol3 import check_protocol3
from onboarding import (
    get_intro_message,
    get_resume_prompt,
    handle_onboarding_answer,
)
from memory import extract_and_save_memories
from database import save_message_record

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
# Resets on bot restart — sufficient for MVP. Module 7 (memory) can make this
# session-persistent later.
_protocol1_session_counts: dict[int, int] = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/start", user_id)
    try:
        user_row = get_or_create_user(user_id)

        if not user_row["onboarding_complete"]:
            step = user_row["onboarding_step"]
            if step == 0:
                # First ever /start — begin onboarding
                reply = get_intro_message()
            else:
                # /start sent again mid-onboarding — resume from current question
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
        save_message_record(user_id, "in", text)

        # --- Onboarding gate ---
        if not user_row["onboarding_complete"]:
            reply = handle_onboarding_answer(user_id, user_row["onboarding_step"], text)
            await update.message.reply_text(reply, parse_mode="Markdown")
            logger.info("OUT | user_id=%s | type=onboarding | step=%d", user_id, user_row["onboarding_step"])
            return

        # Build context dict from the user's profile row.
        # Module 7 will enrich this with diary entries + memory archive.
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
            "family_members":       None,   # TODO Module 7: inject from family_members table
        }

        # --- Protocol 1 check (runs BEFORE DeepSeek) ---
        session_count = _protocol1_session_counts.get(user_id, 0)
        protocol1_reply = check_protocol1(user_id, text, session_count)
        if protocol1_reply:
            _protocol1_session_counts[user_id] = session_count + 1
            await update.message.reply_text(protocol1_reply)
            logger.info("OUT | user_id=%s | type=protocol1 | stage=%d", user_id, session_count + 1)
            return

        # --- Protocol 3 check (runs BEFORE DeepSeek, AFTER Protocol 1) ---
        protocol3_reply = check_protocol3(user_id, text)
        if protocol3_reply:
            await update.message.reply_text(protocol3_reply)
            logger.info("OUT | user_id=%s | type=protocol3", user_id)
            return

        reply = call_deepseek(text, user_context)
        await update.message.reply_text(reply)
        save_message_record(user_id, "out", reply)
        logger.info("OUT | user_id=%s | type=text | content=%s", user_id, reply)

        # Extract and save memories from this turn (runs after reply is sent)
        extract_and_save_memories(user_id, text, reply)
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
