"""Render an :class:`~sportsup.alerts.models.Alert` into clean WhatsApp text.

WhatsApp supports *bold* and _italic_. Times are shown in the user's configured
timezone. Kept separate from the engine so message wording can change without touching
alert logic, and so a different channel could reuse the alert with its own formatter.
"""

from __future__ import annotations

import re
from zoneinfo import ZoneInfo

from ..alerts.models import Alert, AlertType
from .base import OutboundMessage

_LEAD_UNITS = {"d": "day", "h": "hour", "m": "min"}


def _friendly_lead(label: str | None) -> str:
    if not label:
        return "reminder"
    m = re.match(r"(\d+)\s*([dhm])", label)
    if not m:
        return f"{label} reminder"
    n, unit = int(m.group(1)), _LEAD_UNITS[m.group(2)]
    plural = "s" if n != 1 and unit != "min" else ""
    return f"{n} {unit}{plural} to go"


def _kickoff(alert: Alert, tz: ZoneInfo) -> str:
    return alert.fixture.utc_kickoff.astimezone(tz).strftime("%a %d %b, %H:%M %Z")


def format_alert(alert: Alert, tz: ZoneInfo) -> str:
    fx = alert.fixture
    comp = alert.context.get("competition", fx.competition_code)
    hs = alert.context.get("home_score")
    as_ = alert.context.get("away_score")

    if alert.type is AlertType.FIXTURE_REMINDER:
        return (
            f"⚽ *{comp}* — upcoming\n"
            f"{fx.home.name} vs {fx.away.name}\n"
            f"🗓 {_kickoff(alert, tz)}\n"
            f"⏰ {_friendly_lead(alert.lead_label)}"
        )

    if alert.type is AlertType.FINAL_SCORE:
        return (
            f"📋 *{comp}* — Full time\n"
            f"{fx.home.name} {hs}–{as_} {fx.away.name}"
        )

    if alert.type is AlertType.SHOCK_RESULT:
        reason = alert.context.get("reason", "")
        return (
            f"🚨 *UPSET!* — {comp}\n"
            f"{fx.home.name} {hs}–{as_} {fx.away.name}\n"
            f"_{reason}_"
        )

    return alert.summary  # fallback


def message_for_alert(alert: Alert, config, recipient: str) -> OutboundMessage:
    """Build the OutboundMessage for an alert, honoring the delivery config:

    - if ``delivery.alert_template_name`` is set → send as that approved template with the
      message as a single-line body parameter (delivers any time, in or out of the 24h
      window). WhatsApp body params can't contain newlines, so the text is flattened.
    - otherwise → free-form text (delivers only inside the 24h window).
    """
    text = format_alert(alert, config.tzinfo)
    tmpl = config.delivery.alert_template_name
    if tmpl:
        one_line = " · ".join(p for p in text.splitlines() if p.strip())
        return OutboundMessage(
            recipient=recipient,
            template_name=tmpl,
            template_lang=config.delivery.alert_template_lang,
            template_components=[{"type": "body", "parameters": [{"type": "text", "text": one_line}]}],
            dedup_key=alert.dedup_key,
        )
    return OutboundMessage(recipient=recipient, text=text, dedup_key=alert.dedup_key)
