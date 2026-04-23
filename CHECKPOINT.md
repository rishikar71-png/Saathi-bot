# CHECKPOINT — 23 April 2026 (end of Opus 4.7 continuation session)

**Read order for next session:** this file → CLAUDE.md → progress.md.

---

## TL;DR — state at end of this run

Four fixes bundled and ready to deploy. All five touched files compile clean;
address-lock + completion-copy rendering verified.

- **Fix 1 (Batch 2 keyword patch)** — `pending_capture.py`: added spaced
  forms so "my grand kids came today" / "grand son" / "grand children"
  trigger the offer. The original keyword list only matched compound
  forms like "grandkid" / "grandchild". Yesterday's live test phrase
  was a space-form, which silently missed the offer.
- **Fix 2 (Addressing)** — `rituals.py` + `deepseek.py`:
    - `rituals._address(name, salutation)` rewritten to Batch 1c
      semantics — salutation is the full display string (returned
      verbatim), else `{name} Ji` fallback, else "aap". The old
      `f"{name} {sal}".strip()` concatenation produced broken
      addresses like "Durga Ma".
    - `deepseek._build_system_prompt` gains an `address_lock` block,
      prepended right after `language_lock` and before the base prompt.
      Two branches: if salutation ≠ name, it instructs DeepSeek to use
      the exact salutation and explicitly bans appending "ji"/"saab" to
      the bare first name; if salutation is empty, it instructs DeepSeek
      to use `{name} Ji`.
- **Fix 3 (Completion copy)** — `onboarding.py`: `_build_completion_message`
  now covers both deferrals (grandkids and medicines). Four branches:
  both-deferred / meds-only / gk-only / none. Old code mentioned only
  medicines even when grandkids had also been deferred.
- **Fix 4 (Handoff redesign — Batch 3)** — `main.py`: the staged 4-step
  handoff state machine (handoff_step 0 → 1 → 2 → 3) is collapsed to a
  single soft first-contact message for child-led setup. Reasons:
    - `preferred_salutation` was already set at onboarding step 2
    - `bot_name` was already set at onboarding step 16
    - The old state machine advanced unconditionally on the senior's
      reply, so real first-contact messages ("my grand kids came today")
      were ignored in favour of advancing to the next handoff question.
    - Raw replies like "Ma is good" and "nothing" were written verbatim
      to `preferred_salutation` / `bot_name`, corrupting both fields.
  New design: on any `handoff_step < 4`, send `get_handoff_message(0)`
  once (with confusion-branch check), mark `handoff_step=4`, drop into
  normal DeepSeek conversation. If the senior wants to change their
  address or bot name later, DeepSeek handles it in ordinary chat —
  no state machine needed.

---

## Files changed in this run

1. `pending_capture.py` — spaced keyword variants
2. `rituals.py` — `_address()` salutation semantics (line ~437)
3. `deepseek.py` — `address_lock` block + prompt assembly (line ~454)
4. `onboarding.py` — `_build_completion_message` two-deferral copy (line ~1333)
5. `main.py` — handoff block collapsed (line ~1153)
6. `CHECKPOINT.md` + `CLAUDE.md` — session notes

All five .py files pass `python3 -m py_compile`. Address-lock live
tests pass for four address scenarios; completion-copy tests pass for
all four deferral combinations.

---

## What to do in the next session

### Step 1 — push and deploy

```
cd ~/saathi-bot
git add pending_capture.py rituals.py deepseek.py onboarding.py main.py CHECKPOINT.md CLAUDE.md
git commit -m "Batch 2 keyword patch + addressing fix + completion copy + Batch 3 handoff redesign"
git push origin main
```

Railway should auto-deploy. Confirm via dashboard. Expected build time
2–4 min.

### Step 2 — clean-slate live test (child-led)

From Rishi's Telegram:

1. `/adminreset`
2. Run child-led onboarding end-to-end. At **step 2**, set preferred
   address to `Ma`. At **step 7**, defer grandkids with `she will tell
   u`. At **step 10**, defer medicines with `pata nahi`. Pick a bot
   name at step 16 (e.g. `Sage`).
3. Expected completion message should contain:
   *"A couple of small things — you weren't sure about Ma's
   grandchildren's names or medicines earlier. No rush at all. I'll
   gently ask Ma about them once we've started chatting…"*
4. Expected Railway log line: `HANDOFF | stage=staged_sent`.

### Step 3 — DB check BEFORE senior test

Railway dashboard → shell:

```sh
python3 -c "import sqlite3; c=sqlite3.connect('/data/saathi.db'); c.row_factory=sqlite3.Row; r=c.execute('SELECT user_id, name, preferred_salutation, bot_name, pending_grandkids_names, pending_medicines, awaiting_pending_capture, handoff_step FROM users WHERE onboarding_complete=1 ORDER BY user_id DESC LIMIT 3').fetchall(); [print(dict(x)) for x in r]"
```

**Expected:** `preferred_salutation='Ma'`, `bot_name='Sage'`,
`pending_grandkids_names=1`, `pending_medicines=1`,
`awaiting_pending_capture=NULL`, `handoff_step=0` (not yet advanced —
senior hasn't sent anything yet).

### Step 4 — senior's first message → soft greeting + auto-complete

From senior's Telegram (first message of new session):

```
my grand kids came today
```

**Expected Railway log lines (in order):**
```
OUT | user_id=... | type=handoff | collapsed_to_step4 | prior_step=0
PENDING_CAPTURE | user_id=... | offered | kind=grandkids | lang=...
```

**Expected senior-facing replies (two):**

1. Soft greeting: *"Namaste. Rishi asked me to be in touch. I'm Sage
   — I'm here whenever you'd like to talk."*
2. Pending-capture offer: *"By the way — I don't know your
   grandchildren's names yet. If you'd like to share them, I'd love
   to hear — no pressure at all."*

If Fix 1 (keyword patch) works: the offer fires on "grand kids"
(spaced). If Fix 4 (handoff redesign) works: the senior receives BOTH
the soft greeting AND the pending-capture offer in response to their
first message — not a state-machine advance.

DB after:
- `handoff_step=4`
- `awaiting_pending_capture='grandkids'`

### Step 5 — capture names

Senior:
```
Anish, Aman, Akshadha
```

**Expected log:** `PENDING_CAPTURE | user_id=... | kind=grandkids | captured 3 name(s): Anish, Aman, Akshadha`

**Expected reply:** *"Anish, Aman, and Akshadha — thank you for sharing. I'll remember. 🙏"*

DB check:
```sh
python3 -c "import sqlite3; c=sqlite3.connect('/data/saathi.db'); [print(dict(x)) for x in c.execute('SELECT name, relationship FROM family_members WHERE relationship=\"grandchild\" ORDER BY id DESC LIMIT 5').fetchall()]"
```

Should return 3 rows.

### Step 6 — medicines path (same pattern)

Senior: `my medicines ran out today` → expect medicines offer.
Senior: `metformin 8am and 8pm, atorvastatin at night` → expect
`medicines_raw` populated, `pending_medicines=0`, reminders seeded.

### Step 7 — address consistency

From any point post-handoff, send any message. Expected: DeepSeek uses
"Ma" consistently — never "Durga", never "Durga Ji", never "Durga Ma".
This is the Fix 2 test.

If DeepSeek still occasionally slips and uses "Durga" or "Durga Ji":
the `address_lock` needs to be tightened or moved higher in the prompt.
Check the system prompt order with `grep 'ABSOLUTE' deepseek.py` — the
two locks must be prepended before `base_prompt`.

---

## Deferred / post-pilot items (unchanged)

- Conversational-intent capture to structured tables. Names said in
  normal conversation still go to `memories`, not `family_members`.
- News geo-filter gap (Irish/Moldovan/etc. articles slipping through).
- The four handoff bugs (1/2/3/4) from the earlier 22 Apr chatlog are
  resolved by Fix 4 (they were all caused by the unconditional state
  advance and the raw-save-to-fields behaviour).

---

## Two design questions resolved at close (23 Apr 2026)

### Q1 — Move bot-name choice from child onboarding to senior's first session?

**Decision: no, leave it in child-led onboarding (step 16).**

Arguments for moving it: restores senior agency over a personal choice.

Arguments against (and why they win):
1. First-contact rule mandates "no question" on the soft greeting. The
   question we'd be adding is the exact one we just stripped from the
   handoff state machine for causing bugs.
2. The bot must call itself *something* in the soft greeting, so a
   default must exist regardless. You can't not-set it.
3. Seniors deflect naming decisions ("call yourself whatever") — partial
   or joking values would end up in the field. The child is the better
   person to decide on behalf of the household.

Fallback for agency: senior can say "call yourself X" in normal chat
and DeepSeek handles it, same as the address override path. Revisit
post-pilot if data shows seniors routinely renaming the bot.

### Q2 — Is the "whose phone" + button completion + hand-off the next batch?

**Yes — call it Batch 4.** Originally scoped as part of Batch 3. The
fix shipped this session (Batch 3a) only addressed the state machine
bugs. Batch 4 work:
- Step-0 question: "Whose phone is this — yours or your parent's?"
- New DB columns: `setup_device` (`self_phone` / `parent_phone`),
  `pending_handoff_code` (shareable code for the same-phone-different-device
  case)
- Branched completion UI:
  - `parent_phone` path: inline keyboard "I'm done — hand to senior now"
    button. After tap, bot sends soft greeting automatically. No more
    ambiguity about whether the next message is from child or senior.
  - `self_phone` path: generate a short code, display it with copy/share
    instructions. Senior joins by sending the code to the bot on their
    own phone.
- First-message detection that knows which device the message came from.

Ship as its own batch after Batch 3a clears live test. Non-trivial —
full session's work.

---

## Pending commit

```
cd ~/saathi-bot
git add pending_capture.py rituals.py deepseek.py onboarding.py main.py CHECKPOINT.md CLAUDE.md
git commit -m "Batch 2 keyword patch + addressing fix + completion copy + Batch 3a handoff redesign"
git push origin main
```

---

## If the live test fails

1. **Handoff keeps advancing** → check that the `main.py` block at
   ~line 1153 is the new single-message version, not the 4-branch
   state machine. `grep -n "handoff_step = user_row" main.py` should
   show only one occurrence inside the handoff block.
2. **Pending-capture still silent** → check Railway logs for
   `PENDING_CAPTURE` lines at all. If none fire, the deploy didn't
   pick up the new `pending_capture.py`. Force re-deploy.
3. **DeepSeek still says "Durga"** → `grep -n "ABSOLUTE ADDRESS RULE" deepseek.py`
   should return one line in the prompt assembly. If the var is
   defined but unused, the wiring at the bottom of `_build_system_prompt`
   is still broken.
4. **Completion message missing the deferral block** → `grep -n "deferral_note" onboarding.py`
   should show it's rendered inside the return string.
