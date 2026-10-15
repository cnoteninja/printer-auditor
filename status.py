#!/usr/bin/env python3
"""
status.py: plain-English printer/cartridge status labels, in ONE place.

Every printer and cartridge maps to exactly one label:

    OK          - nothing to do
    Order soon  - stock the replacement cartridge now
    Critical    - reorder/fix now (offline printers are always Critical)

Thresholds (also documented in README):

    Critical:   level <= 10%   OR   projected empty within 3 days  OR  offline
    Order soon: level <= 25%   OR   projected empty within 14 days
    OK:         everything else

printer_audit.py, predict_toner.py, and morning_digest.py all import this
module so the labels and thresholds stay consistent everywhere.

Zero third-party dependencies.
"""

CRITICAL_PCT = 10    # at or below this -> Critical
SOON_PCT = 25        # at or below this -> Order soon
CRITICAL_DAYS = 3    # projected empty within this -> Critical
SOON_DAYS = 14       # projected empty within this -> Order soon

OK = "OK"
ORDER_SOON = "Order soon"
CRITICAL = "Critical"

_LABELS = (OK, ORDER_SOON, CRITICAL)
_SEVERITY = {OK: 0, ORDER_SOON: 1, CRITICAL: 2}

_ANSI = {OK: "\033[32m", ORDER_SOON: "\033[33m", CRITICAL: "\033[31m"}
_ANSI_RESET = "\033[0m"

_CSS_CLASS = {OK: "ok", ORDER_SOON: "soon", CRITICAL: "crit"}


def cartridge_status(level_pct, days_left=None):
    """Plain-English status for one supply.

    level_pct: int percent, or "unknown"/"ok"/None when the printer gave no
               usable reading (nothing to flag -> OK)
    days_left: predicted days until empty (float) or None
    """
    if isinstance(level_pct, bool):
        level_pct = int(level_pct)
    if isinstance(level_pct, int):
        if level_pct <= CRITICAL_PCT:
            return CRITICAL
        if level_pct <= SOON_PCT:
            return ORDER_SOON
    if isinstance(days_left, (int, float)):
        if days_left <= CRITICAL_DAYS:
            return CRITICAL
        if days_left <= SOON_DAYS:
            return ORDER_SOON
    return OK


def printer_status(poll_info, days_by_supply=None):
    """Plain-English status for a whole printer.

    poll_info: a poll_printer() dict. Printers that didn't answer SNMP are
               always Critical.
    days_by_supply: {supply desc: days_left} from predict_toner (optional)
    """
    if not poll_info.get("snmp"):
        return CRITICAL
    days_by_supply = days_by_supply or {}
    worst = OK
    for s in poll_info.get("supplies") or []:
        st = cartridge_status(s.get("pct"), days_by_supply.get(s.get("desc")))
        if _SEVERITY[st] > _SEVERITY[worst]:
            worst = st
    return worst


def worse(a, b):
    """Return whichever of two labels is more severe."""
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


def ansi(label):
    """Color-code a label for terminal output (green/yellow/red).

    Passes the label through unchanged when stdout is not a TTY, so piped
    output stays clean.
    """
    import sys
    if label not in _ANSI or not sys.stdout.isatty():
        return label
    return "%s%s%s" % (_ANSI[label], label, _ANSI_RESET)


def css_class(label):
    """CSS class for a label in HTML reports ("ok"/"soon"/"crit")."""
    return _CSS_CLASS.get(label, "ok")


def badge(label):
    """HTML status badge: <span class="badge crit">Critical</span>."""
    import html as _html
    return '<span class="badge %s">%s</span>' % (
        css_class(label), _html.escape(label))


BADGE_CSS = (
    ".badge{display:inline-block;padding:.15em .6em;border-radius:999px;"
    "font-size:.8em;font-weight:600;color:#fff;vertical-align:middle}"
    ".badge.ok{background:#28a745}.badge.soon{background:#d99a00}"
    ".badge.crit{background:#dc3545}"
)
