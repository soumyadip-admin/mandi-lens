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
import time
import traceback
from datetime import datetime

from app import config, db

_lock = threading.Lock()
_last_attempt = None

# Set while the bundled history is being loaded. /health reads this so a
# health check never has to run 1.5M-row aggregates mid-load.
LOADING = threading.Event()

AUTO_REFRESH = os.environ.get("MANDI_AUTO_REFRESH", "1") not in ("0", "false", "False")
# 0 means "no page limit" — a full national pull.
REFRESH_PAGES = int(os.environ.get("MANDI_REFRESH_PAGES", "0") or 0)
# Don't even consider refreshing more often than this.
MIN_GAP_MINUTES = int(os.environ.get("MANDI_MIN_GAP_MINUTES", "30") or 30)


def is_stale():
    """
    True when a refresh is worth attempting.

    Three separate questions, and any 'yes' means refresh:

      1. Has anything ever been imported?
      2. Is the newest *price date* older than the threshold? This is the one
         that matters — a database seeded from a single-day file can have a
         very recent import timestamp while holding year-old prices.
      3. Was the last import a fallback (file/fixture) rather than the live
         feed? If so, the live pull never succeeded and is worth retrying.
    """
    last = db.last_run()
    if not last or not last.get("ended_at"):
        return True

    # (3) last import wasn't live
    if (last.get("source") or "").split(":")[0] != "live":
        return True

    # (2) the data itself is old
    try:
        dmax = db.stats().get("dmax")
        if dmax:
            newest = datetime.strptime(dmax, "%Y-%m-%d")
            if (datetime.now() - newest).total_seconds() / 3600 > config.STALE_AFTER_HOURS:
                return True
    except (ValueError, TypeError):
        return True

    # (1) import timestamp
    try:
        ended = datetime.strptime(last["ended_at"], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return True
    return (datetime.now() - ended).total_seconds() / 3600 > config.STALE_AFTER_HOURS


def _too_soon():
    global _last_attempt
    if _last_attempt is None:
        return False
    return (datetime.now() - _last_attempt).total_seconds() < MIN_GAP_MINUTES * 60


HISTORY_FILE = os.path.join(config.BASE_DIR, "data", "real", "market_history.parquet")

# A thread lock only guards one worker. gunicorn runs several, and they all
# share one SQLite file, so the guard has to live in the database. This row is
# the cross-process mutex: whoever holds it does the load.
LOCK_ROW = "__bootstrap_lock__"


def _acquire_db_lock(minutes=25):
    """
    Take a cross-process lock. Returns True if this worker won the race.

    gunicorn runs several workers and they all share one SQLite file, so a
    thread lock is not enough. This uses a dedicated `locks` table with `name`
    as the primary key: INSERT OR IGNORE only ignores on a *real* constraint,
    which is what makes rowcount a reliable "did I win?" signal.
    """
    me = f"{os.getpid()}@{datetime.now().strftime('%H:%M:%S')}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with db.session() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO locks(name, holder, acquired_at) VALUES(?,?,?)",
                ("history_load", me, now))
            if cur.rowcount == 1:
                return True                       # we took the lock

            row = c.execute(
                "SELECT acquired_at FROM locks WHERE name='history_load'").fetchone()
            if not row:
                return True
            try:
                started = datetime.strptime(row["acquired_at"], "%Y-%m-%d %H:%M:%S")
                age_min = (datetime.now() - started).total_seconds() / 60
            except (ValueError, TypeError):
                age_min = 0
            if age_min > minutes:                 # stale lock, take it over
                c.execute("UPDATE locks SET holder=?, acquired_at=? WHERE name=?",
                          (me, now, "history_load"))
                return True
            return False
    except Exception:
        return True                               # never break a boot on the lock


def _release_db_lock():
    try:
        with db.session() as c:
            c.execute("DELETE FROM locks WHERE name='history_load'")
    except Exception:
        pass


def _load_bundled_history():
    """
    Load the full bundled history if the live API is unreachable.

    The government API does not route from cloud hosts ([Errno 113] No route to
    host), so on a hosted deployment the live pull will always fail. The same
    data is committed to the repo as a parquet file, which we can read locally.
    This runs in the background so a 2-minute load never blocks a page.
    """
    if not os.path.exists(HISTORY_FILE):
        return None
    if not _acquire_db_lock():
        print("[refresh] history load already running in another worker — standing by",
              flush=True)
        return None
    from ingest.loadfile import load
    print("[refresh] loading bundled full history (1.48M rows) in background …", flush=True)
    try:
        import resource
        _soft, _ = resource.getrlimit(resource.RLIMIT_AS)
        _lim = "unlimited" if _soft < 0 else f"{_soft/1e6:.0f} MB"
        print(f"[refresh] memory limit on this instance: {_lim}", flush=True)
    except Exception:
        pass
    LOADING.set()
    try:
        load(HISTORY_FILE, verbose=False)
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            print(f"[refresh] peak RSS during load: {peak:.0f} MB", flush=True)
        except Exception:
            pass
    finally:
        LOADING.clear()
        _release_db_lock()
    n = db.stats().get("n") or 0
    print(f"[refresh] bundled history loaded — {n:,} rows", flush=True)
    return n


def _worker():
    global _last_attempt
    LOADING.set()
    try:
        from ingest import fetch
        # Never fall back to the synthetic fixture during a refresh — mixing
        # demo rows into a real dataset corrupts the max date and the movers.
        mode = "live"
        live_ok = False
        try:
            status = fetch.run(mode, limit_pages=(REFRESH_PAGES or None), verbose=False)
            last = db.last_run() or {}
            live_ok = (last.get("source") or "").split(":")[0] == "live"
            print(f"[refresh] fetch finished status={status} source={last.get('source')} "
                  f"rows={db.stats().get('n'):,}", flush=True)
        except Exception as e:
            print(f"[refresh] fetch failed ({e.__class__.__name__}: {e})", flush=True)

        # If we couldn't get the live feed we do NOT import the bundled history
        # into SQLite any more. app/lake.py queries that parquet file directly,
        # so importing it was a 60-second job that produced nothing the site
        # could not already answer in 30 milliseconds. On a free-tier container
        # that job never even finished.
        if not live_ok:
            pass
    except Exception:
        print("[refresh] failed (site keeps serving existing data)", flush=True)
        traceback.print_exc()
    finally:
        _last_attempt = datetime.now()
        LOADING.clear()
        _lock.release()


_thin_cache = {"at": 0.0, "val": False}


def _thin():
    """
    True when we're running on the tiny single-day seed rather than history.

    db.stats() is four COUNT(DISTINCT ...) scans over 1.48M rows. maybe_refresh
    calls this on every request, so it is cached for 10 s; nothing about the
    answer changes meaningfully in that window.
    """
    now = time.time()
    if _thin_cache["at"] and now - _thin_cache["at"] < 10.0:
        return _thin_cache["val"]
    try:
        val = (db.stats().get("n") or 0) < 100000
    except Exception:
        val = False
    _thin_cache["at"] = now
    _thin_cache["val"] = val
    return val


def maybe_refresh():
    """
    Fire-and-forget. Returns True if a refresh was started, False otherwise.
    Safe to call on every request — it is cheap when there's nothing to do.
    """
    global _last_attempt
    if not AUTO_REFRESH:
        return False
    # A thin database is always worth topping up, even with no API key — the
    # bundled history is local, so there is nothing to be polite about.
    if _thin() and not _too_soon() and _lock.acquire(blocking=False):
        _last_attempt = datetime.now()
        threading.Thread(target=_worker, name="mandi-refresh", daemon=True).start()
        return True
    if not is_stale() or _too_soon():
        return False
    if not _lock.acquire(blocking=False):
        return False          # one is already running
    _last_attempt = datetime.now()
    threading.Thread(target=_worker, name="mandi-refresh", daemon=True).start()
    return True
