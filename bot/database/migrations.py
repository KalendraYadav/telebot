# bot/database/migrations.py  (NEW)
# Run once: python -m bot.database.migrations
# Safe to re-run — every operation is idempotent.

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Resolve path relative to this file so it works from any CWD
_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "bot.db"


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")   # safer for concurrent writes
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cursor = conn.execute(f"PRAGMA table_info({table});")
    return any(row[1] == column for row in cursor.fetchall())


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?;",
        (table,),
    )
    return cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# Individual migration steps — each is idempotent
# ---------------------------------------------------------------------------

def _m001_add_is_active_to_users(conn: sqlite3.Connection) -> None:
    """Add is_active flag to existing users table."""
    if not _column_exists(conn, "users", "is_active"):
        conn.execute(
            "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;"
        )
        logger.info("[M001] Added users.is_active")


def _m002_add_role_to_users(conn: sqlite3.Connection) -> None:
    """Add role column to existing users table (member | admin | superadmin)."""
    if not _column_exists(conn, "users", "role"):
        conn.execute(
            "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'member';"
        )
        logger.info("[M002] Added users.role")


def _m003_create_groups_table(conn: sqlite3.Connection) -> None:
    """Tenant isolation: one row per Telegram group/chat."""
    if not _table_exists(conn, "groups"):
        conn.execute("""
            CREATE TABLE groups (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id       INTEGER NOT NULL UNIQUE,
                title         TEXT,
                is_active     INTEGER NOT NULL DEFAULT 1,
                plan          TEXT    NOT NULL DEFAULT 'free',
                created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            );
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_groups_chat_id ON groups (chat_id);"
        )
        logger.info("[M003] Created groups table")


def _m004_create_group_admins_table(conn: sqlite3.Connection) -> None:
    """Maps Telegram users to groups with an explicit admin role."""
    if not _table_exists(conn, "group_admins"):
        conn.execute("""
            CREATE TABLE group_admins (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                granted_by  INTEGER,
                granted_at  TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (chat_id, telegram_id)
            );
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_group_admins_chat "
            "ON group_admins (chat_id);"
        )
        logger.info("[M004] Created group_admins table")


def _m005_create_session_events_table(conn: sqlite3.Connection) -> None:
    """
    Structured session/event records replacing ad-hoc bot_data storage.
    One active session per group at a time (is_active flag).
    """
    if not _table_exists(conn, "session_events"):
        conn.execute("""
            CREATE TABLE session_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id       INTEGER NOT NULL,
                session_time  TEXT,
                topic         TEXT,
                host          TEXT,
                event_date    TEXT,
                platform      TEXT,
                raw_source    TEXT,
                is_active     INTEGER NOT NULL DEFAULT 1,
                created_by    INTEGER,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            );
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_session_events_chat_active "
            "ON session_events (chat_id, is_active);"
        )
        logger.info("[M005] Created session_events table")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

MIGRATIONS = [
    _m001_add_is_active_to_users,
    _m002_add_role_to_users,
    _m003_create_groups_table,
    _m004_create_group_admins_table,
    _m005_create_session_events_table,
]


def run_all() -> None:
    logger.info("Running migrations against: %s", _DB_PATH)
    conn = _connection()
    try:
        with conn:                          # single transaction for atomicity
            for step in MIGRATIONS:
                try:
                    step(conn)
                except Exception as exc:
                    logger.error("Migration %s failed: %s", step.__name__, exc)
                    raise                   # abort entire transaction on failure
        logger.info("All migrations completed successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_all()