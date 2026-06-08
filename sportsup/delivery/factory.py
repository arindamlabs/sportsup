"""Resolve which sender to use from config + secrets.

`dry_run` (default true, env `SPORTSUP_DRY_RUN` overrides config) forces the console
sender regardless of provider — the master safety switch. Otherwise the configured
provider is built if its credentials are present.
"""

from __future__ import annotations

import logging

from .base import WhatsAppSender
from .console import ConsoleSender

logger = logging.getLogger("sportsup.delivery.factory")


def resolve_dry_run(config, secrets) -> bool:
    if getattr(secrets, "dry_run_override", None) is not None:
        return secrets.dry_run_override
    return config.delivery.dry_run


def build_sender(config, secrets) -> WhatsAppSender | None:
    """Return a sender, or None if a live provider is selected but unconfigured."""
    if resolve_dry_run(config, secrets):
        logger.info("dry-run enabled — using console sender (no real messages sent)")
        return ConsoleSender()

    provider = config.delivery.provider
    if provider == "console":
        return ConsoleSender()

    if provider == "telegram":
        if not (secrets.telegram_bot_token and secrets.telegram_chat_id):
            logger.error(
                "delivery.provider=telegram but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
                "are not set in .env"
            )
            return None
        from .telegram import TelegramSender

        return TelegramSender(secrets.telegram_bot_token, secrets.telegram_chat_id)

    if provider == "meta_cloud":
        if not (secrets.whatsapp_access_token and secrets.whatsapp_phone_number_id):
            logger.error(
                "delivery.provider=meta_cloud but WHATSAPP_ACCESS_TOKEN / "
                "WHATSAPP_PHONE_NUMBER_ID are not set in .env"
            )
            return None
        from .meta_cloud import MetaCloudSender

        return MetaCloudSender(
            secrets.whatsapp_access_token, secrets.whatsapp_phone_number_id
        )

    if provider == "twilio":
        logger.error("Twilio sender is documented as a fallback but not implemented yet")
        return None

    logger.error("unknown delivery provider %r", provider)
    return None
