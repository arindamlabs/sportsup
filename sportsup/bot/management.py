"""Management commands (Phase 10): /mysubs, /pause, /resume, /edit, /unsubscribe.

Read-and-tweak commands over the SubscriberStore. /edit reuses the onboarding options
state for alert types + lead-times; /unsubscribe is the granular counterpart to /stop —
remove one team or a whole tournament, leaving the rest intact. Team removal references
a stored index (kept in user_data) so callback data stays short.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from ..catalog import competition_name
from ..subscribers import ALL_TEAMS
from . import texts
from .onboarding_state import DEFAULT_LEADS, LEAD_CHOICES, OnboardingState

logger = logging.getLogger("sportsup.bot")


def _store(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["sub_store"]


def _btn(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=data)


def _chk(on: bool) -> str:
    return "✅" if on else "▫️"


# --- /mysubs ---------------------------------------------------------------

def render_mysubs(store, sub) -> str:
    lines = [f"📋 <b>Your SportsUp</b> — {'⏸️ paused' if not sub.is_active else '✅ active'}", ""]
    subs = store.list_subscriptions(sub.chat_id)
    if not subs:
        lines.append("No teams yet — send /subscribe to add some.")
    else:
        for code, season in sorted({(s.competition_code, s.season) for s in subs}):
            teams = [s.team for s in subs if s.competition_code == code and s.season == season]
            who = "all teams" if ALL_TEAMS in teams else ", ".join(sorted(teams))
            lines.append(f"• <b>{competition_name(code)}</b>: {who}")
    types = [n for n, on in (("reminders", sub.reminders_enabled), ("upsets", sub.upsets_enabled),
                             ("final scores", sub.finals_enabled)) if on]
    lines += ["", f"🔔 Alerts: {', '.join(types) or 'none'}"]
    if sub.reminders_enabled:
        lines.append(f"⏰ Reminders: {', '.join(sub.lead_times) or '(none)'}")
    lines.append(f"🕑 Timezone: {sub.timezone}")
    lines.append(f"🌙 Quiet hours: {sub.quiet_start}–{sub.quiet_end}" if sub.quiet_enabled
                 else "🌙 Quiet hours: off")
    lines += ["", "Manage: /edit · /settings · /unsubscribe · /pause"]
    return "\n".join(lines)


async def cmd_mysubs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sub = _store(context).get_subscriber(str(update.effective_chat.id))
    if sub is None:
        await update.effective_message.reply_html(texts.NOT_SUBSCRIBED)
        return
    await update.effective_message.reply_html(render_mysubs(_store(context), sub))


# --- /pause and /resume ----------------------------------------------------

async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store, chat_id = _store(context), str(update.effective_chat.id)
    sub = store.get_subscriber(chat_id)
    if sub is None:
        await update.effective_message.reply_html(texts.NOT_SUBSCRIBED)
    elif not sub.is_active:
        await update.effective_message.reply_html(texts.ALREADY_PAUSED)
    else:
        store.set_status(chat_id, "paused")
        await update.effective_message.reply_html(texts.PAUSED)


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store, chat_id = _store(context), str(update.effective_chat.id)
    sub = store.get_subscriber(chat_id)
    if sub is None:
        await update.effective_message.reply_html(texts.NOT_SUBSCRIBED)
    elif sub.is_active:
        await update.effective_message.reply_html(texts.ALREADY_ACTIVE)
    else:
        store.set_status(chat_id, "active")
        await update.effective_message.reply_html(texts.RESUMED)


# --- /edit (alert types + lead-times) --------------------------------------

def _kb_edit(st: OnboardingState) -> InlineKeyboardMarkup:
    rows = [
        [_btn(f"{_chk(st.reminders)} Match reminders", "e:or")],
        [_btn(f"{_chk(st.upsets)} Upsets / shock results", "e:ou")],
        [_btn(f"{_chk(st.finals)} Final scores", "e:of")],
    ]
    leads = [_btn(f"{_chk(v in st.leads)} {v}", f"e:ld:{v}") for v, _ in LEAD_CHOICES]
    for i in range(0, len(leads), 3):
        rows.append(leads[i:i + 3])
    rows.append([_btn("↺ Use defaults", "e:def")])
    rows.append([_btn("✅ Save", "e:save"), _btn("✖ Cancel", "e:cancel")])
    return InlineKeyboardMarkup(rows)


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sub = _store(context).get_subscriber(str(update.effective_chat.id))
    if sub is None:
        await update.effective_message.reply_html(texts.NOT_SUBSCRIBED)
        return
    st = OnboardingState(reminders=sub.reminders_enabled, upsets=sub.upsets_enabled,
                         finals=sub.finals_enabled, leads=list(sub.lead_times))
    context.user_data["edit"] = st
    await update.effective_message.reply_html(texts.EDIT_TITLE, reply_markup=_kb_edit(st))


async def on_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    st: OnboardingState | None = context.user_data.get("edit")
    data = query.data
    if st is None:
        await query.answer(texts.ONB_EXPIRED, show_alert=True)
        return
    if data == "e:cancel":
        await query.answer("Cancelled")
        context.user_data.pop("edit", None)
        await query.edit_message_text(texts.EDIT_NUDGE)
        return
    if data == "e:save":
        await query.answer("Saved ✅")
        store = _store(context)
        sub = store.get_subscriber(str(update.effective_chat.id))
        if sub is not None:
            sub.reminders_enabled, sub.upsets_enabled, sub.finals_enabled = st.reminders, st.upsets, st.finals
            sub.lead_times = st.leads or list(DEFAULT_LEADS)
            store.upsert_subscriber(sub)
        context.user_data.pop("edit", None)
        await query.edit_message_text(texts.EDIT_SAVED)
        return

    if data == "e:or":
        st.toggle_option("reminders")
    elif data == "e:ou":
        st.toggle_option("upsets")
    elif data == "e:of":
        st.toggle_option("finals")
    elif data.startswith("e:ld:"):
        st.toggle_lead(data[5:])
    elif data == "e:def":
        st.use_defaults()
    await query.answer()
    try:
        await query.edit_message_text(texts.EDIT_TITLE, reply_markup=_kb_edit(st), parse_mode="HTML")
    except BadRequest:
        pass


# --- /unsubscribe (granular) -----------------------------------------------

def _kb_competitions(store, chat_id: str) -> InlineKeyboardMarkup:
    subs = store.list_subscriptions(chat_id)
    rows = []
    for code, season in sorted({(s.competition_code, s.season) for s in subs}):
        teams = [s.team for s in subs if s.competition_code == code and s.season == season]
        count = "all teams" if ALL_TEAMS in teams else f"{len(teams)} team(s)"
        rows.append([_btn(f"{competition_name(code)} — {count}", f"u:c:{code}:{season}")])
    rows.append([_btn("Done", "u:done")])
    return InlineKeyboardMarkup(rows)


def _comp_view(store, chat_id: str, code: str, season: int, context) -> tuple[str, InlineKeyboardMarkup]:
    teams = sorted(s.team for s in store.list_subscriptions(chat_id)
                   if s.competition_code == code and s.season == season)
    context.user_data["unsub"] = {"code": code, "season": season, "teams": teams}
    rows: list[list[InlineKeyboardButton]] = []
    if ALL_TEAMS in teams:
        text = f"⚽ <b>{competition_name(code)}</b> — following <b>all teams</b>."
    else:
        text = f"⚽ <b>{competition_name(code)}</b> — tap a team to remove it."
        row: list[InlineKeyboardButton] = []
        for idx, name in enumerate(teams):
            row.append(_btn(f"✖ {name}", f"u:t:{idx}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    rows.append([_btn("🗑 Remove whole tournament", f"u:all:{code}:{season}")])
    rows.append([_btn("◀ Back", "u:back"), _btn("Done", "u:done")])
    return text, InlineKeyboardMarkup(rows)


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store, chat_id = _store(context), str(update.effective_chat.id)
    if not store.list_subscriptions(chat_id):
        await update.effective_message.reply_html(texts.UNSUB_NONE)
        return
    await update.effective_message.reply_html(
        texts.UNSUB_TITLE, reply_markup=_kb_competitions(store, chat_id))


async def _show_list_or_empty(query, store, chat_id: str, context) -> None:
    """After a removal, show the competition list again — or a friendly empty state."""
    if not store.list_subscriptions(chat_id):
        context.user_data.pop("unsub", None)
        await query.edit_message_text(texts.UNSUB_EMPTY)
    else:
        await query.edit_message_text(texts.UNSUB_TITLE, parse_mode="HTML",
                                      reply_markup=_kb_competitions(store, chat_id))


async def on_unsub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    store, chat_id = _store(context), str(update.effective_chat.id)

    if data == "u:done":
        await query.answer()
        context.user_data.pop("unsub", None)
        await query.edit_message_text(texts.UNSUB_DONE)
        return
    if data == "u:back":
        await query.answer()
        await _show_list_or_empty(query, store, chat_id, context)
        return
    if data.startswith("u:c:"):
        _, _, code, season = data.split(":")
        await query.answer()
        text, kb = _comp_view(store, chat_id, code, int(season), context)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return
    if data.startswith("u:all:"):
        _, _, code, season = data.split(":")
        store.remove_subscription(chat_id, code, int(season))
        logger.info("%s removed whole competition %s/%s", chat_id, code, season)
        await query.answer("Removed")
        await _show_list_or_empty(query, store, chat_id, context)
        return
    if data.startswith("u:t:"):
        view = context.user_data.get("unsub")
        if not view:
            await query.answer(texts.ONB_EXPIRED, show_alert=True)
            return
        idx = int(data[4:])
        code, season, teams = view["code"], view["season"], view["teams"]
        if 0 <= idx < len(teams):
            store.remove_subscription(chat_id, code, season, teams[idx])
        await query.answer("Removed")
        remaining = [s for s in store.list_subscriptions(chat_id)
                     if s.competition_code == code and s.season == season]
        if not remaining:
            await _show_list_or_empty(query, store, chat_id, context)
            return
        text, kb = _comp_view(store, chat_id, code, season, context)
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except BadRequest:
            pass
