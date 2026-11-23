# 🌾 Mandi Lens

**A clean, fast front-end for India's open mandi price data.**
`1,480,884 real records` · `1,741 markets` · `14 crops` · `MIT` · **37 tests** · **25/25 real-data checks passed**

[Quick start](#quick-start-no-api-key-needed) · [Deploy in 20 min](DEPLOY.md) · [Validation report](VALIDATION.md) · [API](#api) · **3D Explorer at `/explore`**

A clean, fast, phone-friendly front-end for **India's open mandi price data** —
the wholesale crop prices published by the Government of India on
[data.gov.in](https://data.gov.in).

The government publishes the data. It is free and openly licensed.
It is also buried in a slow portal with inconsistent field names and broken
pagination. This project fixes that.

> **Run it locally in 60 seconds** (below), or [deploy it free on Render](DEPLOY.md) in about 20 minutes.

---

## Why this exists

Every other job-market or "data analyst portfolio" project analyses a static
Kaggle CSV. This one is different in three ways:

1. **It's a tool, not a report.** Someone can use it to decide whether to sell
   potatoes today.
2. **It handles real, messy, live government data** — the part that makes
   everyone else give up.
3. **It has an API**, so other people can build on it.

---

## Quick start (no API key needed)

```bash
pip install -r requirements.txt
python -m ingest.fetch --fixture     # load the bundled sample data
python run.py                        # http://127.0.0.1:5000
```

That's it. You'll have a working site with 900+ realistic rows.

### With live government data

1. Register free at [data.gov.in](https://data.gov.in)
2. My Account → **Generate API Key**
3. Then:

```bash
export DATA_GOV_API_KEY=your_key_here
python -m ingest.fetch               # live fetch, all pages
python run.py
```

---

## What it does

| | |
|---|---|
| **Browse** | Today's prices by crop and market, with week-on-week change |
| **Compare** | Every market's price for a crop, plus the cheapest/most expensive spread |
| **Trend** | 400-day price history as an inline SVG chart (no JS libraries) |
| **Search** | By crop, market or district; filter by state |
| **API** | Free JSON endpoints for the same data |
| **Honest UI** | Warns you when the data is stale instead of pretending it's fresh |
| **Real-scale tested** | Validated on **1,481,324 real rows** — 25/25 checks, see [VALIDATION.md](VALIDATION.md) |

---

## The messy-data handling (the actual work)

`ingest/fetch.py` deals with what the OGD platform really throws at you:

- **Field names differ between dataset mirrors** → 8-name alias map per field,
  matched after normalising case/punctuation
- **Dates arrive in at least 8 formats**, including `DD/MM/YYYY` which breaks
  any parser that assumes American order → explicit format ladder + regex fallback
- **Prices contain commas, rupee symbols, `NR`, `NA`, blanks** → defensive parser
- **Missing modal price but valid min/max** → derived, not dropped
- **Pagination over-runs the last page** → empty-batch guard before trusting `total`
- **Some resources return CSV despite `format=json`** → content-sniffing parser
- **Multiple resource IDs for the same dataset** → tries each until one works

### Loading real exports

Beyond the API, you can load any Agmarknet/data.gov.in export directly:

```bash
python -m ingest.loadfile data/real/agmarknet_export.xlsx     # messy real XLSX
python -m ingest.loadfile history.parquet                     # 1.48M rows, streams
python -m ingest.loadfile https://example.com/prices.csv      # remote file
```

Handles multiple sheets, Excel datetime objects, spaced column names, and
1.5M-row files without running out of memory.

Run the tests for the full list:

```bash
python -m pytest tests/ -q           # 34 unit/integration tests
python tests/check_real_data.py      # 25 live checks against running server
```

---

## API

| Endpoint | Returns |
|---|---|
| `GET /api/prices?commodity=Potato&state=West%20Bengal&limit=100` | matching rows |
| `GET /api/commodities?state=Bihar` | crops available |
| `GET /api/states` | states available |
| `GET /api/trend/Potato` | daily average price series |
| `GET /api/spread/Potato` | latest price per market + change |
| `GET /health` | record count, latest date, last ingest, staleness |

No key required. Rate-limit yourself politely.

---

## Automation

Three options, pick one:

**1. GitHub Actions** (free, no server) — `.github/workflows/refresh.yml` runs
twice daily. Add `DATA_GOV_API_KEY` as a repo secret and it works.

**2. Docker** (self-hosted, unattended):

```bash
DATA_GOV_API_KEY=xxx docker compose up -d
```

Runs the web app *and* a separate ingest loop that refreshes every 6 hours.

**3. Cron** on any machine:

```cron
30 2 * * * cd /path/to/mandi-lens && /usr/bin/python3 -m ingest.scheduler --once
```

---

## Deploy free

| Host | How |
|---|---|
| **Render** | New Web Service → build `pip install -r requirements.txt` → start `gunicorn app.main:app` |
| **Railway / Fly.io** | `Dockerfile` included, just point at it |
| **GitHub Pages** | Not suitable — this needs a Python process |
| **A ₹400 VPS** | `docker compose up -d` and forget it |

Always set `DATA_GOV_API_KEY` in the host's environment variables, never in code.

---

## Project layout

```
mandi-lens/
├── app/
│   ├── config.py         # env-driven settings
│   ├── db.py             # SQLite layer: schema, upserts, queries
│   ├── main.py           # Flask routes + JSON API
│   └── templates/        # Jinja templates, self-contained CSS
├── ingest/
│   ├── fetch.py          # the messy-data engine
│   └── scheduler.py      # always-on refresh loop
├── data/fixtures/        # bundled sample so the demo never needs a key
├── tests/                # 34 tests: parsing, cleaning, routes, API
├── Dockerfile
├── docker-compose.yml
└── .github/workflows/refresh.yml
```

---

## Data honesty

- Prices come from data.gov.in. Nothing is invented, estimated or predicted.
- Prices are **indicative wholesale rates** in ₹ per quintal (100 kg).
- Not every market reports every day.
- Transport, commission and grading costs are not included.
- The UI shows a banner when the data is more than 30 hours old.

---

## Licence

MIT for the code. The data belongs to the Government of India and is published
under the Open Government Data licence — check the terms of each dataset before
redistributing it commercially.
