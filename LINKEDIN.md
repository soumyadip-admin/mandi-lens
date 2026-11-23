# LinkedIn kit

## Post (copy this)

---

I built a clean front-end for India's mandi prices. It's live and free.

The Government of India publishes daily wholesale crop prices on data.gov.in.
The data is open, free, and genuinely useful — but the portal is slow, the field
names change between datasets, and dates come in formats that break most code.

So I fixed the boring part.

**Mandi Lens** turns that mess into something a person can actually use:
→ Pick a crop, see what it's selling for in every market
→ Compare markets — the spread between the cheapest and dearest mandi
→ 2 years of price history as a chart
→ A free JSON API, no key needed

The part I'm most pleased with isn't the design. It's the cleaning layer:

• Dates arrive as DD/MM/YYYY, which silently corrupts any code that assumes
  American order. 03/04/2026 is 3 April here, not 4 March.
• The same field is named "State", "state_name", "statename" and "State Name"
  across dataset mirrors.
• Some endpoints return CSV when you asked for JSON.
• Pagination over-runs the last page.

I tested it against **1,480,884 real rows** across 1,741 markets. That surfaced
three bugs the sample data never would:

- A per-row upsert that needed 3 million SQL round-trips
- A loader that silently ran out of memory at ~200,000 rows
- A search query taking 3.8 seconds

Search now runs in 15 ms. Free-text search on 1.5 million rows.

Honest limits, because it matters:
- It's indicative wholesale data, not an offer
- Not every market reports every day
- Some states aren't in the public history file
- I built the UI and the pipeline; the data is the government's

Why I built this rather than another dashboard: dashboards show you data.
This one answers a question someone actually asks at 7am — "should I sell today?"

Live: [YOUR URL]
Code: https://github.com/soumyadip-admin/mandi-lens

Data: data.gov.in · Built with Python, Flask, SQLite FTS5

#DataAnalytics #Python #OpenData #India #DataEngineering #Portfolio

---

## Tips that matter

1. **Post between 9–11am IST** on a weekday. Tuesday–Thursday is strongest.
2. **Attach 2 images** — `02-onion.png` first (the chart), `03-mobile.png` second.
3. **Put the link in the first comment**, not the post body. LinkedIn reduces
   reach on posts with external links.
4. **Don't say "excited to share."** You built something; say what it does.
5. **Reply to every comment in the first hour.** The algorithm weights early
   engagement heavily.
6. **Tag nothing and nobody** unless you actually know them.

## Shorter version (if you want punchier)

---

I built a working front-end for India's open mandi price data.

The government publishes daily crop prices. Free and public. The portal is
painful to use and the data is genuinely messy — dates in DD/MM/YYYY that break
most code, four different spellings of "State", CSV when you asked for JSON.

Mandi Lens fixes that: pick a crop, compare every market, see 2 years of trend.
Plus a free API.

Tested against 1,480,884 real rows and 1,741 markets. That found three bugs my
sample data hid — including a search query that took 3.8 seconds. It's now 15 ms.

Live: [YOUR URL]

#Python #OpenData #DataAnalytics #India

---

## Images to attach (in linkedin-kit/)

| File | Size | Use |
|---|---|---|
| `02-onion.png` | 1200×860 | **First** — real data, real price spike |
| `03-mobile.png` | 390×800 | Second — proves it works on a phone |
| `01-home.png` | 1200×760 | Alternative |
| `00-social-card.png` | 1200×630 | Use as the post image if you post a link only |

## What to say when someone asks "how long did it take?"

Be honest: **one weekend for the app, one more morning for the real-data
testing.** The second part is the interesting answer — say that testing on real
data found bugs the demo data hid. That's the sentence that makes an
interviewer lean forward.
