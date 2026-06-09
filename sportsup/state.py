"""SQLite-backed state store for dedup and durability across restarts.

Phase 1 establishes the store and the dedup primitive (``sent_alerts``). The alert
engine (Phase 3) and scheduler (Phase 5) build on this so that every alert fires
*exactly once* even if the process restarts mid-run.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_alerts (
    dedup_key   TEXT PRIMARY KEY,
    event_id    TEXT,
    alert_type  TEXT,
    sent_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fixtures (
    fixture_id   TEXT PRIMARY KEY,
    event_id     TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    kickoff_utc  TEXT,
    status       TEXT,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Multi-user (Phase 7): one row per Telegram user, plus their watched
-- (competition, season, team) tuples. Single-user installs simply have one
-- subscriber. `dedup_key` in sent_alerts is namespaced by chat_id so users
-- never share dedup state.
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id          TEXT PRIMARY KEY,
    timezone         TEXT NOT NULL DEFAULT 'UTC',
    quiet_enabled    INTEGER NOT NULL DEFAULT 1,
    quiet_start      TEXT NOT NULL DEFAULT '22:00',
    quiet_end        TEXT NOT NULL DEFAULT '07:00',
    quiet_behavior   TEXT NOT NULL DEFAULT 'defer',
    reminders_enabled INTEGER NOT NULL DEFAULT 1,
    upsets_enabled   INTEGER NOT NULL DEFAULT 1,
    finals_enabled   INTEGER NOT NULL DEFAULT 0,
    lead_times       TEXT NOT NULL DEFAULT '1d,1h',
    status           TEXT NOT NULL DEFAULT 'active',  -- active | paused
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    chat_id          TEXT NOT NULL,
    competition_code TEXT NOT NULL,
    season           INTEGER NOT NULL,
    team             TEXT NOT NULL,   -- canonical team name; '*' = all teams
    created_at       TEXT NOT NULL,
    PRIMARY KEY (chat_id, competition_code, season, team),
    FOREIGN KEY (chat_id) REFERENCES subscribers(chat_id) ON DELETE CASCADE
);
"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    """Thin wrapper around a SQLite database file."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        # The bot runs inbound handlers and the delivery loop concurrently (separate
        # connections to this file). WAL lets readers and the single writer coexist;
        # busy_timeout makes the rare writer-vs-writer overlap wait instead of erroring.
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        """The underlying connection, shared with sibling stores (e.g. SubscriberStore)
        so the whole app uses one DB file, one transaction scope, and FK cascades work."""
        return self._conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # --- dedup ------------------------------------------------------------

    def was_sent(self, dedup_key: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM sent_alerts WHERE dedup_key = ?", (dedup_key,)
        )
        return cur.fetchone() is not None

    def mark_sent(
        self, dedup_key: str, *, event_id: str | None = None, alert_type: str | None = None
    ) -> bool:
        """Record an alert as sent. Returns False if it was already recorded (idempotent)."""
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO sent_alerts (dedup_key, event_id, alert_type, sent_at) "
                "VALUES (?, ?, ?, ?)",
                (dedup_key, event_id, alert_type, _utcnow_iso()),
            )
            return cur.rowcount > 0

    def sent_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM sent_alerts").fetchone()[0]

    def recent_sent(self, limit: int = 20) -> list[sqlite3.Row]:
        """Most recently sent alerts, newest first — powers the `status` view."""
        cur = self._conn.execute(
            "SELECT dedup_key, event_id, alert_type, sent_at FROM sent_alerts "
            "ORDER BY sent_at DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()

    # --- generic meta key/value ------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def close(self) -> None:
        self._conn.close()
