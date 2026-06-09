# Fiskeretta Diagnostics

A free, open diagnostics tool for the **Fisker Ocean** — a community-built
alternative to FreeSker / OceanLink Pro now that Fisker is gone and the official
tools and service are no longer available.

It talks directly to a **Vgate vLinker FD+** (BLE / ELM327 dongle) plugged into
the OBD2 port and reads what the car actually reports: VIN, diagnostic trouble
codes (DTCs) across every module, freeze-frame data, and — with a local copy of
the DTC manual — plain-English descriptions and repair steps.

> Built on protocol knowledge published by the Fisker Owners Association
> community (`puddletools/CAN`, `puddletools/SaavyScripts`). Use at your own risk.

## What it does today

- Connects to the dongle over BLE and holds one persistent connection.
- Reads and decodes the **VIN** (and decodes the model/trim via NHTSA vPIC).
- Scans **DTCs** across all known modules, sorted into what's *failing now* vs
  *historical* vs noise, with per-module counts.
- Drills into a fault for its freeze-frame (odometer + timestamp decoded) and,
  if you supply the DTC manual, the full troubleshooting writeup.
- Runs as a native desktop app (macOS / Windows) or in a browser.

## Build / run

```
pip3 install -r requirements.txt
python3 -m fiskeretta.app        # native app window (recommended)
python3 -m fiskeretta.server     # same UI in a browser: http://localhost:8765
python3 -m fiskeretta.scan       # CLI: find and connect to the vLinker over BLE
```

Package a double-click app with `packaging/build.sh` (see `packaging/README.md`).

## DTC descriptions

The tool ships with **no** DTC text of its own. If you have a local copy of the
Fisker DTC manual (as `dtc_index.json` + `fisker_dtc.jsonl`), point
`FISKERETTA_DTC_CATALOG` at it or drop it in `~/.config/fiskeretta/`, and scans
translate automatically. Without it, codes show as raw hex.

## Status

Active development. See `docs/plans/` for what's in flight.
