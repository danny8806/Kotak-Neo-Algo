# Professional live trading workspace

The dashboard is organized into eight independent views: Overview, News signals, Market monitor, Positions & P&L, Order book, Executed fills, Connections & checks, and Configuration. Navigation changes the active view immediately; it does not reload the account or concatenate every function into one long page.

## Interaction and appearance

- Fixed desktop navigation, a compact overview, a persistent state bar, and a mobile navigation drawer.
- A navy navigation rail, light workspace, restrained teal accents, consistent typography, and explicit data-unavailable states.
- The overview shows gross session P&L, funds, positions, readiness, realized/unrealized breakdown, session context, and broker connections. At the tested 1440 × 1000 viewport it fits without scrolling or clipped cards. Smaller screens scroll within the selected workspace.
- Signal search/direction filters and an evidence pane; separate ORB and instrument-mapping tabs.
- Position filters; order status and symbol/ID/tag search; searchable executed fills.
- Fifty rows per page, complete dataset counts, accessible pagination, and CSV export of every matching row. CSV cells that could be interpreted as formulas are escaped.
- Clicking an account row or its detail button opens an accessible native dialog showing the broker snapshot. Escape and close controls return to the workspace.
- Configuration has grouped sizing, exposure and timing controls. Unsaved edits survive live frames and navigation; discard restores saved values. Armed sessions lock editing.
- The authorization dialog preserves explicit acknowledgement and operator verification. Closing clears both fields. The UI does not change the existing server order gate.
- The diagnostics screen runs only GET checks against health, readiness, state, positions, orders and fills. HTTP 503 readiness is reported as blocked, not successful trading readiness.

## Update behavior and latency

The browser uses the existing WebSocket state stream and a bounded polling fallback. Only the active workspace renders detailed tables. Unchanged books retain their DOM nodes and pagination focus; updates do not overwrite edited configuration. There is no external font, UI framework, CDN, chart library, or generated market data dependency. The three uncompressed UI assets total approximately 76 KB.

Invalid or stale state clears financial values, locks order/configuration actions, and marks the workspace stale. Switching views cannot restore stale P&L. A fresh valid frame restores the live views. Navigation never creates a signal or broker order.

## Validation

The Playwright suite covers desktop/mobile appearance and overflow, one visible view at a time, all eight routes, signal evidence, filters, every page of a 151-row book, detail dialogs, CSV export and formula escaping, pagination focus during updates, unsaved edits, discard/save behavior, armed configuration locking, authorization dialog clearing, read-only diagnostics, unhealthy health responses, deep-link reload, mobile navigation, malformed state and reconnect recovery. Simulated settings changes are isolated in the test server; there are no broker or signal mutations.

The full **130-test Python suite** passed again against the installed production SDK and disposable PostgreSQL 16. The separate **20-worker PostgreSQL reservation race** also passed with no duplicates (0.142 seconds for the full batch in this run). JavaScript syntax and whitespace checks passed. These tests include the simulated signal-to-mapping-to-ORB-to-order-to-fill-to-exit-to-restart lifecycle. They do not prove a real exchange fill.

Run browser checks with `npm ci`, `npx playwright install chromium`, and `npm run test:dashboard`. The deployment workflow runs the browser checks and Python suite before activation. Actual production verification uses the authenticated dashboard, read-only account routes and WebSocket, with order permission off. Production screenshots and account-state captures remain local verification artifacts.
