"""Daily odds-call budget for API-Football's free tier (100 requests/day).

Odds are only used to *sharpen* upset detection; when the budget is spent the engine
falls back to standings/form, so running out degrades gracefully rather than failing.
The counter lives in the state store's ``meta`` table keyed by UTC date, so it survives
restarts and resets naturally at midnight UTC. The cap is set a little under 100 to
leave headroom for the provider health probes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .state import StateStore

logger = logging.getLogger("sportsup.odds_budget")

DEFAULT_DAILY_CAP = 90


class OddsBudget:
    def __init__(self, store: StateStore, *, daily_cap: int = DEFAULT_DAILY_CAP) -> None:
        self.store = store
        self.daily_cap = daily_cap

    @staticmethod
    def _key(now: datetime) -> str:
        return f"odds_calls:{now.astimezone(timezone.utc).strftime('%Y%m%d')}"

    def used(self, now: datetime) -> int:
        return int(self.store.get_meta(self._key(now)) or 0)

    def remaining(self, now: datetime) -> int:
        return max(0, self.daily_cap - self.used(now))

    def try_consume(self, now: datetime) -> bool:
        """Reserve one odds call for today. Returns False if the cap is reached
        (the caller should then skip odds and use the standings fallback)."""
        key = self._key(now)
        used = int(self.store.get_meta(key) or 0)
        if used >= self.daily_cap:
            return False
        self.store.set_meta(key, str(used + 1))
        if used + 1 == self.daily_cap:
            logger.info("odds budget for %s exhausted (%d) — falling back to standings",
                        key, self.daily_cap)
        return True
