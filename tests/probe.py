"""One-off diagnostic: which session variant makes Rohlik's stock follow the chosen address?
Prints only warehouse IDs, HTTP codes and cookie NAMES - never values or credentials."""
import http.cookiejar, json, os, urllib.error, urllib.parse, urllib.request, uuid

BASE = "https://www.rohlik.cz"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
TARGET = "Čápkova 13/5 Brno"          # expected warehouse 8793
PIDS = [1423458, 1454631, 1474802]


class S:
    def __init__(self, udid=False, sid_hdr=None):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.h = {"User-Agent": UA, "Accept": "application/json", "X-Origin": "WEB",
                  "Accept-Language": "cs-CZ,cs;q=0.9"}
        self.h["X-Custom-SessionId"] = sid_hdr or str(uuid.uuid4())
        if udid:
            self.set_cookie("udid", str(uuid.uuid4()))

    def set_cookie(self, name, value):
        c = http.cookiejar.Cookie(0, name, value, None, False, "www.rohlik.cz", True, False, "/", True,
                                  True, None, False, None, None, {})
        self.jar.set_cookie(c)

    def names(self):
        return sorted({c.name for c in self.jar})

    def req(self, m, path, params=None, body=None, raw=False):
        url = BASE + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        h = dict(self.h)
        data = None
        if body is not None:
            data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
        try:
            with self.op.open(urllib.request.Request(url, data=data, headers=h, method=m), timeout=30) as r:
                t = r.read().decode("utf-8", "replace")
                return r.status, (t if raw else (json.loads(t) if t.strip() else None))
        except urllib.error.HTTPError as e:
            return e.code, None

    def home(self):
        try:
            self.op.open(urllib.request.Request(BASE + "/", headers={"User-Agent": UA, "Accept": "text/html"}),
                         timeout=30).read()
        except Exception as e:
            print("   home error", e)

    def stock_wh(self):
        st, rows = self.req("GET", "/api/v1/products/stock", {"products": PIDS})
        if not rows:
            return f"stock {st} no rows"
        return f"stock {st} wh={sorted({r.get('warehouseId') for r in rows})} " + \
               " ".join(f"{r['productId']}:{r.get('maxBasketAmount')}" for r in rows)

    def pick(self, extra=False):
        st, sug = self.req("GET", "/api/v3/address/autocomplete", {"query": TARGET})
        s0 = (sug if isinstance(sug, list) else (sug or {}).get("data") or [None])[0]
        st2, conf = self.req("POST", "/api/v4/address/autocomplete/confirm", body=s0)
        print(f"   autocomplete {st} confirm {st2} -> wh={conf and conf.get('warehouseId')} zone={conf and conf.get('zoneId')}")
        if not conf:
            return None, None
        aid, zone = conf["id"], conf.get("zoneId")
        a, ca = self.req("POST", "/services/frontend-service/delivery-address/check", body={"addressId": aid})
        b, cb = self.req("POST", "/services/frontend-service/delivery-address/check", body={"addressId": aid, "zoneId": zone})
        print("   cookies after checks:", self.names())
        print(f"   check1 {a} keys={list((ca or {}).get('data') or {})[:6]}  check2 {b} keys={list((cb or {}).get('data') or {})[:6]}")
        if extra:
            r1, _ = self.req("GET", "/services/frontend-service/timeslots-api/delivery-information", {"addressId": aid})
            r2, _ = self.req("GET", "/api/v1/delivery-points/nearest", {"addressId": aid})
            r3, _ = self.req("GET", "/services/frontend-service/timeslots-api/0", {"addressId": aid, "reasonableDeliveryTime": "true"})
            print(f"   extra calls: delivery-information {r1}, nearest {r2}, timeslots {r3}")
        return aid, zone


def run(label, **kw):
    print(f"\n== {label}")
    extra = kw.pop("extra", False)
    login = kw.pop("login", False)
    s = S(**kw)
    s.home()
    print("   cookies after home:", s.names())
    print("   before:", s.stock_wh())
    if login:
        st, _ = s.req("POST", "/services/frontend-service/login",
                      body={"email": os.environ["RHL_EMAIL"], "password": os.environ["RHL_PASS"], "name": ""})
        print(f"   login {st}; cookies now: {s.names()}")
        if st != 200:
            return
    s.pick(extra=extra)
    print("   cookies after pick:", s.names())
    print("   AFTER:", s.stock_wh(), "  <- want wh=[8793]")


if __name__ == "__main__":
    run("A baseline (what the report does today)")
    run("B + self-made udid cookie", udid=True)
    run("C + extra follow-up calls the site makes", extra=True)
    run("D udid + extra calls", udid=True, extra=True)
    if os.environ.get("RHL_EMAIL") and os.environ.get("RHL_PASS"):
        run("E logged in via website login endpoint (not MCP)", login=True)
    else:
        print("\n== E skipped: RHL_EMAIL/RHL_PASS secrets not present")
