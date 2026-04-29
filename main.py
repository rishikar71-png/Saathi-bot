from __future__ import annotations

import asyncio
import io
import os
import re
import logging
import time
from collections import deque
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv
from database import (
    init_db, run_startup_migrations, get_or_create_user, save_message_record,
    save_session_turn, get_session_messages, admin_reset_user, get_setup_person,
    get_family_members,
)
from deepseek import call_deepseek, get_user_local_hour, get_time_of_day_label
from protocol1 import check_protocol1
from protocol3 import check_protocol3
from protocol4 import check_protocol4
from onboarding import (
    get_intro_message,
    get_opening_detection_question,
    get_resume_prompt,
    handle_onboarding_answer,
    handle_mode_detection,
    handle_bridge_answer,
    maybe_resume_day2_bridge,
    get_handoff_message,
    get_setup_child_name,
    is_confused_senior,
    get_confusion_response,
    detect_archetype_signal,
    get_archetype_adjustment_text,
)
from memory import extract_and_save_memories
from whisper import transcribe_voice
from tts import text_to_speech
from youtube import detect_music_request, find_music, build_music_message
from reminders import (
    check_and_send_reminders,
    is_acknowledgement,
    mark_reminder_acknowledged,
)
from rituals import check_and_send_rituals, record_first_message
from safety import (
    check_emergency_keywords,
    send_help_prompt,
    handle_help_command,
    handle_help_callback,
    check_inactivity,
)
from end_of_life import (
    find_senior_for_family_member,
    is_death_notification,
    handle_death_notification,
    is_eulogy_yes,
    build_eulogy_prompt,
)
from family import (
    get_or_create_linking_code,
    join_by_code,
    lookup_senior_by_code,
    complete_join_for_senior,
    relay_message_to_senior,
    build_relay_confirmation,
    check_and_send_weekly_report,
)
from policy import POLICY_COMMAND_RESPONSE, USER_POLICY_DOCUMENT

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", 8443))

# Tracks how many times Protocol 1 has fired per user in this process lifetime.
# Resets on bot restart — sufficient for MVP.
_protocol1_session_counts: dict[int, int] = {}

# Archetype onboarding adjustments — First 7 Days only.
# In-memory: 'striver', 'quiet_one', or 'default'. No DB storage.
# Resets on bot restart — recalculates from messages if needed.
_archetype_cache: dict[int, str] = {}

# Language learning loop — tracks consecutive detections of a language
# different from the user's stored preference.
# Structure: {user_id: {"detected": "hindi", "streak": 3}}
# When streak reaches 5, users.language is updated in the DB.
_language_learning: dict[int, dict] = {}

# ---------------------------------------------------------------------------
# In-memory session store — replaces DB-backed get_session_messages /
# save_session_turn in the hot path.
#
# Why: save_session_turn triggers a Turso cloud commit (~10–12s). With the
# old pattern, every message caused 2-3 blocking syncs. With in-memory
# sessions, the hot path never touches the DB for session context.
#
# Gap logic: if the user's last message was >12 minutes ago, clear the
# session (start fresh). This also fixes session contamination — a cricket
# conversation from 20 minutes ago no longer bleeds into "hello".
#
# DB persist: session turns are still written to session_messages table,
# but async via the write queue below (for diary generation / analytics).
# ---------------------------------------------------------------------------
_SESSION_STORE: dict[int, dict] = {}   # user_id → {"turns": deque, "last_ts": float}
_SESSION_GAP_SEC = 12 * 60             # 12 minutes = new session boundary
_SESSION_MAX_ITEMS = 24                # 12 exchange pairs max

# ---------------------------------------------------------------------------
# User row cache — eliminates the ~5s Turso sync on get_or_create_user
# ---------------------------------------------------------------------------
# Every call to get_or_create_user queries libsql/Turso (~4-5s cloud sync).
# For regular conversation the user row is stable — caching is safe.
# Invalidate after any DB write that changes the user row (onboarding steps,
# language updates). Cache is in-process; restarts clear it automatically.
# ---------------------------------------------------------------------------
_USER_CACHE: dict[int, dict] = {}
_STARTUP_MONOTONIC = time.monotonic()   # set once at import time for uptime tracking


async def _get_user_with_cache(user_id: int):
    """Return user row from memory (<1ms) or Turso (slow, first call only)."""
    cached = _USER_CACHE.get(user_id)
    if cached is not None:
        return cached
    try:
        row = await asyncio.to_thread(get_or_create_user, user_id)
    except Exception as _cache_err:
        _err_lower = str(_cache_err).lower()
        if "no such table" in _err_lower:
            # After a connection reset the local replica may be empty (Turso sync
            # returned a blank DB).  Re-run startup migrations + init_db — both
            # are fully idempotent (IF NOT EXISTS / INSERT OR IGNORE) — then retry.
            logging.getLogger(__name__).warning(
                "DB | 'no such table' after reset — re-running schema init and retrying"
            )
            await asyncio.to_thread(run_startup_migrations)
            await asyncio.to_thread(init_db)
            row = await asyncio.to_thread(get_or_create_user, user_id)
        else:
            raise
    if row:
        _USER_CACHE[user_id] = dict(row)
    return _USER_CACHE.get(user_id)


def _invalidate_user_cache(user_id: int) -> None:
    """Drop cached row so next access re-reads from DB."""
    _USER_CACHE.pop(user_id, None)


# ---------------------------------------------------------------------------
# Family-member cache — eliminates the ~5–30s sync Turso call inside
# _run_pipeline that checks whether the incoming user is a registered
# family contact rather than a senior.
# ---------------------------------------------------------------------------
# Root cause of 37s delays: find_senior_for_family_member() makes a raw
# Turso query at the TOP of _run_pipeline (before the placeholder is sent).
# When memory-extraction background tasks hold the libsql global-connection
# mutex for ~28s (6 DeepSeek calls × 4s each), this query blocks for the
# entire duration, stalling the event loop and preventing the placeholder
# from being sent.
#
# Family membership never changes after setup, so an in-process cache is
# 100% correct. First lookup uses asyncio.to_thread (yields to event loop);
# every subsequent lookup returns in <1ms from the dict.
# ---------------------------------------------------------------------------
_FAMILY_CACHE: dict[int, object] = {}
_FM_NOT_CACHED = object()   # sentinel — distinguishes "not looked up" from None


async def _senior_for_family_cached(user_id: int):
    """
    Return senior_id if user_id is a registered family contact, else None.
    Cached permanently — family membership is set at onboarding and never changes.
    First call: asyncio.to_thread (non-blocking). Subsequent calls: dict lookup.
    """
    cached = _FAMILY_CACHE.get(user_id, _FM_NOT_CACHED)
    if cached is not _FM_NOT_CACHED:
        return cached   # None (not a family member) or int (senior_id)
    result = await asyncio.to_thread(find_senior_for_family_member, user_id)
    _FAMILY_CACHE[user_id] = result
    return result


_SESSION_RESET_SIGNALS = {
    "bye", "goodbye", "good night", "goodnight", "talk later", "see you",
    "kal baat karenge", "baad mein baat karte hain", "shubh ratri", "alvida",
    "phir milenge", "raat ko baat karte hain",
}


def _live_session_get(user_id: int, text: str) -> list:
    """Return current session turns from memory. Resets on gap or reset signal."""
    now = time.monotonic()
    state = _SESSION_STORE.get(user_id)
    is_reset = text.strip().lower() in _SESSION_RESET_SIGNALS
    if state is None or is_reset or (now - state["last_ts"]) > _SESSION_GAP_SEC:
        _SESSION_STORE[user_id] = {
            "turns": deque(maxlen=_SESSION_MAX_ITEMS),
            "last_ts": now,
        }
        return []
    state["last_ts"] = now
    return list(state["turns"])


def _live_session_append(user_id: int, role: str, content: str) -> None:
    """Append a turn to the in-memory session (instant, no I/O)."""
    if user_id not in _SESSION_STORE:
        _SESSION_STORE[user_id] = {
            "turns": deque(maxlen=_SESSION_MAX_ITEMS),
            "last_ts": time.monotonic(),
        }
    _SESSION_STORE[user_id]["turns"].append({"role": role, "content": content})
    _SESSION_STORE[user_id]["last_ts"] = time.monotonic()


# ---------------------------------------------------------------------------
# Async write queue — serialises all DB writes through a single background
# worker so they never block the request path.
#
# Why a single worker: libsql/_GLOBAL_CONN is single-writer. Multiple threads
# calling commit() concurrently contend on the same connection. One worker
# eliminates contention entirely; writes are serialised and Turso syncs happen
# one at a time in the background while the user already has their reply.
#
# Initialised in post_init() once the asyncio event loop is running.
# ---------------------------------------------------------------------------
_DB_WRITE_QUEUE: asyncio.Queue | None = None


async def _db_writer_worker() -> None:
    """Single background coroutine that drains the DB write queue."""
    while True:
        fn, args, kwargs = await _DB_WRITE_QUEUE.get()
        try:
            await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as _wq_err:
            logger.warning("DB_QUEUE | write failed | fn=%s | %s", fn.__name__, _wq_err)
        finally:
            _DB_WRITE_QUEUE.task_done()


def _db_queue(fn, *args, **kwargs) -> None:
    """
    Enqueue a DB write function for background execution.
    Falls back to a fire-and-forget asyncio.to_thread call if the queue
    isn't running yet (e.g. during startup before post_init completes).
    Never blocks the caller.
    """
    if _DB_WRITE_QUEUE is not None:
        try:
            _DB_WRITE_QUEUE.put_nowait((fn, args, kwargs))
        except asyncio.QueueFull:
            logger.warning("DB_QUEUE | full — dropping write: %s", fn.__name__)
    else:
        # Queue not ready yet — best-effort synchronous fallback during startup
        try:
            fn(*args, **kwargs)
        except Exception as _fb_err:
            logger.warning("DB_QUEUE | fallback write failed | fn=%s | %s", fn.__name__, _fb_err)


def _get_archetype_adjustment(user_id: int, days_since_first_message: int) -> str | None:
    """
    Return archetype adjustment text from in-memory cache, or None.

    Non-blocking: never does a DB query in the hot path. If the cache is
    empty, returns None immediately — the background task below populates
    it for the next message turn.
    """
    if days_since_first_message > 7:
        return None
    if user_id in _archetype_cache:
        return get_archetype_adjustment_text(_archetype_cache[user_id])
    return None


async def _detect_archetype_background(user_id: int) -> None:
    """
    Background coroutine: reads first 5 inbound messages from DB and
    populates _archetype_cache. Runs once per user (skips if already cached).
    Fires via asyncio.create_task — never awaited in the request path.
    """
    if user_id in _archetype_cache:
        return
    try:
        def _read():
            from database import get_connection
            with get_connection() as conn:
                return conn.execute(
                    "SELECT content FROM messages WHERE user_id=? AND direction='in' "
                    "ORDER BY created_at LIMIT 5",
                    (user_id,),
                ).fetchall()
        rows = await asyncio.to_thread(_read)
        if len(rows) >= 3:
            messages = [r["content"] for r in rows if r["content"]]
            label = detect_archetype_signal(messages)
            _archetype_cache[user_id] = label
            logger.info("ARCHETYPE_BG | user_id=%s | detected=%s", user_id, label)
    except Exception as e:
        logger.warning("ARCHETYPE_BG | failed | user_id=%s | %s", user_id, e)


# ---------------------------------------------------------------------------
# Language detection helpers
# ---------------------------------------------------------------------------

def _detect_message_language(text: str) -> str:
    """
    Detect the dominant language of a message.

    Returns: 'hindi', 'hinglish', or 'english'.

    Rules:
    - If the message contains significant Devanagari script → 'hindi'
    - If the message contains common Hindi/Urdu words in Roman script
      (at least 2 known words) → 'hinglish'
    - Otherwise → 'english'

    This is intentionally simple — false positives are fine.
    The goal is to catch clear cases, not edge cases.
    """
    import unicodedata

    # Count Devanagari characters
    devanagari_count = sum(
        1 for ch in text
        if '\u0900' <= ch <= '\u097F'  # Devanagari Unicode block
    )
    # If more than 3 Devanagari chars, it's Hindi
    if devanagari_count > 3:
        return "hindi"

    # Check for common Hindi/Urdu words written in Roman script
    _HINGLISH_MARKERS = [
        "hoon", "hun", "hai", "hain", "tha", "thi", "the",
        "kya", "nahi", "nhi", "acha", "achha", "theek", "thik",
        "bilkul", "haan", "naa", "bhi", "aur", "lekin", "par",
        "mujhe", "mera", "meri", "mere", "aap", "tum", "main",
        "kuch", "bahut", "thoda", "zyada", "bohot",
        "abhi", "aaj", "kal", "phir", "dobara",
        "ghar", "khana", "pani", "beta", "beti",
        "ji", "yaar", "bhai", "didi",
    ]
    text_lower = text.lower()
    # Word-boundary aware match: split on spaces and punctuation
    words = set(text_lower.replace(",", " ").replace(".", " ").split())
    hinglish_hits = sum(1 for w in _HINGLISH_MARKERS if w in words)
    if hinglish_hits >= 2:
        return "hinglish"

    return "english"


def _update_language_learning(user_id: int, stored_language: str, detected_language: str) -> str:
    """
    Maintain a rolling streak of detected language signals.
    If 5 consecutive messages are detected as a different language than stored,
    update the DB preference and return the new language.
    Returns the language that should be used for this response.
    """
    # Treat hindi and hinglish as compatible — don't flap between them
    def _family(lang: str) -> str:
        return "hindi_family" if lang in ("hindi", "hinglish") else "english"

    stored_family = _family(stored_language)
    detected_family = _family(detected_language)

    if detected_family == stored_family:
        # Consistent — reset streak
        _language_learning.pop(user_id, None)
        return stored_language

    # Different family detected — track streak
    entry = _language_learning.get(user_id, {"detected": detected_language, "streak": 0})
    if entry["detected"] != detected_language:
        # Detection changed mid-streak — reset
        entry = {"detected": detected_language, "streak": 0}
    entry["streak"] += 1
    _language_learning[user_id] = entry

    if entry["streak"] >= 5:
        # 5 consecutive messages in a different language — update DB preference
        try:
            from database import update_user_fields as _uuf_learn
            _uuf_learn(user_id, language=detected_language)
            logger.info(
                "LANG_LEARN | user_id=%s | updated preference: %s → %s",
                user_id, stored_language, detected_language,
            )
        except Exception as _le:
            logger.warning("LANG_LEARN | DB update failed | user_id=%s | %s", user_id, _le)
        _language_learning.pop(user_id, None)
        return detected_language

    # Use detected language for this response even before the DB update
    return detected_language


# ---------------------------------------------------------------------------
# Live data injection — news / cricket / weather queries in conversation
# ---------------------------------------------------------------------------

_NEWS_QUERY_SIGNALS = re.compile(
    r"\b(news|headlines?|current events?|khabar|khabaren|aaj ki khabar|"
    r"what.?s happening|kya ho raha|aaj kya hua|duniya mein kya|india mein kya|"
    r"latest (news|updates?)|tell me (the )?news|koi khabar|kuch naya)\b",
    re.IGNORECASE,
)
_CRICKET_QUERY_SIGNALS = re.compile(
    r"\b(cricket|score|match|india (won|lost|playing)|test match|"
    r"ipl|odi|t20|kya hua match mein|cricket mein kya)\b",
    re.IGNORECASE,
)
_WEATHER_QUERY_SIGNALS = re.compile(
    r"\b(weather|temperature|mausam|garmi|sardi|barish|kitni garmi|"
    r"how.?s the weather|aaj mausam|what.?s the temp)\b",
    re.IGNORECASE,
)


def _inject_live_data_if_needed(text: str, user_context: dict) -> str | None:
    """
    If the user's message is a news, cricket, or weather query, fetch live data
    and return an injection block for the DeepSeek system prompt.

    Returns None if the message is not a live-data query.
    Returns a string instruction block if it is (whether or not API data was available).
    The block either contains real data or explicitly tells DeepSeek not to hallucinate.
    """
    is_news    = bool(_NEWS_QUERY_SIGNALS.search(text))
    is_cricket = bool(_CRICKET_QUERY_SIGNALS.search(text))
    is_weather = bool(_WEATHER_QUERY_SIGNALS.search(text))

    if not (is_news or is_cricket or is_weather):
        return None

    try:
        from apis import fetch_news, fetch_cricket, fetch_cricket_news, fetch_weather
    except ImportError:
        return None

    parts = []

    if is_weather or is_news or is_cricket:
        parts.append(
            "LIVE DATA CONTEXT — CRITICAL RULES:\n"
            "1. Use ONLY the data provided below. Do NOT use training knowledge for weather, news, or cricket.\n"
            "2. Sections marked '— raw data:' contain real live data. Present this warmly and conversationally.\n"
            "   Do NOT read numbers cold. Wrap them in human terms.\n"
            "   INDIA TEMPERATURE SCALE — use this, not Western norms:\n"
            "     Below 20°C = cool or cold | 20–25°C = mild or comfortable | 26–29°C = warm\n"
            "     30–34°C = quite warm, getting hot | 35–38°C = hot | 39°C+ = very hot\n"
            "   NEVER describe 28°C+ as 'pleasant', 'mild', 'cool', or 'refreshing' for Indian cities.\n"
            "   Example: '31°C, scattered clouds' → 'quite warm today — not the coolest day'\n"
            "   Cricket raw data includes a status prefix: LIVE NOW / TODAY (upcoming) / COMPLETED TODAY / UPCOMING.\n"
            "   Use the prefix to speak accurately: 'there's a match on right now' vs 'there's one later today' vs 'the match finished earlier'.\n"
            "3. If a section says 'No live data' — be honest. Do NOT invent temperatures, scores, or headlines.\n"
            "4. Honest response when unavailable: 'I don't have live [weather/news/cricket] at the moment.'"
        )

    profile_city = user_context.get("city") or ""

    # Always try to extract city from the message first — if the senior asks about
    # Delhi weather, use Delhi, even if their profile city is Mumbai.
    # Fall back to profile city only when no city is mentioned in the message.
    _TRAILING_NON_CITY = re.compile(
        r"\b(today|tonight|right now|right|currently|now|just now|"
        r"aaj|abhi|kal|tomorrow|this week|is waqt|"
        r"mein|ka|ki|ke|hai|hain|kya|please|batao|bata|do|"
        r"at|the|this|morning|evening|afternoon|in|ka|bata|karo|zara|jaraa)\b.*$",
        re.IGNORECASE,
    )
    city = profile_city  # default
    if is_weather:
        _city_match = re.search(
            r"(?:weather|mausam|temperature)\s+(?:in|mein|of|ka|ki)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)",
            text, re.IGNORECASE
        ) or re.search(
            r"([A-Za-z]+(?:\s+[A-Za-z]+)?)\s+(?:mein|ka|ki)\s+(?:mausam|weather|temperature)",
            text, re.IGNORECASE
        )
        if _city_match:
            raw_city = _city_match.group(1).strip()
            raw_city = _TRAILING_NON_CITY.sub("", raw_city).strip()
            if raw_city:
                city = raw_city.title()

    if is_weather and city:
        try:
            raw = fetch_weather(city)
            if raw:
                parts.append(f"Weather ({city}) — raw data: {raw}")
            else:
                parts.append(f"Weather ({city}): No live weather data available right now.")
        except Exception:
            parts.append("Weather: Not available right now.")
    elif is_weather and not city:
        parts.append("Weather: I don't know your city — ask me like 'what's the weather in Delhi?' and I'll check.")

    if is_cricket:
        # Disambiguate news-intent vs schedule-intent so news queries route to RSS
        # cricket coverage (Bug E, 29 Apr) instead of always hitting the schedule
        # path. Examples:
        #   News-intent  : "any cricket news?", "Hardik kaisa khel raha?", "IPL preview"
        #   Schedule     : "aaj match?", "score?", "kya ho raha hai"
        is_cricket_news_intent = bool(re.search(
            r"\b(news|khabar|khabren|headline|headlines|analysis|preview|"
            r"report|highlights|sunao|batao|kaisa khel|kaise khel|kaisi)\b",
            text, re.IGNORECASE,
        ))

        if is_cricket_news_intent:
            try:
                cnews = fetch_cricket_news(query_text=text)
                if cnews:
                    parts.append(f"Cricket news — raw data:\n{cnews}")
                else:
                    # No news from RSS — fall back to schedule path so the senior
                    # at least gets factual today's-match info if any exists.
                    raw = fetch_cricket()
                    if raw:
                        parts.append(f"Cricket — raw data: {raw}")
                    else:
                        parts.append(
                            "Cricket news: I don't have current cricket headlines right now."
                        )
            except Exception as _cn_err:
                logger.debug("LIVE_DATA | cricket_news error | %s", _cn_err)
                parts.append("Cricket news: temporarily unavailable.")
        else:
            try:
                raw = fetch_cricket()
                if raw:
                    parts.append(f"Cricket — raw data: {raw}")
                    # Supplementary analysis — only added when a real match
                    # exists, so it can't conflict with the no-match scripted
                    # response below.
                    try:
                        cnews = fetch_cricket_news(query_text=text)
                        if cnews:
                            parts.append(
                                f"Cricket news (supplementary analysis) — raw data:\n{cnews}"
                            )
                    except Exception:
                        pass
                else:
                    # Explicit scripted fallback — prevents DeepSeek using training knowledge
                    # to fabricate yesterday's scores or invent a match result.
                    # Note: Saathi tracks both India international matches AND all IPL teams.
                    # If this fires, there is genuinely no live or scheduled match today.
                    parts.append(
                        "CRICKET — MANDATORY SCRIPTED RESPONSE:\n"
                        "The live cricket API found no match scheduled for today (India international or IPL).\n"
                        "You MUST respond with ONLY these sentences (adapt language to the user's preference):\n"
                        "English: \"No cricket today — at least not in the schedule I can see. "
                        "I'll have live updates the next time there's a match.\"\n"
                        "Hindi: \"Aaj koi cricket match nahi dikh raha — schedule mein kuch nahi hai. "
                        "Jab match hoga, main turant bata dunga.\"\n"
                        "Do NOT add any match names, scores, teams, venues, or results from your training data.\n"
                        "Do NOT use any cricket knowledge from your training data."
                    )
            except Exception:
                parts.append(
                    "CRICKET — MANDATORY SCRIPTED RESPONSE:\n"
                    "Cricket data is unavailable right now.\n"
                    "You MUST respond with ONLY: \"I can't get cricket scores right now — I'll try again later.\"\n"
                    "Do NOT fabricate match results."
                )

    if is_news:
        news_interests = user_context.get("news_interests") or user_context.get("favourite_topics") or ""
        try:
            # Pass query_text=text so fetch_news can detect world/country intent
            # and use international RSS feeds (BBC, Reuters) instead of India-only feeds.
            raw = fetch_news(news_interests, query_text=text)
            if raw:
                headlines = [h.strip() for h in raw.strip().split("\n") if h.strip()]
                n = len(headlines)
                parts.append(
                    f"NEWS HEADLINES — DELIVER FACTUALLY ({n} stories available today):\n"
                    + "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
                    + "\n\nINSTRUCTION: Share these stories naturally. "
                    f"If this is the first time the user is asking about news today, give all {n} — "
                    f"say 'Here's what's happening:' and list them briefly. "
                    f"If they have already received these stories in this conversation, acknowledge that "
                    f"warmly and say you don't have more right now. "
                    f"Do NOT add emotional framing or counsellor language. "
                    f"Do NOT fabricate any additional stories beyond what is listed above."
                )
            else:
                parts.append("News: No live headlines available right now. Tell the user honestly.")
        except Exception:
            parts.append("News: Not available right now. Tell the user honestly.")

    if len(parts) <= 1:
        # Only the header, no actual data
        return (
            "LIVE DATA CONTEXT:\n"
            "The user is asking about live news or events. You do not have access to live data "
            "right now. Tell them honestly and warmly — do not guess, do not give cricket or "
            "sports results unless confirmed above, and do not repeat old information."
        )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Streaming helper — runs DeepSeek in a thread, edits Telegram message live
# ---------------------------------------------------------------------------

async def _async_reply(
    update,
    text: str,
    user_context: dict,
    session_history: list,
    placeholder_msg=None,
) -> str:
    """
    Run call_deepseek in a background thread via asyncio.to_thread so it
    doesn't block the event loop (other users can be served while this one waits).

    If placeholder_msg is already sent (pre-sent before slow API calls), edits
    it with the reply. Otherwise sends the reply as a new message.

    Returns the complete reply string.

    Why asyncio.to_thread instead of streaming:
    - Streaming via queue+thread had a deadlock path: if the producer thread
      failed silently, sentinel never arrived and the bot hung on every message.
    - asyncio.to_thread is a single awaitable with no queue, no sentinel, no
      race condition. If it raises, the exception propagates normally.
    - The UX tradeoff: user sees "…" for the full wait, then complete text appears.
      This is better than silence for 15-25s, and infinitely better than a hung bot.
    """
    # Run the blocking DeepSeek call in a thread pool (non-blocking to event loop)
    try:
        reply = await asyncio.to_thread(
            call_deepseek, text, user_context,
            session_history,  # passed as positional — matches session_messages param
        )
    except Exception as ds_err:
        logger.error("DEEPSEEK | call failed: %s", ds_err)
        # Edit placeholder to error message so senior isn't left with "…"
        error_msg = "Kuch hua — main thodi der mein wapas aata hoon. 🙏"
        if placeholder_msg:
            try:
                await placeholder_msg.edit_text(error_msg)
            except Exception:
                await update.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return ""

    # Deliver the reply — edit placeholder if we have one, else send fresh
    if placeholder_msg:
        try:
            await placeholder_msg.edit_text(reply)
        except Exception:
            # Edit can fail if reply text is identical to placeholder text (rare)
            await update.message.reply_text(reply)
    else:
        await update.message.reply_text(reply)

    return reply


# ---------------------------------------------------------------------------
# Bare-code auto-detect (Bug 3 fix, 20 Apr 2026)
#
# A family member may paste a 6-char linking code without the /join prefix.
# Previously this dropped them into the onboarding flow as if they were a
# new senior. Now:
#   - If a valid family_linking_code is sent, show a confirmation question
#     ("Is this Rishi's Saathi? yes/no") before registering them as family.
#   - A 'yes' reply registers via complete_join_for_senior. A 'no' cancels.
#   - TTL: 10 minutes — an unanswered confirmation expires automatically.
#   - The confirmation guards against typo/misread-code collisions so family
#     doesn't accidentally join a stranger's Saathi.
# ---------------------------------------------------------------------------

_BARE_CODE_RE = re.compile(r"^[A-Z0-9]{6}$")
_JOIN_AFFIRM_RE = re.compile(
    r"^(yes|yeah|yep|yup|y|sure|okay|ok|haan|ha|han|hanji|haanji|ji|"
    r"confirm|correct|right|bilkul)[\s\.\!\?]*$",
    re.IGNORECASE,
)
_JOIN_DECLINE_RE = re.compile(
    r"^(no|nope|nah|n|cancel|nahi|galat|wrong|not)[\s\.\!\?]*$",
    re.IGNORECASE,
)
_PENDING_JOIN_TTL_SEC = 600  # 10 minutes


def _pending_join_is_fresh(asked_at_str: str) -> bool:
    """Return True if the pending join confirmation is still within TTL."""
    if not asked_at_str:
        return False
    try:
        asked_at = datetime.fromisoformat(asked_at_str)
        if asked_at.tzinfo is None:
            asked_at = asked_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - asked_at).total_seconds()
        return age <= _PENDING_JOIN_TTL_SEC
    except Exception:
        return False


async def _handle_bare_code_flow(user_id: int, text: str, user_row, update: Update) -> bool:
    """
    Handle bare (no-slash) family linking codes.

    Returns True if the message was consumed (caller should return from pipeline).
    Returns False if this message isn't a code-flow message — normal onboarding
    should continue.
    """
    from database import update_user_fields as _uuf

    stripped = (text or "").strip()

    # Read pending fields safely — they may not exist on very old rows
    try:
        pending_senior_id = user_row["pending_join_senior_id"] if "pending_join_senior_id" in user_row.keys() else None
    except Exception:
        pending_senior_id = None
    try:
        pending_asked_at = user_row["pending_join_asked_at"] if "pending_join_asked_at" in user_row.keys() else None
    except Exception:
        pending_asked_at = None

    # --- Branch A: there's a pending confirmation, resolve it ---
    if pending_senior_id:
        # Expired pending — clear it silently and fall through to Branch B
        if not _pending_join_is_fresh(pending_asked_at):
            _uuf(user_id, pending_join_senior_id=None, pending_join_asked_at=None)
            _invalidate_user_cache(user_id)
            logger.info("JOIN | pending expired | user_id=%s", user_id)
            # Fall through to Branch B below (maybe this message is a new code)
        else:
            # Pending is fresh — interpret as yes/no/other
            # Re-resolve senior name from DB (don't trust a stale cache)
            try:
                from database import get_or_create_user as _get_senior
                senior_row = await asyncio.to_thread(_get_senior, pending_senior_id)
                senior_name = (senior_row["name"] if senior_row else None) or "your family member"
            except Exception:
                senior_name = "your family member"

            if _JOIN_AFFIRM_RE.match(stripped):
                # Register as family
                success, welcome_msg = await asyncio.to_thread(
                    complete_join_for_senior, pending_senior_id, user_id,
                )
                # Clear pending state
                _uuf(user_id, pending_join_senior_id=None, pending_join_asked_at=None)
                _invalidate_user_cache(user_id)
                # Invalidate family cache so the next message from this user
                # is recognised as a family member
                _FAMILY_CACHE.pop(user_id, None)
                try:
                    await update.message.reply_text(welcome_msg, parse_mode="Markdown")
                except Exception:
                    await update.message.reply_text(welcome_msg)
                logger.info(
                    "JOIN | confirmed via bare code | user_id=%s | senior_id=%s",
                    user_id, pending_senior_id,
                )
                return True

            if _JOIN_DECLINE_RE.match(stripped):
                # Cancel pending
                _uuf(user_id, pending_join_senior_id=None, pending_join_asked_at=None)
                _invalidate_user_cache(user_id)
                await update.message.reply_text(
                    "No problem — I've cancelled that. "
                    "If you have a different code, feel free to send it. 🙏"
                )
                logger.info(
                    "JOIN | declined via bare code | user_id=%s | senior_id=%s",
                    user_id, pending_senior_id,
                )
                return True

            # Neither yes nor no — re-prompt once, don't consume the message
            # as code flow unless it's a clean new 6-char code
            new_code_candidate = stripped.upper()
            if not _BARE_CODE_RE.match(new_code_candidate):
                await update.message.reply_text(
                    f"Just to confirm — is this *{senior_name}*'s Saathi?\n\n"
                    f"Please reply *yes* to connect, or *no* to cancel.",
                    parse_mode="Markdown",
                )
                return True
            # Else: fall through to Branch B to treat as a fresh code

    # --- Branch B: is this a fresh code? ---
    candidate = stripped.upper()
    if not _BARE_CODE_RE.match(candidate):
        return False

    senior = await asyncio.to_thread(lookup_senior_by_code, candidate)
    if not senior:
        # Looks like a code shape but doesn't match — don't consume; let
        # onboarding handle it (e.g., a senior typing their own name in caps
        # by coincidence won't be blocked by this).
        return False

    # Valid code — store pending confirmation and ask
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        _uuf(
            user_id,
            pending_join_senior_id=senior["senior_user_id"],
            pending_join_asked_at=now_iso,
        )
        _invalidate_user_cache(user_id)
    except Exception as e:
        logger.error("JOIN | failed to store pending | user_id=%s | err=%s", user_id, e)
        return False

    senior_name = senior["senior_name"]
    await update.message.reply_text(
        f"This code will connect you to *{senior_name}*'s Saathi.\n\n"
        f"Is that correct? Reply *yes* to connect, or *no* to cancel.",
        parse_mode="Markdown",
    )
    logger.info(
        "JOIN | pending created | user_id=%s | senior_id=%s",
        user_id, senior["senior_user_id"],
    )
    return True


# ---------------------------------------------------------------------------
# Shared message pipeline — Protocol 1 → Protocol 3 → DeepSeek
# Called by both handle_text and receive_voice after text is available.
# ---------------------------------------------------------------------------

async def _run_pipeline(
    user_id: int,
    text: str,
    user_row,
    update: Update,
    input_type: str = "text",
    context: ContextTypes.DEFAULT_TYPE = None,
) -> None:
    """
    Run the full message pipeline for a single user turn.

    input_type is "text" or "voice" — used for message logging only.
    """
    # Retrieve live session history from memory — instant, no DB I/O.
    # The inbound message save happens AFTER the placeholder is sent (below),
    # so the user sees "…" immediately instead of waiting 10-12s for a Turso sync.
    _session_history = _live_session_get(user_id, text)

    # --- End-of-life: death notification from a registered family member ---
    # Only check messages from registered family contacts — prevents abuse.
    # Uses the in-memory cache: first call is asyncio.to_thread (non-blocking);
    # subsequent calls return in <1ms. Eliminates the 5–30s sync Turso block.
    senior_id_for_family = await _senior_for_family_cached(user_id)
    if senior_id_for_family is not None:
        from database import get_or_create_user as _get_senior
        senior_row = _get_senior(senior_id_for_family)
        senior_status = senior_row["account_status"] if senior_row else "active"

        if senior_status == "active" and is_death_notification(text):
            # Mark senior deceased, silence all proactive messages
            eulogy_offer = handle_death_notification(senior_id_for_family, user_id)
            if eulogy_offer:
                await update.message.reply_text(eulogy_offer)
                logger.info(
                    "EOL | death notification received | senior_id=%s | notifier=%s",
                    senior_id_for_family, user_id,
                )
            return

        if senior_status == "deceased":
            eulogy_delivered = senior_row["eulogy_delivered"] if senior_row else 1
            if not eulogy_delivered and is_eulogy_yes(text):
                # Family said yes to eulogy — generate and send
                try:
                    prompt = build_eulogy_prompt(senior_id_for_family)
                    if prompt:
                        eulogy_text = call_deepseek(prompt, {"language": "english"})
                        await update.message.reply_text(eulogy_text)
                        from database import update_user_fields
                        update_user_fields(senior_id_for_family, eulogy_delivered=1)
                        logger.info(
                            "EOL | eulogy delivered | senior_id=%s", senior_id_for_family
                        )
                except Exception as eol_err:
                    logger.error("EOL | eulogy generation failed: %s", eol_err)
                    await update.message.reply_text(
                        "I am so sorry — something went wrong and I wasn't able to send this right now. "
                        "Please try again in a little while."
                    )
            return  # Family messages beyond this point are not processed normally

        # --- Family bridge relay (active senior, registered family member) ---
        # Relay the message warmly to the senior.  One-way: family → senior.
        if senior_status == "active":
            sent = await relay_message_to_senior(
                senior_id_for_family, user_id, text, context.bot,
            )
            if sent:
                senior_name = senior_row["name"] if senior_row else "your family member"
                senior_lang = senior_row["language"] if senior_row else "hindi"
                confirm = build_relay_confirmation(senior_name, senior_lang)
                await update.message.reply_text(confirm, parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    "Something went wrong delivering your message. Please try again. 🙏"
                )
            return

    # --- Full policy request ---
    if text.strip().lower() in ("full policy", "full policy.", "puri policy"):
        await update.message.reply_text(USER_POLICY_DOCUMENT)
        logger.info("OUT | user_id=%s | type=policy_full", user_id)
        return

    # Track first message of the day for adaptive ritual scheduling.
    # NOTE: This is queued to the background writer — NOT called synchronously here.
    # record_first_message() calls get_connection() + commit() which triggers a
    # Turso sync (~5s) even for INSERT OR IGNORE no-ops. Running it synchronously
    # before the placeholder caused a 5s freeze on every single message.
    # The actual DB write is queued below, after the placeholder is sent.

    # --- Medicine reminder acknowledgement ---
    # Checked before anything else so 👍 is never routed to DeepSeek.
    # mark_reminder_acknowledged only matches if there is a reminder sent
    # in the last 2 hours that is still unacknowledged.
    if user_row["onboarding_complete"] and is_acknowledgement(text):
        if mark_reminder_acknowledged(user_id):
            _ack_lang = (user_row["language"] or "english").lower()
            if _ack_lang == "hindi":
                ack_reply = (
                    "Shukriya! Dawai le li — bahut achha kiya. "
                    "Apna khayal rakhein. 🙏"
                )
            elif _ack_lang == "hinglish":
                ack_reply = (
                    "Thank you! Dawai le li — that's great. "
                    "Apna khayal rakhein. 🙏"
                )
            else:
                ack_reply = (
                    "Thank you — glad you've taken it. "
                    "Take care. 🙏"
                )
            await update.message.reply_text(ack_reply)
            # Also save to session history so DeepSeek sees the right-language
            # context on the next turn. Without this, the next short reply
            # (e.g. "ok") gets routed to short-reply disengagement with a
            # mismatched previous-turn language in the history.
            try:
                save_session_turn(user_id, "user", text)
                save_session_turn(user_id, "assistant", ack_reply)
            except Exception:
                pass
            logger.info("OUT | user_id=%s | type=reminder_ack | lang=%s", user_id, _ack_lang)
            return

    # --- Pending family-term capture (child-led /familycode lazy-ask, 22 Apr 2026) ---
    # If /familycode was just invoked on a child-led account with no stored
    # family_term, we asked "what do you call <senior>?". The very next
    # message (within 10 min) is treated as that answer: saved to DB,
    # invite block built, reply sent. No fall-through — the message is
    # consumed entirely and does NOT reach DeepSeek (the answer is a term
    # like "Papa", not a conversational input).
    if user_row["onboarding_complete"] and _pending_term_is_fresh(user_id):
        raw_term = (text or "").strip()
        # Strip trailing punctuation/emoji and cap length — defensive only.
        raw_term = re.sub(r"[\.\!\?\n]+$", "", raw_term).strip()
        # Title-case so "ma" / "papa" / "dadi" render capitalized in the
        # forward message. Single-word inputs already correctly cased
        # ("Rameshji") stay unchanged; multi-word ("durga ji") → "Durga Ji".
        raw_term = raw_term.title()
        if not raw_term or len(raw_term) > 40:
            # Unusable answer → clear pending, let message flow through normally
            # so the senior isn't stuck in a loop if they typed something else.
            _PENDING_FAMILY_TERM_ASK.pop(user_id, None)
            logger.warning(
                "FAMILYCODE_TERM | unusable answer | user_id=%s | len=%d",
                user_id, len(raw_term),
            )
        else:
            try:
                from database import update_user_fields as _uuf_term
                _uuf_term(user_id, family_term=raw_term)
                _invalidate_user_cache(user_id)
                _PENDING_FAMILY_TERM_ASK.pop(user_id, None)
                # Re-fetch code (idempotent — returns the already-stored one)
                code = get_or_create_linking_code(user_id)
                from family import build_family_invite_block_third_person
                invite_block = build_family_invite_block_third_person(
                    family_term=raw_term, code=code
                )
                reply = (
                    f"Got it — I'll refer to them as *{raw_term}* in the "
                    f"message.\n\n"
                    f"{_build_familycode_reply(code, invite_block)}"
                )
                await update.message.reply_text(reply, parse_mode="Markdown")
                logger.info(
                    "OUT | user_id=%s | type=familycode_term_saved | term=%s | code=%s",
                    user_id, raw_term, code,
                )
                return
            except Exception as _term_err:
                _PENDING_FAMILY_TERM_ASK.pop(user_id, None)
                logger.error(
                    "FAMILYCODE_TERM | save failed | user_id=%s | err=%s",
                    user_id, _term_err,
                )
                await update.message.reply_text(
                    "Something went wrong saving that. Please type /familycode again. 🙏"
                )
                return

    # --- Memory question response capture ---
    # If this user was sent a memory question and has not yet responded, capture
    # their next message as the response. Save it to the memories table with full
    # metadata (question_id, question_text, theme) so the memoir is properly linked.
    # This runs after reminder ack (so 👍 doesn't accidentally close a question)
    # but before onboarding gate and all other pipeline stages.
    if user_row["onboarding_complete"]:
        from memory_questions import get_pending_memory_question, save_memory_response
        _pending_qid, _pending_qtext, _pending_qtheme = get_pending_memory_question(user_row)
        if _pending_qid is not None:
            save_memory_response(
                user_id,
                response_text=text,
                question_id=_pending_qid,
                question_text=_pending_qtext,
                theme=_pending_qtheme,
            )
            logger.info(
                "MEMORY_Q | response captured | user_id=%s | question_id=%s | theme=%s",
                user_id, _pending_qid, _pending_qtheme,
            )
            # Do NOT return — let the message continue through the full pipeline
            # so DeepSeek can respond warmly to what the senior just shared.

    # --- Pending-input capture RESPONSE (Batch 2, 22 Apr 2026) ---
    # If we offered grandkids/medicines capture on the previous turn,
    # awaiting_pending_capture will be set. This inbound message is either
    # the data itself or a refusal. Handle it before everything else — it's
    # data, not conversational input, and must not reach DeepSeek.
    #
    # capture_response() handles:
    #   • refusal ("no", "later", "baad mein") → clears awaiting flag, keeps pending flag
    #   • success → writes to family_members (grandkids) or medicines_raw + seeds reminders,
    #     clears both pending_<kind> and awaiting_pending_capture
    #   • parse failure → keeps flags, returns "please list names separated by commas"
    if user_row["onboarding_complete"]:
        _awaiting = (
            user_row["awaiting_pending_capture"]
            if "awaiting_pending_capture" in user_row.keys() else None
        )
        if _awaiting in ("grandkids", "medicines", "medicines_clarify"):
            from pending_capture import capture_response
            try:
                captured, ack = capture_response(user_id, _awaiting, text)
            except Exception as pc_err:
                logger.error(
                    "PENDING_CAPTURE | user_id=%s | capture_response raised: %s",
                    user_id, pc_err,
                )
                # Unstick the senior — clear awaiting flag so future turns aren't
                # routed here. The underlying pending_<kind> flag stays set so the
                # keyword trigger can try again.
                try:
                    from database import update_user_fields as _uuf_pc_err
                    _uuf_pc_err(user_id, awaiting_pending_capture=None)
                    _invalidate_user_cache(user_id)
                except Exception:
                    pass
                captured = False
                ack = "Sorry — something went wrong. Please try again in a bit. 🙏"
            _invalidate_user_cache(user_id)
            await update.message.reply_text(ack)
            # Write inbound message + response to session history and DB.
            _live_session_append(user_id, "user", text)
            _live_session_append(user_id, "assistant", ack)
            _db_queue(save_message_record, user_id, "in", text, input_type)
            _db_queue(save_message_record, user_id, "out", ack)
            _db_queue(save_session_turn, user_id, "user", text)
            _db_queue(save_session_turn, user_id, "assistant", ack)
            logger.info(
                "PENDING_CAPTURE | user_id=%s | response handled | kind=%s | captured=%s",
                user_id, _awaiting, captured,
            )
            return

    # --- Bare-code auto-detect (Bug 3 fix, 20 Apr 2026) ---
    # A brand-new user may paste a family linking code without /join. If we
    # have a pending confirmation from a previous turn, resolve it here.
    # Otherwise, if this message looks like a code and matches a real
    # family_linking_code in DB, ask for confirmation before registering.
    # Both branches run BEFORE the onboarding gate so codes never get routed
    # into the senior onboarding flow.
    if not user_row["onboarding_complete"]:
        _consumed = await _handle_bare_code_flow(user_id, text, user_row, update)
        if _consumed:
            return

    # --- Self-setup deferred bridge: check if it's a new day ---
    # Fires even when onboarding_complete=1, because 'later' marks the user
    # complete-for-today but we want to re-ask Day 2 questions the next day.
    _setup_mode_row = user_row["setup_mode"] if "setup_mode" in user_row.keys() else None
    _bridge_state_row = (
        user_row["self_setup_bridge_state"]
        if "self_setup_bridge_state" in user_row.keys() else None
    )
    _deferred_date_row = (
        user_row["self_setup_deferred_date"]
        if "self_setup_deferred_date" in user_row.keys() else None
    )
    if _setup_mode_row == "self" and _bridge_state_row == "deferred":
        recheck = maybe_resume_day2_bridge(user_id, _deferred_date_row)
        if recheck is not None:
            _invalidate_user_cache(user_id)
            await update.message.reply_text(recheck, parse_mode="Markdown")
            logger.info("OUT | user_id=%s | type=bridge_recheck", user_id)
            return

    # --- Onboarding gate ---
    if not user_row["onboarding_complete"]:
        setup_mode = user_row["setup_mode"] if "setup_mode" in user_row.keys() else None

        if setup_mode is None:
            # First contact — we haven't asked the opening question yet.
            # Send it now and set state to 'pending' so the NEXT message is parsed.
            # Never try to detect mode from an unsolicited first message.
            from database import update_user_fields as _uuf
            _uuf(user_id, setup_mode="pending")
            _invalidate_user_cache(user_id)
            await update.message.reply_text(
                get_opening_detection_question(), parse_mode="Markdown"
            )
            logger.info("OUT | user_id=%s | type=opening_detection_question", user_id)
            return

        if setup_mode == "pending":
            # User is replying to the opening detection question — parse their answer.
            mode, next_msg = handle_mode_detection(user_id, text)
            _invalidate_user_cache(user_id)
            await update.message.reply_text(next_msg, parse_mode="Markdown")
            logger.info("OUT | user_id=%s | type=mode_detection | mode=%s", user_id, mode)
            return

        # --- Self-setup bridge: waiting for now/later answer ---
        bridge_state = (
            user_row["self_setup_bridge_state"]
            if "self_setup_bridge_state" in user_row.keys() else None
        )
        if setup_mode == "self" and bridge_state == "asked":
            reply = handle_bridge_answer(user_id, text)
            _invalidate_user_cache(user_id)
            await update.message.reply_text(reply, parse_mode="Markdown")
            logger.info("OUT | user_id=%s | type=bridge_answer", user_id)
            return

        reply = handle_onboarding_answer(user_id, user_row["onboarding_step"], text)
        await update.message.reply_text(reply, parse_mode="Markdown")
        logger.info(
            "OUT | user_id=%s | type=onboarding | step=%d",
            user_id, user_row["onboarding_step"],
        )
        # User row changed (onboarding_step advanced) — drop cached snapshot.
        _invalidate_user_cache(user_id)
        return

    # --- Child-led handoff: soft first-contact only (Batch 3, 23 Apr 2026) ---
    # In child-led setup (setup_mode='family'), preferred_salutation and bot_name
    # are already collected during the child's 21-question onboarding — step 2
    # (preferred address) and step 16 (bot name). The old 4-step handoff state
    # machine re-asked both, which:
    #   Bug 1 — advanced unconditionally on ANY senior message, so a real
    #           question ("my grand kids came today") as the first reply got
    #           ignored in favour of sending the next handoff question.
    #   Bug 2 — wrote the raw reply to preferred_salutation with no filtering,
    #           so "Ma is good" or "nothing" became the stored address.
    #   Bug 3 — re-asked "what would you like to call me?" even though the
    #           child had already picked a bot name.
    #   Bug 4 — "nothing" / dismissive replies corrupted both fields.
    #
    # New design: send the soft first-contact greeting once, mark handoff
    # complete, and drop straight into normal conversation. If the senior
    # wants to change their address or bot name, they can say so in ordinary
    # conversation — DeepSeek handles it warmly, no state machine needed.
    #
    # In-flight users already at handoff_step 1/2/3 under the old code get the
    # same treatment — they receive the soft greeting once (possibly a second
    # time) and then enter normal conversation. Any partially-set junk values
    # in preferred_salutation / bot_name from the old flow are left alone;
    # either the child already typed over them at onboarding, or the senior
    # can correct in normal chat.
    setup_mode = user_row["setup_mode"] if "setup_mode" in user_row.keys() else None
    handoff_step = user_row["handoff_step"] if "handoff_step" in user_row.keys() else 4

    if setup_mode == "family" and handoff_step is not None and handoff_step < 4:
        child_name = get_setup_child_name(user_id)
        bot_name_for_handoff = (
            user_row["bot_name"] if "bot_name" in user_row.keys() else "Saathi"
        )
        from database import update_user_fields

        replies = []
        # Confusion check — if the senior's first message reads as confused
        # ("who are you", "ye kya hai", "kaun ho"), send the warm confusion
        # response BEFORE the soft greeting. Only meaningful on the very
        # first turn (handoff_step == 0).
        if handoff_step == 0 and is_confused_senior(text):
            replies.append(get_confusion_response(child_name))
            logger.info("OUT | user_id=%s | type=confusion_branch", user_id)

        # The one and only handoff message: the soft first-contact greeting.
        replies.append(
            get_handoff_message(0, child_name, bot_name=bot_name_for_handoff)
        )

        # Mark handoff complete immediately — no more 1/2/3 progression.
        update_user_fields(user_id, handoff_step=4)
        _invalidate_user_cache(user_id)
        logger.info(
            "OUT | user_id=%s | type=handoff | collapsed_to_step4 | prior_step=%s",
            user_id, handoff_step,
        )

        for r in replies:
            await update.message.reply_text(r)
            save_message_record(user_id, "out", r)

        # Persist the senior's opening utterance even though we short-circuit
        # here. Without this, a meaningful first message ("my grand kids came
        # today", "beta aaj dawai nahi li") would be dropped from messages +
        # session history, and turn 3 references to it would have no memory.
        # Fix A (23 Apr 2026) — see Batch 3a live-test critique.
        _db_queue(save_message_record, user_id, "in", text, input_type)
        _live_session_append(user_id, "user", text)
        for r in replies:
            _live_session_append(user_id, "assistant", r)
            _db_queue(save_session_turn, user_id, "assistant", r)
        _db_queue(save_session_turn, user_id, "user", text)
        return

    # Build context dict from user profile.
    days_since_first = user_row["days_since_first_message"] or 1
    archetype_adjustment = _get_archetype_adjustment(user_id, days_since_first)
    # If archetype not yet cached, fire background detection for the NEXT turn.
    # Non-blocking: never awaited here, runs while DeepSeek is processing.
    if archetype_adjustment is None and days_since_first <= 7 and user_id not in _archetype_cache:
        asyncio.create_task(_detect_archetype_background(user_id))

    _local_hour = get_user_local_hour(dict(user_row))
    _time_label = get_time_of_day_label(_local_hour)

    # Fetch the setup person (adult child who ran onboarding). Used by
    # deepseek._build_system_prompt to substitute {SETUP_NAME} into the
    # IDENTITY and FAMILY REFERENCES sections — prevents "Priya" leaks.
    # Returns None for self-setup flow or onboarding-incomplete users.
    _setup_person = get_setup_person(user_id)
    _setup_name = (_setup_person or {}).get("name") if _setup_person else None

    # Full family roster — inject structured list into the FAMILY block of the
    # system prompt. Prevents DeepSeek from fabricating children/grandchildren
    # names when the senior asks about them (the "Rahul and Anjali"
    # hallucination seen in the 22 Apr live chatlog).
    _family_members = get_family_members(user_id)

    # preferred_salutation may be on user_row (if set by senior at handoff
    # step 2 OR by child at onboarding step 2). Used by the FAMILY block so
    # DeepSeek knows Durga is addressed as "Ma".
    try:
        _preferred_salutation = user_row["preferred_salutation"]
    except (IndexError, KeyError):
        _preferred_salutation = None

    user_context = {
        "user_id":                user_id,
        "name":                   user_row["name"],
        "bot_name":               user_row["bot_name"],
        "persona":                user_row["persona"],
        "language":               user_row["language"],
        "city":                   user_row["city"],
        "spouse_name":            user_row["spouse_name"],
        "religion":               user_row["religion"],
        "health_sensitivities":   user_row["health_sensitivities"],
        "music_preferences":      user_row["music_preferences"],
        "favourite_topics":       user_row["favourite_topics"],
        "family_members":         _family_members,
        "setup_name":             _setup_name,
        "preferred_salutation":   _preferred_salutation,
        "archetype_adjustment":   archetype_adjustment,
        "local_hour":             _local_hour,
        "local_time_label":       _time_label,
    }

    # --- Script-based language detection + learning loop ---
    # Runs after user_context is built. Detects the actual language of the current
    # message and adjusts user_context["language"] for this call.
    # If 5+ consecutive messages differ from the stored preference, the DB is updated.
    #
    # SHORT MESSAGE GUARD: Messages ≤3 words are unreliable language signals.
    # A single Hindi word ("Haan"), a voice note that transcribes to one word,
    # or a short English ack ("okay") should never override the stored preference.
    _stored_lang = user_context.get("language") or "english"
    _short_message = len(text.strip().split()) <= 3
    if _short_message:
        # Trust stored preference — don't learn from this, don't override for this call.
        _effective_lang = _stored_lang
        logger.debug("LANG | user_id=%s | short message, using stored=%s", user_id, _stored_lang)
    else:
        _detected_lang = _detect_message_language(text)
        _effective_lang = _update_language_learning(user_id, _stored_lang, _detected_lang)
        if _effective_lang != _stored_lang:
            logger.info(
                "LANG | user_id=%s | override: stored=%s detected=%s effective=%s",
                user_id, _stored_lang, _detected_lang, _effective_lang,
            )
    user_context["language"] = _effective_lang

    # --- Meta-request: language switch ---
    # Must run before all protocols and DeepSeek.
    _LANGUAGE_SWITCH_TO_ENGLISH = [
        "in english", "in english please",
        "speak english", "english please",
        "reply in english", "respond in english",
    ]
    _LANGUAGE_SWITCH_TO_HINDI = [
        "hindi mein", "hindi mein baat karo",
        "hindi please", "hindi mein boliye",
        "in hindi", "in hindi please",
    ]

    msg_lower = text.lower().strip()

    if any(p in msg_lower for p in _LANGUAGE_SWITCH_TO_ENGLISH):
        from database import update_user_fields as _uuf_lang
        _uuf_lang(user_id, language="english")
        _invalidate_user_cache(user_id)
        _lang_reply = "Of course."
        await update.message.reply_text(_lang_reply)
        save_message_record(user_id, "out", _lang_reply)
        save_session_turn(user_id, "user", text)
        save_session_turn(user_id, "assistant", _lang_reply)
        logger.info("OUT | user_id=%s | type=language_switch | lang=english", user_id)
        return

    if any(p in msg_lower for p in _LANGUAGE_SWITCH_TO_HINDI):
        from database import update_user_fields as _uuf_lang
        _uuf_lang(user_id, language="hindi")
        _invalidate_user_cache(user_id)
        _lang_reply = "Bilkul."
        await update.message.reply_text(_lang_reply)
        save_message_record(user_id, "out", _lang_reply)
        save_session_turn(user_id, "user", text)
        save_session_turn(user_id, "assistant", _lang_reply)
        logger.info("OUT | user_id=%s | type=language_switch | lang=hindi", user_id)
        return

    # --- Greeting handler ---
    # User-initiated greetings get a time-aware response, not proactive check-in language.
    _GREETING_TRIGGERS = [
        "hello", "hi", "hey", "good morning", "good afternoon",
        "good evening", "good night", "namaste", "namaskar",
        "haan", "haan haan", "jai shri krishna", "sat sri akal",
        "salam", "adaab", "hola",
    ]

    def _get_time_aware_greeting(
        hour: int,
        language: str,
        name: str,
        salutation: str = "",
    ) -> str:
        _name = (name or "").strip()
        _sal = (salutation or "").strip()
        _lang = (language or "english").lower()

        # Address (Batch 1c semantics — mirrors rituals._address / safety.py):
        #   • preferred_salutation verbatim if set ("Ma", "Rameshji", "Dadi")
        #   • else "{name} Ji" — respectful Indian default ("Durga Ji")
        #   • else empty
        # Bug repro before fix: salutation="Ma", name="Durga" rendered
        # "Good morning, Durga ji" (ignored salutation, used bare name + ji).
        if _sal:
            _addr = _sal
        elif _name:
            _addr = f"{_name} Ji"
        else:
            _addr = ""

        _hi_suffix = f" {_addr}" if _addr else ""
        _eng_suffix = f", {_addr}" if _addr else ""

        if _lang in ("hindi", "hinglish"):
            if 5 <= hour < 12:
                return f"Namaste{_hi_suffix}. Sunkar achha laga. 🙏"
            elif 12 <= hour < 17:
                return f"Namaste{_hi_suffix}. Dopahar kaisi chal rahi hai?"
            elif 17 <= hour < 21:
                return f"Namaste{_hi_suffix}. Aaj ka din kaisa raha?"
            else:
                return f"Namaste{_hi_suffix}. Bahut raat ho gayi — sab theek hai?"
        else:
            if 5 <= hour < 12:
                return f"Good morning{_eng_suffix}. Good to hear from you."
            elif 12 <= hour < 17:
                return f"Good afternoon{_eng_suffix}."
            elif 17 <= hour < 21:
                return f"Good evening{_eng_suffix}. How has the day been?"
            else:
                return f"Hello{_eng_suffix}. Up late tonight?"

    # Only fire the greeting handler if we are NOT mid-session.
    # If there is substantial session history (>= 4 turns), the senior is returning
    # mid-conversation — let the message fall through to the mid-session intercept below.
    _is_fresh_greeting = (
        (msg_lower in _GREETING_TRIGGERS or any(msg_lower.startswith(g) for g in _GREETING_TRIGGERS))
        and len(_session_history) < 4
    )
    if _is_fresh_greeting:
        _greet_reply = _get_time_aware_greeting(
            _local_hour,
            language=user_context.get("language") or "english",
            name=user_context.get("name") or "",
            salutation=user_context.get("preferred_salutation") or "",
        )
        await update.message.reply_text(_greet_reply)
        save_message_record(user_id, "out", _greet_reply)
        save_session_turn(user_id, "user", text)
        save_session_turn(user_id, "assistant", _greet_reply)
        logger.info("OUT | user_id=%s | type=greeting | hour=%d", user_id, _local_hour)
        return

    # --- Emergency keyword check (runs BEFORE Protocol 1) ---
    # Detects PHYSICAL safety signals only ("I fell", "bachao", chest pain, etc.).
    # Mental health / suicidal-ideation phrases go exclusively to Protocol 1.
    # send_help_prompt() sends an immediate text with 112 before any button press.
    # We also attempt a family alert here if the user has consented.
    if check_emergency_keywords(text):
        await send_help_prompt(update)
        # Attempt family alert immediately — don't wait for button press.
        # Only fires if escalation_opted_in = 1 AND contacts have telegram_user_id.
        try:
            from safety import alert_emergency_contacts
            await alert_emergency_contacts(context.bot, user_id, user_row)
        except Exception as _emg_err:
            logger.error("SAFETY | emergency alert failed | user_id=%s | %s", user_id, _emg_err)
        logger.info("OUT | user_id=%s | type=emergency_prompt", user_id)
        return

    # --- Protocol 1 check (runs BEFORE DeepSeek) ---
    session_count = _protocol1_session_counts.get(user_id, 0)
    protocol1_reply, is_escalation = check_protocol1(user_id, text, session_count)

    if is_escalation:
        # Imminent-risk signal. Attempt family alert if user has consented.
        # Build an honest response based on whether the alert actually went out.
        _protocol1_session_counts[user_id] = session_count + 1
        from safety import alert_emergency_contacts
        from protocol1 import _ESCALATION_RESPONSE_ALERT_SENT, _ESCALATION_RESPONSE_NO_ALERT
        _alert_sent = 0
        try:
            _alert_sent = await alert_emergency_contacts(context.bot, user_id, user_row)
        except Exception as _ae:
            logger.error("PROTOCOL1 | family alert failed | user_id=%s | %s", user_id, _ae)
        # Use the honest response variant — never claim alert sent if it wasn't
        _p1_escalation_text = (
            _ESCALATION_RESPONSE_ALERT_SENT if _alert_sent > 0
            else _ESCALATION_RESPONSE_NO_ALERT
        )
        await update.message.reply_text(_p1_escalation_text)
        logger.warning(
            "OUT | user_id=%s | type=protocol1_escalation | alert_sent=%d",
            user_id, _alert_sent,
        )
        return

    if protocol1_reply:
        _protocol1_session_counts[user_id] = session_count + 1
        await update.message.reply_text(protocol1_reply)
        logger.info(
            "OUT | user_id=%s | type=protocol1 | stage=%d",
            user_id, session_count + 1,
        )
        return

    # --- Protocol 4 check (runs AFTER Protocol 1, BEFORE Protocol 3) ---
    # Handles romantic or sexual signals with a gentle, non-shaming boundary.
    _p4_language = user_row["language"] or "english"
    protocol4_reply = check_protocol4(user_id, text, language=_p4_language)
    if protocol4_reply:
        await update.message.reply_text(protocol4_reply)
        save_message_record(user_id, "out", protocol4_reply)
        save_session_turn(user_id, "user", text)
        save_session_turn(user_id, "assistant", protocol4_reply)
        logger.info("OUT | user_id=%s | type=protocol4", user_id)
        return

    # --- Protocol 3 check (runs BEFORE DeepSeek, AFTER Protocol 1) ---
    #
    # Session expiry: clear protocol3_active if >60 min since last P3 trigger.
    # This resets the guard at the start of a new conversation session.
    _p3_active = user_row["protocol3_active"] if "protocol3_active" in user_row.keys() else 0
    _p3_triggered_at = user_row["protocol3_triggered_at"] if "protocol3_triggered_at" in user_row.keys() else None
    if _p3_active and _p3_triggered_at:
        try:
            from datetime import datetime, timezone, timedelta
            triggered = datetime.fromisoformat(_p3_triggered_at)
            if triggered.tzinfo is None:
                triggered = triggered.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - triggered > timedelta(minutes=60):
                from database import update_user_fields as _uuf_p3
                _uuf_p3(user_id, protocol3_active=0, protocol3_triggered_at=None)
                _invalidate_user_cache(user_id)  # drop stale main-cache row
                _p3_active = 0
        except Exception:
            pass  # on parse error, leave flag as-is

    # Inject P3 state into user_context so DeepSeek knows a financial topic
    # was raised and must not give financial advice on follow-up messages.
    user_context["protocol3_active"] = _p3_active

    user_language = user_row["language"] or "english"

    if not _p3_active:
        # Only run keyword detection when P3 hasn't already fired this session
        protocol3_reply = check_protocol3(user_id, text, language=user_language)
        if protocol3_reply:
            await update.message.reply_text(protocol3_reply)
            # Save both sides so DeepSeek has full context on the next message
            save_message_record(user_id, "out", protocol3_reply)
            save_session_turn(user_id, "user", text)
            save_session_turn(user_id, "assistant", protocol3_reply)
            # Mark P3 active — prevents re-fire loop on follow-up messages
            from database import update_user_fields as _uuf_p3
            from datetime import datetime, timezone
            _uuf_p3(
                user_id,
                protocol3_active=1,
                protocol3_triggered_at=datetime.now(timezone.utc).isoformat(),
            )
            # Drop stale main-cache row so the next message within the 5-min
            # cache window sees protocol3_active=1 and doesn't re-fire the
            # keyword check on a follow-up financial message.
            _invalidate_user_cache(user_id)
            logger.info("OUT | user_id=%s | type=protocol3", user_id)
            return
        # If no P3 trigger, fall through to DeepSeek normally

    # --- Music request check (runs BEFORE DeepSeek, AFTER protocols) ---
    music_query = detect_music_request(
        text, music_preferences=user_context.get("music_preferences") or ""
    )
    if music_query:
        lang = (user_context.get("language") or "english").lower()
        try:
            title, url = find_music(music_query)
            reply = build_music_message(title, url, language=lang)
            # Plain text (no parse_mode). Telegram auto-linkifies URLs; using
            # Markdown mode breaks on YouTube IDs containing `_` or `*` (which
            # the parser treats as unclosed italic/bold entities → 400 error).
            await update.message.reply_text(reply)
            save_message_record(user_id, "out", reply)
            logger.info("OUT | user_id=%s | type=music | query=%r", user_id, music_query)
        except Exception as music_err:
            logger.warning("MUSIC | user_id=%s | failed: %s", user_id, music_err)
            if lang in ("hindi", "hinglish"):
                err_msg = (
                    "Abhi koi gaana nahi mil raha. "
                    "Thodi der mein dobara try karein! 🙏"
                )
            else:
                err_msg = (
                    "Couldn't find a song just now. "
                    "Please try again in a moment! 🙏"
                )
            try:
                await update.message.reply_text(err_msg)
            except Exception:
                logger.exception("MUSIC | also failed to send error message")
        return

    # --- Send placeholder BEFORE any slow operations ---
    # All fast early-return paths above have already fired. If we're here,
    # we're going to DeepSeek. Send "…" now so the user sees an immediate
    # response indicator while API fetches and DeepSeek streaming happen.
    _placeholder_msg = None
    try:
        _placeholder_msg = await update.message.reply_text("…")
    except Exception:
        pass

    # --- Queue inbound message DB save + first-message tracking (fire-and-forget) ---
    # Both calls do Turso commits. Queueing them here (AFTER the placeholder) means
    # the user always sees "…" in <1s regardless of Turso sync latency.
    _db_queue(save_message_record, user_id, "in", text, input_type)
    _db_queue(record_first_message, user_id)

    # --- Live data injection for news / cricket / weather queries ---
    # The news/cricket/weather APIs are wired into the morning ritual but NOT into
    # ad-hoc conversation. Without this block, "tell me the news" goes to DeepSeek
    # which hallucinates stale content. Here we detect intent, call the APIs, and
    # inject real data (or an honest "I don't have live news") into the DeepSeek
    # system prompt as a context block.
    # NOTE: Now runs in asyncio.to_thread so RSS/API fetches (requests.get calls)
    # do NOT block the event loop. Without this, a 3-feed RSS fetch (3 × 4s timeout)
    # would freeze the event loop for up to 12–16s, preventing subsequent messages
    # from being processed and stalling TTS background tasks.
    _live_data_injected = await asyncio.to_thread(
        _inject_live_data_if_needed, text, user_context
    )
    if _live_data_injected:
        user_context["live_data_context"] = _live_data_injected
        logger.info("LIVE_DATA | user_id=%s | injected: %r", user_id, _live_data_injected[:80])

    # --- Mid-session return greeting intercept ---
    # When the senior returns with a bare greeting mid-session, build a targeted
    # prompt that explicitly includes the recent thread so DeepSeek references it
    # instead of defaulting to "Good evening. How has the day been?"
    _GREETING_WORDS = {
        "hello", "hello again", "hi", "hi again", "i'm back", "im back",
        "back again", "namaste", "haan", "haan ji",
    }
    _is_mid_session_greeting = (
        len(_session_history) >= 4
        and text.strip().lower().rstrip("!. ") in _GREETING_WORDS
    )
    _original_text = text  # preserve for session saving
    if _is_mid_session_greeting:
        _recent = _session_history[-6:]
        _ctx = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Saathi'}: {m['content']}"
            for m in _recent
        )
        text = (
            f"The user just returned mid-session with a greeting ('{_original_text.strip()}').\n\n"
            f"Recent conversation:\n{_ctx}\n\n"
            f"Respond in 1-2 warm sentences. If there is an unfinished story or topic above, "
            f"reference it warmly — e.g. 'Welcome back — you were just about to tell me about Bombay.' "
            f"Do NOT say 'Good evening. How has the day been?' or any generic time-of-day greeting."
        )
        _session_history = []  # History already embedded in prompt — don't double-inject

    # --- Vulnerability pre-processor ---
    # DeepSeek ignores system-prompt language-lock and no-excavation rules during
    # emotional content. Hard override: wrap the message with explicit instructions
    # before it reaches DeepSeek. _original_text is preserved so session history
    # is never polluted with the injected wrapper.
    # Grief signals — heavier disclosures that need more space than one sentence.
    # These get 2-3 sentences of warm presence, not the strict one-sentence limit.
    _GRIEF_SIGNALS = [
        # English
        "passed away", "he passed", "she passed", "they passed",
        "died", "lost my", "lost him", "lost her",
        "widowed", "widow", "widower",
        "miss him so much", "miss her so much", "miss them so much",
        "gone now", "not here anymore", "not with us anymore",
        "years ago he", "years ago she",
        # Hindi / Hinglish
        "woh nahi rahe", "woh nahi rahi", "woh chale gaye", "woh chali gayi",
        "unka intekaal", "unka nidhan", "guzar gaye", "guzar gayi",
        "bahut yaad aata hai", "bahut yaad aati hai",
        "akele ho gaye hain", "akele ho gayi hain",
        "bichhad gaye", "bichhad gayi",
    ]

    # Loneliness / invisibility signals — one-sentence hold, no excavation
    _VULNERABILITY_SIGNALS = [
        # English
        "nobody needs me", "no one needs me", "feel like nobody",
        "feel invisible", "nobody cares", "no one cares",
        "feel useless", "feel alone", "feel lonely", "feel like a burden",
        "nobody listens", "no one listens", "i am a burden", "i'm a burden",
        "nobody wants me", "no one wants me", "don't belong",
        "feel left out", "feel forgotten", "nobody remembers",
        # Hindi / Hinglish
        "koi zaroorat nahi", "koi nahi chahta", "akela feel",
        "akela hoon", "akele hoon", "koi nahi sunata", "koi nahi sunta",
        "kisi ko zaroorat nahi", "bekar lagta", "bekar lag raha",
        "bojh lag raha", "bojh lagta", "koi yaad nahi karta",
    ]

    _text_lower = text.lower()
    _is_grief = any(sig in _text_lower for sig in _GRIEF_SIGNALS)
    _is_vulnerability = (not _is_grief) and any(sig in _text_lower for sig in _VULNERABILITY_SIGNALS)

    if _is_grief:
        _original_text = text
        _lang = (user_context.get("language") or "english").lower()
        _lang_label = {"hindi": "Hindi", "hinglish": "Hinglish"}.get(_lang, "English")
        text = (
            f"[HARD OVERRIDE — apply before anything else:\n"
            f"1. Respond in {_lang_label} only. Do not switch language for any reason.\n"
            f"2. Two to three short sentences of warm presence. No more.\n"
            f"3. Stay with the grief — do not redirect, do not offer silver lining, do not look forward.\n"
            f"4. No questions. Do not ask what happened or how they are feeling now.\n"
            f"5. No therapy phrases ('it sounds like', 'I hear that'). Plain, warm language only.]\n\n"
            f"Senior's message: {_original_text}"
        )
        logger.info("PIPELINE | user_id=%s | grief_pre_processor triggered", user_id)

    elif _is_vulnerability:
        _original_text = text  # ensure original is preserved (may already be set above)
        _lang = (user_context.get("language") or "english").lower()
        _lang_label = {"hindi": "Hindi", "hinglish": "Hinglish"}.get(_lang, "English")
        text = (
            f"[HARD OVERRIDE — apply before anything else:\n"
            f"1. Respond in {_lang_label} only. Do not switch language for any reason.\n"
            f"2. One plain sentence of acknowledgement. Stop there.\n"
            f"3. Ask nothing. Not what happened. Not how long. Not anything.\n"
            f"4. Do not invite the user to say more.\n"
            f"The senior has shared something vulnerable. Hold the space. Do not fill it.]\n\n"
            f"Senior's message: {_original_text}"
        )
        logger.info("PIPELINE | user_id=%s | vulnerability_pre_processor triggered", user_id)

    # --- Identity / confusion intercept ---
    # If the senior asks who Saathi is or seems confused about what they're using,
    # return the hardcoded designed response without going to DeepSeek.
    # This is more reliable than hoping DeepSeek follows the identity rules.
    _IDENTITY_SIGNALS = [
        "who are you", "what are you", "are you a robot", "are you a bot",
        "are you human", "are you real", "is this a machine", "is this ai",
        "kya tum insaan ho", "kya aap insaan ho", "tum kaun ho", "aap kaun ho",
        "yeh kya hai", "kya ho tum", "kya hain aap",
        "samjha nahi", "samjhi nahi", "kya ho raha hai yahan",
        "i don't understand", "i'm confused", "i'm not sure what this is",
    ]
    # Always check the original message, not the wrapped version that grief/vulnerability may have set
    _msg_stripped = _original_text.strip().lower().rstrip("?.!")
    if any(sig in _msg_stripped for sig in _IDENTITY_SIGNALS):
        _id_lang = (user_context.get("language") or "english").lower()
        if _id_lang in ("hindi", "hinglish"):
            _identity_reply = "Bas baat karne ke liye hoon — aur kuch nahi. 🙏"
        else:
            _identity_reply = "Just someone to chat with — that's really all. 🙏"
        await update.message.reply_text(_identity_reply)
        save_message_record(user_id, "out", _identity_reply)
        save_session_turn(user_id, "user", _original_text)
        save_session_turn(user_id, "assistant", _identity_reply)
        logger.info("OUT | user_id=%s | type=identity_intercept", user_id)
        return

    # --- Short-reply disengagement detector ---
    # If the senior sends a very short reply (≤3 words, no question mark, no question word),
    # inject a HARD OVERRIDE so DeepSeek doesn't ask more questions.
    # This catches "Ok", "Hmm", "👍", "Theek hai", "Haan" etc.
    #
    # IMPORTANT: threshold is ≤3 (not ≤4). "whats the news today" (4 words) and
    # "how are you" (3 words) were both mis-firing when threshold was 4.
    # "how are you" still fires at ≤3, but the question-word exclusion catches it.
    #
    # Question-word exclusion: messages that start with a WH-question or common
    # Hindi question words ("how are you", "what happened", "kya hua", "kaisa hai")
    # are genuine questions — never treat them as disengaged, regardless of length.
    _QUESTION_STARTS = {
        "how", "what", "when", "where", "who", "why", "which",
        "kya", "kaise", "kaun", "kab", "kyun", "kyunki",
    }
    _first_word = _original_text.strip().lower().split()[0] if _original_text.strip() else ""
    _word_count = len(_original_text.strip().split())  # use original, not possibly-wrapped text
    _is_short_disengaged = (
        _word_count <= 3
        and "?" not in _original_text
        and _first_word not in _QUESTION_STARTS
        and not any(sig in _original_text.lower() for sig in ["help", "bachao", "emergency"])
        and not _is_vulnerability  # let vulnerability pre-processor handle emotional shorts
        and not _is_grief          # let grief pre-processor handle grief shorts
    )
    if _is_short_disengaged and not _is_mid_session_greeting:
        _dis_lang = (user_context.get("language") or "english").lower()
        _dis_lang_label = {"hindi": "Hindi", "hinglish": "Hinglish"}.get(_dis_lang, "English")
        # Example MUST match the target language — previously we showed both
        # "'Theek hai.' or 'Alright.'" regardless of language, and DeepSeek
        # sometimes picked the Hindi example even when told English-only.
        _dis_example = {
            "hindi":    "'Theek hai.' or 'Haan.'",
            "hinglish": "'Theek hai.' or 'Alright.'",
        }.get(_dis_lang, "'Alright.' or 'Got it.'")
        text = (
            f"[HARD OVERRIDE — apply before anything else:\n"
            f"1. Respond in {_dis_lang_label} only. Do NOT mix languages.\n"
            f"2. One short, warm, non-questioning sentence. Stop there.\n"
            f"3. Ask nothing. No follow-up. No observations about how they seem.\n"
            f"4. This is a disengaged reply — do not try to extend the conversation.\n"
            f"5. Correct: {_dis_example} — Incorrect: 'Interesting — what do you think about...?']\n\n"
            f"Senior's message: {_original_text}"
        )
        logger.info(
            "PIPELINE | user_id=%s | short_reply_disengagement triggered | lang=%s",
            user_id, _dis_lang,
        )

    # --- Pending-input capture OFFER (Batch 2, 22 Apr 2026) ---
    # If the senior mentions grandkids or medicines in passing AND the matching
    # pending_* flag is set, offer to capture the missing info warmly.
    #
    # Gated against emotional moments: grief, vulnerability, and short-reply
    # disengagement all block the offer — "someone who is there, not someone
    # who is trying". Data collection must never interrupt care.
    #
    # Also gated against _already_awaiting (we don't re-offer if the senior is
    # already in the middle of a capture flow) and _is_mid_session_greeting
    # (a bare "hello" that happened to contain the word "grandson" somehow).
    if (
        user_row["onboarding_complete"]
        and not _is_grief
        and not _is_vulnerability
        and not _is_short_disengaged
        and not _is_mid_session_greeting
    ):
        _pending_grand = (
            user_row["pending_grandkids_names"]
            if "pending_grandkids_names" in user_row.keys() else 0
        ) or 0
        _pending_med = (
            user_row["pending_medicines"]
            if "pending_medicines" in user_row.keys() else 0
        ) or 0
        _already_awaiting = (
            user_row["awaiting_pending_capture"]
            if "awaiting_pending_capture" in user_row.keys() else None
        )
        if _already_awaiting is None and (_pending_grand or _pending_med):
            from pending_capture import detect_pending_trigger, build_capture_offer
            _trigger = detect_pending_trigger(_original_text)
            # Only offer if the pending flag for THIS kind is still set.
            _offer_kind = None
            if _trigger == "grandkids" and _pending_grand:
                _offer_kind = "grandkids"
            elif _trigger == "medicines" and _pending_med:
                _offer_kind = "medicines"
            if _offer_kind is not None:
                _offer_lang = (user_context.get("language") or "english").lower()
                try:
                    _offer_text = build_capture_offer(_offer_kind, _offer_lang)
                except Exception as _offer_err:
                    logger.error(
                        "PENDING_CAPTURE | user_id=%s | build_offer raised: %s",
                        user_id, _offer_err,
                    )
                    _offer_text = None
                if _offer_text:
                    from database import update_user_fields as _uuf_pc_offer
                    try:
                        _uuf_pc_offer(user_id, awaiting_pending_capture=_offer_kind)
                        _invalidate_user_cache(user_id)
                    except Exception as _set_err:
                        logger.error(
                            "PENDING_CAPTURE | user_id=%s | set awaiting failed: %s",
                            user_id, _set_err,
                        )
                        # Fall through to DeepSeek rather than send an offer we
                        # can't handle on the next turn.
                        _offer_text = None
                if _offer_text:
                    # Prefer editing the already-sent placeholder; else send new.
                    try:
                        if _placeholder_msg is not None:
                            await _placeholder_msg.edit_text(_offer_text)
                        else:
                            await update.message.reply_text(_offer_text)
                    except Exception:
                        try:
                            await update.message.reply_text(_offer_text)
                        except Exception:
                            logger.exception(
                                "PENDING_CAPTURE | user_id=%s | failed to send offer",
                                user_id,
                            )
                    # Session history + DB persist (match the normal reply path).
                    _live_session_append(user_id, "user", _original_text)
                    _live_session_append(user_id, "assistant", _offer_text)
                    _db_queue(save_message_record, user_id, "out", _offer_text)
                    _db_queue(save_session_turn, user_id, "user", _original_text)
                    _db_queue(save_session_turn, user_id, "assistant", _offer_text)
                    logger.info(
                        "PENDING_CAPTURE | user_id=%s | offered | kind=%s | lang=%s",
                        user_id, _offer_kind, _offer_lang,
                    )
                    return

    # --- DeepSeek (async, non-blocking) ---
    # _async_reply runs call_deepseek in a thread pool so the event loop stays
    # free for other users. Pre-sent placeholder is edited with the reply when done.
    reply = await _async_reply(update, text, user_context, _session_history, placeholder_msg=_placeholder_msg)
    logger.info("OUT | user_id=%s | type=%s | content=%s", user_id, input_type, reply[:80])

    # --- Update in-memory session (instant) + queue DB persists ---
    # In-memory update is synchronous and takes microseconds.
    # Use _original_text (not the targeted greeting prompt) so session history stays clean.
    _live_session_append(user_id, "user", _original_text)
    _live_session_append(user_id, "assistant", reply)

    # DB writes go to the background queue — never block the response path.
    # Each write queues into _DB_WRITE_QUEUE and is processed by the single
    # worker (_db_writer_worker) — one at a time, no Turso contention.
    _db_queue(save_message_record, user_id, "out", reply)
    _db_queue(save_session_turn, user_id, "user", _original_text)
    _db_queue(save_session_turn, user_id, "assistant", reply)

    # --- TTS voice note (background task) ---
    # Fire-and-forget: the pipeline returns as soon as text is delivered.
    # Voice arrives independently — typically 10-20s later depending on
    # Google Cloud TTS latency from Railway servers. The senior already has
    # the text; voice is a supplementary accessibility layer.
    #
    # Two guards to prevent stale/oversized voice notes:
    #   1. STALENESS: if TTS takes so long that >40s have passed since the text was
    #      sent, the conversation has likely moved on. Drop the voice note silently.
    #   2. LENGTH: long responses (news, cricket, weather — typically >180 chars) are
    #      already well-formatted text. Sending a 2-minute voice reading of a news
    #      bulletin is worse UX than just the text. Skip TTS for these.
    # TTS language: prefer per-message effective language (set by script
    # detection at line ~1303 into user_context["language"]) so Hindi/Hinglish
    # replies use hi-IN-Neural2-A instead of en-IN-Neural2-D. Falls back to
    # the stored user_row preference, then English.
    user_language = (
        user_context.get("language")
        or user_row["language"]
        or "english"
    )
    _tts_sent_at = time.monotonic()
    _TTS_STALE_SECONDS = 40
    _TTS_MAX_CHARS = 180  # skip TTS for long info-dump responses

    async def _send_tts_bg(_uid: int, _reply: str, _lang: str, _upd, _sent_at: float) -> None:
        try:
            if len(_reply) > _TTS_MAX_CHARS:
                logger.info("TTS | user_id=%s | skipped (reply too long: %d chars)", _uid, len(_reply))
                return
            audio_bytes = await asyncio.to_thread(text_to_speech, _reply, _lang)
            elapsed = time.monotonic() - _sent_at
            if elapsed > _TTS_STALE_SECONDS:
                logger.info(
                    "TTS | user_id=%s | dropped (stale: %.1fs since text sent)", _uid, elapsed
                )
                return
            await _upd.message.reply_voice(voice=io.BytesIO(audio_bytes))
            logger.info("TTS | user_id=%s | voice note sent (bg) | elapsed=%.1fs", _uid, elapsed)
        except Exception as _e:
            logger.warning("TTS | user_id=%s | bg failed: %s", _uid, _e)

    asyncio.create_task(_send_tts_bg(user_id, reply, user_language, update, _tts_sent_at))

    # Memory extraction — fire and forget after response is already delivered.
    # create_task so it runs without blocking the handler from returning.
    asyncio.create_task(asyncio.to_thread(extract_and_save_memories, user_id, text, reply))


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — quick health check. Confirms the bot is alive, shows uptime and API key presence."""
    import os, datetime
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/status", user_id)

    lines = ["*Saathi status*", ""]

    # Uptime
    try:
        uptime_s = int(time.monotonic() - _STARTUP_MONOTONIC)
        h, rem = divmod(uptime_s, 3600)
        m, s = divmod(rem, 60)
        lines.append(f"✅ Bot alive — uptime {h}h {m}m {s}s")
    except Exception:
        lines.append("✅ Bot alive")

    # API keys present (not their values — just presence)
    key_checks = [
        ("TELEGRAM_BOT_TOKEN",  "Telegram"),
        ("DEEPSEEK_API_KEY",    "DeepSeek"),
        ("OPENAI_API_KEY",      "Whisper/OpenAI"),
        ("GOOGLE_CLOUD_API_KEY","Google TTS/YouTube"),
        ("CRICKET_API_KEY",     "Cricket"),
        ("WEATHER_API_KEY",     "Weather"),
        ("NEWS_API_KEY",        "News"),
    ]
    key_lines = []
    for env_var, label in key_checks:
        present = bool(os.environ.get(env_var))
        key_lines.append(f"{'✅' if present else '❌'} {label}")
    lines.append("\n".join(key_lines))

    # DB write queue depth
    if _DB_WRITE_QUEUE is not None:
        lines.append(f"📬 DB queue depth: {_DB_WRITE_QUEUE.qsize()}")

    # Current IST time
    from datetime import timezone, timedelta
    ist = datetime.datetime.now(timezone(timedelta(hours=5, minutes=30)))
    lines.append(f"🕐 IST: {ist.strftime('%d %b %Y %H:%M')}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    logger.info("OUT | user_id=%s | type=status", user_id)


async def handle_policy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/policy — sends the short privacy summary. Senior can request full policy by replying."""
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/policy", user_id)
    await update.message.reply_text(POLICY_COMMAND_RESPONSE, parse_mode="Markdown")
    logger.info("OUT | user_id=%s | type=policy_short", user_id)


async def handle_full_policy_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    If the senior replies 'full policy' after the /policy short response,
    send the complete policy document.
    Called from handle_text when the message is exactly 'full policy'.
    """
    user_id = update.effective_user.id
    await update.message.reply_text(USER_POLICY_DOCUMENT)
    logger.info("OUT | user_id=%s | type=policy_full", user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/start", user_id)
    try:
        user_row = get_or_create_user(user_id)

        if not user_row["onboarding_complete"]:
            setup_mode = user_row["setup_mode"] if "setup_mode" in user_row.keys() else None
            step = user_row["onboarding_step"]

            if setup_mode is None or setup_mode == "pending":
                # Ask the opening detection question and mark it as pending.
                # 'pending' = we've asked, waiting for the answer.
                from database import update_user_fields as _uuf
                _uuf(user_id, setup_mode="pending")
                reply = get_opening_detection_question()
            elif setup_mode == "family" and step == 0:
                reply = get_intro_message()
            else:
                reply = get_resume_prompt(user_id, step, setup_mode=setup_mode)
        else:
            if (user_row["language"] or "").lower() == "english":
                reply = "Hello. Good to hear from you."
            else:
                reply = "Namaste! Main yahan hoon. 🙏"

        await update.message.reply_text(reply, parse_mode="Markdown")
        logger.info("OUT | user_id=%s | type=text | content=%s", user_id, reply[:80])
    except Exception as e:
        logger.error("ERR | user_id=%s | error=%s", user_id, e)
        raise


# ---------------------------------------------------------------------------
# /familycode pending-term lazy-ask (22 Apr 2026)
# ---------------------------------------------------------------------------
# In child-led setups the adult child forwards the family-code message to
# another family member. Using the senior's first name in that message would
# read as scam/spam (Indian cultural register: adult children use relational
# terms like "Papa" / "Mom" / "Rishi Uncle", never first names).
#
# We don't want to burden self-setup onboarding with an extra question, so
# the relational term is asked LAZILY the first time /familycode is invoked
# on a child-led account where `family_term` is NULL. The senior's next
# message is captured as the term, saved to DB, and the invite block is
# built and returned.
#
# State lives in-memory: acceptable to lose on process restart (the user
# simply sees the same question again next /familycode call). 10-min TTL
# so a stale ask from yesterday never steals a normal message.
# ---------------------------------------------------------------------------

_PENDING_FAMILY_TERM_ASK: dict[int, float] = {}  # user_id → unix ts
_PENDING_FAMILY_TERM_TTL_SEC = 600  # 10 minutes


def _pending_term_is_fresh(user_id: int) -> bool:
    ts = _PENDING_FAMILY_TERM_ASK.get(user_id)
    if ts is None:
        return False
    if (time.time() - ts) > _PENDING_FAMILY_TERM_TTL_SEC:
        _PENDING_FAMILY_TERM_ASK.pop(user_id, None)
        return False
    return True


def _build_familycode_reply(code: str, invite_block: str) -> str:
    """Shared wrapping for the /familycode reply across both variants."""
    return (
        f"Your family code is *{code}*.\n\n"
        f"To add a family member, copy the message below and forward it "
        f"to them on WhatsApp, iMessage, or however you usually chat:\n\n"
        f"— — —\n\n"
        f"{invite_block}\n\n"
        f"— — —\n\n"
        f"The same code works for more than one person — forward it to "
        f"anyone you'd like to link. 🙏"
    )


async def handle_familycode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/familycode — senior requests a linking code to share with family."""
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/familycode", user_id)
    try:
        user_row = get_or_create_user(user_id)
        if not user_row["onboarding_complete"]:
            await update.message.reply_text(
                "Please complete setup first — then you can share a family code. 🙏"
            )
            return

        code = get_or_create_linking_code(user_id)
        if not code or code == "ERROR":
            await update.message.reply_text("Something went wrong. Please try again. 🙏")
            return

        # Route by how Saathi was set up: self-setup → first-person; child-led → third-person.
        _setup_mode = user_row["setup_mode"] if "setup_mode" in user_row.keys() else None

        from family import (
            build_family_invite_block_first_person,
            build_family_invite_block_third_person,
        )

        if _setup_mode == "self":
            # Self-setup — senior is speaking for themselves.
            invite_block = build_family_invite_block_first_person(code=code)
            await update.message.reply_text(
                _build_familycode_reply(code, invite_block), parse_mode="Markdown"
            )
            logger.info("OUT | user_id=%s | type=familycode | mode=self | code=%s", user_id, code)
            return

        # Child-led (or unknown → treat as child-led by default).
        _family_term = user_row["family_term"] if "family_term" in user_row.keys() else None
        _family_term = (_family_term or "").strip()

        if _family_term:
            invite_block = build_family_invite_block_third_person(
                family_term=_family_term, code=code
            )
            await update.message.reply_text(
                _build_familycode_reply(code, invite_block), parse_mode="Markdown"
            )
            logger.info(
                "OUT | user_id=%s | type=familycode | mode=child_led | term=%s | code=%s",
                user_id, _family_term, code,
            )
            return

        # Child-led and no term yet → lazy-ask.
        _PENDING_FAMILY_TERM_ASK[user_id] = time.time()
        senior_name = user_row["name"] or "them"
        await update.message.reply_text(
            f"Quick question before I give you the message to forward — "
            f"what does the person you're sending this to call {senior_name}?\n\n"
            f"(For example: *Ma*, *Papa*, *Mummy*, *Dadi*, *Nani*, *Aunty*, "
            f"*Maasi*, or *{senior_name} Ji* — whatever term they would "
            f"naturally use for {senior_name}.)",
            parse_mode="Markdown",
        )
        logger.info(
            "OUT | user_id=%s | type=familycode_term_ask | senior_name=%s",
            user_id, senior_name,
        )
    except Exception as e:
        logger.error("ERR | user_id=%s | /familycode error: %s", user_id, e)
        await update.message.reply_text("Something went wrong. Please try again. 🙏")


async def handle_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/join [CODE] — family member links to a senior's profile."""
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=command | content=/join", user_id)
    try:
        args = context.args
        code = args[0] if args else ""
        success, reply = join_by_code(code, user_id)

        # Cache invalidation on successful join:
        # Before /join, this user_id may have been cached as "not a family member"
        # (None) by _senior_for_family_cached, and/or as a senior row by
        # _get_user_with_cache (since any first contact auto-creates a users row).
        # Both caches must be dropped so the next message from this user correctly
        # routes through the family-relay path instead of being treated as a senior.
        if success:
            _FAMILY_CACHE.pop(user_id, None)
            _invalidate_user_cache(user_id)
            logger.info(
                "FAMILY | caches invalidated after /join | user_id=%s", user_id,
            )

        await update.message.reply_text(reply, parse_mode="Markdown")
        logger.info(
            "OUT | user_id=%s | type=join | code=%s | success=%s",
            user_id, code, success,
        )
    except Exception as e:
        logger.error("ERR | user_id=%s | /join error: %s", user_id, e)
        await update.message.reply_text("Something went wrong. Please try again. 🙏")


async def adminreset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != 8711370451:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /adminreset <telegram_id>")
        return
    try:
        target_telegram_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid telegram_id — must be a number.")
        return
    try:
        result = admin_reset_user(target_telegram_id)
    except Exception as _e:
        result = f"DB reset failed: {_e}"
    finally:
        _invalidate_user_cache(target_telegram_id)
        _LIVE_SESSION_STORE.pop(target_telegram_id, None)
    await update.message.reply_text(result)


async def setcity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin-only command to correct a user's city (triggers a timezone re-derive
    on the next message). Useful for travel ("my senior is in London for 3
    weeks"), onboarding typos, and post-pilot corrections.

    Usage: /setcity <telegram_id> <city>
    Example: /setcity 123456789 Melbourne
             /setcity 123456789 New York
    """
    if update.effective_user.id != 8711370451:
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /setcity <telegram_id> <city>\n"
            "Example: /setcity 123456789 Melbourne"
        )
        return
    try:
        target_telegram_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Invalid telegram_id — must be a number.")
        return

    raw_city = " ".join(args[1:]).strip()
    if not raw_city:
        await update.message.reply_text("City cannot be empty.")
        return

    from apis import canonicalize_city, CITY_ALIASES, get_iana_timezone
    from database import update_user_fields
    canonical = canonicalize_city(raw_city)
    key = raw_city.lower()
    known = key in CITY_ALIASES
    iana = get_iana_timezone(canonical)

    try:
        update_user_fields(target_telegram_id, city=canonical)
    except Exception as _e:
        await update.message.reply_text(f"DB update failed: {_e}")
        return
    finally:
        _invalidate_user_cache(target_telegram_id)

    warning = "" if known else (
        "\n\n⚠️ Warning: city not in alias map — stored as title-case. "
        "Weather may not resolve; timezone will fall back to IST unless "
        "the canonical name is also in CITY_TIMEZONE."
    )
    await update.message.reply_text(
        f"✅ City updated for `{target_telegram_id}`:\n"
        f"  input: `{raw_city}`\n"
        f"  stored: `{canonical}`\n"
        f"  timezone: `{iana}`"
        f"{warning}",
        parse_mode="Markdown",
    )


async def testapis_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dev-only command: tests weather/news/cricket APIs live and reports results."""
    if update.effective_user.id != 8711370451:
        return
    import os
    from apis import fetch_weather, fetch_cricket, fetch_news
    lines = ["*API Test Results*\n"]

    # env vars
    lines.append(f"WEATHER_API_KEY set: {'yes' if os.environ.get('WEATHER_API_KEY') else 'NO'}")
    lines.append(f"CRICKET_API_KEY set: {'yes' if os.environ.get('CRICKET_API_KEY') else 'NO'}")
    lines.append(f"NEWS_API_KEY set: {'yes' if os.environ.get('NEWS_API_KEY') else 'NO'}\n")

    # weather
    try:
        w = fetch_weather("Mumbai")
        lines.append(f"Weather (Mumbai): {str(w)[:120] if w else 'None returned'}")
    except Exception as e:
        lines.append(f"Weather error: {e}")

    # cricket
    try:
        c = fetch_cricket()
        lines.append(f"Cricket: {str(c)[:120] if c else 'None returned (no live match — expected)'}")
    except Exception as e:
        lines.append(f"Cricket error: {e}")

    # news
    try:
        n = fetch_news("")
        lines.append(f"News: {str(n)[:200] if n else 'None returned'}")
    except Exception as e:
        lines.append(f"News error: {e}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = update.message.text
    logger.info("IN  | user_id=%s | type=text | content=%s", user_id, text)

    chat_id = update.effective_chat.id

    async def _keep_typing(stop_event: asyncio.Event) -> None:
        """Keep the 'typing…' indicator alive until stop_event is set."""
        while not stop_event.is_set():
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            for _ in range(8):  # 8 × 0.5s = 4s between refreshes
                if stop_event.is_set():
                    return
                await asyncio.sleep(0.5)

    try:
        # Start typing indicator IMMEDIATELY — before any DB work.
        # Previously, get_or_create_user() ran synchronously here and blocked the
        # event loop for up to 10-12s (Turso sync) before the indicator even fired.
        _stop_event = asyncio.Event()
        _typing_task = asyncio.create_task(_keep_typing(_stop_event))

        # Fetch the user row from the in-memory cache (instant) or Turso (~5s first
        # call). After the first message the cache hit means this returns in <1ms,
        # so the typing indicator fires almost immediately.
        user_row = await _get_user_with_cache(user_id)

        try:
            # Hard 25-second timeout on the full pipeline.
            # If a DB call hangs (e.g. malformed libsql connection blocking a
            # thread), _run_pipeline never returns and _keep_typing spins
            # forever until Railway kills the container. The timeout cancels
            # the pipeline cleanly so the bot stays alive for other users.
            await asyncio.wait_for(
                _run_pipeline(user_id, text, user_row, update, input_type="text", context=context),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            logger.error("ERR | user_id=%s | pipeline timeout (>25s) — likely stuck DB call", user_id)
            raise RuntimeError("pipeline timeout")
        finally:
            _stop_event.set()
            await asyncio.sleep(0)  # yield so _keep_typing can exit cleanly
    except Exception as e:
        logger.error("ERR | user_id=%s | error=%s", user_id, e, exc_info=True)
        # Use the user's language if we can read it from cache; default to English.
        _err_lang = "english"
        try:
            _err_row = _USER_CACHE.get(user_id)
            if _err_row:
                _err_lang = (_err_row.get("language") or "english").lower()
        except Exception:
            pass
        if _err_lang in ("hindi", "hinglish"):
            _err_msg = "Maafi chahta hoon, kuch takleef aa rahi hai. Thodi der mein dobara try karein. 🙏"
        else:
            _err_msg = "Sorry, something went wrong on my end. Please try again in a moment. 🙏"
        await update.message.reply_text(_err_msg)
        raise


async def receive_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    file_id = update.message.voice.file_id
    duration = update.message.voice.duration
    logger.info(
        "IN  | user_id=%s | type=voice | file_id=%s | duration_s=%s",
        user_id, file_id, duration,
    )
    try:
        # Fetch user row (from cache, instant) and download voice file concurrently.
        user_row, tg_file = await asyncio.gather(
            _get_user_with_cache(user_id),
            context.bot.get_file(file_id),
        )
        file_bytes = bytes(await tg_file.download_as_bytearray())

        # Show "typing..." while Whisper transcribes
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )

        # Transcribe via Whisper — use the user's language as a hint
        user_language = (user_row["language"] or "hindi") if user_row else "hindi"
        try:
            text = transcribe_voice(file_bytes, user_language=user_language)
            logger.info(
                "WHISPER | user_id=%s | transcribed: %s",
                user_id, text[:80],
            )
        except Exception as whisper_err:
            logger.error("WHISPER | user_id=%s | failed: %s", user_id, whisper_err)
            await update.message.reply_text(
                "Sorry, I couldn't hear that clearly. Could you type it instead? 🙏"
            )
            return

        if not text:
            await update.message.reply_text(
                "I heard something but couldn't make it out. Could you type it? 🙏"
            )
            return

        # Short transcription guard — if Whisper only caught 1–2 words, the recording
        # was likely unclear (not the user disengaging). Ask to resend rather than
        # sending a flat response like "Alright." via the disengagement path.
        if len(text.strip().split()) <= 2:
            user_lang = (user_row["language"] or "hindi") if user_row else "hindi"
            if user_lang in ("hindi", "hinglish"):
                await update.message.reply_text(
                    "Sahi se sun nahi paaya — zara dobara bhejein? 🙏"
                )
            else:
                await update.message.reply_text(
                    "Sorry, couldn't catch that clearly — could you send it again? 🙏"
                )
            return

        # Pass transcribed text through the full pipeline — identical to text messages
        await _run_pipeline(user_id, text, user_row, update, input_type="voice", context=context)

    except Exception as e:
        logger.error("ERR | user_id=%s | error=%s", user_id, e)
        await update.message.reply_text(
            "Maafi chahta hoon, abhi kuch takleef aa rahi hai. Thodi der mein dobara try karein. 🙏"
        )
        raise


async def handle_unsupported_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Catch-all handler for message types Saathi cannot process:
    photos, stickers, GIFs, documents, video, audio files.

    Without this, PTB silently drops the message and the senior gets no response —
    which looks like the bot broke. A warm, language-aware reply is far better.
    """
    user_id = update.effective_user.id
    logger.info("IN  | user_id=%s | type=unsupported_media", user_id)
    try:
        user_row = get_or_create_user(user_id)
        language = (user_row["language"] or "hindi") if user_row else "hindi"
    except Exception:
        language = "hindi"

    if language in ("hindi", "hinglish"):
        reply = (
            "Yeh mujhe abhi samajh nahi aata — "
            "apni baat words mein bataiye na? 🙏"
        )
    else:
        reply = (
            "I can't quite see that yet — "
            "but I'd love to hear about it in your own words. 🙏"
        )

    await update.message.reply_text(reply)
    logger.info("OUT | user_id=%s | type=unsupported_media_reply", user_id)


# ---------------------------------------------------------------------------
# Scheduler job — runs every 60 seconds via PTB JobQueue (APScheduler)
# ---------------------------------------------------------------------------

async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called every minute. Sends due reminders and escalates unacknowledged ones."""
    try:
        await check_and_send_reminders(context.bot)
    except Exception as e:
        logger.error("SCHEDULER | reminder_job failed: %s", e)


async def ritual_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called every minute. Sends morning/afternoon/evening rituals at user-set times."""
    try:
        await check_and_send_rituals(context.bot)
    except Exception as e:
        logger.error("SCHEDULER | ritual_job failed: %s", e)


async def safety_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called every minute. Runs the hourly inactivity check (self-gated to once/hour)."""
    try:
        await check_inactivity(context.bot)
    except Exception as e:
        logger.error("SCHEDULER | safety_job failed: %s", e)


async def weekly_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called every minute. Sends weekly family reports on Sundays 10am IST (self-gated)."""
    try:
        await check_and_send_weekly_report(context.bot)
    except Exception as e:
        logger.error("SCHEDULER | weekly_report_job failed: %s", e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _start_health_server() -> None:
    """
    Bind to Railway's $PORT immediately so the platform considers the container
    healthy and does not kill it during slow DB initialisation.

    This is a minimal HTTP server (GET / → 200 OK).  In polling mode there is
    no PTB webhook listener, so this port is free.  In webhook mode this server
    is NOT started — PTB's run_webhook() owns the port instead.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):  # silence access logs
            pass

    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("HEALTH | server started on port %d", port)


async def post_init(application: Application) -> None:
    """
    Called by PTB once the asyncio event loop is running.
    Starts the DB write queue worker — must run inside the event loop
    because asyncio.Queue() and create_task() require it. Also caches
    the bot's Telegram username for the forward-ready family invite block.
    """
    global _DB_WRITE_QUEUE
    _DB_WRITE_QUEUE = asyncio.Queue()
    asyncio.create_task(_db_writer_worker())
    logger.info("STARTUP | DB write queue started")

    # Cache the bot's Telegram username. Used by build_family_invite_block()
    # so the forward-ready block says "search for @RealBotName" instead of a
    # placeholder. One-time call per deploy; get_me() failure logs an error
    # and the helper falls back to 'Saathi' (still usable, just less precise).
    try:
        from family import set_cached_bot_username
        me = await application.bot.get_me()
        set_cached_bot_username(me.username or "")
    except Exception as e:
        logger.error(
            "STARTUP | bot.get_me() failed — invite block will use fallback: %s", e
        )


def main() -> None:
    # Start health server FIRST — before any DB work — so Railway sees the
    # process as healthy and does not restart the container during slow init.
    # Only needed in polling mode; webhook mode lets PTB own the port.
    if not os.environ.get("WEBHOOK_URL", ""):
        _start_health_server()

    logger.info("STARTUP | run_startup_migrations")
    run_startup_migrations()
    logger.info("STARTUP | init_db")
    init_db()
    logger.info("Database initialised")

    # Verify DB is usable before starting any scheduler or accepting traffic.
    # If this fails, the container log will show a CRITICAL error — Railway keeps
    # restarting, which is better than silently serving a broken bot.
    try:
        from database import get_connection as _gc
        _vc = _gc()
        _vc.execute("SELECT 1 FROM users LIMIT 1")
        logger.info("STARTUP | DB schema verified OK")
    except Exception as _verify_err:
        logger.critical(
            "STARTUP | DB schema verification FAILED: %s — "
            "check Railway Volume mount and DB_PATH env var",
            _verify_err,
        )

    # Seed the memory question bank — inserts 300+ questions if the table is empty.
    # Safe to call on every startup — silently skips if already seeded.
    try:
        from memory_questions import seed_memory_questions
        seed_memory_questions()
        logger.info("Memory question bank ready")
    except Exception as seed_err:
        logger.error("STARTUP | seed_memory_questions failed (non-fatal): %s", seed_err)

    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", handle_status_command))
    app.add_handler(CommandHandler("help", handle_help_command))
    app.add_handler(CommandHandler("policy", handle_policy_command))
    app.add_handler(CommandHandler("familycode", handle_familycode))
    app.add_handler(CommandHandler("join", handle_join))
    app.add_handler(CommandHandler("adminreset", adminreset_command))
    app.add_handler(CommandHandler("setcity", setcity_command))
    app.add_handler(CommandHandler("testapis", testapis_command))
    app.add_handler(CallbackQueryHandler(handle_help_callback, pattern="^help_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, receive_voice))
    # Catch photos, stickers, GIFs, documents, video, audio — return a warm
    # "I can't see that" message instead of silently dropping the message.
    app.add_handler(MessageHandler(
        filters.PHOTO
        | filters.Document.ALL   # catches GIFs/animations too (sent as Documents)
        | filters.Sticker.ALL
        | filters.VIDEO
        | filters.VIDEO_NOTE
        | filters.AUDIO,
        handle_unsupported_media,
    ))

    # Register the reminder scheduler — fires every 60 seconds, first check after 10s
    app.job_queue.run_repeating(reminder_job, interval=60, first=10)
    logger.info("Reminder scheduler registered (interval=60s)")

    # Register the ritual scheduler — same interval, offset by 15s to spread load
    app.job_queue.run_repeating(ritual_job, interval=60, first=15)
    logger.info("Ritual scheduler registered (interval=60s)")

    # Register the safety scheduler — runs every minute, self-gated to hourly
    app.job_queue.run_repeating(safety_job, interval=60, first=30)
    logger.info("Safety scheduler registered (interval=60s, hourly inactivity check)")

    # Register the weekly family report scheduler — runs every minute, self-gated to Sunday 10am IST
    app.job_queue.run_repeating(weekly_report_job, interval=60, first=45)
    logger.info("Weekly report scheduler registered (interval=60s, Sunday 10am IST)")

    if WEBHOOK_URL:
        logger.info("Starting webhook mode on port %s", PORT)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="/webhook",
            webhook_url=f"{WEBHOOK_URL}/webhook",
        )
    else:
        logger.info("No WEBHOOK_URL set — starting in polling mode")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
