"""Sports-data providers behind a common interface.

Adding a new data source = implement :class:`~sportsup.providers.base.SportsDataProvider`
and register it in the router. Nothing else in the app talks to a vendor API directly.
"""

from .base import (
    Capability,
    NotSupportedError,
    ProviderError,
    ProviderUnavailableError,
    RateLimitError,
    SportsDataProvider,
)
from .models import (
    Competition,
    Fixture,
    MatchOdds,
    MatchResult,
    MatchStatus,
    Score,
    Standing,
    TeamRef,
)
from .router import ProviderRouter

__all__ = [
    "Capability",
    "Competition",
    "Fixture",
    "MatchOdds",
    "MatchResult",
    "MatchStatus",
    "NotSupportedError",
    "ProviderError",
    "ProviderRouter",
    "ProviderUnavailableError",
    "RateLimitError",
    "Score",
    "SportsDataProvider",
    "Standing",
    "TeamRef",
]
