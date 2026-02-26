"""Sort files into location-based subdirectories using GPS EXIF data.

Region aliases are persisted in a SQLite database and learned
automatically when the user names detected trips.  Photos near a saved
alias are sorted into that alias's folder.  Photos away from all
aliases are grouped into *trips* by geographic proximity and named via
offline reverse geocoding.  The user may rename trips before executing
the move; those names are then persisted as aliases for future use.
"""

from __future__ import annotations

import math
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from sqlalchemy import Column, Float, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

_Base = declarative_base()


# ------------------------------------------------------------------
# Database model
# ------------------------------------------------------------------


class RegionAliasRow(_Base):  # type: ignore[misc]
    """Persisted region alias (Home, Work, Dubai, …) with centroid and radius."""

    __tablename__ = "gps_region_aliases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alias = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    radius_km = Column(Float, nullable=False, default=5.0)


# ------------------------------------------------------------------
# Sorter
# ------------------------------------------------------------------


class GpsSorter:
    """Sort files by GPS location with trip detection and auto-learned aliases.

    Region aliases are persisted in a SQLite database.  They are
    learned automatically when the user names a detected trip during
    :meth:`execute`.  On subsequent previews, photos near a saved
    alias are sorted into that alias's folder directly.

    The workflow is: call :meth:`preview` to build a plan (including
    detected trips with suggested names), let the user rename trips,
    then call :meth:`execute` with optional ``trip_names`` overrides
    which also persists the aliases for future use.
    """

    # Class-level constants
    TRIP_SPLIT_KM: float = 50.0    # distance threshold for splitting trips
    ALIAS_BUFFER_KM: float = 5.0   # buffer added when saving alias radius

    # EXIF tag for the GPS info sub-IFD
    _EXIF_GPS_INFO = 0x8825  # 34853

    # GPS sub-IFD tag IDs
    _GPS_LATITUDE_REF = 1
    _GPS_LATITUDE = 2
    _GPS_LONGITUDE_REF = 3
    _GPS_LONGITUDE = 4

    def __init__(self, db_url: str | None = None) -> None:
        if db_url is None:
            db_path = Path(tempfile.gettempdir()) / "filetools_gps.db"
            db_url = f"sqlite:///{db_path}"
        self._engine = create_engine(
            db_url, connect_args={"timeout": 30}, pool_pre_ping=True,
        )
        _Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine)

    # ------------------------------------------------------------------
    # Alias persistence
    # ------------------------------------------------------------------

    def get_aliases(self) -> list[dict]:
        """Return all saved region aliases ordered by alias name."""
        with self._session_factory() as session:
            rows = (
                session.query(RegionAliasRow)
                .order_by(RegionAliasRow.alias)
                .all()
            )
            return [self._alias_to_dict(r) for r in rows]

    def delete_alias(self, alias_id: int) -> None:
        """Delete a region alias by id (no-op if not found)."""
        with self._session_factory() as session:
            row = session.get(RegionAliasRow, alias_id)
            if row:
                session.delete(row)
                session.commit()

    def _save_alias(
        self,
        alias: str,
        lat: float,
        lon: float,
        radius_km: float,
    ) -> None:
        """Persist a region alias.  Updates an existing nearby alias if found."""
        with self._session_factory() as session:
            rows = session.query(RegionAliasRow).all()
            for row in rows:
                dist = self._haversine_km(
                    lat, lon, float(row.lat), float(row.lon),
                )
                if dist <= max(float(row.radius_km), radius_km):
                    row.alias = alias  # type: ignore[assignment]
                    row.lat = lat  # type: ignore[assignment]
                    row.lon = lon  # type: ignore[assignment]
                    row.radius_km = radius_km  # type: ignore[assignment]
                    session.commit()
                    return
            new_row = RegionAliasRow(
                alias=alias, lat=lat, lon=lon, radius_km=radius_km,
            )
            session.add(new_row)
            session.commit()

    @staticmethod
    def _alias_to_dict(row: RegionAliasRow) -> dict:
        return {
            "id": row.id,
            "alias": row.alias,
            "lat": row.lat,
            "lon": row.lon,
            "radius_km": row.radius_km,
        }

    # ------------------------------------------------------------------
    # GPS extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _gps_coordinates(path: Path) -> tuple[float, float] | None:
        """Extract (latitude, longitude) from EXIF GPS data.

        Returns a ``(lat, lon)`` tuple or ``None`` when no usable GPS
        tags are found.
        """
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
        """Convert (degrees, minutes, seconds) + reference to decimal degrees."""
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
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
    ) -> float:
        """Return the great-circle distance in km between two points."""
        r = 6371.0  # Earth radius in km
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
    # Alias matching
    # ------------------------------------------------------------------

    @staticmethod
    def _match_alias(
        lat: float,
        lon: float,
        aliases: list[dict],
    ) -> str | None:
        """Return the alias of the closest matching region within its radius.

        Each entry in *aliases* must have keys:
        ``alias``, ``lat``, ``lon``, ``radius_km``.

        Returns ``None`` if no alias is close enough.
        """
        best_name: str | None = None
        best_dist = float("inf")
        for a in aliases:
            dist = GpsSorter._haversine_km(lat, lon, a["lat"], a["lon"])
            if dist <= a["radius_km"] and dist < best_dist:
                best_dist = dist
                best_name = a["alias"]
        return best_name

    @staticmethod
    def _reverse_geocode(coords: list[tuple[float, float]]) -> list[str]:
        """Batch reverse-geocode a list of (lat, lon) tuples.

        Returns a list of folder names like ``Country/City``.
        """
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
        """Remove or replace characters that are invalid in folder names."""
        for ch in '<>:"|?*':
            name = name.replace(ch, "_")
        # Collapse multiple underscores / strip trailing dots & spaces
        while "__" in name:
            name = name.replace("__", "_")
        return name.strip(". ")

    # ------------------------------------------------------------------
    # Timestamp helper
    # ------------------------------------------------------------------

    @staticmethod
    def _file_timestamp(path: Path) -> float:
        """Return the best-guess creation timestamp for *path*.

        Delegates to :meth:`DateSorter._creation_time` which prefers
        EXIF date, then ``st_birthtime``, then ``min(st_ctime, st_mtime)``.
        """
        from file_tools.tools.date_sorter import DateSorter  # noqa: PLC0415

        return DateSorter._creation_time(path)

    # ------------------------------------------------------------------
    # Preview (with trip detection)
    # ------------------------------------------------------------------

    def preview(
        self,
        directory: str | Path,
        *,
        progress_callback: Callable[[int], None] | None = None,
    ) -> dict:
        """Build a move-plan with automatic trip detection.

        Parameters
        ----------
        directory:
            Flat directory whose files to sort.
        progress_callback:
            Called with the number of files scanned so far.

        Returns a dict with keys:

        - ``plan`` – list of entry dicts (file, source, folder,
          destination, lat, lon, group, trip_id, location_name)
        - ``trips`` – list of detected trips (id, suggested_name,
          file_count, start_date, end_date)
        - ``total`` – total file count
        - ``no_gps_count`` – files without GPS data
        """
        root = Path(directory).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Not a directory: {root}")

        aliases = self.get_aliases()

        # Phase 1 – scan files: extract GPS + timestamp ----------------
        file_infos: list[dict] = []
        count = 0
        for entry in sorted(root.iterdir()):
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
                "trip_id": None,
            })
            count += 1
            if progress_callback and count % 50 == 0:
                progress_callback(count)

        if progress_callback:
            progress_callback(count)

        # Phase 2 – sort by timestamp ---------------------------------
        file_infos.sort(key=lambda x: x["timestamp"])

        # Phase 3 – classify each file --------------------------------
        for info in file_infos:
            if info["lat"] is None:
                info["group"] = "no_gps"
                info["folder"] = "No GPS"
                info["location_name"] = "No GPS"
            else:
                matched = self._match_alias(
                    info["lat"], info["lon"], aliases,
                )
                if matched:
                    info["group"] = "location"
                    info["folder"] = self._sanitise_folder(matched)
                    info["location_name"] = matched
                else:
                    info["group"] = "trip"

        # Phase 4 – detect trips (consecutive "trip" entries,
        #           split when distance from cluster centroid exceeds
        #           TRIP_SPLIT_KM) ------------------------------------
        current_trip_id = 0
        in_trip = False
        cluster_lats: list[float] = []
        cluster_lons: list[float] = []
        for info in file_infos:
            if info["group"] == "trip":
                if not in_trip:
                    current_trip_id += 1
                    in_trip = True
                    cluster_lats = [info["lat"]]
                    cluster_lons = [info["lon"]]
                else:
                    c_lat = sum(cluster_lats) / len(cluster_lats)
                    c_lon = sum(cluster_lons) / len(cluster_lons)
                    dist = self._haversine_km(
                        c_lat, c_lon, info["lat"], info["lon"],
                    )
                    if dist > self.TRIP_SPLIT_KM:
                        current_trip_id += 1
                        cluster_lats = [info["lat"]]
                        cluster_lons = [info["lon"]]
                    else:
                        cluster_lats.append(info["lat"])
                        cluster_lons.append(info["lon"])
                info["trip_id"] = current_trip_id
            else:
                in_trip = False
                cluster_lats = []
                cluster_lons = []

        # Phase 5 – batch reverse-geocode trip coordinates -------------
        trip_coords: list[tuple[float, float]] = []
        trip_coord_ids: list[int] = []
        for info in file_infos:
            if info["group"] == "trip" and info["lat"] is not None:
                trip_coords.append((info["lat"], info["lon"]))
                trip_coord_ids.append(info["trip_id"])

        geocoded_by_trip: dict[int, list[str]] = defaultdict(list)
        if trip_coords:
            geocoded = self._reverse_geocode(trip_coords)
            for tid, gname in zip(trip_coord_ids, geocoded):
                geocoded_by_trip[tid].append(gname)

        # Phase 6 – build trip metadata --------------------------------
        trips: list[dict] = []
        trip_ids = sorted(
            {i["trip_id"] for i in file_infos if i["trip_id"] is not None},
        )
        trip_name_map: dict[int, str] = {}
        for tid in trip_ids:
            trip_files = [i for i in file_infos if i["trip_id"] == tid]
            names = geocoded_by_trip.get(tid, [])
            suggested = (
                Counter(names).most_common(1)[0][0]
                if names
                else f"Trip {tid}"
            )
            trip_name_map[tid] = suggested
            lats = [f["lat"] for f in trip_files if f["lat"] is not None]
            lons = [f["lon"] for f in trip_files if f["lon"] is not None]
            centroid_lat = sum(lats) / len(lats) if lats else 0.0
            centroid_lon = sum(lons) / len(lons) if lons else 0.0
            radius = max(
                (self._haversine_km(centroid_lat, centroid_lon, la, lo)
                 for la, lo in zip(lats, lons)),
                default=1.0,
            )

            timestamps = [f["timestamp"] for f in trip_files]
            trips.append({
                "id": tid,
                "suggested_name": suggested,
                "file_count": len(trip_files),
                "start_date": datetime.fromtimestamp(
                    min(timestamps),
                ).strftime("%Y-%m-%d"),
                "end_date": datetime.fromtimestamp(
                    max(timestamps),
                ).strftime("%Y-%m-%d"),
                "centroid_lat": centroid_lat,
                "centroid_lon": centroid_lon,
                "radius_km": radius,
            })

        # Phase 7 – set folder names for trip files --------------------
        for info in file_infos:
            if info["group"] == "trip":
                sname = trip_name_map.get(info["trip_id"], "Unknown")
                info["folder"] = self._sanitise_folder(sname)
                info["location_name"] = sname

        # Phase 8 – set destinations -----------------------------------
        for info in file_infos:
            info["destination"] = str(root / info["folder"] / info["file"])

        # Remove raw timestamp (not needed by frontend / execute)
        for info in file_infos:
            del info["timestamp"]

        no_gps = sum(1 for e in file_infos if e["group"] == "no_gps")
        return {
            "plan": file_infos,
            "trips": trips,
            "total": len(file_infos),
            "no_gps_count": no_gps,
        }

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute(
        self,
        plan: list[dict],
        *,
        trip_names: dict[str, str] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[dict]:
        """Move files according to the *plan* from :meth:`preview`.

        Parameters
        ----------
        plan:
            The list of dicts from :meth:`preview`.
        trip_names:
            Optional mapping of trip-id (as string) → user-chosen folder
            name.  Overrides the suggested name for matching entries.
        progress_callback:
            Called with ``(moved_count, total_count)`` after each file.

        Returns the subset of *plan* entries that were actually moved.
        """
        trip_names = trip_names or {}
        moved: list[dict] = []
        total = len(plan)
        for i, entry in enumerate(plan, 1):
            src = Path(entry["source"])
            if not src.is_file():
                continue

            folder = entry["folder"]
            tid = entry.get("trip_id")
            if tid is not None and str(tid) in trip_names:
                folder = self._sanitise_folder(trip_names[str(tid)])

            dst = src.parent / folder / entry["file"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

            result = dict(entry)
            result["folder"] = folder
            result["destination"] = str(dst)
            moved.append(result)

            if progress_callback:
                progress_callback(i, total)

        # Save aliases for named trips
        if trip_names:
            trip_entries: dict[int, list[dict]] = defaultdict(list)
            for entry in plan:
                if entry.get("trip_id") is not None:
                    trip_entries[entry["trip_id"]].append(entry)

            for tid_str, alias_name in trip_names.items():
                tid = int(tid_str)
                entries = trip_entries.get(tid, [])
                lats = [e["lat"] for e in entries if e.get("lat") is not None]
                lons = [e["lon"] for e in entries if e.get("lon") is not None]
                if lats and lons:
                    centroid_lat = sum(lats) / len(lats)
                    centroid_lon = sum(lons) / len(lons)
                    radius = max(
                        (self._haversine_km(centroid_lat, centroid_lon, la, lo)
                         for la, lo in zip(lats, lons)),
                        default=1.0,
                    ) + self.ALIAS_BUFFER_KM
                    radius = max(radius, 1.0)
                    self._save_alias(
                        alias_name, centroid_lat, centroid_lon, radius,
                    )

        return moved
