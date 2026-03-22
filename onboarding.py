"""
Module 6 — Child-led onboarding flow.

The adult child sets up Saathi on the senior's Telegram account.
Questions are asked one at a time; answers are saved progressively to the DB.

Step layout:
  Step 0   — Intro message + "What is your name?" (setup person)
  Steps 1–20 — The 20 questions about the senior
  After step 20 is answered → complete_onboarding() + warm handoff message

onboarding_step in the DB always reflects which question we are WAITING to
receive an answer for. Advancing happens AFTER the answer is saved.
"""

import re
import logging
from typing import Optional

from database import (
    update_user_fields,
    advance_onboarding_step,
    complete_onboarding,
    add_family_members_bulk,
    save_setup_person,
    save_emergency_contact,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory context — used to personalise later questions with earlier answers.
# Survives only for the life of the process (acceptable for MVP; Module 7 will
# persist this properly via the diary/memory system).
# ---------------------------------------------------------------------------
_ctx: dict[int, dict] = {}

# ---------------------------------------------------------------------------
# Intro message — sent when /start is received and onboarding_step == 0
# ---------------------------------------------------------------------------

INTRO_MESSAGE = (
    "Namaste! I'm Saathi — a companion for your loved one. 🙏\n\n"
    "You're doing something really thoughtful right now. Once we go through "
    "a few questions together, I'll be all set and waiting for them.\n\n"
    "It should take about 5 minutes. Let's begin.\n\n"
    "First — what is *your* name? (So I can introduce you to them properly.)"
)


# ---------------------------------------------------------------------------
# Questions — steps 1 through 18
# Each is a function so it can reference earlier answers via ctx.
# ---------------------------------------------------------------------------

def _q(step: int, ctx: dict) -> str:
    setup   = ctx.get("setup_name", "")
    senior  = ctx.get("senior_name", "your loved one")
    first   = setup.split()[0] if setup else ""

    questions = {
        1: (
            f"Wonderful{', ' + first if first else ''}! "
            f"What is your loved one's name? (First name is fine.)"
        ),
        2: (
            f"How should I address {senior}? "
            f"For example: 'Ji', 'Uncle', 'Aunty', 'Dadi', 'Nana' — "
            f"whatever salutation would feel natural to them."
        ),
        3: f"Which city does {senior} live in?",
        4: (
            f"What language does {senior} feel most comfortable speaking in?\n\n"
            f"For example: Hindi, English, Hinglish, Tamil, Telugu, Bengali, "
            f"Marathi, Gujarati, Punjabi — or a mix."
        ),
        5: (
            f"Is {senior}'s spouse with them? If yes, what is their spouse's name? "
            f"If not, just say 'no' or 'passed away'."
        ),
        6: (
            f"What are the names of {senior}'s children? "
            f"(You can include yourself — just list them separated by commas.)"
        ),
        7: (
            f"Does {senior} have any grandchildren? "
            f"If yes, what are their names? If not, just say 'no'."
        ),
        8: (
            f"Who should I contact in an emergency? "
            f"Please share a name and phone number. "
            f"For example: 'Priya — 9876543210'."
        ),
        9: (
            f"Does {senior} have any health conditions I should be aware of? "
            f"For example: diabetes, blood pressure, heart condition, arthritis, knee pain.\n\n"
            f"This helps me be more thoughtful in conversation. "
            f"You can say 'none' if there are none."
        ),
        10: (
            f"Does {senior} take any medicines regularly?\n\n"
            f"If yes, which ones and at what times? "
            f"For example: 'metformin 8am and 8pm, atorvastatin at night'.\n\n"
            f"If no medicines, just say 'no'."
        ),
        11: (
            f"What kind of music does {senior} love? "
            f"(Old Bollywood, classical, ghazals, devotional, folk — "
            f"whatever comes to mind.)"
        ),
        12: (
            f"What topics does {senior} enjoy talking about? "
            f"(Cricket, cooking, history, movies, religion, travel, "
            f"grandchildren, politics — anything that lights them up.)"
        ),
        13: (
            f"What is {senior}'s religion or faith, if any? "
            f"This helps me be respectful of their beliefs and mark "
            f"the right festivals and occasions. "
            f"You can say 'prefer not to say'."
        ),
        14: (
            f"What news topics does {senior} like to follow? "
            f"For example: cricket scores, Bollywood, local news, national politics, "
            f"business, religion. (Or say 'not interested' if they prefer to skip news.)"
        ),
        15: (
            f"How should I approach {senior}? I can be:\n\n"
            f"• *Friend* — warm, peer-to-peer, easy\n"
            f"• *Caring Child* — respectful, attentive, gentle\n"
            f"• *Grandchild* — playful, devoted, full of love\n"
            f"• *Assistant* — helpful, calm, practical\n\n"
            f"Which feels most right for {senior}?"
        ),
        16: (
            f"What name would you like {senior} to call me? "
            f"I go by Saathi by default — but they can name me "
            f"whatever feels personal and warm to them. "
            f"Meera, Gopal, Kamla — anything. "
            f"(Or just say 'Saathi' to keep the default.)"
        ),
        17: (
            f"What time in the morning would you like me to check in with {senior}?\n\n"
            f"(For example: '8am' or '9 baje')"
        ),
        18: (
            f"What time in the afternoon?\n\n"
            f"(For example: '1pm' or '2 baje')"
        ),
        19: (
            f"And what time in the evening?\n\n"
            f"(For example: '7pm' or '8 baje')"
        ),
        20: (
            f"Last one — I can do a gentle check-in with {senior} three times "
            f"a day (morning, afternoon, evening) and quietly let you know "
            f"if they don't respond. It's a simple safety net called a heartbeat check.\n\n"
            f"Would you like to enable this? (yes / no)"
        ),
    }
    return questions[step]


# ---------------------------------------------------------------------------
# Resume prompt — if /start is sent again mid-onboarding
# ---------------------------------------------------------------------------

def get_resume_prompt(user_id: int, step: int) -> str:
    ctx = _ctx.get(user_id, {})
    senior = ctx.get("senior_name", "your loved one")
    return (
        f"We're still setting {senior} up — let's continue from where we left off.\n\n"
        + _q(step, ctx)
    )


# ---------------------------------------------------------------------------
# Public interface called from main.py
# ---------------------------------------------------------------------------

def get_intro_message() -> str:
    return INTRO_MESSAGE


def handle_onboarding_answer(user_id: int, step: int, text: str) -> str:
    """
    Save the answer for the current step, advance, and return the next message.

    Args:
        user_id: Telegram user ID.
        step:    Current onboarding_step value from the DB (0–20).
        text:    The user's message text.

    Returns:
        The next question to send, or the completion message.
    """
    ctx = _ctx.setdefault(user_id, {})

    # Save this step's answer
    _save_answer(user_id, step, text, ctx)
    logger.info("ONBOARDING | user_id=%s | step=%d answered", user_id, step)

    next_step = step + 1

    if next_step > 20:
        # All questions answered — wrap up
        complete_onboarding(user_id)
        reply = _build_completion_message(user_id, ctx)
        logger.info("ONBOARDING | user_id=%s | complete", user_id)
        return reply

    advance_onboarding_step(user_id, next_step)
    return _q(next_step, ctx)


# ---------------------------------------------------------------------------
# Answer saving — one branch per step
# ---------------------------------------------------------------------------

def _save_answer(user_id: int, step: int, text: str, ctx: dict) -> None:
    t = text.strip()

    if step == 0:
        # Setup person's name
        ctx["setup_name"] = t
        save_setup_person(user_id, t)

    elif step == 1:
        # Senior's preferred name — title-case it
        name = t.title()
        ctx["senior_name"] = name
        update_user_fields(user_id, name=name)

    elif step == 2:
        # Preferred salutation
        update_user_fields(user_id, preferred_salutation=t)

    elif step == 3:
        # City
        update_user_fields(user_id, city=t.title())

    elif step == 4:
        # Language
        update_user_fields(user_id, language=_parse_language(t))

    elif step == 5:
        # Spouse name — treat "no", "nahi", "passed away" etc. as no spouse
        if t.lower() in ("no", "no.", "nahi", "nahi.", "nahin", "passed away",
                         "nahi hain", "nahi hai", "deceased", "expired"):
            update_user_fields(user_id, spouse_name=None)
        else:
            update_user_fields(user_id, spouse_name=t)

    elif step == 6:
        # Children's names — comma-separated
        names = [n.strip().title() for n in t.split(",")
                 if n.strip() and n.strip().lower() not in ("no", "none", "nahi")]
        if names:
            add_family_members_bulk(user_id, names, "child")

    elif step == 7:
        # Grandchildren's names
        if t.lower() not in ("no", "no.", "none", "nahi", "nahin", "nope"):
            names = [n.strip().title() for n in t.split(",") if n.strip()]
            if names:
                add_family_members_bulk(user_id, names, "grandchild")

    elif step == 8:
        # Emergency contact: "Name — 9876543210"
        phone_match = re.search(r"[\d]{10,}", t.replace(" ", "").replace("-", ""))
        phone = phone_match.group() if phone_match else ""
        name  = re.sub(r"[\d\-—:,+\s]+$", "", t).strip().strip("—:,").strip()
        save_emergency_contact(user_id, name or t, phone)

    elif step == 9:
        # Health sensitivities
        if t.lower() not in ("none", "no", "nahi", "nothing", "no.", "nil"):
            update_user_fields(user_id, health_sensitivities=t)

    elif step == 10:
        # Medicines — store raw text; Module 11 will structure it
        if t.lower() not in ("no", "nahi", "none", "nothing", "no.", "nil"):
            update_user_fields(user_id, medicines_raw=t)

    elif step == 11:
        # Music preferences
        update_user_fields(user_id, music_preferences=t)

    elif step == 12:
        # Favourite topics
        update_user_fields(user_id, favourite_topics=t)

    elif step == 13:
        # Religion
        if t.lower() != "prefer not to say":
            update_user_fields(user_id, religion=t)

    elif step == 14:
        # News interests
        if t.lower() not in ("not interested", "none", "no", "nahi", "skip"):
            update_user_fields(user_id, news_interests=t)

    elif step == 15:
        # Persona
        update_user_fields(user_id, persona=_parse_persona(t))

    elif step == 16:
        # Bot name
        if t.lower() in ("saathi", "default", "keep saathi", "no", "same"):
            bot_name = "Saathi"
        else:
            bot_name = t.title()
        ctx["bot_name"] = bot_name
        update_user_fields(user_id, bot_name=bot_name)

    elif step == 17:
        # Morning check-in time
        hhmm = _parse_single_time(t)
        update_user_fields(user_id, morning_checkin_time=hhmm, wake_time=hhmm)

    elif step == 18:
        # Afternoon check-in time
        hhmm = _parse_single_time(t)
        update_user_fields(user_id, afternoon_checkin_time=hhmm)

    elif step == 19:
        # Evening check-in time
        hhmm = _parse_single_time(t)
        update_user_fields(user_id, evening_checkin_time=hhmm, sleep_time=hhmm)

    elif step == 20:
        # Heartbeat + escalation consent
        consent = 1 if t.lower() in (
            "yes", "haan", "ha", "han", "yeah", "y", "sure",
            "ok", "okay", "haa", "bilkul", "please", "please yes"
        ) else 0
        update_user_fields(
            user_id,
            heartbeat_consent=consent,
            heartbeat_enabled=consent,
            escalation_opted_in=consent,
        )


# ---------------------------------------------------------------------------
# Completion message
# ---------------------------------------------------------------------------

def _build_completion_message(user_id: int, ctx: dict) -> str:
    setup_name  = ctx.get("setup_name", "")
    senior_name = ctx.get("senior_name", "your loved one")
    bot_name    = ctx.get("bot_name", "Saathi")
    first       = setup_name.split()[0] if setup_name else ""

    return (
        f"That's everything{', ' + first if first else ''}! 🙏\n\n"
        f"I'm all set for {senior_name}. The next time they open Telegram "
        f"and send me a message, I'll be there with a warm, personal greeting.\n\n"
        f"A couple of things you might want to do:\n"
        f"• Save this Telegram contact on their phone as *{bot_name}*\n"
        f"• Let them know their companion is ready and waiting for them\n\n"
        f"You've given them something really special. 🌟"
    )


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_language(text: str) -> str:
    t = text.lower()
    if "hindi" in t and "english" in t:
        return "hinglish"
    if "hindi" in t:
        return "hindi"
    if "english" in t:
        return "english"
    if "tamil" in t:
        return "tamil"
    if "telugu" in t:
        return "telugu"
    if "bengali" in t or "bangla" in t:
        return "bengali"
    if "marathi" in t:
        return "marathi"
    if "gujarati" in t:
        return "gujarati"
    if "punjabi" in t:
        return "punjabi"
    if "kannada" in t:
        return "kannada"
    if "malayalam" in t:
        return "malayalam"
    if "hinglish" in t:
        return "hinglish"
    # Store whatever they typed, lowercased
    return t.strip()


def _parse_persona(text: str) -> str:
    t = text.lower()
    if "grandchild" in t or "grand" in t:
        return "grandchild"
    if "child" in t or "son" in t or "daughter" in t or "caring" in t:
        return "caring_child"
    if "assistant" in t or "practical" in t or "helpful" in t:
        return "assistant"
    return "friend"


def _parse_single_time(text: str) -> str:
    """Extract a single time from free-form text like '8am', '9 baje', '21:00'.
    Returns 'HH:MM' string, or None if unparseable."""
    import re as _re
    t = text.strip().lower()
    # Common Hindi time words → defaults
    _aliases = {
        "subah": "08:00", "morning": "08:00", "breakfast": "08:00",
        "dopahar": "13:00", "afternoon": "13:00", "lunch": "13:00",
        "shaam": "18:00", "evening": "18:00",
        "raat": "21:00", "night": "21:00", "dinner": "20:00",
    }
    for alias, hhmm in _aliases.items():
        if alias in t:
            return hhmm
    m = _re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje)?", t, _re.IGNORECASE)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    period = (m.group(3) or "").lower()
    if period == "pm" and hour != 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _parse_wake_sleep(text: str) -> tuple[str, str]:
    """Extract two times from free-form text like '6am and 10pm'."""
    times = re.findall(r"\d{1,2}(?::\d{2})?\s*(?:am|pm)", text.lower())
    if len(times) >= 2:
        return times[0].strip(), times[1].strip()
    if len(times) == 1:
        return times[0].strip(), ""
    # Fallback: split on 'and' or comma
    parts = re.split(r"\band\b|,", text, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return text.strip(), ""
