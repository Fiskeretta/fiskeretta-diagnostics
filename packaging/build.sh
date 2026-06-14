#!/usr/bin/env bash
# Build the Fiskeretta Diagnostics native app (macOS), ad-hoc sign it correctly,
# and produce the release zip. Run on the OS you're targeting — PyInstaller does
# not cross-compile.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pyinstaller --noconfirm --clean \
  --distpath "$ROOT/dist" --workpath "$ROOT/build" \
  "$ROOT/packaging/fiskeretta.spec"

APP_NAME="Fiskeretta Diagnostics.app"
APP="$ROOT/dist/$APP_NAME"
ZIP="$ROOT/dist/Fiskeretta-Diagnostics-macOS.zip"

if [ ! -d "$APP" ]; then
  echo "No app bundle at $APP — nothing to sign." >&2
  exit 1
fi

# Sign + package in a clean local staging dir. Building inside a cloud-synced
# folder (iCloud Desktop, Synology Drive, Dropbox) leaves a com.apple.FinderInfo
# xattr on the bundle that codesign rejects ("resource fork, Finder information,
# or similar detritus not allowed") and that the file provider re-adds if you
# strip it in place. Staging to /tmp sidesteps that entirely.
STAGE="$(mktemp -d)"
SAPP="$STAGE/$APP_NAME"
ditto "$APP" "$SAPP"
xattr -cr "$SAPP"
find "$SAPP" -name _CodeSignature -type d -prune -exec rm -rf {} + 2>/dev/null || true

# Ad-hoc sign bottom-up. `codesign --deep` mishandles PyInstaller's bundled
# Python.framework, so sign nested Mach-O objects first, then the framework,
# then seal the outer bundle last (no --deep). A valid seal turns the download
# warning from the dead-end "damaged" error into the normal "unidentified
# developer" prompt that right-click > Open (or Open Anyway) can clear.
echo "Ad-hoc signing $APP_NAME ..."
find "$SAPP" -type f \( -name "*.so" -o -name "*.dylib" \) -print0 \
  | xargs -0 codesign --force --sign - --timestamp=none

FW="$SAPP/Contents/Frameworks/Python.framework"
if [ -d "$FW" ]; then
  codesign --force --sign - --timestamp=none "$FW/Versions/Current/Python"
  codesign --force --sign - --timestamp=none "$FW"
fi

codesign --force --sign - --timestamp=none "$SAPP/Contents/MacOS/Fiskeretta Diagnostics"
codesign --force --sign - --timestamp=none "$SAPP"

if ! codesign --verify --deep --strict "$SAPP"; then
  echo "ERROR: ad-hoc signature failed to verify." >&2
  exit 1
fi
echo "Ad-hoc signed OK (seal valid)."

# Refresh dist/ with the signed bundle and build the release zip from the clean
# staging copy. Use ditto (not zip) so framework symlinks and the signature are
# preserved.
rm -rf "$APP"
ditto "$SAPP" "$APP"
rm -f "$ZIP"
ditto -c -k --keepParent "$SAPP" "$ZIP"
rm -rf "$STAGE"

echo "Done."
echo "  App: $APP"
echo "  Zip: $ZIP"
