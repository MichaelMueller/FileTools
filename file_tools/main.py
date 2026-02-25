"""FastAPI application for FileTools."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from file_tools.tools.dir_compare import compare_directories, sync_directories
from file_tools.tools.pdf_tools import merge_pdfs, parse_page_ranges, split_pdf

# Optional pywebview window – set by desktop.py at runtime
_webview_window = None  # type: ignore[assignment]

app = FastAPI(title="FileTools", version="0.1.0")

_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ---------------------------------------------------------------------------
# HTML entry-point
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    return FileResponse(str(_static_dir / "index.html"))


# ---------------------------------------------------------------------------
# PDF tools
# ---------------------------------------------------------------------------


@app.post("/api/pdf/merge")
async def pdf_merge(files: Annotated[list[UploadFile], File()]) -> Response:
    """Merge multiple uploaded PDF files into one and return the result."""
    if len(files) < 2:
        raise HTTPException(status_code=422, detail="At least two PDF files are required.")

    tmp_paths: list[Path] = []
    try:
        import tempfile

        for upload in files:
            suffix = Path(upload.filename or "file.pdf").suffix or ".pdf"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                content = await upload.read()
                tmp.write(content)
                tmp_paths.append(Path(tmp.name))

        merged_bytes = merge_pdfs(tmp_paths)
    finally:
        for p in tmp_paths:
            p.unlink(missing_ok=True)

    return Response(
        content=merged_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=merged.pdf"},
    )


@app.post("/api/pdf/split")
async def pdf_split(
    file: Annotated[UploadFile, File()],
    ranges: Annotated[str, Form()],
) -> Response:
    """Split an uploaded PDF according to *ranges* and return a ZIP archive."""
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        reader_import = __import__("pypdf", fromlist=["PdfReader"])
        total_pages = len(reader_import.PdfReader(str(tmp_path)).pages)
        page_ranges = parse_page_ranges(ranges, total_pages)
        parts = split_pdf(tmp_path, page_ranges)
    except ValueError as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, part_bytes in enumerate(parts, start=1):
            zf.writestr(f"part_{idx:03d}.pdf", part_bytes)
    zip_buf.seek(0)

    return Response(
        content=zip_buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=split.zip"},
    )


# ---------------------------------------------------------------------------
# Directory tools
# ---------------------------------------------------------------------------


@app.post("/api/dir/compare")
async def dir_compare(
    source: Annotated[str, Form()],
    target: Annotated[str, Form()],
) -> JSONResponse:
    """Compare *source* and *target* directories and return the diff."""
    src = Path(source)
    tgt = Path(target)
    if not src.is_dir():
        raise HTTPException(status_code=422, detail=f"Source is not a directory: {source}")
    if not tgt.is_dir():
        raise HTTPException(status_code=422, detail=f"Target is not a directory: {target}")
    result = compare_directories(src, tgt)
    return JSONResponse(content=result)


@app.post("/api/dir/sync")
async def dir_sync(
    source: Annotated[str, Form()],
    target: Annotated[str, Form()],
    files: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    """Copy missing/modified files from *source* to *target*.

    Optionally restrict to a newline-separated list of relative paths in *files*.
    """
    src = Path(source)
    tgt = Path(target)
    if not src.is_dir():
        raise HTTPException(status_code=422, detail=f"Source is not a directory: {source}")
    if not tgt.is_dir():
        raise HTTPException(status_code=422, detail=f"Target is not a directory: {target}")

    files_list: list[str] | None = None
    if files:
        files_list = [f.strip() for f in files.splitlines() if f.strip()]

    copied = sync_directories(src, tgt, files_list)
    return JSONResponse(content={"copied": copied})


# ---------------------------------------------------------------------------
# pywebview native file-dialog endpoints
# ---------------------------------------------------------------------------


@app.get("/api/dialog/files")
async def dialog_files(multiple: bool = True) -> JSONResponse:
    """Open a native file-open dialog (desktop / pywebview mode only)."""
    if _webview_window is None:
        raise HTTPException(status_code=503, detail="Not running in desktop mode.")
    import webview as _wv  # noqa: PLC0415

    result = _webview_window.create_file_dialog(
        _wv.OPEN_DIALOG,
        allow_multiple=multiple,
        file_types=("PDF Files (*.pdf)", "All Files (*.*)"),
    )
    return JSONResponse(content={"files": list(result) if result else []})


@app.get("/api/dialog/directory")
async def dialog_directory() -> JSONResponse:
    """Open a native folder-select dialog (desktop / pywebview mode only)."""
    if _webview_window is None:
        raise HTTPException(status_code=503, detail="Not running in desktop mode.")
    import webview as _wv  # noqa: PLC0415

    result = _webview_window.create_file_dialog(_wv.FOLDER_DIALOG)
    directory = result[0] if result else None
    return JSONResponse(content={"directory": directory})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def set_webview_window(window: object) -> None:  # noqa: ANN001
    """Called by desktop.py to register the active pywebview window."""
    global _webview_window  # noqa: PLW0603
    _webview_window = window


def run_web(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the uvicorn server (web mode)."""
    uvicorn.run(app, host=host, port=port)
