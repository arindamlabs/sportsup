"""Phase 8 tests: command catalog, /help completeness, service logic, async handlers."""

import asyncio
from types import SimpleNamespace

from sportsup.bot import handlers, service, texts
from sportsup.bot.app import build_application
from sportsup.state import StateStore
from sportsup.subscribers import SubscriberStore


# --- texts / command catalog ----------------------------------------------

def test_help_lists_every_command():
    body = texts.help_text()
    for c in texts.COMMANDS:
        assert f"/{c.name}" in body          # every command appears in /help
    # The three explicitly-required commands are present.
    for required in ("/start", "/help", "/stop"):
        assert required in body


def test_active_commands_are_the_wired_ones():
    assert {c.name for c in texts.active_commands()} == {"start", "help", "subscribe", "stop"}
    # Inactive ones are flagged "coming soon" in /help, not in the menu.
    assert "coming soon" in texts.help_text()


# --- service logic ---------------------------------------------------------

def _store(tmp_path) -> SubscriberStore:
    return SubscriberStore(StateStore(tmp_path / "s.sqlite"))


def test_ensure_subscriber_creates_then_idempotent(tmp_path):
    st = _store(tmp_path)
    sub, created = service.ensure_subscriber(st, "100")
    assert created is True and sub.chat_id == "100"
    sub2, created2 = service.ensure_subscriber(st, "100")
    assert created2 is False
    assert len(st.list_subscribers()) == 1


def test_unsubscribe_cascades_and_handles_absent(tmp_path):
    st = _store(tmp_path)
    service.ensure_subscriber(st, "100")
    st.add_subscription("100", "PL", 2026, "Arsenal")
    assert service.unsubscribe(st, "100") is True
    assert st.get_subscriber("100") is None
    assert st.list_subscriptions("100") == []         # cascade
    assert service.unsubscribe(st, "100") is False     # nothing left


# --- async handlers (lightweight fakes, no network) ------------------------

class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_html(self, text, **kw):
        self.replies.append(text)

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, chat_id):
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.effective_message = FakeMessage()


class FakeContext:
    def __init__(self, store):
        self.application = SimpleNamespace(bot_data={"sub_store": store, "router": None})
        self.user_data = {}


class FakeCallbackUpdate:
    def __init__(self, data, chat_id):
        self.edited = None
        self.answered = False
        outer = self

        class _Q:
            data = None
            message = SimpleNamespace(chat=SimpleNamespace(id=chat_id))

            async def answer(self_):
                outer.answered = True

            async def edit_message_text(self_, text, **kw):
                outer.edited = text

        self.callback_query = _Q()
        self.callback_query.data = data


def test_cmd_start_registers_and_welcomes(tmp_path):
    st = _store(tmp_path)
    upd, ctx = FakeUpdate(100), FakeContext(st)
    asyncio.run(handlers.cmd_start(upd, ctx))
    assert st.get_subscriber("100") is not None
    assert "Welcome" in upd.effective_message.replies[0]

    upd2 = FakeUpdate(100)
    asyncio.run(handlers.cmd_start(upd2, ctx))
    assert "Welcome back" in upd2.effective_message.replies[0]  # idempotent path


def test_cmd_help_replies_with_catalog(tmp_path):
    st = _store(tmp_path)
    upd, ctx = FakeUpdate(1), FakeContext(st)
    asyncio.run(handlers.cmd_help(upd, ctx))
    assert "/start" in upd.effective_message.replies[0]


def test_stop_confirm_deletes_data(tmp_path):
    st = _store(tmp_path)
    service.ensure_subscriber(st, "100")
    st.add_subscription("100", "PL", 2026, "Arsenal")
    upd, ctx = FakeCallbackUpdate("stop:confirm", 100), FakeContext(st)
    asyncio.run(handlers.on_stop_callback(upd, ctx))
    assert upd.answered is True
    assert upd.edited == texts.STOP_DONE
    assert st.get_subscriber("100") is None


def test_stop_cancel_keeps_data(tmp_path):
    st = _store(tmp_path)
    service.ensure_subscriber(st, "100")
    upd, ctx = FakeCallbackUpdate("stop:cancel", 100), FakeContext(st)
    asyncio.run(handlers.on_stop_callback(upd, ctx))
    assert upd.edited == texts.STOP_CANCELLED
    assert st.get_subscriber("100") is not None        # still subscribed


# --- app wiring ------------------------------------------------------------

def test_build_application_registers_handlers(tmp_path):
    app = build_application("123456789:AA-fake-token-for-tests", str(tmp_path / "s.sqlite"))
    # 4 commands + 2 callback handlers + greeting + 2 catch-alls = 9, all in group 0.
    assert len(app.handlers[0]) == 9
    assert "sub_store" in app.bot_data
