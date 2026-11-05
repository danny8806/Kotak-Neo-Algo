# Deployment audit — 2026-11-04 (Asia/Kolkata)

This is an evidence record, not a deployment claim.

## Verified locally

- Audited `origin/main` at `e76fa4bac90af99a14d0e738d336fdde67f88b47` in an isolated worktree; older local uncommitted files were left intact.
- The local test suite passed 83 tests before this audit's WebSocket origin regression tests.
- The old port `8004` returned HTTP 200. The planned loopback-only port `8010` did not respond to a public HTTP probe, as intended for a private binding.
- The latest deployment workflow run `41841201975` failed in its SSH deployment step. Its documented missing secrets are `VPS_SSH_PRIVATE_KEY` and/or `VPS_SSH_KNOWN_HOSTS`.
- A strict-host-key, noninteractive SSH attempt with the only local key was rejected by the VPS. No server command was executed.
- This Windows environment has neither GitHub CLI nor Docker CLI. Build and VPS container checks were therefore not performed here.

## Required before VPS verification

1. Rotate the historical VPS password exposed by the older public repository through the hosting provider console. Do not reuse the exposed password.
2. Obtain the VPS ED25519 host fingerprint independently from that console and verify the server and owner.
3. Establish a dedicated deployment key for an authorized server account, and configure the two Actions secrets through a trusted GitHub session. The bootstrap utility in this folder can perform this once its prerequisites are met.
4. Create `/home/jadhavdnyaneshwar701/kotak-news-live/config/live.env` privately on the VPS with the required non-broker application secrets; keep it mode `600`.
5. Rerun the deploy workflow, then check the actual container, localhost health/auth responses, restart behavior, data persistence, and unchanged port `8004` on the VPS.
6. Establish a dedicated HTTPS hostname before public browser or signal-producer access. A local SSH tunnel is suitable for initial private checks.

The direct API, Dhan and Kotak account sessions, 09:15 candle timestamp convention, real research delivery, and broker-native protective order behavior still require authenticated live verification. Orders remain disabled; no trade was placed.
