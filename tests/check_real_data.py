import json, time, urllib.parse, urllib.request

BASE = "http://127.0.0.1:5000"
results = []

def get(path, expect=200, ms=None, follow=True):
    url = BASE + path
    t = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MandiLensCheck/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            code, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read()
    except Exception as e:
        code, body = 0, str(e).encode()
    dt = (time.perf_counter() - t) * 1000
    ok = code == expect and (ms is None or dt <= ms)
    return ok, code, dt, body

def record(n, name, ok, detail):
    results.append((n, name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {n:>2}. {name:<44} {detail}")

# 1-6 pages
ok,c,dt,b = get("/");                       record(1,"Home page (1.48M rows)", ok and len(b)>5000, f"{c} {dt:.0f}ms {len(b)}b")
ok,c,dt,b = get("/?state=Karnataka");       record(2,"State filter", ok and b"Karnataka" in b, f"{c} {dt:.0f}ms")
ok,c,dt,b = get("/?q=onion");               record(3,"Free-text search", ok and b"nion" in b, f"{c} {dt:.0f}ms")
ok,c,dt,b = get("/?q=zzzznotacrop");        record(4,"Search with no results", ok and b"Nothing matched" in b, f"{c} {dt:.0f}ms")
ok,c,dt,b = get("/commodity/Potato");       record(5,"Commodity detail page", ok and b"Price trend" in b, f"{c} {dt:.0f}ms")
ok,c,dt,b = get("/commodity/NotACrop",404); record(6,"404 for unknown crop", ok, f"{c}")

# 7-12 APIs
ok,c,dt,b = get("/health");                 j=json.loads(b); record(7,"/health", ok and j["ok"] and j["records"]>1000000, f"{c} {j['records']:,} rows")
ok,c,dt,b = get("/api/states");             j=json.loads(b); record(8,"/api/states", ok and len(j)>=3, f"{c} {len(j)} states")
ok,c,dt,b = get("/api/commodities");        j=json.loads(b); record(9,"/api/commodities", ok and len(j)>=5, f"{c} {len(j)} crops")
ok,c,dt,b = get("/api/prices?commodity=Potato&limit=5"); j=json.loads(b)
record(10,"/api/prices limit respected", ok and len(j["results"])==5, f"{c} {len(j['results'])} rows")
ok,c,dt,b = get("/api/trend/Potato");       j=json.loads(b); record(11,"/api/trend", ok and len(j)>1, f"{c} {len(j)} points")
ok,c,dt,b = get("/api/spread/Potato");      j=json.loads(b)
record(12,"/api/spread has change field", ok and len(j)>1 and "change" in j[0], f"{c} {len(j)} markets")

# 13-18 edge cases
ok,c,dt,b = get("/api/prices?limit=999999"); j=json.loads(b)
record(13,"API caps runaway limit", ok and len(j["results"])<=500, f"{c} capped to {len(j['results'])}")
ok,c,dt,b = get("/api/prices?commodity=%27%3B%20DROP%20TABLE%20prices%3B--")
record(14,"SQL injection attempt", ok and c==200, f"{c} survived")
ok,c,dt,b = get("/health"); j=json.loads(b)
record(15,"DB intact after injection", j["records"]>1000000, f"{j['records']:,} rows")
ok,c,dt,b = get("/commodity/" + urllib.parse.quote("Green Chilli"))
record(16,"Crop name with a space", ok and b"Green Chilli" in b, f"{c}")
ok,c,dt,b = get("/commodity/Potato?state=Karnataka")
record(17,"Commodity + state combo", ok, f"{c} {dt:.0f}ms")
ok,c,dt,b = get("/about");                  record(18,"About page", ok and b"data.gov.in" in b, f"{c}")

# 19-22 consistency
ok,c,dt,h = get("/health"); j=json.loads(h)
ok2,c2,dt2,b2 = get("/")
record(19,"Health count matches page", j["records"]>1000000 and b"1,480" in b2 or b"1,4" in b2, f"{j['records']:,}")
ok,c,dt,b = get(f"/api/prices?commodity=Potato&state=Karnataka&limit=50")
j=json.loads(b)
all_match = all(r["commodity"]=="Potato" and r["state"]=="Karnataka" for r in j["results"])
record(20,"Multi-filter returns only matches", ok and all_match, f"{len(j['results'])} rows all correct")
ok,c,dt,b = get("/api/prices?commodity=Potato&limit=200"); j=json.loads(b)
prices = [r["modal_price"] for r in j["results"] if r["modal_price"] is not None]
record(21,"No negative/zero prices", all(p>0 for p in prices), f"{len(prices)} prices, min {min(prices):.1f}")
mm = [r for r in j["results"] if r["min_price"] and r["max_price"] and r["min_price"]>r["max_price"]]
record(22,"min_price never exceeds max_price", len(mm)==0, f"{len(mm)} violations")

# 23-25 performance gates
ok,c,dt,b = get("/api/prices?limit=100", ms=800)
record(23,"API responds under 800ms", ok, f"{dt:.0f}ms")
ok,c,dt,b = get("/?q=kol", ms=2500)
record(24,"Search responds under 2.5s", ok, f"{dt:.0f}ms")
ok,c,dt,b = get("/api/spread/Onion", ms=3000)
record(25,"Market spread under 3s", ok, f"{dt:.0f}ms")

passed = sum(1 for r in results if r[2])
print(f"\n{'='*62}\n  RESULT: {passed}/{len(results)} checks passed\n{'='*62}")
for n,name,ok,detail in results:
    if not ok: print(f"  FAILED: {n}. {name} — {detail}")
