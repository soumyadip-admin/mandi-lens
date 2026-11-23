#!/usr/bin/env python3
"""
Mandi Lens ingest engine.

Pulls the daily commodity-price dataset from data.gov.in and loads it into SQLite.

Handles the documented quirks of the OGD platform:
  * field names vary between dataset mirrors  -> alias mapping
  * dates arrive as several formats           -> normaliser
  * prices may have commas / blanks / "NR"    -> defensive parsing
  * pagination is 1-based and can over-run    -> empty-batch guard
  * some resources return CSV despite format  -> CSV fallback parser

Usage:
    python -m ingest.fetch              # live fetch (needs DATA_GOV_API_KEY)
    python -m ingest.fetch --fixture    # load the bundled sample (offline demo)
    python -m ingest.fetch --auto       # live, fall back to fixture if it fails
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config, db  # noqa: E402

# ---------------------------------------------------------------- normalising
FIELD_ALIASES = {
    "state":     ["state", "state_name", "statename", "state name"],
    "district":  ["district", "district_name", "districtname", "district name"],
    "market":    ["market", "market_name", "marketname", "mandi", "market name"],
    "commodity": ["commodity", "commodity_name", "commodityname", "crop", "commodity name"],
    "variety":   ["variety", "variety_name"],
    "grade":     ["grade", "quality"],
    "arrival_date": ["arrival_date", "date", "arrivaldate", "reported_date",
                     "price_date", "asondate", "as_on_date"],
    "min_price": ["min_price", "minimum_price", "min price", "minprice",
                  "min_x0020_price", "minmodalprice"],
    "max_price": ["max_price", "maximum_price", "max price", "maxprice", "max_x0020_price"],
    "modal_price": ["modal_price", "modal price", "modalprice", "model_price",
                    "modal_x0020_price", "price"],
}

DATE_FORMATS = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%Y/%m/%d",
                "%d-%b-%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"]


def _norm_key(k):
    return re.sub(r"[^a-z]", "", str(k).lower())


def build_keymap(record):
    """Map this record's actual keys onto our canonical field names."""
    lookup = {_norm_key(k): k for k in record.keys()}
    out = {}
    for canonical, aliases in FIELD_ALIASES.items():
        for a in aliases:
            k = _norm_key(a)
            if k in lookup:
                out[canonical] = lookup[k]
                break
    return out


def parse_date(raw):
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.split("T")[0].split(" ")[0] if re.match(r"\d{4}-\d{2}-\d{2}[T ]", s) else s
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    # last resort: pull a d/m/Y-ish triple out of messy text
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        d, mo, y = m.groups()
        try:
            return datetime(int(y), int(mo), int(d)).date().isoformat()
        except ValueError:
            return None
    return None


def parse_price(raw):
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace("\u20b9", "").strip()
    if s in ("", "-", "NA", "N/A", "NR", "None", "null"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v >= 0 else None


def clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.lower() not in ("na", "n/a", "null", "none", "-") else None


def normalise(records, source):
    """Turn raw API records into clean rows. Returns (rows, rejects)."""
    rows, rejects = [], 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for rec in records:
        km = build_keymap(rec)
        if "state" not in km or "commodity" not in km:
            rejects += 1
            continue
        row = {
            "state": clean(rec.get(km["state"])),
            "district": clean(rec.get(km["district"])) if "district" in km else None,
            "market": clean(rec.get(km["market"])) if "market" in km else "Unknown",
            "commodity": clean(rec.get(km["commodity"])),
            "variety": clean(rec.get(km["variety"])) if "variety" in km else "",
            "grade": clean(rec.get(km["grade"])) if "grade" in km else None,
            "arrival_date": parse_date(rec.get(km["arrival_date"])) if "arrival_date" in km else None,
            "min_price": parse_price(rec.get(km["min_price"])) if "min_price" in km else None,
            "max_price": parse_price(rec.get(km["max_price"])) if "max_price" in km else None,
            "modal_price": parse_price(rec.get(km["modal_price"])) if "modal_price" in km else None,
            "source": source,
            "fetched_at": now,
        }
        # Skip rows we cannot use at all
        if not row["state"] or not row["commodity"]:
            rejects += 1
            continue
        if row["arrival_date"] is None:
            rejects += 1
            continue
        if row["variety"] is None:
            row["variety"] = ""
        if row["market"] is None:
            row["market"] = "Unknown"
        # If only min/max exist, derive a modal price rather than dropping the row
        if row["modal_price"] is None:
            if row["min_price"] is not None and row["max_price"] is not None:
                row["modal_price"] = round((row["min_price"] + row["max_price"]) / 2, 2)
            else:
                rejects += 1
                continue
        rows.append(row)
    return rows, rejects


# ---------------------------------------------------------------- transport
def _get(params_url, params):
    r = requests.get(params_url, params=params, timeout=config.REQUEST_TIMEOUT,
                     headers={"User-Agent": "MandiLens/1.0 (+open data client)"})
    r.raise_for_status()
    return r


def parse_payload(text, content_type=""):
    """The endpoint sometimes returns JSON, sometimes CSV. Accept both."""
    body = text.lstrip()
    if body.startswith("{") or body.startswith("["):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return [], 0
        if isinstance(data, dict):
            return data.get("records", []) or [], int(data.get("total", 0) or 0)
        return data, len(data)
    # CSV fallback
    if "csv" in content_type.lower() or "," in body[:200]:
        reader = csv.DictReader(io.StringIO(text))
        return list(reader), len(list(csv.DictReader(io.StringIO(text))))
    return [], 0


def fetch_live(limit_pages=None):
    """Page through the dataset. Returns (records, source_label)."""
    if not config.DATA_GOV_API_KEY:
        raise RuntimeError(
            "DATA_GOV_API_KEY is not set. Get a free key at data.gov.in and export it, "
            "or run with --fixture for the offline demo.")

    last_err = None
    for rid in config.RESOURCE_IDS:
        url = f"{config.BASE_URL}/{rid}"
        records, offset, total, page = [], 0, None, 0
        try:
            while True:
                params = {"api-key": config.DATA_GOV_API_KEY, "format": "json",
                          "offset": offset, "limit": config.PAGE_SIZE}
                r = _get(url, params)
                batch, t = parse_payload(r.text, r.headers.get("Content-Type", ""))
                if total is None and t:
                    total = t
                if not batch:
                    break
                records.extend(batch)
                page += 1
                offset += config.PAGE_SIZE
                if limit_pages and page >= limit_pages:
                    break
                if total and offset >= total:
                    break
                if page >= config.MAX_PAGES:
                    break
                time.sleep(config.POLITE_DELAY)
            if records:
                return records, f"live:{rid}"
        except Exception as e:  # try the next known resource id
            last_err = e
            continue
    raise RuntimeError(f"All resource ids failed. Last error: {last_err}")


def fetch_fixture():
    path = os.path.join(config.FIXTURE_DIR, "sample.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No fixture at {path}")
    with open(path) as f:
        data = json.load(f)
    return data, "fixture"


def load(records, source):
    rows, rejects = normalise(records, source.split(":")[0])
    ins, upd = db.upsert_many(rows)
    return len(rows), ins, upd, rejects


def run(mode="live", limit_pages=None, verbose=True):
    db.init()
    source_pref = mode
    run_id = db.start_run(source_pref)
    message = ""
    try:
        if mode == "fixture":
            raw, src = fetch_fixture()
        elif mode == "auto":
            try:
                raw, src = fetch_live(limit_pages)
            except Exception as e:
                message = f"live fetch failed ({type(e).__name__}); used fixture"
                raw, src = fetch_fixture()
        else:
            raw, src = fetch_live(limit_pages)

        total, ins, upd, rej = load(raw, src)
        status = "ok" if total else "failed"
        if rej and total and rej > total:
            status = "partial"
        db.finish_run(run_id, status, total, ins, upd, message)
        if verbose:
            print(f"[mandi-lens] source={src} records={total} "
                  f"inserted={ins} updated={upd} rejected={rej} status={status}")
            if message:
                print(f"[mandi-lens] {message}")
        return status
    except Exception as e:
        db.finish_run(run_id, "failed", 0, 0, 0, str(e))
        if verbose:
            print(f"[mandi-lens] FAILED: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fetch mandi prices into SQLite")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--fixture", action="store_true", help="load bundled sample data")
    g.add_argument("--auto", action="store_true", help="live, fall back to fixture")
    ap.add_argument("--pages", type=int, default=None, help="cap pages (testing)")
    a = ap.parse_args()
    mode = "fixture" if a.fixture else ("auto" if a.auto else "live")
    run(mode, a.pages)
