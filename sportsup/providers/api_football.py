"""API-Football (api-sports.io) v3 adapter.

Primary use: pre-match 1X2 odds for the shock-result heuristic. Also implements
fixtures/results/standings so it can serve as a full fallback for football-data.org.
Free tier: 100 requests/day — the router treats it as odds-primary, fixtures-fallback.
Docs: https://www.api-football.com/documentation-v3
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .base import Capability, NotSupportedError, ProviderUnavailableError, SportsDataProvider
from .http import HttpClient
from .models import Fixture, MatchOdds, MatchResult, MatchStatus, Score, Standing, TeamRef
from .teams import same_team

logger = logging.getLogger("sportsup.providers.api_football")

BASE_URL = "https://v3.football.api-sports.io"
MATCH_WINNER_BET_ID = 1  # "Match Winner" (1X2)

_STATUS_MAP = {
    "TBD": MatchStatus.SCHEDULED,
    "NS": MatchStatus.TIMED,
    "1H": MatchStatus.IN_PLAY,
    "2H": MatchStatus.IN_PLAY,
    "ET": MatchStatus.IN_PLAY,
    "BT": MatchStatus.IN_PLAY,
    "P": MatchStatus.IN_PLAY,
    "INT": MatchStatus.IN_PLAY,
    "LIVE": MatchStatus.IN_PLAY,
    "HT": MatchStatus.PAUSED,
    "FT": MatchStatus.FINISHED,
    "AET": MatchStatus.FINISHED,
    "PEN": MatchStatus.FINISHED,
    "AWD": MatchStatus.FINISHED,
    "WO": MatchStatus.FINISHED,
    "PST": MatchStatus.POSTPONED,
    "CANC": MatchStatus.CANCELLED,
    "ABD": MatchStatus.CANCELLED,
    "SUSP": MatchStatus.SUSPENDED,
}


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class ApiFootballProvider(SportsDataProvider):
    name = "api-football"

    def __init__(
        self,
        api_key: str,
        *,
        league_map: dict[str, int] | None = None,
        client: HttpClient | None = None,
    ) -> None:
        # Maps our canonical competition_code -> API-Football numeric league id.
        self._league_map = league_map or {"WC": 1, "PL": 39}
        self._client = client or HttpClient(
            BASE_URL, headers={"x-apisports-key": api_key}
        )

    def capabilities(self) -> set[Capability]:
        return {
            Capability.ODDS,
            Capability.FIXTURES,
            Capability.RESULTS,
            Capability.STANDINGS,
        }

    def health_check(self) -> bool:
        try:
            self._client.get_json("/status")
            return True
        except ProviderUnavailableError:
            return False

    def _league_id(self, competition_code: str) -> int:
        try:
            return self._league_map[competition_code]
        except KeyError:
            raise NotSupportedError(
                f"no API-Football league id mapped for competition {competition_code!r}"
            )

    def _to_fixture(self, raw: dict, competition_code: str, season: int) -> Fixture:
        fx, teams = raw["fixture"], raw["teams"]
        return Fixture(
            provider=self.name,
            provider_fixture_id=str(fx["id"]),
            competition_code=competition_code,
            season=season,
            utc_kickoff=_parse_dt(fx["date"]),
            status=_STATUS_MAP.get((fx.get("status") or {}).get("short", ""), MatchStatus.UNKNOWN),
            home=TeamRef(name=teams["home"]["name"], provider_id=str(teams["home"].get("id"))),
            away=TeamRef(name=teams["away"]["name"], provider_id=str(teams["away"].get("id"))),
            matchday=None,
        )

    def _fixtures_raw(
        self, competition_code: str, season: int, date_from: datetime, date_to: datetime
    ) -> list[dict]:
        params = {
            "league": self._league_id(competition_code),
            "season": season,
            "from": date_from.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            "to": date_to.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            "timezone": "UTC",
        }
        return self._client.get_json("/fixtures", params).get("response", [])

    def get_fixtures(
        self, *, competition_code: str, season: int, date_from: datetime, date_to: datetime
    ) -> list[Fixture]:
        return [
            self._to_fixture(r, competition_code, season)
            for r in self._fixtures_raw(competition_code, season, date_from, date_to)
        ]

    def get_results(
        self, *, competition_code: str, season: int, date_from: datetime, date_to: datetime
    ) -> list[MatchResult]:
        out: list[MatchResult] = []
        for r in self._fixtures_raw(competition_code, season, date_from, date_to):
            fixture = self._to_fixture(r, competition_code, season)
            if not fixture.status.is_finished:
                continue
            goals = r.get("goals") or {}
            out.append(
                MatchResult.from_fixture(fixture, Score(home=goals.get("home"), away=goals.get("away")))
            )
        return out

    def get_standings(self, *, competition_code: str, season: int) -> list[Standing]:
        params = {"league": self._league_id(competition_code), "season": season}
        resp = self._client.get_json("/standings", params).get("response", [])
        out: list[Standing] = []
        for league_block in resp:
            for group in (league_block.get("league") or {}).get("standings", []):
                for row in group:
                    allg = row.get("all") or {}
                    out.append(
                        Standing(
                            team=TeamRef(
                                name=row["team"]["name"],
                                provider_id=str(row["team"].get("id")),
                            ),
                            position=row["rank"],
                            played=allg.get("played", 0),
                            won=allg.get("win", 0),
                            draw=allg.get("draw", 0),
                            lost=allg.get("lose", 0),
                            points=row.get("points", 0),
                            goal_difference=row.get("goalsDiff", 0),
                            form=row.get("form"),
                        )
                    )
        return out

    def _find_fixture_id(
        self, competition_code: str, season: int, home_team: str, away_team: str, kickoff: datetime
    ) -> str | None:
        day = kickoff.astimezone(timezone.utc)
        for r in self._fixtures_raw(competition_code, season, day, day):
            teams = r["teams"]
            if same_team(teams["home"]["name"], home_team) and same_team(
                teams["away"]["name"], away_team
            ):
                return str(r["fixture"]["id"])
        return None

    def get_match_odds(
        self,
        *,
        competition_code: str,
        season: int,
        home_team: str,
        away_team: str,
        kickoff: datetime,
    ) -> MatchOdds | None:
        fixture_id = self._find_fixture_id(
            competition_code, season, home_team, away_team, kickoff
        )
        if fixture_id is None:
            logger.info("no API-Football fixture matched for odds: %s v %s", home_team, away_team)
            return None

        resp = self._client.get_json("/odds", {"fixture": fixture_id}).get("response", [])
        for entry in resp:
            for book in entry.get("bookmakers", []):
                for bet in book.get("bets", []):
                    if bet.get("id") != MATCH_WINNER_BET_ID:
                        continue
                    odds = {v["value"].lower(): float(v["odd"]) for v in bet.get("values", [])}
                    if {"home", "draw", "away"} <= odds.keys():
                        return MatchOdds(
                            home_win=odds["home"],
                            draw=odds["draw"],
                            away_win=odds["away"],
                            bookmaker=book.get("name"),
                        )
        return None
