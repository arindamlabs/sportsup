"""Phase 2 (A) tests: fixture sync distinguishes idle vs misspelled watchlist teams."""

from datetime import datetime, timezone

import httpx

from sportsup.config import AppConfig
from sportsup.providers import ProviderRouter
from sportsup.providers.football_data import FootballDataProvider
from sportsup.providers.http import HttpClient
from sportsup.sync import collect_watched_fixtures

MATCHES = {"matches": [
    {"id": 1, "utcDate": "2099-06-12T16:00:00Z", "status": "TIMED",
     "homeTeam": {"id": 1, "name": "England", "tla": "ENG"},
     "awayTeam": {"id": 2, "name": "Iran", "tla": "IRN"},
     "score": {"winner": None, "fullTime": {"home": None, "away": None}}},
]}
# Full roster: England + Spain are valid spellings; "Atlantis" is not present.
TEAMS = {"teams": [
    {"id": 1, "name": "England", "tla": "ENG"},
    {"id": 2, "name": "Iran", "tla": "IRN"},
    {"id": 3, "name": "Spain", "tla": "ESP"},
]}


def _router() -> ProviderRouter:
    def handler(req):
        if req.url.path.endswith("/teams"):
            return httpx.Response(200, json=TEAMS)
        if req.url.path.endswith("/matches"):
            return httpx.Response(200, json=MATCHES)
        return httpx.Response(200, json={})
    client = HttpClient("https://x.test", transport=httpx.MockTransport(handler), sleep=lambda *a: None)
    return ProviderRouter([FootballDataProvider("k", client=client)])


def _config() -> AppConfig:
    return AppConfig.model_validate({
        "fixture_sync_lookahead_days": 10,
        "events": [{
            "id": "wc", "name": "World Cup", "competition_code": "WC", "season": 2026,
            "teams": ["England", "Spain", "Atlantis"],
        }],
    })


def test_idle_vs_unknown_teams():
    results = collect_watched_fixtures(
        _config(), _router(), now=datetime(2099, 6, 10, tzinfo=timezone.utc)
    )
    ef = results[0]
    # England plays in the window -> appears in fixtures.
    assert len(ef.fixtures) == 1 and ef.fixtures[0].home.name == "England"
    # Spain is a valid team with no fixture in window -> idle (not a spelling problem).
    assert ef.idle_teams == ["Spain"]
    # Atlantis is not in the roster -> genuine spelling mismatch.
    assert ef.unknown_teams == ["Atlantis"]


def test_idle_only_when_roster_available():
    # If the team roster can't be fetched, we must not falsely flag spellings.
    def handler(req):
        if req.url.path.endswith("/teams"):
            return httpx.Response(500, json={})
        return httpx.Response(200, json=MATCHES)
    client = HttpClient("https://x.test", transport=httpx.MockTransport(handler), sleep=lambda *a: None)
    router = ProviderRouter([FootballDataProvider("k", client=client)])
    ef = collect_watched_fixtures(
        _config(), router, now=datetime(2099, 6, 10, tzinfo=timezone.utc)
    )[0]
    assert ef.unknown_teams == []          # no false spelling accusations
    assert "Spain" in ef.idle_teams and "Atlantis" in ef.idle_teams
