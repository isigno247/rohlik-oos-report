"""Local stand-in for rohlik.cz's public web endpoints (address picker + stock + prices), fed by
data recorded live on 2026-11-10. Same response shapes as the real site; never talks to Rohlik.
Kept reachable as tests/mock_rohlik_mcp.py so the CI workflow needs no change.

MOCK_BREAK_ZONE=<warehouse_id>: stock for that warehouse is reported from a different warehouse,
so the zone must come out FAILED."""
import json, os, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

FX = Path(__file__).parent / "fixtures"
# address keyword -> (warehouseId, zoneId, fixture)
ZONES = {"Chrášťany": (8801, 1, "chrastany"), "Libocká": (8791, 2, "praha_liboc"),
         "Náchodská": (8795, 3, "horni_pocernice"), "Brno": (8793, 157465, "brno"),
         "Ostrava": (8797, 5, "ostrava")}
DATA = {wh: json.loads((FX / f"{fx}.json").read_text(encoding="utf-8")) for wh, _, fx in ZONES.values()}
BY_ZONE = {z: wh for wh, z, _ in ZONES.values()}
SESS = {}          # sid -> warehouseId
BREAK = int(os.environ.get("MOCK_BREAK_ZONE", "0"))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _sid(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "sid":
                return v
        return None

    def _send(self, code, obj=None, sid=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if sid:
            self.send_header("Set-Cookie", f"sid={sid}; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _path(self):
        u = urlparse(self.path)
        p = u.path[4:] if u.path.startswith("/mcp") else u.path
        return p, parse_qs(u.query)

    def do_GET(self):
        p, q = self._path()
        sid = self._sid() or str(uuid.uuid4())
        if p in ("/", ""):
            return self._send(200, {}, sid)
        if p == "/api/v3/address/autocomplete":
            text = q.get("query", [""])[0]
            return self._send(200, [{"suggestText": text, "city": text.split()[-1], "serviceType": "SEZNAM"}], sid)
        wh = SESS.get(sid, 8801)
        ids = [int(x) for x in q.get("products", [])]
        zone = DATA.get(wh, {})
        if p == "/api/v1/products/stock":
            rows = []
            for pid in ids:
                rec = zone.get(str(pid))
                if rec is None:
                    continue   # real API omits products the warehouse does not carry
                units = rec[0]
                rows.append({"productId": pid, "warehouseId": (99999 if wh == BREAK else wh),
                             "maxBasketAmount": units, "inStock": units > 0,
                             "unavailabilityReason": rec[2] if len(rec) > 2 and units == 0 else None,
                             "expectedReplenishment": rec[3] if len(rec) > 3 else None, "sales": []})
            return self._send(200, rows, sid)
        if p == "/api/v1/products/prices":
            rows = [{"productId": pid, "price": {"amount": zone[str(pid)][1], "currency": "CZK"}, "sales": []}
                    for pid in ids if str(pid) in zone]
            return self._send(200, rows, sid)
        return self._send(404, {"status": 404}, sid)

    def do_POST(self):
        p, _ = self._path()
        sid = self._sid() or str(uuid.uuid4())
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if p == "/api/v4/address/autocomplete/confirm":
            text = body.get("suggestText", "")
            for kw, (wh, zone, _) in ZONES.items():
                if kw in text:
                    return self._send(200, {"id": 1000 + zone, "warehouseId": wh, "zoneId": zone, "isValid": True}, sid)
            return self._send(400, {"status": 400}, sid)
        if p == "/services/frontend-service/delivery-address/check":
            if body.get("zoneId") in BY_ZONE:
                SESS[sid] = BY_ZONE[body["zoneId"]]
            return self._send(200, {"status": 200, "data": {}}, sid)
        return self._send(404, {"status": 404}, sid)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("MOCK_PORT", "8765"))), H).serve_forever()
