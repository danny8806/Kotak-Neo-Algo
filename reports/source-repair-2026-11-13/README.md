# Source repair — 2026-11-13

## Status: prepared, **not active**

The configuration change below was written to the release directory on the VPS
but is **not being used by the running collector**. The collector image bakes
`sources.json` into itself, so the change has no effect until the image is
rebuilt.

| Item | State |
|---|---|
| Host release config `releases/news-chunks-20261113/news_collector/sources.json` | 98 routes, **97 enabled**, Moneycontrol RSS disabled |
| Live image `market-news-db-collector:news-chunks-20261113` (built 10:53 IST) | 98 routes, **98 enabled**, nothing disabled |
| Production collection behaviour | still polls Moneycontrol RSS |

## Why it is not live

`ops/news-stack/Dockerfile` copies the package into the image:

```dockerfile
COPY --chown=newsbot:newsbot news_collector/ /app/news_collector/
```

`compose.yaml` mounts only `news_archive:/app/news_collector/data`,
`codex_auth` and `cli_inbox`. It does **not** bind-mount `news_collector/`.

The loader resolves its config relative to the module file:

```python
ROOT = Path(__file__).resolve().parent
sources = json.loads((sources_path or ROOT / "sources.json").read_text(...))
```

Inside the container `ROOT` is `/app/news_collector`, i.e. the image layer.

Verification command:

```bash
docker run --rm --entrypoint python \
  market-news-db-collector:news-chunks-20261113 -c "
import json
s=json.load(open('/app/news_collector/sources.json'))['sources']
print(len(s), sum(1 for x in s if x.get('enabled',True)))"
# image : 98 98
# host  : 98 97
```

## Correction to earlier reporting

A pass was run at 13:49 IST (`news_ingest_runs.id=890`) and was initially
recorded here as proof the repair worked. That was wrong. The pass ran the
baked image config, which is why `news_source_checks` still refreshed
`Moneycontrol RSS` at `13:49:05 IST` with 15 items. The drop from 81 to 79
returning sources was a coincidental PIB Hindi timeout, not the repair.

## Change prepared

Only one active route was disabled:

| Route | Action | Reason |
|---|---|---|
| `Moneycontrol RSS` | `enabled=false` | Every Moneycontrol RSS variant tested is frozen at 2024-04-23 |

Working replacements kept active:

- `Moneycontrol stocks` HTML — 26 items in the 13:40 pass
- `Google News discovery — Moneycontrol`
- `NSE announcements RSS` / other NSE corporate feeds
- `SEBI RSS`

Two NSE routes were checked and deliberately **kept enabled**:

- `NSE announcements archive mirror` recovered with HTTP 200 and the same
  707-item document as the primary NSE feed. Deduplication prevents duplicate
  records.
- `NSE statement of deviation RSS` is an official low-frequency feed; its
  newest filing is 2025-05-02. It is retained rather than deleted.

`SEBI releases web` is a stale 2026-11-10 health row for a retired HTML route,
not a current poll source. `SEBI RSS` is active.

## Validation of the prepared config

Run against the release directory with `PYTHONPATH` set to it:

- `python -m news_collector.source_registry --check` → `REGISTRY_OK`
  (74 endpoints, 34 active poll sources, 0 errors)
- Collector suite → **217 tests, OK**
  (required installing `protego`, `dateparser`, `feedparser`, `news-please`,
  `pymupdf`, `trafilatura`, `extruct` into `/opt/kotak-test-venv`)

These validate the file only. They do not prove production is using it.

## To make it live

```bash
cd /home/jadhavdnyaneshwar701/market-news-db/current
docker build -f ops/news-stack/Dockerfile \
  -t market-news-db-collector:source-repair-20261113 .
# then repoint compose.yaml image: to the new tag
systemctl start market-news-collect.service
```

Then confirm `Moneycontrol RSS` no longer appears in a refreshed
`news_source_checks` row. The build context is `current/`, which resolves to
`releases/news-chunks-20261113`, so a rebuild reproduces the live code plus
this one config change. The previous image remains tagged and pullable for
rollback.

## Rollback (host file only)

```
/home/jadhavdnyaneshwar701/market-news-db/current/news_collector/sources.json.bak-2026-11-13-pre-repair
```

Copy that file over `sources.json` to discard the prepared change. No source
history, articles, signals, orders or fills were deleted or rewritten.

## Files

- `sources.json` — the prepared 97-enabled configuration
- `../source-health-2026-11-13.md` — full per-source health audit

## Version control

The collector directory on the VPS is **not a git repository**
(`fatal: not a git repository`), so this change has no commit or push history
on the server. The report files in this repository are the only durable record.
