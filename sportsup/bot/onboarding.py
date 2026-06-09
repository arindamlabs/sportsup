"""Interactive onboarding flow (Phase 9): tournaments → teams → options → confirm.

A single inline-keyboard conversation. Transient state lives in ``context.user_data``;
all the decision logic is in :mod:`sportsup.bot.onboarding_state` (pure, tested). Here we
only render keyboards, dispatch callbacks, fetch rosters, and persist on confirm.

Callback data is namespaced ``o:…`` and kept short (team picks reference a roster index,
not the name) to stay under Telegram's 64-byte limit.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from ..catalog import FREE_COMPETITIONS, competition_name, get_competition
from . import texts
from .onboarding_state import (
    LEAD_CHOICES,
    STEP_CONFIRM,
    STEP_OPTIONS,
    STEP_TEAMS,
    STEP_TOURNAMENTS,
    TEAM_PAGE_SIZE,
    OnboardingState,
    commit_onboarding,
)

logger = logging.getLogger("sportsup.bot")

_STATE_KEY = "onb"


def _check(on: bool) -> str:
    return "✅" if on else "▫️"


# --- keyboards -------------------------------------------------------------

def _kb_tournaments(st: OnboardingState) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{_check(c.code in st.tournaments)} {c.emoji} {c.name}",
                                  callback_data=f"o:t:{c.code}")]
            for c in FREE_COMPETITIONS]
    rows.append([InlineKeyboardButton("Next ▶", callback_data="o:tnext"),
                 InlineKeyboardButton("✖ Cancel", callback_data="o:cancel")])
    return InlineKeyboardMarkup(rows)


def _kb_teams(st: OnboardingState) -> InlineKeyboardMarkup:
    code = st.current_code
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(f"{_check(code in st.all_teams)} ⭐ All teams", callback_data="o:a")]
    ]
    start = st.page * TEAM_PAGE_SIZE
    row: list[InlineKeyboardButton] = []
    for i, name in enumerate(st.page_slice()):
        idx = start + i
        label = f"{_check(st.team_selected(code, name))} {name}"
        row.append(InlineKeyboardButton(label, callback_data=f"o:m:{idx}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if st.page_count() > 1:
        nav: list[InlineKeyboardButton] = []
        if st.page > 0:
            nav.append(InlineKeyboardButton("◀", callback_data=f"o:pg:{st.page - 1}"))
        nav.append(InlineKeyboardButton(f"{st.page + 1}/{st.page_count()}", callback_data="o:noop"))
        if st.page < st.page_count() - 1:
            nav.append(InlineKeyboardButton("▶", callback_data=f"o:pg:{st.page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("Done ▶", callback_data="o:tdone"),
                 InlineKeyboardButton("✖ Cancel", callback_data="o:cancel")])
    return InlineKeyboardMarkup(rows)


def _kb_options(st: OnboardingState) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{_check(st.reminders)} Match reminders", callback_data="o:or")],
        [InlineKeyboardButton(f"{_check(st.upsets)} Upsets / shock results", callback_data="o:ou")],
        [InlineKeyboardButton(f"{_check(st.finals)} Final scores", callback_data="o:of")],
    ]
    leads = [InlineKeyboardButton(f"{_check(val in st.leads)} {val}", callback_data=f"o:ld:{val}")
             for val, _ in LEAD_CHOICES]
    for i in range(0, len(leads), 3):
        rows.append(leads[i:i + 3])
    rows.append([InlineKeyboardButton("↺ Use defaults", callback_data="o:def")])
    rows.append([InlineKeyboardButton("Review ▶", callback_data="o:onext"),
                 InlineKeyboardButton("✖ Cancel", callback_data="o:cancel")])
    return InlineKeyboardMarkup(rows)


def _kb_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data="o:save"),
        InlineKeyboardButton("✖ Cancel", callback_data="o:cancel"),
    ]])


def _render(st: OnboardingState) -> tuple[str, InlineKeyboardMarkup]:
    if st.step == STEP_TOURNAMENTS:
        return texts.ONB_TOURNAMENTS, _kb_tournaments(st)
    if st.step == STEP_TEAMS:
        name = competition_name(st.current_code or "")
        return texts.onb_teams_text(name, has_roster=bool(st.current_roster())), _kb_teams(st)
    if st.step == STEP_OPTIONS:
        return texts.ONB_OPTIONS, _kb_options(st)
    return texts.onb_confirm_text(st.summary_lines()), _kb_confirm()


# --- roster IO -------------------------------------------------------------

async def _ensure_roster(context: ContextTypes.DEFAULT_TYPE, st: OnboardingState) -> None:
    """Fetch the current tournament's team roster once (network -> worker thread)."""
    code = st.current_code
    if code is None or code in st.rosters:
        return
    router = context.application.bot_data.get("router")
    comp = get_competition(code)
    names: list[str] = []
    if router is not None and comp is not None:
        try:
            teams = await asyncio.to_thread(
                router.get_teams, competition_code=code, season=comp.season
            )
            names = sorted(t.name for t in teams)
        except Exception:  # noqa: BLE001 — roster is best-effort; fall back to All-teams
            logger.warning("roster fetch failed for %s; offering All-teams only", code)
    st.rosters[code] = names


# --- entry + dispatch ------------------------------------------------------

async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Launch (or restart) the guided setup. Used by /subscribe and new-user /start."""
    context.user_data[_STATE_KEY] = OnboardingState()
    text, kb = _render(context.user_data[_STATE_KEY])
    await update.effective_message.reply_html(text, reply_markup=kb)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    st: OnboardingState | None = context.user_data.get(_STATE_KEY)
    data = query.data

    if st is None:
        await query.answer(texts.ONB_EXPIRED, show_alert=True)
        return

    # Terminal / validation branches answer once and return.
    if data == "o:noop":
        await query.answer()
        return
    if data == "o:cancel":
        await query.answer("Cancelled")
        context.user_data.pop(_STATE_KEY, None)
        await query.edit_message_text(texts.ONB_CANCELLED, parse_mode="HTML")
        return
    if data == "o:tnext" and not st.tournaments:
        await query.answer("Pick at least one tournament first.", show_alert=True)
        return
    if data == "o:save":
        if not st.has_any_subscription():
            await query.answer("No teams selected yet — pick some first.", show_alert=True)
            return
        await query.answer("Saved ✅")
        sub_store = context.application.bot_data["sub_store"]
        chat_id = str(update.effective_chat.id)
        commit_onboarding(sub_store, chat_id, st)   # local sqlite write — fast, no thread
        n = len(sub_store.list_subscriptions(chat_id))
        context.user_data.pop(_STATE_KEY, None)
        logger.info("onboarding complete for %s (%d subscriptions)", chat_id, n)
        await query.edit_message_text(texts.onboarding_done_text(n), parse_mode="HTML")
        return

    await _apply(st, data, context)
    await query.answer()
    text, kb = _render(st)
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest:
        pass  # "message is not modified" — harmless when the visible state didn't change


async def _apply(st: OnboardingState, data: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mutate state for a (non-terminal) callback, loading rosters as steps advance."""
    if data.startswith("o:t:"):
        st.toggle_tournament(data[4:])
    elif data == "o:tnext":
        st.step = STEP_TEAMS
        st.team_index = 0
        st.page = 0
        await _ensure_roster(context, st)
    elif data == "o:a" and st.current_code:
        st.toggle_all_teams(st.current_code)
    elif data.startswith("o:m:"):
        idx = int(data[4:])
        roster = st.current_roster()
        if st.current_code and 0 <= idx < len(roster):
            st.toggle_team(st.current_code, roster[idx])
    elif data.startswith("o:pg:"):
        st.set_page(int(data[5:]))
    elif data == "o:tdone":
        if st.advance_team():
            await _ensure_roster(context, st)
        else:
            st.step = STEP_OPTIONS
    elif data == "o:or":
        st.toggle_option("reminders")
    elif data == "o:ou":
        st.toggle_option("upsets")
    elif data == "o:of":
        st.toggle_option("finals")
    elif data.startswith("o:ld:"):
        st.toggle_lead(data[5:])
    elif data == "o:def":
        st.use_defaults()
    elif data == "o:onext":
        st.step = STEP_CONFIRM
