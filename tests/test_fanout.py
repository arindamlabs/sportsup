"""Phase 7 tests: per-competition fetch fan-out, per-user dedup, odds caching."""

from collections import Counter
from datetime import datetime, timedelta, timezone

from sportsup.config import AppConfig
from sportsup.fanout import plan_for_all_subscribers
from sportsup.providers import Fixture, MatchResult, MatchStatus, Score, Standing, TeamRef
from sportsup.state import StateStore
from sportsup.subscribers import Subscriber, SubscriberStore

UTC = timezone.utc
NOW = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)

BASE_CONFIG = AppConfig.model_validate({
    "timezone": "UTC",
    "reminders": {"lead_times": ["1d"]},
    "delivery": {"provider": "console"},
})


def _fixture(code, home, away, *, when, status=MatchStatus.TIMED) -> Fixture:
    return Fixture(provider="fake", provider_fixture_id=f"{home}{away}", competition_code=code,
                   season=2026, utc_kickoff=when, status=status,
                   home=TeamRef(name=home, tla=home[:3].upper()),
                   away=TeamRef(name=away, tla=away[:3].upper()))


def _result(code, home, away, hs, as_, *, when) -> MatchResult:
    fx = _fixture(code, home, away, when=when, status=MatchStatus.FINISHED)
    return MatchResult.from_fixture(fx, Score(home=hs, away=as_))


class FakeRouter:
    """Counts every call so tests can assert each competition is fetched once."""

    def __init__(self, fixtures=None, results=None, standings=None):
        self.fixtures = fixtures or {}
        self.results = results or {}
        self.standings = standings or {}
        self.calls: Counter = Counter()
        self.odds_calls: Counter = Counter()

    def get_fixtures(self, *, competition_code, season, date_from, date_to):
        self.calls[("fixtures", competition_code, season)] += 1
        return list(self.fixtures.get((competition_code, season), []))

    def get_results(self, *, competition_code, season, date_from, date_to):
        self.calls[("results", competition_code, season)] += 1
        return list(self.results.get((competition_code, season), []))

    def get_standings(self, *, competition_code, season):
        self.calls[("standings", competition_code, season)] += 1
        return list(self.standings.get((competition_code, season), []))

    def get_match_odds(self, *, competition_code, season, home_team, away_team, kickoff):
        self.odds_calls[(competition_code, home_team, away_team)] += 1
        return None


def _setup(tmp_path):
    store = StateStore(tmp_path / "s.sqlite")
    return store, SubscriberStore(store)


def test_shared_competition_fetched_once(tmp_path):
    store, subs = _setup(tmp_path)
    # Two users both watch Brazil in the WC.
    for cid in ("u1", "u2"):
        subs.upsert_subscriber(Subscriber(chat_id=cid, timezone="UTC", lead_times=["1d"]))
        subs.add_subscription(cid, "WC", 2026, "Brazil")

    router = FakeRouter(fixtures={
        ("WC", 2026): [_fixture("WC", "Brazil", "Serbia", when=NOW + timedelta(days=2))],
    })
    plans = plan_for_all_subscribers(BASE_CONFIG, router, store, subs, now=NOW)

    # Fetched ONCE despite two subscribers — the core $0-at-scale property.
    assert router.calls[("fixtures", "WC", 2026)] == 1
    assert len(plans) == 2
    assert all(len(p.alerts) == 1 for p in plans)      # each gets their own 1d reminder
    store.close()


def test_per_user_dedup_namespacing(tmp_path):
    store, subs = _setup(tmp_path)
    for cid in ("u1", "u2"):
        subs.upsert_subscriber(Subscriber(chat_id=cid, timezone="UTC", lead_times=["1d"]))
        subs.add_subscription(cid, "WC", 2026, "Brazil")
    router = FakeRouter(fixtures={
        ("WC", 2026): [_fixture("WC", "Brazil", "Serbia", when=NOW + timedelta(days=2))],
    })

    plans = {p.subscriber.chat_id: p for p in
             plan_for_all_subscribers(BASE_CONFIG, router, store, subs, now=NOW)}
    # Keys are namespaced by chat_id.
    assert plans["u1"].alerts[0].dedup_key.startswith("u1:")
    assert plans["u2"].alerts[0].dedup_key.startswith("u2:")

    # Mark u1's alert sent; u1 goes quiet, u2 is unaffected (separate namespace).
    for a in plans["u1"].alerts:
        store.mark_sent(a.dedup_key)
    plans2 = {p.subscriber.chat_id: p for p in
              plan_for_all_subscribers(BASE_CONFIG, router, store, subs, now=NOW)}
    assert plans2["u1"].alerts == []
    assert len(plans2["u2"].alerts) == 1
    store.close()


def test_per_user_toggles_and_watchlist(tmp_path):
    store, subs = _setup(tmp_path)
    # u1 wants only finals; u2 wants only reminders. Both watch the same match.
    subs.upsert_subscriber(Subscriber(chat_id="u1", reminders_enabled=False,
                                      upsets_enabled=False, finals_enabled=True))
    subs.upsert_subscriber(Subscriber(chat_id="u2", reminders_enabled=True,
                                      upsets_enabled=False, finals_enabled=False,
                                      lead_times=["1d"]))
    for cid in ("u1", "u2"):
        subs.add_subscription(cid, "WC", 2026, "Brazil")

    router = FakeRouter(
        fixtures={("WC", 2026): [_fixture("WC", "Brazil", "Serbia", when=NOW + timedelta(days=2))]},
        results={("WC", 2026): [_result("WC", "Brazil", "Serbia", 2, 0, when=NOW - timedelta(hours=2))]},
    )
    plans = {p.subscriber.chat_id: p for p in
             plan_for_all_subscribers(BASE_CONFIG, router, store, subs, now=NOW)}
    assert [a.type.value for a in plans["u1"].alerts] == ["final_score"]
    assert [a.type.value for a in plans["u2"].alerts] == ["fixture_reminder"]
    store.close()


def test_unwatched_team_excluded(tmp_path):
    store, subs = _setup(tmp_path)
    subs.upsert_subscriber(Subscriber(chat_id="u1", lead_times=["1d"]))
    subs.add_subscription("u1", "WC", 2026, "Brazil")  # not watching Spain
    router = FakeRouter(fixtures={("WC", 2026): [
        _fixture("WC", "Brazil", "Serbia", when=NOW + timedelta(days=2)),
        _fixture("WC", "Spain", "Italy", when=NOW + timedelta(days=2)),
    ]})
    plans = plan_for_all_subscribers(BASE_CONFIG, router, store, subs, now=NOW)
    assert len(plans[0].alerts) == 1
    assert "Brazil" in plans[0].alerts[0].summary
    store.close()


def test_odds_fetched_once_per_match_across_users(tmp_path):
    store, subs = _setup(tmp_path)
    # Both users want upsets on the same finished match.
    for cid in ("u1", "u2"):
        subs.upsert_subscriber(Subscriber(chat_id=cid, reminders_enabled=False,
                                          upsets_enabled=True, finals_enabled=False))
        subs.add_subscription(cid, "WC", 2026, "Brazil")
    router = FakeRouter(
        results={("WC", 2026): [_result("WC", "Brazil", "Serbia", 0, 1, when=NOW - timedelta(hours=2))]},
        standings={("WC", 2026): [
            Standing(team=TeamRef(name="Brazil"), position=1),
            Standing(team=TeamRef(name="Serbia"), position=20),
        ]},
    )
    plan_for_all_subscribers(BASE_CONFIG, router, store, subs, now=NOW)
    # Odds looked up once for the shared match despite two subscribers.
    assert router.odds_calls[("WC", "Brazil", "Serbia")] == 1
    assert router.calls[("results", "WC", 2026)] == 1
    store.close()


def test_paused_subscriber_not_fetched(tmp_path):
    store, subs = _setup(tmp_path)
    subs.upsert_subscriber(Subscriber(chat_id="u1", status="paused", lead_times=["1d"]))
    subs.add_subscription("u1", "SA", 2026, "Inter")
    router = FakeRouter(fixtures={("SA", 2026): [
        _fixture("SA", "Inter", "Roma", when=NOW + timedelta(days=2))]})
    plans = plan_for_all_subscribers(BASE_CONFIG, router, store, subs, now=NOW)
    assert plans == []                                 # paused user produces no plan
    assert router.calls[("fixtures", "SA", 2026)] == 0  # and its competition isn't fetched
    store.close()
