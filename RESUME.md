# SAATHI BOT — Session Resume Prompt

**Read this file FIRST. Then read CHECKPOINT.md, then CLAUDE.md, then progress.md.**

---

## What We Are Building

Saathi is a Telegram-based AI companion for urban Indian seniors aged 65+. The builder (Rishi) is non-technical. All explanations must be in plain English. The full product context is in `CLAUDE.md`.

Modules 0–18 are live on Railway. Database is SQLite on a Railway Volume mounted at `/data` (DB_PATH=/data/saathi.db). Turso/libsql is gone. No WhatsApp yet.

---

## Where We Left Off (22 April 2026)

This session was supposed to live-test three onboarding bugs from 20 Apr.
Before we ran them, Rishi noticed **his medicine reminders were firing in
Hindi despite picking English at onboarding.** Root-causing that surfaced
**two parser bugs** (language + city) that are pilot-blocking. Those
parser fixes are the new top priority.

**Read `CHECKPOINT.md` next — full state is there.** Short version:

| Item | Status | Priority |
|---|---|---|
| Bug 1 — medicine parser | ✅ LIVE-VERIFIED last session | done |
| Bug 2 — emergency contact name (`_extract_contact_name`) | ✅ CODE ALREADY LIVE on Railway (git tree clean, origin up to date — CHECKPOINT/RESUME were stale on 20 Apr) — NEEDS LIVE RETEST | P1 |
| Bug 3 — bare-code auto-join | ✅ CODE DEPLOYED — NEEDS LIVE TEST | P1 |
| **NEW — Language parser accepts "eng"/"hin"/"mix" as garbage → reminders default to Hindi** | ❌ NOT FIXED | **P0** |
| **NEW — City parser has no validation — "Mum"/"Del" stored raw, morning briefing literally says "in Mum"** | ❌ NOT FIXED | **P0** |
| **NEW — `_TEMPLATES["hinglish"]` in `reminders.py` is pure Hindi, not Hinglish** | ❌ NOT FIXED | **P0** |

---

## Pilot-scope decision made this session

**Pilot supports ONLY English, Hindi, and Hinglish.**

Any other language (Tamil, Bengali, Marathi, French, German, etc.) → polite refusal:
> *"My apologies — at present I can only converse in Hindi, English, or a mix of the two. Would you like to continue in any of those?"*

User must pick one of the three to advance onboarding.

---

## First-Moves Checklist for Next Session (Opus 4.7)

1. **Read `CHECKPOINT.md`** — full fix plan, file list, DB alias table, copy.
2. **Write the three P0 fixes:**
   - `onboarding.py` — rewrite `_parse_language` with short-form map + unsupported-language sentinel; add polite-refusal handler at language step (child-led step 4 line 797; self-setup language step line 874)
   - `onboarding.py` — rewrite city step to use shared `CITY_ALIASES` (step 3 in both paths)
   - `apis.py` — export shared `CITY_ALIASES` dict
   - `reminders.py` — rewrite `_TEMPLATES["hinglish"]` to real Hinglish; change `_DEFAULT_TEMPLATE` to English
3. **Push to Railway.** Wait ~60s for deploy.
4. **Correct Rishi's DB row** — easiest is `/adminreset` + redo onboarding.
5. **Retest Bug 2** — `yes. my wife ishween 9833192304` → completion says "Ishween".
6. **Test Bug 3** — wife sends bare family code → confirmation prompt → yes → registered.
7. **Verify medicine reminders now come in English.**
8. **Then move to Module 19** (end-to-end capability tests) and Module 20 (pilot prep).

---

## Two Tasks Scheduled After Parser Fixes + Bug Tests

### Task #A — Module 19 end-to-end capability tests
Protocol 1 (crisis keywords Hindi/English/Hinglish), Protocol 3 (financial/legal), Protocol 4 (inappropriate), voice input (Whisper + short-transcription path), music requests (specific artist + vague + stored-pref fallback), weather/cricket/news live-data injection, morning ritual, memory prompts, emergency keyword → family alert, `/familycode` + `/join` + relay, medicine reminder → 3-attempt escalation → family alert. Test protocol lives in `module_15_test_protocol.md`. Log each result ✅/❌. Any failures get a fix pass before Module 20.

### Task #B — Module 20 pilot prep
Deliverables: written onboarding instructions for 20 seniors + their adult children, Telegram bot link, setup walkthrough doc, family-code flow explainer, escalation behaviour explainer, privacy one-pager, feedback capture plan (Weekly Report has the data; also need a separate "what did you find confusing" channel), pilot duration decision (2 weeks? 4 weeks?), success metrics (session frequency, family engagement, protocol trigger rate, drop-off rate).

**Pilot doc must explicitly say:** "Saathi currently speaks English, Hindi, and Hinglish only." Don't let adult children promise Tamil/Bengali to parents.

---

## Known Corner Cases (deferred, not pilot-blocking)

1. **Emergency contact with no name** (e.g. `yes my wife`): `_extract_contact_name()` returns empty and the `or t` fallback stores the raw text. Fix option when it comes up: prompt "What is your wife's name?" instead of storing garbage.
2. **Two `_USER_CACHE` dicts** (from 19 Apr session): `main.py` unbounded + `database.py` 5-min TTL. Collapse as first post-pilot task.
3. Audit findings from 19 Apr: #3 EOL account_status caching in main; #6 same-turn opt-in + emergency VERIFY; #11/#12 cache hygiene; #15 weekly report skip logging.

---

## User Preferences (repeated for clarity)

- Prioritize intellectual honesty over politeness
- Challenge assumptions, critique first then solution
- No "that's a great point" / "you're absolutely right" filler
- Act as a peer, not subservient
- Default to short answers. Use bullets only when the content is genuinely listy.

---

## Files to Know

- `CHECKPOINT.md` — today's state, full fix plan, DB alias tables, copy
- `CLAUDE.md` — master context + session log
- `progress.md` — module status table
- `module_15_test_protocol.md` — test protocol for engagement + behavioral rules
- `~/.claude/CLAUDE.md` — global workflow rules (high-stakes decision protocol, clean session audit, session close)

---

## Files Changed This Session (22 April — nothing pushed, diagnosis only)

| File | Change |
|---|---|
| `CLAUDE.md` | Session log entry added for 22 Apr (parser bug discovery) |
| `CHECKPOINT.md` | Full 22 Apr handoff — fix plan, alias tables |
| `RESUME.md` | This file |

No code changes pushed. All three P0 fixes are pending — next session writes them.
