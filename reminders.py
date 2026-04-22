"""
Module 11 — Medicine Reminders + Family Escalation

Public interface:
    add_reminder(user_id, medicine_name, time_str, frequency)
    get_due_reminders() -> list
    mark_reminder_sent(reminder_id)
    mark_reminder_acknowledged(user_id) -> bool
    get_unacknowledged_for_escalation() -> list
    mark_family_alerted(reminder_id)
    is_acknowledgement(text) -> bool
    build_reminder_text(name, salutation, medicine_name, language) -> str
    seed_reminders_from_raw(user_id, medicines_raw) -> int
    generate_bell_tone() -> bytes
    check_and_send_reminders(bot)   ← called every minute by JobQueue

All scheduled times are stored and compared in IST (UTC+5:30).
"""

from __future__ import annotations

import io
import math
import re
import struct
import wave
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from database import get_connection

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Bell tone — synthesized with Python stdlib only, no audio library needed.
# A C5 sine wave (523 Hz) with exponential decay sounds like a soft bell.
# Sent as Telegram audio before the voice reminder.
# ---------------------------------------------------------------------------

def generate_bell_tone(duration: float = 1.8, freq: float = 523.25) -> bytes:
    """Return WAV bytes of a soft bell-like tone (C5, ~1.8 s, exponential decay)."""
    sample_rate = 22050
    n_samples = int(sample_rate * duration)
    buf = io.BytesIO()

    with wave.open(buf, "w") as wav_file:
        wav_file.setnchannels(1)   # mono
        wav_file.setsampwidth(2)   # 16-bit PCM
        wav_file.setframerate(sample_rate)

        frames = bytearray()
        for i in range(n_samples):
            t = i / sample_rate
            # Fundamental + harmonics, exponential decay
            env = math.exp(-2.5 * t)
            sample = (
                env * 0.55 * math.sin(2 * math.pi * freq * t) +
                env * 0.25 * math.sin(2 * math.pi * freq * 2 * t) +
                env * 0.10 * math.sin(2 * math.pi * freq * 3 * t) +
                env * 0.05 * math.sin(2 * math.pi * freq * 4 * t)
            )
            value = max(-32767, min(32767, int(sample * 32767)))
            frames += struct.pack("<h", value)

        wav_file.writeframes(bytes(frames))

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Acknowledgement detection
# ---------------------------------------------------------------------------

_ACK_EXACT = {
    "👍", "✅", "🙏",
    "yes", "haan", "ha", "han", "haa", "bilkul", "okay", "ok",
    "done", "ack", "received", "theek hai", "thik hai", "ho gaya",
    "hogaya", "kar li", "le li", "le liya", "kha li", "kha liya",
    "pi li", "pi liya", "dawai le li", "dawai kha li", "le li dawai",
    "kha li dawai",
}

_ACK_SUBSTRINGS = (
    "le li", "kha li", "le liya", "kha liya", "ho gaya",
    "kar li", "pi li", "pi liya",
)


def is_acknowledgement(text: str) -> bool:
    """Return True if the message looks like a medicine reminder acknowledgement."""
    t = text.strip().lower()
    if t in _ACK_EXACT:
        return True
    if "👍" in text or "✅" in text:
        return True
    if len(t) <= 25 and any(sub in t for sub in _ACK_SUBSTRINGS):
        return True
    return False


# ---------------------------------------------------------------------------
# Reminder text builder
# ---------------------------------------------------------------------------

_TEMPLATES = {
    "hindi": (
        "{address}, aapki *{medicine}* ki dawai ka waqt ho gaya hai. 🙏\n"
        "Lene ke baad ek 👍 bhej dijiye — bas itna kaafi hai."
    ),
    # Real Hinglish — English nouns + Hindi connectors. No 'dawai ka waqt'.
    # Written 22 Apr 2026; previously this slot was a copy of the Hindi template.
    "hinglish": (
        "{address}, it's time for your *{medicine}*. 🙏\n"
        "Le lijiye aur ek 👍 bhej dijiye — bas itna hi."
    ),
    "english": (
        "{address}, it's time for your *{medicine}*. 🙏\n"
        "Just send a 👍 once you've taken it — that's all I need."
    ),
}
# Safety-net default: English (the one language we can be reasonably sure a
# pilot user understands even if language wasn't captured cleanly). Previously
# defaulted to Hindi, which is why Rishi — who picked English but whose row
# stored the unparseable 'eng' — got Hindi medicine reminders.
_DEFAULT_TEMPLATE = _TEMPLATES["english"]


def build_reminder_text(
    name: str,
    salutation: Optional[str],
    medicine_name: str,
    language: str = "hindi",
) -> str:
    """Build a personalised, warm medicine reminder message."""
    sal = (salutation or "").strip()
    address = f"{name} {sal}".strip() if sal else (name or "aap")
    template = _TEMPLATES.get((language or "hindi").lower(), _DEFAULT_TEMPLATE)
    return template.format(address=address, medicine=medicine_name)


# ---------------------------------------------------------------------------
# Time utilities
# ---------------------------------------------------------------------------

_TIME_ALIASES = {
    "morning":   "08:00",
    "subah":     "08:00",
    "breakfast": "08:00",
    "afternoon": "13:00",
    "dopahar":   "13:00",
    "lunch":     "13:00",
    "evening":   "18:00",
    "shaam":     "18:00",
    "dinner":    "20:00",
    "night":     "21:00",
    "raat":      "21:00",
    "bedtime":   "21:30",
}

# Accept both ':' and '.' as hour/minute separators — Indian users commonly
# type '11.07 am'. 22 Apr 2026: previously only ':' matched, so '11.07 am'
# was parsed as 11:00 (minutes silently dropped).
_TIME_RE = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?", re.IGNORECASE)


def _normalize_time(time_str: str) -> Optional[str]:
    """Convert a free-form time string to 'HH:MM' (24-hour IST). None if unparseable."""
    t = time_str.strip().lower()
    for alias, hhmm in _TIME_ALIASES.items():
        if alias in t:
            return hhmm
    m = _TIME_RE.search(t)
    if not m:
        return None
    hour, minute, period = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if period == "pm" and hour != 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _ist_now() -> datetime:
    return datetime.now(IST)


def _current_hhmm() -> str:
    return _ist_now().strftime("%H:%M")


def _current_date() -> str:
    return _ist_now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# add_reminder
# ---------------------------------------------------------------------------

def add_reminder(
    user_id: int,
    medicine_name: str,
    time_str: str,
    frequency: str = "daily",
) -> Optional[int]:
    """
    Insert a medicine reminder. time_str can be anything readable
    ("8am", "morning", "21:00", "night", etc.).

    Returns the new row id, or None if the time could not be parsed.
    """
    hhmm = _normalize_time(time_str)
    if not hhmm:
        logger.warning(
            "REMINDER | unparseable time %r for %r (user_id=%s)",
            time_str, medicine_name, user_id,
        )
        return None

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO medicine_reminders
                (user_id, medicine_name, schedule_time, days_of_week, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (user_id, medicine_name.strip().title(), hhmm, frequency),
        )
        conn.commit()
        rid = cursor.lastrowid

    logger.info(
        "REMINDER | added | user_id=%s | medicine=%r | time=%s | id=%s",
        user_id, medicine_name, hhmm, rid,
    )
    return rid


# ---------------------------------------------------------------------------
# get_due_reminders
# ---------------------------------------------------------------------------

def get_due_reminders() -> list:
    """
    Return active reminders that are due to be sent now.

    Two cases are returned:
    1. Initial send — schedule_time matches current IST minute and not sent today yet.
    2. Retry send — already sent today, not acknowledged, 30+ minutes since last send,
       and fewer than 3 attempts have been made (attempts 1 and 2 are retries).
    """
    now_hhmm = _current_hhmm()
    today = _current_date()

    with get_connection() as conn:
        return conn.execute(
            """
            SELECT r.id, r.user_id, r.medicine_name, r.schedule_time,
                   r.reminder_attempt,
                   u.name, u.preferred_salutation, u.language, u.bot_name
            FROM medicine_reminders r
            JOIN users u ON u.user_id = r.user_id
            WHERE r.is_active = 1
              AND COALESCE(u.account_status, 'active') = 'active'
              AND (
                  -- Initial send: scheduled minute reached, not yet sent today
                  (r.schedule_time = ?
                   AND (r.last_sent_at IS NULL OR date(r.last_sent_at) < ?))
                  OR
                  -- Retry: sent today, unacknowledged, 30+ min ago, under 3 attempts
                  (date(r.last_sent_at) = ?
                   AND (r.last_acked_at IS NULL OR r.last_acked_at < r.last_sent_at)
                   AND datetime(r.last_sent_at) <= datetime('now', '-30 minutes')
                   AND r.reminder_attempt < 3)
              )
            """,
            (now_hhmm, today, today),
        ).fetchall()


# ---------------------------------------------------------------------------
# mark_* helpers
# ---------------------------------------------------------------------------

def mark_reminder_sent(reminder_id: int) -> None:
    """
    Record that a reminder was sent. Tracks attempt count:
    - First send of the day: resets reminder_attempt to 1.
    - Subsequent sends (retries): increments reminder_attempt.
    """
    today = _current_date()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT last_sent_at, reminder_attempt FROM medicine_reminders WHERE id = ?",
            (reminder_id,),
        ).fetchone()
        if row and row["last_sent_at"] and row["last_sent_at"][:10] == today:
            # Same-day retry — increment attempt count
            new_attempt = (row["reminder_attempt"] or 0) + 1
        else:
            # First send of the day
            new_attempt = 1
        conn.execute(
            """
            UPDATE medicine_reminders
            SET last_sent_at = datetime('now'),
                reminder_attempt = ?
            WHERE id = ?
            """,
            (new_attempt, reminder_id),
        )
        conn.commit()


def mark_reminder_acknowledged(user_id: int) -> bool:
    """
    Mark the most recently sent, unacknowledged reminder for this user.
    Only matches reminders sent in the last 2 hours.
    Returns True if one was found and marked.
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, ack_streak FROM medicine_reminders
            WHERE user_id = ?
              AND last_sent_at IS NOT NULL
              AND (last_acked_at IS NULL OR last_acked_at < last_sent_at)
              AND datetime(last_sent_at) >= datetime('now', '-2 hours')
            ORDER BY last_sent_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        if not row:
            return False

        conn.execute(
            """
            UPDATE medicine_reminders
            SET last_acked_at    = datetime('now'),
                ack_streak       = ?,
                miss_streak      = 0,
                reminder_attempt = 0
            WHERE id = ?
            """,
            (row["ack_streak"] + 1, row["id"]),
        )
        conn.commit()
    return True


def get_unacknowledged_for_escalation() -> list:
    """
    Return reminders ready for family escalation.

    All three conditions must be met:
    1. All 3 attempts have been sent (reminder_attempt >= 3).
    2. Still unacknowledged after the 3rd attempt, and 30+ min since last send.
    3. User has explicitly opted in to family escalation (escalation_opted_in = 1).

    Family recipient selection (fix 19 Apr 2026):
    The original JOIN restricted to `is_setup_user = 1`, which is only set by
    child-led onboarding. Self-setup users save their emergency contact with
    role='emergency' (no is_setup_user flag), and family members who /join
    later have role='family' (also no is_setup_user flag). Both paths were
    silently dropped from the escalation query.

    New rule: pick any family_member with a non-null telegram_user_id,
    preferring is_setup_user first, then role='family' (joined via code),
    then role='emergency'.
    """
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT r.id, r.user_id, r.medicine_name,
                   u.name, u.preferred_salutation,
                   fm.telegram_user_id AS family_telegram_id,
                   fm.name             AS family_name
            FROM medicine_reminders r
            JOIN users u ON u.user_id = r.user_id
            LEFT JOIN family_members fm
                   ON fm.id = (
                       SELECT id FROM family_members
                       WHERE user_id = r.user_id
                         AND telegram_user_id IS NOT NULL
                       ORDER BY
                           CASE
                               WHEN is_setup_user = 1 THEN 0
                               WHEN role = 'family'  THEN 1
                               WHEN role = 'emergency' THEN 2
                               ELSE 3
                           END,
                           id
                       LIMIT 1
                   )
            WHERE r.is_active = 1
              AND COALESCE(u.account_status, 'active') = 'active'
              AND r.last_sent_at IS NOT NULL
              AND r.reminder_attempt >= 3
              AND (r.last_acked_at IS NULL OR r.last_acked_at < r.last_sent_at)
              AND (r.family_alerted_at IS NULL OR r.family_alerted_at < r.last_sent_at)
              AND datetime(r.last_sent_at) <= datetime('now', '-30 minutes')
              AND u.escalation_opted_in = 1
            """,
        ).fetchall()


def mark_family_alerted(reminder_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE medicine_reminders
            SET family_alerted_at = datetime('now'),
                miss_streak = miss_streak + 1
            WHERE id = ?
            """,
            (reminder_id,),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# seed_reminders_from_raw — parse medicines_raw from onboarding
# ---------------------------------------------------------------------------

_TIME_TOKEN_RE = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)|"
    r"morning|subah|afternoon|dopahar|evening|shaam|"
    r"night|raat|bedtime|lunch|dinner|breakfast)\b",
    re.IGNORECASE,
)

_MED_NAME_RE = re.compile(
    r"^([a-zA-Z][a-zA-Z\s\-]{1,30}?)"
    r"(?=\s+(?:\d|\bat\b|morning|subah|evening|shaam|night|raat|afternoon|dopahar|lunch|dinner|breakfast|bedtime))",
    re.IGNORECASE,
)


_MED_PARSE_PROMPT = """You are parsing a senior's free-text answer about their medicines into structured data.

The answer may include:
- Affirmations ("yes", "sure", "haan") that should be ignored
- Multiple medicines separated by "and", commas, or just spaces
- Each medicine has one or more times (e.g. "8am", "after dinner", "morning")
- Two medicines can share a time ("plavix and pan D at 8 AM" = both at 8 AM)
- Times may be vague ("morning", "after dinner", "before bed", "raat")

Return a JSON array of {{"name": "...", "time": "..."}} pairs. One entry per (medicine, time) combination.
Use the senior's spelling for the medicine name (proper-case it).
Keep the time in the senior's words (e.g. "8 AM", "after dinner", "morning").
If no medicine + time can be extracted, return [].

Examples:
INPUT: "yes. plavix and pan D at 8 AM and Rosouvastatin after dinner"
OUTPUT: [{{"name": "Plavix", "time": "8 AM"}}, {{"name": "Pan D", "time": "8 AM"}}, {{"name": "Rosouvastatin", "time": "after dinner"}}]

INPUT: "metformin 8am and 8pm, atorvastatin at night"
OUTPUT: [{{"name": "Metformin", "time": "8 AM"}}, {{"name": "Metformin", "time": "8 PM"}}, {{"name": "Atorvastatin", "time": "at night"}}]

INPUT: "no medicines"
OUTPUT: []

Now parse this answer. Return ONLY the JSON array, no other text:
INPUT: "{raw}"
OUTPUT:"""


def _deepseek_parse_medicines(medicines_raw: str) -> list[tuple[str, str]]:
    """
    Parse medicine text via DeepSeek into [(name, time), ...] pairs.
    Returns [] on any failure — caller should fall back to regex parser.
    """
    try:
        from deepseek import _get_client
        import json

        prompt = _MED_PARSE_PROMPT.format(raw=medicines_raw.replace('"', "'"))
        response = _get_client().chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=400,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown code fence if DeepSeek wraps the JSON
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        out = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            time = str(item.get("time", "")).strip()
            if name and time:
                out.append((name.title(), time))
        return out
    except Exception as e:
        logger.warning("REMINDER | DeepSeek parse failed, will fall back to regex: %s", e)
        return []


def _regex_parse_medicines(medicines_raw: str) -> list[tuple[str, str]]:
    """Fallback regex-based parser (legacy behavior)."""
    out = []
    entries = re.split(r"[,;]", medicines_raw)
    for entry in entries:
        entry = entry.strip()
        if not entry or entry.lower() in ("no", "none", "nahi", "nil"):
            continue
        times = _TIME_TOKEN_RE.findall(entry)
        if not times:
            continue
        med_match = _MED_NAME_RE.match(entry)
        if med_match:
            med_name = med_match.group(1).strip().title()
        else:
            words = re.split(r"\s+\d", entry, maxsplit=1)[0].strip().split()
            med_name = " ".join(words[:2]).title() if words else "Medicine"
        for time_str in times:
            out.append((med_name, time_str))
    return out


def seed_reminders_from_raw(user_id: int, medicines_raw: str) -> int:
    """
    Parse free-form medicine text from onboarding (stored in users.medicines_raw)
    and create rows in medicine_reminders.

    Primary parser is DeepSeek (handles affirmations, multi-medicine "and"
    splits, shared times). Falls back to regex if DeepSeek fails or returns
    nothing.

    Returns the number of reminders successfully created.
    """
    if not medicines_raw or medicines_raw.strip().lower() in (
        "no", "none", "nahi", "nil", "no.", "skip"
    ):
        return 0

    pairs = _deepseek_parse_medicines(medicines_raw)
    if not pairs:
        logger.info("REMINDER | falling back to regex parser for user_id=%s", user_id)
        pairs = _regex_parse_medicines(medicines_raw)

    count = 0
    for med_name, time_str in pairs:
        rid = add_reminder(user_id, med_name, time_str)
        if rid:
            count += 1

    logger.info(
        "REMINDER | seeded %d reminders for user_id=%s | pairs=%s",
        count, user_id, pairs,
    )
    return count


def _seed_pending_users() -> None:
    """Seed reminders for users who have medicines_raw but no active reminders yet."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT u.user_id, u.medicines_raw
            FROM users u
            WHERE u.medicines_raw IS NOT NULL
              AND u.medicines_raw != ''
              AND NOT EXISTS (
                  SELECT 1 FROM medicine_reminders r
                  WHERE r.user_id = u.user_id AND r.is_active = 1
              )
            """,
        ).fetchall()

    for row in rows:
        try:
            seed_reminders_from_raw(row["user_id"], row["medicines_raw"])
        except Exception as e:
            logger.error("REMINDER | seed failed | user_id=%s | %s", row["user_id"], e)


# ---------------------------------------------------------------------------
# Send helpers (async — used by the scheduler)
# ---------------------------------------------------------------------------

async def _send_reminder(bot, row) -> None:
    """Send bell tone + text + TTS voice to the senior."""
    user_id = row["user_id"]
    name = row["name"] or "aap"
    salutation = row["preferred_salutation"] or ""
    medicine = row["medicine_name"]
    language = row["language"] or "hindi"

    # 1. Bell tone (WAV audio)
    bell = generate_bell_tone()
    await bot.send_audio(
        chat_id=user_id,
        audio=io.BytesIO(bell),
        filename="reminder.wav",
        title="🔔",
        performer="Saathi",
    )

    # 2. Text reminder
    text = build_reminder_text(name, salutation, medicine, language)
    await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")

    # 3. TTS voice note
    try:
        from tts import text_to_speech
        plain = text.replace("*", "")
        audio_bytes = text_to_speech(plain, user_language=language)
        await bot.send_voice(chat_id=user_id, voice=io.BytesIO(audio_bytes))
    except Exception as tts_err:
        logger.warning("REMINDER | TTS failed | user_id=%s | %s", user_id, tts_err)

    logger.info("REMINDER | sent | user_id=%s | medicine=%r", user_id, medicine)


async def _escalate_to_family(bot, row) -> bool:
    """
    Send a warm alert to the family member's Telegram account.
    Returns True if the message was sent, False if skipped or failed.
    The bool is used to gate mark_family_alerted — we must not stamp
    family_alerted_at when no alert actually went through, or the
    reminder will be excluded from the next escalation tick.
    """
    family_id = row["family_telegram_id"]
    if not family_id:
        logger.warning(
            "REMINDER | escalation skipped — no family telegram_id | user_id=%s | "
            "(family member has not /joined via linking code)",
            row["user_id"],
        )
        return False

    name = row["name"] or "aapke ghar ka"
    sal = (row["preferred_salutation"] or "").strip()
    address = f"{name} {sal}".strip() if sal else name
    medicine = row["medicine_name"]
    family_name = row["family_name"] or ""

    alert = (
        f"Namaste{' ' + family_name if family_name else ''}! 🙏\n\n"
        f"*{address}* ko *{medicine}* ki dawai ke liye teen baar reminder bheja — "
        f"abhi tak koi jawab nahi aaya.\n\n"
        f"Ek baar unhe check kar lein. Shukriya! 💙"
    )

    try:
        await bot.send_message(chat_id=family_id, text=alert, parse_mode="Markdown")
        logger.info(
            "REMINDER | family alerted | user_id=%s | family_id=%s",
            row["user_id"], family_id,
        )
        return True
    except Exception as e:
        logger.error(
            "REMINDER | family alert failed | family_id=%s | %s", family_id, e,
        )
        return False


# ---------------------------------------------------------------------------
# check_and_send_reminders — called every minute by the JobQueue
# ---------------------------------------------------------------------------

async def check_and_send_reminders(bot) -> None:
    """
    Main scheduler tick. Runs every minute via main.py job_queue.

    1. Seed any users with medicines_raw but no structured reminders yet.
    2. Send all reminders due at the current IST minute.
    3. Escalate to family any reminder unacknowledged for >30 minutes.
    """
    # Seed users whose medicines haven't been structured yet
    _seed_pending_users()

    # Send due reminders
    for row in get_due_reminders():
        try:
            await _send_reminder(bot, row)
            mark_reminder_sent(row["id"])
        except Exception as e:
            logger.error("REMINDER | send failed | id=%s | %s", row["id"], e)

    # Escalate unacknowledged reminders to family
    for row in get_unacknowledged_for_escalation():
        try:
            sent = await _escalate_to_family(bot, row)
            # Only stamp family_alerted_at when the alert actually went through.
            # If we stamp on a skip, the reminder is excluded from future escalation
            # ticks and silently stops retrying — a senior could miss medicine
            # family-wide without anyone knowing.
            if sent:
                mark_family_alerted(row["id"])
        except Exception as e:
            logger.error("REMINDER | escalation failed | id=%s | %s", row["id"], e)
