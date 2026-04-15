# SAATHI BOT — Session Resume Prompt

**Read this before doing anything else. Then read CLAUDE.md and progress.md.**

---

## What We Are Building

Saathi is a Telegram-based AI companion for urban Indian seniors aged 65+. The builder (Rishi) is non-technical. All explanations must be in plain English. The full product context is in `CLAUDE.md`.

**Current state:** Modules 0–18 complete and live on Railway. Module 19 (end-to-end capability testing) is partially done. Module 20 (pilot prep) has not started. The bot is deployed at Railway.app, auto-deploying from the GitHub repo at `~/saathi-bot`.

---

## File Map

| File | What it does |
|---|---|
| `main.py` | Entry point. PTB (python-telegram-bot) app. `handle_text` → `_run_pipeline` is the core message flow. All handler registration at bottom. In-memory caches, DB write queue, session store all live here. |
| `database.py` | All DB access. Single global connection (`_GLOBAL_CONN`). Turso (libsql embedded replica) with sqlite3 fallback. Self-healing logic added this session. |
| `deepseek.py` | DeepSeek V3 API wrapper. `call_deepseek()` (blocking) and `call_deepseek_streaming()` (streaming). `_build_system_prompt()` builds the full Protocol 2 system prompt with user context. |
| `apis.py` | Three live data APIs: weather (OpenWeatherMap), cricket (CricAPI), news (RSS + NewsAPI fallback). 30-min in-memory cache. `_find_india_match()` and `_parse_match_date()` for IPL/India cricket. |
| `protocol1.py` | Mental health crisis handler. Hardcoded keyword matching. Runs BEFORE DeepSeek. |
| `protocol3.py` | Financial/legal sensitivity handler. Runs BEFORE DeepSeek. |
| `protocol4.py` | Inappropriate content handler. |
| `onboarding.py` | 18-question child-led onboarding flow + self-setup mode + 4-message staged handoff to senior. |
| `memory.py` | Diary entries, memory extraction (DeepSeek), context retrieval injected before every DeepSeek call. |
| `memory_questions.py` | 316-question memory bank across 9 themes. Per-user no-repeat tracking. |
| `rituals.py` | Morning briefing, evening check-in, purpose loops, `record_first_message()`. |
| `reminders.py` | Medicine reminders with family escalation. |
| `safety.py` | Heartbeat, silence detection, emergency keyword handler. |
| `tts.py` | Google Cloud TTS (Neural2 voices). |
| `whisper.py` | OpenAI Whisper for voice transcription. |
| `youtube.py` | YouTube Data API v3 for music search. |
| `family.py` | Family bridge relay + weekly health report. |
| `CLAUDE.md` | Master product context. **Read first every session.** |
| `progress.md` | Module status tracker. Update after every session. |
| `RESUME.md` | This file. |

---

## Architecture: The Message Pipeline

```
Incoming message
      ↓
handle_text (main.py)
  → _keep_typing task started
  → _get_user_with_cache (in-memory, Turso on first call)
  → _run_pipeline (25s hard timeout added this session)
        ↓
      _live_session_get (in-memory, instant)
      _senior_for_family_cached (in-memory after first call)
      [record_first_message removed from here — now queued AFTER placeholder]
      Protocol 1 check (mental health)
      Protocol 3 check (financial/legal)
      Protocol 4 check (inappropriate)
      Music detection → YouTube API → early return
      *** PLACEHOLDER "…" SENT HERE ***
      _db_queue(save_message_record) + _db_queue(record_first_message)
      _inject_live_data_if_needed (asyncio.to_thread — non-blocking)
      Greeting intercept / vulnerability pre-processor / grief pre-processor
      Identity handler
      Short-reply disengagement detector (≤3 words, excluding question-word starts)
      DeepSeek streaming (_async_reply)
      Text delivered (edit placeholder)
      _live_session_append (in-memory)
      _db_queue (out message, session turns)
      TTS background task (skip if >180 chars or >40s stale)
      Memory extraction background task
```

---

## Preferred Approach

- **Non-technical builder.** Never use jargon without explanation. Code changes should be explainable in plain English.
- **Surgical edits only.** Never rewrite a file wholesale. Make targeted changes, explain what each does and why.
- **Always py_compile before push.** `python3 -m py_compile main.py database.py apis.py deepseek.py` — no pushes with syntax errors.
- **Railway is the live environment.** Local `saathi.db` is empty/dev only. Real data is in Turso cloud.
- **Push command:** `cd ~/saathi-bot && git add -A && git commit -m "..." && git push`
- **If git lock error:** `rm -f ~/saathi-bot/.git/HEAD.lock` first.
- **Check health:** `/status` command on the Telegram bot — shows uptime, all 7 API keys, DB queue depth, IST time.
- **Check crashes:** Railway dashboard → Logs tab. `ERR |` lines include full tracebacks (`exc_info=True`).
- **DB writes are queued.** All DB writes go through `_db_queue(fn, *args)` — never called synchronously in the message hot path. This prevents Turso sync latency (~5s) from blocking responses.

---

## Session Log: April 15, 2026 — Bugs Faced and Solutions Applied

### Bug 1 — Placeholder delay: 5–7 seconds before "…" appears
**Root cause:** `record_first_message(user_id)` was called synchronously at the top of `_run_pipeline`, before the placeholder was sent. It calls `get_connection()` + `commit()` — a Turso commit triggers a cloud sync taking ~5s — even on INSERT OR IGNORE no-ops. Every single message had a 5-second freeze regardless of cache warmth.

**Fix (main.py):** Removed the synchronous call. Added `_db_queue(record_first_message, user_id)` after the placeholder is sent. Same fire-and-forget pattern as all other DB writes.

**Status: Fixed and pushed.**

---

### Bug 2 — Short-reply disengagement firing on real queries
**Root cause:** `_is_short_disengaged` check used `_word_count <= 4`. "whats the news today" (4 words, no "?") triggered the HARD OVERRIDE that told DeepSeek to give a one-word disengaged response instead of the news. "how are you" (3 words) also mis-fired.

**Fix (main.py):**
1. Lowered threshold to `_word_count <= 3`
2. Added `_QUESTION_STARTS` exclusion set — messages starting with `how`, `what`, `when`, `where`, `who`, `why`, `which`, `kya`, `kaise`, `kaun`, `kab`, `kyun` are never treated as disengaged regardless of length.

**Status: Fixed and pushed.**

---

### Bug 3 — Bot called user "Sage" (Sage is the bot's name)
**Root cause:** System prompt line: `"You are speaking with Rishi. They call you Sage."` — ambiguous enough that DeepSeek, during the HARD OVERRIDE path, grabbed "Sage" as the user's name rather than the bot's.

**Fix (deepseek.py):** Replaced with explicit two-line statement:
`YOUR NAME IS Sage. The person you are talking to is named Rishi. CRITICAL: Never address Rishi as 'Sage'.`

**Status: Fixed and pushed.**

---

### Bug 4 — IPL cricket showing "no match today" despite 23 matches returned by API
**Root cause:** `_find_india_match()` used a strict ISO date filter: `match_date = (raw_date)[:10]` compared to `today_ist = "YYYY-MM-DD"`. CricAPI free tier returns dates in non-ISO formats ("15 Apr 2026", "15-04-2026") or sometimes no date at all. All 23 IPL matches were silently dropped.

**Fix (apis.py):**
1. Added `_parse_match_date(raw)` that tries multiple formats: ISO fast-path, `%d-%m-%Y`, `%d %b %Y`, `%b %d, %Y`, `%d/%m/%Y`.
2. Added `undated_matches` fallback bucket — tracked matches with no parseable date are kept rather than dropped, and surfaced as SCHEDULED/LIVE/RECENT if no dated matches found.
3. Added debug logging of raw date fields for the first tracked match each call.

**Status: Fixed and pushed. Still need one Railway log check to confirm the raw_date format CricAPI is actually returning.**

---

### Bug 5 — Voice notes arriving 30–60 seconds late
**Root cause:** TTS background task for a 210-char news response took 12s for Google TTS API call, then another 20+ seconds to upload 115KB audio to Telegram. By the time it arrived, the user had sent multiple more messages. Stacked voice notes out of context.

**Fix (main.py):** Two guards in `_send_tts_bg`:
1. **Length guard:** if `len(reply) > 180`, skip TTS entirely. News/weather/cricket responses are already well-formatted text. Voice only for short conversational replies.
2. **Staleness guard:** if `> 40s` elapsed since text was sent, drop the voice note silently.

**Status: Fixed and pushed.**

---

### Bug 6 — Error message hardcoded in Hindi for all users
**Root cause:** `handle_text` error handler had `"Maafi chahta hoon..."` hardcoded regardless of user's language preference.

**Fix (main.py):**
- Check `_USER_CACHE.get(user_id)` for language. English/default: English error message. Hindi/Hinglish: Hindi error message.
- Added `exc_info=True` to the ERR logger so full Python tracebacks appear in Railway logs instead of just the exception message.

**Status: Fixed and pushed.**

---

### Bug 7 — Railway crash loop: `database disk image is malformed`
Two separate failure modes, both fixed.

**Part A — Startup crash loop:**
The libsql embedded replica file (`saathi.db`) on Railway's ephemeral disk got corrupted (likely from a mid-sync crash on a previous run). On restart: `_raw_connect()` opened the corrupted file → "malformed" → fell back to `sqlite3.connect(DB_PATH)` → same corrupted file → crashed. Loop repeated every ~2 seconds, Railway kept restarting.

**Fix (database.py):** Added `_delete_db_file()` helper. `_raw_connect()` now retries on first "malformed": deletes the file, then lets libsql create a fresh embedded replica by syncing from Turso. Local SQLite fallback also does an `integrity_check` before use and deletes+recreates if malformed.

**Part B — Runtime corruption (crash 9 minutes after startup):**
`_GLOBAL_CONN` is set once at startup and reused forever. A periodic libsql sync failed mid-operation, leaving the connection in a broken state. All subsequent DB calls failed. Scheduler jobs (`ritual_job`, `reminder_job`) started logging "malformed" every minute. `_run_pipeline` hung because a DB thread was blocked on the broken connection — `_keep_typing` then spun every 2 seconds until Railway killed the container (~60 seconds of spinning).

**Fix (database.py):** `get_connection()` now does a `PRAGMA foreign_keys = ON` health check on every call. If it raises "malformed/corrupt/disk image", it calls `_reset_connection()` (nulls `_GLOBAL_CONN`, deletes file) and reconnects before returning. Next caller gets a clean connection.

**Fix (main.py):** Added `asyncio.wait_for(..., timeout=25.0)` around `_run_pipeline`. If the pipeline hangs (DB thread blocked), it's cancelled after 25s, `_stop_event.set()` fires in `finally`, `_keep_typing` stops. Bot stays alive.

**Status: Fixed and pushed. Monitor in next session — if "malformed" still appears in scheduler logs, the runtime reset isn't firing correctly and needs investigation.**

---

### Addition — /status command
**Added (main.py):** `/status` handler. Returns: uptime, ✅/❌ for all 7 API keys, DB write queue depth, current IST time. Registered in `main()`. Use this immediately after every deploy.

---

---

### Session Log: April 15, 2026 (continued) — DB Architecture Overhaul

#### Root cause diagnosed: Turso is broken by design for this use case
Full post-mortem written as `Saathi_DB_Technical_Briefing_Apr15.docx` and reviewed by GPT-4o and Gemini. Core finding: libsql's `commit()` is local-only — it never called `sync()`, so Turso cloud was always empty. Every "malformed" error + recovery cascade was triggered by the empty cloud replica being synced over local state. GPT and Gemini both independently confirmed the fix: remove Turso, use sqlite3 + Railway Volume.

#### Commits this session (in addition to Apr 15 earlier commits):
- `e34351c` — malformed detection in `_Connection.execute()` triggers immediate reset
- `e3c7b9f` — "no such table" recovery: re-runs schema migrations on empty DB after reset
- `654af62` — **WAL mode, guarded reset, startup verify** (the three items GPT+Gemini flagged as critical before pilot)

#### What `654af62` does:
1. **WAL mode** (`database.py`): `PRAGMA journal_mode=WAL` + `synchronous=NORMAL` on every sqlite3 connect. Allows concurrent readers + one writer — prevents "database is locked" under asyncio + scheduler thread access.
2. **Guarded reset** (`database.py`): `_reset_connection()` now checks whether Turso or sqlite3 is in use. Turso path: still deletes local replica (cloud is source of truth). sqlite3 path: nulls connection only — NEVER deletes the file. Without this, a runtime malformed on Railway Volume = total data loss.
3. **Integrity check path-awareness** (`database.py`): On startup malformed check, Turso path deletes and resyncs; sqlite3 path logs CRITICAL but preserves the file (WAL recovery may succeed).
4. **Startup DB verify** (`main.py`): `SELECT 1 FROM users LIMIT 1` after init_db() — catches "no such table" before any scheduler fires or traffic arrives. Logs CRITICAL on failure.

#### The ONE remaining action Rishi must take on Railway dashboard:
**Delete `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` env vars.** This removes libsql from the boot path entirely. The bot will use stable sqlite3 from that point on. Then add a Railway Volume (1GB, mount at `/app`, set `DB_PATH=/app/saathi.db`, deploy strategy → Recreate). Without the Volume, DB still resets on container restart — but at least it won't corrupt. Volume is pre-pilot requirement.

---

## Also Done This Session — Global Workflow Protocol

A global high-stakes decision protocol was written to `~/.claude/CLAUDE.md`. It applies to all projects (Saathi, CMO System, Graphic Designer, any future work) automatically. Key rules: Claude flags high-stakes decisions before any work begins, gets explicit yes/no on whether to seek second opinions from GPT and Gemini, produces a three-column comparison table when second opinions come back, waits for typed agreement before proceeding. Session close process also encoded there. Root cause of the Turso decision documented — Claude pattern-matched to wrong solution category and failed to present Railway Volume as an alternative.

**In the next CMO System and Graphic Designer sessions: add one-line pointer to `~/.claude/CLAUDE.md` at the top of those CLAUDE.md files.**

---

## What To Do Next Session

### Step 0 — Railway dashboard actions (Rishi does these, not code)
1. **Delete** `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` env vars on Railway → bot redeploys clean
2. **Add Railway Volume**: Infrastructure tab → Volume → Mount path `/app` → set `DB_PATH=/app/saathi.db` env var → change deploy strategy to "Recreate" (not rolling)
3. After deploy: `/status` on Telegram — all 7 keys ✅, no "malformed" in logs

### Step 1 — Verify bot is healthy (5 min)
1. Send `/status` on Telegram. All 7 keys should be ✅.
2. Send "hello" — placeholder should appear in <2 seconds.
3. Send "how are you" — should get a normal warm response, NOT a one-word disengaged reply.
4. Send "and cricket?" — check Railway logs for `APIS | cricket tracked match | raw_date=...` to confirm date format.
5. Watch Railway logs for `STARTUP | DB schema verified OK` — confirms WAL + schema working.

### Step 2 — Complete Module 19 testing
Run remaining test groups from `module_15_test_protocol.md`:
- Full onboarding flow (child-led, self-setup, confusion branch)
- Protocol 1 triggers (Hindi: "jeena nahi chahta", "khatam kar loon")
- Protocol 3 triggers (financial: "mujhe apni property deni hai bete ko")
- Memory question bank (manually trigger if needed — normally Wednesday/Sunday only)
- Morning ritual at correct time
- Voice input (send a Hindi voice note)
- Emergency command ("I fell", "bachao")
- Family bridge (use `/familycode`, register as family, send a message)

### Step 3 — Build Module 20: Pilot Prep
Not started. Key tasks:
1. **Pilot user selection** — who are the 20 users? Adult children + their parents?
2. **Onboarding guide** — simple one-page doc for adult children (WhatsApp-style language)
3. **Feedback mechanism** — Google Form? WhatsApp group?
4. **Monitoring** — Railway logs sufficient for now; alert on crash optional
5. **Reset tooling** — test `/adminreset` works cleanly before sharing
6. **Privacy policy** — confirm `/policy` command returns correct document

---

## Known Open Questions (don't resolve without data)
- **Turso env var deletion** — Rishi must do this on Railway dashboard before next session
- **Railway Volume** — must be added before pilot (pre-pilot blocker)
- **IPL cricket date format** — check Railway logs after next test
- **Over-reliance limiters** — Week 6 decision after pilot data
- **ElevenLabs voice cloning** — deferred to v2
- **WhatsApp migration** — deferred to v2
