# SportsUp — Deploy to an always-on host

Your laptop sleeps; reminders don't. This deploys the same container to a free, always-on VM.
Recommended: **Oracle Cloud Always-Free ARM** (generous and free forever). Google Cloud `e2-micro`
is a smaller free fallback. Any small Linux VM with Docker works.

## Option A — Oracle Cloud Always-Free (recommended)

### 1. Create the VM
1. Sign up at <https://www.oracle.com/cloud/free/> (a card is required for identity; Always-Free
   resources are never charged).
2. **Compute → Instances → Create Instance**:
   - **Image:** Ubuntu 22.04 (or 24.04).
   - **Shape:** Change shape → **Ampere (ARM) VM.Standard.A1.Flex**, e.g. 1 OCPU / 6 GB (well within
     the Always-Free 4 OCPU / 24 GB allowance).
   - **SSH keys:** upload/generate and save the private key.
   - Create. If you hit "Out of host capacity", pick a less busy home region (Frankfurt, Singapore,
     Mumbai) — the Always-Free region is fixed at first instance creation.
3. Note the public IP. (No inbound ports are needed — SportsUp only makes outbound calls.)

### 2. Connect + install Docker
```bash
ssh -i /path/to/key ubuntu@<PUBLIC_IP>
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER && newgrp docker
```

### 3. Get the code + configure
```bash
git clone https://github.com/arindamlabs/sportsup.git && cd sportsup
cp .env.example .env        # paste your API keys + Telegram bot creds
cp config.example.yaml config.yaml   # edit teams/timezone/etc (or scp your local one)
```
Set secrets in `.env` (`FOOTBALL_DATA_API_KEY`, optional `API_FOOTBALL_KEY`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
Keep `SPORTSUP_DRY_RUN=true` for the first boot.

### 4. Dry-run, then go live
```bash
docker compose run --rm sportsup run --once   # one cycle, console output, nothing sent
# verify creds + a real test:
docker compose run --rm sportsup providers
docker compose run --rm sportsup test-send     # sends one real sample alert to confirm delivery
# when happy, set delivery.dry_run: false (config.yaml) or SPORTSUP_DRY_RUN=false (.env), then:
docker compose up -d                             # always-on, restarts on reboot
docker compose logs -f                           # watch it
```
> The compose service is named `sportsup`; use that if you renamed it. `restart: unless-stopped`
> means it survives VM reboots.

### 5. Operate
```bash
docker compose ps                 # is it running?
docker compose exec sportsup python -m sportsup status   # what's been sent
docker compose pull || git pull && docker compose up -d --build   # update to latest
```
Data persists on the host via the `./data` and `./logs` volume mounts.

## Option B — Google Cloud e2-micro (free fallback)
Create an `e2-micro` instance (us-west1/us-central1/us-east1) with Ubuntu, then follow steps 2–5
above. 1 GB RAM is enough for this workload.

## Option C — any VM / Raspberry Pi
Anything that runs Docker and stays on works. The image is multi-arch (x86 + ARM), so a Pi is fine.

## Notes
- **Outbound only:** no inbound firewall rules needed.
- **Time:** the container works in UTC internally and renders alerts in your `config.yaml` timezone, so
  the host clock just needs to be roughly correct (NTP, on by default).
- **Costs:** football-data.org free tier + Telegram Bot API = $0. Oracle/Google free tiers = $0.
  Watch only that you stay on Always-Free shapes.
