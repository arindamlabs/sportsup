# SportsUp — Runbook

Operating guide for the day-to-day running of SportsUp. For first-time setup see
[`README.md`](README.md); for server deployment see [`DEPLOY.md`](DEPLOY.md).

## What it does, at a glance

`sportsup run` loops three jobs on configurable cadences (`scheduling` in `config.yaml`):
- **fixture sync** — pulls upcoming fixtures for your watched teams (default every 12h)
- **reminder firing** — sends `1d` / `1h` reminders at their lead times (checked every 5m)
- **result polling** — detects finished matches, sends final-score (if enabled) and upset alerts (every 15m)

Everything is deduplicated in `data/sportsup.sqlite`, so alerts fire **exactly once** and survive restarts.

## Safety: dry-run is the default

`delivery.dry_run: true` (or `SPORTSUP_DRY_RUN=true`) makes every send print to the console/log
instead of being delivered. **Nothing is sent until you set it false.** Always validate with
dry-run first.

```bash
python -m sportsup alerts          # preview scheduled reminders + any result alerts
python -m sportsup run --once      # one full cycle, console output, nothing sent
```

## Going live

1. Confirm credentials: `python -m sportsup providers` (data) and `python -m sportsup test-send` (sends one real sample alert).
2. Flip the switch: set `delivery.dry_run: false` in `config.yaml` **or** `SPORTSUP_DRY_RUN=false` in `.env`.
3. Start it: `python -m sportsup run` (foreground) or `docker compose up -d`.

## Telegram setup

Telegram has **no payment method, no template approval, and no per-message cost** — the simplest
reliable path, and the only delivery channel. To enable:

1. In the Telegram app, message **@BotFather** → `/newbot` → pick a name and a username ending in `bot`.
   Copy the **token** it gives you (`123456:ABC...`).
2. Open a chat with your new bot and send it any message (e.g. "hi") — a bot can't message you until you do.
3. Get your numeric **chat id**: message **@userinfobot** `/start` (it replies with your id), or visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `result[].message.chat.id`.
4. Put both in `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=987654321
   ```
5. Set `delivery.provider: telegram` in `config.yaml` (already the default).
6. Test, then go live:
   ```bash
   python -m sportsup run --once                 # dry-run, console output
   SPORTSUP_DRY_RUN=false python -m sportsup notify   # delivers any due alerts for real
   # when happy: set delivery.dry_run: false, then `run` / `docker compose up -d`
   ```
## Monitoring

- **Status view:** `python -m sportsup status` → total sent, last sync time, recent alerts (no network).
- **Logs:** `logs/sportsup.log` (rotating). With Docker: `docker compose logs -f`.

## Reconfiguring (no code changes)

Edit `config.yaml` and restart (`docker compose restart`, or Ctrl-C + rerun):
- add/remove competitions (set `enabled`), edit per-event `teams`, toggle `alerts` types
- change `reminders.lead_times`, `timezone`, `quiet_hours`, `shock_detection` sensitivity, `scheduling` cadences

If a team name isn't matched, `sportsup fixtures` warns "not found in the … team list"; add an alias in
`sportsup/providers/teams.py` or use the provider's spelling.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `chat not found` on send | The chat id is wrong, or the user hasn't messaged the bot yet → verify `TELEGRAM_CHAT_ID`; the user must `/start` the bot once. |
| `401 Unauthorized` on send | Bad/revoked bot token → check `TELEGRAM_BOT_TOKEN` (regenerate via @BotFather if needed). |
| `auth failed (401/403)` on data | Bad/expired API key → check `FOOTBALL_DATA_API_KEY` / `API_FOOTBALL_KEY`. |
| Rate-limited (429) | Built-in retry/backoff handles bursts; if persistent, increase `scheduling.*` intervals. |
| No alerts firing | Still in dry-run? Check `status` for last sync; check `alerts` preview; matches may be future. |
| Team has no reminders | Match postponed/cancelled (no reminders by design), or no fixture in the window. |

## Backups

The only state worth keeping is `data/sportsup.sqlite` (dedup history + subscribers).
Losing it could re-send recent alerts once and, for the bot, lose subscriber data.
`config.yaml` and `.env` are your settings/secrets — back those up out of band.

## Multi-user bot operations

`python -m sportsup bot` is the multi-user service: it handles inbound commands **and**
runs the delivery loop (every 5 min by default; `--deliver-every MIN`, or `--no-deliver`
for inbound-only). It needs `TELEGRAM_BOT_TOKEN` and `FOOTBALL_DATA_API_KEY` in `.env`.

- **Inspect state (no network):** `sportsup subscribers` lists everyone and what they follow.
- **Preview without sending:** `sportsup subs-plan` shows each subscriber's due alerts.
- **Odds budget:** API-Football is capped at ~90 calls/day (`odds_budget`); once spent,
  upset detection falls back to standings/form until midnight UTC.
- **Rate limiting:** inbound is throttled per chat (20 msgs/60s) to guard against floods.

### Cutover from single-user `run` to the bot

Run **one** of `run` or `bot` for a given person — never both at once. They use different
dedup namespaces (`world-cup-2026:…` vs `<chat_id>:…`), so running both double-sends to you.
Do the cutover at a **quiet time** (no watched match within the hour): the bot may re-send
one or two very recent alerts that `run` already delivered, since their dedup keys differ.

The steps below use a Docker Compose **override file**, so the tracked `docker-compose.yml`
is never edited (future `git pull`s stay clean; rollback is deleting one file). Replace
`<user>@<vm-ip>` and paths with your own; run these in the repo directory on the host.

1. **Update + secrets.** `git pull`, then make sure `.env` has `TELEGRAM_BOT_TOKEN`,
   `FOOTBALL_DATA_API_KEY`, and `SPORTSUP_DRY_RUN=false`. (Add `DASHBOARD_PASSWORD` too if
   you'll run the dashboard — see below.)
2. **Migrate** your single-user config into the DB as subscriber #1 (idempotent):
   ```bash
   docker compose run --rm sportsup migrate-config
   docker compose run --rm sportsup subscribers      # verify the import
   ```
3. **Switch `run` → `bot`** without touching the tracked file:
   ```bash
   cat > docker-compose.override.yml <<'YAML'
   services:
     sportsup:
       command: ["bot"]
   YAML
   ```
4. **Recreate** (rebuilds for new code/deps):
   ```bash
   docker compose up -d --build
   docker compose logs -f sportsup        # expect "bot @… ready", "delivery loop ON"
   ```
5. **Verify:** message the bot (`/help`, `/mysubs`); optionally
   `docker compose run --rm sportsup subs-plan` to preview without sending.

**Rollback** (anytime): `rm docker-compose.override.yml && docker compose up -d` — the bot
stops and single-user `run` resumes. `config.yaml` is untouched by migration.

## Admin dashboard

`python -m sportsup dashboard` serves a read-only view of subscribers, their tournaments
and teams, and popularity aggregates. It **binds to localhost** and requires
`DASHBOARD_PASSWORD` in `.env` (it refuses to start without one).

**Access it (never expose it publicly):**
```bash
ssh -L 8080:127.0.0.1:8080 <user>@<vm-ip>   # tunnel the VM's localhost:8080 to yours
# then browse http://localhost:8080  (Basic auth: DASHBOARD_USER / DASHBOARD_PASSWORD)
```

**Run it on the VM** alongside the bot (optional compose service, published only on the
host loopback):
```bash
docker compose up -d dashboard
```

Notes:
- Read-only — it issues only `SELECT`s and shares the SQLite DB with the bot (WAL allows
  concurrent reads). The `./data` mount stays OS-writable because SQLite WAL needs its
  `-wal`/`-shm` files, but the app never writes.
- JSON endpoints `/api/overview` and `/api/subscribers` (same auth) for scripting.
- Keep it off public ports. The Oracle VCN security list does **not** need an ingress rule
  for 8080 — the SSH tunnel is the access path.
