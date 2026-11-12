"""Change-based alerts vs the previous run: a position that newly went out of stock, or newly
dropped to critical stock (<= low_stock_threshold units). Each change is reported once.
Writes docs/data/alerts.json (history for the dashboard) and docs/data/alerts_latest.json
(the latest run with a ready-made e-mail subject + HTML body for the delivery task)."""
from __future__ import annotations

import json
from datetime import date
from html import escape
from pathlib import Path

from .classify import IN, OOS

KEEP_RUNS = 120
DASHBOARD = "https://isigno247.github.io/rohlik-oos-report/"


def detect(cfg: dict, cls: dict, prev: dict | None) -> list[dict]:
    if not prev or not prev.get("positions"):
        return []                                # first run: nothing to compare with
    pp = prev["positions"]
    zn = {z["key"]: z["name"] for z in cfg["zones"]}
    ev = []
    for b in cfg["brands"]:
        for s in b["skus"]:
            sid = str(s["id"])
            short = s.get("short") or s["name"]
            for z in cfg["zones"]:
                k = z["key"]
                c = cls["positions"][sid][k]
                p = (pp.get(sid) or {}).get(k) or {}
                base = {"sku": int(sid), "product": short, "zone": k, "zone_name": zn[k],
                        "price": c.get("price"), "url": f"https://www.rohlik.cz/{sid}"}
                if c["state"] == OOS and p.get("state") == IN:
                    ev.append({"type": "oos", **base, "restock_date": c.get("restock_date")})
                elif c["state"] == IN and c.get("low") and not (p.get("state") == IN and p.get("low")):
                    ev.append({"type": "low", **base, "units": c.get("units")})
    return ev


def _dm(iso: str | None) -> str:
    if not iso:
        return "not given"
    d = date.fromisoformat(iso[:10])
    return f"{d.day:02d}.{d.month:02d}."


def render_email(cfg: dict, events: list[dict], when_str: str) -> tuple[str, str]:
    oos = [e for e in events if e["type"] == "oos"]
    low = sorted([e for e in events if e["type"] == "low"], key=lambda e: e.get("units") or 0)
    parts = []
    if oos:
        parts.append(f"{len(oos)} out of stock")
    if low:
        parts.append(f"{len(low)} critical stock")
    subject = f"{cfg['client']} · Rohlik.cz alert — {', '.join(parts)} ({when_str})"
    th = 'style="text-align:left;background:#eef0f2;color:#515A63;padding:6px;border-bottom:2px solid #F19500"'
    td = 'style="padding:6px;border-bottom:1px solid #e6e8ea"'

    def table(title, color, head, rows):
        h = (f'<div style="font-size:15px;font-weight:bold;color:{color};margin:18px 0 6px 0">{title}</div>'
             '<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;font-size:12px;color:#33393f">'
             '<tr>' + "".join(f"<th {th}>{x}</th>" for x in head) + "</tr>")
        for r in rows:
            h += "<tr>" + "".join(f"<td {td}>{x}</td>" for x in r) + "</tr>"
        return h + "</table>"

    h = ('<div style="font-family:Arial,Helvetica,sans-serif;max-width:720px">'
         '<div style="background-color:#515A63;padding:14px 18px"><div style="font-size:18px;font-weight:bold;color:#ffffff">'
         f'{escape(cfg["client"])} · Rohlik.cz stock alert</div><div style="font-size:12px;color:#ffffff;margin-top:3px">'
         f'Measured {when_str} · changes since the previous measurement</div></div>'
         '<div style="height:4px;background-color:#F19500;font-size:1px;line-height:4px">&nbsp;</div>'
         '<div style="padding:4px 18px 18px 18px">')
    if oos:
        h += table("Newly out of stock", "#d03b3b", ["SKU", "Product", "Warehouse", "Expected restock"],
                   [[e["sku"], f'<a href="{e["url"]}" style="color:#33393f">{escape(e["product"])}</a>',
                     escape(e["zone_name"]), _dm(e.get("restock_date"))] for e in oos])
    if low:
        h += table("Critical stock (5 units or fewer)", "#b57d00", ["SKU", "Product", "Warehouse", "Units left"],
                   [[e["sku"], f'<a href="{e["url"]}" style="color:#33393f">{escape(e["product"])}</a>',
                     escape(e["zone_name"]), f'<b style="color:#b57d00">{e.get("units")}</b>'] for e in low])
    h += (f'<p style="font-size:12px;color:#33393f;margin:16px 0 0 0">Full overview: '
          f'<a href="{DASHBOARD}" style="color:#F19500">availability dashboard</a></p>'
          '<p style="font-size:10px;color:#6b737b;margin:12px 0 0 0">POS Media Czech Republic · automated '
          'Rohlik.cz availability alert · each change is reported once</p></div></div>')
    return subject, h


def publish(root: Path, cfg: dict, events: list[dict], now_iso: str, when_str: str) -> None:
    d = root / "docs" / "data"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "alerts.json"
    hist = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"runs": []}
    if events:
        hist["runs"].append({"run_at": now_iso, "events": events})
        hist["runs"] = hist["runs"][-KEEP_RUNS:]
    hist["last_run"] = now_iso
    f.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    latest = {"run_at": now_iso, "events_count": len(events), "subject": "", "html": ""}
    if events:
        latest["subject"], latest["html"] = render_email(cfg, events, when_str)
    (d / "alerts_latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=1), encoding="utf-8")
