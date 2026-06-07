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
- [ ] **Phase 2 — Data providers.** `SportsDataProvider` interface + football-data.org & API-Football adapters; failover.  ← _in review_
- [ ] **Phase 3 — Alert engine.** Reminder scheduling, result detection, upset heuristic, dedup.
- [ ] **Phase 4 — WhatsApp delivery.** `WhatsAppSender` (Meta Cloud primary, Twilio fallback), formatting, dry-run.
- [ ] **Phase 5 — Runtime.** Scheduler/poller loop, timezone rendering, quiet-hours defer/suppress.
- [ ] **Phase 6 — Hardening.** Retries, backoff, failover, logging + status view, tests, docs (README + runbook + deploy guide).

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
