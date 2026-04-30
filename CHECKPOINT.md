# CHECKPOINT — Resume after 30 Apr 2026 voice + persona session

**Read order for next session:** this file → CLAUDE.md → progress.md.

---

## State at session close

**HEAD on `origin/main`:** assumes `d9d9fd2` once Rishi applies this final
session-close patch. The chain pushed today (30 Apr):

```
d9d9fd2  Wire voice into greeting + identity intercept
626bb67  Wire voice into Protocol 1/3/4 + fix persona in rituals
8b77887  Fix Durga Ji ritual regression + raise TTS char ceiling
```

`8b77887` and `626bb67` were verified live this session. `d9d9fd2` was
generated last; this checkpoint commit is bundled into the same patch
so Rishi applies one file.

---

## Verified live this session

| What | Verification | Source commit |
|---|---|---|
| Bug A (TTS Hindi voice routing) | Code-trace: `LANG \| override: stored=english detected=hinglish effective=hinglish` log + tts.py `_VOICE_MAP['hinglish']`→`hi-IN-Neural2-A`. Voice test was invalidated by Whisper translation (Bug F below) — confirmed via text input. | `aa8a2a9` (yesterday) |
| Bug B (per-turn language nudge) | Live: English voice query after Hinglish exchange → English reply (no autoregression bleed). | `aa8a2a9` |
| Bug E2 (cricket news via RSS) | Live: BAN vs NZ T20I rain-off + Nahid Rana NOC headlines from RSS — real recent stories. | `aa8a2a9` |
| Durga Ji → Ma fix | Live: morning briefing post-deploy reads "Good morning, Ma". rituals.py `user_context` was missing `preferred_salutation` → DeepSeek `address_lock` fell to `Durga Ji`. | `8b77887` |
| Protocol 3 voice | Live: trigger "mere bete ne mujhse paise mange hain — kya karu?" delivered text + voice note. | `626bb67` |

---

## NOT yet verified live

| Item | Why deferred | How to verify |
|---|---|---|
| Persona threading in rituals (`626bb67`) | Rishi's persona='friend' = default fallback, no observable difference. | Manual DB update: `UPDATE users SET persona='grandchild' WHERE user_id=8711370451;` then wait for next ritual — tone should shift to enthusiastic-curious. |
| `_TTS_MAX_CHARS` raised 180→400 (`8b77887`) | No long reply triggered this session. | Send "tell me about Mumbai" or extended news roundup → reply ~250-350 chars → confirm voice fires (no `skipped (reply too long)` in logs). |
| Protocol 1 voice (`626bb67`) | Crisis trigger phrases not safe to live-test casually. | Skip — code path identical to P3 which IS verified. |
| Protocol 4 voice (`626bb67`) | Same caution. | Skip. |
| Greeting voice (`d9d9fd2`) | Patch not yet applied at session close. | After deploy: send `hello` → "Good morning, Ma" text + voice note. |
| Identity intercept voice (`d9d9fd2`) | Same. | After deploy: send `who are you?` → "Just someone to chat with — that's really all 🙏" text + voice. Bonus: `kya tum insaan ho?` → Hindi reply + Hindi voice. |

---

## Bugs surfaced this session

### Bug E1 — REGRESSION on `aa8a2a9` cricket fix

Test: "aaj cricket mein kya ho raha hai?"
Expected: today's IPL match (e.g. MI vs SRH 19:30) or scripted "no match today".
Actual: *"Aaj koi match live nahi chal raha hai, Ma. Ek match hai — Guyana Amazon Warriors vs Perth Scorchers — lekin woh 31 July ko hai, raat 11 baje."*

Two filters that should have stopped this both failed:

1. **Strict IST date filter** — should reject anything that isn't today.
   Returned a match 3 months out.
2. **India/IPL team keyword filter** — should reject anything not India
   national or one of 10 IPL franchises. Guyana Amazon Warriors is CPL;
   Perth Scorchers is BBL.

Likely cause: the `aa8a2a9` change added a second CricAPI call to
`/matches` (full schedule) and merged results — but the merge bypassed
`_find_india_match`'s filter. Worse than pre-fix: pre-fix scripted
"no match today" would have fired cleanly.

**Action next session:** Read apis.py `_find_india_match` and the merge
logic from `aa8a2a9`. Either (a) ensure both filters run on merged
results, or (b) restrict `/matches` results to next-N-days only and to
IPL/India team list pre-filter. Strict integration test required (V4) —
the unit tests for `aa8a2a9` did not catch this.

### Bug D — crime/horror filter leak in world news (DEFERRED FROM 29 APR)

Today: world news returned "British influencer missing after Morocco
trip, phone switched off for days" — not as graphic as 29 Apr's "Japan
zoo staffer dumped wife's body in incinerator" but same category.

**Action next session:** Expand `_IRRELEVANT_TOPIC_SIGNALS` in apis.py.
Add: "missing after", "phone switched off", "feared abducted", "body in",
"dumped body", "killed wife", "murdered", "incinerator", "dismembered".
Or flip filter to allowlist.

### Bug F (NEW) — Whisper translates Hindi voice to English text

When user's stored language is `english`, whisper.py passes
`lang_hint=en` and Whisper transcribes Hindi audio AS English text
(silent translation, not a transcription error).

Pilot-blocking: a senior whose family set them up as English speakers
but who naturally voice-notes in Hindi will get this English-translation
pipeline, lose Hindi TTS, and feel like the bot doesn't understand them.

**Action next session:** Three options to evaluate —
(a) drop the language hint and let Whisper auto-detect;
(b) explicitly use `language='hi'` for Indian users by default and
accept slightly worse English transcription;
(c) skip the hint and inspect the result post-hoc.

### Bug G (NEW) — Protocol 3 language reads stored profile, not per-message

P3 trigger in Hinglish ("mere bete ne mujhse paise mange hain — kya
karu?") returned the English response. `_get_protocol3_response(language)`
reads `user_row["language"]="english"` from stored profile, ignoring
per-message detected language.

**Action next session:** Refactor `check_protocol3` to accept (and prefer)
the per-message effective language. Same pattern as the deepseek
per-turn language nudge from `aa8a2a9`.

### Bug H (NEW) — transient memory retrieval warning

`WARNING - DEEPSEEK | memory retrieval failed: cannot commit - no transaction is active`
— single instance during 29 Apr test, didn't break the reply. Possible
WAL/connection-pool race.

**Action next session:** If recurs, dig into `memory.get_relevant_memories`
for connection-pool issue. Low priority.

---

## Module 19 capability test progress

**PASS:** music tests 1-5 (specific song / genre / artist / vague-fallback /
false-positive guard); E2 (cricket news); test 9 (weather); test 10
(Hindi voice vulnerability handling); test 11 (English voice after
Hinglish); test 12 (TTS quality after Bug A fix).

**FAIL:** E1 (cricket schedule regression); test 7 (world news filter — Bug D).

**To run next session:** test 6 (India news quality), self-setup mode
end-to-end, family bridge bare-code, Hindi conversation 5+ turns,
`_TTS_MAX_CHARS=400` verification with longer replies, persona effect
verification (manual DB update first).

---

## Open items deferred (not pilot-blockers)

| Item | Notes |
|---|---|
| Other hardcoded `None`s in rituals user_context | `spouse_name`, `health_sensitivities`, `family_members` — same bug pattern as persona/salutation. `family_members` has a Module 7 TODO. Surgical scope held this session. |
| Conversational-intent → structured tables | Names/medicines/grandkids in normal chat → `memories` prose, not `family_members`/`medicine_reminders` rows. Post-pilot. |
| Hindi numerals | "ek baje" / "do baje" / "teen baje" not parsed by `_normalize_time`. v1.5. |
| Two `_USER_CACHE` dicts | main.py's unbounded vs database.py's 5-min TTL — collapse post-pilot. |
| Memory extraction noise: "user is called Ma" | "Ma" is salutation, not name. Tighten extraction prompt or add salutation-aware skip. |
| `short_reply_disengagement` over-fires on contextual yes/no | Suppress when previous Saathi turn ended with `?`. Edge case. |
| `_RSS_FEEDS_CRICKET` URL verification | NDTV Sports always works; other URLs are best-effort. |
| Untracked `*.patch` files in working tree | Safe to `rm` after each `git am`. |

---

## Apply the final patch

```bash
cp "/Users/rishikar/AI Projects/saathi_session_close_30apr.patch" ~/saathi-bot/
cd ~/saathi-bot
git am saathi_session_close_30apr.patch
git push origin main
```

This single patch contains two commits: `d9d9fd2` (greeting + identity
voice wiring) and the docs commit (this CHECKPOINT.md update + CLAUDE.md
session log entry).

After deploy, verify the two greeting/identity tests above. Then start
next session per "To run next session" list above.

---

## Workflow note (V7/V8)

- Code change → patch deliverable, /tmp/clone work, NO Edit tool on
  `/Users/rishikar/saathi-bot/`.
- Documentation update → commit-in-place mode is fine, but bundling docs
  WITH the code patch (as done here) is cleaner — single application,
  one commit chain, no risk of forgotten doc commits.
- After any patch failure with `does not match index`: V8 stale-lock
  recovery checklist (`rm -f .git/index.lock` + `git am --abort` + V8
  diagnosis on edit-tool-vs-patch choice).
