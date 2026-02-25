"""Desktop entry-point: wraps the FastAPI app inside a pywebview window."""

from __future__ import annotations

import threading
import time

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

    window = webview.create_window(
        title="FileTools",
        url=f"http://{host}:{port}",
        width=1200,
        height=800,
        resizable=True,
    )

    def _on_loaded() -> None:
        set_webview_window(window)

    webview.start(_on_loaded)


if __name__ == "__main__":  # pragma: no cover
    run_desktop()
