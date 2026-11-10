"""Renders the client report. Every style string is copied from the approved Polaris report
(2026-09-25) so the layout stays identical run to run — only the data changes."""
from __future__ import annotations

import base64
from html import escape
from pathlib import Path

from .classify import IN, OOS, NL, UNV, FAIL, counts

F = "font-family:Arial, Helvetica, sans-serif;"
GREEN, RED, AMBER, GRAY, SLATE, ORANGE, INK, MUTED = (
    "#0ca30c", "#d03b3b", "#fab219", "#9aa0a6", "#515A63", "#F19500", "#1C2529", "#8a8f94")
TABLE = f'<table style="width:100%;border-collapse:collapse;margin:8px 0 18px 0;{F}" cellpadding="0" cellspacing="0">'
TH = (f'<th style="padding:8px 10px;text-align:left;{F}font-size:12px;color:{SLATE};'
      f'background-color:#EDEFF0;border-bottom:2px solid {ORANGE};font-weight:bold;">')
TD = f'<td style="padding:8px 10px;border-bottom:1px solid #E4E7EA;{F}font-size:12px;color:{INK};">'
TDB = f'<td style="padding:8px 10px;border-bottom:1px solid #E4E7EA;{F}font-size:12px;color:{INK};font-weight:bold;">'
NONE = f'<div style="{F}font-size:12px;color:{INK};margin:4px 0 18px 0;">None.</div>'


def kc(x) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",") + " Kč"


def pct(x) -> str:
    return "n/a" if x is None else f"{x:.1f}%"


def h2(t):
    return (f'<div style="{F}font-size:17px;font-weight:bold;color:{SLATE};border-left:4px solid {ORANGE};'
            f'padding-left:10px;margin:26px 0 10px 0;">{t}</div>\n')


def h3(t):
    return f'<div style="{F}font-size:14px;font-weight:bold;color:{SLATE};margin:14px 0 4px 0;">{t}</div>\n'


def row(cells, bg=None):
    style = f' style="background-color:{bg};"' if bg else ""
    return f"<tr{style}>" + "".join(cells) + "</tr>\n"


def head(cols):
    return "<tr>" + "".join(f"{TH}{c}</th>" for c in cols) + "</tr>\n"


def tile(value, label, color):
    return (f'<td style="padding:14px 10px;text-align:center;background-color:#F4F5F6;border:1px solid #E4E7EA;">'
            f'<div style="{F}font-size:22px;font-weight:bold;color:{color};">{value}</div>'
            f'<div style="{F}font-size:10px;color:{SLATE};margin-top:4px;">{label}</div></td>\n')


def num(n, color, bold=True):
    if not n:
        return "0"
    return f'<span style="color:{color};{"font-weight:bold;" if bold else ""}">{n}</span>'


def wh_table(cfg, positions, sku_ids, total: bool):
    out = TABLE + "\n" + head(["Warehouse", "Listed", "In stock", "Out of stock", "Not listed", "Availability %"])
    for z in cfg["zones"]:
        c = counts(cfg, positions, sku_ids, [z["key"]])
        if c[FAIL]:
            cells = [f"{TDB}{z['name']}</td>"] + [f'{TD}<span style="color:{GRAY};">FAILED</span></td>'] * 5
        else:
            cells = [f"{TDB}{z['name']}</td>", f"{TD}{c['listed']}</td>", f"{TD}{num(c[IN], GREEN)}</td>",
                     f"{TD}{num(c[OOS], RED)}</td>", f"{TD}{num(c[NL], GRAY, False)}</td>", f"{TD}{pct(c['avail'])}</td>"]
        out += row(cells, "#FFFFFF" if total else None)
    if total:
        c = counts(cfg, positions, sku_ids)
        t = lambda n, col: f'<span style="color:{col};">{n}</span>' if n else "0"
        out += row([f"{TDB}Total</td>", f"{TDB}{c['listed']}</td>", f"{TDB}{t(c[IN], GREEN)}</td>",
                    f"{TDB}{t(c[OOS], RED)}</td>", f"{TDB}{t(c[NL], GRAY)}</td>", f"{TDB}{pct(c['avail'])}</td>"],
                   "#F4F5F6;font-weight:bold")
    return out + "</table>\n"


def price_cell(p, low_thr):
    s = p["state"]
    if s == NL:
        return f'<span style="{F}font-size:12px;color:{GRAY};font-style:italic;">Not listed</span>'
    if s == FAIL:
        return f'<span style="{F}font-size:12px;color:{GRAY};font-style:italic;">Zone failed</span>'
    promo = p["sale_price"] is not None
    shown = p["sale_price"] if promo else p["price"]
    strike = (f' <span style="{F}font-size:10px;color:{MUTED};text-decoration:line-through;">{kc(p["price"])}</span>'
              if promo else "")
    promo_tag = f' <span style="{F}font-size:10px;color:{ORANGE};font-weight:bold;">· ON PROMOTION</span>' if promo else ""
    if s == UNV:
        return f'<span style="{F}font-size:12px;color:{GRAY};font-style:italic;">unverified · {kc(shown)}</span>'
    if s == IN:
        color, label = (AMBER, "Low stock") if p["low"] else (GREEN, "In stock")
        return (f'<span style="{F}font-size:12px;color:{INK};font-weight:bold;">{kc(shown)}</span>{strike}<br>'
                f'<span style="{F}font-size:10px;color:{color};font-weight:bold;">{label} · {p["units"]} ks</span>{promo_tag}')
    restock = escape(p["restock"]) if p["restock"] else "Restock not given"
    return (f'<span style="{F}font-size:12px;color:{MUTED};">{kc(shown)}</span>{strike}<br>'
            f'<span style="{F}font-size:10px;color:{RED};font-weight:bold;">OOS</span> '
            f'<span style="{F}font-size:10px;color:{SLATE};">· {restock}</span>{promo_tag}')


def narrow_note(cfg, positions, sku_ids):
    n = len(sku_ids)
    listed = {z["name"]: counts(cfg, positions, sku_ids, [z["key"]])["listed"] for z in cfg["zones"]
              if not counts(cfg, positions, sku_ids, [z["key"]])[FAIL]}
    narrow = {k: v for k, v in listed.items() if v < n / 2}
    if not narrow:
        return ""
    wide = {k: v for k, v in listed.items() if k not in narrow}
    join = lambda xs: xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]
    nn = list(narrow)
    if len(set(narrow.values())) == 1:
        v = next(iter(narrow.values()))
        subj = f"{join(nn)} {'each carry' if len(nn) > 1 else 'carries'} only {v} of the {n} tracked SKUs"
    else:
        subj = join([f"{k} carries only {v} of the {n} tracked SKUs" for k, v in narrow.items()])
    vs = ""
    if wide:
        groups = {}
        for k, v in wide.items():
            groups.setdefault(v, []).append(k)
        vs = " (versus " + "; ".join(f"{v} of {n} in {join(ks)}" for v, ks in groups.items()) + ")"
    where = "those two zones" if len(nn) == 2 else ("that zone" if len(nn) == 1 else "those zones")
    return (f'<div style="{F}font-size:12px;color:{SLATE};background-color:#FFF6E8;border:1px solid {ORANGE};'
            f'padding:10px 12px;margin-top:8px;"><b>Note:</b> {subj} per the stored footprint{vs}. This is a '
            f'range/listing difference, not a stock issue, and it means the availability % in {where} is '
            f'measured against a much smaller assortment.</div>\n')


def render(cfg, cls, notes, meta, logo_path: Path) -> str:
    pos = cls["positions"]
    zones = cfg["zones"]
    all_ids = list(pos)
    c = counts(cfg, pos)
    zones_ok = [z for z in zones if meta["zone_status"][z["key"]] == "OK"]
    low_thr = int(cfg["low_stock_threshold"])
    logo = base64.b64encode(logo_path.read_bytes()).decode()

    h = f'<div style="{F}max-width:900px;margin:0 auto;background-color:#FFFFFF;">\n'
    h += (f'<div style="background-color:#FFFFFF;padding:18px 24px;text-align:left;border-bottom:1px solid #E4E7EA;">'
          f'<img src="data:image/png;base64,{logo}" alt="POS Media" width="209" height="48" '
          f'style="display:block;border:0;height:48px;width:auto;"></div>\n')
    h += (f'<div style="background-color:{SLATE};padding:18px 24px;"><div style="{F}font-size:22px;font-weight:bold;'
          f'color:#FFFFFF;">{escape(cfg["report_title"])}</div><div style="{F}font-size:14px;color:#FFFFFF;'
          f'margin-top:6px;">{meta["date_str"]} — measured {meta["time_str"]}</div></div>\n')
    h += f'<div style="height:4px;background-color:{ORANGE};line-height:4px;font-size:0;">&nbsp;</div>\n'
    h += '<div style="padding:22px 24px;">\n'

    # How to read
    h += h2("How to read this report")
    unv_line = (f'<div style="margin-top:6px;"><span style="color:{GRAY};font-weight:bold;font-style:italic;">Unverified</span>'
                f' — no footprint entry yet for this SKU in this warehouse; excluded from all counts.</div>') if c[UNV] else ""
    h += (f'<div style="background-color:#F4F5F6;border:1px solid #E4E7EA;padding:14px 16px;{F}font-size:12px;color:{INK};line-height:1.6;">'
          f'<div><span style="color:{GREEN};font-weight:bold;">In stock</span> — the warehouse carries the product and has units today.</div>'
          f'<div style="margin-top:6px;"><span style="color:{RED};font-weight:bold;">Out of stock</span> — the warehouse is known to carry this product (per the stored footprint), and it has zero units today. Where Rohlik gives a restock estimate, it is shown alongside as a fact.</div>'
          f'<div style="margin-top:6px;"><span style="color:{GRAY};font-weight:bold;">Not listed</span> — the warehouse does not carry this product at all, per the stored footprint. It is a range/listing matter, not a stock-out.</div>'
          f'{unv_line}</div>\n')

    # Overview
    h += h2("Overview")
    h += (f'<div style="{F}font-size:12px;color:{SLATE};margin-bottom:10px;">Counts below are <b>positions</b> '
          f'(SKU &times; warehouse combinations across {len(zones)} zones), not unique products. {len(all_ids)} SKUs tracked.</div>\n')
    h += TABLE + "\n<tr>\n"
    h += tile(len(all_ids), "SKUs tracked", SLATE) + tile(c["listed"], "Listed positions", SLATE)
    h += tile(c[IN], "In stock", GREEN) + tile(c[OOS], "Out of stock", RED)
    h += "</tr><tr>\n"
    h += tile(c[NL], "Not listed", GRAY) + tile(c["low"], f"Low stock (≤{low_thr} ks)", AMBER)
    h += tile(pct(c["avail"]), "Availability %", ORANGE) + tile(f"{len(zones_ok)}/{len(zones)}", "Zones measured", SLATE)
    h += "</tr>\n</table>\n"
    h += (f'<div style="{F}font-size:12px;color:{SLATE};margin:6px 0 4px 0;">Availability % = in stock ÷ listed positions '
          f'(not-listed positions are excluded from this figure).</div>\n')
    if c[UNV]:
        h += (f'<div style="{F}font-size:12px;color:{SLATE};margin:6px 0 4px 0;">{c[UNV]} positions unverified today, '
              f'excluded from the numbers above.</div>\n')
    h += narrow_note(cfg, pos, all_ids)

    h += h2("By warehouse — all brands") + wh_table(cfg, pos, all_ids, total=True)

    # Notes
    h += h2("Notes for this run")
    sections = [("New listings discovered", "new"), ("Possible delisting, baseline may be stale", "stale"),
                ("Listing changes since last run", "changes"), ("Possible related products (needs decision)", "related"),
                ("Unverified positions", "unverified"), ("Suspected data anomalies", "anomalies")]
    for title, key in sections:
        body = "<br>".join(escape(x) for x in notes[key]) or "None."
        h += (f'<div style="margin-bottom:12px;"><div style="{F}font-size:12px;font-weight:bold;color:{SLATE};">{title}</div>'
              f'<div style="{F}font-size:12px;color:{INK};margin-top:2px;">{body}</div></div>\n')

    # Brand cards
    for b in cfg["brands"]:
        ids = [str(s["id"]) for s in b["skus"]]
        names = {str(s["id"]): escape(s["name"]) for s in b["skus"]}
        bc = counts(cfg, pos, ids)
        h += h2(escape(b["name"]))
        h += (f'<div style="{F}font-size:12px;color:{INK};margin-bottom:10px;">{len(ids)} SKUs tracked · {bc["listed"]} listed positions · '
              f'<span style="color:{GREEN};font-weight:bold;">{bc[IN]} in stock</span> · '
              f'<span style="color:{RED};font-weight:bold;">{bc[OOS]} out of stock</span> · '
              f'<span style="color:{GRAY};">{bc[NL]} not listed</span> · availability {pct(bc["avail"])}</div>\n')
        h += h3("By warehouse") + wh_table(cfg, pos, ids, total=False)

        h += h3("Prices by warehouse (all tracked SKUs)") + TABLE + "\n" + head(["SKU", "Product"] + [z["name"] for z in zones])
        for i, sid in enumerate(ids):
            h += row([f"{TD}{sid}</td>", f"{TD}{names[sid]}</td>"] +
                     [f"{TD}{price_cell(pos[sid][z['key']], low_thr)}</td>" for z in zones],
                     "#FFFFFF" if i % 2 == 0 else "#FAFBFB")
        h += "</table>\n"

        h += h3("Not listed")
        nl_rows = [(sid, [z["name"] for z in zones if pos[sid][z["key"]]["state"] == NL]) for sid in ids]
        nl_rows = [r for r in nl_rows if r[1]]
        if nl_rows:
            h += TABLE + "\n" + head(["SKU", "Product", "Not listed in"])
            for i, (sid, zs) in enumerate(nl_rows):
                h += row([f"{TD}{sid}</td>", f"{TD}{names[sid]}</td>",
                          f'{TD}<span style="color:{GRAY};">{", ".join(zs)}</span></td>'],
                         "#FFFFFF" if i % 2 == 0 else "#FAFBFB")
            h += "</table>\n"
        else:
            h += NONE

        h += h3("Out of stock (listed positions only)")
        oos = [(sid, z) for sid in ids for z in zones if pos[sid][z["key"]]["state"] == OOS]
        if oos:
            h += TABLE + "\n" + head(["SKU", "Product", "Warehouse", "Price", "Restock estimate"])
            for sid, z in oos:
                p = pos[sid][z["key"]]
                promo_here = p["sale_price"] is not None
                promo_any = any(pos[sid][k["key"]]["sale_price"] is not None for k in zones
                                if pos[sid][k["key"]]["state"] in (IN, OOS))
                flag = (f' <span style="{F}font-size:10px;color:#FFFFFF;background-color:{RED};font-weight:bold;'
                        f'padding:1px 4px;">OUT OF STOCK WHILE ON PROMOTION</span>') if (promo_here or promo_any) else ""
                h += row([f"{TD}{sid}</td>", f"{TD}{names[sid]}</td>", f"{TD}{z['name']}</td>",
                          f'{TD}<span style="color:{RED};font-weight:bold;">{kc(p["sale_price"] if promo_here else p["price"])}</span>{flag}</td>',
                          f"{TD}{escape(p['restock']) if p['restock'] else 'Not given'}</td>"], "#FFF3F3")
            h += "</table>\n"
        else:
            h += NONE

        h += h3(f"Low stock (≤{low_thr} units, in-stock positions only)")
        lows = [(sid, z) for sid in ids for z in zones if pos[sid][z["key"]]["low"]]
        if lows:
            h += TABLE + "\n" + head(["SKU", "Product", "Warehouse", "Units", "Price"])
            for sid, z in lows:
                p = pos[sid][z["key"]]
                h += row([f"{TD}{sid}</td>", f"{TD}{names[sid]}</td>", f"{TD}{z['name']}</td>",
                          f'{TD}<span style="color:{AMBER};font-weight:bold;">{p["units"]}</span></td>',
                          f"{TD}{kc(p['sale_price'] if p['sale_price'] is not None else p['price'])}</td>"], "#FFFBEF")
            h += "</table>\n"
        else:
            h += NONE

    # Method
    zn = [z["name"] for z in zones]
    zlist = ", ".join(zn)
    failed = [z["name"] for z in zones if meta["zone_status"][z["key"]] != "OK"]
    promos = sum(1 for sid in pos for z in zones if pos[sid][z["key"]]["sale_price"] is not None
                 and pos[sid][z["key"]]["state"] in (IN, OOS))
    p1 = (f"Each of the {len(zones)} tracked zones ({zlist}) was selected via the delivery-address switch and verified "
          f"before any product was measured: the address actually selected in checkout was confirmed to match the zone, "
          f"and canary SKU {cfg['canary_sku']} (units and saleId) was recorded against the previous zone. ")
    p1 += (f"All {len(zones)} zones verified successfully this run; no zone failed verification." if not failed else
           f"{len(failed)} zone(s) could not be verified and are excluded from every count: {', '.join(failed)}.")
    p2 = (f"Listing status (in stock / out of stock / not listed) comes from a stored footprint baseline — established "
          f"{cfg['footprint']['established']} from {escape(cfg['footprint']['source'])}. The footprint is refreshed only on "
          f"request, not automatically; it was not changed by this run. Today's run measured only unit counts and prices "
          f"against that stored baseline — it did not re-derive listing status.")
    p3 = ("No positions were unverified this run (every SKU/zone combination has a footprint entry)." if not c[UNV] else
          f"{c[UNV]} positions were unverified this run (no footprint entry yet) and are excluded from the counts; "
          f"they are listed under Notes.")
    p4 = (f"The low-stock threshold is fixed at {low_thr} units or fewer. Prices and promotion status are reported per "
          f"individual warehouse and are never averaged or collapsed into a range. " +
          ("No SKU showed a promotional (struck-through) price in any zone during this run." if not promos else
           f"{promos} position(s) showed a promotional price this run; these are marked in the prices table."))
    p5 = ("This report is a point-in-time snapshot, not a live feed — stock levels can and do change within minutes of "
          "measurement.")
    p6 = ("This report was generated by an automated scheduled run as a standalone HTML file rather than an email draft; "
          "a human reviews it before it is forwarded.")
    h += h2("Method")
    h += f'<div style="{F}font-size:12px;color:{INK};line-height:1.6;">\n'
    for p in (p1, p2, p3, p4, p5):
        h += f'<p style="margin:0 0 8px 0;">{p}</p>\n'
    h += f'<p style="margin:0;">{p6}</p>\n</div>\n'
    h += '<div style="height:1px;background-color:#E4E7EA;margin:22px 0 14px 0;"></div>\n'
    h += f'<div style="{F}font-size:10px;color:{SLATE};">{escape(cfg["footer"])}</div>\n'
    h += "</div>\n</div>\n"
    return h
