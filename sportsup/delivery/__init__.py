"""Alert delivery behind a swappable sender interface.

The rest of the app builds an :class:`OutboundMessage` and calls a :class:`Sender`.
Which sender is used (console dry-run, Telegram, …) is a config/secrets decision
resolved by :func:`build_sender` — no caller knows the provider.
"""

from .base import OutboundMessage, SendResult, Sender
from .console import ConsoleSender
from .factory import build_sender
from .formatting import format_alert, message_for_alert
from .telegram import TelegramSender

__all__ = [
    "OutboundMessage",
    "SendResult",
    "Sender",
    "ConsoleSender",
    "TelegramSender",
    "build_sender",
    "format_alert",
    "message_for_alert",
]
