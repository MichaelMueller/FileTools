"""FileTools CLI entry-point.

Usage::

    python file_tools.py              # desktop mode (default)
    python file_tools.py --web        # web-only mode
    python file_tools.py --mode web   # same as above
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
        from file_tools.desktop import run_desktop

        run_desktop()
    else:
        print(f"Unknown mode '{mode}'. Use 'desktop' or 'web'.", file=sys.stderr)
        sys.exit(1)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="FileTools launcher")
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
    args = parser.parse_args()
    run(args.mode)


if __name__ == "__main__":
    _cli()
