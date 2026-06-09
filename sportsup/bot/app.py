"""Build and run the PTB Application (long polling).

No webhook / public URL needed — `getUpdates` long-polling works fine on the
outbound-only Oracle VM. The SubscriberStore is created once and shared via
``bot_data``; ``post_init`` registers the command menu so commands autocomplete
in the chat.
"""

from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.ext import Application

from ..state import StateStore
from ..subscribers import SubscriberStore
from .handlers import register_handlers
from .texts import active_commands

logger = logging.getLogger("sportsup.bot")


async def _post_init(application: Application) -> None:
    """Register the in-chat command menu from the catalog (active commands only)."""
    await application.bot.set_my_commands(
        [BotCommand(c.name, c.description) for c in active_commands()]
    )
    me = await application.bot.get_me()
    logger.info("bot @%s ready; %d command(s) registered", me.username, len(active_commands()))


def build_application(token: str, db_path: str) -> Application:
    """Construct the Application with handlers + shared store. Does no network I/O
    (so it's safe to build in tests); polling/post_init happen in :func:`run_bot`."""
    store = StateStore(db_path)
    application = Application.builder().token(token).post_init(_post_init).build()
    application.bot_data["state_store"] = store
    application.bot_data["sub_store"] = SubscriberStore(store)
    register_handlers(application)
    return application


def run_bot(token: str, db_path: str) -> None:
    """Start long-polling. Blocks until interrupted (Ctrl-C / SIGTERM)."""
    application = build_application(token, db_path)
    logger.info("starting long-polling…")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
