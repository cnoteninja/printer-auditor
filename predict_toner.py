#!/usr/bin/env python3
"""
predict_toner: days-until-empty estimates from audit history.

Reads data/toner_history.csv (appended to by printer_audit.py on every audit
run), fits a simple linear depletion rate (level % vs time) per cartridge,
and prints a reorder list:

  1. cartridges already at/below the low-supply threshold (reorder now)
  2. cartridges projected to hit 0% within --threshold days

Part numbers come from cartridges.json (model -> {color: part number}).

Usage:
    python predict_toner.py
    python predict_toner.py --threshold 30
    python predict_toner.py --csv data/toner_history.csv --catalog cartridges.json

Needs at least 2 history points per cartridge before a prediction is made —
run the audit on a schedule (weekly is plenty) and accuracy improves as
history accumulates.

Zero third-party dependencies. Windows / Linux / macOS.
"""

import argparse
import csv
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(HERE, "data", "toner_history.csv")
DEFAULT_CATALOG = os.path.join(HERE, "cartridges.json")


def parse_ts(s):
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


def load_history(path):
    """{(host, model, color, desc): [(timestamp, level), ...]} sorted by time."""
    series = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                level = int(row["level_pct"])
            except (TypeError, ValueError):
                continue
            ts = parse_ts(row.get("timestamp", "") or "")
            if ts is None:
                continue
            key = (row.get("host", ""), row.get("model", ""),
                   row.get("color", ""), row.get("desc", ""))
            series.setdefault(key, []).append((ts, level))
    for pts in series.values():
        pts.sort(key=lambda p: p[0])
    return series


def depletion_days(pts):
    """Linear least-squares fit of level vs time.

    Returns estimated days from the last reading until 0%, or None when a
    prediction can't be made (fewer than 2 points, all readings at the same
    time, or level flat/rising — e.g. a fresh cartridge was installed).
    """
    if len(pts) < 2:
        return None
    t0 = pts[0][0]
    xs = [(t - t0).total_seconds() / 86400.0 for t, _ in pts]  # days
    ys = [float(l) for _, l in pts]
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    denom = n * sum(x * x for x in xs) - sx * sx
    if denom == 0:
        return None
    slope = (n * sum(x * y for x, y in zip(xs, ys)) - sx * sy) / denom
    if slope >= 0:
        return None
    current = ys[-1]
    if current <= 0:
        return 0.0
    return current / -slope


def load_catalog(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def part_number(catalog, model, color):
    """Look up the cartridge part number; graceful when unknown."""
    entry = catalog.get(model)
    if isinstance(entry, dict):
        pn = entry.get((color or "").lower())
        if pn:
            return str(pn)
    return "part number unknown"


def main():
    ap = argparse.ArgumentParser(
        description="Predict toner days-until-empty from audit history")
    ap.add_argument("--csv", default=DEFAULT_CSV,
                    help="history file (default: data/toner_history.csv)")
    ap.add_argument("--catalog", default=DEFAULT_CATALOG,
                    help="cartridge catalog (default: cartridges.json)")
    ap.add_argument("--threshold", type=float, default=14,
                    help="flag cartridges emptying within this many days "
                         "(default: 14)")
    ap.add_argument("--low", type=float, default=10,
                    help="reorder-now level %% (default: 10)")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        print("no history yet: %s — run printer_audit.py first" % args.csv)
        raise SystemExit(1)
    series = load_history(args.csv)
    catalog = load_catalog(args.catalog)
    if catalog:
        known = sum(1 for k in catalog if not k.startswith("_"))
        print("loaded %d model(s) from %s" % (known, args.catalog))

    now_low, projected = [], []
    for (host, model, color, desc), pts in sorted(series.items()):
        current = pts[-1][1]
        pn = part_number(catalog, model, color)
        if current <= args.low:
            now_low.append((host, model, color, current, pn))
            continue
        days = depletion_days(pts)
        if days is not None and days <= args.threshold:
            projected.append((host, model, color, current, days, pn))
    projected.sort(key=lambda r: r[4])

    print("toner reorder list — %d cartridge(s) tracked" % len(series))
    print()
    if now_low:
        print("Reorder now (<= %g%% remaining):" % args.low)
        for host, model, color, level, pn in now_low:
            print("  %-15s  %-30s  %-8s %3d%%  part: %s"
                  % (host, model[:30], color, level, pn))
    else:
        print("Nothing at/below %g%% — good." % args.low)
    print()
    if projected:
        print("Projected empty within %g days:" % args.threshold)
        for host, model, color, level, days, pn in projected:
            print("  %-15s  %-30s  %-8s %3d%%  ~%.1f days left  part: %s"
                  % (host, model[:30], color, level, days, pn))
    else:
        print("Nothing projected to empty within %g days." % args.threshold)


if __name__ == "__main__":
    main()
