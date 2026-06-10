"""Sender interface and message/result types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class OutboundMessage:
    """A message to deliver: free-form `text` to a `recipient` (a Telegram chat id)."""

    recipient: str                       # Telegram chat id, e.g. "987654321"
    text: str | None = None
    dedup_key: str | None = None         # carried through for logging/state correlation


@dataclass
class SendResult:
    ok: bool
    provider: str
    provider_message_id: str | None = None
    error: str | None = None
    error_code: str | None = None
    raw: dict = field(default_factory=dict)


class Sender(ABC):
    name: str = "abstract"

    @abstractmethod
    def send(self, message: OutboundMessage) -> SendResult:
        ...
