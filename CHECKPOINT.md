# CHECKPOINT — Resume 23 Apr 2026 (cont. 3)

**Read order for next session:** this file → CLAUDE.md → progress.md.

---

## State at session close

**Bundled patch covers today's earlier 3 fixes (pending_capture affirmation strip, MEDICINE STATUS block, RULE 13 capability limits) PLUS today's parser overhaul (time parser rewrite, batch-ASK flow, step 0 prompt copy, MEDICINE STATUS time-format rule). NOT YET ON RAILWAY. Packaged as a single `git am` patch.**

Receipts:
- `py_compile` — `reminders.py pending_capture.py onboarding.py deepseek.py main.py database.py` → `COMPILE_OK`.
- Unit tests — `/sessions/serene-fervent-shannon/mnt/outputs/test_parser.py` → 65/65 passing (52 `_normalize_time` cases + 13 `resolve_ambiguous_hour` cases).
- `git diff --stat` — 7 files, ~+896 / −282.

### What was fixed in this bundle

**Fix #A — pending_capture.py: leading affirmation strip** (from earlier today)
- `_strip_leading_affirmation` applied to name extraction and medicines capture.
- Stops "yes. anish and aman" → `["Yes","Anish","Aman"]`.

**Fix #B — database.py + deepseek.py: MEDICINE STATUS injection** (from earlier today)
- `get_today_medicine_status(user_id)` → per-row `sent_today`/`acked_today`/`attempt_count_today`/`family_alerted_today`.
- SQL uses `date(col, '+5 hours', '+30 minutes')` — **separate modifier args** (combined form returns NULL).
- `deepseek._format_medicine_status_block` injected after FAMILY in the system prompt.

**Fix #C — deepseek.py: RULE 13 CAPABILITY LIMITS** (from earlier today)
- Hardcoded rule: CAN chat/remember/voice/music/weather/news/cricket. CANNOT create/schedule/change/cancel reminders.
- Scripted response for "set a reminder" → "I can't set reminders myself — your family does that."

**Fix #D — reminders.py: time parser rewrite (new, today's main work)**
- Pilot-blocker root cause: old parser defaulted bare hours to AM → "1.30" stored as 01:30 (middle of the night). Rewritten with the locked Option A rule set:
  - bare 1–5 → PM (13:00–17:00) — Indian medicine convention
  - bare 6–11 → ambiguous/ASK (placeholder row, `is_active=0`)
  - bare 12 → noon
  - explicit AM/PM → honored verbatim
  - 24-hour forms (13:30, 21:00, 0800 compact) → honored
  - Hindi period words (subah/dopahar/shaam/raat) + English ("morning"/"afternoon"/"evening"/"night") → disambiguate
  - Meal-context words (breakfast/lunch/dinner) → disambiguate digits (e.g. "after breakfast 8" = 08:00; "before dinner 7" = 19:00)
  - Meal phrases alone (no digit) → scripted times ("after dinner" = 20:00, "khali pet" = 07:30)
- **Word-boundary fix:** short tokens ("am", "pm") matched with `\b...\b` so they don't fire inside "shaam"/"naam"/"subah". Pre-fix: `_detect_period_qualifier("shaam 7")` wrongly returned `"am"` → 07:00 AM. Post-fix → `"night"` → 19:00.
- New structured return: `{time_24h, ambiguous, confidence, source, reason}`. Ambiguous cases still emit `time_24h=HH:MM` (bare AM form) so placeholder rows have a value to store.
- New helpers: `add_reminder_structured(user_id, name, time_str)` → `(row_id, parse_result)`, `resolve_reminder_time(reminder_id, hhmm)`, `get_ambiguous_reminders(user_id)`, `resolve_ambiguous_hour(h, m, reply)`.
- `seed_reminders_from_raw` now returns `{seeded_active, seeded_ambiguous, unparseable, pairs_total}` where `seeded_ambiguous` is a list of `{id, medicine_name, raw_time, bare_hhmm}`.

**Fix #E — pending_capture.py: batch-ASK flow (new, today)**
- Medicines branch of `capture_response` consumes the new report and either: saves high-confidence rows silently, or builds a grouped follow-up for ambiguous rows ("For *Pan D* at 9:00 — morning or night?").
- New sub-state `awaiting_pending_capture='medicines_clarify'` routed by `_handle_ambiguity_reply`; supports global answers ("all morning", "sab raat") and per-medicine answers (comma/and/aur-separated).
- Acknowledgements rendered in 12-hour AM/PM via `_humanise('21:30')` → `"9:30 PM"`.

**Fix #F — main.py: medicines_clarify routing (new, today)**
- Single-line change: `if _awaiting in ("grandkids", "medicines", "medicines_clarify"):` keeps the new sub-state flowing through the existing `capture_response` infrastructure.

**Fix #G — onboarding.py: step 0 prompt copy (new, today)**
- Old INTRO_MESSAGE asked only for name → setup person's phone stayed empty in `family_members` row.
- New copy: "First — what is *your* name and phone number? … Just reply like: *Priya 9876543210*".
- `_parse_setup_person` already handled the split; `save_setup_person(user_id, name, phone)` already accepted phone param.

**Fix #H — deepseek.py: TIME FORMAT reply rule (new, today)**
- Added inside RULE 13: when speaking a `schedule_time` from MEDICINE STATUS (stored 24h), ALWAYS convert to 12-hour AM/PM ("13:30" → "1:30 PM"). Never say bare "1:30" — senior cannot tell morning from night and may miss a dose.
- Also: if today's time has already passed, say so explicitly ("Today's 1:30 PM has already passed — I'll remind you again tomorrow at 1:30 PM.").

### Verification discipline rules

V1–V7 live at the top of `/Users/rishikar/saathi-bot/CLAUDE.md`. These are the contract for every future session.

---

## Push command

Patch file: `/Users/rishikar/AI Projects/Saathi Bot/session_23apr_bundle.patch`

On the Mac:

```bash
cp "/Users/rishikar/AI Projects/Saathi Bot/session_23apr_bundle.patch" ~/saathi-bot/
cd ~/saathi-bot
git am session_23apr_bundle.patch
git push origin main
```

Wait 2–4 min for Railway to deploy. Then `/adminreset` and run the live test plan below.

---

## Live test plan (after deploy)

### Test 1 — step 0 prompt collects name + phone
1. `/adminreset` → `/start` → select family setup.
2. Step 0 should now ask for name *and* phone, with example "Priya 9876543210".
3. Reply `rishi 9819787322`.
4. Expected: `family_members` row saved with `name='Rishi'`, `phone='9819787322'`, `relationship='setup'`.

### Test 2 — parser handles "1.30" correctly (the original pilot-blocker)
1. At step 9 (medications), reply: `Pan D at 1.30`.
2. Expected: `medicine_reminders` row with `schedule_time='13:30'` (NOT `01:30`).
3. If current IST is past 13:30 when senior later asks "did you remind me today?", DeepSeek should say: "Today's 1:30 PM has already passed — I'll remind you again tomorrow."

### Test 3 — batch-ASK flow for ambiguous times
1. Medication reply: `Pan D at 9, Thyronorm at 9, Plavix at 8 am`.
2. Expected: 1 active reminder (`Plavix 08:00`), 2 placeholder rows (`Pan D`/`Thyronorm` at bare `09:00` with `is_active=0`).
3. Onboarding should emit a clarifying follow-up: "For *Pan D* and *Thyronorm* at 9:00 — morning or night?"
4. Reply: `morning`.
5. Expected: both placeholder rows update to `schedule_time='09:00'`, `is_active=1`.
6. Confirmation should read "Got it — Pan D and Thyronorm at 9:00 AM." (12-hour format).

### Test 4 — word-boundary fix holds
1. Medication reply: `BP pill at shaam 7`.
2. Expected: `schedule_time='19:00'` (NOT `07:00` — this is the regression the word-boundary fix prevents).

### Test 5 — MEDICINE STATUS block (Fix #B)
1. With at least one reminder scheduled, ask "did you remind me today?" before the time fires.
2. Expected: factual answer ("Today's 9:00 AM for Pan D is coming up — not yet sent."). No fabricated history.

### Test 6 — RULE 13 capability limits (Fix #C)
1. Ask "can you set a reminder for me?".
2. Expected: "I can't set reminders myself — your family does that. I'll let them know you asked." (or Hindi variant if language='hindi').

---

## Open items deferred to next session

- **Hindi numerals ("ek baje", "do baje", "teen baje")** — not parsed. Pilot-scope: skip. V1.5 add.
- **Conversational-intent → structured tables** — names/medicines mentioned in normal chat still land in `memories` as prose, not in `family_members`/`medicine_reminders`. Post-Batch-3 cleanup.
- **Batch 3 handoff redesign** — staged-state corruption of `preferred_salutation` and `bot_name` was solved by collapsing handoff to a single message. Still needs verification in a clean live child-led run.
- **Batch 4 — whose-phone + button completion** — scoped in the 22 Apr session log. Blocks on Batch 3 live verification.
