# Sole production plan: VPS news collection to the dashboard

Updated 12 November 2026. This is the only current architecture and implementation plan. Other historical reports are evidence, not alternative instructions.

## One production path

**VPS scheduler → Python + Codex FETCH + OpenCode → private news database → Codex FINAL → internal dashboard intake → 09:15 executable stock-list lock.**

Everything above runs on the existing Ubuntu VPS. The existing dashboard and news collector are cooperating services on that VPS, with separate persistent databases. No workstation, Codex desktop automation, GitHub inbox, GitHub collection schedule, public signal POST or external HTTP bridge participates in production selection or delivery.

GitHub stores source and supports explicit code maintenance/deployment. It is not the daily news transport or scheduler. Legacy GitHub research workflows are manual-only. The local 09:02 publisher is paused. Do not reactivate either or create another publisher.

## Clock and roles — Asia/Kolkata

| Stage | Clock | Work |
|---|---|---|
| VPS Python collector | Every ten minutes, 24/7, weekends included | Incremental source collection, source-status records, timestamp/issuer parsing, deduplication and durable DB persistence. A single-flight lock prevents overlaps. |
| Codex FETCH | Eligible half-hour slots, including 08:00 and 08:30, configured maximum 48/day | Pin `gpt-6.1-sol`, medium effort. Independently read/search rotating groups covering every configured source; preserve actual retrieval and source-coverage evidence. Reserve 08:55–09:30 on regular NSE sessions for final work. |
| OpenCode discovery | Eligible half-hour slots, configured maximum 48/day | Independent discovery using `opencode/deepseek-v4.1-flash-free`, subject to provider quota. Failure must not stop Python. |
| Decision evidence snapshot | Start of each unpublished 09:00 / 09:05 / 09:10 decision attempt on regular NSE sessions | Include news from the previous regular trading session 15:00 until the actual attempt cutoff, with both source publication and observation before that cutoff. Each attempt is immutable and records its exact cutoff/hash. A retry can include newly collected news; an accepted completed final is never rerun. If a mechanically invalid report has been rejected before any dashboard acceptance, the same Codex selector may correct that mechanical error before 09:12 using the same frozen news cutoff; preserve the entire rejected report, hash and reason in audit history. |
| Codex FINAL | 09:00; retry attempts at 09:05 and 09:10 | Pin `gpt-6.1-sol`, high effort. Sole selector from combined source news, no live tools. Target exactly five LONG/BUY and five SHORT/SELL. No extra verification/critic/heuristic selector. Commit before 09:12. |
| Internal dashboard intake | Every five seconds inside dashboard service | Read today's immutable final report through a SELECT-only news DB account; validate and persist through the shared intake function. No HTTP request. |
| Executable trading-list lock | 09:15 | Only READY stocks received internally strictly before 09:15 are eligible for that session. Exact Dhan/Kotak mapping and existing entry gates remain mandatory. |

Final research checks the regular NSE calendar, not weekdays alone. The included calendar currently covers 2026 and must be updated for later years. News collection continues on closed days. No market-hours entry is created from a weekend or late research report.

## News selection policy

- Use the source's reported content, real URL and stated publication timestamp directly. One dated source is sufficient. Attribute claims; do not claim independent news verification.
- Do not add independent corroboration, second-source, official-filing, materiality or counterevidence review gates to stock selection.
- Never invent stories, sources, timestamps, symbols or picks. Unknown publication time remains unknown and cannot enter the frozen time-limited selection.
- Keep mechanical source-time, exact symbol/current NIFTY 500 NSE EQ equity membership, schema, deduplication, direction and daily-limit checks. The producer and dashboard must use the same executable EQ/INE universe; other series such as BE must not become executable picks.
- Codex FINAL must target exactly five LONG/BUY and five SHORT/SELL, selected from news rather than copied recommendations or ready-made stock lists. A READY report requires all ten distinct symbols, five per side, each linked to source news in the strict window. If dated news cannot support ten, record the specific shortage and retry before the deadline; never fabricate stories, timestamps or directions to fill a quota. The published report uses existing schema v1.0 and `execution_enabled=false`.
- Model auth/quota failure means retry/UNAVAILABLE, not another selecting model or fabricated fallback. Empty/NO_TRADE output must not clear an accumulated dashboard list.

## Data and internal intake

The collector preserves source documents, URLs, publisher/observation/fetch timestamps, source errors, issuer mapping, versions and cursors. PostgreSQL writes are transactional and idempotent. Pending staged batches survive DB outages; source cursors advance only with committed persistence.

Codex FETCH performs its own source reads and native live searches; it must not just summarize Python's output. An independent coverage ledger records each configured source as attempted, successful, blocked, unavailable or pending. Its dated news joins all Python/tool and successful OpenCode news. FINAL uses the combined eligible records; verification-origin flags, second-source corroboration and source-prestige checks must not exclude otherwise dated news. Extraction of source text/time and exact stock identity are mechanical processing, not an independent news-verification requirement. Do not silently drop records at an arbitrary item-count limit; record a specific context/time overflow if the complete decision input cannot be processed.

The approved tool router dispatches public HTTP/feed, static HTML, configured JS, PDF/OCR/table, change-monitoring, source-discovery and archive-lookup adapters inside this same scheduler. Requests/HTTPX/aiohttp/curl/wget retrieve permitted bytes; feedparser/lxml/Scrapy parse feeds/listings; Trafilatura/news-please/Readability clean article context; Lightpanda/Playwright/Crawlee/Puppeteer/Selenium/Crawl4AI handle source-specific rendering. pypdf/PyMuPDF/pdfplumber/pdfminer extract PDF text; Tesseract/OCRmyPDF recover scanned pages and Camelot/tabula recover tables. pandas/Polars normalize batches, RapidFuzz suggests issuer matches, MinHash groups duplicates, DuckDB reports coverage, and FAISS/Qdrant-local provide bounded hashed-text archive similarity. urlwatch/webchanges prioritize changed pages; feed/sitemap refresh is once daily within an ordinary collector pass. FastMCP and restricted Playwright MCP expose source-reading tools only to FETCH. Each adapter must pass relevant VPS tests before activation; optional-tool failure preserves the original source record. No learned embedding model or separate persistent vector server is introduced.

Heavy browser/OCR tasks are single-flight and pause 08:55–09:30 on regular sessions; lightweight source polling continues. Daily maintenance completion and source task cursors are persisted. Provider/auth/access failures remain visible; do not bypass denied sources or reset identities. A useful fallback must preserve real source content/time and share the job's runtime/size limits.

The final immutable record is `news_morning_reports`, tagged `pipeline_version=vps-codex-final-v1`. Existing legacy completed reports are historical and are not silently converted or republished. The new job is `news_collector.vps_final`; older analyst/critic modules are not active scheduling targets.

The dashboard receives SELECT-only access to the news database over Docker's private network. `live_desk.news_intake` reads only today's final report and calls `bridge.app.ingest_report` as an **internal Python function**, not an HTTP bridge. That function retains schema/date/universe checks, daily transaction locking, cumulative five-per-side limits, conflict detection, idempotency and server receipt time.

The intake confirms ACCEPTED or DUPLICATE only with matching session date and SHA-256. Repeated reads/restarts do not duplicate stocks. The old public signal-write route is retired in internal mode. Dashboard login and read APIs remain.

## Existing trading boundary

The authorized sizing default is now two shares per stock. Dashboard settings select fixed quantity or capital per stock (whole shares = floor(configured capital / breakout price)). Submit that configured size without a local broker-balance/minimum-capital/gross-cap gate; Kotak determines its own acceptance/rejection. Preserve transactional duplicate/position-count checks, broker mappings, permissions, persistent day pauses, ORB freshness, reconciliation and confirmed-fill exits. Existing flow uses broker login startup/08:00, Dhan feed warmup09:14, completed09:15–09:16 range, entries09:16–09:30 and software exit15:00 IST. Dhan chart timestamps label candle starts: select exactly the 09:15 bar only after 09:16, with a 09:14 request buffer. Never use the forming 09:16 bar as the opening range. BUY requires a fresh tick crossing strictly above the first-minute high; SELL requires a fresh tick crossing strictly below its low. Persist source-candle OHLC and each entry�s previous/current tick proof. Neither a research report nor a model call sends a trade. Do not place orders for validation.

## Source registry and agents

The Python collector uses feedparser for validated Atom feeds, lxml for their HTML summaries, and Trafilatura to extract bounded visible article text when publisher summary metadata is absent. These helpers operate on already-fetched source bytes inside the existing collector. They do not create another scheduler, network route, selector, inferred publication time or source-access bypass. Other tested browser, processing and CLI products are not active production workers until separately integrated and validated for a concrete source.

The verified native toolset is also installed as optional capabilities in the collector image, inventoried by `python -m news_collector.tool_capabilities`. Article-text fallback can use news-please and Readability on sanitized already-fetched bytes; the existing PDF enrichment command can recover an empty/error page through PyMuPDF, pdfplumber or pdfminer, within its existing size/page bounds. Browser, MCP, OCR, monitoring, table and alternate crawling tools are available on demand; installation does not create another worker or permit arbitrary source navigation. They are not all run on every URL. AI SDK imports, public model catalogues and accessible homepages are not qualified research agents or usable dated news routes. The isolated small Qwen/Ollama benchmark remains a diagnostic, with no new production model service or stock selector.

SearXNG is a private search helper for the same Python collector, with no published port, stock-selection role or scheduler. When Serper is unconfigured, indexed discovery may use `NEWS_SEARXNG_URL=http://news-search:8080`. It retains each route's declared publisher-domain filter and deliberately discards search-engine dates; results remain undated discovery leads until real publisher metadata is available. Engine CAPTCHAs/denials remain failures and are not bypassed. This helper is an internal source lookup, not dashboard signal transport or another producer. Its private settings file and container image digest are managed by the VPS deployment.

Production source URLs and enabled states are in the deployed news service's `news_collector/sources.json` and `source_registry.json`. At the 11 November audit the source file contained 98 registered routes and 86 enabled flags, including seven provider APIs requiring private keys/opt-in. Effective polling still obeys access restrictions, keys and cost guards; use the completed run's source-health records to establish actual coverage. A registered or enabled route is not a successful-fetch guarantee. Do not invent source coverage or enable paid providers implicitly.

Additional CLI names/assignment templates may remain as inactive adapters; they are not production workers until an authenticated live-retrieval runner is configured, tested and explicitly included. Do not claim they are running, automatically install them, or introduce a second final selector. Exact actual model IDs and quota states must be reported from runtime evidence, not inferred from a CLI name. Codex FETCH and FINAL explicitly pin the model and record the actual invocation configuration in runtime audits.

OpenCode free hosted models must be checked through the actual supported CLI, not a generic raw-inference API preflight. Open-source client software and zero-price hosted inference do not establish unlimited provider allowance. A provider quota error remains unavailable; do not reset identities or invent a quota-reset time. Reuse each tool's own authorized login through its supported credential store. A first-time provider login or missing API key requires private account setup; do not copy Codex/broker credentials into other products. This changes neither the scheduled OpenCode model nor Codex FINAL's sole selection role.

## Acceptance and maintenance

Validate the two stages in isolated tests before activation: immutable cutoffs, source-to-symbol consistency, no invented fallback, quota failure, strict final deadline, same-day internal intake, repeated/restarted handoff, daily caps and disabled external publishing. Keep test data out of production reports and broker endpoints.

After deployment verify: active VPS timers, actual completed source-to-DB heartbeat, a final report from the new producer, internal date/hash acknowledgment, exact mapping, no parallel local/GitHub producer and retained historical/order records. A running timer is not proof of fresh DB writes; report errors and incomplete real-session evidence honestly.

Never use older planning documents, branches, diagrams or run reports to restore retired ChatGPT/desktop/GitHub publishing, extra selection stages, an external bridge or a different timing policy. Change this document first when the user changes the plan.
