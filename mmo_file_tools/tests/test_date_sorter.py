"""Tests for the DateSorter class."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mmo_file_tools.tools.date_sorter import DateSorter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sorter() -> DateSorter:
    """Return a fresh DateSorter instance."""
    return DateSorter()


@pytest.fixture()
def photo_dir(tmp_path: Path) -> Path:
    """Create a flat directory with several files for sorting.

    Three files, all written right now so they share a creation date
    in the current month/year.
    """
    root = tmp_path / "photos"
    root.mkdir()
    (root / "pic1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    (root / "pic2.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 200)
    (root / "notes.txt").write_text("hello")
    return root


@pytest.fixture()
def empty_dir(tmp_path: Path) -> Path:
    """An empty directory."""
    root = tmp_path / "empty"
    root.mkdir()
    return root


@pytest.fixture()
def dir_with_subdirs(tmp_path: Path) -> Path:
    """A directory containing files and a sub-directory (should be skipped)."""
    root = tmp_path / "mixed"
    root.mkdir()
    (root / "file.txt").write_text("data")
    sub = root / "subdir"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested")
    return root


# ---------------------------------------------------------------------------
# _creation_time
# ---------------------------------------------------------------------------


class TestCreationTime:
    """Tests for DateSorter._creation_time."""

    def test_returns_float(self, photo_dir: Path) -> None:
        ts = DateSorter._creation_time(photo_dir / "pic1.jpg")
        assert isinstance(ts, float)

    def test_recent_timestamp(self, photo_dir: Path) -> None:
        """Returned timestamp should be very recent (within last 60 s)."""
        ts = DateSorter._creation_time(photo_dir / "pic1.jpg")
        assert time.time() - ts < 60

    def test_fallback_without_birthtime(self, photo_dir: Path) -> None:
        """When st_birthtime is missing, use min(ctime, mtime)."""
        path = photo_dir / "pic1.jpg"
        real_stat = path.stat()

        class FakeStat:
            st_ctime = real_stat.st_ctime
            st_mtime = real_stat.st_mtime
            # no st_birthtime attribute

        with patch.object(Path, "stat", return_value=FakeStat()):
            with patch.object(DateSorter, "_exif_timestamp", return_value=None):
                ts = DateSorter._creation_time(path)
        assert ts == min(real_stat.st_ctime, real_stat.st_mtime)


# ---------------------------------------------------------------------------
# _exif_timestamp
# ---------------------------------------------------------------------------


class TestExifTimestamp:
    """Tests for DateSorter._exif_timestamp."""

    def test_returns_none_for_non_image(self, tmp_path: Path) -> None:
        """Non-image files should return None."""
        txt = tmp_path / "file.txt"
        txt.write_text("hello")
        assert DateSorter._exif_timestamp(txt) is None

    def test_returns_none_for_image_without_exif(self, tmp_path: Path) -> None:
        """JPEG without EXIF data should return None."""
        # Minimal valid JPEG (no EXIF)
        jpg = tmp_path / "noexif.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xd9")
        assert DateSorter._exif_timestamp(jpg) is None

    def test_reads_datetime_original(self, tmp_path: Path) -> None:
        """Should parse DateTimeOriginal from EXIF."""
        from PIL import Image  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        from PIL.ExifTags import Base as ExifBase  # noqa: PLC0415

        exif = img.getexif()
        exif[ExifBase.DateTimeOriginal] = "2025:09:19 14:30:00"
        jpg = tmp_path / "with_exif.jpg"
        img.save(jpg, exif=exif.tobytes())

        ts = DateSorter._exif_timestamp(jpg)
        assert ts is not None
        lt = time.localtime(ts)
        assert lt.tm_year == 2025
        assert lt.tm_mon == 9
        assert lt.tm_mday == 19

    def test_prefers_datetime_original_over_datetime(self, tmp_path: Path) -> None:
        """DateTimeOriginal should take priority over DateTime."""
        from PIL import Image  # noqa: PLC0415
        from PIL.ExifTags import Base as ExifBase  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        exif = img.getexif()
        exif[ExifBase.DateTime] = "2025:11:13 12:00:00"
        exif[ExifBase.DateTimeOriginal] = "2025:09:19 14:30:00"
        jpg = tmp_path / "both_exif.jpg"
        img.save(jpg, exif=exif.tobytes())

        ts = DateSorter._exif_timestamp(jpg)
        lt = time.localtime(ts)
        assert lt.tm_mon == 9  # should use Original, not DateTime

    def test_exif_date_used_by_creation_time(self, tmp_path: Path) -> None:
        """_creation_time should prefer EXIF over filesystem dates."""
        from PIL import Image  # noqa: PLC0415
        from PIL.ExifTags import Base as ExifBase  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        exif = img.getexif()
        exif[ExifBase.DateTimeOriginal] = "2025:09:19 18:21:39"
        jpg = tmp_path / "photo.jpg"
        img.save(jpg, exif=exif.tobytes())

        ts = DateSorter._creation_time(jpg)
        lt = time.localtime(ts)
        assert lt.tm_year == 2025
        assert lt.tm_mon == 9

    def test_returns_none_for_image_with_empty_exif(self, tmp_path: Path) -> None:
        """JPEG with no EXIF tags should hit the ``if not exif`` branch."""
        from PIL import Image  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        jpg = tmp_path / "empty_exif.jpg"
        img.save(jpg)
        assert DateSorter._exif_timestamp(jpg) is None

    def test_returns_none_for_exif_without_date_tags(self, tmp_path: Path) -> None:
        """Image with EXIF but no date tags should return None after the loop."""
        from PIL import Image  # noqa: PLC0415
        from PIL.ExifTags import Base as ExifBase  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        exif = img.getexif()
        exif[ExifBase.Make] = "TestCamera"
        jpg = tmp_path / "nodate_exif.jpg"
        img.save(jpg, exif=exif.tobytes())
        assert DateSorter._exif_timestamp(jpg) is None

    def test_corrupted_exif_returns_none(self, tmp_path: Path) -> None:
        """Corrupted EXIF should not crash, just return None."""
        jpg = tmp_path / "corrupt.jpg"
        # Invalid JPEG-like bytes
        jpg.write_bytes(b"\xff\xd8\xff\xe1\x00\x08Exif\x00\x00GARBAGE")
        result = DateSorter._exif_timestamp(jpg)
        assert result is None


# ---------------------------------------------------------------------------
# _folder_name
# ---------------------------------------------------------------------------


class TestFolderName:
    """Tests for DateSorter._folder_name."""

    def test_known_timestamp(self) -> None:
        # 2025-05-15 12:00:00 UTC
        import calendar as cal

        ts = time.mktime(time.strptime("2025-05-15", "%Y-%m-%d"))
        result = DateSorter._folder_name(ts)
        assert result == "2025/05_May"

    def test_january(self) -> None:
        ts = time.mktime(time.strptime("2024-01-01", "%Y-%m-%d"))
        result = DateSorter._folder_name(ts)
        assert result == "2024/01_Jan"

    def test_december(self) -> None:
        ts = time.mktime(time.strptime("2023-12-31", "%Y-%m-%d"))
        result = DateSorter._folder_name(ts)
        assert result == "2023/12_Dec"


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


class TestPreview:
    """Tests for DateSorter.preview."""

    def test_returns_list_of_dicts(self, sorter: DateSorter, photo_dir: Path) -> None:
        plan = sorter.preview(photo_dir)
        assert isinstance(plan, list)
        assert len(plan) == 3
        for entry in plan:
            assert "file" in entry
            assert "source" in entry
            assert "folder" in entry
            assert "destination" in entry

    def test_source_paths_are_absolute(self, sorter: DateSorter, photo_dir: Path) -> None:
        plan = sorter.preview(photo_dir)
        for entry in plan:
            assert os.path.isabs(entry["source"])
            assert os.path.isabs(entry["destination"])

    def test_folder_format(self, sorter: DateSorter, photo_dir: Path) -> None:
        plan = sorter.preview(photo_dir)
        import re

        for entry in plan:
            assert re.match(r"\d{4}/\d{2}_[A-Z][a-z]{2}", entry["folder"])

    def test_empty_directory(self, sorter: DateSorter, empty_dir: Path) -> None:
        plan = sorter.preview(empty_dir)
        assert plan == []

    def test_skips_subdirectories(self, sorter: DateSorter, dir_with_subdirs: Path) -> None:
        """Sub-directories should not appear in the plan."""
        plan = sorter.preview(dir_with_subdirs)
        assert len(plan) == 1
        assert plan[0]["file"] == "file.txt"

    def test_nonexistent_directory(self, sorter: DateSorter, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            sorter.preview(tmp_path / "nonexistent")

    def test_progress_callback(self, sorter: DateSorter, tmp_path: Path) -> None:
        """Progress callback should be called at least once."""
        root = tmp_path / "big"
        root.mkdir()
        for i in range(5):
            (root / f"f{i}.txt").write_text(f"data{i}")

        calls: list[int] = []
        plan = sorter.preview(root, progress_callback=lambda n: calls.append(n))
        assert len(plan) == 5
        # Final callback with total count
        assert calls[-1] == 5

    def test_progress_callback_every_50(self, sorter: DateSorter, tmp_path: Path) -> None:
        """Progress callback fires at every 50-file boundary during scan."""
        root = tmp_path / "many"
        root.mkdir()
        for i in range(55):
            (root / f"f{i:03d}.txt").write_text(f"data{i}")

        calls: list[int] = []
        sorter.preview(root, progress_callback=lambda n: calls.append(n))
        # Should see both 50 (mid-loop) and 55 (final)
        assert 50 in calls
        assert calls[-1] == 55

    def test_destination_under_root(self, sorter: DateSorter, photo_dir: Path) -> None:
        """Destinations should be within the source root directory."""
        plan = sorter.preview(photo_dir)
        root = str(photo_dir.resolve())
        for entry in plan:
            assert entry["destination"].startswith(root)

    def test_accepts_string_directory(self, sorter: DateSorter, photo_dir: Path) -> None:
        """Should accept a plain string path too."""
        plan = sorter.preview(str(photo_dir))
        assert len(plan) == 3


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


class TestExecute:
    """Tests for DateSorter.execute."""

    def test_moves_files(self, sorter: DateSorter, photo_dir: Path) -> None:
        plan = sorter.preview(photo_dir)
        moved = sorter.execute(plan)
        assert len(moved) == 3

        # Original files should be gone
        for entry in plan:
            assert not Path(entry["source"]).exists()
            assert Path(entry["destination"]).exists()

    def test_creates_target_dirs(self, sorter: DateSorter, photo_dir: Path) -> None:
        plan = sorter.preview(photo_dir)
        sorter.execute(plan)
        for entry in plan:
            assert Path(entry["destination"]).parent.is_dir()

    def test_progress_callback(self, sorter: DateSorter, photo_dir: Path) -> None:
        plan = sorter.preview(photo_dir)
        calls: list[tuple[int, int]] = []
        sorter.execute(plan, progress_callback=lambda m, t: calls.append((m, t)))
        assert len(calls) == len(plan)
        assert calls[-1] == (len(plan), len(plan))

    def test_skips_missing_source(self, sorter: DateSorter, photo_dir: Path) -> None:
        """Files that vanish between preview and execute are skipped."""
        plan = sorter.preview(photo_dir)
        # Delete one file
        Path(plan[0]["source"]).unlink()
        moved = sorter.execute(plan)
        assert len(moved) == len(plan) - 1

    def test_empty_plan(self, sorter: DateSorter) -> None:
        moved = sorter.execute([])
        assert moved == []

    def test_returns_correct_entries(self, sorter: DateSorter, photo_dir: Path) -> None:
        plan = sorter.preview(photo_dir)
        moved = sorter.execute(plan)
        for entry in moved:
            assert "file" in entry
            assert "source" in entry
            assert "destination" in entry
            assert "folder" in entry
