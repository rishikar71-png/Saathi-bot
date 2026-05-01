"""
Module 14 — Family Integration
30 March 2026

Two features:
1. Family bridge: registered family member messages bot → relayed warmly to senior
2. Weekly health report: every Sunday at ~10am IST → mood/health/medicine/activity
   summary sent to all registered family members

Family member registration flow:
  - Senior types /familycode → bot shows a 6-char linking code
  - Family member types /join [CODE] from their own phone → telegram_user_id stored
  - Registration also sets weekly_report_opt_in = 1 on senior's profile

One-way relay only (MVP): family → senior. Senior's replies are not forwarded back.
Family members receive the weekly report as structured updates.

NEVER send to family without telegram_user_id stored in family_members.
NEVER send weekly report without at least one registered family member.
"""

import logging
import os
import random
import string
from datetime import datetime, timezone, timedelta
from typing import Optional

from database import get_connection, update_user_fields
from language_utils import detect_message_language

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bot username cache — populated by main.post_init() on startup via
# set_cached_bot_username(). Used by build_family_invite_block() so the
# forward-ready block can tell a recipient which bot to search for in
# Telegram. Falls back to 'Saathi' (no @) if never set — defensive only;
# post_init runs before any user traffic arrives.
# ---------------------------------------------------------------------------

_BOT_USERNAME: Optional[str] = None


def set_cached_bot_username(username: str) -> None:
    """Called once at startup from main.post_init() after bot.get_me()."""
    global _BOT_USERNAME
    _BOT_USERNAME = username.lstrip("@") if username else None
    logger.info("FAMILY | bot_username cached: %s", _BOT_USERNAME)


def get_cached_bot_username() -> str:
    """Returns cached bot username, or 'Saathi' as a defensive fallback."""
    return _BOT_USERNAME or "Saathi"


# ---------------------------------------------------------------------------
# Forward-ready invite block — two variants.
#
# SELF-SETUP (first-person): the senior themselves is forwarding the message
# to someone they already know — usually their spouse / sibling / a close
# friend who was saved as the emergency contact at onboarding. Written as
# the senior speaking directly ("I've started using Saathi...").
#
# CHILD-LED (third-person): the adult child who set Saathi up for their
# parent is forwarding the message to another family member — the other
# parent, a sibling, an uncle, a close family friend. The adult child
# would NEVER refer to their parent by first name in this register — it's
# always the relational term ("Papa" / "Dad" / "Mummy" / "Rishi Uncle" /
# etc). Using the first name would read as a scam/spam signal to the
# recipient. We pass in the `family_term` the adult child supplied and
# reuse it three times in the body — this avoids needing to know the
# senior's gender, avoids pronoun ambiguity, and keeps the tone natural.
#
# Both variants honour the family-reference-handling rule in CLAUDE.md:
# affection framing, no "emergency contact" / "worried about you" register,
# no obligation. The family member then searches @{bot_username} in
# Telegram, hits Start, sends the code — the bare-code flow in main.py
# catches it and registers them.
#
# Video link (TELEGRAM_SETUP_VIDEO_URL env var) is appended if set, so
# family members unfamiliar with Telegram get a short walkthrough.
# ---------------------------------------------------------------------------

def _video_line() -> str:
    """Append a short 'if you're new to Telegram' pointer when env var is set."""
    url = os.environ.get("TELEGRAM_SETUP_VIDEO_URL", "").strip()
    return f"\n\n(If you're new to Telegram: {url})" if url else ""


def build_family_invite_block_first_person(
    code: str,
    recipient_name: Optional[str] = None,
) -> str:
    """
    SELF-SETUP variant — the senior is forwarding this themselves.
    Speaks in first person ("I've started using Saathi...").

    code             — the 6-char family linking code.
    recipient_name   — first name of the person being invited (usually the
                       emergency contact captured at self-setup). If omitted,
                       greeting falls back to 'Hi there'.
    """
    bot_username = get_cached_bot_username()
    rn = (recipient_name or "").strip()
    greeting = f"Hi {rn}" if rn else "Hi there"
    return (
        f"{greeting} — I've started using Saathi, a little companion "
        f"that chats with me through the day. I'd like you to be the "
        f"person it reaches out to if anything ever comes up, and you'd "
        f"get a short weekly note about how I'm doing.\n\n"
        f"If you're open to it: open Telegram, search for @{bot_username}, "
        f"tap Start, and send the code {code}.\n\n"
        f"No rush, and you can stop anytime."
        f"{_video_line()}"
    )


def build_family_invite_block_third_person(
    family_term: str,
    code: str,
    recipient_name: Optional[str] = None,
) -> str:
    """
    CHILD-LED variant — the adult child is forwarding this to another
    family member. The `family_term` is the relational term the adult
    child uses for the senior ("Papa" / "Dad" / "Mummy" / "Rishi Uncle"
    / etc.). We reuse it three times to sidestep pronoun/gender issues
    and keep the register natural for Indian families.

    family_term      — how the forwarder addresses the senior ("Papa").
                       MUST be non-empty; caller lazy-asks and saves.
    code             — the 6-char family linking code.
    recipient_name   — first name of the family member being invited, if
                       known. Often omitted in the /familycode flow, so
                       greeting falls back to 'Hi there'.
    """
    bot_username = get_cached_bot_username()
    rn = (recipient_name or "").strip()
    greeting = f"Hi {rn}" if rn else "Hi there"
    term = (family_term or "").strip() or "Our family member"
    return (
        f"{greeting} — {term} has started using Saathi, a little companion "
        f"that chats with {term} through the day. {term} would like you to "
        f"be the person it reaches out to if anything ever comes up, and "
        f"you'd get a short weekly note about how {term}'s doing.\n\n"
        f"If you're open to it: open Telegram, search for @{bot_username}, "
        f"tap Start, and send the code {code}.\n\n"
        f"No rush, and you can stop anytime."
        f"{_video_line()}"
    )


# Back-compat shim — the 20 Apr call site in onboarding.py originally
# imported build_family_invite_block (third-person, no family_term). We
# keep the name exported so any stragglers don't break, but new callers
# should use the explicit first/third person helpers above.
def build_family_invite_block(
    senior_name: str,
    code: str,
    recipient_name: Optional[str] = None,
) -> str:
    """DEPRECATED — prefer the explicit first/third person helpers."""
    logger.warning(
        "FAMILY | build_family_invite_block() deprecated — "
        "use first_person / third_person variants instead"
    )
    # Fall back to third-person with senior_name as the term. This is the
    # old behaviour; new code should never reach this.
    return build_family_invite_block_third_person(
        family_term=senior_name,
        code=code,
        recipient_name=recipient_name,
    )


# ---------------------------------------------------------------------------
# Linking codes — senior generates, family member uses to register
# ---------------------------------------------------------------------------

def get_or_create_linking_code(user_id: int) -> str:
    """
    Return the existing family linking code for this user, or generate a new one.
    Codes are 6-char uppercase alphanumeric. Persisted in users.family_linking_code.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT family_linking_code FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()

        if row and row["family_linking_code"]:
            return row["family_linking_code"]

        # Generate new code — retry on (extremely unlikely) collision
        for _ in range(5):
            code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
            with get_connection() as conn:
                existing = conn.execute(
                    "SELECT user_id FROM users WHERE family_linking_code = ?",
                    (code,),
                ).fetchone()
            if not existing:
                update_user_fields(user_id, family_linking_code=code)
                logger.info("FAMILY | code generated | user_id=%s | code=%s", user_id, code)
                return code

        # Fallback: use truncated user_id (will not collide in practice)
        code = str(user_id)[-6:].upper()
        update_user_fields(user_id, family_linking_code=code)
        return code

    except Exception as e:
        logger.error("FAMILY | get_or_create_linking_code failed: %s", e)
        return "ERROR"


def lookup_senior_by_code(code: str) -> Optional[dict]:
    """
    Resolve a linking code to senior info without registering anything. Used by
    the bare-code auto-detect flow to show a confirmation question before
    calling join_by_code().

    Returns dict with: senior_user_id, senior_name, display_name, language.
    `display_name` is family_term if set (what the family member calls the
    senior, e.g. "Ma"), else falls back to senior's actual name (FB-1 fix,
    1 May 2026).

    Returns None if the code doesn't match or the senior is not active.
    """
    code = (code or "").strip().upper()
    if not code:
        return None
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT user_id, name, language, family_term, account_status FROM users "
                "WHERE family_linking_code = ?",
                (code,),
            ).fetchone()
        if not row:
            return None
        if row["account_status"] and row["account_status"] != "active":
            return None
        senior_name = row["name"] or "your family member"
        # FB-1: prefer family_term if the senior set one via /familycode
        family_term = None
        try:
            family_term = row["family_term"] if "family_term" in row.keys() else None
        except Exception:
            family_term = None
        display_name = family_term if family_term else senior_name
        return {
            "senior_user_id": row["user_id"],
            "senior_name":    senior_name,
            "display_name":   display_name,
            "language":       row["language"] or "english",
        }
    except Exception as e:
        logger.error("FAMILY | lookup_senior_by_code failed: %s", e)
        return None


def complete_join_for_senior(senior_user_id: int, family_telegram_id: int) -> tuple:
    """
    Register the family_telegram_id as a family member of senior_user_id, and
    return (success, welcome_message). Assumes the senior row was already
    validated as active by the caller (usually via lookup_senior_by_code).

    Extracted from join_by_code() so the bare-code auto-detect path (which has
    already done the lookup to show a confirmation question) doesn't have to
    re-resolve the code.
    """
    try:
        with get_connection() as conn:
            senior_row = conn.execute(
                "SELECT name, family_term FROM users WHERE user_id = ?",
                (senior_user_id,),
            ).fetchone()
        senior_name = (senior_row["name"] if senior_row else None) or "your family member"
        # FB-1 fix (1 May 2026): prefer family_term if the senior set one via
        # /familycode (e.g. "Ma"). Falls back to actual name for older seniors
        # who set up before this enhancement.
        family_term = None
        if senior_row:
            try:
                family_term = (
                    senior_row["family_term"]
                    if "family_term" in senior_row.keys() else None
                )
            except Exception:
                family_term = None
        display_name = family_term if family_term else senior_name

        # Check if already linked
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM family_members "
                "WHERE user_id = ? AND telegram_user_id = ?",
                (senior_user_id, family_telegram_id),
            ).fetchone()

        if existing:
            return True, (
                f"You're already connected to *{display_name}*'s Saathi. 🙏\n\n"
                f"Any message you send here will be passed to {display_name}.\n"
                f"You'll also receive a weekly update every Sunday."
            )

        # Register family member
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO family_members
                       (user_id, telegram_user_id, name, relationship, role)
                   VALUES (?, ?, 'Family', 'family', 'family')""",
                (senior_user_id, family_telegram_id),
            )
            conn.commit()

        # Enable weekly report for this senior (registration = consent)
        update_user_fields(senior_user_id, weekly_report_opt_in=1)

        logger.info(
            "FAMILY | joined | senior_user_id=%s | family_telegram_id=%s",
            senior_user_id, family_telegram_id,
        )

        return True, (
            f"You're now connected to *{display_name}*'s Saathi. 🙏\n\n"
            f"Any message you send here will be passed to {display_name}.\n\n"
            f"You'll also receive a brief update every Sunday — "
            f"mood, health mentions, and how their week went.\n\n"
            f"Type anything now to send {display_name} a message."
        )

    except Exception as e:
        logger.error("FAMILY | complete_join_for_senior failed: %s", e)
        return False, "Something went wrong. Please try again in a moment. 🙏"


def join_by_code(code: str, family_telegram_id: int) -> tuple:
    """
    Link a family member's Telegram user_id to a senior's profile.

    Returns (success: bool, message: str) to send back to the family member.
    """
    code = code.strip().upper()
    if not code:
        return False, (
            "Please send the code your family member shared with you.\n"
            "Example: /join ABC123"
        )

    senior = lookup_senior_by_code(code)
    if not senior:
        return False, (
            "That code doesn't match any Saathi profile. "
            "Please check the code and try again. 🙏"
        )

    return complete_join_for_senior(senior["senior_user_id"], family_telegram_id)


# ---------------------------------------------------------------------------
# Family bridge — relay messages to the senior
# ---------------------------------------------------------------------------

def get_family_member_info(senior_user_id: int, family_telegram_id: int) -> dict:
    """
    Return the family member's display name as the senior should see it.

    FB-3 fix (1 May 2026): no longer reads senior's stored language. Relay
    wrapper language is now decided per-message in relay_message_to_senior()
    based on the actual message script, not on a learned/drifted preference.
    """
    try:
        with get_connection() as conn:
            fm = conn.execute(
                "SELECT name FROM family_members "
                "WHERE user_id = ? AND telegram_user_id = ?",
                (senior_user_id, family_telegram_id),
            ).fetchone()

        return {
            "family_name": (fm["name"] if fm and fm["name"] else "Aapke parivar wale"),
        }
    except Exception:
        return {"family_name": "Aapke parivar wale"}


async def relay_message_to_senior(
    senior_user_id: int,
    family_telegram_id: int,
    message_text: str,
    bot,
) -> bool:
    """
    Forward a family member's message to the senior, formatted warmly.
    Returns True if sent successfully.
    """
    try:
        info = get_family_member_info(senior_user_id, family_telegram_id)
        family_name = info["family_name"]
        # FB-3 fix (1 May 2026): match wrapper language to the actual message
        # script, not the senior's stored language (which drifts via the
        # implicit script-detection learning loop and was producing Hindi
        # wrappers for English messages).
        language = detect_message_language(message_text)
        # FB-6 fix (1 May 2026): Telegram Markdown v1 was collapsing the
        # paragraph break adjacent to italics into an inline space on Android.
        # Use straight-quote prefix on the body and keep the \n\n separator.
        if language in ("hindi", "hinglish"):
            relay_text = (
                f"*{family_name}* ne aapko sandesh bheja hai 💌\n\n"
                f"\"{message_text}\""
            )
        else:
            relay_text = (
                f"*{family_name}* sent you a message 💌\n\n"
                f"\"{message_text}\""
            )

        await bot.send_message(
            chat_id=senior_user_id,
            text=relay_text,
            parse_mode="Markdown",
        )

        logger.info(
            "FAMILY | relay sent | senior_user_id=%s | from_family=%s | length=%d",
            senior_user_id, family_telegram_id, len(message_text),
        )
        return True

    except Exception as e:
        logger.error("FAMILY | relay_message_to_senior failed: %s", e)
        return False


def build_relay_confirmation(display_name: str, message_text: str) -> str:
    """
    Short confirmation to send back to the family member after relay.

    FB-1 fix (1 May 2026): takes display_name (family_term if set, else senior's
    actual name) so the family member sees the same term they were promised at
    the bare-code confirmation step.
    FB-3 fix (1 May 2026): language now decided per-message from the family
    member's own message script, not the senior's stored language.
    """
    language = detect_message_language(message_text)
    if language in ("hindi", "hinglish"):
        return f"Aapka sandesh *{display_name}* tak pahuncha diya gaya. 🙏"
    return f"Your message has been sent to *{display_name}*. 🙏"


# ---------------------------------------------------------------------------
# Weekly health report — built and sent every Sunday
# ---------------------------------------------------------------------------

def _get_mood_summary(user_id: int, language: str) -> str:
    """Derive a warm 1-2 sentence mood summary from last 7 diary entries."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """SELECT mood_score, mood_label, entry_date
                   FROM diary_entries
                   WHERE user_id = ?
                     AND entry_date >= date('now', '-7 days')
                   ORDER BY entry_date""",
                (user_id,),
            ).fetchall()

        if not rows:
            return (
                "Koi diary summary available nahi hai is hafte."
                if language in ("hindi", "hinglish")
                else "No diary summary available for this week."
            )

        scores = [r["mood_score"] for r in rows if r["mood_score"]]
        if not scores:
            return (
                "Mood data nahi mila." if language in ("hindi", "hinglish") else "No mood data recorded."
            )

        avg = sum(scores) / len(scores)
        # Trend: compare first to last
        trend_up = scores[-1] > scores[0]
        trend_flat = scores[-1] == scores[0]

        # Data sufficiency guard — only raise concern language if there is enough
        # evidence: at least 4 diary entries this week AND at least 2 of them scored
        # 2 or below. A single bad day (illness, bad news) can pull the average below 3
        # without signalling a genuine pattern. We never want to alarm a family member
        # on thin data.
        _enough_data = len(scores) >= 4
        _persistently_low = sum(1 for s in scores if s <= 2) >= 2

        if language in ("hindi", "hinglish"):
            if avg >= 4:
                return f"Kaafi achha — {len(scores)} diary entries mein se zyada acche rahe."
            elif avg >= 3:
                trend_str = "sudharta dikha" if trend_up else ("stable raha" if trend_flat else "thoda neeche gaya")
                return f"Theek-thaak raha — {trend_str} hafte mein."
            else:
                if _enough_data and _persistently_low:
                    return "Kuch dinon mein mood thoda neeche tha — thoda dhyan rakhein."
                else:
                    return "Hafte mein kuch mixed din rahe — ek-do din thoda neeche tha."
        else:
            if avg >= 4:
                return f"Quite good — {len(scores)} entries recorded, mostly positive."
            elif avg >= 3:
                trend_str = "improving" if trend_up else ("steady" if trend_flat else "slightly lower")
                return f"Fairly stable — {trend_str} over the week."
            else:
                if _enough_data and _persistently_low:
                    return "A few quieter, heavier days this week. Worth keeping an eye on."
                else:
                    return "A mixed week — one or two quieter days mixed in."

    except Exception as e:
        logger.warning("REPORT | mood summary failed: %s", e)
        return "—"


def _get_health_summary(user_id: int, language: str) -> str:
    """List health mentions from the last 7 days (max 3)."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """SELECT content FROM health_logs
                   WHERE user_id = ?
                     AND created_at >= datetime('now', '-7 days')
                     AND log_type = 'mention'
                   ORDER BY created_at
                   LIMIT 5""",
                (user_id,),
            ).fetchall()

        items = [r["content"] for r in rows if r["content"]]
        if not items:
            return (
                "Koi khaas sehat ka zikra nahi kiya is hafte."
                if language in ("hindi", "hinglish")
                else "No specific health mentions this week."
            )
        shown = items[:3]
        suffix = "..." if len(items) > 3 else ""
        return ", ".join(shown) + suffix

    except Exception as e:
        logger.warning("REPORT | health summary failed: %s", e)
        return "—"


def _get_medicine_summary(user_id: int, language: str) -> str:
    """Per-medicine adherence in plain language."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """SELECT medicine_name, ack_streak, miss_streak
                   FROM medicine_reminders
                   WHERE user_id = ? AND is_active = 1""",
                (user_id,),
            ).fetchall()

        if not rows:
            return (
                "Koi dawai reminder set nahi hai."
                if language in ("hindi", "hinglish")
                else "No medicines on schedule."
            )

        parts = []
        for r in rows:
            name = r["medicine_name"]
            ack = r["ack_streak"] or 0
            miss = r["miss_streak"] or 0
            if language in ("hindi", "hinglish"):
                if miss > 2:
                    parts.append(f"*{name}* — pichhle {miss} baar miss hui ⚠️")
                elif ack >= 3:
                    parts.append(f"*{name}* — niyamit le rahe hain ✅")
                else:
                    parts.append(f"*{name}* — theek hai")
            else:
                if miss > 2:
                    parts.append(f"*{name}* — missed {miss} times recently ⚠️")
                elif ack >= 3:
                    parts.append(f"*{name}* — taking regularly ✅")
                else:
                    parts.append(f"*{name}* — okay")

        return "\n  ".join(parts)

    except Exception as e:
        logger.warning("REPORT | medicine summary failed: %s", e)
        return "—"


def _get_activity_summary(user_id: int, language: str) -> str:
    """How active was the senior this week?"""
    try:
        with get_connection() as conn:
            count_row = conn.execute(
                """SELECT COUNT(*) as c FROM messages
                   WHERE user_id = ?
                     AND direction = 'in'
                     AND created_at >= datetime('now', '-7 days')""",
                (user_id,),
            ).fetchone()
            last_row = conn.execute(
                "SELECT last_active_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()

        msg_count = count_row["c"] if count_row else 0

        if language in ("hindi", "hinglish"):
            if msg_count > 40:
                return f"Bahut active rahe — {msg_count} messages bheje is hafte. 😊"
            elif msg_count > 15:
                return f"Niyamit baat ki — {msg_count} messages is hafte."
            elif msg_count > 0:
                return f"Thodi baat hui — {msg_count} messages is hafte."
            else:
                return "Is hafte koi message nahi aaya — check karein. 🙏"
        else:
            if msg_count > 40:
                return f"Very active — {msg_count} messages this week. 😊"
            elif msg_count > 15:
                return f"Regular conversations — {msg_count} messages this week."
            elif msg_count > 0:
                return f"Light week — {msg_count} messages."
            else:
                return "No messages this week — worth checking in. 🙏"

    except Exception as e:
        logger.warning("REPORT | activity summary failed: %s", e)
        return "—"


def build_weekly_report(
    senior_user_id: int,
    family_name: str,
    language: str = "hindi",
) -> str:
    """
    Build the full weekly health report for one family member.

    Language follows the senior's preferred language — so the family member
    receives the report in the language they're likely comfortable with.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM users WHERE user_id = ?",
                (senior_user_id,),
            ).fetchone()
        senior_name = row["name"] if row and row["name"] else "your family member"
    except Exception:
        senior_name = "your family member"

    is_hindi = language in ("hindi", "hinglish")

    if is_hindi:
        intro = (
            f"Namaste *{family_name}* ji,\n\n"
            f"Yeh raha is hafte *{senior_name}* ke baare mein chhota sa update. 🙏\n\n"
        )
        sections = (
            f"📊 *Mood:* {_get_mood_summary(senior_user_id, language)}\n\n"
            f"🩺 *Sehat:* {_get_health_summary(senior_user_id, language)}\n\n"
            f"💊 *Dawai:*\n  {_get_medicine_summary(senior_user_id, language)}\n\n"
            f"📱 *Sargarmiyaan:* {_get_activity_summary(senior_user_id, language)}"
        )
        footer = "\n\n— Saathi\n_Yeh report har Ravivar ko aata hai._"
    else:
        intro = (
            f"Hello *{family_name}*,\n\n"
            f"Here is a brief update on how *{senior_name}* has been this week. 🙏\n\n"
        )
        sections = (
            f"📊 *Mood:* {_get_mood_summary(senior_user_id, language)}\n\n"
            f"🩺 *Health mentions:* {_get_health_summary(senior_user_id, language)}\n\n"
            f"💊 *Medicines:*\n  {_get_medicine_summary(senior_user_id, language)}\n\n"
            f"📱 *Activity:* {_get_activity_summary(senior_user_id, language)}"
        )
        footer = "\n\n— Saathi\n_This report is sent every Sunday._"

    return intro + sections + footer


async def check_and_send_weekly_report(bot) -> None:
    """
    Called by the weekly_report_job scheduler (every minute, self-gated).

    Sends reports only on Sundays at 10am IST (±30 min window).
    Dedup: updates last_weekly_report_sent on family_members after sending.
    Never sends twice to the same family member on the same Sunday.
    """
    try:
        # IST = UTC+5:30
        now_utc = datetime.now(timezone.utc)
        ist_offset = timedelta(hours=5, minutes=30)
        now_ist = now_utc + ist_offset

        # Sunday = weekday() == 6 in Python
        if now_ist.weekday() != 6:
            return
        # 10:00–10:59 IST only
        if now_ist.hour != 10:
            return

        today_str = now_ist.strftime("%Y-%m-%d")

        with get_connection() as conn:
            seniors = conn.execute(
                """SELECT user_id, language FROM users
                   WHERE weekly_report_opt_in = 1
                     AND onboarding_complete = 1
                     AND (account_status IS NULL OR account_status = 'active')"""
            ).fetchall()

        for senior in seniors:
            senior_user_id = senior["user_id"]
            language = senior["language"] or "hindi"

            with get_connection() as conn:
                family_members = conn.execute(
                    """SELECT id, telegram_user_id, name, last_weekly_report_sent
                       FROM family_members
                       WHERE user_id = ?
                         AND telegram_user_id IS NOT NULL""",
                    (senior_user_id,),
                ).fetchall()

            for fm in family_members:
                # Skip if already sent this Sunday
                if fm["last_weekly_report_sent"] == today_str:
                    continue

                try:
                    report = build_weekly_report(
                        senior_user_id,
                        family_name=fm["name"] or "there",
                        language=language,
                    )
                    await bot.send_message(
                        chat_id=fm["telegram_user_id"],
                        text=report,
                        parse_mode="Markdown",
                    )
                    # Mark sent for today
                    with get_connection() as conn:
                        conn.execute(
                            "UPDATE family_members SET last_weekly_report_sent = ? WHERE id = ?",
                            (today_str, fm["id"]),
                        )
                        conn.commit()

                    logger.info(
                        "REPORT | sent | senior_user_id=%s | family_telegram_id=%s",
                        senior_user_id, fm["telegram_user_id"],
                    )

                except Exception as send_err:
                    logger.error(
                        "REPORT | send failed | senior=%s | family=%s | %s",
                        senior_user_id, fm["telegram_user_id"], send_err,
                    )

    except Exception as e:
        logger.error("REPORT | check_and_send_weekly_report failed: %s", e)
