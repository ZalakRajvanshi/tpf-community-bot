"""Lightweight SQLite persistence for channel monitoring.

The bot observes Slack activity and keeps a rolling record of messages so a
Human POC can be given context (e.g. the daily digest). Nothing here decides
what to *do* with the data — that stays with the POC. SQLite keeps the POC
demo dependency-free; swap for Postgres/Redis if this ever needs to scale or
run across multiple instances.

Note: on an ephemeral host (e.g. Render's free tier) the DB file is wiped on
redeploy. That is fine for the current local/testing phase; point DB_PATH at a
persistent volume before relying on it in production.
"""

import os
import sqlite3
import time

DB_PATH = os.environ.get("DB_PATH", "bot_data.db")


def _connect():
    # A fresh connection per call keeps things thread-safe under Flask's
    # threaded dev server without sharing a cursor across threads.
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                user_id    TEXT,
                text       TEXT,
                ts         TEXT,          -- Slack message ts (string)
                created_at REAL NOT NULL  -- epoch seconds we recorded it
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS members (
                user_id   TEXT PRIMARY KEY,
                joined_at REAL NOT NULL
            )
            """
        )


def save_message(channel_id, user_id, text, ts):
    """Record one observed channel message."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (channel_id, user_id, text, ts, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (channel_id, user_id, text, ts, time.time()),
        )


def get_messages_since(epoch_seconds):
    """Return all recorded messages newer than the given epoch, oldest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT channel_id, user_id, text, ts, created_at "
            "FROM messages WHERE created_at >= ? ORDER BY created_at ASC",
            (epoch_seconds,),
        ).fetchall()
    return [dict(r) for r in rows]


def record_member(user_id):
    """Remember a workspace join so we don't re-notify on Slack retries.

    Returns True if this is the first time we've seen this member (i.e. the
    POC should be notified), False if we've already recorded them.
    """
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO members (user_id, joined_at) VALUES (?, ?)",
            (user_id, time.time()),
        )
        return cur.rowcount > 0
