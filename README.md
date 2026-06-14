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

## Quick start (for drivers)

You need a **Vgate vLinker FD+** Bluetooth OBD2 adapter.

1. **Plug the vLinker** into the OBD2 port — it's under the dash, near your left knee.
2. **Switch the car to ON (Ready).** The car must be awake or the diagnostic bus
   is asleep and nothing will answer.
5. **Open Fiskeretta.** On first launch you'll see a short safety notice — read
   it and click **Continue**.
6. That's it. The app finds the dongle over Bluetooth, connects on its own, and
   automatically reads every module. No pairing, no settings.

> **First-launch security warning (one time).** The app isn't code-signed, so
> the OS will flag it the first time:
>
> - **macOS** — "Fiskeretta can't be opened because it is from an unidentified
>   developer." Right-click (or Control-click) the app → **Open** → **Open**.
>   If macOS still refuses, go to **System Settings → Privacy & Security**,
>   scroll down, and click **Open Anyway** next to the Fiskeretta message, then
>   launch it again.
> - **Windows** — a blue **"Windows protected your PC"** SmartScreen box. Click
>   **More info** → **Run anyway**.
>
> You only do this once per machine. (The warning is just because the app is
> unsigned, not because anything is wrong. Removing it entirely would require a
> paid Apple Developer ID and a Windows signing certificate — not needed to use
> the app.)

**What you'll see**

- **Active faults** — what's wrong *right now*. Start here.
- **Historical** — codes the car logged before but isn't reporting now.
- **Healthy / Unreachable** — modules that answered clean, or didn't answer.
- Click any code to expand it: when it happened, what it means, and the repair
  steps from the Fisker service manual.
- **Export for AI** — copies a ready-made prompt (your active faults + context)
  to paste into ChatGPT/Claude for a plain-English second opinion.
- **About** (bottom-left) — version number and the safety notice again.

**Safety:** only use it while the car is safely **parked** — never while
driving. Clearing codes permanently erases the car's stored diagnostic history.

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

The packaged app **bundles** the combined Fisker DTC catalog
(`fiskeretta/data/dtc_combined.json`, ~4,000 codes), so descriptions and repair
steps work out of the box — no setup. The About box shows how many codes loaded.

For development you can override the bundled data: point
`FISKERETTA_DTC_CATALOG` at a `dtc_index.json` or drop catalog files in
`~/.config/fiskeretta/`. Regenerate the bundled file with
`tools/build_dtc_combined.py` after updating the source manual/export. Any code
not in the catalog still shows, just as raw hex.

## Status

Active development. See `docs/plans/` for what's in flight.
