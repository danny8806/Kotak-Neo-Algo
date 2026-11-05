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

## Local service

For local receiver development, install `requirements-bridge.txt`, start PostgreSQL 16, set `SIGNAL_DATABASE_URL` to its PostgreSQL URL and set `SIGNAL_BRIDGE_TOKEN`, then run `python -m bridge.app`. It binds to `127.0.0.1:8000` by default and is not reachable from ChatGPT cloud tasks. The combined dashboard service runs at port 8010 and embeds this receiver.
