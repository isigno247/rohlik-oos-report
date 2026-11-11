"""Diagnostic: full anonymous flow incl. delivery-address/set, for all 5 zones.
Prints only warehouse IDs, statuses and unit counts - no cookies, no credentials."""
import http.cookiejar, json, urllib.error, urllib.parse, urllib.request, uuid

BASE = "https://www.rohlik.cz"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
ZONES = [("chrastany", "U Rybníka 29 Chrášťany"), ("praha_liboc", "Libocká 10/64 Praha"),
         ("horni_pocernice", "Náchodská 756/90 Praha"), ("brno", "Čápkova 13/5 Brno"),
         ("ostrava", "Nemocniční 3328/11 Ostrava")]
PIDS = [1423458, 1454631, 1474802, 1474799]


def session():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    sid = str(uuid.uuid4())
    h = {"User-Agent": UA, "Accept": "application/json", "X-Origin": "WEB", "X-Custom-SessionId": sid,
         "Accept-Language": "cs-CZ,cs;q=0.9"}

    def req(m, path, params=None, body=None):
        url = BASE + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        hh = dict(h)
        data = None
        if body is not None:
            data = json.dumps(body).encode(); hh["Content-Type"] = "application/json"
        try:
            with op.open(urllib.request.Request(url, data=data, headers=hh, method=m), timeout=30) as r:
                t = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(t) if t.strip() else None)
        except urllib.error.HTTPError as e:
            return e.code, None
    try:
        op.open(urllib.request.Request(BASE + "/", headers={"User-Agent": UA}), timeout=30).read()
    except Exception as e:
        print("   home error", e)
    return req


def stock(req):
    st, rows = req("GET", "/api/v1/products/stock", {"products": PIDS})
    if not rows:
        return set(), f"stock {st} no rows"
    return {r.get("warehouseId") for r in rows}, " ".join(f"{r['productId']}:{r.get('maxBasketAmount')}" for r in rows)


for variant in ("check+set", "set only"):
    print(f"\n######## variant: {variant}")
    for key, q in ZONES:
        req = session()
        st, sug = req("GET", "/api/v3/address/autocomplete", {"query": q})
        lst = sug if isinstance(sug, list) else (sug or {}).get("suggestions") or (sug or {}).get("data") or []
        if not lst:
            print(f"{key}: autocomplete {st} empty"); continue
        st2, conf = req("POST", "/api/v4/address/autocomplete/confirm", body=lst[0])
        if not conf:
            print(f"{key}: confirm {st2}"); continue
        aid, zone, wh = conf["id"], conf.get("zoneId"), conf.get("warehouseId")
        b0, _ = stock(req)
        if variant == "check+set":
            c1, _ = req("POST", "/services/frontend-service/delivery-address/check", body={"addressId": aid})
            c2, _ = req("POST", "/services/frontend-service/delivery-address/check", body={"addressId": aid, "zoneId": zone})
        else:
            c1 = c2 = "-"
        s1, _ = req("POST", "/services/frontend-service/delivery-address/set", body={"addressId": aid, "zoneId": zone})
        got, detail = stock(req)
        verdict = "OK" if got == {wh} else "WRONG"
        print(f"{key}: expect wh {wh} | before {sorted(b0)} | check {c1}/{c2} set {s1} | after {sorted(got)} {detail} -> {verdict}")
