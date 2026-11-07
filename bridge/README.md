# Direct ChatGPT signal bridge

The live dashboard already embeds this authenticated API receiver. ChatGPT sends the complete JSON report directly to the dashboard over HTTPS; GitHub is not in the daily delivery path.

## Receiver routes

- `POST /api/v1/signals/incoming`: full schema v1.0 report, Bearer token and `Idempotency-Key` required.
- `GET /api/v1/signals/latest`: current-session report, Bearer token required.
- `GET /api/v1/signals/status`: current-session delivery status, Bearer token required.
- `GET /api/v1/health`: liveness only; no signal data.

The receiver validates the signal schema, report date, news cutoff, source timestamps, duplicate JSON keys, deadline and duplicate/conflicting submissions. It stores one immutable report per session. Identical retries return `DUPLICATE`; conflicting reports return HTTP 409. `READY` must arrive between 09:00 and 09:14 IST.

## Configure the direct connection

1. Set a private, random `SIGNAL_BRIDGE_TOKEN` (32+ characters) on the live dashboard service.
2. Give the dashboard a stable public HTTPS host routed to the live app. Its receiver path is `/api/v1/signals/incoming`.
3. Connect an authorized ChatGPT app/action that supports authenticated HTTPS POST and store the matching token in its secure auth settings.
4. Use [the scheduled task prompt](CHATGPT_TASK_PROMPT.md). Confirm `ACCEPTED` or `DUPLICATE` and the matching SHA-256 in the task result.

The repository's `bridge.send` is a command-line sender for an authorized process. It is not a mechanism by which ChatGPT scheduled tasks can automatically execute local Python. Scheduled tasks need an available connected action with HTTP capability. No GitHub job or checked-in signal file participates in daily signal delivery.

## Codex scheduled task

For a local Codex scheduler, use [the Codex task prompt](CODEX_TASK_PROMPT.md) and schedule it for 09:02 Asia/Kolkata on NSE trading weekdays. `node bridge/send_codex.js <report.json>` sends the generated v1.0 report to `SIGNAL_BRIDGE_URL` using `SIGNAL_BRIDGE_TOKEN` from the ignored project `.env`; no OpenAI API key is required. The dashboard bridge remains responsible for validating the report and rejecting late, malformed, duplicate-conflicting, or out-of-universe submissions. The scheduler must report delivery only after an `ACCEPTED` or `DUPLICATE` acknowledgment.

## Unattended Astra research job

Install `requirements-research.txt` and set `OPENAI_API_KEY` in the ignored project `.env` or the local process environment. Run `python -m bridge.research` after 09:00 IST. The default shadow run prints a validated report locally.

The job matches market-wide NSE and BSE disclosures to eligible symbols by ISIN, checks the official constituent file against the terminal snapshot, and applies a configurable five-session median NSE turnover floor (default ₹25 crore). Astra screens filing metadata and ranks candidates using selected exchange PDFs and targeted web search. The report uses exchange timestamps and links from the collected records. BSE errors or unreconciled pagination produce `UNAVAILABLE`. The news screen uses targeted web corroboration and PDF attachments.

Once a shadow run verifies both feeds and successfully exercises Astra, schedule the job for 09:02 IST with a single publisher per session. `python -m bridge.research --deliver` opts into the existing direct bridge. It checks for a prior report, validates the payload, calls `bridge.send`, and verifies authenticated read-back against the acknowledgement hash. The live terminal handles Dhan monitoring and gates Kotak execution behind its existing arming and risk checks.

## Local service

For local receiver development, install `requirements-bridge.txt`, start PostgreSQL 16, set `SIGNAL_DATABASE_URL` to its PostgreSQL URL and set `SIGNAL_BRIDGE_TOKEN`, then run `python -m bridge.app`. It binds to `127.0.0.1:8000` by default and is not reachable from ChatGPT cloud tasks. The combined dashboard service runs at port 8010 and embeds this receiver.
