# Current plan notice

Read [MAIN_PLAN.md](../MAIN_PLAN.md) first. It is the sole current architecture.
The external HTTP bridge, the local 09:02 publisher and the GitHub news schedule
are retired. This file describes how the live dashboard release is built and
activated on the existing VPS.

# Deploy Kotak Neo live-only monitoring to the existing VPS

The previous Sniper paper service used a separate project and host port 8004. The
guarded retirement check ran after the live-only service was verified and
reported that the old container was already absent. Its old data, images,
environment file and volumes were not deleted; no Nginx routes were modified.

**Do not use the old deploy_vps.py for this project.** It stops the existing
dashboard, copies model artifacts and changes Nginx. This deployment keeps those
resources untouched.

## Isolated live-only server deployment

- Source: `Kotak-Neo-Algo` `live_desk`, `bridge` and `scripts`; no paper engine,
  saved models, paper ledger or historic model assets
- Base directory: `/home/jadhavdnyaneshwar701/kotak-news-live`
- Versioned release: `releases/<40-character Git commit SHA>`
- Private settings file: `<base>/config/live.env` (mode `600` or `400`)
- Compose project: `kotak-news-live`
- Bind: `127.0.0.1:8010` (no public plaintext exposure)
- Order setting: `LIVE_ORDERS_ALLOWED` is explicit in the private environment;
  the service starts disarmed after every restart
- Legacy data, images, private environment and volumes: preserved
- Existing Nginx routes: unchanged

The release is built from a committed Git SHA, not from a working tree and not
from the VPS-only `Dockerfile.direct-news` base image. `git archive` the commit,
upload it to `releases/<SHA>`, then run the activation script from that release.

## Step 1 — Prepare private settings on the VPS

Using your authorized SSH login to the VPS:

```bash
sudo install -d -m 700 /home/jadhavdnyaneshwar701/kotak-news-live/config
sudo nano /home/jadhavdnyaneshwar701/kotak-news-live/config/live.env
sudo chmod 600 /home/jadhavdnyaneshwar701/kotak-news-live/config/live.env
```

Required entries, with genuine private values:

```dotenv
SIGNAL_BRIDGE_TOKEN=LONG_UNIQUE_RANDOM_SECRET_AT_LEAST_32_CHARS
DASHBOARD_PASSWORD=LONG_PRIVATE_DASHBOARD_PASSWORD
DASHBOARD_SESSION_SECRET=DIFFERENT_LONG_RANDOM_SECRET_AT_LEAST_32_CHARS
DASHBOARD_PUBLIC_ORIGIN=https://YOUR_PRIVATE_LIVE_SUBDOMAIN
POSTGRES_PASSWORD=64_CHARACTER_URL_SAFE_RANDOM_PASSWORD
SIGNAL_INGEST_MODE=internal
INTERNAL_NEWS_DATABASE_URL=postgresql+psycopg://SELECT_ONLY_USER:PASSWORD@NEWS_DB_HOST:5432/market_news
LIVE_ORDERS_ALLOWED=NO
```

`SIGNAL_INGEST_MODE=internal` and a valid `INTERNAL_NEWS_DATABASE_URL` are
mandatory. `ops/activate_live_vps.sh` refuses to activate a release without them,
and refuses an `external` mode. The news database user must have `SELECT` only on
the news schema; the dashboard never writes there.

The activation script creates `POSTGRES_PASSWORD` locally on the VPS with OpenSSL
when it is absent, without printing or transferring it. Compose runs PostgreSQL 16
in a persistent private volume and points both signal reports and the live order
ledger at that database.

To connect real broker books and market data, configure these in the same
protected server file after the account has API access:

```dotenv
KOTAK_NEO_CONSUMER_KEY=
KOTAK_NEO_MOBILE_NUMBER=
KOTAK_NEO_UCC=
KOTAK_NEO_MPIN=
KOTAK_NEO_TOTP_SECRET=
DHAN_CLIENT_ID=
DHAN_PIN=
DHAN_TOTP_SECRET=
LIVE_ARM_TOTP_SECRET=
```

Never paste broker credentials, VPS passwords, SSH private keys or TOTP secrets
into a model prompt, a Git-tracked file or browser JavaScript.

## Step 2 — Deploy a release by commit SHA

The GitHub workflow **Deploy Kotak live-only dashboard to existing VPS** is
manual-only (`workflow_dispatch`) and is guarded to `refs/heads/main`. A push to
`main` never deploys production: an accidental deploy can replace the running
release while the VPS pipeline is producing the day's report.

From the repository root on a trusted workstation:

```bash
git archive --format=tar.gz --output=live-release.tgz <SHA>
scp live-release.tgz root@200.234.44.93:/tmp/
```

On the VPS:

```bash
set -euo pipefail
BASE=/home/jadhavdnyaneshwar701/kotak-news-live
SHA=<full 40-character SHA>
mkdir -p "$BASE/releases/$SHA"
tar -xzf /tmp/live-release.tgz -C "$BASE/releases/$SHA"
bash "$BASE/releases/$SHA/ops/activate_live_vps.sh" "$BASE" "$SHA"
```

The activation script builds the image from the release `live_desk/Dockerfile`,
starts the isolated container, and then verifies all of the following before it
reports success:

- `/health` reports `LIVE_ONLY` with `orders_armed:false`
- `/health` reports `news_intake.mode == VPS_INTERNAL`
- intake status is neither `STARTING` nor `INTAKE_BLOCKED`
- unauthenticated `/api/state` returns `401`
- the retired `POST /api/v1/signals/incoming` returns `410`
- the authenticated status payload contains `pipeline_version`

If any check fails the script rolls back the isolated live container or stops the
failed first instance. It never touches the legacy Sniper project.

Check manually:

```bash
docker compose -p kotak-news-live \
  --env-file /home/jadhavdnyaneshwar701/kotak-news-live/config/live.env \
  -f /home/jadhavdnyaneshwar701/kotak-news-live/current/live_desk/compose.yaml ps
curl -fsS http://127.0.0.1:8010/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/api/state
```

Expected: health says `LIVE_ONLY`, `orders_armed` is false, and
`news_intake.status` shows the internal read succeeded. The unauthenticated
account-data API returns `401`. `/ready` returns `503` outside the entry window by
design; that is not a deployment failure.

The deploy workflow also runs the signal and live-only tests before it uploads
anything, and verifies Python/JS plus both deployment shell scripts.

A passing CI run is not proof of a deployment. Inspect the Actions log and the
VPS `/health` payload.

## Step 3 — HTTPS browser access

Port 8010 binds only to the server loopback interface. Create a **dedicated HTTPS
subdomain** and proxy its root path to `127.0.0.1:8010`, using a real TLS
certificate and a known public origin. See the Nginx template in this folder.
Never reuse the existing plaintext port 8004 browser login for real broker
controls. Do not change company Nginx routes without approval.

For initial private verification without a hostname, forward the loopback port
with an authorized SSH tunnel and set
`DASHBOARD_PUBLIC_ORIGIN=http://127.0.0.1:8010` plus
`DASHBOARD_COOKIE_SECURE=false` in the private server environment file. Access only
`http://127.0.0.1:8010` through the tunnel. Set the cookie flag back to `true` and
the origin to the final HTTPS hostname before any public routing; the Compose
default remains secure.

## Research handoff and trading readiness

The dashboard reads the immutable `news_morning_reports` row for today through the
internal SQL handoff. There is no scheduled HTTP publish step and no external
token exchange. A report is executable only when all of these hold: the producer
tag is `vps-codex-final-v1`, the report is `READY`, it was received strictly
before 09:15 IST, the news cutoff is at or before 09:00, the report was generated
by 09:12 and generation was not earlier than the cutoff. Anything else stays
visible as research-only with `LEGACY_OR_UNVERIFIED_PRODUCER` or a late-window
reason, and can never enter that session's entry list.

The deployment defaults to real-money orders disabled (`LIVE_ORDERS_ALLOWED=NO`).
The private Compose environment can override it, but only after the Dhan
opening-candle timestamp convention is verified against a real exchange response
and all account and recovery checks pass. The service starts disarmed even when
order permission is enabled. The deployment smoke check does **not** place orders
or authenticate a real Kotak account. Broker-native protective stops and crash
recovery must be independently verified before enabling unattended real-money
operation.

## News collector on the same VPS

The news collector is a separate service with its own database. Keep its release
and image independent from the dashboard release:

- release root: `/home/jadhavdnyaneshwar701/market-news-db`
- job runner: `ops/news-stack/run-news-job.sh gather|morning`
- collection timer: every ten minutes, 24/7
- FINAL timer: `Mon..Fri 09:00,05,10:00 Asia/Kolkata`
- dashboard networking: attach the dashboard container to the private news
  network so `INTERNAL_NEWS_DATABASE_URL` resolves without a public port

Freshness is proven by a recent `last_successful_db_sync.json` marker and a new
`news_ingest_runs` row, not by an active timer. A timer that fires while every job
fails is not collection.

## Legacy dashboard retirement check

The retirement script verifies the live container is healthy and disarmed and its
unauthenticated API is protected before examining the old project. The 5 November
2026 run found the old container already absent, so it performed no teardown. If a
matching legacy container is later started, the script stops only that Compose
project, preserves old data/images/environment/volumes, rechecks the live service
and attempts to restart the old project if verification fails. The live app
remains on `127.0.0.1:8010`; use a private SSH tunnel or configured HTTPS hostname
for access.

## One-time secure SSH bootstrap from your Windows computer

An audit of the user's older public MCX repository identified scripts with
hardcoded historical VPS passwords. **Assume that old VPS password is compromised
and rotate it through your hosting provider's trusted console before continuing.**
Removing it from a repository's current files alone does not erase Git history.

The [bootstrap_deploy_access.py](bootstrap_deploy_access.py) utility allows an
authorized operator to convert a **new private VPS password (or existing private
local environment file)** into a separate ED25519 GitHub Actions deployment key.
This is a local workstation step; it does not expose the password to GitHub.

1. On your Windows computer, install Python and GitHub CLI, then run
   `gh auth login` with permission to manage this repository's Actions secrets.
2. Install `python -m pip install paramiko cryptography`.
3. Using your **hosting provider's VPS console** (not an unverified public key
   scan), obtain and independently confirm the server's ED25519 host fingerprint:

   ```bash
   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
   ```

4. From the freshly checked-out repository on **your PC**, run:

   ```powershell
   python ops/bootstrap_deploy_access.py --trusted-host-fingerprint 'SHA256:REPLACE_WITH_VERIFIED_SERVER_FINGERPRINT'
   ```

   The script uses the private local `VPS_PASS` environment variable if
   configured, or reads it from the older **local and ignored**
   `~/Desktop/MCX-TRADER-LIVE/mcx-trader.env`; otherwise it prompts for the
   password without echoing it. It verifies the actual VPS SSH host key **before**
   password login, generates a separate deployment ED25519 key locally, installs
   only the public half on the VPS, verifies the key login, and writes the private
   key and pinned host-key string directly to GitHub Actions Secrets through your
   authenticated `gh` CLI.

**No VPS password, broker MPIN, key bytes or token should be pasted into a model
prompt or committed.** Do not copy the old root password from the public MCX
repository; rotate it first. The local script is never executed on a GitHub
runner.

5. Complete the server's private `config/live.env`, then dispatch the deployment
   workflow from `main`. The VPS stays private on localhost port 8010 and the
   service starts disarmed.

If the VPS host-key fingerprint differs from the trusted provider console value,
stop and investigate rather than disabling strict SSH host verification.
