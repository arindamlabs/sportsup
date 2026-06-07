"""Match config team names to the canonical names providers actually return.

Your watchlist says "Man United" / "South Korea"; football-data.org says
"Manchester United FC" / "Korea Republic". This module normalizes and resolves both
ways so fixtures can be filtered to watched teams reliably. Aliases live here (not in
config) but are easy to extend; unmatched names are reported, never silently dropped.
"""

from __future__ import annotations

import re

# Canonical alias groups. Every spelling in a tuple is treated as the same team.
# Add a row to support a new naming variant — no other code changes needed.
_ALIAS_GROUPS: list[tuple[str, ...]] = [
    # English Premier League
    ("Manchester United", "Man United", "Man Utd", "Manchester United FC", "MUN"),
    ("Manchester City", "Man City", "Manchester City FC", "MCI"),
    ("Tottenham Hotspur", "Tottenham", "Spurs", "Tottenham Hotspur FC"),
    ("Wolverhampton Wanderers", "Wolves", "Wolverhampton Wanderers FC"),
    ("Newcastle United", "Newcastle", "Newcastle United FC"),
    ("Arsenal", "Arsenal FC"),
    ("Chelsea", "Chelsea FC"),
    ("Liverpool", "Liverpool FC"),
    ("Brighton & Hove Albion", "Brighton", "Brighton Hove Albion"),
    ("West Ham United", "West Ham", "West Ham United FC"),
    ("Nottingham Forest", "Nott'm Forest", "Nottingham Forest FC"),
    # World Cup national teams (common provider spellings)
    ("Korea Republic", "South Korea", "Korea", "Republic of Korea"),
    ("Korea DPR", "North Korea"),
    ("Türkiye", "Turkey", "Turkiye"),
    ("United States", "USA", "USMNT", "United States of America"),
    ("IR Iran", "Iran"),
    ("Côte d'Ivoire", "Ivory Coast", "Cote d'Ivoire"),
    ("Netherlands", "Holland"),
    ("Czechia", "Czech Republic"),
]


def _normalize(name: str) -> str:
    """Lowercase, strip accents-insensitive punctuation/suffixes for matching."""
    s = name.strip().casefold()
    s = re.sub(r"\b(fc|cf|afc|sc)\b", "", s)        # drop common club suffixes
    s = re.sub(r"[^a-z0-9à-ÿ]+", " ", s)            # punctuation -> space
    return re.sub(r"\s+", " ", s).strip()


# Build normalized-spelling -> canonical-name lookup once.
_LOOKUP: dict[str, str] = {}
for group in _ALIAS_GROUPS:
    canonical = group[0]
    for spelling in group:
        _LOOKUP[_normalize(spelling)] = canonical


def canonical_name(name: str) -> str:
    """Return the canonical team name for any known spelling, else the cleaned input."""
    return _LOOKUP.get(_normalize(name), name.strip())


def same_team(a: str, b: str) -> bool:
    """True if two spellings refer to the same team."""
    if _normalize(a) == _normalize(b):
        return True
    return canonical_name(a) == canonical_name(b)


class TeamResolver:
    """Resolves a configured watchlist against names seen in provider data."""

    def __init__(self, watchlist: list[str]) -> None:
        self.watchlist = watchlist
        # canonical-normalized set for fast membership tests
        self._wanted = {_normalize(canonical_name(t)) for t in watchlist}

    def is_watched(self, provider_team_name: str) -> bool:
        return _normalize(canonical_name(provider_team_name)) in self._wanted

    def unmatched(self, provider_team_names: set[str]) -> list[str]:
        """Watchlist entries that never appeared in the provider's team set —
        surfaced so the user can fix a spelling rather than miss alerts silently."""
        seen = {_normalize(canonical_name(n)) for n in provider_team_names}
        return [t for t in self.watchlist if _normalize(canonical_name(t)) not in seen]
