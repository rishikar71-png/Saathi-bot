# SAATHI BOT — Build Progress

Last updated: 22 March 2026
Current phase: Module 6 — Onboarding Flow

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

### ⬜ Module 6 — Onboarding Flow (Child-Led, 18 Questions)
- [ ] First-time user detection
- [ ] Family setup mode offered to adult child
- [ ] All 18 questions asked in sequence with natural conversation
- [ ] Answers stored in `users` and `family_contacts` tables
- [ ] Bot naming step included
- [ ] Persona selection included
- [ ] Heartbeat consent collected
- [ ] Warm handoff message to senior generated in their language

---

### ⬜ Module 7 — Memory System
- [ ] Nightly diary summarisation (midnight cron job)
- [ ] Diary entry structure: family mentions, health, mood, songs, reminders, Protocol 3 flags
- [ ] Context retrieval before each conversation: profile + 3 diary entries + same-day-last-week + same-day-last-month
- [ ] Context silently injected into DeepSeek prompt
- [ ] Life archive storage for memory bank responses

---

### ⬜ Module 8 — Voice Input (Whisper)
- [ ] Voice note received from Telegram
- [ ] Sent to OpenAI Whisper API
- [ ] Transcription returned and processed as text
- [ ] Hindi + Hinglish transcription tested
- [ ] Cost per message logged

---

### ⬜ Module 9 — Voice Output (Google TTS + Melody Clips)
- [ ] Google TTS API connected
- [ ] Personalised voice reminder generation ('Ramesh ji, aapki dawai...')
- [ ] Voice message sent as Telegram audio
- [ ] 6-8 melody clips stored on server (temple bells, shehnai, harmonium)
- [ ] Melody clip sent alongside reminder text

---

### ⬜ Module 10 — Music (YouTube API)
- [ ] YouTube Data API v3 connected
- [ ] Song request detection from conversation
- [ ] Search returns exact video link + title + thumbnail
- [ ] Sent as Telegram message with link

---

### ⬜ Module 11 — Medicine Reminders + Family Escalation
- [ ] Reminders scheduled per user (from onboarding)
- [ ] Reminder fires as voice + melody at scheduled time
- [ ] 3 unanswered attempts → family contact notified
- [ ] Single 👍 acknowledges and resets counter
- [ ] Acknowledgement logged in `health_log`

---

### ⬜ Module 12 — Daily Rituals
- [ ] Morning briefing (name + weather + news + cricket + On This Day + religious/motivational + reminders + open question)
- [ ] Evening check-in + daily reflection prompt
- [ ] 'On This Day' content source integrated
- [ ] Festival/occasion calendar loaded (full Indian calendar)
- [ ] Family birthday/anniversary reminders from onboarding
- [ ] Daily trivia question

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
