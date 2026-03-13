"""Convert PDF files to DICOM Encapsulated PDF objects."""

from __future__ import annotations

import datetime
import json as _json
import os
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir
from pydicom import Dataset, uid
from pydicom.dataset import FileMetaDataset
from pydicom.uid import UID
from pydicom.encaps import encapsulate
from pydicom.sequence import Sequence
from sqlalchemy import Column, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

_Base = declarative_base()


# ------------------------------------------------------------------
# Database model
# ------------------------------------------------------------------


class DcmTagConfigRow(_Base):  # type: ignore[misc]
    """Named DICOM tag configuration."""

    __tablename__ = "dcm_tag_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    tags_json = Column(Text, nullable=False, default="{}")


class Pdf2Dcm:
    """Convert a PDF file into a DICOM Encapsulated PDF (SOP Class 1.2.840.10008.5.1.4.1.1.104.1).

    Usage
    -----
    >>> converter = Pdf2Dcm()
    >>> dcm_bytes = converter.convert(
    ...     pdf_path=Path("report.pdf"),
    ...     tags={"PatientName": "Doe^John", "PatientID": "12345"},
    ... )
    """

    # SOP Class UID for Encapsulated PDF Storage
    SOP_CLASS_UID = "1.2.840.10008.5.1.4.1.1.104.1"

    # Most useful DICOM tags for the dropdown – (keyword, label, default_value)
    COMMON_TAGS: list[tuple[str, str, str]] = [
        ("PatientName", "Patient Name", ""),
        ("PatientID", "Patient ID", ""),
        ("PatientBirthDate", "Patient Birth Date", ""),
        ("PatientSex", "Patient Sex", ""),
        ("StudyDescription", "Study Description", ""),
        ("SeriesDescription", "Series Description", "Report PDF"),
        ("SeriesNumber", "Series Number", "500"),
        ("StudyInstanceUID", "Study Instance UID", ""),
        ("InstitutionName", "Institution Name", ""),
        ("ReferringPhysicianName", "Referring Physician", ""),
        ("AccessionNumber", "Accession Number", ""),
        ("Modality", "Modality", "DOC"),
        ("ImageType", "Image Type", "DERIVED\\SECONDARY"),
        ("Manufacturer", "Manufacturer", ""),
        ("StationName", "Station Name", ""),
        ("StudyDate", "Study Date", ""),
        ("StudyTime", "Study Time", ""),
        ("ContentDate", "Content Date", ""),
        ("ContentTime", "Content Time", ""),
        ("BurnedInAnnotation", "Burned In Annotation", "YES"),
        ("DocumentTitle", "Document Title", "Radiology Report"),
        ("ConceptNameCodeSequence", "Concept Name", ""),
    ]

    # ── public API ────────────────────────────────────────────────────

    def __init__(self, db_url: str | None = None) -> None:
        if db_url is None:
            db_dir = Path(user_data_dir("FileTools", appauthor=False))
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = db_dir / "filetools_pdf2dcm.db"
            db_url = f"sqlite:///{db_path}"
        self._engine = create_engine(
            db_url, connect_args={"timeout": 30}, pool_pre_ping=True,
        )
        _Base.metadata.create_all(self._engine)
        self._Session = sessionmaker(bind=self._engine)

    # ── Tag configs ───────────────────────────────────────────────────

    def get_configs(self) -> list[dict[str, Any]]:
        """Return all saved tag configurations."""
        with self._Session() as s:
            rows = s.query(DcmTagConfigRow).order_by(DcmTagConfigRow.name).all()
            return [
                {"id": r.id, "name": r.name, "tags": _json.loads(r.tags_json)}
                for r in rows
            ]

    def save_config(self, name: str, tags: dict[str, str]) -> dict[str, Any]:
        """Save (or overwrite) a named tag configuration."""
        with self._Session() as s:
            row = s.query(DcmTagConfigRow).filter_by(name=name).first()
            if row:
                row.tags_json = _json.dumps(tags)
            else:
                row = DcmTagConfigRow(name=name, tags_json=_json.dumps(tags))
                s.add(row)
            s.commit()
            return {"id": row.id, "name": row.name, "tags": _json.loads(row.tags_json)}

    def delete_config(self, config_id: int) -> bool:
        """Delete a tag configuration by id. Returns True if deleted."""
        with self._Session() as s:
            row = s.query(DcmTagConfigRow).filter_by(id=config_id).first()
            if not row:
                return False
            s.delete(row)
            s.commit()
            return True

    @staticmethod
    def common_tags() -> list[dict[str, str]]:
        """Return the list of common tags as dicts for the frontend."""
        return [
            {"keyword": kw, "label": lbl, "default": dflt}
            for kw, lbl, dflt in Pdf2Dcm.COMMON_TAGS
        ]

    @staticmethod
    def convert(
        pdf_path: Path | str,
        *,
        template_path: Path | str | None = None,
        tags: dict[str, str] | None = None,
    ) -> bytes:
        """Convert a PDF file into DICOM Encapsulated PDF bytes.

        Parameters
        ----------
        pdf_path:
            Path to the source PDF file.
        template_path:
            Optional path to an existing DICOM file whose dataset is used
            as a template (patient / study level tags are copied).
        tags:
            Additional DICOM keyword→value pairs to set.  These override
            any values copied from the template.

        Returns
        -------
        bytes
            The complete DICOM file content ready to be written to disk.
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.is_file():
            msg = f"PDF file not found: {pdf_path}"
            raise FileNotFoundError(msg)

        pdf_data = pdf_path.read_bytes()

        ds = Pdf2Dcm._build_dataset(pdf_data, template_path, tags)

        return Pdf2Dcm._to_bytes(ds)

    # ── internals ─────────────────────────────────────────────────────

    @staticmethod
    def _build_dataset(
        pdf_data: bytes,
        template_path: Path | str | None,
        tags: dict[str, str] | None,
    ) -> Dataset:
        """Construct a DICOM dataset for an encapsulated PDF."""
        ds = Dataset()

        # Copy template tags if provided
        if template_path is not None:
            template_path = Path(template_path)
            if template_path.is_file():
                from pydicom import dcmread  # noqa: PLC0415

                tmpl = dcmread(str(template_path))
                Pdf2Dcm._copy_template_tags(tmpl, ds)

        # ── Required UIDs & SOP class ────────────────────────────────
        ds.SOPClassUID = Pdf2Dcm.SOP_CLASS_UID
        if not hasattr(ds, "SOPInstanceUID") or not ds.SOPInstanceUID:
            ds.SOPInstanceUID = uid.generate_uid()
        else:  # pragma: no cover – SOPInstanceUID is never copied from templates
            # Always give a fresh SOP Instance UID for a new object
            ds.SOPInstanceUID = uid.generate_uid()
        if not hasattr(ds, "StudyInstanceUID") or not ds.StudyInstanceUID:
            ds.StudyInstanceUID = uid.generate_uid()
        if not hasattr(ds, "SeriesInstanceUID") or not ds.SeriesInstanceUID:
            ds.SeriesInstanceUID = uid.generate_uid()

        # Transfer Syntax
        file_meta = FileMetaDataset()
        file_meta.TransferSyntaxUID = UID(uid.ExplicitVRLittleEndian)
        file_meta.MediaStorageSOPClassUID = UID(Pdf2Dcm.SOP_CLASS_UID)
        file_meta.MediaStorageSOPInstanceUID = UID(ds.SOPInstanceUID)
        file_meta.ImplementationClassUID = UID("1.2.276.0.7230010.3.0.3.6.8")
        ds.file_meta = file_meta
        ds.file_meta.ImplementationVersionName = "FT_PDF2DCM"

        ds.is_little_endian = True
        ds.is_implicit_VR = False

        # ── Mandatory attributes for Encapsulated PDF ────────────────
        now = datetime.datetime.now()
        if not hasattr(ds, "InstanceCreationDate") or not ds.InstanceCreationDate:
            ds.InstanceCreationDate = now.strftime("%Y%m%d")
        if not hasattr(ds, "InstanceCreationTime") or not ds.InstanceCreationTime:
            ds.InstanceCreationTime = now.strftime("%H%M%S")
        if not hasattr(ds, "ContentDate") or not ds.ContentDate:
            ds.ContentDate = now.strftime("%Y%m%d")
        if not hasattr(ds, "ContentTime") or not ds.ContentTime:
            ds.ContentTime = now.strftime("%H%M%S")

        # Modality must be DOC for Encapsulated PDF
        if not hasattr(ds, "Modality") or not ds.Modality:
            ds.Modality = "DOC"

        # Image Type – default to DERIVED\SECONDARY
        if not hasattr(ds, "ImageType") or not ds.ImageType:
            ds.ImageType = "DERIVED\\SECONDARY"

        # Series Number
        if not hasattr(ds, "SeriesNumber") or not ds.SeriesNumber:
            ds.SeriesNumber = "500"

        # Series Description
        if not hasattr(ds, "SeriesDescription") or not ds.SeriesDescription:
            ds.SeriesDescription = "Report PDF"

        # Document Title
        if not hasattr(ds, "DocumentTitle") or not ds.DocumentTitle:
            ds.DocumentTitle = "Radiology Report"

        # Set empty required type 2 elements if missing
        for attr in (
            "PatientName", "PatientID", "PatientBirthDate", "PatientSex",
            "StudyDate", "StudyTime", "AccessionNumber",
            "ReferringPhysicianName", "StudyID",
        ):
            if not hasattr(ds, attr):
                setattr(ds, attr, "")

        if not hasattr(ds, "InstanceNumber") or not ds.InstanceNumber:
            ds.InstanceNumber = "1"

        # BurnedInAnnotation is required for Encapsulated PDF
        if not hasattr(ds, "BurnedInAnnotation") or not ds.BurnedInAnnotation:
            ds.BurnedInAnnotation = "YES"

        # MIME type
        ds.MIMETypeOfEncapsulatedDocument = "application/pdf"

        # ── Apply user-supplied tags ─────────────────────────────────
        Pdf2Dcm._apply_tags(ds, tags or {})

        # ── Ensure Content Date/Time are never empty after overrides ─
        if not ds.ContentDate:
            ds.ContentDate = now.strftime("%Y%m%d")
        if not ds.ContentTime:
            ds.ContentTime = now.strftime("%H%M%S")

        # ── Embed the PDF data ───────────────────────────────────────
        ds.EncapsulatedDocument = pdf_data

        return ds

    @staticmethod
    def _copy_template_tags(tmpl: Dataset, ds: Dataset) -> None:
        """Copy patient / study / series level tags from template."""
        # Tags to copy from template (patient + study + series level)
        _COPY_KEYWORDS = [
            "PatientName", "PatientID", "PatientBirthDate", "PatientSex",
            "StudyInstanceUID", "StudyDate", "StudyTime",
            "StudyDescription", "StudyID",
            "AccessionNumber", "ReferringPhysicianName",
            "InstitutionName", "Manufacturer", "StationName",
            "SeriesInstanceUID", "SeriesDescription", "SeriesNumber",
            "Modality",
        ]
        for kw in _COPY_KEYWORDS:
            if hasattr(tmpl, kw):
                setattr(ds, kw, getattr(tmpl, kw))

    @staticmethod
    def _apply_tags(ds: Dataset, tags: dict[str, str]) -> None:
        """Apply user-supplied tags to the dataset."""
        for keyword, value in tags.items():
            if not keyword or not isinstance(keyword, str):
                continue
            try:
                setattr(ds, keyword, value)
            except (AttributeError, ValueError, TypeError):  # pragma: no cover
                # Skip unknown or invalid tags silently
                pass

    @staticmethod
    def _to_bytes(ds: Dataset) -> bytes:
        """Serialize a dataset to DICOM file bytes."""
        import io  # noqa: PLC0415

        buf = io.BytesIO()
        ds.save_as(buf, write_like_original=False)
        return buf.getvalue()
