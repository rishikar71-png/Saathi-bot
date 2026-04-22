# CHECKPOINT — 22 April 2026 (Opus 4.7 continuation, session 2)

**Read order for next session:** this file → RESUME.md → CLAUDE.md → progress.md.

---

## TL;DR — state at end of this run

Two sets of changes are in the working tree, **verified and unit-tested but
NOT yet pushed to Railway**:

1. The three P0 parser fixes from the earlier 22 Apr Opus-4.6 session
   (language parser + shared city alias map + hinglish template).
2. A new diaspora pilot layer: per-user timezone support for users in
   LA / NY / Melbourne / London / Dubai / Singapore / Sydney etc., plus an
   admin-only `/setcity` command for travel & onboarding corrections.

Rishi needs to `git push` from his terminal. After deploy, the pending live
tests from the earlier run still apply, plus a smoke test for the new
diaspora flow.

| Item | Status |
|---|---|
| P0-1 — `_parse_language()` rewrite + UNSUPPORTED handler at both language steps | ✅ code done, unit-tested (27/27) |
| P0-2 — shared `CITY_ALIASES` in `apis.py` + use at both onboarding city steps | ✅ code done, unit-tested (23/23 with diaspora extensions) |
| P0-3 — `_TEMPLATES["hinglish"]` real-Hinglish rewrite + `_DEFAULT_TEMPLATE` → English | ✅ code done, verified by sample-run |
| Diaspora — `CITY_ALIASES` extended to 132 entries (LA/SF/NY/Seattle/Chicago/Boston/DC/Toronto/Vancouver/London/Dubai/Singapore/Sydney/Melbourne/Auckland/Paris/Berlin/etc.) | ✅ code done, 23/23 cases pass |
| Diaspora — new `CITY_TIMEZONE` IANA map (55 entries) + `get_iana_timezone()` helper in `apis.py` | ✅ code done, 19/19 cases pass |
| Diaspora — `deepseek.py` rewired to use `zoneinfo.ZoneInfo` per user; date/time labels now show user's actual zone (PDT/AEST/BST/etc.) instead of "IST" | ✅ code done, live-clock test clean |
| Diaspora — `rituals.py` scheduler filters by each user's local HH:MM and dedupes on user-local date; `record_first_message` uses local waking-hours window | ✅ code done, dry-run confirms per-user matching |
| Diaspora — `memory_questions.py` Wed/Sun prompts evaluated in user-local weekday + HH:MM; `send_memory_prompt` dedupes on user-local date | ✅ code done |
| Diaspora — `/setcity <telegram_id> <city>` admin-only command in `main.py` (same gate as `/adminreset`) | ✅ code done |
| `py_compile` clean on all 7 touched files | ✅ |
| Rishi's DB row correction (`language='eng'` → `'english'`, possibly `city='Mum'` → `'Mumbai'`) | ⏳ `/adminreset` after deploy |
| Bug 2 — emergency contact name | ⏳ live retest after `/adminreset` |
| Bug 3 — bare-code auto-join | ⏳ live test after `/adminreset` |
| Diaspora smoke test — set an LA or Mumbai test city via `/setcity`, verify check-in fires at local 08:00 and date/time label shows PDT/IST | ⏳ after deploy |
| Module 19 end-to-end capability tests | ⏳ pending |
| Module 20 pilot prep | ⏳ pending |

---

## Files changed in this run

### `onboarding.py` (earlier in this run, pre-compaction)

- `POLITE_UNSUPPORTED_LANGUAGE_MESSAGE` constant added.
- `_parse_language()` rewritten: short-form `_LANG_EXACT_ALIASES` map,
  `_UNSUPPORTED_LANG_TOKENS` tuple, 5-stage resolution (exact → compound
  → single → unsupported-token → UNSUPPORTED default). Gibberish no longer
  stored raw — it becomes `"UNSUPPORTED"` and triggers the polite refusal.
- UNSUPPORTED gate added at step 4 in both `handle_onboarding_answer`
  (child-led) and `_handle_self_setup_answer` (self-setup).
- Both city steps now call `canonicalize_city(t)` with warning-log when
  the raw input isn't in the alias map.

### `apis.py`

- `CITY_ALIASES` (was 64, now 132): added all diaspora cities commonly
  cited by pilot interest list — LA / SF / NY / Seattle / Chicago / Boston
  / DC / Dallas / Houston / Atlanta / Toronto / Vancouver / London / Dubai
  / Abu Dhabi / Doha / Singapore / Hong Kong / Sydney / Melbourne / Auckland
  / Paris / Berlin / Frankfurt / Amsterdam / Zurich. Both spellings + IATA
  airport codes covered (nyc/ny, sfo/sf, yyz/toronto, ams/amsterdam, etc.).
- New module-level `CITY_TIMEZONE: dict[str, str]` IANA-name map (55 keys)
  — keys match canonical CITY_ALIASES values. All Indian cities → Asia/Kolkata.
- New `_IST_TZ` constant + `get_iana_timezone(city) -> str` helper that
  accepts either canonical ("Mumbai") or raw user input ("mum") and falls
  back to `Asia/Kolkata` for unknown cities. Lets diaspora users get their
  actual local clock while preserving pre-22-Apr behaviour for Indian users.
- Old `canonicalize_city()` + `fetch_weather()` retry paths unchanged.

### `deepseek.py`

- Replaced the hardcoded `_CITY_TIMEZONE_OFFSET` dict (which mapped only
  Indian cities to 5.5) with `_user_tz(user)` / `get_user_local_hour(user)`
  / `get_user_local_now(user)` backed by `zoneinfo.ZoneInfo` + the shared
  `apis.get_iana_timezone` helper. DST is handled automatically.
- Lines 283-291 — the "Today's date" + "User's current local time" injection
  in the system prompt no longer hardcodes `(IST)` / `IST approx`. It now
  uses the user's local `datetime` and its `.tzname()` — so DeepSeek
  receives labels like `(PDT)`, `(AEST)`, `(BST)` for diaspora users.

### `rituals.py`

- New `_user_now(city)` / `_user_hhmm(city)` / `_user_date(city)` /
  `_user_hour(city)` / `_user_dow(city)` helpers alongside the existing
  IST helpers (which are still used for nightly bot-housekeeping jobs).
- `_get_users_due_for_ritual(ritual_type)`: SQL now fetches ALL active
  onboarded users; Python-side filter keeps only those whose stored
  check-in time matches their own local HH:MM. Dedupe against `ritual_log`
  now keyed on the user's local date (prevents double-sends around
  local-midnight transitions).
- `check_and_send_rituals`: no longer passes global `now_hhmm`/`today`
  into the due-users query. Nightly adaptive-learning / day-counter /
  EOL-deletion gate remains on IST (global bot-housekeeping).
- `record_first_message(user_id)`: fetches the user's city from DB, then
  uses their local clock for waking-hours check + activity_date + day_of_week.
  Critical for LA users — 10am PDT = 10:30pm IST, which the old check would
  have excluded as "late-night".

### `memory_questions.py`

- New `_user_now(city)` helper (adds `from apis import get_iana_timezone`
  and `zoneinfo.ZoneInfo`).
- `check_and_send_memory_prompts()`: now iterates ALL active users and
  filters in Python by user-local weekday (2=Wed, 6=Sun) + user-local
  HH:MM == stored morning_checkin_time + user-local not-already-sent-today.
- `send_memory_prompt()`: dedupe key for `memory_prompt_log` now uses the
  user's local date (fetches `city` via a single DB read). Without this,
  diaspora users could get two prompts on a single local day when their
  clock is ahead/behind of IST.

### `main.py`

- New admin-only `setcity_command(update, context)` — same gate as
  `adminreset_command` (`update.effective_user.id != 8711370451`). Takes
  `<telegram_id> <city>` (city may be multi-word, e.g. "New York"),
  canonicalizes via `canonicalize_city()`, writes via `update_user_fields`,
  invalidates the cache, and reports stored name + IANA timezone back to
  the admin. Warns if the raw input isn't in `CITY_ALIASES` — the city
  still stores (title-cased) but weather won't resolve and tz falls back
  to IST.
- Handler registered: `app.add_handler(CommandHandler("setcity", setcity_command))`
  right after `/adminreset`.

---

## Verification run (this session)

- `python3 -m py_compile onboarding.py apis.py reminders.py deepseek.py rituals.py memory_questions.py main.py` → clean.
- 23/23 `canonicalize_city` cases pass (new: LA, NY, SF, Mel, Toronto,
  London, Dubai, Singapore + all earlier Indian cases).
- 19/19 `get_iana_timezone` cases pass.
- Live-clock test — UTC 05:05 on 22 Apr 2026:
  - Mumbai: 10:35 IST (22 Apr)
  - Los Angeles: 22:05 PDT (21 Apr — correct)
  - New York: 01:05 EDT (22 Apr)
  - Melbourne: 15:05 AEST (22 Apr)
  - London: 06:05 BST (22 Apr)
  - Dubai: 09:05 +04 (22 Apr)
  - Mumbai–LA hour diff = 12 ✓
- Scheduler dry-run: users with `target_time == _user_hhmm(city)` return
  DUE, others return skip. LA user's `local_date` correctly rolls back to
  previous calendar day while Mumbai/Melbourne sit on current date.
- 27/27 language parser unit cases pass (from earlier in this run).
- 16/16 base city canonicalization cases pass (from earlier in this run).

---

## Next session — first moves

1. **Rishi pushes to Railway** (from terminal in `~/saathi-bot`):

   ```bash
   git add onboarding.py apis.py reminders.py deepseek.py rituals.py memory_questions.py main.py CHECKPOINT.md CLAUDE.md
   git commit -m "P0 parser fixes + diaspora pilot timezone support + /setcity admin command"
   git push origin main
   ```

   Wait ~60s for Railway auto-deploy.

2. **`/adminreset`** on Telegram (for Rishi's own row).
3. **Redo self-setup as Rishi**, using ambiguous inputs deliberately:
   - At city step: type `Mum` → completion message should say "in Mumbai".
   - At language step: type `eng` → should advance to family-members with
     `language` now stored as `english`.
   - (Bonus) Type `tamil` → polite refusal; then `english` → advances.
4. **Retest Bug 2 (emergency contact name):** `yes. my wife ishween 9833192304`
   → completion says "if **Ishween** would also like to get…".
5. **Test Bug 3 (bare-code auto-join):** `/familycode` → grab 6-char code;
   from wife's Telegram send JUST the code (no `/join`) → expect
   confirmation prompt → reply `yes` → expect registration.
6. **Verify English medicine reminders fire** (seed a 2-min reminder
   during onboarding, wait for it).
7. **Diaspora smoke test (optional but recommended before pilot):**
   - `/setcity <your_tg_id> Los Angeles` — verify admin reply shows
     `stored: Los Angeles`, `timezone: America/Los_Angeles`.
   - `/setcity <your_tg_id> Melbourne` — verify `Australia/Melbourne`.
   - `/setcity <your_tg_id> Nainital` — verify the warning-path message
     (unknown city, falls back to IST).
   - `/setcity <your_tg_id> Mumbai` — restore.
   - Any morning briefing after the switch should reference the correct
     local day + tz in the system-prompt context. Main way to eyeball this:
     ask Saathi "what day is it?" in conversation and check the response.

If all pass: move to Module 19 end-to-end capability tests, then Module 20
pilot prep.

---

## Pilot-scope reminder

**Pilot supports only English, Hindi, Hinglish.** Any other language gets
the polite refusal. Module 20 pilot prep docs must say this explicitly in
the adult-child-facing instructions so they don't promise a Tamil/Bengali
parent something we don't deliver.

**Diaspora pilot users (LA / NY / Melbourne):** check-ins, memory prompts,
daily rituals, and DeepSeek's time-awareness all correctly use their local
clock. OWM weather for non-Indian cities is an accepted pilot limitation —
fetch_weather still appends `,IN` in the retry fallback, so non-Indian
cities return None (morning briefing silently omits weather, no crash).
This is documented for next session if we want to expand.

---

## Deferred (non-blocking)

1. **OWM weather outside India** — current `fetch_weather` retry falls
   back to `city,IN` which returns nothing for LA/NY/Mel. Acceptable for
   pilot (weather silently omitted); add a country-code fallback table
   post-pilot if diaspora users ask.
2. Emergency contact with no name (e.g. `yes my wife` with no name after) —
   `_extract_contact_name()` returns empty and the `or t` fallback stores
   the raw text. Low probability, fix if pilot surfaces.
3. Two `_USER_CACHE` dicts (`main.py` unbounded + `database.py` 5-min TTL)
   don't know about each other — every cache bug this month traces back
   to this. **First post-pilot task.**
4. 19 Apr audit findings: #3 EOL account_status in main cache; #6 same-turn
   opt-in + emergency VERIFY; #11/#12 cache hygiene; #15 weekly report skip
   logging. All low-probability.
5. Corner case: compound-of-unsupported-plus-supported (e.g. `"gujarati
   english"`) — current `_parse_language` returns `"english"` because
   `has_english=True` fires before the unsupported-token scan. Arguably
   correct (they can speak English) but could surprise us. Acceptable
   for pilot.
6. `CITY_TIMEZONE` is a static map — if a pilot user's city isn't in it
   they silently fall back to IST. `/setcity` prints the fallback zone
   in its reply so a sharp admin catches this; otherwise it's only
   discoverable via logs. Post-pilot: either extend the map aggressively
   or switch to a library (`tzwhere`, `timezonefinder`) that resolves
   arbitrary cities via geocoding.
