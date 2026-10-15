#!/usr/bin/env python3
"""
check_setup: is this machine ready to run printer-auditor?

Plain-English PASS / FAIL / SKIP lines, no tracebacks. Checks:

  1. Python version (needs 3.8+)
  2. config.json exists and is valid JSON (only checked if the file exists;
     email config stays optional)
  3. bundled demo data parses (so --demo always works)
  4. TCP reachability (port 9100, short timeout) to each host listed under
     "hosts" in config.json — unreachable hosts are reported gracefully,
     not as crashes

Usage:
    python check_setup.py
    python check_setup.py --config myconfig.json

Exit code 0 when nothing FAILs, 1 otherwise.

Zero third-party dependencies. Windows / Linux / macOS.
"""

import argparse
import json
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_PRINTERS = os.path.join(HERE, "demo", "sample_printers.json")
DEMO_HISTORY = os.path.join(HERE, "demo", "sample_toner_history.csv")
MIN_PYTHON = (3, 8)
PORT = 9100
TIMEOUT = 2.0


def main():
    ap = argparse.ArgumentParser(description="Readiness checks for printer-auditor")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"),
                    help="config file to validate (default: config.json)")
    args = ap.parse_args()

    failures = 0

    def say(result, message):
        print("%-4s  %s" % (result, message))

    # 1. Python version ----------------------------------------------------
    if sys.version_info >= MIN_PYTHON:
        say("PASS", "Python %s (>= 3.8)" % sys.version.split()[0])
    else:
        say("FAIL", "Python %s is too old — need 3.8 or newer"
            % sys.version.split()[0])
        failures += 1

    # 2. config file -------------------------------------------------------
    config = {}
    if os.path.exists(args.config):
        try:
            with open(args.config, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("top level must be a JSON object")
            config = data
            say("PASS", "%s is valid JSON" % os.path.basename(args.config))
        except ValueError as exc:
            say("FAIL", "%s is not valid JSON: %s"
                % (os.path.basename(args.config), exc))
            failures += 1
        except OSError as exc:
            say("FAIL", "could not read %s: %s"
                % (os.path.basename(args.config), exc))
            failures += 1
        smtp = config.get("smtp") if isinstance(config.get("smtp"), dict) else {}
        if smtp:
            if smtp.get("host"):
                say("PASS", "smtp.host set (%s) — email can be attempted"
                    % smtp["host"])
            else:
                say("FAIL", "smtp section present but smtp.host is missing "
                            "(or set SMTP_HOST)")
                failures += 1
    else:
        say("SKIP", "no %s — email disabled (copy config.example.json to "
                    "config.json to enable it)" % os.path.basename(args.config))

    # 3. demo bundle --------------------------------------------------------
    demo_ok = True
    try:
        with open(DEMO_PRINTERS, encoding="utf-8") as fh:
            demo_printers = json.load(fh)
        n = len(demo_printers) if isinstance(demo_printers, list) else 0
        if n == 0:
            raise ValueError("no sample printers in file")
    except (OSError, ValueError) as exc:
        demo_ok = False
        say("FAIL", "demo data unreadable (demo/sample_printers.json): %s" % exc)
        failures += 1
    try:
        with open(DEMO_HISTORY, encoding="utf-8") as fh:
            rows = sum(1 for _ in fh) - 1
        if rows < 1:
            raise ValueError("no history rows")
    except (OSError, ValueError) as exc:
        demo_ok = False
        say("FAIL", "demo data unreadable (demo/sample_toner_history.csv): %s" % exc)
        failures += 1
    if demo_ok:
        say("PASS", "demo bundle OK — %d sample printers, %d history rows "
                    "(try: python morning_digest.py --demo)"
            % (n, rows))

    # 4. TCP reachability ---------------------------------------------------
    hosts = config.get("hosts") or []
    if not isinstance(hosts, list):
        hosts = []
    hosts = [h for h in hosts if isinstance(h, str) and h.strip()]
    if not hosts:
        say("SKIP", "no hosts in config.json — run "
                    "python printer_audit.py --subnet 192.168.1.0/24 to discover")
    else:
        for host in hosts:
            try:
                s = socket.create_connection((host, PORT), timeout=TIMEOUT)
                s.close()
                say("PASS", "%s:%d reachable" % (host, PORT))
            except OSError as exc:
                say("FAIL", "%s:%d unreachable (%s)" % (host, PORT, exc))
                failures += 1

    print()
    if failures:
        print("%d check(s) failed — see above" % failures)
    else:
        print("all checks passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # never a traceback — plain English only
        print("FAIL  unexpected error: %s" % exc)
        raise SystemExit(1)
