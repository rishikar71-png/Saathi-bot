# CHECKPOINT — 22 April 2026 (Opus 4.7 continuation)

**Read order for next session:** this file → RESUME.md → CLAUDE.md → progress.md.

---

## TL;DR — state at end of this run

The three P0 parser fixes from the earlier 22 Apr Opus-4.6 session are **written,
verified, and committed to the working tree — NOT yet pushed to Railway.**
Rishi needs to `git push` from his terminal. After deploy, three things
still need live verification on Telegram (see "Pending live tests" below).

| Item | Status |
|---|---|
| P0-1 — `_parse_language()` rewrite + UNSUPPORTED handler at both language steps | ✅ code done, unit-tested (27/27 cases pass) |
| P0-2 — shared `CITY_ALIASES` in `apis.py` + use at both onboarding city steps | ✅ code done, unit-tested (16/16 cases pass, 64 aliases registered) |
| P0-3 — `_TEMPLATES["hinglish"]` real-Hinglish rewrite + `_DEFAULT_TEMPLATE` → English | ✅ code done, verified by sample-run |
| `py_compile` on onboarding.py, apis.py, reminders.py | ✅ clean |
| Rishi's DB row correction (`language='eng'` → `'english'`, possibly `city='Mum'` → `'Mumbai'`) | ⏳ `/adminreset` after deploy |
| Bug 2 — emergency contact name | ⏳ live retest after `/adminreset` |
| Bug 3 — bare-code auto-join | ⏳ live test after `/adminreset` |
| Module 19 end-to-end capability tests | ⏳ pending |
| Module 20 pilot prep | ⏳ pending |

---

## What changed in code (this run)

### `onboarding.py`

1. **New module constant** `POLITE_UNSUPPORTED_LANGUAGE_MESSAGE` (placed near
   the other onboarding message constants, right after `SELF_SETUP_BRIDGE_RECHECK`):

   > "My apologies — at present I can only converse in Hindi, English, or a
   > mix of the two. Would you like to continue in any of those?"

2. **`_parse_language()` rewritten** (replaces the old substring-only parser):
   - New `_UNSUPPORTED_LANG = "UNSUPPORTED"` sentinel.
   - New `_LANG_EXACT_ALIASES` dict — handles `eng`, `hin`, `mix`, `both`, `dono`,
     `angrezi`, Devanagari `हिंदी`/`हिन्दी`, `hindi and english`, `hinglish`, etc.
   - New `_UNSUPPORTED_LANG_TOKENS` tuple — matches `tamil`, `telugu`, `bengali`,
     `bangla`, `marathi`, `gujarati`, `punjabi`, `kannada`, `malayalam`, `urdu`,
     `odia`, `assamese`, `sanskrit`, `konkani`, `sindhi`, `kashmiri`, `maithili`,
     `bhojpuri`, `nepali`, and common non-Indian languages.
   - 5-stage resolution order (documented in docstring): exact → compound
     (hindi+english) → single → unsupported-token → default to UNSUPPORTED.
   - **Safe default:** unknown input returns `_UNSUPPORTED_LANG` rather than
     storing raw garbage. This is the only behavioural regression vs old code —
     gibberish like `"xyz"` no longer goes into the DB as-is. Correct for the
     pilot; if it bites later we can loosen.

3. **UNSUPPORTED gate at step 4 in BOTH flows:**
   - `handle_onboarding_answer` (child-led) — pre-check before `_save_answer`.
   - `_handle_self_setup_answer` (self-setup) — pre-check before
     `_save_self_setup_answer`.
   - Returns `POLITE_UNSUPPORTED_LANGUAGE_MESSAGE` and holds at step 4.

4. **City step rewrite at BOTH flows:**
   - Imports `CITY_ALIASES` and `canonicalize_city` from `apis`.
   - `_save_answer` step 3 (child-led) and `_save_self_setup_answer` step 3
     (self-setup) both now call `canonicalize_city(t)` and log a warning when
     the raw input is not in `CITY_ALIASES` (so pilot feedback can extend the
     list).

### `apis.py`

1. **New module-level `CITY_ALIASES` dict** (64 entries): covers Mumbai, Delhi,
   Bengaluru, Hyderabad, Chennai, Kolkata, Pune, Ahmedabad, Jaipur, Chandigarh,
   Gurugram, Noida, Lucknow, plus extras (Indore, Bhopal, Nagpur, Kochi,
   Trivandrum, Coimbatore, Vizag, Surat, Baroda, Patna, Ranchi, Bhubaneswar,
   Guwahati, Dehradun, Shimla, Goa/Panaji). Values are canonical display names,
   NO `,IN` suffix.

2. **New `canonicalize_city(raw: str) -> str` helper** — lowercase key lookup
   → canonical, falls back to title-case of the raw input if unmapped.

3. **`fetch_weather` retry simplified** — inline `_CITY_ALIASES` removed;
   retry now uses `CITY_ALIASES.get(city.lower(), city) + ",IN"`. Old rows
   still work because the lookup falls through the stored raw city.

### `reminders.py`

1. **`_TEMPLATES["hinglish"]` rewritten** — previously an exact copy of the
   Hindi string, now real Hinglish:

   ```
   "{address}, it's time for your *{medicine}*. 🙏\n"
   "Le lijiye aur ek 👍 bhej dijiye — bas itna hi."
   ```

2. **`_DEFAULT_TEMPLATE` changed from Hindi → English** — safety-net default
   when `language` column contains an unexpected value. Matters only for
   legacy rows (Rishi's current row with `language='eng'` now falls through
   to English instead of Hindi). After deploy + `/adminreset`, his row will
   have `language='english'` and this fallback won't be hit.

---

## Verification run (this session)

- `python3 -m py_compile onboarding.py apis.py reminders.py` → clean.
- `_parse_language` unit test: 27/27 cases pass, including Rishi's original
  failing input `"eng"` → `"english"`, compound `"hindi and english"` →
  `"hinglish"`, Devanagari `"हिंदी"` → `"hindi"`, and all unsupported-language
  tokens → `"UNSUPPORTED"`.
- `canonicalize_city` unit test: 16/16 cases pass including `"Mum"` →
  `"Mumbai"`, `"Del"` → `"New Delhi"`, `"Gurgaon"` → `"Gurugram"`, unknown
  `"Nainital"` → `"Nainital"` (title-case fallback preserves unmapped cities).
- `build_reminder_text` sample-run: English/Hindi/Hinglish templates all
  render correctly; unknown-language input (`'eng'`) now falls through to
  English default (previously fell through to Hindi).

---

## Next session — first moves

1. **Rishi pushes to Railway** (from terminal in `~/saathi-bot`):

   ```bash
   git add onboarding.py apis.py reminders.py CHECKPOINT.md CLAUDE.md
   git commit -m "P0 parser fixes: language + city + hinglish template"
   git push origin main
   ```

   Wait ~60s for Railway auto-deploy.

2. **`/adminreset`** on Telegram.
3. **Redo self-setup as Rishi**, using ambiguous inputs deliberately:
   - At city step: type `Mum` → completion message should say "in Mumbai".
   - At language step: type `eng` → should advance to family-members with
     `language` now stored as `english`.
   - (Bonus test) Type `tamil` first at language step → should get the polite
     refusal, NOT advance. Then type `english` → advances.
4. **Retest Bug 2 (emergency contact name):** `yes. my wife ishween 9833192304`
   → completion says "if **Ishween** would also like to get…".
5. **Test Bug 3 (bare-code auto-join):** `/familycode` → grab 6-char code.
   From wife's Telegram, send JUST the code (no `/join`). Expect confirmation
   prompt → reply `yes` → expect registration.
6. **Verify medicine reminders fire in English** — easiest is to add a
   reminder for 2 minutes from now during onboarding, then wait.

If all 6 pass: move to Module 19 end-to-end capability tests, then Module 20
pilot prep.

---

## Pilot-scope reminder (Rishi locked in this session)

**Pilot supports only English, Hindi, Hinglish.** Any other language gets
the polite refusal. Module 20 pilot prep docs must say this explicitly in
the adult-child-facing instructions so they don't promise a Tamil/Bengali
parent something we don't deliver.

---

## Deferred (non-blocking)

1. Emergency contact with no name (e.g. `yes my wife` with no name after) —
   `_extract_contact_name()` returns empty and the `or t` fallback stores the
   raw text. Low probability, fix if pilot surfaces.
2. Two `_USER_CACHE` dicts (`main.py` unbounded + `database.py` 5-min TTL)
   don't know about each other — every cache bug this month traces back to
   this. **First post-pilot task.**
3. 19 Apr audit findings: #3 EOL account_status in main cache; #6 same-turn
   opt-in + emergency VERIFY; #11/#12 cache hygiene; #15 weekly report skip
   logging. All low-probability.
4. Corner case: if a senior types a language we haven't thought of (e.g.
   `"gujarati english"` — compound of unsupported + supported), current
   `_parse_language` returns `"english"` because `has_english=True` fires
   before the unsupported-token scan. Arguably correct (they can speak
   English) but could surprise us. Acceptable for pilot.
