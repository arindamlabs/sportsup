"""Pure onboarding state — no Telegram objects, so it's fully unit-testable.

Holds what the user has picked so far as they walk through the inline-keyboard flow
(tournaments → teams per tournament → alert options → confirm) and knows how to persist
the result. The async flow in :mod:`sportsup.bot.onboarding` owns rendering and IO; this
module owns the data and the transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..catalog import competition_name, get_competition
from ..subscribers import ALL_TEAMS, Subscriber, SubscriberStore

# Steps, in order.
STEP_TOURNAMENTS = "tournaments"
STEP_TEAMS = "teams"
STEP_OPTIONS = "options"
STEP_CONFIRM = "confirm"

# Lead-time presets offered in onboarding (value -> label).
LEAD_CHOICES: list[tuple[str, str]] = [
    ("1d", "1 day before"),
    ("12h", "12 hours"),
    ("3h", "3 hours"),
    ("1h", "1 hour"),
    ("30m", "30 min"),
]
DEFAULT_LEADS = ["1d", "1h"]
TEAM_PAGE_SIZE = 8


@dataclass
class OnboardingState:
    # Selections (tournaments kept ordered for a stable team-picking sequence).
    tournaments: list[str] = field(default_factory=list)        # competition codes
    rosters: dict[str, list[str]] = field(default_factory=dict)  # code -> sorted team names
    teams: dict[str, set[str]] = field(default_factory=dict)     # code -> chosen team names
    all_teams: set[str] = field(default_factory=set)            # codes set to "all teams"

    reminders: bool = True
    upsets: bool = True
    finals: bool = False
    leads: list[str] = field(default_factory=lambda: list(DEFAULT_LEADS))

    step: str = STEP_TOURNAMENTS
    team_index: int = 0   # which selected tournament we're picking teams for
    page: int = 0         # pagination within the current team roster

    # --- tournaments ------------------------------------------------------

    def toggle_tournament(self, code: str) -> None:
        if code in self.tournaments:
            self.tournaments.remove(code)
            self.teams.pop(code, None)
            self.all_teams.discard(code)
        else:
            self.tournaments.append(code)

    # --- teams ------------------------------------------------------------

    @property
    def current_code(self) -> str | None:
        if 0 <= self.team_index < len(self.tournaments):
            return self.tournaments[self.team_index]
        return None

    def current_roster(self) -> list[str]:
        code = self.current_code
        return self.rosters.get(code, []) if code else []

    def toggle_all_teams(self, code: str) -> None:
        if code in self.all_teams:
            self.all_teams.discard(code)
        else:
            self.all_teams.add(code)
            self.teams.pop(code, None)  # "all" supersedes explicit picks

    def toggle_team(self, code: str, name: str) -> None:
        self.all_teams.discard(code)  # picking a specific team clears "all"
        chosen = self.teams.setdefault(code, set())
        if name in chosen:
            chosen.remove(name)
        else:
            chosen.add(name)

    def team_selected(self, code: str, name: str) -> bool:
        return name in self.teams.get(code, set())

    def has_selection_for(self, code: str) -> bool:
        return code in self.all_teams or bool(self.teams.get(code))

    def advance_team(self) -> bool:
        """Move to the next selected tournament's team picker. Returns False when
        there are no more — the caller then moves to the options step."""
        self.page = 0
        self.team_index += 1
        return self.current_code is not None

    def page_count(self) -> int:
        roster = self.current_roster()
        if not roster:
            return 1
        return (len(roster) + TEAM_PAGE_SIZE - 1) // TEAM_PAGE_SIZE

    def page_slice(self) -> list[str]:
        start = self.page * TEAM_PAGE_SIZE
        return self.current_roster()[start:start + TEAM_PAGE_SIZE]

    def set_page(self, page: int) -> None:
        self.page = max(0, min(page, self.page_count() - 1))

    # --- options ----------------------------------------------------------

    def toggle_option(self, key: str) -> None:
        setattr(self, key, not getattr(self, key))

    def toggle_lead(self, lead: str) -> None:
        if lead in self.leads:
            self.leads.remove(lead)
        else:
            self.leads.append(lead)

    def use_defaults(self) -> None:
        self.reminders, self.upsets, self.finals = True, True, False
        self.leads = list(DEFAULT_LEADS)

    # --- persistence ------------------------------------------------------

    def summary_lines(self) -> list[str]:
        """Human-readable recap for the confirmation screen."""
        lines: list[str] = []
        for code in self.tournaments:
            if code in self.all_teams:
                who = "all teams"
            else:
                picked = sorted(self.teams.get(code, set()))
                who = ", ".join(picked) if picked else "(no teams picked — skipped)"
            lines.append(f"• <b>{competition_name(code)}</b>: {who}")
        types = [n for n, on in
                 (("reminders", self.reminders), ("upsets", self.upsets), ("final scores", self.finals)) if on]
        lines.append(f"• Alerts: {', '.join(types) or 'none'}")
        if self.reminders:
            lines.append(f"• Reminder timing: {', '.join(self.leads) or '(none)'}")
        return lines

    def has_any_subscription(self) -> bool:
        return any(self.has_selection_for(c) for c in self.tournaments)


def commit_onboarding(store: SubscriberStore, chat_id: str, st: OnboardingState) -> Subscriber:
    """Persist the onboarding result: upsert the subscriber's preferences and add a
    subscription per chosen team (or ALL_TEAMS). Existing prefs (timezone/quiet hours,
    set later via /settings) are preserved. Idempotent on the subscription rows."""
    existing = store.get_subscriber(chat_id)
    sub = existing or Subscriber(chat_id=chat_id)
    sub.reminders_enabled = st.reminders
    sub.upsets_enabled = st.upsets
    sub.finals_enabled = st.finals
    sub.lead_times = st.leads or list(DEFAULT_LEADS)
    store.upsert_subscriber(sub)

    for code in st.tournaments:
        comp = get_competition(code)
        if comp is None:
            continue
        if code in st.all_teams:
            store.add_subscription(chat_id, code, comp.season, ALL_TEAMS)
        else:
            for name in sorted(st.teams.get(code, set())):
                store.add_subscription(chat_id, code, comp.season, name)
    return store.get_subscriber(chat_id)
