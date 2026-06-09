"""Shared filesystem locations for user data (config, saved scans, caches)."""

from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "fiskeretta"
SCANS_DIR = CONFIG_DIR / "scans"
