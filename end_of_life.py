"""
End-of-Life Protocol
25 March 2026

Handles the death of a senior user.

TRIGGER: A message from a registered family member (in family_members table)
that contains death notification keywords.

VALIDATION: The message must come from a Telegram user_id that is stored in
family_members for this senior. Unknown user IDs are ignored — prevents abuse.

After notification:
- account_status → 'deceased'
- All reminders and scheduled messages silenced immediately
- Eulogy offer sent ONCE to the family contact
- 30-day data retention countdown begins
- At 30 days: all data permanently deleted

Integration checklist (all wired below in main.py calls):
1. check_if_family_member() + is_death_notification() in _run_pipeline()
2. check_data_deletion() in nightly job (00:05 IST via check_and_send_rituals)
3. account_status guard added to _send_ritual() and check_and_send_reminders()
4. Eulogy generation flow via handle_eulogy_response()
"""

from __future__ import annotations

import re
import logging
from datetime import datetime, timedelta, timezone

from database import get_connection, update_user_fields

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Death notification keywords — English + Hindi/Hinglish
# ---------------------------------------------------------------------------

DEATH_NOTIFICATION_KEYWORDS = [
    # English
    "passed away", "no more", "died", "is no more", "has died",
    "left us", "we lost", "passed on",
    # Hindi / Hinglish
    "nahi rahe", "nahin rahe", "chal base", "guzar gaye",
    "guzar gayi", "chale gaye", "chali gayi", "swarg sidhar gaye",
    "rab ne bula liya", "duniya chhod di",
]

EULOGY_YES_SIGNALS = [
    "yes", "haan", "ha", "han", "please", "sure", "okay", "ok",
    "bilkul", "zaroor", "chahiye", "chahta", "chahti", "send",
]


# Bug O (30 Apr 2026): "died" matched "studied" via substring. A family
# member's casual "I studied your suggestion" could flip the senior's
# account_status to 'deceased'. Compile as word-boundary regex.
_DEATH_NOTIFICATION_RE = [
    re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
    for kw in DEATH_NOTIFICATION_KEYWORDS
]


def is_death_notification(message_text: str) -> bool:
    """Return True if the message contains a death notification keyword.

    Bug O fix: word-boundary regex (was substring; "died" matched
    "studied").
    """
    text_lower = message_text.lower()
    return any(rx.search(text_lower) for rx in _DEATH_NOTIFICATION_RE)


def find_senior_for_family_member(sender_telegram_id: int) -> int | None:
    """
    Reverse lookup: given a Telegram user_id, find the senior they are
    registered as a family member for.

    Returns the senior's user_id, or None if this sender is not a
    registered family member for any senior.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                """SELECT user_id FROM family_members
                   WHERE telegram_user_id = ?
                   LIMIT 1""",
                (sender_telegram_id,),
            ).fetchone()
        return row["user_id"] if row else None
    except Exception as e:
        logger.warning("EOL | find_senior_for_family_member failed: %s", e)
        return None


def get_family_member_by_telegram_id(senior_user_id: int, sender_telegram_id: int):
    """
    Return the family_members row if sender_telegram_id is a registered
    family member for this senior. None otherwise.
    """
    try:
        with get_connection() as conn:
            return conn.execute(
                """SELECT * FROM family_members
                   WHERE user_id = ? AND telegram_user_id = ?
                   LIMIT 1""",
                (senior_user_id, sender_telegram_id),
            ).fetchone()
    except Exception as e:
        logger.warning("EOL | get_family_member failed: %s", e)
        return None


def handle_death_notification(senior_user_id: int, notifier_telegram_id: int) -> str:
    """
    Process a death notification for a senior user.

    Steps:
    1. Update account_status to 'deceased', record timestamp and notifier
    2. Disable all medicine reminders
    3. Return the eulogy offer message to send to the family contact

    account_status = 'deceased' silences all proactive messages immediately —
    the guards in rituals.py and reminders.py check this field.

    Args:
        senior_user_id: The senior's Telegram user_id (primary key in users)
        notifier_telegram_id: Telegram user_id of the family member who notified

    Returns:
        Eulogy offer message string, or None on failure
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        # Update account status
        update_user_fields(
            senior_user_id,
            account_status="deceased",
            death_notification_timestamp=now,
            death_notified_by=str(notifier_telegram_id),
        )

        # Disable all medicine reminders
        with get_connection() as conn:
            conn.execute(
                "UPDATE medicine_reminders SET is_active = 0 WHERE user_id = ?",
                (senior_user_id,),
            )
            conn.commit()

        # Get senior's name for the eulogy offer
        with get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM users WHERE user_id = ?",
                (senior_user_id,),
            ).fetchone()
        name = row["name"] if row and row["name"] else "your family member"

        logger.info("EOL | death notification processed | senior_user_id=%s", senior_user_id)
        return EULOGY_OFFER_MESSAGE.format(name=name)

    except Exception as e:
        logger.error("EOL | handle_death_notification failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Eulogy offer — sent ONCE to the family contact
# ---------------------------------------------------------------------------

EULOGY_OFFER_MESSAGE = (
    "I am deeply sorry to hear about {name}.\n\n"
    "Over our conversations, {name} shared many memories and moments with me. "
    "If you would like, I can put together a short note — the things {name} talked about, "
    "the stories they loved telling, what made them smile.\n\n"
    "There is no rush. If you would like this, just reply *yes*. "
    "If not, that is perfectly fine too.\n\n"
    "I will keep {name}'s conversations safe for 30 days. "
    "After that, everything will be permanently deleted."
)


def is_eulogy_yes(message_text: str) -> bool:
    """Return True if the family contact is saying yes to the eulogy offer."""
    t = message_text.lower().strip()
    # Bug P (30 Apr 2026): substring "yes"/"ha"/"ok" matched "yesterday"/
    # "happy"/"stocks". Use word-boundary regex.
    return any(
        re.search(r"\b" + re.escape(s) + r"\b", t)
        for s in EULOGY_YES_SIGNALS
    )


def build_eulogy_prompt(senior_user_id: int) -> str:
    """
    Build the DeepSeek prompt for eulogy generation.
    Pulls from memories, diary_entries, and user profile.

    Returns the prompt string to send to DeepSeek.
    Set eulogy_delivered = 1 after sending.
    """
    try:
        with get_connection() as conn:
            memories = conn.execute(
                """SELECT question_text, response_text
                   FROM memories
                   WHERE user_id = ?
                   ORDER BY created_at""",
                (senior_user_id,),
            ).fetchall()

            diary_moments = conn.execute(
                """SELECT entry_date, emotional_context, notable_moments
                   FROM diary_entries
                   WHERE user_id = ?
                   ORDER BY entry_date DESC
                   LIMIT 30""",
                (senior_user_id,),
            ).fetchall()

            profile = conn.execute(
                "SELECT name, favourite_topics, music_preferences, religion FROM users WHERE user_id = ?",
                (senior_user_id,),
            ).fetchone()

        name = profile["name"] if profile and profile["name"] else "this person"

        memory_text = "\n".join(
            [f"- When asked '{m['question_text']}', they said: {m['response_text']}"
             for m in memories if m["response_text"]]
        ) if memories else "No stored memory bank responses."

        moments_text = "\n".join(
            [f"- {d['entry_date']}: {d['emotional_context'] or ''} {d['notable_moments'] or ''}".strip()
             for d in diary_moments]
        ) if diary_moments else "No diary moments recorded."

        return EULOGY_GENERATION_PROMPT.format(
            name=name,
            memories=memory_text,
            moments=moments_text,
        )

    except Exception as e:
        logger.error("EOL | build_eulogy_prompt failed: %s", e)
        return None


EULOGY_GENERATION_PROMPT = (
    "You are writing a short, warm note about {name} for their family.\n"
    "{name} has passed away. Their family has asked for this note.\n\n"
    "Here are the memories {name} shared during conversations:\n"
    "{memories}\n\n"
    "Here are some moments from their recent days:\n"
    "{moments}\n\n"
    "Write a note of 8-12 sentences. Rules:\n"
    "- Warm, respectful, personal — not generic\n"
    "- Reference specific things they said or cared about\n"
    "- Do not include anything that could cause family conflict\n"
    "- Do not include anything about health complaints or medications\n"
    "- Do not include anything from Protocol 3 (financial/legal) conversations\n"
    "- The tone is: a friend remembering someone they cared about\n"
    "- End with something forward-looking for the family — not a platitude, "
    "but something real that {name} would have wanted them to know\n\n"
    "This note is sent once. Make it count."
)


# ---------------------------------------------------------------------------
# 30-day deletion — called from nightly job
# ---------------------------------------------------------------------------

def check_data_deletion() -> None:
    """
    Called at 00:05 IST in the nightly job.

    For any user with account_status = 'deceased':
    If 30 days have passed since death_notification_timestamp,
    permanently delete all their data. This is irreversible.
    """
    try:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=30)).isoformat()

        with get_connection() as conn:
            deceased_users = conn.execute(
                """SELECT user_id, name, death_notification_timestamp
                   FROM users
                   WHERE account_status = 'deceased'
                     AND death_notification_timestamp IS NOT NULL
                     AND death_notification_timestamp < ?""",
                (cutoff,),
            ).fetchall()

        for user in deceased_users:
            user_id = user["user_id"]
            logger.info(
                "EOL | 30-day window expired | user_id=%s | deleting all data",
                user_id,
            )
            _permanently_delete_user_data(user_id)

    except Exception as e:
        logger.error("EOL | check_data_deletion failed: %s", e)


def _permanently_delete_user_data(user_id: int) -> None:
    """
    Permanently delete all data for a deceased user after 30-day retention.
    Irreversible.
    """
    tables = [
        "diary_entries",
        "health_logs",
        "memories",
        "medicine_reminders",
        "heartbeat_log",
        "protocol_log",
        "session_log",
        "messages",
        "user_activity_patterns",
        "ritual_log",
        "family_members",
    ]
    try:
        with get_connection() as conn:
            for table in tables:
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            conn.commit()
        logger.info("EOL | permanent deletion complete | user_id=%s", user_id)
    except Exception as e:
        logger.error("EOL | _permanently_delete_user_data failed | user_id=%s | %s", user_id, e)
