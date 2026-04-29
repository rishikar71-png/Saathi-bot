"""
Pending-input capture — Batch 2.

Handles the two deferred-input flows:
  • pending_grandkids_names — set by onboarding step 7 ("she will tell u")
  • pending_medicines       — set by onboarding step 10 or self-setup step 6
                              ("I don't know yet" / "pata nahi")

Flow (both items work the same way):
  1. Senior's message is scanned for a keyword trigger (grandkids / medicines).
  2. If the matching pending flag is set AND the message is NOT emotionally
     vulnerable/grief-laden, we offer to capture the missing info warmly.
     Offer sets awaiting_pending_capture = 'grandkids' | 'medicines'.
  3. Next inbound message is routed to capture_response() — parses + writes
     to family_members (grandkids) or medicines_raw + seed reminders
     (medicines), clears both pending_<kind> and awaiting_pending_capture.
     Refusal clears awaiting_pending_capture only — flag stays set so a
     future trigger can try again.

Governing principle — "someone who is there, not someone who is trying":
  • Offer is soft ("By the way — ... would you like to tell me?").
  • If senior is in a vulnerable or grief state, we DO NOT offer. The
    caller passes is_vulnerable / is_grief booleans.
  • If senior refuses, we don't re-offer in the same session.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword triggers
# ---------------------------------------------------------------------------

# Grandkids mentions — English + Hindi + Hinglish.
# Matched as substrings on lowercased text. Word boundaries kept loose to
# catch "grandkid", "grandkids", "grandchildren", "pota-poti", etc.
_GRANDKID_KEYWORDS = [
    # English — compound forms
    "grandchild", "grandchildren", "grandkid", "grandkids",
    "grandson", "grandsons", "granddaughter", "granddaughters",
    "grandbaby", "grandbabies",
    # English — spaced forms (seniors often type "grand kids", "grand children")
    "grand kid", "grand kids", "grand child", "grand children",
    "grand son", "grand sons", "grand daughter", "grand daughters",
    "grand baby", "grand babies",
    # Hindi / Hinglish
    "pota", "poti", "potey", "pote", "potiyan", "potian",
    "naati", "naatin", "nati", "natin",
    "navasa", "navasi", "nawasa", "nawasi",
]

# Medicine mentions — the senior is referencing their medication in some way.
_MEDICINE_KEYWORDS = [
    # English
    "medicine", "medicines", "medication", "medications",
    "pill", "pills", "tablet", "tablets",
    "prescription", "prescriptions", "dose", "doses",
    # Hindi / Hinglish
    "dawai", "dawaai", "dawa", "davai", "dava", "davayi",
    "goli", "golian", "goliyan",
]

# Refusal signals — senior declined the offer. Language-agnostic substrings.
_REFUSAL_KEYWORDS = [
    "no thanks", "no thank you", "not now", "not yet", "later", "baad mein",
    "abhi nahi", "phir kabhi", "skip", "pass", "leave it", "forget it",
    "chhod do", "rehne do", "rahne do",
]

# Leading-affirmation strip — senior often prefixes the data with "yes."/"haan,"/"sure —".
# Without this, the parser keeps the affirmation token as a "name"
# ("yes. anish and aman" → ["Yes", "Anish", "Aman"] → first row saved as "Yes").
# Pattern lifted from onboarding._CONTACT_AFFIRMATION_RE.
_LEADING_AFFIRMATION_RE = re.compile(
    r"^(yes|yeah|yup|yep|sure|okay|ok|haan|ha|han|hanji|haanji|ji|"
    r"bilkul|theek|thik)[\s\.\,\-—:!]+",
    re.IGNORECASE,
)


def _strip_leading_affirmation(text: str) -> str:
    """Remove leading 'yes.' / 'haan,' / 'sure —' prefix from senior's reply.
    Runs up to 2 times in case of stacked affirmations ('yes, sure, anish')."""
    t = (text or "").strip()
    for _ in range(2):
        new = _LEADING_AFFIRMATION_RE.sub("", t, count=1).strip()
        if new == t:
            break
        t = new
    return t

# Strong yes-signals — senior wants to share. Treat as "proceed to capture
# next turn" rather than starting capture in this turn.
# Currently unused — we always wait for the next message for capture text.


def detect_pending_trigger(text: str) -> Optional[str]:
    """Scan text for a keyword that could trigger a pending-input offer.
    Returns 'grandkids' | 'medicines' | None.
    Grandkids takes precedence if both match (unlikely in practice)."""
    if not text:
        return None
    t = text.lower()
    # Word-boundary check is needed for short keywords like "dose" which
    # could appear as a substring of other words. But for most keywords
    # simple substring match is fine and more tolerant of typos.
    for kw in _GRANDKID_KEYWORDS:
        if kw in t:
            return "grandkids"
    for kw in _MEDICINE_KEYWORDS:
        if kw in t:
            return "medicines"
    return None


def is_refusal(text: str) -> bool:
    """True if text reads like 'no, not now, skip, later' etc.
    Used to clear awaiting_pending_capture without treating text as data."""
    if not text:
        return False
    t = text.strip().lower().rstrip(".!?")
    # Bare "no" / "nahi" / "nope"
    if t in ("no", "no.", "nahi", "nahin", "nope", "nahin.", "nahi.", "n"):
        return True
    return any(kw in t for kw in _REFUSAL_KEYWORDS)


# ---------------------------------------------------------------------------
# Offer copy — language-aware, soft framing
# ---------------------------------------------------------------------------

def build_capture_offer(kind: str, language: str) -> str:
    """Return the warm one-liner that asks the senior if they'd like to
    share the deferred info now. Soft — opens with "by the way" / "vaise".
    Must not feel like a demand or a form to fill."""
    lang = (language or "english").lower()

    if kind == "grandkids":
        if lang == "hindi":
            return (
                "Vaise — aapke potao-potiyon ke naam abhi mujhe pata nahi hain. "
                "Agar batana chahein toh bata sakte hain, koi zarurat nahi hai."
            )
        elif lang == "hinglish":
            return (
                "Vaise — I don't know your grandkids' names yet. "
                "Agar batana chahein toh share kar dein — koi zarurat nahi hai."
            )
        else:  # english
            return (
                "By the way — I don't know your grandchildren's names yet. "
                "If you'd like to share them, I'd love to hear — no pressure at all."
            )

    if kind == "medicines":
        if lang == "hindi":
            return (
                "Vaise — aapki dawaiyon ke baare mein mujhe nahi pata. "
                "Agar bata dein toh sahi samay par yaad dila sakti hoon. "
                "Koi zaroori nahi — jab chahein."
            )
        elif lang == "hinglish":
            return (
                "Vaise — I don't know which medicines you take. "
                "Agar bata dein toh I can remind you at the right times. "
                "No pressure — jab chahein."
            )
        else:  # english
            return (
                "By the way — I don't know which medicines you take. "
                "If you'd like to share them, I can remind you at the right times. "
                "No pressure — whenever you're ready."
            )

    raise ValueError(f"Unknown kind: {kind!r}")


# ---------------------------------------------------------------------------
# Capture handler — parses response, writes to DB, clears flags
# ---------------------------------------------------------------------------

def _extract_names(text: str) -> list[str]:
    """Split free-form text like 'putu has anish and aman, mana has akshadha'
    into individual names. Strips parentage verbs ('has', 'ka beta', 'ki beti')
    and keeps capitalized tokens."""
    if not text:
        return []
    # Strip leading affirmations first — "yes. anish and aman" should not
    # yield "Yes" as a grandchild name.
    text = _strip_leading_affirmation(text)
    if not text:
        return []
    # Common parentage stripping — replace relational phrases with a comma so
    # the downstream splitter treats them as boundaries. Replacing with a
    # plain space would concatenate names ("Putu has Anish" → "Putu  Anish"
    # would split as one token). Known trade-off: this may include the parent
    # name in the extracted list (e.g. "Putu has Anish" yields ["Putu","Anish"]).
    # Acceptable for pilot — senior can correct later; the alternative would
    # require a full NLP parse to distinguish parent from grandchild.
    stripped = text
    _noise = [
        r"\bhas\b", r"\bhave\b",
        r"\bka beta\b", r"\bka bachcha\b", r"\bka ladka\b",
        r"\bki beti\b", r"\bki ladki\b", r"\bki bachchi\b",
        r"\bs son\b", r"\bs daughter\b", r"\b's son\b", r"\b's daughter\b",
        r"\bson is\b", r"\bdaughter is\b",
    ]
    for pat in _noise:
        stripped = re.sub(pat, ",", stripped, flags=re.IGNORECASE)
    # Split on comma, "and", "aur", "&", ";"
    parts = re.split(r",|;|\s+and\s+|\s+aur\s+|\s+&\s+", stripped, flags=re.IGNORECASE)
    out: list[str] = []
    for p in parts:
        p = p.strip().strip(".").strip()
        if not p:
            continue
        # Filter out bare relational tokens left over
        if p.lower() in ("his", "her", "their", "my", "our", "the", "a", "an"):
            continue
        # Keep multi-word entries as one name if they're short (e.g. "Ishween Kaur")
        # but drop anything longer than 4 words (likely a sentence, not a name).
        if len(p.split()) > 4:
            continue
        out.append(p.title())
    return out


def capture_response(
    user_id: int,
    kind: str,
    text: str,
) -> Tuple[bool, str]:
    """Parse the senior's response and write it to the right table.
    Clears pending_<kind> + awaiting_pending_capture on success.
    On refusal, clears only awaiting_pending_capture (pending flag kept).

    Returns (captured, ack_message). ack_message is a warm one-liner for
    the senior; caller sends it after capture completes.
    """
    from database import update_user_fields, add_family_members_bulk

    # Refusal first — never try to parse data out of a "no, not now".
    if is_refusal(text):
        update_user_fields(user_id, awaiting_pending_capture=None)
        logger.info(
            "PENDING_CAPTURE | user_id=%s | kind=%s | refused (flag kept)",
            user_id, kind,
        )
        return (False, "No problem at all — whenever you feel like it. 🙏")

    if kind == "grandkids":
        names = _extract_names(text)
        if not names:
            # Couldn't parse — don't clear flag, ask for a simpler format.
            logger.info(
                "PENDING_CAPTURE | user_id=%s | kind=grandkids | parse failed on %r",
                user_id, text[:80],
            )
            return (
                False,
                "Sorry — could you list their names separated by commas? "
                "For example: 'Anish, Aman, Akshadha'.",
            )
        add_family_members_bulk(user_id, names, "grandchild")
        update_user_fields(
            user_id,
            pending_grandkids_names=0,
            awaiting_pending_capture=None,
        )
        logger.info(
            "PENDING_CAPTURE | user_id=%s | kind=grandkids | captured %d name(s): %s",
            user_id, len(names), ", ".join(names),
        )
        # Warm ack — reflect the names back
        if len(names) == 1:
            ack = f"{names[0]} — lovely name. Thank you for sharing. 🙏"
        elif len(names) == 2:
            ack = f"{names[0]} and {names[1]} — thank you for telling me. 🙏"
        else:
            *first, last = names
            ack = (
                f"{', '.join(first)}, and {last} — thank you for sharing. "
                f"I'll remember. 🙏"
            )
        return (True, ack)

    if kind == "medicines":
        # Strip leading affirmations so "yes. metformin 8am" doesn't confuse
        # downstream parsers.
        text_stripped = _strip_leading_affirmation(text).strip()
        if len(text_stripped) < 3:
            return (
                False,
                "Could you share the names + times? For example: "
                "'metformin 8am and 8pm, atorvastatin at night'.",
            )

        # Save raw text and clear pending flags; schedule_reminders will
        # re-read this if anything fails downstream.
        update_user_fields(
            user_id,
            medicines_raw=text_stripped,
            pending_medicines=0,
            awaiting_pending_capture=None,
        )

        # Seed reminders. Returns a report with seeded_active / seeded_ambiguous
        # / unparseable. Ambiguous rows get inserted as INACTIVE placeholders;
        # we must ASK the senior morning-or-night before the scheduler fires.
        report = {
            "seeded_active": 0,
            "seeded_ambiguous": [],
            "unparseable": [],
            "pairs_total": 0,
        }
        try:
            from reminders import seed_reminders_from_raw
            report = seed_reminders_from_raw(user_id, text_stripped)
        except Exception as seed_err:
            logger.warning(
                "PENDING_CAPTURE | user_id=%s | seed_reminders_from_raw failed: %s",
                user_id, seed_err,
            )

        logger.info(
            "PENDING_CAPTURE | user_id=%s | kind=medicines | captured (len=%d) | "
            "active=%d ambiguous=%d unparseable=%d",
            user_id, len(text_stripped),
            report["seeded_active"],
            len(report["seeded_ambiguous"]),
            len(report["unparseable"]),
        )

        ambiguous = report["seeded_ambiguous"]
        if ambiguous:
            # Move the senior into the clarify sub-state. The next inbound
            # message is routed back here with kind='medicines_clarify'.
            update_user_fields(user_id, awaiting_pending_capture="medicines_clarify")
            return (True, _build_ambiguity_ask(ambiguous))

        # No ambiguous rows — send the standard warm ack.
        ack = "Thank you — I've noted them down. I'll remind you at the right times. 🙏"
        if report["unparseable"]:
            # Keep it soft — don't list all of them; surface just the count.
            ack += (
                f"\n\n(I couldn't parse a time for "
                f"{len(report['unparseable'])} of them — your family can add those later.)"
            )
        return (True, ack)

    if kind == "medicines_clarify":
        # Senior is replying to our "for X and Y — morning or night?" prompt.
        return _handle_ambiguity_reply(user_id, text)

    raise ValueError(f"Unknown kind: {kind!r}")


# ---------------------------------------------------------------------------
# Batch-ASK flow for ambiguous medicine times (23 Apr 2026)
# ---------------------------------------------------------------------------

def _build_ambiguity_ask(ambiguous: list[dict]) -> str:
    """
    Build a plain, non-clinical follow-up asking the senior to pick morning
    vs. night for each ambiguous medicine. Groups medicines that share the
    same bare hour so we don't ask twice about the same time.
    """
    # Group by bare hour.
    by_hour: dict[str, list[str]] = {}
    for row in ambiguous:
        by_hour.setdefault(row["bare_hhmm"], []).append(row["medicine_name"])

    # One-liner per hour bucket.
    lines = []
    for bare_hhmm, meds in by_hour.items():
        h = int(bare_hhmm.split(":")[0])
        # Humanise ("9" not "09").
        hour_plain = str(h)
        if len(meds) == 1:
            lines.append(
                f"For *{meds[0]}* at {hour_plain}:00 — is that morning or night?"
            )
        else:
            meds_text = ", ".join(meds[:-1]) + f" and {meds[-1]}"
            lines.append(
                f"For *{meds_text}* at {hour_plain}:00 — morning or night?"
            )

    header = (
        "Thank you — I've noted those down. One small question so I don't "
        "send a reminder at the wrong hour:"
    )
    footer = (
        "Just reply with \"morning\" or \"night\" for each. You can say "
        "\"all morning\" or \"all night\" if the same answer covers them all."
    )
    return f"{header}\n\n" + "\n".join(lines) + f"\n\n{footer}"


def _handle_ambiguity_reply(user_id: int, reply_text: str) -> Tuple[bool, str]:
    """
    Parse the senior's reply to an ambiguity-ASK. Two cases:
      • Global answer ("all morning" / "all night") — applies to every
        pending ambiguous reminder.
      • Per-medicine answer — we do a best-effort scan: split the reply,
        match each fragment against a known medicine name in the pending
        set, and apply the morning/night token in that fragment.

    In either case, any reminder we couldn't resolve stays inactive and
    its placeholder row is dropped. We prefer NOT firing a reminder at
    the wrong time over guessing.
    """
    from database import update_user_fields
    from reminders import (
        get_ambiguous_reminders, resolve_reminder_time, resolve_ambiguous_hour,
    )

    pending = get_ambiguous_reminders(user_id)
    if not pending:
        # Nothing to resolve — clear the awaiting flag and fall through.
        update_user_fields(user_id, awaiting_pending_capture=None)
        return (False, "Thank you — I've noted that.")

    rt = (reply_text or "").lower().strip()
    # Global-answer detection — compound phrases first.
    global_am = any(p in rt for p in (
        "all morning", "all subah", "all am",
        "everything morning", "sab morning", "sab subah",
    ))
    global_pm = any(p in rt for p in (
        "all night", "all raat", "all pm", "all evening",
        "everything night", "sab raat", "sab night",
    ))

    # Bare period word as global signal (29 Apr 2026): a reply of just
    # "morning" / "night" / "raat" naturally means "all of them" — the
    # original compound-only list missed this. We promote bare period
    # words to global ONLY when:
    #   1. exactly one period type is mentioned (AM or PM, not both)
    #   2. none of the pending medicine names appear in the reply
    # If the reply names a medicine ("Pan D morning, Thyronorm night"),
    # the original per-medicine matching path still wins.
    _AM_BARE_TOKENS = {"morning", "subah", "savere", "savera", "am"}
    _PM_BARE_TOKENS = {"night", "raat", "shaam", "evening", "dopahar", "pm"}
    rt_words = set(re.findall(r"[a-z]+", rt))
    am_hit = bool(rt_words & _AM_BARE_TOKENS)
    pm_hit = bool(rt_words & _PM_BARE_TOKENS)
    has_med_name = any(
        (row["medicine_name"] or "").lower() in rt for row in pending
    )
    if not has_med_name:
        if am_hit and not pm_hit:
            global_am = True
        elif pm_hit and not am_hit:
            global_pm = True

    resolved: list[str] = []
    unresolved: list[str] = []

    for row in pending:
        rid = row["id"]
        med = (row["medicine_name"] or "").strip()
        bare = row["schedule_time"] or "00:00"
        try:
            bh, bm = int(bare.split(":")[0]), int(bare.split(":")[1])
        except Exception:
            bh, bm = 0, 0

        picked: Optional[str] = None
        if global_am:
            picked = f"{bh:02d}:{bm:02d}"
        elif global_pm:
            picked = f"{bh + 12:02d}:{bm:02d}" if bh < 12 else f"{bh:02d}:{bm:02d}"
        else:
            # Per-medicine scan — find the fragment mentioning this medicine.
            med_lc = med.lower()
            # Split on common separators.
            fragments = re.split(r"[,;\.\n]| and |\baur\b", rt, flags=re.IGNORECASE)
            for frag in fragments:
                if med_lc in frag:
                    picked = resolve_ambiguous_hour(bh, bm, frag)
                    if picked:
                        break
            # If no fragment matched by name, try the whole reply — if it
            # clearly picks one side AND there's only one pending med, honour it.
            if not picked and len(pending) == 1:
                picked = resolve_ambiguous_hour(bh, bm, rt)

        if picked and resolve_reminder_time(rid, picked):
            resolved.append(f"{med} at {_humanise(picked)}")
        else:
            unresolved.append(med)

    # Clear awaiting flag — we've processed this turn. If anything is still
    # unresolved, the placeholder rows remain inactive and the senior can
    # correct via normal conversation (Rule 13 routes it back to family).
    update_user_fields(user_id, awaiting_pending_capture=None)

    logger.info(
        "PENDING_CAPTURE | user_id=%s | ambiguity resolved=%d unresolved=%d",
        user_id, len(resolved), len(unresolved),
    )

    parts = []
    if resolved:
        parts.append(
            "Got it — "
            + ", ".join(resolved)
            + ". I'll remind you then. 🙏"
        )
    if unresolved:
        parts.append(
            "I wasn't sure about: "
            + ", ".join(unresolved)
            + ". Your family can fix those any time."
        )
    if not parts:
        parts.append("Thank you. 🙏")

    return (True, " ".join(parts))


def _humanise(hhmm: str) -> str:
    """'13:30' → '1:30 PM'. Used in ack messages so the senior reads the
    time the way they expect."""
    try:
        h, m = int(hhmm.split(":")[0]), int(hhmm.split(":")[1])
    except Exception:
        return hhmm
    suffix = "AM" if h < 12 else "PM"
    display_h = h % 12 or 12
    if m == 0:
        return f"{display_h} {suffix}"
    return f"{display_h}:{m:02d} {suffix}"
