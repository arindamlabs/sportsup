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
instead of going to WhatsApp. **Nothing is sent until you set it false.** Always validate with
dry-run first.

```bash
python -m sportsup alerts          # preview scheduled reminders + any result alerts
python -m sportsup run --once      # one full cycle, console output, nothing sent
```

## Going live

1. Confirm credentials: `python -m sportsup providers` (data) and `python -m sportsup whatsapp-test` (formatting).
2. Send a real connectivity test: `python -m sportsup whatsapp-test --live` → expect "Hello World" on your phone.
3. (Recommended) set up the always-on template below.
4. Flip the switch: set `delivery.dry_run: false` in `config.yaml` **or** `SPORTSUP_DRY_RUN=false` in `.env`.
5. Start it: `python -m sportsup run` (foreground) or `docker compose up -d`.

## Telegram (recommended free channel)

Telegram has **no payment method, no template approval, no 24-hour window, and no per-message cost** —
the simplest reliable path. To enable:

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
WhatsApp stays fully implemented (`provider: meta_cloud`) — switching channels is a one-line config change.

## ⚠️ WhatsApp only: the 24-hour window — and the fix

WhatsApp only lets a business send **free-form text** within 24h of the user last messaging it.
Outside that window Meta rejects text with **error 131047** ("re-engagement"). Two ways to cope:

- **Quick/manual:** message your bot's number from your phone occasionally to keep the window open.
- **Proper/always-on (recommended):** create an approved **utility template** so alerts deliver any time.

### Creating the utility template (one-time)

1. Meta **WhatsApp Manager → Message templates → Create template**.
2. Category **Utility**, name e.g. `sportsup_alert`, language **English (US)**.
3. Body:
   ```
   SportsUp ⚽
   {{1}}
   ```
   Add a sample value for `{{1}}` (e.g. `England vs Croatia — kickoff Wed 17 Jun, 13:00 PDT`).
4. Submit — utility templates are usually approved within minutes to a few hours.
5. Point SportsUp at it in `config.yaml`:
   ```yaml
   delivery:
     alert_template_name: sportsup_alert
     alert_template_lang: en_US
   ```
   SportsUp sends each alert as that template with the message flattened into `{{1}}` (single line,
   markdown stripped — WhatsApp body params can't contain newlines or formatting).
6. Confirm the live path once the template shows **Approved**:
   ```bash
   python -m sportsup whatsapp-test --template            # dry-run: shows the {{1}} it would send
   python -m sportsup whatsapp-test --live --template     # sends one real alert via your template
   ```
   A send error with code `132xxx` usually means the template isn't approved yet.

> **Note:** Meta often re-classifies content templates like sports alerts from **Utility** to
> **Marketing** — that's expected and fine (slightly higher per-message cost, still cents/month for
> personal volume). No content change is needed; just wait for **Approved**.

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
| `131047` on send | Outside 24h window → set up the utility template (above) or message the bot first. |
| `131030` on send | Recipient not on the test number's allowed list → add/verify it in Meta API Setup. |
| `190` on send | Access token invalid/expired → regenerate the System User token (see README Part 3). |
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

1. `sportsup migrate-config` — imports `config.yaml` as subscriber #1 (idempotent).
2. Stop the `run` service/container.
3. Start `bot` (e.g. swap the compose `command:` from `["run"]` to `["bot"]`).
4. Verify with `sportsup subscribers` and `sportsup subs-plan`.

To roll back, stop `bot` and start `run` again — `config.yaml` is untouched by migration.

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
