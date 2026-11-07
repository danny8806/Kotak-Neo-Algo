# Kotak Live Dashboard

The live app receives the daily research report, maps its symbols to Dhan NSE EQ IDs, reads each 09:15 one-minute opening candle, and monitors Dhan ticks for a directional ORB break. If Kotak credentials are absent, Dhan mapping and ORB monitoring still run read-only; Kotak mapping, order readiness, and live order submission remain blocked.

## One direct daily path

1. The Codex schedule runs at 09:02 IST on weekdays and prepares up to five LONG and five SHORT candidates with source evidence.
2. `node bridge/send_codex.js <report.json>` reads the bridge URL and token from the ignored local `.env` and posts the report to `https://deltacapitals.systems/api/v1/signals/incoming` over HTTPS.

Signal intake is restricted to the official Nifty 500 constituent snapshot in `nifty500_universe.json`; the live dashboard refuses out-of-universe candidates. Refresh this checked snapshot before each release with `python scripts/refresh_nifty500_universe.py`. The 45-day freshness limit fails closed if it is neglected.

3. The bridge validates the date, cutoff, sources and JSON contract, stores the report and returns `ACCEPTED` or `DUPLICATE`. The task checks the status and hash. No verified receipt means no entry signals.
4. The supervisor resolves current NSE cash-equity mappings, reads the 09:15 Dhan candle and listens for fresh Dhan ticks.
5. LONG qualifies on a direct cross above the candle high; SHORT qualifies on a direct cross below its low. The system submits whole-share MIS `MKT` requests and reconciles fills from Kotak's reports. Kotak's current guidance describes conversion of API market requests into protected limit orders; submission does not guarantee a fill. Configured square-off is 15:15 IST.

The operational trading snapshot keeps standard NSE `EQ` series with Indian `INE` equity ISINs. REIT units, trade-to-trade series and placeholder rows remain visible in the full constituent audit but are excluded from eligible trading symbols.

The dashboard embeds the receiver in `bridge/app.py`. GitHub is not part of this daily signal path. The schedule reports delivery only after an `ACCEPTED` or `DUPLICATE` acknowledgment.

## Run locally

Use Python 3.11+ and install `requirements-live.txt`. Configure `SIGNAL_DATABASE_URL` and `LIVE_DATABASE_URL` to PostgreSQL, a private 32+ character `SIGNAL_BRIDGE_TOKEN`, `DASHBOARD_PASSWORD` (12+ characters), and a separate 32+ character `DASHBOARD_SESSION_SECRET`. For local HTTP use `DASHBOARD_COOKIE_SECURE=false`; keep `LIVE_ORDERS_ALLOWED=NO`. Then run:

```powershell
python -m live_desk.app
```

Open `http://127.0.0.1:8010`. Docker Compose is `live_desk/compose.yaml`; it starts PostgreSQL 16 and binds the dashboard to `127.0.0.1:8010`, persisting signal and order data in a PostgreSQL volume. Set a private URL-safe `POSTGRES_PASSWORD` in `.env`; generate one with `openssl rand -hex 32`. The Codex sender posts through the public HTTPS bridge endpoint; a loopback-only endpoint cannot receive scheduled reports.

## Codex scheduler

Use [the Codex task prompt](../bridge/CODEX_TASK_PROMPT.md). The sender reads `SIGNAL_BRIDGE_URL` and `SIGNAL_BRIDGE_TOKEN` from the ignored local `.env`; no OpenAI API key or separate HTTP action is needed. Keep the token out of prompts and reports.

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

## Production recovery and live dashboard

Both broker sessions authenticate at application startup and refresh at 08:00 IST while the service is running. Startup before 08:00 does not skip that morning's refresh. Kotak refresh is single-flight, uses 60–300 second failure backoff, and retries expired sessions after a cooldown. Authentication never arms orders. Dhan refresh also reconnects existing data sockets. The application must be running for its timers to execute; Compose restarts the service after a host/process restart.

Kotak's documented `5203 / No Data` response is accepted only as an empty positions/orders/trades book. Other response failures stay blocked. Account books refresh about every five seconds and expire after twenty seconds. Account positions outside the research watchlist receive a separate read-only Dhan ticker subscription. Open P&L is revalued on each snapshot using fresh exchange ticks; missing/stale ticks invalidate the unrealized value immediately. Closed positions retain broker-book realized P&L without a quote. Carry-forward positions with unverified cost basis show unavailable P&L. Gross P&L excludes fees; net after fees stays unavailable until authoritative charges exist. All broker-returned NSE EQ orders/fills are available without the previous row truncation.

The ticker callback performs no broker HTTP requests. Mappings are prepared before opening-range construction. Durable PostgreSQL reservations serialize exposure across workers, then separate HTTP workers submit orders. Kotak calls share per-process pacing (10 mutating requests/second and 20 report requests/second); deploy one active supervisor per broker account. Deployment pins the tested Kotak SDK version. The dashboard shows both authentication states, next refresh times, the blocked stage, and stale-data status.

Queued entries are rechecked for authorization, trading window and tick age before the durable send transition. A lost acknowledgement triggers bounded report-read retries and later reconciliation by the original unique tag. **An unknown submission is never sent again merely because the tag is temporarily absent.** Startup reconciles persisted unknown submissions even without a research report. Unresolved earlier sessions block new entries. EOD cancellation/fill reconciliation exits only confirmed remaining quantities, and completed exits are not sent again.

Kotak requires a whitelisted VPS outbound IP for order APIs even when login and reports work. Verify this in the broker console before any authorized market-session acceptance test. See the [official IP and market-order guidance](https://github.com/Kotak-Neo/Kotak-Neo/blob/main/docs/static-ip-whitelisting.md). This service has no demo or paper execution route; simulated exchange responses exist only in the isolated tests.
