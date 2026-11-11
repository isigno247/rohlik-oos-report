"""Self-checks from the skill's report-spec. Any failure blocks the report from being published."""
from __future__ import annotations

import base64
import hashlib
import re

from .classify import IN, OOS, NL, UNV, FAIL, counts

LOGO_B64_MD5 = "00800bf6a938bfdeedc94977c26281c0"   # fingerprint from pos-media-brand.md
ALLOWED_SIZES = {"10px", "12px", "14px", "17px", "22px"}


def run_checks(html: str, cfg: dict, cls: dict, meas: dict) -> dict:
    r = {}
    pos = cls["positions"]
    r["01_body_starts_with_div_and_is_long"] = html.startswith("<div") and len(html) > 20000
    r["02_no_placeholders"] = not re.search(r"FILE:|/tmp/|PLACEHOLDER|TODO", html)
    imgs = re.findall(r'<img src="data:image/png;base64,([A-Za-z0-9+/=]+)"', html)
    r["03_logo_once_fingerprint_ok"] = (len(imgs) == 1 and
                                        hashlib.md5(imgs[0].encode()).hexdigest() == LOGO_B64_MD5)
    r["04_header_bg_once"] = len(re.findall(r'background-color:#515A63;padding:18px 24px', html, re.I)) == 1
    heads = html.count("border-left:4px solid #F19500")
    r["05_letterhead_colors"] = heads >= 6 and html.upper().count("#F19500") > heads and html.count("#515A63") > 20
    sizes = set(re.findall(r"font-size:(\d+px)", html)) - {"0"}
    r["07a_type_scale_locked"] = sizes <= ALLOWED_SIZES
    r["07b_font_family_with_every_size"] = all("font-family:Arial" in s for s in
                                               re.findall(r'style="([^"]*font-size:\d+px[^"]*)"', html))
    # classification integrity
    fp = cls["footprint"]
    ok = True
    for sid, zs in pos.items():
        for k, p in zs.items():
            if p["state"] == NL and (fp[sid][k] is not False or p["price"] is not None):
                ok = False
            if p["state"] in (IN, OOS) and fp[sid][k] is not True:
                ok = False
    c = counts(cfg, pos)
    total = len(pos) * len(cfg["zones"])
    ok = ok and c[IN] + c[OOS] + c[NL] + c[UNV] + c[FAIL] == total
    ok = ok and (c["avail"] is None or abs(c["avail"] - c[IN] / c["listed"] * 100) < 1e-9)
    r["06_classification_integrity"] = ok
    r["08_brand_tables_present"] = (html.count(">By warehouse</div>") == len(cfg["brands"]) and
                                    "recommend" not in html.lower())
    n_skus = sum(len(b["skus"]) for b in cfg["brands"])
    price_tbl = html.count("Prices by warehouse (all tracked SKUs)") == len(cfg["brands"])
    r["09_prices_table_every_sku"] = price_tbl and all(f">{s['id']}</td>" in html
                                                       for b in cfg["brands"] for s in b["skus"]) and n_skus > 0
    r["10_header_time_and_snapshot"] = bool(re.search(r"\d\d\.\d\d\.\d{4} — measured \d\d:\d\d", html)) and \
        "point-in-time snapshot" in html
    r["11_notes_section_present"] = "Notes for this run" in html
    # strict: every zone must be verified - a partial report is never published
    r["12_zones_verified_and_restored"] = (all(v["status"] == "OK" for v in meas["zones"].values())
                                           and meas["restored"])
    return r
