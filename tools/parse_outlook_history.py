"""Parse the plain-text Polaris reports exported from Outlook into history/polaris.json."""
import json, re, sys
from pathlib import Path

WH = ["Chrášťany", "Praha Liboc", "Horní Počernice", "Brno", "Ostrava"]
KEY = {"Chrášťany": "chrastany", "Praha Liboc": "praha_liboc", "Horní Počernice": "horni_pocernice",
       "Brno": "brno", "Ostrava": "ostrava"}
ROW = re.compile(r"^\s*(" + "|".join(map(re.escape, WH)) + r")\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.,]+)\s*%")
OOS_ROW = re.compile(r"^\s*(14\d{5})\s+(.+?)\s+(" + "|".join(map(re.escape, WH)) + r")\s+([\d\s]+,\d+)\s*Kč\s+(.*?)(?:\s+(\d+))?\s*$")
LOW_ROW = re.compile(r"^\s*(14\d{5})\s+(.+?)\s+(" + "|".join(map(re.escape, WH)) + r")\s+(\d)\s+([\d\s]+,\d+)\s*Kč")


def section(lines, start_pat, stop_pats):
    out, on = [], False
    for ln in lines:
        if not on and re.search(start_pat, ln):
            on = True; continue
        if on and any(re.search(p, ln) for p in stop_pats):
            break
        if on:
            out.append(ln)
    return out


ASCII = {"Chrastany": "Chrášťany", "Horni Pocernice": "Horní Počernice", "Liboc": "Praha Liboc"}


def norm(text):
    text = text.replace("\xa0", " ")
    for a, b in ASCII.items():
        text = re.sub(r"\b" + a + r"\b", b, text) if a != "Liboc" else text
    return text


def parse(date, text):
    text = norm(text)
    lines = text.splitlines()
    zones = {}
    for ln in lines:
        m = ROW.match(ln)
        if m and KEY[m.group(1)] not in zones:
            zones[KEY[m.group(1)]] = {"listed": int(m.group(2)), "in_stock": int(m.group(3)),
                                      "oos": int(m.group(4)), "not_listed": int(m.group(5))}
    mt = re.search(r"measured\s+(\d{1,2}:\d{2})", text)
    oos, last_sku = [], None
    for ln in section(lines, r"^\s*(Out of stock|OUT OF STOCK)( \(listed positions only\))?\s*$",
                      [r"^\s*(Low stock|LOW STOCK)", r"POS Media Czech Republic", r"^\s*(Method|METHOD)"]):
        m0 = re.match(r"^\s*-?\s*(14\d{5})\b", ln)
        if m0:
            last_sku = int(m0.group(1))
        cells = [c.strip() for c in ln.split("\t")]
        wj = next((j for j, c in enumerate(cells) if c in KEY), None)
        if wj is not None and last_sku:
            rest = cells[wj + 2] if len(cells) > wj + 2 else None
            days = cells[wj + 3] if len(cells) > wj + 3 else ""
            oos.append({"sku": last_sku, "zone": KEY[cells[wj]], "restock": rest or None,
                        "days_at_zero": int(days) if days.isdigit() else None})
            continue
        m = re.match(r"^\s*-\s*(14\d{5}).*?--\s*(" + "|".join(map(re.escape, WH)) + r"):.*?restock\s+([\d.]+)?.*?(\d+)\s*day", ln)
        if m:
            oos.append({"sku": int(m.group(1)), "zone": KEY[m.group(2)], "restock": m.group(3),
                        "days_at_zero": int(m.group(4))})
            continue
        m = re.search(r"Warehouse:\s*(" + "|".join(map(re.escape, WH)) + r")\s*\|", ln)
        if m and last_sku:
            oos.append({"sku": last_sku, "zone": KEY[m.group(1)], "restock": None, "days_at_zero": None})
    low = []
    for ln in section(lines, r"^\s*(Low stock|LOW STOCK)( \(.*\))?\s*$",
                      [r"POS Media Czech Republic", r"^\s*(Method|METHOD)", r"^\s*(Notes|NOTES)"]):
        cells = [c.strip() for c in ln.split("\t")]
        if len(cells) >= 4 and re.fullmatch(r"14\d{5}", cells[0]) and cells[2] in KEY and cells[3].isdigit():
            low.append({"sku": int(cells[0]), "zone": KEY[cells[2]], "units": int(cells[3])})
    if len(zones) != 5:
        return None
    tot = {k: sum(z[k] for z in zones.values()) for k in ("listed", "in_stock", "oos", "not_listed")}
    tot["availability"] = round(tot["in_stock"] / tot["listed"] * 100, 1) if tot["listed"] else None
    return {"date": date, "measured": mt.group(1) if mt else None, "source": "outlook",
            "total": tot, "zones": zones, "oos": oos, "low": low}


if __name__ == "__main__":
    raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    days, skipped = [], []
    for d in sorted(raw):
        r = parse(d, raw[d])
        (days.append(r) if r else skipped.append(d))
    Path(sys.argv[2]).write_text(json.dumps({"client": "Polaris", "days": days}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("parsed", len(days), "skipped", skipped)
