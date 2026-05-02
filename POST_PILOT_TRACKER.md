# POST-PILOT TRACKER

*Single source of truth for everything we agreed to defer until after the pilot ships. Created 2 May 2026. Items added here MUST also be removed from CHECKPOINT.md / CLAUDE.md / BUGS_FAMILY_BRIDGE.md "deferred" sections to avoid drift.*

**Status legend:** ⬜ open · 🔄 picked for next iteration · ✅ shipped post-pilot.

---

## P1 — design decisions to revisit with pilot data

### Self-setup consent model
- `onboarding.py:1284-1290` — providing an emergency contact in self-setup auto-flips heartbeat_consent, heartbeat_enabled, escalation_opted_in, weekly_report_opt_in all to 1. Implicit consent.
- Family-led mode asks explicit consent (step 20).
- **Inconsistency to resolve:** either make self-setup explicit too (extra question, more friction), or document the implicit-consent intent in user-facing copy ("By providing an emergency contact, you're enabling safety alerts...").
- Pilot data signal: how many self-setup users complain / are surprised by ping behaviour after first heartbeat fires.

### Self-setup feature parity gaps
Self-setup mode skips several questions that family-led asks. Self-setup users get defaults for:
- **Persona** — defaults to schema value `friend`. Self-setup user can't pick caring_child / grandchild / assistant.
- **Religion** — never asked. Festival reminders won't fire correctly for self-setup users.
- **preferred_salutation** — never asked. Falls back to `{name} Ji` per Batch 1c. Reads old-fashioned for younger users.
- **favourite_topics** — only asks news_interests, no separate topics question.
- **Spouse name + structured family** — see "conversational-intent → structured tables" below.

Pilot data signal: how many self-setup users want to update these post-onboarding. Decide whether to add the questions back or build an in-conversation update flow.

### Persona caring_child ↔ grandchild collapse on emotional prompts
- Both personas produce near-identical replies when vulnerability handler / Rule 5 fires.
- Architectural finding: safety > sensitivity > persona is the correct hierarchy; persona dial only visible on neutral prompts.
- Post-pilot: copy-tighten the two personas so the dial is visible in more states.

### Conversational-intent → structured tables routing
- Names/medicines mentioned in normal chat land in `memories` table as prose, not in `family_members` / `medicine_reminders` rows.
- Self-setup live-confirmed 2 May 2026: "wife ishween and daughter noor" stored as ONE family_members row with name="Wife Ishween And Daughter Noor", not as spouse_name + child row.
- Post-pilot: build either an in-onboarding structured parser OR an in-conversation extractor.

### Soft inference on benign topics
- Bandra → "beach ki hawa"; DeepSeek paints in detail beyond what senior shared.
- Watching, not fixing pre-pilot. Pilot data signal: do seniors notice / mind?

### `/family code` (with space) typo handling
- Telegram doesn't recognize spaced commands. The message goes through normal pipeline as a regular text message — likely a relay to the senior or DeepSeek response.
- Post-pilot: add a typo guard in the slash-command parser, or document the correct command in the family invite block.

---

## P2 — UX nits

### Self-setup ritual times — silent acceptance of unparseable input
- 2 May 2026 live test: "i will message you" for afternoon and evening times stored as None silently.
- No "got it, no scheduled afternoon check-in" ack.
- Post-pilot: add a soft confirm if no time extracted ("Just to confirm — no afternoon check-in?").

### Mistyped 6-char family code falls into onboarding
- If a family member typos one char (e.g. `ABCD12` instead of `ABCD13`), `lookup_senior_by_code` returns None, bare-code flow returns False, user cascades into "are you setting this up for yourself or for a family member?" — confusing.
- Workaround for pilot: instruct adult children to copy-paste, not retype.
- Post-pilot fix: when a 6-char-shaped string fails lookup, send "That code didn't match — please re-check and try again" instead of falling through.

### Markdown special chars in user input get parsed by Telegram
- Family member writes `Hello *Ma*!` — asterisks get stripped or render unexpectedly.
- Pre-existing, not Patch 1 regression. Hardening: escape user-provided text or switch to plain-text mode for relay body.

### `/profiledump` default values misleading pre-onboarding
- `persona: friend` and `language: hindi` show as defaults before onboarding completes (schema NOT NULL defaults).
- Post-pilot: render `<unset>` in the dump if onboarding_complete=0, OR change schema defaults to NULL.

### First-contact rule slight bend in self-setup Mode 2
- First message `Hello… I'm Saathi. I'll be around — you can talk whenever you feel like.` is followed immediately by `What would you like me to call you?` in the same outgoing message.
- CLAUDE.md First-Contact rule: "no question in first outgoing message."
- Defensible because user picked `self` (signaled intent to be onboarded), but strict reading bends the rule.
- Post-pilot: split into two messages with a small delay, OR accept the bend with a CLAUDE.md note.

### Rule 6 (≤3 sentences) not strictly enforced
- 2 May joke response was 4 sentences. Acceptable for jokes; loose enforcement elsewhere.
- Post-pilot: consider adding a sentence-count check in DeepSeek wrapper, OR explicitly carving out exceptions (jokes, multi-step instructions).

---

## P3 — code hygiene / cleanup

### Two `_USER_CACHE` dicts (main.py + database.py)
- Should collapse to one. Several Patch session bugs traced back to the divergence.
- Post-pilot: structural refactor.

### Hindi numerals in time parser ("ek baje", "do baje")
- Deferred to v1.5. Not in pilot scope.

### `apis._extract_first_keyword` dead code
- Defined in apis.py:~1100, no longer called after Bug C2 fix (30 Apr 2026).
- Post-pilot: delete.

### In-process IPL schedule cache
- `_IPL_SCHEDULE_CACHE` held for process lifetime.
- Pushing a refreshed `data/ipl_2026_schedule.json` mid-day requires Railway redeploy to take effect.
- Acceptable for pilot. Post-pilot: add file-mtime check so cache invalidates without redeploy.

### Vestigial `wake_time` / `sleep_time` columns
- Patch 4 stopped writing them. Columns still exist in schema.
- Post-pilot: drop columns via migration (after confirming no legacy reads remain).

### `/runweeklyreport` admin command
- Not built. Without it, Section 6.2 weekly report cannot be tested before Sunday.
- Build pre-Sunday-of-pilot-week-1 if you want to verify it before real users see it.

### Two near-identical Ishween rows in family_members
- 2 May 2026 self-setup live test created both:
  - `family: Wife Ishween And Daughter Noor (no phone)`
  - `emergency_contact: Ishween (9833192304)`
- Harmless but redundant. Same person, two rows.
- Post-pilot: dedup logic when emergency_contact name matches an existing family member.

### Schema defaults vs NULL
- `persona INTEGER DEFAULT 'friend'`, `language TEXT DEFAULT 'hindi'`, `heartbeat_consent INTEGER DEFAULT 0`, `escalation_opted_in INTEGER DEFAULT 0`.
- Post-pilot: review which should be NULL pre-onboarding for cleaner state inspection.

---

## P4 — features not built (deliberately scoped out for pilot)

### Family bridge: senior → family direction
- Currently one-way: family → senior only.
- Post-pilot: design senior-initiated outbound to family.

### Two-way family bridge confirmation
- Currently family member just sees "Your message has been sent to *Ma*." with no read receipt.
- Post-pilot: add "Ma read it" or "Ma replied" signal.

### Self-setup walkthrough video
- Discussed during D1 pilot prep but not built.
- Decision deferred to Rishi: produce or not?

### "What is Saathi" explainer video
- Same as above.

### Senior notification when family joins
- Currently only `weekly_report_opt_in=1` flag set silently. Senior doesn't get a real-time "Priya just connected as your family — she'll get weekly updates."
- Pilot may surface whether seniors care about this signal.

### Telegram install instructions
- Pilot users without Telegram have no in-app guidance.
- Decision: filter recruitment to Telegram users, OR add install steps to senior PDF / video.

---

## How this file gets used

- During pilot: tag any new finding with severity (P1/P2/P3/P4) and add it here.
- Weekly during pilot: review and re-prioritize based on user signal.
- After pilot closes: triage into next-sprint roadmap + close items here.
- Items moved to active development: mark 🔄 here.
- Items shipped: mark ✅ here with commit reference.
