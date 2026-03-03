"""FileTools CLI entry-point.

Usage::

    python file_tools.py              # desktop mode (default)
    python file_tools.py --web        # web-only mode
    python file_tools.py --mode web   # same as above
    python file_tools.py installer    # build Windows NSIS installer
"""

from __future__ import annotations

import argparse
import sys


def run(mode: str = "desktop") -> None:
    """Launch FileTools in the chosen *mode* (``desktop`` or ``web``)."""
    mode = mode.strip().lower()

    if mode == "web":
        from file_tools.main import run_web

        run_web()
    elif mode == "desktop":
        # Show a splash *before* the heavy imports (uvicorn, webview, etc.)
        from file_tools.splash import Splash

        splash = Splash()
        splash.show()

        try:
            from file_tools.desktop import run_desktop

            run_desktop(on_ready=splash.close)
        except Exception:
            splash.close()
            raise
    else:
        print(f"Unknown mode '{mode}'. Use 'desktop' or 'web'.", file=sys.stderr)
        sys.exit(1)


def create_installer() -> None:
    """Build a Windows NSIS installer (portable venv + source)."""
    from pathlib import Path

    from file_tools.tools.installer_builder import InstallerBuilder

    project_root = Path(__file__).resolve().parent
    builder = InstallerBuilder(project_root)
    print("Building installer …")
    installer = builder.build()
    print(f"Installer created: {installer}")


def _cli() -> None:
    parser = argparse.ArgumentParser(description="FileTools launcher")
    sub = parser.add_subparsers(dest="command")

    # Default run mode (no subcommand)
    parser.add_argument(
        "--mode",
        choices=["desktop", "web"],
        default="desktop",
        help="Run mode: 'desktop' (default) opens a native window, 'web' starts a plain HTTP server.",
    )
    parser.add_argument(
        "--web",
        action="store_const",
        const="web",
        dest="mode",
        help="Shorthand for --mode web.",
    )

    # installer subcommand
    sub.add_parser("installer", help="Build a Windows NSIS installer.")

    args = parser.parse_args()

    if args.command == "installer":
        create_installer()
    else:
        run(args.mode)


if __name__ == "__main__":
    _cli()
