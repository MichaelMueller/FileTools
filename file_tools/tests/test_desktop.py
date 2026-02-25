"""Tests for file_tools.desktop (pywebview desktop launcher)."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch


def test_run_desktop_starts_server_and_webview() -> None:
    """run_desktop starts a daemon thread, sleeps, creates a window, and calls start."""
    mock_window = MagicMock()
    mock_webview = MagicMock()
    mock_webview.create_window.return_value = mock_window

    mock_thread = MagicMock()
    mock_thread_cls = MagicMock(return_value=mock_thread)

    with (
        patch.dict("sys.modules", {"webview": mock_webview}),
        patch("file_tools.desktop.webview", mock_webview),
        patch("file_tools.desktop.threading.Thread", mock_thread_cls),
        patch("file_tools.desktop.time.sleep") as mock_sleep,
        patch("file_tools.desktop.set_webview_window") as mock_set,
    ):
        from file_tools.desktop import run_desktop

        run_desktop(host="127.0.0.1", port=9876)

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

        # webview.start() called with the on_loaded callback
        mock_webview.start.assert_called_once()

        # Invoke the callback while the patch is still active, then verify
        on_loaded = mock_webview.start.call_args[0][0]
        on_loaded()
        mock_set.assert_called_once_with(mock_window)


def test_run_server_uses_uvicorn() -> None:
    """_run_server configures and runs uvicorn."""
    mock_server = MagicMock()
    mock_config_cls = MagicMock()
    mock_server_cls = MagicMock(return_value=mock_server)

    with (
        patch("file_tools.desktop.uvicorn.Config", mock_config_cls),
        patch("file_tools.desktop.uvicorn.Server", mock_server_cls),
    ):
        from file_tools.desktop import _run_server, app

        _run_server("127.0.0.1", 8765)

    mock_config_cls.assert_called_once_with(app, host="127.0.0.1", port=8765, log_level="warning")
    mock_server_cls.assert_called_once_with(mock_config_cls.return_value)
    mock_server.run.assert_called_once()
