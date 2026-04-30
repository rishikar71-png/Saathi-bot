# CHECKPOINT — Resume after 30 Apr 2026 collision-audit + voice-fix session

**Read order for next session:** this file → CLAUDE.md → progress.md.

---

## State at session close

**HEAD on `origin/main`:** `9d3d764` (Bugs M/N/O/P). Full chain pushed
this session, all live-verified:

```
9d3d764  Bugs M/N/O/P — substring-collision audit cleanup
33fb625  Bug G  — Protocol 3 per-message language
48aa145  Bug F  — Whisper drop hint for hi/hinglish/en
577b049  Bug Q  — Music HTML entities + top-5 variety
aeb6c36  Bug L  — Capture flow topic-change escape hatch
e5f0068  Bug J  — Pending-capture token-exact match
95c70ab  Bug D  — World news crime/horror filter expansion
b27ec6f  Bug E1 — Cricket token-exact team + 7d UPCOMING horizon
bb815f8  Bug I  — Strip emojis from TTS input
```

12 bugs landed. No code work outstanding from this session.

---

## Verified live this session

| Bug | Severity | What was tested |
|---|---|---|
| I  (TTS emoji) | P0 voice | "who are you?" → identity reply, no "folded hands" in voice. |
| E1 (cricket regression) | P1 hallucination | "aaj cricket?" → clean "no match today", no Guyana/Perth. |
| J  (pending-capture nati) | P0 routing | "any other news - international news" no longer fires grandkids prompt. |
| L  (capture escape hatch) | P0 data | "any news" while awaiting=grandkids → news pipeline, NOT "Any News — lovely name". |
| Q  (music) | P2 UX | "play music" / "kuch sunao" back-to-back returned different songs; no &#39; entities. |
| F  (Whisper Hindi→Eng) | P0 senior unheard | Hindi voice note → Hindi reply + Hindi voice. English voice note → English. |
| G  (P3 per-message lang) | P1 wrong lang | Hinglish P3 trigger ("paise mange hain - kya karu?") → Hindi P3 response. |
| M  (i fell asleep) | **P0 family alert** | "I fell asleep last night" → normal reply, NO emergency alert. |
| N  (jump/mar collisions) | **P0 auto-escalation** | "jumping jacks today" → normal reply, NO family alert. |
| P (financial calendar) | P1 routing | "what's on the calendar today" → normal reply, not financial-neutrality. |

Verified by V4 unit suite (no live trigger needed):

| Bug | Coverage |
|---|---|
| D  (world news filter) | 12/12 must-block + 14/14 must-pass benign Indian headlines. |
| O  (died → studied) | 6/6 cases incl. regression. |
| P (eulogy yes signals) | 7/7 incl. yesterday/happy/stocks regression. |
| P (protocol4 escort) | 7/7 incl. police escort / VIP escort regression. |

---

## Pending (not pilot-blockers, but flagged)

| Item | Notes |
|---|---|
| **"Do you know my grandkids names?" UX** | Bot offers to learn instead of saying "I don't know yet". Copy refinement only — not wrong, just suboptimal. After Bug L, the FAMILY block in DeepSeek system prompt now lists captured grandkids, so subsequent queries will know names. First-time-asked phrasing could change to: "I don't know yet — would you like to share?" instead of "By the way..." |
| `_TTS_MAX_CHARS=400` long-reply test | Code change live since `8b77887`; not stress-tested with a 250-350-char reply. Send "tell me about Mumbai" or extended news roundup to verify voice still fires. |
| Persona effect verification | Manual DB update to set persona='grandchild' then watch ritual tone shift. |
| `apis._IRRELEVANT_TOPIC_SIGNALS` "rape" matches "grape"/"rapeseed" | Minor — would only fire on legit farming/food news headlines, which are rare. Deferred. |
| `reminders._ACK_SUBSTRINGS` Hindi phrases | Limited to ≤25 char messages, low collision risk in mixed-language seniors. Audit-clean. |
| Conversational-intent → structured tables | Names/medicines mentioned in normal chat → memories prose, not family_members/medicine_reminders rows. Post-pilot. |
| Hindi numerals ("ek baje" / "do baje") | Not parsed by `reminders._normalize_time`. v1.5. |
| Two `_USER_CACHE` dicts (main.py + database.py) | Collapse post-pilot. |

---

## Open Module-level work — pick up here next session

**Task 7 (Module 19 + Module 20):**

Module 19 — end-to-end capability tests, remaining tier-1 not yet run:

- India news quality stress-test (TOI vs HT vs NDTV ordering — does the
  feed surface high-quality stories or clickbait first?)
- Self-setup mode end-to-end (senior who picks "for myself" path,
  18-question pacing across 2 days, deferred-bridge handling)
- Family bridge: bare-code paste → confirmation prompt → register
- Hindi conversation, 5+ turns deep — verify language stays Hindi,
  context/memory threading works, persona threading holds
- Long-reply TTS verification (`_TTS_MAX_CHARS=400`)
- Persona effect verification — manual DB update + observe ritual

Module 20 — pilot prep:

- Identify 5 test users from family network
- Build 20-user invite list (target user profile: urban Indian senior
  65+, adult child willing to do family setup)
- Onboarding doc for adult children (child-led setup walkthrough,
  expected timeline, what-to-tell-the-senior-first-time)
- Pre-pilot test plan (the 17-test Module 15 protocol + the test 20
  capability tests)

---

## Deferred from session (process improvements to land in next session's first commit)

**Add to CLAUDE.md:**

- **V9 — No substring match for short keywords.** Any keyword list of
  length ≤5 chars MUST use `\b` word-boundary regex or token-exact
  match. Substring match is only acceptable for keyword lists where
  every entry is a multi-word phrase ≥6 chars and unambiguous in
  context. Audit history: this rule added because the substring-match
  pattern caused ≥7 distinct bugs in 2 days (E1, J, L, M, N, O, P).
- **V10 — Verify patch-application before generating the next patch.**
  Before generating any new `git format-patch`, run `git log --oneline
  -5` against the user's tree and confirm the LAST applied patch from
  this session is in the chain. If a patch is missing, regenerate it
  (or remind the user to apply it) before continuing. The /tmp clone
  defaulting to the user's origin/main makes patch-skip silent — Bug
  M+N+O+P missed origin/main for ~2 hours this session because three
  patches (Q, F, G) were generated and applied on top of M+N+O+P's
  parent before anyone noticed.
- **Product principle locked in:** Don't paternalize seniors. BBC-tier
  real news passes through. Iran strikes, FBI threats, Bondi shootings
  are real events; seniors are adults. The crime/horror filter
  (Bug D) is calibrated for tabloid horror specifics (incinerator,
  dismembered, body-in-fridge), not blanket violence. Sports/celebrity
  fluff (Sebastian Sawe payday, Lewandowski transfer) is fine.

---

## Apply the final docs patch

```bash
cd ~/saathi-bot
rm -f .git/index.lock
git am --abort 2>/dev/null
cp "/Users/rishikar/AI Projects/saathi_session_close_30apr_final.patch" .
git am saathi_session_close_30apr_final.patch
git push origin main
```

Single commit: this CHECKPOINT.md update + CLAUDE.md session log entry +
V9 + V10 rules.

---

## Workflow note

- This session: 9 bug-fix commits + 1 docs commit. Patch-deliverable
  workflow (V8 path A) used throughout; no Edit-tool conflicts; no
  stale `.git/rebase-apply` directories.
- One process gap surfaced: M+N+O+P patch was generated but not
  applied for ~2 hours; subsequent patches landed on top of its parent.
  V10 rule above is the fix.
