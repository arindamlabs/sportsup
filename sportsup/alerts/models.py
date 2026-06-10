"""Alert domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..providers import Fixture


class AlertType(str, Enum):
    FIXTURE_REMINDER = "fixture_reminder"
    FINAL_SCORE = "final_score"
    SHOCK_RESULT = "shock_result"


@dataclass
class Alert:
    """A single alert ready to be (de-duplicated, formatted, and) sent.

    `summary` is a plain one-liner for logging / dry-run; `context` carries the
    structured fields the formatter uses to render the message. `dedup_key` is the
    exactly-once key checked against the state store.
    """

    type: AlertType
    event_id: str
    dedup_key: str
    fixture: Fixture
    summary: str
    scheduled_for: datetime | None = None  # UTC; for reminders. None = send as soon as seen.
    lead_label: str | None = None          # e.g. "1d", "1h" for reminders
    context: dict = field(default_factory=dict)
