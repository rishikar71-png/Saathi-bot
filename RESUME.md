# SAATHI BOT — Session Resume Prompt

**Read this file FIRST. Then read CLAUDE.md and progress.md.**

---

## What We Are Building

Saathi is a Telegram-based AI companion for urban Indian seniors aged 65+. The builder (Rishi) is non-technical. All explanations must be in plain English. The full product context is in `CLAUDE.md`.

Modules 0–18 are live on Railway. Database is SQLite on a Railway Volume mounted at `/data` (DB_PATH=/data/saathi.db). Turso/libsql is gone. No WhatsApp yet.

---

## Where We Left Off (19 April 2026)

Last session: completed the self-setup Day 2 bridge (Option A family-code offer in completion message), fixed music preference fallback and emergency contact name parser surfaced from live testing, then ran a scoped audit and applied six fixes. All changes are local, unpushed to Railway at the time this resume was written — **verify deploy status before resuming work** (see first step below).

### Changes in the last commit chain (pre-push state)

| File | Change |
|---|---|
| `onboarding.py` | Self-setup Day 2 bridge (`SELF_SETUP_BRIDGE_*` constants, `_handle_self_setup_answer`, `handle_bridge_answer`, `maybe_resume_day2_bridge`). Step 12 emergency contact now strips leading "yes."/"sure"/"haan" affirmations, sets heartbeat_consent/heartbeat_enabled/escalation_opted_in/weekly_report_opt_in=1. `_parse_single_time` accepts dot separator (6.30 → 06:30) and has midnight/noon aliases ordered before "night". |
| `youtube.py` | `_is_all_filler()` heuristic — when every word in a music request is a generic filler (song/music/good/kuch/accha/etc.), falls back to stored `music_preferences`. Fixes "get me a good song to listen to" returning foreign songs instead of stored "old hindi songs". |
| `reminders.py` | Escalation JOIN broadened — `get_unacknowledged_for_escalation` now picks any family_member with `telegram_user_id IS NOT NULL`, prioritising `is_setup_user=1` → `role='family'` → `role='emergency'`. `_escalate_to_family` returns bool. `mark_family_alerted` only fires on successful send (no more silent retry loss). |
| `main.py` | `/join` success path now invalidates `_FAMILY_CACHE` and `_USER_CACHE` for the family member's telegram id. Bridge detect + deferred-resume hooks added. `_invalidate_user_cache` called after P3 active-flag writes (both trigger and session expiry paths). |
| `safety.py` | Escalation-skip logs upgraded INFO → WARNING with explicit skip reason. `_get_inactivity_candidates` adds `account_status='active'` filter to skip deceased users in the 30-day pre-deletion window. |

### Still open (deferred — not pilot-blocking)

- **Structural cache unification (#1/#13 from audit):** `main.py._USER_CACHE` (unbounded, manual invalidation) and `database.py._USER_CACHE` (5-min TTL, auto-invalidated by `update_user_fields`) don't know about each other. Every cache bug this month has traced back to this. **First post-pilot task.**
- Audit findings #3 (EOL account_status caching in main), #6 (same-turn opt-in + emergency — VERIFY), #11/#12 (general cache hygiene), #15 (weekly report skip logging). All low-probability. Ignore unless symptoms surface.

---

## First Two Tasks (scheduled)

### Task #3 — Module 19 end-to-end capability tests
Protocol 1 (crisis keywords Hindi/English/Hinglish), Protocol 3 (financial/legal), Protocol 4 (inappropriate), voice input (Whisper + short-transcription path), music requests (specific artist + vague + stored-pref fallback), weather/cricket/news live-data injection, morning ritual, memory prompts, emergency keyword → family alert, `/familycode` + `/join` + relay, medicine reminder → 3-attempt escalation → family alert. Test protocol lives in `module_15_test_protocol.md`. Log each result ✅/❌. Any failures get a fix pass before Module 20.

### Task #4 — Module 20 pilot prep
Deliverables: written onboarding instructions for 20 seniors + their adult children, Telegram bot link, setup walkthrough doc, family-code flow explainer, escalation behaviour explainer, privacy one-pager, feedback capture plan (Weekly Report has the data; also need a separate "what did you find confusing" channel), pilot duration decision (2 weeks? 4 weeks?), success metrics (session frequency, family engagement, protocol trigger rate, drop-off rate).

---

## First-Moves Checklist for Next Session

1. **Confirm the unpushed commits shipped.** Ask Rishi or check Railway log for the latest deploy. If the 19 Apr fixes are not live, pushing is his step from terminal — do not block on this, continue with testing plan meanwhile.
2. **Read CLAUDE.md session log** (last entry is 19 Apr cont.) — full context is there, don't re-derive.
3. **Do NOT re-read** `database.py`, `main.py`, `onboarding.py` unless a specific question requires it. They are very large; pay the token cost only when needed.
4. **Start Task #3** by walking through `module_15_test_protocol.md` and deciding which tests to re-run after this cycle of fixes. Ask Rishi before running anything that requires him to message the bot from a second Telegram account (family member tests).
5. If Task #3 surfaces bugs, fix them in a single commit per bug, test again, then proceed to Task #4.

---

## User Preferences (repeated for clarity)

- Prioritize intellectual honesty over politeness
- Challenge assumptions, critique first then solution
- No "that's a great point" / "you're absolutely right" filler
- Act as a peer, not subservient
- Default to short answers. Use bullets only when the content is genuinely listy.

---

## Files to Know

- `CLAUDE.md` — master context + session log
- `progress.md` — module status table
- `module_15_test_protocol.md` — test protocol for engagement + behavioral rules
- `~/.claude/CLAUDE.md` — global workflow rules (high-stakes decision protocol, clean session audit, session close)
