"""Weekly catalogue check for brand-new SKUs of a tracked brand - no keyword search.

Source: Rohlik's own sitemap (the full product catalogue as published to search engines), filtered
on the brand's URL slug, then confirmed against the product record's brand field. Search is ranking
based and capped, so it is never used here (skill rule 3).

Nothing found here ever enters the counts: candidates are listed in the report as "needs decision"
until someone adds them to config/<client>.yml (with a per-warehouse footprint) or to
discovery.ignore. A failed check is reported as "not checked", never as "none".

The sitemap layout is undocumented. Every run logs what it found (index entries, product sitemaps,
matches) so the first run doubles as the diagnostic.
"""
from __future__ import annotations

import gzip
import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from .measure import UA, Session

log = logging.getLogger("oos.discover")
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
PRODUCT_URL = re.compile(r"/(\d{5,9})-([^/?#]+)")


def _get(url: str) -> str:
    for attempt in range(1, 4):                      # Rohlik answers bursts with HTTP 429: back off and retry
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                raw = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(30 * attempt)
    if url.endswith(".gz") or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def _brand(rec: dict | None) -> str | None:
    if not rec:
        return None
    b = rec.get("brand") or rec.get("brandName") or rec.get("producer")
    if isinstance(b, dict):
        b = b.get("name") or b.get("title")
    return str(b) if b else None


def discover(cfg: dict, base: str, today: date) -> dict:
    d = cfg.get("discovery") or {}
    brand = d.get("brand") or cfg["brands"][0]["name"]
    terms = [t.lower() for t in d.get("slug_terms") or [brand.lower()]]
    known = {int(s["id"]) for b in cfg["brands"] for s in b["skus"]}
    ignore = {int(x) for x in d.get("ignore") or []}
    out = {"checked": today.isoformat(), "status": "not_checked", "source": None, "note": "",
           "candidates": [], "other_brand": []}
    base = base.rstrip("/")
    try:
        index = _get(base + d.get("sitemap_index", "/sitemap.xml"))
        locs = LOC.findall(index)
        flt = d.get("sitemap_filter", "product")
        maps = [u for u in locs if flt in u.lower()]
        log.info("sitemap index: %d entries, %d match '%s': %s", len(locs), len(maps), flt, maps[:10])
        if not maps:
            out["note"] = f"sitemap index has no '{flt}' sitemaps ({len(locs)} entries)"
            return out
        found, urls = {}, 0
        for u in maps[: int(d.get("max_sitemaps", 40))]:
            for loc in LOC.findall(_get(u)):
                urls += 1
                m = PRODUCT_URL.search(loc)
                if m and any(t in m.group(2).lower() for t in terms):
                    found.setdefault(int(m.group(1)), loc)
        log.info("product sitemaps: %d URLs scanned, %d match %s", urls, len(found), terms)
        if urls == 0:
            out["note"] = "product sitemaps were empty"
            return out
        out["status"], out["source"] = "ok", f"Rohlik sitemap, {urls} product URLs"
        missing = sorted(set(found) - known - ignore)
        missing_known = sorted(known - set(found))
        if missing_known:
            log.info("tracked SKUs not in sitemap (sitemap may be incomplete): %s", missing_known)
        det = Session(base).details(missing) if missing else {}
        for pid in missing:
            b = _brand(det.get(pid))
            rec = {"id": pid, "url": found[pid], "brand": b,
                   "name": (det.get(pid) or {}).get("name") or found[pid].rsplit("/", 1)[-1]}
            if b and b.strip().lower() != brand.lower():
                out["other_brand"].append(rec)          # skill rule 10: needs decision, never counted
            else:
                rec["confirmed"] = bool(b)
                out["candidates"].append(rec)
    except Exception as e:                                # never fails the daily report
        out["note"] = f"{type(e).__name__}: {e}"[:200]
        log.warning("discovery not completed: %s", out["note"])
    return out


def load(root: Path, slug: str) -> dict | None:
    f = root / "state" / "discovery" / f"{slug}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def save(root: Path, slug: str, res: dict) -> None:
    f = root / "state" / "discovery" / f"{slug}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


def note_lines(cfg: dict, res: dict | None) -> tuple[list[str], str]:
    """(candidate lines, status line) for the report's 'New listings discovered' note."""
    if not res:
        return [], "Catalogue check for new SKUs not run yet."
    when = date.fromisoformat(res["checked"])
    when_s = f"{when.day:02d}.{when.month:02d}."
    known = {int(s["id"]) for b in cfg["brands"] for s in b["skus"]}
    ignore = {int(x) for x in (cfg.get("discovery") or {}).get("ignore") or []}
    lines = [f"{c['id']} {c['name']}: new in Rohlik's catalogue"
             f"{'' if c.get('confirmed') else ' (brand not confirmed)'}, not tracked yet. Needs decision: "
             f"add to config with a per-warehouse footprint, or to discovery.ignore."
             for c in res.get("candidates", []) if c["id"] not in known | ignore]
    if res["status"] != "ok":
        return lines, f"Catalogue check for new SKUs on {when_s} could not complete; not checked."
    return lines, f"Catalogue check for new SKUs on {when_s}: {'see above' if lines else 'none'}."
