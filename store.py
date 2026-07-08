"""Tiny SQLite store — records who joined, for the daily report.

The report fetches message history live from Slack (see collect.py), so no
messages are stored. The only thing we keep is each member's join time, so the
daily report can list who joined in the last window.

On an ephemeral host (e.g. Render free tier) this file is wiped on redeploy.
Worst case after a wipe: the "new members" section misses recent joins. Point
DB_PATH at a persistent volume to avoid that.
"""

import os
import sqlite3
import time

DB_PATH = os.environ.get("DB_PATH", "bot_data.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the members table if it doesn't exist. Safe on every startup."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS members (
                user_id   TEXT PRIMARY KEY,
                joined_at REAL NOT NULL
            )
            """
        )


def record_member(user_id):
    """Record a member's join time the first time we see them.

    Returns True the first time (a genuinely new member) and False if we've
    already recorded them (e.g. a Slack retry).
    """
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO members (user_id, joined_at) VALUES (?, ?)",
            (user_id, time.time()),
        )
        return cur.rowcount > 0


def get_members_since(epoch_seconds):
    """Return user_ids of members recorded as joining since the given epoch."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id FROM members WHERE joined_at >= ? ORDER BY joined_at ASC",
            (epoch_seconds,),
        ).fetchall()
    return [r["user_id"] for r in rows]
