"""
Module 12 — Daily Rituals

Sends personalised morning briefings, afternoon check-ins, and evening
reflections to each user at the time they chose during onboarding.

Adaptive learning: tracks the hour of the first message each day, and
after 7 days gently nudges morning_checkin_time toward the user's real
rhythm — max ±30 minutes per weekly update.

Public interface (called from main.py):
    record_first_message(user_id)     — call on every inbound pipeline message
    check_and_send_rituals(bot)       — call every minute via JobQueue
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from database import get_connection, update_user_fields

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# IST helpers
# ---------------------------------------------------------------------------

def _ist_now() -> datetime:
    return datetime.now(IST)


def _current_hhmm() -> str:
    return _ist_now().strftime("%H:%M")


def _current_date() -> str:
    return _ist_now().strftime("%Y-%m-%d")


def _current_hour() -> int:
    return _ist_now().hour


def _day_of_week() -> int:
    """0 = Monday, 6 = Sunday."""
    return _ist_now().weekday()


# ---------------------------------------------------------------------------
# Activity tracking — call on every inbound message from main.py
# ---------------------------------------------------------------------------

def record_first_message(user_id: int) -> None:
    """
    Record the hour of the user's first message today.
    Silently ignored if already recorded today (UNIQUE constraint).
    Only records during waking hours (5am–11pm) to avoid skewing
    the average with insomnia or late-night messages.
    """
    hour = _current_hour()
    if hour < 5 or hour > 23:
        return  # outside waking window — don't skew the average
    today = _current_date()
    dow = _day_of_week()
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO user_activity_patterns
                    (user_id, activity_date, day_of_week, first_message_hour)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, today, dow, hour),
            )
            conn.commit()
    except Exception as e:
        logger.warning("RITUALS | record_first_message failed | user_id=%s | %s", user_id, e)


# ---------------------------------------------------------------------------
# Ritual scheduling — query helpers
# ---------------------------------------------------------------------------

def _get_users_due_for_ritual(ritual_type: str, now_hhmm: str, today: str) -> list:
    """
    Return users whose check-in time for this ritual matches the current minute
    and who have not yet received this ritual today.
    """
    time_column = {
        "morning":   "morning_checkin_time",
        "afternoon": "afternoon_checkin_time",
        "evening":   "evening_checkin_time",
    }[ritual_type]

    with get_connection() as conn:
        return conn.execute(
            f"""
            SELECT u.user_id, u.name, u.preferred_salutation, u.language,
                   u.bot_name, u.religion, u.favourite_topics,
                   u.music_preferences, u.city, u.morning_checkin_time,
                   u.afternoon_checkin_time, u.evening_checkin_time
            FROM users u
            WHERE u.onboarding_complete = 1
              AND u.{time_column} = ?
              AND NOT EXISTS (
                  SELECT 1 FROM ritual_log rl
                  WHERE rl.user_id = u.user_id
                    AND rl.ritual_type = ?
                    AND rl.sent_date = ?
              )
            """,
            (now_hhmm, ritual_type, today),
        ).fetchall()


def _mark_ritual_sent(user_id: int, ritual_type: str, today: str) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO ritual_log (user_id, ritual_type, sent_date)
                VALUES (?, ?, ?)
                """,
                (user_id, ritual_type, today),
            )
            conn.commit()
    except Exception as e:
        logger.warning("RITUALS | mark_ritual_sent failed | user_id=%s | %s", user_id, e)


# ---------------------------------------------------------------------------
# Message content — generated via DeepSeek
# ---------------------------------------------------------------------------

def _address(name: Optional[str], salutation: Optional[str]) -> str:
    name = (name or "aap").strip()
    sal = (salutation or "").strip()
    return f"{name} {sal}".strip() if sal else name


def _build_morning_instruction(user_row) -> str:
    name = _address(user_row["name"], user_row["preferred_salutation"])
    religion = user_row["religion"] or ""
    topics = user_row["favourite_topics"] or ""
    city = user_row["city"] or ""
    ist = _ist_now()
    day_name = ist.strftime("%A")
    date_str = ist.strftime("%d %B %Y")

    return (
        f"[MORNING_BRIEFING] Generate a warm morning message for {name}. "
        f"Today is {day_name}, {date_str}{', ' + city if city else ''}. "
        f"Include: (1) a warm personalised greeting using their name, "
        f"(2) one short 'thought for the day' — a verse, proverb, or reflection "
        f"appropriate for their faith/interests "
        f"({'religion: ' + religion if religion else 'keep it universal'}), "
        f"(3) one warm specific question to open the day "
        f"({'related to: ' + topics if topics else 'about their morning'}). "
        f"Keep the total message to 4–5 sentences. Warm, not formal."
    )


def _build_afternoon_instruction(user_row) -> str:
    name = _address(user_row["name"], user_row["preferred_salutation"])
    return (
        f"[AFTERNOON_CHECKIN] Generate a warm afternoon check-in for {name}. "
        f"One or two sentences — ask how their day is going or invite them to share "
        f"something small. Keep it light and warm, not clinical."
    )


def _build_evening_instruction(user_row) -> str:
    name = _address(user_row["name"], user_row["preferred_salutation"])
    return (
        f"[EVENING_REFLECTION] Generate a warm evening reflection prompt for {name}. "
        f"Ask about one good thing from their day — however small. "
        f"Two sentences maximum. Warm and unhurried."
    )


# ---------------------------------------------------------------------------
# Send helpers
# ---------------------------------------------------------------------------

async def _send_ritual(bot, row, ritual_type: str) -> None:
    from deepseek import call_deepseek
    from tts import text_to_speech

    user_id = row["user_id"]
    language = row["language"] or "hindi"

    user_context = {
        "user_id":           user_id,
        "name":              row["name"],
        "bot_name":          row["bot_name"],
        "persona":           None,
        "language":          language,
        "city":              row["city"],
        "spouse_name":       None,
        "religion":          row["religion"],
        "health_sensitivities": None,
        "music_preferences": row["music_preferences"],
        "favourite_topics":  row["favourite_topics"],
        "family_members":    None,
    }

    if ritual_type == "morning":
        instruction = _build_morning_instruction(row)
    elif ritual_type == "afternoon":
        instruction = _build_afternoon_instruction(row)
    else:
        instruction = _build_evening_instruction(row)

    reply = call_deepseek(instruction, user_context)

    # Send text
    await bot.send_message(chat_id=user_id, text=reply)
    logger.info("RITUALS | sent | user_id=%s | type=%s", user_id, ritual_type)

    # Send TTS voice note (failure is non-fatal — text already delivered)
    try:
        audio_bytes = text_to_speech(reply, user_language=language)
        await bot.send_voice(chat_id=user_id, voice=io.BytesIO(audio_bytes))
    except Exception as tts_err:
        logger.warning("RITUALS | TTS failed | user_id=%s | %s", user_id, tts_err)


# ---------------------------------------------------------------------------
# Adaptive learning — nudge morning_checkin_time toward actual behaviour
# ---------------------------------------------------------------------------

def _hhmm_to_minutes(hhmm: Optional[str]) -> Optional[int]:
    """'08:30' → 510 (minutes since midnight). None if unparseable."""
    if not hhmm:
        return None
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _minutes_to_hhmm(minutes: int) -> str:
    minutes = max(0, min(23 * 60 + 59, minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _run_adaptation_pass() -> None:
    """
    For each user:
    - Must have 7+ days of activity data.
    - Must not have been adapted in the last 7 days.
    - Calculate average first_message_hour across all recorded days.
    - If the average differs from morning_checkin_time by more than 15 min,
      nudge morning_checkin_time toward it — capped at 30 min per week.
    """
    try:
        with get_connection() as conn:
            # Find users ready for adaptation: 7+ data points, 7+ days since last adapt
            candidates = conn.execute(
                """
                SELECT u.user_id, u.morning_checkin_time, u.last_adapted_at,
                       AVG(ap.first_message_hour) AS avg_hour,
                       COUNT(ap.id)               AS data_points
                FROM users u
                JOIN user_activity_patterns ap ON ap.user_id = u.user_id
                WHERE u.onboarding_complete = 1
                  AND u.morning_checkin_time IS NOT NULL
                  AND (
                      u.last_adapted_at IS NULL
                      OR datetime(u.last_adapted_at) <= datetime('now', '-7 days')
                  )
                GROUP BY u.user_id
                HAVING data_points >= 7
                """
            ).fetchall()

        for row in candidates:
            _adapt_user(row["user_id"], row["morning_checkin_time"], row["avg_hour"])

    except Exception as e:
        logger.error("RITUALS | adaptation pass failed: %s", e)


def _adapt_user(user_id: int, current_hhmm: str, avg_hour: float) -> None:
    """Nudge morning_checkin_time toward avg_hour by at most 30 min."""
    current_min = _hhmm_to_minutes(current_hhmm)
    if current_min is None:
        return

    # Convert avg_hour (float, e.g. 8.5 = 8:30) to minutes
    target_min = int(round(avg_hour * 60))
    delta = target_min - current_min

    if abs(delta) < 15:
        # Within 15 minutes — close enough, don't nudge
        update_user_fields(user_id, last_adapted_at=datetime.now(IST).isoformat())
        return

    # Cap nudge at 30 minutes in either direction
    nudge = max(-30, min(30, delta))
    new_min = current_min + nudge
    new_hhmm = _minutes_to_hhmm(new_min)

    update_user_fields(
        user_id,
        morning_checkin_time=new_hhmm,
        last_adapted_at=datetime.now(IST).isoformat(),
    )
    logger.info(
        "RITUALS | adapted | user_id=%s | %s → %s (target ~%s, nudge=%+d min)",
        user_id, current_hhmm, new_hhmm,
        _minutes_to_hhmm(target_min), nudge,
    )


# ---------------------------------------------------------------------------
# Main scheduler entry point — called every minute from main.py
# ---------------------------------------------------------------------------

async def check_and_send_rituals(bot) -> None:
    """
    Scheduler tick (every 60 seconds).

    1. Send morning/afternoon/evening rituals to users whose time matches now.
    2. Once daily at 00:05 IST, run the adaptive learning pass.
    """
    now_hhmm = _current_hhmm()
    today = _current_date()

    for ritual_type in ("morning", "afternoon", "evening"):
        for row in _get_users_due_for_ritual(ritual_type, now_hhmm, today):
            try:
                await _send_ritual(bot, row, ritual_type)
                _mark_ritual_sent(row["user_id"], ritual_type, today)
            except Exception as e:
                logger.error(
                    "RITUALS | send failed | user_id=%s | type=%s | %s",
                    row["user_id"], ritual_type, e,
                )

    # Adaptive learning — run once per day at 00:05 IST
    if now_hhmm == "00:05":
        _run_adaptation_pass()
