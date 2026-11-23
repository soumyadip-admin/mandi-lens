#!/usr/bin/env python3
"""
Long-running scheduler for always-on hosts (a VPS, a Raspberry Pi, Docker).

    python -m ingest.scheduler            # loop forever, refresh every N hours
    python -m ingest.scheduler --once     # single refresh, then exit (cron mode)

Refresh interval defaults to 6 hours — the source updates once a day, so polling
more often is rude to a free public API and gains you nothing.
"""
import argparse
import os
import sys
import time
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config, db          # noqa: E402
from ingest import fetch            # noqa: E402

INTERVAL_HOURS = float(os.environ.get("MANDI_INTERVAL_HOURS", 6))


def should_run():
    last = db.last_run()
    if not last or not last.get("ended_at"):
        return True
    try:
        ended = datetime.strptime(last["ended_at"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    age_h = (datetime.now() - ended).total_seconds() / 3600
    return age_h >= INTERVAL_HOURS


def once():
    mode = "live" if config.DATA_GOV_API_KEY else "auto"
    try:
        return fetch.run(mode)
    except Exception:
        traceback.print_exc()
        return "failed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run a single refresh and exit")
    a = ap.parse_args()
    db.init()

    if a.once:
        once()
        return

    print(f"[mandi-lens] scheduler started; refreshing every {INTERVAL_HOURS}h")
    while True:
        try:
            if should_run():
                print(f"[mandi-lens] {datetime.now():%Y-%m-%d %H:%M} refreshing…")
                once()
            else:
                print(f"[mandi-lens] data is fresh; sleeping")
        except Exception:
            traceback.print_exc()
        time.sleep(300)   # check every 5 minutes, act only when due


if __name__ == "__main__":
    main()
