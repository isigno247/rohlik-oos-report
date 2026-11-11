"""Merge Outlook-era history with daily GitHub measurements into docs/data/polaris.json."""
import json
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from parse_outlook_history import parse  # noqa: E402

cfg = yaml.safe_load((ROOT / "config" / "polaris.yml").read_text(encoding="utf-8"))
zones = [z["key"] for z in cfg["zones"]]
days = {}

raw = ROOT / "history" / "polaris_outlook_reports_raw.json"
if raw.exists():
    for date, text in json.loads(raw.read_text(encoding="utf-8")).items():
        d = parse(date, text)
        if d:
            days[date] = d

for f in sorted((ROOT / "state" / "history" / "polaris").glob("*.json")):
    st = json.loads(f.read_text(encoding="utf-8"))
    pos = st["positions"]
    zs, oos, low, grid = {}, [], [], {}
    for sid, pz in pos.items():
        grid[sid] = {k: [pz[k]["state"][:1].upper() if pz[k]["state"] != "not_listed" else "N",
                         pz[k].get("units"), pz[k].get("price"), pz[k].get("sale_price"),
                         [[x["type"], x["price"], x["orig"], x["pct"], x["trigger"], x["till"]]
                          for x in pz[k].get("promos") or []]] for k in zones}
    for k in zones:
        c = {"listed": 0, "in_stock": 0, "oos": 0, "not_listed": 0}
        for sid, pz in pos.items():
            p = pz[k]
            if p["state"] == "in_stock":
                c["listed"] += 1; c["in_stock"] += 1
                if p.get("low"):
                    low.append({"sku": int(sid), "zone": k, "units": p["units"]})
            elif p["state"] == "out_of_stock":
                c["listed"] += 1; c["oos"] += 1
                oos.append({"sku": int(sid), "zone": k, "restock": p.get("restock_date"),
                            "days_at_zero": p.get("days_at_zero"),
                            "promo": any(x["type"] != "premium" for x in p.get("promos") or [])
                            if "promos" in p else p.get("sale_price") is not None})
            elif p["state"] == "not_listed":
                c["not_listed"] += 1
        zs[k] = c
    tot = {k: sum(z[k] for z in zs.values()) for k in ("listed", "in_stock", "oos", "not_listed")}
    tot["availability"] = round(tot["in_stock"] / tot["listed"] * 100, 1) if tot["listed"] else None
    days[st["date"]] = {"date": st["date"], "measured": st.get("measured_at", "")[:5], "source": "github",
                        "total": tot, "zones": zs, "oos": oos, "low": low, "pos": grid}

out = {
    "client": "Polaris",
    "zones": [{"key": z["key"], "name": z["name"]} for z in cfg["zones"]],
    "skus": {str(s["id"]): s.get("short") or s["name"].replace("Polaris ", "") for b in cfg["brands"] for s in b["skus"]},
    "nl": {str(s["id"]): s.get("not_listed_in", []) for b in cfg["brands"] for s in b["skus"]},
    "campaigns": (yaml.safe_load((ROOT / "campaigns.yml").read_text(encoding="utf-8")) or {}).get("campaigns") or []
    if (ROOT / "campaigns.yml").exists() else [],
    "days": [days[d] for d in sorted(days)],
}
dest = ROOT / "docs" / "data" / "polaris.json"
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
shutil.copy(ROOT / "assets" / "pos-media-logo.png", ROOT / "docs" / "logo.png")
print(f"dashboard data: {len(out['days'])} days -> {dest.relative_to(ROOT)}")
