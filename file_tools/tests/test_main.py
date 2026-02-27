"""Tests for the FastAPI application (file_tools.main)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from file_tools import main as main_module
from file_tools.main import app, set_webview_window


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pdf_bytes(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def reset_webview_window():
    """Ensure _webview_window is None before and after each test."""
    set_webview_window(None)
    yield
    set_webview_window(None)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


def test_root_returns_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert b"FileTools" in r.content


# ---------------------------------------------------------------------------
# PDF Merge
# ---------------------------------------------------------------------------


def test_pdf_merge_success(client: TestClient) -> None:
    pdf1 = _make_pdf_bytes(1)
    pdf2 = _make_pdf_bytes(2)
    r = client.post(
        "/api/pdf/merge",
        files=[
            ("files", ("a.pdf", pdf1, "application/pdf")),
            ("files", ("b.pdf", pdf2, "application/pdf")),
        ],
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


def test_pdf_merge_too_few_files(client: TestClient) -> None:
    pdf1 = _make_pdf_bytes(1)
    r = client.post(
        "/api/pdf/merge",
        files=[("files", ("a.pdf", pdf1, "application/pdf"))],
    )
    assert r.status_code == 422


def test_pdf_merge_corrupted_file(client: TestClient) -> None:
    """merge_pdfs raises on corrupt input – backend should return 500."""
    with patch("file_tools.main.merge_pdfs", side_effect=Exception("corrupt PDF")):
        r = client.post(
            "/api/pdf/merge",
            files=[
                ("files", ("a.pdf", b"fake", "application/pdf")),
                ("files", ("b.pdf", b"fake", "application/pdf")),
            ],
        )
    assert r.status_code == 500
    assert "merge failed" in r.json()["detail"].lower()


def test_pdf_merge_permission_error(client: TestClient) -> None:
    """merge_pdfs raises PermissionError – backend should return 422."""
    with patch("file_tools.main.merge_pdfs", side_effect=PermissionError("locked")):
        r = client.post(
            "/api/pdf/merge",
            files=[
                ("files", ("a.pdf", b"fake", "application/pdf")),
                ("files", ("b.pdf", b"fake", "application/pdf")),
            ],
        )
    assert r.status_code == 422
    assert "permission denied" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# PDF Split
# ---------------------------------------------------------------------------


def test_pdf_split_success(client: TestClient) -> None:
    pdf = _make_pdf_bytes(3)
    r = client.post(
        "/api/pdf/split",
        data={"ranges": "1-2,3"},
        files=[("file", ("test.pdf", pdf, "application/pdf"))],
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert len(zf.namelist()) == 2


def test_pdf_split_invalid_ranges(client: TestClient) -> None:
    pdf = _make_pdf_bytes(2)
    r = client.post(
        "/api/pdf/split",
        data={"ranges": "abc"},
        files=[("file", ("test.pdf", pdf, "application/pdf"))],
    )
    assert r.status_code == 422


def test_pdf_split_corrupted_file(client: TestClient) -> None:
    """split_pdf raises on corrupt input – backend should return 500."""
    pdf = _make_pdf_bytes(1)
    with patch("file_tools.main.split_pdf", side_effect=Exception("corrupt")):
        r = client.post(
            "/api/pdf/split",
            data={"ranges": "1"},
            files=[("file", ("test.pdf", pdf, "application/pdf"))],
        )
    assert r.status_code == 500
    assert "split failed" in r.json()["detail"].lower()


def test_pdf_split_permission_error(client: TestClient) -> None:
    """split_pdf raises PermissionError – backend should return 422."""
    pdf = _make_pdf_bytes(1)
    with patch("file_tools.main.split_pdf", side_effect=PermissionError("no access")):
        r = client.post(
            "/api/pdf/split",
            data={"ranges": "1"},
            files=[("file", ("test.pdf", pdf, "application/pdf"))],
        )
    assert r.status_code == 422
    assert "permission denied" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Directory Compare
# ---------------------------------------------------------------------------


def test_dir_compare_success(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    r = client.post(
        "/api/dir/compare",
        data={"source": str(src_dir), "target": str(tgt_dir)},
    )
    assert r.status_code == 200
    data = r.json()
    assert "missing" in data
    assert len(data["missing"]) == 3


def test_dir_compare_invalid_source(client: TestClient, tgt_dir: Path) -> None:
    r = client.post(
        "/api/dir/compare",
        data={"source": "/nonexistent/path", "target": str(tgt_dir)},
    )
    assert r.status_code == 422


def test_dir_compare_invalid_target(client: TestClient, src_dir: Path) -> None:
    r = client.post(
        "/api/dir/compare",
        data={"source": str(src_dir), "target": "/nonexistent/path"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Directory Sync
# ---------------------------------------------------------------------------


def test_dir_sync_success(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    r = client.post(
        "/api/dir/sync",
        data={"source": str(src_dir), "target": str(tgt_dir)},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["copied"]) == 3


def test_dir_sync_with_files_param(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    r = client.post(
        "/api/dir/sync",
        data={"source": str(src_dir), "target": str(tgt_dir), "files": "a.txt"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["copied"] == ["a.txt"]


def test_dir_sync_invalid_source(client: TestClient, tgt_dir: Path) -> None:
    r = client.post(
        "/api/dir/sync",
        data={"source": "/bad/path", "target": str(tgt_dir)},
    )
    assert r.status_code == 422


def test_dir_sync_invalid_target(client: TestClient, src_dir: Path) -> None:
    r = client.post(
        "/api/dir/sync",
        data={"source": str(src_dir), "target": "/bad/path"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Dedup API endpoints
# ---------------------------------------------------------------------------


def _parse_sse(response) -> list[dict]:
    """Parse SSE events from a streaming response."""
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _sse_result(response) -> dict:
    """Extract the final 'result' event from an SSE response."""
    for evt in _parse_sse(response):
        if evt.get("type") == "result":
            return evt
    return {}


def _sse_error(response) -> str:
    """Extract error detail from an SSE response."""
    for evt in _parse_sse(response):
        if evt.get("type") == "error":
            return evt.get("detail", "")
    return ""


def test_dedup_scan_success(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "dup_root"
    root.mkdir()
    (root / "a.txt").write_text("same")
    (root / "b.txt").write_text("same")
    (root / "c.txt").write_text("diff")

    r = client.post(
        "/api/dedup/scan",
        json={"directory": str(root)},
    )
    assert r.status_code == 200
    data = _sse_result(r)
    assert "dup_files" in data
    assert len(data["dup_files"]) == 1
    assert data["stats"]["total_files"] == 3

    # Verify progress events were emitted
    events = _parse_sse(r)
    progress_events = [e for e in events if e.get("type") == "progress"]
    assert len(progress_events) > 0


def test_dedup_scan_invalid_dir(client: TestClient) -> None:
    r = client.post(
        "/api/dedup/scan",
        json={"directory": "/nonexistent/path"},
    )
    assert r.status_code == 422


def test_dedup_delete_file(client: TestClient, tmp_path: Path) -> None:
    f = tmp_path / "todel.txt"
    f.write_text("bye")
    with patch("file_tools.main.DedupScanner.delete_path"):
        r = client.post(
            "/api/dedup/delete",
            json={"path": str(f), "is_dir": False},
        )
    assert r.status_code == 200


def test_dedup_delete_directory(client: TestClient, tmp_path: Path) -> None:
    d = tmp_path / "todel_dir"
    d.mkdir()
    (d / "child.txt").write_text("x")
    with patch("file_tools.main.DedupScanner.delete_path"):
        r = client.post(
            "/api/dedup/delete",
            json={"path": str(d), "is_dir": True},
        )
    assert r.status_code == 200


def test_dedup_delete_nonexistent_file(client: TestClient) -> None:
    r = client.post(
        "/api/dedup/delete",
        json={"path": "/nonexistent", "is_dir": False},
    )
    assert r.status_code == 404


def test_dedup_delete_nonexistent_dir(client: TestClient) -> None:
    r = client.post(
        "/api/dedup/delete",
        json={"path": "/nonexistent", "is_dir": True},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Error handling – files/dirs deleted during operation
# ---------------------------------------------------------------------------


def test_dir_compare_deleted_mid_operation(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    """compare_directories raises FileNotFoundError if a dir disappears."""
    with patch("file_tools.main.compare_directories", side_effect=FileNotFoundError("src gone")):
        r = client.post(
            "/api/dir/compare",
            data={"source": str(src_dir), "target": str(tgt_dir)},
        )
    assert r.status_code == 422
    assert "no longer exists" in r.json()["detail"].lower()


def test_dir_compare_os_error(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    """compare_directories raises OSError if a dir is unreadable."""
    with patch("file_tools.main.compare_directories", side_effect=OSError("access denied")):
        r = client.post(
            "/api/dir/compare",
            data={"source": str(src_dir), "target": str(tgt_dir)},
        )
    assert r.status_code == 422
    assert "cannot read" in r.json()["detail"].lower()


def test_dir_sync_file_deleted(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    """sync_directories raises FileNotFoundError when a file vanishes."""
    with patch("file_tools.main.sync_directories", side_effect=FileNotFoundError("a.txt gone")):
        r = client.post(
            "/api/dir/sync",
            data={"source": str(src_dir), "target": str(tgt_dir), "files": "a.txt"},
        )
    assert r.status_code == 422
    assert "no longer exists" in r.json()["detail"].lower()


def test_dir_sync_permission_error(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    """sync_directories raises PermissionError if target is not writable."""
    with patch("file_tools.main.sync_directories", side_effect=PermissionError("read-only")):
        r = client.post(
            "/api/dir/sync",
            data={"source": str(src_dir), "target": str(tgt_dir), "files": "a.txt"},
        )
    assert r.status_code == 422
    assert "permission denied" in r.json()["detail"].lower()


def test_dir_sync_os_error(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    """sync_directories raises OSError for generic I/O issues."""
    with patch("file_tools.main.sync_directories", side_effect=OSError("disk full")):
        r = client.post(
            "/api/dir/sync",
            data={"source": str(src_dir), "target": str(tgt_dir)},
        )
    assert r.status_code == 422
    assert "sync failed" in r.json()["detail"].lower()


def test_dedup_scan_dir_deleted(client: TestClient, tmp_path: Path) -> None:
    """DedupScanner.scan() raises FileNotFoundError if dir is removed."""
    root = tmp_path / "vanishing"
    root.mkdir()
    with patch("file_tools.main.DedupScanner") as mock_cls:
        mock_cls.return_value.scan.side_effect = FileNotFoundError("gone")
        r = client.post("/api/dedup/scan", json={"directory": str(root)})
    assert r.status_code == 200
    assert "no longer exists" in _sse_error(r).lower()


def test_dedup_scan_permission_error(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "locked"
    root.mkdir()
    with patch("file_tools.main.DedupScanner") as mock_cls:
        mock_cls.return_value.scan.side_effect = PermissionError("no access")
        r = client.post("/api/dedup/scan", json={"directory": str(root)})
    assert r.status_code == 200
    assert "permission denied" in _sse_error(r).lower()


def test_dedup_delete_race_condition(client: TestClient, tmp_path: Path) -> None:
    """File passes existence check but is deleted before delete_path runs."""
    f = tmp_path / "race.txt"
    f.write_text("temp")
    with patch("file_tools.main.DedupScanner.delete_path", side_effect=FileNotFoundError("gone")):
        r = client.post("/api/dedup/delete", json={"path": str(f), "is_dir": False})
    assert r.status_code == 404
    assert "no longer exists" in r.json()["detail"].lower()


def test_dedup_delete_permission_denied(client: TestClient, tmp_path: Path) -> None:
    f = tmp_path / "locked.txt"
    f.write_text("locked")
    with patch("file_tools.main.DedupScanner.delete_path", side_effect=PermissionError("denied")):
        r = client.post("/api/dedup/delete", json={"path": str(f), "is_dir": False})
    assert r.status_code == 422
    assert "permission denied" in r.json()["detail"].lower()


def test_split_to_folder_file_deleted(client: TestClient, tmp_path: Path) -> None:
    """PDF disappears after validation but before splitting."""
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(_make_pdf_bytes(2))
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    with patch("file_tools.main.split_pdf", side_effect=FileNotFoundError("gone")):
        r = client.post(
            "/api/pdf/split-to-folder",
            json={
                "file_path": str(pdf_path),
                "output_dir": str(out_dir),
                "ranges": "",
                "output_type": "pdf",
                "confirmed": True,
            },
        )
    assert r.status_code == 422
    assert "no longer exists" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Mode check
# ---------------------------------------------------------------------------


def test_mode_check(client: TestClient) -> None:
    r = client.get("/api/mode")
    assert r.status_code == 200
    assert r.json()["desktop"] is False


# ---------------------------------------------------------------------------
# File open (supports files and folders)
# ---------------------------------------------------------------------------


def test_file_open_nonexistent(client: TestClient) -> None:
    r = client.post(
        "/api/file/open",
        json={"path": "/nonexistent/file.pdf"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Upload-temp
# ---------------------------------------------------------------------------


def test_upload_temp(client: TestClient) -> None:
    pdf = _make_pdf_bytes(1)
    r = client.post(
        "/api/pdf/upload-temp",
        files=[("file", ("test.pdf", pdf, "application/pdf"))],
    )
    assert r.status_code == 200
    data = r.json()
    assert "path" in data
    p = Path(data["path"])
    assert p.exists()
    p.unlink()  # cleanup


# ---------------------------------------------------------------------------
# Dialog endpoints – no webview window
# ---------------------------------------------------------------------------


def test_dialog_files_no_desktop(client: TestClient) -> None:
    r = client.get("/api/dialog/files")
    assert r.status_code == 503


def test_dialog_directory_no_desktop(client: TestClient) -> None:
    r = client.get("/api/dialog/directory")
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Dialog endpoints – with mocked webview window
# ---------------------------------------------------------------------------


def _mock_webview() -> MagicMock:
    """Create a mock webview module with the new FileDialog enum."""
    wv = MagicMock()
    wv.FileDialog.OPEN = 0
    wv.FileDialog.SAVE = 1
    wv.FileDialog.FOLDER = 2
    return wv


def test_dialog_files_with_window(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = ["/tmp/a.pdf", "/tmp/b.pdf"]
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": _mock_webview()}):
        r = client.get("/api/dialog/files?multiple=true")
    assert r.status_code == 200
    assert r.json()["files"] == ["/tmp/a.pdf", "/tmp/b.pdf"]


def test_dialog_files_cancelled(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = None
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": _mock_webview()}):
        r = client.get("/api/dialog/files")
    assert r.status_code == 200
    assert r.json()["files"] == []


def test_dialog_directory_with_window(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = ["/tmp/mydir"]
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": _mock_webview()}):
        r = client.get("/api/dialog/directory")
    assert r.status_code == 200
    assert r.json()["directory"] == "/tmp/mydir"


def test_dialog_directory_cancelled(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = None
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": _mock_webview()}):
        r = client.get("/api/dialog/directory")
    assert r.status_code == 200
    assert r.json()["directory"] is None


# ---------------------------------------------------------------------------
# set_webview_window helper
# ---------------------------------------------------------------------------


def test_set_webview_window_updates_module() -> None:
    mock_win = MagicMock()
    set_webview_window(mock_win)
    assert main_module._webview_window is mock_win


# ---------------------------------------------------------------------------
# PDF 2 DCM
# ---------------------------------------------------------------------------


def test_pdf2dcm_tags(client: TestClient) -> None:
    with patch("file_tools.main.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.common_tags.return_value = [
            {"keyword": "PatientName", "label": "Patient Name", "default": ""},
        ]
        r = client.get("/api/pdf2dcm/tags")
    assert r.status_code == 200
    tags = r.json()["tags"]
    assert len(tags) == 1
    assert tags[0]["keyword"] == "PatientName"


def test_pdf2dcm_convert(client: TestClient, tmp_path: Path) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 minimal")

    with patch("file_tools.main.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.convert.return_value = b"DICOM_DATA"
        with open(pdf, "rb") as f:
            r = client.post(
                "/api/pdf2dcm/convert",
                files={"pdf": ("test.pdf", f, "application/pdf")},
                data={"tags_json": '{"PatientName": "Test"}'},
            )
    assert r.status_code == 200
    assert r.content == b"DICOM_DATA"
    assert "test.dcm" in r.headers.get("content-disposition", "")


def test_pdf2dcm_convert_with_template(
    client: TestClient, tmp_path: Path,
) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 data")
    tmpl = tmp_path / "tmpl.dcm"
    tmpl.write_bytes(b"DICOM TEMPLATE")

    with patch("file_tools.main.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.convert.return_value = b"DCM_WITH_TMPL"
        with open(pdf, "rb") as fp, open(tmpl, "rb") as ft:
            r = client.post(
                "/api/pdf2dcm/convert",
                files={
                    "pdf": ("doc.pdf", fp, "application/pdf"),
                    "template": ("tmpl.dcm", ft, "application/dicom"),
                },
                data={"tags_json": "{}"},
            )
    assert r.status_code == 200
    assert r.content == b"DCM_WITH_TMPL"


def test_pdf2dcm_convert_empty_pdf(client: TestClient) -> None:
    from io import BytesIO
    r = client.post(
        "/api/pdf2dcm/convert",
        files={"pdf": ("empty.pdf", BytesIO(b""), "application/pdf")},
        data={"tags_json": "{}"},
    )
    assert r.status_code == 422


def test_pdf2dcm_convert_bad_tags_json(
    client: TestClient, tmp_path: Path,
) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 data")
    with open(pdf, "rb") as f:
        r = client.post(
            "/api/pdf2dcm/convert",
            files={"pdf": ("test.pdf", f, "application/pdf")},
            data={"tags_json": "NOT JSON"},
        )
    assert r.status_code == 422


def test_pdf2dcm_convert_desktop(client: TestClient, tmp_path: Path) -> None:
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 data")
    output = tmp_path / "output.dcm"

    with patch("file_tools.main.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.convert.return_value = b"DCM_DESKTOP"
        r = client.post(
            "/api/pdf2dcm/convert-desktop",
            json={
                "pdf_path": str(pdf),
                "output_path": str(output),
                "tags": {"PatientName": "Desktop"},
            },
        )
    assert r.status_code == 200
    assert output.read_bytes() == b"DCM_DESKTOP"
    assert r.json()["saved"] == str(output)


def test_pdf2dcm_convert_desktop_missing_pdf(client: TestClient) -> None:
    r = client.post(
        "/api/pdf2dcm/convert-desktop",
        json={"pdf_path": "", "output_path": "/tmp/out.dcm"},
    )
    assert r.status_code == 422


def test_pdf2dcm_convert_desktop_missing_output(client: TestClient) -> None:
    r = client.post(
        "/api/pdf2dcm/convert-desktop",
        json={"pdf_path": "/tmp/test.pdf", "output_path": ""},
    )
    assert r.status_code == 422


def test_pdf2dcm_convert_desktop_file_not_found(
    client: TestClient,
) -> None:
    with patch("file_tools.main.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.convert.side_effect = FileNotFoundError("not found")
        r = client.post(
            "/api/pdf2dcm/convert-desktop",
            json={
                "pdf_path": "/nonexistent.pdf",
                "output_path": "/tmp/out.dcm",
            },
        )
    assert r.status_code == 422


def test_pdf2dcm_convert_desktop_conversion_error(
    client: TestClient,
) -> None:
    with patch("file_tools.main.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.convert.side_effect = RuntimeError("conversion error")
        r = client.post(
            "/api/pdf2dcm/convert-desktop",
            json={
                "pdf_path": "/some.pdf",
                "output_path": "/tmp/out.dcm",
            },
        )
    assert r.status_code == 500


# -- Tag configuration API ---------------------------------------------------


def test_pdf2dcm_configs_empty(client: TestClient) -> None:
    with patch("file_tools.main.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.return_value.get_configs.return_value = []
        r = client.get("/api/pdf2dcm/configs")
    assert r.status_code == 200
    assert r.json()["configs"] == []


def test_pdf2dcm_configs_list(client: TestClient) -> None:
    with patch("file_tools.main.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.return_value.get_configs.return_value = [
            {"id": 1, "name": "mbits", "tags": {"PatientName": "mbits"}},
        ]
        r = client.get("/api/pdf2dcm/configs")
    assert r.status_code == 200
    cfgs = r.json()["configs"]
    assert len(cfgs) == 1
    assert cfgs[0]["name"] == "mbits"


def test_pdf2dcm_save_config(client: TestClient) -> None:
    with patch("file_tools.main.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.return_value.save_config.return_value = {
            "id": 1, "name": "test", "tags": {"PatientID": "123"},
        }
        r = client.post(
            "/api/pdf2dcm/configs",
            json={"name": "test", "tags": {"PatientID": "123"}},
        )
    assert r.status_code == 200
    assert r.json()["name"] == "test"


def test_pdf2dcm_save_config_empty_name(client: TestClient) -> None:
    r = client.post(
        "/api/pdf2dcm/configs",
        json={"name": "", "tags": {}},
    )
    assert r.status_code == 422


def test_pdf2dcm_delete_config(client: TestClient) -> None:
    with patch("file_tools.main.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.return_value.delete_config.return_value = True
        r = client.delete("/api/pdf2dcm/configs/1")
    assert r.status_code == 200
    assert r.json()["deleted"] == 1


def test_pdf2dcm_delete_config_not_found(client: TestClient) -> None:
    with patch("file_tools.main.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.return_value.delete_config.return_value = False
        r = client.delete("/api/pdf2dcm/configs/999")
    assert r.status_code == 404
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.get_regions.return_value = [
            {"id": 1, "name": "Home", "areas": [
                {"id": 10, "geocoded_name": "DE/Munich",
                 "lat": 48.0, "lon": 11.0, "radius_km": 5.0, "region_id": 1},
            ]},
        ]
        r = client.get("/api/gps-sort/regions")
    assert r.status_code == 200
    regions = r.json()["regions"]
    assert len(regions) == 1
    assert regions[0]["name"] == "Home"
    assert len(regions[0]["areas"]) == 1


def test_gps_sort_regions_create(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.add_region.return_value = {
            "id": 2, "name": "Vacation", "areas": [],
        }
        r = client.post(
            "/api/gps-sort/regions",
            json={"name": "Vacation"},
        )
    assert r.status_code == 200
    assert r.json()["region"]["name"] == "Vacation"


def test_gps_sort_regions_create_empty_name(client: TestClient) -> None:
    r = client.post(
        "/api/gps-sort/regions",
        json={"name": "  "},
    )
    assert r.status_code == 422


def test_gps_sort_regions_update(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.update_region.return_value = {
            "id": 1, "name": "Updated", "areas": [],
        }
        r = client.put(
            "/api/gps-sort/regions",
            json={"id": 1, "name": "Updated"},
        )
    assert r.status_code == 200
    assert r.json()["region"]["name"] == "Updated"


def test_gps_sort_regions_update_not_found(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.update_region.return_value = None
        r = client.put(
            "/api/gps-sort/regions",
            json={"id": 9999, "name": "X"},
        )
    assert r.status_code == 404


def test_gps_sort_regions_update_missing_id(client: TestClient) -> None:
    r = client.put(
        "/api/gps-sort/regions",
        json={"name": "X"},
    )
    assert r.status_code == 422


def test_gps_sort_regions_delete(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.delete_region.return_value = None
        r = client.request(
            "DELETE",
            "/api/gps-sort/regions",
            json={"id": 1},
        )
    assert r.status_code == 200
    assert r.json()["deleted"] is True


# ---------------------------------------------------------------------------
# GPS Sort — Areas
# ---------------------------------------------------------------------------


def test_gps_sort_areas_list(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.get_areas.return_value = [
            {"id": 10, "geocoded_name": "DE/Munich",
             "lat": 48.0, "lon": 11.0, "radius_km": 5.0, "region_id": None},
        ]
        r = client.get("/api/gps-sort/areas")
    assert r.status_code == 200
    areas = r.json()["areas"]
    assert len(areas) == 1
    assert areas[0]["geocoded_name"] == "DE/Munich"


def test_gps_sort_areas_create(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.add_area.return_value = {
            "id": 20, "geocoded_name": "Beach", "lat": 25.0, "lon": 55.0,
            "radius_km": 3.0, "region_id": None,
        }
        r = client.post(
            "/api/gps-sort/areas",
            json={"geocoded_name": "Beach", "lat": 25.0, "lon": 55.0,
                  "radius_km": 3.0},
        )
    assert r.status_code == 200
    assert r.json()["area"]["geocoded_name"] == "Beach"


def test_gps_sort_areas_create_from_url(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.parse_google_maps_url.return_value = (48.0, 11.0)
        MockSorter.return_value.add_area.return_value = {
            "id": 21, "geocoded_name": "Home", "lat": 48.0, "lon": 11.0,
            "radius_km": 5.0, "region_id": None,
        }
        r = client.post(
            "/api/gps-sort/areas",
            json={
                "geocoded_name": "Home",
                "url": "https://www.google.com/maps/@48.0,11.0,15z",
                "radius_km": 5.0,
            },
        )
    assert r.status_code == 200
    assert r.json()["area"]["geocoded_name"] == "Home"


def test_gps_sort_areas_create_bad_url(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.parse_google_maps_url.return_value = None
        r = client.post(
            "/api/gps-sort/areas",
            json={"geocoded_name": "X", "url": "not-a-url"},
        )
    assert r.status_code == 422


def test_gps_sort_areas_create_missing_coords(client: TestClient) -> None:
    r = client.post(
        "/api/gps-sort/areas",
        json={"geocoded_name": "X"},
    )
    assert r.status_code == 422


def test_gps_sort_areas_create_empty_name(client: TestClient) -> None:
    r = client.post(
        "/api/gps-sort/areas",
        json={"geocoded_name": "", "lat": 1.0, "lon": 2.0},
    )
    assert r.status_code == 422


def test_gps_sort_areas_create_with_region(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.add_area.return_value = {
            "id": 22, "geocoded_name": "Place", "lat": 48.0, "lon": 11.0,
            "radius_km": 5.0, "region_id": 1,
        }
        r = client.post(
            "/api/gps-sort/areas",
            json={"geocoded_name": "Place", "lat": 48.0, "lon": 11.0,
                  "region_id": 1},
        )
    assert r.status_code == 200
    assert r.json()["area"]["region_id"] == 1


def test_gps_sort_areas_update(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.update_area.return_value = {
            "id": 10, "geocoded_name": "Updated", "lat": 48.0, "lon": 11.0,
            "radius_km": 10.0, "region_id": None,
        }
        r = client.put(
            "/api/gps-sort/areas",
            json={"id": 10, "geocoded_name": "Updated", "radius_km": 10.0},
        )
    assert r.status_code == 200
    assert r.json()["area"]["geocoded_name"] == "Updated"


def test_gps_sort_areas_update_region_id(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.update_area.return_value = {
            "id": 10, "geocoded_name": "Place", "lat": 48.0, "lon": 11.0,
            "radius_km": 5.0, "region_id": 2,
        }
        r = client.put(
            "/api/gps-sort/areas",
            json={"id": 10, "region_id": 2},
        )
    assert r.status_code == 200
    assert r.json()["area"]["region_id"] == 2
    MockSorter.return_value.update_area.assert_called_once_with(
        10, region_id=2,
    )


def test_gps_sort_areas_update_unassign_region(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.update_area.return_value = {
            "id": 10, "geocoded_name": "Place", "lat": 48.0, "lon": 11.0,
            "radius_km": 5.0, "region_id": None,
        }
        r = client.put(
            "/api/gps-sort/areas",
            json={"id": 10, "region_id": None},
        )
    assert r.status_code == 200
    assert r.json()["area"]["region_id"] is None
    MockSorter.return_value.update_area.assert_called_once_with(
        10, region_id=None,
    )


def test_gps_sort_areas_update_not_found(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.update_area.return_value = None
        r = client.put(
            "/api/gps-sort/areas",
            json={"id": 9999, "geocoded_name": "X"},
        )
    assert r.status_code == 404


def test_gps_sort_areas_update_missing_id(client: TestClient) -> None:
    r = client.put(
        "/api/gps-sort/areas",
        json={"geocoded_name": "X"},
    )
    assert r.status_code == 422


def test_gps_sort_areas_delete(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.delete_area.return_value = None
        r = client.request(
            "DELETE",
            "/api/gps-sort/areas",
            json={"id": 10},
        )
    assert r.status_code == 200
    assert r.json()["deleted"] is True


# ---------------------------------------------------------------------------
# GPS Sort — Parse URL
# ---------------------------------------------------------------------------


def test_gps_sort_parse_url(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.parse_google_maps_url.return_value = (48.0, 11.0)
        r = client.post(
            "/api/gps-sort/parse-url",
            json={"url": "https://www.google.com/maps/@48.0,11.0,15z"},
        )
    assert r.status_code == 200
    assert r.json()["lat"] == 48.0
    assert r.json()["lon"] == 11.0


def test_gps_sort_parse_url_invalid(client: TestClient) -> None:
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.parse_google_maps_url.return_value = None
        r = client.post(
            "/api/gps-sort/parse-url",
            json={"url": "bad"},
        )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GPS Sort — Preview / Reclassify / Execute
# ---------------------------------------------------------------------------


def test_gps_sort_preview_success(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "gps_photos"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    fake_result = {
        "plan": [
            {
                "file": "a.jpg",
                "source": str(root / "a.jpg"),
                "folder": "No GPS",
                "destination": str(root / "No GPS" / "a.jpg"),
                "lat": None,
                "lon": None,
                "location_name": "No GPS",
                "group": "no_gps",
                "area_id": None,
            }
        ],
        "new_areas": [],
        "total": 1,
        "no_gps_count": 1,
    }
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.preview.return_value = fake_result
        r = client.post(
            "/api/gps-sort/preview",
            json={"directory": str(root)},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["no_gps_count"] == 1
    assert len(data["new_areas"]) == 0


def test_gps_sort_reclassify_success(client: TestClient) -> None:
    plan = [
        {"file": "a.jpg", "source": "/tmp/a.jpg", "lat": 48.0, "lon": 11.0},
    ]
    fake_result = {
        "plan": [dict(plan[0], group="area", folder="Home",
                      destination="/tmp/Home/a.jpg", location_name="Home",
                      area_id=1)],
        "new_areas": [],
        "total": 1,
        "no_gps_count": 0,
    }
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.reclassify.return_value = fake_result
        r = client.post(
            "/api/gps-sort/reclassify",
            json={"plan": plan},
        )
    assert r.status_code == 200
    assert r.json()["plan"][0]["group"] == "area"


def test_gps_sort_reclassify_empty(client: TestClient) -> None:
    r = client.post(
        "/api/gps-sort/reclassify",
        json={"plan": []},
    )
    assert r.status_code == 422


def test_gps_sort_preview_not_a_directory(client: TestClient) -> None:
    r = client.post(
        "/api/gps-sort/preview",
        json={"directory": "/nonexistent/path"},
    )
    assert r.status_code == 422


def test_gps_sort_preview_permission_error(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "locked"
    root.mkdir()
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.preview.side_effect = PermissionError("denied")
        r = client.post(
            "/api/gps-sort/preview",
            json={"directory": str(root)},
        )
    assert r.status_code == 422
    assert "permission" in r.json()["detail"].lower()


def test_gps_sort_execute_success(client: TestClient, tmp_path: Path) -> None:
    plan = [
        {
            "file": "a.jpg",
            "source": str(tmp_path / "a.jpg"),
            "folder": "No GPS",
            "destination": str(tmp_path / "No GPS" / "a.jpg"),
            "group": "no_gps",
            "area_id": None,
        }
    ]
    moved_result = [plan[0]]
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.execute.return_value = moved_result
        r = client.post(
            "/api/gps-sort/execute",
            json={"plan": plan},
        )
    assert r.status_code == 200
    assert r.json()["moved"] == 1


def test_gps_sort_execute_with_no_gps_name(client: TestClient, tmp_path: Path) -> None:
    plan = [
        {
            "file": "a.jpg",
            "source": str(tmp_path / "a.jpg"),
            "folder": "No GPS",
            "destination": str(tmp_path / "No GPS" / "a.jpg"),
            "group": "no_gps",
            "area_id": None,
        }
    ]
    moved_result = [dict(plan[0], folder="Unsorted")]
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.execute.return_value = moved_result
        r = client.post(
            "/api/gps-sort/execute",
            json={"plan": plan, "no_gps_name": "Unsorted"},
        )
    assert r.status_code == 200
    assert r.json()["moved"] == 1
    # Verify no_gps_name was passed through
    MockSorter.return_value.execute.assert_called_once()
    call_kwargs = MockSorter.return_value.execute.call_args
    assert call_kwargs[1]["no_gps_name"] == "Unsorted"


def test_gps_sort_execute_empty_plan(client: TestClient) -> None:
    r = client.post(
        "/api/gps-sort/execute",
        json={"plan": []},
    )
    assert r.status_code == 422


def test_gps_sort_execute_permission_error(client: TestClient) -> None:
    plan = [{"file": "x.jpg", "source": "/tmp/x.jpg", "folder": "No GPS", "destination": "/tmp/No GPS/x.jpg"}]
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.execute.side_effect = PermissionError("denied")
        r = client.post(
            "/api/gps-sort/execute",
            json={"plan": plan},
        )
    assert r.status_code == 422
    assert "permission" in r.json()["detail"].lower()


def test_gps_sort_execute_os_error(client: TestClient) -> None:
    plan = [{"file": "x.jpg", "source": "/tmp/x.jpg", "folder": "No GPS", "destination": "/tmp/No GPS/x.jpg"}]
    with patch("file_tools.main.GpsSorter") as MockSorter:
        MockSorter.return_value.execute.side_effect = OSError("disk full")
        r = client.post(
            "/api/gps-sort/execute",
            json={"plan": plan},
        )
    assert r.status_code == 422
    assert "move failed" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# run_web
# ---------------------------------------------------------------------------


def test_run_web_calls_uvicorn() -> None:
    with patch("file_tools.main.uvicorn.run") as mock_run:
        from file_tools.main import run_web
        run_web(host="0.0.0.0", port=9999)
        mock_run.assert_called_once_with(app, host="0.0.0.0", port=9999)
