"""Change-based alerts vs the previous run: a position that newly went out of stock, or newly
dropped to critical stock (<= low_stock_threshold units). Each change is reported once.
Writes docs/data/alerts.json (history for the dashboard) and docs/data/alerts_latest.json
(the latest run with a ready-made e-mail subject + HTML body for the delivery task)."""
from __future__ import annotations

import json
from datetime import date
from html import escape
from pathlib import Path

import yaml

from .classify import IN, OOS

LOCATION = {"chrastany": "Praha-západ", "praha_liboc": "Praha 6", "horni_pocernice": "Praha 9",
            "brno": "Jihomoravský kraj", "ostrava": "Moravskoslezský kraj"}
PROMO_CZ = {"sale": "AKCE", "multipack": "MULTIBUY", "longtermAction": "TRVALE NÍZKÁ CENA", "expiration": "VÝPRODEJ"}
CONTACT = ("Tan Mai · Retail Media Campaign Lead · POS Media Europe s.r.o. · Budějovická 778/3, 140 00 Praha 4 · "
           "+420 775 413 798 · mai.ngoc.tan@posmedia.cz")

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
                pr = next((x for x in c.get("promos") or [] if x.get("type") != "premium"), None)
                base["promo"] = {"type": pr["type"], "pct": pr.get("pct"), "trigger": pr.get("trigger")} if pr else None
                if c["state"] == OOS and p.get("state") == IN:
                    ev.append({"type": "oos", **base, "restock_date": c.get("restock_date")})
                elif c["state"] == IN and c.get("low") and not (p.get("state") == IN and p.get("low")):
                    ev.append({"type": "low", **base, "units": c.get("units")})
    return ev


def long_term(cfg: dict, cls: dict, events: list[dict]) -> list[dict]:
    """Positions out of stock for at least long_oos_threshold_days (default 3), longest first."""
    min_days = int(cfg.get("long_oos_threshold_days", 3))
    fresh = {(e["sku"], e["zone"]) for e in events if e["type"] == "oos"}
    zn = {z["key"]: z["name"] for z in cfg["zones"]}
    out = []
    for b in cfg["brands"]:
        for s in b["skus"]:
            sid = str(s["id"])
            for z in cfg["zones"]:
                c = cls["positions"][sid][z["key"]]
                d = c.get("days_at_zero") or 0
                if c["state"] == OOS and d >= min_days and (int(sid), z["key"]) not in fresh:
                    pr = next((x for x in c.get("promos") or [] if x.get("type") != "premium"), None)
                    out.append({"type": "long", "sku": int(sid), "product": s.get("short") or s["name"],
                                "zone": z["key"], "zone_name": zn[z["key"]], "days": d,
                                "restock_date": c.get("restock_date"), "url": f"https://www.rohlik.cz/{sid}",
                                "promo": {"type": pr["type"], "pct": pr.get("pct")} if pr else None})
    return sorted(out, key=lambda e: -e["days"])


def _days_cs(n):
    return f"{n} {'den' if n == 1 else 'dny' if 2 <= n <= 4 else 'dní'}"


def _dm(iso: str | None) -> str:
    if not iso:
        return "neuvedeno"
    d = date.fromisoformat(iso[:10])
    return f"{d.day:02d}.{d.month:02d}."


F = "font-family:Arial,Helvetica,sans-serif;"


def _chip(text, fg, bg):
    return (f'<span style="display:inline-block;{F}font-size:11px;font-weight:bold;color:{fg};background-color:{bg};'
            f'padding:3px 9px;border-radius:10px;white-space:nowrap">{text}</span>')


def _badges(e):
    out = ""
    if e.get("promo"):
        t = PROMO_CZ.get(e["promo"]["type"], "AKCE")
        if e["promo"].get("pct"):
            t += f' −{e["promo"]["pct"]} %'
        out += _chip(t, "#ffffff", "#d03b3b") + " "
    for c in e.get("campaigns") or []:
        out += _chip("KAMPAŇ: " + escape(c), "#1C2529", "#F19500") + " "
    return f'<br><span style="line-height:22px">{out}</span>' if out else ""


def _product(e, imgs):
    m = imgs.get(str(e["sku"])) or {}
    img = (f'<img src="{m["img"]}" height="44" alt="{escape(e["product"])}" style="display:block;height:44px;width:auto;'
           f'max-width:52px;margin:0 auto;border:0;font-size:8px;line-height:9px;color:#9aa0a6">') if m.get("img") else ""
    url = m.get("url") or e["url"]
    return (f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse"><tr>'
            f'<td width="52" align="center" style="width:52px;padding:0 10px 0 0;vertical-align:middle">{img}</td>'
            f'<td style="vertical-align:middle;{F}font-size:12px"><a href="{url}" style="color:#1C2529;text-decoration:none;'
            f'font-weight:bold">{escape(e["product"])}</a><br><span style="color:#6b737b;font-size:11px">SKU {e["sku"]}</span>{_badges(e)}'
            f'</td></tr></table>')


def _pct(v):
    return "–" if v is None else f"{v:.1f}".replace(".", ",") + " %"


def _context(ctx):
    t = ctx.get("total")
    if not t:
        return ""
    prev = ctx.get("prev_avail")
    pv = f" (při předchozím měření {_pct(prev)})" if prev is not None else ""
    word = "pozice" if t["oos"] in (2, 3, 4) else ("pozice" if t["oos"] == 1 else "pozic")
    return (f'<div style="{F}font-size:13px;color:#33393f;margin-top:12px;padding:10px 12px;background-color:#f4f5f6">'
            f'Celkový stav: vyprodáno <b>{t["oos"]} z {t["listed"]}</b> listovaných {word} · dostupnost '
            f'<b>{_pct(t["avail"])}</b>{pv}</div>')


def render_email(cfg: dict, events: list[dict], when_str: str, imgs: dict | None = None,
                 ctx: dict | None = None) -> tuple[str, str]:
    imgs, ctx = imgs or {}, ctx or {}
    hot = lambda e: 0 if (e.get("promo") or e.get("campaigns")) else 1     # promo / campaign first
    oos = sorted([e for e in events if e["type"] == "oos"], key=hot)
    low = sorted([e for e in events if e["type"] == "low"], key=lambda e: (hot(e), e.get("units") or 0))
    parts = []
    if oos:
        parts.append(f"{len(oos)}× vyprodáno")
    if low:
        parts.append(f"{len(low)}× kritická zásoba")
    weekly_only = not parts and bool(ctx.get("long"))
    if weekly_only:
        subject = (f"{cfg['client']} · Rohlik.cz týdenní přehled — {len(ctx['long'])}× dlouhodobě vyprodáno "
                   f"({when_str})")
    else:
        if ctx.get("long"):
            parts.append(f"{len(ctx['long'])}× dlouhodobě vyprodáno")
        subject = f"{cfg['client']} · Rohlik.cz upozornění — {', '.join(parts)} ({when_str})"

    def tile(n, label, col, bg):
        return (f'<td width="50%" style="padding:0 6px 0 0"><table width="100%" cellspacing="0" cellpadding="0" '
                f'style="border-collapse:collapse;background-color:{bg};border-left:4px solid {col}"><tr><td style="padding:12px 14px;{F}">'
                f'<div style="font-size:26px;font-weight:bold;color:{col};line-height:1">{n}</div>'
                f'<div style="font-size:12px;color:#33393f;margin-top:4px">{label}</div></td></tr></table></td>')

    def wh(e):
        loc = LOCATION.get(e["zone"])
        return escape(e["zone_name"]) + (f'<br><span style="color:#6b737b;font-size:11px">{loc}</span>' if loc else "")

    def gtable(head, events, cells):
        """One block per product: the product cell spans one row per warehouse."""
        groups = {}
        for e in events:
            groups.setdefault(e["sku"], []).append(e)
        td = f'{F}font-size:12px;color:#33393f;padding:7px 8px;vertical-align:middle'
        h = ('<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse"><tr>'
             + "".join(f'<th align="left" style="{F}font-size:11px;color:#515A63;background-color:#eef0f2;padding:7px 8px;'
                       f'border-bottom:2px solid #F19500">{x}</th>' for x in head) + "</tr>")
        for evs in groups.values():
            n = len(evs)
            for i, e in enumerate(evs):
                last = "border-bottom:1px solid #e6e8ea;" if i == n - 1 else ""
                h += "<tr>"
                if i == 0:
                    h += f'<td rowspan="{n}" style="{td};border-bottom:1px solid #e6e8ea">{_product(e, imgs)}</td>'
                h += "".join(f'<td style="{td};{last}">{c}</td>' for c in [wh(e)] + cells(e)) + "</tr>"
        return h + "</table>"

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
         f'<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%;max-width:680px;background-color:#ffffff">'
         f'<tr><td style="padding:16px 22px;background-color:#ffffff"><img src="{DASHBOARD}logo.png" width="190" alt="POS Media" '
         f'style="display:block;width:190px;height:auto;border:0;{F}font-size:16px;font-weight:bold;color:#515A63"></td></tr>'
         f'<tr><td style="background-color:#515A63;padding:18px 22px;{F}">'
         f'<div style="font-size:11px;font-weight:bold;letter-spacing:1.5px;color:#1C2529;background-color:#F19500;'
         f'display:inline-block;padding:3px 8px">{"TÝDENNÍ PŘEHLED" if weekly_only else "UPOZORNĚNÍ NA ZÁSOBY"}</div>'
         f'<div style="font-size:22px;font-weight:bold;color:#ffffff;margin-top:8px">{escape(cfg["client"])} · Rohlik.cz</div>'
         f'<div style="font-size:13px;color:#d9dde1;margin-top:4px">Změřeno {when_str} · {"dlouhodobé výpadky" if weekly_only else "změny oproti předchozímu měření"}</div></td></tr>'
         f'<tr><td style="height:4px;background-color:#F19500;font-size:1px;line-height:4px">&nbsp;</td></tr>'
         f'<tr><td style="padding:18px 22px 6px 22px">'
         + ('' if weekly_only else '<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse"><tr>'
            + tile(len(oos), "nově vyprodáno", "#d03b3b", "#fdecec")
            + tile(len(low), "kritická zásoba (5 ks a méně)", "#b57d00", "#fff6e6") + '</tr></table>')
         + _context(ctx) + '</td></tr><tr><td style="padding:0 22px 8px 22px">')
    restock = lambda e: (_chip(_dm(e.get("restock_date")), "#a86400", "#fff1d9") if e.get("restock_date")
                         else _chip("neuvedeno", "#6b737b", "#eef0f2"))
    if oos:
        h += h2("Nově vyprodané")
        h += gtable(["Produkt", "Sklad", "Naskladnění (odhad Rohlíku)"], oos, lambda e: [restock(e)])
    if low:
        h += h2("Kritická zásoba")
        h += gtable(["Produkt", "Sklad", "Zbývá"], low,
                    lambda e: [_chip(f'{e.get("units")} ks', "#ffffff",
                                     "#d03b3b" if (e.get("units") or 0) <= 2 else "#F19500")])
    longs = ctx.get("long") or []
    if longs:
        cap = int(ctx.get("long_cap", 8))
        shown, rest = longs[:cap], len(longs) - cap

        def days_cell(e):
            c = _chip(_days_cs(e["days"]), "#ffffff", "#d03b3b" if e["days"] >= 7 else "#F19500")
            if e["days"] >= 14:
                c += "<br>" + _chip("riziko delistingu", "#b02a2a", "#fdecec")
            return c
        h += h2(f"Dlouhodobě vyprodané ({ctx.get('long_min', 3)}+ dny)")
        h += gtable(["Produkt", "Sklad", "Na nule", "Naskladnění (odhad Rohlíku)"], shown,
                    lambda e: [days_cell(e), restock(e)])
        if rest > 0:
            word = "pozice" if 2 <= rest <= 4 else ("pozici" if rest == 1 else "pozic")
            h += (f'<p style="{F}font-size:12px;color:#33393f;margin:10px 0 0 0">… a ještě <b>{rest}</b> {word} '
                  f'najdete na <a href="{DASHBOARD}" style="color:#F19500;font-weight:bold">dashboardu</a>.</p>')
    h += (f'</td></tr><tr><td align="center" style="padding:20px 22px 24px 22px">'
          f'<a href="{DASHBOARD}" style="display:inline-block;{F}font-size:13px;font-weight:bold;color:#ffffff;'
          f'background-color:#F19500;text-decoration:none;padding:11px 22px;border-radius:3px">Otevřít dashboard dostupnosti</a></td></tr>'
          f'<tr><td style="padding:14px 22px 6px 22px;border-top:1px solid #e4e7ea;{F}font-size:12px;color:#33393f">'
          f'<b style="color:#515A63">Dotazy k upozornění:</b> {CONTACT}<br>'
          f'<span style="color:#6b737b">Úpravy nebo odhlášení: stačí odpovědět na tento e-mail.</span></td></tr>'
          f'<tr><td style="padding:6px 22px 16px 22px;{F}font-size:10px;color:#6b737b">'
          f'POS Media Czech Republic · automatické upozornění na dostupnost na Rohlik.cz · každá změna se hlásí '
          f'jen jednou · zásoby se měří 2× denně · data naskladnění jsou odhady Rohlíku</td></tr></table></td></tr></table>')
    return subject, h


def publish(root: Path, cfg: dict, events: list[dict], now_iso: str, when_str: str,
            ctx: dict | None = None) -> None:
    cf = root / "campaigns.yml"
    camps = ((yaml.safe_load(cf.read_text(encoding="utf-8")) or {}).get("campaigns") or []) if cf.exists() else []
    day = now_iso[:10]
    for e in events:
        e["campaigns"] = [c["name"] for c in camps
                          if str(e["sku"]) in map(str, c.get("skus", [])) and str(c["start"]) <= day <= str(c["end"])]
    d = root / "docs" / "data"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "alerts.json"
    hist = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"runs": []}
    if events:
        hist["runs"].append({"run_at": now_iso, "events": events})
        hist["runs"] = hist["runs"][-KEEP_RUNS:]
    hist["last_run"] = now_iso
    f.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    ctx = ctx or {}
    weekly = bool(ctx.get("weekly")) and bool(ctx.get("long"))
    send = bool(events) or weekly
    latest = {"run_at": now_iso, "events_count": (len(events) or len(ctx.get("long") or [])) if send else 0,
              "kind": "alert" if events else ("weekly" if weekly else "none"), "subject": "", "html": ""}
    if send:
        pf = d / "products.json"
        imgs = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
        latest["subject"], latest["html"] = render_email(cfg, events, when_str, imgs, ctx)
    (d / "alerts_latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=1), encoding="utf-8")
