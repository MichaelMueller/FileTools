"""Tests for the GpsSorter class."""

from __future__ import annotations

import math
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from file_tools.tools.gps_sorter import GpsSorter, RegionAliasRow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sorter() -> GpsSorter:
    """Return a fresh GpsSorter with an in-memory DB."""
    return GpsSorter(db_url="sqlite:///:memory:")


@pytest.fixture()
def photo_dir(tmp_path: Path) -> Path:
    """Directory with some dummy files."""
    root = tmp_path / "photos"
    root.mkdir()
    (root / "pic1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    (root / "pic2.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 200)
    (root / "notes.txt").write_text("hello")
    return root


@pytest.fixture()
def empty_dir(tmp_path: Path) -> Path:
    root = tmp_path / "empty"
    root.mkdir()
    return root


@pytest.fixture()
def dir_with_subdirs(tmp_path: Path) -> Path:
    root = tmp_path / "mixed"
    root.mkdir()
    (root / "file.txt").write_text("data")
    sub = root / "subdir"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested")
    return root


# ---------------------------------------------------------------------------
# _dms_to_decimal
# ---------------------------------------------------------------------------


class TestDmsToDecimal:
    """Tests for GpsSorter._dms_to_decimal."""

    def test_north(self) -> None:
        result = GpsSorter._dms_to_decimal((48.0, 8.0, 30.0), "N")
        expected = 48.0 + 8.0 / 60 + 30.0 / 3600
        assert abs(result - expected) < 1e-8

    def test_south(self) -> None:
        result = GpsSorter._dms_to_decimal((33.0, 51.0, 0.0), "S")
        assert result < 0
        assert abs(result - -(33.0 + 51.0 / 60)) < 1e-8

    def test_east(self) -> None:
        result = GpsSorter._dms_to_decimal((11.0, 34.0, 48.0), "E")
        expected = 11.0 + 34.0 / 60 + 48.0 / 3600
        assert abs(result - expected) < 1e-8

    def test_west(self) -> None:
        result = GpsSorter._dms_to_decimal((2.0, 10.0, 0.0), "W")
        assert result < 0


# ---------------------------------------------------------------------------
# _haversine_km
# ---------------------------------------------------------------------------


class TestHaversine:
    """Tests for GpsSorter._haversine_km."""

    def test_same_point(self) -> None:
        assert GpsSorter._haversine_km(48.0, 11.0, 48.0, 11.0) == 0.0

    def test_known_distance(self) -> None:
        # Munich (48.1351, 11.5820) → Berlin (52.5200, 13.4050)
        dist = GpsSorter._haversine_km(48.1351, 11.5820, 52.5200, 13.4050)
        # Should be ~ 504 km
        assert 490 < dist < 520

    def test_antipodal(self) -> None:
        # Roughly opposite sides of earth → ~ 20000 km
        dist = GpsSorter._haversine_km(0, 0, 0, 180)
        assert abs(dist - math.pi * 6371) < 10


# ---------------------------------------------------------------------------
# _gps_coordinates
# ---------------------------------------------------------------------------


class TestGpsCoordinates:
    """Tests for GpsSorter._gps_coordinates."""

    def test_returns_none_for_non_image(self, tmp_path: Path) -> None:
        txt = tmp_path / "file.txt"
        txt.write_text("hello")
        assert GpsSorter._gps_coordinates(txt) is None

    def test_returns_none_for_image_without_gps(self, tmp_path: Path) -> None:
        jpg = tmp_path / "noexif.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xd9")
        assert GpsSorter._gps_coordinates(jpg) is None

    def test_returns_none_when_exif_empty(self, tmp_path: Path) -> None:
        """Image opens but getexif() returns empty → None."""
        from PIL import Image  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        jpg = tmp_path / "empty_exif.jpg"
        img.save(jpg)  # saved without any EXIF
        # Patch getexif to return falsy to ensure branch is hit
        with patch("PIL.Image.Image.getexif", return_value={}):
            assert GpsSorter._gps_coordinates(jpg) is None

    def test_extracts_gps_from_exif(self, tmp_path: Path) -> None:
        """Create a JPEG with GPS EXIF and verify extraction."""
        from PIL import Image  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        exif = img.getexif()

        # Build GPS IFD
        gps_ifd = {
            1: "N",  # GPSLatitudeRef
            2: (48.0, 8.0, 30.0),  # GPSLatitude
            3: "E",  # GPSLongitudeRef
            4: (11.0, 34.0, 48.0),  # GPSLongitude
        }
        exif.get_ifd(0x8825).update(gps_ifd)

        jpg = tmp_path / "gps_photo.jpg"
        img.save(jpg, exif=exif.tobytes())

        result = GpsSorter._gps_coordinates(jpg)
        assert result is not None
        lat, lon = result
        assert abs(lat - (48 + 8 / 60 + 30 / 3600)) < 0.001
        assert abs(lon - (11 + 34 / 60 + 48 / 3600)) < 0.001

    def test_corrupted_exif_returns_none(self, tmp_path: Path) -> None:
        jpg = tmp_path / "corrupt.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xe1\x00\x08Exif\x00\x00GARBAGE")
        assert GpsSorter._gps_coordinates(jpg) is None

    def test_image_with_exif_but_no_gps_ifd(self, tmp_path: Path) -> None:
        """Image has EXIF data but no GPS sub-IFD → None."""
        from PIL import Image  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        exif = img.getexif()
        # Add a non-GPS EXIF tag so exif is truthy
        exif[0x0132] = "2025:01:01 00:00:00"  # DateTime
        jpg = tmp_path / "no_gps_ifd.jpg"
        img.save(jpg, exif=exif.tobytes())
        assert GpsSorter._gps_coordinates(jpg) is None

    def test_gps_ifd_with_missing_fields(self, tmp_path: Path) -> None:
        """GPS IFD present but missing longitude → None."""
        from PIL import Image  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        exif = img.getexif()
        # Only latitude, no longitude
        gps_ifd = {
            1: "N",
            2: (48.0, 8.0, 30.0),
            # Missing keys 3 and 4
        }
        exif.get_ifd(0x8825).update(gps_ifd)
        jpg = tmp_path / "partial_gps.jpg"
        img.save(jpg, exif=exif.tobytes())
        assert GpsSorter._gps_coordinates(jpg) is None


# ---------------------------------------------------------------------------
# _match_alias
# ---------------------------------------------------------------------------


class TestMatchAlias:
    """Tests for GpsSorter._match_alias."""

    def test_within_radius(self) -> None:
        aliases = [{"alias": "Home", "lat": 48.135, "lon": 11.582, "radius_km": 5.0}]
        result = GpsSorter._match_alias(48.136, 11.583, aliases)
        assert result == "Home"

    def test_outside_radius(self) -> None:
        aliases = [{"alias": "Home", "lat": 48.135, "lon": 11.582, "radius_km": 0.001}]
        result = GpsSorter._match_alias(52.52, 13.405, aliases)
        assert result is None

    def test_empty_aliases(self) -> None:
        assert GpsSorter._match_alias(48.0, 11.0, []) is None

    def test_closest_wins(self) -> None:
        aliases = [
            {"alias": "Far", "lat": 48.2, "lon": 11.7, "radius_km": 50.0},
            {"alias": "Near", "lat": 48.135, "lon": 11.582, "radius_km": 50.0},
        ]
        result = GpsSorter._match_alias(48.136, 11.583, aliases)
        assert result == "Near"


# ---------------------------------------------------------------------------
# _reverse_geocode
# ---------------------------------------------------------------------------


class TestReverseGeocode:
    """Tests for GpsSorter._reverse_geocode."""

    def test_empty_list(self) -> None:
        assert GpsSorter._reverse_geocode([]) == []

    def test_returns_correct_count(self) -> None:
        coords = [(48.1351, 11.5820), (52.52, 13.405)]
        result = GpsSorter._reverse_geocode(coords)
        assert len(result) == 2
        for folder in result:
            assert "/" in folder  # country/city format

    def test_known_city(self) -> None:
        # Munich
        result = GpsSorter._reverse_geocode([(48.1351, 11.5820)])
        assert len(result) == 1
        # Should contain "DE" for Germany
        assert "DE" in result[0] or "Munich" in result[0] or "München" in result[0]


# ---------------------------------------------------------------------------
# _sanitise_folder
# ---------------------------------------------------------------------------


class TestSanitiseFolder:
    """Tests for GpsSorter._sanitise_folder."""

    def test_removes_invalid_chars(self) -> None:
        assert "<" not in GpsSorter._sanitise_folder('a<b>c:d"e')
        assert ">" not in GpsSorter._sanitise_folder('a<b>c:d"e')

    def test_no_change_for_valid(self) -> None:
        assert GpsSorter._sanitise_folder("DE/Munich") == "DE/Munich"

    def test_collapses_underscores(self) -> None:
        result = GpsSorter._sanitise_folder("a:::b")
        assert "___" not in result


# ---------------------------------------------------------------------------
# _file_timestamp
# ---------------------------------------------------------------------------


class TestFileTimestamp:
    """Tests for GpsSorter._file_timestamp."""

    def test_returns_float(self, tmp_path: Path) -> None:
        f = tmp_path / "ts.txt"
        f.write_text("hello")
        result = GpsSorter._file_timestamp(f)
        assert isinstance(result, float)
        assert result > 0

    def test_delegates_to_date_sorter(self, tmp_path: Path) -> None:
        f = tmp_path / "ts2.txt"
        f.write_text("data")
        with patch(
            "file_tools.tools.date_sorter.DateSorter._creation_time",
            return_value=12345.0,
        ):
            assert GpsSorter._file_timestamp(f) == 12345.0


# ---------------------------------------------------------------------------
# Region alias persistence
# ---------------------------------------------------------------------------


class TestAliasPersistence:
    """Tests for DB-backed region alias persistence."""

    def test_get_aliases_empty(self, sorter: GpsSorter) -> None:
        assert sorter.get_aliases() == []

    def test_save_and_get_alias(self, sorter: GpsSorter) -> None:
        sorter._save_alias("Home", 48.135, 11.582, radius_km=2.0)
        aliases = sorter.get_aliases()
        assert len(aliases) == 1
        assert aliases[0]["alias"] == "Home"
        assert aliases[0]["lat"] == 48.135
        assert aliases[0]["lon"] == 11.582
        assert aliases[0]["radius_km"] == 2.0
        assert "id" in aliases[0]

    def test_get_aliases_ordered(self, sorter: GpsSorter) -> None:
        sorter._save_alias("Work", 48.2, 11.6, radius_km=1.0)
        sorter._save_alias("Home", 1.0, 2.0, radius_km=1.0)
        aliases = sorter.get_aliases()
        assert len(aliases) == 2
        assert aliases[0]["alias"] == "Home"
        assert aliases[1]["alias"] == "Work"

    def test_save_alias_updates_nearby(self, sorter: GpsSorter) -> None:
        """Saving an alias near an existing one updates it."""
        sorter._save_alias("Old Name", 48.135, 11.582, radius_km=5.0)
        # Same location, new name
        sorter._save_alias("New Name", 48.136, 11.583, radius_km=5.0)
        aliases = sorter.get_aliases()
        assert len(aliases) == 1
        assert aliases[0]["alias"] == "New Name"

    def test_save_alias_creates_new_when_far(self, sorter: GpsSorter) -> None:
        """Saving an alias far from any existing creates a new entry."""
        sorter._save_alias("Home", 48.135, 11.582, radius_km=1.0)
        sorter._save_alias("Dubai", 25.2, 55.3, radius_km=1.0)
        aliases = sorter.get_aliases()
        assert len(aliases) == 2

    def test_delete_alias(self, sorter: GpsSorter) -> None:
        sorter._save_alias("Tmp", 0.0, 0.0, radius_km=1.0)
        aliases = sorter.get_aliases()
        sorter.delete_alias(aliases[0]["id"])
        assert sorter.get_aliases() == []

    def test_delete_nonexistent_is_noop(self, sorter: GpsSorter) -> None:
        sorter.delete_alias(9999)  # should not raise

    def test_alias_to_dict(self) -> None:
        row = RegionAliasRow(
            id=7, alias="Test", lat=1.5, lon=2.5, radius_km=3.0,
        )
        d = GpsSorter._alias_to_dict(row)
        assert d == {
            "id": 7, "alias": "Test",
            "lat": 1.5, "lon": 2.5, "radius_km": 3.0,
        }


# ---------------------------------------------------------------------------
# preview (with trip detection)
# ---------------------------------------------------------------------------


class TestPreview:
    """Tests for GpsSorter.preview with mocked GPS + timestamp data."""

    def test_no_gps_goes_to_no_gps_folder(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        plan = result["plan"]
        assert len(plan) == 3
        for entry in plan:
            assert entry["folder"] == "No GPS"
            assert entry["location_name"] == "No GPS"
            assert entry["group"] == "no_gps"
            assert entry["lat"] is None

    def test_alias_match_from_db(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        """Photos near a saved alias are classified as 'location'."""
        sorter._save_alias("Home", 48.135, 11.582, radius_km=50.0)
        with (
            patch.object(
                GpsSorter, "_gps_coordinates", return_value=(48.136, 11.583),
            ),
            patch.object(GpsSorter, "_file_timestamp", return_value=2000.0),
        ):
            result = sorter.preview(photo_dir)
        for entry in result["plan"]:
            assert entry["folder"] == "Home"
            assert entry["location_name"] == "Home"
            assert entry["group"] == "location"

    def test_trip_detection_creates_trips(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        """Files with GPS but no alias → trip group."""
        with (
            patch.object(
                GpsSorter, "_gps_coordinates", return_value=(25.2, 55.3),
            ),
            patch.object(GpsSorter, "_file_timestamp", return_value=3000.0),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=["AE/Dubai", "AE/Dubai", "AE/Dubai"],
            ),
        ):
            result = sorter.preview(photo_dir)
        assert len(result["trips"]) == 1
        trip = result["trips"][0]
        assert trip["suggested_name"] == "AE/Dubai"
        assert trip["file_count"] == 3
        assert "centroid_lat" in trip
        assert "centroid_lon" in trip
        assert "radius_km" in trip
        for entry in result["plan"]:
            assert entry["group"] == "trip"
            assert entry["trip_id"] == 1
            assert entry["folder"] == "AE/Dubai"

    def test_alias_breaks_trip_sequence(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """Alias-matched photos break the trip sequence → two trips."""
        root = tmp_path / "trips"
        root.mkdir()
        files = ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"]
        for f in files:
            (root / f).write_text(f)

        sorter._save_alias("Home", 48.135, 11.582, radius_km=50.0)

        ts_counter = [0]

        def fake_ts(path: Path) -> float:
            ts_counter[0] += 1
            return float(ts_counter[0] * 1000)

        def fake_gps(path: Path) -> tuple[float, float] | None:
            if path.name in ("a.jpg", "b.jpg"):
                return (25.2, 55.3)  # Dubai
            if path.name == "c.jpg":
                return (48.136, 11.583)  # Home
            if path.name in ("d.jpg", "e.jpg"):
                return (52.52, 13.405)  # Berlin
            return None

        with (
            patch.object(GpsSorter, "_gps_coordinates", side_effect=fake_gps),
            patch.object(GpsSorter, "_file_timestamp", side_effect=fake_ts),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=[
                    "AE/Dubai", "AE/Dubai",
                    "DE/Berlin", "DE/Berlin",
                ],
            ),
        ):
            result = sorter.preview(root)

        assert len(result["trips"]) == 2
        groups = {e["file"]: e["group"] for e in result["plan"]}
        assert groups["a.jpg"] == "trip"
        assert groups["b.jpg"] == "trip"
        assert groups["c.jpg"] == "location"
        assert groups["d.jpg"] == "trip"
        assert groups["e.jpg"] == "trip"

        trip_ids = {e["file"]: e["trip_id"] for e in result["plan"]}
        assert trip_ids["a.jpg"] == trip_ids["b.jpg"]
        assert trip_ids["d.jpg"] == trip_ids["e.jpg"]
        assert trip_ids["a.jpg"] != trip_ids["d.jpg"]

    def test_distance_splits_trips(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """Photos far apart split into separate trips even if consecutive."""
        root = tmp_path / "split"
        root.mkdir()
        for f in ("a.jpg", "b.jpg", "c.jpg", "d.jpg"):
            (root / f).write_text(f)

        ts_counter = [0]

        def fake_ts(path: Path) -> float:
            ts_counter[0] += 1
            return float(ts_counter[0] * 1000)

        def fake_gps(path: Path) -> tuple[float, float] | None:
            if path.name in ("a.jpg", "b.jpg"):
                return (25.2, 55.3)   # Dubai
            if path.name in ("c.jpg", "d.jpg"):
                return (52.52, 13.405)  # Berlin (>50 km from Dubai)
            return None

        with (
            patch.object(GpsSorter, "_gps_coordinates", side_effect=fake_gps),
            patch.object(GpsSorter, "_file_timestamp", side_effect=fake_ts),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=[
                    "AE/Dubai", "AE/Dubai",
                    "DE/Berlin", "DE/Berlin",
                ],
            ),
        ):
            result = sorter.preview(root)

        assert len(result["trips"]) == 2
        trip_ids = {e["file"]: e["trip_id"] for e in result["plan"]}
        assert trip_ids["a.jpg"] == trip_ids["b.jpg"]
        assert trip_ids["c.jpg"] == trip_ids["d.jpg"]
        assert trip_ids["a.jpg"] != trip_ids["c.jpg"]

    def test_nearby_photos_stay_in_same_trip(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """Photos close together remain in the same trip cluster."""
        root = tmp_path / "close"
        root.mkdir()
        for f in ("a.jpg", "b.jpg", "c.jpg"):
            (root / f).write_text(f)

        ts_counter = [0]

        def fake_ts(path: Path) -> float:
            ts_counter[0] += 1
            return float(ts_counter[0] * 1000)

        # All within ~10 km of each other in Dubai
        def fake_gps(path: Path) -> tuple[float, float] | None:
            if path.name == "a.jpg":
                return (25.20, 55.30)
            if path.name == "b.jpg":
                return (25.21, 55.31)
            if path.name == "c.jpg":
                return (25.19, 55.29)
            return None

        with (
            patch.object(GpsSorter, "_gps_coordinates", side_effect=fake_gps),
            patch.object(GpsSorter, "_file_timestamp", side_effect=fake_ts),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=["AE/Dubai", "AE/Dubai", "AE/Dubai"],
            ),
        ):
            result = sorter.preview(root)

        assert len(result["trips"]) == 1
        for entry in result["plan"]:
            assert entry["trip_id"] == 1

    def test_empty_directory(self, sorter: GpsSorter, empty_dir: Path) -> None:
        result = sorter.preview(empty_dir)
        assert result["plan"] == []
        assert result["trips"] == []
        assert result["total"] == 0

    def test_skips_subdirectories(
        self, sorter: GpsSorter, dir_with_subdirs: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(dir_with_subdirs)
        assert len(result["plan"]) == 1
        assert result["plan"][0]["file"] == "file.txt"

    def test_nonexistent_directory(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        with pytest.raises(FileNotFoundError):
            sorter.preview(tmp_path / "nonexistent")

    def test_returns_dict_structure(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        assert "plan" in result
        assert "trips" in result
        assert "total" in result
        assert "no_gps_count" in result
        for entry in result["plan"]:
            for key in ("file", "source", "folder", "destination",
                        "lat", "lon", "location_name", "group", "trip_id"):
                assert key in entry

    def test_source_paths_are_absolute(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        for entry in result["plan"]:
            assert os.path.isabs(entry["source"])
            assert os.path.isabs(entry["destination"])

    def test_destination_under_root(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        root = str(photo_dir.resolve())
        for entry in result["plan"]:
            assert entry["destination"].startswith(root)

    def test_accepts_string_directory(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(str(photo_dir))
        assert len(result["plan"]) == 3

    def test_progress_callback(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        root = tmp_path / "big"
        root.mkdir()
        for i in range(5):
            (root / f"f{i}.txt").write_text(f"data{i}")

        calls: list[int] = []
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(
                root, progress_callback=lambda n: calls.append(n),
            )
        assert len(result["plan"]) == 5
        assert calls[-1] == 5

    def test_progress_callback_every_50(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """Progress callback fires every 50 files during scan."""
        root = tmp_path / "many"
        root.mkdir()
        for i in range(60):
            (root / f"f{i:03d}.txt").write_text(f"data{i}")

        calls: list[int] = []
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(
                root, progress_callback=lambda n: calls.append(n),
            )
        assert len(result["plan"]) == 60
        assert 50 in calls  # intermediate callback at count == 50
        assert calls[-1] == 60

    def test_mixed_gps_and_no_gps(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        """Some files with GPS, some without."""

        def fake_gps(path: Path) -> tuple[float, float] | None:
            if path.name == "pic1.jpg":
                return (48.1351, 11.5820)
            return None

        with (
            patch.object(GpsSorter, "_gps_coordinates", side_effect=fake_gps),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=["DE/Munich"],
            ),
        ):
            result = sorter.preview(photo_dir)

        folders = {e["file"]: e["folder"] for e in result["plan"]}
        assert folders["pic1.jpg"] == "DE/Munich"
        assert folders["pic2.jpg"] == "No GPS"
        assert folders["notes.txt"] == "No GPS"

    def test_no_timestamp_in_plan_entries(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        """Raw timestamp must not leak into the plan entries."""
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        for entry in result["plan"]:
            assert "timestamp" not in entry

    def test_trip_date_range(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """Trip metadata includes correct start/end dates."""
        root = tmp_path / "dated"
        root.mkdir()
        (root / "a.jpg").write_text("a")
        (root / "b.jpg").write_text("b")

        counter = [0]

        def fake_ts(path: Path) -> float:
            counter[0] += 1
            # a.jpg: 2025-03-10 00:00 UTC, b.jpg: 2025-03-17 00:00 UTC
            if counter[0] <= 2:
                return 1741564800.0 if counter[0] == 1 else 1742169600.0
            return 1741564800.0

        with (
            patch.object(
                GpsSorter, "_gps_coordinates", return_value=(25.0, 55.0),
            ),
            patch.object(GpsSorter, "_file_timestamp", side_effect=fake_ts),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=["AE/Dubai", "AE/Dubai"],
            ),
        ):
            result = sorter.preview(root)

        assert len(result["trips"]) == 1
        trip = result["trips"][0]
        assert trip["start_date"] == "2025-03-10"
        assert trip["end_date"] == "2025-03-17"

    def test_trip_suggested_name_most_common(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """Suggested name is the most common reverse-geocoded city."""
        root = tmp_path / "multi"
        root.mkdir()
        for f in ("a.jpg", "b.jpg", "c.jpg"):
            (root / f).write_text(f)

        with (
            patch.object(
                GpsSorter, "_gps_coordinates", return_value=(25.0, 55.0),
            ),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=["AE/Dubai", "AE/Abu Dhabi", "AE/Dubai"],
            ),
        ):
            result = sorter.preview(root)

        assert result["trips"][0]["suggested_name"] == "AE/Dubai"

    def test_no_gps_count(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        assert result["no_gps_count"] == 3
        assert result["total"] == 3

    def test_trip_centroid_in_metadata(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        """Trip metadata includes centroid and radius."""
        with (
            patch.object(
                GpsSorter, "_gps_coordinates", return_value=(25.2, 55.3),
            ),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=["AE/Dubai", "AE/Dubai", "AE/Dubai"],
            ),
        ):
            result = sorter.preview(photo_dir)
        trip = result["trips"][0]
        assert abs(trip["centroid_lat"] - 25.2) < 0.01
        assert abs(trip["centroid_lon"] - 55.3) < 0.01
        assert isinstance(trip["radius_km"], float)


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


class TestExecute:
    """Tests for GpsSorter.execute."""

    def test_moves_files(self, sorter: GpsSorter, photo_dir: Path) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        plan = result["plan"]
        moved = sorter.execute(plan)
        assert len(moved) == 3
        for entry in plan:
            assert not Path(entry["source"]).exists()
            assert Path(entry["destination"]).exists()

    def test_creates_target_dirs(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            plan = sorter.preview(photo_dir)["plan"]
        sorter.execute(plan)
        for entry in plan:
            assert Path(entry["destination"]).parent.is_dir()

    def test_progress_callback(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            plan = sorter.preview(photo_dir)["plan"]
        calls: list[tuple[int, int]] = []
        sorter.execute(plan, progress_callback=lambda m, t: calls.append((m, t)))
        assert len(calls) == len(plan)
        assert calls[-1] == (len(plan), len(plan))

    def test_skips_missing_source(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            plan = sorter.preview(photo_dir)["plan"]
        Path(plan[0]["source"]).unlink()
        moved = sorter.execute(plan)
        assert len(moved) == len(plan) - 1

    def test_empty_plan(self, sorter: GpsSorter) -> None:
        assert sorter.execute([]) == []

    def test_returns_correct_entries(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            plan = sorter.preview(photo_dir)["plan"]
        moved = sorter.execute(plan)
        for entry in moved:
            assert "file" in entry
            assert "folder" in entry

    def test_trip_names_override(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """User-provided trip names override suggested folder names."""
        root = tmp_path / "rename"
        root.mkdir()
        (root / "a.jpg").write_text("a")
        (root / "b.jpg").write_text("b")

        with (
            patch.object(
                GpsSorter, "_gps_coordinates", return_value=(25.0, 55.0),
            ),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=["AE/Dubai", "AE/Dubai"],
            ),
        ):
            result = sorter.preview(root)

        trip_id = result["trips"][0]["id"]
        moved = sorter.execute(
            result["plan"],
            trip_names={str(trip_id): "Dubai Holiday 2025"},
        )
        assert len(moved) == 2
        for entry in moved:
            assert entry["folder"] == "Dubai Holiday 2025"
            assert Path(entry["destination"]).exists()
            assert "Dubai Holiday 2025" in entry["destination"]

    def test_trip_names_partial_override(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """Only some trips renamed; others use suggested name."""
        root = tmp_path / "partial"
        root.mkdir()
        files = ["a.jpg", "b.jpg", "c.jpg"]
        for f in files:
            (root / f).write_text(f)

        sorter._save_alias("Home", 48.135, 11.582, radius_km=50.0)

        counter = [0]

        def fake_ts(path: Path) -> float:
            counter[0] += 1
            return float(counter[0] * 1000)

        def fake_gps(path: Path) -> tuple[float, float] | None:
            if path.name == "a.jpg":
                return (25.2, 55.3)
            if path.name == "b.jpg":
                return (48.136, 11.583)  # Home
            if path.name == "c.jpg":
                return (52.52, 13.405)
            return None

        with (
            patch.object(GpsSorter, "_gps_coordinates", side_effect=fake_gps),
            patch.object(GpsSorter, "_file_timestamp", side_effect=fake_ts),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=["AE/Dubai", "DE/Berlin"],
            ),
        ):
            result = sorter.preview(root)

        assert len(result["trips"]) == 2
        # Only rename trip 1, leave trip 2 as suggested
        trip1_id = result["trips"][0]["id"]
        moved = sorter.execute(
            result["plan"],
            trip_names={str(trip1_id): "My Dubai Trip"},
        )

        folders = {e["file"]: e["folder"] for e in moved}
        assert folders["a.jpg"] == "My Dubai Trip"
        assert folders["b.jpg"] == "Home"
        assert folders["c.jpg"] == "DE/Berlin"  # original suggested name

    def test_execute_saves_aliases(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """Execute with trip_names persists aliases in DB."""
        root = tmp_path / "persist"
        root.mkdir()
        (root / "a.jpg").write_text("a")
        (root / "b.jpg").write_text("b")

        with (
            patch.object(
                GpsSorter, "_gps_coordinates", return_value=(25.2, 55.3),
            ),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=["AE/Dubai", "AE/Dubai"],
            ),
        ):
            result = sorter.preview(root)

        tid = result["trips"][0]["id"]
        sorter.execute(
            result["plan"],
            trip_names={str(tid): "Dubai Holiday"},
        )

        aliases = sorter.get_aliases()
        assert len(aliases) == 1
        assert aliases[0]["alias"] == "Dubai Holiday"
        assert abs(aliases[0]["lat"] - 25.2) < 0.01
        assert abs(aliases[0]["lon"] - 55.3) < 0.01
        assert aliases[0]["radius_km"] >= 1.0

    def test_saved_alias_auto_matches_next_preview(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """After saving an alias via execute, next preview auto-matches it."""
        root = tmp_path / "reuse"
        root.mkdir()
        (root / "a.jpg").write_text("a")

        # First run: save alias
        with (
            patch.object(
                GpsSorter, "_gps_coordinates", return_value=(25.2, 55.3),
            ),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=["AE/Dubai"],
            ),
        ):
            result = sorter.preview(root)

        tid = result["trips"][0]["id"]
        sorter.execute(
            result["plan"],
            trip_names={str(tid): "Dubai Holiday"},
        )

        # Restore file for second preview
        (root / "a.jpg").write_text("a")

        # Second run: should auto-match alias
        with (
            patch.object(
                GpsSorter, "_gps_coordinates", return_value=(25.2, 55.3),
            ),
            patch.object(GpsSorter, "_file_timestamp", return_value=2000.0),
        ):
            result2 = sorter.preview(root)

        assert len(result2["trips"]) == 0  # no trips — matched as alias
        assert result2["plan"][0]["group"] == "location"
        assert result2["plan"][0]["folder"] == "Dubai Holiday"

    def test_execute_no_trip_names_no_alias_saved(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        """Execute without trip_names does not save any aliases."""
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            plan = sorter.preview(photo_dir)["plan"]
        sorter.execute(plan)
        assert sorter.get_aliases() == []
