"""SportsUp entrypoint / CLI.

Commands:
  validate  — load and validate config + boot the state store, then exit
  plan      — print what *would* be tracked (events, teams, alert toggles, schedule)
  providers — probe configured data providers (connectivity/auth health)
  fixtures  — fetch & print upcoming fixtures for watched teams (read-only)
  run       — boot everything; the live scheduler/poller arrives in Phase 5

Run with:  python -m sportsup <command> [--config config.yaml]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .alerts import AlertEngine
from .config import AppConfig, load_config
from .logging_setup import setup_logging
from .providers import ProviderError
from .providers.router import build_router
from .providers.teams import TeamResolver
from .settings import Secrets
from .state import StateStore
from .sync import collect_watched_fixtures

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


def _build_router_or_warn(secrets: Secrets, config: AppConfig, logger):
    league_map = {
        e.competition_code: e.api_football_league
        for e in config.events
        if e.api_football_league is not None
    }
    router = build_router(secrets, league_map=league_map or None)
    if router is None:
        logger.error(
            "No data-provider credentials found. Set FOOTBALL_DATA_API_KEY (and "
            "optionally API_FOOTBALL_KEY) in .env. See .env.example."
        )
    return router


def cmd_providers(args, logger) -> int:
    config = load_config(args.config)
    secrets = Secrets()
    router = _build_router_or_warn(secrets, config, logger)
    if router is None:
        return 2
    logger.info("Probing %d provider(s)...", len(router.providers))
    ok = True
    for name, healthy in router.health().items():
        logger.info("  %-20s %s", name, "OK" if healthy else "UNREACHABLE / auth failed")
        ok = ok and healthy
    return 0 if ok else 1


def cmd_fixtures(args, logger) -> int:
    config = load_config(args.config)
    secrets = Secrets()
    router = _build_router_or_warn(secrets, config, logger)
    if router is None:
        return 2

    tz = config.tzinfo
    logger.info(
        "Upcoming fixtures for watched teams (next %d days, times in %s):",
        config.fixture_sync_lookahead_days, config.timezone,
    )
    total = 0
    for ef in collect_watched_fixtures(config, router):
        logger.info("-" * 60)
        logger.info("%s", ef.event.name)
        if ef.error:
            logger.error("  fetch failed: %s", ef.error)
            continue
        if not ef.fixtures:
            logger.info("  (no upcoming fixtures for watched teams in window)")
        for fx in ef.fixtures:
            local = fx.utc_kickoff.astimezone(tz)
            logger.info(
                "  %s  %s vs %s  [%s]",
                local.strftime("%a %d %b %H:%M %Z"),
                fx.home.name, fx.away.name, fx.status.value,
            )
            total += 1
        if ef.idle_teams:
            logger.info(
                "  watched teams with no match in the next %d days: %s",
                config.fixture_sync_lookahead_days, ", ".join(ef.idle_teams),
            )
        if ef.unknown_teams:
            logger.warning(
                "  watchlist names not found in the %s team list — check spelling: %s",
                ef.event.name, ", ".join(ef.unknown_teams),
            )
    logger.info("-" * 60)
    logger.info("%d watched fixture(s) found.", total)
    return 0


def cmd_alerts(args, logger) -> int:
    """Dry-run preview of the alert engine: scheduled reminders + any result/upset alerts.
    Does NOT mark anything as sent (that happens at delivery time in Phase 4/5)."""
    config = load_config(args.config)
    secrets = Secrets()
    router = _build_router_or_warn(secrets, config, logger)
    if router is None:
        return 2
    store = StateStore(args.db)
    engine = AlertEngine(config, store)
    now = datetime.now(timezone.utc)

    # 1) Upcoming-fixture reminders (planned off synced fixtures).
    logger.info("=" * 60)
    logger.info("SCHEDULED REMINDERS (times in %s)", config.timezone)
    logger.info("=" * 60)
    reminder_count = 0
    fixtures_by_event = {ef.event.id: ef for ef in collect_watched_fixtures(config, router, now=now)}
    for ev in config.enabled_events:
        ef = fixtures_by_event.get(ev.id)
        if ef is None or ef.error:
            continue
        reminders = engine.unsent(engine.plan_reminders(ev, ef.fixtures, now=now))
        for a in reminders:
            local = a.scheduled_for.astimezone(config.tzinfo).strftime("%a %d %b %H:%M %Z")
            logger.info("  %s  %s", local, a.summary)
            reminder_count += 1
    if reminder_count == 0:
        logger.info("  (none scheduled in the current window)")

    # 2) Recent results -> final-score / shock alerts.
    logger.info("=" * 60)
    logger.info("RESULT ALERTS (finished matches in the last %d days)", args.results_days)
    logger.info("=" * 60)
    result_count = 0
    since = now - timedelta(days=args.results_days)
    for ev in config.enabled_events:
        resolver = TeamResolver(ev.teams)
        try:
            results = router.get_results(
                competition_code=ev.competition_code, season=ev.season,
                date_from=since, date_to=now,
            )
        except ProviderError as exc:
            logger.warning("  %s: results unavailable (%s)", ev.name, exc)
            continue
        watched = [
            r for r in results
            if not ev.teams or resolver.is_watched(r.fixture.home.name)
            or resolver.is_watched(r.fixture.away.name)
        ]
        standings = None
        if ev.alerts.shock_result and watched:
            try:
                standings = router.get_standings(competition_code=ev.competition_code, season=ev.season)
            except ProviderError:
                standings = None

        def odds_lookup(r):
            try:
                return router.get_match_odds(
                    competition_code=ev.competition_code, season=ev.season,
                    home_team=r.fixture.home.name, away_team=r.fixture.away.name,
                    kickoff=r.fixture.utc_kickoff,
                )
            except ProviderError:
                return None

        alerts = engine.unsent(
            engine.evaluate_results(ev, watched, odds_lookup=odds_lookup, standings=standings)
        )
        for a in alerts:
            logger.info("  %s", a.summary)
            result_count += 1
    if result_count == 0:
        logger.info("  (no finished watched matches / no upsets in window)")

    logger.info("=" * 60)
    logger.info("Dry-run: %d reminder(s) + %d result alert(s). Nothing sent or marked.",
                reminder_count, result_count)
    store.close()
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
    sub.add_parser("providers", parents=[common], help="probe data-provider health")
    sub.add_parser("fixtures", parents=[common], help="fetch upcoming fixtures for watched teams")
    p_alerts = sub.add_parser("alerts", parents=[common], help="dry-run preview of reminders + result alerts")
    p_alerts.add_argument("--results-days", type=int, default=3, help="how far back to scan for finished matches")
    sub.add_parser("run", parents=[common], help="boot everything (scheduler arrives in Phase 5)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = setup_logging(args.log_level)

    handlers = {
        "validate": cmd_validate,
        "plan": cmd_plan,
        "providers": cmd_providers,
        "fixtures": cmd_fixtures,
        "alerts": cmd_alerts,
        "run": cmd_run,
    }
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
