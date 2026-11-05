# Production hardening and acceptance record — 5 November 2026

The updated application is deployed and the live dashboard is connected to both brokers. This record distinguishes production observations from simulated exchange tests. No real order was submitted, cancelled, or armed during this work. No production research report was inserted.

## Deployed release

- Deployed at 17:47:49 IST; final component check at 17:50:17 IST.
- Source archive SHA-256: `8394ccf97db6c567f23f26304151885be91aa7649372edfe6af9f5cb0bf4d93f`.
- VPS release: `/home/jadhavdnyaneshwar701/kotak-news-live/releases/hardening-20261105-8394ccf97db6c567`.
- Image: `kotak-news-live:hardening-8394ccf97db6c567`.
- The application layer was built over the exact previously verified runtime image; no dependency upgrade or PostgreSQL restart was performed. The tested Kotak SDK is pinned to `kotakneoapi==3.0.13`.
- The checkout contains the source changes, test additions and CI configuration. Changes have not been committed or pushed.
- SSH used pinned host keys with rejection of unknown keys and the private credential loader. Credential values were never printed or written into reports, source, or automation memory.

## Production observations

| Component | Observed result |
|---|---|
| `/health` | HTTP 200, live-only, disarmed |
| `/ready` | HTTP 503; trading prerequisites intentionally unmet |
| Authenticated root, JS, CSS and state | HTTP 200 |
| Positions, orders and fills APIs | HTTP 200 |
| The same account APIs without a session | HTTP 401 |
| Dashboard WebSocket | Connected; received valid live state |
| Kotak authentication | AUTHENTICATED |
| Dhan authentication | AUTHENTICATED |
| Account book connection | Connected and fresh |
| Runtime errors | None reported at final check |
| Docker service at host boot | Enabled |
| Application restart policy | `unless-stopped` |
| `/demo`, `/paper`, `/api/demo`, `/api/paper` | HTTP 404 |

Both authentication supervisors show their next refresh at **6 November 2026, 08:00 IST**. Startup and daily refresh scenarios were exercised with controlled clocks. Actual application startup login was observed on the VPS. A transient Dhan invalid-TOTP rejection recovered on the automatic retry; no credential change was needed. A future morning refresh has not yet occurred and is not claimed as observed.

The current broker books report zero positions, zero orders and zero fills. The dashboard therefore shows zero gross session P&L from confirmed empty books. Net after fees remains unavailable because authoritative charges have not been supplied. The funds gate is blocked independently of monitoring connectivity.

## Failures corrected

1. Kotak's documented `5203 / No Data` empty-book response was incorrectly treated as a connection failure. It is now accepted only for empty positions/orders/trades books; other errors stay blocked.
2. Negative available funds incorrectly hid the account books. Monitoring now remains available while the funds gate blocks entries.
3. Kotak lacked a reusable startup/daily/expired-session manager. Login now uses single-flight refresh, cooldown/backoff, safe status reporting and client replacement without holding the authentication lock during broker HTTP calls.
4. Dhan login before 08:00 could skip the same morning's scheduled refresh. It now refreshes at 08:00, before expiry and after invalidation. Data sockets reconnect when the token changes.
5. The first crossing after arming could be missed because the runtime had no previous monitored price. Monitoring now seeds crossing history while the order gate remains closed.
6. Queued entries could outlive authorization or freshness. Workers now recheck the gate, session, cutoff and tick age before the durable send transition; unsent entries can be marked ABORTED.
7. Unfilled acknowledgements with a null fill price could not be persisted. They now remain valid acknowledgements with no invented fill price.
8. Conflicting order IDs/quantities and regressing terminal reports could overwrite ledger state. These updates now fail closed.
9. Unknown submissions could remain unreconciled after restart without a signal runtime. Account refresh now reconciles persisted uncertain outcomes; earlier unresolved sessions block new entries.
10. Completed exits could lead to a subsequent position mismatch. Recovery now checks only the remaining quantity after confirmed exit fills.
11. Report reconciliation held the runtime tick lock during HTTP calls. Those calls now happen outside that lock. Broker pacing is shared across worker threads.
12. Position P&L could stay stale between account refreshes. Snapshots now value positions against current fresh ticks and expire stale quotes/books. Position-only feeds cannot submit orders or unlock signal readiness.
13. Closed realized P&L depended on an unnecessary live quote, and missing data could appear as zero. Closed P&L now comes from broker books; unknown open values and unverified carry-forward costs remain unavailable.
14. State and UI silently truncated order/fill rows. All broker-returned NSE EQ rows are now available.
15. A broken dashboard stream could leave old monetary values visible. Fetch timeouts, invalid-stream handling and a stale-data watchdog clear those values and disable arming.

The active application and production image have no demo/paper trading route or synthetic position source. Simulation code is confined to tests and is not copied into the application image. Legacy unrelated VPS data was not deleted.

## Validation evidence

- **130 Python tests passed** against the actual installed SDK and a disposable PostgreSQL 16 container on a separate internal Docker network. No broker credentials were supplied to the tests.
- Separate **20-worker, independent-ledger PostgreSQL race test passed**: exactly five LONG and five SHORT reservations, no duplicate symbol reservations; final run 0.180 seconds for the complete concurrent batch. This is a database test, not exchange-order latency.
- Twenty concurrent refresh requests produced one login. Slow report calls did not serialize unrelated HTTP calls on the authentication lock.
- The full HTTP replay passed: validated signal intake → exact broker mappings → completed ORB → binary tick → first post-arm cross → market-order request → acknowledgement/fill → EOD exit → repeated EOD check → restart without another order. All exchange behavior in this test is simulated.
- The real SDK wire contract passed through `httpx.MockTransport`; it checked the `MKT`, `MIS`, exact trading symbol and tag fields without contacting an exchange.
- Lost acknowledgements, delayed tag visibility, partial/unfilled reports, order conflicts, terminal-state regression, stale queued ticks, disarm, malformed ticks and persisted restart recovery passed.
- Long/short realized and unrealized P&L, closed positions without quotes, unverified carry-forward cost, stale books/quotes, negative funds and 151-row books passed.
- Desktop and 390-pixel mobile browser checks passed, including P&L components, 151 orders/fills, disabled order controls, invalid-stream clearing and zero JavaScript errors.
- The **actual production dashboard** passed separate desktop/mobile browser checks through a temporary GET-only SSH proxy. The proxy has been stopped. Production screenshots are saved beside this report.
- Python compilation, JavaScript syntax and `git diff --check` passed. CI now includes the pinned SDK and desktop/mobile Playwright checks; no GitHub CI run or push is claimed.

## Live instrument and historical-data audit

All **495 eligible NSE EQ symbols** in this project's current universe resolved exactly in both the authorized Kotak master and Dhan's official master, with no unresolved symbol. These are eligible equity symbols, not an assumption that every Nifty constituent row is tradeable EQ. Full symbol/ID mappings are in `live-broker-mapping-audit-2026-11-05.json`.

Read-only Dhan historical queries for RELIANCE, TCS and HDFCBANK returned the first one-minute timestamp at 09:16 IST, consistent with the configured end-labelled 09:15 opening candle. The broker returned wider arrays than the requested two-minute range; opening-range selection continues to require one exact expected timestamp. This is historical-data evidence, not proof of a new live tick, ORB crossing or exchange fill.

## Retry and latency contract

The service uses one active supervisor per broker account. Durable PostgreSQL reservations protect caps across workers; HTTP submission workers keep broker networking off the ticker callback. Per-process sliding-window pacing allows at most 10 mutating requests/second and 20 report requests/second. The account read model refreshes about every five seconds and expires after twenty seconds; WebSocket state is sent every two seconds.

After a placement timeout or missing acknowledgement, the original tag is reconciled with bounded read retries and subsequent account refreshes. **The placement itself is not blindly replayed.** A temporarily absent tag cannot prove that the first request failed. Explicit rejection and unresolved ambiguity remain visible rather than risking a duplicate position.

## Remaining acceptance gates

This is not a zero-bug guarantee or completed live-trade certification. Current production remains disarmed with server order permission off. The reported stage is `CURRENT_SESSION_SIGNALS`; the report is UNAVAILABLE, mappings/ORB/ticker for current candidates have not started, and the market-entry window is closed. Funds are below the configured requirement. Separate operator-arming TOTP is not configured.

Before live-trade acceptance, the operator must verify the VPS outbound IP is whitelisted in Kotak, fund/risk settings and operator controls are correct, and a genuine current-session research report is accepted before the intake deadline. During a market session, real ticker freshness, the ORB crossing, broker acceptance/protected price, actual fills, partial-fill handling and EOD square-off still require observed acceptance. No real orders were placed to manufacture this evidence.

Kotak documents that login, report and portfolio APIs do not verify IP-based order eligibility. Its current market-order guidance also describes protection-limit conversion of API `MKT` requests, so acceptance is not a guaranteed immediate fill. See [official IP and order guidance](https://github.com/Kotak-Neo/Kotak-Neo/blob/main/docs/static-ip-whitelisting.md) and [official empty-book response contract](https://github.com/Kotak-Neo/kotak-neo-python/blob/main/docs/functions/README.md).

Machine-readable final evidence: `production-components-2026-11-05.json`.
