"""Tests for the Telegram sender + channel switching."""

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from sportsup.alerts.models import Alert, AlertType
from sportsup.config import AppConfig
from sportsup.delivery import OutboundMessage, build_sender, message_for_alert
from sportsup.delivery.telegram import TelegramSender, _to_html
from sportsup.providers import Fixture, MatchStatus, TeamRef
from sportsup.providers.http import HttpClient

UTC = timezone.utc


def _sender(handler) -> TelegramSender:
    client = HttpClient("https://api.telegram.test/bottoken",
                        transport=httpx.MockTransport(handler), sleep=lambda *a: None)
    return TelegramSender("token", "99887766", client=client)


def test_telegram_send_success_uses_chat_id_and_html():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json
        assert req.url.path.endswith("/sendMessage")
        captured.update(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    res = _sender(handler).send(OutboundMessage(recipient="ignored", text="*UPSET!* _wow_"))
    assert res.ok and res.provider == "telegram" and res.provider_message_id == "42"
    assert captured["chat_id"] == "99887766"          # sender's chat id, not message.recipient
    assert captured["parse_mode"] == "HTML"
    assert "<b>UPSET!</b>" in captured["text"] and "<i>wow</i>" in captured["text"]


def test_telegram_send_surfaces_error():
    def handler(req):
        return httpx.Response(400, json={"ok": False, "error_code": 400,
                                         "description": "Bad Request: chat not found"})
    res = _sender(handler).send(OutboundMessage(recipient="x", text="hi"))
    assert not res.ok and res.error_code == "400"
    assert "chat not found" in res.error


def test_to_html_escapes_ampersand():
    assert _to_html("Brighton & Hove") == "Brighton &amp; Hove"


# --- channel switching -----------------------------------------------------

def _secrets(**kw):
    base = dict(dry_run_override=None, whatsapp_access_token=None, whatsapp_phone_number_id=None,
                whatsapp_recipient=None, telegram_bot_token=None, telegram_chat_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_factory_builds_telegram_when_configured():
    cfg = AppConfig.model_validate({"delivery": {"provider": "telegram", "dry_run": False}})
    sender = build_sender(cfg, _secrets(telegram_bot_token="t", telegram_chat_id="1"))
    assert isinstance(sender, TelegramSender)


def test_factory_telegram_missing_creds_returns_none():
    cfg = AppConfig.model_validate({"delivery": {"provider": "telegram", "dry_run": False}})
    assert build_sender(cfg, _secrets()) is None


def test_telegram_provider_ignores_whatsapp_template():
    # Even with a template configured, telegram alerts must be plain text (no template).
    cfg = AppConfig.model_validate({"delivery": {
        "provider": "telegram", "alert_template_name": "sportsup_alert"}})
    fx = Fixture(provider="t", provider_fixture_id="1", competition_code="WC", season=2026,
                 utc_kickoff=datetime(2026, 6, 13, 22, tzinfo=UTC), status=MatchStatus.FINISHED,
                 home=TeamRef(name="Brazil"), away=TeamRef(name="Serbia"))
    alert = Alert(AlertType.FINAL_SCORE, "wc", "k", fx, summary="",
                  context={"competition": "WC", "home_score": 1, "away_score": 0})
    msg = message_for_alert(alert, cfg, "chatid")
    assert msg.text is not None and msg.template_name is None
