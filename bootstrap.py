#!/usr/bin/env python3
"""
Seed the database on first boot — for free hosts with no persistent disk.

Order of preference:
  1. If the DB already has rows, do nothing.
  2. Fetch live from data.gov.in if DATA_GOV_API_KEY is set.
  3. Load the bundled Agmarknet XLSX export (real data, one day, 18 states).
  4. Load the bundled JSON fixture (works offline, 907 rows).

Run before gunicorn:
    python bootstrap.py && gunicorn app.main:app
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import config, db          # noqa: E402


def rows():
    db.init()
    return db.stats().get("n") or 0


def main():
    db.init()
    n = rows()
    if n > 0:
        print(f"[bootstrap] database already has {n:,} rows — skipping seed")
        return 0

    # 1) live — but NEVER block a boot on a full national pull.
    #
    # Fresh national data is ~745k rows and takes 4-8 minutes with polite
    # paging. Render free tier kills a deploy that doesn't open $PORT in time.
    # So a boot does at most a couple of pages: the newest records are enough
    # to make the site truthful, and the cron job does the full pull later.
    if config.DATA_GOV_API_KEY:
        try:
            from ingest import fetch
            quick = int(os.environ.get("BOOT_FETCH_PAGES", "2") or 0)
            if quick > 0:
                print(f"[bootstrap] quick live fetch ({quick} pages) …")
                try:
                    status = fetch.run("live", limit_pages=quick, verbose=True)
                    print(f"[bootstrap] quick live fetch status={status} rows={rows():,}")
                except Exception as e:
                    print(f"[bootstrap] quick live fetch failed: {e}")
        except Exception as e:
            print(f"[bootstrap] live fetch import failed: {e}")

    # 2) bundled real Agmarknet export (also tops up a thin live boot)
    xlsx = os.path.join(config.BASE_DIR, "data", "real", "agmarknet_export.xlsx")
    if os.path.exists(xlsx):
        try:
            from ingest.loadfile import load
            print("[bootstrap] loading bundled Agmarknet export …")
            load(xlsx, verbose=True)
            if rows() > 0:
                print(f"[bootstrap] seeded {rows():,} rows from real export")
                return 0
        except Exception as e:
            print(f"[bootstrap] xlsx seed failed: {e}")

    # 3) offline fixture
    try:
        from ingest import fetch
        print("[bootstrap] loading offline fixture …")
        fetch.run("fixture", verbose=True)
    except Exception as e:
        print(f"[bootstrap] fixture seed failed: {e}")
        return 1

    n = rows()
    print(f"[bootstrap] done — {n:,} rows")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
