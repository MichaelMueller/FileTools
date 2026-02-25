"""Tests for the FastAPI application (file_tools.main)."""

from __future__ import annotations

import io
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


def test_dialog_files_with_window(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = ["/tmp/a.pdf", "/tmp/b.pdf"]
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": MagicMock(OPEN_DIALOG=0, FOLDER_DIALOG=1)}):
        r = client.get("/api/dialog/files?multiple=true")
    assert r.status_code == 200
    assert r.json()["files"] == ["/tmp/a.pdf", "/tmp/b.pdf"]


def test_dialog_files_cancelled(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = None
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": MagicMock(OPEN_DIALOG=0, FOLDER_DIALOG=1)}):
        r = client.get("/api/dialog/files")
    assert r.status_code == 200
    assert r.json()["files"] == []


def test_dialog_directory_with_window(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = ["/tmp/mydir"]
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": MagicMock(OPEN_DIALOG=0, FOLDER_DIALOG=1)}):
        r = client.get("/api/dialog/directory")
    assert r.status_code == 200
    assert r.json()["directory"] == "/tmp/mydir"


def test_dialog_directory_cancelled(client: TestClient) -> None:
    mock_window = MagicMock()
    mock_window.create_file_dialog.return_value = None
    set_webview_window(mock_window)

    with patch.dict("sys.modules", {"webview": MagicMock(OPEN_DIALOG=0, FOLDER_DIALOG=1)}):
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
# run_web
# ---------------------------------------------------------------------------


def test_run_web_calls_uvicorn() -> None:
    with patch("file_tools.main.uvicorn.run") as mock_run:
        from file_tools.main import run_web
        run_web(host="0.0.0.0", port=9999)
        mock_run.assert_called_once_with(app, host="0.0.0.0", port=9999)
