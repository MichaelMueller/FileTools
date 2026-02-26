"""Sort files into year/month subdirectories based on creation date."""

from __future__ import annotations

import calendar
import os
import shutil
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Callable


class DateSorter:
    """Scan files in a directory and sort them into ``YYYY/MM_Mon`` folders.

    The creation date is determined with the following priority:

    1. **EXIF metadata** – ``DateTimeOriginal`` or ``DateTimeDigitized``
       (the actual moment a photo was taken on the camera / smartphone).
    2. **File-system birth-time** (``st_birthtime``), available on Windows
       and macOS 10.13+.
    3. **Fallback** – the earlier of ``st_mtime`` and ``st_ctime``.

    The preview step builds a plan without moving anything.  The user
    must explicitly call :meth:`execute` with the plan to perform moves.
    """

    # EXIF tag IDs
    _EXIF_DATETIME_ORIGINAL = 0x9003   # 36867
    _EXIF_DATETIME_DIGITIZED = 0x9004  # 36868
    _EXIF_DATETIME = 0x0132            # 306  (less specific fallback)

    @staticmethod
    def _exif_timestamp(path: Path) -> float | None:
        """Try to extract a creation timestamp from EXIF data.

        Returns a Unix timestamp or ``None`` if no usable tag is found.
        """
        try:
            from PIL import Image  # noqa: PLC0415

            with Image.open(path) as img:
                exif = img.getexif()
                if not exif:
                    return None
                for tag_id in (
                    DateSorter._EXIF_DATETIME_ORIGINAL,
                    DateSorter._EXIF_DATETIME_DIGITIZED,
                    DateSorter._EXIF_DATETIME,
                ):
                    val = exif.get(tag_id)
                    if val:
                        # Format: "YYYY:MM:DD HH:MM:SS"
                        dt = datetime.strptime(val, "%Y:%m:%d %H:%M:%S")
                        return dt.timestamp()
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def _creation_time(path: Path) -> float:
        """Return the best-guess creation timestamp for *path*.

        Prefers EXIF date, then ``st_birthtime``, then
        ``min(st_ctime, st_mtime)``.
        """
        exif_ts = DateSorter._exif_timestamp(path)
        if exif_ts is not None:
            return exif_ts

        st = path.stat()
        # st_birthtime is available on Windows and macOS 10.13+
        if hasattr(st, "st_birthtime"):
            return st.st_birthtime
        # Fallback: earliest of ctime and mtime
        return min(st.st_ctime, st.st_mtime)

    @staticmethod
    def _folder_name(timestamp: float) -> str:
        """Return ``YYYY/MM_Mon`` for the given Unix *timestamp*.

        Example: ``2025/05_May``
        """
        import time  # noqa: PLC0415

        t = time.localtime(timestamp)
        month_abbr = calendar.month_abbr[t.tm_mon]
        return f"{t.tm_year}/{t.tm_mon:02d}_{month_abbr}"

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def preview(
        self,
        directory: str | Path,
        *,
        progress_callback: Callable[[int], None] | None = None,
    ) -> list[dict]:
        """Build a move-plan for every file directly under *directory*.

        Returns a list of dicts, each with:
        - ``file``: original file name
        - ``source``: absolute source path
        - ``folder``: target sub-folder (e.g. ``2025/05_May``)
        - ``destination``: absolute destination path

        Sub-directories are **not** recursed into.
        """
        root = Path(directory).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Not a directory: {root}")

        plan: list[dict] = []
        count = 0
        for entry in sorted(root.iterdir()):
            if not entry.is_file():
                continue
            ts = self._creation_time(entry)
            folder = self._folder_name(ts)
            dest = root / folder / entry.name
            plan.append(
                {
                    "file": entry.name,
                    "source": str(entry),
                    "folder": folder,
                    "destination": str(dest),
                }
            )
            count += 1
            if progress_callback and count % 50 == 0:
                progress_callback(count)

        if progress_callback:
            progress_callback(count)

        return plan

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute(
        self,
        plan: list[dict],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[dict]:
        """Move files according to the *plan* returned by :meth:`preview`.

        Parameters
        ----------
        plan:
            The list of dicts from :meth:`preview`.
        progress_callback:
            Called with ``(moved_count, total_count)`` after each file.

        Returns
        -------
        list[dict]
            The subset of *plan* entries that were actually moved.
        """
        moved: list[dict] = []
        total = len(plan)
        for i, entry in enumerate(plan, 1):
            src = Path(entry["source"])
            dst = Path(entry["destination"])
            if not src.is_file():
                continue  # skip files that disappeared
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append(entry)
            if progress_callback:
                progress_callback(i, total)
        return moved
