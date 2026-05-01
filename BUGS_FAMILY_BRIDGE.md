# BUGS — Family Bridge (opened 1 May 2026)

Catalog from the live bare-code test on 1 May 2026. Six bugs identified.
Fix decisions locked with Rishi the same day. Verification discipline
V1–V10 (in CLAUDE.md) applies to every patch in this list.

**Status legend:** ⬜ open · 🔄 in progress · ✅ shipped + verified.

---

## Fix sequence (small → large)

1. **FB-1 + FB-3 + FB-6** — single patch to `family.py` + minor `main.py` thread-through. Mechanical, no schema, no flow change. Ship first.
2. **FB-2** — new flow step (`awaiting_family_name` after Yes confirm). Schema: no new column needed (reuse `family_members.name`). Touches `_handle_bare_code_flow` + new state in user_row.
3. **FB-4** — largest. Third opening-detection option. Touches `onboarding.py` opening question copy, `handle_mode_detection`, `setup_mode` state machine.
4. **FB-5** — auto-resolves with FB-1. Verify after FB-1 deploy; close.

---

## P1 — pilot-blocking trust/clarity breaks

### FB-1: `family_term` ignored in family-side bot messages ⬜

**Symptom:** Senior tells `/familycode` that family member calls them "Ma".
Bot stores `users.family_term="Ma"`. But every Saathi-to-family message
uses `users.name` ("Durga"). 5 broken touchpoints:

1. Bare-code confirm prompt: `This code will connect you to *Durga*'s Saathi.`
2. Welcome msg: `You're now connected to *Durga*'s Saathi.`
3. Welcome msg: `Any message you send here will be passed to *Durga*.`
4. Welcome msg: `Type anything now to send *Durga* a message.`
5. Welcome msg (weekly report line): `weekly update on *Durga*`
6. Relay confirmation back to family: `Aapka sandesh *Durga* tak pahuncha diya gaya.`

**Root cause:** `lookup_senior_by_code` (family.py:233), `complete_join_for_senior`
(family.py:264), and `build_relay_confirmation` (family.py:407) all SELECT only
`name` from `users`. Never read `family_term`. `_handle_bare_code_flow` confirm
prompt at main.py:894 uses returned `senior_name`.

**Fix:**
- `lookup_senior_by_code` — add `family_term` to SELECT, return `display_name = family_term or name`
- `complete_join_for_senior` — add `family_term` to SELECT, use `display_name` in welcome message template
- `build_relay_confirmation` — accept `display_name` as a param (or refactor signature to take a dict)
- `_handle_bare_code_flow` — read `senior["display_name"]` instead of `senior["senior_name"]`
- Fallback: if `family_term` is NULL (older seniors who set up before this enhancement), fall back to `name` — preserves existing behavior

**V4 test cases:**
- (a) `family_term="Ma"`, `name="Durga"` → all 6 touchpoints render "Ma"
- (b) `family_term=NULL`, `name="Durga"` → all 6 render "Durga" (backward-compat)
- (c) `family_term=""`, `name="Durga"` → empty-string treated like NULL → renders "Durga"

---

### FB-2: `family_members.name` hardcoded to `'Family'` on bare-code join ⬜

**Symptom:** Senior sees `Family ne aapko sandesh bheja hai 💌` — generic,
not the actual sender's name.

**Root cause:** `family.py:289` —
`INSERT INTO family_members (...) VALUES (..., 'Family', 'family', 'family')`.
The bare-code flow never asks the family member for their name.

**Fix decision (Rishi, 1 May): Path (b) — explicit ask after Yes confirm.**
Family members may go by endearing/nick names that differ from their TG name,
so auto-capture from `update.effective_user.first_name` is wrong.

**Flow change:**
1. Family member pastes code → confirmation prompt (existing)
2. Family member replies `Yes` → register row with `name='Family'` (placeholder), set `awaiting_family_name=1` on family member's user_row
3. Bot asks: `Great! What name should *Ma* see your messages from? (For example: your first name, or what *Ma* usually calls you.)`
4. Family member replies → strip leading affirmations (V9-compliant), title-case, validate (≤30 chars, not blank, not just punctuation), UPDATE `family_members.name`
5. Send full welcome message with `*display_name*` references everywhere
6. Clear `awaiting_family_name` flag

**Schema change:** add `awaiting_family_name INTEGER DEFAULT 0` to `users` table
(or reuse `awaiting_pending_capture` enum with new value `'family_name'`).
Lean: separate column — different lifecycle.

**Edge cases:**
- Family member sends junk ("yes", "sure", "ok", emoji-only) → re-prompt once
- Family member sends very long input → trim to 30 chars, log warning
- Family member never replies → `awaiting_family_name` stays set; on next message, treat as the answer
- Family member sends a 6-char code-shaped string → still treat as their name (low collision risk; they're past the bare-code flow already)

**V4 test cases:**
- (a) `Yes` → `Priya` → senior sees `Priya ne aapko sandesh bheja hai`
- (b) `Yes` → `yes priya` → leading affirmation stripped → "Priya"
- (c) `Yes` → `🙏` (emoji only) → re-prompt
- (d) `Yes` → empty whitespace → re-prompt
- (e) `Yes` → `Priya Sharma` → both words preserved → "Priya Sharma"

---

### FB-3: Relay wrappers in Hindi when senior was onboarded in English ⬜

**Symptom:** Both relay directions render in Hindi:
- Senior side: `Family ne aapko sandesh bheja hai 💌`
- Family side: `Aapka sandesh Durga tak pahuncha diya gaya. 🙏`

Even though both sides typed in English (`Hello ma` / `How are you`).

**Root cause:** Both functions read `users.language` (family.py:352, main.py:981).
Most plausible explanation (unverified): script-detection learning loop shipped
7 Apr 2026 has drifted `users.language` from `english` to `hindi`/`hinglish`
based on Module 19 Task 4 (Hindi 5-turn) + recent Hinglish queries.

**Fix decision (Rishi, 1 May): Path (iii) — match the message's own script.**
Relay is a transactional wrapper, not a conversation. No global state.

**Implementation:**
- Senior-side relay (`relay_message_to_senior`): detect script of `message_text`
  itself. If Devanagari → Hindi wrapper. If common Hindi/Hinglish romanized
  words detected → Hinglish wrapper. Else → English wrapper.
- Family-side ack (`build_relay_confirmation`): detect script of the family
  member's message that's about to be relayed. Same logic.
- Reuse the existing `_detect_message_language` in main.py — DO NOT duplicate.
  Either import it or move it to a shared module (e.g. `language_utils.py`).

**Open verification (deferrable):** independent of fix, run `/profiledump` to
confirm `users.language` actually drifted. Even if it did, FB-3 fix makes the
relay wrapper independent of stored language. But if drift confirmed, we may
also want to address whether morning briefing / TTS / DeepSeek system prompt
language should also be per-message vs. learned. **Out of scope for this bug.**

**V4 test cases:**
- (a) Family sends `Hello ma` → senior wrapper: `Priya sent you a message 💌`, family ack: `Your message has been sent to *Ma*. 🙏`
- (b) Family sends `Namaste Maa` → senior wrapper: `Priya ne aapko sandesh bheja hai 💌`, family ack: `Aapka sandesh *Ma* tak pahuncha diya gaya. 🙏`
- (c) Family sends `नमस्ते मा` (Devanagari) → both Hindi
- (d) Family sends `Hi maa, kaise ho?` (Hinglish) → both Hinglish

---

## P2 — UX gaps

### FB-4: Opening detection question missing "I have a code" path ⬜

**Symptom:** Family member who lands on `/start` first (instead of pasting
code first) gets routed into child-led onboarding for a fictitious senior.
Recovery only if they happen to paste the code anyway.

**Root cause:** `get_opening_detection_question` offers only `myself` and
`family member`. No path for joining as family of an existing senior.

**Fix decision (Rishi, 1 May): Option 1 — add a third option.**

**Implementation options (need product call before coding):**
- **(a)** Three explicit options: `myself` / `family member` / `joining` (or `code`)
  - `joining` → ask for the code, route to bare-code flow
  - Cleanest, most discoverable
- **(b)** Keep two visible options + auto-detect: if first message after `/start`
  shape-matches `^[A-Z0-9]{6}$`, divert to bare-code flow
  - Less discoverable; relies on family member knowing to send the code

Lean: **(a)** — pilot families won't guess (b) without docs.

**Files:** `onboarding.py` (opening detection question copy + `handle_mode_detection`),
`main.py` (mode routing branch).

**V4 test cases:**
- `myself` → self-setup mode (existing)
- `family member` → child-led setup mode (existing)
- `joining` / `code` / `i have a code` → ask for the code, route to bare-code flow
- Garbage answer → re-prompt with the three options
- Backward-compat: existing in-flight `setup_mode='pending'` users with old `myself` / `family member` answers still work

---

### FB-5: `/familycode` ack copy promises something it doesn't fully deliver 🔄

**Symptom:** Bot says `Got it — I'll refer to them as *Ma* in the message.`
Strictly true only for the WhatsApp forward block. False for every Saathi-to-family
message (per FB-1).

**Status:** auto-resolves once FB-1 ships.

**Action after FB-1 deploy:** verify that the promise becomes literally true
(every family-side bot message uses `family_term`). Close FB-5 then.

---

## P3 — formatting

### FB-6: Senior-side relay format collapses paragraph break ⬜

**Symptom:** Code at `family.py:381-382` writes
`*{family_name}* ne aapko sandesh bheja hai 💌\n\n_{message_text}_` but
Android Telegram renders as `Family ne aapko sandesh bheja hai 💌  Hello ma`
on a single visual line with 2 spaces (re-pasted, confirmed).

**Most likely cause:** Telegram Markdown v1 parse mode collapses `\n\n` adjacent
to `_..._` italics block into a soft line break that the mobile renderer
displays inline.

**Fix:** ship as part of FB-1 patch. Two options:
- **(a)** Drop italics on message body, use a single `\n\n` + bold or quote prefix:
  `*{family_name}* sent you a message 💌\n\n"{message_text}"`
- **(b)** Switch from Markdown to MarkdownV2 (escape required for special chars)
- **(c)** Use a more obvious visual separator: `*{family_name}* sent you a message 💌\n———\n_{message_text}_`

Lean: **(a)** — least Markdown trickery, clearest visual hierarchy.

**V4 test:** screenshot of senior-side rendering after fix shows
`Priya sent you a message 💌` on its own line, message body on a new line.

---

## Workflow notes

- All fixes ship via V8 path-or-commit choice made at start of each patch.
- FB-1 + FB-3 + FB-6 likely commit-in-place (single touch to `family.py`).
- FB-2 likely commit-in-place (new state column + flow change in `main.py` + family.py).
- FB-4 — patch-deliverable mode if it grows beyond 3 files; otherwise commit-in-place.
- V9 word-boundary rule applies to FB-2 affirmation strip and FB-3 script detection.
- V10 patch-chain verification applies if multiple patches generated in one session.

## Test coverage

After all fixes ship, run a fresh family-bridge end-to-end:
1. `/adminreset` senior account
2. `/familycode` flow → confirm `family_term` saved + forward block correct
3. Second TG user `/start` → confirm third option visible
4. Pick `joining` → bot asks for code
5. Paste code → confirm prompt uses `family_term`
6. `Yes` → bot asks family member's name
7. Reply with name → welcome message uses `family_term` everywhere, includes name
8. Send English message → English wrappers on both sides, name shown to senior
9. Send Hindi message → Hindi wrappers on both sides
10. Verify formatting: senior-side relay shows wrapper + message on separate lines
