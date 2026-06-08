# SportsUp — Plan & Progress Tracker

Configurable personal tool that tracks sporting events and sends WhatsApp alerts for
watched teams. Single user, config-first, no-spam, ToS-aware.

## Locked Phase 0 decisions (approved 2026-06-07)
- **WhatsApp:** Meta WhatsApp Cloud API on a **free test number** (official, free, zero ban risk).
  Built behind a swappable `WhatsAppSender` interface; Twilio + real-number are documented fallbacks.
  Unofficial libs (whatsapp-web.js/Baileys) are **excluded** — ToS violation, rising 2026 enforcement.
- **Hosting:** develop on WSL2 + Docker; deploy the container to an **Oracle Always-Free ARM VM** (always-on).
- **Data:** **hybrid** behind a `SportsDataProvider` interface — football-data.org (fixtures/results/standings,
  10 req/min) + API-Football (pre-match odds, 100 req/day) for upset detection; failover between them.
- **Stack:** Python · pydantic · SQLite · APScheduler · httpx · Docker.
- **Timezone:** `America/Los_Angeles` (Pacific).

## Upset heuristic (full design in Phase 3)
Layered, explainable `upset_index` in [0,1]: odds-implied-probability (preferred) → standings/ranking-gap
fallback → form-differential tiebreaker. Tunable via `shock_detection` config. Logs *why* each upset fired.

## Phases (stop for review at each boundary)
- [x] **Phase 0 — Research & decisions.** Decision doc delivered; choices locked above.
- [x] **Phase 1 — Scaffold.** Config schema, state/dedup store, secrets handling, Docker, runnable skeleton.
- [x] **Phase 2 — Data providers.** `SportsDataProvider` interface + football-data.org & API-Football adapters; failover.
- [x] **Phase 3 — Alert engine.** Reminder scheduling, result detection, upset heuristic, dedup.
- [x] **Phase 4 — WhatsApp delivery.** `WhatsAppSender` (Meta Cloud primary, Twilio fallback), formatting, dry-run.
- [x] **Phase 5 — Runtime.** Scheduler/poller loop, timezone rendering, quiet-hours defer/suppress.
- [ ] **Phase 6 — Hardening.** Retries, backoff, failover, logging + status view, tests, docs (README + runbook + deploy guide).  ← _in review_

## Working agreement
- Small reviewable commits. Stop at each phase boundary for review.
- Dry-run/console mode from Phase 4 so logic is verifiable without sending real messages.
- Call out anything affecting cost / legality (ToS) / reliability before acting.

## Phase 1 — deliverables & acceptance
**Deliverables**
- `sportsup/config.py` — typed, validated config (events on/off, per-event teams, per-type toggles,
  lead-times, timezone, quiet hours, shock sensitivity). Loads `config.yaml`.
- `sportsup/settings.py` — secrets from `.env` (never committed).
- `sportsup/state.py` — SQLite dedup/state store, survives restarts.
- `sportsup/logging_setup.py`, `sportsup/__main__.py` — CLI: `validate` / `plan` / `run`.
- `config.example.yaml` (committed) + `config.yaml` (git-ignored, seeded with your watchlists).
- `.env.example`, `.gitignore`, `Dockerfile`, `docker-compose.yml`, `requirements.txt`, `pyproject.toml`.
- `tests/test_config.py`, `tests/test_state.py`.

**Acceptance criteria**
- `python -m sportsup validate` reports config + state OK.
- `python -m sportsup plan` prints tracked events, teams, toggles, schedule, credential status.
- Invalid config is rejected with a clear message (unknown tz, bad lead-time, duplicate event id, unknown field).
- State store persists `sent_alerts` and is idempotent (`mark_sent` twice → recorded once).
- `docker compose run --rm sportsup plan` works in WSL2.

**How to verify (you)**
- `pip install -r requirements.txt && python -m sportsup plan` → see your 18 WC + 5 EPL teams.
- Toggle an event `enabled: false` in `config.yaml`, re-run `plan` → it drops out.
- Break something (e.g. `timezone: Mars/Phobos`) → clear validation error, no crash.

## Phase 2 — deliverables & acceptance
**Deliverables**
- `sportsup/providers/` package:
  - `models.py` — normalized `TeamRef`/`Fixture`/`MatchResult`/`Standing`/`MatchOdds` (vendor-agnostic).
  - `base.py` — `SportsDataProvider` ABC, `Capability` enum, error taxonomy (`ProviderUnavailableError`/`RateLimitError`/`NotSupportedError`).
  - `http.py` — shared httpx client with bounded retry/backoff (Retry-After aware); injectable transport for offline tests.
  - `football_data.py` — football-data.org adapter (fixtures/results/standings).
  - `api_football.py` — API-Football adapter (odds; also fixtures/results/standings as fallback).
  - `teams.py` — alias-based team resolution (Man United↔Manchester United, South Korea↔Korea Republic, Türkiye↔Turkey, …); reports unmatched watchlist names.
  - `get_teams()` on the interface + both adapters: validates watchlist spellings against the full competition roster, so sync distinguishes a real typo (`unknown_teams`, warning) from a watched team that simply has no fixture in the window (`idle_teams`, neutral info).
  - `router.py` — capability routing + failover; `build_router(secrets)` factory (keyless → None).
- `sportsup/sync.py` — `collect_watched_fixtures()` read path (fetch + watchlist filter).
- CLI: `providers` (health probe) and `fixtures` (print upcoming watched fixtures in local tz).
- `tests/test_providers.py` — 8 offline tests (parsing, odds math, aliases, failover).

**Acceptance criteria**
- Both adapters parse fixtures/results/standings (+odds for API-Football) into normalized models — verified offline via `httpx.MockTransport`.
- Router fails over to the next capable provider on outage; odds requests only hit a provider that supports them.
- Team resolver matches your watchlist shorthand to provider canonical names and surfaces unmatched spellings.
- `fixtures`/`providers` exit cleanly with a clear message when no API keys are set.
- 18/18 tests pass.

**How to verify (you)** — needs a free [football-data.org token](https://www.football-data.org/client/register) in `.env`:
- `python -m sportsup providers` → both providers report OK (or just football-data.org if only that key is set).
- `python -m sportsup fixtures` → upcoming matches for your watched teams, kickoff in Pacific time. (World Cup 2026 fixtures are already published; EPL 2026-27 appears once the season is scheduled.)
- Without keys, both commands print a clear "set FOOTBALL_DATA_API_KEY" message and exit 2.

## Phase 3 — deliverables & acceptance
**Deliverables**
- `sportsup/alerts/` package:
  - `models.py` — `AlertType` (fixture_reminder / final_score / shock_result) + `Alert` (dedup_key, fixture, summary, scheduled_for, context).
  - `shock.py` — explainable `evaluate_upset()` → `UpsetEvaluation(is_upset, upset_index 0..1, signal_used, reason)`. Tries `signal_priority` order: odds (implied win-prob ≤ 1−sensitivity & below opponent) → standings (position gap ≥ min_position_gap) → form (recent-points gap over form_window). Draw/no-data → not an upset.
  - `engine.py` — `AlertEngine`: `plan_reminders()` (one per upcoming fixture × lead-time still in the future), `evaluate_results()` (final-score if enabled + shock if upset), `unsent()`/`mark_sent()` dedup against the SQLite store.
- CLI `alerts` — dry-run preview of scheduled reminders + result/upset alerts (does not mark sent).
- `tests/test_alerts.py` — 9 tests (odds/standings/form upset paths, draw, reminder windowing, results + dedup).

**Acceptance criteria**
- Reminders generated for each watched fixture at every configured lead-time, future-only, sorted; stable dedup keys.
- Shock heuristic flags genuine underdog wins and rejects favourite-wins/draws; severity + reason explained.
- Final-score alerts only when enabled; result alerts only for finished matches.
- Dedup is exactly-once and survives restarts (`mark_sent` idempotent).
- 29/29 tests pass.

**How to verify (you)**
- `python -m sportsup alerts` → scheduled `1d`/`1h` reminders for your World Cup teams in Pacific time (verified: 30 reminders), and any recent upsets once matches start.
- `python -m sportsup alerts --results-days 14` → widen the finished-match scan window.

## Phase 4 — deliverables & acceptance
**Deliverables**
- `sportsup/delivery/` package:
  - `base.py` — `WhatsAppSender` ABC + `OutboundMessage` (text or template) + `SendResult`.
  - `console.py` — `ConsoleSender` (dry-run: prints instead of sending).
  - `meta_cloud.py` — `MetaCloudSender` (Graph API `/{phone_number_id}/messages`; text + template; surfaces error codes incl. 131047 out-of-window).
  - `formatting.py` — `format_alert()` → clean WhatsApp text (emoji, *bold*, local-tz kickoff, score/upset framing).
  - `factory.py` — `build_sender()`: `dry_run` (env `SPORTSUP_DRY_RUN` overrides config) forces console; else builds the configured provider if creds present.
- `providers/http.py` — generalized to support POST (`post_json`) with the same retry/backoff.
- CLI: `whatsapp-test [--live]` (formatting previews; `--live` sends a real `hello_world` template) and `notify` (engine→formatter→sender; console in dry-run; marks dedup only on real delivery).
- `tests/test_delivery.py` — 10 tests (formatting, console, Meta success/error/template payload, factory selection).

**Acceptance criteria**
- Alerts render as readable WhatsApp messages in the configured timezone.
- Dry-run (default) never sends a real message; `notify` in dry-run marks nothing sent (repeatable).
- Meta sender returns a clear `SendResult` on success (message id) and failure (error + code).
- Provider is swappable purely via `delivery.provider` + factory.
- 39/39 tests pass.

**How to verify (you)**
- `python -m sportsup whatsapp-test` → see the three message styles (reminder/final/upset) printed.
- `python -m sportsup whatsapp-test --live` → receive a real "Hello World" on your WhatsApp (proves delivery end-to-end).
- `SPORTSUP_DRY_RUN=false python -m sportsup notify` → would deliver any currently-due alerts for real (none until matches start; reminders fire on schedule in Phase 5).

> **WhatsApp 24-hour-window note:** free-form text alerts deliver only inside WhatsApp's 24h
> customer-service window (i.e. within 24h of you messaging the bot). Outside it, Meta requires a
> pre-approved **utility template** — surfaced as error 131047. For reliable always-on alerts we'll
> add a simple approved utility template in Phase 6 (runbook); the test number's `hello_world`
> template already works any time for the connectivity check.

## Phase 5 — deliverables & acceptance
**Deliverables**
- `config.py` — new `scheduling` section (`fixture_sync_hours`, `reminder_check_minutes`, `result_poll_minutes`, `result_lookback_days`).
- `sportsup/pipeline.py` — shared `plan_all_reminders()` / `gather_result_alerts()` used by CLI and runtime (single source of truth).
- `sportsup/runtime.py`:
  - `in_quiet_hours()` (handles overnight windows) + pure `classify_reminder()` / `classify_result()` (send / wait / defer / drop).
  - `SchedulerRuntime` — APScheduler `BlockingScheduler` with three interval jobs (fixture sync, reminder firing, result polling); `run_once()` for cron/tests; delivers via the sender and marks dedup only on real sends.
- `engine.plan_reminders(..., include_past=True)` — catch-up for the runtime (deliver a missed reminder for a still-upcoming match) vs. forward-only preview for `alerts`.
- CLI `run [--once]` now starts the real runtime; `docker compose` runs it (`restart: unless-stopped`, dry-run safe by default).
- `tests/test_runtime.py` — 6 tests (quiet-hours windowing, classification matrix, `run_once` send + dedup).

**Acceptance criteria**
- Reminders fire at their lead times in local tz; quiet-hours `defer` postpones to after the window, `suppress` drops them; stale reminders (match already started) never fire.
- Result/upset alerts deliver on the poll cadence, gated by quiet hours.
- Everything dedups exactly-once across cycles and restarts.
- 45/45 tests pass; `run --once` completes a full cycle against live data.

**How to verify (you)**
- `python -m sportsup run --once` → one sync/fire/poll cycle (dry-run console; nothing sent).
- `python -m sportsup run` → always-on loop (Ctrl-C to stop). With `dry_run: false` it sends real alerts at lead times.
- `docker compose up -d` → runs the service in the background (still dry-run until you flip `SPORTSUP_DRY_RUN=false`).

## Phase 6 — deliverables & acceptance
**Deliverables**
- Resilience: `plan_reminders` skips postponed/cancelled/suspended fixtures; runtime jobs wrapped so a
  transient error logs and the scheduler keeps running; provider failover + retry/backoff already in place.
- Out-of-24h-window delivery: `delivery.alert_template_name` config + `message_for_alert()` builder sends
  alerts as an approved utility template (single-line `{{1}}`) when configured, else free-form text.
- `status` CLI — sent-alert history, totals, last sync (reads the store, no network); `state.recent_sent()`.
- Docs: `RUNBOOK.md` (operations, template creation, troubleshooting) + `DEPLOY.md` (Oracle/GCP always-on)
  + README updates.
- `tests/test_hardening.py` — 4 tests (postponed-skip, text vs template message, recent_sent).

**Acceptance criteria**
- Postponed/cancelled matches never generate reminders; a failing event doesn't stop the runtime.
- With a template configured, alerts send as a template (deliver any time); otherwise as text.
- `status` shows last sync + recent sends; runbook + deploy guide are complete and accurate.
- 49/49 tests pass.

**How to verify (you)**
- `python -m sportsup status` → totals, last sync, recent alerts.
- Create the `sportsup_alert` template (RUNBOOK), set `delivery.alert_template_name`, then a live alert
  delivers outside the 24h window.
- Follow `DEPLOY.md` to run it 24/7 on a free Oracle ARM VM.

---
**SportsUp is feature-complete.** Optional future work: Twilio sender implementation, multi-recipient
support, live in-match alerts (paid data tier), and a small web status page.
