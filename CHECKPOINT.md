# CHECKPOINT — 22 April 2026

Session closed so Rishi can reopen in Opus 4.7 (this was an Opus 4.6 session).
Next session: read this file first, then RESUME.md, then CLAUDE.md, then progress.md.

---

## TL;DR — what the next session picks up

We came in planning to live-test three onboarding bugs from the 20 Apr session.
Before we ran those tests, Rishi surfaced a new issue: **medicine reminders
were firing in Hindi despite his language being English.** Root-causing that
led us to **two parser bugs** (language + city) that are pilot-blocking.

Those parser fixes are now the top priority. Bug 2 and Bug 3 live tests
are still pending but lower priority than the parser work.

---

## New issues found this session (not yet fixed)

### Issue A — Language parser accepts short forms silently and stores garbage

**File:** `onboarding.py`, `_parse_language()` at lines 1041–1067.

The parser does substring matching:
```python
if "english" in t: return "english"
if "hindi"   in t: return "hindi"
...
return t.strip()   # ← unmapped tokens stored raw
```

Short forms break it:
- `eng` → `"english" in "eng"` is False → falls through to `return "eng"`
- `hin` → same → stored as `"hin"`
- `mix`, `both` → stored raw
- `hindi and english` → works (matches both substrings, returns `"hinglish"`)
- `hindi english` → works (same reason)

**Rishi's case:** he typed `eng` during onboarding → DB now has `language = "eng"`.

**Cascade into reminders.py:**
```python
_TEMPLATES.get("eng".lower(), _DEFAULT_TEMPLATE)
# "eng" not a key → returns _DEFAULT_TEMPLATE
# _DEFAULT_TEMPLATE = _TEMPLATES["hindi"]  ← Hindi template
```

That's why Rishi received Hindi medicine reminders despite picking English.
Chat responses stayed English because DeepSeek has independent script
detection that reads recent messages — reminders bypass that entirely.

### Issue B — `_TEMPLATES["hinglish"]` is pure Hindi, not Hinglish

**File:** `reminders.py`, lines 109–123.

```python
_TEMPLATES = {
    "hindi":    "{address}, aapki *{medicine}* ki dawai ka waqt ho gaya hai. 🙏\n..."
    "hinglish": "{address}, aapki *{medicine}* ki dawai ka waqt ho gaya hai. 🙏\n..."  # ← identical to Hindi
    "english":  "{address}, it's time for your *{medicine}*. 🙏\n..."
}
```

Whoever wrote the hinglish slot copy-pasted the Hindi string. Needs to be
real Hinglish (English words + Hindi connectors, no "dawai").

### Issue C — City parser has zero validation or alias handling

**File:** `onboarding.py`, step 3 (child-led line 795–796) and step 3 in
self-setup (around line 871).

```python
elif step == 3:  # City
    update_user_fields(user_id, city=t.title())
```

Takes whatever the user types, does `.title()`, stores it.

**Effects when the user types short forms:**

| User types | Stored | Downstream impact |
|---|---|---|
| `Mum` | `Mum` | OWM weather fetch returns 404 (`apis.py` retries with `,IN` suffix but still fails); morning briefing literally says *"It's Wednesday in Mum…"* — confirmed in Rishi's Telegram log |
| `Del` | `Del` | same pattern |
| `blr`, `blore`, `bby` | stored raw | weather fails silently |

**City IS actively used — four places:**
1. `apis.fetch_weather(city)` — morning briefing weather line
2. `deepseek.py` line 26–27 — timezone offset for time-awareness in system prompt (falls back to 5.5 / IST if city unknown, so Indian cities accidentally still work for clocks, but diaspora seniors would break)
3. `rituals.py` line 389 — morning briefing text: *"Today is Wednesday, [date], in [city]"*
4. `deepseek.py` line 267–268 — `Lives in: {city}` personalisation context

So city isn't vestigial. It's real. It just isn't normalised at capture time.

---

## Agreed fix plan for next session

### Scope decision (Rishi, this session)
> At the pilot stage we are only supporting English, Hindi, and Hinglish.
> None of the other languages.
>
> If a user types Tamil / German / Bengali / anything else, the bot should
> reply politely: *"My apologies. At present I can only converse in Hindi,
> English, or a mix of the two. Would you like to continue in any of those?"*
> and keep them on the language question until they pick one of the three.

### Fix 1 — Rewrite `_parse_language()` (`onboarding.py`)

New behaviour:
- Lowercase + strip the input.
- **Explicit short-form mapping** (check before substring matching):
  - `{"eng", "english", "angrezi", "inglish"}` → `english`
  - `{"hin", "hindi", "हिंदी"}` → `hindi`
  - `{"mix", "both", "hinglish", "mixed", "mix of both", "dono", "hindi english", "hindi and english", "english and hindi", "english hindi"}` → `hinglish`
- **Unsupported-language detection** — if the input matches any non-supported
  language token, return a sentinel like `"UNSUPPORTED"`:
  - Tokens to match: `tamil`, `telugu`, `bengali`, `bangla`, `marathi`,
    `gujarati`, `punjabi`, `kannada`, `malayalam`, `urdu`, `oriya`,
    `odia`, `assamese`, `sanskrit`, `konkani`, `spanish`, `french`,
    `german`, `italian`, `chinese`, `japanese`, `portuguese`, `arabic`,
    `russian`.
- **Fallback** for anything else — return `"UNSUPPORTED"` rather than
  storing raw text. Safer default than silently storing garbage.

### Fix 2 — Handle `"UNSUPPORTED"` at the language step

In the onboarding step handler (child-led step 4 — line 797; self-setup
step that asks language — line 874):

```python
parsed = _parse_language(t)
if parsed == "UNSUPPORTED":
    # Send polite refusal; do NOT advance the step.
    return POLITE_UNSUPPORTED_LANGUAGE_MESSAGE
update_user_fields(user_id, language=parsed)
advance_onboarding_step(...)
```

Message copy (agreed with Rishi):
> "My apologies — at present I can only converse in Hindi, English, or a
> mix of the two. Would you like to continue in any of those?"

### Fix 3 — Rewrite `_TEMPLATES["hinglish"]` (`reminders.py`)

Proposed actual Hinglish copy (not final — Rishi can tweak):
```
"{address}, aapki *{medicine}* ka time ho gaya. 🙏\n"
"Ek 👍 bhej dijiye jab le lein — bas itna kaafi hai."
```

Also change `_DEFAULT_TEMPLATE` fallback: it currently defaults to Hindi.
After Fix 1, no unmapped values should reach it — but as belt-and-braces,
default should be **English** (the one language we're guaranteed the
pilot audience understands), not Hindi.

### Fix 4 — City parser with alias map (`onboarding.py` + `apis.py`)

Shared module-level alias map (probably in `apis.py`, imported by
onboarding). Suggested minimum list for the pilot:

| User types | Canonical |
|---|---|
| `mum`, `mumbai`, `bombay`, `bby` | `Mumbai` |
| `del`, `delhi`, `new delhi`, `ndl` | `New Delhi` |
| `blr`, `bengaluru`, `bangalore`, `blore` | `Bengaluru` |
| `hyd`, `hyderabad`, `hydrabad` | `Hyderabad` |
| `chn`, `chennai`, `madras` | `Chennai` |
| `kol`, `kolkata`, `calcutta`, `cal` | `Kolkata` |
| `pune` | `Pune` |
| `ahmedabad`, `amdavad`, `ahd` | `Ahmedabad` |
| `jaipur`, `jpr` | `Jaipur` |
| `chandigarh`, `chd` | `Chandigarh` |
| `gurgaon`, `gurugram`, `ggn` | `Gurugram` |
| `noida` | `Noida` |
| `lucknow`, `lko` | `Lucknow` |

At step 3 in both onboarding flows:
```python
canonical = CITY_ALIASES.get(t.lower()) or t.strip().title()
update_user_fields(user_id, city=canonical)
if t.lower() not in CITY_ALIASES:
    logger.warning("ONBOARDING | city not in alias map: %r", t)
```

The warning is so we can extend the list when pilot feedback surfaces
something we missed. We still accept the user's input — we just log it.

### Fix 5 — One-off correction to Rishi's existing DB row

Rishi's row currently has `language = "eng"` and possibly `city = "Mum"`.
After deploy, either:
- Run `/adminreset` and redo onboarding from scratch, OR
- One-off SQL:
  ```sql
  UPDATE users SET language = 'english', city = 'Mumbai'
  WHERE telegram_user_id = <rishi_id>;
  ```

Easier: `/adminreset`. Cleaner given we haven't validated the migration.

---

## Still pending from 20 Apr — do NOT skip

Once the parser fixes are in and Rishi's row is corrected, live-test
these. Both were supposed to run today but got deferred:

### Bug 2 live test — emergency contact name parser
```
/adminreset
```
Go through self-setup. At emergency contact step:
```
yes. my wife ishween 9833192304
```
Completion message should say *"if **Ishween** would also like to get…"*.

> Status note: `_extract_contact_name()` regex with `\b` word boundaries
> is already committed and pushed (git tree clean, origin/main up to date
> as of this session start). CHECKPOINT and RESUME were stale on 20 Apr
> claiming it wasn't pushed — corrected here. The fix IS live; it just
> hasn't been live-tested with a fresh onboarding run.

### Bug 3 live test — bare-code auto-join
Same account. Run `/familycode`. Grab the 6-char code. From wife's
Telegram, send JUST the code (no `/join`). Expected:
1. Bot: *"This code will connect you to \*rishi\*'s Saathi. Is that correct? Reply \*yes\* or \*no\*."*
2. She replies `yes`.
3. Bot sends welcome.
4. She types anything → relayed to Rishi.

---

## Files that will change in next session

| File | What | Priority |
|---|---|---|
| `onboarding.py` | Rewrite `_parse_language`; add unsupported-language handler at both language steps; rewrite city step at both onboarding paths to use alias map | P0 |
| `apis.py` | Export shared `CITY_ALIASES` dict; ensure existing weather retry logic uses the canonical form | P0 |
| `reminders.py` | Fix `_TEMPLATES["hinglish"]`; change `_DEFAULT_TEMPLATE` to English | P0 |
| DB row for Rishi | Correct via `/adminreset` after deploy | P0 |
| Live Bug 2 + Bug 3 tests | After parser fixes deploy | P1 |
| `CLAUDE.md` | Session log entry | end of next session |

---

## Rishi's pilot-scope reminder (don't drift from this)

> **Pilot supports only English, Hindi, Hinglish. Anything else → polite refusal.**

When we build Module 20 pilot prep docs, the setup walkthrough should say
this explicitly in the adult-child-facing instructions so they don't
promise a Tamil-speaking parent something we don't deliver.

---

## Deferred non-blocking items (still open from earlier sessions)

1. Emergency contact with no name (e.g. `yes my wife`) — stores raw fallback. Low probability, fix if pilot surfaces.
2. Two `_USER_CACHE` dicts (`main.py` unbounded + `database.py` 5-min TTL) don't know about each other — every cache bug this month traces back to this. **First post-pilot task.**
3. 19 Apr audit: #3 EOL account_status in main cache; #6 same-turn opt-in + emergency VERIFY; #11/#12 cache hygiene; #15 weekly report skip logging. All low-probability.
