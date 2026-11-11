# Sole VPS pipeline: completion and remaining live evidence

Current plan: [MAIN_PLAN.md](../MAIN_PLAN.md). This is a deployment report, not a second plan.

## Changes completed

- Paused local `kotak-bridge-health-check` daily 09:02 automation through the Codex automation tool. The two historical one-time test automations remain paused.
- Changed GitHub news collection, candidate-freeze, JSON publishing, learning audit and candidate-outcome workflows to manual-only. Changed live/news deployment workflows to explicit/manual deployment; pushes cannot reactivate the old pipeline.
- Removed seven obsolete automation-planning documents from the main news repository, removed old `docs/plan` documents from both repositories and local checkout, and closed superseded design PR #14. Git history is retained; older release folders are rollback history.
- Published identical canonical `MAIN_PLAN.md` documents and canonical README/AGENTS entry points in both repositories.
- Implemented internal SELECT-only SQL intake, shared validation/idempotency/date/hash acknowledgment, retired external POST route and sole `news_collector.vps_final` selection. The new selector has no separate critic or deterministic stock-pick fallback. Direct source content and source timestamp policy is used with mechanical date/ticker/schema gates.
- Enabled only VPS collection every ten minutes and final attempts at 09:00, 09:05, 09:10, with a strict 09:12 commit deadline and 09:15 trading-list lock. No 08:00/08:30/08:55 analyst scheduler remains.

## Deployed and verified state

The initial internal release passed 204 dashboard/trading tests and 137 news tests. A subsequent committed deployment strengthened producer provenance, final timing/holiday gates and UI serialization; its recorded suite passed 213 tests. The current collector release includes bulk archive synchronization and job deadlines and passed 137 news tests. See `reports/vps-internal-20261111.md` for that evidence.

Read-only checks after resumption on 11 November:

- Dashboard current release `3d7cafd6904e566313a4c36cf6d97de4ec6850ce`; healthy, `VPS_INTERNAL` intake, authenticated broker, disarmed and zero open positions. Only the prior two real IRB diagnostic orders remain; no order submitted by this task.
- News current release `vps-final-20261111-2`; active ten-minute timer and final timer next scheduled 12 November at 09:00 IST.
- Completed source-to-database heartbeat at **14:32:28 IST**, 98 source routes, 73 with items, five failed, three blocked, zero pending batches. These values are completed-run evidence, not a guarantee that every source works.
- External authenticated signal POST returns **410**. Internal intake returns `LEGACY_REPORT_PRESERVED` for today's historical report. Trading gate returns `LEGACY_OR_UNVERIFIED_PRODUCER`; it cannot arm from the old local publisher's list.
- Today's legacy seven-stock news report and eight-stock dashboard record were preserved without relabeling or merging. The first genuine `vps-codex-final-v1` session report is expected from the next regular session's final job; it has not yet been observed live.

## GitHub source synchronization

- News repository: `97617cfa4df20ca0e6a104843e23ac1e85d5c1ac` — complete deployed collector modules, registries, deployment files, current dashboard intake and shared contract, tests and canonical plan synchronized to main.
- Dashboard repository: `d878879252e321ec038e9d4d7dd187cf82ab16c5` — internal intake, producer/final gates, current supporting code/tests and same canonical plan synchronized to main.
- Source-only commits do not mean the VPS was redeployed again; the deployed code/release pointers above are the actual runtime evidence.

## Limits and blocked verification

OpenCode's last eligible slot remains QUOTA_LIMIT for `opencode/deepseek-v4.1-flash-free`. Codex discovery's last eligible result was NO_VERIFIED_RESULTS. Python collection continues independently. Neither status is permission to invent stock picks.

Automatic approval review rejected a real Codex FINAL smoke test on the frozen database snapshot because it would export internal/derived news to the external model service without additional payload/destination approval. The rejected invocation did not run and was not bypassed. Offline/isolated tests and production read-only checks are complete; a real-model smoke test and the first next-session producer-to-lock run remain unverified.

No new research batch, synthetic production signal, broker order, credential print, broker-budget change or arming action was performed in this work.
