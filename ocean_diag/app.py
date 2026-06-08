"""
Native-window launcher for ocean-diag.

Runs the aiohttp server (+ bleak) on a background thread and shows the existing
web UI in a native OS window via pywebview — no browser, no terminal. Cross-
platform: WKWebView on macOS, WebView2 on Windows. Connection persistence and
device memory come from the server's shared ConnectionManager, so launching the
app auto-connects to the remembered dongle.

    python3 -m ocean_diag.app
"""

import asyncio
import threading
from typing import Optional

from aiohttp import web

from . import server

HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"

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
        raise SystemExit("ocean-diag server failed to start within 10s.")

    window = webview.create_window("Ocean Diag", URL, width=900, height=720, min_size=(640, 480))
    try:
        window.events.closing += lambda: _shutdown()
    except Exception:
        pass  # event API varies by pywebview version; daemon thread dies on exit anyway
    webview.start()


if __name__ == "__main__":
    main()
