# SportsUp

A configurable tool that tracks sporting events (e.g. **FIFA World Cup 2026**,
**EPL 2026-27**) and sends alerts (**Telegram** by default, WhatsApp optional) for the
teams you care about:

- **Upcoming-fixture reminders** (e.g. day-before + kickoff-soon) in your local timezone
- **Shock-result detection** — flags upsets via an odds/standings/form heuristic
- **Final scores** (configurable, default off)

It runs two ways: **single-user**, driven by `config.yaml` (the original mode), or as a
**multi-user self-service Telegram bot** where anyone can `/subscribe`, pick tournaments
and teams, tune their alerts, and manage everything in chat — all on free tiers ($0).

> **Status:** Feature-complete (Phases 0–10). Single-user runtime + the multi-user bot:
> guided onboarding, per-user timezone/quiet-hours/alert-toggles, granular + full
> unsubscribe, pause/resume, rate-limiting, and an odds-call budget. See [`PLAN.md`](PLAN.md),
> [`RUNBOOK.md`](RUNBOOK.md) (operations), and [`DEPLOY.md`](DEPLOY.md) (always-on hosting).
> `dry_run` is on by default, so nothing is sent until you opt in.

## Multi-user Telegram bot

```bash
python -m sportsup migrate-config   # one-time: import config.yaml as subscriber #1
python -m sportsup bot              # run the bot: inbound commands + delivery loop
```

Users message the bot and manage themselves entirely in chat:

| Command | What it does |
|---|---|
| `/start`, `hi` | Subscribe + launch guided setup |
| `/subscribe` | Pick tournaments → teams → alert types/timing (inline keyboards) |
| `/mysubs` | Show everything you follow + your settings |
| `/edit` | Change alert types and reminder timing |
| `/settings` | Set your timezone and quiet hours |
| `/unsubscribe` | Remove a single team or a whole tournament |
| `/pause`, `/resume` | Mute/unmute alerts without losing your setup |
| `/stop` | Full unsubscribe — deletes all your data (with confirmation) |
| `/help` | List every command |

Data is fetched **once per competition** and fanned out to every subscriber, so adding
users adds no API calls. Each user gets their own timezone, quiet hours, alert toggles,
lead-times, and a per-user dedup key. Cutover from single-user is in [`RUNBOOK.md`](RUNBOOK.md).

## Quick start (WSL2)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # fill in later phases; not needed to boot
cp config.example.yaml config.yaml   # already provided, edit to taste

python -m sportsup plan       # show what would be tracked
python -m sportsup validate   # check config + state store
```

### With Docker

```bash
docker compose run --rm sportsup plan
```

## Configuration

- **`config.yaml`** — everything you tune (events, teams, toggles, lead-times, timezone, quiet hours,
  shock sensitivity). Git-ignored because it's personal; `config.example.yaml` is the committed template.
- **`.env`** — secrets only (API keys, WhatsApp token). Git-ignored; `.env.example` is the template.

See [`config.example.yaml`](config.example.yaml) for the annotated schema.

## CLI

| Command | What it does |
|---|---|
| `python -m sportsup plan` | Print tracked events/teams/toggles + schedule + credential status |
| `python -m sportsup validate` | Validate config and initialize the SQLite state store |
| `python -m sportsup providers` | Probe data-provider connectivity/auth (needs API keys in `.env`) |
| `python -m sportsup fixtures` | Fetch & print upcoming fixtures for watched teams in your timezone |
| `python -m sportsup alerts` | Dry-run preview of scheduled reminders + result/upset alerts |
| `python -m sportsup whatsapp-test [--live] [--template]` | Preview formatting; `--live` sends a real test (`hello_world`, or your `--template`) |
| `python -m sportsup notify` | Deliver due alerts once via the configured sender (console in dry-run) |
| `python -m sportsup status` | Show sent-alert history + last sync (reads the state store, no network) |
| `python -m sportsup test-send [--type upset]` | Send one real sample alert via the configured channel (ignores dry_run) |
| `python -m sportsup migrate-config` | Import `config.yaml` into the DB as subscriber #1 (multi-user) |
| `python -m sportsup subscribers` | List subscribers and the teams/tournaments they follow (no network) |
| `python -m sportsup subs-plan` | Multi-user dry-run: preview each subscriber's alerts (one fetch per competition) |
| `python -m sportsup bot` | Run the multi-user Telegram bot: guided `/subscribe` onboarding, `/help`, `/stop`, + delivery loop |
| `python -m sportsup run` | Start the always-on runtime (fixture sync + reminders + result polling) |
| `python -m sportsup run --once` | Run a single sync/fire/poll cycle and exit (cron-style) |

`providers` and `fixtures` need at least `FOOTBALL_DATA_API_KEY` in `.env`
([free token](https://www.football-data.org/client/register)); add `API_FOOTBALL_KEY` to
enable odds-based shock detection later. Without keys they exit with a clear message.

## Design notes

- **Delivery:** swappable sender interface — **Telegram** (free, default), **WhatsApp** Meta Cloud API,
  or console (dry-run). Switch with one line: `delivery.provider`. Unofficial WhatsApp automation is
  intentionally **not** used (ToS / ban risk).
- **Data:** football-data.org (fixtures/results/standings) + API-Football (odds) behind a provider interface.
- **State:** SQLite store gives exactly-once alerts that survive restarts.
- **Hosting:** develop locally, deploy the same container to an always-on host (Oracle Always-Free ARM VM).

## Development

```bash
pip install -r requirements.txt pytest
pytest
```
