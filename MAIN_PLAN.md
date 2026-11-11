# Sole production plan: VPS news collection to the dashboard

Updated 11 November 2026. This is the only current architecture and implementation plan. Other historical reports are evidence, not alternative instructions.

## One production path

**VPS scheduler → Python + Codex FETCH + OpenCode → private news database → Codex FINAL → internal dashboard intake → 09:15 executable stock-list lock.**

Everything above runs on the existing Ubuntu VPS. The existing dashboard and news collector are cooperating services on that VPS, with separate persistent databases. No workstation, Codex desktop automation, GitHub inbox, GitHub collection schedule, public signal POST or external HTTP bridge participates in production selection or delivery.

GitHub stores source and supports explicit code maintenance/deployment. It is not the daily news transport or scheduler. Legacy GitHub research workflows are manual-only. The local 09:02 publisher is paused. Do not reactivate either or create another publisher.

## Clock and roles — Asia/Kolkata

| Stage | Clock | Work |
|---|---|---|
| VPS Python collector | Every ten minutes, 24/7, weekends included | Incremental source collection, source-status records, timestamp/issuer parsing, deduplication and durable DB persistence. A single-flight lock prevents overlaps. |
| Codex FETCH | Eligible half-hour slots, configured maximum 48/day | Independently discover fresh source URLs using actual live search; preserve retrieval evidence. Reserve 08:00–09:30 on regular NSE sessions for final work. |
| OpenCode discovery | Eligible half-hour slots, configured maximum 48/day | Independent discovery using `opencode/deepseek-v4.1-flash-free`, subject to provider quota. Failure must not stop Python. |
| Evidence freeze | 09:00 on regular NSE sessions | Freeze source records from previous regular NSE session 15:00 until strictly before today 09:00, with publication and observation timestamps before cutoff. |
| Codex FINAL | 09:00; retry attempts at 09:05 and 09:10 | One sole selector using the frozen combined source records, no live tools. No separate critic and no heuristic/keyword fallback selecting additional stocks. Commit before 09:12. |
| Internal dashboard intake | Every five seconds inside dashboard service | Read today's immutable final report through a SELECT-only news DB account; validate and persist through the shared intake function. No HTTP request. |
| Executable trading-list lock | 09:15 | Only READY stocks received internally strictly before 09:15 are eligible for that session. Exact Dhan/Kotak mapping and existing entry gates remain mandatory. |

Final research checks the regular NSE calendar, not weekdays alone. The included calendar currently covers 2026 and must be updated for later years. News collection continues on closed days. No market-hours entry is created from a weekend or late research report.

## News selection policy

- Use the source's reported content, real URL and stated publication timestamp directly. One dated source is sufficient. Attribute claims; do not claim independent news verification.
- Do not add independent corroboration, second-source, official-filing, materiality or counterevidence review gates to stock selection.
- Never invent stories, sources, timestamps, symbols or picks. Unknown publication time remains unknown and cannot enter the frozen time-limited selection.
- Keep mechanical source-time, exact symbol/current NIFTY 500 membership, schema, deduplication, direction and daily-limit checks.
- Codex FINAL selects up to five LONG and five SHORT; fewer or zero is valid. The published report uses existing schema v1.0 and `execution_enabled=false`.
- Model auth/quota failure means retry/UNAVAILABLE, not another selecting model or fabricated fallback. Empty/NO_TRADE output must not clear an accumulated dashboard list.

## Data and internal intake

The collector preserves source documents, URLs, publisher/observation/fetch timestamps, source errors, issuer mapping, versions and cursors. PostgreSQL writes are transactional and idempotent. Pending staged batches survive DB outages; source cursors advance only with committed persistence.

The final immutable record is `news_morning_reports`, tagged `pipeline_version=vps-codex-final-v1`. Existing legacy completed reports are historical and are not silently converted or republished. The new job is `news_collector.vps_final`; older analyst/critic modules are not active scheduling targets.

The dashboard receives SELECT-only access to the news database over Docker's private network. `live_desk.news_intake` reads only today's final report and calls `bridge.app.ingest_report` as an **internal Python function**, not an HTTP bridge. That function retains schema/date/universe checks, daily transaction locking, cumulative five-per-side limits, conflict detection, idempotency and server receipt time.

The intake confirms ACCEPTED or DUPLICATE only with matching session date and SHA-256. Repeated reads/restarts do not duplicate stocks. The old public signal-write route is retired in internal mode. Dashboard login and read APIs remain.

## Existing trading boundary

This plan connects research to the current dashboard; it does not alter the authorized one-share strategy, broker credentials, live permissions, persistent day pauses, ORB freshness gates, order reconciliation or exits. Existing configured flow uses broker login startup/08:00, Dhan feed warmup09:14, completed09:15–09:16 range, entries09:16–09:30 and software exit15:00 IST. Orders require real current broker mappings and the existing readiness conditions. Neither a research report nor a model call sends a trade.

## Source registry and agents

The Python collector uses feedparser for validated Atom feeds, lxml for their HTML summaries, and Trafilatura to extract bounded visible article text when publisher summary metadata is absent. These helpers operate on already-fetched source bytes inside the existing collector. They do not create another scheduler, network route, selector, inferred publication time or source-access bypass. Other tested browser, processing and CLI products are not active production workers until separately integrated and validated for a concrete source.

The verified native toolset is also installed as optional capabilities in the collector image, inventoried by `python -m news_collector.tool_capabilities`. Article-text fallback can use news-please and Readability on sanitized already-fetched bytes; the existing PDF enrichment command can recover an empty/error page through PyMuPDF, pdfplumber or pdfminer, within its existing size/page bounds. Browser, MCP, OCR, monitoring, table and alternate crawling tools are available on demand; installation does not create another worker or permit arbitrary source navigation. They are not all run on every URL. AI SDK imports, public model catalogues and accessible homepages are not qualified research agents or usable dated news routes. The isolated small Qwen/Ollama benchmark remains a diagnostic, with no new production model service or stock selector.

SearXNG is a private search helper for the same Python collector, with no published port, stock-selection role or scheduler. When Serper is unconfigured, indexed discovery may use `NEWS_SEARXNG_URL=http://news-search:8080`. It retains each route's declared publisher-domain filter and deliberately discards search-engine dates; results remain undated discovery leads until real publisher metadata is available. Engine CAPTCHAs/denials remain failures and are not bypassed. This helper is an internal source lookup, not dashboard signal transport or another producer. Its private settings file and container image digest are managed by the VPS deployment.

Production source URLs and enabled states are in the deployed news service's `news_collector/sources.json` and `source_registry.json`. At the 11 November audit the source file contained 98 registered routes and 86 enabled flags, including seven provider APIs requiring private keys/opt-in. Effective polling still obeys access restrictions, keys and cost guards; use the completed run's source-health records to establish actual coverage. A registered or enabled route is not a successful-fetch guarantee. Do not invent source coverage or enable paid providers implicitly.

Additional CLI names/assignment templates may remain as inactive adapters; they are not production workers until an authenticated live-retrieval runner is configured, tested and explicitly included. Do not claim they are running, automatically install them, or introduce a second final selector. Exact actual model IDs and quota states must be reported from runtime evidence, not inferred from a CLI name. Codex's model was not pinned/evidenced in the audited VPS invocation; the old desktop Sol configuration does not establish it.

OpenCode free hosted models must be checked through the actual supported CLI, not a generic raw-inference API preflight. Open-source client software and zero-price hosted inference do not establish unlimited provider allowance. A provider quota error remains unavailable; do not reset identities or invent a quota-reset time. Reuse each tool's own authorized login through its supported credential store. A first-time provider login or missing API key requires private account setup; do not copy Codex/broker credentials into other products. This changes neither the scheduled OpenCode model nor Codex FINAL's sole selection role.

## Acceptance and maintenance

Validate the two stages in isolated tests before activation: immutable cutoffs, source-to-symbol consistency, no invented fallback, quota failure, strict final deadline, same-day internal intake, repeated/restarted handoff, daily caps and disabled external publishing. Keep test data out of production reports and broker endpoints.

After deployment verify: active VPS timers, actual completed source-to-DB heartbeat, a final report from the new producer, internal date/hash acknowledgment, exact mapping, no parallel local/GitHub producer and retained historical/order records. A running timer is not proof of fresh DB writes; report errors and incomplete real-session evidence honestly.

Never use older planning documents, branches, diagrams or run reports to restore retired ChatGPT/desktop/GitHub publishing, extra selection stages, an external bridge or a different timing policy. Change this document first when the user changes the plan.
