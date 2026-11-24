"""
Query the bundled parquet file directly with DuckDB.

Why this exists
---------------
The original design copied the 1.48M-row history into SQLite at boot. That is
a 60-second CPU-bound job on a laptop and considerably worse on a free-tier
container: on Render the load never finished, and while it ran the process kept
getting restarted.

DuckDB reads the parquet file in place and answers the same questions in
milliseconds, with no import step, no index build and no second copy on disk.
The file is 4.5 MB; a full scan of it is 0.03 s.

SQLite is still used, but only for the small mutable bits — the run log and the
cross-process lock. Anything read-only that lives in the price history goes
through here.
"""
import json
import os
import threading
from datetime import date, datetime

import duckdb

from . import config

# gunicorn runs several threads per worker. A single DuckDB connection is not
# thread-safe, and serialising every query behind one lock turned 30 concurrent
# requests into a 1.2-second queue. A connection per thread removes the queue
# entirely: each has its own scan of the same 4.5 MB file, which is cheap.
_local = threading.local()

PARQUET = os.path.join(config.BASE_DIR, "data", "real", "market_history.parquet")

# Aliases so the SQL below reads exactly like the old SQLite queries.
_SRC = "(SELECT state, district, market, commodity, " \
       "CAST(arrival_date AS VARCHAR) AS arrival_date, " \
       "min_price, max_price, modal_price FROM read_parquet(?)) AS prices"


PRECOMPUTED = os.path.join(config.BASE_DIR, "data", "real", "precomputed.json")

_pre = {}


def available():
    return os.path.exists(PARQUET)


def preloaded():
    """True when the precomputed JSON is present, i.e. pages need no queries."""
    return bool(_pre) or os.path.exists(PRECOMPUTED)


def _load_pre():
    if not _pre:
        try:
            with open(PRECOMPUTED) as f:
                _pre.update(json.load(f))
        except Exception:
            pass
    return _pre or None


def _conn():
    con = getattr(_local, "con", None)
    if con is None:
        con = duckdb.connect()
        # One thread per query is enough: the file is small and the point is to
        # stop queries competing for the same worker's CPU.
        con.execute("PRAGMA threads=1")
        _local.con = con
    return con


def _q(sql, args=()):
    cur = _conn().execute(sql, list(args))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------- cache
# The parquet file is replaced wholesale on deploy, so every answer below is
# stable for the life of the process. Each home page render was costing 238 ms
# of DuckDB work (stats 55, summary 83, crops 54, latest 24, rest 22) and that
# was the whole reason concurrency topped out around 14 requests a second.
# Memoising turns every one of them into a dict lookup.
_CACHE = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL = 900.0          # 15 minutes, or until bump_cache()


def bump_cache():
    """Drop every memoised result. Call after the data changes."""
    with _CACHE_LOCK:
        _CACHE.clear()
    _pre.clear()


def _cached(key, fn):
    import time as _t
    now = _t.time()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < CACHE_TTL:
        return hit[1]
    val = fn()
    with _CACHE_LOCK:
        _CACHE[key] = (now, val)
    return val


def status():
    """
    Cheap readiness check. Uses parquet metadata, not a scan.
    """
    if not available():
        return {"ready": False, "rows": 0, "latest_date": None}
    try:
        import pyarrow.parquet as pq
        n = pq.ParquetFile(PARQUET).metadata.num_rows
    except Exception:
        n = 0
    return {"ready": n > 0, "rows": n, "latest_date": None}


def stats():
    p = _load_pre()
    if p:
        return p["stats"]
    return _cached("stats", lambda: _stats())


def _stats():
    return _q(
        "SELECT COUNT(*) n, COUNT(DISTINCT commodity) c, COUNT(DISTINCT state) s, "
        "COUNT(DISTINCT market) m, MIN(arrival_date) dmin, MAX(arrival_date) dmax "
        "FROM " + _SRC, [PARQUET])[0]


def states():
    p = _load_pre()
    if p:
        return p["states"]
    return _cached("states", lambda: _states())


def _states():
    return [r["state"] for r in _q(
        "SELECT DISTINCT state FROM " + _SRC + " ORDER BY state", [PARQUET])]


def commodities(state=None, limit=400):
    p = _load_pre()
    if p and not state:
        return p["commodities"][:limit]
    return _cached(("commodities", state, limit), lambda: _commodities(state, limit))


def _commodities(state, limit):
    if state:
        return _q(
            "SELECT commodity, COUNT(*) n FROM " + _SRC +
            " WHERE state=? GROUP BY commodity ORDER BY n DESC, commodity LIMIT ?",
            [PARQUET, state, limit])
    return _q(
        "SELECT commodity, COUNT(*) n FROM " + _SRC +
        " GROUP BY commodity ORDER BY n DESC, commodity LIMIT ?",
        [PARQUET, limit])


def latest_date(state=None, commodity=None):
    p = _load_pre()
    if p and not commodity:
        return p["latest"].get(state or "*")
    return _cached(("latest", state, commodity),
                   lambda: _latest_date(state, commodity))


def _latest_date(state, commodity):
    sql = "SELECT MAX(arrival_date) d FROM " + _SRC + " WHERE 1=1"
    args = [PARQUET]
    if state:
        sql += " AND state=?"; args.append(state)
    if commodity:
        sql += " AND commodity=?"; args.append(commodity)
    r = _q(sql, args)
    return r[0]["d"] if r else None


def search(commodity=None, state=None, district=None, q=None,
           date_from=None, date_to=None, sort="date", limit=200):
    return _cached(("search", commodity, state, district, q, date_from,
                    date_to, sort, limit),
                   lambda: _search(commodity, state, district, q,
                                   date_from, date_to, sort, limit))


def _search(commodity, state, district, q, date_from, date_to, sort, limit):
    sql = ["SELECT state, district, market, commodity, arrival_date, " \
           "min_price, max_price, modal_price FROM " + _SRC + " WHERE 1=1"]
    args = [PARQUET]
    if commodity:
        sql.append("AND commodity = ?"); args.append(commodity)
    if state:
        sql.append("AND state = ?"); args.append(state)
    if district:
        sql.append("AND district = ?"); args.append(district)
    if q:
        # Case-insensitive substring across the text columns. DuckDB runs this
        # as a vectorised filter over a 4.5 MB file, which costs milliseconds.
        like = f"%{q.lower()}%"
        sql.append("AND (LOWER(commodity) LIKE ? OR LOWER(market) LIKE ? "
                   "OR LOWER(district) LIKE ? OR LOWER(state) LIKE ?)")
        args += [like] * 4
    if date_from:
        sql.append("AND arrival_date >= ?"); args.append(date_from)
    if date_to:
        sql.append("AND arrival_date <= ?"); args.append(date_to)
    order = {"date": "arrival_date DESC, commodity",
             "price": "modal_price DESC",
             "price_asc": "modal_price ASC",
             "crop": "commodity, arrival_date DESC"}.get(sort, "arrival_date DESC")
    sql.append(f"ORDER BY {order} LIMIT ?")
    args.append(limit)
    return _q(" ".join(sql), args)


def commodity_trend(commodity, state=None, days=400):
    p = _load_pre()
    if p and not state:
        rows = p["trends"].get(commodity)
        if rows is not None:
            return rows[-days:]
    return _cached(("trend", commodity, state, days),
                   lambda: _commodity_trend(commodity, state, days))


def _commodity_trend(commodity, state, days):
    sql = ("SELECT arrival_date d, ROUND(AVG(modal_price)) avg_price, COUNT(*) n "
           "FROM " + _SRC + " WHERE commodity=?")
    args = [PARQUET, commodity]
    if state:
        sql += " AND state=?"; args.append(state)
    sql += " AND arrival_date IS NOT NULL GROUP BY arrival_date " \
           "ORDER BY arrival_date DESC LIMIT ?"
    args.append(days)
    rows = _q(sql, args)
    return list(reversed(rows))


def crops3d(limit=10):
    p = _load_pre()
    if p:
        return p["crops3d"][:limit]
    return _cached(("crops3d", limit), lambda: _crops3d(limit))


def _crops3d(limit):
    return _q(
        "SELECT commodity, ROUND(AVG(modal_price), 0) AS avg_price, "
        "COUNT(DISTINCT market) AS markets, COUNT(*) AS n FROM " + _SRC +
        " WHERE modal_price BETWEEN 1 AND 100000 GROUP BY commodity "
        "ORDER BY n DESC LIMIT ?", [PARQUET, limit])


def summary(state=None, limit=40):
    """One row per crop: markets reporting, latest average price, week-on-week."""
    p = _load_pre()
    if p:
        rows = p["summary"].get(state or "*")
        if rows is not None:
            return rows[:limit]
    return _cached(("summary", state, limit), lambda: _summary(state, limit))


def _summary(state, limit):
    clause = "AND state = ?" if state else ""
    sql = f"""
    WITH src AS (SELECT * FROM {_SRC} WHERE 1=1 {clause}),
    bounds AS (
        SELECT commodity, MAX(arrival_date) AS d FROM src GROUP BY commodity
    ),
    cur AS (
        SELECT commodity, MAX(arrival_date) AS d,
               AVG(modal_price) AS cur_avg,
               COUNT(DISTINCT market) AS markets
        FROM src JOIN bounds USING (commodity)
        WHERE src.arrival_date = bounds.d
        GROUP BY commodity
    ),
    prev AS (
        SELECT c.commodity,
               AVG(c.modal_price) AS prev_avg,
               COUNT(DISTINCT c.market) AS prev_markets
        FROM src c JOIN bounds b ON c.commodity = b.commodity
        WHERE c.arrival_date < b.d
          AND CAST(c.arrival_date AS DATE) >= CAST(b.d AS DATE) - INTERVAL 8 DAY
        GROUP BY c.commodity
    )
    SELECT b.commodity, b.d AS latest_date,
           cur.cur_avg, cur.markets,
           prev.prev_avg, prev.prev_markets
    FROM bounds b
    LEFT JOIN cur  ON cur.commodity  = b.commodity
    LEFT JOIN prev ON prev.commodity = b.commodity
    ORDER BY markets DESC NULLS LAST, b.commodity
    LIMIT ?
    """
    args = [PARQUET] + ([state] if state else []) + [limit]
    rows = _q(sql, args)
    out = []
    for r in rows:
        pct = None
        pa, ca = r.get("prev_avg"), r.get("cur_avg")
        if pa and ca is not None and (r.get("prev_markets") or 0) >= 2:
            pct = round((ca - pa) / pa * 100, 1)
        out.append({"commodity": r["commodity"],
                    "price": round(ca, 0) if ca is not None else None,
                    "markets": r.get("markets") or 0, "pct": pct})
    return out


def _summary_fallback(state=None, limit=40):
    """Simpler shape if the window join is unavailable. Same contract."""
    clause = "AND state = ?" if state else ""
    sql = f"""
    SELECT commodity, MAX(arrival_date) latest_date,
           AVG(modal_price) cur_avg, COUNT(DISTINCT market) markets
    FROM {_SRC} WHERE 1=1 {clause}
    GROUP BY commodity ORDER BY markets DESC, commodity LIMIT ?
    """
    args = [PARQUET] + ([state] if state else []) + [limit]
    for r in (rows := _q(sql, args)):
        r["prev_avg"] = None
        r["prev_markets"] = 0
    return rows


def market_spread(commodity, state=None, limit=60):
    """Latest price per market plus change against that market's previous report."""
    p = _load_pre()
    if p and not state:
        rows = p["spreads"].get(commodity)
        if rows is not None:
            return rows[:limit]
    return _cached(("spread", commodity, state, limit),
                   lambda: _market_spread(commodity, state, limit))


def _market_spread(commodity, state, limit):
    clause = "AND state = ?" if state else ""
    sql = f"""
    WITH per_day AS (
        SELECT state, district, market, arrival_date,
               AVG(modal_price) AS price,
               MIN(min_price)   AS min_price,
               MAX(max_price)   AS max_price
        FROM {_SRC}
        WHERE commodity = ? {clause}
        GROUP BY state, district, market, arrival_date
    ),
    ranked AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY state, market
                                     ORDER BY arrival_date DESC) AS rn
        FROM per_day
    )
    SELECT a.state, a.district, a.market, a.arrival_date,
           ROUND(a.price) AS modal_price, a.min_price, a.max_price,
           b.price AS prev_price, b.arrival_date AS prev_date
    FROM ranked a
    LEFT JOIN ranked b
           ON a.state = b.state AND a.market = b.market AND b.rn = 2
    WHERE a.rn = 1
    ORDER BY a.price DESC NULLS LAST
    LIMIT ?
    """
    args = [PARQUET, commodity] + ([state] if state else []) + [limit]
    rows = _q(sql, args)
    for r in rows:
        pv, pd = r.pop("prev_price"), r.pop("prev_date")
        if pv is not None and r["modal_price"] is not None and pv:
            r["change"] = round(r["modal_price"] - pv)
            r["pct"] = round((r["modal_price"] - pv) / pv * 100, 1)
        else:
            r["change"] = None
            r["pct"] = None
        r["prev_date"] = pd
    return rows
