"""Tests for the GpsSorter class."""

from __future__ import annotations

import math
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from file_tools.tools.gps_sorter import AreaRow, GpsSorter, RegionRow


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
        dist = GpsSorter._haversine_km(48.1351, 11.5820, 52.5200, 13.4050)
        assert 490 < dist < 520

    def test_antipodal(self) -> None:
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
        from PIL import Image  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        jpg = tmp_path / "empty_exif.jpg"
        img.save(jpg)
        with patch("PIL.Image.Image.getexif", return_value={}):
            assert GpsSorter._gps_coordinates(jpg) is None

    def test_extracts_gps_from_exif(self, tmp_path: Path) -> None:
        from PIL import Image  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        exif = img.getexif()
        gps_ifd = {
            1: "N", 2: (48.0, 8.0, 30.0),
            3: "E", 4: (11.0, 34.0, 48.0),
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
        from PIL import Image  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        exif = img.getexif()
        exif[0x0132] = "2025:01:01 00:00:00"
        jpg = tmp_path / "no_gps_ifd.jpg"
        img.save(jpg, exif=exif.tobytes())
        assert GpsSorter._gps_coordinates(jpg) is None

    def test_gps_ifd_with_missing_fields(self, tmp_path: Path) -> None:
        from PIL import Image  # noqa: PLC0415

        img = Image.new("RGB", (1, 1))
        exif = img.getexif()
        gps_ifd = {1: "N", 2: (48.0, 8.0, 30.0)}
        exif.get_ifd(0x8825).update(gps_ifd)
        jpg = tmp_path / "partial_gps.jpg"
        img.save(jpg, exif=exif.tobytes())
        assert GpsSorter._gps_coordinates(jpg) is None


# ---------------------------------------------------------------------------
# _match_area
# ---------------------------------------------------------------------------


class TestMatchArea:
    """Tests for GpsSorter._match_area."""

    def test_within_radius(self) -> None:
        areas = [{"id": 1, "geocoded_name": "DE/Munich",
                  "lat": 48.135, "lon": 11.582, "radius_km": 5.0,
                  "region_id": None}]
        result = GpsSorter._match_area(48.136, 11.583, areas)
        assert result is not None
        assert result["id"] == 1

    def test_outside_radius(self) -> None:
        areas = [{"id": 1, "geocoded_name": "Home",
                  "lat": 48.135, "lon": 11.582, "radius_km": 0.001,
                  "region_id": None}]
        result = GpsSorter._match_area(52.52, 13.405, areas)
        assert result is None

    def test_empty_areas(self) -> None:
        assert GpsSorter._match_area(48.0, 11.0, []) is None

    def test_closest_wins(self) -> None:
        areas = [
            {"id": 1, "geocoded_name": "Far", "lat": 48.2, "lon": 11.7,
             "radius_km": 50.0, "region_id": None},
            {"id": 2, "geocoded_name": "Near", "lat": 48.135, "lon": 11.582,
             "radius_km": 50.0, "region_id": None},
        ]
        result = GpsSorter._match_area(48.136, 11.583, areas)
        assert result is not None
        assert result["id"] == 2


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
            assert "/" in folder

    def test_known_city(self) -> None:
        result = GpsSorter._reverse_geocode([(48.1351, 11.5820)])
        assert len(result) == 1
        assert "DE" in result[0] or "Munich" in result[0] or "München" in result[0]


# ---------------------------------------------------------------------------
# parse_google_maps_url
# ---------------------------------------------------------------------------


class TestParseGoogleMapsUrl:
    """Tests for GpsSorter.parse_google_maps_url."""

    def test_at_style(self) -> None:
        url = "https://www.google.com/maps/@48.1351,11.5820,15z"
        result = GpsSorter.parse_google_maps_url(url)
        assert result is not None
        assert abs(result[0] - 48.1351) < 1e-4
        assert abs(result[1] - 11.5820) < 1e-4

    def test_query_style(self) -> None:
        url = "https://www.google.com/maps?q=48.1351,11.5820"
        result = GpsSorter.parse_google_maps_url(url)
        assert result is not None

    def test_ll_style(self) -> None:
        url = "https://maps.google.com/?ll=48.1351,11.5820"
        result = GpsSorter.parse_google_maps_url(url)
        assert result is not None

    def test_place_style(self) -> None:
        url = "https://www.google.com/maps/place/Munich/@48.1351,11.5820,14z"
        result = GpsSorter.parse_google_maps_url(url)
        assert result is not None

    def test_plain_coords(self) -> None:
        result = GpsSorter.parse_google_maps_url("48.1351,11.5820")
        assert result is not None

    def test_negative_coords(self) -> None:
        result = GpsSorter.parse_google_maps_url("-33.8688,151.2093")
        assert result is not None
        assert abs(result[0] - (-33.8688)) < 1e-4

    def test_invalid_returns_none(self) -> None:
        assert GpsSorter.parse_google_maps_url("not a url") is None

    def test_empty_returns_none(self) -> None:
        assert GpsSorter.parse_google_maps_url("") is None


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
# Region CRUD
# ---------------------------------------------------------------------------


class TestRegionCrud:
    """Tests for region create / read / update / delete."""

    def test_get_regions_empty(self, sorter: GpsSorter) -> None:
        assert sorter.get_regions() == []

    def test_add_region(self, sorter: GpsSorter) -> None:
        r = sorter.add_region("Home")
        assert r["name"] == "Home"
        assert "id" in r
        assert r["areas"] == []

    def test_get_regions_returns_added(self, sorter: GpsSorter) -> None:
        sorter.add_region("Alpha")
        sorter.add_region("Beta")
        regs = sorter.get_regions()
        assert len(regs) == 2
        names = [r["name"] for r in regs]
        assert "Alpha" in names
        assert "Beta" in names

    def test_update_region(self, sorter: GpsSorter) -> None:
        r = sorter.add_region("Old")
        updated = sorter.update_region(r["id"], name="New")
        assert updated is not None
        assert updated["name"] == "New"

    def test_update_region_nonexistent(self, sorter: GpsSorter) -> None:
        assert sorter.update_region(9999, name="X") is None

    def test_delete_region(self, sorter: GpsSorter) -> None:
        r = sorter.add_region("TMP")
        sorter.delete_region(r["id"])
        assert sorter.get_regions() == []

    def test_delete_region_unassigns_areas(self, sorter: GpsSorter) -> None:
        r = sorter.add_region("Home")
        sorter.add_area("DE/Munich", 48.135, 11.582, region_id=r["id"])
        sorter.delete_region(r["id"])
        areas = sorter.get_areas()
        assert len(areas) == 1
        assert areas[0]["region_id"] is None

    def test_delete_nonexistent_is_noop(self, sorter: GpsSorter) -> None:
        sorter.delete_region(9999)  # should not raise

    def test_get_regions_includes_areas(self, sorter: GpsSorter) -> None:
        r = sorter.add_region("Home")
        sorter.add_area("DE/Munich", 48.135, 11.582, region_id=r["id"])
        sorter.add_area("DE/Augsburg", 48.37, 10.9, region_id=r["id"])
        regs = sorter.get_regions()
        assert len(regs) == 1
        assert len(regs[0]["areas"]) == 2


# ---------------------------------------------------------------------------
# Area CRUD
# ---------------------------------------------------------------------------


class TestAreaCrud:
    """Tests for area create / read / update / delete."""

    def test_get_areas_empty(self, sorter: GpsSorter) -> None:
        assert sorter.get_areas() == []

    def test_add_area(self, sorter: GpsSorter) -> None:
        a = sorter.add_area("DE/Munich", 48.135, 11.582)
        assert a["geocoded_name"] == "DE/Munich"
        assert a["lat"] == 48.135
        assert a["lon"] == 11.582
        assert a["radius_km"] == 5.0
        assert a["region_id"] is None
        assert "id" in a

    def test_add_area_with_region(self, sorter: GpsSorter) -> None:
        r = sorter.add_region("Home")
        a = sorter.add_area("DE/Munich", 48.135, 11.582, region_id=r["id"])
        assert a["region_id"] == r["id"]

    def test_add_area_custom_radius(self, sorter: GpsSorter) -> None:
        a = sorter.add_area("DE/Munich", 48.135, 11.582, radius_km=10.0)
        assert a["radius_km"] == 10.0

    def test_get_areas_returns_added(self, sorter: GpsSorter) -> None:
        sorter.add_area("A", 1.0, 2.0)
        sorter.add_area("B", 3.0, 4.0)
        areas = sorter.get_areas()
        assert len(areas) == 2

    def test_update_area_name(self, sorter: GpsSorter) -> None:
        a = sorter.add_area("Old", 48.0, 11.0)
        updated = sorter.update_area(a["id"], geocoded_name="New")
        assert updated is not None
        assert updated["geocoded_name"] == "New"
        assert updated["lat"] == 48.0  # unchanged

    def test_update_area_coords(self, sorter: GpsSorter) -> None:
        a = sorter.add_area("Place", 48.0, 11.0)
        updated = sorter.update_area(a["id"], lat=50.0, lon=12.0)
        assert updated is not None
        assert updated["lat"] == 50.0
        assert updated["lon"] == 12.0

    def test_update_area_radius(self, sorter: GpsSorter) -> None:
        a = sorter.add_area("Place", 48.0, 11.0, radius_km=5.0)
        updated = sorter.update_area(a["id"], radius_km=15.0)
        assert updated is not None
        assert updated["radius_km"] == 15.0

    def test_update_area_assign_region(self, sorter: GpsSorter) -> None:
        r = sorter.add_region("Home")
        a = sorter.add_area("DE/Munich", 48.135, 11.582)
        updated = sorter.update_area(a["id"], region_id=r["id"])
        assert updated is not None
        assert updated["region_id"] == r["id"]

    def test_update_area_unassign_region(self, sorter: GpsSorter) -> None:
        r = sorter.add_region("Home")
        a = sorter.add_area("DE/Munich", 48.135, 11.582, region_id=r["id"])
        updated = sorter.update_area(a["id"], region_id=None)
        assert updated is not None
        assert updated["region_id"] is None

    def test_update_area_nonexistent(self, sorter: GpsSorter) -> None:
        assert sorter.update_area(9999, geocoded_name="X") is None

    def test_delete_area(self, sorter: GpsSorter) -> None:
        a = sorter.add_area("TMP", 0.0, 0.0)
        sorter.delete_area(a["id"])
        assert sorter.get_areas() == []

    def test_delete_area_nonexistent_is_noop(self, sorter: GpsSorter) -> None:
        sorter.delete_area(9999)

    def test_area_to_dict(self) -> None:
        row = AreaRow(
            id=7, geocoded_name="DE/Munich",
            lat=48.135, lon=11.582, radius_km=3.0, region_id=None,
        )
        d = GpsSorter._area_to_dict(row)
        assert d == {
            "id": 7, "geocoded_name": "DE/Munich",
            "lat": 48.135, "lon": 11.582,
            "radius_km": 3.0, "region_id": None,
        }

    def test_area_to_dict_with_region(self) -> None:
        row = AreaRow(
            id=8, geocoded_name="DE/Munich",
            lat=48.135, lon=11.582, radius_km=3.0, region_id=2,
        )
        d = GpsSorter._area_to_dict(row)
        assert d["region_id"] == 2


# ---------------------------------------------------------------------------
# Preview
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

    def test_existing_area_match(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        """Photos near a saved area (unassigned) are classified as 'area'."""
        sorter.add_area("Home", 48.135, 11.582, radius_km=50.0)
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
            assert entry["group"] == "area"

    def test_area_with_region_match(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        """Photos near an area assigned to a region -> group='region'."""
        r = sorter.add_region("My Home")
        sorter.add_area("DE/Munich", 48.135, 11.582,
                        radius_km=50.0, region_id=r["id"])
        with (
            patch.object(
                GpsSorter, "_gps_coordinates", return_value=(48.136, 11.583),
            ),
            patch.object(GpsSorter, "_file_timestamp", return_value=2000.0),
        ):
            result = sorter.preview(photo_dir)
        for entry in result["plan"]:
            assert entry["folder"] == "My Home"
            assert entry["group"] == "region"

    def test_new_area_created_for_unmatched(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        """Files with GPS but no matching area -> new area auto-created."""
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
        assert len(result["new_areas"]) == 1
        new_area = result["new_areas"][0]
        assert new_area["geocoded_name"] == "AE/Dubai"
        for entry in result["plan"]:
            assert entry["group"] == "area"
            assert entry["area_id"] == new_area["id"]
            assert entry["folder"] == "AE/Dubai"

    def test_distance_splits_clusters(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """Photos far apart split into separate clusters/areas."""
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
                return (25.2, 55.3)
            if path.name in ("c.jpg", "d.jpg"):
                return (52.52, 13.405)
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

        assert len(result["new_areas"]) == 2
        area_ids = {e["file"]: e["area_id"] for e in result["plan"]}
        assert area_ids["a.jpg"] == area_ids["b.jpg"]
        assert area_ids["c.jpg"] == area_ids["d.jpg"]
        assert area_ids["a.jpg"] != area_ids["c.jpg"]

    def test_nearby_photos_same_cluster(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """Photos close together remain in the same area."""
        root = tmp_path / "close"
        root.mkdir()
        for f in ("a.jpg", "b.jpg", "c.jpg"):
            (root / f).write_text(f)

        ts_counter = [0]

        def fake_ts(path: Path) -> float:
            ts_counter[0] += 1
            return float(ts_counter[0] * 1000)

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

        assert len(result["new_areas"]) == 1
        for entry in result["plan"]:
            assert entry["area_id"] == result["new_areas"][0]["id"]

    def test_area_breaks_cluster_sequence(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """An area-matched photo breaks the cluster sequence."""
        root = tmp_path / "mixed"
        root.mkdir()
        files = ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"]
        for f in files:
            (root / f).write_text(f)

        sorter.add_area("Home", 48.135, 11.582, radius_km=50.0)

        ts_counter = [0]

        def fake_ts(path: Path) -> float:
            ts_counter[0] += 1
            return float(ts_counter[0] * 1000)

        def fake_gps(path: Path) -> tuple[float, float] | None:
            if path.name in ("a.jpg", "b.jpg"):
                return (25.2, 55.3)
            if path.name == "c.jpg":
                return (48.136, 11.583)
            if path.name in ("d.jpg", "e.jpg"):
                return (52.52, 13.405)
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

        assert len(result["new_areas"]) == 2
        groups = {e["file"]: e["group"] for e in result["plan"]}
        assert groups["c.jpg"] == "area"  # matched existing area
        assert groups["a.jpg"] == "area"  # new area (Dubai)
        assert groups["d.jpg"] == "area"  # new area (Berlin)

    def test_empty_directory(self, sorter: GpsSorter, empty_dir: Path) -> None:
        result = sorter.preview(empty_dir)
        assert result["plan"] == []
        assert result["new_areas"] == []
        assert result["total"] == 0

    def test_recursive_scanning(
        self, sorter: GpsSorter, dir_with_subdirs: Path,
    ) -> None:
        """With recursive=True, files in subdirs are included."""
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(dir_with_subdirs, recursive=True)
        files = {e["file"] for e in result["plan"]}
        assert "file.txt" in files
        assert "nested.txt" in files

    def test_non_recursive_scanning(
        self, sorter: GpsSorter, dir_with_subdirs: Path,
    ) -> None:
        """With recursive=False, only direct children are included."""
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(dir_with_subdirs, recursive=False)
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
        assert "new_areas" in result
        assert "total" in result
        assert "no_gps_count" in result
        for entry in result["plan"]:
            for key in ("file", "source", "folder", "destination",
                        "lat", "lon", "location_name", "group", "area_id"):
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
        assert 50 in calls
        assert calls[-1] == 60

    def test_mixed_gps_and_no_gps(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
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
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        for entry in result["plan"]:
            assert "timestamp" not in entry

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

    def test_new_area_has_metadata(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        """New areas include file_count, start_date, end_date."""
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
        assert len(result["new_areas"]) == 1
        na = result["new_areas"][0]
        assert na["file_count"] == 3
        assert "start_date" in na
        assert "end_date" in na

    def test_existing_area_not_duplicated(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        """If an area already exists near a new cluster, it is reused."""
        sorter.add_area("AE/Dubai", 25.2, 55.3, radius_km=10.0)
        with (
            patch.object(
                GpsSorter, "_gps_coordinates", return_value=(25.21, 55.31),
            ),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        assert len(result["new_areas"]) == 0
        assert len(sorter.get_areas()) == 1


# ---------------------------------------------------------------------------
# Reclassify
# ---------------------------------------------------------------------------


class TestReclassify:
    """Tests for GpsSorter.reclassify."""

    def test_reclassify_matches_new_area(self, sorter: GpsSorter) -> None:
        """Files get reassigned when an area is added."""
        plan = [
            {"file": "a.jpg", "source": "/tmp/a.jpg", "lat": 48.13, "lon": 11.58},
            {"file": "b.jpg", "source": "/tmp/b.jpg", "lat": None, "lon": None},
        ]
        # No areas yet -> creates new area
        with patch.object(GpsSorter, "_reverse_geocode", return_value=["DE/Munich"]):
            result = sorter.reclassify(plan)
        assert result["plan"][0]["group"] == "area"
        assert result["plan"][1]["group"] == "no_gps"

        # Add area for the coords -> still matches
        sorter.add_area("Home", 48.13, 11.58, radius_km=5.0)
        result2 = sorter.reclassify(plan)
        assert result2["plan"][0]["group"] == "area"
        assert result2["plan"][0]["folder"] == "Home"
        assert result2["plan"][1]["group"] == "no_gps"

    def test_reclassify_with_region(self, sorter: GpsSorter) -> None:
        """After assigning area to region, reclassify uses region name."""
        r = sorter.add_region("My Home")
        sorter.add_area("DE/Munich", 48.13, 11.58,
                        radius_km=5.0, region_id=r["id"])
        plan = [
            {"file": "a.jpg", "source": "/tmp/a.jpg", "lat": 48.13, "lon": 11.58},
        ]
        result = sorter.reclassify(plan)
        assert result["plan"][0]["group"] == "region"
        assert result["plan"][0]["folder"] == "My Home"

    def test_reclassify_preserves_source(self, sorter: GpsSorter) -> None:
        plan = [{"file": "x.jpg", "source": "/photos/x.jpg", "lat": 25.0, "lon": 55.0}]
        with patch.object(GpsSorter, "_reverse_geocode", return_value=["AE/Dubai"]):
            result = sorter.reclassify(plan)
        assert result["plan"][0]["source"] == "/photos/x.jpg"
        assert result["plan"][0]["file"] == "x.jpg"

    def test_reclassify_sets_destinations(self, sorter: GpsSorter) -> None:
        plan = [{"file": "a.jpg", "source": "/photos/a.jpg", "lat": 48.0, "lon": 11.0}]
        sorter.add_area("Munich", 48.0, 11.0, radius_km=10.0)
        result = sorter.reclassify(plan)
        assert result["plan"][0]["destination"].endswith("Munich/a.jpg")

    def test_reclassify_empty_plan(self, sorter: GpsSorter) -> None:
        result = sorter.reclassify([])
        assert result["plan"] == []
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# Execute
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

    def test_no_gps_name_override(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        plan = result["plan"]
        moved = sorter.execute(plan, no_gps_name="Unsorted")
        assert len(moved) == 3
        for entry in moved:
            assert entry["folder"] == "Unsorted"
            assert Path(entry["destination"]).exists()
            assert "Unsorted" in entry["destination"]

    def test_no_gps_name_empty_uses_default(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        plan = result["plan"]
        moved = sorter.execute(plan, no_gps_name="")
        assert len(moved) == 3
        for entry in moved:
            assert entry["folder"] == "No GPS"

    def test_no_gps_name_none_uses_default(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        plan = result["plan"]
        moved = sorter.execute(plan, no_gps_name=None)
        assert len(moved) == 3
        for entry in moved:
            assert entry["folder"] == "No GPS"

    def test_no_gps_name_sanitised(
        self, sorter: GpsSorter, photo_dir: Path,
    ) -> None:
        with (
            patch.object(GpsSorter, "_gps_coordinates", return_value=None),
            patch.object(GpsSorter, "_file_timestamp", return_value=1000.0),
        ):
            result = sorter.preview(photo_dir)
        plan = result["plan"]
        moved = sorter.execute(plan, no_gps_name='Bad<>Name:"test"')
        assert len(moved) == 3
        for entry in moved:
            assert "<" not in entry["folder"]
            assert ">" not in entry["folder"]

    def test_no_gps_name_with_regions(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """no_gps_name only affects no_gps entries, not area/region entries."""
        root = tmp_path / "mixed"
        root.mkdir()
        for f in ("a.jpg", "b.jpg", "c.jpg"):
            (root / f).write_text(f)

        counter = [0]

        def fake_ts(path: Path) -> float:
            counter[0] += 1
            return float(counter[0] * 1000)

        def fake_gps(path: Path) -> tuple[float, float] | None:
            if path.name in ("a.jpg", "b.jpg"):
                return (25.2, 55.3)
            return None

        with (
            patch.object(GpsSorter, "_gps_coordinates", side_effect=fake_gps),
            patch.object(GpsSorter, "_file_timestamp", side_effect=fake_ts),
            patch.object(
                GpsSorter,
                "_reverse_geocode",
                return_value=["AE/Dubai", "AE/Dubai"],
            ),
        ):
            result = sorter.preview(root)

        moved = sorter.execute(
            result["plan"],
            no_gps_name="Unknown Location",
        )

        folders = {e["file"]: e["folder"] for e in moved}
        assert folders["a.jpg"] == "AE/Dubai"
        assert folders["b.jpg"] == "AE/Dubai"
        assert folders["c.jpg"] == "Unknown Location"

    def test_areas_persist_after_execute(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """Areas created during preview persist after execute."""
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

        sorter.execute(result["plan"])
        areas = sorter.get_areas()
        assert len(areas) == 1
        assert areas[0]["geocoded_name"] == "AE/Dubai"

    def test_saved_area_auto_matches_next_preview(
        self, sorter: GpsSorter, tmp_path: Path,
    ) -> None:
        """After auto-creating an area, next preview auto-matches it."""
        root = tmp_path / "reuse"
        root.mkdir()
        (root / "a.jpg").write_text("a")

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

        sorter.execute(result["plan"])

        # Restore file for second preview
        (root / "a.jpg").write_text("a")

        with (
            patch.object(
                GpsSorter, "_gps_coordinates", return_value=(25.2, 55.3),
            ),
            patch.object(GpsSorter, "_file_timestamp", return_value=2000.0),
        ):
            result2 = sorter.preview(root)

        assert len(result2["new_areas"]) == 0
        assert result2["plan"][0]["group"] == "area"
        assert result2["plan"][0]["folder"] == "AE/Dubai"


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    """Tests for _migrate_legacy_aliases."""

    def test_migrates_old_aliases(self, tmp_path: Path) -> None:
        """Old gps_region_aliases data is migrated to regions + areas."""
        from sqlalchemy import create_engine, text

        db_path = tmp_path / "migrate.db"
        url = f"sqlite:///{db_path}"
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text(
                "CREATE TABLE gps_region_aliases ("
                "  id INTEGER PRIMARY KEY,"
                "  alias TEXT NOT NULL,"
                "  original_name TEXT,"
                "  lat REAL NOT NULL,"
                "  lon REAL NOT NULL,"
                "  radius_km REAL NOT NULL DEFAULT 5.0"
                ")"
            ))
            conn.execute(text(
                "INSERT INTO gps_region_aliases "
                "(alias, original_name, lat, lon, radius_km) "
                "VALUES ('Home', 'DE/Munich', 48.135, 11.582, 3.0)"
            ))
            conn.commit()
        eng.dispose()

        sorter = GpsSorter(db_url=url)
        regions = sorter.get_regions()
        assert len(regions) == 1
        assert regions[0]["name"] == "Home"
        assert len(regions[0]["areas"]) == 1
        assert regions[0]["areas"][0]["geocoded_name"] == "DE/Munich"
        assert regions[0]["areas"][0]["lat"] == 48.135

    def test_no_migration_when_areas_exist(self, tmp_path: Path) -> None:
        """Migration is skipped if areas table already has data."""
        from sqlalchemy import create_engine, text

        db_path = tmp_path / "skip.db"
        url = f"sqlite:///{db_path}"

        # First create with no old data to set up tables
        s1 = GpsSorter(db_url=url)
        s1.add_area("Existing", 1.0, 2.0)

        # Now add old table
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS gps_region_aliases ("
                "  id INTEGER PRIMARY KEY,"
                "  alias TEXT NOT NULL,"
                "  original_name TEXT,"
                "  lat REAL NOT NULL,"
                "  lon REAL NOT NULL,"
                "  radius_km REAL NOT NULL DEFAULT 5.0"
                ")"
            ))
            conn.execute(text(
                "INSERT INTO gps_region_aliases "
                "(alias, original_name, lat, lon, radius_km) "
                "VALUES ('Old', 'XX/Old', 10.0, 20.0, 5.0)"
            ))
            conn.commit()
        eng.dispose()

        # Re-create sorter — migration should be skipped
        s2 = GpsSorter(db_url=url)
        regions = s2.get_regions()
        assert len(regions) == 0  # Old data NOT migrated
        areas = s2.get_areas()
        assert len(areas) == 1
        assert areas[0]["geocoded_name"] == "Existing"
