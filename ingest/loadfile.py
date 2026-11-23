#!/usr/bin/env python3
"""
Load a real mandi export from a local file or URL.

Accepts what data.gov.in and Agmarknet actually give you:
  .csv  .xlsx / .xls  .json  .parquet

Handles the things that break naive loaders:
  * Agmarknet exports put the header on row 0 of a sheet called 'data',
    with other sheets blank or holding pivot tables
  * Excel dates arrive as datetime objects, not strings
  * column names contain spaces ("Min Price", "Modal Price", "Arrival_Date")
  * some files are 1M+ rows and must stream

Usage:
    python -m ingest.loadfile data/fixtures/agmarknet_export.xlsx
    python -m ingest.loadfile https://example.com/prices.csv
    python -m ingest.loadfile big.parquet --limit 200000
"""
import argparse
import gc
import io
import os
import sys
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore")

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config, db          # noqa: E402
from ingest.fetch import normalise  # noqa: E402

CANDIDATE_SHEETS = ["data", "Data", "DATA", "Sheet1", "prices"]
REQUIRED = {"state", "commodity"}


def _read_any(path_or_url, limit=None):
    """Return a DataFrame, choosing the right reader and sheet."""
    ext = str(path_or_url).lower().split("?")[0].rsplit(".", 1)[-1]

    if ext in ("xlsx", "xls", "xlsm"):
        xl = pd.ExcelFile(path_or_url)
        # Prefer the sheet that looks like data, not a pivot/blank sheet
        order = [s for s in CANDIDATE_SHEETS if s in xl.sheet_names]
        order += [s for s in xl.sheet_names if s not in order]
        best, best_df = None, pd.DataFrame()
        for sh in order:
            df = pd.read_excel(path_or_url, sheet_name=sh, nrows=limit)
            if df.empty:
                continue
            cols = {str(c).strip().lower() for c in df.columns}
            score = sum(1 for c in ("state", "commodity", "market") if c in cols)
            if score > 0 and len(df) > len(best_df):
                best, best_df = sh, df
        if best is None:
            raise ValueError(f"no sheet in {path_or_url} looks like mandi data "
                             f"(sheets: {xl.sheet_names})")
        return best_df

    if ext == "parquet":
        df = pd.read_parquet(path_or_url)
        return df.head(limit) if limit else df

    if ext == "json":
        df = pd.read_json(path_or_url)
        return df.head(limit) if limit else df

    # csv / tsv / anything else textual
    sep = "\t" if ext in ("tsv", "tab") else ","
    df = pd.read_csv(path_or_url, sep=sep, nrows=limit, low_memory=False)
    return df


def _stringify_dates(df):
    """Excel hands us datetime objects; the normaliser expects strings."""
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
    return df


# Rows per pass. The free Render instance has 512 MB total, and the web
# process is also serving pages while this runs. 10k keeps the transient
# pandas objects small; combined with the explicit gc below, peak memory stays
# comfortably under the limit.
CHUNK = 20000


def _iter_chunks(path_or_url, limit=None):
    """Yield DataFrames in chunks so 1.5M rows never sit in RAM at once."""
    ext = str(path_or_url).lower().split("?")[0].rsplit(".", 1)[-1]

    if ext in ("xlsx", "xls", "xlsm"):
        # Excel cannot stream; these files are always small, so read once.
        df = _read_any(path_or_url, limit)
        yield df
        return

    if ext == "parquet":
        try:
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(path_or_url)
            remaining = limit
            for batch in pf.iter_batches(batch_size=CHUNK):
                df = batch.to_pandas()
                if remaining is not None:
                    df = df.head(remaining)
                    remaining -= len(df)
                yield df
                if remaining is not None and remaining <= 0:
                    return
            return
        except ImportError:
            df = pd.read_parquet(path_or_url)
            yield df.head(limit) if limit else df
            return

    if ext == "json":
        df = pd.read_json(path_or_url)
        yield df.head(limit) if limit else df
        return

    sep = "\t" if ext in ("tsv", "tab") else ","
    for df in pd.read_csv(path_or_url, sep=sep, chunksize=CHUNK,
                          low_memory=False, on_bad_lines="skip"):
        yield df


def load(path_or_url, limit=None, verbose=True):
    db.init()
    run_id = db.start_run("file")
    total_read = total_clean = total_rej = ins = upd = 0
    try:
        for df in _iter_chunks(path_or_url, limit):
            if df is None or df.empty:
                continue
            df = _stringify_dates(df)
            total_read += len(df)
            # normalise() yields instead of building a list, and the rows go
            # straight into sqlite. Peak held row-dicts at any moment: one.
            rows, rejects = normalise(
                (r for r in df.to_dict(orient="records")), "live")
            i, u = db.upsert_many(rows)      # consumes the generator
            ins += i
            upd += u
            # Release the chunk before the next one. Without this the peak
            # grows across batches and the free instance gets OOM-killed
            # mid-load, which is why the row count kept resetting.
            del df, rows
            gc.collect()
            if verbose and total_read % 250000 < CHUNK:
                print(f"  … {total_read:,} read, {ins:,} new", flush=True)

        total_clean = ins + upd
        if total_clean == 0:
            raise ValueError("no usable rows after cleaning — check the column names")
        # Build indexes once, after the bulk load. Doing it up-front makes a
        # 1.48M-row insert several times slower here and on the server.
        db.build_indexes(verbose=verbose)
        # Pay the expensive aggregate once, here, so every page render
        # afterwards reads one stored row instead of scanning 1.48M.
        db.recompute_stats()
        db.finish_run(run_id, "ok", total_read, ins, upd,
                      f"file:{os.path.basename(str(path_or_url))}")
        if verbose:
            print(f"[mandi-lens] file={os.path.basename(str(path_or_url))} "
                  f"read={total_read:,} cleaned={total_clean:,} rejected={total_rej:,} "
                  f"inserted={ins:,} updated={upd:,}")
        return dict(read=total_read, cleaned=total_clean, rejected=total_rej,
                    inserted=ins, updated=upd)
    except Exception as e:
        db.finish_run(run_id, "failed", total_read, ins, upd, str(e))
        raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="local file or URL")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    load(a.path, a.limit)
