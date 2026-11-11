# VPS news and Kotak dashboard

**The sole current plan is [MAIN_PLAN.md](MAIN_PLAN.md). Read it before changing this project.**

VPS scheduler → Python/Codex/OpenCode news collection → private news DB → Codex FINAL → internal dashboard intake → 09:15 executable stock-list lock.

Everything in the runtime news path runs on the existing VPS. GitHub hosts source and explicit code maintenance/deployment; local Codex and GitHub schedules are not production news publishers. Historical reports describe past states and do not override the current plan.

- News source configuration: `news_collector/sources.json` and `source_registry.json` in the deployed news service.
- Final selector: `news_collector.vps_final`.
- Internal dashboard intake: `live_desk.news_intake`.
- Dashboard and broker service: `live_desk/`.
- Existing report schema: `schema/stock-signals.schema.json`.

See MAIN_PLAN.md for clocks, direct-source news policy, exact mechanical gates, model quota handling, private database isolation and test requirements. Never print credentials or place live orders as a connectivity test.
