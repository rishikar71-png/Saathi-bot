# SAATHI BOT — Build Progress

Last updated: 23 March 2026
Current phase: Module 14 — Family Integration

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
- **23 March update — full system prompt rewrite (22 March design decisions):**
- [x] Governing principle embedded: "Saathi is someone who is there… not someone who is trying"
- [x] Protocol 2 expanded from 4 → 11 rules (no over-praise, 3-sentence limit, 4-state energy match, no diagnosis, language switching, privacy language, gentle disagreement)
- [x] Three-mode engagement added: Active (ask) / Present (observe/offer) / Anchoring (forward-anchor)
- [x] All 11 behavioural rules added (Guided Drift, Low-Engagement Handling, Multi-Day, High-Engagement Containment, Vulnerability, Returning User, Organic Depth, Language Texture, Softening, Imperfection, Pacing)
- [x] Identity reinforcement principle added (restore post-retirement sense of relevance)
- [x] Self-description rules added + if-explicitly-asked response templates
- [x] First-contact behavioral rule added (7 rules: no question, no explanation, no enthusiasm, low-pressure, short, calm, silence-friendly)
- [x] Anti-growth-hack rule added (calm presence always wins over engagement optimisation)
- [x] Family reference handling rule added (affection framing only — never concern or minimising)
- [x] _build_system_prompt updated to concatenate user context block (no format variables)

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

### ✅ Module 6 — Onboarding Flow (Child-Led + Self Setup)
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
- **23 March update — 22 March design decisions:**
- [x] Opening detection question added: "Are you setting this up for yourself or for a family member?" fires before any onboarding steps
- [x] Mode 1 (family): detect_setup_mode() → 'family' → existing child-led 20-question flow
- [x] Mode 2 (self-setup): detect_setup_mode() → 'self' → MODE_2_FIRST_MESSAGE + Day 1 questions (5 questions), Day 2 questions in natural conversation
- [x] Staged 4-message handoff to senior: handoff_step column (0–4) tracks progress; Message 1 sent on senior's first contact, Messages 2–4 follow after each senior response
- [x] Confusion branch: is_confused_senior() detects confused first messages; warm explanation sent before handoff Message 1
- [x] get_setup_child_name() fetches child's name for handoff and confusion messages (from family_members is_setup_user=1)
- [x] Third-person bug fix: validate_no_third_person() guard added; all senior-facing messages verified first-person
- [x] Family reference framing validation: validate_family_framing() guard; affection framing only, concern/minimising framing blocked
- [x] New DB columns: setup_mode (TEXT), handoff_step (INTEGER DEFAULT 0)
- [x] main.py updated: mode detection gate before onboarding gate; handoff sequence handled before normal pipeline

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
- **23 March update — emotional memory context (22 March design decision):**
- [x] DB migration: emotional_context (TEXT) and notable_moments (TEXT, JSON array) columns added to diary_entries
- [x] Nightly diary prompt replaced with DIARY_SUMMARISATION_PROMPT (9 fields including emotional_context and notable_moments)
- [x] write_diary_entry() now saves emotional_context and notable_moments alongside existing fields
- [x] _format_diary_entry() helper: formats diary entry with emotional_context first; notable_moments as pipe-separated list
- [x] get_relevant_memories() updated to fetch and inject emotional_context into DeepSeek context (not just bare mood labels)
- [x] This is the difference between a database and a relationship: "You sounded so happy when you mentioned Priya last time" is now possible

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
- [x] Weather/news/cricket/On This Day — external API integrations deferred to later pass
- [x] Festival/occasion calendar — deferred
- [x] Family birthday/anniversary reminders from onboarding — deferred
- **23 March update — API pipes confirmed + First 7 Days arc (22 March design decisions):**
- [x] API pipe audit complete: NO open web browsing found — morning briefing was already DeepSeek-generated only
- [x] WEATHER_WRAP_PROMPT added: raw weather data → DeepSeek → warm sentence (never raw temperature)
- [x] CRICKET_WRAP_PROMPT added: raw score → DeepSeek → warm sentence with drama and context
- [x] NEWS_WRAP_PROMPT added: raw headline → DeepSeek → gentle summary, offer to say more
- [x] wrap_weather(), wrap_cricket(), wrap_news() helper functions ready for when API keys are integrated
- [x] ALLOWED_INFORMATION_SOURCES whitelist defined: weather_api, cricket_api, news_api only
- [x] FIRST_7_DAYS_ARC defined: 7 day-by-day configs (goal, morning_question, evening_prompt, topics_to_avoid, purpose_loops_active, saathi_posture, depth_ceiling)
- [x] get_day_arc(days_since_first_message) returns arc config for today; falls back to full-engagement post-Day 7
- [x] _build_morning_instruction() updated to use arc config: depth ceiling, posture, morning question all arc-driven
- [x] days_since_first_message column added to users table (INTEGER DEFAULT 0)
- [x] _increment_days_since_first_message() runs at 00:05 IST nightly — increments all onboarded users
- [x] Ritual query updated to fetch days_since_first_message for arc lookup

---

### ✅ Module 13 — Safety Features
- [x] Emergency keyword detection: "help", "bachao", "I fell", "gir gaya", "ambulance", "call someone" + 15 Hindi/English variants — fires before Protocol 1 in pipeline
- [x] /help command: inline keyboard with "I'm okay, just checking 🙏" and "I need help right now"
- [x] "I need help" callback: immediately reassures senior, alerts all family contacts with telegram_user_id
- [x] Family alert: fires only if escalation_opted_in=1 AND contacts have telegram_user_id — explicit opt-in respected
- [x] No contacts configured fallback: tells senior to call someone or dial 112
- [x] Inactivity detector: adaptive threshold = 2× average inter-message gap, bounded [24h, 168h], default 48h
- [x] Inactivity check: runs once per hour (module-level gate), opt-in only (heartbeat_consent=1)
- [x] Inactivity check-in: warm language-aware message ("kuch dino se aapki baat nahi hui — theek hain aap?")
- [x] Outlier gaps > 14 days excluded from threshold calculation (vacations, illness)
- [x] Every inactivity alert logged to heartbeat_log (alert_type='inactivity_checkin')
- [x] safety_job() registered in JobQueue (60s interval, 30s offset) — self-gated to once/hour
- [x] CallbackQueryHandler registered in main.py for help_ok/help_needed callbacks
- [ ] Heartbeat pings (3× daily, 👍 resets counter) — deferred: requires heartbeat scheduler design separate from inactivity

---

### ✅ Module 14 — Family Integration
- [x] Family bridge: `/familycode` + `/join` commands; family messages relayed warmly to senior
- [x] Weekly health report: self-gated to Sunday 10am IST; mood trends + health complaints + medication adherence sent to registered family contacts
- [x] database.py: family_linking_code (users), last_weekly_report_sent (family_members) added
- [x] protocol_log CHECK constraint widened via table-recreation migration (SQLite limitation)

---

### ✅ Module 15 — Engagement Design Polish
- [x] Full 16-group test suite run (module_15_test_protocol.md)
- [x] Groups 1–13: all PASS (Group 13 required fix — greeting handler intercepted mid-session returns)
- [x] Group 13 fix: `len(_session_history) < 4` guard added to greeting handler; `_original_text` prevents targeted prompt saving to history
- [x] Group 14: skipped — evening ritual requires waiting for scheduled time; verify during pilot
- [x] Group 15 (high-engagement containment): PASS
- [x] Group 16 (vulnerability — warm without probing): PASS — "That's a heavy thing to carry quietly."
- **Bugs found and fixed during testing:**
- [x] Language priming escape hatch removed: "unless you write in another language first" → now absolute for English users
- [x] Explicit language-lock rule added to system prompt: never switch language based on emotional content
- [x] Rule 4B-i added: vulnerability disclosure → one plain acknowledgement, no multi-part probe
- [x] Hardcoded vulnerability pre-processor in main.py: detects loneliness signals before DeepSeek, wraps message with hard override (language + no-probe)
- [x] Protocol 1 false positive fixed: `nobody (cares|would miss me|needs me)` → `nobody would miss me` only

---

### ✅ Module 16 — 300+ Memory Question Bank
- [x] 316 questions written across 9 themes (35 per theme, 36 for Wisdom & Beliefs):
  - Childhood & School (35)
  - Family & Relationships (35)
  - Career & Life (35)
  - India & History (35)
  - Food & Culture (35)
  - Music & Films (35)
  - Places & Travel (35)
  - Festivals & Traditions (35)
  - Wisdom & Beliefs (36)
- [x] `memory_questions` table in database.py (question text, theme — global bank)
- [x] `user_question_tracking` table — per-user no-repeat record; reset when bank exhausted
- [x] `memory_prompt_log` table — UNIQUE(user_id, sent_date) prevents double-send
- [x] `pending_memory_question_id`, `pending_memory_question_text`, `pending_memory_question_theme` columns added to users table via migration
- [x] `seed_memory_questions()` — populates DB on startup, skips if already seeded
- [x] `get_next_memory_question(user_id)` — random unasked question; auto-resets cycle when exhausted
- [x] `send_memory_prompt(bot, user_id)` — sends question as text + TTS, records in both tracking tables, sets pending flag
- [x] `check_and_send_memory_prompts(bot)` — Wednesday + Sunday only, at morning_checkin_time
- [x] Wired into `check_and_send_rituals()` in rituals.py (after morning/afternoon/evening block)
- [x] `seed_memory_questions()` called from `main()` on startup
- [x] Response capture block in `_run_pipeline()` in main.py: pending question detected → save_memory_response() → memories table fully linked → message continues to DeepSeek (no early return)
- [x] Senior responses stored in existing `memories` table (Module 7) — question_id, question_text, and theme now all populated (previously always NULL)
- [x] All syntax checks passed; schema verified in test DB; seed + selection verified end-to-end
- Note: 316 questions = ~3 years of non-repeating weekly prompts per user. Cycle resets automatically.

---

### ✅ Module 17 — Voice Upgrade (WaveNet → Neural2)
- [x] Upgrade English voice: `en-IN-Wavenet-D` → `en-IN-Neural2-D` (same gender — male, natural)
- [x] Upgrade Hindi voice: `hi-IN-Wavenet-A` → `hi-IN-Neural2-A` (same gender — female, warm)
- [x] Hinglish also upgraded: shares hi-IN-Neural2-A with Hindi
- [x] _DEFAULT_VOICE updated to en-IN-Neural2-D
- [x] All other regional languages audited — Neural2 not available for ta-IN, bn-IN, mr-IN, gu-IN, kn-IN, ml-IN; remain on WaveNet (best tier available for them)
- [x] No API key change needed — same GOOGLE_CLOUD_API_KEY, Neural2 is on same endpoint
- [x] Syntax verified — py_compile passed
- Note: Audio quality testing against live Telegram TTS to be done in Module 19 (end-to-end capability testing)

---

### ✅ Module 18 — News, Sports & Weather APIs
- [x] Weather: OpenWeatherMap (`/data/2.5/weather`, metric units, city from user profile)
- [x] Cricket: CricAPI (`/v1/currentMatches`, filtered for India matches, score + status + venue)
- [x] News: NewsAPI.org (`/v2/top-headlines`, country=in, filtered by user's news_interests keyword; category map for common topics; [Removed] articles skipped)
- [x] apis.py created: `fetch_weather(city)`, `fetch_cricket()`, `fetch_news(interests)` — each returns plain-text string or None
- [x] All three fetches are independently optional: if key not in env, returns None — no crash, morning briefing continues with DeepSeek-generated content
- [x] 30-minute in-memory cache (`_CACHE` dict with TTL) prevents hammering APIs when many users share same check-in time
- [x] `_build_morning_instruction()` updated: calls all three fetches, wraps via existing `wrap_weather/news/cricket` DeepSeek wrappers, injects pre-wrapped sentences into morning prompt
- [x] `_get_users_due_for_ritual()` query updated to select `news_interests` column
- [x] Cricket: only included in morning briefing when an India match is live/recent — not forced every day
- [x] All syntax checks passed; 5-test smoke suite passed (None-on-missing-key, cache, match detection, no-India-match, [Removed] skip)
- **New Railway env vars to add:**
  - `WEATHER_API_KEY` — openweathermap.org (free tier)
  - `CRICKET_API_KEY` — api.cricapi.com (free tier, 100 calls/day)
  - `NEWS_API_KEY` — newsapi.org (free tier, 100 calls/day)

---

### ⬜ Module 19 — End-to-End Capability Testing
- [ ] YouTube music: request a song by name, by mood, by genre — confirm real links returned
- [ ] YouTube music: vague request ("kuch sunao") — confirm fallback to preferences
- [ ] News: morning briefing fires with a real headline (not hallucinated)
- [ ] Cricket: morning briefing includes real score/schedule when a match is live
- [ ] Weather: morning briefing references real conditions for user's city
- [ ] Voice (Neural2): send a voice message and verify quality improvement
- [ ] Voice: Hindi and English both tested
- [ ] Full pipeline test: onboarding → memory question → medicine reminder → evening ritual → family bridge

---

### ⬜ Module 20 — Testing + Pilot Prep
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
