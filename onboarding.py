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
from apis import CITY_ALIASES, canonicalize_city

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
    r"wife's\s+name|son's\s+name|daughter's\s+name|child|kid)\b"
    r"(\s+(is|hai|name|named|called)\b)?"
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
# Address (display) helper — 22 Apr 2026
#
# preferred_salutation is how Saathi addresses the senior in conversation.
# It is NOT an honorific appended to the first name — it is the full display
# string the family uses ("Ma" / "Papa" / "Durga Ji" / "Rameshji" / "Dadi").
#
# name is kept as the reference identity (used for DB joins, system prompt
# identity, memory references like "her name is Durga").
#
# Default when the child skips step 2: "{first_name} Ji" (respectful Indian
# register — "Durga Ji", "Ramesh Ji"). Senior can override at handoff step 1
# (writes to preferred_salutation, leaves name untouched).
# ---------------------------------------------------------------------------

def _default_address(senior_name: str) -> str:
    """Default display address when the child didn't specify one."""
    n = (senior_name or "").strip()
    if not n or n == "your loved one":
        return "your loved one"
    return f"{n} Ji"


def _address(ctx: dict) -> str:
    """Return the display address to use when talking ABOUT the senior."""
    salutation = (ctx.get("salutation") or "").strip()
    if salutation:
        return salutation
    return _default_address(ctx.get("senior_name", ""))


# ---------------------------------------------------------------------------
# Step 0 helper — parse "rishi 9819787322" into (name, phone) so we can
# capture the setup person's phone once and offer it as the default emergency
# contact at step 8.
# ---------------------------------------------------------------------------

def _parse_setup_person(text: str) -> tuple[str, str]:
    """
    Extract (name, phone) from step 0's free-text reply. The child may type
    just a name ("Rishi") or name + phone ("Rishi 9819787322" / "rishi, 98197 87322").
    Name is title-cased. Phone is digits only (10+ digits required).
    """
    t = (text or "").strip()
    # Find a 10+ digit run (allow spaces/dashes inside the input).
    compact = re.sub(r"[\s\-—]", "", t)
    phone_match = re.search(r"\d{10,}", compact)
    phone = phone_match.group() if phone_match else ""
    # Name is whatever remains after stripping digits + separators from edges.
    name_raw = re.sub(r"[\d\-—:,+]+", " ", t)
    name_raw = re.sub(r"\s+", " ", name_raw).strip(" -—:,.")
    return name_raw.title(), phone


# ---------------------------------------------------------------------------
# Step 10 (medicines) — detect "I don't know yet / she'll tell me later"
# so we warm-ack instead of silently failing.
# ---------------------------------------------------------------------------

_MEDICINE_UNKNOWN_SIGNALS = (
    # Unknown / uncertain
    "don't know", "dont know", "do not know", "not sure", "unsure",
    "no idea",
    # Child defers to own follow-up
    "i'll check", "ill check", "will check", "check later",
    "will fill", "need to fill", "needs to fill", "will let you know",
    "tell you later", "find out", "figure out", "get back",
    # Child defers to senior ("she'll tell you", "let her input them", etc.)
    "will inform", "she'll inform", "she will inform", "he'll inform", "he will inform",
    "will tell", "she'll tell", "she will tell", "he'll tell", "he will tell",
    "will share", "she'll share", "she will share", "he'll share", "he will share",
    "will send", "she'll send", "she will send", "he'll send", "he will send",
    "will update", "she'll update", "she will update",
    "she'll fill", "she will fill", "he'll fill", "he will fill",
    "she'll input", "she will input", "he'll input", "he will input",
    "she'll add", "she will add", "he'll add", "he will add",
    "she'll do", "she will do", "he'll do", "he will do",
    "she'll give", "she will give", "he'll give", "he will give",
    "let her", "let him",  # "let her input them", "let her fill it", "let him tell you"
    "ask her", "ask him",
    "she knows", "he knows",
    # Hindi / Hinglish
    "pata nahi", "maalum nahi", "malum nahi", "nahi pata",
    "bata nahi", "pata karke", "pooch ke",
    "khud bata", "khud batayegi", "khud batayega",
    "wo bata", "woh bata", "vo bata",
    "baad mein bata", "baad me bata",
    "khud fill", "khud bhar",
)


def _is_medicines_unknown(text: str) -> bool:
    """True if the child is deferring — e.g. 'I don't know, she'll tell me'."""
    tl = (text or "").lower()
    return any(sig in tl for sig in _MEDICINE_UNKNOWN_SIGNALS)


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

# Pilot scope (22 Apr 2026) — we only support English, Hindi, Hinglish.
# Anything else gets this polite refusal and the language step does NOT advance.
POLITE_UNSUPPORTED_LANGUAGE_MESSAGE = (
    "My apologies — at present I can only converse in Hindi, English, or a "
    "mix of the two. Would you like to continue in any of those?"
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

def get_handoff_message(
    handoff_step: int,
    child_name: str = "Someone",
    bot_name: str = "Saathi",
) -> str:
    """
    Returns the appropriate handoff message for the given step.

    handoff_step=0: Message 1 — calm first contact, no question
    handoff_step=1: Message 2 — ask senior's preferred address
    handoff_step=2: Message 3 — ask what they'd like to call the bot
    handoff_step=3: Message 4 — open invitation, complete handoff

    22 Apr 2026: bot_name is now threaded through. The child chooses a bot
    name at step 16 of onboarding ('sage' / 'Meera' / 'Gopal' / etc.); that
    name must be the one the senior hears in the first handoff message.
    Hardcoding 'Saathi' here ignored their choice.

    child_name is title-cased defensively — setup person's stored name may be
    lowercased if they typed it that way ('rishi') and we want the senior to
    see 'Rishi' in the very first message.
    """
    cn = (child_name or "Someone").strip().title() or "Someone"
    bn = (bot_name or "Saathi").strip() or "Saathi"
    messages = {
        0: f"Namaste. {cn} asked me to be in touch. I'm {bn} — I'm here whenever you'd like to talk.",
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
    # Display address — "{name} Ji" by default, or whatever the child typed at step 2.
    # Used from step 3 onwards when addressing/referring to the senior.
    addr    = _address(ctx)
    # Step 2 primes the default in its own wording so the child knows they can just skip.
    addr_preview = _default_address(senior) if senior and senior != "your loved one" else "your loved one"

    questions = {
        1: (
            f"Wonderful{', ' + first if first else ''}! "
            f"What is your loved one's name? (First name is fine.)"
        ),
        2: (
            f"What does {addr_preview} like to be called by family? "
            f"Whatever feels natural — 'Ma', 'Papa', 'Dadi', 'Nana', "
            f"'Rameshji', 'Aunty' — or a full name if you prefer.\n\n"
            f"If you're not sure, just say 'skip' and I'll call {addr_preview}."
        ),
        3: f"Which city does {addr} live in?",
        4: (
            f"What language does {addr} feel most comfortable speaking in?\n\n"
            f"Right now I can chat in *Hindi*, *English*, or a mix of the two "
            f"(*Hinglish*). Which is best for {addr}?"
        ),
        5: (
            f"Is {addr}'s spouse with them? If yes, what is their spouse's name? "
            f"If not, just say 'no' or 'passed away'."
        ),
        6: (
            f"What are the names of {addr}'s children? "
            f"(You can include yourself — just list them separated by commas.)"
        ),
        7: (
            f"Does {addr} have any grandchildren? "
            f"If yes, what are their names? If not, just say 'no'."
        ),
        8: _q_emergency_contact(ctx),
        9: (
            f"Does {addr} have any health conditions I should be aware of? "
            f"For example: diabetes, blood pressure, heart condition, arthritis, knee pain.\n\n"
            f"This helps me be more thoughtful in conversation. "
            f"You can say 'none' if there are none."
        ),
        10: (
            f"Does {addr} take any medicines regularly?\n\n"
            f"If yes, which ones and at what times? "
            f"For example: 'metformin 8am and 8pm, atorvastatin at night'.\n\n"
            f"If no medicines, just say 'no'. "
            f"If you'd like to add them later, just say 'I don't know yet'."
        ),
        11: (
            f"What kind of music does {addr} love? "
            f"(Old Bollywood, classical, ghazals, devotional, folk — "
            f"whatever comes to mind.)"
        ),
        # Step 12 — MERGED topics + news interests (22 Apr 2026).
        # One answer populates both favourite_topics and news_interests columns.
        12: (
            f"What topics does {addr} enjoy — in conversation or in the news?\n\n"
            f"For example: cricket, Bollywood, cooking, history, movies, "
            f"religion, travel, children, grandchildren, family, politics, "
            f"business — anything that lights them up."
        ),
        13: (
            f"What is {addr}'s religion or faith, if any? "
            f"This helps me be respectful of their beliefs and mark "
            f"the right festivals and occasions. "
            f"You can say 'prefer not to say'."
        ),
        # Step 14 RETIRED — merged into step 12 above. handle_onboarding_answer
        # skips 12 → 15 directly. This entry stays as a defensive fallthrough
        # for any user row already sitting at step 14 when this deploys.
        14: (
            f"What is {addr}'s religion or faith, if any? "
            f"This helps me be respectful of their beliefs and mark "
            f"the right festivals and occasions. "
            f"You can say 'prefer not to say'."
        ),
        15: (
            f"How should I approach {addr}? I can be:\n\n"
            f"• *Friend* — warm, peer-to-peer, easy\n"
            f"• *Caring Child* — respectful, attentive, gentle\n"
            f"• *Grandchild* — playful, devoted, full of love\n"
            f"• *Assistant* — helpful, calm, practical\n\n"
            f"Which feels most right for {addr}?"
        ),
        16: (
            f"What name would you like {addr} to call me? "
            f"I go by Saathi by default — but they can name me "
            f"whatever feels personal and warm to them. "
            f"Meera, Gopal, Kamla — anything. "
            f"(Or just say 'Saathi' to keep the default.)"
        ),
        17: (
            f"What time in the morning would you like me to check in with {addr}?\n\n"
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
            f"Last one — I can do a gentle check-in with {addr} three times "
            f"a day (morning, afternoon, evening) and quietly let you know "
            f"if they don't respond. It's a simple safety net called a heartbeat check.\n\n"
            f"Would you like to enable this? (yes / no)"
        ),
    }
    return questions[step]


def _q_emergency_contact(ctx: dict) -> str:
    """
    Step 8 question — offers the setup person as the default emergency contact
    when we captured a phone at step 0. Avoids making the child type their own
    name + number twice in a 5-minute flow.
    """
    setup_name  = (ctx.get("setup_name") or "").strip()
    setup_phone = (ctx.get("setup_phone") or "").strip()
    first = setup_name.split()[0] if setup_name else ""

    if first and setup_phone:
        return (
            f"Who should I contact in an emergency?\n\n"
            f"Shall I use you — *{first}, {setup_phone}*? "
            f"Reply 'yes' to confirm, or share a different name and number "
            f"(e.g. 'Priya — 9876543210')."
        )
    if first and not setup_phone:
        return (
            f"Who should I contact in an emergency?\n\n"
            f"Shall I use you, *{first}*? If yes, please share your phone number. "
            f"Or share a different name and number (e.g. 'Priya — 9876543210')."
        )
    return (
        "Who should I contact in an emergency? "
        "Please share a name and phone number. "
        "For example: 'Priya — 9876543210'."
    )


# ---------------------------------------------------------------------------
# Resume prompt — if /start is sent again mid-onboarding
# ---------------------------------------------------------------------------

def get_resume_prompt(user_id: int, step: int, setup_mode: str = None) -> str:
    if setup_mode == "self":
        q = _self_setup_question(step)
        return q or "We're almost done — just a few more things to set up."
    ctx = _ctx.setdefault(user_id, {})
    _rehydrate_family_ctx(user_id, ctx)
    addr = _address(ctx)
    return (
        f"We're still setting {addr} up — let's continue from where we left off.\n\n"
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


def _rehydrate_family_ctx(user_id: int, ctx: dict) -> None:
    """
    Populate ctx from the DB so later questions can render the senior's
    address, reuse the setup person's name/phone, etc. across process
    restarts. ctx is module-level in-memory state — this is the one-line
    guard against losing it mid-onboarding.
    """
    if "senior_name" in ctx and "salutation" in ctx and "setup_name" in ctx:
        return
    try:
        with get_connection() as conn:
            urow = conn.execute(
                "SELECT name, preferred_salutation, bot_name FROM users "
                "WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if urow:
                if urow["name"]:
                    ctx.setdefault("senior_name", urow["name"])
                if urow["preferred_salutation"]:
                    ctx.setdefault("salutation", urow["preferred_salutation"])
                if urow["bot_name"]:
                    ctx.setdefault("bot_name", urow["bot_name"])
            srow = conn.execute(
                "SELECT name, phone FROM family_members "
                "WHERE user_id = ? AND is_setup_user = 1 LIMIT 1",
                (user_id,),
            ).fetchone()
            if srow:
                if srow["name"]:
                    ctx.setdefault("setup_name", srow["name"])
                if srow["phone"]:
                    ctx.setdefault("setup_phone", srow["phone"])
    except Exception as e:
        logger.warning("ONBOARDING | rehydrate failed for user_id=%s: %s", user_id, e)


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
    # Rehydrate ctx so later questions can use the senior's address + reuse
    # setup person's name/phone on emergency contact step.
    _rehydrate_family_ctx(user_id, ctx)

    # Language step gate (pilot scope: English / Hindi / Hinglish only).
    # If the answer is an unsupported language, do NOT save and do NOT
    # advance — send the polite refusal and hold at step 4.
    if step == 4 and _parse_language(text) == _UNSUPPORTED_LANG:
        logger.info(
            "ONBOARDING | user_id=%s | mode=family | step=4 unsupported language: %r",
            user_id, text,
        )
        return POLITE_UNSUPPORTED_LANGUAGE_MESSAGE

    # Step 10 medicine-unknown branch — warm ack, stay on step 10 logic,
    # but advance so the flow continues. The deferred flag is surfaced in
    # the completion message.
    _save_answer(user_id, step, text, ctx)
    logger.info("ONBOARDING | user_id=%s | mode=family | step=%d answered", user_id, step)

    next_step = step + 1

    # Step 14 is retired — news_interests is populated at step 12.
    # Anyone reaching step 13 advances straight to 15.
    if next_step == 14:
        next_step = 15

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
    # Language step gate (pilot scope: English / Hindi / Hinglish only).
    # If the answer is an unsupported language, do NOT save and do NOT
    # advance — send the polite refusal and hold at step 4.
    if step == 4 and _parse_language(text) == _UNSUPPORTED_LANG:
        logger.info(
            "ONBOARDING | user_id=%s | mode=self | step=4 unsupported language: %r",
            user_id, text,
        )
        return POLITE_UNSUPPORTED_LANGUAGE_MESSAGE

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
        canonical = canonicalize_city(t)
        if t.strip().lower() not in CITY_ALIASES:
            logger.warning(
                "ONBOARDING | user_id=%s | mode=self | city not in alias map: %r → stored as %r",
                user_id, t, canonical,
            )
        update_user_fields(user_id, city=canonical)
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
    tl = t.lower()

    if step == 0:
        # Parse "rishi 9819787322" into name + phone. Title-case name so the
        # handoff greeting to the senior reads 'Rishi' not 'rishi'.
        name, phone = _parse_setup_person(t)
        if not name:
            # Fallback if parsing collapsed to empty — keep original input.
            name = t.title()
        ctx["setup_name"]  = name
        ctx["setup_phone"] = phone
        save_setup_person(user_id, name, phone)

    elif step == 1:
        name = t.title()
        ctx["senior_name"] = name
        update_user_fields(user_id, name=name)

    elif step == 2:
        # preferred_salutation = full display address used by Saathi when
        # addressing/referring to the senior. Stored AS-IS (no title-casing)
        # so 'Ji' stays 'Ji', 'Rameshji' stays 'Rameshji', etc.
        # Skip signals → fall back to default "{name} Ji".
        skip_signals = (
            "skip", "no", "nahi", "none", "no preference",
            "koi nahi", "koi bhi", "whatever", "anything",
            "no idea", "not sure", "dont know", "don't know",
        )
        if tl in skip_signals or not t:
            salutation = _default_address(ctx.get("senior_name", ""))
        else:
            salutation = t  # as-typed
        ctx["salutation"] = salutation
        update_user_fields(user_id, preferred_salutation=salutation)

    elif step == 3:
        canonical = canonicalize_city(t)
        if t.strip().lower() not in CITY_ALIASES:
            logger.warning(
                "ONBOARDING | user_id=%s | mode=family | city not in alias map: %r → stored as %r",
                user_id, t, canonical,
            )
        update_user_fields(user_id, city=canonical)

    elif step == 4:
        update_user_fields(user_id, language=_parse_language(t))

    elif step == 5:
        if tl in ("no", "no.", "nahi", "nahi.", "nahin", "passed away",
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
        if tl not in ("no", "no.", "none", "nahi", "nahin", "nope"):
            names = [n.strip().title() for n in t.split(",") if n.strip()]
            if names:
                add_family_members_bulk(user_id, names, "grandchild")

    elif step == 8:
        # Default-to-setup-person path — "yes" / "use me" / same first name
        # reuses the setup person's captured name + phone.
        setup_name  = (ctx.get("setup_name") or "").strip()
        setup_phone = (ctx.get("setup_phone") or "").strip()
        setup_first = setup_name.split()[0].lower() if setup_name else ""

        affirm_signals = (
            "yes", "yes.", "yeah", "yup", "yep", "sure", "okay", "ok",
            "haan", "ha", "han", "haanji", "ji", "hanji", "bilkul",
            "use me", "me", "myself", "use you", "use yourself",
            "same", "same as above", "that's me", "thats me",
            "confirm", "confirmed", "correct",
        )
        # Child affirms AND we have phone → reuse setup person.
        if tl in affirm_signals and setup_first and setup_phone:
            save_emergency_contact(user_id, setup_name.title(), setup_phone)
            logger.info("ONBOARDING | user_id=%s | step 8 reused setup person as emergency", user_id)
            return
        # Child affirms + supplies phone in the same reply (e.g. "yes 9819787322"
        # or "sure, 9819787322"). Strict check: after stripping the leading
        # affirmation + separator, the remainder must be ONLY digits/separators.
        # Otherwise they're giving a different contact ("yes someone else 9876...")
        # and we must NOT reuse the setup person's name.
        if setup_first:
            affirm_strip_re = re.compile(
                r"^(yes|yeah|yup|yep|sure|okay|ok|haan|ha|han|haanji|hanji|ji|"
                r"bilkul|confirm|confirmed|correct|same)[\s\.\,\-—:!]+",
                re.IGNORECASE,
            )
            stripped = affirm_strip_re.sub("", t)
            if stripped != t and re.fullmatch(r"[\d\s\-—\+\(\)]+", stripped or ""):
                phone_match = re.search(r"\d{10,}", re.sub(r"[\s\-—]", "", t))
                if phone_match:
                    save_emergency_contact(user_id, setup_name.title(), phone_match.group())
                    logger.info("ONBOARDING | user_id=%s | step 8 reused setup name + new phone", user_id)
                    return
        # Otherwise, parse fresh name + phone from the reply (original behaviour).
        phone_match = re.search(r"\d{10,}", t.replace(" ", "").replace("-", ""))
        phone = phone_match.group() if phone_match else ""
        name = _extract_contact_name(t) or t
        save_emergency_contact(user_id, name, phone)

    elif step == 9:
        if tl not in ("none", "no", "nahi", "nothing", "no.", "nil"):
            update_user_fields(user_id, health_sensitivities=t)

    elif step == 10:
        # "I don't know yet" handler — don't silent-fail, flag for follow-up
        # via completion message. medicines_raw stays NULL; the completion
        # message reminds the child how to add medicines later.
        if _is_medicines_unknown(t):
            ctx["medicines_deferred"] = True
            logger.info("ONBOARDING | user_id=%s | step 10 medicines deferred ('%s')", user_id, t[:40])
            return
        if tl not in ("no", "nahi", "none", "nothing", "no.", "nil"):
            update_user_fields(user_id, medicines_raw=t)

    elif step == 11:
        update_user_fields(user_id, music_preferences=t)

    elif step == 12:
        # MERGED (22 Apr 2026) — one answer, two columns.
        # favourite_topics → DeepSeek conversation context.
        # news_interests   → fetch_news() query filter.
        # Acceptable risk: if the answer is non-news-fetchable
        # ('grandchildren, cooking'), news headlines silently degrade. Not a
        # crash. Pilot learning will tell us whether to split back to two
        # questions or add a small DeepSeek classifier in v1.1.
        if tl not in ("not interested", "none", "no", "nahi", "skip"):
            update_user_fields(
                user_id,
                favourite_topics=t,
                news_interests=t,
            )

    elif step == 13:
        if tl != "prefer not to say":
            update_user_fields(user_id, religion=t)

    elif step == 14:
        # Step 14 RETIRED — news_interests now populated at step 12.
        # This branch is a no-op kept as a defensive fallthrough for any
        # user row already sitting at step 14 when this change deploys.
        pass

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
    bot_name    = ctx.get("bot_name", "Saathi")
    first       = setup_name.split()[0] if setup_name else ""
    # Display address — "Durga Ji" by default, or whatever the child chose at step 2.
    addr        = _address(ctx)

    # Optional gentle reminder when the child deferred medicines at step 10.
    medicines_note = ""
    if ctx.get("medicines_deferred"):
        medicines_note = (
            f"One small thing — you weren't sure about {addr}'s medicines earlier. "
            f"No rush. I'll gently ask {addr} about them once we've started "
            f"chatting, so you don't need to do anything.\n\n"
        )

    return (
        f"That's everything{', ' + first if first else ''}! 🙏\n\n"
        f"I'm all set for {addr}. The next time they message me, "
        f"I'll greet them warmly and personally.\n\n"
        f"{medicines_note}"
        f"A couple of things you might want to do:\n"
        f"• Save this Telegram contact on their phone as *{bot_name}*\n"
        f"• Let them know their companion is ready and waiting for them\n\n"
        f"If you'd like to link another family member later (the other "
        f"parent, a sibling, anyone), type /familycode anytime and I'll give "
        f"you a short message you can forward to them.\n\n"
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

    Self-setup is FIRST-PERSON — the senior is forwarding the block
    themselves. The body speaks as the senior ("I've started using
    Saathi..."). No relational term needed (first person dodges the
    pronoun/gender problem entirely).
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

    from family import build_family_invite_block_first_person
    invite_block = build_family_invite_block_first_person(
        code=code,
        recipient_name=emergency_name,
    )

    return (
        "That's everything — thank you for telling me. 🙏\n\n"
        f"Would you like {emergency_name} to get a short weekly note about "
        f"how you're doing, and be able to send you a quick message through "
        f"me anytime?\n\n"
        f"If so, copy the message below and forward it to {emergency_name} "
        f"on WhatsApp, iMessage, or however you usually chat:\n\n"
        f"— — —\n\n"
        f"{invite_block}\n\n"
        f"— — —\n\n"
        "No rush. I'll be here whenever you'd like to talk."
    )


def _get_senior_name_from_db(user_id: int) -> Optional[str]:
    """DB fallback for senior's name — survives process restart during bridge defer."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM users WHERE user_id = ?", (user_id,),
            ).fetchone()
            return row["name"] if row and row["name"] else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

# Pilot scope: English / Hindi / Hinglish only. Anything else returns the
# _UNSUPPORTED_LANG sentinel and the caller must NOT advance the step.
_UNSUPPORTED_LANG = "UNSUPPORTED"

# Exact-match short-form map. Checked first so 'eng'/'hin'/'mix' do not fall
# through to substring matching (which was the bug before 22 Apr 2026).
_LANG_EXACT_ALIASES = {
    # English
    "eng": "english",
    "english": "english",
    "angrezi": "english",
    "angreji": "english",
    "inglish": "english",
    "e": "english",
    # Hindi
    "hin": "hindi",
    "hindi": "hindi",
    "हिंदी": "hindi",
    "हिन्दी": "hindi",
    "h": "hindi",
    # Hinglish
    "mix": "hinglish",
    "mixed": "hinglish",
    "both": "hinglish",
    "dono": "hinglish",
    "hinglish": "hinglish",
    "hindi english": "hinglish",
    "english hindi": "hinglish",
    "hindi and english": "hinglish",
    "english and hindi": "hinglish",
    "hindi or english": "hinglish",
    "english or hindi": "hinglish",
    "mix of both": "hinglish",
    "mix of hindi and english": "hinglish",
    "hindi plus english": "hinglish",
    "english plus hindi": "hinglish",
}

# Tokens that indicate an unsupported language. If any of these appears as a
# substring of the user's input (and no supported-language signal matched
# first), we return UNSUPPORTED rather than storing the raw text.
_UNSUPPORTED_LANG_TOKENS = (
    "tamil", "telugu", "bengali", "bangla", "marathi", "gujarati",
    "punjabi", "kannada", "malayalam", "urdu", "oriya", "odia",
    "assamese", "sanskrit", "konkani", "sindhi", "kashmiri",
    "maithili", "bhojpuri", "nepali",
    "spanish", "french", "german", "italian", "chinese", "mandarin",
    "cantonese", "japanese", "korean", "portuguese", "arabic",
    "russian", "dutch", "swedish", "turkish", "thai", "vietnamese",
)


def _parse_language(text: str) -> str:
    """
    Parse a free-form language answer into one of: 'english', 'hindi',
    'hinglish', or the sentinel _UNSUPPORTED_LANG.

    Pilot scope (22 Apr 2026): we only support English, Hindi, Hinglish.
    Anything else (Tamil, Bengali, Marathi, French, gibberish, etc.) returns
    _UNSUPPORTED_LANG. The caller (handle_onboarding_answer /
    _handle_self_setup_answer) is responsible for holding the user at the
    language step until they pick one of the three.

    Ordering matters:
      1. Exact short-form match — 'eng', 'hin', 'mix', 'both' must resolve
         to supported languages even though they don't contain 'hindi' /
         'english' as substrings.
      2. Compound checks — 'hindi and english' must resolve to hinglish
         BEFORE single-language substring matching (else it would match
         'hindi' first and return 'hindi').
      3. Single-language substring match.
      4. Unsupported-language token match.
      5. Default to UNSUPPORTED (safer than silently storing raw text).
    """
    t = (text or "").strip().lower()
    if not t:
        return _UNSUPPORTED_LANG

    # 1. Exact short-form match.
    if t in _LANG_EXACT_ALIASES:
        return _LANG_EXACT_ALIASES[t]

    # 2. Compound-language substring (BEFORE single-language checks).
    has_hindi = "hindi" in t or "हिंदी" in t or "हिन्दी" in t
    has_english = "english" in t or "angrezi" in t or "angreji" in t or "inglish" in t
    if has_hindi and has_english:
        return "hinglish"
    if "hinglish" in t or "mix of" in t:
        return "hinglish"

    # 3. Single-language substring.
    if has_english:
        return "english"
    if has_hindi:
        return "hindi"

    # 4. Unsupported-language detection.
    for tok in _UNSUPPORTED_LANG_TOKENS:
        if tok in t:
            return _UNSUPPORTED_LANG

    # 5. Unknown input — treat as unsupported, NOT as raw text.
    return _UNSUPPORTED_LANG


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
