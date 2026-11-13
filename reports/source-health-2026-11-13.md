# Fetcher source health — 2026-11-13 (audit 13:50, repair applied 14:00 IST)

Scope: the live collector release `news-chunks-20261113`
(`/home/jadhavdnyaneshwar701/market-news-db/current`) and its own
`news_source_checks` rows. This is the collector's real probe result; no source
was bypassed or whitelisted during the audit.

## Result

Latest full source pass: `news_ingest_runs.id=880`,
`2026-11-13 13:21 IST`, configured **98**, returning items **80**, failed 5,
archive total **22,867**. The 13:40 pass completed with **81 OK** and
**22,702** fetched items.

| Health | Configured sources | Meaning |
|---|---:|---|
| OK with items | **81** | Reachable and returned at least one item |
| EMPTY | 4 | HTTP 200 but parser found no items |
| ERROR | 6 | HTTP failure/timeout |
| SKIPPED_COST_GUARD | 7 | Paid provider not opted in |
| **Total configured** | **98** | |

Six further health rows are not in `sources.json`: Codex CLI and OpenCode CLI
both `QUOTA_LIMIT` (current), plus `NSE announcements web`,
`BSE announcements web`, `RBI releases` (EMPTY) and `SEBI releases web` (404),
which are **stale rows from 2026-11-10**. Those four HTML routes were retired
and are no longer polled; SEBI RSS remains active, so the 404 is not a live
collection failure.

Collector heartbeat: `DATABASE_SYNC_OK` at 13:21:35 IST,
`sources_with_items=80`, `pending_batches=0`, observations 22,867.

Later pass `news_ingest_runs.id=890` at 13:49 IST: 98 configured,
**79 with items**, **6 failed**, 19,464 observations, `DATABASE_SYNC_OK`,
`pending_batches=0`.

> **Correction.** This pass was first reported as proof of a repair. It was
> not. It ran the image's baked 98-enabled configuration, which is why
> `news_source_checks` refreshed `Moneycontrol RSS` at 13:49:05 IST with 15
> items. The 81 → 79 change was a coincidental PIB Hindi timeout. See
> `source-repair-2026-11-13/README.md`.

## Repairs prepared at 13:47 IST — **not active in production**

| Route | Action | Evidence |
|---|---|---|
| Moneycontrol RSS | **Disabled in the release file only** | Every Moneycontrol RSS variant tested is frozen at 2024-04-23; `Moneycontrol stocks` HTML (26 items) and `Google News discovery — Moneycontrol` remain active |
| NSE announcements archive mirror | **Kept enabled** | It recovered and returned HTTP 200 with the same 707-item document as the primary feed; deduplication prevents double records |
| NSE statement of deviation RSS | **Kept enabled** | Official feed returns 20 items, newest 2025-05-02; it is a low-frequency category, and other NSE corporate feeds remain active |
| SEBI releases web | **No action** | Its 404 row is stale from 2026-11-10; the retired HTML route is not polled and `SEBI RSS` is active |

The original file was backed up as
`news_collector/sources.json.bak-2026-11-13-pre-repair`. The file now holds 98
registered routes, **97 enabled**, and validates as `REGISTRY_OK` with **217
passing tests**. However, `sources.json` is copied into the collector image by
the Dockerfile and is not bind-mounted, so the running container still uses
the old 98-enabled copy at `/app/news_collector/sources.json`. Production
behaviour is unchanged until the image is rebuilt and `compose.yaml` is
repointed.

## What improved since the 11 November audit

- **5paisa Stock News — fixed.** The collector now polls `https://www.5paisa.com/news`
  instead of `/news/stocks`; 8 current items pass the article-path rule.
- **Angel One Stock News — fixed.** 10 items, no longer robots-blocked.
- **ICICI Direct Market News — fixed.** 19 items, no longer robots-blocked.
- **BSE corporate actions, BSE financial results, BSE board meetings,
  BSE insider trading — OK** with 302, 51, 715 and 9 items.
- **GDELT global article index RSS — OK** with 4,959 items. The separate
  `GDELT DOC API` route still times out, but it is no longer the only GDELT path.
- **Google News discovery feeds** cover several blocked publishers (Reuters,
  NDTV Profit, Financial Express, Moneycontrol, Mint, ET, CNBC-TV18).

## Sources needing attention

### ERROR (5 persistent + 1 intermittent)

| Source | Evidence |
|---|---|
| Financial Express legacy RSS | HTTP 403 Forbidden |
| Financial Express markets | HTTP 403 Forbidden |
| Reuters India news | HTTP 401 Forbidden |
| NDTV Profit markets | HTTP 403 Forbidden |
| GDELT DOC API | HTTPS read timeout (7 s) |
| PIB India releases Hindi | Intermittent read timeout in the 13:49 pass; was OK earlier |

These are publisher-side/access failures, not collector crashes. The 403/401
routes must not be bypassed; Google News discovery feeds provide dated leads.

### EMPTY (configured, 4)

| Source | Evidence |
|---|---|
| HDFC Securities News | HTTP 200, no links matching `/(?:market-news\|research)/...` |
| Business Standard BSE filings | HTTP 200, page is client-rendered; no static article links |
| ALFA Finder indexed discovery | HTTP 200, zero items |
| Google News indexed — BazaarWatch | HTTP 200 RSS, zero `<item>` elements |

Stale rows (checked 2026-11-10, not currently polled): `NSE announcements web`,
`BSE announcements web` and `RBI releases` returned HTTP 200 with no static
items; `SEBI releases web` returned 404 after the SEBI site restructure. Their
official RSS/table replacements remain active.

### Frozen but marked OK

| Source | Latest item | Note |
|---|---|---|
| Moneycontrol RSS | 2024-04-23 | Disabled; feed returns 15 items, all April 2024 |
| NSE statement of deviation RSS | 2025-05-02 | Retained; official low-frequency feed, 20 items all from 2025 |
| Federal Reserve board meetings | 2026-09-08 | No newer meeting item |
| NSE related party transactions RSS | 2026-09-11 | No newer filing |
| NSE unitholding patterns RSS | 2026-10-22 | No newer filing |
| NSE SAST regulation 31 RSS | 2026-10-29 | No newer filing |
| NSE financial results RSS | 2026-11-03 | No newer filing |
| NSE circulars RSS | 2026-11-11 | One day old |

`OK` means the HTTP request and parser succeeded, not that the feed is fresh.
Old items are excluded from the morning window by `published_at`.

Cursor freshness of the 81 OK sources: 22 have a cursor item within 24 hours,
8 are older than 24 hours, and 51 have no cursor row because no new dated item
has advanced it. That is not itself a failure.

### Intentionally inactive

- **Paid APIs, no opt-in (7):** Brave News, Exa, GNews, Marketaux, NewsAPI,
  NewsData, Tavily — `SKIPPED_COST_GUARD`.
- **AI CLIs (2):** Codex CLI and OpenCode CLI — `QUOTA_LIMIT`. On 13 November
  Codex FINAL returned `CLI_ERROR`, so the pipeline wrote `UNAVAILABLE` at 09:10.

## Conclusion

The collector is healthy and running every 10 minutes: 81 of 98 configured
sources produced items, 22,702 observations were fetched, the database sync
succeeded, and no staged batch is pending.

The real coverage gaps are five active HTTP/timeout failures and four
static-empty pages. The safe repairs prepared were:

1. Disabled the globally frozen Moneycontrol RSS and kept the working HTML and
   Google News routes.
2. Kept the NSE archive mirror: it recovered and matches the primary feed, so
   it adds resilience without duplicate records.
3. Kept the low-frequency NSE statement-of-deviation feed and documented that
   its newest filing is from May 2025.
4. Left SEBI's retired HTML 404 row as historical evidence; `SEBI RSS` is the
   active route.
5. Financial Express, Reuters, NDTV and GDELT DOC remain visible failures;
   their Google News discovery feeds provide dated leads.

Source configuration changed only for Moneycontrol RSS in the release file
(98 registered, **97 enabled**); no source history was deleted. Production is
still running the old 98-enabled image, so no data-collection behaviour has
changed yet.
