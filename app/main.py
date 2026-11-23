"""Mandi Lens — a clean front-end for India's open mandi price data."""
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request, abort

from . import config, db, refresh

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


# ------------------------------------------------------------------ helpers
@app.template_filter("rupees")
def rupees(v):
    if v is None:
        return "—"
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return "—"


@app.template_filter("delta")
def delta(v):
    if v is None:
        return None
    try:
        return f"{'+' if float(v) > 0 else ''}{int(round(float(v)))}"
    except (TypeError, ValueError):
        return None


@app.template_filter("nicedate")
def nicedate(d):
    if not d:
        return "—"
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%d %b %Y")
    except ValueError:
        return d


def freshness():
    """How old is our data, and should we warn the user?"""
    d = db.stats()
    last = db.last_run()
    info = {
        "latest_date": d.get("dmax"),
        "records": d.get("n") or 0,
        "commodities": d.get("c") or 0,
        "states": d.get("s") or 0,
        "markets": d.get("m") or 0,
        "run": last,
        "stale": False,
        "source": (last or {}).get("source", "unknown"),
        # Seed source tells visitors (and us) how this instance was populated:
        # 'live' = full national feed, 'file' = bundled Agmarknet export,
        # 'fixture' = offline demo sample.
    }
    if last and last.get("ended_at"):
        try:
            ended = datetime.strptime(last["ended_at"], "%Y-%m-%d %H:%M:%S")
            age_h = (datetime.now() - ended).total_seconds() / 3600
            info["age_hours"] = round(age_h, 1)
            info["stale"] = age_h > config.STALE_AFTER_HOURS
        except ValueError:
            pass
    src = info["source"]
    info["dataset_note"] = {
        "live":    "Live feed from data.gov.in.",
        "file":    "Seeded from a bundled snapshot of real government data.",
        "fixture": "Offline demo sample — synthetic data for trying the interface.",
    }.get(src, "Data source unknown.")
    info["is_live"] = (src == "live")
    return info


def sparkline(series, w=560, h=120):
    """Pure-SVG sparkline — no JS libraries, works offline."""
    pts = [p for p in series if p.get("avg_price") is not None]
    if len(pts) < 2:
        return None
    vals = [p["avg_price"] for p in pts]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    n = len(pts)
    coords = []
    for i, p in enumerate(pts):
        x = 8 + i * (w - 16) / (n - 1)
        y = h - 12 - ((p["avg_price"] - lo) / span) * (h - 30)
        coords.append((round(x, 1), round(y, 1)))
    line = " ".join(f"{x},{y}" for x, y in coords)
    area = f"8,{h-8} " + line + f" {coords[-1][0]},{h-8}"
    trend_up = vals[-1] >= vals[0]
    return {
        "line": line, "area": area, "w": w, "h": h,
        "first": vals[0], "last": vals[-1], "lo": lo, "hi": hi,
        "up": trend_up, "n": n,
        "color": "#3fbf7f" if trend_up else "#e0567a",
    }


# ------------------------------------------------------------------ routes
@app.route("/")
def index():
    info = freshness()
    state = request.args.get("state") or None
    q = (request.args.get("q") or "").strip() or None

    if not info["records"]:
        return render_template("empty.html", info=info)

    commodities = db.commodities(state)
    summary = db.summary(state, limit=60)
    latest = db.latest_date(state=state)

    movers = [m for m in summary if m["pct"] is not None]
    risers = sorted([m for m in movers if m["pct"] > 0], key=lambda m: -m["pct"])[:5]
    fallers = sorted([m for m in movers if m["pct"] < 0], key=lambda m: m["pct"])[:5]

    rows = []
    if q:
        rows = db.search(state=state, q=q, limit=60)

    crops3d = []
    try:
        with db.session() as c:
            crops3d = [dict(r) for r in c.execute(
                """
                SELECT commodity,
                       ROUND(AVG(modal_price), 0) AS avg_price,
                       COUNT(DISTINCT market)     AS markets,
                       COUNT(*)                   AS n
                FROM prices
                WHERE modal_price BETWEEN 1 AND 100000
                GROUP BY commodity
                HAVING n > 0
                ORDER BY n DESC
                LIMIT 10
                """).fetchall()]
    except Exception:
        crops3d = []

    return render_template("index.html", crops3d=crops3d, info=info, states=db.states(),
                           state=state or "", q=q or "", commodities=commodities,
                           rows=rows, risers=risers, fallers=fallers,
                           latest=latest, summary=summary)


@app.route("/commodity/<path:name>")
def commodity(name):
    info = freshness()
    state = request.args.get("state") or None
    series = db.commodity_trend(name, state, days=400)
    if not series:
        abort(404)
    spread = db.market_spread(name, state, limit=80)
    chart = sparkline(series)
    best = max(spread, key=lambda r: r["modal_price"] or 0) if spread else None
    cheapest = min(spread, key=lambda r: r["modal_price"] or 10**9) if spread else None
    first, last = series[0]["avg_price"], series[-1]["avg_price"]
    overall = round((last - first) / first * 100, 1) if first else None
    return render_template("commodity.html", info=info, name=name, state=state or "",
                           states=db.states(), series=series, chart=chart,
                           spread=spread, best=best, cheapest=cheapest,
                           overall=overall)


@app.route("/explore")
def explore():
    """3D explorer — the same figures, rendered as orbitable geometry."""
    base = """
        SELECT commodity,
               ROUND(AVG(modal_price), 0) AS avg_price,
               COUNT(DISTINCT market)     AS markets,
               COUNT(DISTINCT state)      AS states,
               COUNT(*)                   AS n
        FROM prices
        WHERE modal_price BETWEEN 1 AND 100000
        {group}
        ORDER BY markets DESC
        LIMIT 14
    """
    with db.session() as c:
        # Prefer crops with enough rows to be meaningful. On a small or freshly
        # seeded database nothing clears that bar, so fall back to the plain
        # aggregate rather than showing an empty page.
        rows = [dict(r) for r in c.execute(
            base.format(group="GROUP BY commodity HAVING n > 100")).fetchall()]
        if not rows:
            rows = [dict(r) for r in c.execute(
                base.format(group="GROUP BY commodity HAVING n > 0")).fetchall()]

    info = freshness()
    totals = {
        "markets": info.get("markets") or 0,
        "records": info.get("records") or 0,
    }
    if not rows:
        return render_template("empty.html", info=info), 200
    return render_template("explore3d.html", rows=rows, totals=totals, info=info)


@app.route("/about")
def about():
    return render_template("about.html", info=freshness())


# ------------------------------------------------------------------ API
@app.route("/api/prices")
def api_prices():
    return jsonify({
        "commodity": request.args.get("commodity"),
        "state": request.args.get("state"),
        "count": 0,
        "results": db.search(
            commodity=request.args.get("commodity"),
            state=request.args.get("state"),
            district=request.args.get("district"),
            q=request.args.get("q"),
            limit=min(int(request.args.get("limit", 100)), 500)),
    })


@app.route("/api/commodities")
def api_commodities():
    return jsonify(db.commodities(request.args.get("state")))


@app.route("/api/states")
def api_states():
    return jsonify(db.states())


@app.route("/api/trend/<path:name>")
def api_trend(name):
    return jsonify(db.commodity_trend(name, request.args.get("state"), days=400))


@app.route("/api/spread/<path:name>")
def api_spread(name):
    return jsonify(db.market_spread(name, request.args.get("state"), limit=100))


@app.before_request
def _kick_refresh():
    """Opportunistically refresh stale data in the background, never inline."""
    if request.endpoint in ("health", "index"):
        try:
            refresh.maybe_refresh()
        except Exception:
            pass


@app.route("/health")
def health():
    i = freshness()
    return jsonify({"ok": i["records"] > 0, "records": i["records"],
                    "latest_date": i["latest_date"], "stale": i["stale"],
                    "last_run": i["run"], "generated_at":
                    datetime.now(timezone.utc).isoformat(timespec="seconds")})


@app.errorhandler(404)
def nf(e):
    return render_template("404.html", info=freshness()), 404


# Create the schema at import so `gunicorn app.main:app` works with no extra step.
db.init()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
