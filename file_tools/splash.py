"""Lightweight Win32 splash screen (ctypes only, no tkinter)."""

from __future__ import annotations

import ctypes
import sys
import threading
from pathlib import Path

if sys.platform == "win32":
    import ctypes.wintypes as wt
else:  # pragma: no cover
    wt = None  # type: ignore[assignment]

# Win32 constants
_WM_CLOSE = 0x0010
_WM_DESTROY = 0x0002
_WM_PAINT = 0x000F
_WS_POPUP = 0x80000000
_WS_VISIBLE = 0x10000000
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_LAYERED = 0x00080000
_SM_CXSCREEN = 0
_SM_CYSCREEN = 1
_DT_CENTER = 0x00000001
_DT_VCENTER = 0x00000004
_DT_SINGLELINE = 0x00000020
_TRANSPARENT = 1
_LWA_ALPHA = 0x00000002
_SW_SHOWNOACTIVATE = 8
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_HWND_TOPMOST = -1
_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x00000010
_LR_DEFAULTSIZE = 0x00000040
_DI_NORMAL = 0x0003
_ICON_SIZE = 48

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
        text: str = "FileTools initialization \u2026",
        *,
        width: int = 400,
        height: int = 170,
    ) -> None:
        self._text = text
        self._width = width
        self._height = height
        self._hwnd: int = 0
        self._hicon: int = 0
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

    def _run(self) -> None:  # pragma: no cover
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        kernel32 = ctypes.windll.kernel32

        # Set correct return types for 64-bit handle safety.
        kernel32.GetModuleHandleW.restype = wt.HMODULE
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
        ]
        user32.BeginPaint.restype = wt.HDC
        user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(_PAINTSTRUCT)]
        user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(_PAINTSTRUCT)]
        user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        user32.DrawTextW.argtypes = [
            wt.HDC, wt.LPCWSTR, ctypes.c_int,
            ctypes.POINTER(wt.RECT), wt.UINT,
        ]
        user32.DefWindowProcW.restype = _LRESULT
        user32.DefWindowProcW.argtypes = [
            wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
        ]
        user32.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
        user32.RegisterClassW.restype = wt.ATOM
        user32.SetLayeredWindowAttributes.argtypes = [
            wt.HWND, wt.DWORD, wt.BYTE, wt.DWORD,
        ]
        user32.SetWindowPos.argtypes = [
            wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wt.UINT,
        ]
        user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        user32.UpdateWindow.argtypes = [wt.HWND]
        user32.SetForegroundWindow.argtypes = [wt.HWND]
        user32.DestroyWindow.argtypes = [wt.HWND]
        user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        gdi32.CreateSolidBrush.restype = wt.HBRUSH
        gdi32.CreateFontW.restype = wt.HANDLE
        gdi32.SelectObject.restype = wt.HANDLE
        gdi32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
        gdi32.DeleteObject.argtypes = [wt.HANDLE]
        gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
        gdi32.SetTextColor.argtypes = [wt.HDC, wt.DWORD]
        user32.LoadImageW.restype = wt.HANDLE
        user32.LoadImageW.argtypes = [
            wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
            ctypes.c_int, ctypes.c_int, wt.UINT,
        ]
        user32.DrawIconEx.argtypes = [
            wt.HDC, ctypes.c_int, ctypes.c_int, wt.HANDLE,
            ctypes.c_int, ctypes.c_int, wt.UINT, wt.HANDLE, wt.UINT,
        ]
        user32.DestroyIcon.argtypes = [wt.HANDLE]

        hinstance = kernel32.GetModuleHandleW(None)

        # Load the app icon from file.
        icon_path = str(Path(__file__).with_name("static") / "icon.ico")
        self._hicon = user32.LoadImageW(
            None, icon_path, _IMAGE_ICON,
            _ICON_SIZE, _ICON_SIZE,
            _LR_LOADFROMFILE,
        )
        bg_brush = gdi32.CreateSolidBrush(self._BG_COLOR)

        self._wndproc_cb = _WNDPROC(self._wndproc)

        wc = _WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_cb
        wc.hInstance = hinstance
        wc.hbrBackground = bg_brush
        wc.lpszClassName = self._CLASS_NAME

        # Unregister any leftover class from a previous run, then register.
        user32.UnregisterClassW(self._CLASS_NAME, hinstance)
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            self._ready.set()
            return

        screen_w = user32.GetSystemMetrics(_SM_CXSCREEN)
        screen_h = user32.GetSystemMetrics(_SM_CYSCREEN)
        x = (screen_w - self._width) // 2
        y = (screen_h - self._height) // 2

        hwnd = user32.CreateWindowExW(
            _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW | _WS_EX_LAYERED,
            self._CLASS_NAME,
            "FileTools",
            _WS_POPUP | _WS_VISIBLE,
            x, y, self._width, self._height,
            None, None, hinstance, None,
        )

        if not hwnd:
            self._ready.set()
            return

        self._hwnd = hwnd

        # Make fully opaque via layered-window API (guarantees rendering).
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, _LWA_ALPHA)

        # Force the window on top and visible.
        user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
        user32.SetWindowPos(
            hwnd, wt.HWND(_HWND_TOPMOST), 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE,
        )
        user32.UpdateWindow(hwnd)
        user32.SetForegroundWindow(hwnd)

        self._ready.set()

        # Message loop — blocks until WM_CLOSE / WM_DESTROY.
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:  # pragma: no cover
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        if msg == _WM_PAINT:
            ps = _PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))

            rect = wt.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            cw = rect.right - rect.left

            gdi32.SetBkMode(hdc, _TRANSPARENT)
            gdi32.SetTextColor(hdc, self._FG_COLOR)

            # Draw the icon centred near the top.
            icon_top = 24
            if self._hicon:
                ix = (cw - _ICON_SIZE) // 2
                user32.DrawIconEx(
                    hdc, ix, icon_top, self._hicon,
                    _ICON_SIZE, _ICON_SIZE, 0, None, _DI_NORMAL,
                )

            # Draw text below the icon.
            font = gdi32.CreateFontW(
                -18, 0, 0, 0, 400, 0, 0, 0, 0, 0, 0, 0, 0, "Segoe UI",
            )
            old_font = gdi32.SelectObject(hdc, font)

            text_rect = wt.RECT()
            text_rect.left = rect.left
            text_rect.right = rect.right
            text_rect.top = icon_top + _ICON_SIZE + 16
            text_rect.bottom = rect.bottom
            user32.DrawTextW(
                hdc, self._text, -1, ctypes.byref(text_rect),
                _DT_CENTER | _DT_SINGLELINE,
            )

            gdi32.SelectObject(hdc, old_font)
            gdi32.DeleteObject(font)
            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0

        if msg == _WM_CLOSE:
            if self._hicon:
                user32.DestroyIcon(self._hicon)
                self._hicon = 0
            user32.DestroyWindow(hwnd)
            return 0

        if msg == _WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
