"""Tests for the Pdf2Dcm class."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from pydicom import Dataset, dcmread
from pydicom.dataset import FileMetaDataset
from pydicom.uid import UID

from mmo_file_tools.tools.pdf2dcm import DcmTagConfigRow, Pdf2Dcm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def converter(tmp_path: Path) -> Pdf2Dcm:
    """Create a Pdf2Dcm instance with an in-memory DB."""
    return Pdf2Dcm(db_url="sqlite:///:memory:")


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    """Create a minimal valid-looking PDF file."""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF"
    )
    return pdf


@pytest.fixture()
def template_dcm(tmp_path: Path) -> Path:
    """Create a minimal DICOM file to use as a template."""
    ds = Dataset()
    ds.PatientName = "Template^Patient"
    ds.PatientID = "TMPL001"
    ds.PatientBirthDate = "19900101"
    ds.PatientSex = "M"
    ds.StudyDescription = "Template Study"
    ds.StudyInstanceUID = "1.2.3.4.5.6.7.8.9"
    ds.SeriesInstanceUID = "1.2.3.4.5.6.7.8.9.1"
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.104.1"
    ds.SOPInstanceUID = "1.2.3.4.5.6.7.8.9.2"
    ds.Modality = "DOC"
    file_meta = FileMetaDataset()
    file_meta.TransferSyntaxUID = UID("1.2.840.10008.1.2.1")
    file_meta.MediaStorageSOPClassUID = UID(ds.SOPClassUID)
    file_meta.MediaStorageSOPInstanceUID = UID(ds.SOPInstanceUID)
    file_meta.ImplementationClassUID = UID("1.2.3.4.5.6.7")
    ds.file_meta = file_meta

    dcm_path = tmp_path / "template.dcm"
    ds.save_as(str(dcm_path), write_like_original=False)
    return dcm_path


# ---------------------------------------------------------------------------
# common_tags
# ---------------------------------------------------------------------------


class TestCommonTags:
    """Tests for Pdf2Dcm.common_tags."""

    def test_returns_list(self) -> None:
        result = Pdf2Dcm.common_tags()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_tag_structure(self) -> None:
        result = Pdf2Dcm.common_tags()
        for tag in result:
            assert "keyword" in tag
            assert "label" in tag
            assert "default" in tag

    def test_contains_patient_name(self) -> None:
        keywords = [t["keyword"] for t in Pdf2Dcm.common_tags()]
        assert "PatientName" in keywords

    def test_contains_study_description(self) -> None:
        keywords = [t["keyword"] for t in Pdf2Dcm.common_tags()]
        assert "StudyDescription" in keywords

    def test_contains_series_description(self) -> None:
        keywords = [t["keyword"] for t in Pdf2Dcm.common_tags()]
        assert "SeriesDescription" in keywords

    def test_contains_image_type(self) -> None:
        keywords = [t["keyword"] for t in Pdf2Dcm.common_tags()]
        assert "ImageType" in keywords

    def test_contains_series_number(self) -> None:
        keywords = [t["keyword"] for t in Pdf2Dcm.common_tags()]
        assert "SeriesNumber" in keywords

    def test_contains_document_title(self) -> None:
        keywords = [t["keyword"] for t in Pdf2Dcm.common_tags()]
        assert "DocumentTitle" in keywords


# ---------------------------------------------------------------------------
# convert – basic
# ---------------------------------------------------------------------------


class TestConvertBasic:
    """Tests for Pdf2Dcm.convert with no template."""

    def test_returns_bytes(self, sample_pdf: Path) -> None:
        result = Pdf2Dcm.convert(sample_pdf)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_valid_dicom(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.SOPClassUID == Pdf2Dcm.SOP_CLASS_UID
        assert ds.Modality == "DOC"

    def test_contains_pdf_data(self, sample_pdf: Path) -> None:
        pdf_data = sample_pdf.read_bytes()
        dcm_bytes = Pdf2Dcm.convert(sample_pdf)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.EncapsulatedDocument == pdf_data

    def test_mime_type(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.MIMETypeOfEncapsulatedDocument == "application/pdf"

    def test_has_uids(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.SOPInstanceUID
        assert ds.StudyInstanceUID
        assert ds.SeriesInstanceUID

    def test_unique_sop_instance_uid(self, sample_pdf: Path) -> None:
        dcm_bytes1 = Pdf2Dcm.convert(sample_pdf)
        dcm_bytes2 = Pdf2Dcm.convert(sample_pdf)
        ds1 = dcmread(io.BytesIO(dcm_bytes1))
        ds2 = dcmread(io.BytesIO(dcm_bytes2))
        assert ds1.SOPInstanceUID != ds2.SOPInstanceUID

    def test_has_file_meta(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.file_meta.TransferSyntaxUID
        assert ds.file_meta.MediaStorageSOPClassUID == Pdf2Dcm.SOP_CLASS_UID

    def test_burned_in_annotation(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.BurnedInAnnotation == "YES"

    def test_has_dates(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.ContentDate
        assert ds.ContentTime
        assert ds.InstanceCreationDate
        assert ds.InstanceCreationTime

    def test_empty_type2_elements(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf)
        ds = dcmread(io.BytesIO(dcm_bytes))
        # Type 2 elements should exist (may be empty)
        assert hasattr(ds, "PatientName")
        assert hasattr(ds, "PatientID")
        assert hasattr(ds, "AccessionNumber")

    def test_default_image_type(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.ImageType == ["DERIVED", "SECONDARY"]

    def test_default_series_number(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.SeriesNumber == "500"

    def test_default_series_description(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.SeriesDescription == "Report PDF"

    def test_default_document_title(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.DocumentTitle == "Radiology Report"

    def test_content_date_always_set(self, sample_pdf: Path) -> None:
        """ContentDate/ContentTime must never be empty, even if overridden with blank."""
        dcm_bytes = Pdf2Dcm.convert(sample_pdf, tags={"ContentDate": "", "ContentTime": ""})
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.ContentDate
        assert ds.ContentTime


# ---------------------------------------------------------------------------
# convert – with tags
# ---------------------------------------------------------------------------


class TestConvertWithTags:
    """Tests for Pdf2Dcm.convert with user-supplied tags."""

    def test_sets_patient_name(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf, tags={"PatientName": "Doe^John"})
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert str(ds.PatientName) == "Doe^John"

    def test_sets_patient_id(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf, tags={"PatientID": "12345"})
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.PatientID == "12345"

    def test_sets_study_description(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(
            sample_pdf, tags={"StudyDescription": "Test Study"},
        )
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.StudyDescription == "Test Study"

    def test_sets_series_description(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(
            sample_pdf, tags={"SeriesDescription": "Report"},
        )
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.SeriesDescription == "Report"

    def test_sets_multiple_tags(self, sample_pdf: Path) -> None:
        tags = {
            "PatientName": "Smith^Jane",
            "PatientID": "99999",
            "PatientSex": "F",
            "StudyDescription": "Annual Checkup",
        }
        dcm_bytes = Pdf2Dcm.convert(sample_pdf, tags=tags)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert str(ds.PatientName) == "Smith^Jane"
        assert ds.PatientID == "99999"
        assert ds.PatientSex == "F"
        assert ds.StudyDescription == "Annual Checkup"

    def test_sets_study_instance_uid(self, sample_pdf: Path) -> None:
        uid = "1.2.276.0.7230010.3.1.2.2433542412.19924.1637668162.233"
        dcm_bytes = Pdf2Dcm.convert(
            sample_pdf, tags={"StudyInstanceUID": uid},
        )
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.StudyInstanceUID == uid

    def test_empty_tags_dict(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf, tags={})
        assert isinstance(dcm_bytes, bytes)

    def test_invalid_keyword_skipped(self, sample_pdf: Path) -> None:
        # Should not raise, just skip
        dcm_bytes = Pdf2Dcm.convert(
            sample_pdf, tags={"NotARealDicomTag99999": "value"},
        )
        assert isinstance(dcm_bytes, bytes)


# ---------------------------------------------------------------------------
# convert – with template
# ---------------------------------------------------------------------------


class TestConvertWithTemplate:
    """Tests for Pdf2Dcm.convert with a DICOM template file."""

    def test_copies_patient_name(
        self, sample_pdf: Path, template_dcm: Path,
    ) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf, template_path=template_dcm)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert str(ds.PatientName) == "Template^Patient"

    def test_copies_patient_id(
        self, sample_pdf: Path, template_dcm: Path,
    ) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf, template_path=template_dcm)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.PatientID == "TMPL001"

    def test_copies_study_description(
        self, sample_pdf: Path, template_dcm: Path,
    ) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf, template_path=template_dcm)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.StudyDescription == "Template Study"

    def test_copies_study_instance_uid(
        self, sample_pdf: Path, template_dcm: Path,
    ) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf, template_path=template_dcm)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.StudyInstanceUID == "1.2.3.4.5.6.7.8.9"

    def test_fresh_sop_instance_uid(
        self, sample_pdf: Path, template_dcm: Path,
    ) -> None:
        dcm_bytes = Pdf2Dcm.convert(sample_pdf, template_path=template_dcm)
        ds = dcmread(io.BytesIO(dcm_bytes))
        # SOP Instance UID should be different from template
        assert ds.SOPInstanceUID != "1.2.3.4.5.6.7.8.9.2"

    def test_tags_override_template(
        self, sample_pdf: Path, template_dcm: Path,
    ) -> None:
        dcm_bytes = Pdf2Dcm.convert(
            sample_pdf,
            template_path=template_dcm,
            tags={"PatientName": "Override^Name"},
        )
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert str(ds.PatientName) == "Override^Name"

    def test_nonexistent_template_ignored(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(
            sample_pdf, template_path=Path("/nonexistent/template.dcm"),
        )
        assert isinstance(dcm_bytes, bytes)

    def test_still_contains_pdf(
        self, sample_pdf: Path, template_dcm: Path,
    ) -> None:
        pdf_data = sample_pdf.read_bytes()
        dcm_bytes = Pdf2Dcm.convert(sample_pdf, template_path=template_dcm)
        ds = dcmread(io.BytesIO(dcm_bytes))
        assert ds.EncapsulatedDocument == pdf_data


# ---------------------------------------------------------------------------
# convert – error cases
# ---------------------------------------------------------------------------


class TestConvertErrors:
    """Tests for error handling in Pdf2Dcm.convert."""

    def test_nonexistent_pdf(self) -> None:
        with pytest.raises(FileNotFoundError):
            Pdf2Dcm.convert(Path("/nonexistent/file.pdf"))

    def test_string_path(self, sample_pdf: Path) -> None:
        dcm_bytes = Pdf2Dcm.convert(str(sample_pdf))
        assert isinstance(dcm_bytes, bytes)


class TestDefaultDbUrl:
    """Test Pdf2Dcm with default db_url (user_data_dir)."""

    def test_init_default_db(self, tmp_path: Path) -> None:
        """When db_url is None, uses user_data_dir to create the DB."""
        fake_dir = tmp_path / "appdata" / "MMO FileTools"
        with patch("mmo_file_tools.tools.pdf2dcm.user_data_dir", return_value=str(fake_dir)):
            p = Pdf2Dcm()
        assert (fake_dir / "mmo_file_tools_pdf2dcm.db").exists()


# ---------------------------------------------------------------------------
# _build_dataset
# ---------------------------------------------------------------------------


class TestBuildDataset:
    """Tests for Pdf2Dcm._build_dataset."""

    def test_returns_dataset(self) -> None:
        ds = Pdf2Dcm._build_dataset(b"PDF DATA", None, None)
        assert isinstance(ds, Dataset)

    def test_encapsulated_document(self) -> None:
        data = b"TEST PDF CONTENT"
        ds = Pdf2Dcm._build_dataset(data, None, None)
        assert ds.EncapsulatedDocument == data

    def test_sop_class_uid(self) -> None:
        ds = Pdf2Dcm._build_dataset(b"X", None, None)
        assert ds.SOPClassUID == Pdf2Dcm.SOP_CLASS_UID


# ---------------------------------------------------------------------------
# _copy_template_tags
# ---------------------------------------------------------------------------


class TestCopyTemplateTags:
    """Tests for Pdf2Dcm._copy_template_tags."""

    def test_copies_fields(self) -> None:
        tmpl = Dataset()
        tmpl.PatientName = "Source^Pat"
        tmpl.PatientID = "SRC001"
        tmpl.InstitutionName = "Hospital"
        ds = Dataset()
        Pdf2Dcm._copy_template_tags(tmpl, ds)
        assert str(ds.PatientName) == "Source^Pat"
        assert ds.PatientID == "SRC001"
        assert ds.InstitutionName == "Hospital"

    def test_missing_fields_skipped(self) -> None:
        tmpl = Dataset()
        tmpl.PatientName = "Only^Name"
        ds = Dataset()
        Pdf2Dcm._copy_template_tags(tmpl, ds)
        assert str(ds.PatientName) == "Only^Name"
        assert not hasattr(ds, "InstitutionName")


# ---------------------------------------------------------------------------
# _apply_tags
# ---------------------------------------------------------------------------


class TestApplyTags:
    """Tests for Pdf2Dcm._apply_tags."""

    def test_sets_tag(self) -> None:
        ds = Dataset()
        Pdf2Dcm._apply_tags(ds, {"PatientName": "Test"})
        assert str(ds.PatientName) == "Test"

    def test_empty_dict(self) -> None:
        ds = Dataset()
        Pdf2Dcm._apply_tags(ds, {})
        # Should not raise

    def test_empty_keyword_skipped(self) -> None:
        ds = Dataset()
        Pdf2Dcm._apply_tags(ds, {"": "value"})
        # Should not raise

    def test_none_keyword_skipped(self) -> None:
        ds = Dataset()
        Pdf2Dcm._apply_tags(ds, {None: "value"})  # type: ignore[dict-item]
        # Should not raise


# ---------------------------------------------------------------------------
# _to_bytes
# ---------------------------------------------------------------------------


class TestToBytes:
    """Tests for Pdf2Dcm._to_bytes."""

    def test_returns_bytes(self, sample_pdf: Path) -> None:
        ds = Pdf2Dcm._build_dataset(sample_pdf.read_bytes(), None, None)
        result = Pdf2Dcm._to_bytes(ds)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_roundtrip(self, sample_pdf: Path) -> None:
        ds = Pdf2Dcm._build_dataset(sample_pdf.read_bytes(), None, None)
        dcm_bytes = Pdf2Dcm._to_bytes(ds)
        ds2 = dcmread(io.BytesIO(dcm_bytes))
        assert ds2.SOPClassUID == ds.SOPClassUID


# ---------------------------------------------------------------------------
# Tag configurations (DB-backed)
# ---------------------------------------------------------------------------


class TestGetConfigs:
    """Tests for Pdf2Dcm.get_configs."""

    def test_empty_initially(self, converter: Pdf2Dcm) -> None:
        assert converter.get_configs() == []

    def test_returns_saved(self, converter: Pdf2Dcm) -> None:
        converter.save_config("cfg1", {"PatientName": "Test"})
        cfgs = converter.get_configs()
        assert len(cfgs) == 1
        assert cfgs[0]["name"] == "cfg1"
        assert cfgs[0]["tags"] == {"PatientName": "Test"}

    def test_ordered_by_name(self, converter: Pdf2Dcm) -> None:
        converter.save_config("Bravo", {})
        converter.save_config("Alpha", {})
        cfgs = converter.get_configs()
        assert [c["name"] for c in cfgs] == ["Alpha", "Bravo"]


class TestSaveConfig:
    """Tests for Pdf2Dcm.save_config."""

    def test_creates_new(self, converter: Pdf2Dcm) -> None:
        result = converter.save_config("myconfig", {"PatientID": "123"})
        assert result["name"] == "myconfig"
        assert result["tags"] == {"PatientID": "123"}
        assert "id" in result

    def test_overwrites_existing(self, converter: Pdf2Dcm) -> None:
        converter.save_config("myconfig", {"PatientID": "123"})
        result = converter.save_config("myconfig", {"PatientID": "456"})
        assert result["tags"] == {"PatientID": "456"}
        assert len(converter.get_configs()) == 1

    def test_multiple_configs(self, converter: Pdf2Dcm) -> None:
        converter.save_config("a", {"A": "1"})
        converter.save_config("b", {"B": "2"})
        assert len(converter.get_configs()) == 2

    def test_empty_tags(self, converter: Pdf2Dcm) -> None:
        result = converter.save_config("empty", {})
        assert result["tags"] == {}


class TestDeleteConfig:
    """Tests for Pdf2Dcm.delete_config."""

    def test_deletes_existing(self, converter: Pdf2Dcm) -> None:
        cfg = converter.save_config("todelete", {"X": "Y"})
        assert converter.delete_config(cfg["id"]) is True
        assert len(converter.get_configs()) == 0

    def test_returns_false_for_missing(self, converter: Pdf2Dcm) -> None:
        assert converter.delete_config(999) is False

    def test_does_not_affect_others(self, converter: Pdf2Dcm) -> None:
        c1 = converter.save_config("keep", {"A": "1"})
        c2 = converter.save_config("drop", {"B": "2"})
        converter.delete_config(c2["id"])
        cfgs = converter.get_configs()
        assert len(cfgs) == 1
        assert cfgs[0]["name"] == "keep"
