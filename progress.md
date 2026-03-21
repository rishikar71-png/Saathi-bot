# SAATHI BOT — Build Progress

Last updated: 21 March 2026
Current phase: Module 2 — Database Schema (SQLite, v2-Ready)

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

### ⬜ Module 2 — Database Schema (SQLite, v2-Ready)
**Critical: design with family caregiver dashboard in mind from day one.**
- [ ] `users` table
- [ ] `family_contacts` table
- [ ] `diary_entries` table
- [ ] `health_log` table
- [ ] `memory_archive` table
- [ ] `reminders` table
- [ ] `heartbeat_log` table
- [ ] `protocol_log` table
- [ ] `session_log` table
- [ ] Schema reviewed — all v2 dashboard fields present

---

### ⬜ Module 3 — DeepSeek Integration + System Prompt + Protocol 2 Sensitivity Wrapper
- [ ] DeepSeek API connected and responding
- [ ] Base system prompt written (Saathi persona, language, cultural context)
- [ ] Protocol 2 sensitivity wrapper baked into system prompt:
  - [ ] Indirect emotional distress scanning
  - [ ] Family conflict venting handling (validate feeling not story)
  - [ ] Money-guilt signal handling
  - [ ] Warmth default
- [ ] Language auto-detection (Hindi in → Hindi out, Hinglish in → Hinglish out)
- [ ] Context injection working (profile + diary entries fed to DeepSeek)
- [ ] Warm follow-up question default implemented
- [ ] Signal-reading graceful exit implemented

---

### ⬜ Module 4 — Protocol 1: Mental Health Crisis Handler
- [ ] Keyword/phrase list compiled (English + Hindi/Hinglish)
- [ ] Runs BEFORE DeepSeek on every message
- [ ] Stage 1 response written and tested
- [ ] Stage 2 response written and tested (Vandrevala Foundation mention)
- [ ] Stage 3 family alert implemented
- [ ] Auto-escalation for imminent-action language
- [ ] Protocol trigger logged to `protocol_log` table

---

### ⬜ Module 5 — Protocol 3: Financial/Legal Handler
- [ ] Trigger keyword list compiled (Bucket 1, 2, 3)
- [ ] Runs BEFORE DeepSeek on every message
- [ ] Response posture implemented (5-step)
- [ ] Neutral on transaction, warm on feeling
- [ ] Protocol trigger logged to `protocol_log` table

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
