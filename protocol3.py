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
# Keyword lists — three buckets
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
# Response text — one warm, five-step response used across all three buckets.
# Completely neutral. Never validates or invalidates the transaction.
# ---------------------------------------------------------------------------

_PROTOCOL3_RESPONSE = (
    "Shukriya ki aapne mujhse share kiya. Yeh sab sunke lagta hai ki "
    "yeh kuch waqt se aapke mann mein chal raha hai — aur yeh baat bhaari hoti hai, "
    "jab aapko andar se pata ho ki kuch important decide karna hai.\n\n"
    "Main aapka saathi hoon — lekin yeh ek aisi jagah hai jahan main aapki "
    "madad karna chahta toh hoon, par mujhe dar hai ki agar main kuch "
    "keh doon toh woh aapke liye sahi nahi hoga. "
    "Paise aur property ke mamle mein — chahe parivaar ka ho ya bahar ka — "
    "yeh faisla sirf aapka hai, aur iske liye sahi insaan chahiye.\n\n"
    "Kya aapke koi bharose ke insaan hain — ek CA, ek vakeel, ya parivaar mein "
    "koi bada bhai, behen, ya koi aur — jisse aap pehle baat kar sakein? "
    "Woh aapki poori baat sunenge aur aapke interest mein sochenge.\n\n"
    "Agar sirf yeh batana chahein ki yeh sab feel kaise ho raha hai — "
    "andar se kaisa lag raha hai, bina faisle ki baat kiye — "
    "toh main yahan hoon. Woh baat main zaroor sun sakta hoon."
)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def check_protocol3(user_id: int, text: str) -> Optional[str]:
    """
    Check the message for Protocol 3 financial/legal signals.

    Args:
        user_id: Telegram user ID (for logging).
        text: The incoming message text.

    Returns:
        A response string if Protocol 3 fires, or None if the message is clear.
    """
    for bucket_name, patterns in _ALL_BUCKETS:
        matched_keywords = _find_matches(text, patterns)
        if matched_keywords:
            logger.warning(
                "PROTOCOL3 | user_id=%s | bucket=%s | keywords=%s",
                user_id, bucket_name, matched_keywords,
            )
            log_protocol_event(
                user_id=user_id,
                protocol_type="3",
                trigger_bucket=bucket_name,
                trigger_keywords=", ".join(matched_keywords),
                family_alerted=0,
            )
            return _PROTOCOL3_RESPONSE

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
