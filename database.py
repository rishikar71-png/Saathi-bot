import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "saathi.db")
TURSO_URL   = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

# ---------------------------------------------------------------------------
# Global connection cache — ONE connection for the entire process lifetime.
#
# Problem we're solving: libsql embedded-replica calls raw.sync() on every
# connect(), and each sync takes 25–45 seconds against Turso's cloud. With
# the old pattern (new connection per query), startup required 3 separate
# syncs (migrations → init_db → seed_questions) = 90+ seconds, causing
# Railway's container timeout to kill the process before the bot started.
#
# Solution: connect and sync exactly ONCE on startup, then reuse the same
# connection for all queries. The 'with get_connection() as conn:' pattern
# still commits/rolls back correctly — it just no longer closes the connection.
# ---------------------------------------------------------------------------
_GLOBAL_CONN = None


# ---------------------------------------------------------------------------
# libsql compatibility layer
# libsql_experimental connections don't support row_factory or context manager
# in the same way sqlite3 does. This thin wrapper adds both so the rest of
# database.py can treat Turso exactly like a local SQLite connection.
# ---------------------------------------------------------------------------

class _Row:
    """sqlite3.Row-compatible dict-like row. Supports row["col"], row[idx], row.keys()."""
    __slots__ = ("_keys", "_vals")

    def __init__(self, keys, vals):
        self._keys = keys
        self._vals = vals

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        try:
            return self._vals[self._keys.index(key)]
        except ValueError:
            raise KeyError(key)

    def keys(self):
        return list(self._keys)

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __repr__(self):
        return repr(dict(zip(self._keys, self._vals)))


class _Cursor:
    """Wraps a libsql cursor, converting rows to _Row objects."""
    __slots__ = ("_cur",)

    def __init__(self, cur):
        self._cur = cur

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self):
        return getattr(self._cur, "rowcount", -1)

    def execute(self, sql, params=()):
        self._cur.execute(sql, params)
        return self

    def executemany(self, sql, seq):
        self._cur.executemany(sql, seq)
        return self

    def _wrap(self, row):
        if row is None:
            return None
        desc = self._cur.description
        if desc:
            keys = [d[0] for d in desc]
            return _Row(keys, list(row))
        return row

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in (self._cur.fetchall() or [])]

    def __iter__(self):
        for row in self._cur:
            yield self._wrap(row)


class _Connection:
    """
    Wraps a libsql_experimental connection to be sqlite3-compatible:
    - Accepts conn.row_factory = sqlite3.Row (stored but handled at cursor level)
    - Supports 'with conn:' context manager (commit on success, rollback on error)
    - conn.execute() returns a _Cursor

    CRITICAL — write tracking:
    libsql's embedded-replica calls sync() inside commit(), which takes 10–12 seconds
    against Turso's cloud. The OLD code called commit() on EVERY 'with conn:' block —
    including pure SELECT operations — causing a 10–12s sync after every DB read.

    Fix: track whether any write (INSERT/UPDATE/DELETE/REPLACE) was executed in this
    block. Only call commit() (and thus sync()) if a write actually happened.
    Read-only blocks exit cleanly with no commit and no cloud sync.
    """
    __slots__ = ("_conn", "row_factory", "_dirty")

    def __init__(self, conn):
        self._conn = conn
        self.row_factory = None
        self._dirty = False  # becomes True when a write statement is executed

    def cursor(self):
        return _Cursor(self._conn.cursor())

    def execute(self, sql, params=()):
        # libsql requires a tuple — convert lists defensively so no callsite can break this
        if isinstance(params, list):
            params = tuple(params)
        # Track writes — mark dirty so __exit__ knows to commit (and sync to cloud)
        _sql_upper = sql.strip().upper()
        if any(_sql_upper.startswith(kw) for kw in ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER")):
            self._dirty = True
        try:
            return _Cursor(self._conn.execute(sql, params))
        except Exception as _exec_err:
            _err_str = str(_exec_err).lower()
            if any(kw in _err_str for kw in ("malformed", "corrupt", "not a database", "disk image")):
                import logging
                logging.getLogger(__name__).error(
                    "DB | malformed detected mid-query — resetting connection for next caller | sql=%s",
                    sql[:80],
                )
                _reset_connection()
            raise

    def executemany(self, sql, seq):
        self._dirty = True
        self._conn.executemany(sql, seq)

    def commit(self):
        self._conn.commit()
        self._dirty = False

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass
        self._dirty = False

    def close(self):
        self._conn.close()

    def sync(self):
        self._conn.sync()

    def __enter__(self):
        self._dirty = False  # reset for this block
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            if self._dirty:
                # Write happened — commit (this triggers Turso cloud sync, ~10–12s)
                self.commit()
            # else: pure read — skip commit entirely, no cloud sync
        else:
            self.rollback()
        return False


def _delete_db_file() -> None:
    """Delete the local DB file and its journal/wal siblings."""
    import logging
    _log = logging.getLogger(__name__)
    for suffix in ("", "-journal", "-wal", "-shm"):
        path = DB_PATH + suffix
        try:
            if os.path.exists(path):
                os.remove(path)
                _log.warning("DB | deleted corrupted file: %s", path)
        except Exception as del_err:
            _log.error("DB | could not delete %s: %s", path, del_err)


def _raw_connect():
    """
    Returns the global cached connection, creating it on first call.

    Turso/libsql: connects once, syncs once, then reuses the same connection
    for the entire process lifetime. This avoids the 25-45s sync penalty on
    every new connection (which was causing Railway startup timeouts).

    sqlite3 fallback: check_same_thread=False so the single connection can be
    used safely from the asyncio event loop and PTB's job scheduler.

    Self-healing: if the local DB file is malformed (corrupted embedded replica
    from an interrupted sync), delete it and retry so libsql creates a fresh
    copy from Turso. This prevents the crash-loop seen when Railway restarts
    after a mid-sync crash.
    """
    global _GLOBAL_CONN
    if _GLOBAL_CONN is not None:
        return _GLOBAL_CONN

    import logging
    _log = logging.getLogger(__name__)

    if TURSO_URL and TURSO_TOKEN:
        for attempt in (1, 2):   # attempt 2 retries after deleting the corrupted file
            try:
                import libsql_experimental as libsql      # noqa: PLC0415
                raw = libsql.connect(DB_PATH, sync_url=TURSO_URL, auth_token=TURSO_TOKEN)
                raw.sync()   # pull latest state from cloud — done exactly ONCE at startup
                _log.info("DB | Turso cloud connection established (global, single sync)")
                _GLOBAL_CONN = _Connection(raw)
                return _GLOBAL_CONN
            except Exception as e:
                err_str = str(e).lower()
                if "malformed" in err_str or "corrupt" in err_str or "not a database" in err_str:
                    if attempt == 1:
                        # Corrupted local replica — delete and retry once
                        _log.warning(
                            "DB | local replica malformed on attempt %d — deleting and retrying", attempt
                        )
                        _delete_db_file()
                        continue   # retry
                    else:
                        _log.error(
                            "DB | Turso still failing after file deletion (%s) — falling back to local SQLite",
                            e,
                        )
                else:
                    _log.error("DB | Turso connection FAILED (%s) — falling back to local SQLite", e)
                break  # non-malformed error or second attempt failed — fall through

    # Pure local SQLite path (Railway Volume or local dev).
    # Run integrity_check before connecting for real. If the file is malformed:
    #   - Turso was the primary path: safe to delete and restart (cloud is intact)
    #   - sqlite3 is the primary path: log a CRITICAL error but do NOT delete —
    #     the file is the only copy of all data; let WAL recovery attempt first.
    if os.path.exists(DB_PATH):
        import logging as _lg
        _ic_log = _lg.getLogger(__name__)
        try:
            test_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            result = test_conn.execute("PRAGMA integrity_check").fetchone()
            test_conn.close()
            if result and result[0] != "ok":
                raise Exception(f"integrity_check returned: {result[0]}")
        except Exception as _ic_err:
            if TURSO_URL and TURSO_TOKEN:
                # Turso was primary — deleting replica is safe
                _ic_log.warning(
                    "DB | local SQLite replica malformed (%s) — deleting for fresh Turso sync", _ic_err
                )
                _delete_db_file()
            else:
                # sqlite3 is primary — DO NOT delete; log critically and continue
                _ic_log.critical(
                    "DB | CRITICAL: saathi.db integrity check failed (%s). "
                    "File preserved — WAL recovery may succeed. "
                    "If bot fails to start, restore from Railway Volume backup.",
                    _ic_err,
                )

    _sqlite_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    # WAL mode: allows concurrent readers alongside the single writer.
    # Without this, any two threads accessing the DB simultaneously (asyncio
    # event loop + APScheduler job thread) will see "database is locked" errors.
    _sqlite_conn.execute("PRAGMA journal_mode=WAL")
    _sqlite_conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL; faster than FULL
    _GLOBAL_CONN = _sqlite_conn
    return _GLOBAL_CONN


def _reset_connection() -> None:
    """
    Discard the global connection so _raw_connect() will re-establish a clean
    one on the next call.

    IMPORTANT — file deletion policy:
    - libsql/Turso path: safe to delete the local replica; cloud copy is intact,
      next connect() will sync a fresh replica from Turso.
    - sqlite3/Railway Volume path: NEVER delete the file — it is the only copy
      of all user data. Null the connection and let sqlite3 reconnect to the
      same (possibly WAL-recovered) file instead.
    """
    global _GLOBAL_CONN
    import logging
    _log = logging.getLogger(__name__)

    if TURSO_URL and TURSO_TOKEN:
        # Turso: delete local replica — cloud is the source of truth
        _log.warning("DB | resetting malformed libsql connection and deleting replica")
        _GLOBAL_CONN = None
        _delete_db_file()
    else:
        # sqlite3: discard connection object only — DO NOT delete the file
        _log.warning(
            "DB | resetting malformed sqlite3 connection (file preserved — Railway Volume is source of truth)"
        )
        try:
            if _GLOBAL_CONN is not None:
                _GLOBAL_CONN.close()
        except Exception:
            pass
        _GLOBAL_CONN = None


def get_connection():
    global _GLOBAL_CONN
    conn = _raw_connect()
    conn.row_factory = sqlite3.Row   # no-op on _Connection but kept for sqlite3 path
    try:
        conn.execute("PRAGMA foreign_keys = ON")
    except Exception as _pragma_err:
        # If a basic PRAGMA fails with malformed/corrupt, the connection is dead.
        # Reset it so the NEXT caller gets a fresh one.
        _err = str(_pragma_err).lower()
        if any(kw in _err for kw in ("malformed", "corrupt", "not a database", "disk image")):
            _reset_connection()
            # Re-create the connection fresh for THIS caller too.
            _GLOBAL_CONN = None
            conn = _raw_connect()
    return conn


def run_startup_migrations() -> None:
    """
    Explicit startup migration — runs directly against the live DB file.
    Called first in main() before init_db(). Safe to run on every startup:
    CREATE TABLE uses IF NOT EXISTS, each ALTER TABLE is individually
    wrapped in try/except so duplicate-column errors are silently skipped.
    """
    import logging
    _log = logging.getLogger(__name__)
    conn = _raw_connect()
    migrations = [
        # session buffer table
        """CREATE TABLE IF NOT EXISTS session_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            role        TEXT    NOT NULL,
            content     TEXT    NOT NULL,
            created_at  TEXT    DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_session_messages_user_time ON session_messages(user_id, created_at)",
        # users table — new columns added after initial deploy
        "ALTER TABLE users ADD COLUMN onboarding_complete INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN current_session_start TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN last_message_at TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN protocol3_active INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN protocol3_triggered_at TEXT DEFAULT NULL",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception as e:
            _log.debug("MIGRATION skip: %s", e)

    # Widen protocol_log CHECK constraint to allow protocol_type '4'.
    # SQLite has no ALTER CONSTRAINT — recreate the table with data preserved.
    try:
        # Only run if the old constraint blocks '4'
        try:
            conn.execute(
                "INSERT INTO protocol_log (user_id, protocol_type, trigger_bucket, trigger_keywords) "
                "VALUES (-1, '4', 'test', 'test')"
            )
            # If it succeeded, the constraint already allows '4' — clean up test row
            conn.execute("DELETE FROM protocol_log WHERE user_id = -1")
            conn.commit()
        except Exception:
            # Constraint blocks '4' — recreate table
            conn.execute("""CREATE TABLE IF NOT EXISTS protocol_log_new (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                protocol_type       TEXT    NOT NULL,
                trigger_bucket      TEXT,
                trigger_keywords    TEXT,
                family_alerted      INTEGER DEFAULT 0,
                family_alert_time   TEXT,
                created_at          TEXT    DEFAULT (datetime('now'))
            )""")
            conn.execute("INSERT INTO protocol_log_new SELECT * FROM protocol_log")
            conn.execute("DROP TABLE protocol_log")
            conn.execute("ALTER TABLE protocol_log_new RENAME TO protocol_log")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_protocol_log_user_id ON protocol_log(user_id)")
            conn.commit()
            _log.info("MIGRATION: protocol_log CHECK constraint widened for P4")
    except Exception as e:
        _log.debug("MIGRATION protocol_log widen skip: %s", e)
    conn.commit()
    # Backfill: mark existing users who already have a name as onboarding complete
    # so they are never sent through the onboarding flow again.
    # Wrapped in try/except: on a fresh Railway deploy the users table doesn't
    # exist yet when this runs (init_db() hasn't been called). Safe to skip —
    # there are no users to backfill on a fresh DB.
    try:
        conn.execute("""
            UPDATE users
            SET onboarding_complete = 1
            WHERE name IS NOT NULL
              AND name != ''
              AND onboarding_complete = 0
        """)
        conn.commit()
    except Exception as e:
        _log.debug("MIGRATION backfill skip (fresh DB): %s", e)
    # Do NOT close — _raw_connect() now returns the global cached connection.
    # Closing it here would destroy the connection for the rest of the process.
    _log.info("STARTUP MIGRATIONS complete")


def init_db() -> None:
    """Create all tables and indexes. Safe to call on every startup."""
    import logging, time
    _log = logging.getLogger(__name__)

    with get_connection() as conn:
        t = time.time(); _create_tables(conn)
        _log.info("DB | _create_tables done (%.2fs)", time.time() - t)

        t = time.time(); _migrate_users_table(conn)
        _log.info("DB | _migrate_users_table done (%.2fs)", time.time() - t)

        t = time.time(); _migrate_reminders_table(conn)
        _log.info("DB | _migrate_reminders_table done (%.2fs)", time.time() - t)

        t = time.time(); _migrate_family_members_table(conn)
        _log.info("DB | _migrate_family_members_table done (%.2fs)", time.time() - t)

        t = time.time(); _migrate_diary_table(conn)
        _log.info("DB | _migrate_diary_table done (%.2fs)", time.time() - t)

        t = time.time(); _create_indexes(conn)
        _log.info("DB | _create_indexes done (%.2fs)", time.time() - t)

        t = time.time(); _backfill_onboarding_complete(conn)
        _log.info("DB | _backfill done (%.2fs)", time.time() - t)

        t = time.time(); conn.commit()
        _log.info("DB | commit done (%.2fs)", time.time() - t)


def _backfill_onboarding_complete(conn: sqlite3.Connection) -> None:
    """
    One-time backfill: mark users who already have a name set as onboarding complete.
    Safe to run on every startup — only updates rows where onboarding_complete is still 0.
    """
    conn.execute("""
        UPDATE users
        SET onboarding_complete = 1
        WHERE onboarding_complete = 0
          AND name IS NOT NULL
          AND name != ''
    """)


# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------

def _create_tables(conn: sqlite3.Connection) -> None:

    # ------------------------------------------------------------------
    # users — senior profile. Expanded for full product + v2 dashboard.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id                 INTEGER PRIMARY KEY,
            name                    TEXT,
            preferred_salutation    TEXT,
            age                     INTEGER,
            city                    TEXT,
            language                TEXT    DEFAULT 'hindi',
            persona                 TEXT    DEFAULT 'friend',
            bot_name                TEXT    DEFAULT 'Saathi',
            formality_level         TEXT    DEFAULT 'warm',
            spouse_name             TEXT,
            religion                TEXT,
            favourite_topics        TEXT,
            music_preferences       TEXT,
            news_interests          TEXT,
            health_sensitivities    TEXT,
            medicines_raw           TEXT,
            wake_time               TEXT,
            sleep_time              TEXT,
            morning_checkin_time    TEXT,
            afternoon_checkin_time  TEXT,
            evening_checkin_time    TEXT,
            last_adapted_at         TEXT,
            heartbeat_consent       INTEGER DEFAULT 0,
            heartbeat_enabled       INTEGER DEFAULT 0,
            escalation_opted_in     INTEGER DEFAULT 0,
            onboarding_complete     INTEGER DEFAULT 0,
            onboarding_step         INTEGER DEFAULT 0,
            last_active_at          TEXT,
            created_at              TEXT    DEFAULT (datetime('now')),
            updated_at              TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # family_members — linked family with roles + notification prefs.
    # Supports v2 caregiver dashboard access control.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS family_members (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL REFERENCES users(user_id),
            name                TEXT    NOT NULL,
            relationship        TEXT,
            telegram_user_id    INTEGER,
            phone               TEXT,
            role                TEXT    DEFAULT 'family',
            notification_prefs  TEXT    DEFAULT '{}',
            is_setup_user       INTEGER DEFAULT 0,
            created_at          TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # messages — full conversation history, in and out.
    # Powers context retrieval and nightly diary summarisation.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL REFERENCES users(user_id),
            direction           TEXT    NOT NULL CHECK(direction IN ('in', 'out')),
            message_type        TEXT    NOT NULL DEFAULT 'text',
            content             TEXT,
            telegram_message_id INTEGER,
            voice_file_id       TEXT,
            session_id          TEXT,
            created_at          TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # diary_entries — nightly summaries generated by DeepSeek.
    # Primary data source for v2 family dashboard mood trends.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS diary_entries (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id                 INTEGER NOT NULL REFERENCES users(user_id),
            entry_date              TEXT    NOT NULL,
            mood_score              INTEGER CHECK(mood_score BETWEEN 1 AND 5),
            mood_label              TEXT,
            health_complaints       TEXT    DEFAULT '[]',
            family_mentioned        TEXT    DEFAULT '[]',
            songs_requested         TEXT    DEFAULT '[]',
            reminders_acknowledged  INTEGER DEFAULT 0,
            protocol1_triggered     INTEGER DEFAULT 0,
            protocol3_triggered     INTEGER DEFAULT 0,
            emotions_summary        TEXT,
            full_summary            TEXT,
            created_at              TEXT    DEFAULT (datetime('now')),
            UNIQUE(user_id, entry_date)
        )
    """)

    # ------------------------------------------------------------------
    # health_logs — medicine acknowledgements + passive health mentions.
    # Powers medication adherence rate in v2 weekly family report.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(user_id),
            log_type        TEXT    NOT NULL DEFAULT 'mention',
            content         TEXT,
            source          TEXT    DEFAULT 'conversation',
            medicine_name   TEXT,
            reminder_id     INTEGER REFERENCES medicine_reminders(id),
            created_at      TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # medicine_reminders — scheduled per user, per medicine.
    # Tracks ack/miss streaks for family adherence report.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS medicine_reminders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(user_id),
            medicine_name   TEXT    NOT NULL,
            dosage          TEXT,
            schedule_time   TEXT    NOT NULL,
            days_of_week    TEXT    DEFAULT 'daily',
            is_active       INTEGER DEFAULT 1,
            last_sent_at      TEXT,
            last_acked_at     TEXT,
            family_alerted_at TEXT,
            reminder_attempt  INTEGER DEFAULT 0,
            ack_streak        INTEGER DEFAULT 0,
            miss_streak       INTEGER DEFAULT 0,
            created_at        TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # memories — life story responses to memory bank questions.
    # Builds personal memoir over months; surfaced in daily rituals.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL REFERENCES users(user_id),
            question_id         INTEGER,
            question_text       TEXT,
            response_text       TEXT,
            response_voice_file_id TEXT,
            theme               TEXT,
            created_at          TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # heartbeat_log — ping and response history + family alert history.
    # v2 dashboard: last active time, alert frequency.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS heartbeat_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL REFERENCES users(user_id),
            ping_time           TEXT    NOT NULL,
            response_time       TEXT,
            ping_number         INTEGER DEFAULT 1,
            family_alerted      INTEGER DEFAULT 0,
            family_alert_time   TEXT,
            alert_type          TEXT,
            created_at          TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # protocol_log — Protocol 1 (crisis) and Protocol 3 (financial)
    # trigger events. Anonymised. v2 dashboard: alert history.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS protocol_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL REFERENCES users(user_id),
            protocol_type       TEXT    NOT NULL CHECK(protocol_type IN ('1', '3', '4')),
            trigger_bucket      TEXT,
            trigger_keywords    TEXT,
            family_alerted      INTEGER DEFAULT 0,
            family_alert_time   TEXT,
            created_at          TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # session_log — session length, frequency, time of day.
    # Over-reliance monitoring: 3am patterns, session frequency.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(user_id),
            session_id      TEXT    UNIQUE NOT NULL,
            start_time      TEXT    NOT NULL,
            end_time        TEXT,
            duration_seconds INTEGER,
            message_count   INTEGER DEFAULT 0,
            hour_of_day     INTEGER CHECK(hour_of_day BETWEEN 0 AND 23),
            topics          TEXT    DEFAULT '[]',
            created_at      TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # user_activity_patterns — tracks first daily message time per user.
    # Powers adaptive morning check-in time (Module 12).
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_activity_patterns (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL REFERENCES users(user_id),
            activity_date       TEXT    NOT NULL,
            day_of_week         INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
            first_message_hour  INTEGER NOT NULL CHECK(first_message_hour BETWEEN 0 AND 23),
            created_at          TEXT    DEFAULT (datetime('now')),
            UNIQUE(user_id, activity_date)
        )
    """)

    # ------------------------------------------------------------------
    # ritual_log — records when each daily ritual was sent per user.
    # Prevents double-sending if the scheduler ticks during the same minute.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ritual_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(user_id),
            ritual_type TEXT    NOT NULL CHECK(ritual_type IN ('morning', 'afternoon', 'evening')),
            sent_date   TEXT    NOT NULL,
            created_at  TEXT    DEFAULT (datetime('now')),
            UNIQUE(user_id, ritual_type, sent_date)
        )
    """)

    # ------------------------------------------------------------------
    # session_messages — in-session conversation buffer.
    # Stores the current session's turn-by-turn exchanges so DeepSeek
    # receives full conversation context, not just diary summaries.
    # A session is any run of messages with gaps < SESSION_EXPIRY_MINUTES.
    # Rows older than 2 hours are automatically excluded from queries.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(user_id),
            role        TEXT    NOT NULL CHECK(role IN ('user', 'assistant')),
            content     TEXT    NOT NULL,
            created_at  TEXT    DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_messages_user_time
            ON session_messages(user_id, created_at)
    """)

    # ------------------------------------------------------------------
    # memory_questions — global bank of 300+ evocative life-story questions.
    # Seeded once on startup by memory_questions.py if the table is empty.
    # Not per-user — every user draws from the same bank.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_questions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            question_text TEXT    NOT NULL,
            theme         TEXT    NOT NULL,
            created_at    TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # user_question_tracking — records which questions have been asked to
    # which senior, so no question repeats until the full bank is exhausted.
    # Reset (all rows for a user deleted) when the bank cycles.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_question_tracking (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(user_id),
            question_id INTEGER NOT NULL REFERENCES memory_questions(id),
            asked_at    TEXT    DEFAULT (datetime('now')),
            UNIQUE(user_id, question_id)
        )
    """)

    # ------------------------------------------------------------------
    # memory_prompt_log — prevents sending more than one memory question
    # per day per user. UNIQUE(user_id, sent_date) is the guard.
    # ------------------------------------------------------------------
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_prompt_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(user_id),
            question_id INTEGER NOT NULL REFERENCES memory_questions(id),
            sent_date   TEXT    NOT NULL,
            UNIQUE(user_id, sent_date)
        )
    """)


# ---------------------------------------------------------------------------
# Migration — add new columns to the existing users table without data loss.
# Each ALTER TABLE is attempted individually; if the column already exists
# SQLite raises an OperationalError which we silently ignore.
# ---------------------------------------------------------------------------

_USERS_NEW_COLUMNS = [
    "ALTER TABLE users ADD COLUMN preferred_salutation TEXT",
    "ALTER TABLE users ADD COLUMN city TEXT",
    "ALTER TABLE users ADD COLUMN persona TEXT DEFAULT 'friend'",
    "ALTER TABLE users ADD COLUMN bot_name TEXT DEFAULT 'Saathi'",
    "ALTER TABLE users ADD COLUMN formality_level TEXT DEFAULT 'warm'",
    "ALTER TABLE users ADD COLUMN spouse_name TEXT",
    "ALTER TABLE users ADD COLUMN religion TEXT",
    "ALTER TABLE users ADD COLUMN favourite_topics TEXT",
    "ALTER TABLE users ADD COLUMN music_preferences TEXT",
    "ALTER TABLE users ADD COLUMN news_interests TEXT",
    "ALTER TABLE users ADD COLUMN health_sensitivities TEXT",
    "ALTER TABLE users ADD COLUMN medicines_raw TEXT",
    "ALTER TABLE users ADD COLUMN wake_time TEXT",
    "ALTER TABLE users ADD COLUMN sleep_time TEXT",
    "ALTER TABLE users ADD COLUMN evening_checkin_time TEXT",
    "ALTER TABLE users ADD COLUMN morning_checkin_time TEXT",
    "ALTER TABLE users ADD COLUMN afternoon_checkin_time TEXT",
    "ALTER TABLE users ADD COLUMN last_adapted_at TEXT",
    "ALTER TABLE users ADD COLUMN heartbeat_consent INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN heartbeat_enabled INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN escalation_opted_in INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN onboarding_step INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN last_active_at TEXT",
    "ALTER TABLE users ADD COLUMN updated_at TEXT DEFAULT (datetime('now'))",
    # Module 6 — onboarding mode detection and staged senior handoff
    "ALTER TABLE users ADD COLUMN setup_mode TEXT",
    "ALTER TABLE users ADD COLUMN handoff_step INTEGER DEFAULT 0",
    # Module 12 — First 7 Days arc
    "ALTER TABLE users ADD COLUMN days_since_first_message INTEGER DEFAULT 0",
    # Phase 1 — Privacy and trust tracking (Rule 10 privacy question)
    "ALTER TABLE users ADD COLUMN privacy_question_answered BOOLEAN DEFAULT 0",
    "ALTER TABLE users ADD COLUMN trust_check_count INTEGER DEFAULT 0",
    # Phase 1 — Account status and end-of-life protocol
    "ALTER TABLE users ADD COLUMN account_status TEXT DEFAULT 'active'",
    "ALTER TABLE users ADD COLUMN death_notification_timestamp TEXT DEFAULT NULL",
    "ALTER TABLE users ADD COLUMN death_notified_by TEXT DEFAULT NULL",
    "ALTER TABLE users ADD COLUMN eulogy_delivered BOOLEAN DEFAULT 0",
    # Phase 1 — Weekly report opt-in and family bridge opt-out
    "ALTER TABLE users ADD COLUMN weekly_report_opt_in BOOLEAN DEFAULT 0",
    "ALTER TABLE users ADD COLUMN family_bridge_opt_out BOOLEAN DEFAULT 0",
    # Module 6 two-mode fix — self-setup day tracking
    "ALTER TABLE users ADD COLUMN self_setup_day1_complete INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN self_setup_day2_complete INTEGER DEFAULT 0",
    # Module 5 Protocol 3 context fix — prevents re-fire loop
    "ALTER TABLE users ADD COLUMN protocol3_active INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN protocol3_triggered_at TEXT DEFAULT NULL",
    # Fix 5 — onboarding guard (missing from earlier migration pass)
    "ALTER TABLE users ADD COLUMN onboarding_complete INTEGER DEFAULT 0",
    # Fix 1 — session buffer tracking columns
    "ALTER TABLE users ADD COLUMN current_session_start TEXT DEFAULT NULL",
    "ALTER TABLE users ADD COLUMN last_message_at TEXT DEFAULT NULL",
    # Module 14 — family linking code for /familycode + /join flow
    "ALTER TABLE users ADD COLUMN family_linking_code TEXT DEFAULT NULL",
    # Module 16 — pending memory question (cleared after senior responds)
    "ALTER TABLE users ADD COLUMN pending_memory_question_id INTEGER DEFAULT NULL",
    "ALTER TABLE users ADD COLUMN pending_memory_question_text TEXT DEFAULT NULL",
    "ALTER TABLE users ADD COLUMN pending_memory_question_theme TEXT DEFAULT NULL",
]


def _migrate_users_table(conn: sqlite3.Connection) -> None:
    for sql in _USERS_NEW_COLUMNS:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists (sqlite3.OperationalError or libsql equivalent)


_REMINDERS_NEW_COLUMNS = [
    "ALTER TABLE medicine_reminders ADD COLUMN family_alerted_at TEXT",
    "ALTER TABLE medicine_reminders ADD COLUMN reminder_attempt INTEGER DEFAULT 0",
]


def _migrate_reminders_table(conn: sqlite3.Connection) -> None:
    for sql in _REMINDERS_NEW_COLUMNS:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists (sqlite3.OperationalError or libsql equivalent)


# Module 14 — add last_weekly_report_sent to family_members for dedup
_FAMILY_MEMBERS_NEW_COLUMNS = [
    "ALTER TABLE family_members ADD COLUMN last_weekly_report_sent TEXT DEFAULT NULL",
]


def _migrate_family_members_table(conn: sqlite3.Connection) -> None:
    for sql in _FAMILY_MEMBERS_NEW_COLUMNS:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists (sqlite3.OperationalError or libsql equivalent)


# Module 7 — add emotional_context and notable_moments to diary_entries
_DIARY_NEW_COLUMNS = [
    "ALTER TABLE diary_entries ADD COLUMN emotional_context TEXT",
    "ALTER TABLE diary_entries ADD COLUMN notable_moments TEXT",
]


def _migrate_diary_table(conn: sqlite3.Connection) -> None:
    for sql in _DIARY_NEW_COLUMNS:
        try:
            conn.execute(sql)
        except Exception:
            pass  # column already exists (sqlite3.OperationalError or libsql equivalent)


# ---------------------------------------------------------------------------
# Indexes — one per user_id foreign key, plus targeted query helpers.
# ---------------------------------------------------------------------------

def _create_indexes(conn: sqlite3.Connection) -> None:
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_family_members_user_id    ON family_members(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_messages_user_id           ON messages(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_messages_session_id        ON messages(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_diary_entries_user_id      ON diary_entries(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_diary_entries_date         ON diary_entries(user_id, entry_date)",
        "CREATE INDEX IF NOT EXISTS idx_health_logs_user_id        ON health_logs(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_medicine_reminders_user_id ON medicine_reminders(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_memories_user_id           ON memories(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_heartbeat_log_user_id      ON heartbeat_log(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_protocol_log_user_id           ON protocol_log(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_session_log_user_id            ON session_log(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_activity_patterns_user_id      ON user_activity_patterns(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_activity_patterns_date         ON user_activity_patterns(user_id, activity_date)",
        "CREATE INDEX IF NOT EXISTS idx_ritual_log_user_id             ON ritual_log(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_question_tracking_user     ON user_question_tracking(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_memory_prompt_log_user          ON memory_prompt_log(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_memory_questions_theme          ON memory_questions(theme)",
    ]
    for sql in indexes:
        conn.execute(sql)


# ---------------------------------------------------------------------------
# Helper — used by message handlers in main.py
# ---------------------------------------------------------------------------

def update_user_fields(user_id: int, **kwargs) -> None:
    """Update one or more columns in the users table in a single query."""
    if not kwargs:
        return
    set_clause = ", ".join(f"{k} = ?" for k in kwargs)
    values = tuple(kwargs.values()) + (user_id,)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE users SET {set_clause}, updated_at = datetime('now') WHERE user_id = ?",
            values,
        )
        conn.commit()
    # Invalidate cache — profile just changed, next read must go to DB
    invalidate_user_cache(user_id)


def advance_onboarding_step(user_id: int, step: int) -> None:
    update_user_fields(user_id, onboarding_step=step)


def complete_onboarding(user_id: int) -> None:
    update_user_fields(user_id, onboarding_complete=1, onboarding_step=21)


def add_family_members_bulk(user_id: int, names: list, relationship: str) -> None:
    """Insert multiple family members in one transaction."""
    if not names:
        return
    with get_connection() as conn:
        for name in names:
            clean = name.strip().title()
            if clean and clean.lower() not in ("no", "none", "nahi"):
                conn.execute(
                    """
                    INSERT INTO family_members (user_id, name, relationship, role)
                    VALUES (?, ?, ?, 'family')
                    """,
                    (user_id, clean, relationship),
                )
        conn.commit()


def save_setup_person(user_id: int, name: str) -> None:
    """Record the adult child who performed onboarding in family_members."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO family_members (user_id, name, relationship, role, is_setup_user)
            VALUES (?, ?, 'setup', 'family', 1)
            """,
            (user_id, name.strip()),
        )
        conn.commit()


def save_emergency_contact(user_id: int, name: str, phone: str) -> None:
    """Save the emergency contact collected during onboarding."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO family_members (user_id, name, relationship, phone, role)
            VALUES (?, ?, 'emergency_contact', ?, 'emergency')
            """,
            (user_id, name.strip(), phone.strip()),
        )
        conn.commit()


def save_message_record(
    user_id: int,
    direction: str,
    content: str,
    message_type: str = "text",
) -> None:
    """Persist an inbound or outbound message to the messages table."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO messages (user_id, direction, message_type, content)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, direction, message_type, content),
        )
        conn.commit()


def upsert_diary_entry(user_id: int, entry_date: str, **kwargs) -> None:
    """
    Insert or replace a diary entry for the given user and date.
    kwargs maps directly to diary_entries columns.
    """
    cols = ["user_id", "entry_date"] + list(kwargs.keys())
    placeholders = ", ".join("?" for _ in cols)
    values = (user_id, entry_date) + tuple(kwargs.values())
    with get_connection() as conn:
        conn.execute(
            f"""
            INSERT OR REPLACE INTO diary_entries ({', '.join(cols)})
            VALUES ({placeholders})
            """,
            values,
        )
        conn.commit()


def log_protocol_event(
    user_id: int,
    protocol_type: str,
    trigger_bucket: str,
    trigger_keywords: str,
    family_alerted: int = 0,
) -> None:
    """Write a Protocol 1 or Protocol 3 trigger event to protocol_log."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO protocol_log
                (user_id, protocol_type, trigger_bucket, trigger_keywords, family_alerted)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, protocol_type, trigger_bucket, trigger_keywords, family_alerted),
        )
        conn.commit()


def get_recent_protocol1_stage1_count(user_id: int, hours: int = 24) -> int:
    """
    Return the number of Protocol 1 Stage 1 triggers for this user
    in the last `hours` hours. Used to decide whether to fire Stage 2
    even after a bot restart (the in-memory counter resets on restart;
    this DB query ensures Stage 2 fires reliably within a 24-hour window).
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM protocol_log
                WHERE user_id = ?
                  AND protocol_type = '1'
                  AND trigger_bucket = 'stage1'
                  AND datetime(created_at) >= datetime('now', ? || ' hours')
                """,
                (user_id, f"-{hours}"),
            ).fetchone()
            return row["cnt"] if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Session buffer — in-session conversation history for DeepSeek context.
# A session is a run of messages with gaps < 60 minutes.
# ---------------------------------------------------------------------------

SESSION_EXPIRY_MINUTES = 60
# Max turns to retrieve — keeps context window manageable.
# 20 pairs = 40 messages; at ~50 tokens each ≈ 2000 tokens of history.
_SESSION_MAX_TURNS = 20


def save_session_turn(user_id: int, role: str, content: str) -> None:
    """
    Append one turn (user or assistant) to the session_messages table.
    Call this after every message exchange — DeepSeek, Protocol 3, rituals.
    """
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO session_messages (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content),
            )
            conn.commit()
    except Exception:
        pass  # non-fatal — degraded to diary-only context if this fails


def get_session_messages(user_id: int) -> list:
    """
    Return all turns from the current session (last 60 min) for this user,
    ordered oldest-first, capped at _SESSION_MAX_TURNS pairs (40 rows).

    Returns a list of dicts: [{'role': 'user'|'assistant', 'content': str}, ...]
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT role, content FROM session_messages
                WHERE user_id = ?
                  AND created_at >= datetime('now', ? || ' minutes')
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (user_id, f"-{SESSION_EXPIRY_MINUTES}", _SESSION_MAX_TURNS * 2),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    except Exception:
        return []


def clear_session_messages(user_id: int) -> None:
    """Delete all session messages for a user (e.g. on session expiry)."""
    try:
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM session_messages WHERE user_id = ?", (user_id,)
            )
            conn.commit()
    except Exception:
        pass


def admin_reset_user(telegram_id: int) -> str:
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE user_id = ?", (telegram_id,))
        row = c.fetchone()
        if not row:
            return f"User {telegram_id} not found in DB."
        user_id = row[0]
        c.execute("DELETE FROM diary_entries WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM health_logs WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM session_log WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM session_messages WHERE user_id = ?", (user_id,))
        conn.commit()
        return f"Reset complete for user {telegram_id}."


# ---------------------------------------------------------------------------
# In-memory user row cache
#
# get_or_create_user() is called on EVERY incoming message. With the libsql
# embedded replica, even a pure SELECT can take 10–12s when the connection
# is under load from concurrent background writes. User profiles almost never
# change mid-conversation (only during onboarding), so caching the row
# in memory per user drops this from 10–12s to ~0ms on every subsequent call.
#
# Cache is invalidated on update_user_fields() so onboarding changes are
# always reflected immediately.
# ---------------------------------------------------------------------------
import time as _time

_USER_CACHE: dict[int, tuple[float, object]] = {}   # user_id → (timestamp, row)
_USER_CACHE_TTL = 300   # 5 minutes — refresh silently in background after this


def invalidate_user_cache(user_id: int) -> None:
    """Remove a user from the in-memory cache. Call after any profile update."""
    _USER_CACHE.pop(user_id, None)


def get_or_create_user(user_id: int) -> sqlite3.Row:
    # Fast path: return cached row if fresh enough
    cached = _USER_CACHE.get(user_id)
    if cached is not None:
        ts, row = cached
        if _time.time() - ts < _USER_CACHE_TTL:
            return row
        # Stale — fall through to refresh from DB

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (user_id) VALUES (?)", (user_id,)
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()

    # Store in cache
    _USER_CACHE[user_id] = (_time.time(), row)
    return row
