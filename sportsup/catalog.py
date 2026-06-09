"""The free competition catalog users can subscribe to.

These are the 12 competitions available on the football-data.org **free** tier — the
menu the multi-user bot offers during onboarding (Phase 9) and the source of display
names + API-Football league ids used by migration and the fan-out planner (Phase 7).

`api_football_league` enables odds-based upset detection for that competition (the
100 req/day budget is managed separately); `None` means upset detection falls back to
standings/form. `season` is the season we currently track for that competition and is
the only field likely to need a yearly bump — everything else is stable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Competition:
    code: str                       # football-data.org code — our canonical key
    name: str                       # human-friendly display name
    season: int                     # currently-tracked season (start year)
    api_football_league: int | None  # API-Football league id for odds, or None
    emoji: str = "⚽"               # shown in onboarding keyboards


# Ordered roughly by how likely a casual fan is to want it. Seasons are the current/
# upcoming season as of 2026-06; bump the year when a new season starts.
FREE_COMPETITIONS: list[Competition] = [
    Competition("WC",  "FIFA World Cup 2026",        2026, 1,    "🏆"),
    Competition("PL",  "English Premier League",     2026, 39,   "🏴"),
    Competition("CL",  "UEFA Champions League",      2026, 2,    "✨"),
    Competition("EC",  "UEFA European Championship",  2028, 4,    "🇪🇺"),
    Competition("PD",  "La Liga (Spain)",            2026, 140,  "🇪🇸"),
    Competition("BL1", "Bundesliga (Germany)",       2026, 78,   "🇩🇪"),
    Competition("SA",  "Serie A (Italy)",            2026, 135,  "🇮🇹"),
    Competition("FL1", "Ligue 1 (France)",           2026, 61,   "🇫🇷"),
    Competition("ELC", "EFL Championship (England)",  2026, 40,   "🏴"),
    Competition("DED", "Eredivisie (Netherlands)",    2026, 88,   "🇳🇱"),
    Competition("PPL", "Primeira Liga (Portugal)",    2026, 94,   "🇵🇹"),
    Competition("BSA", "Brasileirão Série A (Brazil)", 2026, 71,   "🇧🇷"),
]

_BY_CODE: dict[str, Competition] = {c.code: c for c in FREE_COMPETITIONS}


def get_competition(code: str) -> Competition | None:
    """Look up a catalog competition by football-data.org code (case-insensitive)."""
    return _BY_CODE.get(code.strip().upper())


def competition_name(code: str) -> str:
    """Display name for a code, falling back to the code itself if unknown."""
    comp = get_competition(code)
    return comp.name if comp else code


def league_map() -> dict[str, int]:
    """code -> API-Football league id, for every catalog competition that has odds.

    Passed to `build_router` so odds lookups work for any subscribed competition
    (not just the ones a single config.yaml happened to list)."""
    return {c.code: c.api_football_league for c in FREE_COMPETITIONS if c.api_football_league}
