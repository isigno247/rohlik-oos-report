"""Entry point: python -m oos.run --client polaris"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .alerts import detect, long_term, publish
from .checks import run_checks
from . import discover as disc
from .classify import OOS, build_notes, classify, counts, new_state
from .measure import Session, base_url, run_measure
from .report2 import render

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Prague")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--no-report", action="store_true",
                    help="afternoon run: update state, dashboard data and alerts, keep the morning report")
    ap.add_argument("--discover", action="store_true",
                    help="run the catalogue check for new SKUs now (otherwise Monday mornings)")
    ap.add_argument("--skip-if-done", action="store_true",
                    help="backup morning run: do nothing if today's morning run already happened")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("oos")

    cfg = yaml.safe_load((ROOT / "config" / f"{args.client}.yml").read_text(encoding="utf-8"))
    state_path = ROOT / "state" / f"{args.client}.json"
    prev = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
    if args.skip_if_done and (ROOT / "state" / "raw" / args.client /
                              f"{datetime.now(TZ).date().isoformat()}_am.json").exists():
        log.info("morning run already done today - backup run skipped")
        return 0

    meas = run_measure(cfg)                       # one uninterrupted pass, restore in finally
    now = datetime.now(TZ)
    today = now.date()
    cls = classify(cfg, meas, prev, today)
    failed = [z["name"] for z in cfg["zones"] if meas["zones"].get(z["key"], {}).get("status") != "OK"]
    notes = build_notes(cfg, cls, prev, today, failed)

    # weekly catalogue check for brand-new SKUs; the latest result is shown every day until handled
    slug = cfg["report_slug"]
    dcfg = cfg.get("discovery") or {}
    last = disc.load(ROOT, slug) or {}
    wd, due_wd = now.weekday(), int(dcfg.get("weekday", 0))
    retry = (last.get("status") != "ok" and 0 < wd - due_wd <= 2          # Monday failed (e.g. HTTP 429):
             and (today - date.fromisoformat(last.get("checked", "2000-01-01"))).days <= 2)  # retry Tue/Wed
    if args.discover or (not args.no_report and dcfg.get("enabled", True) and (wd == due_wd or retry)):
        fresh = disc.discover(cfg, base_url(), today)
        fresh["alerted"] = (disc.load(ROOT, slug) or {}).get("alerted", [])   # alert each SKU only once
        disc.save(ROOT, slug, fresh)
    dres = disc.load(ROOT, slug)
    notes["catalogue"], notes["catalogue_status"] = disc.note_lines(cfg, dres)
    # brand-new SKUs from the catalogue check: alert each one once, then keep it in the report notes
    cat_hits = []
    if dres:
        known = {int(s["id"]) for b in cfg["brands"] for s in b["skus"]}
        ignore = {int(x) for x in dcfg.get("ignore") or []}
        alerted = set(dres.get("alerted") or [])
        cat_hits = [c for c in dres.get("candidates", []) if c["id"] not in known | ignore | alerted]
        if cat_hits:
            dres["alerted"] = sorted(alerted | {c["id"] for c in cat_hits})
            disc.save(ROOT, slug, dres)
    for o in (dres or {}).get("other_brand", []):
        notes["related"].append(f"{o['id']} {o['name']}: brand field '{o['brand']}', URL matches "
                                f"{cfg['brands'][0]['name']}. Needs decision.")

    # raw Rohlik records for every zero / not-listed position: the evidence behind any listing call
    raw_dir = ROOT / "state" / "raw" / args.client
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{today.isoformat()}_{'pm' if args.no_report else 'am'}.json").write_text(
        json.dumps({k: v.get("raw", {}) for k, v in meas["zones"].items()}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    for old in sorted(raw_dir.glob("*.json"))[:-28]:        # keep two weeks
        old.unlink()
    meta = {"date_str": now.strftime("%d.%m.%Y"), "time_str": now.strftime("%H:%M ") + now.tzname(),
            "zone_status": {z["key"]: meas["zones"].get(z["key"], {}).get("status", "FAILED") for z in cfg["zones"]}}
    hist_file = ROOT / "docs" / "data" / f"{cfg['report_slug']}.json"
    history = json.loads(hist_file.read_text(encoding="utf-8"))["days"] if hist_file.exists() else []
    html = render(cfg, cls, notes, meta, ROOT / "assets" / "pos-media-logo.png", history, today)
    checks = run_checks(html, cfg, cls, meas)

    out_dir = ROOT / "reports" / cfg["report_slug"]
    out_dir.mkdir(parents=True, exist_ok=True)
    for k, v in checks.items():
        log.info("self-check %-40s %s", k, "PASS" if v else "FAIL")
    if not all(checks.values()):
        (ROOT / "failed_report.html").write_text(html, encoding="utf-8")   # uploaded as artifact only
        log.error("Self-checks failed — report NOT published. See failed_report.html artifact.")
        return 1

    name = f"{cfg['report_slug']}_oos_report_{today.isoformat()}.html"
    if not args.no_report:                        # one client report per day: the morning one
        (out_dir / name).write_text(html, encoding="utf-8")
        (out_dir / "latest.html").write_text(html, encoding="utf-8")
    events = detect(cfg, cls, prev)

    # warehouse watch: addresses where Rohlik is expected to switch to a new warehouse (e.g. Opatovice).
    # Alerts once, on the run the assigned warehouse first changes; never touches the report.
    watch_f = ROOT / "state" / "watch" / f"{args.client}.json"
    seen = json.loads(watch_f.read_text(encoding="utf-8")) if watch_f.exists() else {}
    watch_hits = []
    for w in cfg.get("watch_addresses") or []:
        try:
            s = Session(base_url())
            s.warm()
            wh = s.resolve(w["address_query"])
        except Exception as e:                       # a failed watch never fails the run
            log.warning("warehouse watch %s: %s", w["name"], e)
            continue
        log.info("warehouse watch %s: warehouse %s (expected %s)", w["name"], wh, w["expect_warehouse_id"])
        if wh != int(w["expect_warehouse_id"]) and wh != seen.get(w["name"]):
            watch_hits.append({"name": w["name"], "address": w["address_query"], "warehouse_id": wh,
                               "was": seen.get(w["name"]) or int(w["expect_warehouse_id"])})
        seen[w["name"]] = wh
    watch_f.parent.mkdir(parents=True, exist_ok=True)
    watch_f.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
    tot = counts(cfg, cls["positions"])
    ctx = {"total": {"oos": tot[OOS], "listed": tot["listed"], "avail": tot["avail"]},
           "prev_avail": counts(cfg, prev["positions"])["avail"] if prev and prev.get("positions") else None,
           "long": long_term(cfg, cls, events), "long_min": int(cfg.get("long_oos_threshold_days", 3)),
           "weekly": now.weekday() == 0 and not args.no_report,       # Monday morning: weekly digest
           "watch": watch_hits, "catalogue": cat_hits}
    publish(ROOT, cfg, events, now.isoformat(timespec="minutes"), now.strftime("%d.%m.%Y %H:%M"), ctx)
    log.info("Alerts: %d new event(s)", len(events))
    st = new_state(cfg, cls, meas, today, meta["time_str"], checks)
    state_path.parent.mkdir(exist_ok=True)
    state_path.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    hist = ROOT / "state" / "history" / args.client
    hist.mkdir(parents=True, exist_ok=True)
    (hist / f"{today.isoformat()}.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    log.info("Report %s: reports/%s/%s", "kept (afternoon run)" if args.no_report else "written",
             cfg["report_slug"], name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
