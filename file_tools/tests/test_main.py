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
    r = client.post(
        "/api/pdf/merge",
        files=[],
    )
    assert r.status_code == 422


def test_pdf_merge_single_file(client: TestClient) -> None:
    pdf1 = _make_pdf_bytes(2)
    r = client.post(
        "/api/pdf/merge",
        files=[("files", ("a.pdf", pdf1, "application/pdf"))],
    )
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"


def test_pdf_merge_corrupted_file(client: TestClient) -> None:
    """merge_pdfs raises on corrupt input â€“ backend should return 500."""
    with patch("file_tools.tools.pdf_tools.merge_pdfs", side_effect=Exception("corrupt PDF")):
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
    """merge_pdfs raises PermissionError â€“ backend should return 422."""
    with patch("file_tools.tools.pdf_tools.merge_pdfs", side_effect=PermissionError("locked")):
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
# PDF Merge by Path (desktop mode)
# ---------------------------------------------------------------------------


def test_pdf_merge_by_path_success(client: TestClient, tmp_path: Path) -> None:
    f1 = tmp_path / "a.pdf"
    f2 = tmp_path / "b.pdf"
    f1.write_bytes(_make_pdf_bytes(1))
    f2.write_bytes(_make_pdf_bytes(2))
    r = client.post(
        "/api/pdf/merge-by-path",
        data={"file_paths": f"{f1}\n{f2}"},
    )
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"


def test_pdf_merge_by_path_single_file(client: TestClient, tmp_path: Path) -> None:
    f1 = tmp_path / "a.pdf"
    f1.write_bytes(_make_pdf_bytes(3))
    r = client.post(
        "/api/pdf/merge-by-path",
        data={"file_paths": str(f1)},
    )
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"


def test_pdf_merge_by_path_no_files(client: TestClient) -> None:
    r = client.post(
        "/api/pdf/merge-by-path",
        data={"file_paths": ""},
    )
    assert r.status_code == 422


def test_pdf_merge_by_path_missing_file(client: TestClient) -> None:
    r = client.post(
        "/api/pdf/merge-by-path",
        data={"file_paths": "/nonexistent/file.pdf"},
    )
    assert r.status_code == 404


def test_pdf_merge_by_path_no_open_handles(client: TestClient, tmp_path: Path) -> None:
    """After merge-by-path, source files must not be locked."""
    f1 = tmp_path / "a.pdf"
    f1.write_bytes(_make_pdf_bytes(1))
    r = client.post(
        "/api/pdf/merge-by-path",
        data={"file_paths": str(f1)},
    )
    assert r.status_code == 200
    # Must be deletable immediately
    f1.unlink()
    assert not f1.exists()


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
    """split_pdf raises on corrupt input â€“ backend should return 500."""
    pdf = _make_pdf_bytes(1)
    with patch("file_tools.tools.pdf_tools.split_pdf", side_effect=Exception("corrupt")):
        r = client.post(
            "/api/pdf/split",
            data={"ranges": "1"},
            files=[("file", ("test.pdf", pdf, "application/pdf"))],
        )
    assert r.status_code == 500
    assert "split failed" in r.json()["detail"].lower()


def test_pdf_split_permission_error(client: TestClient) -> None:
    """split_pdf raises PermissionError â€“ backend should return 422."""
    pdf = _make_pdf_bytes(1)
    with patch("file_tools.tools.pdf_tools.split_pdf", side_effect=PermissionError("no access")):
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
    with patch("file_tools.tools.dedup_scanner.DedupScanner.delete_path"):
        r = client.post(
            "/api/dedup/delete",
            json={"path": str(f), "is_dir": False},
        )
    assert r.status_code == 200


def test_dedup_delete_directory(client: TestClient, tmp_path: Path) -> None:
    d = tmp_path / "todel_dir"
    d.mkdir()
    (d / "child.txt").write_text("x")
    with patch("file_tools.tools.dedup_scanner.DedupScanner.delete_path"):
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
# Error handling â€“ files/dirs deleted during operation
# ---------------------------------------------------------------------------


def test_dir_compare_deleted_mid_operation(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    """compare_directories raises FileNotFoundError if a dir disappears."""
    with patch("file_tools.tools.dir_compare.compare_directories", side_effect=FileNotFoundError("src gone")):
        r = client.post(
            "/api/dir/compare",
            data={"source": str(src_dir), "target": str(tgt_dir)},
        )
    assert r.status_code == 422
    assert "no longer exists" in r.json()["detail"].lower()


def test_dir_compare_os_error(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    """compare_directories raises OSError if a dir is unreadable."""
    with patch("file_tools.tools.dir_compare.compare_directories", side_effect=OSError("access denied")):
        r = client.post(
            "/api/dir/compare",
            data={"source": str(src_dir), "target": str(tgt_dir)},
        )
    assert r.status_code == 422
    assert "cannot read" in r.json()["detail"].lower()


def test_dir_sync_file_deleted(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    """sync_directories raises FileNotFoundError when a file vanishes."""
    with patch("file_tools.tools.dir_compare.sync_directories", side_effect=FileNotFoundError("a.txt gone")):
        r = client.post(
            "/api/dir/sync",
            data={"source": str(src_dir), "target": str(tgt_dir), "files": "a.txt"},
        )
    assert r.status_code == 422
    assert "no longer exists" in r.json()["detail"].lower()


def test_dir_sync_permission_error(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    """sync_directories raises PermissionError if target is not writable."""
    with patch("file_tools.tools.dir_compare.sync_directories", side_effect=PermissionError("read-only")):
        r = client.post(
            "/api/dir/sync",
            data={"source": str(src_dir), "target": str(tgt_dir), "files": "a.txt"},
        )
    assert r.status_code == 422
    assert "permission denied" in r.json()["detail"].lower()


def test_dir_sync_os_error(client: TestClient, src_dir: Path, tgt_dir: Path) -> None:
    """sync_directories raises OSError for generic I/O issues."""
    with patch("file_tools.tools.dir_compare.sync_directories", side_effect=OSError("disk full")):
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
    with patch("file_tools.tools.dedup_scanner.DedupScanner") as mock_cls:
        mock_cls.return_value.scan.side_effect = FileNotFoundError("gone")
        r = client.post("/api/dedup/scan", json={"directory": str(root)})
    assert r.status_code == 200
    assert "no longer exists" in _sse_error(r).lower()


def test_dedup_scan_permission_error(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "locked"
    root.mkdir()
    with patch("file_tools.tools.dedup_scanner.DedupScanner") as mock_cls:
        mock_cls.return_value.scan.side_effect = PermissionError("no access")
        r = client.post("/api/dedup/scan", json={"directory": str(root)})
    assert r.status_code == 200
    assert "permission denied" in _sse_error(r).lower()


def test_dedup_delete_race_condition(client: TestClient, tmp_path: Path) -> None:
    """File passes existence check but is deleted before delete_path runs."""
    f = tmp_path / "race.txt"
    f.write_text("temp")
    with patch("file_tools.tools.dedup_scanner.DedupScanner.delete_path", side_effect=FileNotFoundError("gone")):
        r = client.post("/api/dedup/delete", json={"path": str(f), "is_dir": False})
    assert r.status_code == 404
    assert "no longer exists" in r.json()["detail"].lower()


def test_dedup_delete_permission_denied(client: TestClient, tmp_path: Path) -> None:
    f = tmp_path / "locked.txt"
    f.write_text("locked")
    with patch("file_tools.tools.dedup_scanner.DedupScanner.delete_path", side_effect=PermissionError("denied")):
        r = client.post("/api/dedup/delete", json={"path": str(f), "is_dir": False})
    assert r.status_code == 422
    assert "permission denied" in r.json()["detail"].lower()


def test_split_to_folder_file_deleted(client: TestClient, tmp_path: Path) -> None:
    """PDF disappears after validation but before splitting."""
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(_make_pdf_bytes(2))
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    with patch("file_tools.tools.pdf_tools.split_pdf", side_effect=FileNotFoundError("gone")):
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
# Dialog endpoints â€“ no webview window
# ---------------------------------------------------------------------------


def test_dialog_files_no_desktop(client: TestClient) -> None:
    r = client.get("/api/dialog/files")
    assert r.status_code == 503


def test_dialog_directory_no_desktop(client: TestClient) -> None:
    r = client.get("/api/dialog/directory")
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Dialog endpoints â€“ with mocked webview window
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
    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
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

    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
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

    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
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

    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
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
    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
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
    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
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
    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.return_value.get_configs.return_value = []
        r = client.get("/api/pdf2dcm/configs")
    assert r.status_code == 200
    assert r.json()["configs"] == []


def test_pdf2dcm_configs_list(client: TestClient) -> None:
    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.return_value.get_configs.return_value = [
            {"id": 1, "name": "mbits", "tags": {"PatientName": "mbits"}},
        ]
        r = client.get("/api/pdf2dcm/configs")
    assert r.status_code == 200
    cfgs = r.json()["configs"]
    assert len(cfgs) == 1
    assert cfgs[0]["name"] == "mbits"


def test_pdf2dcm_save_config(client: TestClient) -> None:
    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
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
    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.return_value.delete_config.return_value = True
        r = client.delete("/api/pdf2dcm/configs/1")
    assert r.status_code == 200
    assert r.json()["deleted"] == 1


def test_pdf2dcm_delete_config_not_found(client: TestClient) -> None:
    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.return_value.delete_config.return_value = False
        r = client.delete("/api/pdf2dcm/configs/999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# run_web
# ---------------------------------------------------------------------------

def test_run_web_calls_uvicorn() -> None:
    with patch("file_tools.main.uvicorn.run") as mock_run:
        from file_tools.main import run_web
        run_web(host="0.0.0.0", port=9999)
        mock_run.assert_called_once_with(app, host="0.0.0.0", port=9999)


# ---------------------------------------------------------------------------
# PDF Merge â€“ ValueError / OSError error paths
# ---------------------------------------------------------------------------


def test_pdf_merge_value_error(client: TestClient) -> None:
    with patch("file_tools.tools.pdf_tools.merge_pdfs", side_effect=ValueError("bad")):
        r = client.post(
            "/api/pdf/merge",
            files=[("files", ("a.pdf", b"fake", "application/pdf"))],
        )
    assert r.status_code == 422
    assert "invalid input" in r.json()["detail"].lower()


def test_pdf_merge_os_error(client: TestClient) -> None:
    with patch("file_tools.tools.pdf_tools.merge_pdfs", side_effect=OSError("disk")):
        r = client.post(
            "/api/pdf/merge",
            files=[("files", ("a.pdf", b"fake", "application/pdf"))],
        )
    assert r.status_code == 422
    assert "merge failed" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# PDF Merge by Path â€“ error paths
# ---------------------------------------------------------------------------


def test_pdf_merge_by_path_empty_paths(client: TestClient) -> None:
    r = client.post(
        "/api/pdf/merge-by-path",
        data={"file_paths": "   "},
    )
    assert r.status_code == 422


def test_pdf_merge_by_path_value_error(client: TestClient, tmp_path: Path) -> None:
    f = tmp_path / "a.pdf"
    f.write_bytes(_make_pdf_bytes(1))
    with patch("file_tools.tools.pdf_tools.merge_pdfs", side_effect=ValueError("bad")):
        r = client.post(
            "/api/pdf/merge-by-path",
            data={"file_paths": str(f)},
        )
    assert r.status_code == 422
    assert "invalid input" in r.json()["detail"].lower()


def test_pdf_merge_by_path_permission_error(client: TestClient, tmp_path: Path) -> None:
    f = tmp_path / "a.pdf"
    f.write_bytes(_make_pdf_bytes(1))
    with patch("file_tools.tools.pdf_tools.merge_pdfs", side_effect=PermissionError("locked")):
        r = client.post(
            "/api/pdf/merge-by-path",
            data={"file_paths": str(f)},
        )
    assert r.status_code == 422
    assert "permission denied" in r.json()["detail"].lower()


def test_pdf_merge_by_path_os_error(client: TestClient, tmp_path: Path) -> None:
    f = tmp_path / "a.pdf"
    f.write_bytes(_make_pdf_bytes(1))
    with patch("file_tools.tools.pdf_tools.merge_pdfs", side_effect=OSError("disk")):
        r = client.post(
            "/api/pdf/merge-by-path",
            data={"file_paths": str(f)},
        )
    assert r.status_code == 422
    assert "merge failed" in r.json()["detail"].lower()


def test_pdf_merge_by_path_generic_error(client: TestClient, tmp_path: Path) -> None:
    f = tmp_path / "a.pdf"
    f.write_bytes(_make_pdf_bytes(1))
    with patch("file_tools.tools.pdf_tools.merge_pdfs", side_effect=RuntimeError("unexpected")):
        r = client.post(
            "/api/pdf/merge-by-path",
            data={"file_paths": str(f)},
        )
    assert r.status_code == 500
    assert "merge failed" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# PDF Split â€“ additional error paths
# ---------------------------------------------------------------------------


def test_pdf_split_corrupt_file(client: TestClient) -> None:
    """Sending garbage data triggers Cannot read PDF error."""
    r = client.post(
        "/api/pdf/split",
        data={"ranges": "1"},
        files=[("file", ("test.pdf", b"not a pdf", "application/pdf"))],
    )
    assert r.status_code == 422
    assert "cannot read pdf" in r.json()["detail"].lower()


def test_pdf_split_no_ranges(client: TestClient) -> None:
    """Empty ranges splits every page."""
    pdf = _make_pdf_bytes(2)
    r = client.post(
        "/api/pdf/split",
        data={"ranges": ""},
        files=[("file", ("test.pdf", pdf, "application/pdf"))],
    )
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert len(zf.namelist()) == 2


def test_pdf_split_jpeg_output(client: TestClient) -> None:
    """Split to JPEG output type."""
    pdf = _make_pdf_bytes(2)
    r = client.post(
        "/api/pdf/split",
        data={"ranges": "1", "output_type": "jpeg"},
        files=[("file", ("test.pdf", pdf, "application/pdf"))],
    )
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert len(names) == 1
    assert names[0].endswith(".jpg")


def test_pdf_split_os_error(client: TestClient) -> None:
    pdf = _make_pdf_bytes(1)
    with patch("file_tools.tools.pdf_tools.split_pdf", side_effect=OSError("disk")):
        r = client.post(
            "/api/pdf/split",
            data={"ranges": "1"},
            files=[("file", ("test.pdf", pdf, "application/pdf"))],
        )
    assert r.status_code == 422
    assert "split failed" in r.json()["detail"].lower()


def test_pdf_split_generic_error(client: TestClient) -> None:
    pdf = _make_pdf_bytes(1)
    with patch("file_tools.tools.pdf_tools.split_pdf", side_effect=RuntimeError("boom")):
        r = client.post(
            "/api/pdf/split",
            data={"ranges": "1"},
            files=[("file", ("test.pdf", pdf, "application/pdf"))],
        )
    assert r.status_code == 500
    assert "split failed" in r.json()["detail"].lower()


def test_pdf_split_value_error(client: TestClient) -> None:
    pdf = _make_pdf_bytes(1)
    with patch("file_tools.tools.pdf_tools.split_pdf", side_effect=ValueError("bad range")):
        r = client.post(
            "/api/pdf/split",
            data={"ranges": "1"},
            files=[("file", ("test.pdf", pdf, "application/pdf"))],
        )
    assert r.status_code == 422
    assert "split failed" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Split to folder â€“ additional paths
# ---------------------------------------------------------------------------


def test_split_to_folder_pdf_not_found(client: TestClient, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    r = client.post(
        "/api/pdf/split-to-folder",
        json={"file_path": "/nonexistent.pdf", "output_dir": str(out_dir)},
    )
    assert r.status_code == 422
    assert "pdf not found" in r.json()["detail"].lower()


def test_split_to_folder_output_dir_not_found(client: TestClient, tmp_path: Path) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(_make_pdf_bytes(1))
    r = client.post(
        "/api/pdf/split-to-folder",
        json={"file_path": str(pdf), "output_dir": "/nonexistent/dir"},
    )
    assert r.status_code == 422
    assert "output directory not found" in r.json()["detail"].lower()


def test_split_to_folder_success(client: TestClient, tmp_path: Path) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(_make_pdf_bytes(2))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    r = client.post(
        "/api/pdf/split-to-folder",
        json={
            "file_path": str(pdf),
            "output_dir": str(out_dir),
            "confirmed": True,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["written"]) == 2


def test_split_to_folder_with_ranges(client: TestClient, tmp_path: Path) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(_make_pdf_bytes(3))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    r = client.post(
        "/api/pdf/split-to-folder",
        json={
            "file_path": str(pdf),
            "output_dir": str(out_dir),
            "ranges": "1-2",
            "confirmed": True,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["written"]) == 1


def test_split_to_folder_conflict_check(client: TestClient, tmp_path: Path) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(_make_pdf_bytes(1))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    # Pre-create the output file to trigger conflict
    (out_dir / "test_1.pdf").write_text("existing")
    r = client.post(
        "/api/pdf/split-to-folder",
        json={
            "file_path": str(pdf),
            "output_dir": str(out_dir),
            "confirmed": False,
        },
    )
    assert r.status_code == 200
    assert len(r.json()["conflicts"]) == 1


def test_split_to_folder_jpeg(client: TestClient, tmp_path: Path) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(_make_pdf_bytes(1))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    r = client.post(
        "/api/pdf/split-to-folder",
        json={
            "file_path": str(pdf),
            "output_dir": str(out_dir),
            "output_type": "jpeg",
            "confirmed": True,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["written"]) == 1
    assert data["written"][0].endswith(".jpg")


def test_split_to_folder_permission_error(client: TestClient, tmp_path: Path) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(_make_pdf_bytes(1))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with patch("file_tools.tools.pdf_tools.split_pdf", side_effect=PermissionError("denied")):
        r = client.post(
            "/api/pdf/split-to-folder",
            json={
                "file_path": str(pdf),
                "output_dir": str(out_dir),
                "confirmed": True,
            },
        )
    assert r.status_code == 422
    assert "permission denied" in r.json()["detail"].lower()


def test_split_to_folder_os_error(client: TestClient, tmp_path: Path) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(_make_pdf_bytes(1))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with patch("file_tools.tools.pdf_tools.split_pdf", side_effect=OSError("disk")):
        r = client.post(
            "/api/pdf/split-to-folder",
            json={
                "file_path": str(pdf),
                "output_dir": str(out_dir),
                "confirmed": True,
            },
        )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Dedup â€“ OSError on delete
# ---------------------------------------------------------------------------


def test_dedup_delete_os_error(client: TestClient, tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("x")
    with patch("file_tools.tools.dedup_scanner.DedupScanner.delete_path", side_effect=OSError("fail")):
        r = client.post(
            "/api/dedup/delete",
            json={"path": str(f), "is_dir": False},
        )
    assert r.status_code == 422
    assert "delete failed" in r.json()["detail"].lower()


def test_dedup_scan_os_error(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "d"
    root.mkdir()
    with patch("file_tools.tools.dedup_scanner.DedupScanner") as mock_cls:
        mock_cls.return_value.scan.side_effect = OSError("fail")
        r = client.post("/api/dedup/scan", json={"directory": str(root)})
    assert r.status_code == 200
    assert "scan failed" in _sse_error(r).lower()


# ---------------------------------------------------------------------------
# Date Sort
# ---------------------------------------------------------------------------


def test_date_sort_preview_success(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    (root / "a.txt").write_text("hello")
    r = client.post("/api/date-sort/preview", json={"directory": str(root)})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert len(data["plan"]) == 1


def test_date_sort_preview_not_a_dir(client: TestClient) -> None:
    r = client.post("/api/date-sort/preview", json={"directory": "/nonexistent"})
    assert r.status_code == 422


def test_date_sort_preview_file_not_found(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "d"
    root.mkdir()
    with patch("file_tools.tools.date_sorter.DateSorter") as mock_cls:
        mock_cls.return_value.preview.side_effect = FileNotFoundError("gone")
        r = client.post("/api/date-sort/preview", json={"directory": str(root)})
    assert r.status_code == 422


def test_date_sort_preview_permission_error(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "d"
    root.mkdir()
    with patch("file_tools.tools.date_sorter.DateSorter") as mock_cls:
        mock_cls.return_value.preview.side_effect = PermissionError("no access")
        r = client.post("/api/date-sort/preview", json={"directory": str(root)})
    assert r.status_code == 422
    assert "permission denied" in r.json()["detail"].lower()


def test_date_sort_preview_os_error(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "d"
    root.mkdir()
    with patch("file_tools.tools.date_sorter.DateSorter") as mock_cls:
        mock_cls.return_value.preview.side_effect = OSError("io error")
        r = client.post("/api/date-sort/preview", json={"directory": str(root)})
    assert r.status_code == 422
    assert "preview failed" in r.json()["detail"].lower()


def test_date_sort_execute_success(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    f = root / "a.txt"
    f.write_text("hello")
    plan = [{"file": "a.txt", "source": str(f), "folder": "2025/01_Jan",
             "destination": str(root / "2025" / "01_Jan" / "a.txt")}]
    r = client.post("/api/date-sort/execute", json={"plan": plan})
    assert r.status_code == 200
    assert r.json()["moved"] == 1


def test_date_sort_execute_empty_plan(client: TestClient) -> None:
    r = client.post("/api/date-sort/execute", json={"plan": []})
    assert r.status_code == 422


def test_date_sort_execute_permission_error(client: TestClient) -> None:
    with patch("file_tools.tools.date_sorter.DateSorter") as mock_cls:
        mock_cls.return_value.execute.side_effect = PermissionError("no access")
        r = client.post("/api/date-sort/execute", json={"plan": [{"file": "a"}]})
    assert r.status_code == 422
    assert "permission denied" in r.json()["detail"].lower()


def test_date_sort_execute_os_error(client: TestClient) -> None:
    with patch("file_tools.tools.date_sorter.DateSorter") as mock_cls:
        mock_cls.return_value.execute.side_effect = OSError("disk full")
        r = client.post("/api/date-sort/execute", json={"plan": [{"file": "a"}]})
    assert r.status_code == 422
    assert "move failed" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# PDF2DCM convert â€“ FileNotFoundError and generic exception
# ---------------------------------------------------------------------------


def test_pdf2dcm_convert_file_not_found(client: TestClient, tmp_path: Path) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 data")
    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.convert.side_effect = FileNotFoundError("not found")
        with open(pdf, "rb") as f:
            r = client.post(
                "/api/pdf2dcm/convert",
                files={"pdf": ("test.pdf", f, "application/pdf")},
                data={"tags_json": "{}"},
            )
    assert r.status_code == 422


def test_pdf2dcm_convert_generic_error(client: TestClient, tmp_path: Path) -> None:
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 data")
    with patch("file_tools.tools.pdf2dcm.Pdf2Dcm") as MockPdf2Dcm:
        MockPdf2Dcm.convert.side_effect = RuntimeError("unexpected")
        with open(pdf, "rb") as f:
            r = client.post(
                "/api/pdf2dcm/convert",
                files={"pdf": ("test.pdf", f, "application/pdf")},
                data={"tags_json": "{}"},
            )
    assert r.status_code == 500


# ---------------------------------------------------------------------------
# File open â€“ success path
# ---------------------------------------------------------------------------


def test_file_open_success(client: TestClient, tmp_path: Path) -> None:
    f = tmp_path / "test.txt"
    f.write_text("hi")
    with patch("os.startfile", create=True):
        r = client.post("/api/file/open", json={"path": str(f)})
    assert r.status_code == 200
    assert r.json()["opened"] == str(f)


# ---------------------------------------------------------------------------
# Dialog endpoints â€“ with custom file_types
# ---------------------------------------------------------------------------


def test_dialog_files_custom_types(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = ["/tmp/a.dcm"]
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": _mock_webview()}):
        r = client.get("/api/dialog/files?file_types=DICOM (*.dcm)")
    assert r.status_code == 200
    assert r.json()["files"] == ["/tmp/a.dcm"]


def test_dialog_save_no_desktop(client: TestClient) -> None:
    r = client.get("/api/dialog/save")
    assert r.status_code == 503


def test_dialog_save_with_window(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = "/tmp/out.pdf"
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": _mock_webview()}):
        r = client.get("/api/dialog/save?filename=test.pdf")
    assert r.status_code == 200
    assert r.json()["path"] == "/tmp/out.pdf"


def test_dialog_save_with_custom_types(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = "/tmp/out.dcm"
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": _mock_webview()}):
        r = client.get("/api/dialog/save?file_types=DICOM (*.dcm)")
    assert r.status_code == 200


def test_dialog_save_cancelled(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = None
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": _mock_webview()}):
        r = client.get("/api/dialog/save")
    assert r.status_code == 200
    assert r.json()["path"] is None


def test_dialog_save_returns_list(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = ["/tmp/result.pdf"]
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": _mock_webview()}):
        r = client.get("/api/dialog/save")
    assert r.status_code == 200
    assert r.json()["path"] == "/tmp/result.pdf"


# ---------------------------------------------------------------------------
# File save endpoint
# ---------------------------------------------------------------------------


def test_file_save(client: TestClient, tmp_path: Path) -> None:
    dest = tmp_path / "saved.pdf"
    r = client.post(
        "/api/file/save",
        data={"path": str(dest)},
        files=[("file", ("test.pdf", b"PDF content", "application/pdf"))],
    )
    assert r.status_code == 200
    assert dest.read_bytes() == b"PDF content"


# ---------------------------------------------------------------------------
# Dialog directory with default_dir
# ---------------------------------------------------------------------------


def test_dialog_directory_with_default_dir(client: TestClient, tmp_path: Path) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = [str(tmp_path)]
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": _mock_webview()}):
        r = client.get(f"/api/dialog/directory?default_dir={tmp_path}")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Image Shrinker endpoints
# ---------------------------------------------------------------------------


def _make_test_image(path: Path, width: int = 800, height: int = 600) -> Path:
    from PIL import Image as PILImage

    img = PILImage.new("RGB", (width, height), (255, 0, 0))
    img.save(path, format="JPEG", quality=95)
    img.close()
    return path


def test_image_shrink_by_path_percent(client: TestClient, tmp_path: Path) -> None:
    p = _make_test_image(tmp_path / "photo.jpg", 1000, 800)
    r = client.post(
        "/api/image/shrink-by-path",
        data={
            "file_paths": str(p),
            "scale_percent": 50,
            "max_width": 0,
            "max_height": 0,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["results"][0]["new_size"] == [500, 400]
    assert "_shrunk" in data["results"][0]["path"]


def test_image_shrink_by_path_replace(client: TestClient, tmp_path: Path) -> None:
    p = _make_test_image(tmp_path / "photo.jpg", 1000, 800)
    r = client.post(
        "/api/image/shrink-by-path",
        data={
            "file_paths": str(p),
            "scale_percent": 50,
            "max_width": 0,
            "max_height": 0,
            "replace": "true",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["results"][0]["new_size"] == [500, 400]
    assert data["results"][0]["path"] == str(p)


def test_image_shrink_by_path_max_width(client: TestClient, tmp_path: Path) -> None:
    p = _make_test_image(tmp_path / "wide.jpg", 2000, 1000)
    r = client.post(
        "/api/image/shrink-by-path",
        data={"file_paths": str(p), "scale_percent": 0, "max_width": 1000, "max_height": 0},
    )
    assert r.status_code == 200
    assert r.json()["results"][0]["new_size"] == [1000, 500]


def test_image_shrink_by_path_no_files(client: TestClient) -> None:
    r = client.post(
        "/api/image/shrink-by-path",
        data={"file_paths": "", "scale_percent": 50, "max_width": 0, "max_height": 0},
    )
    assert r.status_code == 422


def test_image_shrink_by_path_invalid_params(client: TestClient, tmp_path: Path) -> None:
    p = _make_test_image(tmp_path / "photo.jpg")
    r = client.post(
        "/api/image/shrink-by-path",
        data={"file_paths": str(p), "scale_percent": 50, "max_width": 100, "max_height": 0},
    )
    assert r.status_code == 422


def test_image_shrink_by_path_generic_error(client: TestClient, tmp_path: Path) -> None:
    p = _make_test_image(tmp_path / "photo.jpg")
    with patch("file_tools.tools.image_shrinker.ImageShrinker.shrink", side_effect=RuntimeError("boom")):
        r = client.post(
            "/api/image/shrink-by-path",
            data={"file_paths": str(p), "scale_percent": 50, "max_width": 0, "max_height": 0},
        )
    assert r.status_code == 500


def test_image_shrink_upload(client: TestClient, tmp_path: Path) -> None:
    p = _make_test_image(tmp_path / "photo.jpg", 1000, 800)
    with open(p, "rb") as f:
        r = client.post(
            "/api/image/shrink",
            data={"scale_percent": 50, "max_width": 0, "max_height": 0},
            files=[("files", ("photo.jpg", f, "image/jpeg"))],
        )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "photo.jpg" in z.namelist()


def test_image_shrink_upload_invalid_params(client: TestClient, tmp_path: Path) -> None:
    p = _make_test_image(tmp_path / "photo.jpg")
    with open(p, "rb") as f:
        r = client.post(
            "/api/image/shrink",
            data={"scale_percent": 50, "max_width": 100, "max_height": 0},
            files=[("files", ("photo.jpg", f, "image/jpeg"))],
        )
    assert r.status_code == 422


def test_image_shrink_upload_generic_error(client: TestClient, tmp_path: Path) -> None:
    p = _make_test_image(tmp_path / "photo.jpg")
    with patch("file_tools.tools.image_shrinker.ImageShrinker.shrink", side_effect=RuntimeError("boom")):
        with open(p, "rb") as f:
            r = client.post(
                "/api/image/shrink",
                data={"scale_percent": 50, "max_width": 0, "max_height": 0},
                files=[("files", ("photo.jpg", f, "image/jpeg"))],
            )
    assert r.status_code == 500
