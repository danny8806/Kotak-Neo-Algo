# ChatGPT scheduled task: research and send today's report

Configure this prompt on the ChatGPT scheduled task. Connect an authorized HTTPS action that can POST JSON directly to the dashboard receiver. Store the bearer token in that connected action's secret/auth settings; never place it in this prompt or report.

> Every NSE trading day shortly after 09:00 Asia/Kolkata, load the current official Nifty 500 constituent list and screen the entire list for material news first available from the previous NSE session at 15:00 IST through today's 09:00 IST. Check NSE/BSE corporate announcements and company filings, then corroborate material events against reliable financial press, regulatory disclosures and relevant sector/company sources where available. Record which sources/universe snapshot were checked, verify source URLs and publication times, current company-symbol identity, figures, significance and counterevidence. Do not claim that every source on the internet was searched. Select at most five LONG and five SHORT candidates, ranked separately from 1 to 5. Fewer candidates are acceptable; do not invent candidates or fill slots with weak evidence. Use only current official Nifty 500 symbols; exclude symbols without an exact live-eligible NSE cash-equity mapping.
>
> Produce one complete JSON object matching `schema/stock-signals.schema.json` version 1.0. Set today's IST `session_date`, today's 09:00:00+05:30 `news_cutoff_at`, actual `generated_at`, `market` to `NSE`, `timezone` to `Asia/Kolkata`, and `execution_enabled` to `false`. Each candidate needs `symbol`, `side`, `rank`, `reason`, `news_summary`, `first_available_at`, at least one HTTPS source with `url`, `title`, and `published_at`, and `confidence` only if defensible. All events and sources must be available by the cutoff. If nothing is sufficiently supported, use `NO_TRADE` or `UNAVAILABLE`, an empty `signals` list, and a clear `reason`.
>
> Send the entire JSON object directly to the connected dashboard action for `POST https://<configured-dashboard-host>/api/v1/signals/incoming`. Include `Content-Type: application/json`, `Authorization: Bearer` using the connected action's stored secret, and a stable `Idempotency-Key` for this session. Do not use GitHub, a repository file, or an intermediary publisher to deliver the report.
>
> Confirm delivery only if the bridge returns HTTP success and JSON status `ACCEPTED` or `DUPLICATE` with today's `session_date` and the same `sha256` as the canonical JSON body. Report the acknowledgment and hash. If the connected action is unavailable, the request fails, or the acknowledgment does not match, say `NOT DELIVERED` and give the reason. Producing the JSON or completing the scheduled run alone is not delivery. Never place orders or include credentials in the report.

## Receiver contract

- Endpoint: `POST https://<dashboard-host>/api/v1/signals/incoming`.
- Body: complete schema v1.0 JSON object, maximum 256,000 bytes.
- Headers: `Content-Type: application/json`, `Authorization: Bearer <secret stored in the connected action>`, `Idempotency-Key: <stable unique session key>`.
- A report must arrive after 09:00 IST. `READY` must arrive before 09:14 IST.
- Same report retry returns `DUPLICATE`; conflicting report for the session returns HTTP 409.
- `GET /api/v1/signals/status` and `/api/v1/signals/latest` also require the bearer token. Dashboard state confirms what the terminal received.

## Setup needed outside this repository

The dashboard host needs public HTTPS, DNS/TLS routing to the live app, and a private `SIGNAL_BRIDGE_TOKEN`. The ChatGPT account needs a connected app/action that can make authenticated HTTPS POST requests and securely store the matching secret. The app already exposes the receiver; this repository cannot grant ChatGPT account permissions or create its connected-app secret.
