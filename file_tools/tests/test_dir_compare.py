"""Tests for file_tools.tools.dir_compare."""

from __future__ import annotations

from pathlib import Path

from file_tools.tools.dir_compare import (
    compare_directories,
    hash_file,
    sync_directories,
)


# ---------------------------------------------------------------------------
# hash_file
# ---------------------------------------------------------------------------


def test_hash_file_same_content_same_hash(tmp_path: Path) -> None:
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("hello")
    f2.write_text("hello")
    assert hash_file(f1) == hash_file(f2)


def test_hash_file_different_content_different_hash(tmp_path: Path) -> None:
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("hello")
    f2.write_text("world")
    assert hash_file(f1) != hash_file(f2)


def test_hash_file_empty(tmp_path: Path) -> None:
    f = tmp_path / "empty.bin"
    f.write_bytes(b"")
    h = hash_file(f)
    assert isinstance(h, str) and len(h) > 0


# ---------------------------------------------------------------------------
# compare_directories
# ---------------------------------------------------------------------------


def test_compare_all_missing(src_dir: Path, tgt_dir: Path) -> None:
    result = compare_directories(src_dir, tgt_dir)
    assert set(result["missing"]) == {"a.txt", "b.txt", str(Path("sub") / "c.txt")}
    assert result["modified"] == []
    assert result["identical"] == []
    assert result["extra"] == []


def test_compare_all_identical(src_dir: Path, tgt_dir: Path) -> None:
    # Mirror source to target first
    import shutil
    for f in src_dir.rglob("*"):
        if f.is_file():
            dest = tgt_dir / f.relative_to(src_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)

    result = compare_directories(src_dir, tgt_dir)
    assert result["missing"] == []
    assert result["modified"] == []
    assert len(result["identical"]) == 3
    assert result["extra"] == []


def test_compare_modified(src_dir: Path, tgt_dir: Path) -> None:
    # Copy one file but alter its content in target
    (tgt_dir / "a.txt").write_text("different")
    result = compare_directories(src_dir, tgt_dir)
    assert "a.txt" in result["modified"]
    assert "b.txt" in result["missing"]


def test_compare_extra(src_dir: Path, tgt_dir: Path) -> None:
    # Extra file only in target
    (tgt_dir / "extra.txt").write_text("bonus")
    result = compare_directories(src_dir, tgt_dir)
    assert "extra.txt" in result["extra"]


# ---------------------------------------------------------------------------
# sync_directories
# ---------------------------------------------------------------------------


def test_sync_copies_missing(src_dir: Path, tgt_dir: Path) -> None:
    copied = sync_directories(src_dir, tgt_dir)
    assert len(copied) == 3
    assert (tgt_dir / "a.txt").read_text() == "hello"
    assert (tgt_dir / "b.txt").read_text() == "world"
    assert (tgt_dir / "sub" / "c.txt").read_text() == "deep"


def test_sync_explicit_list(src_dir: Path, tgt_dir: Path) -> None:
    copied = sync_directories(src_dir, tgt_dir, files_to_copy=["a.txt"])
    assert copied == ["a.txt"]
    assert (tgt_dir / "a.txt").exists()
    assert not (tgt_dir / "b.txt").exists()


def test_sync_empty_list(src_dir: Path, tgt_dir: Path) -> None:
    copied = sync_directories(src_dir, tgt_dir, files_to_copy=[])
    assert copied == []


def test_sync_copies_modified(src_dir: Path, tgt_dir: Path) -> None:
    (tgt_dir / "a.txt").write_text("old content")
    copied = sync_directories(src_dir, tgt_dir)
    assert "a.txt" in copied
    assert (tgt_dir / "a.txt").read_text() == "hello"
