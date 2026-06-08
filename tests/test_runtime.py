"""Phase 5 tests: quiet-hours windowing, reminder/result classification, run_once dedup."""

from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

import httpx

from sportsup.alerts.models import Alert, AlertType
from sportsup.config import AppConfig
from sportsup.delivery.base import SendResult, WhatsAppSender
from sportsup.providers import Fixture, MatchStatus, TeamRef
from sportsup.providers.football_data import FootballDataProvider
from sportsup.providers.http import HttpClient
from sportsup.providers.router import ProviderRouter
from sportsup.runtime import SchedulerRuntime, classify_reminder, classify_result, in_quiet_hours
from sportsup.state import StateStore

UTC = timezone.utc


# --- quiet hours -----------------------------------------------------------

def test_in_quiet_hours_overnight_window():
    qh = SimpleNamespace(enabled=True, start=time(22, 0), end=time(7, 0))
    assert in_quiet_hours(time(23, 0), qh)      # late evening
    assert in_quiet_hours(time(2, 0), qh)       # small hours
    assert not in_quiet_hours(time(12, 0), qh)  # midday


def test_in_quiet_hours_same_day_and_disabled():
    qh = SimpleNamespace(enabled=True, start=time(1, 0), end=time(6, 0))
    assert in_quiet_hours(time(3, 0), qh)
    assert not in_quiet_hours(time(8, 0), qh)
    assert not in_quiet_hours(time(3, 0), SimpleNamespace(enabled=False, start=time(1), end=time(6)))


# --- classification --------------------------------------------------------

def _cfg(**kw) -> AppConfig:
    body = {
        "reminders": {"lead_times": ["1h"]},
        "events": [{"id": "wc", "name": "WC", "competition_code": "WC", "season": 2026,
                    "teams": ["Brazil"]}],
    }
    body.update(kw)
    return AppConfig.model_validate(body)


def _reminder(kickoff, scheduled_for) -> Alert:
    fx = Fixture(provider="t", provider_fixture_id="1", competition_code="WC", season=2026,
                 utc_kickoff=kickoff, status=MatchStatus.TIMED,
                 home=TeamRef(name="Brazil"), away=TeamRef(name="Serbia"))
    return Alert(AlertType.FIXTURE_REMINDER, "wc", "k", fx, summary="",
                 scheduled_for=scheduled_for, lead_label="1h")


def test_classify_reminder_stale_wait_send():
    cfg = _cfg(quiet_hours={"enabled": False})
    now = datetime(2026, 6, 13, 19, 0, tzinfo=UTC)
    # stale: match already kicked off
    assert classify_reminder(_reminder(now - timedelta(hours=1), now - timedelta(hours=2)), now, cfg) == "drop"
    # not due yet
    assert classify_reminder(_reminder(now + timedelta(hours=3), now + timedelta(hours=1)), now, cfg) == "wait"
    # due and outside quiet hours
    assert classify_reminder(_reminder(now + timedelta(hours=2), now - timedelta(minutes=5)), now, cfg) == "send"


def test_classify_reminder_quiet_defer_vs_suppress():
    now = datetime(2026, 6, 13, 19, 0, tzinfo=UTC)
    due = _reminder(now + timedelta(hours=2), now - timedelta(minutes=5))
    defer_cfg = _cfg(quiet_hours={"enabled": True, "start": "00:00", "end": "23:59", "behavior": "defer"})
    assert classify_reminder(due, now, defer_cfg) == "defer"
    suppress_cfg = _cfg(quiet_hours={"enabled": True, "start": "00:00", "end": "23:59", "behavior": "suppress"})
    assert classify_reminder(due, now, suppress_cfg) == "drop"


def test_classify_result_respects_quiet_hours():
    now = datetime(2026, 6, 13, 19, 0, tzinfo=UTC)
    assert classify_result(now, _cfg(quiet_hours={"enabled": False})) == "send"
    assert classify_result(now, _cfg(quiet_hours={"enabled": True, "start": "00:00", "end": "23:59",
                                                  "behavior": "defer"})) == "defer"


# --- run_once integration + dedup -----------------------------------------

class _FakeSender(WhatsAppSender):
    name = "fake"

    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return SendResult(ok=True, provider="fake", provider_message_id="x")


def test_run_once_sends_due_reminder_then_dedups(tmp_path):
    now = datetime.now(UTC)
    kickoff = (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    matches = {"matches": [{
        "id": 1, "utcDate": kickoff, "status": "TIMED",
        "homeTeam": {"id": 1, "name": "Brazil", "tla": "BRA"},
        "awayTeam": {"id": 2, "name": "Serbia", "tla": "SRB"},
        "score": {"winner": None, "fullTime": {"home": None, "away": None}},
    }]}
    teams = {"teams": [{"id": 1, "name": "Brazil", "tla": "BRA"},
                       {"id": 2, "name": "Serbia", "tla": "SRB"}]}

    def handler(req):
        if req.url.path.endswith("/teams"):
            return httpx.Response(200, json=teams)
        return httpx.Response(200, json=matches)

    client = HttpClient("https://x.test", transport=httpx.MockTransport(handler), sleep=lambda *a: None)
    router = ProviderRouter([FootballDataProvider("k", client=client)])
    cfg = _cfg(quiet_hours={"enabled": False}, fixture_sync_lookahead_days=10)
    store = StateStore(tmp_path / "s.sqlite")
    sender = _FakeSender()
    rt = SchedulerRuntime(cfg, router, sender, store, "+15550100")

    rt.run_once()
    assert len(sender.sent) == 1                      # the 1h reminder (due, match in 30m) was delivered
    assert "Brazil vs Serbia" in sender.sent[0].text

    rt.run_once()
    assert len(sender.sent) == 1                      # deduped — not sent again
    store.close()
