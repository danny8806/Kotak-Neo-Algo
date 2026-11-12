# Current plan notice

Read [MAIN_PLAN.md](../MAIN_PLAN.md) first. It is the sole current architecture. The external HTTP signal bridge, the local 09:02 publisher and the GitHub news schedule are retired and must not be restored.

# Kotak Live Dashboard

The live dashboard maps research symbols to Dhan NSE EQ IDs, reads each 09:15
one-minute opening candle, and monitors Dhan ticks for a directional ORB break.
If Kotak credentials are absent, Dhan mapping and ORB monitoring still run
read-only; Kotak mapping, order readiness and live order submission stay blocked.

## Internal daily path

1. The VPS collector writes source records to the private news database every ten
   minutes, 24/7. The Codex FINAL job runs at 09:00 with retries at 09:05 and 09:10
   on regular NSE sessions and writes one immutable `news_morning_reports` row
   tagged `pipeline_version=vps-codex-final-v1`.
2. `live_desk/news_intake.py` runs inside the dashboard process and reads that row
   with a SELECT-only news database account over the Docker private network. It
   makes no HTTP request and holds no write grant on the news database.
3. The intake re-validates the schema, session date, cutoff and deadline, then
   calls `bridge.app.ingest_report` as an internal Python function. The shared
   function keeps the universe check, daily transaction lock, cumulative
   five-per-side cap, conflict detection, idempotency and server receipt time.
4. Only a `READY` report produced by `vps-codex-final-v1`, received strictly
   before 09:15 IST, is executable. The executable watchlist freezes at 09:15.
   Later or unverified reports stay visible as research-only and never change
   that session's trading list.

Signal intake is restricted to the official Nifty 500 constituent snapshot in
`nifty500_universe.json`. Refresh it with
`python scripts/refresh_nifty500_universe.py`; the 45-day freshness limit fails
closed. The operational trading set keeps standard NSE `EQ` rows with Indian
`INE` equity ISINs; REIT units, trade-to-trade series and placeholder rows remain
in the audit but are excluded from eligible trading symbols.

Provenance is stored on `signal_reports` and `signal_batches` and returned by the
authenticated status API. A report with a null or legacy `pipeline_version` is
displayed with `LEGACY_OR_UNVERIFIED_PRODUCER` and is never entry-eligible. The
dashboard refuses to relabel an old report: only the producer writes the marker.

The retired public write route `POST /api/v1/signals/incoming` returns `410` in
internal mode. Dashboard login and read APIs remain. GitHub is not part of the
daily signal path.

## Run locally

Use Python 3.11+ and install `requirements-live.txt`. Configure
`SIGNAL_DATABASE_URL` and `LIVE_DATABASE_URL` to PostgreSQL, a private 32+
character `SIGNAL_BRIDGE_TOKEN`, `DASHBOARD_PASSWORD` (12+ characters) and a
separate 32+ character `DASHBOARD_SESSION_SECRET`. Keep `LIVE_ORDERS_ALLOWED=NO`.
Then run:

```powershell
python -m live_desk.app
```

Open `http://127.0.0.1:8010`. Docker Compose is `live_desk/compose.yaml`; it starts
PostgreSQL 16 and binds the dashboard to `127.0.0.1:8010`, persisting signal and
order data in a PostgreSQL volume. Set a private URL-safe `POSTGRES_PASSWORD` in
`.env`; generate one with `openssl rand -hex 32`.

For a UI-only local run without the private news network, set
`SIGNAL_INGEST_MODE=external`. That mode exists only for isolated development and
is rejected by the production activation script.

For a prefix-stripping HTTPS proxy such as `/kotak-live/`, add `text/html` to its
Nginx `sub_filter_types` when that location rewrites `/api/`, `/static/` and
`/ws`; the HTML base URL must also be rewritten. Keep `DASHBOARD_BASE_PATH` empty
when Nginx already rewrites upstream redirects and cookies. For a clean prefix
proxy without Nginx rewrites, use
[the Nginx path example](../ops/nginx.live-path.example.conf) and set
`DASHBOARD_BASE_PATH=/kotak-live`. The app then prefixes browser API, static
asset, login and WebSocket paths itself.

## Health and verification

`GET /health` is unauthenticated liveness only and never contains credentials or
database errors. It reports `news_intake.mode` and `news_intake.status` so a
deployment can prove the internal handoff actually reached its database. A status
of `STARTING` or `INTAKE_BLOCKED` means the dashboard is not production-ready even
if the process is up.

## Trading configuration

Private environment credentials are required for Kotak Neo and read-only Dhan
market data. `live_desk/policy.json` sets defaults: the 09:15 opening candle,
09:30 entry cutoff, 15:00 exit, up to five positions per side, and two shares per
stock by default. The authenticated dashboard selects quantity per stock or
capital per stock, maximum total/long/short positions (up to 10 total),
entry cutoff up to 09:30 IST, and exit time while disarmed.
These settings persist in the PostgreSQL live ledger, survive restarts and are
locked while armed. PostgreSQL advisory locks serialize daily cap reservations
across workers. The exit setting may be earlier than 15:00 but cannot be later.
Legacy settings above 15:00 are capped at startup. Broker funds are informational;
no local available-cash/minimum-capital/gross-notional gate blocks either sizing mode.

The policy requires `dhan_candle_timestamp_mode` to be `start`. The opening range
uses only the completed bar timestamped 09:15 IST, fetched with a 09:14 request
buffer. The 12 November raw-data audit corrected an earlier mistaken end-label
assumption that selected the forming 09:16 bar. The candle OHLC is saved under
`LIVE_ORB_AUDIT_DIR`; each order intent also records its actual previous/current
tick pair and range in an `ORB_TRIGGER` event. Dhan access tokens are generated on startup and
refreshed daily before expiry. India VIX is fetched read-only from the configured
Dhan `IDX_I` security ID and shown as context; it never triggers an entry. Compose
defaults `LIVE_ORDERS_ALLOWED` to `NO`; the service starts disarmed after every
restart. Manual arming requires operator TOTP, exact acknowledgment, fresh feeds
and broker checks.

There is no stop-loss or daily-loss cutoff. At 15:00 the supervisor checks the
live Kotak position against persisted bot fills and can recover close-only work
after a process restart. For an open or partial entry it requests cancellation
and waits for Kotak's terminal order report before exiting filled shares; if
confirmation or position matching fails it blocks the exit for manual
resolution. The software square-off is not a broker-native protective order.
Feed outages, process failure and gaps can still leave real positions exposed.

## Main modules

- `app.py`: authenticated dashboard, internal intake wiring and read APIs.
- `news_intake.py`: SELECT-only news database handoff, strict final window.
- `research_contract.py`: pipeline version, cutoff, deadline and lock constants.
- `nse_calendar.py`: regular NSE session check (2026 holidays; extend yearly).
- `supervisor.py`: session, feed, broker and readiness coordinator, executable
  watchlist gate and research gate.
- `runtime.py`, `strategy.py`, `orb.py`: ORB decisions and order lifecycle.
- `dhan_data.py`, `dhan_instruments.py`, `ticker.py`: read-only data, mapping and
  live prices.
- `kotak_gateway.py`, `kotak_instruments.py`, `broker_view.py`: Kotak login,
  orders, reconciliation and broker reports.
- `ledger.py`: persistent order intents and state changes.
- `analytics.py`: descriptive, broker-confirmed trade outcome summaries; it never
  changes strategy or places orders.
- `static/`: dashboard UI.

The authenticated **Trade journal** view and `GET /api/live/journal` retain
entry/exit intents, state-change events and deduplicated Kotak fills in
PostgreSQL. Fill history starts at deployment; it does not backfill older broker
fills. Closed-trade gross P&L uses confirmed entry and exit average prices and
quantities. Partial exits are shown separately with remaining quantity. Closed
trade return uses confirmed entry notional; gross loss and drawdown are shown
before fees. Fees, taxes and net performance stay unavailable unless
authoritative broker charge data is added. Win rate and average win/loss are
descriptive historical statistics, not a trained model or a promise of future
results. The endpoint is bounded to the latest 5,000 records per record type.

CI uses mocked broker responses; it does not establish live account behavior or
profitability.

## Production recovery and live dashboard

`LIVE_FIXED_ENTRY_QUANTITY=2` sets the initial fixed size; authenticated saved
settings select a positive integer quantity or capital mode. Capital mode rounds
down the configured per-stock amount divided by the breakout price. Kotak decides
margin acceptance/rejection. Position counts, fresh books/ticks, entry windows,
server permissions and session authorization continue to apply. Legacy gross
allocation fields remain storage compatibility data; they do not block entries.

With explicit server authorization, `LIVE_AUTO_TRADING=YES` together with
`LIVE_ORDERS_ALLOWED=YES` automatically enables the saved sizing mode for
entries after broker login, valid session signals, mappings, opening ranges,
fresh ticks/books and recovery checks pass inside 09:16–09:30 IST. This mode
requires no separate operator TOTP or dashboard login for daily execution.
Broker login OTPs remain unchanged. Signing out or closing the dashboard does not
stop automatic trading. **Stop new orders today** persists a daily pause in
PostgreSQL, including across restarts. A health failure after activation also
pauses automatic entries for the rest of that session. The next day's valid
session evaluates readiness again. Old breakout events are never replayed and the
15:00 square-off remains unchanged. Manual mode retains operator TOTP.

Both broker sessions authenticate at application startup and refresh at 08:00 IST
while the service is running. Startup before 08:00 does not skip that morning's
refresh. Kotak refresh is single-flight, uses 60–300 second failure backoff and
retries expired sessions after a cooldown. Authentication never arms orders. Dhan
refresh also reconnects existing data sockets. The application must be running for
its timers to execute; Compose restarts the service after a host/process restart.

Dhan ticker messages can contain multiple packets. The terminal handles each
packet and rejects duplicate security IDs before subscribing. The default
`DHAN_TICK_TIMESTAMP_OFFSET_SECONDS=0` uses the packet epoch as supplied. Set
`19800` only if a read-only feed capture verifies that the packet timestamps are
consistently 5 hours 30 minutes ahead. This setting subtracts that offset from
exchange trade time. Normal freshness checks still apply.

Kotak's documented `5203 / No Data` response is accepted only as an empty
positions/orders/trades book. Other response failures stay blocked. Account books
refresh about every five seconds and expire after twenty seconds. Account
positions outside the research watchlist receive a separate read-only Dhan ticker
subscription. Open P&L is revalued on each snapshot using fresh exchange ticks;
missing/stale ticks invalidate the unrealized value immediately. Closed positions
retain broker-book realized P&L without a quote. Carry-forward positions with
unverified cost basis show unavailable P&L. Gross P&L excludes fees; net after
fees stays unavailable until authoritative charges exist. All broker-returned NSE
EQ orders/fills are available without row truncation.

The ticker callback performs no broker HTTP requests. Mappings are prepared before
opening-range construction. Durable PostgreSQL reservations serialize exposure
across workers, then separate HTTP workers submit orders. Kotak calls share
per-process pacing (10 mutating requests/second and 20 report requests/second);
deploy one active supervisor per broker account. Deployment pins the tested Kotak
SDK version. The dashboard shows both authentication states, next refresh times,
the blocked stage and stale-data status.

Queued entries are rechecked for authorization, trading window and tick age before
the durable send transition. A lost acknowledgement triggers bounded report-read
retries and later reconciliation by the original unique tag. **An unknown
submission is never sent again merely because the tag is temporarily absent.**
Startup reconciles persisted unknown submissions even without a research report.
Unresolved earlier sessions block new entries. At the 09:30 IST entry cutoff the
service stops dispatching entries and requests cancellation of working bot
entries. Any fill that races the cancellation is reconciled from the broker book.
EOD square-off exits only confirmed remaining quantities, and completed exits are
not sent again.

Kotak requires a whitelisted VPS outbound IP for order APIs even when login and
reports work. Verify this in the broker console before any authorized
market-session acceptance test. See the
[official IP and market-order guidance](https://github.com/Kotak-Neo/Kotak-Neo/blob/main/docs/static-ip-whitelisting.md).
This service has no demo or paper execution route; simulated exchange responses
exist only in the isolated tests.
