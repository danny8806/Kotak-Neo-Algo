# Kotak ORB release QA

9 November 2026, Asia/Kolkata. The authenticated production probe ran at 16:09 IST against deployed commit `5c8e0a6`. The QA patch and regression tests are in feature-branch commit `407f30a`. Production and the QA patch are separate releases.

## Results

| Area | Parameters and failure cases exercised | Result |
| --- | --- | --- |
| Research intake | Session date and IST offsets, bearer authentication, payload and source validation, idempotency, parallel batches, daily side limits, exact 09:15 lock, late research kept out of entry eligibility, restart | Passed in integration |
| Dhan and Kotak mapping | Exact NSE cash-equity symbols, broker security IDs, ambiguous or missing rows, duplicate IDs, single master download in the ten-stock replay | Passed in integration |
| Opening range | 09:15–09:16 candle selection, start/end timestamp convention, missing or malformed OHLC, concurrent reads, Dhan request pacing, a slow peer candle | Passed in unit and integration |
| Exchange tick handling | Binary packet length, exchange timestamp, timestamp offset, float32 price normalization, stale packets, five-second crossing baseline, socket reconnect and old-generation orders | Passed with the QA patch |
| Capital and sizing | LONG and SHORT crosses, equality at the ORB boundary, integer shares, paise arithmetic, zero affordable shares, per-stock capital, aggregate gross cap, per-side and total limits, 09:16 entry boundary, exact 09:30 cutoff | Passed against a separate integer-paise model in 20,000 seeded cases |
| Settings and authorization | All seven settings, cross-field validation, persistence after restart, armed edits locked, CSRF origin checks, authenticated state and WebSocket, disarmed order gate | Passed in API and browser tests |
| Concurrency | 30 simultaneous breakout candidates with duplicate ticks, durable ten-entry cap, 20 PostgreSQL reservation attempts across ten processes, slow database reads and broker reports, queued-order checks after broker pacing | Passed with no duplicate dispatches or reservations observed |
| Additional scheduling stress | Seed 1732026, 50 runs, 3,000 shuffled crossing packets and 500 simulated SDK dispatches | Zero duplicate dispatches observed. The full PostgreSQL suite covers durable caps separately. |
| Broker lifecycle | Exact Kotak SDK payload, timeout after acceptance, late report visibility, missing order tag, conflicting broker IDs and fills, retry reads, unknown outcomes surviving restart | Passed in isolated integration |
| Exit and reconciliation | 09:30 working-entry cancellation, late fills, EOD opposite-side exits for exact confirmed MIS quantities, exclusion of unrelated or mismatched positions, partial fills and rejected exits | Passed in isolated integration. Rejected or partially filled terminal exits can require operator follow-up. |
| P&L and dashboard | Closed versus open positions, stale quotes and books, realized versus unrealized P&L, persisted fill deduplication, desktop and mobile navigation, journal, export, stale UI state | Passed in browser and integration |

The two GitHub workflows passed. The live-terminal workflow ran 173 Python tests, container and Compose checks, and the PostgreSQL concurrency probe. The ten-stock simulated replay measured 67.1 ms median and 94.8 ms p95 from packet receipt to SDK dispatch. These are samples from replay, excluding broker execution and fill time.

- [Live-terminal QA workflow](https://github.com/danny8806/Kotak-Neo-Algo/actions/runs/42603105739)
- [Signal and safety workflow](https://github.com/danny8806/Kotak-Neo-Algo/actions/runs/42603110831)
- [Feature branch PR](https://github.com/danny8806/Kotak-Neo-Algo/pull/8)

## Reproduced defect and correction

In the deployed build, a price received after a six-second gap could trigger against an expired crossing baseline. A regression test reproduced the queued submission. The QA patch seeds a new baseline after a gap and requires two fresh prices for the next qualifying cross. It also tags ticks by WebSocket connection and rechecks that connection immediately before Kotak dispatch. A queued order from the previous connection is durably aborted, and a reconnect requires fresh quotes and operator re-arming.

## Production acceptance status

The live dashboard was healthy, protected account state returned HTTP 401 without a session, and both brokers authenticated with fresh books. The account had zero open positions, zero unresolved orders, zero current candidates, and zero live dispatch samples. Trading permission was off and the readiness endpoint returned HTTP 503. The blockers included the closed entry window, missing current-session signals and opening data, and the funds check.

Saved policy at the probe was 100,000 INR per stock, 1,000,000 INR gross, ten total positions with a maximum of five per side, 09:30 IST entry cutoff and 15:15 IST square-off. The QA patch is pending deployment. A qualifying market-session run is still needed to verify live 09:15 candle delivery, real Kotak order acceptance, and real-fill reconciliation and exit behavior.
