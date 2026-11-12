"""Entry point: python -m oos.run --client polaris"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .alerts import detect, long_term, publish
from .checks import run_checks
from .classify import OOS, build_notes, classify, counts, new_state
from .measure import run_measure
from .report2 import render

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Prague")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--no-report", action="store_true",
                    help="afternoon run: update state, dashboard data and alerts, keep the morning report")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("oos")

    cfg = yaml.safe_load((ROOT / "config" / f"{args.client}.yml").read_text(encoding="utf-8"))
    state_path = ROOT / "state" / f"{args.client}.json"
    prev = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None

    meas = run_measure(cfg)                       # one uninterrupted pass, restore in finally
    now = datetime.now(TZ)
    today = now.date()
    cls = classify(cfg, meas, prev, today)
    failed = [z["name"] for z in cfg["zones"] if meas["zones"].get(z["key"], {}).get("status") != "OK"]
    notes = build_notes(cfg, cls, prev, today, failed)
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
    tot = counts(cfg, cls["positions"])
    ctx = {"total": {"oos": tot[OOS], "listed": tot["listed"], "avail": tot["avail"]},
           "prev_avail": counts(cfg, prev["positions"])["avail"] if prev and prev.get("positions") else None,
           "long": long_term(cfg, cls, events), "long_min": int(cfg.get("long_oos_threshold_days", 3)),
           "weekly": now.weekday() == 0 and not args.no_report}       # Monday morning: weekly digest
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
