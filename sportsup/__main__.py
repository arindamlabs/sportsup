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
from .alerts.models import Alert, AlertType
from .config import AppConfig, load_config
from .delivery import ConsoleSender, OutboundMessage, build_sender, format_alert, message_for_alert
from .logging_setup import setup_logging
from .pipeline import gather_result_alerts, plan_all_reminders
from .providers import Fixture, MatchStatus, TeamRef
from .providers.router import build_router
from .runtime import SchedulerRuntime
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
    Does NOT mark anything as sent (that happens at delivery time via `notify`)."""
    config = load_config(args.config)
    secrets = Secrets()
    router = _build_router_or_warn(secrets, config, logger)
    if router is None:
        return 2
    store = StateStore(args.db)
    engine = AlertEngine(config, store)
    now = datetime.now(timezone.utc)

    logger.info("=" * 60)
    logger.info("SCHEDULED REMINDERS (times in %s)", config.timezone)
    logger.info("=" * 60)
    reminders = sorted(plan_all_reminders(config, router, engine, now), key=lambda a: a.scheduled_for)
    for a in reminders:
        local = a.scheduled_for.astimezone(config.tzinfo).strftime("%a %d %b %H:%M %Z")
        logger.info("  %s  %s", local, a.summary)
    if not reminders:
        logger.info("  (none scheduled in the current window)")

    logger.info("=" * 60)
    logger.info("RESULT ALERTS (finished matches in the last %d days)", args.results_days)
    logger.info("=" * 60)
    results = gather_result_alerts(config, router, engine, now,
                                   lookback_days=args.results_days, logger=logger)
    for a in results:
        logger.info("  %s", a.summary)
    if not results:
        logger.info("  (no finished watched matches / no upsets in window)")

    logger.info("=" * 60)
    logger.info("Dry-run: %d reminder(s) + %d result alert(s). Nothing sent or marked.",
                len(reminders), len(results))
    store.close()
    return 0


def _sample_alerts(now) -> list[Alert]:
    """Synthetic alerts (not from live data) to preview message formatting."""
    base = dict(provider="sample", competition_code="WC", season=2026)
    fx_up = Fixture(**base, provider_fixture_id="0", utc_kickoff=now + timedelta(days=1),
                    status=MatchStatus.TIMED, home=TeamRef(name="Brazil"), away=TeamRef(name="Morocco"))
    fx_done = Fixture(**base, provider_fixture_id="1", utc_kickoff=now - timedelta(hours=2),
                      status=MatchStatus.FINISHED, home=TeamRef(name="Saudi Arabia"), away=TeamRef(name="Argentina"))
    ctx = {"competition": "FIFA World Cup 2026"}
    return [
        Alert(AlertType.FIXTURE_REMINDER, "world-cup-2026", "sample:rem", fx_up,
              summary="", lead_label="1d", context={**ctx}),
        Alert(AlertType.FINAL_SCORE, "world-cup-2026", "sample:final", fx_done,
              summary="", context={**ctx, "home_score": 2, "away_score": 1}),
        Alert(AlertType.SHOCK_RESULT, "world-cup-2026", "sample:shock", fx_done,
              summary="", context={**ctx, "home_score": 2, "away_score": 1,
                                   "signal_used": "odds",
                                   "reason": "Saudi Arabia won with only ~6% pre-match implied chance (vs Argentina ~83%)"}),
    ]


def cmd_whatsapp_test(args, logger) -> int:
    """Preview formatted messages, and with --live send a real test message:
    the built-in 'hello_world' template, or (with --template) your configured
    alert_template_name carrying a sample upset alert — to confirm the live path."""
    config = load_config(args.config)
    secrets = Secrets()
    recipient = secrets.whatsapp_recipient
    if not recipient:
        logger.error("Set WHATSAPP_RECIPIENT (your number in E.164) in .env.")
        return 2

    tz = config.tzinfo
    console = ConsoleSender()
    logger.info("Message formatting previews (these are what alerts will look like):")
    for a in _sample_alerts(datetime.now(timezone.utc)):
        console.send(OutboundMessage(recipient=recipient, text=format_alert(a, tz), dedup_key=a.dedup_key))

    # The sample alert used for a live --template send (the upset is the richest test).
    sample = _sample_alerts(datetime.now(timezone.utc))[-1]

    if not args.live:
        logger.info("")
        if args.template:
            tmpl = config.delivery.alert_template_name
            if tmpl:
                param = message_for_alert(sample, config, recipient).template_components[0]["parameters"][0]["text"]
                logger.info("With --live, would send via template '%s' with {{1}} = %s", tmpl, param)
            else:
                logger.info("Set delivery.alert_template_name in config.yaml to test a custom template.")
        else:
            logger.info("Add --live to send a real 'hello_world' template to %s and confirm delivery.", recipient)
        return 0

    if not (secrets.whatsapp_access_token and secrets.whatsapp_phone_number_id):
        logger.error("Live send needs WHATSAPP_ACCESS_TOKEN + WHATSAPP_PHONE_NUMBER_ID in .env.")
        return 2

    from .delivery.meta_cloud import MetaCloudSender
    sender = MetaCloudSender(secrets.whatsapp_access_token, secrets.whatsapp_phone_number_id)

    if args.template:
        tmpl = config.delivery.alert_template_name
        if not tmpl:
            logger.error("Set delivery.alert_template_name in config.yaml first (and ensure the template is Approved).")
            return 2
        message = message_for_alert(sample, config, recipient)  # template mode (config has the name)
        logger.info("Sending live message via template '%s' to %s ...", tmpl, recipient)
        expect = f"an alert from template '{tmpl}'"
    else:
        message = OutboundMessage(recipient=recipient, template_name="hello_world", template_lang="en_US")
        logger.info("Sending live 'hello_world' template to %s ...", recipient)
        expect = "'Hello World'"

    res = sender.send(message)
    if res.ok:
        logger.info("LIVE SEND OK (message id %s). Check WhatsApp on %s — you should see %s.",
                    res.provider_message_id, recipient, expect)
        return 0
    logger.error("LIVE SEND FAILED: %s (code %s)%s", res.error, res.error_code,
                 "  [template not approved yet? check status in WhatsApp Manager]"
                 if res.error_code in ("132001", "132000", "132012", "132015") else "")
    return 1


def cmd_notify(args, logger) -> int:
    """Run the engine once and deliver due alerts through the configured sender.
    Console (dry-run) by default; this is what Phase 5's scheduler will call on a loop."""
    config = load_config(args.config)
    secrets = Secrets()
    router = _build_router_or_warn(secrets, config, logger)
    if router is None:
        return 2
    recipient = secrets.whatsapp_recipient
    if not recipient:
        logger.error("Set WHATSAPP_RECIPIENT in .env.")
        return 2
    sender = build_sender(config, secrets)
    if sender is None:
        return 2

    store = StateStore(args.db)
    engine = AlertEngine(config, store)
    now = datetime.now(timezone.utc)
    tz = config.tzinfo

    # Reminders whose lead time has arrived, plus result/shock alerts for finished matches.
    due = [a for a in plan_all_reminders(config, router, engine, now, include_past=True)
           if a.scheduled_for <= now]
    due += gather_result_alerts(config, router, engine, now,
                                lookback_days=args.results_days, logger=logger)

    sent = failed = 0
    for a in due:
        res = sender.send(message_for_alert(a, config, recipient))
        if res.ok:
            sent += 1
            # Only persist dedup on a *real* delivery so dry-runs stay repeatable.
            if res.provider != "console":
                engine.mark_sent(a)
        else:
            failed += 1
            logger.error("send failed for %s: %s (code %s)", a.dedup_key, res.error, res.error_code)

    note = "dry-run: nothing marked sent." if sender.name == "console" else ""
    logger.info("notify via %s: %d due, %d sent, %d failed. %s", sender.name, len(due), sent, failed, note)
    store.close()
    return 0


def cmd_status(args, logger) -> int:
    """Show what's been sent and when the runtime last synced (reads the state store)."""
    config = load_config(args.config)
    store = StateStore(args.db)
    tz = config.tzinfo

    logger.info("SportsUp status")
    logger.info("  state store : %s", store.db_path)
    logger.info("  alerts sent : %d total", store.sent_count())
    last_sync = store.get_meta("last_fixture_sync_utc")
    if last_sync:
        local = datetime.fromisoformat(last_sync).astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
        logger.info("  last sync   : %s (%s fixtures cached)", local, store.get_meta("cached_fixture_count") or "?")
    else:
        logger.info("  last sync   : never (runtime not started yet)")

    recent = store.recent_sent(args.limit)
    logger.info("  recent sent (newest first, up to %d):", args.limit)
    for r in recent:
        local = datetime.fromisoformat(r["sent_at"]).astimezone(tz).strftime("%d %b %H:%M %Z")
        logger.info("    %s  %-16s %s", local, r["alert_type"] or "-", r["dedup_key"])
    if not recent:
        logger.info("    (nothing sent yet — run `notify` or `run` after matches start)")
    store.close()
    return 0


def cmd_run(args, logger) -> int:
    """Start the always-on scheduling runtime (or a single cycle with --once)."""
    config = load_config(args.config)
    secrets = Secrets()
    router = _build_router_or_warn(secrets, config, logger)
    if router is None:
        return 2
    recipient = secrets.whatsapp_recipient
    if not recipient:
        logger.error("Set WHATSAPP_RECIPIENT in .env.")
        return 2
    sender = build_sender(config, secrets)
    if sender is None:
        return 2

    store = StateStore(args.db)
    _print_plan(config, secrets, logger)
    runtime = SchedulerRuntime(config, router, sender, store, recipient)
    try:
        if args.once:
            runtime.run_once()
        else:
            runtime.run()  # blocks until interrupted
    finally:
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
    p_wa = sub.add_parser("whatsapp-test", parents=[common], help="preview message formatting; --live sends a real test message")
    p_wa.add_argument("--live", action="store_true", help="actually send a real test message via Meta Cloud API")
    p_wa.add_argument("--template", action="store_true",
                      help="send via the configured alert_template_name (instead of hello_world)")
    p_notify = sub.add_parser("notify", parents=[common], help="deliver due alerts via the configured sender (console if dry-run)")
    p_notify.add_argument("--results-days", type=int, default=3, help="how far back to scan for finished matches")
    p_status = sub.add_parser("status", parents=[common], help="show sent-alert history + last sync (no network)")
    p_status.add_argument("--limit", type=int, default=20, help="how many recent alerts to show")
    p_run = sub.add_parser("run", parents=[common], help="start the always-on scheduling runtime")
    p_run.add_argument("--once", action="store_true", help="run a single sync/fire/poll cycle and exit (cron-style)")
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
        "whatsapp-test": cmd_whatsapp_test,
        "notify": cmd_notify,
        "status": cmd_status,
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
