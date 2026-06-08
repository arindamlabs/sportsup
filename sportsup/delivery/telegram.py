"""Telegram Bot API sender.

Free, official, no payment method / template approval / 24h-window — ideal for personal
alerts. Sends via the Bot API ``/sendMessage``. The recipient is the bot's configured
``chat_id`` (set once via env), so ``OutboundMessage.recipient`` is ignored here.

Messages are sent as HTML (Telegram renders *bold*/_italic_ from format_alert as <b>/<i>).
"""

from __future__ import annotations

import html
import logging
import re

from ..providers.base import ProviderUnavailableError
from ..providers.http import HttpClient
from .base import OutboundMessage, SendResult, WhatsAppSender

logger = logging.getLogger("sportsup.delivery.telegram")


def _to_html(text: str) -> str:
    """Convert our simple *bold*/_italic_ markdown to Telegram-safe HTML."""
    esc = html.escape(text, quote=False)               # &, <, > (e.g. "Brighton & Hove")
    esc = re.sub(r"\*(.+?)\*", r"<b>\1</b>", esc)       # *bold*
    esc = re.sub(r"_(.+?)_", r"<i>\1</i>", esc)         # _italic_
    return esc


class TelegramSender(WhatsAppSender):
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, *, client: HttpClient | None = None) -> None:
        self._chat_id = chat_id
        self._client = client or HttpClient(f"https://api.telegram.org/bot{bot_token}")

    def send(self, message: OutboundMessage) -> SendResult:
        text = message.text or ""
        payload = {
            "chat_id": self._chat_id,
            "text": _to_html(text),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = self._client.post_json("/sendMessage", payload)
        except ProviderUnavailableError as exc:
            return SendResult(ok=False, provider=self.name, error=f"network/server error: {exc}")

        data = resp.data or {}
        if resp.status_code == 200 and data.get("ok"):
            msg_id = str((data.get("result") or {}).get("message_id", ""))
            logger.info("sent Telegram message %s to chat %s", msg_id, self._chat_id)
            return SendResult(ok=True, provider=self.name, provider_message_id=msg_id, raw=data)

        err = data.get("description") if isinstance(data, dict) else None
        code = data.get("error_code") if isinstance(data, dict) else None
        result = SendResult(
            ok=False, provider=self.name,
            error=err or f"HTTP {resp.status_code}",
            error_code=str(code) if code is not None else None,
            raw=data if isinstance(data, dict) else {},
        )
        logger.error("Telegram send failed (HTTP %s, code %s): %s", resp.status_code, result.error_code, result.error)
        return result
