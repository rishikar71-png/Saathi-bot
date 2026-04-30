# CHECKPOINT — Resume after 30 Apr 2026 Module 19 verification session

**Read order for next session:** this file → CLAUDE.md → progress.md.

---

## State at session close

**HEAD on `origin/main`:** `bc03385`. The session shipped four commits on top
of `3387acf`:

```
bc03385  Bug E1'' — cricket schedule falls back to RSS when CricAPI lags
c6682ec  /cricdebug — drop Markdown, add defensive try/except + immediate ack
22694f9  Bug C2 — drop user interests from news keyword filter; add /cricdebug for Bug E1' diagnosis
608e288  Module 19 — /setpersona + /profiledump admin commands for testing
3387acf  30 Apr 2026 session close: V9 + V10 + session log + CHECKPOINT
```

Rishi confirmed the Bug E1'' push at session close. **First task next session:
verify Bug E1'' live (live test detailed below).** Docs (CHECKPOINT.md +
CLAUDE.md + progress.md) are the only outstanding diff at session close.

---

## Module 19 — what landed this session

| Task | Status | Notes |
|---|---|---|
| 6 — Persona effect | ✅ partial pass | `friend` register clearly distinct. `caring_child` ↔ `grandchild` collapse on emotional/keyed prompts (vulnerability handler, Rule 5 no-over-praise dominate). Persona dial moves the needle but only on neutral prompts. **Not pilot-blocking; copy work for post-pilot.** |
| 4 — Hindi 5-turn conversation | ✅ strong pass | Language stayed Hindi all 5 turns. FAMILY block working (Mana name surfaced from family roster, no fabrication). Context threading clean (turn 2 references turn 1, pronoun resolution). Persona register visible on grandchild ("Wah Ma," "Arre Ma woh din"). Three-mode engagement varied. Cricket query (turn 5) honest fallback. |
| 5 — Long-reply TTS | ✅ partial pass | Voice fired for 145 + 225 char replies. 300+ char regime not exercised because Rule 6 caps DeepSeek at 3 sentences. No skip observed in tested regime. |
| 1 — India news quality | ✅ Bug C2 fixed and verified | Pre-fix: VyOS (open-source network OS) surfaced as India news because `news_interests='family'` was used as a HARD keyword filter — RSS pipeline returned nothing → NewsAPI `/v2/everything?q=family` surfaced global tech press. Post-fix: real headlines (Assam exit poll, Indus river analysis, oil prices). |
| Bug E1'' — Cricket | ⏳ shipped, not yet live-verified | CricAPI free-tier didn't have GT vs RCB tonight at 19:30 IST in either `/currentMatches` or `/matches` at test time (14:00 IST). Fix wires cricket RSS feeds (ESPNCricinfo / Cricbuzz / NDTV Sports) as schedule-fallback when CricAPI is silent. |
| 3 — Family bridge bare-code | ⏳ deferred | Needs second Telegram user. |
| 2 — Self-setup mode end-to-end | ⏳ deferred | `/adminreset` + 2-day pacing test. Wall-clock dependent. |

---

## Live test plan — first task next session

**Step 1 — Bug E1'' verification:**

In Telegram:
```
aaj cricket hai kya?
```

Pass criteria:
- Reply mentions Gujarat Titans vs RCB at 19:30 IST as today's match, framed naturally
- Reply does NOT invent score, venue, or result that isn't in the RSS headlines
- Language stays Hindi/Hinglish
- If reply still says "schedule mein kuch nahi hai" → either `_RSS_FEEDS_CRICKET` URLs are returning empty (feed quality issue) OR DeepSeek over-conservatively interpreted permissive headlines as "no match." Run `/cricdebug` and the new path-debug logs in Railway to disambiguate.

**Risk to watch on Bug E1'' permissive path:** the new instruction gives DeepSeek
discretion to interpret RSS headlines as "match today" signals. Real risk it
over-interprets a generic preview ("CSK looks ahead to next week") as today's
match. If bot starts fabricating fixtures that don't exist, tighten the
instruction (require explicit kick-off time mention, not just team names).

**Step 2 — Bug C2 stability re-test:**

Cache TTL is 30 min. After cache expiry, retest:
```
Aaj ki main khabar kya hai?
```

Expected: real Indian top headlines, no VyOS / no global tech press surfacing.
If VyOS reappears → there's a code path my fix missed, dig into Railway logs.

---

## Module 19 remaining tier-1 tasks

After Bug E1'' verification clears, run:

**Task 3 — Family bridge bare-code:** needs second Telegram user. Have a family
member (different Telegram account) paste the family code (no `/join` command).
Expected: confirmation prompt → reply yes → register. The bare-code flow
landed on 22 Apr; not yet live-tested.

**Task 2 — Self-setup mode end-to-end:** `/adminreset` your own user, then on
opening pick "for myself." Day 1 should ask 5 questions. Day 2 should resume
naturally with remaining questions in conversation. Includes bridge-deferred
flow ("for myself, not now").

---

## New admin commands shipped this session

| Command | Purpose |
|---|---|
| `/setpersona <telegram_id> <persona>` | Switch persona post-onboarding. Valid: friend / caring_child / grandchild / assistant. Cache-invalidated. Used for Module 19 Task 6. |
| `/profiledump [telegram_id]` | Dump key profile fields for the user (name, salutation, language, persona, interests, family roster). Defaults to admin's own ID. Used to localize Bug C2. |
| `/cricdebug` | Diagnostic for Bug E1'. Bypasses cache, calls CricAPI `/currentMatches` + `/matches` directly, dumps raw match list with parsed dates and tracked-team classification. Plain text output (Markdown caused silent failures in V1). |

All admin commands gated on `update.effective_user.id == 8711370451`.

---

## Pending findings (not pilot-blocking)

| Item | Notes |
|---|---|
| Persona collapse on emotional prompts | `caring_child` and `grandchild` produce near-identical replies when vulnerability handler / Rule 5 no-over-praise fires. Architectural finding: safety > sensitivity > persona is the correct hierarchy, but persona descriptions read as too similar on neutral content too. Post-pilot: tighten copy. |
| Soft inference on prompts | Bandra → "beach ki hawa," "khidki se aati hawa." Senior didn't say beach. DeepSeek paints in detail. Light embellishment on benign topics. Watching, not fixing. |
| `apis._extract_first_keyword` now dead code | After Bug C2 fix, no longer called anywhere. Function still defined in apis.py:1112. Leave for now (might use for ranking post-pilot), or delete in a cleanup pass. |
| `_TTS_MAX_CHARS=400` regime above 225 chars | Implicit pass via shared code path, not explicit. Not pilot-blocking. |
| Conversational-intent → structured tables | Names/medicines mentioned in normal chat → memories prose, not family_members / medicine_reminders rows. Post-pilot. |
| Two `_USER_CACHE` dicts (main.py + database.py) | Collapse post-pilot. |
| Hindi numerals ("ek baje" / "do baje") | Not parsed by `reminders._normalize_time`. v1.5. |

---

## Module 20 — pilot prep (still pending)

- Identify 5 test users from family network
- Build 20-user invite list (target user profile: urban Indian senior 65+, adult
  child willing to do family setup)
- Onboarding doc for adult children (child-led setup walkthrough, expected
  timeline, what-to-tell-the-senior-first-time)
- Pre-pilot test plan (the 17-test Module 15 protocol + Module 19 capability tests)

---

## Verification discipline reminders

Active rules in CLAUDE.md (V1–V10). Pertinent reminders for next session:

- **V8** path A (`/tmp/clone` for patches) vs path B (Edit tool for commit-in-place):
  pick at the start of any change. This session used path B throughout — clean.
- **V9** no substring match for keywords ≤5 chars. Audit-clean as of 30 Apr.
- **V10** before generating any new patch, run `git log --oneline -5` against
  user's repo and confirm previous patch is in chain. Skipped this session
  because path B (commit-in-place) was used; V10 only matters for path A.

---

## Workflow note

This session was largely diagnostic + targeted fixes after live capability
tests surfaced two real bugs (C2 and E1''). The pattern that worked:

1. Live test → finding → diagnose with admin commands (or build admin command
   if missing) → trace to specific code lines → fix → V2/V3/V4 verify → ship
2. `/profiledump` and `/cricdebug` paid for themselves immediately. Worth
   keeping in the codebase as ongoing diagnostics rather than removing.
