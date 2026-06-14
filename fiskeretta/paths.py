"""Shared filesystem locations for user data (config, saved scans, caches).

The data dir lives in the OS-native per-user location so it survives app updates
and is found by standard backup tools. Earlier versions used ~/.config/fiskeretta
on every platform; storage.migrate_legacy_dir() moves that to the native dir on
first launch.
"""

import os
import sys
from pathlib import Path

APP_NAME = "Fiskeretta"

# Where older builds stored data on macOS/Windows (XDG path is still correct on Linux).
LEGACY_CONFIG_DIR = Path.home() / ".config" / "fiskeretta"


def _native_config_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / APP_NAME
    return Path.home() / ".config" / "fiskeretta"  # Linux/other: XDG is already native


CONFIG_DIR = _native_config_dir()
SCANS_DIR = CONFIG_DIR / "scans"
