"""football-data.org v4 adapter — fixtures, results, standings (no odds).

Free tier: 10 req/min, covers the World Cup and Premier League among 12 competitions.
Docs: https://www.football-data.org/documentation/api
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .base import Capability, ProviderUnavailableError, SportsDataProvider
from .http import HttpClient
from .models import Fixture, MatchResult, MatchStatus, Score, Standing, TeamRef

logger = logging.getLogger("sportsup.providers.football_data")

BASE_URL = "https://api.football-data.org/v4"

# football-data.org status string -> our normalized status
_STATUS_MAP = {
    "SCHEDULED": MatchStatus.SCHEDULED,
    "TIMED": MatchStatus.TIMED,
    "IN_PLAY": MatchStatus.IN_PLAY,
    "PAUSED": MatchStatus.PAUSED,
    "FINISHED": MatchStatus.FINISHED,
    "AWARDED": MatchStatus.FINISHED,
    "POSTPONED": MatchStatus.POSTPONED,
    "SUSPENDED": MatchStatus.SUSPENDED,
    "CANCELLED": MatchStatus.CANCELLED,
}


def _parse_dt(value: str) -> datetime:
    # e.g. "2026-06-11T16:00:00Z"
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc)


def _team(raw: dict) -> TeamRef:
    return TeamRef(
        name=raw.get("name") or raw.get("shortName") or "Unknown",
        provider_id=str(raw["id"]) if raw.get("id") is not None else None,
        short_name=raw.get("shortName"),
        tla=raw.get("tla"),
    )


class FootballDataProvider(SportsDataProvider):
    name = "football-data.org"

    def __init__(self, api_key: str, *, client: HttpClient | None = None) -> None:
        self._client = client or HttpClient(
            BASE_URL, headers={"X-Auth-Token": api_key}
        )

    def capabilities(self) -> set[Capability]:
        return {Capability.FIXTURES, Capability.RESULTS, Capability.STANDINGS}

    def health_check(self) -> bool:
        try:
            self._client.get_json("/competitions/PL")
            return True
        except ProviderUnavailableError:
            return False

    def _matches(
        self, competition_code: str, season: int, date_from: datetime, date_to: datetime
    ) -> list[dict]:
        params = {
            "dateFrom": date_from.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            "dateTo": date_to.astimezone(timezone.utc).strftime("%Y-%m-%d"),
        }
        data = self._client.get_json(f"/competitions/{competition_code}/matches", params)
        return data.get("matches", [])

    def _to_fixture(self, raw: dict, competition_code: str, season: int) -> Fixture:
        return Fixture(
            provider=self.name,
            provider_fixture_id=str(raw["id"]),
            competition_code=competition_code,
            season=season,
            utc_kickoff=_parse_dt(raw["utcDate"]),
            status=_STATUS_MAP.get(raw.get("status", ""), MatchStatus.UNKNOWN),
            home=_team(raw["homeTeam"]),
            away=_team(raw["awayTeam"]),
            matchday=raw.get("matchday"),
        )

    def get_fixtures(
        self, *, competition_code: str, season: int, date_from: datetime, date_to: datetime
    ) -> list[Fixture]:
        return [
            self._to_fixture(m, competition_code, season)
            for m in self._matches(competition_code, season, date_from, date_to)
        ]

    def get_results(
        self, *, competition_code: str, season: int, date_from: datetime, date_to: datetime
    ) -> list[MatchResult]:
        results: list[MatchResult] = []
        for m in self._matches(competition_code, season, date_from, date_to):
            fixture = self._to_fixture(m, competition_code, season)
            if not fixture.status.is_finished:
                continue
            full = (m.get("score") or {}).get("fullTime") or {}
            score = Score(home=full.get("home"), away=full.get("away"))
            results.append(MatchResult.from_fixture(fixture, score))
        return results

    def get_standings(self, *, competition_code: str, season: int) -> list[Standing]:
        data = self._client.get_json(f"/competitions/{competition_code}/standings")
        out: list[Standing] = []
        for block in data.get("standings", []):
            if block.get("type") and block["type"] != "TOTAL":
                continue  # skip HOME/AWAY splits; TOTAL is the league table
            for row in block.get("table", []):
                out.append(
                    Standing(
                        team=_team(row["team"]),
                        position=row["position"],
                        played=row.get("playedGames", 0),
                        won=row.get("won", 0),
                        draw=row.get("draw", 0),
                        lost=row.get("lost", 0),
                        points=row.get("points", 0),
                        goal_difference=row.get("goalDifference", 0),
                        form=row.get("form"),
                    )
                )
            if out:
                break  # first TOTAL block is the overall table
        return out
