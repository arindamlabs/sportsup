"""Phase 9 tests: onboarding state machine + persistence (+ a light async path)."""

import asyncio
from types import SimpleNamespace

from sportsup.bot import onboarding
from sportsup.bot.onboarding_state import OnboardingState, commit_onboarding
from sportsup.state import StateStore
from sportsup.subscribers import ALL_TEAMS, SubscriberStore, watchlist_for


def _store(tmp_path) -> SubscriberStore:
    return SubscriberStore(StateStore(tmp_path / "s.sqlite"))


# --- state transitions -----------------------------------------------------

def test_tournament_toggle_add_remove_clears_teams():
    st = OnboardingState()
    st.toggle_tournament("PL")
    st.toggle_tournament("WC")
    assert st.tournaments == ["PL", "WC"]
    st.toggle_team("PL", "Arsenal")
    st.toggle_tournament("PL")               # de-select PL
    assert st.tournaments == ["WC"]
    assert "PL" not in st.teams              # its team picks were dropped


def test_all_teams_and_explicit_are_mutually_exclusive():
    st = OnboardingState()
    st.toggle_tournament("WC")
    st.rosters["WC"] = ["Brazil", "Japan"]
    st.toggle_team("WC", "Brazil")
    assert st.has_selection_for("WC")
    st.toggle_all_teams("WC")                # all-teams supersedes explicit picks
    assert "WC" in st.all_teams and not st.teams.get("WC")
    st.toggle_team("WC", "Japan")            # picking again clears all-teams
    assert "WC" not in st.all_teams and st.team_selected("WC", "Japan")


def test_pagination_bounds():
    st = OnboardingState()
    st.tournaments = ["PL"]
    st.rosters["PL"] = [f"Team{i}" for i in range(20)]   # 3 pages of 8
    assert st.page_count() == 3
    assert len(st.page_slice()) == 8
    st.set_page(99)
    assert st.page == 2 and len(st.page_slice()) == 4     # clamped + remainder
    st.set_page(-5)
    assert st.page == 0


def test_advance_team_walks_selected_then_stops():
    st = OnboardingState()
    st.tournaments = ["PL", "WC"]
    assert st.current_code == "PL"
    assert st.advance_team() is True and st.current_code == "WC"
    assert st.advance_team() is False and st.current_code is None  # done


def test_options_toggles_and_defaults():
    st = OnboardingState()
    st.toggle_option("finals")
    st.toggle_option("upsets")
    st.toggle_lead("1d")                     # remove default 1d
    assert st.finals is True and st.upsets is False and "1d" not in st.leads
    st.use_defaults()
    assert (st.reminders, st.upsets, st.finals) == (True, True, False)
    assert st.leads == ["1d", "1h"]


# --- persistence -----------------------------------------------------------

def test_commit_writes_subscriber_and_subscriptions(tmp_path):
    store = _store(tmp_path)
    st = OnboardingState()
    st.tournaments = ["WC", "PL"]
    st.rosters = {"WC": ["Brazil", "Japan"], "PL": ["Arsenal"]}
    st.toggle_team("WC", "Brazil")
    st.toggle_team("WC", "Japan")
    st.toggle_all_teams("PL")                # follow all of the PL
    st.finals = True
    st.leads = ["3h"]

    commit_onboarding(store, "777", st)
    sub = store.get_subscriber("777")
    assert sub is not None and sub.finals_enabled is True and sub.lead_times == ["3h"]
    subs = store.list_subscriptions("777")
    assert sorted(watchlist_for(subs, "WC", 2026)) == ["Brazil", "Japan"]
    assert watchlist_for(subs, "PL", 2026) == []          # ALL_TEAMS -> empty = all
    assert any(s.team == ALL_TEAMS for s in subs if s.competition_code == "PL")


def test_commit_skips_tournaments_with_no_pick(tmp_path):
    store = _store(tmp_path)
    st = OnboardingState()
    st.tournaments = ["WC", "PL"]
    st.rosters = {"WC": ["Brazil"]}
    st.toggle_team("WC", "Brazil")           # PL left with no selection
    commit_onboarding(store, "777", st)
    subs = store.list_subscriptions("777")
    assert {s.competition_code for s in subs} == {"WC"}   # PL skipped (nothing chosen)


def test_has_any_subscription():
    st = OnboardingState()
    st.tournaments = ["WC"]
    assert st.has_any_subscription() is False
    st.toggle_all_teams("WC")
    assert st.has_any_subscription() is True


# --- async flow (lightweight fakes, no roster network) ---------------------

class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answered = None
        self.edited = None
        self.message = SimpleNamespace(chat=SimpleNamespace(id=900))

    async def answer(self, text=None, show_alert=False):
        self.answered = text or "ok"

    async def edit_message_text(self, text, **kw):
        self.edited = text


class FakeCtx:
    def __init__(self, store):
        self.user_data = {}
        self.application = SimpleNamespace(bot_data={"sub_store": store, "router": None})


def test_callback_flow_select_and_save(tmp_path):
    store = _store(tmp_path)
    ctx = FakeCtx(store)
    # Seed state directly (skip the message reply of start_onboarding).
    st = OnboardingState()
    ctx.user_data["onb"] = st

    async def fire(data):
        upd = SimpleNamespace(callback_query=FakeQuery(data),
                              effective_chat=SimpleNamespace(id=900))
        await onboarding.on_callback(upd, ctx)
        return upd.callback_query

    # Pick a tournament, advance (router=None -> empty roster), choose All-teams, save.
    asyncio.run(_drive(fire))
    subs = store.list_subscriptions("900")
    assert subs and subs[0].competition_code == "WC"
    assert store.get_subscriber("900") is not None


async def _drive(fire):
    await fire("o:t:WC")        # select WC
    await fire("o:tnext")       # -> teams step (roster empty since router None)
    await fire("o:a")           # All teams
    await fire("o:tdone")       # -> options
    await fire("o:onext")       # -> confirm
    q = await fire("o:save")    # persist
    assert "all set" in (q.edited or "")


def test_callback_requires_a_pick_to_save(tmp_path):
    store = _store(tmp_path)
    ctx = FakeCtx(store)
    ctx.user_data["onb"] = OnboardingState(step="confirm", tournaments=["WC"])

    async def go():
        q = FakeQuery("o:save")
        upd = SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=900))
        await onboarding.on_callback(upd, ctx)
        return q

    q = asyncio.run(go())
    assert "No teams selected" in (q.answered or "")
    assert store.get_subscriber("900") is None    # nothing saved
