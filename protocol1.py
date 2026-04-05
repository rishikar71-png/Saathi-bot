"""
Protocol 1 — Mental Health Crisis Handler

Runs BEFORE DeepSeek on every incoming message.
Returns a response string if a crisis signal is detected, or None if clear.

Three stages:
  Stage 1 — Warm acknowledgement, stay present, invite more.
  Stage 2 — Gently surface iCall helpline + offer to contact family (with consent).
  Auto-escalate — Imminent-action language: family alert without waiting for consent.
              (Family alert mechanism is a stub — implemented in Module 13/14.)

Keyword matching is intentionally broad. False positives are handled gracefully
by Stage 1's warm, non-alarming response — a warm check-in is never harmful.
"""

import re
import logging
from typing import Optional
from database import log_protocol_event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword lists
# ---------------------------------------------------------------------------

# Stage 1 triggers — distress signals, exhaustion with life, hopelessness.
# Cast wide. A false positive just gets a warm, caring reply.
_STAGE1_PATTERNS = [
    # Hindi / Hinglish
    r"jeena nahi",
    r"jina nahi",
    r"jeene ka mann nahi",
    r"jine ka mann nahi",
    r"zindagi se thak",
    r"zindagi nahi chahiye",
    r"zindagi nahi chahta",
    r"zindagi nahi chahti",
    r"zindagi se pareshan",
    r"khatam kar",
    r"khatam ho jaye",
    r"khatam ho jana",
    r"sab khatam",
    r"mar jana",
    r"marna chahta",
    r"marna chahti",
    r"maut aaye",
    r"mujhe nahi rehna",
    r"rehna nahi chahta",
    r"rehna nahi chahti",
    r"koi fayda nahi",
    r"koi umeed nahi",
    r"ummeed nahi",
    r"sab bekar hai",
    r"sab bekaar",
    r"jine layak nahi",
    r"thak gaya hun zindagi",
    r"thak gayi hun zindagi",
    r"mann nahi lagta",
    r"aage nahi badh sakta",
    r"aage nahi badh sakti",
    r"akela feel",
    r"bahut akela",
    r"koi nahi hai",
    r"koi nahi mera",
    r"koi sunne wala nahi",
    r"koi samajhne wala nahi",
    r"bojh ban gaya",
    r"bojh ban gayi",
    r"sabke liye bojh",
    r"sab pareshan hain mere se",
    # English
    r"don'?t want to (live|go on|be here|exist)",
    r"want to die",
    r"wish i was dead",
    r"wish i were dead",
    r"no reason to live",
    r"no point (in living|going on|anymore)",
    r"better off (dead|without me)",
    r"can'?t go on",
    r"end (it|my life|everything|it all)",
    r"not worth living",
    r"life is (not worth|pointless|meaningless)",
    r"so tired of (living|life|everything)",
    r"tired of living",
    r"give up on life",
    r"nothing to live for",
    r"nobody would miss me",
    r"everyone would be better",
    r"burden (to everyone|to my family|on everyone)",
    r"feel so alone",
    r"completely alone",
]

# Auto-escalation triggers — explicit, imminent action language.
# Family is alerted immediately without waiting for consent.
_ESCALATION_PATTERNS = [
    # Hindi / Hinglish
    r"abhi khatam kar",
    r"aaj khatam kar",
    r"khatam kar lunga",
    r"khatam kar lungi",
    r"khatam karne wala",
    r"khatam karne wali",
    r"abhi mar",
    r"aaj mar",
    r"neend ki goli",
    r"dawa kha lunga",
    r"dawa kha lungi",
    r"bahut saari dawaiyaan",
    r"nadi mein",
    r"chhat se",
    r"ghar se bhaag",
    # English
    r"going to (kill|end|hurt) (myself|my life)",
    r"kill myself",
    r"end my life",
    r"hurt myself",
    r"take (all|the) (pills|tablets|medicine)",
    r"have a plan",
    r"already decided",
    r"tonight i (will|am going to)",
    r"say goodbye",
    r"final (message|note|goodbye)",
    r"jump",
    r"hang myself",
]

# Compile all patterns case-insensitively
_STAGE1_RE = [re.compile(p, re.IGNORECASE) for p in _STAGE1_PATTERNS]
_ESCALATION_RE = [re.compile(p, re.IGNORECASE) for p in _ESCALATION_PATTERNS]


# ---------------------------------------------------------------------------
# Response text
# ---------------------------------------------------------------------------

# Stage 1 — warm, present, no helpline yet. Feels like a caring friend.
_STAGE1_RESPONSE = (
    "Main yahan hoon. Aur main sun raha hoon — poori tarah se.\n\n"
    "Jo aap feel kar rahe hain, usse main samajhna chahta hoon. "
    "Kya aap mujhe thoda aur bata sakte hain — aaj kya hua, ya kaafi waqt se yeh feel ho raha hai?"
)

# Stage 2 — after a second trigger in the same session (or if Stage 1 wasn't enough).
# Surfaces iCall gently, offers family contact with consent.
_STAGE2_RESPONSE = (
    "Aap jo share kar rahe hain, usse main bahut seriously le raha hoon — "
    "aur main chahta hoon ki aap jaanein ki aap akele nahi hain.\n\n"
    "Ek baat poochhunga — kya kabhi aapne kisi aise insaan se baat ki hai "
    "jo bas sunne ke liye hi hota hai? iCall ek aisi jagah hai — "
    "wahan koi judge nahi karta, sirf sunte hain. "
    "Number hai: 9152987821. Yeh free hai aur Hindi mein baat kar sakte hain.\n\n"
    "Aur agar aap chahein, toh main aapke kisi apne ko — Priya ya kisi aur ko — "
    "quietly bata sakta hoon ki aapko aaj thodi zyada zaroorat hai unki. "
    "Kya aap chahenge?"
)

# Auto-escalation — used when imminent-action language is detected.
_ESCALATION_RESPONSE = (
    "Main yahan hoon. Abhi, is pal. Aap akele nahi hain.\n\n"
    "Jo aap feel kar rahe hain woh bahut bhaari hai — aur main chahta hoon ki "
    "koi aapke paas ho. Maine aapke apnon ko bata diya hai ki aapko abhi unki zaroorat hai.\n\n"
    "Aap yahan raho mere saath. Mujhe batao — abhi is pal aap kahan hain?"
)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def check_protocol1(
    user_id: int,
    text: str,
    session_trigger_count: int = 0,
) -> Optional[str]:
    """
    Check the message for Protocol 1 crisis signals.

    Args:
        user_id: Telegram user ID (for logging).
        text: The incoming message text.
        session_trigger_count: How many times Protocol 1 has already fired
            in this session (0 = first time). Caller tracks this.

    Returns:
        A response string if Protocol 1 fires, or None if the message is clear.
    """
    matched_keywords = _find_matches(text, _ESCALATION_RE)
    if matched_keywords:
        logger.warning(
            "PROTOCOL1 ESCALATION | user_id=%s | keywords=%s",
            user_id, matched_keywords,
        )
        log_protocol_event(
            user_id=user_id,
            protocol_type="1",
            trigger_bucket="escalation",
            trigger_keywords=", ".join(matched_keywords),
            family_alerted=1,
        )
        # TODO Module 13/14: trigger actual family alert here
        return _ESCALATION_RESPONSE

    matched_keywords = _find_matches(text, _STAGE1_RE)
    if matched_keywords:
        stage = 2 if session_trigger_count >= 1 else 1
        logger.warning(
            "PROTOCOL1 STAGE%d | user_id=%s | keywords=%s",
            stage, user_id, matched_keywords,
        )
        log_protocol_event(
            user_id=user_id,
            protocol_type="1",
            trigger_bucket=f"stage{stage}",
            trigger_keywords=", ".join(matched_keywords),
            family_alerted=0,
        )
        return _STAGE2_RESPONSE if stage == 2 else _STAGE1_RESPONSE

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_matches(text: str, patterns: list) -> list[str]:
    """Return list of pattern strings that matched (for logging)."""
    matched = []
    for regex in patterns:
        if regex.search(text):
            matched.append(regex.pattern)
    return matched
