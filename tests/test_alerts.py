"""Phase 3 tests: shock heuristic + alert engine (reminders, results, dedup)."""

from datetime import datetime, timedelta, timezone

from sportsup.alerts import AlertEngine, AlertType, evaluate_upset
from sportsup.config import AppConfig
from sportsup.providers import Fixture, MatchOdds, MatchResult, MatchStatus, Score, Standing, TeamRef
from sportsup.state import StateStore

UTC = timezone.utc


def _config(**shock) -> AppConfig:
    return AppConfig.model_validate({
        "events": [{
            "id": "wc", "name": "World Cup", "competition_code": "WC", "season": 2026,
            "teams": ["Brazil"],
            "alerts": {"upcoming_fixtures": True, "shock_result": True, "final_scores": True},
        }],
        "shock_detection": shock or {},
    })


def _result(home, away, hs, as_, status=MatchStatus.FINISHED, kickoff=None) -> MatchResult:
    fx = Fixture(
        provider="test", provider_fixture_id="1", competition_code="WC", season=2026,
        utc_kickoff=kickoff or datetime(2026, 6, 13, 18, tzinfo=UTC), status=status,
        home=TeamRef(name=home), away=TeamRef(name=away),
    )
    return MatchResult.from_fixture(fx, Score(home=hs, away=as_))


# --- shock heuristic: odds -------------------------------------------------

def test_odds_upset_when_underdog_wins():
    cfg = _config(sensitivity=0.65)
    r = _result("Brazil", "Morocco", 0, 1)            # away underdog wins
    odds = MatchOdds(home_win=1.3, draw=5.0, away_win=11.0)
    ev = evaluate_upset(r, config=cfg, odds=odds)
    assert ev.is_upset and ev.signal_used == "odds"
    assert ev.upset_index > 0.65


def test_odds_no_upset_when_favourite_wins():
    cfg = _config(sensitivity=0.65)
    r = _result("Brazil", "Morocco", 2, 0)            # home favourite wins
    odds = MatchOdds(home_win=1.3, draw=5.0, away_win=11.0)
    ev = evaluate_upset(r, config=cfg, odds=odds)
    assert not ev.is_upset and ev.signal_used == "odds"


def test_draw_is_not_an_upset():
    cfg = _config()
    ev = evaluate_upset(_result("Brazil", "Morocco", 1, 1), config=cfg,
                        odds=MatchOdds(home_win=1.3, draw=5.0, away_win=11.0))
    assert not ev.is_upset and ev.signal_used is None


# --- shock heuristic: standings & form fallbacks ---------------------------

def test_standings_gap_upset_when_no_odds():
    cfg = _config(min_position_gap=8)
    r = _result("Brighton", "Manchester City", 1, 0)
    standings = [
        Standing(team=TeamRef(name="Manchester City"), position=2),
        Standing(team=TeamRef(name="Brighton"), position=18),
    ]
    ev = evaluate_upset(r, config=cfg, odds=None, standings=standings)
    assert ev.is_upset and ev.signal_used == "standings"


def test_form_fallback_upset():
    cfg = _config(signal_priority=["form"], form_window=5)
    r = _result("Strugglers", "FlyingHigh", 1, 0)
    standings = [
        Standing(team=TeamRef(name="Strugglers"), position=10, form="LLLLL"),
        Standing(team=TeamRef(name="FlyingHigh"), position=3, form="WWWWW"),
    ]
    ev = evaluate_upset(r, config=cfg, odds=None, standings=standings)
    assert ev.is_upset and ev.signal_used == "form"


# --- engine: reminders -----------------------------------------------------

def test_plan_reminders_future_only_and_dedup_keys(tmp_path):
    cfg = _config()
    store = StateStore(tmp_path / "s.sqlite")
    engine = AlertEngine(cfg, store)
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    fx = Fixture(
        provider="test", provider_fixture_id="9", competition_code="WC", season=2026,
        utc_kickoff=now + timedelta(days=2), status=MatchStatus.TIMED,
        home=TeamRef(name="Brazil", tla="BRA"), away=TeamRef(name="Serbia", tla="SRB"),
    )
    alerts = engine.plan_reminders(cfg.events[0], [fx], now=now)
    labels = {a.lead_label for a in alerts}
    assert labels == {"1d", "1h"}  # both lead-times still in the future
    assert all(a.type is AlertType.FIXTURE_REMINDER for a in alerts)
    assert any(a.dedup_key.endswith(":reminder:1d") for a in alerts)
    # Reminders are scheduled before kickoff.
    assert all(a.scheduled_for < fx.utc_kickoff for a in alerts)
    store.close()


def test_plan_reminders_skips_past_lead_windows(tmp_path):
    cfg = _config()
    store = StateStore(tmp_path / "s.sqlite")
    engine = AlertEngine(cfg, store)
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    fx = Fixture(
        provider="test", provider_fixture_id="9", competition_code="WC", season=2026,
        utc_kickoff=now + timedelta(minutes=30), status=MatchStatus.TIMED,
        home=TeamRef(name="Brazil"), away=TeamRef(name="Serbia"),
    )
    # Both 1d and 1h windows already passed -> no reminders.
    assert engine.plan_reminders(cfg.events[0], [fx], now=now) == []
    store.close()


# --- engine: results + dedup ----------------------------------------------

def test_evaluate_results_final_and_shock_then_dedup(tmp_path):
    cfg = _config(sensitivity=0.65)
    store = StateStore(tmp_path / "s.sqlite")
    engine = AlertEngine(cfg, store)
    r = _result("Brazil", "Morocco", 0, 1)
    odds = MatchOdds(home_win=1.3, draw=5.0, away_win=11.0)

    alerts = engine.evaluate_results(cfg.events[0], [r], odds_lookup=lambda _r: odds)
    types = {a.type for a in alerts}
    assert types == {AlertType.FINAL_SCORE, AlertType.SHOCK_RESULT}

    # Dedup: mark one sent, it drops out of `unsent`; the other remains.
    assert engine.mark_sent(alerts[0]) is True
    remaining = engine.unsent(alerts)
    assert len(remaining) == 1 and remaining[0].dedup_key != alerts[0].dedup_key
    # Marking again is idempotent (no second send).
    assert engine.mark_sent(alerts[0]) is False
    store.close()


def test_unfinished_match_produces_no_result_alert(tmp_path):
    cfg = _config()
    store = StateStore(tmp_path / "s.sqlite")
    engine = AlertEngine(cfg, store)
    r = _result("Brazil", "Morocco", None, None, status=MatchStatus.IN_PLAY)
    assert engine.evaluate_results(cfg.events[0], [r], odds_lookup=lambda _r: None) == []
    store.close()
