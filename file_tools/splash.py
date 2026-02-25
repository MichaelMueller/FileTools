"""Lightweight Win32 splash screen (ctypes only, no tkinter)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import threading

# Win32 constants
_WM_CLOSE = 0x0010
_WM_DESTROY = 0x0002
_WM_PAINT = 0x000F
_WS_POPUP = 0x80000000
_WS_VISIBLE = 0x10000000
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TOOLWINDOW = 0x00000080
_SM_CXSCREEN = 0
_SM_CYSCREEN = 1
_DT_CENTER = 0x00000001
_DT_VCENTER = 0x00000004
_DT_SINGLELINE = 0x00000020
_TRANSPARENT = 1

# Pointer-sized result, matches LRESULT / LONG_PTR on both 32- and 64-bit.
_LRESULT = ctypes.c_ssize_t

_WNDPROC = ctypes.WINFUNCTYPE(
    _LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HANDLE),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HANDLE),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wt.HDC),
        ("fErase", wt.BOOL),
        ("rcPaint", wt.RECT),
        ("fRestore", wt.BOOL),
        ("fIncUpdate", wt.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


def _rgb(r: int, g: int, b: int) -> int:
    """Convert RGB to Win32 COLORREF (BGR)."""
    return r | (g << 8) | (b << 16)


class Splash:
    """Borderless dark splash window shown while the application loads.

    Uses only ``ctypes`` and the Win32 API — no tkinter dependency.
    Safe to call on non-Windows platforms (``show`` is a no-op).
    """

    _CLASS_NAME = "FileToolsSplash"
    _BG_COLOR = _rgb(0x1E, 0x1E, 0x2E)
    _FG_COLOR = _rgb(0xCD, 0xD6, 0xF4)

    def __init__(
        self,
        text: str = "Starting FileTools\u2026",
        *,
        width: int = 380,
        height: int = 100,
    ) -> None:
        self._text = text
        self._width = width
        self._height = height
        self._hwnd: int = 0
        self._ready = threading.Event()
        # prevent GC of the C callback
        self._wndproc_cb: _WNDPROC | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(self) -> None:
        """Display the splash in a background thread."""
        if sys.platform != "win32":
            return
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        self._ready.wait(timeout=3)

    def close(self) -> None:
        """Close the splash (safe to call from any thread)."""
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, _WM_CLOSE, 0, 0)
            self._hwnd = 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        kernel32 = ctypes.windll.kernel32

        # Set correct return types for 64-bit handle safety.
        kernel32.GetModuleHandleW.restype = wt.HMODULE
        user32.CreateWindowExW.restype = wt.HWND
        user32.BeginPaint.restype = wt.HDC
        user32.DefWindowProcW.restype = _LRESULT
        gdi32.CreateSolidBrush.restype = wt.HBRUSH
        gdi32.CreateFontW.restype = wt.HANDLE
        gdi32.SelectObject.restype = wt.HANDLE

        hinstance = kernel32.GetModuleHandleW(None)
        bg_brush = gdi32.CreateSolidBrush(self._BG_COLOR)

        self._wndproc_cb = _WNDPROC(self._wndproc)

        wc = _WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_cb
        wc.hInstance = hinstance
        wc.hbrBackground = bg_brush
        wc.lpszClassName = self._CLASS_NAME

        user32.RegisterClassW(ctypes.byref(wc))

        screen_w = user32.GetSystemMetrics(_SM_CXSCREEN)
        screen_h = user32.GetSystemMetrics(_SM_CYSCREEN)
        x = (screen_w - self._width) // 2
        y = (screen_h - self._height) // 2

        self._hwnd = user32.CreateWindowExW(
            _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW,
            self._CLASS_NAME,
            None,
            _WS_POPUP | _WS_VISIBLE,
            x, y, self._width, self._height,
            0, 0, hinstance, 0,
        )

        # Force the window to render immediately.
        _SW_SHOW = 5
        user32.ShowWindow(self._hwnd, _SW_SHOW)
        user32.UpdateWindow(self._hwnd)

        self._ready.set()

        # Message loop — blocks until WM_CLOSE / WM_DESTROY.
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        if msg == _WM_PAINT:
            ps = _PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))

            rect = wt.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))

            gdi32.SetBkMode(hdc, _TRANSPARENT)
            gdi32.SetTextColor(hdc, self._FG_COLOR)

            font = gdi32.CreateFontW(
                -22, 0, 0, 0, 400, 0, 0, 0, 0, 0, 0, 0, 0, "Segoe UI",
            )
            old_font = gdi32.SelectObject(hdc, font)

            user32.DrawTextW(
                hdc, self._text, -1, ctypes.byref(rect),
                _DT_CENTER | _DT_VCENTER | _DT_SINGLELINE,
            )

            gdi32.SelectObject(hdc, old_font)
            gdi32.DeleteObject(font)
            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0

        if msg == _WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
