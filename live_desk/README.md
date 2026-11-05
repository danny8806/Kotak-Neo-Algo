# Kotak Live Dashboard

The live app receives the daily research report, monitors Dhan market data, applies the ORB rule, sends qualifying orders to Kotak Neo, and displays actual Kotak orders, fills and positions.

## One direct daily path

1. ChatGPT prepares the complete schema v1.0 report by 09:00 IST.
2. The scheduled task's authorized HTTPS action POSTs the report directly to `https://<dashboard-host>/api/v1/signals/incoming` using the bridge Bearer token and idempotency key.

Signal intake is restricted to the official Nifty 500 constituent snapshot in `nifty500_universe.json`; the live dashboard refuses out-of-universe candidates. Refresh this checked snapshot before each release with `python scripts/refresh_nifty500_universe.py`. The 45-day freshness limit fails closed if it is neglected.
3. The bridge validates the date, cutoff, sources and JSON contract, stores the report and returns `ACCEPTED` or `DUPLICATE`. The task checks the status and hash. No verified receipt means no entry signals.
4. The supervisor resolves current NSE cash-equity mappings, reads the 09:15 Dhan candle and listens for fresh Dhan ticks.
5. LONG qualifies on a direct cross above the candle high; SHORT qualifies on a direct cross below its low. The system uses whole-share MIS market orders and reconciles fills from Kotak's reports. Configured square-off is 15:15 IST.

The dashboard embeds the receiver in `bridge/app.py`. GitHub is not part of this daily signal path. A running scheduled task alone does not prove delivery; inspect its acknowledgment and the authenticated dashboard state.

## Run locally

Use Python 3.11+ and install `requirements-live.txt`. Configure `SIGNAL_DATABASE_URL` and `LIVE_DATABASE_URL` to PostgreSQL, a private 32+ character `SIGNAL_BRIDGE_TOKEN`, `DASHBOARD_PASSWORD` (12+ characters), and a separate 32+ character `DASHBOARD_SESSION_SECRET`. For local HTTP use `DASHBOARD_COOKIE_SECURE=false`; keep `LIVE_ORDERS_ALLOWED=NO`. Then run:

```powershell
python -m live_desk.app
```

Open `http://127.0.0.1:8010`. Docker Compose is `live_desk/compose.yaml`; it starts PostgreSQL 16 and binds the dashboard to `127.0.0.1:8010`, persisting signal and order data in a PostgreSQL volume. Set a private URL-safe `POSTGRES_PASSWORD` in `.env`; generate one with `openssl rand -hex 32`. A cloud ChatGPT task cannot reach loopback; production delivery needs a public HTTPS hostname and reverse proxy.

## Connect ChatGPT

Use [the task prompt](../bridge/CHATGPT_TASK_PROMPT.md). Store the matching `SIGNAL_BRIDGE_TOKEN` in an authorized ChatGPT connected app/action that can make HTTPS POST requests. Do not put the token in the task prompt. This repository implements the HTTP receiver and command-line sender; it cannot grant app permissions or establish that a scheduled task has an HTTP-capable action. The task must report `NOT DELIVERED` until a valid acknowledgment is received.

## Trading configuration

Private environment credentials are required for Kotak Neo and read-only Dhan market data. `live_desk/policy.json` sets defaults: the 09:15 opening candle, 10:00 entry cutoff, 15:15 exit, up to five positions per side, ₹1 lakh per position and ₹10 lakh gross cap. The authenticated dashboard lets an operator customize per-stock allocation, maximum total/long/short positions (up to 10 total), gross cap, entry cutoff and exit time while disarmed. These settings persist in the PostgreSQL live ledger, survive restarts and are locked while armed. PostgreSQL transaction advisory locks serialize daily cap reservations across workers. The signal contract supports at most five LONG and five SHORT Nifty 500 candidates; actual entries remain limited by the configured per-side, total-position and gross caps.

The policy sets `dhan_candle_timestamp_mode` to `end`, verified against the returned Dhan 09:15 one-minute candle timestamps on 2026-11-05. Recheck this setting if Dhan changes its chart timestamp convention. Dhan access tokens are generated on startup and refreshed daily before expiry by the Dhan token supervisor. India VIX is fetched read-only from the configured Dhan `IDX_I` security ID and shown as context; it never triggers an entry. Compose defaults `LIVE_ORDERS_ALLOWED` to `NO`; the service starts disarmed after every restart. Dashboard arming also requires operator TOTP, exact acknowledgment, fresh feeds and broker checks.

There is no stop-loss or daily-loss cutoff. At 15:15 the supervisor checks the live Kotak position against persisted bot fills and can recover close-only work after a process restart. For an open or partial entry, it requests cancellation and waits for Kotak's terminal order report before exiting filled shares; if confirmation or position matching fails, it blocks the exit for manual resolution. The software square-off is not a broker-native protective order. Feed outages, process failure and gaps can still leave real positions exposed; credentialed broker tests and verified HTTPS access are required before unattended trading.

## Main modules

- `app.py`: authenticated dashboard and direct signal API.
- `supervisor.py`: session, feed, broker and readiness coordinator.
- `runtime.py`, `strategy.py`, `orb.py`: ORB decisions and order lifecycle.
- `dhan_data.py`, `dhan_instruments.py`, `ticker.py`: read-only data, mapping and live prices.
- `kotak_gateway.py`, `kotak_instruments.py`, `broker_view.py`: Kotak login, orders, reconciliation and broker reports.
- `ledger.py`: persistent order intents and state changes.
- `static/`: dashboard UI.

CI uses mocked broker responses; it does not establish live account behavior or profitability.
