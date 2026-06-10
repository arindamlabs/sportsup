"""Console sender — the dry-run / test mode.

Prints exactly what *would* be sent instead of delivering it, so logic can be
validated without spending quota or spamming the user. This is what `dry_run: true`
(the default) resolves to.
"""

from __future__ import annotations

import logging

from .base import OutboundMessage, SendResult, Sender

logger = logging.getLogger("sportsup.delivery.console")


class ConsoleSender(Sender):
    name = "console"

    def send(self, message: OutboundMessage) -> SendResult:
        body = message.text or ""
        logger.info(
            "DRY-RUN -> %s%s:\n%s",
            message.recipient,
            f" [{message.dedup_key}]" if message.dedup_key else "",
            "\n".join(f"    | {line}" for line in body.splitlines()),
        )
        return SendResult(ok=True, provider=self.name, provider_message_id="dry-run")
