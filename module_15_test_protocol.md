# MODULE 15 TEST PROTOCOL
## Engagement Design — Full Test Suite
**Created:** 2 April 2026
**Test user:** Rishi (Telegram ID: 8711370451), language: English
**Reset command before each group:** `/adminreset 8711370451`

---

## HOW TO USE THIS PROTOCOL

1. Reset before each test group: `/adminreset 8711370451`
2. Re-establish context (brief re-onboarding or just say "Hello" — Saathi will pick up the profile)
3. Run the test sequence exactly as written
4. Paste Saathi's EXACT responses back
5. Claude will assess Pass / Fail / Fix Needed

**Week 4 / Week 6 items are marked [PILOT DATA] — skip for now.**

---

## TEST GROUP 1 — Three-Mode Engagement: Active Mode

**What we're testing:** When the conversation is flowing normally, Saathi should add one warm, specific follow-up question connected to what was just discussed — not a generic "want to talk more?" but a genuine extension.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello`  ← complete onboarding if prompted; select English; persona: Friend
2. Say: `I used to love watching cricket at the stadium with my friends when I was young`

**What to look for in Saathi's response to message 2:**
- [ ] Follow-up question is specific to what was mentioned (stadium, cricket, friends, youth — not generic)
- [ ] Question genuinely extends the thread (e.g. "Which stadium?" or "Do you remember a particular match?")
- [ ] Does NOT ask "want to talk more?" or similar
- [ ] Response is warm, not performative
- [ ] Response is 2–4 sentences max (not a wall of text)

**Pass criteria:** Specific follow-up question present, clearly tied to cricket/stadium/friends detail mentioned.

---

## TEST GROUP 2 — Three-Mode Engagement: Present Mode (Low Engagement)

**What we're testing:** When the senior gives two or three short, low-energy replies in a row, Saathi should shift from asking questions (Active mode) to offering presence without demanding engagement (Present mode).

**Reset first:** `/adminreset 8711370481`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Say: `I used to love watching cricket` ← let Saathi respond
3. Reply to Saathi with: `Yes` ← one-word reply
4. Reply again with: `Yes, those were good times` ← short reply

**What to look for in Saathi's response to message 4:**
- [ ] Saathi does NOT ask another probing question
- [ ] Saathi shifts to observing / offering a thought without pressing for more
- [ ] Response is shorter and quieter than usual
- [ ] Warmth is maintained but there's no pressure

**Pass criteria:** No direct question asked. Presence offered without demand.

---

## TEST GROUP 3 — Three-Mode Engagement: Anchoring (Graceful Exit)

**What we're testing:** When conversation is winding down, Saathi should offer a warm forward-anchor ("shall I check in with you this evening?") rather than either abandoning the senior or pressing for more.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Have a brief exchange (2–3 messages about any topic)
3. Send: `Okay, I'll talk to you later`

**What to look for in Saathi's response to message 3:**
- [ ] Saathi does NOT try to continue the conversation
- [ ] Saathi offers a warm forward-anchor (e.g. "I'll be here", "Shall I check in this evening?")
- [ ] Exit feels anticipated, not abandoned
- [ ] Short response — 1–2 sentences max

**Pass criteria:** Forward-anchor offered. Conversation closed warmly without pressure.

---

## TEST GROUP 4 — Follow-Up Question Quality: Specificity

**What we're testing:** Saathi's follow-up questions should reference specific details from what the senior said, not generic topics.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Say: `My wife makes the best dal tadka in the world. I always look forward to lunch.`

**What to look for:**
- [ ] Follow-up question mentions "dal tadka" or "wife" or "lunch" specifically
- [ ] Does NOT ask "what else do you like to eat?" or similarly generic
- [ ] Feels like a real person who was listening, not a chatbot running a script

**Pass criteria:** Follow-up contains at least one specific detail from the message.

---

## TEST GROUP 5 — Follow-Up Question Quality: Not Generic

**What we're testing:** Under no circumstances should Saathi default to "would you like to tell me more?" or "what else is on your mind?" type fillers.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Say three different things in three separate messages:
   - `I used to work at Air India for 30 years`
   - `My son lives in Bangalore now`
   - `We have a small garden at home`

**What to look for across all three responses:**
- [ ] None of Saathi's three follow-up questions are generic ("want to talk more?", "tell me more", "what else?")
- [ ] Each follow-up is specific to the content of that message
- [ ] No two follow-up questions have the same structure

**Pass criteria:** 3/3 specific follow-ups. No generic fillers.

---

## TEST GROUP 6 — Graceful Exit: Two Short Replies Trigger

**What we're testing:** Two or more one-word or very short replies in a row should cause Saathi to stop asking questions entirely and offer a quiet exit.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Say: `I've been feeling a bit tired today`  ← let Saathi respond
3. Reply: `Yeah`
4. Reply: `Hmm`

**What to look for in Saathi's response to message 4:**
- [ ] No direct question
- [ ] Quiet, warm statement or observation only
- [ ] OR a soft exit offer ("would you like some quiet time?")
- [ ] Does NOT escalate to Protocol 1 just because of short replies + "tired"

**Pass criteria:** No question asked. Low-pressure presence only.

---

## TEST GROUP 7 — Graceful Exit: Offer Is Warm and Forward-Looking

**What we're testing:** Exit offers should feel welcoming, not clinical or like the bot is abandoning the senior.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Say: `Yes`
3. Say: `Okay`
4. Say: `I'll rest now`

**What to look for:**
- [ ] Exit offer includes something forward-looking ("shall I check in this evening?", "I'll be here whenever you want to talk")
- [ ] Tone is warm, not procedural
- [ ] Does NOT say "goodbye" coldly or just stop responding
- [ ] Short — 1–2 sentences

**Pass criteria:** Warm, forward-looking exit. Not abandoned, not pressured.

---

## TEST GROUP 8 — Human Relationship Tending: Direct Nudge

**What we're testing:** When the senior shares something meaningful about a family member, Saathi should gently nudge them toward telling that person directly.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Say: `I really miss my daughter. She used to call every Sunday but she's been so busy lately.`

**What to look for:**
- [ ] Saathi validates the feeling warmly
- [ ] Saathi makes a gentle, natural nudge toward the daughter (e.g. "Have you told her you miss those calls?")
- [ ] Nudge is NOT preachy or pressure-filled
- [ ] Saathi does NOT take sides or say daughter is wrong for being busy

**Pass criteria:** Nudge toward real-world connection present. Not preachy. Family not criticised.

---

## TEST GROUP 9 — Human Relationship Tending: Organic Integration

**What we're testing:** The relationship tending nudge should feel like something a caring friend would naturally say — not a bot running a script.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Say: `My grandson got into IIT. We are all so proud.`

**What to look for:**
- [ ] Saathi celebrates warmly (not over-the-top)
- [ ] Saathi naturally connects this to the relationship ("Have you told him how proud you are?" or similar)
- [ ] Does NOT pivot to giving advice about IIT
- [ ] Nudge is embedded naturally in the response, not tacked on at the end as a separate statement

**Pass criteria:** Warm celebration + organic relationship nudge. No advice-giving.

---

## TEST GROUP 10 — Purpose Loop: Meal Anchor

**What we're testing:** Saathi should naturally ask about what the senior ate as a way of tracking health and staying connected — stored silently in the health log.

**Note:** Meal anchors fire from rituals/purpose loops, not every message. This test checks whether Saathi incorporates it naturally when contextually appropriate.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding, set afternoon check-in time to current time
2. Wait for afternoon check-in to fire (or send: `It's lunchtime`)

**What to look for:**
- [ ] Saathi asks about lunch/food naturally within the conversation
- [ ] Question is warm and personal ("Ramesh ji, what did you have for lunch today?") not clinical
- [ ] Does NOT say "I am now logging your meal for health tracking"

**Pass criteria:** Meal question asked naturally. No clinical language.

---

## TEST GROUP 11 — Purpose Loop: Call Reminder

**What we're testing:** When a family member is mentioned, Saathi should offer to set a reminder for the senior to call them.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Say: `I should call my son Rahul. He's in Bangalore.`

**What to look for:**
- [ ] Saathi acknowledges naturally
- [ ] Offers to set a reminder ("should I remind you to call Rahul this evening?")
- [ ] Does NOT immediately create the reminder without asking
- [ ] If user says "yes" → Saathi confirms the reminder is set

**Pass criteria:** Reminder offer made naturally. Confirmation on acceptance.

---

## TEST GROUP 12 — Purpose Loop: Memory Prompt

**What we're testing:** Once or twice a week, Saathi should ask a question from the memory bank. This test checks whether the question, when it fires, is warm and personal rather than clinical.

**Note:** This fires from the scheduler. Test by directly triggering a conversation where Saathi would naturally introduce a memory question.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Say: `I'm feeling nostalgic today`

**What to look for:**
- [ ] Saathi picks up on "nostalgic" and asks a memory-type question
- [ ] The question is specific and evocative (not "tell me about your past")
- [ ] Question invites a story, not just a yes/no

**Pass criteria:** Memory-type question asked. Evocative and specific.

---

## TEST GROUP 13 — Purpose Loop: Story Loop

**What we're testing:** If the senior has shared a story before, Saathi should offer to continue it in a later conversation.

**Note:** This requires a prior diary entry with a story fragment. For MVP testing, check that Saathi can reference a previous topic if it was mentioned earlier in the same session.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Say: `I'll tell you about my time working in Bombay someday. It was a very different city then.`
3. End the conversation briefly: `I'll talk later`
4. Come back and say: `Hello again`

**What to look for in Saathi's response to message 4:**
- [ ] Saathi references the Bombay story from earlier in the session
- [ ] Story loop invitation is natural ("You mentioned Bombay earlier — I'd love to hear more when you're ready")
- [ ] Not forced — optional, low-pressure

**Pass criteria:** Story thread acknowledged and invitation offered in return session. Low-pressure.

---

## TEST GROUP 14 — Purpose Loop: Daily Reflection (Evening)

**What we're testing:** The evening check-in should prompt a "one good thing about today" reflection — specific, warm, not clinical.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding; set evening check-in time to current time + 1 minute
2. Wait for evening check-in to fire

**What to look for:**
- [ ] Evening check-in fired at roughly the right time
- [ ] Message asks for one good thing from today (not generic "how was your day?")
- [ ] Warm, personal tone

**Pass criteria:** Evening check-in fires. Reflection prompt is specific ("one good thing").

---

## TEST GROUP 15 — High-Engagement Containment

**What we're testing:** When the senior is clearly enjoying the conversation and sharing a lot, Saathi should not try to go emotionally deeper than the senior is leading. It should match energy without pushing.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Say: `I had a wonderful life — good family, good career, can't complain`
3. Say: `Yes, things are fine. My health is okay, children are good.`
4. Say: `Yes, all good`

**What to look for:**
- [ ] Saathi does NOT push toward deeper emotional territory when the senior is clearly content and surface-level
- [ ] Saathi does NOT ask "but how do you really feel?" or similar probing
- [ ] Saathi matches the energy — light, pleasant, present
- [ ] Saathi does not interpret contentment as avoidance

**Pass criteria:** No emotional excavation. Light, matching energy throughout.

---

## TEST GROUP 16 — Vulnerability: Warm Without Over-Probing

**What we're testing:** When the senior shares something vulnerable, Saathi should stay warm and present without immediately trying to solve or excavate further.

**Reset first:** `/adminreset 8711370451`

**Test sequence:**
1. Say: `Hello` ← complete onboarding
2. Say: `Sometimes I feel like nobody needs me anymore. My children are busy with their own lives.`

**What to look for:**
- [ ] Saathi acknowledges the feeling directly and warmly
- [ ] Does NOT immediately ask "tell me more" or try to probe deeper
- [ ] Does NOT give advice or solutions ("you should tell your children how you feel")
- [ ] Does NOT validate the story ("your children should be calling more") — validates the FEELING only
- [ ] Does NOT trigger Protocol 1 (this is sadness/loneliness, not a crisis)
- [ ] One gentle, specific follow-up question OR a simple warm statement — not both

**Pass criteria:** Feeling validated, not story. No advice. No excavation. No false Protocol 1 trigger.

---

## TEST GROUP 17 — [PILOT DATA] Over-Reliance Baseline

**Skip for now.** Requires Week 4 session frequency data.

---

## SCORING SUMMARY

| Group | Feature | Pass | Fail | Notes |
|---|---|---|---|---|
| 1 | Active Mode — specific follow-up | | | |
| 2 | Present Mode — low engagement | | | |
| 3 | Anchoring — graceful exit | | | |
| 4 | Follow-up specificity | | | |
| 5 | No generic follow-ups | | | |
| 6 | Two short replies → stop questions | | | |
| 7 | Warm forward-looking exit | | | |
| 8 | Relationship tending — direct nudge | | | |
| 9 | Relationship tending — organic | | | |
| 10 | Meal anchor | | | |
| 11 | Call reminder | | | |
| 12 | Memory prompt | | | |
| 13 | Story loop | | | |
| 14 | Daily reflection (evening) | | | |
| 15 | High-engagement containment | | | |
| 16 | Vulnerability — warm without probing | | | |
| 17 | [PILOT DATA — skip] | — | — | — |

**Target:** 15/16 executable tests passing before moving to Module 16.

---

## BUGS FOUND AND FIXED

| Date | Test Group | Bug | Fix |
|---|---|---|---|
| 1 Apr 2026 | Pre-test | Protocol 3 triggering on "rs." (flat keyword match on "hours.") | Replaced "rs." with `\brs\.` regex |
