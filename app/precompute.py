"""
Precompute everything the pages need, into one small JSON file.

Why
---
Render's free instance gets about 0.1 vCPU. A DuckDB scan that costs 60 ms on a
laptop costs several seconds there, and the home page needed six of them, which
is why it took 40 s and timed out while the other pages merely crawled.

So we do that work once, offline, and commit the answer. At runtime the app
reads a single JSON file into memory and serves from dicts: no scans, no CPU,
no timeouts. On-demand search still queries the parquet, because those queries
depend on what the visitor typed.

Run after the data changes:
    python -m app.precompute
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import lake  # noqa: E402

OUT = os.path.join(lake.config.BASE_DIR, "data", "real", "precomputed.json")

# Commodities that get a pre-built chart and spread. Everything else falls back
# to a live query.
SPREAD_MARKETS = 120
TREND_POINTS = 400


def build():
    t0 = time.time()
    if not lake.available():
        raise SystemExit("no bundled parquet found — nothing to precompute")

    stats = lake._stats()
    crops = lake._crops3d(40)
    commodities = [
        {"commodity": c["commodity"], "n": c["n"]}
        for c in lake._commodities(None, 400)
    ]

    names = [c["commodity"] for c in commodities]

    trends = {}
    spreads = {}
    for name in names:
        trends[name] = lake._commodity_trend(name, None, TREND_POINTS)
        spreads[name] = lake._market_spread(name, None, SPREAD_MARKETS)

    states = lake._states()
    summary_all = lake._summary(None, 60)
    summary_by_state = {s: lake._summary(s, 60) for s in states}

    latest = lake._latest_date(None, None)
    latest_by_state = {s: lake._latest_date(s, None) for s in states}

    payload = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stats": stats,
        "states": states,
        "commodities": commodities,
        "crops3d": crops,
        "summary": {"*": summary_all, **summary_by_state},
        "latest": {"*": latest, **latest_by_state},
        "trends": trends,
        "spreads": spreads,
    }

    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, separators=(",", ":"), default=str)
    os.replace(tmp, OUT)

    size = os.path.getsize(OUT) / 1024
    print(f"[precompute] wrote {OUT} ({size:.0f} KB) in {time.time() - t0:.1f}s")
    print(f"[precompute] {stats['n']:,} rows, {len(names)} crops, "
          f"{len(states)} states, {len(trends)} trends, {len(spreads)} spreads")
    return payload


if __name__ == "__main__":
    build()
