"""
Module 12 — Daily Rituals
Updated: 23 March 2026 — 22 March design decisions applied.

Changes in this update:
1. API pipe confirmation: weather, cricket, news use CURATED APIs ONLY.
   No open web browsing. No URL fetching outside the three whitelisted sources.
   (Current state: API fetches not yet integrated — DeepSeek generates content.
    Wrap prompts are in place ready for API integration when keys are added.)
2. Information wrapping: raw data from APIs goes through DeepSeek before delivery.
   Senior never sees a raw temperature, score, or headline — only a warm sentence.
3. First 7 Days arc: conversation depth and morning question follow a structured arc.
   'days_since_first_message' in users table drives which arc config is active.

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
# MODULE 12 — INFORMATION WRAPPING
#
# RULE: Information from APIs must ALWAYS be wrapped in care.
# Never deliver raw data. Raw data → DeepSeek → warm sentence → senior.
#
# ALLOWED sources: weather_api, cricket_api, news_api ONLY.
# BANNED: open web browsing, URL fetching outside these three sources.
#
# Current status: API fetches not yet integrated (no API keys configured).
# DeepSeek generates plausible content from context until API keys are added.
# Wrap prompts below are ready to use the moment API data is available.
# ---------------------------------------------------------------------------

WEATHER_WRAP_PROMPT = """You are given a weather summary for {city} today: {weather_data}
Rewrite this as 1-2 sentences in a warm, caring way — like a family member
who wants the senior to have a comfortable day.
Focus on what the senior should do or be aware of. Never give raw numbers.
Never say "the temperature is X degrees." Say what it means for their day.
Example: "It's quite warm in Mumbai today — maybe stay inside in the afternoon
and drink plenty of water."
"""

CRICKET_WRAP_PROMPT = """You are given a cricket score/update: {cricket_data}
Rewrite this as 1-2 sentences in a warm, conversational way — like telling
a cricket-loving friend about the match.
Never give raw scores alone. Give the score with context and a sense of the drama.
Example: "India is looking strong — 245/6 against Australia. Should be an exciting finish."
"""

NEWS_WRAP_PROMPT = """You are given a news headline/summary: {news_data}
Rewrite this as 1-2 sentences in a gentle, non-alarming way for an elderly person.
Avoid political opinion. Stick to factual summary.
Offer to share more if they're interested — don't dump information.
Example: "There was a big meeting in Delhi today — the Prime Minister met some
world leaders. Would you like to know more about it?"
"""

ALLOWED_INFORMATION_SOURCES = ["weather_api", "cricket_api", "news_api"]


def wrap_weather(city: str, weather_data: str) -> str:
    """Wrap raw weather data in care via DeepSeek. Never deliver raw temperature."""
    from deepseek import _get_client
    prompt = WEATHER_WRAP_PROMPT.format(city=city, weather_data=weather_data)
    try:
        response = _get_client().chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=80,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("RITUALS | weather wrap failed: %s", e)
        return ""


def wrap_cricket(cricket_data: str) -> str:
    """Wrap raw cricket score in care via DeepSeek. Never deliver raw score alone."""
    from deepseek import _get_client
    prompt = CRICKET_WRAP_PROMPT.format(cricket_data=cricket_data)
    try:
        response = _get_client().chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=80,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("RITUALS | cricket wrap failed: %s", e)
        return ""


def wrap_news(news_data: str) -> str:
    """Wrap raw news headline in care via DeepSeek. Never deliver a raw headline."""
    from deepseek import _get_client
    prompt = NEWS_WRAP_PROMPT.format(news_data=news_data)
    try:
        response = _get_client().chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=80,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("RITUALS | news wrap failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# MODULE 12 — FIRST 7 DAYS ARC
#
# The first week sets the habit. Don't go deep too fast.
# Arc: comfort → routine → memory → personalisation → relationship → identity → reflection
#
# 'days_since_first_message' in the users table tracks which day we're on.
# Incremented at 00:05 IST in the nightly pass.
# ---------------------------------------------------------------------------

FIRST_7_DAYS_ARC = {
    1: {
        "goal": "Comfort and orientation",
        "morning_question": None,
        "evening_prompt": "How was your day today?",
        "topics_to_offer": ["weather", "cricket", "music", "simple chitchat"],
        "purpose_loops_active": [],
        "saathi_posture": (
            "Light conversation. Remove awkwardness. Offer topics. "
            "Repeat: 'Just say hello anytime — I'll take it from there.'"
        ),
    },
    2: {
        "goal": "Routine formation",
        "morning_question": "How did you sleep last night?",
        "evening_prompt": "Did anything nice happen today?",
        "topics_to_offer": ["weather", "cricket", "family mentions", "food"],
        "purpose_loops_active": ["meal_anchor", "call_reminder"],
        "saathi_posture": (
            "Introduce gentle routine. Make the next session feel anticipated. "
            "Use forward-anchoring: 'I'll check in with you this evening.'"
        ),
    },
    3: {
        "goal": "First memory trigger",
        "morning_question": "Where did you grow up?",
        "evening_prompt": "What's one thing from today you'd like to remember?",
        "topics_to_offer": ["childhood", "hometown", "early life"],
        "purpose_loops_active": ["meal_anchor", "call_reminder", "memory_prompt"],
        "saathi_posture": (
            "One question from memory bank. Keep it light. "
            "Don't rush to the next question. Let it breathe."
        ),
    },
    4: {
        "goal": "Personalisation moment",
        "morning_question": None,
        "evening_prompt": "What was something good about today, even if it was small?",
        "topics_to_offer": ["anything — but reference stored context naturally"],
        "purpose_loops_active": ["meal_anchor", "call_reminder", "memory_prompt", "story_loop"],
        "saathi_posture": (
            "Use something stored from earlier. 'You mentioned…' — "
            "this is the moment the senior feels genuinely remembered, not just processed."
        ),
        "special_instruction": "MUST reference at least one specific detail from previous conversations today.",
    },
    5: {
        "goal": "Relationship bridge",
        "morning_question": "Have you spoken to [family member name] recently?",
        "evening_prompt": "Did you get to talk to anyone today?",
        "topics_to_offer": ["family connection", "real-world plans"],
        "purpose_loops_active": ["meal_anchor", "call_reminder", "memory_prompt", "story_loop"],
        "saathi_posture": (
            "Gentle nudge toward real-world human connection. "
            "Frame as emotional bridge: 'I feel Priya would really enjoy hearing this from you.'"
        ),
    },
    6: {
        "goal": "Identity reinforcement",
        "morning_question": "What did you enjoy most about your work?",
        "morning_question_alt": "What are you proudest of in your life?",
        "evening_prompt": "Is there something from your life you think younger people don't appreciate enough?",
        "topics_to_offer": ["career", "achievements", "wisdom", "life experience"],
        "purpose_loops_active": ["meal_anchor", "call_reminder", "memory_prompt", "story_loop", "daily_reflection"],
        "saathi_posture": (
            "Restore sense of professional identity, authority, and social relevance. "
            "Not through flattery but through genuine acknowledgement. "
            "'You've seen so much — I like hearing how you think about this.'"
        ),
    },
    7: {
        "goal": "Reflection loop",
        "morning_question": "What's one thing you're looking forward to this week?",
        "evening_prompt": "What was one good part of your week — even something small?",
        "topics_to_offer": ["reflection", "gratitude", "week ahead"],
        "purpose_loops_active": ["meal_anchor", "call_reminder", "memory_prompt", "story_loop", "daily_reflection"],
        "saathi_posture": (
            "Set the pattern for the Sunday evening ritual. "
            "This question becomes a weekly anchor going forward."
        ),
        "special_instruction": (
            "This establishes the weekly reflection ritual. After Day 7, the Sunday evening "
            "prompt 'What was one good part of your week?' becomes permanent."
        ),
    },
}


def get_day_arc(days_since_first_message: int) -> dict:
    """
    Returns the arc configuration for the given day number.
    After Day 7, returns a standard post-onboarding config (full engagement).

    Args:
        days_since_first_message: integer (1 = first day, 2 = second day, etc.)
    """
    if 1 <= days_since_first_message <= 7:
        return FIRST_7_DAYS_ARC[days_since_first_message]
    return {
        "goal": "Ongoing relationship",
        "morning_question": None,
        "evening_prompt": "What was one good part of today, even if it was small?",
        "topics_to_offer": ["anything"],
        "purpose_loops_active": ["meal_anchor", "call_reminder", "memory_prompt", "story_loop", "daily_reflection"],
        "saathi_posture": "Full engagement mode. Three-mode framework applies. Senior leads depth (Rule 8).",
    }


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
                   u.afternoon_checkin_time, u.evening_checkin_time,
                   COALESCE(u.days_since_first_message, 1) AS days_since_first_message
            FROM users u
            WHERE u.onboarding_complete = 1
              AND COALESCE(u.account_status, 'active') = 'active'
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


def _get_days_since_first_message(user_row) -> int:
    """Return days_since_first_message from user row, defaulting to 1."""
    try:
        d = user_row["days_since_first_message"]
        return max(1, int(d)) if d else 1
    except Exception:
        return 1


def _build_morning_instruction(user_row) -> str:
    name = _address(user_row["name"], user_row["preferred_salutation"])
    religion = user_row["religion"] or ""
    topics = user_row["favourite_topics"] or ""
    city = user_row["city"] or ""
    ist = _ist_now()
    day_name = ist.strftime("%A")
    date_str = ist.strftime("%d %B %Y")

    # First 7 Days arc — depth and question are arc-driven
    day_number = _get_days_since_first_message(user_row)
    arc = get_day_arc(day_number)

    sections = [
        f"[MORNING_BRIEFING] Generate a warm morning message for {name}.",
        f"Today is {day_name}, {date_str}{', ' + city if city else ''}.",
        f"Day {day_number} arc goal: {arc['goal']}",
        f"Tone posture: {arc['saathi_posture']}",
    ]

    if religion:
        sections.append(f"Faith context (for thought of the day): {religion}")

    if arc.get("morning_question"):
        sections.append(f"End with this question naturally: {arc['morning_question']}")
    elif topics:
        sections.append(f"End with a warm question related to: {topics}")
    else:
        sections.append("End with a warm open question or observation — no specific topic required today.")

    sections += [
        "",
        "Rules:",
        "- Maximum 4-5 sentences total. Seniors should not need to scroll.",
        "- Warmth first, information second.",
        "- Never raw data. Always wrap in care.",
        "- Follow the senior's lead on depth — Rule 8 of Protocol 2 governs this.",
    ]

    return "\n".join(sections)


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
# days_since_first_message — incremented once per calendar day at midnight
# Drives the First 7 Days arc in morning briefings.
# ---------------------------------------------------------------------------

def _increment_days_since_first_message() -> None:
    """
    Increment days_since_first_message by 1 for every active user.
    Called once per day at 00:05 IST.
    Only increments for users who have completed onboarding.
    """
    try:
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE users
                SET days_since_first_message = COALESCE(days_since_first_message, 0) + 1
                WHERE onboarding_complete = 1
                """
            )
            conn.commit()
        logger.info("RITUALS | days_since_first_message incremented for all active users")
    except Exception as e:
        logger.error("RITUALS | days_since_first_message increment failed: %s", e)


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

    # Memory question prompts — Wednesday and Sunday only, at morning check-in time.
    # check_and_send_memory_prompts() handles its own day-of-week guard internally.
    try:
        from memory_questions import check_and_send_memory_prompts
        await check_and_send_memory_prompts(bot)
    except Exception as mem_q_err:
        logger.error("RITUALS | memory prompt dispatch failed: %s", mem_q_err)

    # Nightly jobs — run once per day at 00:05 IST
    if now_hhmm == "00:05":
        _run_adaptation_pass()
        _increment_days_since_first_message()
        # End-of-life: delete data for users 30 days past death notification
        try:
            from end_of_life import check_data_deletion
            check_data_deletion()
        except Exception as eol_err:
            logger.error("RITUALS | check_data_deletion failed: %s", eol_err)
