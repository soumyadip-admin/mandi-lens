"""
Lazy background refresh — the only design that works on a free host.

Why not a separate cron service? On Render's free tier every service gets its
own ephemeral disk. A cron container that downloads 745,000 rows writes them to
a database the web service will never see. The job succeeds, the site never
changes. So the refresh has to happen *inside* the web process, against the
database that process is actually reading.

How it stays safe:
  * only one refresh runs at a time (module-level lock)
  * it runs in a daemon thread, so requests are never blocked
  * it is skipped entirely if the data is fresh enough
  * it is bounded by MANDI_REFRESH_PAGES unless MANDI_REFRESH_PAGES=0 (unbounded)
  * every failure is swallowed and logged — a refresh must never break a page

Set MANDI_AUTO_REFRESH=0 to disable it completely.
"""
import os
import threading
import traceback
from datetime import datetime

from app import config, db

_lock = threading.Lock()
_last_attempt = None

AUTO_REFRESH = os.environ.get("MANDI_AUTO_REFRESH", "1") not in ("0", "false", "False")
# 0 means "no page limit" — a full national pull.
REFRESH_PAGES = int(os.environ.get("MANDI_REFRESH_PAGES", "0") or 0)
# Don't even consider refreshing more often than this.
MIN_GAP_MINUTES = int(os.environ.get("MANDI_MIN_GAP_MINUTES", "30") or 30)


def is_stale():
    """True when the newest data is older than the configured threshold."""
    last = db.last_run()
    if not last or not last.get("ended_at"):
        return True
    try:
        ended = datetime.strptime(last["ended_at"], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return True
    age_h = (datetime.now() - ended).total_seconds() / 3600
    return age_h > config.STALE_AFTER_HOURS


def _too_soon():
    global _last_attempt
    if _last_attempt is None:
        return False
    return (datetime.now() - _last_attempt).total_seconds() < MIN_GAP_MINUTES * 60


def _worker():
    global _last_attempt
    try:
        from ingest import fetch
        mode = "live" if config.DATA_GOV_API_KEY else "auto"
        status = fetch.run(mode, limit_pages=(REFRESH_PAGES or None), verbose=False)
        print(f"[refresh] finished status={status} rows={db.stats().get('n')}", flush=True)
    except Exception:
        print("[refresh] failed (site keeps serving existing data)", flush=True)
        traceback.print_exc()
    finally:
        _last_attempt = datetime.now()
        _lock.release()


def maybe_refresh():
    """
    Fire-and-forget. Returns True if a refresh was started, False otherwise.
    Safe to call on every request — it is cheap when there's nothing to do.
    """
    global _last_attempt
    if not AUTO_REFRESH or not config.DATA_GOV_API_KEY:
        return False
    if not is_stale() or _too_soon():
        return False
    if not _lock.acquire(blocking=False):
        return False          # one is already running
    _last_attempt = datetime.now()
    threading.Thread(target=_worker, name="mandi-refresh", daemon=True).start()
    return True
