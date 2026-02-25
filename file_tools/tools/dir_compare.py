"""Directory compare and sync utilities using xxhash3_128."""

from __future__ import annotations

import shutil
from pathlib import Path

import xxhash


def hash_file(path: Path) -> str:
    """Return the xxh3_128 hex digest of *path*."""
    h = xxhash.xxh3_128()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compare_directories(source: Path, target: Path) -> dict[str, list[str]]:
    """Compare *source* and *target* directories recursively.

    Returns a dict with four keys:
    - ``missing``  – files present in source but absent from target
    - ``modified`` – files present in both but with different content
    - ``identical`` – files present in both with identical content
    - ``extra``    – files present in target but absent from source
    """
    source_files = {
        f.relative_to(source): f for f in source.rglob("*") if f.is_file()
    }
    target_files = {
        f.relative_to(target): f for f in target.rglob("*") if f.is_file()
    }

    result: dict[str, list[str]] = {
        "missing": [],
        "modified": [],
        "identical": [],
        "extra": [],
    }

    for rel, src_file in source_files.items():
        rel_str = str(rel)
        if rel not in target_files:
            result["missing"].append(rel_str)
        else:
            if hash_file(src_file) == hash_file(target_files[rel]):
                result["identical"].append(rel_str)
            else:
                result["modified"].append(rel_str)

    for rel in target_files:
        if rel not in source_files:
            result["extra"].append(str(rel))

    return result


def sync_directories(
    source: Path,
    target: Path,
    files_to_copy: list[str] | None = None,
) -> list[str]:
    """Copy *files_to_copy* from *source* to *target*.

    If *files_to_copy* is ``None``, all missing and modified files are copied.
    Returns the list of relative paths that were actually copied.
    """
    if files_to_copy is None:
        cmp = compare_directories(source, target)
        files_to_copy = cmp["missing"] + cmp["modified"]

    copied: list[str] = []
    for rel_str in files_to_copy:
        src_file = source / rel_str
        tgt_file = target / rel_str
        tgt_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, tgt_file)
        copied.append(rel_str)
    return copied
