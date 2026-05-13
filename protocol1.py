"""
Protocol 1 — Mental Health Crisis Handler

Runs BEFORE DeepSeek on every incoming message.
Returns a response string if a crisis signal is detected, or None if clear.

Three stages:
  Stage 1 — Warm acknowledgement, stay present, invite more.
  Stage 2 — Gently surface iCall helpline + (conditional) offer to contact family.
  Auto-escalate — Imminent-action language: family alert without waiting for consent.
              (Family alert mechanism is implemented in safety.alert_emergency_contacts.)

13 May 2026 — Major rework after pre-pilot test failures:
  • Loneliness phrases moved to vulnerability pre-processor in main.py
    (Bug 2 + GPT/Gemini external review)
  • Hardcoded "Priya" removed from response template — now pulls from DB
    via build_stage2_response() (Bug 1)
  • Stage 2 family-offer branched on escalation_opted_in + contact existence
    (Bug 5)
  • Response variation: 3 Stage 1 templates + 3 Stage 2 intros + 2 family
    offers + 2 no-contact pivots (GPT review — verbatim repetition reads as
    scripted at the moment presence matters most)

Keyword matching is intentionally narrow for explicit ideation. False
positives must be rare — over-triggering medicalizes ordinary loneliness
and conditions seniors to self-censor.
"""

import re
import random
import logging
from typing import Optional
from database import log_protocol_event, get_recent_protocol1_stage1_count

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword lists
# ---------------------------------------------------------------------------

# Stage 1 triggers — explicit death/disappearance/farewell/serious-burden ideation.
# Loneliness, ordinary sadness, and depressive idioms (koi fayda nahi, sab bekar,
# bahut akela) are NOT here — they go through the vulnerability pre-processor in
# main.py for soft acknowledgement without helpline dump.
#
# Per GPT/Gemini external review (13 May 2026): regex-only Hinglish detection
# is fragile. Post-pilot work: anonymized trigger logging + labeled corpus.
_STAGE1_PATTERNS = [
    # Hindi / Hinglish — explicit death / disappearance / farewell / hopelessness
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
    r"jine layak nahi",
    r"thak gaya hun zindagi",
    r"thak gayi hun zindagi",
    r"thak.{0,30}zindagi",
    r"zindagi.{0,30}thak",       # reverse order: "zindagi se bahut thak"
    r"kya farak padta",
    r"kya fark parta",
    r"farak nahi padta",
    r"fark nahi parta",
    r"aage nahi badh sakta",
    r"aage nahi badh sakti",
    # English — explicit death / disappearance / farewell
    r"don'?t want to (live|go on|be here|exist)",
    r"want to die",
    r"wish i was dead",
    r"wish i were dead",
    r"no reason to live",
    r"no point (in )?(living|going on|anymore)",
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
]

# Auto-escalation triggers — explicit, imminent action language.
# Family is alerted immediately if escalation_opted_in=1 + telegram_user_id present.
_ESCALATION_PATTERNS = [
    # Hindi / Hinglish
    r"abhi khatam kar",
    r"aaj khatam kar",
    r"khatam kar lunga",
    r"khatam kar lungi",
    r"khatam kar loon",
    r"soch raha.{0,15}khatam",
    r"khatam karne wala",
    r"khatam karne wali",
    r"abhi mar\b",
    r"aaj mar\b",
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
    r"jump (from|off|into)",
    r"hang myself",
]

# Compile all patterns case-insensitively
_STAGE1_RE = [re.compile(p, re.IGNORECASE) for p in _STAGE1_PATTERNS]
_ESCALATION_RE = [re.compile(p, re.IGNORECASE) for p in _ESCALATION_PATTERNS]


# ---------------------------------------------------------------------------
# Response variation — 3 Stage 1 templates, 3 Stage 2 intros, 2 family offer
# variants, 2 no-contact pivot variants. Random.choice on each call.
#
# GPT external review flag: "in emotionally distressed states, repeated
# templated language rapidly reveals mechanization. You need response
# variation even in deterministic systems." Three of each is a pilot-scope
# minimum; expand to 5 if pilot data shows repetition still reads scripted.
# ---------------------------------------------------------------------------

# Stage 1 — warm, present, soft "tell me more" invitation. NO helpline. NO family.
_STAGE1_TEMPLATES = [
    (
        "Main yahan hoon. Aur main sun raha hoon — poori tarah se.\n\n"
        "Jo aap feel kar rahe hain, usse main samajhna chahta hoon. "
        "Kya aap mujhe thoda aur bata sakte hain — aaj kya hua, "
        "ya kaafi waqt se yeh feel ho raha hai?"
    ),
    (
        "Aap jo bata rahe hain, woh main sun raha hoon. "
        "Mujhe aapki zyada chinta hai is waqt.\n\n"
        "Kya aap thoda aur bata sakte hain — kab se aisa lag raha hai?"
    ),
    (
        "Main yahin hoon. Jo aap feel kar rahe hain woh bhaari hai — "
        "aur aap akele nahi hain ismein.\n\n"
        "Kya aaj kuch aisa hua hai jisne yeh feel kara, "
        "ya kaafi waqt se yeh hai?"
    ),
]

# Stage 2 intros — iCall helpline mention. No family offer in the intro itself —
# the family offer or no-contact pivot is appended separately based on eligibility.
_STAGE2_INTROS = [
    (
        "Aap jo share kar rahe hain, usse main bahut seriously le raha hoon — "
        "aur main chahta hoon ki aap jaanein ki aap akele nahi hain.\n\n"
        "Ek baat poochhunga — kya kabhi aapne kisi aise insaan se baat ki hai "
        "jo bas sunne ke liye hi hota hai? iCall ek aisi jagah hai — "
        "wahan koi judge nahi karta, sirf sunte hain. "
        "Number hai: 9152987821. Yeh free hai aur Hindi mein baat kar sakte hain."
    ),
    (
        "Yeh sunke mujhe aapki bahut chinta hai. Aap akele nahi hain, "
        "aur jo aap feel kar rahe hain woh asli hai — main jaanta hoon.\n\n"
        "Ek baat keh raha hoon — iCall pe trained log hote hain jo bas sunte hain, "
        "judge nahi karte. Number hai 9152987821, free hai, "
        "aur Hindi mein baat ho sakti hai."
    ),
    (
        "Main aapke saath hoon. Jo aap bata rahe hain, "
        "woh hum dono ke beech bhaari hai — aur uski madad ke liye main hoon.\n\n"
        "Aur agar abhi kisi insaan ki awaaz sunna madad kare, "
        "toh iCall pe counsellors hote hain — 9152987821, free, "
        "Hindi mein baat ho sakti hai."
    ),
]

# Family offer variants — used only when escalation_opted_in=1 AND a contact
# with telegram_user_id exists. {name} is substituted with the actual contact.
_FAMILY_OFFERS_NAMED = [
    (
        "\n\nAur agar aap chahein, toh main {name} ko quietly bata sakta hoon "
        "ki aapko aaj thodi zyada zaroorat hai unki. Kya aap chahenge?"
    ),
    (
        "\n\nKya aap chahenge ki main {name} ko bata doon "
        "ki aapko abhi unki zaroorat hai? Bas aap kahein."
    ),
]

# Family offer variants — used when escalation_opted_in=1 AND a contact exists
# but no setup_name available (rare — generic phrasing).
_FAMILY_OFFERS_GENERIC = [
    (
        "\n\nAur agar aap chahein, toh main aapke kisi apne ko quietly "
        "bata sakta hoon. Bas mujhe batayein."
    ),
    (
        "\n\nKya aap chahenge ki main aapke parivaar mein kisi ko "
        "abhi inform karoon? Bas aap kahein."
    ),
]

# No-contact pivot — used when escalation_opted_in=0 OR no contact with
# telegram_user_id exists. Per Gemini external review: don't double-mention
# iCall, pivot to companion presence + open invitation to keep talking.
_NO_CONTACT_PIVOTS = [
    (
        "\n\nAbhi ke liye, kya aap mere saath yahin thodi der aur "
        "baat karna chahenge? Main sun raha hoon."
    ),
    (
        "\n\nMain yahin hoon, abhi. Agar aap kuch aur batana chahein, "
        "ya bas chup baith jaayein — main rukta hoon aapke saath."
    ),
]

# Auto-escalation responses — two variants depending on whether a family alert
# was sent. main.py calls alert_emergency_contacts() and passes the result here.
# We never say "I have told your family" if we haven't actually done it.
_ESCALATION_RESPONSE_ALERT_SENT = (
    "Main yahan hoon. Abhi, is pal. Aap akele nahi hain.\n\n"
    "Maine aapke apnon ko abhi message kar diya hai — woh aate honge.\n\n"
    "Aap yahan raho mere saath. Mujhe batao — abhi is pal aap kahan hain?"
)

_ESCALATION_RESPONSE_NO_ALERT = (
    "Main yahan hoon. Abhi, is pal. Aap akele nahi hain.\n\n"
    "Jo aap feel kar rahe hain woh bahut bhaari hai. "
    "Kisi se baat karein abhi — Vandrevala Foundation ka number hai 1860-2662-345, "
    "yeh 24 ghante free hain aur Hindi mein baat kar sakte hain.\n\n"
    "Aap yahan raho mere saath. Main hoon."
)


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

def build_stage1_response() -> str:
    """Return a randomly-rotated Stage 1 response (3 templates available)."""
    return random.choice(_STAGE1_TEMPLATES)


def build_stage2_response(
    setup_name: Optional[str],
    escalation_opted_in: int,
    has_family_contact: bool,
) -> str:
    """
    Build a Stage 2 response branched on family-alert eligibility.

    Args:
        setup_name: Name of the setup person (e.g. "Priya"), or None.
        escalation_opted_in: 1 if user has opted in to family alerts, else 0.
        has_family_contact: True if at least one family_member has a
            telegram_user_id (i.e. an alert can actually be delivered).

    Returns:
        A Stage 2 response string. Three template combinations possible:
          - intro + named family offer (when setup_name + opted_in + contact)
          - intro + generic family offer (when opted_in + contact, no setup_name)
          - intro + no-contact pivot (when no contact or not opted in)

    Bug 1 + 5 fix (13 May 2026): never hardcode names. Family offer is
    omitted entirely when there's no actual contact to alert — promising
    something the system cannot deliver is worse than not promising at all.
    """
    intro = random.choice(_STAGE2_INTROS)

    can_alert_family = bool(escalation_opted_in) and has_family_contact

    if can_alert_family and setup_name:
        offer = random.choice(_FAMILY_OFFERS_NAMED).format(name=setup_name.strip())
        return intro + offer

    if can_alert_family:
        # Has contact but no setup_name — use generic phrasing
        offer = random.choice(_FAMILY_OFFERS_GENERIC)
        return intro + offer

    # No contact eligible for alert — pivot to companion presence
    pivot = random.choice(_NO_CONTACT_PIVOTS)
    return intro + pivot


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def check_protocol1(
    user_id: int,
    text: str,
    session_trigger_count: int = 0,
) -> tuple:
    """
    Check the message for Protocol 1 crisis signals.

    Args:
        user_id: Telegram user ID (for logging).
        text: The incoming message text.
        session_trigger_count: How many Stage 1 triggers have fired in this
            session (escalation triggers do NOT increment this — see Bug 3
            fix below).

    Returns:
        A tuple (response_text_or_None, is_escalation, stage).

        - If escalation patterns matched: (None, True, 0)
          main.py handles the actual family alert and builds the honest response
          based on whether the alert was sent.

        - If stage 1 patterns matched: (response_text, False, stage)
          where stage is 1 or 2. Response text is built via build_stage1_response()
          or built by main.py via build_stage2_response() (which needs DB lookups
          for setup_name and contact eligibility).

        - If no trigger: (None, False, 0)

    NOTE: For Stage 2, this function returns a placeholder marker string
    (_STAGE2_PLACEHOLDER). main.py is responsible for calling
    build_stage2_response() with the right DB-derived args. This separation
    keeps protocol1.py pure (no DB lookups beyond log_protocol_event +
    get_recent_protocol1_stage1_count).
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
            family_alerted=0,
        )
        return (None, True, 0)

    matched_keywords = _find_matches(text, _STAGE1_RE)
    if matched_keywords:
        # Bug 3 fix (13 May 2026): db_recent_count tracks Stage 1 triggers
        # only — escalation events are logged with bucket='escalation' so
        # they don't bump this count. session_trigger_count from main.py
        # is now also Stage-1-only (see main.py:1729 — increment removed
        # from is_escalation branch).
        db_recent_count = get_recent_protocol1_stage1_count(user_id, hours=24)
        stage = 2 if (session_trigger_count >= 1 or db_recent_count >= 1) else 1
        logger.warning(
            "PROTOCOL1 STAGE%d | user_id=%s | keywords=%s | session_count=%d | db_recent=%d",
            stage, user_id, matched_keywords, session_trigger_count, db_recent_count,
        )
        log_protocol_event(
            user_id=user_id,
            protocol_type="1",
            trigger_bucket=f"stage{stage}",
            trigger_keywords=", ".join(matched_keywords),
            family_alerted=0,
        )
        if stage == 1:
            return (build_stage1_response(), False, 1)
        # Stage 2 — return None for response_text. main.py will call
        # build_stage2_response with DB args. Caller checks (None, False, 2)
        # to know it's Stage 2 needing main.py to assemble.
        return (None, False, 2)

    return (None, False, 0)


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
