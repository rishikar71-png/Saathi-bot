"""
Protocol 3 — Financial & Legal Sensitivity Handler

Runs BEFORE DeepSeek on every incoming message, AFTER Protocol 1.
Returns a response string if a financial/legal signal is detected, or None if clear.

Three trigger buckets (from CLAUDE.md):
  Bucket 1 — External financial pressure: someone asking the senior for money,
              loans, investments — including guilt-wrapped versions.
  Bucket 2 — Asset & inheritance decisions: giving property, transferring assets,
              changing a will, cutting someone in/out.
  Bucket 3 — Will & estate planning: making a will, who to include, what happens
              to savings.

Response posture (all three buckets, five steps):
  1. Hear them fully (response acknowledges what they shared).
  2. Name the weight — 'this sounds like something you've been carrying.'
  3. Honest limits — Saathi is a companion, not an advisor.
  4. Point to a real human — family lawyer, CA, trusted sibling. Not a helpline.
  5. Leave the door open — offer to talk about how it feels, not the decision.

Critical rule: completely neutral on what the senior does with their money.
Never validate or invalidate the transaction. Never take sides.
"""

import re
import logging
from typing import Optional
from database import log_protocol_event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flat keyword list — simple case-insensitive substring match.
# Runs BEFORE the bucket regex patterns as a broad first pass.
# ---------------------------------------------------------------------------

FINANCIAL_KEYWORDS = [
    "invest", "investment", "investing",
    "business", "scheme", "shares", "stocks",
    "mutual fund", "fixed deposit", "fd",
    "loan", "borrow", "lend", "lending",
    "property", "real estate", "savings",
    "insurance", "policy", "fraud", "scam",
    "cheating", "money", "lakhs", "crore",
    "rupees", "₹", "paise",
    "paisa", "nivesh", "vyapar",
    "dhandha", "karz", "udhaar", "zameen",
    "bima", "yojana",
]

# ---------------------------------------------------------------------------
# Keyword lists — three buckets (regex patterns for complex phrase matching)
# ---------------------------------------------------------------------------

# Bucket 1 — External financial pressure: someone asking for money/investment.
# Includes guilt-wrapped requests from family members.
_BUCKET1_PATTERNS = [
    # Hindi / Hinglish
    r"paisa dena",
    r"paise dena",
    r"paise maang",
    r"paisa maang",
    r"paisa udhaar",
    r"paise udhaar",
    r"udhaar dena",
    r"udhaar lena",
    r"loan dena",
    r"loan maanga",
    r"loan ke liye",
    r"invest karna chahte",
    r"invest kar do",
    r"nivesh karna",
    r"nivesh karo",
    r"business mein paisa",
    r"business ke liye paisa",
    r"business mein lagao",
    r"mujhe paisa chahiye",
    r"hume paisa chahiye",
    r"mera business",
    r"meri company",
    r"share kharido",
    r"share lelo",
    r"share mein lagao",
    r"mutual fund",
    r"fd karao",
    r"fd tod do",
    r"fixed deposit tod",
    r"apna paisa do",
    r"thoda paisa de do",
    r"ek baar paisa de do",
    # English
    r"\brs\.",          # "Rs." currency abbreviation — word-boundary prevents matching "hours."
    r"lend (me|us|him|her|them)",
    r"borrow(ing)? (money|from you|some)",
    r"asking (me|us) for money",
    r"asked (me|us) for money",
    r"(wants?|need) money from (me|us)",
    r"invest(ment)? (in|into) (my|his|her|their)",
    r"put (your|my) money (in|into)",
    r"business (needs?|requires?) (money|funds|investment)",
    r"business (is|going) (struggling|under)",
    r"(send|give|transfer) (him|her|them) money",
    r"(send|give|transfer) money",
    r"financial (help|support|assistance)",
    r"loan (for|to)",
    r"(take|break) (my|your|the) (FD|fixed deposit|savings)",
]

# Bucket 2 — Asset & inheritance decisions.
_BUCKET2_PATTERNS = [
    # Hindi / Hinglish
    r"property (dena|de do|transfer)",
    r"makaan (dena|de do|transfer|likhna)",
    r"zameen (dena|de do|transfer|likhna)",
    r"ghar (likhna|unke naam|ke naam)",
    r"unke naam kar",
    r"uske naam kar",
    r"naam transfer",
    r"property transfer",
    r"virasat",
    r"varasat",
    r"wirasat",
    r"hissa dena",
    r"hissa baantna",
    r"barabar baantna",
    r"khaarij karna",
    r"will mein naam",
    r"will se nikalna",
    r"will mein daalna",
    r"succession",
    r"uttaradhikaar",
    # English
    r"(give|transfer|sign over) (the |my )?(property|house|flat|land|assets?)",
    r"(put|transfer) (the |my )?(house|flat|property|land) in (his|her|their|your) name",
    r"(add|remove|cut out|leave out|include) (him|her|them|someone) (from|in|out of) (the )?(will|inheritance|estate)",
    r"(change|update|rewrite|redo) (the |my )?will",
    r"(change|update) (beneficiary|nominee)",
    r"inheritance",
    r"estate planning",
    r"who gets (my|the)",
    r"(leave|give) (everything|it all|my assets?) to",
    r"disinherit",
    r"cut (him|her|them) out",
    r"(divide|split) (my |the )?assets",
    r"(share|portion|cut) of (my |the )?estate",
]

# Bucket 3 — Will & estate planning.
_BUCKET3_PATTERNS = [
    # Hindi / Hinglish
    r"vasiyat",
    r"vasiat",
    r"wasiyat",
    r"will banana",
    r"will likhna",
    r"will banao",
    r"will bana lo",
    r"will banwao",
    r"will kaise banta",
    r"will kaise likhte",
    r"apni sampatti",
    r"sampatti ka kya hoga",
    r"mere baad kya hoga",
    r"main na rahun toh",
    r"meri mrityu ke baad",
    r"marne ke baad",
    r"power of attorney",
    r"poa",
    r"nominee",
    r"nomination",
    r"bank account ka kya hoga",
    r"savings ka kya hoga",
    # English
    r"(make|write|draft|create|prepare) (a |my |the )?will",
    r"last will",
    r"testament",
    r"(what happens?|who gets?) (to )?(my )?(money|savings|assets?|property|everything) (when|after|if) (i|i'm) (die|dead|gone|no longer)",
    r"after (i'm gone|i die|my death|i pass)",
    r"when (i'm gone|i die|i pass away)",
    r"(power of attorney|POA)",
    r"(financial|legal) (arrangements?|planning|documents?)",
    r"(set up|sort out|organise) (my )?(affairs|finances|estate)",
    r"probate",
    r"executor",
    r"trustee",
    r"(add|change|update) (my )?(nominee|nomination)",
]

# Compile all patterns case-insensitively
_BUCKET1_RE = [re.compile(p, re.IGNORECASE) for p in _BUCKET1_PATTERNS]
_BUCKET2_RE = [re.compile(p, re.IGNORECASE) for p in _BUCKET2_PATTERNS]
_BUCKET3_RE = [re.compile(p, re.IGNORECASE) for p in _BUCKET3_PATTERNS]

_ALL_BUCKETS = [
    ("bucket1", _BUCKET1_RE),
    ("bucket2", _BUCKET2_RE),
    ("bucket3", _BUCKET3_RE),
]

# ---------------------------------------------------------------------------
# Response text — language-branched, same five-step posture in both languages.
# Completely neutral. Never validates or invalidates the transaction.
# ---------------------------------------------------------------------------

_PROTOCOL3_ENGLISH_RESPONSE = (
    "That sounds like it's been weighing on you for a while.\n\n"
    "I have to be honest — this is not something I should help you decide. "
    "Money decisions, even with family, need someone who truly knows your situation: "
    "a CA, a trusted relative, or a lawyer.\n\n"
    "If you just want to talk about how it feels — not the decision itself — I'm here for that."
)

_PROTOCOL3_HINDI_RESPONSE = (
    "Lagta hai yeh kuch waqt se aapke mann mein chal raha hai.\n\n"
    "Main seedha baat karta hoon — paise aur sampatti ke mamle mein main aapki "
    "madad karne ki sthiti mein nahi hoon. Yeh faisla sirf aapka hai, aur iske liye "
    "kisi aisa chahiye jo aapki poori situation jaanta ho — ek CA, ek vakeel, "
    "ya parivaar mein koi bharose ka insaan.\n\n"
    "Agar sirf yeh batana chahein ki andar se kaisa lag raha hai — "
    "faisle ki baat nahi, bas feel ki baat — main yahan hoon."
)


def _get_protocol3_response(language: str) -> str:
    """Return the Protocol 3 response in the user's preferred language."""
    lang = (language or "english").strip().lower()
    if lang in ("hindi", "hinglish", "hindi/english mix"):
        return _PROTOCOL3_HINDI_RESPONSE
    # Default to English — safer than defaulting to Hindi for unknown values
    return _PROTOCOL3_ENGLISH_RESPONSE


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def check_protocol3(user_id: int, text: str, language: str = "english") -> Optional[str]:
    """
    Check the message for Protocol 3 financial/legal signals.

    Args:
        user_id: Telegram user ID (for logging).
        text: The incoming message text.
        language: User's preferred language from the users table.
                  Defaults to 'english' — never assume Hindi.

    Returns:
        A response string in the user's language if Protocol 3 fires,
        or None if the message is clear.
    """
    text_lower = text.lower()

    # Flat keyword check — broad first pass.
    # Bug P (30 Apr 2026): substring match collided "lend" with "calendar"/
    # "splendid"/"blender". Use word-boundary regex.
    _financial_re = [
        re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        for kw in FINANCIAL_KEYWORDS
    ]
    matched_keywords = [
        FINANCIAL_KEYWORDS[i]
        for i, rx in enumerate(_financial_re)
        if rx.search(text_lower)
    ]
    if matched_keywords:
        logger.warning(
            "PROTOCOL3 | user_id=%s | bucket=keyword_match | keywords=%s | language=%s",
            user_id, matched_keywords, language,
        )
        log_protocol_event(
            user_id=user_id,
            protocol_type="3",
            trigger_bucket="keyword_match",
            trigger_keywords=", ".join(matched_keywords),
            family_alerted=0,
        )
        return _get_protocol3_response(language)

    for bucket_name, patterns in _ALL_BUCKETS:
        matched_keywords = _find_matches(text, patterns)
        if matched_keywords:
            logger.warning(
                "PROTOCOL3 | user_id=%s | bucket=%s | keywords=%s | language=%s",
                user_id, bucket_name, matched_keywords, language,
            )
            log_protocol_event(
                user_id=user_id,
                protocol_type="3",
                trigger_bucket=bucket_name,
                trigger_keywords=", ".join(matched_keywords),
                family_alerted=0,
            )
            return _get_protocol3_response(language)

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
