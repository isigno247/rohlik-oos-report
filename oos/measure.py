"""Measurement pass over Rohlik's public web endpoints - no login, no MCP.

Per zone, an anonymous session does what the site's own address picker does:
  1. GET  /api/v3/address/autocomplete?query=<address>      -> address payload
  2. POST /api/v4/address/autocomplete/confirm  (payload)    -> id, warehouseId, zoneId
  3. POST /services/frontend-service/delivery-address/check {addressId}
  4. POST /services/frontend-service/delivery-address/check {addressId, zoneId}
  5. POST /services/frontend-service/delivery-address/set   {addressId, zoneId}  <- pins the warehouse
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

    def resolve(self, query: str) -> int:
        """Which warehouse Rohlik assigns to an address - read only, nothing is pinned to the session."""
        res = self._req("GET", "/api/v3/address/autocomplete", {"query": query}, retries=2)
        sugg = res if isinstance(res, list) else (res or {}).get("data") or (res or {}).get("suggestions") or []
        if not sugg:
            raise ToolError(f"no address suggestion for '{query}'")
        conf = self._req("POST", "/api/v4/address/autocomplete/confirm", body=sugg[0], retries=2)
        if not conf or not conf.get("warehouseId"):
            raise ToolError(f"address confirm returned no warehouse for '{query}'")
        return int(conf["warehouseId"])

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
        # the call that actually pins the warehouse to an anonymous session (verified 2026-11-11)
        self._req("POST", "/services/frontend-service/delivery-address/set",
                  body={"addressId": conf["id"], "zoneId": conf.get("zoneId")})
        return {"warehouse_id": int(conf["warehouseId"]), "zone_id": conf.get("zoneId"),
                "address": sugg[0].get("suggestText")}

    def stock(self, ids: list[int]) -> dict[int, dict]:
        out = {}
        for i in range(0, len(ids), 20):
            rows = self._req("GET", "/api/v1/products/stock", {"products": ids[i:i + 20]}) or []
            out.update({int(r["productId"]): r for r in rows})
        return out

    def details(self, ids: list[int]) -> dict[int, dict]:
        """Product records (name, brand, flags). Used only as listing evidence, never for stock;
        a failure here never fails the zone."""
        out = {}
        for i in range(0, len(ids), 20):
            try:
                rows = self._req("GET", "/api/v1/products", {"products": ids[i:i + 20]}, retries=1) or []
            except ToolError as e:
                log.info("product details unavailable: %s", e)
                return out
            if isinstance(rows, dict):
                rows = rows.get("data") or rows.get("products") or []
            out.update({int(r.get("id") or r.get("productId")): r for r in rows
                        if isinstance(r, dict) and (r.get("id") or r.get("productId"))})
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


def delisting_evidence(rules: dict, stock_row: dict | None, detail_row: dict | None) -> str | None:
    """Explicit 'no longer offered' signal from Rohlik, or None. Text patterns are matched anywhere
    in the raw stock/product record; flags are exact field values (config: listing.delisted_flags).
    Never inferred from days at zero - that stays a passive 'possible delisting' note."""
    pats = [x.lower() for x in rules.get("delisted_patterns") or []]
    for src, row in (("stock", stock_row), ("product", detail_row)):
        if not row:
            continue
        raw = json.dumps(row, ensure_ascii=False).lower()
        hit = next((x for x in pats if x in raw), None)
        if hit:
            return f"Rohlik {src} record says '{hit}'"
        for f in rules.get("delisted_flags") or []:
            if f.get("source", "stock") == src and f["field"] in row and row[f["field"]] == f["value"]:
                return f"Rohlik {src} record has {f['field']}={json.dumps(f['value'])}"
    return None


def parse(stock_row: dict | None, price_row: dict | None) -> dict:
    s, p = stock_row or {}, price_row or {}
    sales = [x for x in (p.get("sales") or s.get("sales") or []) if isinstance(x, dict) and x.get("active", True)]
    promos = []
    for x in sales:
        pr, orig = _amount(x.get("price")), _amount(x.get("originalPrice")) or _amount(p.get("price"))
        promos.append({"type": x.get("type") or "sale", "price": pr, "orig": orig,
                       "pct": round((1 - pr / orig) * 100) if pr and orig else None,
                       "trigger": int(x.get("triggerAmount") or 1), "till": (x.get("validTill") or "")[:10] or None})
    # the shopper-facing promotion: anything except the personal Xtra member price
    sale = next((x for x in sales if x.get("type") != "premium"), None)
    rep = s.get("expectedReplenishment")
    rep_date = rep if isinstance(rep, str) else (rep or {}).get("date") if isinstance(rep, dict) else None
    in_stock = bool(s.get("inStock"))
    return {
        "units": int(s.get("maxBasketAmount") or 0) if in_stock else 0,
        "price": _amount(p.get("price")),
        "sale_price": _amount(sale.get("price")) if isinstance(sale, dict) else None,
        "sale_id": (sale.get("id") or sale.get("saleId")) if isinstance(sale, dict) else None,
        "promos": promos,
        "reason": (s.get("unavailabilityReason") if stock_row is not None else
                   "Not returned for this warehouse today (possible delisting)"),
        "restock_date": (rep_date or "")[:10] or None,
        "missing": stock_row is None,
    }


def measure(cfg: dict, base: str) -> dict:
    zones = cfg["zones"]
    rules = cfg.get("listing") or {}
    carried = {z["key"]: [] for z in zones}
    every = [int(s["id"]) for b in cfg["brands"] for s in b["skus"]]
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
        zr = {"status": "FAILED", "verification": None, "canary": None, "products": {},
              "probes": {}, "raw": {}}
        try:
            sess = Session(base)
            sess.warm()
            addr = sess.set_address(z["address_query"])
            wh = addr["warehouse_id"]
            exp = z.get("expected_warehouse_id")
            ids = carried[key]
            # stock for EVERY tracked SKU: a footprint 'not listed' position that suddenly has units
            # is hard evidence of a new listing. Prices only where the footprint says carried.
            st, pr = sess.stock(every), sess.prices(ids)
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
                products = {pid: parse(st.get(pid), pr.get(pid)) for pid in ids}
                zero = [pid for pid in ids if products[pid]["units"] == 0]
                nl = [pid for pid in every if pid not in ids]
                det = sess.details(zero + nl) if (zero or nl) else {}
                for pid in zero:
                    # Rohlik omits the stock record for products a warehouse does not offer (confirmed
                    # 18.11.2026: delisted Brno 1454635 and never-carried SKUs both come back empty,
                    # genuine OOS comes back with "Vyprodáno"). Classify confirms it over two runs.
                    products[pid]["delisted"] = (delisting_evidence(rules, st.get(pid), det.get(pid))
                                                 or ("no stock record returned for this warehouse"
                                                     if products[pid]["missing"] else None))
                probes = {}
                for pid in nl:
                    r = st.get(pid) or {}
                    probes[pid] = {"returned": pid in st, "in_stock": bool(r.get("inStock")),
                                   "units": int(r.get("maxBasketAmount") or 0) if r.get("inStock") else 0,
                                   "reason": r.get("unavailabilityReason")}
                # raw evidence for every zero/not-listed position, so a new Rohlik signal can be read
                # straight from state/raw instead of guessed
                raw = {str(pid): {"stock": st.get(pid), "product": det.get(pid)} for pid in zero + nl}
                zr.update(status="OK",
                          verification={"method": "web_session", "warehouse_id": wh, "zone_id": addr["zone_id"]},
                          products=products, probes=probes, raw=raw)
        except ToolError as e:
            log.error("zone %s failed: %s", key, e)
            zr["verification"] = {"method": "web_session", "problems": [str(e)]}
        result["zones"][key] = zr
        log.info("zone %s: %s", key, zr["status"])
        time.sleep(1)   # be polite: a handful of requests per zone, once a day
    return result


def base_url() -> str:
    base = os.environ.get("ROHLIK_BASE_URL")
    if not base:  # the CI self-test still exports ROHLIK_MCP_URL for the local mock
        mcp = os.environ.get("ROHLIK_MCP_URL", "")
        base = mcp[:-4] if mcp.endswith("/mcp") else "https://www.rohlik.cz"
    return base


def run_measure(cfg: dict) -> dict:
    return measure(cfg, base_url())
