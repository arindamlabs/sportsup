"""Multi-user delivery cycle: turn fanned-out alerts into delivered messages.

One cycle = plan every active subscriber's due alerts (fetching each competition once),
then for each alert apply that subscriber's own quiet-hours/timezone, deliver it to
their own chat, and record it as sent (per-user dedup) on real delivery. This is the
multi-user counterpart of :class:`sportsup.runtime.SchedulerRuntime`, reusing the same
pure classification logic so behaviour (stale-drop, quiet-hours defer/suppress) matches.

It is intentionally synchronous and side-effect-light: the bot runs it on a repeating
job (in a worker thread, since the providers use blocking httpx).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .alerts.models import Alert, AlertType
from .config import AppConfig
from .delivery import message_for_alert
from .delivery.base import WhatsAppSender
from .fanout import plan_for_all_subscribers
from .providers.router import ProviderRouter
from .runtime import classify_reminder, classify_result
from .state import StateStore
from .subscribers import SubscriberStore, effective_config

logger = logging.getLogger("sportsup.mux_delivery")


@dataclass
class DeliveryStats:
    subscribers: int = 0
    sent: int = 0
    deferred: int = 0   # quiet-hours defer or not-yet-due — retried next cycle
    dropped: int = 0    # stale / suppressed — marked sent so they never resurface
    failed: int = 0

    def __str__(self) -> str:
        return (f"{self.subscribers} subscriber(s): {self.sent} sent, {self.deferred} deferred, "
                f"{self.dropped} dropped, {self.failed} failed")


def run_delivery_cycle(
    base_config: AppConfig, router: ProviderRouter, store: StateStore,
    sub_store: SubscriberStore, sender: WhatsAppSender, *, now: datetime | None = None,
) -> DeliveryStats:
    """Plan + deliver one cycle of alerts across all active subscribers."""
    now = now or datetime.now(timezone.utc)
    stats = DeliveryStats()
    plans = plan_for_all_subscribers(base_config, router, store, sub_store, now=now, include_past=True)

    for plan in plans:
        sub = plan.subscriber
        stats.subscribers += 1
        # Per-user config for quiet-hours/timezone decisions and message rendering.
        eff = effective_config(sub, base_config, [])
        for alert in plan.alerts:
            if alert.type == AlertType.FIXTURE_REMINDER:
                action = classify_reminder(alert, now, eff)
            else:
                action = classify_result(now, eff)

            if action == "send":
                _deliver(alert, eff, sub.chat_id, sender, store, stats)
            elif action == "drop":
                _mark(store, alert)
                stats.dropped += 1
            else:  # "defer" (quiet hours) or "wait" (lead window not reached) — leave it
                stats.deferred += 1

    if stats.sent or stats.failed:
        logger.info("delivery cycle: %s", stats)
    else:
        logger.debug("delivery cycle: %s", stats)
    return stats


def _deliver(
    alert: Alert, config: AppConfig, chat_id: str, sender: WhatsAppSender,
    store: StateStore, stats: DeliveryStats,
) -> None:
    res = sender.send(message_for_alert(alert, config, chat_id))
    if res.ok:
        stats.sent += 1
        # Persist dedup only on a real delivery so dry-runs stay repeatable.
        if res.provider != "console":
            _mark(store, alert)
    else:
        stats.failed += 1
        logger.error("delivery failed for %s -> %s: %s (code %s)",
                     alert.dedup_key, chat_id, res.error, res.error_code)


def _mark(store: StateStore, alert: Alert) -> None:
    store.mark_sent(alert.dedup_key, event_id=alert.event_id, alert_type=alert.type.value)
