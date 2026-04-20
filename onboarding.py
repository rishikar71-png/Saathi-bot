"""
Module 6 — Onboarding flow.
Updated: 23 March 2026 — 22 March design decisions applied.

Two modes:
  Mode 1 (child-led / family) — adult child sets up the bot on the senior's device.
    - Opening detection asks "for yourself or family member?"
    - Child answers 20 questions about the senior
    - Senior then receives a 4-message staged handoff (tracked via handoff_step)
    - Confusion branch handles seniors who don't know why the bot appeared

  Mode 2 (self-setup) — tech-comfortable senior sets up for themselves.
    - Same opening detection, different path
    - Questions spread across Day 1 (4-5) and natural Day 2 follow-up
    - No staged handoff needed; MODE_2_FIRST_MESSAGE lands immediately

Hard rule — first person always:
  The moment Saathi speaks TO the senior, it is always in first person.
  Never "the primary user prefers" or "your parent mentioned".
  Always "what would you like me to call you?"

onboarding_step in the DB reflects which question we are WAITING to receive
an answer for. Advancing happens AFTER the answer is saved.
"""

import re
import logging
from typing import Optional

from policy import FAMILY_SETUP_POLICY_SECTIONS
from database import (
    update_user_fields,
    advance_onboarding_step,
    complete_onboarding,
    add_family_members_bulk,
    save_setup_person,
    save_emergency_contact,
    get_connection,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory context — personalises later questions with earlier answers.
# ---------------------------------------------------------------------------
_ctx: dict[int, dict] = {}


# ---------------------------------------------------------------------------
# Shared helper: extract a clean contact name from free-text answers like
#   "yes. my wife - ishween 9833192304"
#   "my son rahul, 9876543210"
#   "sure, daughter priya - 8765..."
# Used by BOTH the child-led emergency contact step (step 8) and the
# self-setup emergency contact step (step 12).
# ---------------------------------------------------------------------------
_CONTACT_AFFIRMATION_RE = re.compile(
    r"^(yes|yeah|yup|yep|sure|okay|ok|haan|ha|han|hanji|haanji|ji|"
    r"bilkul|theek|thik)[\s\.\,\-—:!]+",
    re.IGNORECASE,
)

# Relation qualifiers that often precede a name. We strip the qualifier itself
# (not the name). "my wife - ishween" → "ishween". Handles optional possessives
# ("my", "mera", "meri"), optional copulas ("is", "hai", "name"), and common
# separators (space, dash, colon).
_CONTACT_QUALIFIER_RE = re.compile(
    r"^("
    r"(it'?s?\s+)?"
    r"(my|mera|meri|mere)\s+"
    r"(wife|husband|son|daughter|brother|sister|mother|father|mom|mum|dad|"
    r"papa|mummy|beta|beti|bhai|behen|bahen|pati|patni|biwi|"
    r"wife's\s+name|son's\s+name|daughter's\s+name|child|kid)"
    r"(\s+(is|hai|name|named|called))?"
    r"[\s\-—:,]*"
    r")+",
    re.IGNORECASE,
)


def _extract_contact_name(text: str) -> str:
    """
    Strip leading affirmations, relation qualifiers, and trailing phone/noise
    from a free-text emergency contact answer. Returns the bare name.

    Example: "yes. my wife - ishween 9833192304" -> "Ishween"
    """
    t = text.strip()
    # 1. Strip leading affirmations ("yes.", "sure,", "haan", "okay")
    t = _CONTACT_AFFIRMATION_RE.sub("", t)
    # 2. Strip relation qualifiers ("my wife -", "my son is", "it's my daughter")
    t = _CONTACT_QUALIFIER_RE.sub("", t)
    # 3. Strip trailing digits, dashes, whitespace (phone number + separators)
    t = re.sub(r"[\d\-—:,+\s]+$", "", t).strip()
    # 4. Strip leftover punctuation on edges
    t = t.strip("—:,-. ").strip()
    return t.title() if t else ""


# ---------------------------------------------------------------------------
# ARCHETYPE SIGNAL DETECTION — runs after senior's 3rd-5th message
# during the First 7 Days. Adjusts Saathi's onboarding tone only.
# NOT permanent classification. No archetype label stored in DB.
# Protocol 2 Rule 9 (Archetype Posture) takes over after Day 7.
# ---------------------------------------------------------------------------

ARCHETYPE_SIGNAL_CHECK_PROMPT = """You are Saathi's internal classifier. Do NOT respond to the user.
Your only job is to identify which of three onboarding tones fits this senior best.

Here are the senior's first few messages:
{messages}

Choose ONE label:
- striver — wants to be active, useful, productive. References achievements or capability. May push back on being "just" a senior. Responds well to purpose, goals, doing things.
- quiet_one — takes time to warm up. Short replies. Gentle. May seem hesitant or unsure. Benefits from patience and slower pacing. Needs Saathi to stay present without pressure.
- default — neither of the above. Normal warm engagement works fine.

Respond with ONLY one word: striver, quiet_one, or default.
Do not explain. Do not greet. Just the label."""

STRIVER_ONBOARDING_ADJUSTMENT = """
ONBOARDING TONE NOTE (First 7 Days only — remove after onboarding arc completes):
This senior shows Striver signals. Adjust your tone:
- Acknowledge capability and achievement, not just warmth
- Offer purpose-oriented threads: "You've clearly kept yourself very active — what does your day look like now?"
- Don't be overly solicitous. Match their energy with some directness.
- Engage with what they're proud of before going into reflection or memory.
- Avoid: excessive gentleness that could feel patronising."""

QUIET_ONE_ONBOARDING_ADJUSTMENT = """
ONBOARDING TONE NOTE (First 7 Days only — remove after onboarding arc completes):
This senior shows Quiet One signals. Adjust your tone:
- Slow down. Don't rush to the next question.
- Accept short replies without pushing for more. "That's enough for now — I just like knowing."
- Extend Self Setup Mode to 3 days if applicable — spread questions even more gently.
- Warmth before curiosity. Be present without being demanding.
- Avoid: back-to-back questions, enthusiasm that could feel overwhelming."""


def detect_archetype_signal(messages: list[str]) -> str:
    """
    Send the senior's first few messages to DeepSeek for archetype classification.
    Returns 'striver', 'quiet_one', or 'default'.
    Only called once, after the 3rd-5th message.
    """
    from deepseek import _get_client
    messages_text = "\n".join(f"- {m}" for m in messages if m.strip())
    prompt = ARCHETYPE_SIGNAL_CHECK_PROMPT.format(messages=messages_text)
    try:
        response = _get_client().chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=10,
        )
        label = response.choices[0].message.content.strip().lower()
        if label in ("striver", "quiet_one", "default"):
            return label
        return "default"
    except Exception as e:
        logger.warning("ARCHETYPE | detection failed: %s", e)
        return "default"


def get_archetype_adjustment_text(archetype: str) -> str | None:
    """Return the system prompt adjustment string for a given archetype label."""
    if archetype == "striver":
        return STRIVER_ONBOARDING_ADJUSTMENT
    if archetype == "quiet_one":
        return QUIET_ONE_ONBOARDING_ADJUSTMENT
    return None


# ---------------------------------------------------------------------------
# OPENING DETECTION — sent BEFORE any onboarding questions
# Determines Mode 1 (family) vs Mode 2 (self-setup)
# ---------------------------------------------------------------------------

OPENING_DETECTION_QUESTION = (
    "Namaste! I'm Saathi — a companion for elderly loved ones. 🙏\n\n"
    "Before we begin, one quick question:\n\n"
    "*Are you setting this up for yourself, or for a family member?*\n\n"
    "Reply: *myself* or *family member*"
)


def detect_setup_mode(text: str) -> Optional[str]:
    """
    Parse the user's answer to the opening detection question.
    Returns 'self', 'family', or None if unclear.

    RULE: Never default to a mode — always ask again if unclear.
    A wrong default silently puts a self-setup senior into the child-led flow.
    """
    t = text.lower().strip()
    self_signals = [
        "myself", "for myself", "for me", "i am setting", "i'm setting",
        "self setup", "apne liye", "mujhe", "mere liye",
        "i will use", "i'll use", "i want to use", "for myself",
        "setting up for me", "for myself",
    ]
    family_signals = [
        "family member", "for my", "for a family", "for someone",
        "my parent", "my father", "my mother", "my dad", "my mom",
        "maa", "papa", "mata", "pita", "my grandparent", "dadi", "nana",
        "someone else", "unke liye", "for them", "for my parent",
        "for a parent", "for a relative",
    ]
    for s in self_signals:
        if s in t:
            return "self"
    for s in family_signals:
        if s in t:
            return "family"
    # Single-word common answers
    if t in ("myself", "me", "self", "i", "mujhe", "mera"):
        return "self"
    if t in ("family", "parent", "them", "relative", "unke", "someone"):
        return "family"
    # Never default — ask again
    return None


# ---------------------------------------------------------------------------
# MODE 1 — CHILD-LED SETUP
# Intro message sent to the adult child after mode='family' is confirmed
# ---------------------------------------------------------------------------

INTRO_MESSAGE = (
    "Wonderful! You're doing something really thoughtful right now. 🙏\n\n"
    "Once we go through a few questions together, I'll be all set and waiting for them.\n\n"
    "It should take about 5 minutes. Let's begin.\n\n"
    "First — what is *your* name? (So I can introduce you to them properly.)"
)


# ---------------------------------------------------------------------------
# MODE 2 — SELF SETUP
# First message sent immediately when mode='self' is confirmed
# RULE: No question, no enthusiasm, calm presence only.
# ---------------------------------------------------------------------------

MODE_2_FIRST_MESSAGE = (
    "Hello… I'm Saathi. I'll be around — you can talk whenever you feel like."
)

# Day 1 questions — asked naturally, max 4-5 in first session
SELF_SETUP_DAY_1_QUESTIONS = [
    "What would you like me to call you?",
    "And what would you like to call me? You can choose any name.",
    "Which city are you in?",
    "What language do you prefer to chat in — Hindi, English, or a mix?",
    "Is there anyone I should know about — family members you talk to often?",
]

# Day 2 questions — asked after the bridge question (now or next day)
# Revised 19 Apr 2026: every question is load-bearing. wake_time/sleep_time
# and cricket-only question dropped (unused or lossy). Emergency contact
# added so self-setup users get safety features and family bridge access.
SELF_SETUP_DAY_2_QUESTIONS = [
    # Q6 — Medicines (proper format — feeds reminder parser)
    (
        "Do you take any medicines regularly?\n\n"
        "If yes, please list them with their times — e.g. "
        "'metformin 8am and 8pm, BP tablet at night'.\n\n"
        "If not, just say 'no'."
    ),
    # Q7 — Morning check-in time
    "What time in the morning would you like me to say hello? (e.g. 8am or 9 baje)",
    # Q8 — Afternoon check-in time
    "And what time in the afternoon? (e.g. 1pm or 2 baje)",
    # Q9 — Evening check-in time
    "What time in the evening works for a chat? (e.g. 7pm or 8 baje)",
    # Q10 — News interests (open — replaces cricket yes/no)
    (
        "What news topics do you enjoy following — cricket, Bollywood, "
        "local news, politics, business, religion, or something else?\n\n"
        "(Or say 'skip' if you'd prefer to pass on news.)"
    ),
    # Q11 — Music preferences
    "What kind of music do you enjoy?",
    # Q12 — Emergency contact (Rishi's phrasing, 19 Apr 2026)
    (
        "Would you like to add someone from your family or a close friend "
        "I should contact in an emergency? Share a name and phone number — "
        "or say 'skip' if you'd rather not."
    ),
]

# Bridge question — asked after Day 1 (step 5) is answered. User chooses now or tomorrow.
SELF_SETUP_BRIDGE_QUESTION = (
    "Thank you. 🙏\n\n"
    "I'd love to know a few more things so I can be most useful — just 5 more questions. "
    "Shall we do them now, or would you prefer I ask tomorrow?\n\n"
    "Reply: *now* or *tomorrow*"
)

SELF_SETUP_BRIDGE_DEFERRED_REPLY = (
    "Of course — we'll pick it up tomorrow. 🙏\n\n"
    "I'll be around today if you'd like to chat about anything else."
)

SELF_SETUP_BRIDGE_RECHECK = (
    "Good to see you again.\n\n"
    "Yesterday I mentioned a few more questions — shall we do them now? Just 5 short ones.\n\n"
    "Reply: *now* or *later*"
)


def _self_setup_question(step: int) -> Optional[str]:
    """Return the self-setup question for the given step (1-indexed). None if done."""
    all_q = SELF_SETUP_DAY_1_QUESTIONS + SELF_SETUP_DAY_2_QUESTIONS
    if 1 <= step <= len(all_q):
        return all_q[step - 1]
    return None


def _today_ist_str() -> str:
    """Return today's IST date as YYYY-MM-DD (for deferred-date comparison)."""
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(tz=ist).strftime("%Y-%m-%d")


def detect_bridge_answer(text: str) -> str:
    """
    Parse user's yes/later response to the bridge question.
    Returns 'yes' (wants to continue now) or 'later' (tomorrow).
    RULE: Unclear answers default to 'later' — never force questions on a user who didn't consent.
    """
    t = text.lower().strip()
    # Multi-word 'later' phrases — substring match is safe here
    later_phrases = ("not now", "baad mein", "abhi nahi", "nahi abhi")
    for p in later_phrases:
        if p in t:
            return "later"
    # Single-word signals — word-boundary match to avoid false positives
    # (e.g. "no" must NOT match "now", "baad" must NOT match random substrings)
    words = set(re.findall(r"\w+", t))
    later_words = {"tomorrow", "later", "kal", "baad", "nahi", "no"}
    yes_words = {
        "yes", "now", "sure", "haan", "ha", "han", "okay", "ok",
        "abhi", "yeah", "yup", "bilkul", "theek",
    }
    # 'later' takes priority when both appear — ambiguity preserves user agency
    if words & later_words:
        return "later"
    if words & yes_words:
        return "yes"
    # Unclear → safer default is 'later'
    return "later"


# ---------------------------------------------------------------------------
# STAGED HANDOFF — 4 messages sent to senior after child-led setup completes
#
# CRITICAL: Message 1 is sent immediately when senior first contacts the bot.
# Then WAIT. Do not send Message 2 until senior responds or initiates.
# Messages 2, 3, 4 follow conversationally after each senior response.
#
# handoff_step tracks where we are:
#   0 = senior hasn't messaged yet (setup just completed)
#   1 = Message 1 sent (waiting for senior response)
#   2 = Message 2 sent ("What would you like me to call you?")
#   3 = Message 3 sent ("What would you like to call me?")
#   4 = Message 4 sent — handoff complete, full DeepSeek pipeline from here
# ---------------------------------------------------------------------------

def get_handoff_message(handoff_step: int, child_name: str = "Someone") -> str:
    """
    Returns the appropriate handoff message for the given step.

    handoff_step=0: Message 1 — calm first contact, no question
    handoff_step=1: Message 2 — ask senior's preferred name
    handoff_step=2: Message 3 — ask what they'd like to call the bot
    handoff_step=3: Message 4 — open invitation, complete handoff
    """
    messages = {
        0: f"Namaste. {child_name} asked me to be in touch. I'm Saathi — I'm here whenever you'd like to talk.",
        1: "Can I ask you something? What would you like me to call you?",
        2: "And what would you like to call me? You can choose any name.",
        3: "I'm really glad we're talking. We can chat about anything — your day, memories, music, cricket, whatever you feel like. Would you like me to tell you something interesting, or would you like to tell me about your day?",
    }
    return messages.get(handoff_step, messages[3])


def get_setup_child_name(user_id: int) -> str:
    """Fetch the name of the adult child who set up the bot (is_setup_user=1)."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT name FROM family_members
                WHERE user_id = ? AND is_setup_user = 1
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            return row["name"] if row else "Someone"
    except Exception:
        return "Someone"


# ---------------------------------------------------------------------------
# CONFUSION BRANCH — triggers if senior's first message sounds confused
# ---------------------------------------------------------------------------

CONFUSION_SIGNALS = [
    "who are you", "who is this", "what is this", "kya hai ye", "kaun ho",
    "kaun hai", "ye kya hai", "kyun aaya", "kyun aya", "wrong number",
    "galat number", "kaise aaya", "kahan se", "kisne bheja", "kisne diya",
    "why did you", "why are you", "i don't know you", "mujhe nahi pata",
    "pehchanta nahi", "pehchanti nahi",
]


def is_confused_senior(message: str) -> bool:
    """
    Detects if a senior's first message suggests confusion about the bot's appearance.
    If True, trigger the confusion branch before the handoff message.
    """
    message_lower = message.lower()
    return any(signal in message_lower for signal in CONFUSION_SIGNALS)


def get_confusion_response(child_name: str = "Someone") -> str:
    """
    Warm explanation for a confused senior.
    RULE: affection framing only — never concern framing.
    """
    return (
        f"I'm Saathi — {child_name} thought you might enjoy having someone to chat with. "
        "Think of me as just someone to chat with whenever you feel like it. "
        "There's no need to do anything right now — I'm here whenever you'd like to talk."
    )


# ---------------------------------------------------------------------------
# FIRST-PERSON VALIDATION
# Banned phrases must never appear in messages addressed TO the senior.
# Third-person framing about the senior is only permitted in child-facing messages.
# ---------------------------------------------------------------------------

BANNED_THIRD_PERSON_PHRASES = [
    "the primary user",
    "your parent",
    "the senior",
    "the user prefers",
    "they prefer",
    "their name is",
]


def validate_no_third_person(message: str) -> bool:
    """
    Returns True if the message passes (no banned third-person phrases).
    Call this before sending any message directly to a senior.
    """
    message_lower = message.lower()
    return not any(p.lower() in message_lower for p in BANNED_THIRD_PERSON_PHRASES)


# ---------------------------------------------------------------------------
# FAMILY REFERENCE FRAMING VALIDATION
# All references to who set up the bot must use affection framing, not concern.
# ---------------------------------------------------------------------------

BANNED_FAMILY_FRAMING = [
    "was worried", "were worried", "wanted to make sure", "check on you",
    "keeping an eye", "nothing serious", "just a small", "not a big deal",
    "don't worry", "no tension",
]


def validate_family_framing(message: str) -> bool:
    """
    Returns True if family references use affection framing, not concern or minimising.
    PERMITTED: "Priya thought you might enjoy having someone to chat with."
    BANNED: "Priya was worried about you." / "Nothing serious."
    """
    message_lower = message.lower()
    return not any(p.lower() in message_lower for p in BANNED_FAMILY_FRAMING)


# ---------------------------------------------------------------------------
# CHILD-LED SETUP — Questions (steps 1–20, addressed TO the adult child)
# These are NOT third-person bugs — they are correctly addressed to the child.
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

def get_resume_prompt(user_id: int, step: int, setup_mode: str = None) -> str:
    if setup_mode == "self":
        q = _self_setup_question(step)
        return q or "We're almost done — just a few more things to set up."
    ctx = _ctx.get(user_id, {})
    senior = ctx.get("senior_name", "your loved one")
    return (
        f"We're still setting {senior} up — let's continue from where we left off.\n\n"
        + _q(step, ctx)
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_intro_message() -> str:
    return INTRO_MESSAGE


def get_opening_detection_question() -> str:
    return OPENING_DETECTION_QUESTION


def handle_mode_detection(user_id: int, text: str):
    """
    Handle the user's response to the opening detection question.
    Returns (setup_mode, next_message) tuple.
    setup_mode: 'self' or 'family'
    next_message: the message to send next
    """
    mode = detect_setup_mode(text)
    if mode is None:
        # Unclear answer — ask again
        return (None, (
            "Sorry, I didn't quite catch that! 🙏\n\n"
            "Are you setting this up *for yourself* or *for a family member*?\n\n"
            "Just reply: *myself* or *family member*"
        ))

    update_user_fields(user_id, setup_mode=mode)

    if mode == "family":
        return ("family", INTRO_MESSAGE)
    else:
        # Self-setup: send first message + first question
        update_user_fields(user_id, onboarding_step=1)
        first_q = SELF_SETUP_DAY_1_QUESTIONS[0]
        return ("self", f"{MODE_2_FIRST_MESSAGE}\n\n{first_q}")


def handle_onboarding_answer(user_id: int, step: int, text: str) -> str:
    """
    Save the answer for the current step, advance, and return the next message.

    For family mode: standard 20-question flow.
    For self-setup mode: SELF_SETUP_DAY_1_QUESTIONS then complete.
    """
    ctx = _ctx.setdefault(user_id, {})

    # Determine mode
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT setup_mode FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            setup_mode = row["setup_mode"] if row else "family"
    except Exception:
        setup_mode = "family"

    if setup_mode == "self":
        return _handle_self_setup_answer(user_id, step, text, ctx)

    # --- Family / child-led flow ---
    _save_answer(user_id, step, text, ctx)
    logger.info("ONBOARDING | user_id=%s | mode=family | step=%d answered", user_id, step)

    next_step = step + 1
    if next_step > 20:
        complete_onboarding(user_id)
        # handoff_step starts at 0 — senior hasn't been greeted yet
        update_user_fields(user_id, handoff_step=0)
        reply = _build_completion_message(user_id, ctx)
        logger.info("ONBOARDING | user_id=%s | complete", user_id)
        return reply

    advance_onboarding_step(user_id, next_step)
    return _q(next_step, ctx)


def _handle_self_setup_answer(user_id: int, step: int, text: str, ctx: dict) -> str:
    """
    Handle answers in self-setup mode.
    Steps 1–5 = Day 1. After step 5 → bridge question (now/tomorrow).
    Steps 6–10 = Day 2 (only reached via 'yes' to bridge or next-day resume).
    After step 10 → onboarding fully complete.
    """
    _save_self_setup_answer(user_id, step, text, ctx)
    logger.info("ONBOARDING | user_id=%s | mode=self | step=%d answered", user_id, step)

    next_step = step + 1
    day1_len = len(SELF_SETUP_DAY_1_QUESTIONS)
    day2_len = len(SELF_SETUP_DAY_2_QUESTIONS)

    # End of Day 1 → ask bridge question, wait for yes/later.
    # Do NOT call complete_onboarding — onboarding_complete stays 0 until bridge resolves.
    if next_step == day1_len + 1:  # == 6
        update_user_fields(user_id, self_setup_bridge_state="asked")
        logger.info("ONBOARDING | user_id=%s | self-setup day 1 done → bridge asked", user_id)
        return SELF_SETUP_BRIDGE_QUESTION

    # End of Day 2 → fully complete
    if next_step > day1_len + day2_len:  # > 12
        update_user_fields(user_id, self_setup_bridge_state=None)
        complete_onboarding(user_id)
        logger.info("ONBOARDING | user_id=%s | self-setup fully complete", user_id)
        return _build_self_setup_completion_message(user_id, ctx)

    # Day 2 question in progress — advance and return next question
    advance_onboarding_step(user_id, next_step)
    next_q = _self_setup_question(next_step)
    return next_q or "Let's continue."


def handle_bridge_answer(user_id: int, text: str) -> str:
    """
    Called when setup_mode='self' AND self_setup_bridge_state='asked'.
    Parses yes/later and routes:
      - 'yes'    → clear state, advance to step 6, ask Day 2 q1
      - 'later'  → set state='deferred', store today's IST date, mark onboarding_complete=1
                   so user can chat freely today. Re-check fires on next day.
    """
    answer = detect_bridge_answer(text)
    day1_len = len(SELF_SETUP_DAY_1_QUESTIONS)

    if answer == "yes":
        update_user_fields(
            user_id,
            self_setup_bridge_state=None,
            onboarding_step=day1_len + 1,  # step 6
        )
        logger.info("ONBOARDING | user_id=%s | bridge=yes → Day 2 q1", user_id)
        return _self_setup_question(day1_len + 1)

    # 'later' path
    update_user_fields(
        user_id,
        self_setup_bridge_state="deferred",
        self_setup_deferred_date=_today_ist_str(),
        onboarding_complete=1,  # let user chat freely today
    )
    logger.info("ONBOARDING | user_id=%s | bridge=later → deferred", user_id)
    return SELF_SETUP_BRIDGE_DEFERRED_REPLY


def maybe_resume_day2_bridge(user_id: int, deferred_date: Optional[str]) -> Optional[str]:
    """
    Called on every inbound message when setup_mode='self' AND bridge_state='deferred'.
    If today's IST date > deferred_date → re-ask the bridge question and flip state back to 'asked'.
    Returns the re-check message, or None if it's still the same day (user chats normally).
    """
    if not deferred_date:
        return None
    today = _today_ist_str()
    if today > deferred_date:
        update_user_fields(
            user_id,
            self_setup_bridge_state="asked",
            onboarding_complete=0,  # roll back so bridge-answer handler can fire
        )
        logger.info("ONBOARDING | user_id=%s | deferred date passed → re-ask bridge", user_id)
        return SELF_SETUP_BRIDGE_RECHECK
    return None


def _save_self_setup_answer(user_id: int, step: int, text: str, ctx: dict) -> None:
    """Save self-setup mode answers.
    Day 1 (steps 1–5): name, bot name, city, language, family members.
    Day 2 (steps 6–12): medicines, morning/afternoon/evening check-in times,
                        news interests, music preferences, emergency contact.
    """
    t = text.strip()
    tl = t.lower()

    # --- Day 1 ---
    if step == 1:  # What would you like me to call you?
        name = t.title()
        ctx["senior_name"] = name
        update_user_fields(user_id, name=name)
    elif step == 2:  # What would you like to call me?
        bot_name = t.title() if tl not in ("saathi", "default") else "Saathi"
        ctx["bot_name"] = bot_name
        update_user_fields(user_id, bot_name=bot_name)
    elif step == 3:  # City
        update_user_fields(user_id, city=t.title())
    elif step == 4:  # Language
        update_user_fields(user_id, language=_parse_language(t))
    elif step == 5:  # Family members
        if tl not in ("no", "none", "nahi", "nobody"):
            names = [n.strip().title() for n in re.split(r"[,\n]", t) if n.strip()]
            if names:
                add_family_members_bulk(user_id, names, "family")

    # --- Day 2 ---
    elif step == 6:  # Medicines + times (free text → reminder parser)
        if tl not in ("no", "nahi", "none", "nothing", "no.", "nil", "skip"):
            update_user_fields(user_id, medicines_raw=t)
    elif step == 7:  # Morning check-in time
        hhmm = _parse_single_time(t)
        if hhmm:
            update_user_fields(user_id, morning_checkin_time=hhmm)
    elif step == 8:  # Afternoon check-in time
        hhmm = _parse_single_time(t)
        if hhmm:
            update_user_fields(user_id, afternoon_checkin_time=hhmm)
    elif step == 9:  # Evening check-in time
        hhmm = _parse_single_time(t)
        if hhmm:
            update_user_fields(user_id, evening_checkin_time=hhmm)
    elif step == 10:  # News interests (open, replaces cricket yes/no)
        if tl not in ("no", "nahi", "none", "nothing", "no.", "nil", "skip",
                      "not interested"):
            update_user_fields(user_id, news_interests=t)
    elif step == 11:  # Music preferences
        if tl not in ("no", "nahi", "none", "nothing", "no.", "nil", "skip"):
            update_user_fields(user_id, music_preferences=t)
    elif step == 12:  # Emergency contact — name + phone
        skip_signals = ("skip", "no", "nahi", "none", "nothing", "no.", "nil",
                        "no thanks", "later")
        if tl not in skip_signals:
            phone_match = re.search(r"[\d]{10,}", t.replace(" ", "").replace("-", ""))
            phone = phone_match.group() if phone_match else ""
            # Use the shared contact-name extractor: strips affirmations,
            # relation qualifiers ("my wife -"), and trailing phone noise.
            final_name = _extract_contact_name(t) or t
            save_emergency_contact(user_id, final_name, phone)
            ctx["emergency_name"] = final_name
            # Opting in to a contact = opting in to safety features.
            # weekly_report_opt_in defaults on per Option A (19 Apr 2026):
            # the family-code offer in the completion message serves as consent.
            update_user_fields(
                user_id,
                heartbeat_consent=1,
                heartbeat_enabled=1,
                escalation_opted_in=1,
                weekly_report_opt_in=1,
            )


# ---------------------------------------------------------------------------
# Answer saving — family/child-led mode (steps 0–20)
# ---------------------------------------------------------------------------

def _save_answer(user_id: int, step: int, text: str, ctx: dict) -> None:
    t = text.strip()

    if step == 0:
        ctx["setup_name"] = t
        save_setup_person(user_id, t)

    elif step == 1:
        name = t.title()
        ctx["senior_name"] = name
        update_user_fields(user_id, name=name)

    elif step == 2:
        update_user_fields(user_id, preferred_salutation=t)

    elif step == 3:
        update_user_fields(user_id, city=t.title())

    elif step == 4:
        update_user_fields(user_id, language=_parse_language(t))

    elif step == 5:
        if t.lower() in ("no", "no.", "nahi", "nahi.", "nahin", "passed away",
                         "nahi hain", "nahi hai", "deceased", "expired"):
            update_user_fields(user_id, spouse_name=None)
        else:
            update_user_fields(user_id, spouse_name=t)

    elif step == 6:
        names = [n.strip().title() for n in t.split(",")
                 if n.strip() and n.strip().lower() not in ("no", "none", "nahi")]
        if names:
            add_family_members_bulk(user_id, names, "child")

    elif step == 7:
        if t.lower() not in ("no", "no.", "none", "nahi", "nahin", "nope"):
            names = [n.strip().title() for n in t.split(",") if n.strip()]
            if names:
                add_family_members_bulk(user_id, names, "grandchild")

    elif step == 8:
        phone_match = re.search(r"[\d]{10,}", t.replace(" ", "").replace("-", ""))
        phone = phone_match.group() if phone_match else ""
        # Use the shared contact-name extractor: strips affirmations,
        # relation qualifiers ("my wife -"), and trailing phone noise.
        name = _extract_contact_name(t) or t
        save_emergency_contact(user_id, name, phone)

    elif step == 9:
        if t.lower() not in ("none", "no", "nahi", "nothing", "no.", "nil"):
            update_user_fields(user_id, health_sensitivities=t)

    elif step == 10:
        if t.lower() not in ("no", "nahi", "none", "nothing", "no.", "nil"):
            update_user_fields(user_id, medicines_raw=t)

    elif step == 11:
        update_user_fields(user_id, music_preferences=t)

    elif step == 12:
        update_user_fields(user_id, favourite_topics=t)

    elif step == 13:
        if t.lower() != "prefer not to say":
            update_user_fields(user_id, religion=t)

    elif step == 14:
        if t.lower() not in ("not interested", "none", "no", "nahi", "skip"):
            update_user_fields(user_id, news_interests=t)

    elif step == 15:
        update_user_fields(user_id, persona=_parse_persona(t))

    elif step == 16:
        if t.lower() in ("saathi", "default", "keep saathi", "no", "same"):
            bot_name = "Saathi"
        else:
            bot_name = t.title()
        ctx["bot_name"] = bot_name
        update_user_fields(user_id, bot_name=bot_name)

    elif step == 17:
        hhmm = _parse_single_time(t)
        update_user_fields(user_id, morning_checkin_time=hhmm, wake_time=hhmm)

    elif step == 18:
        hhmm = _parse_single_time(t)
        update_user_fields(user_id, afternoon_checkin_time=hhmm)

    elif step == 19:
        hhmm = _parse_single_time(t)
        update_user_fields(user_id, evening_checkin_time=hhmm, sleep_time=hhmm)

    elif step == 20:
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
# Completion message — sent to the adult child after all 20 questions
# ---------------------------------------------------------------------------

def _build_completion_message(user_id: int, ctx: dict) -> str:
    setup_name  = ctx.get("setup_name", "")
    senior_name = ctx.get("senior_name", "your loved one")
    bot_name    = ctx.get("bot_name", "Saathi")
    first       = setup_name.split()[0] if setup_name else ""

    return (
        f"That's everything{', ' + first if first else ''}! 🙏\n\n"
        f"I'm all set for {senior_name}. The next time they message me, "
        f"I'll greet them warmly and personally.\n\n"
        f"A couple of things you might want to do:\n"
        f"• Save this Telegram contact on their phone as *{bot_name}*\n"
        f"• Let them know their companion is ready and waiting for them\n\n"
        f"You've given them something really special. 🌟"
        f"{FAMILY_SETUP_POLICY_SECTIONS}"
    )


def _get_emergency_contact_name(user_id: int) -> Optional[str]:
    """DB-backed lookup — survives process restarts between bridge defer/resume."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM family_members "
                "WHERE user_id = ? AND role = 'emergency' "
                "ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            return row["name"] if row else None
    except Exception:
        return None


def _build_self_setup_completion_message(user_id: int, ctx: dict) -> str:
    """
    Completion message for self-setup mode.
    If an emergency contact was provided, append the family-code offer
    (Option A, 19 Apr 2026) so the senior can optionally share the code
    with that person to enable weekly updates + message relay.
    """
    base = (
        "That's everything — thank you for telling me. 🙏\n\n"
        "I'll be here whenever you'd like to talk."
    )

    # Prefer ctx (current session), fall back to DB (resume-from-defer case)
    emergency_name = ctx.get("emergency_name") or _get_emergency_contact_name(user_id)
    if not emergency_name:
        return base

    # Generate the linking code the senior can share
    try:
        from family import get_or_create_linking_code
        code = get_or_create_linking_code(user_id)
    except Exception as e:
        logger.warning("ONBOARDING | family code generation failed: %s", e)
        return base

    if not code or code == "ERROR":
        return base

    return (
        "That's everything — thank you for telling me. 🙏\n\n"
        f"One last thing, if you'd like: if {emergency_name} would also "
        f"like to get a gentle weekly update from me, or be able to send you a "
        f"quick message through me anytime, have them send me this code:\n\n"
        f"*{code}*\n\n"
        "No rush — they can do this whenever.\n\n"
        "I'll be here whenever you'd like to talk."
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
    """Extract a single time from free-form text like '8am', '6.30', '9 baje', '21:00'.
    Returns 'HH:MM' string, or None if unparseable.
    Accepts colon OR dot as separator (Indian English commonly writes '6.30')."""
    import re as _re
    t = text.strip().lower()
    # Order matters: 'midnight' must be checked BEFORE 'night' (substring),
    # and 'noon' before anything containing 'n'.
    _aliases = {
        "midnight": "00:00", "noon": "12:00",
        "subah": "08:00", "morning": "08:00", "breakfast": "08:00",
        "dopahar": "13:00", "afternoon": "13:00", "lunch": "13:00",
        "shaam": "18:00", "evening": "18:00",
        "raat": "21:00", "night": "21:00", "dinner": "20:00",
    }
    for alias, hhmm in _aliases.items():
        if alias in t:
            return hhmm
    # Accept ':' or '.' as minute separator (e.g. '6.30', '6:30', '6')
    m = _re.search(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|baje)?", t, _re.IGNORECASE)
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
