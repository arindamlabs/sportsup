"""Normalized, vendor-agnostic domain models.

Every provider adapter maps its raw JSON into these shapes, so the rest of the app
(alert engine, formatter, store) never sees a vendor's payload structure.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class MatchStatus(str, Enum):
    SCHEDULED = "SCHEDULED"   # future, exact time may be TBD
    TIMED = "TIMED"           # future, kickoff time known
    IN_PLAY = "IN_PLAY"
    PAUSED = "PAUSED"         # half-time etc.
    FINISHED = "FINISHED"
    POSTPONED = "POSTPONED"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_finished(self) -> bool:
        return self is MatchStatus.FINISHED

    @property
    def is_upcoming(self) -> bool:
        return self in (MatchStatus.SCHEDULED, MatchStatus.TIMED)


class TeamRef(BaseModel):
    """A team as a provider knows it. `provider_id` is opaque and provider-scoped."""

    name: str
    provider_id: str | None = None
    short_name: str | None = None
    tla: str | None = None  # three-letter abbreviation, e.g. 'MUN'


class Competition(BaseModel):
    code: str          # e.g. 'WC', 'PL' (football-data.org code, used as our canonical key)
    name: str
    provider_id: str | None = None


class Score(BaseModel):
    home: int | None = None
    away: int | None = None

    @property
    def is_complete(self) -> bool:
        return self.home is not None and self.away is not None


class Fixture(BaseModel):
    """An upcoming or in-progress match, normalized across providers."""

    provider: str
    provider_fixture_id: str
    competition_code: str
    season: int
    utc_kickoff: datetime          # always timezone-aware UTC
    status: MatchStatus
    home: TeamRef
    away: TeamRef
    matchday: int | None = None

    def stable_id(self) -> str:
        """Provider-independent id for dedup/storage: competition + teams + date."""
        d = self.utc_kickoff.strftime("%Y%m%d")
        h = (self.home.tla or self.home.name).replace(" ", "")
        a = (self.away.tla or self.away.name).replace(" ", "")
        return f"{self.competition_code}:{d}:{h}-v-{a}"

    def involves(self, team_names: set[str]) -> bool:
        """True if either side matches one of the given (already-normalized) names."""
        return _norm(self.home.name) in team_names or _norm(self.away.name) in team_names


class MatchResult(BaseModel):
    """A finished match with its score and winner."""

    fixture: Fixture
    score: Score
    winner: str | None = None  # 'HOME' | 'AWAY' | 'DRAW' | None

    @classmethod
    def from_fixture(cls, fixture: Fixture, score: Score) -> "MatchResult":
        winner: str | None = None
        if score.is_complete:
            if score.home > score.away:      # type: ignore[operator]
                winner = "HOME"
            elif score.away > score.home:    # type: ignore[operator]
                winner = "AWAY"
            else:
                winner = "DRAW"
        return cls(fixture=fixture, score=score, winner=winner)


class Standing(BaseModel):
    team: TeamRef
    position: int
    played: int = 0
    won: int = 0
    draw: int = 0
    lost: int = 0
    points: int = 0
    goal_difference: int = 0
    form: str | None = None  # e.g. 'WWDLW' (most-recent last/first per provider)


class MatchOdds(BaseModel):
    """Pre-match 1X2 decimal odds, used by the shock-result heuristic."""

    home_win: float
    draw: float
    away_win: float
    bookmaker: str | None = None

    def implied_probabilities(self) -> dict[str, float]:
        """De-vigged implied probabilities for home/draw/away (sum to 1.0)."""
        raw = {
            "HOME": 1.0 / self.home_win,
            "DRAW": 1.0 / self.draw,
            "AWAY": 1.0 / self.away_win,
        }
        overround = sum(raw.values())
        return {k: v / overround for k, v in raw.items()}


def _norm(name: str) -> str:
    return name.strip().casefold()
