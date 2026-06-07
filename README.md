# SportsUp

A configurable, single-user tool that tracks sporting events (e.g. **FIFA World Cup 2026**,
**EPL 2026-27**) and sends **WhatsApp alerts** for the teams you care about:

- **Upcoming-fixture reminders** (e.g. day-before + kickoff-soon) in your local timezone
- **Shock-result detection** — flags upsets via an odds/standings/form heuristic
- **Final scores** (configurable, default off)

Events, teams, alert types, reminder lead-times, timezone, quiet hours, and upset sensitivity all live
in editable config — adding a competition or team is a config change, not a code change.

> **Status:** Phase 2 (data providers) complete — fixtures/results/standings/odds behind a
> failover provider interface. Alert engine and WhatsApp delivery land in later phases.
> See [`PLAN.md`](PLAN.md). Nothing is sent yet; `dry_run` is on by default.

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
| `python -m sportsup run` | Boot everything (live scheduler arrives in Phase 5) |

`providers` and `fixtures` need at least `FOOTBALL_DATA_API_KEY` in `.env`
([free token](https://www.football-data.org/client/register)); add `API_FOOTBALL_KEY` to
enable odds-based shock detection later. Without keys they exit with a clear message.

## Design notes

- **Delivery:** Meta WhatsApp Cloud API (free test number) behind a swappable sender interface.
  Unofficial WhatsApp automation is intentionally **not** used (ToS / ban risk).
- **Data:** football-data.org (fixtures/results/standings) + API-Football (odds) behind a provider interface.
- **State:** SQLite store gives exactly-once alerts that survive restarts.
- **Hosting:** develop locally, deploy the same container to an always-on host (Oracle Always-Free ARM VM).

## Development

```bash
pip install -r requirements.txt pytest
pytest
```
