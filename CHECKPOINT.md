# CHECKPOINT — 23 April 2026 (end of Opus 4.7 session, Batch 2 deployed)

**Read order for next session:** this file → CLAUDE.md → progress.md.

---

## TL;DR — state at end of this run

**Batch 2 is deployed and active on Railway.**

- Commit: `7be4eed` — "Batch 2: deferred senior inputs (grandkids + medicines)"
- Railway deployment ID: `659e9652`
- Pushed: Apr 22, 2026, 7:52 PM IST
- Status: Active, deployment successful, 1 replica running (us-west2, python@3.11.15)
- Confirmed from Railway dashboard export this session.

Earlier today's live test chatlog showed zero `PENDING_CAPTURE` log lines.
Most likely cause: the child-led onboarding that preceded the senior's
"my grand kids came today" message happened during or before Railway
finished building Batch 2 (push at 14:22 UTC, test at 14:26 UTC — 4-min
window against a typical 2–4 min Railway build). Under the old code,
step 7 "she will tell u" never wrote `pending_grandkids_names=1`. Later,
when the senior mentioned grandkids, the keyword matched but the gate
(`pending_grandkids_names=1`) was false, so no offer fired. Silent no-op.

**This is diagnosis, not confirmed. Next session's first task is to run
the live test cleanly from scratch and verify.**

---

## First task in next session — Batch 2 live test

### Step 1 — confirm Batch 2 is still the active deploy

Railway dashboard → Saathi service → Deployments. Top row should still
show "Batch 2: deferred senior inputs..." as Active.

If a newer deploy has replaced it (unlikely — nothing has been pushed
since), check what's changed before running the test.

### Step 2 — reset and redo child-led onboarding on clean Batch 2 code

From Rishi's Telegram:

1. `/adminreset`
2. Child-led onboarding, all 21 steps.
3. At **step 7 (grandkids)**: type `she will tell u` (or any deferral
   signal — "later", "dunno", "she'll tell you", etc.).
4. At **step 10 (medicines)**: type `pata nahi` (or any deferral
   signal — "don't know yet", "will fill later", etc.).
5. Complete all remaining steps. Step through the handoff to senior.

### Step 3 — DB check BEFORE testing the keyword trigger

From Railway shell (dashboard → Saathi service → shell icon, or
`railway run --service <service> bash`):

```sh
sqlite3 /data/saathi.db "SELECT id, pending_grandkids_names, pending_medicines, awaiting_pending_capture, pending_prompt_sent_at FROM users WHERE onboarding_complete=1 ORDER BY id DESC LIMIT 3;"
```

**Expected:** `pending_grandkids_names=1`, `pending_medicines=1`,
`awaiting_pending_capture=NULL`, `pending_prompt_sent_at=NULL`.

- If both pending flags are `1`: B2.2 (persistence) works. Move to step 4.
- If either is `0`: B2.2 is broken. Do NOT continue to step 4. Debug
  `onboarding.py` deferral detection for the flag that didn't set.

### Step 4 — keyword-triggered offer (senior flow)

From the senior's Telegram account, first message of a new session:

```
my grand kids came today
```

**Expected Railway log line:**
```
PENDING_CAPTURE | user_id=... | offered=grandkids
```

**Expected senior-facing reply:**
> "By the way — I don't know your grandchildren's names yet. If you'd
> like to share them, I'd love to hear — no pressure at all."

(Or the Hindi/Hinglish equivalent depending on the senior's language.)

- If fires: B2.3 keyword-trigger offer works. Move to step 5.
- If does not fire: check what _is_vulnerability / _is_grief /
  _is_short_disengaged / _is_mid_session_greeting returned in the log;
  one of the gates is eating the offer.

### Step 5 — capture

Senior replies with actual names:

```
Anish, Aman, Akshadha
```

**Expected log line:** `PENDING_CAPTURE | captured 3 name(s): Anish, Aman, Akshadha`

**Expected reply:** "Anish, Aman, and Akshadha — thank you for sharing.
I'll remember. 🙏"

**DB check after:**
```sh
sqlite3 /data/saathi.db "SELECT name, relationship FROM family_members WHERE user_id=<senior_id> AND relationship='grandchild';"
```
Should return 3 rows.

Also:
```sh
sqlite3 /data/saathi.db "SELECT id, pending_grandkids_names, awaiting_pending_capture FROM users WHERE id=<senior_id>;"
```
Should show `pending_grandkids_names=0` and `awaiting_pending_capture=NULL`.

### Step 6 — medicines offer (same pattern)

Senior: `my medicines ran out today`

Expected: `PENDING_CAPTURE | offered=medicines` log line + soft "by
the way — I don't know which medicines you take..." reply.

Senior replies with schedule (e.g. `metformin 8am and 8pm, atorvastatin at night`).

Expected: `medicines_raw` populated, `pending_medicines=0`, reminders
seeded in `medicine_reminders`.

---

## Known structural issues (flagged, NOT Batch 2's problem)

From the earlier-today live chatlog diagnosis, after Batch 2 didn't fire
the senior said names and medicines anyway via normal conversation.
Those were captured into `memories` (as prose strings) and NOT into the
structured `family_members` / `medicine_reminders` tables.

This is a gap in the conversational-intent capture layer — the system
understands what was said, but doesn't route structured data to
structured tables outside the Batch 2 offer/capture flow or the
onboarding flow.

**Not in scope for Batch 2 or Batch 3.** Worth raising after Batch 3
as a post-pilot cleanup item.

---

## Batch 3 — still deferred, not yet started

Handoff redesign, folds in:

- **Bug 1** — `main.py:1126-1132` staged handoff state machine
  unconditionally advances even when the senior's message is a real
  question (senior said "my grand kids came today" after handoff
  message 1; bot ignored it and sent handoff message 2 as if it were
  a state-machine answer).

- **Bug 2** — `main.py:1139-1142` handoff step 2 saves the senior's
  raw reply to `preferred_salutation` with no affirmation filtering.
  "Ma is good" becomes the stored address.

- **Bug 3** — handoff re-asks "what would you like to call me?" even
  when `bot_name` was already set at child-led step 2.

- **Bug 4** — "nothing" / "nothing" replies corrupted
  `preferred_salutation` and `bot_name` fields.

- Original Batch 3 scope: step-0 "whose phone" question, `setup_device`
  column, `pending_handoff_code` column, branched completion message
  (button vs code), staged push sequence with delays.

All four bugs in the live chatlog trace back to the handoff state
machine being too eager to advance. Batch 3 will replace the
unconditional state advance with a conditional one (message must
look like a state-machine answer before advancing; otherwise treat as
a normal senior message).

**Do NOT start Batch 3 until Batch 2 live test passes.** Rishi's
explicit constraint.

---

## Files changed in this run

None — diagnosis + CHECKPOINT/CLAUDE.md hygiene only.

Batch 2 code was pushed in the prior run (7be4eed).

---

## Pending commit before next session

```
cd ~/saathi-bot
git add CHECKPOINT.md CLAUDE.md
git commit -m "Session notes: Batch 2 deploy confirmed, testing plan"
git push origin main
```

(No code changes. Just hygiene files.)
