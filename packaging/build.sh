#!/usr/bin/env bash
# Build the Fiskeretta Diagnostics native app (macOS) and ad-hoc sign it.
# Run on the OS you're targeting — PyInstaller does not cross-compile.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pyinstaller --noconfirm --clean \
  --distpath "$ROOT/dist" --workpath "$ROOT/build" \
  "$ROOT/packaging/fiskeretta.spec"

APP="$ROOT/dist/Fiskeretta Diagnostics.app"
if [ -d "$APP" ]; then
  xattr -cr "$APP" || true
  if codesign --force --deep --sign - "$APP" 2>/dev/null && codesign --verify "$APP" 2>/dev/null; then
    echo "Ad-hoc signed OK."
  else
    # Some macOS versions refuse ad-hoc signing because of the protected
    # com.apple.provenance xattr. Leave the bundle cleanly unsigned — it runs
    # locally on Intel; on Apple Silicon, run `codesign --force --deep -s - "$APP"`
    # on your own machine. See packaging/README.md.
    find "$APP" -name _CodeSignature -type d -exec rm -rf {} + 2>/dev/null || true
    echo "Note: left UNSIGNED (ad-hoc signing failed on this macOS). See packaging/README.md."
  fi
fi

echo "Done: ${APP:-dist/}"
