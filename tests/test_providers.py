"""Phase 2 tests: provider parsing, team resolution, and router failover — all offline."""

from datetime import datetime, timezone

import httpx
import pytest

from sportsup.providers import (
    Capability,
    MatchOdds,
    MatchStatus,
    ProviderRouter,
    ProviderUnavailableError,
)
from sportsup.providers.api_football import ApiFootballProvider
from sportsup.providers.base import SportsDataProvider
from sportsup.providers.football_data import FootballDataProvider
from sportsup.providers.http import HttpClient
from sportsup.providers.teams import TeamResolver, canonical_name, same_team

# --- sample payloads -------------------------------------------------------

FD_MATCHES = {
    "matches": [
        {
            "id": 1, "utcDate": "2026-06-11T16:00:00Z", "status": "TIMED", "matchday": 1,
            "homeTeam": {"id": 10, "name": "Mexico", "tla": "MEX"},
            "awayTeam": {"id": 11, "name": "Poland", "tla": "POL"},
            "score": {"winner": None, "fullTime": {"home": None, "away": None}},
        },
        {
            "id": 2, "utcDate": "2026-06-10T16:00:00Z", "status": "FINISHED",
            "homeTeam": {"id": 12, "name": "Brazil", "tla": "BRA"},
            "awayTeam": {"id": 13, "name": "Serbia", "tla": "SRB"},
            "score": {"winner": "HOME", "fullTime": {"home": 2, "away": 0}},
        },
    ]
}

FD_STANDINGS = {
    "standings": [
        {"type": "TOTAL", "table": [
            {"position": 1, "team": {"id": 57, "name": "Arsenal FC", "tla": "ARS"},
             "playedGames": 3, "won": 3, "draw": 0, "lost": 0, "points": 9,
             "goalDifference": 6, "form": "WWW"},
        ]},
        {"type": "HOME", "table": []},
    ]
}

AF_FIXTURES = {
    "response": [
        {
            "fixture": {"id": 100, "date": "2026-06-10T16:00:00+00:00", "status": {"short": "NS"}},
            "league": {"id": 1, "season": 2026},
            "teams": {"home": {"id": 1, "name": "Brazil"}, "away": {"id": 2, "name": "Serbia"}},
            "goals": {"home": None, "away": None},
        }
    ]
}

AF_ODDS = {
    "response": [
        {"bookmakers": [
            {"id": 8, "name": "Bet365", "bets": [
                {"id": 1, "name": "Match Winner", "values": [
                    {"value": "Home", "odd": "1.50"},
                    {"value": "Draw", "odd": "4.00"},
                    {"value": "Away", "odd": "6.50"},
                ]}
            ]}
        ]}
    ]
}


def _mock_client(routes: dict[str, dict], headers=None) -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for path, payload in routes.items():
            if request.url.path.endswith(path):
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"error": "no route", "path": request.url.path})

    return HttpClient("https://example.test", headers=headers or {},
                      transport=httpx.MockTransport(handler), sleep=lambda *a: None)


# --- football-data.org -----------------------------------------------------

def test_football_data_fixtures_and_results():
    client = _mock_client({"/matches": FD_MATCHES})
    p = FootballDataProvider("key", client=client)
    window = (datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 6, 30, tzinfo=timezone.utc))

    fixtures = p.get_fixtures(competition_code="WC", season=2026, date_from=window[0], date_to=window[1])
    assert len(fixtures) == 2
    f0 = fixtures[0]
    assert f0.home.name == "Mexico" and f0.away.name == "Poland"
    assert f0.status is MatchStatus.TIMED
    assert f0.utc_kickoff == datetime(2026, 6, 11, 16, 0, tzinfo=timezone.utc)
    assert f0.stable_id() == "WC:20260611:MEX-v-POL"

    results = p.get_results(competition_code="WC", season=2026, date_from=window[0], date_to=window[1])
    assert len(results) == 1  # only the FINISHED one
    assert results[0].score.home == 2 and results[0].winner == "HOME"


def test_football_data_standings():
    client = _mock_client({"/standings": FD_STANDINGS})
    p = FootballDataProvider("key", client=client)
    table = p.get_standings(competition_code="PL", season=2026)
    assert len(table) == 1
    assert table[0].team.name == "Arsenal FC"
    assert table[0].position == 1 and table[0].points == 9


# --- API-Football ----------------------------------------------------------

def test_api_football_fixtures_and_odds():
    client = _mock_client({"/fixtures": AF_FIXTURES, "/odds": AF_ODDS})
    p = ApiFootballProvider("key", league_map={"WC": 1}, client=client)

    fixtures = p.get_fixtures(
        competition_code="WC", season=2026,
        date_from=datetime(2026, 6, 10, tzinfo=timezone.utc),
        date_to=datetime(2026, 6, 10, tzinfo=timezone.utc),
    )
    assert len(fixtures) == 1 and fixtures[0].away.name == "Serbia"

    odds = p.get_match_odds(
        competition_code="WC", season=2026, home_team="Brazil", away_team="Serbia",
        kickoff=datetime(2026, 6, 10, 16, 0, tzinfo=timezone.utc),
    )
    assert odds is not None
    assert odds.home_win == 1.50 and odds.bookmaker == "Bet365"


def test_match_odds_implied_probabilities():
    odds = MatchOdds(home_win=1.5, draw=4.0, away_win=6.5)
    probs = odds.implied_probabilities()
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    assert probs["HOME"] > probs["AWAY"]  # favourite has higher implied prob


# --- team resolution -------------------------------------------------------

def test_team_aliases():
    assert canonical_name("Man United") == "Manchester United"
    assert canonical_name("South Korea") == "Korea Republic"
    assert same_team("Türkiye", "Turkey")
    assert same_team("Arsenal", "Arsenal FC")
    assert not same_team("Arsenal", "Chelsea")


def test_team_resolver_watchlist_and_unmatched():
    resolver = TeamResolver(["Man United", "South Korea", "Atlantis"])
    assert resolver.is_watched("Manchester United FC")
    assert resolver.is_watched("Korea Republic")
    assert not resolver.is_watched("Chelsea")
    seen = {"Manchester United FC", "Korea Republic", "Chelsea FC"}
    assert resolver.unmatched(seen) == ["Atlantis"]  # never appeared -> surfaced


# --- router failover -------------------------------------------------------

class _DeadProvider(SportsDataProvider):
    name = "dead"

    def capabilities(self):
        return {Capability.FIXTURES}

    def get_fixtures(self, **kwargs):
        raise ProviderUnavailableError("simulated outage")


def test_router_fails_over_to_next_provider():
    good = FootballDataProvider("key", client=_mock_client({"/matches": FD_MATCHES}))
    router = ProviderRouter([_DeadProvider(), good])
    fixtures = router.get_fixtures(
        competition_code="WC", season=2026,
        date_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
        date_to=datetime(2026, 6, 30, tzinfo=timezone.utc),
    )
    assert len(fixtures) == 2  # served by the healthy fallback


def test_router_odds_only_from_capable_provider():
    # football-data has no ODDS capability; router should report none support it.
    fd = FootballDataProvider("key", client=_mock_client({}))
    router = ProviderRouter([fd])
    from sportsup.providers import NotSupportedError
    with pytest.raises(NotSupportedError):
        router.get_match_odds(
            competition_code="WC", season=2026, home_team="A", away_team="B",
            kickoff=datetime(2026, 6, 10, tzinfo=timezone.utc),
        )
