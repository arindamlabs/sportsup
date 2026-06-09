"""Pure view-model builders for the dashboard — no web framework involved.

Everything the page and the JSON API show is computed here from the stores, so it's all
unit-testable without spinning up a server. Read-only: only SELECT-backed store methods
are used.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..catalog import competition_name
from ..state import StateStore
from ..subscribers import ALL_TEAMS, SubscriberStore


@dataclass
class TournamentFollow:
    code: str
    name: str
    season: int
    teams: list[str]            # explicit team names, or [] when following all
    all_teams: bool


@dataclass
class UserRow:
    chat_id: str
    status: str
    timezone: str
    reminders: bool
    upsets: bool
    finals: bool
    lead_times: list[str]
    quiet_hours: str            # "22:00–07:00" or "off"
    tournaments: list[TournamentFollow] = field(default_factory=list)


@dataclass
class Overview:
    subscribers: int
    active: int
    paused: int
    subscriptions: int
    competitions: int
    last_delivery_utc: str | None
    last_delivery_stats: str | None


@dataclass
class Popularity:
    tournaments: list[tuple[str, int]]   # (display name, subscriber count)
    teams: list[tuple[str, int]]


def build_overview(sub_store: SubscriberStore, state: StateStore) -> Overview:
    subscribers = sub_store.list_subscribers()
    active = sum(1 for s in subscribers if s.is_active)
    return Overview(
        subscribers=len(subscribers),
        active=active,
        paused=len(subscribers) - active,
        subscriptions=sub_store.subscription_count(),
        competitions=len(sub_store.active_competitions()),
        last_delivery_utc=state.get_meta("last_delivery_utc"),
        last_delivery_stats=state.get_meta("last_delivery_stats"),
    )


def _tournaments_for(sub_store: SubscriberStore, chat_id: str) -> list[TournamentFollow]:
    subs = sub_store.list_subscriptions(chat_id)
    out: list[TournamentFollow] = []
    for code, season in sorted({(s.competition_code, s.season) for s in subs}):
        teams = [s.team for s in subs if s.competition_code == code and s.season == season]
        all_teams = ALL_TEAMS in teams
        out.append(TournamentFollow(
            code=code, name=competition_name(code), season=season,
            teams=[] if all_teams else sorted(teams), all_teams=all_teams,
        ))
    return out


def build_user_rows(sub_store: SubscriberStore) -> list[UserRow]:
    rows: list[UserRow] = []
    for s in sub_store.list_subscribers():
        rows.append(UserRow(
            chat_id=s.chat_id, status=s.status, timezone=s.timezone,
            reminders=s.reminders_enabled, upsets=s.upsets_enabled, finals=s.finals_enabled,
            lead_times=list(s.lead_times),
            quiet_hours=f"{s.quiet_start}–{s.quiet_end}" if s.quiet_enabled else "off",
            tournaments=_tournaments_for(sub_store, s.chat_id),
        ))
    return rows


def build_popularity(sub_store: SubscriberStore, *, team_limit: int = 20) -> Popularity:
    tournaments = [(competition_name(code), n) for code, n in sub_store.competition_popularity()]
    return Popularity(tournaments=tournaments, teams=sub_store.team_popularity(team_limit))


def overview_json(sub_store: SubscriberStore, state: StateStore) -> dict:
    return asdict(build_overview(sub_store, state))


def subscribers_json(sub_store: SubscriberStore) -> list[dict]:
    return [asdict(r) for r in build_user_rows(sub_store)]
