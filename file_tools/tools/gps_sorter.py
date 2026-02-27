"""Sort files into location-based subdirectories using GPS EXIF data.

The data model uses two concepts:

* **Area** – a geographic location discovered from file coordinates.
  Each area has a geocoded name (e.g. ``DE/Munich``), centroid
  coordinates and a matching radius.  Areas are auto-created during
  :meth:`GpsSorter.preview` and persist across sessions.

* **Region** – a user-defined grouping of one or more areas.  Files
  that fall into an area assigned to a region are sorted into the
  region's folder.  Unassigned areas use their own geocoded name as
  folder.  A region may be empty (areas can be assigned later).

Workflow: call :meth:`preview` (scans files, creates new areas),
let the user organise areas into regions, then call :meth:`execute`.
"""

from __future__ import annotations

import math
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from platformdirs import user_data_dir
from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    inspect as sa_inspect,
)
from sqlalchemy.orm import declarative_base, sessionmaker

_Base = declarative_base()


# ------------------------------------------------------------------
# Database models
# ------------------------------------------------------------------


class RegionRow(_Base):  # type: ignore[misc]
    """User-defined region (Home, Vacation-2025, …)."""

    __tablename__ = "gps_regions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)


class AreaRow(_Base):  # type: ignore[misc]
    """Geographic area discovered from file GPS coordinates."""

    __tablename__ = "gps_areas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    geocoded_name = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    radius_km = Column(Float, nullable=False, default=5.0)
    region_id = Column(
        Integer,
        ForeignKey("gps_regions.id", ondelete="SET NULL"),
        nullable=True,
    )


# ------------------------------------------------------------------
# Sorter
# ------------------------------------------------------------------


class GpsSorter:
    """Sort files by GPS location with area/region management.

    Areas are auto-discovered from file GPS data and persisted in a
    SQLite database.  Users organise areas into regions.  Files in an
    area that belongs to a region are sorted into the region's folder;
    otherwise the area's geocoded name is used as folder.
    """

    # Class-level constants
    TRIP_SPLIT_KM: float = 50.0
    ALIAS_BUFFER_KM: float = 5.0

    # EXIF tag for the GPS info sub-IFD
    _EXIF_GPS_INFO = 0x8825  # 34853

    # GPS sub-IFD tag IDs
    _GPS_LATITUDE_REF = 1
    _GPS_LATITUDE = 2
    _GPS_LONGITUDE_REF = 3
    _GPS_LONGITUDE = 4

    def __init__(self, db_url: str | None = None) -> None:
        if db_url is None:
            db_dir = Path(user_data_dir("FileTools", appauthor=False))
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = db_dir / "filetools_gps.db"
            db_url = f"sqlite:///{db_path}"
        self._engine = create_engine(
            db_url, connect_args={"timeout": 30}, pool_pre_ping=True,
        )
        _Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)
        self._migrate_legacy_aliases()

    # ------------------------------------------------------------------
    # Legacy migration
    # ------------------------------------------------------------------

    def _migrate_legacy_aliases(self) -> None:
        """Migrate old ``gps_region_aliases`` data to new tables."""
        inspector = sa_inspect(self._engine)
        if "gps_region_aliases" not in inspector.get_table_names():
            return
        with self._session_factory() as session:
            # Only migrate if areas table is empty
            if session.query(AreaRow).count() > 0:
                return
            from sqlalchemy import text  # noqa: PLC0415

            rows = session.execute(
                text(
                    "SELECT alias, original_name, lat, lon, radius_km "
                    "FROM gps_region_aliases"
                ),
            ).fetchall()
            for alias, original_name, lat, lon, radius_km in rows:
                region = RegionRow(name=alias)
                session.add(region)
                session.flush()
                area = AreaRow(
                    geocoded_name=original_name or alias,
                    lat=lat,
                    lon=lon,
                    radius_km=radius_km,
                    region_id=region.id,
                )
                session.add(area)
            session.commit()

    # ------------------------------------------------------------------
    # Region CRUD
    # ------------------------------------------------------------------

    def get_regions(self) -> list[dict]:
        """Return all regions with their assigned areas."""
        with self._session_factory() as session:
            regions = (
                session.query(RegionRow)
                .order_by(RegionRow.name)
                .all()
            )
            result = []
            for r in regions:
                areas = (
                    session.query(AreaRow)
                    .filter(AreaRow.region_id == r.id)
                    .order_by(AreaRow.geocoded_name)
                    .all()
                )
                result.append({
                    "id": r.id,
                    "name": r.name,
                    "areas": [self._area_to_dict(a) for a in areas],
                })
            return result

    def add_region(self, name: str) -> dict:
        """Create a new region and return it as a dict."""
        with self._session_factory() as session:
            row = RegionRow(name=name)
            session.add(row)
            session.commit()
            session.refresh(row)
            return {"id": row.id, "name": row.name, "areas": []}

    def update_region(
        self,
        region_id: int,
        *,
        name: str | None = None,
    ) -> dict | None:
        """Update a region.  Returns updated dict or None."""
        with self._session_factory() as session:
            row = session.get(RegionRow, region_id)
            if not row:
                return None
            if name is not None:
                row.name = name  # type: ignore[assignment]
            session.commit()
            session.refresh(row)
            areas = (
                session.query(AreaRow)
                .filter(AreaRow.region_id == row.id)
                .order_by(AreaRow.geocoded_name)
                .all()
            )
            return {
                "id": row.id,
                "name": row.name,
                "areas": [self._area_to_dict(a) for a in areas],
            }

    def delete_region(self, region_id: int) -> None:
        """Delete a region.  Its areas become unassigned."""
        with self._session_factory() as session:
            # Unassign areas first
            session.query(AreaRow).filter(
                AreaRow.region_id == region_id,
            ).update({"region_id": None})
            row = session.get(RegionRow, region_id)
            if row:
                session.delete(row)
            session.commit()

    # ------------------------------------------------------------------
    # Area CRUD
    # ------------------------------------------------------------------

    def get_areas(self) -> list[dict]:
        """Return all areas ordered by geocoded name."""
        with self._session_factory() as session:
            rows = (
                session.query(AreaRow)
                .order_by(AreaRow.geocoded_name)
                .all()
            )
            return [self._area_to_dict(r) for r in rows]

    def add_area(
        self,
        geocoded_name: str,
        lat: float,
        lon: float,
        radius_km: float = 5.0,
        region_id: int | None = None,
    ) -> dict:
        """Create a new area and return it as a dict."""
        with self._session_factory() as session:
            row = AreaRow(
                geocoded_name=geocoded_name,
                lat=lat,
                lon=lon,
                radius_km=radius_km,
                region_id=region_id,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._area_to_dict(row)

    def update_area(
        self,
        area_id: int,
        *,
        geocoded_name: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        radius_km: float | None = None,
        region_id: int | None = ...,  # type: ignore[assignment]
    ) -> dict | None:
        """Update fields of an existing area.  Returns updated dict or None.

        *region_id* uses a sentinel default (``...``) so that callers
        can explicitly set it to ``None`` (unassign).
        """
        with self._session_factory() as session:
            row = session.get(AreaRow, area_id)
            if not row:
                return None
            if geocoded_name is not None:
                row.geocoded_name = geocoded_name  # type: ignore[assignment]
            if lat is not None:
                row.lat = lat  # type: ignore[assignment]
            if lon is not None:
                row.lon = lon  # type: ignore[assignment]
            if radius_km is not None:
                row.radius_km = radius_km  # type: ignore[assignment]
            if region_id is not ...:
                row.region_id = region_id  # type: ignore[assignment]
            session.commit()
            session.refresh(row)
            return self._area_to_dict(row)

    def delete_area(self, area_id: int) -> None:
        """Delete an area by id (no-op if not found)."""
        with self._session_factory() as session:
            row = session.get(AreaRow, area_id)
            if row:
                session.delete(row)
                session.commit()

    @staticmethod
    def _area_to_dict(row: AreaRow) -> dict:
        return {
            "id": row.id,
            "geocoded_name": row.geocoded_name,
            "lat": row.lat,
            "lon": row.lon,
            "radius_km": row.radius_km,
            "region_id": row.region_id,
        }

    @staticmethod
    def parse_google_maps_url(url: str) -> tuple[float, float] | None:
        """Extract (lat, lon) from a Google Maps URL.

        Supports formats like:
        - ``https://maps.google.com/?q=48.135,11.582``
        - ``https://www.google.com/maps/@48.135,11.582,15z``
        - ``https://www.google.com/maps/place/.../@48.135,11.582,...``
        - ``https://goo.gl/maps/...`` with ``@lat,lon``
        - plain ``48.135,11.582`` coordinate pairs

        Returns ``(lat, lon)`` or ``None``.
        """
        import re  # noqa: PLC0415

        m = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if m:
            return (float(m.group(1)), float(m.group(2)))
        m = re.search(r'[?&]q(?:uery)?=(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if m:
            return (float(m.group(1)), float(m.group(2)))
        m = re.search(r'[?&]ll=(-?\d+\.\d+),(-?\d+\.\d+)', url)
        if m:
            return (float(m.group(1)), float(m.group(2)))
        m = re.fullmatch(r'\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*', url)
        if m:
            return (float(m.group(1)), float(m.group(2)))
        return None

    # ------------------------------------------------------------------
    # GPS extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _gps_coordinates(path: Path) -> tuple[float, float] | None:
        """Extract (latitude, longitude) from EXIF GPS data."""
        try:
            from PIL import Image  # noqa: PLC0415

            with Image.open(path) as img:
                exif = img.getexif()
                if not exif:
                    return None
                gps_ifd = exif.get_ifd(GpsSorter._EXIF_GPS_INFO)
                if not gps_ifd:
                    return None
                lat_ref = gps_ifd.get(GpsSorter._GPS_LATITUDE_REF)
                lat_vals = gps_ifd.get(GpsSorter._GPS_LATITUDE)
                lon_ref = gps_ifd.get(GpsSorter._GPS_LONGITUDE_REF)
                lon_vals = gps_ifd.get(GpsSorter._GPS_LONGITUDE)
                if not (lat_ref and lat_vals and lon_ref and lon_vals):
                    return None
                lat = GpsSorter._dms_to_decimal(lat_vals, lat_ref)
                lon = GpsSorter._dms_to_decimal(lon_vals, lon_ref)
                return (lat, lon)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _dms_to_decimal(
        dms: tuple[float, float, float],
        ref: str,
    ) -> float:
        """Convert (degrees, minutes, seconds) + reference to decimal."""
        degrees = float(dms[0])
        minutes = float(dms[1])
        seconds = float(dms[2])
        decimal = degrees + minutes / 60.0 + seconds / 3600.0
        if ref in ("S", "W"):
            decimal = -decimal
        return decimal

    # ------------------------------------------------------------------
    # Distance calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _haversine_km(
        lat1: float, lon1: float, lat2: float, lon2: float,
    ) -> float:
        """Return the great-circle distance in km between two points."""
        r = 6371.0
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(d_lon / 2) ** 2
        )
        return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    # ------------------------------------------------------------------
    # Area matching
    # ------------------------------------------------------------------

    @staticmethod
    def _match_area(
        lat: float,
        lon: float,
        areas: list[dict],
    ) -> dict | None:
        """Return the closest matching area dict within its radius."""
        best: dict | None = None
        best_dist = float("inf")
        for a in areas:
            dist = GpsSorter._haversine_km(lat, lon, a["lat"], a["lon"])
            if dist <= a["radius_km"] and dist < best_dist:
                best_dist = dist
                best = a
        return best

    @staticmethod
    def _reverse_geocode(coords: list[tuple[float, float]]) -> list[str]:
        """Batch reverse-geocode a list of (lat, lon) tuples."""
        if not coords:
            return []
        import reverse_geocoder as rg  # noqa: PLC0415

        results = rg.search(coords, verbose=False)
        folders: list[str] = []
        for r in results:
            city = r.get("name", "Unknown")
            country = r.get("cc", "XX")
            folders.append(f"{country}/{city}")
        return folders

    # ------------------------------------------------------------------
    # Folder name sanitisation
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitise_folder(name: str) -> str:
        """Remove or replace characters invalid in folder names."""
        for ch in '<>:"|?*':
            name = name.replace(ch, "_")
        while "__" in name:
            name = name.replace("__", "_")
        return name.strip(". ")

    # ------------------------------------------------------------------
    # Timestamp helper
    # ------------------------------------------------------------------

    @staticmethod
    def _file_timestamp(path: Path) -> float:
        """Return the best-guess creation timestamp for *path*."""
        from file_tools.tools.date_sorter import DateSorter  # noqa: PLC0415

        return DateSorter._creation_time(path)

    # ------------------------------------------------------------------
    # Internal: build plan from file_infos
    # ------------------------------------------------------------------

    def _build_plan(
        self,
        file_infos: list[dict],
        root: Path,
    ) -> dict:
        """Classify files, create areas for new clusters, return plan dict.

        Shared logic used by both :meth:`preview` and :meth:`reclassify`.
        """
        areas = self.get_areas()
        regions = self.get_regions()
        region_map: dict[int, str] = {r["id"]: r["name"] for r in regions}

        # Phase 1 – classify each file against existing areas ----------
        for info in file_infos:
            if info["lat"] is None:
                info["group"] = "no_gps"
                info["folder"] = "No GPS"
                info["location_name"] = "No GPS"
                info["area_id"] = None
                continue
            matched = self._match_area(info["lat"], info["lon"], areas)
            if matched:
                info["area_id"] = matched["id"]
                rid = matched.get("region_id")
                if rid and rid in region_map:
                    info["group"] = "region"
                    info["folder"] = self._sanitise_folder(region_map[rid])
                    info["location_name"] = region_map[rid]
                else:
                    info["group"] = "area"
                    info["folder"] = self._sanitise_folder(
                        matched["geocoded_name"],
                    )
                    info["location_name"] = matched["geocoded_name"]
            else:
                info["group"] = "new"
                info["area_id"] = None

        # Phase 2 – cluster unmatched ("new") files by proximity -------
        current_cluster = 0
        in_cluster = False
        cluster_lats: list[float] = []
        cluster_lons: list[float] = []
        for info in file_infos:
            if info["group"] == "new":
                if not in_cluster:
                    current_cluster += 1
                    in_cluster = True
                    cluster_lats = [info["lat"]]
                    cluster_lons = [info["lon"]]
                else:
                    c_lat = sum(cluster_lats) / len(cluster_lats)
                    c_lon = sum(cluster_lons) / len(cluster_lons)
                    dist = self._haversine_km(
                        c_lat, c_lon, info["lat"], info["lon"],
                    )
                    if dist > self.TRIP_SPLIT_KM:
                        current_cluster += 1
                        cluster_lats = [info["lat"]]
                        cluster_lons = [info["lon"]]
                    else:
                        cluster_lats.append(info["lat"])
                        cluster_lons.append(info["lon"])
                info["_cluster_id"] = current_cluster
            else:
                in_cluster = False
                cluster_lats = []
                cluster_lons = []

        # Phase 3 – reverse-geocode new clusters -----------------------
        cluster_coords: list[tuple[float, float]] = []
        cluster_ids: list[int] = []
        for info in file_infos:
            if info["group"] == "new" and info["lat"] is not None:
                cluster_coords.append((info["lat"], info["lon"]))
                cluster_ids.append(info["_cluster_id"])

        geocoded_by_cluster: dict[int, list[str]] = defaultdict(list)
        if cluster_coords:
            geocoded = self._reverse_geocode(cluster_coords)
            for cid, gname in zip(cluster_ids, geocoded):
                geocoded_by_cluster[cid].append(gname)

        # Phase 4 – create areas for new clusters & build metadata -----
        new_areas: list[dict] = []
        cluster_id_set = sorted(
            {i["_cluster_id"] for i in file_infos if i.get("_cluster_id")},
        )
        cluster_area_map: dict[int, dict] = {}

        for cid in cluster_id_set:
            cfiles = [i for i in file_infos if i.get("_cluster_id") == cid]
            names = geocoded_by_cluster.get(cid, [])
            suggested = (
                Counter(names).most_common(1)[0][0]
                if names
                else f"Area {cid}"
            )
            lats = [f["lat"] for f in cfiles if f["lat"] is not None]
            lons = [f["lon"] for f in cfiles if f["lon"] is not None]
            centroid_lat = sum(lats) / len(lats) if lats else 0.0
            centroid_lon = sum(lons) / len(lons) if lons else 0.0
            radius = max(
                (self._haversine_km(centroid_lat, centroid_lon, la, lo)
                 for la, lo in zip(lats, lons)),
                default=1.0,
            ) + self.ALIAS_BUFFER_KM
            radius = max(radius, 1.0)

            # Check if an area already exists nearby (avoid duplicates)
            existing = self._match_area(centroid_lat, centroid_lon, areas)
            if existing:
                area_dict = existing
            else:
                area_dict = self.add_area(
                    suggested, centroid_lat, centroid_lon, radius,
                )
                areas.append(area_dict)
                new_areas.append(area_dict)

            cluster_area_map[cid] = area_dict

            # Attach extra metadata for the response
            timestamps = [f.get("timestamp", 0) for f in cfiles]
            area_dict["_file_count"] = len(cfiles)
            area_dict["_start_date"] = (
                datetime.fromtimestamp(min(timestamps)).strftime("%Y-%m-%d")
                if timestamps and any(t > 0 for t in timestamps) else ""
            )
            area_dict["_end_date"] = (
                datetime.fromtimestamp(max(timestamps)).strftime("%Y-%m-%d")
                if timestamps and any(t > 0 for t in timestamps) else ""
            )

        # Phase 5 – assign new-cluster files to their areas ------------
        for info in file_infos:
            if info["group"] == "new":
                area = cluster_area_map.get(info.get("_cluster_id", -1))
                if area:
                    info["area_id"] = area["id"]
                    info["group"] = "area"
                    info["folder"] = self._sanitise_folder(
                        area["geocoded_name"],
                    )
                    info["location_name"] = area["geocoded_name"]

        # Phase 6 – destinations ---------------------------------------
        for info in file_infos:
            info["destination"] = str(root / info["folder"] / info["file"])

        # Clean up internal keys
        for info in file_infos:
            info.pop("_cluster_id", None)
            info.pop("timestamp", None)

        no_gps = sum(1 for e in file_infos if e["group"] == "no_gps")

        # Build new_areas response with metadata
        new_areas_response = []
        for a in new_areas:
            entry = {k: v for k, v in a.items() if not k.startswith("_")}
            entry["file_count"] = a.get("_file_count", 0)
            entry["start_date"] = a.get("_start_date", "")
            entry["end_date"] = a.get("_end_date", "")
            new_areas_response.append(entry)

        return {
            "plan": file_infos,
            "new_areas": new_areas_response,
            "total": len(file_infos),
            "no_gps_count": no_gps,
        }

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def preview(
        self,
        directory: str | Path,
        *,
        recursive: bool = True,
        progress_callback: Callable[[int], None] | None = None,
    ) -> dict:
        """Build a move-plan with automatic area discovery.

        Parameters
        ----------
        directory:
            Directory whose files to sort.
        recursive:
            If ``True``, scan subdirectories recursively.
        progress_callback:
            Called with the number of files scanned so far.

        Returns a dict with keys ``plan``, ``new_areas``, ``total``,
        ``no_gps_count``.
        """
        root = Path(directory).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Not a directory: {root}")

        # Scan files
        file_infos: list[dict] = []
        count = 0
        entries = sorted(root.rglob("*")) if recursive else sorted(root.iterdir())
        for entry in entries:
            if not entry.is_file():
                continue
            gps = self._gps_coordinates(entry)
            ts = self._file_timestamp(entry)
            file_infos.append({
                "file": entry.name,
                "source": str(entry),
                "lat": gps[0] if gps else None,
                "lon": gps[1] if gps else None,
                "timestamp": ts,
                "group": "",
                "folder": "",
                "destination": "",
                "location_name": "",
                "area_id": None,
            })
            count += 1
            if progress_callback and count % 50 == 0:
                progress_callback(count)

        if progress_callback:
            progress_callback(count)

        # Sort by timestamp
        file_infos.sort(key=lambda x: x["timestamp"])

        return self._build_plan(file_infos, root)

    # ------------------------------------------------------------------
    # Reclassify
    # ------------------------------------------------------------------

    def reclassify(self, plan: list[dict]) -> dict:
        """Re-evaluate *plan* entries against current areas/regions.

        Skips the expensive file-scan / EXIF-read phase.
        """
        file_infos: list[dict] = []
        for entry in plan:
            file_infos.append({
                "file": entry["file"],
                "source": entry["source"],
                "lat": entry.get("lat"),
                "lon": entry.get("lon"),
                "timestamp": 0,
                "group": "",
                "folder": "",
                "destination": "",
                "location_name": "",
                "area_id": None,
            })

        root = Path(file_infos[0]["source"]).parent if file_infos else Path(".")
        return self._build_plan(file_infos, root)

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute(
        self,
        plan: list[dict],
        *,
        no_gps_name: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[dict]:
        """Move files according to the *plan* from :meth:`preview`.

        Parameters
        ----------
        plan:
            The list of dicts from :meth:`preview`.
        no_gps_name:
            Optional folder name for files without GPS data.
        progress_callback:
            Called with ``(moved_count, total_count)`` after each file.

        Returns the subset of *plan* entries that were actually moved.
        """
        moved: list[dict] = []
        total = len(plan)
        for i, entry in enumerate(plan, 1):
            src = Path(entry["source"])
            if not src.is_file():
                continue

            folder = entry["folder"]
            if entry.get("group") == "no_gps" and no_gps_name:
                folder = self._sanitise_folder(no_gps_name)

            dst = Path(entry["destination"])
            if entry.get("group") == "no_gps" and no_gps_name:
                dst = dst.parent.parent / folder / entry["file"]

            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

            result = dict(entry)
            result["folder"] = folder
            result["destination"] = str(dst)
            moved.append(result)

            if progress_callback:
                progress_callback(i, total)

        return moved
