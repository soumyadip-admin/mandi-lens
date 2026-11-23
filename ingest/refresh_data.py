#!/usr/bin/env python3
"""
Build the daily data file that ships in the repo.

Why this exists
---------------
The government API (`api.data.gov.in`) does not route from cloud hosts:
Render gets `[Errno 113] No route to host`. So the app can never fetch fresh
data at runtime — the data has to arrive *with the code*.

This script runs on GitHub Actions, which is not blocked the same way, and on
a developer machine. It refreshes `data/real/market_history.parquet`, which is
committed to the repo. Render then redeploys automatically and the site has
new numbers. No server ever needs to phone home.

Sources, in priority order:
  1. The published national history parquet (covers the last ~2 years).
  2. data.gov.in live API, if DATA_GOV_API_KEY is set and reachable.
  3. Whatever is already in the repo (no-op, leaves the file untouched).

Usage:
    python -m ingest.refresh_data              # normal run
    python -m ingest.refresh_data --check      # report freshness, change nothing
"""
import argparse
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HISTORY_URL = (
    "https://raw.githubusercontent.com/heybadrinath/MandiLens/"
    "main/data/published/all_market_history.parquet"
)
OUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "real", "market_history.parquet",
)
COLUMNS = ["state", "district", "market", "commodity",
           "date", "min_price", "max_price", "representative_price"]


def log(msg):
    print(f"[refresh_data] {msg}", flush=True)


def current_stats():
    """Rows and newest date of the file we're about to replace."""
    import pandas as pd
    if not os.path.exists(OUT_PATH):
        return 0, None
    try:
        df = pd.read_parquet(OUT_PATH, columns=["arrival_date"])
        return len(df), str(df["arrival_date"].max())
    except Exception as e:
        log(f"could not read existing file: {e}")
        return 0, None


def download(url, dest):
    import requests
    log(f"downloading {url[:70]}…")
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    with open(dest, "wb") as f:
        f.write(r.content)
    return len(r.content)


def build_from_parquet(src_path):
    """Trim the published parquet to exactly the columns the app needs."""
    import pandas as pd
    log("reading published history …")
    df = pd.read_parquet(src_path, columns=COLUMNS)
    before = len(df)

    df = df.rename(columns={
        "representative_price": "modal_price",
        "date": "arrival_date",
    })
    df = df.dropna(subset=["state", "market", "commodity", "arrival_date", "modal_price"])
    df["district"] = df["district"].fillna("")
    df["min_price"] = df["min_price"].fillna(df["modal_price"])
    df["max_price"] = df["max_price"].fillna(df["modal_price"])
    df = df[(df["modal_price"] > 0) & (df["modal_price"] < 1_000_000)]

    log(f"kept {len(df):,} of {before:,} rows")
    log(f"newest date: {df['arrival_date'].max()}")
    return df


def build_from_api():
    """Pull today's national feed straight from data.gov.in, if reachable."""
    from app import config
    from ingest.fetch import fetch_live
    import pandas as pd

    if not config.DATA_GOV_API_KEY:
        raise RuntimeError("DATA_GOV_API_KEY not set")

    log("trying the live API …")
    records, src = fetch_live()
    log(f"live API returned {len(records):,} records ({src})")

    rows = []
    for r in records:
        rows.append({
            "state": r.get("state"),
            "district": r.get("district") or "",
            "market": r.get("market"),
            "commodity": r.get("commodity"),
            "arrival_date": r.get("arrival_date"),
            "min_price": r.get("min_price"),
            "max_price": r.get("max_price"),
            "modal_price": r.get("modal_price"),
        })
    df = pd.DataFrame(rows).dropna(subset=["state", "market", "commodity",
                                          "arrival_date", "modal_price"])
    return df


def write(df):
    import pandas as pd
    df = df.drop_duplicates(
        subset=["state", "market", "commodity", "arrival_date"], keep="last"
    )
    tmp = OUT_PATH + ".tmp"
    df.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, OUT_PATH)
    size_mb = os.path.getsize(OUT_PATH) / 1e6
    log(f"wrote {OUT_PATH} — {len(df):,} rows, {size_mb:.1f} MB")
    return len(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only, change nothing")
    a = ap.parse_args()

    n, newest = current_stats()
    log(f"current file: {n:,} rows, newest {newest}")

    if a.check:
        if newest is None:
            log("no data file present")
            return 1
        age = (datetime.now(timezone.utc).date()
               - datetime.fromisoformat(newest).date()).days
        log(f"data is {age} day(s) old")
        return 0 if age <= 2 else 2

    df = None

    # 1) published national history
    try:
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "history.parquet")
            download(HISTORY_URL, dest)
            df = build_from_parquet(dest)
    except Exception as e:
        log(f"published history unavailable: {e.__class__.__name__}: {e}")

    # 2) live API, appended on top so today's rows win
    try:
        live = build_from_api()
        if df is None or df.empty:
            df = live
        else:
            df = __import__("pandas").concat([df, live], ignore_index=True)
        log(f"merged live API rows — total now {len(df):,}")
    except Exception as e:
        log(f"live API unavailable: {e.__class__.__name__}: {e}")

    if df is None or len(df) == 0:
        log("no source produced data — leaving the existing file untouched")
        return 0          # not a failure: keep serving what we have

    write(df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
