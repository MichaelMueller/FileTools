"""Startup and runtime diagnostics for MMO FileTools."""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Any, ClassVar


class Diagnostics:
    """Logs, crash traces, startup breadcrumbs and a hang watchdog.

    Everything here has to keep working while the app is only half alive — that
    is precisely when it matters — so no method may raise.  A failure inside
    diagnostics must never become the reason the application dies.
    """

    APP_SLUG = "mmo_file_tools"
    LOGGER_NAME = "mmo_file_tools"

    _MAX_BYTES = 1_000_000
    _BACKUP_COUNT = 3
    #: uvicorn logs to stderr by default, which is discarded under pythonw.exe.
    _ADOPTED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

    _installed: ClassVar[bool] = False
    _watchdog: ClassVar[threading.Timer | None] = None
    #: faulthandler needs the stream to stay open for the process lifetime.
    _crash_stream: ClassVar[IO[str] | None] = None
    _prev_excepthook: ClassVar[Any] = None
    #: At most one modal popup per process, so a failing thread cannot spam.
    _notified: ClassVar[bool] = False

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    @classmethod
    def log_dir(cls) -> Path:
        """Return the log directory, creating it if needed.

        Same base as the databases (``user_data_dir``), which on Windows is the
        installation directory — so logs sit next to the app the user installed.
        """
        from platformdirs import user_data_dir  # noqa: PLC0415

        directory = Path(user_data_dir(cls.APP_SLUG, appauthor=False)) / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @classmethod
    def logger(cls) -> logging.Logger:
        """Return the application logger."""
        return logging.getLogger(cls.LOGGER_NAME)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    @classmethod
    def install(cls) -> None:
        """Set up file logging, exception hooks and the crash handler.

        Idempotent, and swallows its own failures: running without diagnostics is
        bad, but crashing because diagnostics could not start is worse.
        """
        if cls._installed:
            return
        cls._installed = True
        try:
            cls._install_unguarded()
        except Exception:  # noqa: BLE001 - diagnostics must never break startup
            pass

    @classmethod
    def _install_unguarded(cls) -> None:
        directory = cls.log_dir()

        handler = RotatingFileHandler(
            directory / "app.log",
            maxBytes=cls._MAX_BYTES,
            backupCount=cls._BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)-12s %(message)s"),
        )

        log = cls.logger()
        log.setLevel(logging.INFO)
        log.addHandler(handler)
        log.propagate = False

        # Route uvicorn through the same file, otherwise its errors vanish.
        for name in cls._ADOPTED_LOGGERS:
            adopted = logging.getLogger(name)
            adopted.addHandler(handler)
            adopted.setLevel(logging.INFO)

        cls._crash_stream = (directory / "crash.log").open("a", encoding="utf-8")
        faulthandler.enable(cls._crash_stream)

        cls._prev_excepthook = sys.excepthook
        sys.excepthook = cls._on_exception
        threading.excepthook = cls._on_thread_exception

        log.info("--- start: %s", " ".join(sys.argv))
        log.info("exe=%s", sys.executable)
        log.info("version=%s", sys.version.replace("\n", " "))

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    @classmethod
    def _on_exception(cls, exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        """Log an unhandled exception in the main thread, then chain."""
        cls.logger().critical("unhandled exception", exc_info=(exc_type, exc, tb))
        cls.notify(f"MMO FileTools hit an unexpected error:\n\n{exc_type.__name__}: {exc}")
        if cls._prev_excepthook is not None:
            cls._prev_excepthook(exc_type, exc, tb)

    @classmethod
    def _on_thread_exception(cls, args: Any) -> None:
        """Log an exception that killed a worker thread.

        The server runs in a daemon thread, so without this its death is silent:
        the window stays open while every API call fails.
        """
        name = getattr(args.thread, "name", "?")
        cls.logger().critical(
            "unhandled exception in thread %s", name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        cls.notify(
            f"A background task of MMO FileTools stopped working "
            f"(thread {name}).\n\n{args.exc_type.__name__}: {args.exc_value}",
        )

    # ------------------------------------------------------------------
    # Breadcrumbs and watchdog
    # ------------------------------------------------------------------

    #: Written once startup succeeded. The batch wrapper polls for this so it can
    #: close its console window instead of waiting for the whole app lifetime.
    OK_MARKER = ".startup-ok"

    @classmethod
    def mark_started(cls) -> None:
        """Signal a completed startup to the launcher wrapper."""
        try:
            (cls.log_dir() / cls.OK_MARKER).touch()
        except Exception:  # noqa: BLE001 - never break startup over a marker
            pass

    @classmethod
    def breadcrumb(cls, phase: str) -> None:
        """Record that *phase* was reached.

        On a hang the last breadcrumb is what identifies the phase we got stuck
        in, so these are logged unconditionally rather than only on error.
        """
        cls.logger().info("phase: %s", phase)

    @classmethod
    def start_watchdog(cls, seconds: float, phase: str) -> None:
        """Dump all thread stacks if *phase* is not cleared within *seconds*."""
        cls.clear_watchdog()
        timer = threading.Timer(seconds, cls._on_watchdog_timeout, args=(seconds, phase))
        timer.daemon = True
        cls._watchdog = timer
        timer.start()

    @classmethod
    def clear_watchdog(cls) -> None:
        """Cancel a pending watchdog, if any."""
        if cls._watchdog is not None:
            cls._watchdog.cancel()
            cls._watchdog = None

    @classmethod
    def _on_watchdog_timeout(cls, seconds: float, phase: str) -> None:
        cls.logger().error(
            "watchdog: '%s' not reached after %.0fs - dumping all thread stacks",
            phase, seconds,
        )
        if cls._crash_stream is not None:
            faulthandler.dump_traceback(cls._crash_stream)
            cls._crash_stream.flush()

    # ------------------------------------------------------------------
    # User notification
    # ------------------------------------------------------------------

    @classmethod
    def notify(cls, message: str) -> None:
        """Make a failure visible, without trusting ctypes.

        A missing DLL breaks ctypes itself and is a likely reason to be here, so
        fall back to showing the log.  The user must never be left with no
        feedback at all.
        """
        if cls._notified:
            return
        cls._notified = True
        try:
            import ctypes  # noqa: PLC0415

            ctypes.windll.user32.MessageBoxW(0, message, "MMO FileTools - Error", 0x10)
            return
        except Exception:  # noqa: BLE001 - ctypes may be the broken part
            pass
        # notepad.exe is always present and needs no file association — unlike
        # os.startfile on a ".log", which silently does nothing when none is set.
        try:
            import subprocess  # noqa: PLC0415

            subprocess.Popen(["notepad.exe", str(cls.log_dir() / "app.log")])  # noqa: S603, S607
            return
        except Exception:  # noqa: BLE001 - fall through to the folder
            pass
        try:
            os.startfile(str(cls.log_dir()))  # noqa: S606
        except Exception:  # noqa: BLE001 - last resort, nothing left to try
            pass
