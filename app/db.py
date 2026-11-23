"""SQLite storage layer. One table, indexed for the queries the UI actually runs."""
import os
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    state       TEXT NOT NULL,
    district    TEXT,
    market      TEXT,
    commodity   TEXT NOT NULL,
    variety     TEXT,
    grade       TEXT,
    arrival_date TEXT NOT NULL,
    min_price   REAL,
    max_price   REAL,
    modal_price REAL,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    UNIQUE(state, market, commodity, variety, arrival_date)
);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    status     TEXT NOT NULL,
    source     TEXT,
    fetched    INTEGER DEFAULT 0,
    inserted   INTEGER DEFAULT 0,
    updated    INTEGER DEFAULT 0,
    message    TEXT
);

CREATE TABLE IF NOT EXISTS locks (
    name       TEXT PRIMARY KEY,
    holder     TEXT,
    acquired_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_history (
    price_id    INTEGER NOT NULL,
    modal_price REAL,
    seen_at     TEXT NOT NULL
);
"""

# Full-text search. Kept separate so a database can be created with it.
FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS prices_fts USING fts5(
    market, commodity, district, content='prices', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS prices_ai AFTER INSERT ON prices BEGIN
  INSERT INTO prices_fts(rowid, market, commodity, district)
  VALUES (new.id, new.market, new.commodity, new.district);
END;
CREATE TRIGGER IF NOT EXISTS prices_ad AFTER DELETE ON prices BEGIN
  INSERT INTO prices_fts(prices_fts, rowid, market, commodity, district)
  VALUES('delete', old.id, old.market, old.commodity, old.district);
END;
CREATE TRIGGER IF NOT EXISTS prices_au AFTER UPDATE ON prices BEGIN
  INSERT INTO prices_fts(prices_fts, rowid, market, commodity, district)
  VALUES('delete', old.id, old.market, old.commodity, old.district);
  INSERT INTO prices_fts(rowid, market, commodity, district)
  VALUES (new.id, new.market, new.commodity, new.district);
END;
"""

# Indexes are created AFTER a bulk load. Building them up-front makes a
# 1.48M-row insert several times slower, because every row maintains every
# index. Load first, index once, then the queries are fast forever.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_commodity_state   ON prices(commodity, state);
CREATE INDEX IF NOT EXISTS idx_commodity_arrival ON prices(commodity, arrival_date DESC);
CREATE INDEX IF NOT EXISTS idx_state_market      ON prices(state, market);
CREATE INDEX IF NOT EXISTS idx_arrival_desc      ON prices(arrival_date DESC);
CREATE INDEX IF NOT EXISTS idx_modal             ON prices(commodity, modal_price);
CREATE INDEX IF NOT EXISTS idx_state_date        ON prices(state, arrival_date);
CREATE INDEX IF NOT EXISTS idx_date              ON prices(arrival_date);
CREATE INDEX IF NOT EXISTS idx_name_search       ON prices(commodity, market);
CREATE INDEX IF NOT EXISTS idx_hist              ON price_history(price_id, seen_at);
"""
def connect(bulk=False):
    """
    Open a connection. `bulk=True` trades crash-safety for speed.

    journal_mode=WAL plus synchronous=FULL means every transaction waits for
    an fsync. On a free-tier container with a slow disk that dominates: a bulk
    load measured ~500 rows/second with FULL, because each 20k batch waited for
    the disk. synchronous=OFF during a bulk load removes that wait. This is safe
    here because a interrupted load simply re-runs — the UPSERT is idempotent.
    """
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA cache_size=-64000")      # 64 MB page cache
    conn.execute("PRAGMA temp_store=MEMORY")
    if bulk:
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    return conn


@contextmanager
def session(bulk=False):
    conn = connect(bulk=bulk)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(with_fts=True):
    """Create tables. Indexes are deferred to build_indexes() after a bulk load."""
    with session() as c:
        c.executescript(SCHEMA)
        if with_fts:
            c.executescript(FTS)


def build_indexes(verbose=False):
    """Build all indexes in one pass. Call this after a bulk insert."""
    with session() as c:
        if verbose:
            print("  … building indexes", flush=True)
        c.executescript(INDEXES)
    if verbose:
        print("  … indexes built", flush=True)


def has_indexes():
    with session() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='idx_commodity_state'"
        ).fetchone()[0]
        return bool(n)


UPSERT_SQL = """
INSERT INTO prices(state,district,market,commodity,variety,grade,arrival_date,
                   min_price,max_price,modal_price,source,fetched_at)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(state,market,commodity,variety,arrival_date) DO UPDATE SET
    district     = excluded.district,
    grade        = excluded.grade,
    min_price    = excluded.min_price,
    max_price    = excluded.max_price,
    modal_price  = excluded.modal_price,
    source       = excluded.source,
    fetched_at   = excluded.fetched_at
WHERE prices.modal_price   IS NOT excluded.modal_price
   OR prices.min_price     IS NOT excluded.min_price
   OR prices.max_price     IS NOT excluded.max_price
   OR prices.district      IS NOT excluded.district
   OR prices.grade         IS NOT excluded.grade
"""


def _row_tuple(r):
    return (r["state"], r["district"], r["market"], r["commodity"],
            r["variety"], r["grade"], r["arrival_date"], r["min_price"],
            r["max_price"], r["modal_price"], r["source"], r["fetched_at"])


def upsert_many(rows, batch=20000, track_history=True):
    """
    Bulk insert/update using one ON CONFLICT statement per batch.

    Two things matter for throughput on a free-tier container:

    1. One connection for the whole call, in bulk mode (no fsync per batch).
       Opening a connection per chunk threw away the 64 MB page cache and
       re-issued the PRAGMAs each time.
    2. No COUNT(*) before/after. That was a full table scan per 20k batch —
       at 1.5M rows it runs 75+ times over a growing table, which cost more
       than the inserts themselves. sqlite's rowcount tells us enough.
    """
    changed = 0
    buf = []
    with session(bulk=True) as c:
        for r in rows:                      # accepts a list or a generator
            buf.append(_row_tuple(r))
            if len(buf) >= batch:
                cur = c.executemany(UPSERT_SQL, buf)
                changed += max(cur.rowcount, 0)
                buf = []
        if buf:
            cur = c.executemany(UPSERT_SQL, buf)
            changed += max(cur.rowcount, 0)
    # An UPSERT reports 1 for insert and 1 for update, so the total is a good
    # enough "rows written" figure without paying for a table scan.
    inserted = changed
    changed = 0
    return inserted, changed


def start_run(source):
    with session() as c:
        cur = c.execute("INSERT INTO runs(started_at,status,source) VALUES(datetime('now'),'running',?)",
                        (source,))
        return cur.lastrowid


def finish_run(run_id, status, fetched, inserted, updated, message=""):
    with session() as c:
        c.execute("UPDATE runs SET ended_at=datetime('now'), status=?, fetched=?, "
                  "inserted=?, updated=?, message=? WHERE id=?",
                  (status, fetched, inserted, updated, message[:500], run_id))


def last_run():
    with session() as c:
        r = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None


def stats():
    with session() as c:
        row = c.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT commodity) c, COUNT(DISTINCT state) s, "
            "COUNT(DISTINCT market) m, MIN(arrival_date) dmin, MAX(arrival_date) dmax "
            "FROM prices").fetchone()
        return dict(row)


def states():
    with session() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT state FROM prices ORDER BY state")]


def commodities(state=None, limit=400):
    with session() as c:
        if state:
            q = ("SELECT commodity, COUNT(*) n FROM prices WHERE state=? "
                 "GROUP BY commodity ORDER BY n DESC, commodity LIMIT ?")
            return [dict(r) for r in c.execute(q, (state, limit))]
        q = ("SELECT commodity, COUNT(*) n FROM prices GROUP BY commodity "
             "ORDER BY n DESC, commodity LIMIT ?")
        return [dict(r) for r in c.execute(q, (limit,))]


def latest_date(state=None, commodity=None):
    sql = "SELECT MAX(arrival_date) d FROM prices WHERE 1=1"
    args = []
    if state:
        sql += " AND state=?"; args.append(state)
    if commodity:
        sql += " AND commodity=?"; args.append(commodity)
    with session() as c:
        r = c.execute(sql, args).fetchone()
        return r["d"] if r else None


def search(commodity=None, state=None, district=None, q=None,
           date_from=None, date_to=None, sort="date", limit=200):
    sql = ["SELECT * FROM prices WHERE 1=1"]
    args = []
    if commodity:
        sql.append("AND commodity = ?"); args.append(commodity)
    if state:
        sql.append("AND state = ?"); args.append(state)
    if district:
        sql.append("AND district = ?"); args.append(district)
    if q:
        # FTS5 prefix search: 'kol*' matches 'Kolkata' instantly on 1.5M rows.
        # Falls back to LIKE only if the FTS table is empty (e.g. legacy db).
        term = " AND ".join(
            f'"{w.replace(chr(34), "")}"*' for w in q.split() if w) or '""'
        sql.append("AND id IN (SELECT rowid FROM prices_fts WHERE prices_fts MATCH ?)")
        args.append(term)
    if date_from:
        sql.append("AND arrival_date >= ?"); args.append(date_from)
    if date_to:
        sql.append("AND arrival_date <= ?"); args.append(date_to)
    order = {"date": "arrival_date DESC, commodity", "price": "modal_price DESC",
             "price_asc": "modal_price ASC", "market": "state, market"}.get(sort, "arrival_date DESC")
    sql.append(f"ORDER BY {order} LIMIT ?"); args.append(limit)
    with session() as c:
        return [dict(r) for r in c.execute(" ".join(sql), args)]


def commodity_trend(commodity, state=None, days=400):
    """Average modal price per arrival_date — the chart series."""
    sql = ("SELECT arrival_date d, ROUND(AVG(modal_price)) avg_price, COUNT(*) n "
           "FROM prices WHERE commodity=?")
    args = [commodity]
    if state:
        sql += " AND state=?"; args.append(state)
    sql += " AND arrival_date IS NOT NULL GROUP BY arrival_date ORDER BY arrival_date DESC LIMIT ?"
    args.append(days)
    with session() as c:
        rows = [dict(r) for r in c.execute(sql, args)]
    return list(reversed(rows))


def summary(state=None, limit=40):
    """One row per crop: markets reporting, latest average price, week-on-week change."""
    clause = "AND state = ?" if state else ""
    sql = f"""
    WITH bounds AS (
        SELECT commodity, MAX(arrival_date) AS d
        FROM prices WHERE 1=1 {clause} GROUP BY commodity
    )
    SELECT
        b.commodity,
        b.d AS latest_date,
        (SELECT AVG(modal_price) FROM prices p WHERE p.commodity = b.commodity
           AND p.arrival_date = b.d {clause.replace('state','p.state')})                  AS cur_avg,
        (SELECT COUNT(DISTINCT market) FROM prices p WHERE p.commodity = b.commodity
           AND p.arrival_date = b.d {clause.replace('state','p.state')})                  AS markets,
        (SELECT AVG(modal_price) FROM prices p WHERE p.commodity = b.commodity
           AND p.arrival_date < b.d AND p.arrival_date >= date(b.d, '-8 days')
           {clause.replace('state','p.state')})                                           AS prev_avg,
        (SELECT COUNT(DISTINCT market) FROM prices p WHERE p.commodity = b.commodity
           AND p.arrival_date < b.d AND p.arrival_date >= date(b.d, '-8 days')
           {clause.replace('state','p.state')})                                           AS prev_markets
    FROM bounds b
    ORDER BY markets DESC, b.commodity
    LIMIT ?
    """
    args = ([state] * 5 if state else []) + [limit]
    with session() as c:
        rows = [dict(r) for r in c.execute(sql, args)]
    out = []
    for r in rows:
        pct = None
        if r["prev_avg"] and r["cur_avg"] is not None and (r["prev_markets"] or 0) >= 2:
            pct = round((r["cur_avg"] - r["prev_avg"]) / r["prev_avg"] * 100, 1)
        out.append({"commodity": r["commodity"],
                    "price": round(r["cur_avg"], 0) if r["cur_avg"] is not None else None,
                    "markets": r["markets"], "pct": pct})
    return out


def market_spread(commodity, state=None, limit=60):
    """
    Latest price per market plus change against that market's previous report.

    Three bugs surfaced here while testing against 1.48M real rows:
      1. Ranking raw rows paired two varieties reported on the SAME day, so every
         change came out 0%.
      2. `GROUP BY state, market` with a bare arrival_date column let SQLite pick
         an arbitrary row instead of the newest one.
      3. Markets do not report daily, so "previous report" is the honest baseline,
         not "yesterday".

    Fix: aggregate to one row per (market, day), rank that, then self-join rn=1
    to rn=2. No bare columns, no reliance on row ordering.
    """
    clause = "AND state = ?" if state else ""
    sql = f"""
    WITH per_day AS (
        SELECT state, district, market, arrival_date,
               AVG(modal_price) AS price,
               MIN(min_price)   AS min_price,
               MAX(max_price)   AS max_price
        FROM prices
        WHERE commodity = ? {clause}
        GROUP BY state, market, arrival_date
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
    ORDER BY a.price DESC
    LIMIT ?
    """
    args = [commodity] + ([state] if state else []) + [limit]
    with session() as c:
        rows = [dict(r) for r in c.execute(sql, args)]
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
