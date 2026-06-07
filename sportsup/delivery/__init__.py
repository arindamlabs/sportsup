"""WhatsApp delivery behind a swappable sender interface.

The rest of the app builds an :class:`OutboundMessage` and calls a
:class:`WhatsAppSender`. Which sender is used (console dry-run, Meta Cloud, …) is a
config/secrets decision resolved by :func:`build_sender` — no caller knows the provider.
"""

from .base import OutboundMessage, SendResult, WhatsAppSender
from .console import ConsoleSender
from .factory import build_sender
from .formatting import format_alert

__all__ = [
    "OutboundMessage",
    "SendResult",
    "WhatsAppSender",
    "ConsoleSender",
    "build_sender",
    "format_alert",
]
