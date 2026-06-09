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
    Command("mysubs", "List the tournaments and teams you follow", True),
    Command("edit", "Change your alert types and reminder timing", True),
    Command("settings", "Set your timezone and quiet hours", True),
    Command("unsubscribe", "Stop following a single team or tournament", True),
    Command("pause", "Pause all alerts without losing your setup", True),
    Command("resume", "Resume alerts after a pause", True),
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

# --- management (Phase 10) -------------------------------------------------

NOT_SUBSCRIBED = "You're not subscribed yet. Send /start to set up your alerts."
PAUSED = "⏸️ Alerts paused. Your setup is saved — send /resume to turn them back on."
ALREADY_PAUSED = "Your alerts are already paused. Send /resume to turn them back on."
RESUMED = "▶️ Alerts resumed. Welcome back!"
ALREADY_ACTIVE = "Your alerts are already on. Use /pause if you want a break."

EDIT_TITLE = "⚙️ <b>Edit alert settings</b>\nToggle alert types and reminder timing, then Save."
EDIT_SAVED = "✅ Saved. Use /subscribe to add teams or /unsubscribe to remove them."
EDIT_NUDGE = "To add or remove teams, use /subscribe or /unsubscribe."

UNSUB_NONE = "You have no subscriptions to remove. Send /subscribe to add some."
UNSUB_TITLE = "➖ <b>Unsubscribe</b>\nPick a tournament to edit, or remove it entirely."
UNSUB_DONE = "Done. Send /mysubs to see what you're following."
UNSUB_EMPTY = "You've removed everything. Send /subscribe to follow teams again."
RATE_LIMITED = "🐢 You're sending messages very fast — give me a moment, then try again."

SET_TZ_PROMPT = ("🕑 Send me your timezone as an <b>IANA name</b>, e.g. "
                 "<code>America/Los_Angeles</code>, <code>Europe/London</code>, or "
                 "<code>Asia/Kolkata</code>.")
SET_TZ_INVALID = ("That doesn't look like a valid timezone. Use an IANA name like "
                  "<code>Europe/Berlin</code> — see the 'TZ identifier' column on Wikipedia's "
                  "list of tz database time zones.")
SET_DONE = "⚙️ Settings saved. Send /mysubs to review everything."
