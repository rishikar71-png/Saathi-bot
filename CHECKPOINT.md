# CHECKPOINT — Resume after 29 Apr 2026 evening session

**Read order for next session:** this file → CLAUDE.md → progress.md.

---

## State at session close

**HEAD on `origin/main`:** `aa8a2a9` — Bugs A, B, E shipped (TTS language threading + per-turn language nudge + cricket data freshness + cricket RSS news). Sits on top of `44e453c` (CHECKPOINT update from afternoon run) and the earlier 29 Apr pilot-blocker fixes.

**Working tree:** has 5 untracked `*.patch` files (`saathi_bug_b_29apr.patch`, `saathi_bugs_abe_29apr.patch`, `saathi_bundle_29apr.patch`, `saathi_followup_29apr.patch`, `session_23apr_bundle.patch`). Safe to `rm` — all are leftovers from `git am` rounds whose commits are now in history. Next session should clean up.

**Bugs A, B, E are deployed but NOT YET LIVE-VERIFIED.** They were unit-tested in the patch session (14/14 V4 tests pass) but Telegram-side verification is the first task of the next session.

---

## Module 19 capability test results — 29 Apr evening run (pre-fix)

This was the test run that surfaced Bugs A, B, E. Recording for the record:

| # | Test | Result | Notes |
|---|---|---|---|
| 1 | Music — specific song "kabhi kabhi mere dil mein" | ✅ | Real Mukesh original |
| 2 | Music — genre "old hindi songs" | ✅ | Old is Gold compilation |
| 3 | Music — artist "kishore kumar" | ✅ | Kishore Hits compilation |
| 4 | Music — vague "kuch acha sunao" → preference | ✅ | 90s Hindi (matches stored "old hindi") |
| 5 | False positive guard "nobody listens to me anymore" | ✅ | Vulnerability handling fired, no YouTube |
| 6 | India news "koi news hai aaj?" | ⚠️ | Surfaced Lewandowski/Juventus (transfer gossip from TOI sports section). Content-quality issue, not pilot-blocking. **Will be partly mitigated by Bug E Part 2** (cricket-intent queries now route to ESPNCricinfo/Cricbuzz instead of TOI sports) |
| 7 | World news "any world news?" | ⚠️ | Real BBC/Reuters stories but one was "Japan zoo staffer dumped wife's body in incinerator" — slipped through `_IRRELEVANT_TOPIC_SIGNALS`. **Bug D — needs filter expansion. Deferred to next session.** |
| 7b | Hindi follow-up "Trump ke baare mein kuch?" | ✅ | Language switch + memory recall |
| 8 | Cricket "aaj cricket mein kya ho raha hai?" | ❌ | Bot said "no match today" but Match 41 (MI vs SRH 19:30) existed. **Bug E — root cause: CricAPI `/currentMatches` doesn't include scheduled-future matches. Fixed in `aa8a2a9`.** |
| 9 | Weather "aaj ka weather kaisa hai?" | ✅ | Mumbai 33°C with hazy weather, real OWM data |
| 10 | Hindi voice "aaj mood thoda heavy hai" | ✅ | Whisper transcribed; vulnerability handling fired (2 sentences, no probing) |
| 11 | English voice "tell me about today's weather" | ❌ | Reply came back in Hinglish despite English query. **Bug B — DeepSeek autoregressing from session history when language switches back. Fixed in `aa8a2a9` (per-turn language nudge).** |
| 12 | TTS voice quality | ❌ | Every TTS call used `en-IN-Neural2-D` even for Hindi/Hinglish replies. **Bug A — TTS path read stale `user_row['language']` instead of per-message effective language. Fixed in `aa8a2a9` (1-line change).** |

**Score:** 8 PASS, 4 FAIL (Bugs A, B, D, E surfaced). 3 fixed in `aa8a2a9`. Bug D deferred.

---

## NEXT SESSION FIRST TASK — Verify Bugs A, B, E live

After Railway deploy of `aa8a2a9` completes, run these 4 tests in order:

| # | Test | Expected post-fix | Log signal |
|---|---|---|---|
| A1 | Send any Hindi voice note (e.g. "aaj theek hun") | TTS uses Hindi voice | `TTS \| lang=hi-IN \| voice=hi-IN-Neural2-A` |
| B1 | After 3-4 Hinglish exchanges, send clean English: "tell me about today's weather" | Reply in English, not Hinglish | Reply text in English; LANG override line absent or `effective=english` |
| E1 | "aaj cricket mein kya ho raha hai?" | Bot mentions MI vs SRH 19:30 tonight (or whatever match is scheduled) | `cricket merged matches (40+ total)`; `cricket fetched \| TODAY (upcoming) — Mumbai Indians...` |
| E2 | "any cricket news?" | Bot returns ESPNCricinfo / Cricbuzz / NDTV Sports headline | `cricket_news fetched via RSS \| keyword=...` |

**Also run** the RSS URL verification curl loop before assuming `_RSS_FEEDS_CRICKET` URLs work:

```bash
for u in \
  "https://www.espncricinfo.com/rss/content/story/feeds/0.xml" \
  "https://static.espncricinfo.com/rss/livescores.xml" \
  "https://www.cricbuzz.com/rss/cricket-features-rss" \
  "https://www.cricbuzz.com/api/cricbuzz/rss/cricket" \
  "https://feeds.feedburner.com/ndtvsports-cricket"; do
  echo "==> $u"
  curl -sS -o /dev/null -w "  http %{http_code}  bytes=%{size_download}\n" --max-time 5 "$u"
done
```

Replace any non-200 entries with working URLs from feedspot.com or the publisher's RSS hub. NDTV Sports is the most-reliable backstop.

---

## Pilot-blocker tests (29 Apr afternoon — STILL ALL PASSING)

| # | Test | Status |
|---|---|---|
| 1 | Step 0 captures name + phone | ✅ |
| 2 | "1.30" → 13:30 (bare 1–5 PM default) | ✅ |
| 3 | Batch-ASK clarify for ambiguous medicines | ✅ |
| 4 | "BP pill at shaam 7" → 19:00 (Hindi word-boundary) | ✅ |
| 5 | "did you remind me today?" → factual (MEDICINE STATUS block) | ✅ |
| 6 | "can you set a reminder for me?" → RULE 13 capability response | ✅ |

All bonus passes (greeting salutation, FAMILY block names, unsupported-language gate, handoff collapse) still holding.

---

## After Bug A/B/E verification — Module 19 remaining tier-1 items

1. **Reminder firing live** — wait for BP pill at 19:00 IST. Verify text + bell + TTS voice; reply with 👍 to confirm ack.
2. **Daily ritual firing live** — tomorrow morning 10:00 IST briefing; verify weather + news + cricket all wrap warmly.
3. **Self-setup mode end-to-end** — `/adminreset`, pick "myself" path. Verify confusion-branch handling, 2-day pacing, day-2 emergency contact bridge. Deferred from earlier verification.
4. **Family bridge** — `/familycode`, have wife send the bare code from her Telegram, verify confirmation prompt + auto-join + relay.
5. **Hindi conversation quality at length** — 5+ Hindi turns, watch for English bleed.

After Tier-1: Module 20 pilot prep (5 test users, 20-user invite list, onboarding doc).

---

## Open items deferred (not pilot-blockers)

| Item | Notes |
|---|---|
| **Bug D — crime/horror filter leak in world news** | 29 Apr evening test surfaced "Japan zoo staffer dumped wife's body in incinerator". `_IRRELEVANT_TOPIC_SIGNALS` needs to catch "body in", "dumped body", "killed wife", "murdered", "incinerator", "dismembered", and similar. Or flip to allowlist. Pilot-blocking-ish. **Fix early next session before pilot.** |
| **Bug C — sports gossip surfacing as Indian news** | Lewandowski/Juventus from TOI sports section. Add transfer-rumour patterns to `_LOW_QUALITY_TITLE_SIGNALS` or downrank sport-section RSS items. Less critical. |
| Conversational-intent → structured tables | Names/medicines/grandkids mentioned in normal chat still land in `memories` as prose, not in `family_members` / `medicine_reminders`. Post-pilot. |
| Hindi numerals | "ek baje" / "do baje" / "teen baje" not parsed by `_normalize_time`. v1.5 add. |
| Two `_USER_CACHE` dicts | main.py's unbounded cache vs database.py's 5-min TTL — collapse post-pilot. |
| Self-setup mode end-to-end test | Module 19 capability test (above). Deferred from earlier sessions. |
| Untracked `*.patch` files | 5 patch files in `~/saathi-bot/`. Safe to `rm` — commits all in history. |
| Memory extraction noise: "user is called Ma" | "Ma" is salutation, not name. Tighten extraction prompt or add salutation-aware skip post-pilot. |
| `short_reply_disengagement` over-fires on contextual yes/no replies | Pre-processor should suppress when previous Saathi turn ended with `?`. Edge case. |
| `_RSS_FEEDS_CRICKET` URL verification | Candidate URLs need curl verification on first deploy. Backup: NDTV Sports always works. |

---

## Workflow note for next session (V8 reminder)

When shipping fixes:

- **Bug fix in code** → Patch deliverable. Do NOT use Edit tool on `/Users/rishikar/saathi-bot/`. Modify files in `/tmp/<workdir>/clone` via bash; generate patch from `/tmp`; Rishi `git am`s onto a clean working tree.
- **Documentation update (`CLAUDE.md` / `CHECKPOINT.md` / `progress.md`)** → Commit-in-place. Edit tool fine; final instruction is `git add <files> && git commit && git push`.

If `error: <file>: does not match index` ever appears, the wrong path was chosen — diagnose, abort the patch, switch to commit-in-place for that change.

**Stale-lock recovery** (when `error: could not write index` or `previous rebase directory still exists`):
1. `rm -f .git/index.lock`
2. `ls .git/rebase-apply 2>/dev/null && rm -rf .git/rebase-apply`
3. `git am --abort`
4. `git status` — confirm clean
5. (`lsof .git/index` showing Spotlight/mdworker is benign — git's atomic-rename writes are not blocked by readers)
