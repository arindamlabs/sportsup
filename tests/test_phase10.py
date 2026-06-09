"""Phase 10 tests: odds budget, rate limiter, management commands, settings flow."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sportsup.bot import management, settings_flow
from sportsup.bot.ratelimit import RateLimiter
from sportsup.config import AppConfig
from sportsup.fanout import plan_for_all_subscribers
from sportsup.odds_budget import OddsBudget
from sportsup.providers import MatchResult, MatchStatus, Score, Standing, TeamRef, Fixture
from sportsup.state import StateStore
from sportsup.subscribers import ALL_TEAMS, Subscriber, SubscriberStore

UTC = timezone.utc
NOW = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)


# --- odds budget -----------------------------------------------------------

def test_odds_budget_caps_and_resets_per_day(tmp_path):
    store = StateStore(tmp_path / "s.sqlite")
    b = OddsBudget(store, daily_cap=3)
    assert b.remaining(NOW) == 3
    assert all(b.try_consume(NOW) for _ in range(3))
    assert b.try_consume(NOW) is False          # cap reached
    assert b.remaining(NOW) == 0
    assert b.try_consume(NOW + timedelta(days=1)) is True   # new UTC day resets


class _OddsRouter:
    def __init__(self, results, standings):
        self._results, self._standings = results, standings
        self.odds_calls = 0

    def get_fixtures(self, **kw):
        return []

    def get_results(self, *, competition_code, season, **kw):
        return list(self._results.get((competition_code, season), []))

    def get_standings(self, *, competition_code, season):
        return list(self._standings.get((competition_code, season), []))

    def get_match_odds(self, **kw):
        self.odds_calls += 1
        return None


def test_fanout_skips_odds_when_budget_exhausted(tmp_path):
    store = StateStore(tmp_path / "s.sqlite")
    subs = SubscriberStore(store)
    subs.upsert_subscriber(Subscriber(chat_id="u1", reminders_enabled=False,
                                      upsets_enabled=True, finals_enabled=False))
    subs.add_subscription("u1", "WC", 2026, "Brazil")
    fx = Fixture(provider="f", provider_fixture_id="1", competition_code="WC", season=2026,
                 utc_kickoff=NOW - timedelta(hours=2), status=MatchStatus.FINISHED,
                 home=TeamRef(name="Brazil"), away=TeamRef(name="Serbia"))
    router = _OddsRouter(
        results={("WC", 2026): [MatchResult.from_fixture(fx, Score(home=0, away=1))]},
        standings={("WC", 2026): [Standing(team=TeamRef(name="Brazil"), position=1),
                                  Standing(team=TeamRef(name="Serbia"), position=20)]},
    )
    base = AppConfig.model_validate({"timezone": "UTC", "delivery": {"provider": "console"}})
    budget = OddsBudget(store, daily_cap=0)            # no odds calls allowed
    plan_for_all_subscribers(base, router, store, subs, now=NOW, odds_budget=budget)
    assert router.odds_calls == 0                     # fell back to standings, no API spend


# --- rate limiter ----------------------------------------------------------

def test_rate_limiter_window_and_isolation():
    rl = RateLimiter(max_per_window=3, window_seconds=60)
    assert all(rl.check("a", 100.0) for _ in range(3))
    assert rl.check("a", 100.0) is False              # 4th in window denied
    assert rl.just_tripped("a") is True               # warn exactly once
    assert rl.check("a", 100.0) is False
    assert rl.just_tripped("a") is False              # not again
    assert rl.check("b", 100.0) is True               # other chat unaffected
    assert rl.check("a", 200.0) is True               # window rolled over


# --- shared fakes for handlers ---------------------------------------------

class FakeMsg:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_html(self, t, **k):
        self.replies.append(t)

    async def reply_text(self, t, **k):
        self.replies.append(t)


class FakeUpdate:
    def __init__(self, chat_id, text=""):
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.effective_message = FakeMsg(text)


class FakeQuery:
    def __init__(self, data, chat_id):
        self.data = data
        self.answered = None
        self.edited = None
        self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id))

    async def answer(self, text=None, show_alert=False):
        self.answered = text or "ok"

    async def edit_message_text(self, t, **k):
        self.edited = t


class CbUpdate:
    def __init__(self, data, chat_id):
        self.callback_query = FakeQuery(data, chat_id)
        self.effective_chat = SimpleNamespace(id=chat_id)


class FakeCtx:
    def __init__(self, sub_store):
        self.user_data = {}
        self.application = SimpleNamespace(bot_data={"sub_store": sub_store})


def _store(tmp_path):
    return SubscriberStore(StateStore(tmp_path / "s.sqlite"))


# --- /pause /resume /mysubs ------------------------------------------------

def test_pause_resume_cycle(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="100"))
    ctx = FakeCtx(st)

    upd = FakeUpdate(100)
    asyncio.run(management.cmd_pause(upd, ctx))
    assert st.get_subscriber("100").status == "paused"

    upd2 = FakeUpdate(100)
    asyncio.run(management.cmd_pause(upd2, ctx))      # already paused
    assert "already paused" in upd2.effective_message.replies[0]

    upd3 = FakeUpdate(100)
    asyncio.run(management.cmd_resume(upd3, ctx))
    assert st.get_subscriber("100").status == "active"


def test_mysubs_render_lists_everything(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="100", timezone="Asia/Tokyo"))
    st.add_subscription("100", "WC", 2026, "Brazil")
    st.add_subscription("100", "PL", 2026, ALL_TEAMS)
    text = management.render_mysubs(st, st.get_subscriber("100"))
    assert "Brazil" in text and "all teams" in text and "Asia/Tokyo" in text


# --- granular /unsubscribe -------------------------------------------------

def test_unsubscribe_removes_single_team(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="100"))
    st.add_subscription("100", "PL", 2026, "Arsenal")
    st.add_subscription("100", "PL", 2026, "Chelsea")
    ctx = FakeCtx(st)

    asyncio.run(management.on_unsub_callback(CbUpdate("u:c:PL:2026", 100), ctx))  # open PL
    assert ctx.user_data["unsub"]["teams"] == ["Arsenal", "Chelsea"]
    asyncio.run(management.on_unsub_callback(CbUpdate("u:t:0", 100), ctx))        # remove Arsenal
    teams = [s.team for s in st.list_subscriptions("100")]
    assert teams == ["Chelsea"]


def test_unsubscribe_removes_whole_competition(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="100"))
    st.add_subscription("100", "PL", 2026, "Arsenal")
    st.add_subscription("100", "WC", 2026, "Brazil")
    ctx = FakeCtx(st)
    asyncio.run(management.on_unsub_callback(CbUpdate("u:all:PL:2026", 100), ctx))
    assert {s.competition_code for s in st.list_subscriptions("100")} == {"WC"}


# --- /edit -----------------------------------------------------------------

def test_edit_toggles_and_saves(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="100", finals_enabled=False))
    ctx = FakeCtx(st)
    asyncio.run(management.cmd_edit(FakeUpdate(100), ctx))    # seed edit state
    asyncio.run(management.on_edit_callback(CbUpdate("e:of", 100), ctx))   # toggle finals on
    asyncio.run(management.on_edit_callback(CbUpdate("e:save", 100), ctx))
    assert st.get_subscriber("100").finals_enabled is True


# --- /settings -------------------------------------------------------------

def test_settings_timezone_valid_and_invalid(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="100", timezone="UTC"))
    ctx = FakeCtx(st)
    ctx.user_data["awaiting"] = "tz"

    asyncio.run(settings_flow.receive_timezone(FakeUpdate(100, "Asia/Tokyo"), ctx))
    assert st.get_subscriber("100").timezone == "Asia/Tokyo"
    assert "awaiting" not in ctx.user_data

    ctx.user_data["awaiting"] = "tz"
    bad = FakeUpdate(100, "Not/AZone")
    asyncio.run(settings_flow.receive_timezone(bad, ctx))
    assert st.get_subscriber("100").timezone == "Asia/Tokyo"     # unchanged
    assert "valid timezone" in bad.effective_message.replies[0]


def test_settings_quiet_hours_preset(tmp_path):
    st = _store(tmp_path)
    st.upsert_subscriber(Subscriber(chat_id="100", quiet_enabled=False))
    ctx = FakeCtx(st)
    asyncio.run(settings_flow.on_settings_callback(CbUpdate("s:qh:23:00-08:00", 100), ctx))
    sub = st.get_subscriber("100")
    assert sub.quiet_enabled and sub.quiet_start == "23:00" and sub.quiet_end == "08:00"
