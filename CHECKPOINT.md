# CHECKPOINT — 23 April 2026 (end of Opus 4.7 continuation session, part 2)

**Read order for next session:** this file → CLAUDE.md → progress.md.

---

## TL;DR — state at end of this run

- **Batch 3a** (4 fixes) is already on `origin/main` at commit `ca920e4`.
  Railway has deployed it.
- **Fix A** (one-line-ish patch to `main.py`) is edited in the working
  tree but NOT yet pushed. Reason: review of the Batch 3a handoff collapse
  revealed that the senior's first-utterance content was being dropped
  entirely (not saved to `messages`, not appended to session history)
  because the handoff block `return`s before those writes happen.
  Fix A queues `save_message_record("in", text)`, `_live_session_append`
  for user + assistant, and `save_session_turn` writes before the return.
- **CHECKPOINT Step 4 in the previous version was wrong** — it expected
  two replies on turn 1 (soft greeting + pending-capture offer). The code
  short-circuits after the soft greeting, so only one reply fires on turn 1.
  Offer fires on turn 2. Test plan below is corrected.

---

## What changed in this run

1. `main.py` — handoff block (~line 1210–1225), post-reply persistence added.
   See comment block `Fix A (23 Apr 2026) — see Batch 3a live-test critique.`

That's the only code change. `py_compile` clean. All symbols in scope
(`_db_queue` defined line 260, `_live_session_append` line 223,
`save_message_record` + `save_session_turn` imported lines 20–21,
`text` + `input_type` are `_run_pipeline` parameters).

---

## Push command (Fix A)

```
cd ~/saathi-bot
git add main.py CHECKPOINT.md
git commit -m "Fix A: persist senior's first utterance in handoff collapse (Batch 3a follow-up)"
git push origin main
```

Wait 2–4 min for Railway rebuild, then run the corrected test below.

---

## Corrected live-test plan (supersedes the old CHECKPOINT Step 4)

### Step 1 — SKIP (no longer needed)

Batch 3a already pushed. Fix A push command is above.

### Step 2 — clean-slate child-led onboarding

From Rishi's Telegram:

1. `/adminreset`
2. Run child-led onboarding end-to-end.
   - **Step 2** preferred address = `Ma`
   - **Step 7** defer grandkids with `she will tell u`
   - **Step 10** defer medicines with `pata nahi`
   - **Step 16** bot name = `Sage`
3. Expected completion message includes the two-deferral block:
   *"A couple of small things — you weren't sure about Ma's grandchildren's
   names or medicines earlier. No rush at all. I'll gently ask Ma about
   them once we've started chatting…"*

### Step 3 — DB check BEFORE senior test

Railway dashboard → shell:

```sh
python3 -c "import sqlite3; c=sqlite3.connect('/data/saathi.db'); c.row_factory=sqlite3.Row; r=c.execute('SELECT user_id, name, preferred_salutation, bot_name, pending_grandkids_names, pending_medicines, awaiting_pending_capture, handoff_step FROM users WHERE onboarding_complete=1 ORDER BY user_id DESC LIMIT 3').fetchall(); [print(dict(x)) for x in r]"
```

**Expected:** `preferred_salutation='Ma'`, `bot_name='Sage'`,
`pending_grandkids_names=1`, `pending_medicines=1`,
`awaiting_pending_capture=NULL`, `handoff_step=0`.

### Step 4 — senior's FIRST message (non-trigger opening)

From senior's Telegram:

```
hello
```

(Anything that is NOT a grandkids/medicines capture trigger.)

**Expected Railway log lines:**
```
OUT | user_id=... | type=handoff | collapsed_to_step4 | prior_step=0
```

**Expected senior-facing reply (ONE):**
*"Namaste. Rishi asked me to be in touch. I'm Sage — I'm here whenever
you'd like to talk."*

**DB check after:**

```sh
python3 -c "import sqlite3; c=sqlite3.connect('/data/saathi.db'); c.row_factory=sqlite3.Row; r=c.execute('SELECT user_id, handoff_step, awaiting_pending_capture FROM users ORDER BY user_id DESC LIMIT 1').fetchone(); print(dict(r))"
```

Expect: `handoff_step=4`, `awaiting_pending_capture=NULL`.

**Fix A verification** — inbound message is persisted:

```sh
python3 -c "import sqlite3; c=sqlite3.connect('/data/saathi.db'); c.row_factory=sqlite3.Row; [print(dict(x)) for x in c.execute('SELECT direction, content FROM messages ORDER BY id DESC LIMIT 5').fetchall()]"
```

Expect to see both `direction='in' content='hello'` AND
`direction='out' content='Namaste...'`. If the inbound row is missing,
Fix A didn't deploy.

### Step 4b — senior's SECOND message (triggers grandkids offer)

```
my grand kids came today
```

**Expected log lines:**
```
PENDING_CAPTURE | user_id=... | offered | kind=grandkids | lang=...
```

**Expected reply:**
*"By the way — I don't know your grandchildren's names yet. If you'd
like to share them, I'd love to hear — no pressure at all."*

DB after:
- `handoff_step=4` (unchanged)
- `awaiting_pending_capture='grandkids'`

If Fix 1 (Batch 2 keyword patch) works: the offer fires on the spaced
form `grand kids`.

### Step 5 — capture names

Senior:
```
Anish, Aman, Akshadha
```

**Expected log:** `PENDING_CAPTURE | user_id=... | kind=grandkids | captured 3 name(s): Anish, Aman, Akshadha`

**Expected reply:** *"Anish, Aman, and Akshadha — thank you for sharing. I'll remember. 🙏"*

DB:
```sh
python3 -c "import sqlite3; c=sqlite3.connect('/data/saathi.db'); [print(dict(x)) for x in c.execute('SELECT name, relationship FROM family_members WHERE relationship=\"grandchild\" ORDER BY id DESC LIMIT 5').fetchall()]"
```

Should return 3 rows. `pending_grandkids_names` should now be `0`.

### Step 6 — medicines path

- Senior: `my medicines ran out today` → expect medicines offer.
- Senior: `metformin 8am and 8pm, atorvastatin at night` → expect
  `medicines_raw` populated, `pending_medicines=0`, reminders seeded.

### Step 7 — address consistency (Fix 2 test)

From any point post-handoff, send any message. Expect DeepSeek uses
"Ma" consistently — never "Durga", never "Durga Ji", never "Durga Ma".

If DeepSeek slips: check system prompt order:
```
grep -n "ABSOLUTE" deepseek.py
```
Both `language_lock` and `address_lock` must be prepended before
`base_prompt`.

---

## If something fails

1. **Handoff keeps advancing through 1/2/3** → grep `main.py` for the
   old state machine: `grep -n "handoff_step = user_row" main.py`.
   Should show one occurrence in the handoff block.
2. **Pending-capture silent on "grand kids"** → check Railway logs for
   any `PENDING_CAPTURE` lines. If none fire after step 4b, deploy may
   not have picked up `pending_capture.py`. Force redeploy.
3. **DeepSeek still says "Durga"** → `grep -n "ABSOLUTE ADDRESS RULE" deepseek.py`.
   If the string exists but never reaches the prompt, the assembly at
   the end of `_build_system_prompt` is still broken.
4. **Fix A not working (step 4 inbound row missing)** → confirm
   `main.py` at lines ~1210–1225 has the `_db_queue(save_message_record, ...)`
   block. If working tree clean but DB still missing the row, Railway
   deploy stale — force redeploy.
5. **Completion message missing the deferral block** →
   `grep -n "deferral_note" onboarding.py` should show it rendered in
   the return string.

---

## Deferred / post-pilot items

- Conversational-intent capture → structured tables. Names said in
  normal conversation still go to `memories`, not `family_members`.
- News geo-filter gap (Irish/Moldovan/etc. articles slipping through).
- The unified cache (merge the two `_USER_CACHE` dicts across main.py
  and database.py) — first post-pilot task.

---

## Two design questions resolved this session (23 Apr 2026)

### Q1 — Move bot-name choice from child onboarding to senior's first session?

**Decision: no, leave it in child-led onboarding (step 16).**

Arguments against moving it:
1. First-contact rule mandates "no question" on the soft greeting.
2. The bot must call itself *something* in the soft greeting — default
   required regardless.
3. Seniors deflect naming decisions — partial or joking values would
   end up in the field.

Fallback for agency: senior can say "call yourself X" in normal chat
and DeepSeek handles it.

### Q2 — "whose phone" + button completion + hand-off = next batch?

**Yes — Batch 4.** Work includes:
- Step-0 question: "Whose phone is this — yours or your parent's?"
- New DB columns: `setup_device` (`self_phone` / `parent_phone`),
  `pending_handoff_code`
- Branched completion UI (inline keyboard "I'm done" button for same-phone
  path; shareable code for different-phone path)
- First-message device detection

Ship after Batch 3a + Fix A clear live test. Non-trivial — full session.

---

## Third design question resolved this session

### Q3 — What to do when senior's first utterance has real content?

**Decision: Fix A (minimal) — persist, but still short-circuit to soft greeting.**

Rejected alternative (Fix B): fall through the pipeline so the senior's
first utterance gets engaged with in full. Rejected because it violates
the First-Contact Behavioral Rule (no enthusiasm, silence-friendly,
presence not response). A substantive DeepSeek reply on top of the soft
greeting would feel overeager.

Fix A trade-off: senior's content is captured in DB + session history,
so turn 2+ DeepSeek calls can reference it. But turn 1 itself only
produces the soft greeting. This is intentional.

---

## Pilot blockers remaining

None, if Fix A clears live test.

If live test passes clean: resume Module 19 end-to-end capability tests
and Module 20 pilot prep.
