# printer-auditor

Find every network printer, poll it over SNMP, and generate a fleet report —
models, page counts, and toner levels with low-supply flagging.

Stop discovering dead toner cartridges when a user calls angry. Run this
monthly and reorder before it matters.

**Zero third-party dependencies** — includes a minimal stdlib-only SNMPv2c
client (`snmp.py`), so it runs anywhere Python runs.

## Quick start

```bash
# Discover printers on the subnet, poll them, build the report
python printer_audit.py --subnet 192.168.1.0/24

# Or skip discovery and poll known hosts
python printer_audit.py --hosts 192.168.1.50 192.168.1.51 --community private
```

Open `printers.html`. Reorder anything at 10% or below.

## How it works

1. **Discovery** — scans the subnet for port 9100 (JetDirect), which means
   "printer" with very few false positives
2. **SNMP poll** — pulls model, serial, page count (`prtMarkerLifeCount`),
   and every supply's description/level/capacity from the Printer MIB
3. **Report** — HTML fleet cards with toner bars; supplies ≤10% counted up top

## Files

| File | Purpose |
|---|---|
| `printer_audit.py` | Discovery + polling + HTML report |
| `snmp.py` | Minimal SNMPv2c GET client (stdlib only) |
| `printers.csv` / `printers.html` | Generated output (git-ignored) |

## Requirements

- Python 3.8+, no pip packages
- Printers must allow SNMP (community string, default `public`)
- Windows / Linux / macOS

## License

MIT — use it on client jobs.
