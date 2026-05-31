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
from dataclasses import dataclass
from typing import Optional
from database import log_protocol_event

logger = logging.getLogger(__name__)


@dataclass
class Protocol3Result:
    """Richer return for the detection/intervention split (31 May 2026).

    response:         the P3 reply string when the protocol FIRES, else None.
    context_detected: a financial CONTEXT noun (or a fire) was present. Logged
                      for observability; NOT yet injected into DeepSeek (A2,
                      deferred — needs a defined behavioural spec first).
    reason:           short label for logging (crisis_keyword / bucketN /
                      context_only / None).
    """

    response: Optional[str]
    context_detected: bool
    reason: Optional[str]

# ---------------------------------------------------------------------------
# Layer A — split into CONTEXT vs CRISIS (31 May 2026 fix).
#
# Root cause of the 31 May false positive: the old flat FINANCIAL_KEYWORDS list
# fired Protocol 3 (the heaviest non-crisis response) on the MERE MENTION of a
# financial noun. "the tenant in one of my rented property is leaving" tripped
# the bare word "property" and the senior was told to see a lawyer. A senior
# talking about ordinary life involving money/property/business must not trip
# the intervention.
#
# Fix (external review V6 — GPT + Gemini both endorsed): SPLIT DETECTION FROM
# INTERVENTION.
#   CONTEXT_KEYWORDS -> only set context_detected=True. NEVER fire P3, NEVER set
#                       the 60-min protocol3_active flag. Logged for observability.
#   CRISIS_KEYWORDS  -> unambiguous financial-malice terms. Solo-fire + flag.
#   Layer B buckets  -> decision / pressure / asset patterns. Fire + flag.
#
# Deliberately EXCLUDED from solo-fire (caught in Layer B only when paired with
# money): bare "cheating"/"cheat" (marital, card games, exams) and "dhoka"/
# "dhokha" (general betrayal — love, friendship, trust). Both are ambiguous and
# solo-firing them recreates the exact false-positive class this fix removes.
# ---------------------------------------------------------------------------

# Context-only: financial nouns that, alone, mean nothing actionable.
# These NEVER fire Protocol 3 and NEVER set the session flag.
CONTEXT_KEYWORDS = [
    "invest", "investment", "investing",
    "business", "scheme", "shares", "stocks",
    "mutual fund", "fixed deposit", "fd",
    "loan", "borrow", "lend", "lending",
    "property", "real estate", "savings",
    "insurance", "policy", "money", "lakhs",
    "crore", "rupees", "₹", "paise",
    "paisa", "nivesh", "vyapar",
    "dhandha", "karz", "udhaar", "zameen",
    "bima", "yojana",
]

# Unambiguous financial-malice terms — almost always signal a scam/fraud concern
# in a senior's mouth. These solo-fire Protocol 3 and set the session flag.
# NOTE: bare "cheating"/"cheat" and "dhoka"/"dhokha" intentionally NOT here
# (see comment above) — they are routed through Layer B paired with money.
CRISIS_KEYWORDS = [
    "fraud", "scam", "ghotala", "froad",
    "thag", "thagi", "thaggi",
]

# Backwards-compat alias (kept so any external reference doesn't break).
FINANCIAL_KEYWORDS = CONTEXT_KEYWORDS + CRISIS_KEYWORDS

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
    # --- Added 31 May 2026 (review-named coverage gaps) ---
    # Relational / pestering pressure (English)
    r"keeps? asking (me )?for (money|cash|paisa|paise)",
    r"wants? (his|her|their) share",
    r"forcing me to (give|help|pay|lend|sign)",
    r"pressuring me",
    r"pressure to (give|pay|lend|help|sign)",
    # Relational pressure (Hinglish)
    r"pais[ae] maang raha",
    r"paise maang rahe",
    r"pais[ae] mang raha",
    r"paise mang rahe",
    r"hissa maang",
    r"paise ke liye (force|pressure|majboor)",
    r"pais[ae] dena padega",
    # Borrowing under request
    r"wants? to borrow",
    r"udhaar maang",
    r"karz maang",
    # Investment solicitation
    r"promising (high )?returns",
    r"invest(ment)? opportunity",
    # Authority-figure pressure (agent/manager/broker + financial action)
    r"\b(agent|manager|broker|advisor)\b.{0,30}\b(invest|sign|scheme|returns|polic)",
    r"nivesh karne (bol|keh|kah)",
    r"pais[ae] lagane (bol|keh|kah)",
    # Cheating / dhoka ONLY when paired with money (bare forms excluded from Layer A)
    r"cheat(ing|ed|s)? me\b.{0,25}\b(money|savings|paisa|paise|lakhs|crore|fd|deposit|bank|account)",
    r"financial(ly)? cheat",
    r"pais[ae].{0,15}dhokh?a",
    r"dhokh?a.{0,15}(paisa|paise|bank|account|savings|fd)",
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
    # --- Added 31 May 2026 (review-named coverage gaps) ---
    # Urgent / coerced liquidation (English) — bounded proximity, never .*
    r"sell(ing)? (the |my )?(flat|house|property|land|shares?|stocks?)\b.{0,20}\b(quick|fast|urgent|to help|for him|for her|for them|for his|for her)",
    r"break(ing)? (my |the )?(fd|fixed deposit|savings)",
    # Liquidation (Hinglish)
    r"property bech",
    r"flat bech",
    r"makaan bech",
    r"zameen bech",
    r"ghar bech",
    r"fd tod",
    r"fixed deposit tod",
    r"shares? bech",
    # Coerced signing / control of accounts (require financial/legal context)
    r"(financial|bank|property|legal|loan|investment) (papers?|documents?)",
    r"sign(ing)? (some |the )?(papers?|documents?)\b.{0,25}\b(property|house|flat|land|money|account|bank|share|investment|loan|will|nominee)",
    r"put(ting)? (his|her|their) name on (the )?(account|property|house|flat)",
    r"make (him|her|them) (the |a )?nominee",
    r"naam chadhana",
    r"naam chadha",
    r"nominee banana",
    r"paper par sign",
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

def check_protocol3(user_id: int, text: str, language: str = "english") -> "Protocol3Result":
    """
    Check the message for Protocol 3 financial/legal signals.

    Detection/intervention split (31 May 2026). Evaluation order:
      1. CRISIS_KEYWORDS (Layer A, unambiguous malice) -> FIRE + flag.
      2. Layer B buckets (decision / pressure / asset)  -> FIRE + flag.
      3. CONTEXT_KEYWORDS (bare financial nouns)        -> DETECT ONLY, no fire.
      4. otherwise                                      -> clear.

    Args:
        user_id: Telegram user ID (for logging).
        text: The incoming message text.
        language: User's preferred language from the users table.
                  Defaults to 'english' — never assume Hindi.

    Returns:
        Protocol3Result. `.response` is the reply string ONLY when the protocol
        fires (crisis keyword or a Layer B bucket); it is None for context-only
        and clear messages. The caller fires + sets the 60-min session flag iff
        `.response` is truthy — so context-only mentions never contaminate the
        next hour of conversation.
    """
    text_lower = text.lower()

    # --- Layer A: CRISIS keywords (unambiguous malice — solo-fire + flag) ---
    # Word-boundary regex (V9): short keywords must not substring-collide.
    _crisis_re = [
        re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        for kw in CRISIS_KEYWORDS
    ]
    crisis_hits = [
        CRISIS_KEYWORDS[i] for i, rx in enumerate(_crisis_re) if rx.search(text_lower)
    ]
    if crisis_hits:
        logger.warning(
            "PROTOCOL3 | user_id=%s | bucket=crisis_keyword | keywords=%s | language=%s",
            user_id, crisis_hits, language,
        )
        log_protocol_event(
            user_id=user_id,
            protocol_type="3",
            trigger_bucket="crisis_keyword",
            trigger_keywords=", ".join(crisis_hits),
            family_alerted=0,
        )
        return Protocol3Result(
            response=_get_protocol3_response(language),
            context_detected=True,
            reason="crisis_keyword",
        )

    # --- Layer B: decision / pressure / asset buckets (fire + flag) ---
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
            return Protocol3Result(
                response=_get_protocol3_response(language),
                context_detected=True,
                reason=bucket_name,
            )

    # --- Layer A: CONTEXT keywords (detect only — NO fire, NO session flag) ---
    _context_re = [
        re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        for kw in CONTEXT_KEYWORDS
    ]
    context_hits = [
        CONTEXT_KEYWORDS[i] for i, rx in enumerate(_context_re) if rx.search(text_lower)
    ]
    if context_hits:
        # INFO not WARNING: this is awareness, not an intervention. No DB event,
        # no fire, no flag. (A2 — feeding this to DeepSeek — is deferred.)
        logger.info(
            "PROTOCOL3 | user_id=%s | context_only (no fire) | keywords=%s",
            user_id, context_hits,
        )
        return Protocol3Result(response=None, context_detected=True, reason="context_only")

    return Protocol3Result(response=None, context_detected=False, reason=None)


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
