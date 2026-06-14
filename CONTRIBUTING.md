# Contributing to Fiskeretta Diagnostics

Thanks for helping improve a free, community diagnostics tool for the Fisker Ocean.

## Ground rules

- Be respectful — see the [Code of Conduct](CODE_OF_CONDUCT.md).
- **This tool talks to a real car.** Reads are safe; the one write action is
  clearing DTCs. Treat anything touching the UDS or clear paths with extra care,
  and do all on-car testing on a **parked** vehicle only.

## Dev setup

Requires Python 3.11–3.13.

```
pip install -r requirements.txt      # bleak (3.x), aiohttp, pywebview
python3 -m fiskeretta.app            # native window — auto-connects to a saved dongle
python3 -m fiskeretta.server         # same UI in a browser at http://localhost:8765
python3 -m fiskeretta.scan           # CLI: scan for the BLE dongle
```

No hardware handy? The server still runs — it just reports "no dongle found".

## Project layout

| Path | What |
|---|---|
| `fiskeretta/connection.py` | Persistent BLE/ELM327 connection manager |
| `fiskeretta/core.py` | ELM327 session (send / notify) over BLE |
| `fiskeretta/uds.py` | UDS services — read/clear DTCs, VIN, ECU discovery |
| `fiskeretta/report.py`, `registry.py`, `modules.py` | Scan assembly + module map |
| `fiskeretta/dtc_catalog.py` | DTC descriptions (bundled `data/dtc_combined.json`) |
| `fiskeretta/server.py` | aiohttp + WebSocket bridge to the UI |
| `fiskeretta/static/index.html` | The entire front end (one self-contained file) |

## Making a change

1. Fork, then branch off `main`: `git checkout -b short-description`.
2. Match the surrounding style. Keep `index.html` self-contained — no build step,
   no bundler, vanilla JS.
3. There's no test suite yet, so before you push, at minimum:
   ```
   python -m compileall -q fiskeretta
   python -c "import fiskeretta.server"
   ```
   CI runs exactly this on every PR.
4. Open a PR against `main` describing **what** changed and **why**. If it touches
   BLE/UDS, say how you tested it on hardware.

## Good to know

- `bleak` is pinned to `>=3,<4` — the 3.x CoreBluetooth (macOS) / WinRT (Windows)
  behavior is what's tested. Don't loosen the pin without testing on a real dongle.
- `fiskeretta/data/dtc_combined.json` is community/manual-derived **reference data**,
  not original source; regenerate it with `tools/build_dtc_combined.py`.
- Packaging the desktop app: `pyinstaller packaging/fiskeretta.spec` on each target
  OS (no cross-compiling). See `packaging/README.md`.
