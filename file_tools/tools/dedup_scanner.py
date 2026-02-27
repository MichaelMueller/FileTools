"""File and directory deduplication scanner with SQLAlchemy-based caching."""

from __future__ import annotations

import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable

import xxhash
from platformdirs import user_data_dir
from sqlalchemy import Column, Float, String, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

_Base = declarative_base()

# ---------------------------------------------------------------------------
# Database model
# ---------------------------------------------------------------------------


class FileHash(_Base):  # type: ignore[misc]
    """Cached hash entry for a single file, keyed by path + mtime + size."""

    __tablename__ = "file_hashes"

    path = Column(String, primary_key=True)
    mtime = Column(Float, nullable=False)
    size = Column(Float, nullable=False)
    hash = Column(String, nullable=False)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class DedupScanner:
    """Scan a directory tree, computing xxHash3-128 for files and directories.

    Hashes are cached in a SQLite database so re-scans of unchanged files
    are fast.  The database URL is taken from the *db_url* parameter
    (defaults to a temp-dir SQLite file).
    """

    def __init__(self, db_url: str | None = None) -> None:
        if db_url is None:
            db_dir = Path(user_data_dir("FileTools", appauthor=False))
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = db_dir / "filetools_dedup.db"
            db_url = f"sqlite:///{db_path}"

        self._engine = create_engine(
            db_url, connect_args={"timeout": 30}, pool_pre_ping=True,
        )
        _Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)

    # -- public API ---------------------------------------------------------

    def scan(
        self,
        root: Path,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Scan *root* and return duplicate groups.

        Parameters
        ----------
        root:
            Directory tree to scan.
        progress_callback:
            Optional callable invoked as ``progress_callback(files_scanned, dirs_scanned)``
            after each file/directory is processed.

        Returns a dict with keys:
        - ``dup_dirs``  – list of duplicate directory groups
        - ``dup_files`` – list of duplicate file groups
        - ``stats``     – summary counts
        """
        root = root.resolve()
        session = self._session_factory()
        try:
            file_hashes: dict[str, str] = {}  # abs-path → hash
            dir_hashes: dict[str, str] = {}   # abs-path → hash

            total_files = 0
            total_dirs = 0

            # Walk bottom-up so children are hashed before parents
            for dirpath, dirnames, filenames in os.walk(root, topdown=False):
                dp = Path(dirpath)

                for fname in filenames:
                    fpath = dp / fname
                    try:
                        h = self._file_hash(session, fpath)
                    except (OSError, PermissionError):
                        continue
                    file_hashes[str(fpath)] = h
                    total_files += 1
                    if progress_callback is not None:
                        progress_callback(total_files, total_dirs)

                # Dir hash = hash of sorted child hashes
                child_hashes: list[str] = []
                for fname in sorted(filenames):
                    fp = str(dp / fname)
                    if fp in file_hashes:
                        child_hashes.append(file_hashes[fp])
                for dname in sorted(dirnames):
                    sub = str(dp / dname)
                    if sub in dir_hashes:
                        child_hashes.append(dir_hashes[sub])

                if child_hashes:
                    h = xxhash.xxh3_128()
                    for ch in child_hashes:
                        h.update(ch.encode())
                    dir_hashes[str(dp)] = h.hexdigest()
                else:
                    dir_hashes[str(dp)] = "empty"
                total_dirs += 1
                if progress_callback is not None:
                    progress_callback(total_files, total_dirs)

            session.commit()

            # Build duplicate groups ------------------------------------------
            # Remove root itself from dir_hashes
            dir_hashes.pop(str(root), None)

            # Remove empty dirs from dedup consideration
            dir_hashes = {p: h for p, h in dir_hashes.items() if h != "empty"}

            dup_dirs = self._build_groups(dir_hashes, is_dir=True)
            # Filter out dirs whose parents are already in a dup group
            dup_dirs = self._filter_nested_dirs(dup_dirs)

            dup_files = self._build_groups(file_hashes, is_dir=False)
            # Filter out files that are inside already-reported dup dirs
            dup_dir_paths = set()
            for grp in dup_dirs:
                for item in grp["items"]:
                    dup_dir_paths.add(item["path"])
            dup_files = self._filter_files_in_dup_dirs(dup_files, dup_dir_paths)

            return {
                "dup_dirs": dup_dirs,
                "dup_files": dup_files,
                "stats": {
                    "total_files": total_files,
                    "total_dirs": total_dirs,
                },
            }
        finally:
            session.close()

    @staticmethod
    def delete_path(path: Path) -> None:
        """Move a file or directory to the system trash / recycle bin.

        Falls back to permanent deletion if *send2trash* is unavailable
        or the trash operation fails (e.g. network drives).
        """
        path = path.resolve()
        if not path.exists():
            msg = f"Path not found: {path}"
            raise FileNotFoundError(msg)

        from send2trash import send2trash  # noqa: PLC0415

        send2trash(str(path))

    # -- internal -----------------------------------------------------------

    def _file_hash(self, session: Session, fpath: Path) -> str:
        """Return the xxh3_128 hex digest of *fpath*, using cache if valid."""
        stat = fpath.stat()
        key = str(fpath)
        cached: FileHash | None = session.get(FileHash, key)

        if (
            cached is not None
            and cached.mtime == stat.st_mtime  # type: ignore[comparison-overlap]
            and cached.size == stat.st_size  # type: ignore[comparison-overlap]
        ):
            return cached.hash  # type: ignore[return-value]

        h = xxhash.xxh3_128()
        with open(fpath, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        digest = h.hexdigest()

        if cached is not None:
            cached.mtime = stat.st_mtime  # type: ignore[assignment]
            cached.size = stat.st_size  # type: ignore[assignment]
            cached.hash = digest  # type: ignore[assignment]
        else:
            session.add(FileHash(path=key, mtime=stat.st_mtime, size=stat.st_size, hash=digest))

        return digest

    @staticmethod
    def _build_groups(hashes: dict[str, str], *, is_dir: bool) -> list[dict]:
        """Group paths by hash, returning only groups with 2+ members."""
        by_hash: dict[str, list[str]] = defaultdict(list)
        for path, h in hashes.items():
            by_hash[h].append(path)

        groups: list[dict] = []
        for h, paths in sorted(by_hash.items()):
            if len(paths) < 2:
                continue
            items = []
            for p in sorted(paths):
                pp = Path(p)
                if is_dir:
                    # Dir size = sum of all files inside
                    size = sum(f.stat().st_size for f in pp.rglob("*") if f.is_file())
                else:
                    try:
                        size = pp.stat().st_size
                    except OSError:
                        size = 0
                items.append({"path": p, "size": size, "is_dir": is_dir})
            groups.append({"hash": h, "items": items})
        return groups

    @staticmethod
    def _filter_nested_dirs(groups: list[dict]) -> list[dict]:
        """Remove directory groups where all members are children of another dup group."""
        # Collect all dup dir paths
        all_dup_dirs: set[str] = set()
        for grp in groups:
            for item in grp["items"]:
                all_dup_dirs.add(item["path"])

        filtered: list[dict] = []
        for grp in groups:
            # Keep the group if at least one member is NOT a direct child of another dup dir
            dominated = True
            for item in grp["items"]:
                parent = str(Path(item["path"]).parent)
                if parent not in all_dup_dirs:
                    dominated = False
                    break
            if not dominated:
                filtered.append(grp)
            else:
                # Remove these paths from the set so deeper nesting works
                for item in grp["items"]:
                    all_dup_dirs.discard(item["path"])

        return filtered

    @staticmethod
    def _filter_files_in_dup_dirs(
        file_groups: list[dict], dup_dir_paths: set[str],
    ) -> list[dict]:
        """Remove file groups where all members live inside reported dup dirs."""
        if not dup_dir_paths:
            return file_groups

        def _is_inside_dup_dir(fpath: str) -> bool:
            for dp in dup_dir_paths:
                if fpath.startswith(dp + os.sep):
                    return True
            return False

        filtered: list[dict] = []
        for grp in file_groups:
            new_items = [i for i in grp["items"] if not _is_inside_dup_dir(i["path"])]
            if len(new_items) >= 2:
                grp["items"] = new_items
                filtered.append(grp)
        return filtered
