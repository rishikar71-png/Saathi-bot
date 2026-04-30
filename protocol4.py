"""
Protocol 4 — Sexual / Romantic Boundary Handler

Runs AFTER Protocol 1 (mental health crisis), BEFORE Protocol 3 (financial/legal).
Returns a response string if a romantic or sexual signal is detected, or None if clear.

Posture: Acknowledge the warmth without reciprocating romantically.
Reframe the relationship gently. Do not shame or lecture.
Leave the door open for companionship.
"""

import re
import logging
from typing import Optional

from database import log_protocol_event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trigger phrases — phrase matching, not single keywords, to avoid false positives.
# "I love you" fires. "I love cricket" does not.
# ---------------------------------------------------------------------------

_TRIGGERS = [
    # English — explicit
    "i love you",
    "i'm in love with you",
    "in an intimate way",
    "intimate with you",
    "physical closeness",
    "touch you",
    "kiss you",
    "hold you",
    "sleep with you",
    "romantic",
    "you turn me on",
    "you're attractive",
    "you are attractive",
    "be my lover",
    "be my partner",
    "be my girlfriend",
    "be my boyfriend",
    "be my wife",
    "be my husband",
    "marry me",
    "make love",
    "sexual",
    "sexy",
    "sexxy",
    "put out",
    "a little more than friends",
    "intimate",
    "naughty",
    # English — paid/transactional services
    # Bug P: bare "escort" was too ambiguous (police escort, VIP escort,
    # security escort, escorted by). Replaced with specific paid-service
    # phrases.
    "escort service",
    "escort agency",
    "find me an escort",
    "hire an escort",
    "call girl",
    "sex worker",
    "red light",
    "get sex",
    "physical services",
    "sex for money",
    "pay for sex",
    # English — soft escalation (catches "intimate way" pattern)
    "more than a companion",
    "more than a friend",
    "special connection",
    "feel something for you",
    "feelings for you",
    "attracted to you",
    "closer to you",
    "want to be with you",
    "need you in a different way",
    # Hindi / Hinglish
    "tum mujhe pasand ho",
    "tumse pyaar",
    "tumse mohabbat",
    "tumhare kareeb",
    "tumhe chhoona",
    "pyaar karo",
    "mujhse shaadi",
    "ek alag rishta",
    "sirf dost nahi",
]

_SERVICES_TRIGGERS = {
    "escort service",
    "escort agency",
    "find me an escort",
    "hire an escort",
    "call girl",
    "sex worker",
    "red light",
    "get sex",
    "physical services",
    "sex for money",
    "pay for sex",
}

_PROTOCOL4_SERVICES_ENGLISH = (
    "That's not something I'm able to help with, and I wouldn't want to point you somewhere "
    "that could be unsafe. If you're feeling lonely or longing for closeness — those are real "
    "feelings, and we can talk about that. I'm here for that part."
)

_PROTOCOL4_ENGLISH = (
    "I'm here as a friend, and that's the space I'm meant to be in for you.\n\n"
    "That warmth you're feeling — I understand it. "
    "Let's keep our connection warm and simple, and I'm always here when you want to talk."
)

_PROTOCOL4_HINDI = (
    "Jo aapne kaha, usmein sachchi bhavna hai — aur main usse halka nahi leta.\n\n"
    "Lekin main woh nahi ban sakta aapke liye. Main ek saathi hoon — baat karne ke liye, "
    "saath baithne ke liye, din ki baatein karne ke liye. Yeh nahi badlega.\n\n"
    "Agar kuch aur baat karna chahein, toh main yahan hoon."
)


def check_protocol4(user_id: int, text: str, language: str = "english") -> Optional[str]:
    """
    Check the message for romantic or sexual signals.

    Returns a response string in the user's language if Protocol 4 fires,
    or None if the message is clear.
    """
    t = text.lower().strip()
    # Bug P (30 Apr 2026): substring match made bare keywords leak. Word-
    # boundary regex for each trigger.
    matched = [
        trigger for trigger in _TRIGGERS
        if re.search(r"\b" + re.escape(trigger) + r"\b", t)
    ]
    if not matched:
        return None

    logger.warning(
        "PROTOCOL4 | user_id=%s | keywords=%s | language=%s",
        user_id, matched, language,
    )
    log_protocol_event(
        user_id=user_id,
        protocol_type="4",
        trigger_bucket="romantic_sexual",
        trigger_keywords=", ".join(matched),
        family_alerted=0,
    )

    if any(trigger in _SERVICES_TRIGGERS for trigger in matched):
        return _PROTOCOL4_SERVICES_ENGLISH

    lang = (language or "english").strip().lower()
    if lang in ("hindi", "hinglish", "hindi/english mix"):
        return _PROTOCOL4_HINDI
    return _PROTOCOL4_ENGLISH
