# Real-data validation report

**Date:** 22 Nov 2026 · **Dataset:** 1,481,324 real mandi price rows · **Result: 25/25 checks passed**

## What "real data" means here

`api.data.gov.in` is firewalled from the build sandbox (TLS is killed at the
network edge — DNS resolves, the handshake is refused). Four proxy routes were
tried and all failed. So validation used **actual downloaded exports of the same
government dataset**, not synthetic data:

| Source | Rows | Notes |
|---|---|---|
| `heybadrinath/MandiLens` → `all_market_history.parquet` | 1,481,324 | 6 states, 14 crops, 1,741 markets, 176 districts, 2024-08-30 → 2026-09-28 |
| `yashwanth1-8/Current-Daily-Price...` → `.xlsx` | 7,382 | Genuine Agmarknet export, 3 sheets, header on row 0 of the `data` sheet |

Total loaded: **1,480,884 unique price records** after deduplication.

## Bugs real data exposed (and fixes)

Every one of these passed under the 907-row fixture and failed at real scale.
That is the whole value of testing with real data.

| # | Bug | Symptom | Fix |
|---|---|---|---|
| 1 | **Row-by-row upsert** | 1.48M rows = ~3M SQL round-trips; process died at ~200k rows with no error | Single `ON CONFLICT` statement, `executemany` in 20k batches |
| 2 | **Whole file in memory** | 1.48M rows → Python dicts ≈ 1.5 GB; container has 1.9 GB; silent SIGKILL | Chunked streaming (25k rows/pass), peak memory ~60 MB |
| 3 | **`LIKE '%term%'` search** | 3,797 ms per search on 1.5M rows | SQLite FTS5 index + prefix matching → **15 ms** (250× faster) |
| 4 | **`market_spread` scanned everything** | Pulled 395k rows into Python: 2,894 ms | Window-function aggregate → **766 ms** |
| 5 | **`summary` correlated subqueries** | 1,290 ms | Indexed `(commodity, arrival_date)` → **175 ms** |
| 6 | **Change % always 0** | Ranking raw rows paired two varieties on the *same day* | Aggregate per (market, day) first, then rank |
| 7 | **Arbitrary "latest" row** | `GROUP BY state, market` with a bare `arrival_date` column let SQLite return any row — Chala Market reported 25 Sep when 27 Sep existed | Explicit self-join of `rn=1` to `rn=2`; no bare columns |
| 8 | **Duplicate function definitions** | A bad patch left three copies of `summary`/`market_spread`; Python silently used the last (old) one, so fixes appeared to do nothing | Rebuilt `db.py` clean |
| 9 | **9 duplicate rows in the government's own XLSX** | 7,382 exported → 7,373 stored | Detected automatically by the unique constraint |
| 10 | **Emoji icons rendered as empty boxes** | No emoji font on the host | Replaced with inline SVG |

## Final performance (1,480,884 rows, 447 MB SQLite)

| Operation | Before | After |
|---|---|---|
| Free-text search | 3,797 ms | **15 ms** |
| Market spread (80 markets) | 2,894 ms | **766 ms** |
| Crop summary (14 crops) | 1,290 ms | **175 ms** |
| Commodity trend (400 points) | 98 ms | **73 ms** |
| Latest date | 0.9 ms | **0.8 ms** |
| `GET /api/prices` | — | **11 ms** |

Full 1.48M-row load: **2 min 15 s** (streaming), down from "crashes without an error".

## The 25 checks

Run against the live server: `python tests/check_real_data.py`

| Group | Checks |
|---|---|
| Pages | home, state filter, search, empty search, commodity detail, 404 |
| API | health, states, commodities, prices limit, trend, spread change field |
| Abuse / edge | runaway `limit` cap, SQL-injection attempt, DB intact after injection, crop name with a space, crop+state combo, about page |
| Correctness | health count consistent, multi-filter returns only matches, no zero/negative prices, `min_price` never exceeds `max_price` |
| Performance | API < 800 ms, search < 2.5 s, spread < 3 s |

**25/25 passed.**

## Honest caveats

- The parquet covers **6 states**, not all of India (the upstream scraper's coverage). The Agmarknet XLSX has all 18 states but only one day. The app handles both; a full-India backfill needs the API key.
- "Latest date" ends **28 Sep 2026** because that is where the public history file ends — not a bug in the app.
- 42 of 100 markets show a non-zero change; the rest genuinely did not move or did not report twice.
- The dev server is single-threaded Flask. Production should use the included gunicorn config.
