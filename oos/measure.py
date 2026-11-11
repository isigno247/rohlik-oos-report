"""Measurement pass over Rohlik's public web endpoints - no login, no MCP.

Per zone, an anonymous session does what the site's own address picker does:
  1. GET  /api/v3/address/autocomplete?query=<address>      -> address payload
  2. POST /api/v4/address/autocomplete/confirm  (payload)    -> id, warehouseId, zoneId
  3. POST /services/frontend-service/delivery-address/check {addressId}
  4. POST /services/frontend-service/delivery-address/check {addressId, zoneId}
Then stock and prices are read for the SKUs the footprint says the zone carries. Every stock row
carries a warehouseId; the zone counts as verified only if it equals the warehouse Rohlik named in
step 2 (and the config's expected_warehouse_id, when set). Each zone gets a fresh session, so no
state leaks between zones and nobody's account is ever touched.
"""
from __future__ import annotations

import http.cookiejar
import json
import logging
import os
import time
import urllib.parse
import urllib.request
import uuid

log = logging.getLogger("oos.measure")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class ToolError(RuntimeError):
    pass


class Session:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.headers = {"User-Agent": UA, "Accept": "application/json", "X-Origin": "WEB",
                        "X-Custom-SessionId": str(uuid.uuid4()), "Accept-Language": "cs-CZ,cs;q=0.9"}

    def _req(self, method: str, path: str, params=None, body=None, retries: int = 3):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data, hdr = None, dict(self.headers)
        if body is not None:
            data = json.dumps(body).encode()
            hdr["Content-Type"] = "application/json"
        last = None
        for attempt in range(1, retries + 1):
            try:
                with self.op.open(urllib.request.Request(url, data=data, headers=hdr, method=method),
                                  timeout=30) as r:
                    raw = r.read().decode("utf-8", "replace")
                return json.loads(raw) if raw.strip() else None
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code} on {method} {path}"
                if e.code < 500 and e.code != 429:
                    break
            except Exception as e:  # network blips
                last = f"{type(e).__name__} on {method} {path}: {e}"
            time.sleep(2 * attempt)
        raise ToolError(last)

    def warm(self):
        try:  # first visit like a browser, so the server issues its anonymous cookies
            self.op.open(urllib.request.Request(self.base + "/", headers={"User-Agent": UA}), timeout=30).read()
        except Exception as e:
            log.warning("homepage warm-up failed: %s", e)

    def set_address(self, query: str) -> dict:
        res = self._req("GET", "/api/v3/address/autocomplete", {"query": query})
        sugg = res if isinstance(res, list) else (res or {}).get("data") or (res or {}).get("suggestions") or []
        if not sugg:
            raise ToolError(f"no address suggestion for '{query}'")
        conf = self._req("POST", "/api/v4/address/autocomplete/confirm", body=sugg[0])
        if not conf or not conf.get("id") or not conf.get("warehouseId"):
            raise ToolError(f"address confirm returned no warehouse for '{query}'")
        self._req("POST", "/services/frontend-service/delivery-address/check", body={"addressId": conf["id"]})
        self._req("POST", "/services/frontend-service/delivery-address/check",
                  body={"addressId": conf["id"], "zoneId": conf.get("zoneId")})
        return {"warehouse_id": int(conf["warehouseId"]), "zone_id": conf.get("zoneId"),
                "address": sugg[0].get("suggestText")}

    def stock(self, ids: list[int]) -> dict[int, dict]:
        out = {}
        for i in range(0, len(ids), 20):
            rows = self._req("GET", "/api/v1/products/stock", {"products": ids[i:i + 20]}) or []
            out.update({int(r["productId"]): r for r in rows})
        return out

    def prices(self, ids: list[int]) -> dict[int, dict]:
        out = {}
        for i in range(0, len(ids), 20):
            rows = self._req("GET", "/api/v1/products/prices", {"products": ids[i:i + 20]}) or []
            out.update({int(r["productId"]): r for r in rows})
        return out


def _amount(x):
    if isinstance(x, dict):
        x = x.get("amount", x.get("full"))
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def parse(stock_row: dict | None, price_row: dict | None) -> dict:
    s, p = stock_row or {}, price_row or {}
    sales = p.get("sales") or s.get("sales") or []
    sale = sales[0] if sales else None
    rep = s.get("expectedReplenishment")
    rep_date = rep if isinstance(rep, str) else (rep or {}).get("date") if isinstance(rep, dict) else None
    in_stock = bool(s.get("inStock"))
    return {
        "units": int(s.get("maxBasketAmount") or 0) if in_stock else 0,
        "price": _amount(p.get("price")),
        "sale_price": _amount(sale.get("price")) if isinstance(sale, dict) else None,
        "sale_id": (sale.get("id") or sale.get("saleId")) if isinstance(sale, dict) else None,
        "reason": (s.get("unavailabilityReason") if stock_row is not None else
                   "Not returned for this warehouse today (possible delisting)"),
        "restock_date": (rep_date or "")[:10] or None,
        "missing": stock_row is None,
    }


def measure(cfg: dict, base: str) -> dict:
    zones = cfg["zones"]
    carried = {z["key"]: [] for z in zones}
    for b in cfg["brands"]:
        for s in b["skus"]:
            excluded = set(s.get("not_listed_in", [])) | set(s.get("unverified_in", []))
            for z in zones:
                if z["key"] not in excluded:
                    carried[z["key"]].append(int(s["id"]))

    result = {"zones": {}, "restored": True}   # nothing to restore: no account is used
    seen_wh = {}
    for z in zones:
        key = z["key"]
        zr = {"status": "FAILED", "verification": None, "canary": None, "products": {}}
        try:
            sess = Session(base)
            sess.warm()
            addr = sess.set_address(z["address_query"])
            wh = addr["warehouse_id"]
            exp = z.get("expected_warehouse_id")
            ids = carried[key]
            st, pr = sess.stock(ids), sess.prices(ids)
            got = {int(r.get("warehouseId", -1)) for r in st.values()}
            problems = []
            if exp and wh != int(exp):
                problems.append(f"address resolved to warehouse {wh}, expected {exp}")
            if got and got != {wh}:
                problems.append(f"stock came from warehouse(s) {sorted(got)}, not {wh}")
            if not st:
                problems.append("stock endpoint returned no rows")
            if wh in seen_wh:
                problems.append(f"same warehouse as {seen_wh[wh]} - zone switch did not take effect")
            if problems:
                log.warning("zone %s not verified: %s", key, "; ".join(problems))
                zr["verification"] = {"method": "web_session", "problems": problems, "warehouse_id": wh}
            else:
                seen_wh[wh] = key
                zr.update(status="OK",
                          verification={"method": "web_session", "warehouse_id": wh, "zone_id": addr["zone_id"]},
                          products={pid: parse(st.get(pid), pr.get(pid)) for pid in ids})
        except ToolError as e:
            log.error("zone %s failed: %s", key, e)
            zr["verification"] = {"method": "web_session", "problems": [str(e)]}
        result["zones"][key] = zr
        log.info("zone %s: %s", key, zr["status"])
        time.sleep(1)   # be polite: a handful of requests per zone, once a day
    return result


def run_measure(cfg: dict) -> dict:
    base = os.environ.get("ROHLIK_BASE_URL")
    if not base:  # the CI self-test still exports ROHLIK_MCP_URL for the local mock
        mcp = os.environ.get("ROHLIK_MCP_URL", "")
        base = mcp[:-4] if mcp.endswith("/mcp") else "https://www.rohlik.cz"
    return measure(cfg, base)
