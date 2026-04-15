# SAATHI BOT — SESSION RESUME PROMPT
**Paste this at the start of every new session. Read CLAUDE.md and progress.md first.**

---

## WHO I AM AND HOW I WORK

I am Rishi, the non-technical founder building Saathi Bot. I cannot read or write code.
All instructions must be in plain English. When Terminal commands are needed, give me exact copy-paste commands one line at a time and tell me what each one does.

**Working preferences (non-negotiable):**
- Challenge assumptions and push back when I'm wrong — intellectual honesty over agreement
- Fix root causes, not symptoms. Partial fixes that mask the real problem have caused weeks of delay
- Think through the full pipeline before suggesting a fix
- Brief diagnosis before prescription when the problem is non-obvious
- Do not ask unnecessary clarifying questions — read CLAUDE.md and progress.md first and infer context
- Never jargon without explanation

---

## PROJECT OVERVIEW

Saathi is a Telegram-based AI companion for urban Indian seniors aged 65+. Full product spec is in CLAUDE.md.

**Codebase:** `/Users/rishikar/saathi-bot/`
**Hosting:** Railway.app — auto-deploys on `git push origin main`
**Language:** Python 3
**Key files:** `main.py`, `deepseek.py`, `apis.py`, `protocol1.py`, `protocol3.py`, `safety.py`, `memory.py`, `rituals.py`, `tts.py`, `youtube.py`, `reminders.py`, `family.py`, `database.py`, `onboarding.py`

---

## CURRENT STATE — START HERE

### Git State

| Location | What's there |
|---|---|
| **Remote / Railway (live)** | `d2fcdb2` — streaming deadlock code, old 10k-token system prompt |
| **Local (NOT YET PUSHED)** | 2 commits ahead: asyncio.to_thread fix + system prompt cut + news filter |

### First thing to do in a new session: push to Railway

Run these in Terminal from `~/saathi-bot`:
```
cd ~/saathi-bot
rm -f .git/index.lock
git push origin main
```
*(The `rm -f .git/index.lock` is always needed first — a stale Mac lock file blocks commits/pushes)*

After pushing, Railway auto-deploys. Watch for the green deploy in the Railway dashboard, then send a test message to Saathi on Telegram.

---

## WHAT WAS FIXED — READY TO PUSH

### Fix 1 — Streaming deadlock (bot going completely silent)
**File:** `main.py`
**Cause:** `_stream_reply()` used asyncio.Queue + producer thread. Silent producer failure caused `await queue.get()` to block forever — bot became permanently unresponsive.
**Fix:** Replaced with `_async_reply()` using `asyncio.to_thread(call_deepseek, ...)`. No queue, no deadlock path. Falls back to plain reply on error.

### Fix 2 — Core latency (20–60 second responses) — ROOT CAUSE FIXED
**File:** `deepseek.py`
**Cause:** `_BASE_SYSTEM_PROMPT` was 684 lines / ~10,000 tokens, sent to DeepSeek on every single message.
**Fix:** Condensed to 176 lines / ~2,825 tokens (73% reduction). All 12 behavioral rules fully preserved. Verbose examples, "why" paragraphs, and redundant restatements removed.
**Expected result:** Text responses 20–60s → 5–10s.

### Fix 3 — Temperature ("31°C = pleasant")
**File:** `deepseek.py`
**Cause:** Temperature instruction was only injected with weather data. DeepSeek used Western norms the rest of the time.
**Fix:** India temperature scale embedded directly inside `_BASE_SYSTEM_PROMPT`. DeepSeek sees it on every call.
Rule: `below 20°C = cool/cold | 20–25°C = mild | 26–29°C = warm | 30–34°C = quite warm/hot | 35°C+ = hot`. Never "pleasant" for 28°C+.

### Fix 4 — News irrelevant content ("popular streamer allegations")
**File:** `apis.py`
**Cause:** Geo-filter caught Dubai/Ukraine/Pakistan but not topic-irrelevant content.
**Fix:** Added `_IRRELEVANT_TOPIC_SIGNALS` list. Skips gaming, streaming, influencer, K-pop, crypto when no keyword specified. Applied in `_fetch_news_from_rss()` after `_NON_INDIA_GEO_SIGNALS`.

### Already live on Railway (from earlier sessions):
- Cricket: scripted "no data" response prevents DeepSeek hallucinating scores
- Delhi weather: city alias map ("Delhi" → "New Delhi,IN") + `,IN` retry
- Placeholder `…` sent BEFORE live API calls
- All API timeouts 4s
- Typing indicator, unsupported media handler, mood sufficiency guard

---

## OUTSTANDING ISSUES (verify after push)

| Issue | Status | What to check |
|---|---|---|
| Latency | Fixed in push — verify | Text should be 5–10s. Voice ~15s (DeepSeek + TTS stacked). |
| Temperature phrasing | Fixed in push — verify | Ask "what's the weather in Delhi?" — should say "quite warm" or "hot", not "pleasant" |
| News relevance | Fixed in push — verify | Ask "any news today?" — no gaming/streaming/influencer content |
| Voice latency (~25s) | Partially improved | Still DeepSeek + TTS stacked. Future fix: fire TTS in parallel. Not blocking pilot. |
| Turso DB query latency | Not yet investigated | If text latency is still >10s after push, this is next. ~6 DB queries run before placeholder is sent. |

---

## MODULE STATUS

| # | Module | Status |
|---|---|---|
| 0–18 | All modules | ✅ Done |
| 19 | End-to-end capability testing | ✅ Done |
| **20** | **Testing + 20-user pilot prep** | **⬜ Not started — this is next** |

Module 20 involves:
- Onboarding guide for adult children (plain English, WhatsApp-ready)
- Pilot welcome kit + what to tell the senior
- Feedback form / weekly check-in process
- Pilot success metrics defined upfront
- Any hardening identified from live testing before pilot users join

---

## ARCHITECTURE QUICK REFERENCE

```
Incoming Telegram message
  → Protocol 1 check (mental health crisis, hardcoded, before DeepSeek)
  → Protocol 3 check (financial/legal, hardcoded, before DeepSeek)
  → Memory retrieval (Turso cloud DB — ~6 queries)
  → Placeholder "…" sent to Telegram immediately
  → Live data injection if weather/cricket/news query (APIs called here)
  → asyncio.to_thread(call_deepseek) — non-blocking DeepSeek V3 call
  → Edit placeholder with final text response
  → TTS generated and sent as voice note
  → Memory extraction + diary stored
```

**DeepSeek:** `model="deepseek-chat"`, `temperature=0.8`, `max_tokens=400`
**Language lock:** Prepended as first line of every system prompt — cannot be overridden
**DB:** Turso cloud (production only). Local `saathi.db` is empty — never use it for testing.
**Streaming:** Disabled. `call_deepseek_streaming()` is in deepseek.py but not wired in main.py — do not reconnect it.

---

## RAILWAY ENV VARS — ALL CONFIRMED SET

`TELEGRAM_BOT_TOKEN` ✅  `DEEPSEEK_API_KEY` ✅  `OPENAI_API_KEY` ✅  `GOOGLE_CLOUD_API_KEY` ✅  
`ELEVENLABS_API_KEY` ✅  `WEBHOOK_URL` ✅  `WEATHER_API_KEY` ✅  `CRICKET_API_KEY` ✅  
`NEWS_API_KEY` ✅  `TURSO_DATABASE_URL` ✅  `TURSO_AUTH_TOKEN` ✅

---

## PERMISSIONS CLAUDE NEEDS

**For debugging:**
- Read/write: `/Users/rishikar/saathi-bot/` (all Python files)
- Read: `CLAUDE.md`, `progress.md`
- Bash: `py_compile` checks, `grep`, `git status`, `git log`, `git diff`
- Cannot push git directly — Mac filesystem creates lock files the sandbox cannot delete. All `git commit` and `git push` must be run by Rishi in Terminal.

**For new module builds (in addition to above):**
- Read all existing module files before writing new code
- Write new `.py` files to `/Users/rishikar/saathi-bot/`
- Update `CLAUDE.md` session log and `progress.md` after each module

---

## SESSION START CHECKLIST

1. Read `/Users/rishikar/saathi-bot/CLAUDE.md` fully
2. Read `/Users/rishikar/saathi-bot/progress.md`
3. Run `git -C ~/saathi-bot log --oneline -5` to confirm git state
4. If the 2 local commits aren't pushed — give Rishi the push commands first (see above)
5. After push confirmed, verify fixes with a live Telegram test
6. Then proceed with Module 20
