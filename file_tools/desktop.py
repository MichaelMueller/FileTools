"""Desktop entry-point: wraps the FastAPI app inside a pywebview window."""

from __future__ import annotations

import socket
import sys
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


def _wait_for_server(host: str, port: int, timeout: float = 5.0) -> None:
    """Block until the server accepts a TCP connection.

    Raises ``RuntimeError`` if the server does not respond within *timeout* seconds.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(
        f"Server on {host}:{port} did not accept connections within {timeout:.1f} s."
    )


_uvicorn_server: uvicorn.Server | None = None


def _run_server(host: str, port: int) -> None:
    global _uvicorn_server  # noqa: PLW0603
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    _uvicorn_server = uvicorn.Server(config)
    _uvicorn_server.run()


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
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DrMichaelMueller.FileTools")

    port = _find_port(host, port, _PORT_ATTEMPTS)

    thread = threading.Thread(target=_run_server, args=(host, port), daemon=True)
    thread.start()
    # Wait until the server is actually accepting connections.
    try:
        _wait_for_server(host, port)
    except RuntimeError as exc:
        if sys.platform == "win32":
            import ctypes  # noqa: PLC0415
            ctypes.windll.user32.MessageBoxW(0, str(exc), "FileTools – Startup Error", 0x10)
        raise

    _icon_path = str(Path(__file__).parent / "static" / "icon.ico")
    window = webview.create_window(
        title="FileTools",
        url=f"http://{host}:{port}",
        width=1200,
        height=800,
        resizable=True,
        text_select=True,
    )

    def _on_loaded() -> None:  # pragma: no cover – pywebview callback
        set_webview_window(window)

    def _set_icon_win32() -> None:  # pragma: no cover
        """Set the window icon via Win32 ctypes (no System.Drawing)."""
        if sys.platform != "win32":
            return
        try:
            import ctypes  # noqa: PLC0415
            import ctypes.wintypes as wt  # noqa: PLC0415
            import os  # noqa: PLC0415

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            # -- argtypes for 64-bit safety --
            WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
            user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
            user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
            user32.IsWindowVisible.argtypes = [wt.HWND]
            user32.GetWindowTextLengthW.argtypes = [wt.HWND]
            user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
            user32.LoadImageW.restype = wt.HANDLE
            user32.LoadImageW.argtypes = [
                wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
                ctypes.c_int, ctypes.c_int, wt.UINT,
            ]

            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x0010
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1

            my_pid = os.getpid()

            def _find_main_hwnd() -> int | None:
                """Find the first visible, titled window belonging to this process."""
                result: list[int] = []

                def _cb(hwnd: int, _lparam: int) -> bool:
                    pid = wt.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value == my_pid and user32.IsWindowVisible(hwnd):
                        if user32.GetWindowTextLengthW(hwnd) > 0:
                            result.append(hwnd)
                            return False  # stop enumeration
                    return True  # continue

                user32.EnumWindows(WNDENUMPROC(_cb), 0)
                return result[0] if result else None

            # Poll for the webview window (up to 8 s).
            hwnd = None
            for _ in range(80):
                hwnd = _find_main_hwnd()
                if hwnd:
                    break
                time.sleep(0.1)
            if not hwnd:
                return

            hicon_sm = user32.LoadImageW(
                None, _icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE,
            )
            hicon_lg = user32.LoadImageW(
                None, _icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE,
            )
            if hicon_sm:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_sm)
            if hicon_lg:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_lg)
        except Exception:  # noqa: BLE001
            pass  # non-critical

    window.events.loaded += _on_loaded

    # Set the icon in a background thread so it doesn't block the GUI.
    threading.Thread(target=_set_icon_win32, daemon=True).start()

    # Close the splash just before entering the blocking webview event-loop.
    if callable(on_ready):
        on_ready()

    # Start the GUI and surface any errors to the user (no console when
    # launched from the installer). If webview fails to create or start
    # (missing WebView2 runtime, pythonnet issues, etc.), write a traceback
    # to `filetools-error.log` in the current working directory and show a
    # MessageBox so the user is aware of the failure.
    def _log_and_alert(msg: str, exc: Exception | None = None) -> None:  # pragma: no cover
        try:
            import ctypes
            import traceback as _tb
            # write traceback
            log = Path.cwd() / "filetools-error.log"
            with open(log, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
                if exc is not None:
                    f.write(_tb.format_exc())
                    f.write("\n")
        except Exception:
            pass
        try:
            ctypes.windll.user32.MessageBoxW(0, msg, "FileTools - Error", 0x10)
        except Exception:
            pass

    try:
        webview.start(gui="edgechromium", private_mode=False)
    except Exception as exc:  # pragma: no cover - runtime error path
        # Attempt graceful uvicorn shutdown
        if _uvicorn_server is not None:
            _uvicorn_server.should_exit = True
        _log_and_alert("FileTools failed to start the GUI. See filetools-error.log for details.", exc)
        # Ensure process exit to avoid leaving background threads running
        import os  # noqa: PLC0415
        os._exit(1)

    # Ensure the uvicorn server shuts down when the window closes.
    if _uvicorn_server is not None:  # pragma: no cover
        _uvicorn_server.should_exit = True
    # os._exit bypasses atexit handlers and thread join waits that can hang.
    import os  # noqa: PLC0415
    os._exit(0)


if __name__ == "__main__":  # pragma: no cover
    run_desktop()
