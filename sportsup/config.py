"""Typed, validated configuration schema for SportsUp.

Everything the user is meant to tune lives here and is loaded from ``config.yaml``:
events (on/off), per-event team watchlists, per-event alert toggles, reminder
lead-times, timezone, quiet hours, and shock-detection sensitivity. Adding a
competition or team is a config edit — no code change.

Secrets (API keys, WhatsApp tokens) deliberately do NOT live here; they come from
the environment via :mod:`sportsup.settings`.
"""

from __future__ import annotations

import re
from datetime import time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# --- helpers ---------------------------------------------------------------

_LEAD_RE = re.compile(r"^\s*(\d+)\s*([dhm])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60}


def parse_lead_time(value: str) -> timedelta:
    """Parse a lead-time like '1d', '2h', '30m' into a timedelta."""
    match = _LEAD_RE.match(str(value))
    if not match:
        raise ValueError(
            f"invalid lead-time {value!r}; use forms like '1d', '2h', '30m'"
        )
    amount, unit = int(match.group(1)), match.group(2).lower()
    return timedelta(seconds=amount * _UNIT_SECONDS[unit])


def _parse_hhmm(value: str) -> time:
    try:
        hh, mm = str(value).split(":")
        return time(hour=int(hh), minute=int(mm))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"invalid time {value!r}; expected 'HH:MM'") from exc


# --- models ----------------------------------------------------------------


class AlertToggles(BaseModel):
    """Per-event switches for each alert type."""

    model_config = {"extra": "forbid"}

    upcoming_fixtures: bool = True
    shock_result: bool = True
    final_scores: bool = False  # per spec: configurable, default OFF


class EventConfig(BaseModel):
    """A single competition the tool can track."""

    model_config = {"extra": "forbid"}

    id: str = Field(..., description="stable slug, e.g. 'world-cup-2026'")
    name: str
    enabled: bool = True
    # Provider lookup keys (resolved/validated in Phase 2 against live data).
    competition_code: str = Field(
        ..., description="football-data.org competition code, e.g. 'WC', 'PL'"
    )
    api_football_league: int | None = Field(
        None, description="API-Football league id (for odds), e.g. 1=WC, 39=EPL"
    )
    season: int = Field(..., description="season year, e.g. 2026")
    teams: list[str] = Field(default_factory=list, description="watchlist; empty = all")
    alerts: AlertToggles = AlertToggles()

    @field_validator("teams")
    @classmethod
    def _dedupe_teams(cls, teams: list[str]) -> list[str]:
        seen, out = set(), []
        for t in teams:
            key = t.strip().casefold()
            if key and key not in seen:
                seen.add(key)
                out.append(t.strip())
        return out


class QuietHoursConfig(BaseModel):
    model_config = {"extra": "forbid"}

    enabled: bool = True
    start: time = Field(default=time(22, 0))
    end: time = Field(default=time(7, 0))
    # What to do with an alert that lands inside quiet hours.
    behavior: str = Field("defer", pattern="^(defer|suppress)$")

    @field_validator("start", "end", mode="before")
    @classmethod
    def _coerce_time(cls, v: object) -> object:
        return _parse_hhmm(v) if isinstance(v, str) else v


class ReminderConfig(BaseModel):
    model_config = {"extra": "forbid"}

    # e.g. ['1d', '1h'] -> a day-before reminder and a kickoff-soon reminder.
    lead_times: list[str] = Field(default_factory=lambda: ["1d", "1h"])

    @field_validator("lead_times")
    @classmethod
    def _validate_lead_times(cls, values: list[str]) -> list[str]:
        for v in values:
            parse_lead_time(v)  # raises on bad input
        return values

    @property
    def lead_deltas(self) -> list[timedelta]:
        return sorted((parse_lead_time(v) for v in self.lead_times), reverse=True)


class ShockDetectionConfig(BaseModel):
    """Tunable knobs for the upset heuristic (designed in Phase 3)."""

    model_config = {"extra": "forbid"}

    # Odds path: fire when the winner's pre-match implied loss-probability >= sensitivity
    # (0.65 ~ winner had <=35% implied chance). Higher = only flag bigger shocks.
    sensitivity: float = Field(0.65, ge=0.0, le=1.0)
    # Standings fallback: min league-table position gap for an upset.
    min_position_gap: int = Field(8, ge=1)
    # Recent matches considered for the form-differential tiebreaker.
    form_window: int = Field(5, ge=1)
    # Order in which signals are tried when available.
    signal_priority: list[str] = Field(default_factory=lambda: ["odds", "standings", "form"])

    @field_validator("signal_priority")
    @classmethod
    def _validate_signals(cls, values: list[str]) -> list[str]:
        allowed = {"odds", "standings", "form"}
        bad = [v for v in values if v not in allowed]
        if bad:
            raise ValueError(f"unknown shock signals {bad}; allowed: {sorted(allowed)}")
        return values


class DeliveryConfig(BaseModel):
    model_config = {"extra": "forbid"}

    provider: str = Field("meta_cloud", pattern="^(meta_cloud|twilio|console)$")
    # Safety: when true, format + log messages instead of sending. Env can override.
    dry_run: bool = True
    # Optional approved utility template so alerts deliver OUTSIDE the 24h window.
    # When set, alerts are sent as this template with the message as a single body
    # parameter {{1}} (recommended template body: "SportsUp ⚽\n{{1}}"). When null,
    # alerts are sent as free-form text (delivers only inside the 24h window).
    alert_template_name: str | None = None
    alert_template_lang: str = "en_US"


class SchedulingConfig(BaseModel):
    """Cadences for the always-on runtime (Phase 5), tuned to respect API rate limits:
    fixtures change rarely (sync a couple times a day) while reminders need a tight check."""

    model_config = {"extra": "forbid"}

    fixture_sync_hours: int = Field(12, ge=1, le=168)      # re-pull fixtures this often
    reminder_check_minutes: int = Field(5, ge=1, le=180)   # how often to fire due reminders
    result_poll_minutes: int = Field(15, ge=1, le=360)     # how often to scan for results
    result_lookback_days: int = Field(2, ge=1, le=30)      # how far back to scan for finished matches


class AppConfig(BaseModel):
    """Root configuration object."""

    model_config = {"extra": "forbid"}

    timezone: str = "America/Los_Angeles"
    fixture_sync_lookahead_days: int = Field(10, ge=1, le=60)
    quiet_hours: QuietHoursConfig = QuietHoursConfig()
    reminders: ReminderConfig = ReminderConfig()
    shock_detection: ShockDetectionConfig = ShockDetectionConfig()
    delivery: DeliveryConfig = DeliveryConfig()
    scheduling: SchedulingConfig = SchedulingConfig()
    events: list[EventConfig] = Field(default_factory=list)

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, tz: str) -> str:
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"unknown timezone {tz!r}; use an IANA name like 'America/Los_Angeles'"
            ) from exc
        return tz

    @model_validator(mode="after")
    def _validate_event_ids(self) -> "AppConfig":
        ids = [e.id for e in self.events]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate event id(s): {sorted(dupes)}")
        return self

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def enabled_events(self) -> list[EventConfig]:
        return [e for e in self.events if e.enabled]


# --- loading ---------------------------------------------------------------


def load_config(path: str | Path) -> AppConfig:
    """Load and validate config from a YAML file, raising a clear error on problems."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"config file not found at {p}. Copy config.example.yaml to config.yaml."
        )
    raw = yaml.safe_load(p.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"config at {p} must be a YAML mapping, got {type(raw).__name__}")
    return AppConfig.model_validate(raw)
