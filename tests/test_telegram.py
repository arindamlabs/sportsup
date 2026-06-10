"""Tests for the Telegram sender + channel selection."""

from types import SimpleNamespace

import httpx

from sportsup.config import AppConfig
from sportsup.delivery import OutboundMessage, build_sender
from sportsup.delivery.telegram import TelegramSender, _to_html
from sportsup.providers.http import HttpClient


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

    # No recipient -> falls back to the sender's default chat id (single-user).
    res = _sender(handler).send(OutboundMessage(recipient="", text="*UPSET!* _wow_"))
    assert res.ok and res.provider == "telegram" and res.provider_message_id == "42"
    assert captured["chat_id"] == "99887766"          # default chat id used
    assert captured["parse_mode"] == "HTML"
    assert "<b>UPSET!</b>" in captured["text"] and "<i>wow</i>" in captured["text"]


def test_telegram_send_routes_to_message_recipient():
    # Multi-user: each message targets its own chat via recipient (overrides default).
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json
        captured.update(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    res = _sender(handler).send(OutboundMessage(recipient="12345", text="hi"))
    assert res.ok and captured["chat_id"] == "12345"  # message recipient, not the default


def test_telegram_send_surfaces_error():
    def handler(req):
        return httpx.Response(400, json={"ok": False, "error_code": 400,
                                         "description": "Bad Request: chat not found"})
    res = _sender(handler).send(OutboundMessage(recipient="x", text="hi"))
    assert not res.ok and res.error_code == "400"
    assert "chat not found" in res.error


def test_to_html_escapes_ampersand():
    assert _to_html("Brighton & Hove") == "Brighton &amp; Hove"


# --- channel selection -----------------------------------------------------

def _secrets(**kw):
    base = dict(dry_run_override=None, telegram_bot_token=None, telegram_chat_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_factory_builds_telegram_when_configured():
    cfg = AppConfig.model_validate({"delivery": {"provider": "telegram", "dry_run": False}})
    sender = build_sender(cfg, _secrets(telegram_bot_token="t", telegram_chat_id="1"))
    assert isinstance(sender, TelegramSender)


def test_factory_telegram_missing_creds_returns_none():
    cfg = AppConfig.model_validate({"delivery": {"provider": "telegram", "dry_run": False}})
    assert build_sender(cfg, _secrets()) is None


def test_factory_force_live_bypasses_dry_run():
    # `test-send` builds the real provider even when dry_run is on.
    from sportsup.delivery.console import ConsoleSender
    cfg = AppConfig.model_validate({"delivery": {"provider": "telegram", "dry_run": True}})
    s = _secrets(telegram_bot_token="t", telegram_chat_id="1")
    assert isinstance(build_sender(cfg, s), ConsoleSender)
    assert isinstance(build_sender(cfg, s, force_live=True), TelegramSender)
