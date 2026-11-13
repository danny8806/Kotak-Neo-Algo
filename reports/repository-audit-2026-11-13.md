# Repository and live deployment audit — 2026-11-13

## Live release identity

| Component | Evidence | Verdict |
|---|---|---|
| Dashboard release | `/home/jadhavdnyaneshwar701/kotak-news-live/current` → `releases/early-final-20261113` | Live |
| Dashboard `REVISION` | `2401ad94d999227fe9a5861f1281e6f4c4389260` | Equals `origin/main` HEAD |
| Dashboard file check | SHA-256 of `live_desk/{supervisor,news_intake,app,research_contract}.py` and `MAIN_PLAN.md` | Byte-identical to the local working tree |
| Collector release | `/home/jadhavdnyaneshwar701/market-news-db/current` → `releases/news-chunks-20261113` | Live |
| Collector `REVISION` | `9a67e1a8d15164c4762be164de513064f79b628f` | Not present on `origin` |

The dashboard container started at `2026-11-13T05:51:13Z`, is `healthy`, and
has restart policy `unless-stopped`. Local `HEAD` is `e972070`; the working
tree matches `origin/main` (`2401ad94`) after ignoring CRLF line endings. The
local branch has not been fast-forwarded.

## Test evidence

- Full VPS suite against the **exact live dashboard release**
  (`early-final-20261113`): **244 tests, OK, 35.9 s** using `/opt/kotak-test-venv`
  and isolated test schemas.
- News collector unit suite against `news-chunks-20261113`: **137 tests, OK**.
- Previous 213-test result was from the older `3d7cafd` release and is not the
  current evidence.

## Repository gap: collector code is not versioned

The deployed collector contains **22 Python modules that do not exist in this
repository**:

```
codex_only.py            final_preflight.py      frozen_gateway.py
news_chunks.py           source_gateway.py       tool_capabilities.py
tool_extractors.py       tool_router.py          tool_worker.py
transports.py
test_access_recovery.py  test_approved_tools.py  test_codex_only.py
test_early_final_window.py  test_final_input_encoding.py
test_final_prevention.py  test_native_tools.py  test_news_chunks.py
test_private_search.py   test_source_repairs.py  test_tool_extractors.py
```

Five committed snapshot files differ materially from production:

| File | Difference |
|---|---|
| `vps_final.py` | +222/−36 lines: exact 5+5 enforcement, repair retry, chunked input, preflight audit, Codex-only cached mode |
| `continuous_worker.py` | +50/−7: native tool router, preflight, chunk-cache warmup, pending-batch status |
| `engine.py` | +37/−9 |
| `broker_sources.py` | +43/−8 |
| `archive.py` | 5 changed lines |

`db_store.py` and `source_registry.py` differ only by a UTF-8 BOM. The local
snapshot also has no `sources.json` or `source_registry.json`; the only copy is
`reports/system-flow/deployed-news-sources/sources.json`, which is older (for
example, 5paisa still points at `/news/stocks` instead of the working `/news`).

**Consequence:** the running collector cannot be rebuilt, reviewed or tested
from this repository. The deployed code is authoritative but untraceable.

## Live behaviour verified

- Collector pass `news_ingest_runs.id=880`, 13:21 IST: 98 configured,
  80 item-bearing, 5 failed, 22,867 archived observations,
  `DATABASE_SYNC_OK`, `pending_batches=0`.
- Source health: 81 of 98 configured sources OK; 6 errors, 4 empty,
  7 cost-guarded. Full detail in `reports/source-health-2026-11-13.md`.
- Dashboard `/health`: `LIVE_ONLY`, `orders_armed=false`, intake
  `VPS_INTERNAL`, today `NO_NEW_CANDIDATES` / research `UNAVAILABLE`.
- `/api/state`: `research_gate=RECEIVED_AFTER_LOCK`, `watchlist_locked=true`,
  `watchlist_eligible=false`, 10 displayed signals, **0 eligible**.
- Unauthenticated `/api/state` returns **401**; unauthenticated login is
  rejected. Retired public routes return **404**, not a writable endpoint.
- No order intents today; no live order was sent during any audit.

## 12–13 November report evidence

| Date | Genuine `news_morning_reports` | Dashboard intake | Gate outcome |
|---|---|---|---|
| 2026-11-12 | `READY`, 10 signals, `vps-codex-final-v1`, 09:10 cutoff | 09:11:50 IST, same provenance | Before 09:15 → eligible list |
| 2026-11-13 | `UNAVAILABLE`, 0 signals, `CLI_ERROR` at 09:10 | genuine report rejected as no candidates | No genuine list |
| 2026-11-13 (manual) | not in `news_morning_reports` | 09:16:08 IST, 10 signals labelled `vps-codex-final-v1` | `RECEIVED_AFTER_LOCK`, 0 eligible |

The 13 November manual list was inserted by
`reports/final-input-fix-20261113/add_dashboard_list.py` through
`bridge.app.ingest_report(..., strict_final=False)`. The 09:15 lock prevented
execution, but the report falsely carries the FINAL producer's provenance.
If a similar insert happened before 09:15, it would pass the provenance gate.
The manual producer should be labelled distinctly (for example
`operator-manual`) rather than `vps-codex-final-v1`.

## Open risks

1. Production collector code is unversioned and not reproducible from the repo.
2. Manual dashboard reports can claim `vps-codex-final-v1`; only the receipt
   time currently stops a pre-lock insertion.
3. `MAIN_PLAN.md` says the 11 November audit had 86 enabled flags; the deployed
   `sources.json` has **98 enabled** of 98.
4. Six configured sources still fail (Financial Express ×2, Reuters, NDTV
   Profit, GDELT DOC API, NSE archive mirror) and four are static-empty.
5. Codex and OpenCode both hit quota on 13 November; FINAL correctly wrote
   `UNAVAILABLE`, so the morning had no genuine candidates.
6. Database credentials previously exposed still require rotation.
7. Weekend behaviour is correct: the next morning timer is Monday 16 November
   08:00 IST; collection continues every 10 minutes.
