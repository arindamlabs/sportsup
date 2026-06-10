"""Always-on scheduling runtime.

Three cadences (all configurable under `scheduling`):
  * fixture sync     — re-pull fixtures every `fixture_sync_hours`
  * reminder firing  — every `reminder_check_minutes`, deliver due reminders
  * result polling   — every `result_poll_minutes`, deliver final/upset alerts

Quiet hours are applied per the configured behaviour (defer or suppress). The decision
logic (`classify_reminder` / `classify_result`) is pure and unit-tested; APScheduler only
wires it to a clock. Dedup is via the state store, so restarts never double-send.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import Literal

from .alerts import AlertEngine
from .alerts.models import Alert
from .config import AppConfig
from .delivery import message_for_alert
from .delivery.base import Sender
from .pipeline import gather_result_alerts, plan_all_reminders
from .providers.router import ProviderRouter
from .state import StateStore

logger = logging.getLogger("sportsup.runtime")

Action = Literal["send", "wait", "defer", "drop"]


def in_quiet_hours(local_t: time, qh) -> bool:
    """True if a local time-of-day falls within the (possibly overnight) quiet window."""
    if not qh.enabled or qh.start == qh.end:
        return False
    if qh.start < qh.end:                 # same-day window, e.g. 01:00–06:00
        return qh.start <= local_t < qh.end
    return local_t >= qh.start or local_t < qh.end  # overnight, e.g. 22:00–07:00


def _quiet_now(now: datetime, config: AppConfig) -> bool:
    return in_quiet_hours(now.astimezone(config.tzinfo).timetz().replace(tzinfo=None), config.quiet_hours)


def classify_reminder(alert: Alert, now: datetime, config: AppConfig) -> Action:
    """Decide what to do with a reminder at `now`:
    drop (stale/ suppressed), wait (not due yet), defer (quiet hours), or send."""
    if alert.fixture.utc_kickoff <= now:
        return "drop"  # match already kicked off — a reminder now is stale
    if alert.scheduled_for and alert.scheduled_for > now:
        return "wait"  # lead window hasn't arrived
    if _quiet_now(now, config):
        return "defer" if config.quiet_hours.behavior == "defer" else "drop"
    return "send"


def classify_result(now: datetime, config: AppConfig) -> Action:
    """Result alerts have no lead time; only quiet hours gate them."""
    if _quiet_now(now, config):
        return "defer" if config.quiet_hours.behavior == "defer" else "drop"
    return "send"


class SchedulerRuntime:
    def __init__(
        self,
        config: AppConfig,
        router: ProviderRouter,
        sender: Sender,
        store: StateStore,
        recipient: str,
    ) -> None:
        self.config = config
        self.router = router
        self.sender = sender
        self.store = store
        self.recipient = recipient
        self.engine = AlertEngine(config, store)
        self._fixtures: dict[str, list] = {}

    # --- jobs -------------------------------------------------------------

    def sync_fixtures(self) -> None:
        try:
            from .sync import collect_watched_fixtures
            total = 0
            for ef in collect_watched_fixtures(self.config, self.router):
                if ef.error:
                    logger.warning("fixture sync error for %s: %s", ef.event.id, ef.error)
                    continue
                self._fixtures[ef.event.id] = ef.fixtures
                total += len(ef.fixtures)
            self.store.set_meta("last_fixture_sync_utc", datetime.now(timezone.utc).isoformat())
            self.store.set_meta("cached_fixture_count", str(total))
            logger.info("fixture sync complete: %d watched fixtures cached", total)
        except Exception:  # noqa: BLE001 — never let a job kill the scheduler
            logger.exception("fixture sync failed; keeping previous cache")

    def fire_reminders(self) -> None:
        now = datetime.now(timezone.utc)
        for ev in self.config.enabled_events:
            try:
                fixtures = self._fixtures.get(ev.id, [])
                reminders = self.engine.unsent(
                    self.engine.plan_reminders(ev, fixtures, now=now, include_past=True)
                )
                for a in reminders:
                    action = classify_reminder(a, now, self.config)
                    if action == "send":
                        self._deliver(a)
                    elif action == "drop":
                        self.engine.mark_sent(a)  # stale or suppressed — never resurface
            except Exception:  # noqa: BLE001
                logger.exception("reminder firing failed for %s", ev.id)

    def poll_results(self) -> None:
        now = datetime.now(timezone.utc)
        try:
            alerts = gather_result_alerts(
                self.config, self.router, self.engine, now,
                lookback_days=self.config.scheduling.result_lookback_days, logger=logger,
            )
        except Exception:  # noqa: BLE001
            logger.exception("result polling failed")
            return
        for a in alerts:
            action = classify_result(now, self.config)
            if action == "send":
                self._deliver(a)
            elif action == "drop":
                self.engine.mark_sent(a)

    def run_once(self) -> None:
        """A single full cycle — handy for cron-style invocation and tests."""
        self.sync_fixtures()
        self.fire_reminders()
        self.poll_results()

    # --- delivery ---------------------------------------------------------

    def _deliver(self, alert: Alert) -> None:
        res = self.sender.send(message_for_alert(alert, self.config, self.recipient))
        if res.ok:
            # Persist dedup only on real delivery so dry-runs stay repeatable.
            if res.provider != "console":
                self.engine.mark_sent(alert)
        else:
            logger.error("delivery failed for %s: %s (code %s)", alert.dedup_key, res.error, res.error_code)

    # --- scheduler --------------------------------------------------------

    def run(self) -> None:
        """Start the blocking scheduler loop (Ctrl-C / SIGTERM to stop)."""
        from apscheduler.schedulers.blocking import BlockingScheduler

        sc = self.config.scheduling
        scheduler = BlockingScheduler(timezone="UTC")
        logger.info(
            "starting runtime: fixture sync every %dh, reminders every %dm, results every %dm "
            "(sender=%s, tz=%s, quiet=%s)",
            sc.fixture_sync_hours, sc.reminder_check_minutes, sc.result_poll_minutes,
            self.sender.name, self.config.timezone,
            "on" if self.config.quiet_hours.enabled else "off",
        )
        self.sync_fixtures()  # prime the cache before the first reminder check
        scheduler.add_job(self.sync_fixtures, "interval", hours=sc.fixture_sync_hours,
                          id="fixture_sync", max_instances=1)
        scheduler.add_job(self.fire_reminders, "interval", minutes=sc.reminder_check_minutes,
                          id="fire_reminders", max_instances=1)
        scheduler.add_job(self.poll_results, "interval", minutes=sc.result_poll_minutes,
                          id="poll_results", max_instances=1)
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("runtime stopped")
