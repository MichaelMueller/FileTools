"""Desktop entry-point: wraps the FastAPI app inside a pywebview window."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import uvicorn
import webview

from file_tools.main import app, set_webview_window

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8765


def _run_server(host: str, port: int) -> None:
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def run_desktop(host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT) -> None:
    """Start the FastAPI server in a daemon thread and open a pywebview window."""
    thread = threading.Thread(target=_run_server, args=(host, port), daemon=True)
    thread.start()
    # Give uvicorn a moment to bind the port before opening the window.
    time.sleep(1)

    _icon_path = str(Path(__file__).parent / "static" / "icon.ico")
    window = webview.create_window(
        title="FileTools",
        url=f"http://{host}:{port}",
        width=1200,
        height=800,
        resizable=True,
    )

    def _on_loaded() -> None:
        set_webview_window(window)
        # Set the Windows form icon (pywebview's icon= only works on GTK/QT)
        try:
            from webview.platforms.winforms import BrowserView  # noqa: PLC0415
            from System.Drawing import Icon as WinIcon  # type: ignore[import]  # noqa: PLC0415

            form = BrowserView.instances.get(window.uid)
            if form is not None:
                form.Icon = WinIcon(_icon_path)
        except Exception:  # noqa: BLE001
            pass  # non-critical – fall back to default icon

    webview.start(_on_loaded, gui="edgechromium", icon=_icon_path, private_mode=False)


if __name__ == "__main__":  # pragma: no cover
    run_desktop()
