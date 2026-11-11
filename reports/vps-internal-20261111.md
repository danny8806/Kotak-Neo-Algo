# VPS internal news-to-dashboard release — 11 November 2026

Status at **13:55 IST**. This report records what was actually deployed and
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

## 3. Dashboard activation verified at 12:45 IST

- Release: `/home/jadhavdnyaneshwar701/kotak-news-live/releases/vps-internal-20261111`
- Image: `kotak-news-live:vps-internal-20261111`
- Recorded commit: `3f35483290f9a9b0211a617a50b3c29cca54d075`
- Container: `kotak-news-live-kotak-live-news-1`, bound to `127.0.0.1:8010`
- Networks: `kotak-news-live_default` and `market-news-private`
- Runtime mode: `SIGNAL_INGEST_MODE=internal`; intake URL uses the SELECT-only
  `news_reader` account on the private news network

Verified live: `/health` returned `LIVE_ONLY` with `orders_armed:false`;
unauthenticated `/api/state` returned `401`; the retired route returned `410`;
`/ready` returns `503` after 09:30 by design.

### Known gap in that container

The running image predates the WebSocket encoder and health-intake changes. Live
evidence on 11 November:

- `GET /health` has no `news_intake` object.
- The dashboard database has no `signal_reports.pipeline_version` column.
- `docker logs` still shows
  `TypeError: Object of type date is not JSON serializable` from `/ws`.

A redeploy from the commit in this report is required. Until then the live
dashboard is on the internal intake path but cannot show provenance or serve a
clean WebSocket.

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

Intraday runs report `opencode: SKIPPED_BUDGET` and `codex: SKIPPED_BUDGET`
because source collection already consumes the 300 s job budget. Final selection
is a separate 4-minute `run-news-job.sh morning` service on the 09:00/09:05/09:10
timer, so this does not replace or disable the FINAL job.

## 6. Test evidence

- Full VPS suite: **213 tests, OK, 34.672 s** using `/opt/kotak-test-venv`,
  `TEST_DATABASE_URL` pointed at the news PostgreSQL host with isolated schemas.
- News collector unit suite inside the new image: **137 tests, OK, 0.730 s**.
- `py_compile` on the changed Python modules and `node --check
  live_desk/static/app.js` pass.

These tests use synthetic exchange responses. They do not prove broker behavior,
fills or profitability.

## 7. Open limits

- The running dashboard image must be replaced by the commit described here before
  `/health.news_intake`, provenance columns and the WebSocket fix are live.
- No verified `vps-codex-final-v1` report exists yet; the first possible one is
  tomorrow's FINAL run.
- Codex/OpenCode quota remains unverified. Codex returned `NO_VERIFIED_RESULTS`
  during the audit and no model ID is recorded. The FINAL job fails closed rather
  than fabricating a selection.
- Five sources failed and three were blocked in the reference run; registered
  routes are not a coverage guarantee.
- The NSE holiday calendar covers 2026 only.
- Database credentials exposed in earlier tool output still need rotation.
- GitHub `main` does not yet contain this release; it is committed on a branch and
  must be merged before the manual deploy workflow can be dispatched from `main`.

## 8. Next verification

1. Deploy the committed SHA and confirm `/health` shows
   `"news_intake":{"mode":"VPS_INTERNAL"`, the status API returns
   `pipeline_version`, and `/ws` no longer logs a serialization error.
2. Confirm today's report renders as `LEGACY_OR_UNVERIFIED_PRODUCER` and is not in
   the executable list.
3. At the next regular session, verify the 09:05/09:10 FINAL row carries
   `vps-codex-final-v1`, the internal intake acknowledges the same date and SHA-256,
   and the 09:15 lock admits only that report.
