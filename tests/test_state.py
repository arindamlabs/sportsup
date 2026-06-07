"""Phase 1 acceptance tests for the SQLite state/dedup store."""

from sportsup.state import StateStore


def test_dedup_is_idempotent(tmp_path):
    store = StateStore(tmp_path / "s.sqlite")
    key = "world-cup-2026:fixture123:reminder:1h"
    assert store.was_sent(key) is False
    assert store.mark_sent(key, event_id="world-cup-2026", alert_type="reminder") is True
    assert store.was_sent(key) is True
    # Second mark is a no-op (no duplicate alert).
    assert store.mark_sent(key) is False
    assert store.sent_count() == 1
    store.close()


def test_state_persists_across_reopen(tmp_path):
    db = tmp_path / "s.sqlite"
    s1 = StateStore(db)
    s1.mark_sent("k1")
    s1.set_meta("last_sync", "2026-06-07T00:00:00+00:00")
    s1.close()

    s2 = StateStore(db)  # reopen same file -> survives "restart"
    assert s2.was_sent("k1") is True
    assert s2.get_meta("last_sync") == "2026-06-07T00:00:00+00:00"
    s2.close()
