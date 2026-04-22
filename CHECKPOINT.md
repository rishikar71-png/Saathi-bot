# CHECKPOINT — 22 April 2026 (end of Opus 4.7 session 4)

**Read order for next session:** this file → CLAUDE.md → progress.md.

---

## TL;DR — state at end of this run

Two batches of fixes landed this session: **Batch 1c** (dynamic setup_name in
DeepSeek system prompt) and **Batch 1d** (structured family roster injection).
Both verified by `py_compile` + render tests (11/11) + DB integration tests
(3/3). **Code is in the working tree, NOT yet pushed to Railway.**

Rishi needs to push before the next live test. Next session will start with a
fresh chatlog to confirm the two hallucination bugs are fixed live, then
continue to Batch 2.

Note: Batch 1 + Batch 1b (copy & list fixes + data-correctness fixes from the
first two chatlogs this week) were pushed between the last checkpoint and
this session — they are NOT still pending. This checkpoint covers only what
was done in the current run (Batches 1c and 1d).

| Fix | Status |
|---|---|
| 1c. Replace hardcoded "Priya" with `{SETUP_NAME}` placeholder in deepseek.py (lines 87, 94); runtime substitution from `user_context["setup_name"]`; generic placeholders for lines 121, 165, 184, 329; new NAME USAGE rule | ✅ done |
| 1d. Structured FAMILY block in system prompt (senior/spouse/children/grandchildren/setup_by/emergency); "use these names, never invent" header; grandkids fallback text when empty; flat list, no gender labels | ✅ done |
| `get_setup_person(user_id)` helper in database.py | ✅ done |
| `get_family_members(user_id)` helper in database.py | ✅ done |
| `_format_family_block(user_context)` renderer in deepseek.py | ✅ done |
| main.py threads `setup_name`, `family_members`, `preferred_salutation` into `user_context` | ✅ done |
| `py_compile` clean on deepseek.py / main.py / database.py | ✅ |
| Render tests (5 scenarios for 1c + 6 scenarios for 1d) | ✅ 11/11 pass |
| DB integration test (real sqlite + seeded child-led Durga setup) | ✅ 3/3 pass |
| Live retest after deploy | ⏳ next session |

---

## Context from earlier in the session (before 1c/1d)

Before Batches 1c and 1d, Rishi pasted a third live chatlog from a child-led
onboarding + handoff for senior "Durga". I delegated diagnosis to the
Explore agent and surfaced four distinct bugs:

- **Bug 1 — main.py:1126-1132** — staged handoff state machine unconditionally
  advances, even when the senior's message is a real question (not a
  state-machine answer). The "sacred first-contact moment" breaks if the
  senior tries to engage.
- **Bug 2 — main.py:1139-1142** — handoff step 2 saves the senior's raw reply
  to `preferred_salutation` with no affirmation filtering. "Ma is good"
  becomes the stored address → future messages address her as "Ma is good".
- **Bug 3 — deepseek.py:87, 94** — hardcoded "Priya" example in the system
  prompt; DeepSeek was copying it into responses. **FIXED THIS SESSION
  (Batch 1c).**
- **Bug 4 — main.py:1191** — `"family_members": None` TODO since Module 7;
  children/grandchildren names were never in the DeepSeek context →
  "Rahul and Anjali" hallucination. **FIXED THIS SESSION (Batch 1d).**

Bugs 1 and 2 are deferred to **Batch 3 (handoff redesign)** which was
already on the roadmap. Bugs 3 and 4 were hotfixed this session because
they were pilot-blocking (wrong-name addressing + invented family names
destroy trust immediately).

Design decision locked in this session (Rishi): the FAMILY block does NOT
label children as son/daughter — we don't capture gender at onboarding.
Flat list only. No relational qualifiers on the setup person either (we
know the name and phone, not the relation).

---

## Files changed in this run

### `database.py`

Two new helpers appended after `save_emergency_contact`:

- `get_family_members(user_id) -> list[dict]` — returns all rows from
  `family_members` for this senior, ordered by `id ASC` (preserves
  onboarding-order so children render in the order typed). Each dict has
  `name`, `relationship`, `phone`, `role`, `is_setup_user`.

- `get_setup_person(user_id) -> dict | None` — returns the adult child who
  ran onboarding. Matches on `relationship='setup' OR is_setup_user=1`.
  Returns `{'name': str, 'phone': str}` or `None` if self-setup / onboarding
  incomplete.

### `deepseek.py`

**Name-leak surgery (6 places):**

- Line 87 (IDENTITY — "who set this up"):
  `"Priya thought you might enjoy..."` → `"{SETUP_NAME} thought you might enjoy..."`

- Line 94 (FAMILY REFERENCES — PERMITTED):
  `"Priya thought you might enjoy..."` → `"{SETUP_NAME} thought you might enjoy..."`
  Added new **NAME USAGE** line: "Only use family names that appear in the
  user's profile context below. Never invent names. If you do not know a
  family member's name, refer to them by relationship ("your daughter",
  "your grandson") or neutrally ("them"). Never guess a name."

- Line 121 (CALL REMINDER example):
  `"Should I remind you to call Rahul this evening?"` →
  `"Should I remind you to call [their name] this evening?"` (instruction
  now explicitly says "using that person's actual name from the user
  profile").

- Line 165 (Rule 5A):
  `"Priya would love hearing that."` →
  `"[Their name] would love hearing that."` (instruction now says "using
  their actual name from the user profile").

- Line 184 (proactive memory):
  `"I remember you were thinking about Priya's results..."` →
  `"I remember you were thinking about [their family member]'s results..."`

- Line 329 (user_profile_section example in `_build_system_prompt`):
  `"You sounded so happy when you mentioned Priya last time..."` →
  `"You sounded so happy when you mentioned [their family member] last
  time — have you spoken to them again?"`

**`_BASE_SYSTEM_PROMPT` substitution (in `_build_system_prompt`):**

```python
_setup_name = (user_context.get("setup_name") or "").strip() or "a family member"
base_prompt = _BASE_SYSTEM_PROMPT.replace("{SETUP_NAME}", _setup_name)
prompt = language_lock + base_prompt + "\n\n" + user_profile_section
```

Fallback "a family member" reads plainly inside the sentence: "a family
member thought you might enjoy having someone to chat with." Used for
self-setup / onboarding-incomplete users.

**New `_format_family_block(user_context)` function (added above
`_build_system_prompt`):**

Renders a structured FAMILY block per the design Rishi locked in this
session:

```
FAMILY (use these names exactly — never invent names not listed here):
- Senior: Durga (addressed as "Ma")
- Spouse: Ishween
- Children: Putu, Mana
- Grandchildren: not known yet — do not invent names
- Setup by: Rishi
- Emergency contact: Rishi (9819787322)
```

Design rules:
- Flat list. No gender labels on children (we don't collect gender at
  onboarding).
- No invented relations — only what's explicitly stored.
- Grandchildren show "not known yet — do not invent names" when empty (the
  primary hallucination fix).
- Senior line omits the parenthetical when salutation equals name (no
  "Ramesh (addressed as Ramesh)").
- Returns `None` if nothing to show (suppresses empty block).
- Catchall `relationship='family'` rows (self-setup step 5 unspecified)
  render as "Other family mentioned: X, Y, Z".

**Wiring (in `_build_system_prompt` context_lines block):**

Replaced old `- Family: {family_members}` one-liner with:

```python
family_block = _format_family_block(user_context)
if family_block:
    context_lines.append(f"\n{family_block}")
```

### `main.py`

**Import:** added `get_setup_person, get_family_members` to the existing
database-import block at line 19.

**`_run_pipeline` context build (around line 1195):**

- Fetches `_setup_person = get_setup_person(user_id)` → extracts name into
  `_setup_name`.
- Fetches `_family_members = get_family_members(user_id)` → full roster.
- Reads `user_row["preferred_salutation"]` into `_preferred_salutation`
  (wrapped in try/except for safety on older rows).
- `user_context` now carries three new keys:
  - `setup_name`: for `{SETUP_NAME}` substitution in base prompt.
  - `family_members`: list of dicts (replaces the old `None` TODO).
  - `preferred_salutation`: for the "addressed as" line in FAMILY block.

---

## Verification results (from this run)

```
py_compile: deepseek.py main.py database.py → COMPILE OK

Batch 1c render tests — 5/5 pass:
  ✓ setup_name="Rishi"      → Priya + Rahul removed, "Rishi thought..." present
  ✓ setup_name=None         → falls back to "a family member thought..."
  ✓ setup_name=""           → falls back to "a family member thought..."
  ✓ generic placeholders present — [their name], [their family member], NAME USAGE rule
  ✓ setup_name="  Rishi  "  → stripped cleanly inside sentence

Batch 1d render tests — 6/6 pass:
  ✓ Scenario A: Durga (Ma), 2 children, setup+emergency=Rishi → full block correct
  ✓ Scenario B: Ramesh (Rameshji), no spouse, with grandkids → grandkids listed, no fallback line
  ✓ Scenario C: self-setup with relationship='family' rows → "Other family mentioned" line
  ✓ Scenario D: salutation == name (no parenthetical dupe)
  ✓ Scenario E: all empty → _format_family_block returns None (suppresses block)
  ✓ Scenario F: full _build_system_prompt render — no Priya/Rahul, FAMILY block present, invent rule present

DB integration tests — 3/3 pass (real sqlite, seeded Durga/Rishi child-led setup):
  ✓ get_setup_person returns {'name': 'Rishi', 'phone': '9819787322'}
  ✓ get_family_members returns 4 rows categorized correctly
    (1 setup, 2 children, 1 emergency_contact)
  ✓ empty user returns [] and None
```

---

## Push commands

```
cd ~/saathi-bot
git add deepseek.py database.py main.py CHECKPOINT.md CLAUDE.md
git commit -m "Batch 1c+1d: dynamic setup_name in system prompt; inject real family roster (fixes Priya/Rahul name leaks + children hallucination)"
git push origin main
```

---

## Next-session plan

Rishi is restarting with a **fresh chatlog from a child-led family setup
followed by the senior asking identity + family questions** to verify the
two hallucination fixes live. Expected observations:

1. Senior asks "who set this up for me?" or "did someone set you up?" →
   DeepSeek response uses the actual setup_name from this user's onboarding
   (e.g. "Rishi thought you might enjoy..."), never "Priya".

2. Senior asks "do you remember my kids / children / daughters / sons?" →
   response lists the actual children names from this user's
   `family_members` table (e.g. "Putu and Mana"). Never "Rahul and Anjali"
   or any other invented pair.

3. Senior asks "do you remember my grandchildren / grandkids?" → response
   acknowledges it doesn't know their names yet and invites Ma to share
   them, or references them only by relation. Never fabricates names.

4. Salutation threading still works (from last session's fix) — every
   reference to the senior uses "Ma" (or whatever the senior chose at
   handoff), never the raw name "Durga".

Things to watch for in the live test:
- Check the Railway logs for the system prompt body to confirm the FAMILY
  block is being rendered with real data, not empty.
- If setup_name is empty (self-setup path), confirm the fallback "a family
  member thought..." reads plainly, not awkwardly.
- If grandchildren were captured at onboarding, confirm they appear in the
  block instead of the "not known yet" fallback.

After live verification: start **Batch 2** — deferred senior inputs
(pending_grandkids_names + pending_medicines DB columns, step 7 + 10
deferral detection, keyword-triggered ask-Ma-later for grandkids, scheduled
day-2 prompt for medicines).

Then **Batch 3** — handoff redesign, which folds in:
- Bug 1 from third chatlog (main.py:1126-1132 — staged handoff state
  machine unconditionally advances even when senior asks a real question)
- Bug 2 from third chatlog (main.py:1139-1142 — "Ma is good" saved verbatim
  to `preferred_salutation`; needs affirmation filtering)
- Original Bug F scope: step-0 "whose phone" question, setup_device column,
  pending_handoff_code column, branched completion message (button vs
  code), staged push sequence with delays.

---

## Deferred / open from this session

- `/medicines` copy question from earlier session — already resolved in
  Batch 1: completion message was softened to "I'll gently ask Ma about
  them once we've started chatting" instead of referencing the non-existent
  `/medicines` command. No wiring needed.

- Grandchildren deferral flag (`ctx["grandkids_deferred"]`) is currently
  in-memory only (onboarding.py:1170) — no DB column. Batch 2 is the
  correct place to add the `pending_grandkids_names` column + surface the
  deferral in the FAMILY block ("Grandchildren: Ma will share when we
  chat"). For now, absent grandkids render as "not known yet — do not
  invent names" which is safe but unaware of the explicit deferral.
