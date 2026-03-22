# SAATHI BOT — Build Progress

Last updated: 22 March 2026
Current phase: Module 12 — Daily Rituals

---

## Module Status

### ✅ Module 0 — Project Setup
- [x] GitHub repo `saathi-bot` created (private)
- [x] Railway.app connected to GitHub
- [x] All environment variables added to Railway:
  - TELEGRAM_BOT_TOKEN
  - DEEPSEEK_API_KEY
  - OPENAI_API_KEY
  - GOOGLE_CLOUD_API_KEY
  - ELEVENLABS_API_KEY
- [x] Python project structure created (`main.py`, `requirements.txt`, `.env.example`)
- [x] Telegram webhook registered
- [x] First successful deployment to Railway (bot responds to /start)

---

### ✅ Module 1 — Telegram Bot + Basic Echo
- [x] Bot receives text messages and echoes them back
- [x] Bot receives voice notes (stores temporarily for Whisper in Module 8)
- [x] /start command handled with placeholder greeting
- [x] Logging in place (message received, response sent, errors)

---

### ✅ Module 2 — Database Schema (SQLite, v2-Ready)
**Critical: design with family caregiver dashboard in mind from day one.**
- [x] `users` table (expanded with full onboarding + persona + safety fields)
- [x] `family_members` table (roles, notification prefs, setup user flag)
- [x] `messages` table (full in/out history, session grouping)
- [x] `diary_entries` table (mood score, health, family, protocol flags)
- [x] `health_logs` table (medicine ack + passive health mentions)
- [x] `medicine_reminders` table (schedule, ack/miss streaks)
- [x] `memories` table (life archive with theme + question linking)
- [x] `heartbeat_log` table (ping history, family alert history)
- [x] `protocol_log` table (Protocol 1 + 3 trigger events, anonymised)
- [x] `session_log` table (session length, frequency, hour-of-day)
- [x] Schema reviewed — all v2 dashboard fields present
- [x] Indexes on user_id for all tables + targeted query indexes
- [x] Migration function for existing users table (no data loss)

---

### ✅ Module 3 — DeepSeek Integration + System Prompt + Protocol 2 Sensitivity Wrapper
- [x] DeepSeek API connected and responding (deepseek-chat / V3)
- [x] Base system prompt written (Saathi persona, language, cultural context)
- [x] Protocol 2 sensitivity wrapper baked into system prompt:
  - [x] Indirect emotional distress scanning (Rule 1)
  - [x] Family conflict venting handling — validate feeling not story (Rule 2)
  - [x] Money-guilt signal handling (Rule 3)
  - [x] Warmth default (Rule 4)
- [x] Language auto-detection (Hindi in → Hindi out, Hinglish in → Hinglish out)
- [x] Context injection working (profile fields from users table fed to DeepSeek)
- [x] Warm follow-up question default implemented
- [x] Signal-reading graceful exit implemented
- [x] Human relationship tending nudges in system prompt
- [x] Persona system (friend/caring_child/grandchild/assistant) shapes tone

---

### ✅ Module 4 — Protocol 1: Mental Health Crisis Handler
- [x] Keyword/phrase list compiled (English + Hindi/Hinglish) — 40+ Stage 1 patterns, 15+ escalation patterns
- [x] Runs BEFORE DeepSeek on every message
- [x] Stage 1 response — warm, present, invites more conversation
- [x] Stage 2 response — iCall helpline (9152987821) + family contact offer with consent
- [x] Auto-escalation path — fires on imminent-action language, family alert stub in place
- [x] Protocol trigger logged to `protocol_log` table via log_protocol_event()
- [x] Session-level trigger count tracked (upgrades Stage 1 → Stage 2 on repeated trigger)

---

### ✅ Module 5 — Protocol 3: Financial/Legal Handler
- [x] Trigger keyword list compiled (Bucket 1, 2, 3) — Hindi/Hinglish/English, 60+ patterns
- [x] Runs BEFORE DeepSeek, AFTER Protocol 1, on every message
- [x] Response posture implemented (5-step): acknowledge → name the weight → honest limits → real human → leave door open
- [x] Completely neutral on transaction — warm on feeling only
- [x] Protocol trigger logged to `protocol_log` table (bucket name recorded)

---

### ✅ Module 6 — Onboarding Flow (Child-Led, 18 Questions)
- [x] First-time user detection (onboarding_complete = 0 gate in main.py)
- [x] Onboarding offered as family setup mode on /start
- [x] All 18 questions asked one at a time, personalised with earlier answers (setup_name, senior_name)
- [x] Answers stored progressively: users table (name, salutation, city, language, spouse, health, medicines, music, topics, religion, news, persona, bot_name, wake/sleep times, heartbeat consent) + family_members table (setup person, children, grandchildren, emergency contact)
- [x] Bot naming step (step 16) — stores in users.bot_name
- [x] Persona selection (step 15) — friend / caring_child / grandchild / assistant
- [x] Heartbeat consent collected (step 18) — sets heartbeat_consent + heartbeat_enabled
- [x] Warm personalised completion message with senior name + bot name
- [x] /start mid-onboarding resumes from current step (not restart)
- [x] medicines_raw column added to users table (parsed by Module 11)

---

### ✅ Module 7 — Memory System
- [x] save_memory(user_id, memory_text, memory_type) — saves to memories table (response_text + theme columns)
- [x] get_relevant_memories(user_id) — 5 recent memories + last 3 diary summaries + same-day-last-week + same-day-last-month; returns formatted string
- [x] extract_and_save_memories(user_id, user_message, bot_response) — DeepSeek JSON extraction after every turn; silently swallows failures
- [x] write_diary_entry(user_id) — fetches today's messages, DeepSeek summarises into full diary_entries row (mood_score, mood_label, health_complaints, family_mentioned, songs_requested, protocol flags, emotions_summary, full_summary)
- [x] deepseek.py: get_relevant_memories() called inside call_deepseek() before system prompt is built; injected as memory_context block
- [x] main.py: save_message_record() for every inbound + outbound message; extract_and_save_memories() called after each DeepSeek reply
- [x] database.py: save_message_record() and upsert_diary_entry() added
- [x] Nightly scheduling is a stub — Module 12 will wire the cron call to write_diary_entry()

---

### ✅ Module 8 — Voice Input (Whisper)
- [x] Voice note received from Telegram — downloaded into memory as bytes (no disk writes)
- [x] Sent to OpenAI Whisper API with language hint (user's language preference mapped to Whisper codes)
- [x] Transcription passed through full pipeline: Protocol 1 → Protocol 3 → DeepSeek — identical to text messages
- [x] Hindi/Hinglish: language hint "hi" passed; Tamil, Telugu, Bengali, Marathi, Gujarati, Punjabi, Kannada, Malayalam all mapped
- [x] Error handling: Whisper failure → "Sorry, I couldn't hear that clearly. Could you type it instead?"
- [x] Empty transcription handled separately with a prompt to type
- [x] main.py refactored: _run_pipeline() helper shared between handle_text and receive_voice

---

### ✅ Module 9 — Voice Output (Google TTS + Melody Clips)
- [x] Google TTS REST API connected via GOOGLE_CLOUD_API_KEY
- [x] text_to_speech(text, user_language) → OGG_OPUS bytes
- [x] WaveNet voices per language: hi-IN-Wavenet-A (Hindi/Hinglish), en-IN-Wavenet-D (English), ta-IN, bn-IN, mr-IN, gu-IN, kn-IN, ml-IN WaveNet voices mapped; English fallback for others
- [x] Speaking rate 0.9 — slightly slower for elderly clarity
- [x] Markdown stripped before TTS so symbols aren't read aloud
- [x] main.py: text sent first, voice note follows via reply_voice(); TTS failure is a silent warning — response always delivered
- [ ] Melody clips (temple bells, shehnai, harmonium) — deferred to Module 11 (medicine reminders) where they are needed
- [ ] Personalised reminder voice notes ('Ramesh ji, aapki dawai...') — deferred to Module 11

---

### ✅ Module 10 — Music (YouTube API)
- [x] YouTube Data API v3 connected via GOOGLE_CLOUD_API_KEY (regionCode=IN, relevanceLanguage=hi)
- [x] detect_music_request(): 50+ signal patterns in English + Hindi/Hinglish (bhajan, ghazal, gana, sunao, bajao, classical, qawwali, kirtan, aarti, mantra, etc.)
- [x] Vague requests ("kuch sunao", "play something") fall back to user's music preferences from onboarding
- [x] "Indian" context appended to queries that don't already specify a genre/language
- [x] find_music() returns top YouTube result title + URL
- [x] Warm response: "🎵 Yeh lijiye!" + bold title + clickable URL
- [x] YouTube failure sends friendly Hindi fallback message, never crashes pipeline
- [x] Music check runs after Protocol 1 + 3, before DeepSeek — exits pipeline on match

---

### ✅ Module 11 — Medicine Reminders + Family Escalation
- [x] add_reminder(user_id, medicine_name, time_str, frequency) — parses free-form time ("8am", "morning", "21:00") to HH:MM IST
- [x] get_due_reminders() — initial send at schedule_time + up to 2 retries at 30-min intervals; skips if acked
- [x] Reminder fires as: bell tone (synthesized C5 WAV, stdlib only) + text message + TTS voice note
- [x] Single 👍 / "haan" / "le li" / "kha li" acknowledges — updates ack_streak, resets miss_streak, resets reminder_attempt
- [x] Ack detection runs before all other pipeline steps so 👍 is never routed to DeepSeek
- [x] Three attempts before escalation: reminder sent at 0 min, +30 min, +60 min; family alerted only after 3rd goes unacknowledged (90 min total)
- [x] Explicit opt-in only: escalation_opted_in INTEGER DEFAULT 0 in users table; family never alerted unless user opted in at onboarding
- [x] seed_reminders_from_raw() parses medicines_raw from onboarding on first scheduler tick
- [x] check_and_send_reminders(bot) registered as JobQueue job (60s interval) in main.py
- [x] family_alerted_at + reminder_attempt columns added to medicine_reminders (schema + migration)
- [x] escalation_opted_in column added to users (schema + migration)
- [x] onboarding step 18 sets escalation_opted_in alongside heartbeat_consent and heartbeat_enabled
- [ ] Note: family alert requires telegram_user_id — onboarding currently only collects phone number. Future pass needed.

---

### ✅ Module 12 — Daily Rituals
- [x] Three check-in questions added to onboarding (steps 17/18/19): morning, afternoon, evening times stored in users table
- [x] Heartbeat consent moves to step 20; onboarding now completes at step 21
- [x] morning_checkin_time, afternoon_checkin_time columns added to users (evening_checkin_time was already present)
- [x] user_activity_patterns table: records first daily message hour + day_of_week per user (UNIQUE per user/date)
- [x] ritual_log table: prevents double-sending per user/ritual_type/date
- [x] rituals.py: check_and_send_rituals() queries users by stored check-in time, sends via DeepSeek + TTS
- [x] Morning briefing: warm greeting + thought for the day (religion/topics aligned) + one open question
- [x] Afternoon check-in: warm one-line prompt
- [x] Evening check-in: daily reflection (one good thing from today)
- [x] record_first_message(user_id) called on every inbound message — tracks waking-hours first message only (5am–11pm)
- [x] Adaptive learning: after 7+ days data, nudge morning_checkin_time toward average first-message hour (max ±30 min per week, skips if delta <15 min)
- [x] Adaptation rate-limited: last_adapted_at column in users, re-adapts only after 7 days
- [x] ritual_job() registered in JobQueue (60s interval, 15s offset from reminder_job)
- [ ] Weather/news/cricket/On This Day — external API integrations deferred to later pass
- [ ] Festival/occasion calendar — deferred
- [ ] Family birthday/anniversary reminders from onboarding — deferred

---

### ⬜ Module 13 — Safety Features
- [ ] Heartbeat: 3 pings at 30-min intervals (morning/afternoon/evening)
- [ ] Single 👍 resets heartbeat counter
- [ ] Family alert sent if no response after 3 pings (warm framing)
- [ ] Silence detection: 4+ hours no message during waking hours → gentle check-in
- [ ] Silence detection: 30 min no response to check-in → quiet family alert
- [ ] Emergency command: 'help', 'emergency', 'I fell', 'call someone' → immediate family alert
- [ ] All heartbeat/alert events logged to `heartbeat_log`

---

### ⬜ Module 14 — Family Integration
- [ ] Family bridge: family messages bot → relayed warmly to senior
- [ ] Weekly health report: every Sunday → mood trends + health complaints + medication adherence to family contact

---

### ⬜ Module 15 — Engagement Design Polish
- [ ] Warm follow-up question default tested across conversation types
- [ ] Signal-reading graceful exit tested (two short replies / silence detection)
- [ ] Human relationship tending nudges tested
- [ ] Purpose loops (all 5) tested end-to-end

---

### ⬜ Module 16 — 300+ Memory Question Bank
- [ ] Questions written/compiled across all themes:
  - Childhood & School
  - Family & Relationships
  - Career & Life
  - India & History
  - Food & Culture
- [ ] Stored in database
- [ ] Random selection logic (no repeats until bank exhausted)
- [ ] Responses stored in `memory_archive`

---

### ⬜ Module 17 — Testing + Pilot Prep
- [ ] End-to-end test with all modules connected
- [ ] 5 test users (non-seniors) run through full flow
- [ ] 20-user pilot invite list prepared
- [ ] Onboarding instructions written for adult children
- [ ] Railway scaled as needed

---

## Known Issues & Decisions Made During Build

*(Add entries here as they arise)*

| Date | Issue/Decision | Resolution |
|---|---|---|
| 21 Mar | — | — |

---

## Files Created So Far

| File | Purpose |
|---|---|
| CLAUDE.md | Master context — read at start of every session |
| progress.md | This file — module-by-module build tracker |
| protocol1.py | Protocol 1 crisis handler — keyword matching, staged responses, escalation |
| protocol3.py | Protocol 3 financial/legal handler — three bucket keyword matching, warm deflection response |
| onboarding.py | Child-led 18-question onboarding flow — progressive DB saving, personalised questions, warm completion |
| memory.py | Memory system — save/retrieve memories, extract from conversation, nightly diary entry |
| whisper.py | Voice transcription — OGG bytes → Whisper API → text, with per-language hints |
| tts.py | Google Cloud TTS — text → OGG_OPUS bytes, WaveNet voices per language, markdown stripped |
| youtube.py | Music request detection + YouTube Data API v3 search, warm link response |
| reminders.py | Medicine reminders: scheduler, bell tone, TTS voice, ack detection, family escalation |
