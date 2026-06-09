"""Synchronous bot logic over the SubscriberStore.

Kept separate from the async PTB handlers so the actual behaviour (register, stop,
look up) is plain, fast-testable code with no Telegram objects involved. Handlers are
thin async wrappers that translate an Update into one of these calls plus a reply.
"""

from __future__ import annotations

from ..subscribers import Subscriber, SubscriberStore


def ensure_subscriber(store: SubscriberStore, chat_id: str) -> tuple[Subscriber, bool]:
    """Return the subscriber for this chat, creating one with defaults if new.

    Returns ``(subscriber, created)``. Idempotent — an existing subscriber is returned
    untouched (we don't reset their preferences on a repeat /start)."""
    existing = store.get_subscriber(chat_id)
    if existing is not None:
        return existing, False
    return store.upsert_subscriber(Subscriber(chat_id=chat_id)), True


def unsubscribe(store: SubscriberStore, chat_id: str) -> bool:
    """Full unsubscribe: delete the subscriber and (via cascade) their subscriptions.
    Returns False if they weren't subscribed."""
    return store.delete_subscriber(chat_id)


def is_subscribed(store: SubscriberStore, chat_id: str) -> bool:
    return store.get_subscriber(chat_id) is not None
