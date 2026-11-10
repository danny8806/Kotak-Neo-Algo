# Kotak Live Trading System

One direct path: **Codex schedule → HTTPS signal bridge → Dhan symbol map and 09:15 ORB → Kotak Neo**. The scheduler can run any day and time; off-session reports map symbols without starting ORB monitoring. GitHub is only for code releases.

The [5 November production acceptance record](reports/production-hardening-2026-11-05.md) documents the deployed startup/08:00 IST authentication, broker-book and live P&L fixes, 130 passing tests, 495-symbol dual-broker mapping audit, live dashboard checks, and remaining market-session acceptance gates. Production remains disarmed; simulated test fills are not live execution evidence.

The [dashboard workspace guide](reports/dashboard-workspace-2026-11-05.md) covers the separate views, interactive books, configuration controls, connection checks and desktop/mobile validation.

## Daily flow

1. The Codex schedule can run on any calendar day and at any time. It sends only newly qualified symbols, up to five LONG and five SHORT per daily report; off-session reports are accepted for mapping without starting ORB monitoring.
2. `node bridge/send_codex.js <report.json>` reads the HTTPS bridge URL and one bearer token from the ignored project `.env`, then sends each new batch to `https://deltacapitals.systems/api/v1/signals/incoming`.
3. The bridge validates the date, timestamps, symbols and report format, then adds new symbols to today's signal list. The scheduler confirms `ACCEPTED` or `DUPLICATE`; otherwise it reports `NOT DELIVERED`.
4. Dhan maps each new NSE symbol, supplies its 09:15 one-minute candle and subscribes it on the current live feed. A LONG qualifies on a cross above the candle high; a SHORT qualifies on a cross below its low.
5. When the live session is enabled, ready and operator-armed, the supervisor applies sizing limits, sends qualifying MIS orders to Kotak Neo, and reconciles actual broker orders and fills. The configured square-off time is 15:15 IST.

The report is a research watchlist, not an order instruction. Signals arriving after the configured entry cutoff are stored and monitored but cannot create new entries that session.

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

## Codex scheduler setup

The Codex automation is named **Daily Kotak signals to dashboard**. It uses the existing `SIGNAL_BRIDGE_URL` and `SIGNAL_BRIDGE_TOKEN` entries in the ignored local `.env`; it does not need an OpenAI API key or a separate ChatGPT HTTP action. The project includes the small Node sender, so no Python install is needed for delivery.

The receiver is public at the HTTPS route above, with Nginx forwarding `/api/v1/` to the loopback-only live app. Signal submission uses the bridge token. Dashboard login and per-session operator arming protect account controls and live orders; the scheduler cannot arm orders.

## Live trading locks

Dhan supplies read-only market data; Kotak Neo is the order broker. Keep account credentials private and out of reports, prompts, source files and browser code.

Orders are disabled by default (`LIVE_ORDERS_ALLOWED=NO`). Live entries also require broker credentials, healthy Dhan/Kotak sessions, funds, exact mappings and a fresh opening candle. An operator must arm each session from the dashboard after these checks pass. The scheduler only sends research signals and cannot arm or place orders.

The dashboard lets a disarmed operator customize per-stock allocation, total/per-side position caps, gross cap, entry cutoff and EOD exit time. Defaults are ₹1 lakh per stock, ten total positions, ₹10 lakh gross, 15:00 entry cutoff and 15:15 exit. Settings persist on the server and lock while orders are armed; existing saved server settings take precedence over these defaults. Research accepts up to 10 candidates (five per side), all in the Nifty 500 universe; order limits still apply.

There is no stop-loss or daily-loss cutoff. A scheduled software square-off is not a broker-native protective order. The VPS smoke check verifies service health and access controls only; it does not authenticate a real Kotak account or submit a trade.

## Repository map

- `live_desk/`: dashboard, API, ORB runtime, Dhan market data and Kotak order integration.
- `bridge/`: authenticated signal receiver, Codex sender and task prompts.
- `schema/`, `scripts/`: JSON contract, Nifty 500 audit tooling and signal validation.
- `ops/`: private VPS deployment and maintenance scripts.
- `tests/`: regression and failure-case tests.
- `.github/workflows/`: CI, deployment and legacy signal publication workflows; none is needed for the Codex-to-dashboard handoff.

See [live_desk/README.md](live_desk/README.md) for the application flow and [ops/README.md](ops/README.md) for VPS deployment.
