"""Delivery tests: alert formatting, the console sender, and factory selection."""

from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sportsup.alerts.models import Alert, AlertType
from sportsup.config import AppConfig
from sportsup.delivery import ConsoleSender, OutboundMessage, build_sender, format_alert
from sportsup.delivery.telegram import TelegramSender
from sportsup.providers import Fixture, MatchStatus, TeamRef

TZ = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc


def _fixture(home="Brazil", away="Morocco", status=MatchStatus.TIMED):
    return Fixture(
        provider="t", provider_fixture_id="1", competition_code="WC", season=2026,
        utc_kickoff=datetime(2026, 6, 13, 22, 0, tzinfo=UTC),  # 15:00 PDT
        status=status, home=TeamRef(name=home), away=TeamRef(name=away),
    )


def test_format_reminder_has_local_time_and_teams():
    a = Alert(AlertType.FIXTURE_REMINDER, "wc", "k", _fixture(), summary="",
              lead_label="1d", context={"competition": "FIFA World Cup 2026"})
    msg = format_alert(a, TZ)
    assert "FIFA World Cup 2026" in msg
    assert "Brazil vs Morocco" in msg
    assert "15:00 PDT" in msg          # rendered in configured timezone
    assert "1 day to go" in msg


def test_format_final_and_shock():
    final = Alert(AlertType.FINAL_SCORE, "wc", "k", _fixture(status=MatchStatus.FINISHED),
                  summary="", context={"competition": "WC", "home_score": 0, "away_score": 1})
    fmsg = format_alert(final, TZ)
    assert "Full time" in fmsg and "Brazil 0–1 Morocco" in fmsg

    shock = Alert(AlertType.SHOCK_RESULT, "wc", "k", _fixture(status=MatchStatus.FINISHED),
                  summary="", context={"competition": "WC", "home_score": 0, "away_score": 1,
                                       "reason": "Morocco won with only ~9% implied chance"})
    smsg = format_alert(shock, TZ)
    assert "UPSET" in smsg and "9% implied chance" in smsg


def test_console_sender_is_ok():
    res = ConsoleSender().send(OutboundMessage(recipient="987654321", text="hi"))
    assert res.ok and res.provider == "console"


# --- factory ---------------------------------------------------------------

def _secrets(**kw):
    base = dict(dry_run_override=None, telegram_bot_token=None, telegram_chat_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_factory_dry_run_uses_console():
    cfg = AppConfig.model_validate({"delivery": {"provider": "telegram", "dry_run": True}})
    sender = build_sender(cfg, _secrets(telegram_bot_token="t", telegram_chat_id="1"))
    assert isinstance(sender, ConsoleSender)


def test_factory_live_telegram_when_configured():
    cfg = AppConfig.model_validate({"delivery": {"provider": "telegram", "dry_run": False}})
    sender = build_sender(cfg, _secrets(telegram_bot_token="t", telegram_chat_id="1"))
    assert isinstance(sender, TelegramSender)


def test_factory_live_telegram_missing_creds_returns_none():
    cfg = AppConfig.model_validate({"delivery": {"provider": "telegram", "dry_run": False}})
    assert build_sender(cfg, _secrets()) is None


def test_factory_env_override_forces_dry_run():
    cfg = AppConfig.model_validate({"delivery": {"provider": "telegram", "dry_run": False}})
    sender = build_sender(cfg, _secrets(dry_run_override=True,
                                        telegram_bot_token="t", telegram_chat_id="1"))
    assert isinstance(sender, ConsoleSender)
