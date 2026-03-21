import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "saathi.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                name        TEXT,
                age         INTEGER,
                language    TEXT    DEFAULT 'hindi',
                onboarding_complete INTEGER DEFAULT 0,
                created_at  TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.commit()


def get_or_create_user(user_id: int) -> sqlite3.Row:
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
        return row
