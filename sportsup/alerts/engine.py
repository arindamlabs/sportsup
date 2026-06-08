"""Alert engine: build fixture reminders and result/upset alerts, with dedup.

Pure-ish: reminder planning and result evaluation take already-fetched data, so they're
easy to test. IO (fetching odds/standings) is done by the caller and passed in. Dedup is
delegated to the state store via :meth:`unsent` / :meth:`mark_sent`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Iterable

from ..config import AppConfig, EventConfig, parse_lead_time
from ..providers import Fixture, MatchOdds, MatchResult, Standing
from ..state import StateStore
from .models import Alert, AlertType
from .shock import evaluate_upset

logger = logging.getLogger("sportsup.alerts.engine")

# A function that returns pre-match odds for a finished match (or None if unavailable).
OddsLookup = Callable[[MatchResult], "MatchOdds | None"]


class AlertEngine:
    def __init__(self, config: AppConfig, store: StateStore) -> None:
        self.config = config
        self.store = store
        self.tz = config.tzinfo

    # --- reminders --------------------------------------------------------

    def plan_reminders(
        self, event: EventConfig, fixtures: Iterable[Fixture], *, now: datetime,
        include_past: bool = False,
    ) -> list[Alert]:
        """Reminders for upcoming fixtures × configured lead-times.

        By default only reminders whose lead-window is still in the future are returned
        (a clean forward schedule for previews). With ``include_past=True`` the runtime
        also gets reminders whose window has already arrived — for a still-upcoming match —
        so a missed/late tick can catch up (dedup keeps it exactly-once)."""
        if not event.alerts.upcoming_fixtures:
            return []
        alerts: list[Alert] = []
        for fx in fixtures:
            if fx.utc_kickoff <= now:
                continue  # match already kicked off — no reminders
            if not fx.status.is_upcoming:
                continue  # postponed / cancelled / suspended — don't remind
            for lead_label in self.config.reminders.lead_times:
                fire_at = fx.utc_kickoff - parse_lead_time(lead_label)
                if fire_at < now and not include_past:
                    continue  # lead window already passed; don't backfill in preview mode
                alerts.append(
                    Alert(
                        type=AlertType.FIXTURE_REMINDER,
                        event_id=event.id,
                        dedup_key=f"{event.id}:{fx.stable_id()}:reminder:{lead_label}",
                        fixture=fx,
                        scheduled_for=fire_at,
                        lead_label=lead_label,
                        summary=self._reminder_summary(fx, lead_label),
                        context=self._fixture_context(event, fx),
                    )
                )
        alerts.sort(key=lambda a: a.scheduled_for or now)
        return alerts

    # --- results ----------------------------------------------------------

    def evaluate_results(
        self,
        event: EventConfig,
        results: Iterable[MatchResult],
        *,
        odds_lookup: OddsLookup | None = None,
        standings: list[Standing] | None = None,
    ) -> list[Alert]:
        """Final-score alerts (if enabled) and shock-result alerts (if an upset fires)."""
        alerts: list[Alert] = []
        for r in results:
            fx = r.fixture
            if not fx.status.is_finished:
                continue

            if event.alerts.final_scores:
                alerts.append(
                    Alert(
                        type=AlertType.FINAL_SCORE,
                        event_id=event.id,
                        dedup_key=f"{event.id}:{fx.stable_id()}:final",
                        fixture=fx,
                        summary=self._final_summary(r),
                        context={**self._fixture_context(event, fx),
                                 "home_score": r.score.home, "away_score": r.score.away},
                    )
                )

            if event.alerts.shock_result:
                odds = odds_lookup(r) if odds_lookup else None
                ev = evaluate_upset(r, config=self.config, odds=odds, standings=standings)
                if ev.is_upset:
                    alerts.append(
                        Alert(
                            type=AlertType.SHOCK_RESULT,
                            event_id=event.id,
                            dedup_key=f"{event.id}:{fx.stable_id()}:shock",
                            fixture=fx,
                            summary=self._shock_summary(r, ev),
                            context={
                                **self._fixture_context(event, fx),
                                "home_score": r.score.home, "away_score": r.score.away,
                                "upset_index": ev.upset_index,
                                "signal_used": ev.signal_used,
                                "reason": ev.reason,
                            },
                        )
                    )
        return alerts

    # --- dedup ------------------------------------------------------------

    def unsent(self, alerts: Iterable[Alert]) -> list[Alert]:
        return [a for a in alerts if not self.store.was_sent(a.dedup_key)]

    def mark_sent(self, alert: Alert) -> bool:
        return self.store.mark_sent(
            alert.dedup_key, event_id=alert.event_id, alert_type=alert.type.value
        )

    # --- formatting helpers (plain summaries; rich templates in Phase 4) --

    def _fixture_context(self, event: EventConfig, fx: Fixture) -> dict:
        return {
            "competition": event.name,
            "home": fx.home.name,
            "away": fx.away.name,
            "kickoff_local": fx.utc_kickoff.astimezone(self.tz).isoformat(),
        }

    def _local(self, fx: Fixture) -> str:
        return fx.utc_kickoff.astimezone(self.tz).strftime("%a %d %b %H:%M %Z")

    def _reminder_summary(self, fx: Fixture, lead_label: str) -> str:
        return (
            f"[{lead_label} reminder] {fx.home.name} vs {fx.away.name} — "
            f"kickoff {self._local(fx)}"
        )

    def _final_summary(self, r: MatchResult) -> str:
        fx = r.fixture
        return f"[Full time] {fx.home.name} {r.score.home}–{r.score.away} {fx.away.name}"

    def _shock_summary(self, r: MatchResult, ev) -> str:
        fx = r.fixture
        return (
            f"[UPSET · {ev.signal_used}] {fx.home.name} {r.score.home}–{r.score.away} "
            f"{fx.away.name} — {ev.reason} (index {ev.upset_index:.2f})"
        )
