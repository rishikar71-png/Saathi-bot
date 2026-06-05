# CHECKPOINT — Resume after 4 June 2026 session

**Read order for next session:** this file → CLAUDE.md (V1–V10 + latest session log) → POST_PILOT_TRACKER.md → progress.md.

---

## Headline: PILOT LIVE — backup + diary verified, now monitoring

**4 June: 18 invites sent. 5 June: backup live-verified, nightly diary wired + verified, name validator shipped.** Reality check from the DB snapshot: **only 1 of 17 real invitees has actually onboarded** (Gati). The watch-and-respond phase is the job now — chase onboarding + triage real-user bugs.

**HEAD at 5 June close:** `fc8216c` (name validator) on top of `db8702d` (diary job) on top of `7ffe965` (4 June docs). Plus an unpushed doc commit (this file + CLAUDE.md + POST_PILOT_TRACKER.md). Push:
```bash
cd ~/saathi-bot && rm -f .git/index.lock && git add -A && git commit -m "Session close 5 June: backup verified, diary wired+verified, name validator, tracker" && git push origin main
```
Also: revert Railway `DIARY_TIME_IST` to `00:30` (or delete it — code default is 00:30).

---

## HEAD state at session close (4 June 2026)

All of today's work is committed AND pushed (clean tree at session start of this doc write):

```
4899ca5  Add daily DB backup job (free offsite Telegram dump)
a511919  Protocol 3: tighten authority-pressure pattern (drop travel-agent misfire)
c334240  Protocol 3: split detection from intervention (fix 'property' false positive)
02d8823  23 May 2026 session close (Patch 9 docs)
```

**One uncommitted change remains: the session-close doc updates** (this file + CLAUDE.md + progress.md + POST_PILOT_TRACKER.md, and the 31 May→4 June date correction in protocol3.py/main.py comments). Push them:
```bash
cd ~/saathi-bot && git add -A && git commit -m "Session close 4 June: Protocol 3 fix + backup job + pilot ship; date corrections" && git push origin main
```

---

## What shipped today

### 1. Protocol 3 false-positive fix — DONE, live, verified
- **Incident:** senior said "the tenant in one of my rented property is leaving" → Protocol 3 fired the "see a CA/lawyer" response. Root cause: the flat `FINANCIAL_KEYWORDS` list fired the heaviest response on the *mere mention* of a financial noun ("property").
- **Fix (V6 external review — GPT + Gemini both endorsed): split detection from intervention.** `protocol3.py` now has `CONTEXT_KEYWORDS` (bare nouns → detect only, NO fire, NO 60-min flag), `CRISIS_KEYWORDS` (fraud/scam/ghotala/froad/thag — solo-fire), and expanded Layer B buckets (relational pressure, liquidation, coerced signing, borrowing, solicitation, financial-authority pressure) with bounded proximity, no bare `will`/`sign`. `check_protocol3` now returns `Protocol3Result(response, context_detected, reason)`; the one caller (main.py) uses `.response`.
- **Two deviations from reviewer consensus (Rishi approved):** (a) bare `cheating`/`cheat` NOT solo-fire (marital/cards/exam ambiguity) — routed through Layer B paired with money; (b) bare `dhoka`/`dhokha` NOT solo-fire (general betrayal). Both trivially reversible (strings in `CRISIS_KEYWORDS`).
- **Verified:** py_compile clean; fixture 45/45 (`test_protocol3_split.py` in outputs); LIVE on Telegram — tenant sentence → normal reply, business news → normal, "travel agent wants me to sign" → normal, "transfer my flat to my son?" → P3 fires.
- **A2 deferred:** the `context_detected` flag is logged, NOT fed to DeepSeek (no behavioural spec yet). Memo: `AI Projects/Saathi Bot/Saathi_Protocol3_FalsePositive_Briefing_May31.md` (filename is misdated — actually 4 June).

### 2. Daily DB backup job — built + pushed, NOT yet live-verified
- Railway native Volume backups require the **Pro plan**; Hobby (current) does not have them. (This corrects the 15 May note that claimed Hobby had native backups.) Chosen path: free offsite Telegram dump.
- `main.py`: `_make_db_snapshot` (sqlite online backup API — WAL-safe) + `backup_job` (self-gated, fires on first tick at/after `BACKUP_TIME_IST`, default 03:30 IST; sends `saathi.db` snapshot to admin chat `8711370451`; overridable via `BACKUP_TIME_IST` / `BACKUP_CHAT_ID`). Registered on JobQueue (60s).
- **Verified:** py_compile clean; snapshot fixture 6/6 (`test_backup_snapshot.py` — WAL + concurrent writer, integrity check, content match). **NOT live-verified** — `bot.send_document` + the `/data` path inside the job have not run for real.
- **FIRST TASK NEXT SESSION:** confirm a `.db` file actually arrives in the admin Telegram chat — either wait for the 03:30 IST run, or force it (set Railway `BACKUP_TIME_IST` a couple minutes ahead, watch for the document + `BACKUP | sent daily snapshot` log, then revert).

### 3. Pilot invite tracker
- Cowork artifact **"saathi-pilot-invite-tracker"** (editable, add/delete rows, all fields default Unknown, auto-suggests message per Setup+Telegram, localStorage-persisted) + a static `AI Projects/Saathi Bot/Saathi-Pilot-Send-Tracker.md`.
- Use it to log each invitee and fill language/Telegram/sensitivities as you learn them.

### 4. Also verified
- **Bombay leak Test B (Patch 9):** PASS — `hello` mid-session → "Hello again, Rishi Ji" soft fallback, no fabricated noun.
- Railway shows `4899ca5` Active.

---

## KNOWN LIMITATION shipped with the pilot (not a blocker, decide later)

Family-facing alerts (medicine-miss escalation, silence detection, weekly report) only reach a family member who has **joined via the family code** (which links their Telegram). Self-setup users enter a phone *number* for emergency contact, and a phone number cannot receive Telegram alerts. So for the self-setup pilot seniors, family alerting is **inert** until a family member joins. The senior's companion experience is fully functional regardless. Rajneesh (family-led) is linked, so his alerts work. (Logged in POST_PILOT_TRACKER.)

---

## kaea brand work — DEFERRED

No "kaea brand spec" doc exists; it was only ever a one-line item. The 6 user-facing deliverables already carry kaea framing (done 13 May). Decision: drop for now; a proper kaea identity doc is its own focused session when kaea.ai needs it.

---

## Next session — priority order

1. **Pilot monitoring (the main job).** Only 1 of 17 real invitees has onboarded. Two fronts: (a) **onboarding conversion** — why have 16 not started? Likely Telegram-install friction or the invite didn't land; chase via the right WhatsApp message (with-Telegram vs no-Telegram). (b) **real-user bug triage** — watch Railway logs + admin Telegram; fix anything pilot-breaking immediately.
2. **Run `pilot_status.py` on the latest backup `.db`** for a fast read (onboarded count, per-user message volume + last-active, family-alert reachability, medicine streaks, protocol triggers, late-night usage). Script lives in the outputs scratch dir; point it at any snapshot: `python3 pilot_status.py <snapshot.db>`.
3. **Fill the tracker** as invitees reply (language / Telegram / status).
4. **Decide the family-escalation limitation** — self-setup users' phone-number contacts can't get Telegram alerts. Want their families to `/join` via code so alerts work?
5. **Restore drill** (take up shortly) — download a snapshot, load it, confirm the bot boots + integrity_check ok. An untested backup is not a backup.
6. POST_PILOT_TRACKER items only if surfaced as pilot-blocking by real users.

## Pilot-ops cadence (agreed 5 June)

- **Daily (~2 min):** glance at admin Telegram for the 03:30 backup `.db` (confirms the bot is alive + DB intact) and for any Protocol-1/emergency alert. Skim Railway logs for `ERROR`/`failed`.
- **Every 2–3 days (~10 min):** send the newest backup to this session; `pilot_status.py` readout → check new onboards, who's gone quiet, any protocol triggers from real users, late-night patterns.
- **Weekly:** review the Sunday family-report path (once a real family member has `/join`ed); re-prioritize POST_PILOT_TRACKER against real signal; chase un-onboarded invitees.
- **Immediately, any time:** a Protocol-1 trigger from a real user, a crash loop in logs, or a senior reporting the bot is broken → triage now, don't wait for the cadence.

---

## Verification discipline reminders (CLAUDE.md V1–V10)
- V6: external review (GPT/Gemini) when touching Protocol 1/3, medicine reminders, family escalation, DB schema. (Used for the Protocol 3 fix this session.)
- V8: pick patch-or-commit-in-place at the start. This session used commit-in-place (Rishi pushes from terminal).
- Sandbox cannot push (no creds) and cannot delete in the mount without `allow_cowork_file_delete`.

---

## Cleanup notes
- Comment dates in `protocol3.py` / `main.py` were mislabelled "31 May" mid-session; corrected to "4 June 2026" in the session-close commit. The memo filename `Saathi_Protocol3_FalsePositive_Briefing_May31.md` is still misdated (cosmetic — left as-is).
- Throwaway test fixtures (`test_protocol3_split.py`, `test_backup_snapshot.py`) live in the outputs scratch dir, not the repo.
