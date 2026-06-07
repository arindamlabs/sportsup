"""Meta WhatsApp Cloud API sender.

Sends via the Graph API ``/{phone_number_id}/messages`` endpoint. Supports free-form
text (valid inside WhatsApp's 24-hour customer-service window) and pre-approved
templates (valid any time — used for the connectivity test and out-of-window alerts).

Known error codes surfaced for clarity:
  131047  re-engagement required — recipient is outside the 24h window; use a template
  131030  recipient not in the test number's allowed list — verify the number in Meta
  190     invalid/expired access token
"""

from __future__ import annotations

import logging

from ..providers.base import ProviderUnavailableError
from ..providers.http import HttpClient
from .base import OutboundMessage, SendResult, WhatsAppSender

logger = logging.getLogger("sportsup.delivery.meta_cloud")

DEFAULT_API_VERSION = "v21.0"


class MetaCloudSender(WhatsAppSender):
    name = "meta_cloud"

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        *,
        api_version: str = DEFAULT_API_VERSION,
        client: HttpClient | None = None,
    ) -> None:
        self._phone_number_id = phone_number_id
        self._client = client or HttpClient(
            f"https://graph.facebook.com/{api_version}",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    def _payload(self, message: OutboundMessage) -> dict:
        base = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": message.recipient,
        }
        if message.template_name:
            return {
                **base,
                "type": "template",
                "template": {
                    "name": message.template_name,
                    "language": {"code": message.template_lang},
                    **({"components": message.template_components}
                       if message.template_components else {}),
                },
            }
        return {**base, "type": "text", "text": {"preview_url": False, "body": message.text or ""}}

    def send(self, message: OutboundMessage) -> SendResult:
        try:
            resp = self._client.post_json(f"/{self._phone_number_id}/messages", self._payload(message))
        except ProviderUnavailableError as exc:
            return SendResult(ok=False, provider=self.name, error=f"network/server error: {exc}")

        data = resp.data or {}
        if resp.status_code == 200 and data.get("messages"):
            msg_id = data["messages"][0].get("id")
            logger.info("sent WhatsApp message %s to %s", msg_id, message.recipient)
            return SendResult(ok=True, provider=self.name, provider_message_id=msg_id, raw=data)

        err = data.get("error", {}) if isinstance(data, dict) else {}
        code = err.get("code")
        result = SendResult(
            ok=False,
            provider=self.name,
            error=err.get("message") or f"HTTP {resp.status_code}",
            error_code=str(code) if code is not None else None,
            raw=data if isinstance(data, dict) else {},
        )
        logger.error(
            "WhatsApp send failed (HTTP %s, code %s): %s%s",
            resp.status_code, result.error_code, result.error,
            "  [tip: outside 24h window — send a template, or message the bot first]"
            if str(code) == "131047" else "",
        )
        return result
