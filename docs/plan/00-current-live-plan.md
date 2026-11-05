# Current live system status

Updated: 5 November 2026 (Asia/Kolkata).

## Intended daily flow

1. The ChatGPT scheduled task selects up to five LONG and five SHORT candidates from the current official Nifty 500 constituents, with verified sources available by 09:00 IST.
2. It sends one schema-valid JSON report directly to `POST /api/v1/signals/incoming` over authenticated HTTPS and confirms `ACCEPTED` or `DUPLICATE` plus the matching body hash. GitHub is not in this signal path.
3. PostgreSQL stores the report and live order state. Dhan supplies NSE cash-equity candles and fresh market ticks; the terminal validates exact Dhan and Kotak instrument mappings and checks the 09:15 one-minute ORB in the signal direction.
4. An operator configures allocation, position caps, gross exposure, entry cutoff and EOD exit in the dashboard. The terminal stays disarmed by default and submits Kotak orders only after the session readiness and arming gates pass.
5. Kotak order and trade reports confirm fills. The service persists each intent before submission, reconciles restart/retry state and schedules exits for the configured EOD time.

## Current evidence and gaps

- The dashboard, direct signal bridge, Dhan feed, Kotak gateway, Nifty 500 universe/mapping tools and PostgreSQL order ledger are implemented in this repository.
- ORB, lifecycle, validation and mapping checks use mocked tests. A PostgreSQL CI job now includes cross-connection tests for 20 concurrent entry attempts and 20 duplicate report deliveries.
- Local verification passed 71 non-database tests, Python compilation, JavaScript syntax, workflow/Compose YAML parsing, shell syntax and the 5-LONG/5-SHORT signal contract. PostgreSQL integration tests have not run on this computer because it has no PostgreSQL server or Docker.
- The replacement branch is based on `danny8806/Kotak-Neo-Algo` main. Its previous multi-dashboard app is removed from the branch; run PostgreSQL CI before merging or deployment.
- A ChatGPT scheduled run with confirmed bridge acknowledgment has not been demonstrated for this release. No Kotak credentials are configured, so no account session or real order was tested.
- Public HTTPS, DNS/TLS routing, deployment secrets, server PostgreSQL startup and deployed readiness remain unverified for the PostgreSQL version.

## Safety constraints

- Only Nifty 500 members may be delivered; reports are capped at five candidates per side and ten overall.
- No signal may arm trading. The server starts disarmed and order permission defaults to `NO`.
- Ambiguous Kotak submissions are reconciled by tag and never retried automatically.
- Exit quantity comes from reconciled broker fills. The configured EOD square-off is not a broker-native protective stop.
- No real-money execution is considered verified until the ChatGPT delivery, PostgreSQL lifecycle, Dhan data, exact Kotak mappings, account session and controlled broker-order path have all passed.
