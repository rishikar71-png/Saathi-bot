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
import random
import string
from datetime import datetime, timezone, timedelta

from database import get_connection, update_user_fields

logger = logging.getLogger(__name__)


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

    try:
        with get_connection() as conn:
            senior_row = conn.execute(
                "SELECT user_id, name, language, account_status FROM users "
                "WHERE family_linking_code = ?",
                (code,),
            ).fetchone()

        if not senior_row:
            return False, (
                "That code doesn't match any Saathi profile. "
                "Please check the code and try again. 🙏"
            )

        if senior_row["account_status"] and senior_row["account_status"] != "active":
            return False, "This profile is no longer active. Please check with your family."

        senior_user_id = senior_row["user_id"]
        senior_name = senior_row["name"] or "your family member"

        # Check if already linked
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM family_members "
                "WHERE user_id = ? AND telegram_user_id = ?",
                (senior_user_id, family_telegram_id),
            ).fetchone()

        if existing:
            return True, (
                f"You're already connected to *{senior_name}*'s Saathi. 🙏\n\n"
                f"Any message you send here will be passed to {senior_name}.\n"
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
            f"You're now connected to *{senior_name}*'s Saathi. 🙏\n\n"
            f"Any message you send here will be passed to {senior_name}.\n\n"
            f"You'll also receive a brief update every Sunday — "
            f"mood, health mentions, and how their week went.\n\n"
            f"Type anything now to send {senior_name} a message."
        )

    except Exception as e:
        logger.error("FAMILY | join_by_code failed: %s", e)
        return False, "Something went wrong. Please try again in a moment. 🙏"


# ---------------------------------------------------------------------------
# Family bridge — relay messages to the senior
# ---------------------------------------------------------------------------

def get_family_member_info(senior_user_id: int, family_telegram_id: int) -> dict:
    """Return name and language info for a registered family member."""
    try:
        with get_connection() as conn:
            fm = conn.execute(
                "SELECT name FROM family_members "
                "WHERE user_id = ? AND telegram_user_id = ?",
                (senior_user_id, family_telegram_id),
            ).fetchone()
            senior = conn.execute(
                "SELECT language FROM users WHERE user_id = ?",
                (senior_user_id,),
            ).fetchone()

        return {
            "family_name": (fm["name"] if fm and fm["name"] else "Aapke parivar wale"),
            "language": (senior["language"] if senior and senior["language"] else "hindi"),
        }
    except Exception:
        return {"family_name": "Aapke parivar wale", "language": "hindi"}


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
        language = info["language"]

        if language in ("hindi", "hinglish"):
            relay_text = (
                f"*{family_name}* ne aapko sandesh bheja hai 💌\n\n"
                f"_{message_text}_"
            )
        else:
            relay_text = (
                f"*{family_name}* sent you a message 💌\n\n"
                f"_{message_text}_"
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


def build_relay_confirmation(senior_name: str, language: str) -> str:
    """Short confirmation to send back to the family member after relay."""
    if language in ("hindi", "hinglish"):
        return f"Aapka sandesh *{senior_name}* tak pahuncha diya gaya. 🙏"
    return f"Your message has been sent to *{senior_name}*. 🙏"


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
