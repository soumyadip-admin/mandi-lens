# 🚀 Deploy Mandi Lens today

Total time: **~20 minutes.** Everything below is copy-paste. No config files to edit.

---

## Step 0 — Get your free data.gov.in key (3 min)

You cannot skip this if you want live all-India data.

1. Go to **https://data.gov.in**
2. Sign in (Google/K Parichay registration)
3. Click your name → **My Account**
4. Find **API Key** → **Generate Your New API Key**
5. Copy it. It looks like `579b464db66ec23bdd000001…`

Keep it in a notes app for now — you'll paste it into Render in Step 2.

> Without a key the app still works: it seeds itself from the bundled real
> Agmarknet export (18 states, one day of data). With a key it pulls the full
> current national dataset.

### If you get stuck on the JanParichay / Meri Pehchaan login

This is the most common blocker and it is **not your fault** — data.gov.in routes
all sign-ins through India's national SSO.

| What you see | What it means | Do this |
|---|---|---|
| Redirected to `meripehchaan.gov.in` | Normal. It is the SSO gate. | Sign in, or register a JanParichay username |
| Sign In button greyed out | Both checkboxes must be ticked | Tick "I consent to MeriPehchaan terms" |
| "Incorrect Captcha" / loop back | Session cookie dropped | Clear cookies for `gov.in`, try another browser |
| Registration asks for Aadhaar/mobile OTP | Standard KYC | Use your mobile number — Aadhaar is optional on most flows |
| Still stuck after 3 tries | Government portals rate-limit aggressively | **Skip it. Deploy without the key today.** |

**You do not need the key to post today.** Without it, `bootstrap.py` seeds the
app from the bundled Agmarknet export — 7,373 rows of real government data
across 18 states. The site says so honestly in a blue banner. Add the key later
by updating the environment variable on Render and clicking **Manual Deploy**;
nothing else changes.

---

## Step 1 — Push to GitHub (5 min)

In the project folder:

```bash
cd mandi-lens
git init
git add .
git commit -m "Mandi Lens: clean front-end for India's open mandi price data"
git branch -M main
git remote add origin https://github.com/soumyadip-admin/mandi-lens.git
git push -u origin main
```

**Create the repo first** on GitHub: New repository → name `mandi-lens` →
Public → **don't** add README/gitignore (you already have them) → Create.

Check before pushing: `git status` must **not** list `data/mandi.db` or `.env`.
They're already in `.gitignore`.

---

## Step 2 — Deploy on Render (8 min)

You already have a Render account. Two options:

### Option A — Blueprint (recommended, uses the included `render.yaml`)

1. Render dashboard → **New +** → **Blueprint**
2. Connect your `mandi-lens` repo
3. Render reads `render.yaml` and shows you a single service: `mandi-lens`
4. Click **Apply**
5. When it asks for `DATA_GOV_API_KEY`, paste your key from Step 0
6. Wait ~3 minutes for the build

### Option B — Manual web service

1. **New +** → **Web Service** → connect the repo
2. Fill in exactly:

| Field | Value |
|---|---|
| Runtime | **Python 3** |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python bootstrap.py && gunicorn app.main:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120` |
| Health Check Path | `/health` |
| Instance Type | **Free** |

3. **Environment** → Add Environment Variable:
   - Key: `DATA_GOV_API_KEY`
   - Value: your key
4. **Create Web Service**

> **Free tier note:** it sleeps after 15 minutes of no traffic and takes ~50s to
> wake. The cron job in `render.yaml` pings it twice a day, so it stays warm
> enough. Don't be alarmed by the first slow load.

---

## Step 3 — Verify it's alive (2 min)

Open `https://YOUR-APP.onrender.com/health`. You should see:

```json
{"ok": true, "records": 1480884, "latest_date": "...", "stale": false}
```

If `records` is `0`, the seed didn't run — check the Render **Logs** tab for
`[bootstrap]`. If `ok` is false but records > 0, it's just stale data.

Then check the actual site: does the home page show crops and prices? Does
`/commodity/Onion` draw a chart? Pinch it on your phone — it's mobile-first.

---

## Step 4 — Post to LinkedIn

Use the files in `linkedin-kit/` and the copy in `LINKEDIN.md`.

Attach **2 images**: `02-onion.png` (the chart) and `03-mobile.png` (phones).
Put the live URL in the first comment too — LinkedIn sometimes buries links.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Build fails: `pyarrow` | Free tier memory | Remove `pyarrow` from requirements — the app only needs it for `.parquet` |
| `records: 0` | Seed failed | Check logs; run `python bootstrap.py` locally to see the error |
| Data stale | Background refresh hasn't run yet | Set `MANDI_AUTO_REFRESH=1`, then visit `/health` — it kicks the refresh |
| Site shows "bundled snapshot" banner | `DATA_GOV_API_KEY` missing or rejected | Add the key in **Environment**, then **Manual Deploy** |
| 502 on first load | Free tier cold start | Wait 60s and refresh |
| `DATA_GOV_API_KEY` invalid | Key typo | Regenerate at data.gov.in → Environment → update → **Manual Deploy** |

---

## How freshness works (read this once)

There is **no cron job**, on purpose. On Render's free tier every service gets
its own ephemeral disk — a cron container that downloads 745,000 rows writes them
to a database your website will never read. It succeeds and changes nothing.

Instead, the web process refreshes itself:

* On boot, `bootstrap.py` pulls **2 pages** of live data (fast — the deploy must
  open a port within Render's timeout) and then tops up from the bundled export.
* Every request to `/` or `/health` checks whether data is older than 30 hours.
  If it is, a **background thread** starts a full pull. Requests are never blocked.
* A module-level lock means only one refresh ever runs at a time.

Tuning knobs (all optional environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `MANDI_AUTO_REFRESH` | `1` | Set `0` to disable background refresh entirely |
| `MANDI_REFRESH_PAGES` | `0` | Pages per refresh. `0` = full national pull |
| `MANDI_MIN_GAP_MINUTES` | `30` | Never refresh more often than this |
| `BOOT_FETCH_PAGES` | `2` | Pages fetched during boot |

## What I could not do for you

I have **no access to your GitHub or Render account**, and you should never paste
credentials into a chat. So: your login, your click. Everything above is written
so that's 20 minutes of clicking, not configuring.
