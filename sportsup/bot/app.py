"""Build and run the PTB Application (long polling) + the delivery loop.

No webhook / public URL needed — `getUpdates` long-polling works fine on the
outbound-only Oracle VM. The SubscriberStore is created once and shared via
``bot_data``; ``post_init`` registers the command menu so commands autocomplete
in the chat.

The bot is the unified runtime: alongside inbound handling it runs the multi-user
delivery cycle on a repeating JobQueue job. The cycle does blocking network I/O
(httpx providers), so it runs in a worker thread (``asyncio.to_thread``) on its own
DB connection — WAL + busy_timeout keep it clean against the inbound handlers.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand, Update
from telegram.ext import Application

from ..config import AppConfig
from ..settings import Secrets
from ..state import StateStore
from ..subscribers import SubscriberStore
from .handlers import register_handlers
from .texts import active_commands

logger = logging.getLogger("sportsup.bot")

DEFAULT_DELIVERY_MINUTES = 5


async def _post_init(application: Application) -> None:
    """Register the in-chat command menu from the catalog (active commands only)."""
    await application.bot.set_my_commands(
        [BotCommand(c.name, c.description) for c in active_commands()]
    )
    me = await application.bot.get_me()
    logger.info("bot @%s ready; %d command(s) registered", me.username, len(active_commands()))


def _build_delivery_sender(config: AppConfig, secrets: Secrets):
    """A sender for the delivery loop. Respects dry_run (console), else Telegram —
    built directly from the bot token so it works without a single configured chat
    (each message carries its own recipient)."""
    from ..delivery.console import ConsoleSender
    from ..delivery.factory import resolve_dry_run

    if resolve_dry_run(config, secrets):
        return ConsoleSender()
    if config.delivery.provider == "telegram" and secrets.telegram_bot_token:
        from ..delivery.telegram import TelegramSender

        return TelegramSender(secrets.telegram_bot_token, secrets.telegram_chat_id or "")
    from ..delivery import build_sender

    return build_sender(config, secrets, force_live=True)


def _setup_delivery(
    application: Application, db_path: str, config: AppConfig, secrets: Secrets,
    *, every_minutes: int,
) -> None:
    """Register the repeating multi-user delivery job, if we have data + a sender."""
    from ..catalog import league_map
    from ..mux_delivery import run_delivery_cycle
    from ..providers.router import build_router

    router = build_router(secrets, league_map=league_map())
    if router is None:
        logger.warning("delivery loop OFF: no data-provider credentials (inbound only). "
                       "Set FOOTBALL_DATA_API_KEY in .env to enable alerts.")
        return
    sender = _build_delivery_sender(config, secrets)
    if sender is None:
        logger.warning("delivery loop OFF: no delivery credentials.")
        return

    # The delivery loop runs in a worker thread, so give it its own DB connection.
    store = StateStore(db_path)
    sub_store = SubscriberStore(store)

    async def _job(context) -> None:
        try:
            await asyncio.to_thread(
                run_delivery_cycle, config, router, store, sub_store, sender
            )
        except Exception:  # noqa: BLE001 — a job must never kill the bot
            logger.exception("delivery cycle failed; will retry next interval")

    application.job_queue.run_repeating(_job, interval=every_minutes * 60, first=10)
    logger.info("delivery loop ON: every %dm (sender=%s, dry_run=%s)",
                every_minutes, sender.name, sender.name == "console")


def build_application(
    token: str, db_path: str, *, config: AppConfig | None = None,
    secrets: Secrets | None = None, deliver: bool = True,
    delivery_minutes: int = DEFAULT_DELIVERY_MINUTES,
) -> Application:
    """Construct the Application with handlers + shared store. Network I/O (polling,
    post_init) happens in :func:`run_bot`. When ``deliver`` and config/secrets are
    supplied, the multi-user delivery job is registered too."""
    store = StateStore(db_path)
    application = Application.builder().token(token).post_init(_post_init).build()
    application.bot_data["state_store"] = store
    application.bot_data["sub_store"] = SubscriberStore(store)
    register_handlers(application)
    if deliver and config is not None and secrets is not None:
        _setup_delivery(application, db_path, config, secrets, every_minutes=delivery_minutes)
    return application


def run_bot(
    token: str, db_path: str, *, config: AppConfig | None = None,
    secrets: Secrets | None = None, deliver: bool = True,
    delivery_minutes: int = DEFAULT_DELIVERY_MINUTES,
) -> None:
    """Start long-polling. Blocks until interrupted (Ctrl-C / SIGTERM)."""
    application = build_application(
        token, db_path, config=config, secrets=secrets,
        deliver=deliver, delivery_minutes=delivery_minutes,
    )
    logger.info("starting long-polling…")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
