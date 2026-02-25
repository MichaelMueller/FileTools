"""Desktop entry-point: wraps the FastAPI app inside a pywebview window."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import uvicorn
import webview

from file_tools.main import app, set_webview_window

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8765
_PORT_ATTEMPTS = 5


def _port_available(host: str, port: int) -> bool:
    """Return *True* if *port* can be bound on *host*."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _find_port(host: str, start: int, attempts: int) -> int:
    """Return the first available port starting at *start*.

    Raises ``RuntimeError`` after *attempts* failures.
    """
    for offset in range(attempts):
        port = start + offset
        if _port_available(host, port):
            return port
    msg = (
        f"Could not find a free port after {attempts} attempts "
        f"(tried {start}–{start + attempts - 1})."
    )
    raise RuntimeError(msg)


def _run_server(host: str, port: int) -> None:
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def run_desktop(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    *,
    on_ready: object | None = None,
) -> None:
    """Start the FastAPI server in a daemon thread and open a pywebview window.

    Parameters
    ----------
    on_ready:
        Optional callable invoked just before the webview event-loop starts
        (e.g. to close a splash screen).
    """
    # Set a custom AppUserModelID so Windows shows our icon in the taskbar
    # instead of the default Python icon.
    import ctypes  # noqa: PLC0415

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DrMichaelMueller.FileTools")

    port = _find_port(host, port, _PORT_ATTEMPTS)

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

    def _set_icon() -> None:
        """Set the Windows form icon via the native WinForms handle."""
        try:
            import time as _t

            from System.Drawing import Icon as WinIcon  # type: ignore[import]  # noqa: PLC0415

            # window.native may not be ready immediately
            for _ in range(10):
                form = getattr(window, "native", None)
                if form is not None:
                    break
                _t.sleep(0.1)
            if form is not None:
                form.Icon = WinIcon(_icon_path)
        except Exception:  # noqa: BLE001
            pass  # non-critical – fall back to default icon

    window.events.loaded += _on_loaded
    window.events.shown += _set_icon

    # Close the splash just before entering the blocking webview event-loop.
    if callable(on_ready):
        on_ready()

    webview.start(gui="edgechromium", private_mode=False)


if __name__ == "__main__":  # pragma: no cover
    run_desktop()
