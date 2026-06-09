# Packaging Fiskeretta Diagnostics

Builds a double-click native app via PyInstaller. **Run the build on each target
OS** — PyInstaller does not cross-compile (you can't make a Windows `.exe` from a
Mac).

## Build

```
pip3 install -r requirements.txt -r requirements-dev.txt
pyinstaller packaging/fiskeretta.spec
```

Output:

- **macOS:** `dist/Fiskeretta Diagnostics.app` — double-click to launch.
- **Windows:** `dist/Fiskeretta Diagnostics/Fiskeretta Diagnostics.exe` (needs the WebView2 runtime,
  pre-installed on Win10/11).

## Icon (optional)

Drop `packaging/icon.icns` (macOS) or `packaging/icon.ico` (Windows) and rebuild;
the spec picks it up automatically. Without one, the default PyInstaller icon is
used.

## macOS notes

- **Bluetooth permission:** the spec sets `NSBluetoothAlwaysUsageDescription`, so
  macOS prompts for Bluetooth access on first launch. Allow it, or the app can't
  see the dongle.
- **Gatekeeper:** the app is unsigned (or ad-hoc signed). First launch:
  right-click → **Open**, or clear quarantine with
  `xattr -dr com.apple.quarantine "dist/Fiskeretta Diagnostics.app"`. Proper signing needs
  an Apple Developer ID (not required for personal use).
- **Ad-hoc signing / `com.apple.provenance`:** on some macOS versions, ad-hoc
  signing fails with *"resource fork, Finder information, or similar detritus not
  allowed"* because of a protected `com.apple.provenance` xattr that `xattr -cr`
  can't strip. `build.sh` then leaves the bundle **unsigned**, which runs fine
  locally on **Intel** Macs. On **Apple Silicon**, a valid signature is required
  to launch — sign on your own machine with
  `codesign --force --deep -s - "dist/Fiskeretta Diagnostics.app"` (usually succeeds on a
  normal macOS release).

## DTC catalog in the packaged app

The Fisker DTC manual is intentionally kept out of the bundle. To get
descriptions in the packaged app, drop `dtc_index.json` at either:

- `~/.config/fiskeretta/dtc_index.json`, or
- next to the app executable,

or set `FISKERETTA_DTC_CATALOG=/path/to/dtc_index.json`. Without it the app still
runs; codes just show as raw hex.
