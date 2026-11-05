# End-to-end completion roadmap

Updated: 5 November 2026 (Asia/Kolkata)

## Goal

One disarmed-by-default workflow: ChatGPT researches current Nifty 500 constituents and sends a validated report directly to the authenticated dashboard bridge; Dhan supplies NSE cash-equity candles and ticks; the desk evaluates the 09:15 one-minute ORB in the report direction; Kotak Neo is the only broker. Position amount, total and per-side limits, gross exposure, entry cutoff and EOD exit remain operator-configurable. The system stores each stage and shows its state in the authenticated dashboard.

## Completion gates

1. **Universe and instruments** — Fetch the official Nifty Indices Nifty 500 constituent CSV and current Dhan and Kotak NSE cash masters. Join by exact current symbol and ISIN as an audit aid, but accept an order mapping only when exchange, cash segment, instrument group, series, canonical trading symbol and numeric broker token are all valid. Save master dates and SHA-256 values. Explicitly report non-EQ, suspended, renamed, missing and ambiguous rows; never guess aliases. Repeat on every session so additions/removals are reflected. The current official source can contain 501 rows; use the source rows, not an assumed count.
2. **Research and delivery** — The weekday ChatGPT task emits schema-valid JSON with at most five LONG and five SHORT candidates, evidence URLs/times, and execution disabled. It sends directly to the bridge only through an authenticated HTTPS action and verifies the server acknowledgment/hash. Missing endpoint/authentication means `NOT DELIVERED`, not a GitHub fallback. The Codex validator is an independent read-only check and never creates or delivers trading signals.
3. **Market and strategy** — Authenticate Dhan read-only, confirm the current session and server timestamps, validate the 09:15 one-minute candle convention, subscribe to fresh data for only validated candidates, and test the first cross in the stated side. Stale feed, invalid candle, unresolved instrument, limit breach, missing report or late signal blocks entry.
4. **Broker and lifecycle** — Confirm Kotak identity, funds, orders, trades, fills and positions; persist an intent before one submission; reconcile acknowledgments, partial fills, errors, reconnects and restarts. At configured EOD time, exit only broker-confirmed net quantities and reconcile the closing fills. Keep the dashboard disarmed by default and require per-session operator arming. Do not use a simulated order as evidence of a live broker order.
5. **Audit and operations** — Persist immutable report/evidence, source/master hashes, Dhan market timestamps, decisions, settings, intents, broker events, fills, exits, costs and realized P&L. Add alerting, backups and restore drills, deployment/readiness checks, and a per-session acceptance report. Keep research performance evaluation separate from execution.

## Work completed in this pass

- Re-ran the full mocked/unit suite and today's report validator.
- Added a current-universe Dhan/Kotak mapping audit utility and regression tests.
- Queried the official current Nifty 500 constituent list and the latest public instrument-master snapshots; the generated audit records exact coverage and blocks unsupported instruments.
- Confirmed the scheduled ChatGPT research task and the separate Codex validator are configured. Their next scheduled executions are not evidence that delivery or trade execution succeeded.

## Current blockers to real readiness

- No project-local Dhan/Kotak credentials or direct bridge HTTPS endpoint/token were present during this pass.
- Kotak authenticated account identity, broker books and trade permissions cannot be verified here. No real orders should be submitted until those checks pass and the operator explicitly arms a session.
- Public instrument masters prove symbol resolution, not account-specific MIS eligibility, live quotes, order acceptance, fills, square-off behavior or profitability.
- ChatGPT's scheduled task must have a secure connected HTTP action configured for this dashboard host before it can deliver directly. The task prompt alone cannot create that connection.
- The currently implemented audit uses publicly obtainable master snapshots; production should obtain Kotak's current file path from the authorized Kotak Scrip Master API response rather than rely on a guessed or hardcoded file date.

## Acceptance checklist

- [ ] Current official constituents fetched and auditable; all rows classified, no silent drops.
- [ ] Dhan and Kotak master snapshots current and hashed; each candidate's exact eligible cash-equity mapping passes, all others have an explicit block reason.
- [ ] Automated weekday report conforms to the schema and comes from verifiable sources; no unsupported claims or fallback publishing.
- [ ] A real non-trading bridge submission is accepted and read back with matching session and hash.
- [ ] Dhan live read-only login, timestamp, candle, and tick freshness checks pass in a market session.
- [ ] Kotak read-only identity, funds, positions, order/trade reports, and exact selected-symbol mappings pass.
- [ ] PostgreSQL-backed runtime tests pass: 20 concurrent entry attempts within the configured five-per-side/ten-total limit, partial-fill recovery, EOD exit, idempotency, and persistence.
- [ ] Broker-approved test/paper environment or explicit per-session operator authorization is used before any real order test; never send an order merely to prove connectivity.
- [ ] Backup/restore, alerts, VPS readiness, and end-of-day reconciliation have a recorded drill.
