"""Tests for mmo_file_tools.diagnostics."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mmo_file_tools.diagnostics import Diagnostics

#: Captured before the autouse fixture patches log_dir, so the real
#: implementation stays reachable for the test that exercises it.
_REAL_LOG_DIR = Diagnostics.__dict__["log_dir"].__func__


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    """Point the log directory at tmp_path and undo global state afterwards."""
    import sys

    prev_hook = sys.excepthook
    prev_thread_hook = threading.excepthook
    log = Diagnostics.logger()
    prev_handlers = list(log.handlers)
    prev_propagate = log.propagate

    with patch.object(Diagnostics, "log_dir", return_value=tmp_path):
        yield tmp_path

    Diagnostics.clear_watchdog()
    if Diagnostics._crash_stream is not None:
        Diagnostics._crash_stream.close()
    Diagnostics._crash_stream = None
    Diagnostics._installed = False
    Diagnostics._notified = False
    Diagnostics._prev_excepthook = None
    for handler in list(log.handlers):
        handler.close()
    log.handlers = prev_handlers
    log.propagate = prev_propagate
    for name in Diagnostics._ADOPTED_LOGGERS:
        logging.getLogger(name).handlers = []
    sys.excepthook = prev_hook
    threading.excepthook = prev_thread_hook


class TestLogDir:
    """log_dir is the only place that decides where diagnostics land."""

    def test_uses_user_data_dir_logs(self, tmp_path: Path) -> None:
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "app")):
            result = _REAL_LOG_DIR(Diagnostics)
        assert result == tmp_path / "app" / "logs"
        assert result.is_dir()


class TestInstall:
    def test_creates_log_file_and_hooks(self, tmp_path: Path) -> None:
        import sys

        Diagnostics.install()
        Diagnostics.breadcrumb("unit test")
        for handler in Diagnostics.logger().handlers:
            handler.flush()
        content = (tmp_path / "app.log").read_text(encoding="utf-8")
        assert "phase: unit test" in content
        # The environment must be recorded – it is what identifies a broken runtime.
        assert "exe=" in content
        assert "version=" in content
        # Bound classmethods are recreated on each attribute access, so compare
        # by equality rather than identity.
        assert sys.excepthook == Diagnostics._on_exception
        assert threading.excepthook == Diagnostics._on_thread_exception

    def test_is_idempotent(self) -> None:
        Diagnostics.install()
        count = len(Diagnostics.logger().handlers)
        Diagnostics.install()
        assert len(Diagnostics.logger().handlers) == count

    def test_adopts_uvicorn_loggers(self, tmp_path: Path) -> None:
        """uvicorn logs to stderr, which is discarded under pythonw.exe."""
        Diagnostics.install()
        logging.getLogger("uvicorn.error").error("server exploded")
        for handler in logging.getLogger("uvicorn.error").handlers:
            handler.flush()
        assert "server exploded" in (tmp_path / "app.log").read_text(encoding="utf-8")

    def test_failure_does_not_propagate(self) -> None:
        """Diagnostics must never be the reason the app fails to start."""
        with patch.object(Diagnostics, "_install_unguarded", side_effect=OSError("disk full")):
            Diagnostics.install()  # must not raise


class TestHooks:
    """The hooks call notify(), which would pop a real modal MessageBox and hang
    the suite — so it is patched here and tested on its own in TestNotify."""

    @pytest.fixture(autouse=True)
    def _no_popup(self):
        with patch.object(Diagnostics, "notify") as notify:
            self._no_popup_spy = notify
            yield notify

    def test_main_thread_exception_is_logged_and_chained(self, tmp_path: Path) -> None:
        Diagnostics.install()
        previous = MagicMock()
        Diagnostics._prev_excepthook = previous
        try:
            raise ValueError("boom")
        except ValueError as exc:
            Diagnostics._on_exception(type(exc), exc, exc.__traceback__)
        for handler in Diagnostics.logger().handlers:
            handler.flush()
        content = (tmp_path / "app.log").read_text(encoding="utf-8")
        assert "unhandled exception" in content
        assert "ValueError: boom" in content
        previous.assert_called_once()
        self._no_popup_spy.assert_called_once()

    def test_main_thread_exception_without_previous_hook(self, tmp_path: Path) -> None:
        Diagnostics.install()
        Diagnostics._prev_excepthook = None
        try:
            raise ValueError("boom")
        except ValueError as exc:
            Diagnostics._on_exception(type(exc), exc, exc.__traceback__)  # must not raise

    def test_thread_exception_is_logged(self, tmp_path: Path) -> None:
        """The server thread dying is otherwise completely silent."""
        Diagnostics.install()
        try:
            raise RuntimeError("server thread died")
        except RuntimeError as exc:
            args = SimpleNamespace(
                exc_type=type(exc), exc_value=exc, exc_traceback=exc.__traceback__,
                thread=threading.current_thread(),
            )
            Diagnostics._on_thread_exception(args)
        for handler in Diagnostics.logger().handlers:
            handler.flush()
        content = (tmp_path / "app.log").read_text(encoding="utf-8")
        assert "unhandled exception in thread" in content
        assert "server thread died" in content
        # The user must be told: a dead server thread leaves a window whose
        # every API call fails.
        self._no_popup_spy.assert_called_once()

    def test_thread_exception_without_thread_name(self, tmp_path: Path) -> None:
        Diagnostics.install()
        try:
            raise RuntimeError("nameless")
        except RuntimeError as exc:
            args = SimpleNamespace(
                exc_type=type(exc), exc_value=exc, exc_traceback=exc.__traceback__,
                thread=None,
            )
            Diagnostics._on_thread_exception(args)  # must not raise


class TestStartMarker:
    """The wrapper closes its console window on this marker."""

    def test_mark_started_creates_marker(self, tmp_path: Path) -> None:
        Diagnostics.mark_started()
        assert (tmp_path / Diagnostics.OK_MARKER).is_file()

    def test_mark_started_survives_failure(self) -> None:
        with patch.object(Diagnostics, "log_dir", side_effect=OSError("no disk")):
            Diagnostics.mark_started()  # must not raise


class TestWatchdog:
    def test_fires_and_dumps_thread_stacks(self, tmp_path: Path) -> None:
        Diagnostics.install()
        fired = threading.Event()
        real = Diagnostics._on_watchdog_timeout.__func__

        def _spy(seconds: float, phase: str) -> None:
            real(Diagnostics, seconds, phase)
            fired.set()

        with patch.object(Diagnostics, "_on_watchdog_timeout", _spy):
            Diagnostics.start_watchdog(0.05, "window loaded")
            assert fired.wait(timeout=5)
        for handler in Diagnostics.logger().handlers:
            handler.flush()
        assert "watchdog" in (tmp_path / "app.log").read_text(encoding="utf-8")
        # The stack dump is what makes a hang diagnosable.
        assert "Thread" in (tmp_path / "crash.log").read_text(encoding="utf-8")

    def test_timeout_without_crash_stream(self, tmp_path: Path) -> None:
        Diagnostics.install()
        Diagnostics._crash_stream = None
        Diagnostics._on_watchdog_timeout(1.0, "phase")  # must not raise

    def test_cleared_watchdog_does_not_fire(self) -> None:
        Diagnostics.install()
        with patch.object(Diagnostics, "_on_watchdog_timeout") as timeout:
            Diagnostics.start_watchdog(0.05, "window loaded")
            Diagnostics.clear_watchdog()
            threading.Event().wait(0.3)
            timeout.assert_not_called()

    def test_starting_twice_replaces_the_timer(self) -> None:
        Diagnostics.install()
        Diagnostics.start_watchdog(10, "one")
        first = Diagnostics._watchdog
        Diagnostics.start_watchdog(10, "two")
        assert Diagnostics._watchdog is not first
        assert not first.is_alive()

    def test_clear_without_watchdog(self) -> None:
        Diagnostics.clear_watchdog()  # must not raise


class TestNotify:
    def test_uses_messagebox(self) -> None:
        box = MagicMock()
        with patch.dict("sys.modules", {"ctypes": MagicMock(windll=MagicMock(user32=box))}):
            Diagnostics.notify("something broke")
        box.MessageBoxW.assert_called_once()

    def test_falls_back_to_notepad(self, tmp_path: Path) -> None:
        """ctypes may be the broken part – that is how this bug presented.

        notepad.exe rather than os.startfile, because ".log" frequently has no
        file association and then nothing appears at all.
        """
        broken = MagicMock()
        broken.windll.user32.MessageBoxW.side_effect = OSError("ctypes is broken")
        with patch.dict("sys.modules", {"ctypes": broken}):
            with patch("subprocess.Popen") as popen:
                Diagnostics.notify("something broke")
        popen.assert_called_once_with(["notepad.exe", str(tmp_path / "app.log")])

    def test_falls_back_to_the_log_folder_without_notepad(self, tmp_path: Path) -> None:
        broken = MagicMock()
        broken.windll.user32.MessageBoxW.side_effect = OSError("ctypes is broken")
        with patch.dict("sys.modules", {"ctypes": broken}):
            with patch("subprocess.Popen", side_effect=OSError("no notepad")):
                with patch("os.startfile", create=True) as startfile:
                    Diagnostics.notify("something broke")
        startfile.assert_called_once_with(str(tmp_path))

    def test_survives_every_channel_failing(self) -> None:
        broken = MagicMock()
        broken.windll.user32.MessageBoxW.side_effect = OSError("no ctypes")
        with patch.dict("sys.modules", {"ctypes": broken}):
            with patch("subprocess.Popen", side_effect=OSError("no notepad")):
                with patch("os.startfile", create=True, side_effect=OSError("no shell")):
                    Diagnostics.notify("something broke")  # must not raise

    def test_only_notifies_once(self) -> None:
        """A failing thread must not be able to spam modal dialogs."""
        box = MagicMock()
        with patch.dict("sys.modules", {"ctypes": MagicMock(windll=MagicMock(user32=box))}):
            Diagnostics.notify("first")
            Diagnostics.notify("second")
        assert box.MessageBoxW.call_count == 1
