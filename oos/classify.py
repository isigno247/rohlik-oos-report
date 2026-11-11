"""Three-state classification against the stored footprint, plus counts, notes and state."""
from __future__ import annotations

import re
from datetime import date, timedelta

IN, OOS, NL, UNV, FAIL = "in_stock", "out_of_stock", "not_listed", "unverified", "failed"


def fmt_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{int(d)}. {int(m)}. {y}"


def restock_text(reason: str | None, iso: str | None, today: date) -> str | None:
    """'Vyprodáno. Očekáváme ve st.' + 2026-11-11 -> 'Očekáváme ve st. (odhad 11. 11. 2026)'."""
    txt = re.sub(r"^\s*Vyprodáno\.?\s*", "", reason or "").strip()
    if txt and "dnes" in txt:
        return f"{txt.rstrip('.')} ({fmt_date(today.isoformat())})"
    if txt and iso:
        return f"{txt} (odhad {fmt_date(iso)})"
    if iso:
        return f"odhad {fmt_date(iso)}"
    return txt or None


WD = {"po": 0, "út": 1, "ut": 1, "st": 2, "čt": 3, "ct": 3, "pá": 4, "pa": 4, "so": 5, "ne": 6}


def restock_iso(reason: str | None, iso: str | None, today: date) -> str | None:
    """Rohlik's restock hint as a date: explicit date, 'dnes', 'zítra' or a weekday ('ve čt.')."""
    if iso:
        return str(iso)[:10]
    t = (reason or "").lower()
    if "dnes" in t:
        return today.isoformat()
    if "zítra" in t or "zitra" in t:
        return (today + timedelta(days=1)).isoformat()
    m = re.search(r"\bve?\s+(po|út|ut|st|čt|ct|pá|pa|so|ne)\.", t)
    if m:
        delta = (WD[m.group(1)] - today.weekday()) % 7 or 7
        return (today + timedelta(days=delta)).isoformat()
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.(?:\s*(\d{4}))?", t)
    if m:
        y = int(m.group(3) or today.year)
        return date(y, int(m.group(2)), int(m.group(1))).isoformat()
    return None


def footprint_of(cfg: dict) -> dict:
    fp = {}
    for b in cfg["brands"]:
        for s in b["skus"]:
            nl, unv = set(s.get("not_listed_in", [])), set(s.get("unverified_in", []))
            fp[str(s["id"])] = {z["key"]: (None if z["key"] in unv else z["key"] not in nl)
                                for z in cfg["zones"]}
    return fp


def classify(cfg: dict, meas: dict, prev_state: dict | None, today: date) -> dict:
    low = int(cfg["low_stock_threshold"])
    fp = footprint_of(cfg)
    prev_pos = (prev_state or {}).get("positions", {})
    positions = {}
    for b in cfg["brands"]:
        for s in b["skus"]:
            sid = str(s["id"])
            positions[sid] = {}
            for z in cfg["zones"]:
                k = z["key"]
                zr = meas["zones"].get(k, {"status": "FAILED"})
                carried = fp[sid][k]
                p = {"state": None, "units": None, "price": None, "sale_price": None,
                     "sale_id": None, "restock": None, "restock_date": None, "zero_since": None, "low": False}
                if zr["status"] != "OK":
                    p["state"] = FAIL
                elif carried is None:
                    p["state"] = UNV
                elif carried is False:
                    p["state"] = NL          # no API call was made; no price ever shown
                else:
                    m = zr["products"][int(sid)]
                    p.update(units=m["units"], price=m["price"], sale_price=m["sale_price"],
                             sale_id=m["sale_id"])
                    if m["units"] > 0:
                        p["state"] = IN
                        p["low"] = m["units"] <= low
                    else:
                        p["state"] = OOS
                        p["restock"] = restock_text(m["reason"], m["restock_date"], today)
                        p["restock_date"] = restock_iso(m["reason"], m["restock_date"], today)
                        before = prev_pos.get(sid, {}).get(k, {})
                        p["zero_since"] = (before.get("zero_since") if before.get("state") == OOS
                                           else None) or today.isoformat()
                positions[sid][k] = p
    return {"footprint": fp, "positions": positions}


def counts(cfg, positions, sku_ids=None, zone_keys=None):
    zone_keys = zone_keys or [z["key"] for z in cfg["zones"]]
    sku_ids = sku_ids or list(positions)
    c = {IN: 0, OOS: 0, NL: 0, UNV: 0, FAIL: 0, "low": 0}
    for sid in sku_ids:
        for k in zone_keys:
            p = positions[sid][k]
            c[p["state"]] += 1
            c["low"] += 1 if p["low"] else 0
    c["listed"] = c[IN] + c[OOS]
    c["avail"] = (c[IN] / c["listed"] * 100) if c["listed"] else None
    return c


def build_notes(cfg, cls, prev_state, today, failed_zone_names):
    pos, fp = cls["positions"], cls["footprint"]
    names = {str(s["id"]): s["name"] for b in cfg["brands"] for s in b["skus"]}
    zname = {z["key"]: z["name"] for z in cfg["zones"]}
    thr = int(cfg["stale_oos_threshold_days"])
    notes = {k: [] for k in ["new", "stale", "changes", "related", "unverified", "anomalies"]}

    for sid, zs in pos.items():
        for k, p in zs.items():
            if p["state"] == OOS and p["zero_since"]:
                days = (today - date.fromisoformat(p["zero_since"])).days
                if days > thr:
                    notes["stale"].append(f"{sid} {names[sid]} — {zname[k]}: out of stock since "
                                          f"{fmt_date(p['zero_since'])} ({days} days). The footprint "
                                          f"may be stale; worth a baseline refresh for this position.")
            if p["state"] == UNV:
                notes["unverified"].append(f"{sid} {names[sid]} — {zname[k]}: no footprint entry yet.")

    prev_fp = (prev_state or {}).get("footprint")
    if prev_fp:
        for sid, zs in fp.items():
            for k, carried in zs.items():
                before = prev_fp.get(sid, {}).get(k)
                if sid not in prev_fp:
                    if carried:
                        notes["changes"].append(f"{sid} {names[sid]} — {zname[k]}: added to the tracked footprint.")
                elif before is not None and carried is not None and before != carried:
                    notes["changes"].append(f"{sid} {names[sid]} — {zname[k]}: "
                                            f"{'not listed → listed' if carried else 'listed → not listed'} "
                                            f"(footprint update).")

    for b in cfg["brands"]:
        for r in b.get("related_name_matches") or []:
            notes["related"].append(str(r))

    prev_in = (prev_state or {}).get("zone_in_stock", {})
    for z in cfg["zones"]:
        k = z["key"]
        if k in prev_in and all(pos[s][k]["state"] != FAIL for s in pos):
            now_in = sum(1 for s in pos if pos[s][k]["state"] == IN)
            if prev_in[k] - now_in > 10:
                notes["anomalies"].append(f"{z['name']}: in-stock positions dropped from {prev_in[k]} "
                                          f"to {now_in} since the previous run (suspected, not confirmed).")
    for n in failed_zone_names:
        notes["anomalies"].append(f"{n}: the zone switch could not be verified, so this warehouse "
                                  f"was not measured and is excluded from every count.")
    return notes


def new_state(cfg, cls, meas, today, measured_at, checks):
    pos = cls["positions"]
    return {
        "date": today.isoformat(),
        "measured_at": measured_at,
        "footprint": cls["footprint"],
        "footprint_established": cfg["footprint"]["established"],
        "positions": pos,
        "zone_in_stock": {z["key"]: sum(1 for s in pos if pos[s][z["key"]]["state"] == IN)
                          for z in cfg["zones"] if meas["zones"].get(z["key"], {}).get("status") == "OK"},
        "verification": {k: {"status": v["status"], "verification": v.get("verification"),
                             "canary": v.get("canary")} for k, v in meas["zones"].items()},
        "restored": meas["restored"],
        "self_checks": checks,
    }
