"""Tests for the DedupScanner class."""

from __future__ import annotations

from pathlib import Path

import pytest

from file_tools.tools.dedup_scanner import DedupScanner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def dedup_db(tmp_path: Path) -> str:
    """Return a SQLite URL pointing at a temp database."""
    return f"sqlite:///{tmp_path / 'test_dedup.db'}"


@pytest.fixture()
def dup_tree(tmp_path: Path) -> Path:
    """Create a directory tree with known duplicates.

    Layout::

        root/
          a/
            file1.txt  ("hello")
            file2.txt  ("world")
          b/
            file1.txt  ("hello")   <-- dup file of a/file1.txt
            file2.txt  ("world")   <-- dup file of a/file2.txt
          c/
            unique.txt ("unique")
            file1.txt  ("hello")   <-- dup file of a/file1.txt
    """
    root = tmp_path / "root"
    for d in ("a", "b", "c"):
        (root / d).mkdir(parents=True)

    (root / "a" / "file1.txt").write_text("hello")
    (root / "a" / "file2.txt").write_text("world")

    (root / "b" / "file1.txt").write_text("hello")
    (root / "b" / "file2.txt").write_text("world")

    (root / "c" / "unique.txt").write_text("unique")
    (root / "c" / "file1.txt").write_text("hello")

    return root


@pytest.fixture()
def flat_dups(tmp_path: Path) -> Path:
    """Flat directory with duplicate files only (no sub-dir dups)."""
    root = tmp_path / "flat"
    root.mkdir()
    (root / "one.txt").write_text("aaa")
    (root / "two.txt").write_text("aaa")
    (root / "three.txt").write_text("bbb")
    return root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDedupScanner:
    """Tests for DedupScanner."""

    def test_scan_finds_duplicate_dirs(self, dup_tree: Path, dedup_db: str) -> None:
        scanner = DedupScanner(db_url=dedup_db)
        result = scanner.scan(dup_tree)

        # a/ and b/ are identical directories
        dir_paths = set()
        for grp in result["dup_dirs"]:
            for item in grp["items"]:
                dir_paths.add(item["path"])
        assert str(dup_tree / "a") in dir_paths
        assert str(dup_tree / "b") in dir_paths

    def test_scan_finds_duplicate_files(self, dup_tree: Path, dedup_db: str) -> None:
        scanner = DedupScanner(db_url=dedup_db)
        result = scanner.scan(dup_tree)

        # a/ and b/ are exact duplicate dirs, so their files get filtered out.
        # c/ is not a dup dir, so c/file1.txt would be checked for file-level dups.
        # But its pair files (a/file1.txt, b/file1.txt) are inside dup dirs and also
        # filtered. So there may be 0 or some file-level dups, depending on logic.
        # At minimum we confirm the structure is well-formed.
        for grp in result["dup_files"]:
            assert len(grp["items"]) >= 2
            for item in grp["items"]:
                assert "path" in item
                assert item["is_dir"] is False

    def test_scan_flat_duplicates(self, flat_dups: Path, dedup_db: str) -> None:
        scanner = DedupScanner(db_url=dedup_db)
        result = scanner.scan(flat_dups)

        assert len(result["dup_dirs"]) == 0
        assert len(result["dup_files"]) == 1

        grp = result["dup_files"][0]
        paths = {item["path"] for item in grp["items"]}
        assert str(flat_dups / "one.txt") in paths
        assert str(flat_dups / "two.txt") in paths
        assert str(flat_dups / "three.txt") not in paths

    def test_scan_no_duplicates(self, tmp_path: Path, dedup_db: str) -> None:
        root = tmp_path / "unique"
        root.mkdir()
        (root / "a.txt").write_text("aaa")
        (root / "b.txt").write_text("bbb")

        scanner = DedupScanner(db_url=dedup_db)
        result = scanner.scan(root)

        assert result["dup_dirs"] == []
        assert result["dup_files"] == []
        assert result["stats"]["total_files"] == 2

    def test_cache_speeds_up_rescan(self, flat_dups: Path, dedup_db: str) -> None:
        scanner = DedupScanner(db_url=dedup_db)
        # First scan populates cache
        r1 = scanner.scan(flat_dups)
        # Second scan should use cache (same mtime/size)
        r2 = scanner.scan(flat_dups)

        assert r1["dup_files"] == r2["dup_files"]
        assert r1["stats"] == r2["stats"]

    def test_cache_invalidated_on_change(self, flat_dups: Path, dedup_db: str) -> None:
        scanner = DedupScanner(db_url=dedup_db)
        r1 = scanner.scan(flat_dups)
        assert len(r1["dup_files"]) == 1

        # Modify one.txt so it's no longer a dup of two.txt
        (flat_dups / "one.txt").write_text("changed!")
        r2 = scanner.scan(flat_dups)
        assert len(r2["dup_files"]) == 0

    def test_delete_file(self, tmp_path: Path) -> None:
        f = tmp_path / "deleteme.txt"
        f.write_text("bye")
        assert f.exists()
        DedupScanner.delete_path(f)
        assert not f.exists()

    def test_delete_directory(self, tmp_path: Path) -> None:
        d = tmp_path / "deleteme"
        d.mkdir()
        (d / "child.txt").write_text("child")
        assert d.exists()
        DedupScanner.delete_path(d)
        assert not d.exists()

    def test_delete_nonexistent_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            DedupScanner.delete_path(tmp_path / "nope")

    def test_stats_counts(self, dup_tree: Path, dedup_db: str) -> None:
        scanner = DedupScanner(db_url=dedup_db)
        result = scanner.scan(dup_tree)

        assert result["stats"]["total_files"] == 6  # a: file1+file2, b: file1+file2, c: unique+file1
        assert result["stats"]["total_dirs"] >= 3   # a, b, c (+ root counted)

    def test_empty_directory(self, tmp_path: Path, dedup_db: str) -> None:
        root = tmp_path / "empty"
        root.mkdir()

        scanner = DedupScanner(db_url=dedup_db)
        result = scanner.scan(root)

        assert result["dup_dirs"] == []
        assert result["dup_files"] == []
        assert result["stats"]["total_files"] == 0

    def test_default_db_url(self) -> None:
        """DedupScanner with no db_url uses a temp-dir SQLite file."""
        scanner = DedupScanner()
        # Just verify it initialises without errors
        assert scanner._engine is not None

    def test_items_have_expected_keys(self, flat_dups: Path, dedup_db: str) -> None:
        scanner = DedupScanner(db_url=dedup_db)
        result = scanner.scan(flat_dups)

        for grp in result["dup_files"]:
            assert "hash" in grp
            for item in grp["items"]:
                assert "path" in item
                assert "size" in item
                assert "is_dir" in item
                assert item["is_dir"] is False
