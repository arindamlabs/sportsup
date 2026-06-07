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
- [ ] **Phase 1 — Scaffold.** Config schema, state/dedup store, secrets handling, Docker, runnable skeleton.  ← _in review_
- [ ] **Phase 2 — Data providers.** `SportsDataProvider` interface + football-data.org & API-Football adapters; failover.
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
