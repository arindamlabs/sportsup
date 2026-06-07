"""Phase 1 acceptance tests for the config schema."""

from datetime import timedelta

import pytest

from sportsup.config import AppConfig, load_config, parse_lead_time


def test_load_example_config_has_two_events(tmp_path):
    cfg = load_config("config.example.yaml")
    assert cfg.timezone == "America/Los_Angeles"
    ids = {e.id for e in cfg.events}
    assert {"world-cup-2026", "epl-2026-27"} <= ids


def test_lead_time_parsing():
    assert parse_lead_time("1d") == timedelta(days=1)
    assert parse_lead_time("2h") == timedelta(hours=2)
    assert parse_lead_time("30m") == timedelta(minutes=30)
    with pytest.raises(ValueError):
        parse_lead_time("soon")


def test_final_scores_defaults_off():
    cfg = AppConfig.model_validate(
        {"events": [{"id": "x", "name": "X", "competition_code": "PL", "season": 2026}]}
    )
    assert cfg.events[0].alerts.final_scores is False
    assert cfg.events[0].alerts.upcoming_fixtures is True


def test_invalid_timezone_rejected():
    with pytest.raises(ValueError):
        AppConfig.model_validate({"timezone": "Mars/Phobos"})


def test_duplicate_event_ids_rejected():
    with pytest.raises(ValueError):
        AppConfig.model_validate(
            {
                "events": [
                    {"id": "dup", "name": "A", "competition_code": "PL", "season": 2026},
                    {"id": "dup", "name": "B", "competition_code": "WC", "season": 2026},
                ]
            }
        )


def test_unknown_field_rejected():
    with pytest.raises(ValueError):
        AppConfig.model_validate({"surprise": True})


def test_enabled_events_filter_and_team_dedup():
    cfg = AppConfig.model_validate(
        {
            "events": [
                {
                    "id": "a",
                    "name": "A",
                    "competition_code": "PL",
                    "season": 2026,
                    "enabled": False,
                    "teams": ["Arsenal", "arsenal", "Liverpool"],
                },
                {"id": "b", "name": "B", "competition_code": "WC", "season": 2026},
            ]
        }
    )
    assert [e.id for e in cfg.enabled_events] == ["b"]
    assert cfg.events[0].teams == ["Arsenal", "Liverpool"]  # case-insensitive dedup


def test_bad_lead_time_in_config_rejected():
    with pytest.raises(ValueError):
        AppConfig.model_validate({"reminders": {"lead_times": ["1x"]}})
