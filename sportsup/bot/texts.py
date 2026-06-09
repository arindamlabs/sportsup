"""User-facing strings and the single source of truth for the command catalog.

``COMMANDS`` drives BOTH the in-chat command menu (Telegram ``setMyCommands``) and the
``/help`` text, so the two can never drift — satisfying the requirement that ``/help``
list every command. Commands whose handlers aren't wired yet are marked ``active=False``:
they still appear in ``/help`` (as "coming soon") but are kept out of the tap-to-run menu
so we never advertise a button that does nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

BOT_NAME = "SportsUp"


@dataclass(frozen=True)
class Command:
    name: str          # without the leading slash
    description: str    # one line — shown in /help and the command menu
    active: bool        # is the handler wired up yet?


# Order is the order shown in /help and the menu. Keep descriptions one line.
COMMANDS: list[Command] = [
    Command("start", "Subscribe and see what SportsUp can do", True),
    Command("help", "Show every command and how it works", True),
    Command("subscribe", "Follow tournaments and teams (guided setup)", True),
    Command("mysubs", "List the tournaments and teams you follow", False),
    Command("unsubscribe", "Stop following a single team or tournament", False),
    Command("edit", "Change your teams, alert types, or reminder timing", False),
    Command("settings", "Set your timezone and quiet hours", False),
    Command("pause", "Pause all alerts without losing your setup", False),
    Command("resume", "Resume alerts after a pause", False),
    Command("stop", "Unsubscribe completely and delete your data", True),
]


def active_commands() -> list[Command]:
    """Commands with a wired handler — what we register in the Telegram menu."""
    return [c for c in COMMANDS if c.active]


def help_text() -> str:
    lines = [
        f"<b>{BOT_NAME} — commands</b>",
        "",
        "I send alerts for the teams you follow: upcoming-match reminders, "
        "shock results (upsets), and (optionally) final scores — in your timezone.",
        "",
    ]
    for c in COMMANDS:
        suffix = "" if c.active else "  <i>(coming soon)</i>"
        lines.append(f"/{c.name} — {c.description}{suffix}")
    lines += [
        "",
        "You can pause anytime with /pause, and /stop removes everything. "
        "Your data is only ever used to send you these alerts.",
    ]
    return "\n".join(lines)


def welcome_text(*, created: bool) -> str:
    if created:
        return (
            f"👋 Welcome to <b>{BOT_NAME}</b>!\n\n"
            "I'll alert you about the teams you follow — match reminders, upsets, and "
            "optional final scores, in your timezone.\n\n"
            "Let's set you up 👇"
        )
    return (
        f"👋 Welcome back to <b>{BOT_NAME}</b>! You're already subscribed.\n\n"
        "Use /subscribe to follow more, or /help to see everything I can do."
    )


# --- onboarding (Phase 9) --------------------------------------------------

ONB_TOURNAMENTS = "🏆 <b>Pick your tournaments</b>\nTap to select as many as you like, then <b>Next</b>."
ONB_OPTIONS = ("🔔 <b>Alert settings</b>\nChoose which alerts you want and how early to be "
               "reminded, then <b>Review</b>.")
ONB_CANCELLED = "Setup cancelled — nothing was saved. Send /subscribe to start again."
ONB_EXPIRED = "This menu expired. Send /subscribe to start again."


def onb_teams_text(competition: str, *, has_roster: bool) -> str:
    if not has_roster:
        return (f"⚽ <b>{competition}</b>\nI can't load the team list right now — tap "
                "<b>⭐ All teams</b> to follow the whole competition, then <b>Done</b>.")
    return (f"⚽ <b>{competition}</b>\nPick the teams to follow (or <b>⭐ All teams</b>), "
            "then <b>Done</b>.")


def onb_confirm_text(lines: list[str]) -> str:
    return "\n".join(["📋 <b>Confirm your subscription</b>", ""] + lines)


def onboarding_done_text(n: int) -> str:
    teams = f"{n} team{'s' if n != 1 else ''}"
    return (f"🎉 You're all set — following {teams}.\n\n"
            "I'll send alerts as matches approach. Use /subscribe to add more, "
            "/pause to mute, or /help to see everything.")


STOP_CONFIRM = (
    "⚠️ This will <b>delete all your subscriptions and data</b> and stop every alert.\n\n"
    "Are you sure?"
)
STOP_DONE = "✅ Done — you're fully unsubscribed and your data is deleted. Send /start anytime to come back."
STOP_NOTHING = "You weren't subscribed, so there was nothing to delete. Send /start to subscribe."
STOP_CANCELLED = "👍 Cancelled — nothing was changed. You're still subscribed."

UNKNOWN_COMMAND = "I don't recognise that command. Send /help to see what I can do."
GREETING_FALLBACK = "👋 Send /help to see what I can do, or /start to subscribe."
