"""Async Telegram handlers — thin wrappers around :mod:`sportsup.bot.service`.

Each handler pulls the chat id off the Update, calls a synchronous service function,
and replies with a string from :mod:`sportsup.bot.texts`. The shared SubscriberStore
lives in ``context.application.bot_data['sub_store']`` (set up in :mod:`app`).
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from . import management, onboarding, service, settings_flow, texts
from .settings_flow import AWAITING_TZ

logger = logging.getLogger("sportsup.bot")

# Plain greetings that should behave like /start (case-insensitive, whole message).
_GREETING_RE = r"(?i)^\s*(hi|hii+|hey+|hello|yo|start)\s*$"

_STORE_KEY = "sub_store"


def _store(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data[_STORE_KEY]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    _sub, created = service.ensure_subscriber(_store(context), chat_id)
    logger.info("/start from %s (created=%s)", chat_id, created)
    await update.effective_message.reply_html(texts.welcome_text(created=created))
    # A brand-new user goes straight into guided setup; returning users get the
    # welcome above (they can re-run setup anytime with /subscribe).
    if created:
        await onboarding.start_onboarding(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_html(texts.help_text())


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask for confirmation before any destructive removal."""
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes, delete everything", callback_data="stop:confirm"),
        InlineKeyboardButton("Cancel", callback_data="stop:cancel"),
    ]])
    await update.effective_message.reply_html(texts.STOP_CONFIRM, reply_markup=keyboard)


async def on_stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat.id)
    if query.data == "stop:confirm":
        deleted = service.unsubscribe(_store(context), chat_id)
        logger.info("/stop confirmed by %s (deleted=%s)", chat_id, deleted)
        await query.edit_message_text(texts.STOP_DONE if deleted else texts.STOP_NOTHING)
    else:
        await query.edit_message_text(texts.STOP_CANCELLED)


async def on_greeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A bare 'hi'/'hello' is treated as /start (the brief asked for this)."""
    await cmd_start(update, context)


async def on_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(texts.UNKNOWN_COMMAND)


async def on_other_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # A stray text message is the timezone reply if we're expecting one, else a nudge.
    if context.user_data.get("awaiting") == AWAITING_TZ:
        await settings_flow.receive_timezone(update, context)
        return
    await update.effective_message.reply_text(texts.GREETING_FALLBACK)


async def rate_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs first for every update; drops (and warns once) when a chat floods us."""
    import time

    limiter = context.application.bot_data.get("ratelimiter")
    if limiter is None or update.effective_chat is None:
        return
    chat_id = str(update.effective_chat.id)
    if not limiter.check(chat_id, time.monotonic()):
        if limiter.just_tripped(chat_id) and update.effective_message is not None:
            await update.effective_message.reply_text(texts.RATE_LIMITED)
        raise ApplicationHandlerStop  # stop all further handlers for this update


def register_handlers(application) -> None:
    """Wire all handlers. The rate guard runs first (group -1). In the main group,
    specific command/callback/greeting handlers come before the catch-alls (one handler
    runs per update within a group, first match wins)."""
    application.add_handler(TypeHandler(Update, rate_guard), group=-1)

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("subscribe", onboarding.start_onboarding))
    application.add_handler(CommandHandler("mysubs", management.cmd_mysubs))
    application.add_handler(CommandHandler("edit", management.cmd_edit))
    application.add_handler(CommandHandler("settings", settings_flow.cmd_settings))
    application.add_handler(CommandHandler("unsubscribe", management.cmd_unsubscribe))
    application.add_handler(CommandHandler("pause", management.cmd_pause))
    application.add_handler(CommandHandler("resume", management.cmd_resume))
    application.add_handler(CommandHandler("stop", cmd_stop))

    application.add_handler(CallbackQueryHandler(on_stop_callback, pattern=r"^stop:"))
    application.add_handler(CallbackQueryHandler(onboarding.on_callback, pattern=r"^o:"))
    application.add_handler(CallbackQueryHandler(management.on_edit_callback, pattern=r"^e:"))
    application.add_handler(CallbackQueryHandler(management.on_unsub_callback, pattern=r"^u:"))
    application.add_handler(CallbackQueryHandler(settings_flow.on_settings_callback, pattern=r"^s:"))

    application.add_handler(MessageHandler(filters.Regex(_GREETING_RE) & ~filters.COMMAND, on_greeting))
    # Catch-alls (added last so real commands/greetings win).
    application.add_handler(MessageHandler(filters.COMMAND, on_unknown))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_other_text))
