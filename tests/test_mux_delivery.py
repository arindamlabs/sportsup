"""Tests for the multi-user delivery cycle: per-user routing, dedup, quiet hours, dry-run."""

from collections import Counter
from datetime import datetime, timedelta, timezone

from sportsup.config import AppConfig
from sportsup.delivery.base import SendResult, WhatsAppSender
from sportsup.delivery.console import ConsoleSender
from sportsup.mux_delivery import run_delivery_cycle
from sportsup.providers import Fixture, MatchStatus, TeamRef
from sportsup.state import StateStore
from sportsup.subscribers import Subscriber, SubscriberStore

UTC = timezone.utc
NOW = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)

# provider=console so message_for_alert renders plain text (no WhatsApp template).
BASE = AppConfig.model_validate({
    "timezone": "UTC", "reminders": {"lead_times": ["1h"]},
    "delivery": {"provider": "console"},
    "quiet_hours": {"enabled": False},
})


def _due_fixture() -> Fixture:
    # Kickoff in 30m; with a 1h lead the reminder window has already opened -> due now.
    return Fixture(provider="fake", provider_fixture_id="1", competition_code="WC", season=2026,
                   utc_kickoff=NOW + timedelta(minutes=30), status=MatchStatus.TIMED,
                   home=TeamRef(name="Brazil", tla="BRA"), away=TeamRef(name="Serbia", tla="SRB"))


class FakeRouter:
    def __init__(self, fixtures):
        self._fixtures = fixtures

    def get_fixtures(self, *, competition_code, season, date_from, date_to):
        return list(self._fixtures.get((competition_code, season), []))

    def get_results(self, *, competition_code, season, date_from, date_to):
        return []

    def get_standings(self, *, competition_code, season):
        return []

    def get_match_odds(self, **kw):
        return None


class FakeSender(WhatsAppSender):
    name = "fake"

    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return SendResult(ok=True, provider="fake", provider_message_id="1")


def _setup(tmp_path):
    store = StateStore(tmp_path / "s.sqlite")
    subs = SubscriberStore(store)
    return store, subs


def _two_subscribers(subs, **kw):
    for cid in ("u1", "u2"):
        subs.upsert_subscriber(Subscriber(chat_id=cid, timezone="UTC", lead_times=["1h"], **kw))
        subs.add_subscription(cid, "WC", 2026, "Brazil")


def test_delivers_to_each_subscriber_own_chat(tmp_path):
    store, subs = _setup(tmp_path)
    _two_subscribers(subs)
    router = FakeRouter({("WC", 2026): [_due_fixture()]})
    sender = FakeSender()

    stats = run_delivery_cycle(BASE, router, store, subs, sender, now=NOW)
    assert stats.subscribers == 2 and stats.sent == 2
    # Each message routed to that subscriber's own chat_id.
    assert {m.recipient for m in sender.sent} == {"u1", "u2"}
    for m in sender.sent:
        assert "Brazil vs Serbia" in m.text


def test_dedup_marks_then_skips(tmp_path):
    store, subs = _setup(tmp_path)
    _two_subscribers(subs)
    router = FakeRouter({("WC", 2026): [_due_fixture()]})
    sender = FakeSender()

    run_delivery_cycle(BASE, router, store, subs, sender, now=NOW)
    assert len(sender.sent) == 2
    stats2 = run_delivery_cycle(BASE, router, store, subs, sender, now=NOW)
    assert stats2.sent == 0 and len(sender.sent) == 2   # deduped per-user


def test_quiet_hours_defers_without_marking(tmp_path):
    store, subs = _setup(tmp_path)
    # All-day quiet window -> reminders defer, are NOT marked, and retry later.
    _two_subscribers(subs, quiet_enabled=True, quiet_start="00:00", quiet_end="23:59",
                     quiet_behavior="defer")
    router = FakeRouter({("WC", 2026): [_due_fixture()]})
    sender = FakeSender()

    stats = run_delivery_cycle(BASE, router, store, subs, sender, now=NOW)
    assert stats.sent == 0 and stats.deferred == 2 and len(sender.sent) == 0
    # Not marked: once quiet hours lift, the same reminder can still go out.
    assert not store.was_sent("u1:wc-2026:" + _due_fixture().stable_id() + ":reminder:1h")


def test_dry_run_console_does_not_mark(tmp_path):
    store, subs = _setup(tmp_path)
    _two_subscribers(subs)
    router = FakeRouter({("WC", 2026): [_due_fixture()]})
    console = ConsoleSender()

    run_delivery_cycle(BASE, router, store, subs, console, now=NOW)
    # Console "sends" but nothing is marked, so a second cycle would send again.
    stats2 = run_delivery_cycle(BASE, router, store, subs, console, now=NOW)
    assert stats2.sent == 2


def test_paused_subscriber_gets_nothing(tmp_path):
    store, subs = _setup(tmp_path)
    subs.upsert_subscriber(Subscriber(chat_id="u1", status="paused", lead_times=["1h"]))
    subs.add_subscription("u1", "WC", 2026, "Brazil")
    sender = FakeSender()
    stats = run_delivery_cycle(BASE, FakeRouter({("WC", 2026): [_due_fixture()]}),
                               store, subs, sender, now=NOW)
    assert stats.subscribers == 0 and stats.sent == 0
