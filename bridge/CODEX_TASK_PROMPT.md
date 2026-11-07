# Codex scheduled task: research and deliver today's signals

Run on each NSE trading weekday at 09:02 Asia/Kolkata.

1. Read `schema/stock-signals.schema.json` and the current official eligible-universe snapshot at `live_desk/nifty500_universe.json`.
2. Research NSE and BSE filings and reliable financial sources for events first available from the previous NSE session at 15:00 IST through today's 09:00 IST cutoff. Use current official Nifty 500 symbols only. Verify each source URL, publication time, symbol identity, materiality, and counterevidence. Do not invent candidates or sources.
3. Prepare up to five LONG and five SHORT candidates, ranked separately from 1 to 5. Fewer candidates are valid when evidence is insufficient. If none qualify, use `NO_TRADE`; if the required sources or universe cannot be verified, use `UNAVAILABLE`. Non-READY reports must have an empty `signals` array and a clear `reason`.
4. Produce one JSON report that exactly matches schema version 1.0. Set `execution_enabled` to `false`, `market` to `NSE`, `timezone` to `Asia/Kolkata`, today's session date, the exact 09:00 IST cutoff, and the actual generation time. Every candidate requires a reason, news summary, first-available timestamp, and at least one real HTTPS source published no later than the cutoff.
5. Save the report to a temporary JSON file and run `node bridge/send_codex.js <report-file>` from the project root. The sender reads `SIGNAL_BRIDGE_URL` and `SIGNAL_BRIDGE_TOKEN` from the ignored project `.env`, posts directly to the dashboard bridge, and prints only the acknowledgment. Never display or include secret values in the report or result.
6. Report delivery only when the sender prints `ACCEPTED` or `DUPLICATE` with today's session date and SHA-256. If it fails, report `NOT DELIVERED` and the safe error. Do not retry with another host, disable TLS checks, arm orders, or place orders.
