# ChatGPT scheduled task: research and send today's report

Configure this prompt on the ChatGPT scheduled task. Connect an authorized HTTPS action that can POST JSON directly to the dashboard receiver. Store the bearer token in that connected action's secret/auth settings; never place it in this prompt or report.

> On each scheduled NSE trading weekday run, load the current official Nifty 500 constituent list and screen newly available material news since the previous run (first run starts from previous NSE session 15:00 IST). Check NSE/BSE corporate announcements and company filings, then corroborate material events against reliable financial press, regulatory disclosures and relevant sector/company sources where available. Verify source URLs and publication times, current company-symbol identity, figures, significance and counterevidence. Do not claim that every source on the internet was searched. Send only new symbols, up to five LONG and five SHORT total for the session. Never repeat a symbol, change its direction, or invent candidates or sources. Use only current official Nifty 500 symbols and exact live-eligible NSE cash-equity mappings. If no new candidates qualify, skip delivery.
>
> Produce one JSON batch matching `schema/stock-signals.schema.json` version 1.0. Set today's IST `session_date`, actual current IST time as `news_cutoff_at`, actual `generated_at`, `market` to `NSE`, `timezone` to `Asia/Kolkata`, and `execution_enabled` to `false`. Rank symbols in this new batch separately by side starting at 1. Each candidate needs `symbol`, `side`, `rank`, `reason`, `news_summary`, `first_available_at`, at least one HTTPS source with `url`, `title`, and `published_at`, and `confidence` only if defensible. All events and sources must be available by the cutoff.
>
> Send the batch JSON directly to the connected dashboard action for `POST https://deltacapitals.systems/api/v1/signals/incoming`. Include `Content-Type: application/json`, `Authorization: Bearer` using the connected action's stored secret, and a content-specific unique `Idempotency-Key`. The bridge accumulates new symbols in the session list. Do not use GitHub, a repository file, or an intermediary publisher to deliver the report.
>
> Confirm delivery only if the bridge returns HTTP success and JSON status `ACCEPTED` or `DUPLICATE` with today's `session_date` and the same `sha256` as the canonical JSON body. Report the acknowledgment and hash. If the connected action is unavailable, the request fails, or the acknowledgment does not match, say `NOT DELIVERED` and give the reason. Producing the JSON or completing the scheduled run alone is not delivery. Never place orders or include credentials in the report.

## Receiver contract

- Endpoint: `POST https://<dashboard-host>/api/v1/signals/incoming`.
- Body: complete schema v1.0 JSON object, maximum 256,000 bytes.
- Headers: `Content-Type: application/json`, `Authorization: Bearer <secret stored in the connected action>`, `Idempotency-Key: <stable unique session key>`.
- A batch must arrive after 09:00 IST and before 15:00 IST. The bridge accepts only new symbols and accumulates up to five per side; conflicting directions return HTTP 409.
- Identical retries return `DUPLICATE`; conflicting content for the same idempotency key returns HTTP 409.
- `GET /api/v1/signals/status` and `/api/v1/signals/latest` also require the bearer token. Dashboard state confirms what the terminal received.

## Setup needed outside this repository

The dashboard host is `https://deltacapitals.systems`; Nginx routes `/api/v1/` over HTTPS to the live app on loopback port 8010. The ChatGPT account needs a connected app/action that can make authenticated HTTPS POST requests and securely store the matching `SIGNAL_BRIDGE_TOKEN`. The app already exposes the receiver; this repository cannot grant ChatGPT account permissions or create its connected-app secret.
