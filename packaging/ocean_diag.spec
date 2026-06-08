# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Ocean Diag.

Build (run on each target OS — no cross-compiling):
    pip3 install -r requirements.txt -r requirements-dev.txt
    pyinstaller packaging/ocean_diag.spec

Output:
    macOS:   dist/Ocean Diag.app
    Windows: dist/Ocean Diag/Ocean Diag.exe
"""
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
sys.path.insert(0, ROOT)  # make `ocean_diag` importable for collect_* below

# Optional icon: drop packaging/icon.icns (macOS) or packaging/icon.ico (Windows).
_icns = os.path.join(SPECPATH, "icon.icns")
_ico = os.path.join(SPECPATH, "icon.ico")
if sys.platform == "darwin" and os.path.exists(_icns):
    icon = _icns
elif sys.platform == "win32" and os.path.exists(_ico):
    icon = _ico
else:
    icon = None

# Bundle ocean_diag/static/* and pull in dynamically-imported backends.
datas = collect_data_files("ocean_diag")
hiddenimports = collect_submodules("bleak") + collect_submodules("webview")

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ocean Diag",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app — no terminal
    icon=icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Ocean Diag",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Ocean Diag.app",
        icon=icon,
        bundle_identifier="com.oceandiag.app",
        info_plist={
            # Required or macOS silently denies Bluetooth to the bundled app.
            "NSBluetoothAlwaysUsageDescription": "Ocean Diag uses Bluetooth to talk to your OBD2 dongle.",
            "NSBluetoothPeripheralUsageDescription": "Ocean Diag uses Bluetooth to talk to your OBD2 dongle.",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "10.15.0",
        },
    )
