"""Phase 4 tests: formatting, console sender, Meta Cloud sender, factory selection."""

from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx

from sportsup.alerts.models import Alert, AlertType
from sportsup.config import AppConfig
from sportsup.delivery import ConsoleSender, OutboundMessage, build_sender, format_alert
from sportsup.delivery.meta_cloud import MetaCloudSender
from sportsup.providers import Fixture, MatchStatus, TeamRef
from sportsup.providers.http import HttpClient

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
    res = ConsoleSender().send(OutboundMessage(recipient="+1555", text="hi"))
    assert res.ok and res.provider == "console"


# --- Meta Cloud sender (mocked transport) ---------------------------------

def _meta_sender(handler) -> MetaCloudSender:
    client = HttpClient("https://graph.test/v21.0",
                        transport=httpx.MockTransport(handler), sleep=lambda *a: None)
    return MetaCloudSender("token", "123456", client=client)


def test_meta_cloud_send_text_success():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST" and req.url.path.endswith("/123456/messages")
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})
    res = _meta_sender(handler).send(OutboundMessage(recipient="+1555", text="hello"))
    assert res.ok and res.provider_message_id == "wamid.ABC"


def test_meta_cloud_send_surfaces_error_code():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {
            "message": "Re-engagement message", "code": 131047,
        }})
    res = _meta_sender(handler).send(OutboundMessage(recipient="+1555", text="hello"))
    assert not res.ok and res.error_code == "131047"
    assert "Re-engagement" in res.error


def test_meta_cloud_template_payload_shape():
    captured = {}
    def handler(req: httpx.Request) -> httpx.Response:
        import json
        captured.update(json.loads(req.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.T"}]})
    _meta_sender(handler).send(OutboundMessage(
        recipient="+1555", template_name="hello_world", template_lang="en_US"))
    assert captured["type"] == "template"
    assert captured["template"]["name"] == "hello_world"


# --- factory ---------------------------------------------------------------

def _secrets(**kw):
    base = dict(dry_run_override=None, whatsapp_access_token=None,
                whatsapp_phone_number_id=None, whatsapp_recipient="+1555")
    base.update(kw)
    return SimpleNamespace(**base)


def test_factory_dry_run_uses_console():
    cfg = AppConfig.model_validate({"delivery": {"provider": "meta_cloud", "dry_run": True}})
    sender = build_sender(cfg, _secrets(whatsapp_access_token="t", whatsapp_phone_number_id="1"))
    assert isinstance(sender, ConsoleSender)


def test_factory_live_meta_when_configured():
    cfg = AppConfig.model_validate({"delivery": {"provider": "meta_cloud", "dry_run": False}})
    sender = build_sender(cfg, _secrets(whatsapp_access_token="t", whatsapp_phone_number_id="1"))
    assert isinstance(sender, MetaCloudSender)


def test_factory_live_meta_missing_creds_returns_none():
    cfg = AppConfig.model_validate({"delivery": {"provider": "meta_cloud", "dry_run": False}})
    assert build_sender(cfg, _secrets()) is None


def test_factory_env_override_forces_dry_run():
    cfg = AppConfig.model_validate({"delivery": {"provider": "meta_cloud", "dry_run": False}})
    sender = build_sender(cfg, _secrets(dry_run_override=True,
                                        whatsapp_access_token="t", whatsapp_phone_number_id="1"))
    assert isinstance(sender, ConsoleSender)
