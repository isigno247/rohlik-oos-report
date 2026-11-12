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
        return "neuvedeno"
    d = date.fromisoformat(iso[:10])
    return f"{d.day:02d}.{d.month:02d}."


F = "font-family:Arial,Helvetica,sans-serif;"


def _chip(text, fg, bg):
    return (f'<span style="display:inline-block;{F}font-size:11px;font-weight:bold;color:{fg};background-color:{bg};'
            f'padding:3px 9px;border-radius:10px;white-space:nowrap">{text}</span>')


def _product(e, imgs):
    m = imgs.get(str(e["sku"])) or {}
    img = (f'<img src="{m["img"]}" width="40" height="40" alt="" style="display:block;width:40px;height:40px;'
           f'border:1px solid #e4e7ea;border-radius:4px;background:#ffffff">') if m.get("img") else ""
    url = m.get("url") or e["url"]
    return (f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse"><tr>'
            f'<td style="padding:0 10px 0 0;vertical-align:middle">{img}</td>'
            f'<td style="vertical-align:middle;{F}font-size:12px"><a href="{url}" style="color:#1C2529;text-decoration:none;'
            f'font-weight:bold">{escape(e["product"])}</a><br><span style="color:#6b737b;font-size:11px">SKU {e["sku"]}</span>'
            f'</td></tr></table>')


def render_email(cfg: dict, events: list[dict], when_str: str, imgs: dict | None = None) -> tuple[str, str]:
    imgs = imgs or {}
    oos = [e for e in events if e["type"] == "oos"]
    low = sorted([e for e in events if e["type"] == "low"], key=lambda e: e.get("units") or 0)
    parts = []
    if oos:
        parts.append(f"{len(oos)}× vyprodáno")
    if low:
        parts.append(f"{len(low)}× kritická zásoba")
    subject = f"{cfg['client']} · Rohlik.cz upozornění — {', '.join(parts)} ({when_str})"

    def tile(n, label, col, bg):
        return (f'<td width="50%" style="padding:0 6px 0 0"><table width="100%" cellspacing="0" cellpadding="0" '
                f'style="border-collapse:collapse;background-color:{bg};border-left:4px solid {col}"><tr><td style="padding:12px 14px;{F}">'
                f'<div style="font-size:26px;font-weight:bold;color:{col};line-height:1">{n}</div>'
                f'<div style="font-size:12px;color:#33393f;margin-top:4px">{label}</div></td></tr></table></td>')

    def h2(t):
        return (f'<div style="{F}font-size:17px;font-weight:bold;color:#515A63;border-bottom:2px solid #F19500;'
                f'padding:22px 0 5px 0;margin-bottom:8px">{t}</div>')

    def table(head, rows):
        h = ('<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse"><tr>'
             + "".join(f'<th align="left" style="{F}font-size:11px;color:#515A63;background-color:#eef0f2;padding:7px 8px;'
                       f'border-bottom:2px solid #F19500">{x}</th>' for x in head) + "</tr>")
        for r in rows:
            h += "<tr>" + "".join(f'<td style="{F}font-size:12px;color:#33393f;padding:8px;border-bottom:1px solid #e6e8ea;'
                                  f'vertical-align:middle">{x}</td>' for x in r) + "</tr>"
        return h + "</table>"

    h = (f'<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;background-color:#f4f5f6">'
         f'<tr><td align="center" style="padding:20px 10px">'
         f'<table width="680" cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:680px;max-width:680px;background-color:#ffffff">'
         f'<tr><td style="padding:16px 22px;background-color:#ffffff"><img src="{DASHBOARD}logo.png" width="190" alt="POS Media" '
         f'style="display:block;width:190px;height:auto;border:0"></td></tr>'
         f'<tr><td style="background-color:#515A63;padding:18px 22px;{F}">'
         f'<div style="font-size:11px;font-weight:bold;letter-spacing:1.5px;color:#1C2529;background-color:#F19500;'
         f'display:inline-block;padding:3px 8px">UPOZORNĚNÍ NA ZÁSOBY</div>'
         f'<div style="font-size:22px;font-weight:bold;color:#ffffff;margin-top:8px">{escape(cfg["client"])} · Rohlik.cz</div>'
         f'<div style="font-size:13px;color:#d9dde1;margin-top:4px">Změřeno {when_str} · změny oproti předchozímu měření</div></td></tr>'
         f'<tr><td style="height:4px;background-color:#F19500;font-size:1px;line-height:4px">&nbsp;</td></tr>'
         f'<tr><td style="padding:18px 22px 6px 22px"><table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse"><tr>'
         + tile(len(oos), "nově vyprodáno", "#d03b3b", "#fdecec")
         + tile(len(low), "kritická zásoba (5 ks a méně)", "#b57d00", "#fff6e6")
         + '</tr></table></td></tr><tr><td style="padding:0 22px 8px 22px">')
    if oos:
        h += h2("Nově vyprodané")
        h += table(["Produkt", "Sklad", "Očekávané naskladnění"],
                   [[_product(e, imgs), escape(e["zone_name"]),
                     _chip(_dm(e.get("restock_date")), "#a86400", "#fff1d9") if e.get("restock_date")
                     else _chip("neuvedeno", "#6b737b", "#eef0f2")] for e in oos])
    if low:
        h += h2("Kritická zásoba")
        h += table(["Produkt", "Sklad", "Zbývá"],
                   [[_product(e, imgs), escape(e["zone_name"]),
                     _chip(f'{e.get("units")} ks', "#ffffff",
                           "#d03b3b" if (e.get("units") or 0) <= 2 else "#F19500")] for e in low])
    h += (f'</td></tr><tr><td align="center" style="padding:20px 22px 24px 22px">'
          f'<a href="{DASHBOARD}" style="display:inline-block;{F}font-size:13px;font-weight:bold;color:#ffffff;'
          f'background-color:#F19500;text-decoration:none;padding:11px 22px;border-radius:3px">Otevřít dashboard dostupnosti</a></td></tr>'
          f'<tr><td style="padding:12px 22px 16px 22px;border-top:1px solid #e4e7ea;{F}font-size:10px;color:#6b737b">'
          f'POS Media Czech Republic · automatické upozornění na dostupnost na Rohlik.cz · každá změna se hlásí '
          f'jen jednou · zásoby se měří 2× denně</td></tr></table></td></tr></table>')
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
        pf = d / "products.json"
        imgs = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
        latest["subject"], latest["html"] = render_email(cfg, events, when_str, imgs)
    (d / "alerts_latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=1), encoding="utf-8")
