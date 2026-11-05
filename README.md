# Kotak Live Trading System

One direct path: **ChatGPT scheduled task → HTTPS signal bridge in the dashboard → Dhan ORB check → Kotak Neo**. GitHub hosts the code only; it is not part of daily signal delivery.

## Daily flow

1. At 09:00 IST, the ChatGPT task researches news and produces a complete JSON report matching `schema/stock-signals.schema.json`.
2. Through a configured, authorized HTTP-capable ChatGPT app/action, the task POSTs that JSON directly to `https://<dashboard-host>/api/v1/signals/incoming` with `Authorization: Bearer <SIGNAL_BRIDGE_TOKEN>` and an `Idempotency-Key`.
3. The bridge validates the report's date, source timestamps, schema and deadline. The task must confirm `ACCEPTED` or `DUPLICATE`; otherwise it reports `NOT DELIVERED`.
4. The dashboard stores the report. During the session, Dhan provides the 09:15 one-minute candle and fresh market prices. LONG candidates qualify on a cross above the candle high; SHORT candidates qualify on a cross below its low.
5. The live supervisor applies sizing and readiness checks, then sends qualifying MIS orders to Kotak Neo and reconciles actual broker orders/fills. The configured square-off time is 15:15 IST.

The report is a research watchlist, not an order instruction. A missing, late, invalid or non-`READY` report means no new entries.

## Start the dashboard

Use Python 3.11+ and install dependencies:

```powershell
python -m pip install -r requirements-live.txt
```

Set private environment variables before running `python -m live_desk.app`:

```text
SIGNAL_DATABASE_URL=postgresql+psycopg://kotak:<URL-safe-password>@127.0.0.1:5432/kotak_live
LIVE_DATABASE_URL=postgresql+psycopg://kotak:<URL-safe-password>@127.0.0.1:5432/kotak_live
SIGNAL_BRIDGE_TOKEN=<random secret, at least 32 characters>
DASHBOARD_PASSWORD=<private password, at least 12 characters>
DASHBOARD_SESSION_SECRET=<different random secret, at least 32 characters>
DASHBOARD_COOKIE_SECURE=false
LIVE_ORDERS_ALLOWED=NO
```

Start a PostgreSQL 16 server and create the `kotak_live` database first. Use a URL-safe password, such as one generated with `openssl rand -hex 32`. The signal receiver and live order ledger share this PostgreSQL database. Open `http://127.0.0.1:8010` and sign in. The `false` cookie setting is only for local HTTP development. Production needs HTTPS and secure cookies. Docker Compose at `live_desk/compose.yaml` starts PostgreSQL and the dashboard on a private Docker network, binding the dashboard to loopback port 8010.

## Connect ChatGPT directly

The dashboard already serves the authenticated receiver. The live app includes `bridge.app`; it does not need a separate bridge container. Configure the same private `SIGNAL_BRIDGE_TOKEN` in the dashboard and in a secure ChatGPT connected action that can make authenticated HTTPS POST requests. The scheduled task should use [the prompt](bridge/CHATGPT_TASK_PROMPT.md), send the full report object, and verify the returned status and SHA-256 hash.

The server must be reachable through a stable public HTTPS host. A loopback-only address such as `127.0.0.1:8010` cannot receive a request from ChatGPT. The repository contains the API and CLI sender, but cannot configure the ChatGPT account's connected app, create its secret, expose the VPS through DNS/TLS, or prove a task run. Scheduled tasks can use supported connected tools; direct HTTP delivery depends on an authorized app/action being available to the account. See the official [Scheduled tasks documentation](https://learn.chatgpt.com/docs/automations).

## Live trading locks

Dhan supplies read-only market data; Kotak Neo is the order broker. Keep account credentials private and out of reports, prompts, source files and browser code.

Orders are disabled by default (`LIVE_ORDERS_ALLOWED=NO`). The ORB runtime also refuses to start until the actual Dhan 09:15 candle timestamp convention is verified and set in `live_desk/policy.json`. Each session requires a human operator to arm the authenticated dashboard after broker, funds, mapping and feed checks pass. The signal cannot arm orders.

The dashboard lets a disarmed operator customize per-stock allocation, total/per-side position caps, gross cap, entry cutoff and EOD exit time. Defaults are ₹1 lakh per stock, ten total positions, ₹10 lakh gross, 10:00 entry cutoff and 15:15 exit. Settings persist on the server and lock while orders are armed. Research accepts up to 10 candidates (five per side), all in the Nifty 500 universe; order limits still apply.

There is no stop-loss or daily-loss cutoff. A scheduled software square-off is not a broker-native protective order. The VPS smoke check verifies service health and access controls only; it does not authenticate a real Kotak account or submit a trade.

## Repository map

- `live_desk/`: dashboard, API, ORB runtime, Dhan market data and Kotak order integration.
- `bridge/`: authenticated signal receiver, direct sender and ChatGPT task prompt.
- `schema/`, `scripts/`: JSON contract, Nifty 500 audit tooling and signal validation.
- `ops/`: private VPS deployment and maintenance scripts.
- `tests/`: regression and failure-case tests.
- `.github/workflows/`: CI, deployment and legacy signal publication workflows; none is needed for the direct ChatGPT-to-dashboard handoff.

See [live_desk/README.md](live_desk/README.md) for the application flow and [ops/README.md](ops/README.md) for VPS deployment.
