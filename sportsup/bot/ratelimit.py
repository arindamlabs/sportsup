"""Per-chat inbound rate limiting — a small abuse guard for a public-facing bot.

A fixed-window counter per chat: at most ``max_per_window`` updates per ``window_seconds``.
In-memory only (resets on restart), which is the right trade-off for a friend-scale bot —
it throttles accidental floods and trivial spam without any storage. Pure and clock-injected
so it's deterministic to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    max_per_window: int = 20
    window_seconds: int = 60
    # chat_id -> (window_start_epoch, count_in_window)
    _state: dict[str, tuple[float, int]] = field(default_factory=dict)

    def check(self, chat_id: str, now: float) -> bool:
        """Record an inbound event; return True if allowed, False if over the limit.

        The first request that exceeds the limit returns False (so the caller can warn
        once); further requests in the same window also return False silently."""
        start, count = self._state.get(chat_id, (now, 0))
        if now - start >= self.window_seconds:
            start, count = now, 0          # window rolled over
        count += 1
        self._state[chat_id] = (start, count)
        return count <= self.max_per_window

    def just_tripped(self, chat_id: str) -> bool:
        """True only on the exact request that first crosses the limit — so we send the
        'slow down' notice once per window rather than on every blocked message."""
        start, count = self._state.get(chat_id, (0.0, 0))
        return count == self.max_per_window + 1
