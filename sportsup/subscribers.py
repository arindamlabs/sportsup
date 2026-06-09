"""Subscribers and their subscriptions — the multi-user data layer (Phase 7).

A :class:`Subscriber` is one Telegram user with their own preferences (timezone,
quiet hours, which alert types they want, reminder lead-times, paused/active). A
:class:`Subscription` is one watched (competition, season, team) tuple. Several users
can watch the same competition — the fan-out planner fetches it once and dedups
per-user, so adding users never adds API calls.

:class:`SubscriberStore` shares the :class:`~sportsup.state.StateStore` connection
(one SQLite file), so a ``/stop`` that deletes a subscriber cascades to their
subscriptions, and everything stays in one transactional scope.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import (
    AlertToggles,
    AppConfig,
    EventConfig,
    QuietHoursConfig,
    ReminderConfig,
)
from .state import StateStore

ALL_TEAMS = "*"  # sentinel team value meaning "every team in the competition"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_leads(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


@dataclass
class Subscriber:
    """One user and their delivery preferences."""

    chat_id: str
    timezone: str = "UTC"
    quiet_enabled: bool = True
    quiet_start: str = "22:00"
    quiet_end: str = "07:00"
    quiet_behavior: str = "defer"
    reminders_enabled: bool = True
    upsets_enabled: bool = True
    finals_enabled: bool = False
    lead_times: list[str] = field(default_factory=lambda: ["1d", "1h"])
    status: str = "active"  # active | paused
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def toggles(self) -> AlertToggles:
        return AlertToggles(
            upcoming_fixtures=self.reminders_enabled,
            shock_result=self.upsets_enabled,
            final_scores=self.finals_enabled,
        )

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "Subscriber":
        return cls(
            chat_id=row["chat_id"],
            timezone=row["timezone"],
            quiet_enabled=bool(row["quiet_enabled"]),
            quiet_start=row["quiet_start"],
            quiet_end=row["quiet_end"],
            quiet_behavior=row["quiet_behavior"],
            reminders_enabled=bool(row["reminders_enabled"]),
            upsets_enabled=bool(row["upsets_enabled"]),
            finals_enabled=bool(row["finals_enabled"]),
            lead_times=_split_leads(row["lead_times"]),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class Subscription:
    chat_id: str
    competition_code: str
    season: int
    team: str  # canonical team name, or ALL_TEAMS

    @property
    def all_teams(self) -> bool:
        return self.team == ALL_TEAMS


class SubscriberStore:
    """CRUD over the ``subscribers`` and ``subscriptions`` tables.

    Shares the StateStore connection. Methods use ``with self.conn`` (sqlite3's
    transaction context) so each write commits atomically or rolls back.
    """

    def __init__(self, state: StateStore) -> None:
        self.conn = state.conn

    # --- subscribers ------------------------------------------------------

    def upsert_subscriber(self, sub: Subscriber) -> Subscriber:
        """Insert a new subscriber or update an existing one's preferences.

        ``created_at`` is preserved on update; ``updated_at`` is always refreshed."""
        now = _utcnow_iso()
        leads = ",".join(sub.lead_times)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO subscribers (
                    chat_id, timezone, quiet_enabled, quiet_start, quiet_end,
                    quiet_behavior, reminders_enabled, upsets_enabled, finals_enabled,
                    lead_times, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    timezone=excluded.timezone,
                    quiet_enabled=excluded.quiet_enabled,
                    quiet_start=excluded.quiet_start,
                    quiet_end=excluded.quiet_end,
                    quiet_behavior=excluded.quiet_behavior,
                    reminders_enabled=excluded.reminders_enabled,
                    upsets_enabled=excluded.upsets_enabled,
                    finals_enabled=excluded.finals_enabled,
                    lead_times=excluded.lead_times,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (
                    sub.chat_id, sub.timezone, int(sub.quiet_enabled), sub.quiet_start,
                    sub.quiet_end, sub.quiet_behavior, int(sub.reminders_enabled),
                    int(sub.upsets_enabled), int(sub.finals_enabled), leads, sub.status,
                    sub.created_at or now, now,
                ),
            )
        return self.get_subscriber(sub.chat_id)  # type: ignore[return-value]

    def get_subscriber(self, chat_id: str) -> Subscriber | None:
        row = self.conn.execute(
            "SELECT * FROM subscribers WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return Subscriber._from_row(row) if row else None

    def list_subscribers(self, *, status: str | None = None) -> list[Subscriber]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM subscribers WHERE status = ? ORDER BY created_at", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM subscribers ORDER BY created_at"
            ).fetchall()
        return [Subscriber._from_row(r) for r in rows]

    def set_status(self, chat_id: str, status: str) -> None:
        """Pause or resume a subscriber (``active`` | ``paused``)."""
        with self.conn:
            self.conn.execute(
                "UPDATE subscribers SET status = ?, updated_at = ? WHERE chat_id = ?",
                (status, _utcnow_iso(), chat_id),
            )

    def delete_subscriber(self, chat_id: str) -> bool:
        """Full unsubscribe: remove the user and (via FK cascade) all their
        subscriptions. Returns False if there was nothing to delete.

        NOTE: their entries in ``sent_alerts`` are intentionally left in place — they
        are harmless dedup tombstones keyed by chat_id and prevent any in-flight
        re-send; a brand-new subscriber with the same chat_id starts clean anyway."""
        with self.conn:
            cur = self.conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))
            return cur.rowcount > 0

    # --- subscriptions ----------------------------------------------------

    def add_subscription(self, chat_id: str, competition_code: str, season: int, team: str) -> bool:
        """Watch a team (or ALL_TEAMS) in a competition. Idempotent — returns False
        if the exact subscription already existed."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO subscriptions "
                "(chat_id, competition_code, season, team, created_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, competition_code.upper(), season, team, _utcnow_iso()),
            )
            return cur.rowcount > 0

    def remove_subscription(
        self, chat_id: str, competition_code: str, season: int, team: str | None = None
    ) -> int:
        """Granular unsubscribe. With ``team`` set, removes that one team; with
        ``team=None`` removes the whole competition. Returns rows removed."""
        code = competition_code.upper()
        with self.conn:
            if team is None:
                cur = self.conn.execute(
                    "DELETE FROM subscriptions WHERE chat_id=? AND competition_code=? AND season=?",
                    (chat_id, code, season),
                )
            else:
                cur = self.conn.execute(
                    "DELETE FROM subscriptions "
                    "WHERE chat_id=? AND competition_code=? AND season=? AND team=?",
                    (chat_id, code, season, team),
                )
            return cur.rowcount

    def list_subscriptions(self, chat_id: str) -> list[Subscription]:
        rows = self.conn.execute(
            "SELECT chat_id, competition_code, season, team FROM subscriptions "
            "WHERE chat_id = ? ORDER BY competition_code, team",
            (chat_id,),
        ).fetchall()
        return [
            Subscription(r["chat_id"], r["competition_code"], r["season"], r["team"])
            for r in rows
        ]

    def subscription_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]

    def competition_popularity(self) -> list[tuple[str, int]]:
        """(competition_code, distinct-subscriber count), most-followed first."""
        rows = self.conn.execute(
            "SELECT competition_code, COUNT(DISTINCT chat_id) AS n FROM subscriptions "
            "GROUP BY competition_code ORDER BY n DESC, competition_code"
        ).fetchall()
        return [(r["competition_code"], r["n"]) for r in rows]

    def team_popularity(self, limit: int = 20) -> list[tuple[str, int]]:
        """(team, distinct-subscriber count) excluding the ALL_TEAMS sentinel."""
        rows = self.conn.execute(
            "SELECT team, COUNT(DISTINCT chat_id) AS n FROM subscriptions "
            "WHERE team != ? GROUP BY team ORDER BY n DESC, team LIMIT ?",
            (ALL_TEAMS, limit),
        ).fetchall()
        return [(r["team"], r["n"]) for r in rows]

    def active_competitions(self) -> set[tuple[str, int]]:
        """Distinct (competition_code, season) watched by any ACTIVE subscriber —
        the set the fan-out planner fetches once each. Paused users are excluded."""
        rows = self.conn.execute(
            "SELECT DISTINCT s.competition_code, s.season FROM subscriptions s "
            "JOIN subscribers u ON u.chat_id = s.chat_id WHERE u.status = 'active'"
        ).fetchall()
        return {(r["competition_code"], r["season"]) for r in rows}


# --- bridging a subscriber to the existing engine --------------------------


def watchlist_for(
    subs: list[Subscription], competition_code: str, season: int
) -> list[str]:
    """The team watchlist for one (competition, season) from a user's subscriptions.

    Returns ``[]`` (meaning "all teams", per the engine's convention) if the user
    subscribed to ALL_TEAMS for it; otherwise the explicit team names."""
    teams: list[str] = []
    for s in subs:
        if s.competition_code == competition_code and s.season == season:
            if s.all_teams:
                return []
            teams.append(s.team)
    return teams


def subscriber_from_config(chat_id: str, config: AppConfig) -> Subscriber:
    """Map a single-user AppConfig onto a Subscriber row. Per-event alert toggles
    collapse to per-user toggles by OR-ing across the enabled events (a single user's
    two events in the live config share the same toggles, so this is lossless there)."""
    enabled = config.enabled_events
    qh = config.quiet_hours
    return Subscriber(
        chat_id=chat_id,
        timezone=config.timezone,
        quiet_enabled=qh.enabled,
        quiet_start=qh.start.strftime("%H:%M"),
        quiet_end=qh.end.strftime("%H:%M"),
        quiet_behavior=qh.behavior,
        reminders_enabled=any(e.alerts.upcoming_fixtures for e in enabled),
        upsets_enabled=any(e.alerts.shock_result for e in enabled),
        finals_enabled=any(e.alerts.final_scores for e in enabled),
        lead_times=list(config.reminders.lead_times),
    )


def import_single_user(store: "SubscriberStore", config: AppConfig, chat_id: str) -> tuple[Subscriber, int]:
    """Migrate a single-user config.yaml into the DB as one subscriber + their
    subscriptions. Idempotent: re-running updates the subscriber and re-adds any
    missing subscriptions without duplicating. Returns (subscriber, subscriptions_added)."""
    sub = store.upsert_subscriber(subscriber_from_config(chat_id, config))
    added = 0
    for ev in config.enabled_events:
        teams = ev.teams or [ALL_TEAMS]
        for team in teams:
            if store.add_subscription(chat_id, ev.competition_code, ev.season, team):
                added += 1
    return sub, added


def effective_config(sub: Subscriber, base: AppConfig, events: list[EventConfig]) -> AppConfig:
    """A per-user AppConfig: the user's timezone/quiet-hours/lead-times/toggles applied
    on top of shared settings (shock sensitivity, scheduling), with ``events`` set to
    just this user's subscriptions. Lets the existing AlertEngine run unchanged per user.
    """
    quiet = QuietHoursConfig.model_validate(
        {
            "enabled": sub.quiet_enabled,
            "start": sub.quiet_start,
            "end": sub.quiet_end,
            "behavior": sub.quiet_behavior,
        }
    )
    reminders = ReminderConfig(lead_times=sub.lead_times or ["1d", "1h"])
    return base.model_copy(
        update={
            "timezone": sub.timezone,
            "quiet_hours": quiet,
            "reminders": reminders,
            "events": events,
        }
    )
