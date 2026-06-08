"""Phase 6 tests: schedule-change resilience, template delivery, status query."""

from datetime import datetime, timedelta, timezone

from sportsup.alerts import AlertEngine
from sportsup.alerts.models import Alert, AlertType
from sportsup.config import AppConfig
from sportsup.delivery import message_for_alert
from sportsup.providers import Fixture, MatchStatus, TeamRef
from sportsup.state import StateStore

UTC = timezone.utc


def _cfg(**delivery) -> AppConfig:
    return AppConfig.model_validate({
        "delivery": delivery or {},
        "events": [{"id": "wc", "name": "WC", "competition_code": "WC", "season": 2026,
                    "teams": ["Brazil"]}],
    })


def _fx(status, kickoff):
    return Fixture(provider="t", provider_fixture_id="1", competition_code="WC", season=2026,
                   utc_kickoff=kickoff, status=status,
                   home=TeamRef(name="Brazil"), away=TeamRef(name="Serbia"))


def test_postponed_or_cancelled_fixture_gets_no_reminder(tmp_path):
    cfg = _cfg()
    engine = AlertEngine(cfg, StateStore(tmp_path / "s.sqlite"))
    now = datetime(2026, 6, 13, 12, tzinfo=UTC)
    future = now + timedelta(days=2)
    ev = cfg.events[0]

    assert engine.plan_reminders(ev, [_fx(MatchStatus.POSTPONED, future)], now=now) == []
    assert engine.plan_reminders(ev, [_fx(MatchStatus.CANCELLED, future)], now=now) == []
    # A normal upcoming match still produces reminders.
    assert engine.plan_reminders(ev, [_fx(MatchStatus.TIMED, future)], now=now)


def _alert():
    fx = _fx(MatchStatus.FINISHED, datetime(2026, 6, 13, 22, tzinfo=UTC))
    return Alert(AlertType.SHOCK_RESULT, "wc", "wc:k:shock", fx, summary="",
                 context={"competition": "WC", "home_score": 0, "away_score": 1,
                          "reason": "big upset"})


def test_message_for_alert_text_by_default():
    msg = message_for_alert(_alert(), _cfg(), "+1555")
    assert msg.text and msg.template_name is None


def test_message_for_alert_uses_template_when_configured():
    cfg = _cfg(alert_template_name="sportsup_alert", alert_template_lang="en_US")
    msg = message_for_alert(_alert(), cfg, "+1555")
    assert msg.template_name == "sportsup_alert"
    param = msg.template_components[0]["parameters"][0]["text"]
    assert "\n" not in param          # body params must be single-line
    assert "WC" in param and "big upset" in param


def test_recent_sent_returns_marked_within_limit(tmp_path):
    store = StateStore(tmp_path / "s.sqlite")
    for i in range(5):
        store.mark_sent(f"k{i}", event_id="wc", alert_type="reminder")
    rows = store.recent_sent(limit=3)
    assert len(rows) == 3
    assert all(r["event_id"] == "wc" for r in rows)
    assert store.sent_count() == 5
    store.close()
