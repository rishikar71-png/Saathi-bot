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
    seed_reminders_from_raw(user_id, medicines_raw) -> dict (report)
    add_reminder_structured(user_id, name, time_str) -> (row_id, parse_result)
    resolve_reminder_time(reminder_id, hhmm) -> bool
    get_ambiguous_reminders(user_id) -> list[dict]
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
    """Build a personalised, warm medicine reminder message.

    Addressing follows Batch 1c semantics (23 Apr 2026):
      • preferred_salutation is the FULL display string ("Ma", "Rameshji").
        If set, use it verbatim.
      • Else fall back to "{name} Ji" — respectful Indian default.
      • Else "aap".
    The previous `f"{name} {sal}".strip()` treated salutation as a suffix and
    produced "Durga Ma" when salutation='Ma'. See rituals._address() for the
    canonical helper.
    """
    sal = (salutation or "").strip()
    nm = (name or "").strip()
    if sal:
        address = sal
    elif nm:
        address = f"{nm} Ji"
    else:
        address = "aap"
    template = _TEMPLATES.get((language or "hindi").lower(), _DEFAULT_TEMPLATE)
    return template.format(address=address, medicine=medicine_name)


# ---------------------------------------------------------------------------
# Time utilities — structured parser (23 Apr 2026, Option A rule set)
#
# Pilot-blocker: the old parser silently defaulted bare hours to AM, so
# "1.30" stored as 01:30 (middle of the night) when the senior clearly
# meant 1:30 PM (post-lunch). Pan D, BP pills, etc. are lunch/evening
# medicines in Indian households. See time_parser_review_memo_23apr.md.
#
# Rules (locked after GPT + Gemini external review):
#   • Bare hour 1–5 (no AM/PM, no Hindi period word) → 13:00–17:00 PM.
#   • Bare hour 6–11 (no AM/PM, no Hindi period word) → AMBIGUOUS → ASK.
#   • Bare hour 12 → 12:00 noon (never midnight for medicines).
#   • Explicit AM/PM → honored verbatim.
#   • 24-hour format (13:30, 21:00) → honored verbatim.
#   • Hindi period words resolve ambiguity:
#        subah / morning / breakfast        → AM context (1–11 → 01–11)
#        dopahar / afternoon / lunch        → PM 12–17 context
#        shaam / evening                    → PM 17–19 context
#        raat / night                       → PM 19–23 context
#   • Meal references ("after dinner", "lunch ke baad") → scripted times.
#   • Standalone period words ("morning", "raat", "bedtime") → scripted.
# ---------------------------------------------------------------------------

# Standalone period words — used when no digit is present at all.
# Maps to a single canonical time. These are high-confidence because the
# phrase itself is the intent ("morning" = 8am).
_PERIOD_WORD_TIMES = {
    "morning":   "08:00",
    "subah":     "08:00",
    "breakfast": "09:00",
    "afternoon": "13:00",
    "dopahar":   "13:00",
    "lunch":     "13:00",
    "evening":   "18:00",
    "shaam":     "18:00",
    "dinner":    "20:00",
    "night":     "21:00",
    "raat":      "21:00",
    "bedtime":   "21:30",
    "noon":      "12:00",
    "khali pet": "07:30",   # empty stomach — typical early-morning dose
}

# Meal-linked phrases — explicit reference to before/after a meal.
# High-confidence scripted times; no further AM/PM disambiguation needed.
_MEAL_PHRASE_TIMES = [
    # Order matters — longer phrases first to avoid partial matches.
    ("after breakfast",    "09:00"),
    ("before breakfast",   "07:30"),
    ("breakfast ke baad",  "09:00"),
    ("breakfast se pehle", "07:30"),

    ("after lunch",        "14:00"),
    ("before lunch",       "12:30"),
    ("lunch ke baad",      "14:00"),
    ("lunch se pehle",     "12:30"),
    ("khana ke baad",      "14:00"),   # ambiguous-ish but lunch is the common default
    ("khane ke baad",      "14:00"),

    ("after dinner",       "20:00"),
    ("before dinner",      "19:30"),
    ("dinner ke baad",     "20:00"),
    ("dinner se pehle",    "19:30"),
    ("raat ke khane baad", "20:00"),

    ("post dinner",        "20:00"),
    ("post lunch",         "14:00"),
    ("post breakfast",     "09:00"),

    ("khane se pehle",     "12:30"),   # before-meal default = pre-lunch
    ("bhojan ke baad",     "14:00"),
    ("bhojan se pehle",    "12:30"),
]

# Hindi / English period qualifiers that *precede* a digit to disambiguate
# bare hours. "subah 9" → AM context, "raat 9" → PM context.
# The same word may appear as part of _PERIOD_WORD_TIMES (when it stands
# alone) or here (when it modifies a following digit).
_AM_QUALIFIERS = ("subah", "morning", "am", "a.m.", "a m", "savere", "savera")
_PM_QUALIFIERS_NOON  = ("dopahar", "afternoon", "noon")                   # → 12–16
_PM_QUALIFIERS_EVE   = ("shaam", "evening")                               # → 16–19
_PM_QUALIFIERS_NIGHT = ("raat", "night", "pm", "p.m.", "p m")             # → 18–23

# Accept both ':' and '.' as hour/minute separators — Indian users commonly
# type '11.07 am'. Also accept no separator with 3–4 digits (e.g. "130" = 1:30).
_TIME_RE = re.compile(
    r"(?<!\d)(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?!\d)",
    re.IGNORECASE,
)
# Compact "130" / "1930" form — 3 or 4 bare digits, no separator.
_COMPACT_TIME_RE = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")


def _result(
    time_24h: Optional[str],
    *,
    ambiguous: bool,
    confidence: str,
    source: str,
    reason: str,
) -> dict:
    """Shape the parser's structured return."""
    return {
        "time_24h":   time_24h,
        "ambiguous":  ambiguous,
        "confidence": confidence,   # "high" | "low" | "none"
        "source":     source,
        "reason":     reason,
    }


def _valid(hour: int, minute: int) -> bool:
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _word_or_substring_in(tokens: tuple, text: str) -> bool:
    """
    True if any token appears in text. Short tokens (≤3 chars like "am", "pm",
    "a m", "p m") are matched with word boundaries to prevent "am" matching
    inside "shaam" / "subah am" etc. Longer tokens match as plain substrings
    (safe — "subah" doesn't collide with anything).
    """
    for tok in tokens:
        if not tok:
            continue
        if len(tok) <= 4:
            # word-boundary match — "am" must not match "shaam" or "naam"
            if re.search(rf"\b{re.escape(tok)}\b", text):
                return True
        else:
            if tok in text:
                return True
    return False


def _detect_period_qualifier(text: str) -> Optional[str]:
    """
    Return a qualifier label ('am', 'noon', 'evening', 'night') if the text
    contains a Hindi/English period word or meal-context word. None otherwise.

    Used to disambiguate bare hours like "subah 9" (AM) vs "raat 9" (PM),
    and to handle compounds like "after breakfast 8" (AM) vs "before dinner 7"
    (night).

    IMPORTANT: short tokens like "am"/"pm" are matched with word boundaries so
    they don't spuriously fire on "shaam", "naam", etc. The 22-Apr regression
    that stored "shaam 7" as 07:00 was caused by substring-matching "am".
    """
    t = text.lower()

    # Meal-context words imply a period even when a digit is also present.
    # e.g. "after breakfast 8" → AM context; "before dinner 7" → night context.
    # Checked BEFORE the short AM/PM tokens so "breakfast" → "am" wins.
    if "breakfast" in t:
        return "am"
    if "dinner" in t:
        return "night"

    # Night wins over evening when both present ("raat ko shaam" — rare).
    if _word_or_substring_in(_PM_QUALIFIERS_NIGHT, t):
        return "night"
    if _word_or_substring_in(_PM_QUALIFIERS_EVE, t):
        return "evening"
    if _word_or_substring_in(_PM_QUALIFIERS_NOON, t):
        return "noon"
    if _word_or_substring_in(_AM_QUALIFIERS, t):
        return "am"
    # "lunch" alone → noon (not covered above because "lunch" isn't in the
    # qualifier tuples; keeping those tuples clean).
    if "lunch" in t:
        return "noon"
    return None


def _apply_qualifier(hour: int, qualifier: str) -> int:
    """Shift bare hour into the correct half of the day given a period qualifier."""
    # AM: 1–11 stay as-is (01–11). 12 becomes 00.
    if qualifier == "am":
        if hour == 12:
            return 0
        return hour
    # Noon qualifier: 12–5 maps to 12–17 (after-lunch afternoon window).
    if qualifier == "noon":
        if 1 <= hour <= 5:
            return hour + 12
        if hour == 12:
            return 12
        if 6 <= hour <= 11:
            # "dopahar 11" is unusual but we honor it as 11 (late morning).
            return hour
        return hour
    # Evening: 4–7 → 16–19. 8+ stays (unusual).
    if qualifier == "evening":
        if 1 <= hour <= 11:
            return hour + 12 if hour <= 11 else hour
        return hour
    # Night: 6–11 → 18–23. 12 → 00. 1–5 → 13–17 (late night unusual,
    # but "raat 1" typically means 1am — return bare).
    if qualifier == "night":
        if 6 <= hour <= 11:
            return hour + 12
        if hour == 12:
            return 0
        # "raat 1" / "raat 2" — late-night hours stay as 01–05.
        return hour
    return hour


def _normalize_time(time_str: str) -> dict:
    """
    Parse a free-form time string into a structured result dict.

    Always returns a dict with the shape from _result(). Callers check
    ``ambiguous`` and ``time_24h`` — if ambiguous=True they should hold the
    row and ASK the senior (morning or night?). If time_24h is None and
    ambiguous=False, the input was unparseable.

    See module docstring for the full rule set.
    """
    raw = (time_str or "").strip()
    if not raw:
        return _result(None, ambiguous=False, confidence="none",
                       source="unparseable", reason="empty input")

    t = raw.lower()

    # --- 1. Detect a period qualifier (subah/raat/shaam/dopahar/AM/PM word,
    # breakfast/lunch/dinner meal-context). This feeds into steps 5 and 6
    # below when a digit is present.
    qualifier = _detect_period_qualifier(t)

    # --- 2. Try the canonical HH(:/.)MM(am/pm)? regex first. Explicit digit
    # beats pure meal-phrase: "after breakfast 8" → 08:00, not 09:00. ---
    m = _TIME_RE.search(t)
    digit_hour: Optional[int] = None
    digit_minute: int = 0
    has_explicit_ampm = False

    if m:
        digit_hour = int(m.group(1))
        digit_minute = int(m.group(2) or 0)
        period = (m.group(3) or "").lower().replace(".", "").replace(" ", "")
        has_explicit_ampm = period in ("am", "pm")
        if has_explicit_ampm:
            if period == "pm" and digit_hour != 12:
                digit_hour += 12
            elif period == "am" and digit_hour == 12:
                digit_hour = 0
            if not _valid(digit_hour, digit_minute):
                return _result(None, ambiguous=False, confidence="none",
                               source="unparseable",
                               reason=f"invalid explicit AM/PM time {raw!r}")
            return _result(
                f"{digit_hour:02d}:{digit_minute:02d}",
                ambiguous=False, confidence="high",
                source="explicit_ampm",
                reason=f"explicit {period.upper()} → {digit_hour:02d}:{digit_minute:02d}",
            )
        # No explicit AM/PM. Check for 24-hour format (hour ≥ 13 with minutes).
        # "13:30" / "21:00" → unambiguous.
        if m.group(2) is not None and digit_hour >= 13 and _valid(digit_hour, digit_minute):
            return _result(
                f"{digit_hour:02d}:{digit_minute:02d}",
                ambiguous=False, confidence="high",
                source="24hour",
                reason=f"24-hour format → {digit_hour:02d}:{digit_minute:02d}",
            )
        # Hour = 0 with minutes ("00:30") → unambiguous midnight-bucket.
        if digit_hour == 0 and m.group(2) is not None and _valid(digit_hour, digit_minute):
            return _result(
                f"{digit_hour:02d}:{digit_minute:02d}",
                ambiguous=False, confidence="high",
                source="24hour",
                reason="explicit 00:xx → midnight window",
            )

    # --- 4. If no digit found via primary regex, try compact form "130", "1930". ---
    if digit_hour is None:
        cm = _COMPACT_TIME_RE.search(t)
        if cm:
            num = cm.group(1)
            if len(num) == 4:
                # 4-digit compact is military 24-hour convention ("0800" = 8 AM,
                # "1930" = 7:30 PM). Always unambiguous if valid.
                digit_hour = int(num[:2])
                digit_minute = int(num[2:])
                if _valid(digit_hour, digit_minute):
                    return _result(
                        f"{digit_hour:02d}:{digit_minute:02d}",
                        ambiguous=False, confidence="high",
                        source="24hour",
                        reason=f"compact 24-hour {num} → {digit_hour:02d}:{digit_minute:02d}",
                    )
            elif len(num) == 3:
                # 3-digit compact is H:MM ("130" = 1:30, "930" = 9:30).
                # Falls through to bare-digit rules below.
                digit_hour = int(num[:1])
                digit_minute = int(num[1:])

    # --- 5. If qualifier present + digit present → apply qualifier. ---
    if digit_hour is not None and qualifier:
        shifted = _apply_qualifier(digit_hour, qualifier)
        if _valid(shifted, digit_minute):
            return _result(
                f"{shifted:02d}:{digit_minute:02d}",
                ambiguous=False, confidence="high",
                source="hindi_period",
                reason=f"qualifier={qualifier!r} + bare hour {digit_hour} → {shifted:02d}:{digit_minute:02d}",
            )

    # --- 6a. No digit: try meal-linked phrases first (more specific). ---
    if digit_hour is None:
        for phrase, hhmm in _MEAL_PHRASE_TIMES:
            if phrase in t:
                return _result(hhmm, ambiguous=False, confidence="high",
                               source="meal_phrase", reason=f"'{phrase}' → {hhmm}")

    # --- 6b. Standalone period word (no digit) — "morning", "raat", "bedtime". ---
    if digit_hour is None:
        for word, hhmm in _PERIOD_WORD_TIMES.items():
            if word in t:
                return _result(hhmm, ambiguous=False, confidence="high",
                               source="period_word", reason=f"'{word}' → {hhmm}")
        return _result(None, ambiguous=False, confidence="none",
                       source="unparseable",
                       reason=f"no digit or period word in {raw!r}")

    # --- 7. Bare digit with no qualifier — apply the 1–5 / 6–11 / 12 rule. ---
    if not _valid(digit_hour, digit_minute):
        return _result(None, ambiguous=False, confidence="none",
                       source="unparseable",
                       reason=f"invalid bare hour {digit_hour} in {raw!r}")

    if digit_hour == 12:
        # 12:xx with no AM/PM → NOON for medicine context. Never midnight.
        return _result(
            f"12:{digit_minute:02d}",
            ambiguous=False, confidence="high",
            source="bare_noon",
            reason=f"bare 12 → 12:{digit_minute:02d} (noon)",
        )

    if 1 <= digit_hour <= 5:
        # Indian medicine-timing default: post-lunch / pre-dinner window.
        shifted = digit_hour + 12
        return _result(
            f"{shifted:02d}:{digit_minute:02d}",
            ambiguous=False, confidence="high",
            source="bare_hour_pm_default",
            reason=(
                f"bare {digit_hour} → {shifted:02d}:{digit_minute:02d} PM "
                f"(post-lunch default; Indian medicine timing convention)"
            ),
        )

    if 6 <= digit_hour <= 11:
        # Genuine ambiguity — caller should hold row inactive (is_active=0)
        # and ASK. We still return time_24h as the bare HH:MM (AM form) so
        # the placeholder row has something to store; resolve_ambiguous_hour
        # will update it to the correct half-day once the senior clarifies.
        return _result(
            f"{digit_hour:02d}:{digit_minute:02d}",
            ambiguous=True, confidence="low",
            source="bare_hour_ambiguous",
            reason=(
                f"bare {digit_hour} → ambiguous (could be {digit_hour:02d}:{digit_minute:02d} AM "
                f"or {digit_hour + 12:02d}:{digit_minute:02d} PM)"
            ),
        )

    # 0 or >23 fell through somewhere — unreachable, but return a safe default.
    return _result(None, ambiguous=False, confidence="none",
                   source="unparseable",
                   reason=f"unreachable bare hour {digit_hour} in {raw!r}")


# ---------------------------------------------------------------------------
# Ambiguity-resolution parser — used by the batch ASK flow in pending_capture.
# Given the original ambiguous hour + the senior's reply ("morning" / "night"
# / "subah" / "PM"), return the resolved HH:MM.
# ---------------------------------------------------------------------------

_AM_REPLY_TOKENS = (
    "morning", "am", "a.m.", "a m", "subah", "savere", "savera",
    "breakfast time", "before lunch", "pehle",
)
_PM_REPLY_TOKENS = (
    "night", "pm", "p.m.", "p m", "raat", "evening", "shaam",
    "afternoon", "dopahar", "after lunch", "dinner",
)


def resolve_ambiguous_hour(
    original_hour: int,
    original_minute: int,
    reply_text: str,
) -> Optional[str]:
    """
    Caller passes the bare-hour digits that came back ambiguous, plus the
    senior's clarification reply. Returns an HH:MM string or None if the
    reply doesn't clearly pick morning vs. night.

    Short tokens ("am", "pm", "a m", "p m") are matched with word boundaries
    to prevent "am" from firing inside "shaam" / "naam".
    """
    if not (6 <= original_hour <= 11):
        return None
    rt = (reply_text or "").lower()
    is_am = _word_or_substring_in(_AM_REPLY_TOKENS, rt)
    is_pm = _word_or_substring_in(_PM_REPLY_TOKENS, rt)
    if is_am and not is_pm:
        return f"{original_hour:02d}:{original_minute:02d}"
    if is_pm and not is_am:
        return f"{original_hour + 12:02d}:{original_minute:02d}"
    return None


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

    Returns the new row id, or None if the time could not be parsed OR if
    the time came back AMBIGUOUS. Ambiguous times are not silently stored —
    the caller (seed_reminders_from_raw / pending_capture) is responsible
    for collecting ambiguous rows and asking a follow-up.

    Legacy callers that passed a string directly (and expect the row id)
    still work for unambiguous inputs; they simply get None on ambiguity.
    Use ``add_reminder_structured`` when you need the full parse result.
    """
    rid, _ = add_reminder_structured(user_id, medicine_name, time_str, frequency)
    return rid


def add_reminder_structured(
    user_id: int,
    medicine_name: str,
    time_str: str,
    frequency: str = "daily",
) -> tuple[Optional[int], dict]:
    """
    Same as ``add_reminder`` but also returns the structured parse result.
    Use from seed_reminders_from_raw / pending_capture so ambiguous rows
    can be collected for a follow-up ASK.

    Returns (row_id, parse_result). row_id is None if:
      • time_str unparseable (parse_result['source'] == 'unparseable'), or
      • time came back ambiguous (parse_result['ambiguous'] == True).

    In the ambiguous case we DO insert a placeholder row with schedule_time
    set to the bare hour in 'HH:MM' format AND is_active=0, so the caller
    has an id to update once the senior clarifies. The placeholder stays
    inactive until a valid HH:MM is written back via ``resolve_reminder_time``.
    """
    parse = _normalize_time(time_str)

    # Unparseable — don't insert anything.
    if parse["source"] == "unparseable":
        logger.warning(
            "REMINDER | unparseable time %r for %r (user_id=%s) | reason=%s",
            time_str, medicine_name, user_id, parse["reason"],
        )
        return (None, parse)

    # Ambiguous — insert an INACTIVE placeholder row with the bare hour
    # stored in schedule_time. ``is_active=0`` guarantees the scheduler
    # will NEVER fire it. The caller updates via resolve_reminder_time
    # once the senior picks AM or PM.
    if parse["ambiguous"]:
        # Extract bare hour + minute from the reason (safer than re-parsing).
        m = re.search(r"bare\s+(\d{1,2}).*?(\d{2}):(\d{2})\s*AM", parse["reason"])
        hour_bare = int(m.group(1)) if m else 0
        minute_bare = int(m.group(2)) if m else 0
        placeholder_hhmm = f"{hour_bare:02d}:{minute_bare:02d}"
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO medicine_reminders
                    (user_id, medicine_name, schedule_time, days_of_week, is_active)
                VALUES (?, ?, ?, ?, 0)
                """,
                (user_id, medicine_name.strip().title(), placeholder_hhmm, frequency),
            )
            conn.commit()
            rid = cursor.lastrowid
        logger.info(
            "REMINDER | ambiguous placeholder | user_id=%s | medicine=%r | bare=%s | id=%s",
            user_id, medicine_name, placeholder_hhmm, rid,
        )
        return (rid, parse)

    hhmm = parse["time_24h"]
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
        "REMINDER | added | user_id=%s | medicine=%r | time=%s | id=%s | source=%s",
        user_id, medicine_name, hhmm, rid, parse["source"],
    )
    return (rid, parse)


def resolve_reminder_time(reminder_id: int, hhmm: str) -> bool:
    """
    Activate an ambiguous-placeholder reminder by writing the resolved HH:MM
    and setting is_active=1. Returns True on success.
    """
    if not re.fullmatch(r"\d{2}:\d{2}", hhmm or ""):
        return False
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE medicine_reminders
            SET schedule_time = ?, is_active = 1
            WHERE id = ?
            """,
            (hhmm, reminder_id),
        )
        conn.commit()
    logger.info(
        "REMINDER | resolved ambiguous reminder id=%s → %s",
        reminder_id, hhmm,
    )
    return True


def get_ambiguous_reminders(user_id: int) -> list:
    """
    Return all inactive-placeholder reminders for the user (waiting for the
    senior to pick morning/night). Used by the batch-ASK follow-up in
    pending_capture.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, medicine_name, schedule_time
            FROM medicine_reminders
            WHERE user_id = ? AND is_active = 0
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


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


def seed_reminders_from_raw(user_id: int, medicines_raw: str) -> dict:
    """
    Parse free-form medicine text from onboarding (stored in users.medicines_raw)
    and create rows in medicine_reminders.

    Primary parser is DeepSeek (handles affirmations, multi-medicine "and"
    splits, shared times). Falls back to regex if DeepSeek fails or returns
    nothing.

    Returns a report dict:
        {
            "seeded_active": int,       # rows inserted with is_active=1
            "seeded_ambiguous": list,   # [{id, medicine_name, raw_time, bare_hhmm}, ...]
            "unparseable": list,        # [(medicine_name, raw_time), ...]
            "pairs_total": int,
        }

    The caller (pending_capture or onboarding) is responsible for:
      • If seeded_ambiguous is non-empty, send a follow-up asking "for
        {meds} — morning or night?" and then call resolve_reminder_time()
        for each id once the senior clarifies.
      • If unparseable is non-empty, include it in the ack so the senior
        knows which medicines weren't captured.
    """
    report = {
        "seeded_active": 0,
        "seeded_ambiguous": [],
        "unparseable": [],
        "pairs_total": 0,
    }

    if not medicines_raw or medicines_raw.strip().lower() in (
        "no", "none", "nahi", "nil", "no.", "skip"
    ):
        return report

    pairs = _deepseek_parse_medicines(medicines_raw)
    if not pairs:
        logger.info("REMINDER | falling back to regex parser for user_id=%s", user_id)
        pairs = _regex_parse_medicines(medicines_raw)
    report["pairs_total"] = len(pairs)

    for med_name, time_str in pairs:
        rid, parse = add_reminder_structured(user_id, med_name, time_str)
        if rid is None:
            # Unparseable — add_reminder_structured already logged a warning.
            report["unparseable"].append((med_name, time_str))
            continue
        if parse["ambiguous"]:
            # Placeholder row inserted (is_active=0). Surface for ASK flow.
            m = re.search(r"(\d{2}):(\d{2})\s*AM", parse["reason"])
            bare = f"{m.group(1)}:{m.group(2)}" if m else "??:??"
            report["seeded_ambiguous"].append({
                "id":            rid,
                "medicine_name": med_name.title(),
                "raw_time":      time_str,
                "bare_hhmm":     bare,
            })
        else:
            report["seeded_active"] += 1

    logger.info(
        "REMINDER | seeded user_id=%s | active=%d | ambiguous=%d | unparseable=%d | pairs=%s",
        user_id,
        report["seeded_active"],
        len(report["seeded_ambiguous"]),
        len(report["unparseable"]),
        pairs,
    )
    return report


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

    # Batch 1c addressing (23 Apr 2026): salutation verbatim, else "{name} Ji",
    # else the Hindi "household" fallback. See rituals._address().
    raw_name = (row["name"] or "").strip()
    sal = (row["preferred_salutation"] or "").strip()
    if sal:
        address = sal
    elif raw_name:
        address = f"{raw_name} Ji"
    else:
        address = "aapke ghar ka"
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
