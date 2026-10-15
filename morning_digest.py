#!/usr/bin/env python3
"""
morning_digest: one HTML digest for the morning coffee.

Combines the latest audit and the toner predictions into a single
digest.html, so one glance (or one email) tells you everything:

  1. printers needing attention (Critical / Order soon, offline first)
  2. offline printers
  3. toner reorder list, with part numbers from cartridges.json
  4. top page-count growers over the last 30 days

Usage:
    python morning_digest.py --hosts 192.168.1.50 192.168.1.51
    python morning_digest.py --subnet 192.168.1.0/24
    python morning_digest.py --hosts 192.168.1.50 --mailto you@example.com
    python morning_digest.py --demo            # no printers, no network:
                                              # sample data -> sample digest.html

The audit poll reuses printer_audit.py, predictions reuse predict_toner.py,
and the OK / Order soon / Critical labels come from status.py — nothing is
re-implemented here.

Email (--mailto): uses the "smtp" section of config.json. Credentials are
NEVER hardcoded — username/password may use ${ENV_VAR} expansion, and any
missing value falls back to SMTP_HOST / SMTP_PORT / SMTP_USERNAME /
SMTP_PASSWORD / SMTP_FROM environment variables. See config.example.json.

Zero third-party dependencies. Windows / Linux / macOS.
"""

import argparse
import csv
import datetime
import html
import json
import os
import smtplib
import sys
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import printer_audit  # noqa: E402
import predict_toner  # noqa: E402
import status  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_DIR = os.path.join(HERE, "demo")  # bundled sample data (never data/, which is git-ignored)
DEMO_PRINTERS = os.path.join(DEMO_DIR, "sample_printers.json")
DEMO_HISTORY = os.path.join(DEMO_DIR, "sample_toner_history.csv")
DEFAULT_CONFIG = os.path.join(HERE, "config.json")
DEFAULT_CATALOG = os.path.join(HERE, "cartridges.json")

GROWTH_DAYS = 30
TOP_GROWERS = 5


# ---------------------------------------------------------------- config/smtp

def load_config(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _expand(value):
    """${VAR} / $VAR expansion; non-strings pass through untouched."""
    return os.path.expandvars(value) if isinstance(value, str) else value


def smtp_settings(config):
    """Merge config.json's "smtp" section with environment fallbacks."""
    s = config.get("smtp") or {}
    if not isinstance(s, dict):
        s = {}
    return {
        "host": _expand(s.get("host")) or os.environ.get("SMTP_HOST"),
        "port": int(s.get("port") or os.environ.get("SMTP_PORT") or 587),
        "use_tls": bool(s.get("use_tls", True)),
        "username": _expand(s.get("username")) or os.environ.get("SMTP_USERNAME"),
        "password": _expand(s.get("password")) or os.environ.get("SMTP_PASSWORD"),
        "from_addr": _expand(s.get("from")) or os.environ.get("SMTP_FROM"),
        "to": s.get("to") or [],
    }


def send_digest(settings, recipients, subject, html_body):
    """Send the digest as an HTML email. Raises RuntimeError on bad config."""
    if not settings["host"]:
        raise RuntimeError(
            "no SMTP host: set smtp.host in config.json or SMTP_HOST env var")
    from_addr = settings["from_addr"] or settings["username"]
    if not from_addr:
        raise RuntimeError(
            "no From address: set smtp.from in config.json or SMTP_FROM env var")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.set_content("This digest is HTML — open it in an HTML-capable mail client.")
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(settings["host"], settings["port"], timeout=20) as smtp:
        if settings["use_tls"]:
            smtp.starttls()
        if settings["username"]:
            smtp.login(settings["username"], settings["password"] or "")
        smtp.send_message(msg)


# ------------------------------------------------------- predictions / growth

def build_predictions(history_path, catalog, low, threshold):
    """{(host, model, color, desc): (current_pct, days_left_or_None, part_no)}."""
    series = predict_toner.load_history(history_path)
    out = {}
    for (host, model, color, desc), pts in series.items():
        current = pts[-1][1]
        days = predict_toner.depletion_days(pts)
        pn = predict_toner.part_number(catalog, model, color)
        out[(host, model, color, desc)] = (current, days, pn)
    return out


def page_growth(history_path, days=GROWTH_DAYS):
    """[(host, model, pages_then, pages_now, growth)] sorted by growth desc.

    'then' = earliest reading in the last `days` days, 'now' = latest.
    """
    if not os.path.exists(history_path):
        return []
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)
    per = {}
    with open(history_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts = predict_toner.parse_ts(row.get("timestamp", "") or "")
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            if ts < cutoff:
                continue
            try:
                pages = int(row.get("pages", ""))
            except (TypeError, ValueError):
                continue
            per.setdefault((row.get("host", ""), row.get("model", "")),
                           []).append((ts, pages))
    out = []
    for (host, model), pts in per.items():
        pts.sort(key=lambda p: p[0])
        then, latest = pts[0][1], pts[-1][1]
        out.append((host, model, then, latest, latest - then))
    out.sort(key=lambda r: r[4], reverse=True)
    return out


def demo_growth(printers):
    """Page growers for --demo, from each sample printer's pages_30d_ago."""
    out = []
    for p in printers:
        if not p.get("snmp"):
            continue
        ago = p.get("pages_30d_ago")
        try:
            then, latest = int(ago), int(p.get("pages"))
        except (TypeError, ValueError):
            continue
        out.append((p["host"], p.get("model", ""), then, latest, latest - then))
    out.sort(key=lambda r: r[4], reverse=True)
    return out


def days_by_supply(predictions):
    """{supply desc: days_left} per host, for status.printer_status()."""
    out = {}
    for (host, _model, _color, desc), (_cur, days, _pn) in predictions.items():
        out.setdefault(host, {})[desc] = days
    return out


# ------------------------------------------------------------------ rendering

def _days_str(days):
    if days is None:
        return "n/a"
    return "~%.0f" % days if days >= 10 else "~%.1f" % days


def build_html(printers, predictions, growers, demo=False):
    e = html.escape
    by_supply_days = days_by_supply(predictions)
    statuses = {p["host"]: status.printer_status(p, by_supply_days.get(p["host"]))
                for p in printers}

    need_attention = sorted(
        (p for p in printers if statuses[p["host"]] != status.OK),
        key=lambda p: (0 if not p.get("snmp") else
                       1 if statuses[p["host"]] == status.CRITICAL else 2,
                       p["host"]))
    offline = [p for p in printers if not p.get("snmp")]
    n_crit = sum(1 for s in statuses.values() if s == status.CRITICAL)
    n_soon = sum(1 for s in statuses.values() if s == status.ORDER_SOON)

    # --- section 1: printers needing attention ---------------------------
    if need_attention:
        cards = []
        for p in need_attention:
            st = statuses[p["host"]]
            if not p.get("snmp"):
                cards.append(
                    "<div class='card'><h3>%s %s</h3>"
                    "<p>Offline — did not answer SNMP: %s</p></div>" % (
                        e(p["host"]), status.badge(st),
                        e(p.get("error", "no response"))))
                continue
            rows = []
            for s in p.get("supplies") or []:
                st_s = status.cartridge_status(
                    s.get("pct"), by_supply_days.get(p["host"], {}).get(s.get("desc")))
                rows.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    e(s["desc"]), printer_audit.toner_bar(s.get("pct")),
                    status.badge(st_s)))
            cards.append(
                "<div class='card'><h3>%s %s</h3>"
                "<p>Model: %s · Pages: %s</p>"
                "<table><tr><th>Supply</th><th>Level</th><th>Status</th></tr>"
                "%s</table></div>" % (
                    e(p["host"]), status.badge(st),
                    e(p.get("model") or "?"),
                    e(str(p.get("pages") if p.get("pages") is not None else "?")),
                    "".join(rows)))
        attention_html = "".join(cards)
    else:
        attention_html = "<p>All printers OK. Nothing needs attention. Enjoy the coffee.</p>"

    # --- section 3: toner reorder list ------------------------------------
    reorder = []
    for (host, model, color, desc), (current, days, pn) in predictions.items():
        st = status.cartridge_status(current, days)
        if st == status.OK:
            continue
        reorder.append((st, days if days is not None else float("inf"),
                        host, model, color, desc, current, days, pn))
    reorder.sort(key=lambda r: (0 if r[0] == status.CRITICAL else 1, r[1]))
    if reorder:
        rows = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td></tr>" % (
                e(host), e(model or "?"), e(color or "?"), e(desc),
                "%d%%" % current, _days_str(days),
                "%s<br><small>%s</small>" % (status.badge(st), e(pn)))
            for st, _o, host, model, color, desc, current, days, pn in reorder)
        reorder_html = (
            "<table><tr><th>Printer</th><th>Model</th><th>Color</th>"
            "<th>Cartridge</th><th>Level</th><th>Days left</th>"
            "<th>Status / Part #</th></tr>%s</table>" % rows)
    else:
        reorder_html = "<p>No cartridges need reordering.</p>"

    # --- section 4: top page-count growers ---------------------------------
    if growers:
        rows = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>+%s</td></tr>" % (
                e(host), e(model or "?"), f"{then:,}", f"{latest:,}", f"{growth:,}")
            for host, model, then, latest, growth in growers[:TOP_GROWERS])
        growers_html = (
            "<table><tr><th>Printer</th><th>Model</th><th>Pages (%dd ago)</th>"
            "<th>Pages (now)</th><th>Growth</th></tr>%s</table>" % (GROWTH_DAYS, rows))
    else:
        growers_html = ("<p>Not enough page-count history yet — it builds up as "
                        "the audit runs (data/page_history.csv is local-only).</p>")

    stamped = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    title_note = " (demo data)" if demo else ""
    return """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Printer Morning Digest</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1000px;margin:2em auto;padding:0 1em}}
.card{{border:1px solid #ddd;border-radius:8px;padding:1em;margin:1em 0}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:.4em .6em;text-align:left}}
th{{background:#f5f5f5}}.bar{{background:#eee;border-radius:4px;height:14px;width:180px;display:inline-block}}
.bar div{{height:14px;border-radius:4px}}.g{{background:#28a745}}.y{{background:#d99a00}}.r{{background:#dc3545}}
.b{{background:#eee;padding:.15em .5em;border-radius:4px}}
{badges}
h2{{margin-top:2em;border-bottom:2px solid #eee;padding-bottom:.3em}}</style></head><body>
<h1>Printer Morning Digest{note}</h1>
<p>{ts} · {n} printer(s): <b>{c} critical</b>, <b>{s} order soon</b>, {off} offline.</p>
<h2>Needs attention</h2>
{attention}
<h2>Offline printers</h2>
{offline_html}
<h2>Toner reorder list</h2>
{reorder}
<h2>Top page-count growers ({gd} days)</h2>
{growers}
<p><small>Generated by <a href="https://github.com/cnoteninja/printer-auditor">printer-auditor</a>.
Critical = ≤10% left, empty within 3 days, or offline · Order soon = ≤25% or empty within 14 days.</small></p>
</body></html>""".format(
        note=title_note, ts=stamped, n=len(printers), c=n_crit, s=n_soon,
        off=len(offline), badges=status.BADGE_CSS,
        attention=attention_html,
        offline_html=("".join(
            "<div class='card'><h3>%s %s</h3><p>%s</p></div>" % (
                e(p["host"]), status.badge(status.CRITICAL),
                e(p.get("error", "no response")))
            for p in offline) or "<p>No offline printers.</p>"),
        reorder=reorder_html, growers=growers_html, gd=GROWTH_DAYS)


def print_terminal_summary(printers, predictions, statuses):
    print()
    print("morning digest — %d printer(s)" % len(printers))
    for p in printers:
        st = statuses[p["host"]]
        print("  %-15s  %s" % (p["host"], status.ansi(st)))
    flagged = sum(1 for s in statuses.values() if s != status.OK)
    if flagged:
        print("%d printer(s) need attention — see digest.html" % flagged)
    else:
        print("all printers OK")


# --------------------------------------------------------------------- main

def run_demo(args):
    print("demo mode: bundled sample data, no network")
    with open(DEMO_PRINTERS, encoding="utf-8") as fh:
        printers = json.load(fh)
    catalog = predict_toner.load_catalog(args.catalog)
    predictions = build_predictions(DEMO_HISTORY, catalog, args.low, args.threshold)
    growers = demo_growth(printers)
    html_doc = build_html(printers, predictions, growers, demo=True)
    out = os.path.join(HERE, os.path.basename(args.out))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html_doc)
    by_supply_days = days_by_supply(predictions)
    statuses = {p["host"]: status.printer_status(p, by_supply_days.get(p["host"]))
                for p in printers}
    print_terminal_summary(printers, predictions, statuses)
    print("sample digest -> %s" % out)
    return out, html_doc, printers


def main():
    ap = argparse.ArgumentParser(description="Morning coffee digest for the printer fleet")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--subnet", help="e.g. 192.168.1.0/24")
    g.add_argument("--hosts", nargs="+", help="explicit host list")
    ap.add_argument("--demo", action="store_true",
                    help="run against bundled sample data (no network)")
    ap.add_argument("--community", default="public")
    ap.add_argument("--csv", default=os.path.join(HERE, "data", "toner_history.csv"),
                    help="toner history file")
    ap.add_argument("--pages", default=os.path.join(HERE, "data", "page_history.csv"),
                    help="page-count history file")
    ap.add_argument("--catalog", default=DEFAULT_CATALOG)
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help="config file (default: config.json)")
    ap.add_argument("--threshold", type=float, default=status.SOON_DAYS,
                    help="flag cartridges emptying within this many days")
    ap.add_argument("--low", type=float, default=status.CRITICAL_PCT,
                    help="reorder-now level %%")
    ap.add_argument("--out", default="digest.html")
    ap.add_argument("--mailto", nargs="*", metavar="ADDR",
                    help="email the digest; with no ADDRs uses smtp.to from config")
    args = ap.parse_args()

    if args.demo:
        out, html_doc, _printers = run_demo(args)
    else:
        if not args.subnet and not args.hosts:
            ap.error("need --subnet, --hosts, or --demo")
        hosts = printer_audit.discover(args.subnet) if args.subnet else args.hosts
        if not hosts:
            print("no printers found")
            raise SystemExit(0)
        printers = []
        for h in hosts:
            print("polling %s..." % h)
            printers.append(printer_audit.poll_printer(h, args.community))
        printer_audit.append_history(printers)
        printer_audit.append_page_history(printers)
        catalog = predict_toner.load_catalog(args.catalog)
        predictions = build_predictions(args.csv, catalog, args.low, args.threshold)
        growers = page_growth(args.pages)
        html_doc = build_html(printers, predictions, growers)
        out = os.path.join(HERE, os.path.basename(args.out))
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html_doc)
        by_supply_days = days_by_supply(predictions)
        statuses = {p["host"]: status.printer_status(
            p, by_supply_days.get(p["host"])) for p in printers}
        print_terminal_summary(printers, predictions, statuses)
        print("digest -> %s" % out)

    if args.mailto is not None:
        config = load_config(args.config)
        settings = smtp_settings(config)
        recipients = args.mailto or settings["to"]
        if not recipients:
            print("no recipients: pass addresses to --mailto or set smtp.to in config")
            raise SystemExit(1)
        subject = "Printer morning digest — %s%s" % (
            datetime.date.today().isoformat(), " (demo)" if args.demo else "")
        try:
            send_digest(settings, recipients, subject, html_doc)
        except Exception as exc:
            print("email failed: %s" % exc)
            raise SystemExit(1)
        print("emailed digest to %s" % ", ".join(recipients))


if __name__ == "__main__":
    main()
