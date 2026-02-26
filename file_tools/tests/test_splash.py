"""Tests for file_tools.splash (Win32 splash screen)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from file_tools.splash import Splash


class TestSplashInit:
    """Constructor tests."""

    def test_default_values(self) -> None:
        s = Splash()
        assert s._text == "FileTools initialization \u2026"
        assert s._width == 380
        assert s._height == 100
        assert s._hwnd == 0

    def test_custom_values(self) -> None:
        s = Splash("Loading…", width=500, height=200)
        assert s._text == "Loading…"
        assert s._width == 500
        assert s._height == 200


class TestSplashShow:
    """Show / close lifecycle tests."""

    def test_show_noop_on_non_windows(self) -> None:
        """On non-Windows platforms, show() does nothing."""
        s = Splash()
        with patch.object(sys, "platform", "linux"):
            s.show()
        assert s._hwnd == 0

    def test_show_starts_daemon_thread(self) -> None:
        """show() launches a daemon thread and waits for the ready event."""
        s = Splash()

        mock_thread = MagicMock()

        def _fake_start():
            # Simulate the thread setting _ready
            s._ready.set()

        mock_thread.start = _fake_start
        mock_thread.daemon = True

        with (
            patch.object(sys, "platform", "win32"),
            patch("file_tools.splash.threading.Thread", return_value=mock_thread) as mock_cls,
        ):
            s.show()
            mock_cls.assert_called_once()
            kwargs = mock_cls.call_args
            assert kwargs.kwargs.get("daemon") is True

    def test_close_posts_wm_close(self) -> None:
        """close() posts WM_CLOSE to the splash window."""
        s = Splash()
        s._hwnd = 12345

        mock_user32 = MagicMock()
        with patch("ctypes.windll", create=True) as mock_windll:
            mock_windll.user32 = mock_user32
            s.close()

        mock_user32.PostMessageW.assert_called_once_with(12345, 0x0010, 0, 0)
        assert s._hwnd == 0

    def test_close_noop_when_no_hwnd(self) -> None:
        """close() does nothing if the window was never created."""
        s = Splash()
        # Should not raise
        s.close()
        assert s._hwnd == 0


class TestSplashWndproc:
    """Window procedure tests (mocked GDI/user32)."""

    def test_wm_destroy_posts_quit(self) -> None:
        s = Splash()
        WM_DESTROY = 0x0002

        mock_user32 = MagicMock()
        mock_user32.PostQuitMessage = MagicMock()
        mock_gdi32 = MagicMock()

        with patch("ctypes.windll", create=True) as mock_windll:
            mock_windll.user32 = mock_user32
            mock_windll.gdi32 = mock_gdi32
            result = s._wndproc(1, WM_DESTROY, 0, 0)

        mock_user32.PostQuitMessage.assert_called_once_with(0)
        assert result == 0

    def test_wm_paint_draws_text(self) -> None:
        s = Splash("Hello")
        WM_PAINT = 0x000F

        mock_user32 = MagicMock()
        mock_user32.BeginPaint.return_value = 42  # fake HDC
        mock_gdi32 = MagicMock()
        mock_gdi32.CreateFontW.return_value = 99  # fake HFONT
        mock_gdi32.SelectObject.return_value = 88  # old font

        with patch("ctypes.windll", create=True) as mock_windll:
            mock_windll.user32 = mock_user32
            mock_windll.gdi32 = mock_gdi32
            result = s._wndproc(1, WM_PAINT, 0, 0)

        assert result == 0
        mock_user32.BeginPaint.assert_called_once()
        mock_gdi32.SetBkMode.assert_called_once()
        mock_gdi32.SetTextColor.assert_called_once()
        mock_gdi32.CreateFontW.assert_called_once()
        mock_user32.DrawTextW.assert_called_once()
        mock_gdi32.DeleteObject.assert_called_once_with(99)
        mock_user32.EndPaint.assert_called_once()

    def test_default_message_delegates(self) -> None:
        s = Splash()
        UNHANDLED_MSG = 0x9999

        mock_user32 = MagicMock()
        mock_user32.DefWindowProcW.return_value = 42
        mock_gdi32 = MagicMock()

        with patch("ctypes.windll", create=True) as mock_windll:
            mock_windll.user32 = mock_user32
            mock_windll.gdi32 = mock_gdi32
            result = s._wndproc(1, UNHANDLED_MSG, 0, 0)

        mock_user32.DefWindowProcW.assert_called_once_with(1, UNHANDLED_MSG, 0, 0)
        assert result == 42
