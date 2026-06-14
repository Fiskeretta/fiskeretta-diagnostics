"""PyInstaller entry point — launches the native Fiskeretta Diagnostics window."""

import os
import sys

def _strip_mark_of_the_web() -> None:
    """Remove the Windows "Mark of the Web" from our own bundled files.

    When a user downloads the release zip, Windows tags every extracted file
    with a Zone.Identifier alternate data stream marking it as internet-sourced.
    The .NET Framework then refuses to load the MOTW-tagged managed assembly
    pythonnet/runtime/Python.Runtime.dll, so pywebview's WinForms backend dies
    with "Failed to resolve Python.Runtime.Loader.Initialize" before the window
    can open. The app has write access to its own extracted folder, so we clear
    the stream from every bundled file at startup — before anything imports clr.

    Best effort: any failure here is swallowed so it can never block launch.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base or sys.platform != "win32":
        return
    for root, _dirs, files in os.walk(base):
        for name in files:
            try:
                os.remove(os.path.join(root, name) + ":Zone.Identifier")
            except OSError:
                pass  # no stream present, or locked — nothing to do


_strip_mark_of_the_web()

from fiskeretta.app import main

if __name__ == "__main__":
    main()
