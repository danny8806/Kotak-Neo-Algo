# VPS internal news-to-dashboard release — 11 November 2026

Status at **14:00 IST**. This report records what was actually deployed and
verified on the VPS. It supersedes the 12:07 audit and the 12:13 recheck. The
sole plan is [MAIN_PLAN.md](../MAIN_PLAN.md).

## 1. Architecture now in force

**VPS scheduler → Python collector + Codex FETCH + OpenCode → private news
database → Codex FINAL → internal dashboard intake → 09:15 executable stock-list
lock.**

No workstation, desktop automation, GitHub inbox, GitHub collection schedule,
public signal POST or external HTTP bridge participates in production selection.
The retired route `POST /api/v1/signals/incoming` returns `410` in internal mode.

## 2. Code changes in this release

- `live_desk/news_intake.py`: SELECT-only SQL handoff. Reads today's
  `news_morning_reports` row, requires `evidence.pipeline_version ==
  vps-codex-final-v1`, enforces same-day date, cutoff ≤ 09:00 and generation ≤
  09:12, then calls `bridge.app.ingest_report` in-process. No HTTP, no write grant.
- `live_desk/research_contract.py`, `live_desk/nse_calendar.py`: shared pipeline
  version, final window and regular-NSE-session check. The 2026 holiday calendar
  is included and must be extended for later years; dates outside the published
  year fail closed and never open an entry window.
- `bridge/app.py`: nullable `pipeline_version` on `signal_reports` and
  `signal_batches`, additive startup migration, provenance stored and returned by
  the authenticated status API. A legacy/null-provenance report is never
  entry-eligible. The retired public route returns `410`.
- `live_desk/supervisor.py`: the 09:15 lock requires a regular NSE session, exact
  producer version, receipt strictly before 09:15, cutoff ≤ 09:00, generation ≤
  09:12 and generation ≥ cutoff. The retired batch-aggregation fallback was
  removed. `research_gate` is exposed in the snapshot. All `weekday()>=5` session
  gates were replaced with holiday-aware checks.
- `live_desk/app.py`: WebSocket payloads go through `jsonable_encoder`; `/health`
  reports unauthenticated intake mode/status for deployment verification.
- `scripts/validate_signals.py`: optional `strict_final` enforces the final window.
- `ops/activate_live_vps.sh`: refuses activation unless the mode is `internal`, the
  SELECT-only news URL is valid, Compose defaults to internal, `/health` shows
  `VPS_INTERNAL`, intake is not `STARTING`/`INTAKE_BLOCKED`, `/api/state` returns
  `401`, the retired route returns `410` and the status payload contains
  `pipeline_version`.
- `.github/workflows/deploy-live-vps.yml`: `workflow_dispatch` only, guarded to
  `refs/heads/main`. A push to `main` no longer deploys production.

## 3. Dashboard activation

### 12:45 IST — internal intake first activated

- Release: `/home/jadhavdnyaneshwar701/kotak-news-live/releases/vps-internal-20261111`
- Image: `kotak-news-live:vps-internal-20261111`
- Recorded commit: `3f35483290f9a9b0211a617a50b3c29cca54d075`
- Container: `kotak-news-live-kotak-live-news-1`, bound to `127.0.0.1:8010`
- Networks: `kotak-news-live_default` and `market-news-private`
- Runtime mode: `SIGNAL_INGEST_MODE=internal`; intake URL uses the SELECT-only
  `news_reader` account on the private news network

That container proved the internal path but predated the WebSocket encoder and
health-intake changes: `/health` had no `news_intake`, the database had no
`pipeline_version` column and `/ws` logged `TypeError: Object of type date is not
JSON serializable`.

### 13:55 IST — redeploy from the committed SHA

- SHA: `3d7cafd6904e566313a4c36cf6d97de4ec6850ce` (branch merged to `main`)
- Release: `/home/jadhavdnyaneshwar701/kotak-news-live/releases/3d7cafd6904e566313a4c36cf6d97de4ec6850ce`
- `last_verified_live_commit` now records the same SHA
- `current` symlink and container point at the new release
- Activation script output:
  `PASS: internal intake, provenance status, retired public route 410, orders DISARMED`

Post-deploy checks against the live container:

- `/health`: `LIVE_ONLY`, `orders_armed:false`,
  `news_intake.mode=VPS_INTERNAL`, `status=LEGACY_REPORT_PRESERVED`
- `signal_reports` and `signal_batches` both have `pipeline_version`
- authenticated `/api/state`: `research_gate.status=LEGACY_OR_UNVERIFIED_PRODUCER`,
  `required=vps-codex-final-v1`, `pipeline_version=null`
- all eight stored signals show `entry_eligible=false` and
  `entry_status=RESEARCH_ONLY`
- authenticated `/api/v1/signals/status` returns `pipeline_version` (null today)
- authenticated `/ws` returns a JSON snapshot; zero serialization errors since
  the restart
- unauthenticated `/api/state` returns `401`; the retired route returns `410`;
  `/ready` returns `503` after 09:30 by design

### SELECT-only grant

`news_reader` has `SELECT` only on the ten news tables, including
`news_morning_reports`. `has_table_privilege` confirms `INSERT=false` and
`UPDATE=false`; `SELECT=true`. The dashboard therefore cannot alter the news
record it reads.

## 4. Legacy report preservation

Today's news report was written by the legacy producer at **09:00:31** with seven
signals and no `evidence.pipeline_version`. The optional 09:05/09:10 slots did not
replace it. `vps_final` correctly refused to relabel a completed legacy report
(`LEGACY_REPORT_PRESERVED`).

The dashboard row for 2026-11-11 was received at **09:13:11** with eight signals
(seven plus a later batch) and no provenance. It must render as
`LEGACY_OR_UNVERIFIED_PRODUCER` and must not be executable. The first genuine
`vps-codex-final-v1` report can only be produced at tomorrow's 09:00/09:05/09:10
run.

## 5. News collector repair

The collector had been timing out on every ten-minute job after the archive grew.
Changes, released as `market-news-db/releases/vps-final-20261111-2` (image
`market-news-db-collector:vps-final-20261111-2`):

- `db_store.py`: bulk cached article inserts/updates, bulk symbol and revision
  writes instead of per-row statements.
- `archive.py`: oldest-first replay with an optional deadline.
- `continuous_worker.py`: `NEWS_JOB_TIME_BUDGET_SECONDS` (default 300) and a
  `faulthandler` watchdog (`NEWS_JOB_WATCHDOG_SECONDS`, default 420).
- `engine.py`: total wall-clock fetch deadline.
- `vps_final.py`: refuses to relabel legacy completed reports and records selector
  status/model/quota evidence in `final_audit`.
- `codex_research.py`: records the parsed model evidence from Codex JSON.

Measured on the VPS against an isolated schema: 28,121 staged records across three
batches synced in **5.13 s, 4.45 s and 8.38 s** (about 18 s total) versus minutes
before the patch.

Spool recovery and current state:

- 13 pending batches were drained in two replay passes (12 + 1); pending is 0.
- First full job on the patched image finished in **76 s** with
  `DATABASE_SYNC_OK` at **13:48:36 IST**: 98 configured sources, 74 with items,
  5 failed, 3 blocked, 16,778 observations, 1 batch committed.
- `last_successful_db_sync.json` was rewritten at 13:48:36 and
  `news_ingest_runs` received the matching row.
- `market-news-collect.timer` was re-enabled after the manual run. The next
  scheduled job (13:50) finished at **13:51:23 IST** with `DATABASE_SYNC_OK`,
  16,765 observations, 1 batch committed, 0 pending. Two consecutive scheduled
  runs now prove a stable heartbeat, not just a manual run.

Intraday runs at :10, :40 and :50 report `opencode: SKIPPED_BUDGET` and
`codex: SKIPPED_BUDGET` because model slots are only claimable in the first ten
minutes of each half hour (`model_slot`), not because collection used the whole
budget. The :00 and :30 runs are the eligible slots; source collection continues
every ten minutes. Final selection is a separate 4-minute `run-news-job.sh
morning` service on the 09:00/09:05/09:10 timer, so this does not replace or
disable the FINAL job.

Actual model evidence from the 11:32 IST slot (`news_source_checks`, kind
`ai_cli`):

- OpenCode CLI: `QUOTA_LIMIT`, HTTP 429, 0 items, model
  `opencode/deepseek-v4.1-flash-free`, provider reset recorded as
  `2026-11-12T00:00:00Z`.
- Codex CLI: `NO_VERIFIED_RESULTS`, 0 items. No Codex model ID is recorded in
  the audit.

Both lanes ran and failed closed with zero records. They did not fabricate
candidates and did not block the Python collection lane.

## 6. Test evidence

- Full VPS suite: **213 tests, OK, 34.672 s** using `/opt/kotak-test-venv`,
  `TEST_DATABASE_URL` pointed at the news PostgreSQL host with isolated schemas.
- News collector unit suite inside the new image: **137 tests, OK, 0.730 s**.
- `py_compile` on the changed Python modules and `node --check
  live_desk/static/app.js` pass.

These tests use synthetic exchange responses. They do not prove broker behavior,
fills or profitability.

## 7. Open limits

- No verified `vps-codex-final-v1` report exists yet; the first possible one is
  tomorrow's FINAL run. Today's dashboard correctly rejects the legacy report.
- OpenCode is quota-blocked until `2026-11-12T00:00:00Z` (HTTP 429, free-model
  limit); Codex returned `NO_VERIFIED_RESULTS` with no model ID evidenced. Both
  lanes failed closed with zero records rather than inventing candidates. The
  FINAL job must do the same if quota is still unavailable.
- Five sources failed and three were blocked in the reference run; registered
  routes are not a coverage guarantee.
- The NSE holiday calendar covers 2026 only and fails closed after that; it must
  be extended before 2027.
- Database credentials exposed in earlier tool output still need rotation.
- The dashboard is bound to `127.0.0.1:8010`; public access depends on the
  existing HTTPS proxy, which was not re-tested in this change.

## 8. Next verification

1. At the next regular session, verify the 09:05/09:10 FINAL row carries
   `vps-codex-final-v1` in `evidence`, the internal intake acknowledges the same
   date and SHA-256, and the 09:15 lock admits only that report.
2. Confirm the first live `vps-codex-final-v1` report renders as executable and
   that a later or legacy batch still cannot change the frozen list.
3. Rotate the exposed database credentials in a planned window and re-verify the
   collector heartbeat and intake after the change.
