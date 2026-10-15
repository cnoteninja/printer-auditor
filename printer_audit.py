#!/usr/bin/env python3
"""
printer-audit: find every network printer and report toner + page counts.

Scans a subnet for JetDirect (9100) hosts, polls each via SNMP (stdlib-only
client in snmp.py), and builds a shareable HTML fleet report with toner bars
and low-supply flagging.

Usage:
    python printer_audit.py --subnet 192.168.1.0/24
    python printer_audit.py --hosts 192.168.1.50 192.168.1.51 --community private

Zero third-party dependencies. Windows / Linux / macOS.
"""

import argparse
import csv
import datetime
import html
import ipaddress
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from snmp import get as snmp_get, SNMPError  # noqa: E402
from status import printer_status, ansi, badge, BADGE_CSS, CRITICAL, ORDER_SOON  # noqa: E402

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "printers.csv")

# Per-run toner history (local only — data/ is git-ignored). Written on every
# audit; read by predict_toner.py for days-until-empty estimates.
HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HISTORY_CSV = os.path.join(HISTORY_DIR, "toner_history.csv")
# Per-run page counts (local only). Read by morning_digest.py for the
# top page-count growers.
PAGE_HISTORY_CSV = os.path.join(HISTORY_DIR, "page_history.csv")

COLOR_KEYWORDS = ("black", "cyan", "magenta", "yellow")

OID = {
    "sys_descr": "1.3.6.1.2.1.1.1.0",
    "model": "1.3.6.1.2.1.25.3.2.1.3.1",
    "serial": "1.3.6.1.2.1.43.5.1.1.17.1",
    "pages": "1.3.6.1.2.1.43.10.2.1.4.1.1",
}
SUPPLY_DESC = "1.3.6.1.2.1.43.11.1.1.6.1.%d"
SUPPLY_LEVEL = "1.3.6.1.2.1.43.11.1.1.9.1.%d"
SUPPLY_MAX = "1.3.6.1.2.1.43.11.1.1.8.1.%d"
SUPPLY_TYPE = "1.3.6.1.2.1.43.11.1.1.5.1.%d"  # 3 = toner, 4 = ink, 9 = drum...


def port_open(host, port, timeout=1.0):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def discover(subnet, workers=64):
    """Hosts with 9100 (JetDirect) open — likely printers."""
    net = ipaddress.ip_network(subnet, strict=False)
    hosts = [str(h) for h in net.hosts()]
    print("scanning %d hosts for port 9100..." % len(hosts))
    found = []

    def check(h):
        return h if port_open(h, 9100) else None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(check, hosts):
            if r:
                found.append(r)
                print("  found:", r)
    return sorted(found)


def poll_printer(host, community):
    info = {"host": host, "snmp": False}
    try:
        base = snmp_get(host, [OID["sys_descr"], OID["model"], OID["serial"],
                               OID["pages"]], community)
    except SNMPError as exc:
        info["error"] = str(exc)
        return info
    info["snmp"] = True
    info["sys_descr"] = str(base.get(OID["sys_descr"], ""))[:80]
    info["model"] = str(base.get(OID["model"], ""))[:80]
    info["serial"] = str(base.get(OID["serial"], ""))
    info["pages"] = base.get(OID["pages"])
    supplies = []
    for i in range(1, 7):
        try:
            r = snmp_get(host, [SUPPLY_DESC % i, SUPPLY_LEVEL % i,
                                SUPPLY_MAX % i, SUPPLY_TYPE % i], community)
        except SNMPError:
            break
        desc = r.get(SUPPLY_DESC % i)
        if desc is None:
            break
        level, mx = r.get(SUPPLY_LEVEL % i), r.get(SUPPLY_MAX % i)
        pct = None
        if isinstance(level, int) and isinstance(mx, int) and mx > 0 and level >= 0:
            pct = round(level / mx * 100)
        elif level == -2:
            pct = "unknown"
        elif level == -3:
            pct = "ok"
        supplies.append({"desc": str(desc)[:40], "pct": pct,
                         "type": r.get(SUPPLY_TYPE % i)})
    info["supplies"] = supplies
    return info


def save_csv(printers):
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["host", "model", "serial", "page_count", "supply", "level_pct"])
        for p in printers:
            if not p.get("snmp"):
                w.writerow([p["host"], "SNMP FAILED", p.get("error", ""), "", "", ""])
                continue
            if p["supplies"]:
                for s in p["supplies"]:
                    w.writerow([p["host"], p["model"], p["serial"], p["pages"],
                                s["desc"], s["pct"]])
            else:
                w.writerow([p["host"], p["model"], p["serial"], p["pages"], "", ""])


def guess_color(desc):
    """Map a supply description like 'Black Toner Cartridge' to a color key."""
    d = desc.lower()
    for c in COLOR_KEYWORDS:
        if c in d:
            return c
    return "other"


def append_history(printers):
    """Append one row per cartridge per printer to data/toner_history.csv."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    new = not os.path.exists(HISTORY_CSV)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["timestamp", "host", "model", "color", "desc", "level_pct"])
        for p in printers:
            if not p.get("snmp"):
                continue
            for s in p.get("supplies") or []:
                if not isinstance(s["pct"], int):  # skip "unknown"/"ok"/None
                    continue
                w.writerow([ts, p["host"], p["model"], guess_color(s["desc"]),
                            s["desc"], s["pct"]])


def append_page_history(printers):
    """Append one row per printer to data/page_history.csv (local-only)."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    new = not os.path.exists(PAGE_HISTORY_CSV)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(PAGE_HISTORY_CSV, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["timestamp", "host", "model", "pages"])
        for p in printers:
            if not p.get("snmp"):
                continue
            try:
                pages = int(p["pages"])
            except (TypeError, ValueError):
                continue
            w.writerow([ts, p["host"], p["model"], pages])


def toner_bar(pct):
    if pct in ("unknown", "ok", None) or pct == "":
        return "<span class='b'>%s</span>" % html.escape(str(pct or "n/a"))
    pct = int(pct)
    cls = "g" if pct > 25 else ("y" if pct > 10 else "r")
    return ("<div class='bar'><div class='%s' style='width:%d%%'></div></div> %d%%"
            % (cls, pct, pct))


def build_html(printers):
    e = html.escape
    cards = []
    low = 0
    n_crit = n_soon = 0
    for p in printers:
        st = printer_status(p)  # no prediction data here — % and offline only
        if st == CRITICAL:
            n_crit += 1
        elif st == ORDER_SOON:
            n_soon += 1
        if not p.get("snmp"):
            cards.append("<div class='card'><h3>%s %s</h3><p>SNMP failed: %s</p></div>"
                         % (e(p["host"]), badge(st), e(p.get("error", ""))))
            continue
        sup_rows = "".join(
            "<tr><td>%s</td><td>%s</td></tr>" % (e(s["desc"]), toner_bar(s["pct"]))
            for s in p["supplies"])
        for s in p["supplies"]:
            if isinstance(s["pct"], int) and s["pct"] <= 10:
                low += 1
        cards.append(
            "<div class='card'><h3>%s %s <small>%s</small></h3>"
            "<p>Model: %s · Serial: %s · Pages: %s</p>"
            "<table><tr><th>Supply</th><th>Level</th></tr>%s</table></div>" % (
                e(p["host"]), badge(st), e(p.get("sys_descr", "")[:40]),
                e(p["model"] or "?"), e(p["serial"] or "?"),
                e(str(p["pages"] if p["pages"] is not None else "?")),
                sup_rows or "<tr><td colspan=2>no supply data</td></tr>"))
    return """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Printer Fleet Audit</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1000px;margin:2em auto;padding:0 1em}}
.card{{border:1px solid #ddd;border-radius:8px;padding:1em;margin:1em 0}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:.4em .6em;text-align:left}}
th{{background:#f5f5f5}}.bar{{background:#eee;border-radius:4px;height:14px;width:220px;display:inline-block}}
.bar div{{height:14px;border-radius:4px}}.g{{background:#28a745}}.y{{background:#ffc107}}.r{{background:#dc3545}}
.b{{background:#eee;padding:.15em .5em;border-radius:4px}}
{badges}</style></head><body>
<h1>Printer Fleet Audit</h1>
<p>{n} printers found · {l} supplies at ≤10% · {c} critical, {s} order soon.</p>
{cards}
<p><small>Generated by <a href="https://github.com/cnoteninja/printer-auditor">printer-auditor</a>.
Levels ≤10% should be reordered now.</small></p>
</body></html>""".format(n=len(printers), l=low, c=n_crit, s=n_soon,
                         badges=BADGE_CSS, cards="".join(cards))


def main():
    ap = argparse.ArgumentParser(description="Audit network printer fleet")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--subnet", help="e.g. 192.168.1.0/24")
    g.add_argument("--hosts", nargs="+", help="explicit host list")
    ap.add_argument("--community", default="public")
    ap.add_argument("--out", default="printers.html")
    args = ap.parse_args()

    hosts = discover(args.subnet) if args.subnet else args.hosts
    if not hosts:
        print("no printers found")
        raise SystemExit(0)
    printers = []
    for h in hosts:
        print("polling %s..." % h)
        info = poll_printer(h, args.community)
        print("  -> %s" % ansi(printer_status(info)))
        printers.append(info)
    save_csv(printers)
    append_history(printers)
    append_page_history(printers)
    print("history appended -> data/toner_history.csv, data/page_history.csv")
    outdir = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(outdir, os.path.basename(args.out))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(build_html(printers))
    ok = sum(1 for p in printers if p.get("snmp"))
    print("done: %d/%d printers polled via SNMP -> %s" % (ok, len(printers), out))


if __name__ == "__main__":
    main()
