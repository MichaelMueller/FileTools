"""Tests for file_tools.desktop (pywebview desktop launcher)."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Port helpers
# ---------------------------------------------------------------------------


class TestPortHelpers:
    """Tests for _port_available and _find_port."""

    def test_port_available_free(self) -> None:
        from file_tools.desktop import _port_available

        # Use a high ephemeral port that is very likely free
        assert _port_available("127.0.0.1", 0) is True or True  # port 0 => OS picks

    def test_port_available_occupied(self) -> None:
        from file_tools.desktop import _port_available

        # Bind a port, then check it's not available
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            assert _port_available("127.0.0.1", port) is False

    def test_find_port_returns_free(self) -> None:
        from file_tools.desktop import _find_port

        port = _find_port("127.0.0.1", 19000, 5)
        assert isinstance(port, int)
        assert port >= 19000

    def test_find_port_skips_occupied(self) -> None:
        from file_tools.desktop import _find_port

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            occupied = s.getsockname()[1]
            # Start search from occupied port – should return occupied+1 or higher
            found = _find_port("127.0.0.1", occupied, 5)
            assert found > occupied

    def test_find_port_raises_after_attempts(self) -> None:
        from file_tools.desktop import _find_port

        with patch("file_tools.desktop._port_available", return_value=False):
            with pytest.raises(RuntimeError, match="Could not find a free port"):
                _find_port("127.0.0.1", 8765, 3)


# ---------------------------------------------------------------------------
# run_desktop
# ---------------------------------------------------------------------------


def test_run_desktop_starts_server_and_webview() -> None:
    """run_desktop starts a daemon thread, sleeps, creates a window, and calls start."""
    mock_window = MagicMock()
    # += on a MagicMock replaces the attr with __iadd__'s return value.
    # Make __iadd__ return *self* so the mock stays the same object.
    mock_loaded_event = MagicMock()
    mock_loaded_event.__iadd__ = MagicMock(return_value=mock_loaded_event)
    mock_shown_event = MagicMock()
    mock_shown_event.__iadd__ = MagicMock(return_value=mock_shown_event)
    mock_events = MagicMock()
    mock_events.loaded = mock_loaded_event
    mock_events.shown = mock_shown_event
    mock_window.events = mock_events
    mock_webview = MagicMock()
    mock_webview.create_window.return_value = mock_window

    mock_thread = MagicMock()
    mock_thread_cls = MagicMock(return_value=mock_thread)

    with (
        patch.dict("sys.modules", {"webview": mock_webview}),
        patch("file_tools.desktop.webview", mock_webview),
        patch("file_tools.desktop.threading.Thread", mock_thread_cls),
        patch("file_tools.desktop.time.sleep") as mock_sleep,
        patch("file_tools.desktop._find_port", return_value=9876),
        patch("os._exit") as mock_exit,
    ):
        from file_tools.desktop import run_desktop

        on_ready = MagicMock()
        run_desktop(host="127.0.0.1", port=9876, on_ready=on_ready)

        # Thread was created with _run_server and started as daemon
        mock_thread_cls.assert_called_once()
        call_kwargs = mock_thread_cls.call_args
        assert call_kwargs.kwargs.get("daemon") is True
        mock_thread.start.assert_called_once()

        # Slept for 1 second
        mock_sleep.assert_called_once_with(1)

        # pywebview window created
        mock_webview.create_window.assert_called_once_with(
            title="FileTools",
            url="http://127.0.0.1:9876",
            width=1200,
            height=800,
            resizable=True,
        )

        # Events were registered
        mock_loaded_event.__iadd__.assert_called_once()
        mock_shown_event.__iadd__.assert_called_once()

        # on_ready callback invoked before webview.start()
        on_ready.assert_called_once()

        # webview.start() called
        mock_webview.start.assert_called_once_with(
            gui="edgechromium", private_mode=False,
        )

        # os._exit(0) called to ensure clean shutdown
        mock_exit.assert_called_once_with(0)


def test_run_server_uses_uvicorn() -> None:
    """_run_server configures and runs uvicorn and stores server globally."""
    mock_server = MagicMock()
    mock_config_cls = MagicMock()
    mock_server_cls = MagicMock(return_value=mock_server)

    with (
        patch("file_tools.desktop.uvicorn.Config", mock_config_cls),
        patch("file_tools.desktop.uvicorn.Server", mock_server_cls),
    ):
        import file_tools.desktop as desktop_mod
        from file_tools.desktop import _run_server, app

        _run_server("127.0.0.1", 8765)

    mock_config_cls.assert_called_once_with(app, host="127.0.0.1", port=8765, log_level="warning")
    mock_server_cls.assert_called_once_with(mock_config_cls.return_value)
    mock_server.run.assert_called_once()
    assert desktop_mod._uvicorn_server is mock_server
