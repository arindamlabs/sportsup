"""/settings (Phase 10): per-user timezone and quiet hours.

Timezone is entered as a free-text IANA name (validated against ZoneInfo) — far more
flexible than a button list and what power users expect. Quiet hours are picked from a
few presets (or turned off). Both are stored on the subscriber and already honoured by
the delivery loop via ``effective_config`` (per-user tz + quiet-hours classification).
"""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from . import texts

logger = logging.getLogger("sportsup.bot")

# (start, end) quiet-hour windows offered as presets.
QH_PRESETS: list[tuple[str, str]] = [("22:00", "07:00"), ("23:00", "08:00"), ("00:00", "06:00")]
AWAITING_TZ = "tz"


def _store(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["sub_store"]


def settings_text(sub) -> str:
    qh = f"{sub.quiet_start}–{sub.quiet_end}" if sub.quiet_enabled else "off"
    return ("⚙️ <b>Settings</b>\n\n"
            f"🕑 Timezone: <b>{sub.timezone}</b>\n"
            f"🌙 Quiet hours: <b>{qh}</b>\n\n"
            "Alerts are sent in your timezone and held during quiet hours.")


def _settings_kb(sub) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("🕑 Change timezone", callback_data="s:tz")]]
    for start, end in QH_PRESETS:
        on = sub.quiet_enabled and sub.quiet_start == start and sub.quiet_end == end
        mark = "✅ " if on else ""
        rows.append([InlineKeyboardButton(f"{mark}🌙 Quiet {start}–{end}",
                                          callback_data=f"s:qh:{start}-{end}")])
    off = "✅ " if not sub.quiet_enabled else ""
    rows.append([InlineKeyboardButton(f"{off}☀️ Quiet hours off", callback_data="s:qhoff")])
    rows.append([InlineKeyboardButton("Done", callback_data="s:done")])
    return InlineKeyboardMarkup(rows)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sub = _store(context).get_subscriber(str(update.effective_chat.id))
    if sub is None:
        await update.effective_message.reply_html(texts.NOT_SUBSCRIBED)
        return
    await update.effective_message.reply_html(settings_text(sub), reply_markup=_settings_kb(sub))


async def on_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    store, chat_id = _store(context), str(update.effective_chat.id)
    sub = store.get_subscriber(chat_id)
    if sub is None:
        await query.answer(texts.NOT_SUBSCRIBED, show_alert=True)
        return

    if data == "s:done":
        await query.answer()
        context.user_data.pop("awaiting", None)
        await query.edit_message_text(texts.SET_DONE)
        return
    if data == "s:tz":
        await query.answer()
        context.user_data["awaiting"] = AWAITING_TZ
        await query.edit_message_text(texts.SET_TZ_PROMPT, parse_mode="HTML")
        return
    if data == "s:qhoff":
        sub.quiet_enabled = False
        store.upsert_subscriber(sub)
        await query.answer("Quiet hours off")
    elif data.startswith("s:qh:"):
        start, end = data[len("s:qh:"):].split("-")
        sub.quiet_enabled, sub.quiet_start, sub.quiet_end = True, start, end
        store.upsert_subscriber(sub)
        await query.answer("Saved")

    sub = store.get_subscriber(chat_id)
    try:
        await query.edit_message_text(settings_text(sub), reply_markup=_settings_kb(sub),
                                      parse_mode="HTML")
    except BadRequest:
        pass


async def receive_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the text reply after the user tapped 'Change timezone'."""
    tz = (update.effective_message.text or "").strip()
    try:
        ZoneInfo(tz)
    except Exception:  # noqa: BLE001 — any failure means an unusable tz name
        await update.effective_message.reply_html(texts.SET_TZ_INVALID)
        return
    store, chat_id = _store(context), str(update.effective_chat.id)
    sub = store.get_subscriber(chat_id)
    if sub is not None:
        sub.timezone = tz
        store.upsert_subscriber(sub)
    context.user_data.pop("awaiting", None)
    logger.info("%s set timezone to %s", chat_id, tz)
    await update.effective_message.reply_html(
        f"✅ Timezone set to <b>{tz}</b>. Use /settings to adjust quiet hours.")
