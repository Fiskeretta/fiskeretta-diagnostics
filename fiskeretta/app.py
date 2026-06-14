"""
Native-window launcher for fiskeretta.

Runs the aiohttp server (+ bleak) on a background thread and shows the existing
web UI in a native OS window via pywebview — no browser, no terminal. Cross-
platform: WKWebView on macOS, WebView2 on Windows. Connection persistence and
device memory come from the server's shared ConnectionManager, so launching the
app auto-connects to the remembered dongle.

    python3 -m fiskeretta.app
"""

import asyncio
import sys
import threading
from pathlib import Path
from typing import Optional

from aiohttp import web

from . import server

HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"


def _icon_path() -> Optional[str]:
    """Locate the app icon for the current platform.

    Windows' WinForms backend loads the icon via System.Drawing.Icon, which
    accepts ONLY a real .ico — handing it a PNG throws "Argument 'picture' must
    be a picture that can be used as a Icon" on the GUI thread and takes the whole
    app down (the window dies and the UI reports "server connection lost"). So we
    return a .ico on Windows and a PNG elsewhere, never the wrong format."""
    here = Path(__file__).resolve().parent
    mei = Path(getattr(sys, "_MEIPASS", here))
    name = "icon.ico" if sys.platform == "win32" else "icon_512.png"
    candidates = [
        here.parent / "packaging" / name,   # dev tree
        mei / "packaging" / name,            # frozen (if packaging/ is bundled)
        here / "data" / name,                # frozen (collected under fiskeretta/data)
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def _set_macos_dock_icon(path: Optional[str]) -> None:
    """Set the Dock icon for a non-bundled run. pywebview's Cocoa backend ignores
    its `icon=` argument, so a plain `python -m fiskeretta.app` otherwise shows the
    generic Python rocket. (A packaged .app gets its icon from the bundle instead.)"""
    if not path:
        return
    try:
        from AppKit import NSApplication, NSImage
        img = NSImage.alloc().initWithContentsOfFile_(path)
        if img:
            NSApplication.sharedApplication().setApplicationIconImage_(img)
    except Exception:
        pass  # best effort — never block launch on a cosmetic icon

# The server's asyncio loop (created on the background thread), captured so the
# GUI thread can schedule a clean disconnect on window close.
_loop: Optional[asyncio.AbstractEventLoop] = None


def _run_server(ready: threading.Event) -> None:
    """Spin up the aiohttp app on this thread's own event loop and run forever.
    bleak shares this loop; pywebview owns the main thread for the GUI."""
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    runner = web.AppRunner(server.build_app())
    _loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, HOST, PORT)
    _loop.run_until_complete(site.start())

    ready.set()
    _loop.run_forever()


def _shutdown() -> None:
    """Disconnect the dongle cleanly when the window closes (best effort)."""
    if _loop is not None:
        try:
            fut = asyncio.run_coroutine_threadsafe(server._manager.disconnect(), _loop)
            fut.result(timeout=5)
        except Exception:
            pass


def main() -> None:
    try:
        import webview
    except ImportError:
        raise SystemExit(
            "pywebview is not installed.\n"
            "Install it with:  pip3 install -r requirements.txt\n"
            "(or:              pip3 install pywebview)"
        )

    ready = threading.Event()
    threading.Thread(target=_run_server, args=(ready,), daemon=True).start()
    if not ready.wait(timeout=10):
        raise SystemExit("fiskeretta server failed to start within 10s.")

    icon = _icon_path()
    if sys.platform == "darwin":
        _set_macos_dock_icon(icon)  # Dock icon for the non-bundled dev run

    window = webview.create_window("Fiskeretta Diagnostics", URL, width=1180, height=940, min_size=(900, 620))
    try:
        window.events.closing += lambda: _shutdown()
    except Exception:
        pass  # event API varies by pywebview version; daemon thread dies on exit anyway

    # icon= sets the taskbar/window icon on Windows & Linux; macOS uses the Dock
    # icon set above (its Cocoa backend ignores this argument).
    start_kwargs = {"icon": icon} if (icon and sys.platform != "darwin") else {}
    webview.start(**start_kwargs)


if __name__ == "__main__":
    main()
