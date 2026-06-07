"""SportsUp entrypoint / CLI.

Phase 1 commands:
  validate  — load and validate config + boot the state store, then exit
  plan      — print what *would* be tracked (events, teams, alert toggles, schedule)
  run       — boot everything; the live scheduler/poller arrives in Phase 5

Run with:  python -m sportsup <command> [--config config.yaml]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__
from .config import AppConfig, load_config
from .logging_setup import setup_logging
from .settings import Secrets
from .state import StateStore

DEFAULT_CONFIG = "config.yaml"
DEFAULT_DB = "data/sportsup.sqlite"


def _resolve_dry_run(config: AppConfig, secrets: Secrets) -> bool:
    """Env override wins; otherwise fall back to the config value."""
    if secrets.dry_run_override is not None:
        return secrets.dry_run_override
    return config.delivery.dry_run


def _print_plan(config: AppConfig, secrets: Secrets, logger) -> None:
    tz = config.tzinfo
    now_local = datetime.now(timezone.utc).astimezone(tz)
    dry_run = _resolve_dry_run(config, secrets)

    logger.info("=" * 64)
    logger.info("SportsUp v%s — execution plan", __version__)
    logger.info("=" * 64)
    logger.info("Timezone        : %s  (now: %s)", config.timezone, now_local.strftime("%Y-%m-%d %H:%M %Z"))
    logger.info("Delivery        : provider=%s  dry_run=%s", config.delivery.provider, dry_run)
    logger.info(
        "Quiet hours     : %s (%s–%s, on-hit: %s)",
        "on" if config.quiet_hours.enabled else "off",
        config.quiet_hours.start.strftime("%H:%M"),
        config.quiet_hours.end.strftime("%H:%M"),
        config.quiet_hours.behavior,
    )
    logger.info("Reminder leads  : %s", ", ".join(config.reminders.lead_times) or "(none)")
    logger.info(
        "Shock detection : sensitivity=%.2f  min_gap=%d  form_window=%d  priority=%s",
        config.shock_detection.sensitivity,
        config.shock_detection.min_position_gap,
        config.shock_detection.form_window,
        "→".join(config.shock_detection.signal_priority),
    )

    creds = secrets.configured_providers()
    logger.info(
        "Credentials     : %s",
        ", ".join(f"{k}={'set' if v else 'MISSING'}" for k, v in creds.items()),
    )

    enabled = config.enabled_events
    logger.info("-" * 64)
    logger.info("Tracking %d of %d configured event(s):", len(enabled), len(config.events))
    for ev in config.events:
        status = "ON " if ev.enabled else "off"
        on_types = [name for name, flag in vars(ev.alerts).items() if flag]
        logger.info("  [%s] %s  (%s, season %s)", status, ev.name, ev.competition_code, ev.season)
        if ev.enabled:
            logger.info("        teams (%d): %s", len(ev.teams), ", ".join(ev.teams) or "ALL")
            logger.info("        alerts: %s", ", ".join(on_types) or "(none enabled)")
    logger.info("=" * 64)


def cmd_validate(args, logger) -> int:
    config = load_config(args.config)
    logger.info("Config OK: %s", Path(args.config).resolve())
    store = StateStore(args.db)
    store.set_meta("last_validate_utc", datetime.now(timezone.utc).isoformat())
    logger.info("State store OK: %s (%d alerts recorded so far)", store.db_path, store.sent_count())
    store.close()
    return 0


def cmd_plan(args, logger) -> int:
    config = load_config(args.config)
    secrets = Secrets()
    _print_plan(config, secrets, logger)
    return 0


def cmd_run(args, logger) -> int:
    config = load_config(args.config)
    secrets = Secrets()
    store = StateStore(args.db)
    _print_plan(config, secrets, logger)
    logger.info("State store ready at %s", store.db_path)
    logger.warning(
        "Phase 1 skeleton: data providers (Phase 2), alert engine (Phase 3), "
        "WhatsApp delivery (Phase 4) and the live scheduler (Phase 5) are not wired up yet. "
        "Nothing will be sent. Exiting cleanly."
    )
    store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    # Common flags live on a shared parent so they're accepted *after* the subcommand
    # (e.g. `sportsup validate --config foo.yaml`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=DEFAULT_CONFIG, help="path to config.yaml")
    common.add_argument("--db", default=DEFAULT_DB, help="path to SQLite state store")
    common.add_argument("--log-level", default="INFO")

    parser = argparse.ArgumentParser(prog="sportsup", description=__doc__, parents=[common])
    parser.add_argument("--version", action="version", version=f"sportsup {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", parents=[common], help="validate config + state store, then exit")
    sub.add_parser("plan", parents=[common], help="print what would be tracked")
    sub.add_parser("run", parents=[common], help="boot everything (scheduler arrives in Phase 5)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = setup_logging(args.log_level)

    handlers = {"validate": cmd_validate, "plan": cmd_plan, "run": cmd_run}
    try:
        return handlers[args.command](args, logger)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Configuration error: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
