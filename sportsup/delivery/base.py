"""Sender interface and message/result types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class OutboundMessage:
    """A message to deliver. Either free-form `text` (works inside WhatsApp's 24-hour
    window) or a pre-approved `template` (works any time). Senders that don't support
    templates ignore that field."""

    recipient: str                       # E.164, e.g. "+14155550100"
    text: str | None = None
    template_name: str | None = None
    template_lang: str = "en_US"
    template_components: list | None = None
    dedup_key: str | None = None         # carried through for logging/state correlation


@dataclass
class SendResult:
    ok: bool
    provider: str
    provider_message_id: str | None = None
    error: str | None = None
    error_code: str | None = None
    raw: dict = field(default_factory=dict)


class WhatsAppSender(ABC):
    name: str = "abstract"

    @abstractmethod
    def send(self, message: OutboundMessage) -> SendResult:
        ...
