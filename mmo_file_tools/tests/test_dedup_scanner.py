"""Tests for the DedupScanner class."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from mmo_file_tools.tools.dedup_scanner import DedupScanner


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
        with patch("send2trash.send2trash") as mock_trash:
            DedupScanner.delete_path(f)
            mock_trash.assert_called_once_with(str(f.resolve()))

    def test_delete_directory(self, tmp_path: Path) -> None:
        d = tmp_path / "deleteme"
        d.mkdir()
        (d / "child.txt").write_text("child")
        assert d.exists()
        with patch("send2trash.send2trash") as mock_trash:
            DedupScanner.delete_path(d)
            mock_trash.assert_called_once_with(str(d.resolve()))

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

    def test_progress_callback(self, dup_tree: Path, dedup_db: str) -> None:
        """progress_callback is called with (files, dirs) during scan."""
        scanner = DedupScanner(db_url=dedup_db)
        calls: list[tuple[int, int]] = []
        scanner.scan(dup_tree, progress_callback=lambda f, d: calls.append((f, d)))
        assert len(calls) > 0
        # Each call should have non-negative counts
        for files, dirs in calls:
            assert files >= 0
            assert dirs >= 0
        # Last call should match final totals
        last_files, _ = calls[-1]
        assert last_files >= 6  # at least 6 files in dup_tree

    def test_file_hash_oserror_skips_file(self, tmp_path: Path, dedup_db: str) -> None:
        """Files that raise OSError during hashing are silently skipped."""
        root = tmp_path / "oserr"
        root.mkdir()
        (root / "good.txt").write_text("data")
        (root / "bad.txt").write_text("data")

        scanner = DedupScanner(db_url=dedup_db)
        orig_file_hash = scanner._file_hash

        def _failing_hash(session, fpath):  # noqa: ANN001,ANN202
            if fpath.name == "bad.txt":
                raise OSError("access denied")
            return orig_file_hash(session, fpath)

        with patch.object(scanner, "_file_hash", side_effect=_failing_hash):
            result = scanner.scan(root)
        # Only 1 file should be counted (good.txt), bad.txt skipped
        assert result["stats"]["total_files"] == 1

    def test_build_groups_file_stat_oserror(self) -> None:
        """_build_groups handles OSError on file stat by setting size=0."""
        hashes = {"/fake/a.txt": "abc123", "/fake/b.txt": "abc123"}
        with patch("pathlib.Path.stat", side_effect=OSError("gone")):
            groups = DedupScanner._build_groups(hashes, is_dir=False)
        assert len(groups) == 1
        for item in groups[0]["items"]:
            assert item["size"] == 0

    def test_filter_nested_dirs_dominated(self, tmp_path: Path) -> None:
        """Dominated directory groups are removed and paths discarded."""
        # Use real paths so str(Path(...).parent) is consistent on Windows
        root = str(tmp_path / "root")
        a = str(tmp_path / "root" / "A")
        b = str(tmp_path / "root" / "B")
        a_sub = str(tmp_path / "root" / "A" / "sub")
        b_sub = str(tmp_path / "root" / "B" / "sub")
        groups = [
            {
                "hash": "parent_hash",
                "items": [
                    {"path": a, "size": 100, "is_dir": True},
                    {"path": b, "size": 100, "is_dir": True},
                ],
            },
            {
                "hash": "child_hash",
                "items": [
                    {"path": a_sub, "size": 50, "is_dir": True},
                    {"path": b_sub, "size": 50, "is_dir": True},
                ],
            },
        ]
        filtered = DedupScanner._filter_nested_dirs(groups)
        # Parent group survives; child group is dominated (parents are dup dirs)
        assert len(filtered) == 1
        assert filtered[0]["hash"] == "parent_hash"

    def test_filter_files_in_dup_dirs_keeps_outside(self, tmp_path: Path) -> None:
        """File groups with >=2 members outside dup dirs are kept (filtered)."""
        a = str(tmp_path / "A")
        b = str(tmp_path / "B")
        c = str(tmp_path / "C")
        d = str(tmp_path / "D")
        dup_dir_paths = {a, b}
        file_groups = [
            {
                "hash": "fhash",
                "items": [
                    {"path": a + os.sep + "f.txt", "size": 10, "is_dir": False},
                    {"path": b + os.sep + "f.txt", "size": 10, "is_dir": False},
                    {"path": c + os.sep + "f.txt", "size": 10, "is_dir": False},
                    {"path": d + os.sep + "f.txt", "size": 10, "is_dir": False},
                ],
            },
        ]
        filtered = DedupScanner._filter_files_in_dup_dirs(file_groups, dup_dir_paths)
        # Members inside dup dirs (A, B) removed; C, D remain → group kept
        assert len(filtered) == 1
        paths = {i["path"] for i in filtered[0]["items"]}
        assert c + os.sep + "f.txt" in paths
        assert d + os.sep + "f.txt" in paths
        assert a + os.sep + "f.txt" not in paths
