# CHECKPOINT — Resume after 29 Apr 2026 session

**Read order for next session:** this file → CLAUDE.md → progress.md.

---

## State at session close

**HEAD on `origin/main`:** Bug B commit (clarify ambiguous medicines during onboarding) on top of bare-hour parser fix on top of futures-imports on top of greeting fix on top of `aa32242`/`ba922ac`/`5cd84ff`/`11a1221`. All three 29 Apr pilot-blockers are deployed and verified live on Telegram.

**Working tree:** clean. Two `?? .patch` files untracked (`saathi_bug_b_29apr.patch`, `saathi_bundle_29apr.patch`, `saathi_followup_29apr.patch`, `session_23apr_bundle.patch`) — leftover from this session's `git am` rounds. Safe to delete or `.gitignore`.

**V8 added** to `CLAUDE.md` governing edit-tool-vs-patch workflow choice. Future sessions should pick the path at the start of any change to avoid the recurring `does not match index` failures that burned ~4 hours today.

---

## Live test verification (29 Apr session)

| # | Test | Status | Evidence |
|---|---|---|---|
| 1 | Step 0 captures name + phone | ✅ | `step 8 reused setup person as emergency` log line |
| 2 | "1.30" → 13:30 (bare 1–5 PM default) | ✅ | `REMINDER \| added \| time=13:30 \| source=bare_hour_pm_default` |
| 3 | Batch-ASK clarify for ambiguous medicines | ✅ | clarify question fired on step 10; per-medicine reply (`pan d in the morning and thyronorm at night`) resolved both rows; `step 10 medicines_clarify resolved -> step 11 (before=2 after=0)` |
| 4 | "BP pill at shaam 7" → 19:00 (Hindi word-boundary) | ✅ | `REMINDER \| added \| time=19:00 \| source=hindi_period \| id=24` — period qualifier `shaam` matched cleanly, no false "am" substring hit |
| 5 | "did you remind me today?" → factual (MEDICINE STATUS block) | ✅ | Reply: *"Today's BP pill is scheduled for 7 PM — that's still a few hours away. I'll let you know when it's time."* — factual, no invented history; "7 PM" rendering confirms TIME FORMAT rule firing |
| 6 | "can you set a reminder for me?" → RULE 13 capability response | ✅ | Reply: *"I can't set reminders myself, Ma — that's something the family handles during setup. But if you need a change, I can pass it along to Rishi. Would you like me to do that?"* — capability refused, salutation + setup-person name from FAMILY block, relay-offer scaffolding |
| Bonus A | Greeting respects salutation 'Ma' | ✅ | "Good afternoon, Ma." after senior typed "hi" (29 Apr afternoon run) |
| Bonus B | Bug B per-medicine resolution | ✅ | Pan D 09:00 + Thyronorm 21:00 from per-medicine reply |
| Bonus C | Bare hour parser stores "09:00" not "09:09" | ✅ | `bare=09:00 \| id=21` in seed log |
| Bonus D | Children names from FAMILY block | ✅ | `do you know the names of my children` → "Mana and Putu." — no hallucination |
| Bonus E | Grandchildren names from FAMILY block | ✅ | `do you know my grandkids` → "Akshdha, Aman, Anish, and Noor." — all 4, no fabrication |
| Bonus F | Unsupported language gate (family-mode) | ✅ | `odia` → polite refusal, held at step 4; `english` → advanced. Gate now verified live in BOTH modes |
| Bonus G | Handoff collapse to step 4 (Batch 3) | ✅ | `OUT \| type=handoff \| collapsed_to_step4 \| prior_step=0` |

---

## Next session priorities (in order)

**All 6 pilot-blocker tests passed live on 29 Apr afternoon run.** No code edits this run — verification only. Move to Module 19.

1. **Module 19 — End-to-End Capability Testing:**
   - YouTube music: by name, by mood, by genre — confirm real links
   - YouTube vague request ("kuch sunao") — fallback to preferences
   - News: morning briefing returns real headline
   - Cricket: morning briefing has real score when match is live
   - Weather: real conditions for user's city
   - Neural2 voice quality: send a voice message in Hindi + English

2. **Module 20 — Pilot prep:**
   - 5 test users (non-seniors) run through full flow
   - 20-user pilot invite list
   - Onboarding instructions written for adult children

---

## Open items deferred (not pilot-blockers)

| Item | Notes |
|---|---|
| Conversational-intent → structured tables | Names/medicines/grandkids mentioned in normal chat still land in `memories` as prose, not in `family_members` / `medicine_reminders`. Capture should mirror onboarding's pending_capture flow with similar clarify mechanics. Confirmed not pilot-blocking. |
| Hindi numerals | "ek baje" / "do baje" / "teen baje" not parsed by `_normalize_time`. v1.5 add. |
| Two `_USER_CACHE` dicts | main.py's unbounded cache vs database.py's 5-min TTL — collapse as first post-pilot task (ref 19 Apr session log). |
| Self-setup mode end-to-end test | Module 19 capability test should exercise self-setup at least once before pilot. |
| Untracked `*.patch` files | `saathi_bug_b_29apr.patch`, `saathi_bundle_29apr.patch`, `saathi_followup_29apr.patch`, `session_23apr_bundle.patch` sitting in `~/saathi-bot/`. Safe to delete or gitignore. |
| Memory extraction noise: "user is called Ma" | 29 Apr afternoon run logged `MEMORY \| type=family \| saved: The user is called Ma.` — "Ma" is salutation, not name. Diary system is treating it as a family fact. Low risk (FAMILY block always overrides identity at runtime), but pollutes the memory archive. Tighten extraction prompt or add a salutation-aware skip post-pilot. |
| `short_reply_disengagement` over-fires on contextual yes/no replies | 29 Apr run: `PIPELINE \| short_reply_disengagement triggered \| lang=english` for the affirmation reply to Saathi's own yes/no question. Outcome was correct (DeepSeek's reply held), but the pre-processor classification is wrong — a "yes" answering a direct question is engagement, not disengagement. Pre-processor should suppress disengagement detection when the previous Saathi turn ended with `?`. Edge case, not pilot-blocking. |

---

## Workflow note for next session (V8 reminder)

When shipping fixes:

- **Bug fix in code** → Patch deliverable. Do NOT use Edit tool on `/Users/rishikar/saathi-bot/`. Modify files in `/tmp/clone` via bash; generate patch from `/tmp`; Rishi `git am`s onto a clean working tree.
- **Documentation update (`CLAUDE.md` / `CHECKPOINT.md` / `progress.md`)** → Commit-in-place. Edit tool fine; final instruction is `git add <files> && git commit && git push`.

If `error: <file>: does not match index` ever appears, the wrong path was chosen — diagnose, abort the patch, switch to commit-in-place for that change.

**Stale-lock recovery** (when `error: could not write index` appears):
1. `rm -f .git/index.lock`
2. `git am --abort`
3. `git status` — confirm clean
4. (`lsof .git/index` showing Spotlight is benign)
