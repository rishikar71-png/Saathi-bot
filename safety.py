"""
Module 13 — Safety Features

Physical emergency detection + /help command + family alerts + inactivity detector.

Public interface (called from main.py):
    check_emergency_keywords(text) -> bool
        Returns True if the message looks like a physical emergency cry.
        Call BEFORE Protocol 1 in the pipeline.

    handle_help_command(update, context)
        Handles the /help Telegram command.
        Sends inline keyboard: "I'm okay" / "I need help".

    handle_help_callback(update, context)
        Handles inline keyboard button presses for the help flow.

    check_inactivity(bot)
        Async. Called every hour by the scheduler.
        Sends gentle check-in to users who haven't messaged in longer than
        their adaptive threshold. Opt-in only (heartbeat_consent = 1).

Design decisions:
    - Family alert fires only if escalation_opted_in = 1 AND contacts have telegram_user_id.
    - Inactivity detection only for users with heartbeat_consent = 1.
    - Adaptive threshold = 2× average inter-message gap, bounded [24h, 168h].
    - Max one inactivity check-in per threshold period per user.
    - All inactivity alerts logged to heartbeat_log (alert_type='inactivity_checkin').
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from database import get_connection, get_or_create_user

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# IST helpers
# ---------------------------------------------------------------------------

def _ist_now() -> datetime:
    return datetime.now(IST)


# ---------------------------------------------------------------------------
# Emergency keyword detection — physical safety only.
# Mental health crisis is handled separately by Protocol 1.
# ---------------------------------------------------------------------------

_EMERGENCY_EXACT = {
    # "help" alone is intentionally excluded — far too common a word.
    # "can you help me decide?" must not trigger emergency.
    # Only contextual emergency phrases are listed here.
    "help!", "help!!", "sos",
    "emergency", "bachao", "bachao!", "madad",
    "ambulance", "112",
    "i fell", "i have fallen",
    "call someone", "kisi ko bulao",
    "please help", "help me",
    "mujhe madad", "madad karo",
    "gir gaya", "gir gayi", "gir gaye",
}

_EMERGENCY_SUBSTRINGS = (
    "i fell", "i have fallen",
    "gir gaya", "gir gayi", "gir gaye",
    "call someone", "kisi ko bulao",
    "please help", "help me",
    "mujhe madad", "madad karo",
    "gir pada", "gir padi",
)


def check_emergency_keywords(text: str) -> bool:
    """
    Return True if the message looks like a physical emergency.

    Rules:
    - Short messages (≤ 5 words) that exactly match or contain emergency words.
    - Or longer messages containing unambiguous emergency substrings.
    """
    t = text.strip().lower()
    words = t.split()

    # Exact match against short-message set
    if t in _EMERGENCY_EXACT:
        return True

    # Short message (≤ 5 words) containing an unambiguous emergency word.
    # "help" is excluded — too common ("can you help me?", "need some help").
    # Physical emergency signals only: fell, ambulance, bachao, etc.
    if len(words) <= 5:
        for keyword in ("emergency", "bachao", "madad", "ambulance", "112"):
            if keyword in words:
                return True

    # Unambiguous physical emergency substrings in any length message
    for sub in _EMERGENCY_SUBSTRINGS:
        if sub in t:
            return True

    return False


# ---------------------------------------------------------------------------
# Emergency contact alert
# ---------------------------------------------------------------------------

def _get_family_contacts_with_telegram(user_id: int) -> list:
    """Return family members who have a telegram_user_id set."""
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT name, telegram_user_id, relationship
            FROM family_members
            WHERE user_id = ?
              AND telegram_user_id IS NOT NULL
            ORDER BY is_setup_user DESC, id ASC
            LIMIT 3
            """,
            (user_id,),
        ).fetchall()


async def alert_emergency_contacts(bot, user_id: int, user_row) -> int:
    """
    Send an urgent Telegram alert to all family contacts with telegram_user_id.

    Returns the number of contacts successfully alerted.
    Only fires if escalation_opted_in = 1.
    """
    if not user_row["escalation_opted_in"]:
        logger.info(
            "SAFETY | family alert skipped — escalation_opted_in=0 | user_id=%s", user_id
        )
        return 0

    contacts = _get_family_contacts_with_telegram(user_id)
    if not contacts:
        logger.info(
            "SAFETY | family alert skipped — no contacts with telegram_user_id | user_id=%s",
            user_id,
        )
        return 0

    name = (user_row["name"] or "Aapke ghar ka").strip()
    sal = (user_row["preferred_salutation"] or "").strip()
    address = f"{name} {sal}".strip() if sal else name

    alert_text = (
        f"🚨 *Saathi se urgent alert*\n\n"
        f"*{address}* ne abhi help maanga hai.\n\n"
        f"Please unhe turant contact karein. 🙏"
    )

    sent = 0
    for contact in contacts:
        try:
            await bot.send_message(
                chat_id=contact["telegram_user_id"],
                text=alert_text,
                parse_mode="Markdown",
            )
            logger.info(
                "SAFETY | emergency alert sent | user_id=%s | contact=%s",
                user_id, contact["name"],
            )
            sent += 1
        except Exception as e:
            logger.error(
                "SAFETY | emergency alert failed | contact=%s | %s",
                contact["telegram_user_id"], e,
            )

    return sent


# ---------------------------------------------------------------------------
# /help command handler
# ---------------------------------------------------------------------------

async def handle_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles /help. Sends a warm message with an inline keyboard.
    Available to all users regardless of safety opt-in — the senior may need
    this before they've set preferences.
    """
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/help", user_id)

    keyboard = [
        [InlineKeyboardButton("I'm okay, just checking 🙏", callback_data="help_ok")],
        [InlineKeyboardButton("I need help right now", callback_data="help_needed")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "I'm here. Do you need help right now? 🙏",
        reply_markup=reply_markup,
    )


async def handle_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles inline keyboard button presses from the /help flow.
    Also triggered when emergency keywords are detected in a regular message.
    """
    query = update.callback_query
    await query.answer()  # acknowledge the button press to Telegram

    user_id = query.from_user.id
    data = query.data

    if data == "help_ok":
        await query.edit_message_text(
            "Glad to hear that. I'm always here whenever you need me. 🙏"
        )
        logger.info("SAFETY | help_ok | user_id=%s", user_id)
        return

    if data == "help_needed":
        logger.info("SAFETY | help_needed | user_id=%s", user_id)

        # Immediately acknowledge the senior — they should not wait
        await query.edit_message_text(
            "Okay. I'm letting your family know right now. Stay where you are — "
            "help is on its way. I'm right here with you. 🙏"
        )

        # Alert contacts
        user_row = get_or_create_user(user_id)
        sent = await alert_emergency_contacts(context.bot, user_id, user_row)

        if sent == 0:
            # No contacts could be alerted — give the senior a fallback
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "I wasn't able to reach your contacts automatically — "
                    "please call someone close to you, or dial *112* for emergency services. "
                    "I'm here with you. 🙏"
                ),
                parse_mode="Markdown",
            )


# ---------------------------------------------------------------------------
# Emergency keyword pipeline trigger
# Sends the inline keyboard in response to physical emergency text.
# Called from main.py _run_pipeline() before Protocol 1.
# ---------------------------------------------------------------------------

async def send_help_prompt(update: Update) -> None:
    """Send the /help inline keyboard in response to a detected emergency keyword."""
    keyboard = [
        [InlineKeyboardButton("I'm okay, just checking 🙏", callback_data="help_ok")],
        [InlineKeyboardButton("I need help right now", callback_data="help_needed")],
    ]
    await update.message.reply_text(
        "I'm here. Do you need help right now? 🙏",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------------------------------------------------------------------
# Inactivity detection — adaptive threshold, opt-in only
# ---------------------------------------------------------------------------

# Module-level gate: run inactivity check at most once per hour
_last_inactivity_run_hour: Optional[str] = None


def _should_run_inactivity_check() -> bool:
    global _last_inactivity_run_hour
    current_hour = _ist_now().strftime("%Y-%m-%d %H")
    if _last_inactivity_run_hour == current_hour:
        return False
    _last_inactivity_run_hour = current_hour
    return True


def _calculate_threshold_hours(user_id: int) -> int:
    """
    Adaptive inactivity threshold for this user.

    Algorithm:
    1. Fetch the timestamps of the last 30 inbound messages.
    2. Calculate gaps between consecutive messages (in hours).
    3. Discard gaps > 14 days (outliers: vacations, illness).
    4. Average remaining gaps and multiply by 2.
    5. Clamp to [24, 168] hours. Default 48h if < 5 messages.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT created_at FROM messages
            WHERE user_id = ? AND direction = 'in'
            ORDER BY created_at DESC
            LIMIT 30
            """,
            (user_id,),
        ).fetchall()

    if len(rows) < 5:
        return 48  # not enough data — use default

    timestamps = []
    for r in rows:
        try:
            # SQLite stores as naive UTC; parse and make UTC-aware
            dt = datetime.fromisoformat(r["created_at"]).replace(tzinfo=timezone.utc)
            timestamps.append(dt)
        except (ValueError, TypeError):
            continue

    if len(timestamps) < 5:
        return 48

    gaps_hours = []
    for i in range(len(timestamps) - 1):
        gap = (timestamps[i] - timestamps[i + 1]).total_seconds() / 3600
        if gap <= 14 * 24:  # discard outliers > 14 days
            gaps_hours.append(gap)

    if not gaps_hours:
        return 48

    avg_gap = sum(gaps_hours) / len(gaps_hours)
    threshold = int(avg_gap * 2)
    return max(24, min(168, threshold))


def _get_inactivity_candidates() -> list:
    """
    Return users with heartbeat_consent=1 who have sent at least one message,
    along with their last inbound message time and user profile.
    """
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT u.user_id, u.name, u.preferred_salutation, u.language, u.bot_name
            FROM users u
            WHERE u.onboarding_complete = 1
              AND u.heartbeat_consent = 1
              AND EXISTS (
                  SELECT 1 FROM messages m
                  WHERE m.user_id = u.user_id AND m.direction = 'in'
              )
            """
        ).fetchall()


def _get_last_message_time(user_id: int) -> Optional[datetime]:
    """Return the datetime of the user's most recent inbound message (UTC-aware)."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT MAX(created_at) AS last_at FROM messages
            WHERE user_id = ? AND direction = 'in'
            """,
            (user_id,),
        ).fetchone()
    if not row or not row["last_at"]:
        return None
    try:
        return datetime.fromisoformat(row["last_at"]).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _has_recent_inactivity_alert(user_id: int, threshold_hours: int) -> bool:
    """Return True if an inactivity check-in was already sent within threshold_hours."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM heartbeat_log
            WHERE user_id = ?
              AND alert_type = 'inactivity_checkin'
              AND datetime(created_at) >= datetime('now', ? || ' hours')
            LIMIT 1
            """,
            (user_id, f"-{threshold_hours}"),
        ).fetchone()
    return row is not None


def _log_inactivity_alert(user_id: int) -> None:
    now_ist = _ist_now().isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO heartbeat_log
                (user_id, ping_time, family_alerted, alert_type)
            VALUES (?, ?, 0, 'inactivity_checkin')
            """,
            (user_id, now_ist),
        )
        conn.commit()


def _build_inactivity_message(name: Optional[str], salutation: Optional[str],
                               language: Optional[str]) -> str:
    """Build a warm, non-alarming inactivity check-in message."""
    name = (name or "").strip()
    sal = (salutation or "").strip()
    address = f"{name} {sal}".strip() if sal else (name or "aap")
    lang = (language or "hindi").lower()

    if lang in ("hindi", "hinglish"):
        return (
            f"{address}, kuch dino se aapki baat nahi hui — "
            f"bas yeh jaanna tha ki aap theek hain. 🙏\n\n"
            f"Koi baat hai toh batayein, main yahan hoon."
        )
    else:
        return (
            f"{address}, I haven't heard from you in a little while — "
            f"just wanted to make sure you're okay. 🙏\n\n"
            f"I'm here whenever you'd like to chat."
        )


async def check_inactivity(bot) -> None:
    """
    Hourly inactivity check. For each opted-in user:
    1. Calculate adaptive threshold based on their messaging pattern.
    2. If last message is older than threshold and no recent alert sent:
       send a gentle check-in and log it to heartbeat_log.
    """
    if not _should_run_inactivity_check():
        return

    now_utc = datetime.now(timezone.utc)
    candidates = _get_inactivity_candidates()

    for row in candidates:
        user_id = row["user_id"]
        try:
            last_msg = _get_last_message_time(user_id)
            if last_msg is None:
                continue

            threshold_hours = _calculate_threshold_hours(user_id)
            elapsed_hours = (now_utc - last_msg).total_seconds() / 3600

            if elapsed_hours < threshold_hours:
                continue  # still within normal window

            if _has_recent_inactivity_alert(user_id, threshold_hours):
                continue  # already sent one within this window

            # Send the check-in
            msg = _build_inactivity_message(
                row["name"], row["preferred_salutation"], row["language"]
            )
            await bot.send_message(chat_id=user_id, text=msg)
            _log_inactivity_alert(user_id)

            logger.info(
                "SAFETY | inactivity checkin sent | user_id=%s | elapsed=%.1fh | threshold=%dh",
                user_id, elapsed_hours, threshold_hours,
            )

        except Exception as e:
            logger.error("SAFETY | inactivity check failed | user_id=%s | %s", user_id, e)
