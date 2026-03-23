"""
Module 7 — Memory System

Four public functions:

  save_memory(user_id, memory_text, memory_type)
      Persist one memory to the memories table.

  get_relevant_memories(user_id, current_message=None) -> str
      Return a formatted context string: 5 most recent memories +
      last 3 diary summaries + same-day-last-week diary entry.
      Called by deepseek.py before every API call.

  extract_and_save_memories(user_id, user_message, bot_response)
      Ask DeepSeek to extract anything worth remembering from this
      conversation turn, then save each extracted memory.
      Called by main.py after every DeepSeek response.

  write_diary_entry(user_id) -> bool
      Summarise today's conversation transcript into a diary_entries row.
      Intended to be called nightly (Module 12 will schedule this).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from openai import OpenAI

from database import get_connection, save_message_record, upsert_diary_entry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy DeepSeek client — memory module creates its own instance to avoid
# circular imports with deepseek.py
# ---------------------------------------------------------------------------

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )
    return _client


def _call(prompt: str, max_tokens: int = 300) -> str:
    """Minimal DeepSeek call for extraction/summarisation tasks."""
    response = _get_client().chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# save_memory
# ---------------------------------------------------------------------------

def save_memory(user_id: int, memory_text: str, memory_type: str = "general") -> None:
    """
    Save one memory to the memories table.

    memory_type should be one of: family, health, preference, emotion, event, general
    Maps to the 'theme' column in the memories table.
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO memories (user_id, response_text, theme)
            VALUES (?, ?, ?)
            """,
            (user_id, memory_text.strip(), memory_type),
        )
        conn.commit()
    logger.info("MEMORY | user_id=%s | type=%s | saved: %s", user_id, memory_type, memory_text[:60])


# ---------------------------------------------------------------------------
# get_relevant_memories
# ---------------------------------------------------------------------------

def _format_diary_entry(row) -> str:
    """
    Format a single diary entry row into natural language for DeepSeek context.
    Prioritises emotional_context and notable_moments over raw mood labels.
    This is the difference between a database and a relationship.
    """
    lines = []
    if row["entry_date"]:
        lines.append(f"[{row['entry_date']}]")
    # Prefer emotional_context over bare mood label
    try:
        ec = row["emotional_context"]
    except Exception:
        ec = None
    if ec:
        lines.append(f"Emotional context: {ec}")
    elif row["full_summary"]:
        label = f" ({row['mood_label']})" if row["mood_label"] else ""
        lines.append(f"Summary{label}: {row['full_summary'][:200]}")

    try:
        nm = row["notable_moments"]
    except Exception:
        nm = None
    if nm:
        import json as _json
        moments = nm if isinstance(nm, list) else _json.loads(nm)
        if moments:
            lines.append("Notable moments: " + " | ".join(moments))

    if row["family_mentioned"]:
        import json as _json
        try:
            fam = _json.loads(row["family_mentioned"])
            if fam:
                lines.append(f"Family mentioned: {', '.join(fam)}")
        except Exception:
            pass

    return "\n".join(lines)


def get_relevant_memories(user_id: int, current_message: str = None) -> str:
    """
    Build a context string to inject into the DeepSeek system prompt.

    Includes:
    - 5 most recent memories from the memories table
    - Last 3 diary entries with full emotional context
    - Diary entry from exactly one week ago
    - Diary entry from exactly one month ago

    Returns an empty string if there is nothing to inject yet.
    """
    parts: list[str] = []

    with get_connection() as conn:
        # --- 5 most recent memories ---
        mem_rows = conn.execute(
            """
            SELECT response_text, theme, created_at
            FROM memories
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 5
            """,
            (user_id,),
        ).fetchall()

        # --- Last 3 diary entries (with emotional context) ---
        diary_rows = conn.execute(
            """
            SELECT entry_date, mood_label, emotional_context, notable_moments,
                   family_mentioned, full_summary
            FROM diary_entries
            WHERE user_id = ?
            ORDER BY entry_date DESC
            LIMIT 3
            """,
            (user_id,),
        ).fetchall()

        # --- Same day last week ---
        week_ago_row = conn.execute(
            """
            SELECT entry_date, mood_label, emotional_context, notable_moments,
                   family_mentioned, full_summary
            FROM diary_entries
            WHERE user_id = ? AND entry_date = date('now', '-7 days')
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        # --- Same day last month ---
        month_ago_row = conn.execute(
            """
            SELECT entry_date, mood_label, emotional_context, notable_moments,
                   family_mentioned, full_summary
            FROM diary_entries
            WHERE user_id = ? AND entry_date = date('now', '-30 days')
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

    if mem_rows:
        lines = [f"- [{row['theme']}] {row['response_text']}" for row in mem_rows]
        parts.append("Things I remember:\n" + "\n".join(lines))

    if diary_rows:
        diary_blocks = [_format_diary_entry(row) for row in diary_rows if row["full_summary"] or row["emotional_context"]]
        if diary_blocks:
            parts.append("Recent days:\n" + "\n\n".join(diary_blocks))

    extras: list[str] = []
    if week_ago_row and (week_ago_row["full_summary"] or week_ago_row["emotional_context"]):
        extras.append(f"This day last week:\n{_format_diary_entry(week_ago_row)}")
    if month_ago_row and (month_ago_row["full_summary"] or month_ago_row["emotional_context"]):
        extras.append(f"This day last month:\n{_format_diary_entry(month_ago_row)}")
    if extras:
        parts.append("\n\n".join(extras))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# extract_and_save_memories
# ---------------------------------------------------------------------------

def extract_and_save_memories(
    user_id: int, user_message: str, bot_response: str
) -> None:
    """
    After each conversation turn, ask DeepSeek to extract memorable facts
    and save each one.

    This makes one extra DeepSeek API call per turn.
    Failures are logged and silently swallowed — memory extraction must
    never crash the main message flow.
    """
    prompt = f"""Read this one conversation exchange and extract facts worth remembering for future conversations.

User said: {user_message}
Bot replied: {bot_response}

Rules:
- Only extract facts that are genuinely memorable (names, health info, preferences, emotional moments, significant events)
- Do NOT extract generic pleasantries, greetings, or obvious things
- Each memory should be a short, clear sentence
- Return a JSON array. Each object must have "text" and "type"
- Valid types: family, health, preference, emotion, event
- Return [] if there is nothing worth saving

Return only valid JSON. No explanation, no markdown.
"""
    try:
        raw = _call(prompt, max_tokens=200)
        # Strip markdown code fences if model wraps the JSON
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        memories = json.loads(raw)
        if not isinstance(memories, list):
            return
        saved = 0
        for m in memories:
            if isinstance(m, dict) and m.get("text"):
                save_memory(user_id, m["text"], m.get("type", "general"))
                saved += 1
        if saved:
            logger.info("MEMORY | user_id=%s | extracted %d memories", user_id, saved)
    except Exception as e:
        logger.warning("MEMORY | user_id=%s | extraction failed: %s", user_id, e)


# ---------------------------------------------------------------------------
# write_diary_entry
# ---------------------------------------------------------------------------

def write_diary_entry(user_id: int) -> bool:
    """
    Summarise today's conversation into a single diary_entries row.

    Fetches all of today's messages from the messages table, asks DeepSeek
    to analyse them, then upserts the result into diary_entries.

    Returns True if an entry was written, False if there were no messages today.
    Intended to be called nightly — Module 12 will schedule this.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_connection() as conn:
        msg_rows = conn.execute(
            """
            SELECT direction, content
            FROM messages
            WHERE user_id = ? AND date(created_at) = date('now')
            ORDER BY created_at ASC
            """,
            (user_id,),
        ).fetchall()

        p1_count = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM protocol_log
            WHERE user_id = ? AND protocol_type = '1' AND date(created_at) = date('now')
            """,
            (user_id,),
        ).fetchone()["cnt"]

        p3_count = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM protocol_log
            WHERE user_id = ? AND protocol_type = '3' AND date(created_at) = date('now')
            """,
            (user_id,),
        ).fetchone()["cnt"]

    if not msg_rows:
        logger.info("DIARY | user_id=%s | no messages today, skipping", user_id)
        return False

    # Build transcript — cap at 3000 chars to stay within token budget
    lines = []
    for row in msg_rows:
        speaker = "Senior" if row["direction"] == "in" else "Saathi"
        lines.append(f"{speaker}: {row['content']}")
    transcript = "\n".join(lines)[:3000]

    # Updated prompt (Module 7 — 22 March design decision):
    # Produces emotional_context and notable_moments, not just mood labels.
    # This is the difference between a database and a relationship.
    prompt = f"""You are creating a diary entry for an elderly Indian person's companion bot.
This diary entry will be read at the start of tomorrow's conversation to help
the bot speak naturally and personally.

Read the conversation below and create a diary entry with these fields:

1. mood: One word (happy / content / tired / quiet / irritated / sad / anxious / energetic)

2. emotional_context: 2-3 sentences describing the emotional texture of the conversation.
   This is the most important field. Write it so that tomorrow's bot can reference
   specific emotional moments naturally.

   GOOD: "Senior sounded really happy after talking about Priya's upcoming visit.
          Became animated and warm when describing Priya's cooking.
          The conversation had a light, nostalgic quality."

   BAD: "Senior was happy today. Talked about family."

3. health_mentions: Any physical symptoms, medicines mentioned, or health topics raised.
   Include exact words used where possible.
   Example: "Mentioned knee pain ('thoda dard hai ghuthe mein'), said it comes and goes."

4. family_mentions: Names of family members mentioned and context.
   Example: "Priya (daughter) — visiting next week, very excited about this.
              Rahul (son) — brief mention, no emotional weight."

5. topics: List of main topics discussed (be specific, not generic).
   Example: ["Priya's visit next week", "old Bombay memories", "cricket scores", "lunch"]
   Not: ["family", "food", "sports"]

6. notable_moments: 1-3 specific moments worth remembering for tomorrow.
   Things the bot can naturally reference.
   Example: ["Senior laughed a lot remembering the 1983 World Cup",
              "Mentioned feeling tired in the afternoon — something about a poor night's sleep",
              "Asked about a specific old Hindi song (Lag Ja Gale)"]

7. songs_requested: Any specific songs or music requested or mentioned.

8. protocol_triggers: Any Protocol 1 or Protocol 3 triggers that fired today. (Usually empty)

9. conversation_length: "short" / "medium" / "long" (rough estimate of engagement level)

Output as a JSON object with these exact field names.
Do not add any explanation — just the JSON.

CONVERSATION TO SUMMARISE:
{transcript}"""

    try:
        raw = _call(prompt, max_tokens=600)
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(raw)
    except Exception as e:
        logger.error("DIARY | user_id=%s | generation failed: %s", user_id, e)
        return False

    upsert_diary_entry(
        user_id=user_id,
        entry_date=today,
        mood_score=int(data.get("mood_score", 3)) if data.get("mood_score") else 3,
        mood_label=data.get("mood", data.get("mood_label", "neutral")),
        health_complaints=json.dumps(data.get("health_complaints", data.get("health_mentions", []))),
        family_mentioned=json.dumps(data.get("family_mentioned", data.get("family_mentions", []))),
        songs_requested=json.dumps(data.get("songs_requested", [])),
        reminders_acknowledged=0,
        protocol1_triggered=1 if p1_count > 0 else 0,
        protocol3_triggered=1 if p3_count > 0 else 0,
        emotions_summary=data.get("emotions_summary", data.get("emotional_context", "")[:400]),
        full_summary=data.get("full_summary", ""),
        # Module 7 new fields — emotional context and notable moments
        emotional_context=data.get("emotional_context", ""),
        notable_moments=json.dumps(data.get("notable_moments", [])),
    )

    logger.info(
        "DIARY | user_id=%s | entry written for %s | mood=%s",
        user_id, today, data.get("mood_label"),
    )
    return True
