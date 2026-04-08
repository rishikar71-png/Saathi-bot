# SAATHI BOT — Master Context for Claude Code
**Read this file at the start of every session before doing anything else.**
**After any meaningful work, update the `## Session Log` section at the bottom.**

---

## What Saathi Is

Saathi is a **Telegram-based AI companion for urban Indian seniors aged 65+**. It lives inside an app they already know — no downloads, no new interface, no learning curve. It remembers their family, habits, music, and moods. It speaks in their language and is always there.

**For families:** Saathi is an early warning system for decline + an emotional support layer + financial protection. Adult children pay a small subscription (~Rs 299/month in v2) to give their parent all three. The senior uses it free.

**The builder is non-technical.** All instructions must be translatable into plain English. No jargon without explanation.

---

## Stack

| Component | Choice | Notes |
|---|---|---|
| Platform | Telegram (v1) → WhatsApp (v2) | WhatsApp API has 24hr session windows + template approvals — prevents always-on conversation |
| AI Brain | DeepSeek V3 + Sensitivity Wrapper | ~20x cheaper than Claude. Excellent Hindi/Hinglish. Sensitivity wrapper in system prompt |
| Voice Input | OpenAI Whisper | ~Rs 0.50/min. Hindi/Hinglish accuracy. Non-negotiable for accessibility |
| Voice Output | Google Cloud TTS | Personalised voice reminders. Free tier sufficient for MVP |
| Music | YouTube Data API v3 | Exact video link + thumbnail. Free tier: 10,000 searches/day |
| Database | SQLite (MVP) → Supabase (v2) | **Schema must be designed for v2 family dashboard from day one** |
| Hosting | Railway.app | Auto-deploys from GitHub. Free tier → Rs 400/month |
| Safety | Protocol 1 + Protocol 2 + Protocol 3 | See Protocols section below |

---

## Architecture — The Pipeline

Every incoming message flows through this sequence. Each stage is independent and swappable:

```
Incoming message
      ↓
[1] Protocol 1 check — mental health crisis keywords (runs BEFORE DeepSeek)
      ↓
[2] Protocol 3 check — financial/legal keywords (runs BEFORE DeepSeek)
      ↓
[3] Memory retrieval — user profile + last 3 diary entries + same-day-last-week
      ↓
[4] DeepSeek V3 — with sensitivity wrapper baked into system prompt (Protocol 2)
      ↓
[5] Response sent to user
      ↓
[6] Nightly: conversation summarised into diary entry
```

---

## Database Schema — Design for v2 From Day One

**CRITICAL INSTRUCTION:** Design the SQLite schema with the v2 family caregiver dashboard in mind from the first line of code. This prevents a full database redesign in v2. The dashboard will need to read: mood trends, health complaint history, medication adherence, last active time, conversation summaries, memory archive entries, heartbeat alert history, and protocol trigger logs.

Core tables needed:
- `users` — senior profile (name, language, persona, bot name, wake/sleep times, etc.)
- `family_contacts` — linked family members with roles and notification preferences
- `diary_entries` — nightly summaries (mood, health, family mentions, emotions)
- `health_log` — medicine acknowledgements, health complaints from conversation
- `memory_archive` — life story entries from memory bank questions
- `reminders` — scheduled medicine/call reminders per user
- `heartbeat_log` — ping history and family alert history
- `protocol_log` — Protocol 1/3 trigger events (anonymised, timestamped)
- `session_log` — session length, frequency, time of day (for over-reliance monitoring)

---

## The Three Protocols — NEVER Delegated to DeepSeek

### Protocol 1 — Mental Health Crisis (HARDCODED, runs BEFORE DeepSeek)
**Triggers:** Explicit crisis keywords in English + Hindi/Hinglish. Examples: 'jeena nahi chahta', 'khatam kar loon', 'I don't want to go on', 'main thak gaya hun zindagi se'.

**Three stages:**
1. Stay present — warm acknowledgement, invite more conversation. No helpline dump immediately.
2. Gently mention Vandrevala Foundation (1860-2662-345, 24/7) + offer to contact family with consent.
3. If user consents → immediate family alert. Bot stays in conversation.
4. **Auto-escalate** if imminent-action language detected — family alerted without waiting for consent.

**Never:** minimise, lecture, ask about methods, list numbers and change subject.

**Indian helplines:** Vandrevala Foundation 1860-2662-345 | iCall TISS 9152987821 | AASRA 9820466627

### Protocol 2 — Sensitivity Wrapper (INSIDE DeepSeek system prompt, shapes every response)
Four instructions baked into the DeepSeek system prompt:
1. Scan every message for indirect emotional distress — 'aaj mann nahi lag raha', 'thak gaya hun', 'koi sunne wala nahi hai' — shift to elevated warmth before responding to surface content.
2. **Family conflict venting:** Validate the emotion ONLY — never the interpretation of the other person's behaviour. Never say 'your son was wrong' or 'you deserve better.' Reflect back: 'what does your heart tell you?' Encourage direct conversation with family. **Validate the feeling, not the story.**
3. When money-guilt signals appear (but don't trigger Protocol 3 keywords) → emotional validation only, never opinion on what they should do.
4. Never respond to emotional undertones with generic or information-only replies. When in doubt, be warmer.

### Protocol 3 — Financial & Legal Sensitivity (HARDCODED, runs BEFORE DeepSeek)
**Three trigger buckets:**
- **Bucket 1:** External financial pressure — someone asking senior for money, loans, investments, including guilt-wrapped versions ('my grandson's business needs help').
- **Bucket 2:** Asset & inheritance decisions — giving property, transferring assets, changing a will, cutting someone in/out.
- **Bucket 3:** Will & estate planning — making a will, who to include, what happens to savings.

**Response posture (all three buckets):**
1. Hear them fully — let them finish.
2. Name the weight — 'this sounds like something you have been carrying for a while.'
3. Honest limits — 'this is not something I am able to help you decide, and I would be worried if I tried.'
4. Point to a real human — trusted friend, family lawyer, CA, sibling. Not a helpline.
5. Leave the door open — 'if you want to talk about how this is sitting with you — not the decision, just how it feels — I am here for that.'

**Critical rule:** Completely neutral on what the senior does with their money. Never validate or invalidate the transaction. Never take sides in a family financial dispute.

---

## Onboarding — Child-Led Setup

The adult child does setup. The senior wakes up to a waiting companion. The bot detects first-time setup and offers a 'family setup mode' for the adult child.

**18 onboarding questions cover:**
Preferred name + salutation | City | Language preference | Spouse | Children's names | Grandchildren's names | Emergency contact | Medications + timing | Health sensitivities | Music preferences | News interests | Religion | Favourite topics | Bot persona preference | Formality level | Wake-up time | Sleep time | Heartbeat alert consent

**First message to senior:** 'Namaste, Ramesh ji. Your daughter Priya set this up for you. I am Meera — I am here whenever you want to talk.'

**Bot naming:** Onboarding asks what name the senior wants for the bot. Used in every message and voice note. Bot suggests saving contact under that name.

---

## Persona System

Four personas: Friend / Caring Child / Grandchild / Assistant. Chosen at onboarding. Affects tone, vocabulary, terms of endearment. Stored in profile, changeable anytime.

---

## Engagement Design — The Four Mechanisms

1. **Warm follow-up question default:** After every response, Saathi adds one warm, specific follow-up question connected to what was just discussed — not generic 'want to talk more?' but a genuine extension. ('Do you remember where you were when India won in 1983?'). Default posture is continued engagement.

2. **Signal-reading graceful exit:** Two short replies in a row, or the senior goes quiet → Saathi does not ask another question. Offers warm exit: 'It sounds like you might be winding down — shall I check in with you this evening?' Standard exit: 'Shall I come back in an hour?' — makes next conversation feel anticipated.

3. **Purpose loops — daily anchors:**
   - MEAL ANCHOR: 'Ramesh ji, what did you have for lunch today? I like knowing.' (stored silently in health log)
   - CALL REMINDER: 'Should I remind you to call Priya this evening?'
   - MEMORY PROMPT: Once/twice a week, a question from the 300+ question bank.
   - STORY LOOP: 'You were telling me about your time in Bombay last week — would you like to continue?'
   - DAILY REFLECTION: Evening — 'What was one good thing about today, even if it was small?'

4. **Human relationship tending:** Saathi actively nudges toward real-world human connection. 'That sounds like something Priya would love to hear — have you told her?' Built into system prompt, not keyword-triggered.

---

## Memory System

- **Daily diary:** Every night at midnight, summarise conversation into diary entry: family mentioned, health complaints, mood/emotional tone, songs requested, reminders acknowledged, Protocol 3 triggers logged.
- **Context retrieval:** Before every conversation, silently feed DeepSeek: permanent user profile + last 3 diary entries + entry from exactly one week ago + entry from one month ago.
- **Life archive:** Voice responses to memory bank questions transcribed and stored — builds a personal memoir over months.

---

## Safety Features

- **Heartbeat:** 3 pings at 30-min intervals during morning/afternoon/evening. Single 👍 resets counter. Family alert is warm, not clinical. Consent-first at onboarding. Disableable anytime.
- **Silence detection:** No messages during waking hours for 4+ hours → gentle check-in. No response in 30 minutes → quiet family alert.
- **Emergency command:** Any message with 'help', 'emergency', 'I fell', 'call someone' → immediate alert to emergency contact. Bot stays in conversation: 'I've let Priya know. I'm here with you.'

---

## Daily Rituals

- **Morning briefing** (at wake-up time): Name greeting + weather + one news headline in their interest area + cricket score if relevant + 'On This Day' nostalgia moment + religious verse/motivational thought + day's reminders + specific open question.
- **Evening check-in** (at preferred time): 'Kaise raha aapka din?' + daily reflection prompt.
- **'On This Day' feed:** One historical moment — Bollywood birthday, cricket milestone, freedom movement event, cultural anniversary.
- **Festival awareness:** Full Indian calendar. Religion-matched. Family birthdays/anniversaries from onboarding.
- **Daily trivia:** 'Question of the Day' — Bollywood trivia, cricket history, Indian geography.

---

## Voice & Music

- **Voice input:** Whisper transcribes voice notes → processed as text. Hindi + Hinglish + Indian-accented English.
- **Melody clip reminders:** 6-8 royalty-free Indian melody clips (temple bells, shehnai, harmonium) stored on server. Sent alongside reminder text.
- **Personalised voice reminders:** Google TTS generates audio addressed by senior's name. 'Ramesh ji, aapki dawai ka waqt ho gaya hai.'
- **Song search:** Any request → YouTube Data API → exact video link + thumbnail. Senior taps, YouTube opens.
- **ElevenLabs voice cloning:** DEFERRED TO v2.

---

## Health & Reminders

- **Medicine reminders:** Fire as voice + melody at scheduled times. 3 unanswered attempts → family notified. Single 👍 acknowledges.
- **Passive health log:** Health mentions stored silently from conversation.
- **Weekly family report:** Every Sunday → mood trends, health complaints flagged, medication adherence rate.

---

## Over-Reliance — Baseline First, No Limiters in MVP

Instrument everything from day one: session length, frequency, time of day, whether conversations about family/real-world plans go up or down over time. Red flags: 3am patterns (anxiety/insomnia), explicit preference for bot over family calls. Formal engagement limits are a **Week 6 decision** after real user data. Saathi's built-in safeguard: actively tending human relationships throughout.

---

## Monetisation

- **Senior:** Free always.
- **Family (v1):** Silence detection + heartbeat alerts + medicine adherence tracking + weekly health report + family bridge + emergency command + Protocol 3 (financial protection).
- **v1.5 premium hook:** ElevenLabs voice cloning — child pays to have their own voice deliver parent's morning greeting.
- **v2:** Family caregiver dashboard at ~Rs 299/month.

---

## Build Modules — Progress Tracker

See `progress.md` for detailed status. Summary:

| # | Module | Status |
|---|---|---|
| 0 | Project setup (Railway, GitHub, env vars) | ✅ Done |
| 1 | Telegram bot + basic message echo | ✅ Done |
| 2 | Database schema (SQLite, v2-ready) | ✅ Done |
| 3 | DeepSeek integration + system prompt + Protocol 2 wrapper | ✅ Done |
| 4 | Protocol 1 — Mental health crisis handler | ✅ Done |
| 5 | Protocol 3 — Financial/legal handler | ✅ Done |
| 6 | Onboarding flow (child-led, 18 questions) | ✅ Done |
| 7 | Memory system (diary, retrieval, context injection) | ✅ Done |
| 8 | Voice input (Whisper) | ✅ Done |
| 9 | Voice output (Google TTS + melody clips) | ✅ Done |
| 10 | Music (YouTube API) | ✅ Done |
| 11 | Medicine reminders + family escalation | ✅ Done |
| 12 | Daily rituals (morning briefing, evening check-in, purpose loops) | ✅ Done |
| 13 | Safety (heartbeat, silence detection, emergency command) | ✅ Done |
| 14 | Family bridge + weekly health report | ✅ Done |
| 15 | Engagement design (follow-up questions, graceful exit, relationship tending) | ✅ Done |
| 16 | 300+ memory question bank | ✅ Done |
| 17 | Voice upgrade (WaveNet → Neural2) | ✅ Done |
| 18 | News, sports & weather APIs wired in | ✅ Done |
| 19 | End-to-end capability testing (YouTube, news, sports, weather, voice) | ⬜ Not started |
| 20 | Testing + 20-user pilot prep | ⬜ Not started |

---

## API Keys Needed (store as environment variables — never in code)

- `TELEGRAM_BOT_TOKEN` — from BotFather
- `DEEPSEEK_API_KEY` — from platform.deepseek.com
- `OPENAI_API_KEY` — for Whisper (voice input)
- `GOOGLE_CLOUD_API_KEY` — for TTS + YouTube Data API
- `ELEVENLABS_API_KEY` — for voice cloning (v2, but set up now)

---

## Memory Maintenance Instructions for Claude Code

**At the start of every session:** Read this file (CLAUDE.md) and `progress.md` before doing anything.

**After completing any module or meaningful work:**
1. Update the module status table above (⬜ → 🔄 in progress / ✅ done).
2. Add an entry to the Session Log below with: date, what was built, any decisions made, any problems encountered, what to do next.
3. If a new decision was made that changes architecture, update the relevant section of this file.

**If context is running low mid-session:**
1. Write a `CHECKPOINT.md` in the project root summarising: what was just completed, exact file paths changed, what comes next, any open issues.
2. In the next session, read CLAUDE.md + progress.md + CHECKPOINT.md before resuming.

---

## Session Log

*(Update this after every session)*

| Date | What was done | Decisions made | Next step |
|---|---|---|---|
| 21 Mar 2026 | CLAUDE.md created. Documents read. Ready to begin Module 0. | Build order confirmed. Database to be v2-ready from day one. | Start Module 0: project setup + env vars |
| 21 Mar 2026 | Module 0 complete. Bot live on Railway, responding to /start on Telegram. | Railway + GitHub pipeline confirmed working. | Start Module 1: text/voice echo + logging |
| 21 Mar 2026 | Module 1 complete. database.py created (users table). main.py updated: text echo, voice handler, structured logging, /start preserved. | saathi.db excluded from git via .gitignore. Voice handler stubs file_id for Whisper in Module 8. | Start Module 2: full v2-ready SQLite schema |
| 21 Mar 2026 | Module 2 complete. Full 10-table schema in database.py. Users table migrated safely. All indexes in place. | Named tables: users, family_members, messages, diary_entries, health_logs, medicine_reminders, memories, heartbeat_log, protocol_log, session_log. | Start Module 3: DeepSeek + system prompt + Protocol 2 |
| 21 Mar 2026 | Module 3 complete. deepseek.py created with full Protocol 2 system prompt. main.py echo replaced with real AI responses. openai library added to requirements. English language default fixed: IMPORTANT instruction moved to first line of system prompt + Hindi example phrases removed + conversation history primed with two English exchanges before each user message. | DeepSeek uses openai SDK with base_url override. Context dict built from users table — enriched by Module 7 later. Conversation priming (hardcoded assistant+user turns) proved more reliable than system prompt instruction alone for language enforcement. | Start Module 4: Protocol 1 crisis handler |
| 22 Mar 2026 | Module 4 complete. protocol1.py created: keyword matching (Hindi/Hinglish/English), Stage 1 warm response, Stage 2 with iCall (9152987821), auto-escalation path. log_protocol_event() added to database.py. main.py updated to run Protocol 1 before every DeepSeek call with session-level trigger count tracking. | Family alert in escalation path is a stub — wired in Module 13/14. Session trigger count lives in-memory (_protocol1_session_counts dict) — Module 7 can persist this. | Start Module 5: Protocol 3 financial/legal handler |
| 22 Mar 2026 | Module 5 complete. protocol3.py created: three bucket keyword lists (Bucket 1 external pressure, Bucket 2 asset/inheritance, Bucket 3 will/estate) in Hindi/Hinglish/English. Single warm five-step response across all buckets — neutral on transaction, warm on feeling, points to CA/lawyer/trusted family. main.py wired: Protocol 3 runs after Protocol 1, before DeepSeek. | Deliberately one response for all buckets — avoids Saathi appearing to distinguish severity of financial decisions. | Start Module 6: onboarding flow |
| 22 Mar 2026 | Module 6 complete. onboarding.py created: 18-question child-led flow (steps 0–18). Answers saved progressively to DB. Step 0 = setup person name → family_members. Steps 1–18 cover all CLAUDE.md fields. database.py: update_user_fields(), advance_onboarding_step(), complete_onboarding(), add_family_members_bulk(), save_setup_person(), save_emergency_contact() added. medicines_raw column added via schema + migration. main.py: /start and handle_text both gate on onboarding_complete. | medicines_raw stored as raw text for Module 11 to parse into medicine_reminders. In-memory _ctx dict holds setup/senior name for question personalisation across steps. | Start Module 7: memory system (diary, retrieval, context injection) |
| 22 Mar 2026 | Module 11 complete. reminders.py created: add_reminder() normalises free-form time strings to HH:MM IST; get_due_reminders() matches current IST minute against schedule_time; check_and_send_reminders() sends bell tone (synthesized WAV, no dependencies) + text + TTS voice per due reminder; family escalation via telegram_user_id after 30 min unacknowledged; seed_reminders_from_raw() parses onboarding medicines_raw text on first scheduler tick. main.py: ack detection before onboarding gate (👍 marks reminder ack, returns early); reminder_job() registered on JobQueue (60s interval). database.py: family_alerted_at column added to medicine_reminders schema + migration. requirements.txt: [job-queue] extra added for APScheduler. | Bell tone is synthesized C5 WAV using only stdlib (wave/struct/math) — no audio lib needed. Family alert requires family member's telegram_user_id, which onboarding doesn't yet collect (only phone). This should be added to onboarding in a future pass. | Start Module 12: daily rituals |
| 22 Mar 2026 | Module 10 complete. youtube.py created: detect_music_request() matches 50+ English+Hindi/Hinglish music signal patterns, strips command words to extract clean search query, falls back to user music preferences for vague requests, appends "Indian" context when needed. find_music() calls YouTube Data API v3 (regionCode=IN, relevanceLanguage=hi) and returns top result. main.py: music check inserted after Protocol 3, before DeepSeek — returns immediately with clickable YouTube link. YouTube failure sends a warm fallback, never crashes pipeline. | Same GOOGLE_CLOUD_API_KEY used for TTS and YouTube — YouTube Data API v3 must be enabled in Cloud Console. | Start Module 11: medicine reminders + family escalation |
| 22 Mar 2026 | Module 11 corrected and complete. Two product corrections applied: (1) 3 reminder attempts before escalation — sends at scheduled time, +30 min, +60 min; family alerted only after all 3 go unacknowledged (90 min total). reminder_attempt column tracks daily attempt count, resets to 0 on ack. (2) Explicit opt-in only — escalation_opted_in column added to users (default 0); family never alerted unless opted in. Onboarding step 18 now sets escalation_opted_in alongside heartbeat_consent. | escalation_opted_in must always default to 0. Never escalate to family without explicit consent. Three reminders is the product minimum — this must not be changed to 1 in any future pass. | Start Module 12: daily rituals |
| 22 Mar 2026 | Module 12 complete. rituals.py created: morning/afternoon/evening rituals sent at user-set times via DeepSeek + TTS. Onboarding extended to 21 steps (0–20): steps 17/18/19 collect morning/afternoon/evening check-in times separately; heartbeat consent moves to step 20. user_activity_patterns table tracks first daily message hour. Adaptive learning nudges morning_checkin_time ±30 min/week max toward observed behaviour after 7 days of data. Weather/news/cricket/On This Day deferred to future pass. | Check-in times are user-set, not hardcoded. Adaptation is rate-limited to once per 7 days and caps at ±30 min per nudge to feel natural. Waking-hours only (5am–11pm) for activity tracking. | Start Module 13: Safety features |
| 22 Mar 2026 | Module 13 complete. safety.py created: emergency keyword detection (Hindi/English/Hinglish, 20+ patterns) fires before Protocol 1 in pipeline. /help command sends inline keyboard; "I need help" callback reassures senior and alerts family contacts (escalation_opted_in=1 required, telegram_user_id required per contact). Inactivity detector runs hourly, adaptive threshold = 2× avg inter-message gap (capped 24–168h, default 48h), logs to heartbeat_log. Heartbeat pings (3× daily) deferred. | Emergency keyword check must always run before Protocol 1. Family alert requires both escalation_opted_in=1 AND contact has telegram_user_id — never alert without both. | Start Module 14: Family bridge + weekly health report |
| 22 Mar 2026 | Module 9 complete. tts.py created: text_to_speech() calls Google Cloud TTS REST API with GOOGLE_CLOUD_API_KEY, returns OGG_OPUS bytes. WaveNet voices per language (hi-IN-Wavenet-A for Hindi/Hinglish, en-IN-Wavenet-D for English, others mapped). Speaking rate 0.9. Markdown stripped before TTS. main.py: text sent first, then voice note via reply_voice(); TTS failure is a logged warning — text already delivered so no silent failure. | Melody clips (for reminders) deferred to Module 11 where reminders fire. Only conversational responses get TTS for now. | Start Module 10: music via YouTube API |
| 22 Mar 2026 | Module 8 complete. whisper.py created: transcribe_voice() downloads OGG bytes, sends to Whisper API with language hint (Hindi/Hinglish→hi, English→en, etc.), returns text. main.py refactored: _run_pipeline() helper extracted so text and voice handlers share identical Protocol 1→3→DeepSeek logic. receive_voice() now downloads file into memory (no disk), transcribes, runs full pipeline. Error paths: Whisper fail → friendly message; empty transcription → prompt to type. | Synchronous OpenAI client in async handlers is fine for MVP — note for v2 to wrap in asyncio.to_thread(). | Start Module 9: voice output (Google TTS + melody clips) |
| 22 Mar 2026 | Module 7 complete. memory.py created: save_memory(), get_relevant_memories() (5 recent memories + last 3 diary entries + same-day-last-week + same-day-last-month), extract_and_save_memories() (DeepSeek JSON extraction per turn), write_diary_entry() (nightly DeepSeek summarisation → diary_entries upsert). deepseek.py: calls get_relevant_memories() inside call_deepseek() before building system prompt. main.py: save_message_record() called for in+out messages; extract_and_save_memories() called after each reply. database.py: save_message_record() and upsert_diary_entry() added. | extract_and_save_memories makes one extra DeepSeek call per turn — acceptable for MVP, revisit cost in v2. write_diary_entry scheduled by Module 12. | Start Module 8: voice input (Whisper) |
| 1 Apr 2026 | Four pre-test fixes applied + Module 14 wired. Fix 1: deepseek.py injects today's date (IST) into system prompt context block — DeepSeek no longer hallucinates dates. Fix 2: protocol4.py expanded with `sexxy`, `put out`, `a little more than friends`, `intimate`, `naughty`. Fix 3: youtube.py gains exclusion patterns for conversational "listen" phrases (`they dont listen`, `nobody listens`, `listen to me`, Hindi equivalents) — prevents false music triggers mid-conversation. Fix 4: deepseek.py system prompt gains explicit medication-refusal rule — when senior says they don't want medicine, respond in medication context, never reframe as autonomy philosophy. Module 14 wiring: database.py gains `family_linking_code` column (users), `last_weekly_report_sent` column (family_members), protocol_log CHECK constraint widened for P4. main.py: `/familycode` and `/join` command handlers added; family bridge relay wired into EOL pipeline section (registered family → message relayed to senior with warm formatting + confirmation sent back); `weekly_report_job` registered on scheduler (60s interval, self-gated to Sunday 10am IST). `_run_pipeline` now receives `context` param for bot access in relay. | protocol_log CHECK constraint migration uses table-recreation approach (SQLite limitation). Family relay is one-way (family→senior) for MVP. `context` param added to `_run_pipeline` to support relay bot access. | Start Module 15: Engagement design (follow-up questions, graceful exit, relationship tending) |
| 5 Apr 2026 | Module 15 complete. All 16 groups resolved. Group 16 required three-part fix: (1) deepseek.py — removed "unless you write in another language first" escape hatch from language priming; added language-lock-during-emotional-moments rule to system prompt; added Rule 4B-i (no excavation on vulnerability). (2) main.py — hardcoded vulnerability pre-processor: detects loneliness/vulnerability signals before DeepSeek call and wraps message with hard override (correct language, one plain acknowledgement, no questions). (3) protocol1.py — false positive removed: `nobody (cares\|would miss me\|needs me)` narrowed to `nobody would miss me` only; "nobody needs me" is loneliness not crisis and must fall through to vulnerability pre-processor. Group 16 final response: "That's a heavy thing to carry quietly." — PASS. | System prompt rules alone are insufficient to enforce language and response-shape during emotional content — DeepSeek overrides them. Hardcoded pre-processing (wrapping the message before it reaches DeepSeek) is the only reliable fix. Protocol 1 keyword lists must be reviewed for loneliness vs. crisis distinction before pilot. | Start Module 16: 300+ memory question bank. |
| 6 Apr 2026 | Module 18 complete. apis.py created: fetch_weather() (OpenWeatherMap), fetch_cricket() (CricAPI), fetch_news() (NewsAPI.org). All three are optional — return None gracefully if API key not configured, so morning briefing falls back to DeepSeek-generated content with no crash. 30-minute in-memory cache prevents duplicate API calls when multiple users share the same morning check-in time. _build_morning_instruction() in rituals.py updated to fetch real data, wrap via existing wrap_weather/wrap_news/wrap_cricket DeepSeek wrappers, and inject the result into the morning prompt. _get_users_due_for_ritual() query updated to fetch news_interests column. Three new env vars required in Railway: WEATHER_API_KEY, CRICKET_API_KEY, NEWS_API_KEY. | API data is always wrapped by DeepSeek before reaching the senior — raw numbers never shown directly. All three fetches are independently optional: bot works fine with 0, 1, 2, or all 3 keys configured. Cricket only included when an India match is live or recent — not forced every day. | Start Module 19: end-to-end capability testing. |
| 7 Apr 2026 | GPT 25-test evaluation run. 14/25 pass. 14 fixes implemented across 7 files (safety.py, protocol1.py, protocol3.py, deepseek.py, youtube.py, main.py, database.py). P0: mental health phrases removed from physical emergency keywords; Protocol 1 escalation now wires real family alert and uses honest response variants (_ESCALATION_RESPONSE_ALERT_SENT / _NO_ALERT); Stage 2 persistence backed by DB query (get_recent_protocol1_stage1_count); physical emergency now sends immediate 112 text + family alert without requiring button press. P1: greeting handler now language-aware + includes senior's name; language priming in call_deepseek() made dynamic per user language; hard language lock prepended as first line of every DeepSeek system prompt; implicit script detection + 5-message learning loop added (_detect_message_language, _update_language_learning). P2: grief signals separated from vulnerability signals (grief gets 2-3 sentences, vulnerability gets 1); short-reply disengagement pre-processor added; identity/confusion handler added; Protocol 3 responses shortened; medication rule softened; music response rotates 5 variants; build_music_message() now accepts language param. All 7 files pass py_compile. | Never lie to senior — escalation response is now two honest variants. No voice calls — bot only sends text messages to family contacts. Language lock must be first line of system prompt, not buried in rules. Script detection (Devanagari Unicode range) is reliable for Hindi; Hinglish detected via common word list. | Start Module 19: end-to-end capability testing. |
| 7 Apr 2026 | Module 19 capability test + full fix round. Two test passes run. Pass 1 (spot-check A–G): Protocol 1 pattern gaps fixed — `khatam kar loon`, `soch raha.{0,15}khatam` added to _ESCALATION_PATTERNS; `kya farak padta`, `thak.{0,30}zindagi` and hopelessness variants added to _STAGE1_PATTERNS; short-message language guard (≤3 words skip detection) added to main.py. Pass 2 (capability test — music/voice/news): Fix 1 (youtube.py) — find_music() now has 3-attempt fallback chain: original query → noise-stripped query → artist-only short query. Never shows "gaana nahi mil raha". Fix 2 (main.py receive_voice) — short transcriptions (≤2 words) return warm "please resend" instead of going through disengagement path. Fix 3 (main.py + deepseek.py) — _inject_live_data_if_needed() added: detects news/cricket/weather queries in conversation, calls fetch_news/fetch_cricket/fetch_weather directly, injects real data (or honest "no live data today") into DeepSeek system prompt. Prevents hallucinated news. All files pass py_compile. | Live data APIs were only wired to morning ritual, not to ad-hoc conversation — this was the root cause of repeated/hallucinated cricket news. wrap_news/wrap_cricket/wrap_weather reused from rituals.py — no new DeepSeek prompts needed. | Module 19 complete. Start Module 20: pilot prep. |
| 8 Apr 2026 | API verification + NewsAPI fallback fix. All three live data APIs confirmed working: weather (OpenWeatherMap ✅), cricket (CricAPI ✅), news (NewsAPI ✅). NewsAPI free tier returns 0 articles for country=in filter — fixed in apis.py: primary attempt uses country=in, falls back to /v2/everything with q=keyword (or "India") if 0 articles returned. All three keys confirmed added to Railway. test_apis.py created for one-time verification. | NewsAPI country=in is unreliable on free tier; /v2/everything with keyword query is the reliable fallback. Cricket correctly returns None on non-match days — not an error. | Start Module 20: pilot prep. |
| 8 Apr 2026 | Four pre-pilot fixes from Gemini evaluation. Fix 1 (typing indicator): send_chat_action("typing") added to handle_text and receive_voice in main.py — fires before every pipeline call and before Whisper transcription. Seniors no longer see a silent pause. Fix 2 (unsupported media handler): handle_unsupported_media() added to main.py — catches photos, stickers, GIFs, documents, video, audio. Returns language-aware warm message instead of silent drop. Registered after VOICE handler. Fix 3 (weekly report mood hardening): _get_mood_summary() in family.py now has a data sufficiency guard — concern language ("worth keeping an eye on") only fires if ≥4 diary entries exist AND ≥2 scored ≤2. A single bad day can no longer trigger a family alarm. Fix 4 (health check server): _start_health_server() added to main.py — starts a stdlib HTTP daemon thread on HEALTH_PORT (default 8080). GET /health returns 200 OK. Railway can be configured to use this for liveness checks. All files pass py_compile. | Typing indicator fires for ALL message types including fast exits — acceptable, response arrives quickly so the brief indicator is a feature not noise. Health server uses stdlib only, no new dependencies, daemon thread so it exits cleanly with main process. Mood sufficiency thresholds (≥4 entries, ≥2 scores ≤2) are conservative — err on the side of not alarming family. | Start Module 20: pilot prep. |
| 5 Apr 2026 | Module 17 complete. tts.py updated: hi-IN-Wavenet-A → hi-IN-Neural2-A; en-IN-Wavenet-D → en-IN-Neural2-D; _DEFAULT_VOICE updated to Neural2-D. All other regional languages remain on WaveNet (Neural2 not yet available for ta-IN, bn-IN, mr-IN, gu-IN, kn-IN, ml-IN). Same API endpoint, same key — drop-in upgrade. | Neural2 is only available for hi-IN and en-IN among Indian languages. No API key change needed. Audio quality improvement is significant — better prosody, more natural. | Start Module 18: News, sports & weather APIs. |
| 5 Apr 2026 | Module 16 complete. memory_questions.py created: 316 questions across 9 themes (35 per theme, 36 for Wisdom & Beliefs). Three new DB tables: memory_questions (global bank), user_question_tracking (per-user no-repeat tracking, resets on exhaustion), memory_prompt_log (daily send guard). Three new users columns: pending_memory_question_id, pending_memory_question_text, pending_memory_question_theme. seed_memory_questions() seeds bank on startup. get_next_memory_question(user_id) picks random unasked question. send_memory_prompt(bot, user_id) sends question as text + TTS, records in both tracking tables, sets pending flag on user. check_and_send_memory_prompts(bot) wired into check_and_send_rituals() in rituals.py — runs Wednesday + Sunday only at user's morning_checkin_time. main.py: seed_memory_questions() called on startup; response capture block added before onboarding gate — when senior has pending question, their next message is saved to memories table fully linked (question_id + question_text + theme all set), then message continues through normal pipeline so DeepSeek responds warmly. | Twice-a-week cadence (Wednesday + Sunday) chosen for pilot — adjust based on user feedback. Response capture does NOT return early — senior's response flows through to DeepSeek so conversation continues naturally. Existing memories table question_id and question_text columns are now fully populated (they were designed for this in Module 2 but were previously always NULL). 316 questions gives ~3 years of non-repeating weekly prompts per user. | Start Module 17: Voice upgrade (WaveNet → Neural2). |
