# CHECKPOINT — Resume after 1 May 2026 Bug E1''''' session

**Read order for next session:** this file → CLAUDE.md → progress.md.

---

## State at session close

**HEAD on `origin/main`:** the cricket-schedule pivot commit + this docs commit.

```
<E1''''' commit>  Bug E1''''': pivot to static IPL 2026 schedule
<docs commit>     1 May 2026 session close: CHECKPOINT/CLAUDE/progress
87029e7           30 Apr (cont. 3) session close docs
bc03385           Bug E1'' — RSS schedule fallback (now superseded)
```

**Live verified at session close:** `aaj cricket hai kya?` →
*"Haan Ma, aaj IPL mein match hai — Rajasthan Royals aur Delhi Capitals
aapas mein khel rahe hain. Shaam ko 7:30 baje se hai, Jaipur mein."*
Logs: `APIS | IPL schedule lookup SUCCESS for 2026-05-01 | TODAY (IPL) —
Rajasthan Royals vs Delhi Capitals — 43rd Match — at Sawai Mansingh
Stadium, Jaipur — start 7:30 PM IST`. No fabrication, salutation
respected, language matched.

---

## Bug E1''''' — what shipped this session

**The arc** (4 attempts in one session, only the last works):

| Attempt | Approach | Outcome |
|---|---|---|
| Bug E1''' | Scrape ESPNCricinfo homepage (SSR JSON: title + startTime UTC) | Worked from Mac IP (200), but Cloudflare 403'd Railway's datacenter IP |
| Bug E1'''' | Scrape Cricbuzz homepage (different infra, no Cloudflare) | Returned 200 from Railway, but the structured matchInfo JSON is streamed via React Server Components AFTER initial SSR — only 1 occurrence in curl response vs 84 in post-hydration browser save |
| Static (E1''''') | data/ipl_2026_schedule.json — extracted from one-off browser save of Cricbuzz IPL series page | **Works.** 70 league-phase matches, 12 doubleheader days, full venue/time data. Zero network failure modes. Refresh via `scripts/refresh_ipl_schedule.py` when needed. |

**Code changes:**
- `apis.py` -111 LoC (drop ESPN + Cricbuzz scraper code, add `_load_ipl_schedule()` + `_format_ipl_lookup()` + `lookup_today_ipl_match()`)
- `main.py` -97 LoC (swap import + call site, simplify /cricdebug probe to call lookup directly)
- `data/ipl_2026_schedule.json` NEW (~10KB, 70 matches, season range 2026-03-28 → 2026-05-24)
- `scripts/refresh_ipl_schedule.py` NEW (one-off refresh tool — parses any saved Cricbuzz HTML, regenerates the JSON)
- `scripts/smoke_test_espn_scrape.sh`, `scripts/smoke_test_cricbuzz.sh`, `scripts/diag_cricbuzz.py` DELETED (dead diagnostic scripts)

**V4 tests passing (6/6):**
1. Today=2026-05-01 → RR vs DC, Sawai Mansingh, 7:30 PM IST
2. Yesterday=2026-04-30 → RCB vs GT, Narendra Modi, Ahmedabad
3. Doubleheader 2026-05-03 → SRH vs KKR (3:30 PM) + GT vs PBKS (7:30 PM), 2 lines, ordered
4. Tomorrow=2026-05-02 → CSK vs MI, MA Chidambaram, Chennai
5. Post-schedule 2026-06-01 → None (graceful)
6. Pre-schedule 2026-03-01 → None (graceful)

**New module-level lookup helper:** `_format_ipl_lookup(date_iso)` — pure
function, takes any IST date string. `lookup_today_ipl_match()` is a thin
wrapper that pulls today's IST date and delegates. The helper exists so
unit tests can target arbitrary dates without mocking `datetime.now()`.

---

## Live test plan — first task next session

**Step 1 — Bug C2 stability re-test (cache TTL passed by next session):**

```
Aaj ki main khabar kya hai?
```

Pass: real Indian top headlines (e.g., Assam exit poll, oil prices, Indus
river analysis, NOT VyOS / global tech press). If VyOS reappears, there's
a code path the 30 Apr fix missed.

**Step 2 — Family bridge bare-code (Module 19 Task 3):**

Needs a second Telegram user. From a family member's account (different
TG ID), paste the family code (no `/join` command). Expected:
- Confirmation prompt → "Reply yes or no"
- Reply `yes` → register
- Cache invalidates so the new family member's messages route correctly
  in the bridge

This flow shipped 22 Apr (Bug 3). Never live-tested.

**Step 3 — Self-setup mode end-to-end (Module 19 Task 2):**

`/adminreset` your own user. On opening pick **"for myself"**. Expected:
- Day 1 asks 5 questions
- Day 2 (after wall-clock advance) asks remaining
- Bridge-deferred branch: pick "for myself, not now"

Wall-clock dependent — schedule when convenient.

---

## Module 19 — final scoreboard

| Task | Status | Notes |
|---|---|---|
| 1 — India news quality | ✅ Bug C2 fixed + verified | Pending one stability re-test after cache TTL passes |
| 4 — Hindi 5-turn | ✅ strong pass | Logged 30 Apr (cont. 3) |
| 5 — Long-reply TTS | ✅ partial pass | 300+ char regime not exercised |
| 6 — Persona effect | ✅ partial pass | caring_child ↔ grandchild collapse on emotional prompts (post-pilot copy work) |
| Bug E1 family (' through ''''') | ✅ resolved via static schedule | Live-verified this session |
| 3 — Family bridge bare-code | ⏳ deferred | Needs 2nd TG user |
| 2 — Self-setup mode E2E | ⏳ deferred | Wall-clock dependent |

After Tasks 2 + 3 clear: Module 19 done → Module 20 pilot prep.

---

## New diagnostic shipped this session

`/cricdebug` second message now contains:
```
=== IPL static schedule lookup (Railway-side) ===
file_meta: matches=70 | range=['2026-03-28', '2026-05-24']

Result:
TODAY (IPL) — Rajasthan Royals vs Delhi Capitals — 43rd Match — at
Sawai Mansingh Stadium, Jaipur — start 7:30 PM IST
```

If `file_meta` shows `matches=?` or `range=?`, the JSON file isn't being
loaded — check Railway deploy includes `data/`.

---

## IPL schedule maintenance

**The static schedule expires.** Refresh ritual:

```bash
# 1. In Chrome, open:
#    https://www.cricbuzz.com/cricket-series/9241/indian-premier-league-2026/matches
# 2. Cmd+S → "Webpage, HTML Only" → save (e.g. to ~/Downloads/)
# 3. Run:
python3 scripts/refresh_ipl_schedule.py ~/Downloads/IPL*schedule*.html
git add data/ipl_2026_schedule.json
git commit -m "refresh IPL schedule"
git push origin main
# 4. Wait for Railway redeploy
```

**Set a calendar reminder for ~20 May** to refresh once playoffs (Q1,
Eliminator, Q2, Final) are scheduled. Until then, on those dates
`lookup_today_ipl_match()` returns None and the bot falls through to
cricket_news RSS or scripted no-match — graceful, just no schedule.

**For IPL 2027:** new series ID — find it in any IPL match URL. Update
the URL in `scripts/refresh_ipl_schedule.py` docstring + the constant in
`apis.py` if data file rename is needed.

---

## Pending findings (not pilot-blocking)

| Item | Notes |
|---|---|
| Persona collapse on emotional prompts | `caring_child` and `grandchild` produce near-identical replies when vulnerability handler / Rule 5 fires. Post-pilot copy tightening. |
| Soft inference on benign topics | Bandra → "beach ki hawa". DeepSeek paints in detail. Watching, not fixing. |
| Conversational-intent → structured tables | Names/medicines mentioned in normal chat → memories prose, not family_members / medicine_reminders rows. Post-pilot. |
| Two `_USER_CACHE` dicts (main.py + database.py) | Collapse post-pilot. |
| Hindi numerals ("ek baje" / "do baje") in time parser | v1.5. |
| `apis._extract_first_keyword` dead code | Defined in apis.py:~1100, no longer called after Bug C2 fix. Clean up post-pilot. |
| In-process schedule cache | `_IPL_SCHEDULE_CACHE` held for process lifetime. Pushing a refreshed schedule mid-day requires Railway redeploy to take effect. Acceptable for pilot; add file-mtime check post-pilot if needed. |

---

## Module 20 — pilot prep (still pending)

- Identify 5 test users from family network
- Build 20-user invite list (urban Indian senior 65+, adult child willing
  to do family setup)
- Onboarding doc for adult children (child-led setup walkthrough,
  expected timeline, what-to-tell-the-senior-first-time)
- Pre-pilot test plan (Module 15 17-test protocol + Module 19 capability
  tests)

---

## Verification discipline reminders

Active rules in CLAUDE.md (V1–V10). This session used V8 path B
(commit-in-place via Edit tool) throughout — clean, no `does not match
index` failures. V9 (no substring match for short keywords) and V10
(verify previous patch in chain before generating next) didn't apply this
session — single commit-in-place flow.

---

## Workflow note — what worked, what didn't

**What worked:**
- Iterative diagnostic via `/cricdebug` admin command paid off again.
  When Cricbuzz scrape silently failed in production, /cricdebug's parser
  dry-run section would have told us in seconds (we ended up using a Mac
  diag script instead, also fast).
- V4 unit tests against uploaded HTML fixtures caught the 0-match bug
  early (regex mismatch on extra `\\` before `{`).
- Browser-save → extractor script is a powerful pattern when the data we
  need is JS-hydrated (curl can't get it, browser-save can, then we
  extract once and hardcode).

**What burned time (~50 min on cricket alone):**
- Two failed scrape attempts before pivoting. Each needed 2–3 deploy
  iterations to confirm.
- The pattern: live data source promises richness, then the structured
  data turns out to be hydrated post-SSR. Pre-flight discipline next
  time: BEFORE writing a parser, first verify the data we want is in
  what curl returns (not just what the browser shows).
