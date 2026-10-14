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

# Predict days-until-empty from accumulated history, with part numbers
python predict_toner.py
```

Open `printers.html`. Reorder anything at 10% or below.

## Toner history + depletion prediction

Every audit run appends one row per cartridge per printer to
`data/toner_history.csv` (timestamp, host, model, color, level %). That file
is **local-only** — `data/` is git-ignored so your fleet's supply data never
leaves the machine. Run the audit on a schedule (weekly is plenty) and the
history gets more accurate.

`predict_toner.py` reads the history, fits a simple linear depletion rate
(level % vs time) per cartridge, and prints a reorder list:

```bash
python predict_toner.py                 # flag anything emptying within 14 days
python predict_toner.py --threshold 30  # ...within 30 days
python predict_toner.py --csv data/toner_history.csv --catalog cartridges.json
```

Example output:

```
loaded 3 model(s) from cartridges.json
toner reorder list — 4 cartridge(s) tracked

Reorder now (<= 10% remaining):
  192.168.1.52     Brother HL-L2390DW              black      8%  part: TN-760
  192.168.1.53     Some Mystery Printer 9000       black      9%  part: part number unknown

Projected empty within 14 days:
  192.168.1.50     HP LaserJet M404dn              black     20%  ~10.0 days left  part: CF258A
```

Notes:

- A cartridge needs **at least 2 history points** before a prediction is made.
- Flat or rising levels (fresh cartridge installed) are reported as not
  depleting — no false alarms.
- Part numbers come from `cartridges.json`; models not listed show
  `part number unknown` instead of guessing.

## Cartridge catalog (`cartridges.json`)

Maps printer model → cartridge color → part number, so the reorder list tells
you exactly what to buy:

```json
{
  "HP LaserJet M404dn": {
    "black": "CF258A"
  },
  "HP Color LaserJet Pro MFP M479fdw": {
    "black": "CF410A",
    "cyan": "CF411A",
    "magenta": "CF413A",
    "yellow": "CF412A"
  }
}
```

- **Model names must match the model strings printers report via SNMP**
  (check the model column in `printers.csv` or the HTML report — `predict_toner.py`
  does exact matching).
- Colors are lowercase: `black`, `cyan`, `magenta`, `yellow`. Add your own
  models and colors freely — unknown models/colors fall back to
  `part number unknown`.
- The file is committed to the repo as an editable template (the `.gitignore`
  carves out an exception for it), so edit it in place.

## How it works

1. **Discovery** — scans the subnet for port 9100 (JetDirect), which means
   "printer" with very few false positives
2. **SNMP poll** — pulls model, serial, page count (`prtMarkerLifeCount`),
   and every supply's description/level/capacity from the Printer MIB
3. **History** — appends per-cartridge levels to `data/toner_history.csv`
   (local-only, git-ignored)
4. **Report** — HTML fleet cards with toner bars; supplies ≤10% counted up top
5. **Prediction** — `predict_toner.py` fits a depletion rate per cartridge
   from history and prints a reorder list with part numbers

## Files

| File | Purpose |
|---|---|
| `printer_audit.py` | Discovery + polling + HTML report + history logging |
| `predict_toner.py` | Days-until-empty prediction + reorder list |
| `cartridges.json` | Model → {color: part number} catalog (edit me) |
| `snmp.py` | Minimal SNMPv2c GET client (stdlib only) |
| `data/toner_history.csv` | Per-run cartridge levels (local-only, git-ignored) |
| `printers.csv` / `printers.html` | Generated output (git-ignored) |

## Requirements

- Python 3.8+, no pip packages
- Printers must allow SNMP (community string, default `public`)
- Windows / Linux / macOS

## License

MIT — use it on client jobs.
