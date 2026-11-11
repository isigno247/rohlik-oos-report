"""Renders the daily report in the layout of the e-mails sent since 20.10.2026
(reference: reference/polaris_email_2026-11-11.html). Only data changes run to run."""
from __future__ import annotations

import base64
from datetime import date, timedelta
from html import escape
from pathlib import Path

from .classify import IN, OOS, NL, counts

F = "font-family:Arial,Helvetica,sans-serif;"
SLATE, ORANGE, GREEN, RED, AMBER, GREY, INK, MUTED = (
    "#515A63", "#F19500", "#0ca30c", "#d03b3b", "#fab219", "#9aa0a6", "#33393f", "#6b737b")
P = '<p style="font-size:12px; color:#33393f; margin:4px 0; line-height:1.5">'
TBL = '<table width="100%" cellspacing="0" cellpadding="6" style="font-family:Arial; font-size:12px; color:#33393f; border-collapse:collapse"><tbody>'
TR = '<tr style="border-bottom:1px solid #e6e8ea">'


def h2(t):
    return (f'<div style="{F}font-size:17px;font-weight:bold;color:{SLATE};border-bottom:2px solid {ORANGE};'
            f'padding:18px 0 4px 0;margin-bottom:10px">{t}</div>')


def head(cols):
    return '<tr bgcolor="#eef0f2" style="color:#515A63">' + "".join(
        f'<th align="{a}">{t}</th>' for t, a in cols) + "</tr>"


def kc(x):
    return ("%.1f" % x).replace(".", ",") + " Kč" if x is not None else ""


def dm(d: date):
    return f"{d.day:02d}.{d.month:02d}."


def dmx(d: date):
    return f"{d.day:02d}.{d.month:02d}"


def pct(x):
    return "–" if x is None else f"{x:.1f}%"


def restock_label(iso, today, long=False):
    if not iso:
        return "not given"
    d = date.fromisoformat(iso)
    if d <= today:
        return f"today ({dm(today)})"
    return dm(d)


def days_at_zero(sid, zone, today, history):
    """Consecutive days at zero incl. today, from the dated history (gaps count as continuing)."""
    first = today
    for day in reversed(history):
        d = date.fromisoformat(day["date"])
        if d >= today:
            continue
        hit = next((o for o in day.get("oos", []) if str(o["sku"]) == str(sid) and o["zone"] == zone), None)
        if not hit:
            break
        if hit.get("days_at_zero"):
            return hit["days_at_zero"] + (today - d).days
        first = d
    return (today - first).days + 1


def render(cfg, cls, notes, meta, logo_path: Path, history: list, today: date) -> str:
    pos, zones = cls["positions"], cfg["zones"]
    zname = {z["key"]: z["name"] for z in zones}
    skus = [s for b in cfg["brands"] for s in b["skus"]]
    short = {str(s["id"]): s.get("short") or s["name"].replace("Polaris ", "") for s in skus}
    c = counts(cfg, pos)
    low_thr = int(cfg["low_stock_threshold"])
    stale_thr = int(cfg["stale_oos_threshold_days"])
    logo = base64.b64encode(logo_path.read_bytes()).decode()

    oos_rows = []
    for s in skus:
        sid = str(s["id"])
        for z in zones:
            p = pos[sid][z["key"]]
            if p["state"] == OOS:
                p["days_at_zero"] = days_at_zero(sid, z["key"], today, history)
                oos_rows.append((sid, z, p))
    oos_rows.sort(key=lambda r: -r[2]["days_at_zero"])
    low_rows = [(str(s["id"]), z, pos[str(s["id"])][z["key"]]) for s in skus for z in zones
                if pos[str(s["id"])][z["key"]]["low"]]
    promos = [(sid, z) for s in skus for z in zones for sid in [str(s["id"])]
              if pos[sid][z["key"]]["state"] in (IN, OOS) and pos[sid][z["key"]]["sale_price"] is not None]
    stale = [(sid, z, p) for sid, z, p in oos_rows if p["days_at_zero"] > stale_thr]

    # history incl. today, for trend + "vs yesterday"
    hist = [d for d in history if d["date"] < today.isoformat()]
    tot_today = c["avail"]
    prev = hist[-1]["total"]["availability"] if hist else None
    series = [(d["date"], d["total"]["availability"]) for d in hist if d["total"]["availability"] is not None]
    if tot_today is not None:
        series.append((today.isoformat(), tot_today))
    series = series or [(today.isoformat(), 0.0)]
    since = date.fromisoformat(series[0][0])
    avg = sum(v for _, v in series) / len(series)
    low_d, low_v = min(series, key=lambda x: (x[1], x[0]))

    h = f'<div style="{F}max-width:900px;margin:0 auto">'
    h += (f'<div style="background-color:#ffffff; padding:14px 20px"><img src="data:image/png;base64,{logo}" '
          f'width="210" height="48" alt="POS Media" style="display:block;border:0"></div>')
    h += (f'<div style="background-color:#515A63; padding:14px 20px"><div style="{F}font-size:22px;font-weight:bold;'
          f'color:#ffffff">Polaris availability report</div><div style="{F}font-size:14px;color:#ffffff;margin-top:4px">'
          f'Rohlik.cz · dog food · {meta["date_str"]} — measured {meta["time_str"]}</div></div>')
    h += '<div style="height:4px; background-color:#F19500; font-size:10px; line-height:4px">&nbsp;</div>'
    h += '<div style="padding:0 20px 20px 20px">'

    # executive summary
    if prev is None:
        vs = "no previous measurement to compare"
    else:
        dlt = round(tot_today - prev, 1)
        vs = "unchanged vs yesterday" if dlt == 0 else f"{'up' if dlt > 0 else 'down'} {abs(dlt):.1f} pp vs yesterday"
    h += h2("Executive summary")
    h += f'{P}<b style="color:#515A63">Overall:</b> {pct(tot_today)} available today ({c[IN]} of {c["listed"]} listed positions), {vs}.</p>'
    if oos_rows:
        per = {}
        for _, z, _ in oos_rows:
            per[z["name"]] = per.get(z["name"], 0) + 1
        order = sorted(per.items(), key=lambda kv: (-kv[1], [z["name"] for z in zones].index(kv[0])))
        dates = sorted(p["restock_date"] for _, _, p in oos_rows if p.get("restock_date"))
        if dates:
            a, b = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
            a = max(a, today); b = max(b, today)
            due = f" Restocks due {dm(a)}" if a == b else f" Restocks due {a.day:02d}.–{dm(b)}"
        else:
            due = " No restock dates given."
        n = len(oos_rows)
        h += (f'{P}<b style="color:#515A63">Out of stock:</b> {n} position{"s" if n != 1 else ""} in {len(per)} '
              f'warehouse{"s" if len(per) != 1 else ""} ({", ".join(f"{k} {v}" for k, v in order)}).{due}</p>')
    else:
        h += f'{P}<b style="color:#515A63">Out of stock:</b> None. All {c["listed"]} listed positions in stock.</p>'
    watch = []
    if low_rows:
        zl = [z["name"] for z in zones if any(r[1]["key"] == z["key"] for r in low_rows)]
        zs = zl[0] if len(zl) == 1 else ", ".join(zl[:-1]) + " and " + zl[-1]
        units = [p["units"] for _, _, p in low_rows]
        watch.append(f"{len(low_rows)} low-stock position{'s' if len(low_rows) != 1 else ''} "
                     f"({min(units)}–{max(units)} units) in {zs}." if min(units) != max(units) else
                     f"{len(low_rows)} low-stock position{'s' if len(low_rows) != 1 else ''} ({min(units)} units) in {zs}.")
    else:
        watch.append("No low-stock positions.")
    for sid, z, p in stale:
        watch.append(f"{escape(short[sid])} in {z['name']} at zero for {p['days_at_zero']} days, possible delisting.")
    watch.append("No promotions." if not promos else f"{len(promos)} position{'s' if len(promos) != 1 else ''} on promotion.")
    watch.append("No listing changes." if not notes["changes"] else "Listing changes, see notes.")
    h += f'{P}<b style="color:#515A63">Watch:</b> {" ".join(watch)}</p>'
    h += (f'{P}<b style="color:#515A63">Trend:</b> {avg:.1f}% average since {dmx(since)}, lowest {low_v:.1f}% '
          f'({dmx(date.fromisoformat(low_d))}).</p>')
    action = "None." if not notes["changes"] and not notes["anomalies"] else "Review the notes below."
    h += f'{P}<b style="color:#515A63">Action needed today:</b> {action}</p>'

    h += h2("How to read this report")
    h += (f'{P}<b style="color:#0ca30c">In stock</b> – the warehouse carries the product and has units today. '
          f'<b style="color:#d03b3b">Out of stock</b> – the warehouse carries the product and has zero units today; '
          f'the restock date is shown when Rohlik gives one. <b style="color:#9aa0a6">Not listed</b> – the warehouse '
          f'does not carry the product at all. Low stock means {low_thr} units or fewer. Numbers count positions '
          f'(SKU × warehouse), not unique products.</p>')

    # overview tiles
    def tile(v, label, col):
        return (f'<td align="center" style="padding:10px 6px; border:1px solid #e1e4e7"><div style="{F}font-size:22px;'
                f'font-weight:bold;color:{col}">{v}</div><div style="{F}font-size:10px;color:{MUTED}">{label}</div></td>')
    h += h2("Overview")
    h += ('<table width="100%" cellspacing="6" cellpadding="0" style="font-family:Arial"><tbody><tr>'
          + tile(len(skus), "SKUs tracked", SLATE) + tile(c["listed"], "listed positions", SLATE)
          + tile(c[IN], "in stock", GREEN) + tile(c[OOS], "out of stock", RED) + tile(c[NL], "not listed", GREY)
          + tile(c["low"], "low stock", "#b57d00") + tile(pct(c["avail"]), "availability", SLATE)
          + "</tr></tbody></table>")
    listed = {z["name"]: counts(cfg, pos, None, [z["key"]])["listed"] for z in zones}
    groups = {}
    for k, v in listed.items():
        groups.setdefault(v, []).append(k)
    j = lambda xs: xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]
    parts = [f"{j(ks)} list{'s' if len(ks) == 1 else ''} {v} of the {len(skus)} SKUs" if i == 0 else f"{', '.join(ks)} list {v}"
             for i, (v, ks) in enumerate(sorted(groups.items()))]
    h += (f'<p style="font-size:12px; color:#33393f; margin:4px 0; line-height:1.5; color:#6b737b">Availability = in '
          f'stock ÷ listed positions. {", ".join(parts)}.</p>')

    def wh_table():
        t = TBL + head([("Warehouse", "left"), ("Listed", "right"), ("In stock", "right"), ("Out of stock", "right"),
                        ("Not listed", "right"), ("Availability", "right")])
        for z in zones:
            k = counts(cfg, pos, None, [z["key"]])
            oos = f'<b style="color:#d03b3b">{k[OOS]}</b>' if k[OOS] else "0"
            t += (f'{TR}<td><b>{z["name"]}</b></td><td align="right">{k["listed"]}</td><td align="right"><span '
                  f'style="color:#0ca30c">{k[IN]}</span></td><td align="right">{oos}</td><td align="right"><span '
                  f'style="color:#9aa0a6">{k[NL]}</span></td><td align="right">{pct(k["avail"])}</td></tr>')
        t += (f'{TR}<td><b>Total</b></td><td align="right"><b>{c["listed"]}</b></td><td align="right"><b>{c[IN]}</b></td>'
              f'<td align="right"><b>{c[OOS]}</b></td><td align="right"><b>{c[NL]}</b></td><td align="right"><b>'
              f'{pct(c["avail"])}</b></td></tr>')
        return t + "</tbody></table>"
    h += h2("By warehouse") + wh_table()

    # trend: 6 weeks, Monday-start, one square per day
    by_date = {d["date"]: d for d in hist}
    monday = today - timedelta(days=today.weekday()) - timedelta(weeks=5)
    h += h2(f"Availability trend, since {dmx(since)}")
    h += (f'{P}Average availability over the {len(series)} measured days is {avg:.1f}%; today it is {pct(tot_today)}. '
          f'One square per day, last 6 weeks; percentages are since {dmx(since)}.</p>')
    h += ('<table cellspacing="0" cellpadding="10" style="font-family:Arial; border:1px solid #d5d9dd"><tbody><tr><td>'
          '<table cellspacing="1" cellpadding="0" style="font-family:Arial"><tbody><tr>')
    h += '<td width="5"></td>'.join(
        f'<td colspan="7" style="font-size:10px; color:#6b737b; border-left:1px solid #d5d9dd; padding-left:2px">'
        f'{dm(monday + timedelta(weeks=w))}</td>' for w in range(6)) + "</tr>"
    for z in zones:
        vals = [d["zones"][z["key"]] for d in hist if z["key"] in d["zones"]]
        k = counts(cfg, pos, None, [z["key"]])
        rates = [v["in_stock"] / v["listed"] * 100 for v in vals if v["listed"]] + ([k["avail"]] if k["avail"] is not None else [])
        zavg = sum(rates) / len(rates) if rates else None
        h += (f'<tr><td colspan="47" style="font-size:12px; color:#33393f; padding:8px 0 3px 0"><b>{z["name"]}</b> · '
              f'{pct(zavg)} since {dmx(since)} · today {k[IN]} of {k["listed"]} in stock</td></tr><tr>')
        cells = []
        for w in range(6):
            wk = []
            for dd in range(7):
                d = monday + timedelta(weeks=w, days=dd)
                if d > today:
                    wk.append('<td width="6" height="14"></td>'); continue
                if d == today:
                    o = k[OOS]
                elif d.isoformat() in by_date:
                    o = by_date[d.isoformat()]["zones"][z["key"]]["oos"]
                else:
                    o = None
                col = "#e1e4e7" if o is None else "#0ca30c" if o == 0 else "#fab219" if o == 1 else "#d03b3b"
                wk.append(f'<td bgcolor="{col}" width="6" height="14"></td>')
            cells.append("".join(wk))
        h += '<td width="5"></td>'.join(cells) + "</tr>"
    h += "</tbody></table></td></tr></tbody></table>"
    h += ('<p style="font-size:12px; color:#33393f; margin:4px 0; line-height:1.5; font-size:10px; color:#6b737b">'
          '<span style="color:#0ca30c">■</span> all listed products in stock &nbsp; <span style="color:#fab219">■</span>'
          ' 1 product out of stock &nbsp; <span style="color:#d03b3b">■</span> 2 or more out of stock &nbsp; '
          '<span style="color:#9aa0a6">■</span> no measurement</p>')

    # notes
    stale_txt = "; ".join(f"{sid} {escape(short[sid])} in {z['name']}: {p['days_at_zero']} days at zero "
                          f"(limit {stale_thr}), restock {restock_label(p.get('restock_date'), today)}"
                          for sid, z, p in stale)
    h += h2("Notes for this run")
    for title, body in [("New listings discovered", "None."),
                        ("Possible delisting, baseline may be stale", (stale_txt + "; baseline unchanged.") if stale else "None."),
                        ("Listing changes since last run", "<br>".join(map(escape, notes["changes"])) or "None."),
                        ("Possible related products (needs decision)", "<br>".join(map(escape, notes["related"])) or "None."),
                        ("Suspected data anomalies", "<br>".join(map(escape, notes["anomalies"])) or "None.")]:
        h += f'{P}<b style="color:#515A63">{title}:</b> {body}</p>'

    # brand card
    h += h2("Polaris")
    h += (f'{P}{len(skus)} SKUs · {c["listed"]} listed positions · {c[IN]} in stock · {c[OOS]} out of stock · '
          f'{c[NL]} not listed · {pct(c["avail"])} availability</p>')
    h += wh_table()
    h += h2("Prices per warehouse")
    h += TBL + head([("SKU", "left"), ("Product", "left")] + [(z["name"], "left") for z in zones])
    for s in skus:
        sid = str(s["id"])
        row = f"{TR}<td>{sid}</td><td>{escape(short[sid])}</td>"
        for z in zones:
            p = pos[sid][z["key"]]
            if p["state"] == NL:
                row += '<td><i style="color:#9aa0a6">not listed</i></td>'
                continue
            price = kc(p["sale_price"]) + f' <s style="color:#9aa0a6">{kc(p["price"])}</s>' if p["sale_price"] is not None else kc(p["price"])
            if p["state"] == OOS:
                lab = restock_label(p.get("restock_date"), today)
                back = f"back {lab}" if lab != "not given" else "restock not given"
                row += (f'<td><span style="color:#9aa0a6">{price}</span><br><span style="color:#d03b3b">OOS · '
                        f'{back}</span></td>')
            else:
                row += f"<td>{price}</td>"
        h += row + "</tr>"
    h += "</tbody></table>"
    h += h2("Not listed")
    h += TBL + head([("SKU", "left"), ("Product", "left"), ("Not listed in", "left")])
    for s in skus:
        sid = str(s["id"])
        zl = [z["name"] for z in zones if pos[sid][z["key"]]["state"] == NL]
        if zl:
            h += f"{TR}<td>{sid}</td><td>{escape(short[sid])}</td><td>{', '.join(zl)}</td></tr>"
    h += "</tbody></table>"
    h += h2("Out of stock")
    h += TBL + head([("SKU", "left"), ("Product", "left"), ("Warehouse", "left"), ("Price", "left"),
                     ("Restock", "left"), ("Days at 0", "right")])
    for sid, z, p in oos_rows:
        price = kc(p["sale_price"] if p["sale_price"] is not None else p["price"])
        h += (f"{TR}<td>{sid}</td><td>{escape(short[sid])}</td><td>{z['name']}</td><td>{price}</td>"
              f"<td>{restock_label(p.get('restock_date'), today)}</td><td align=\"right\">{p['days_at_zero']}</td></tr>")
    if not oos_rows:
        h += f'{TR}<td colspan="6">None.</td></tr>'
    h += "</tbody></table>"
    h += h2(f"Low stock ({low_thr} units or fewer)")
    h += TBL + head([("SKU", "left"), ("Product", "left"), ("Warehouse", "left"), ("Units", "right"), ("Price", "left")])
    for sid, z, p in low_rows:
        h += (f"{TR}<td>{sid}</td><td>{escape(short[sid])}</td><td>{z['name']}</td><td align=\"right\">{p['units']}</td>"
              f"<td>{kc(p['sale_price'] if p['sale_price'] is not None else p['price'])}</td></tr>")
    if not low_rows:
        h += f'{TR}<td colspan="5">None.</td></tr>'
    h += "</tbody></table>"
    h += ('<p style="font-family:Arial; font-size:10px; color:#6b737b; margin:18px 0 0 0">POS Media Czech Republic · '
          'automated daily report · Rohlik.cz availability, Polaris</p>')
    h += "</div></div>"
    return h
