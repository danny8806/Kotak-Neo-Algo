# Ten-stock next-minute ORB test

Design for a one-shot test using the production terminal. The script prepares an
operator-approved test plan and displays results. The existing live supervisor
owns market data, authorization, orders, and recovery. A standalone script does
not instantiate a second execution runtime or call Kotak directly.

## Test universe

The IDs below matched in both broker masters in the 9 November 2026 audit.
Resolve each mapping again from the current masters before starting the test.
The sides are test fixtures to be reviewed with the plan, not research signals.

| NSE stock | Dhan ID | Kotak symbol | Test direction |
|---|---:|---|---|
| RELIANCE | 2885 | RELIANCE-EQ | LONG |
| TCS | 11536 | TCS-EQ | SHORT |
| INFY | 1594 | INFY-EQ | LONG |
| HDFCBANK | 1333 | HDFCBANK-EQ | SHORT |
| ICICIBANK | 4963 | ICICIBANK-EQ | LONG |
| SBIN | 3045 | SBIN-EQ | SHORT |
| AXISBANK | 5900 | AXISBANK-EQ | LONG |
| BHARTIARTL | 10604 | BHARTIARTL-EQ | SHORT |
| LT | 11483 | LT-EQ | LONG |
| ITC | 1660 | ITC-EQ | SHORT |

## Script contract

- Default command previews the ten stocks, their sides, fixed quantity of two
  shares, proposed candle interval, entry cutoff, and current blockers.
- The live command submits that plan to the authenticated terminal. The
  terminal must explicitly support this test plan and accept operator arming
  through its existing acknowledgement and TOTP flow. A test plan is separate
  from the research bridge and must not be published as a news report.
- Use one active supervisor and its existing account ledger. Reject the test
  if another strategy is active, recovery is pending, a selected stock has an
  overlapping Kotak position or working order, or the session is unsuitable.
- Keep the existing server order permission, funds check, entry cutoff,
  per-side and total caps, and gross cap. Reject a plan that cannot finish its
  opening candle and have a post-close entry window before the cutoff.
- The test configuration is session scoped and expires at the cutoff. Its
  metadata, candle bounds, and per-stock outcomes should be visible in the
  dashboard. A restart disarms entries and leaves existing intents in the
  normal ledger for reconciliation and square-off.

## Production call path

1. In the supervisor, call the existing Dhan and Kotak instrument resolvers,
   enforce unique Dhan IDs, and require the exact Kotak NSE EQ symbol. Establish
   the existing `LiveTicker` subscription while disarmed. Once the subscription
   is ready, choose the next IST minute boundary. The policy entry window is
   09:16-09:30, so the opening candle and a post-close entry window must both fit
   inside it. For a ready feed at 09:20:23, the candle is
   [09:21:00, 09:22:00). A 13:04 ready time is rejected by the entry cutoff.
2. Accumulate high and low per stock from exchange trade timestamps in that
   interval. The current `opening_range()` is hardcoded to 09:15, so the test
   needs an explicit interval or a candle collector upstream of the runtime.
   The current ticker reads the first packet in each WebSocket message. Decode
   every framed packet before running the test. Use the verified timestamp
   convention and keep the normal stale-tick gate. A missing or incomplete
   candle leaves that stock ineligible.
3. Seal the range after the minute ends. Load the ranges into the existing
   `LiveRuntime` and use its normal crossing logic. The operator arms after the
   completed ranges and live readiness checks pass. A breakout while disarmed
   remains an observation. A stock already outside its range at arming needs a
   fresh crossing before it can enter.
4. Add a test-only fixed sizing input at the runtime boundary. Use the same
   crossing logic and reserve exactly two shares with
   `LiveLedger.reserve_entry()`. Leave deduplication and exposure enforcement in
   that transaction. If the two-share intent exceeds any limit, skip it. Use the
   normal asynchronous order worker, freshness and authorization recheck,
   durable tag, `KotakSession` pacing, and `place_kotak_order()` gateway.
5. For each stock report its security ID, trading symbol, candle high and low,
   crossing tick, expected side, quantity, tag, broker order ID, and status.
   Distinguish no cross, blocked, accepted, rejected, filled, and unknown.
   Reconcile an uncertain submission by its original tag and never replay it.
   Cancel working bot entries and reconcile any confirmed MIS fills through
   the existing end-of-day path.

## Acceptance checks

First use a test transport with the real gateway and SDK to exercise a
ten-symbol burst, including both sides, duplicate packets, disarming during
reservation, and rejected or unknown acknowledgements. Check that each accepted
request has exactly two shares and the matching NSE EQ symbol. On an armed
live run, only a Kotak order ID or verified broker-book record counts as a
broker acknowledgement. A run with fewer than ten actual crossings may send
fewer orders. Do not create a crossing to complete the count.

This is an implementation design. The current terminal does not expose a
next-minute test-plan endpoint or a fixed-quantity override.
