"""Phase 7 tests: subscriber/subscription store, cascade delete, config migration."""

from sportsup.config import AppConfig
from sportsup.state import StateStore
from sportsup.subscribers import (
    ALL_TEAMS,
    Subscriber,
    SubscriberStore,
    effective_config,
    import_single_user,
    watchlist_for,
)


def _store(tmp_path) -> SubscriberStore:
    return SubscriberStore(StateStore(tmp_path / "s.sqlite"))


def test_upsert_roundtrips_and_updates(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="42", timezone="Europe/London", lead_times=["2h"]))
    got = st.get_subscriber("42")
    assert got.timezone == "Europe/London" and got.lead_times == ["2h"]
    created = got.created_at

    # Update preserves created_at, refreshes prefs.
    st.upsert_subscriber(Subscriber(chat_id="42", timezone="UTC", finals_enabled=True,
                                    created_at=created))
    got = st.get_subscriber("42")
    assert got.timezone == "UTC" and got.finals_enabled is True
    assert got.created_at == created


def test_list_and_status_filter(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="a"))
    st.upsert_subscriber(Subscriber(chat_id="b"))
    st.set_status("b", "paused")
    assert {s.chat_id for s in st.list_subscribers()} == {"a", "b"}
    assert [s.chat_id for s in st.list_subscribers(status="active")] == ["a"]
    assert st.get_subscriber("b").is_active is False


def test_subscriptions_add_idempotent_and_remove(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="a"))
    assert st.add_subscription("a", "PL", 2026, "Arsenal") is True
    assert st.add_subscription("a", "PL", 2026, "Arsenal") is False  # dup ignored
    st.add_subscription("a", "PL", 2026, "Chelsea")
    st.add_subscription("a", "WC", 2026, "Brazil")
    assert len(st.list_subscriptions("a")) == 3

    # Remove a single team.
    assert st.remove_subscription("a", "PL", 2026, "Arsenal") == 1
    # Remove a whole competition.
    assert st.remove_subscription("a", "PL", 2026) == 1  # Chelsea
    assert {s.competition_code for s in st.list_subscriptions("a")} == {"WC"}


def test_active_competitions_excludes_paused(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="a"))
    st.upsert_subscriber(Subscriber(chat_id="b"))
    st.add_subscription("a", "PL", 2026, "Arsenal")
    st.add_subscription("b", "SA", 2026, "Inter")
    assert st.active_competitions() == {("PL", 2026), ("SA", 2026)}
    st.set_status("b", "paused")
    assert st.active_competitions() == {("PL", 2026)}  # paused user's comp dropped


def test_delete_subscriber_cascades(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="a"))
    st.add_subscription("a", "PL", 2026, "Arsenal")
    st.add_subscription("a", "WC", 2026, ALL_TEAMS)
    assert st.delete_subscriber("a") is True
    assert st.get_subscriber("a") is None
    assert st.list_subscriptions("a") == []          # FK cascade removed subscriptions
    assert st.delete_subscriber("a") is False         # nothing left to delete


def test_watchlist_for_handles_all_teams(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="a"))
    st.add_subscription("a", "WC", 2026, ALL_TEAMS)
    st.add_subscription("a", "PL", 2026, "Arsenal")
    subs = st.list_subscriptions("a")
    assert watchlist_for(subs, "WC", 2026) == []          # ALL_TEAMS -> empty = all
    assert watchlist_for(subs, "PL", 2026) == ["Arsenal"]


# --- migration from single-user config -------------------------------------

_CONFIG = {
    "timezone": "America/Los_Angeles",
    "quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00", "behavior": "defer"},
    "reminders": {"lead_times": ["1d", "1h"]},
    "delivery": {"provider": "telegram", "dry_run": False},
    "events": [
        {"id": "wc", "name": "WC", "competition_code": "WC", "season": 2026,
         "teams": ["Brazil", "Japan"],
         "alerts": {"upcoming_fixtures": True, "shock_result": True, "final_scores": False}},
        {"id": "pl", "name": "PL", "competition_code": "PL", "season": 2026, "teams": [],
         "alerts": {"upcoming_fixtures": True, "shock_result": True, "final_scores": False}},
    ],
}


def test_import_single_user_maps_prefs_and_subs(tmp_path):
    st = _store(tmp_path)
    cfg = AppConfig.model_validate(_CONFIG)
    sub, added = import_single_user(st, cfg, "555")
    assert added == 3                                  # Brazil, Japan, and PL=ALL_TEAMS
    assert sub.timezone == "America/Los_Angeles"
    assert sub.quiet_start == "22:00" and sub.quiet_behavior == "defer"
    assert sub.lead_times == ["1d", "1h"]
    assert sub.reminders_enabled and sub.upsets_enabled and not sub.finals_enabled
    subs = st.list_subscriptions("555")
    assert watchlist_for(subs, "WC", 2026) == ["Brazil", "Japan"]
    assert watchlist_for(subs, "PL", 2026) == []       # empty teams -> ALL_TEAMS


def test_import_single_user_is_idempotent(tmp_path):
    st = _store(tmp_path)
    cfg = AppConfig.model_validate(_CONFIG)
    import_single_user(st, cfg, "555")
    _, added2 = import_single_user(st, cfg, "555")     # re-run
    assert added2 == 0                                 # nothing duplicated
    assert len(st.list_subscribers()) == 1


def test_effective_config_applies_user_prefs(tmp_path):
    base = AppConfig.model_validate(_CONFIG)
    sub = Subscriber(chat_id="9", timezone="Asia/Tokyo", quiet_enabled=False,
                     lead_times=["3h"])
    eff = effective_config(sub, base, base.events[:1])
    assert eff.timezone == "Asia/Tokyo"
    assert eff.quiet_hours.enabled is False
    assert eff.reminders.lead_times == ["3h"]
    # Shared (non-user) settings are preserved from the base config.
    assert eff.shock_detection.sensitivity == base.shock_detection.sensitivity
