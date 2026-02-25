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
from file_tools.tools.dedup_scanner import DedupScanner
from file_tools.tools.pdf_tools import (
    merge_pdfs,
    parse_page_ranges,
    split_pdf,
    split_pdf_to_images,
)

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
async def pdf_merge(
    files: Annotated[list[UploadFile], File()],
    dpi: Annotated[int, Form()] = 150,
    max_side_px: Annotated[int, Form()] = 0,
    margin_mm: Annotated[float, Form()] = 0.0,
) -> Response:
    """Merge multiple uploaded PDF/image files into one PDF and return the result."""
    if len(files) < 2:
        raise HTTPException(status_code=422, detail="At least two files are required.")

    tmp_paths: list[Path] = []
    try:
        import tempfile

        for upload in files:
            suffix = Path(upload.filename or "file.pdf").suffix or ".pdf"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                content = await upload.read()
                tmp.write(content)
                tmp_paths.append(Path(tmp.name))

        merged_bytes = merge_pdfs(
            tmp_paths, dpi=dpi, max_side_px=max_side_px, margin_mm=margin_mm,
        )
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
    ranges: Annotated[str, Form()] = "",
    output_type: Annotated[str, Form()] = "pdf",
) -> Response:
    """Split an uploaded PDF according to *ranges* and return a ZIP archive.

    If *ranges* is empty every page becomes its own file.
    *output_type* can be ``pdf`` or ``jpeg``.
    """
    pdf_bytes = await file.read()

    from pypdf import PdfReader as _Reader  # noqa: PLC0415

    total_pages = len(_Reader(io.BytesIO(pdf_bytes)).pages)
    if ranges.strip():
        try:
            page_ranges = parse_page_ranges(ranges, total_pages)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
    else:
        page_ranges = [(i, i) for i in range(1, total_pages + 1)]

    ext = "jpg" if output_type == "jpeg" else "pdf"
    stem = Path(file.filename or "file").stem

    if output_type == "jpeg":
        # pypdfium2 needs a real file – write temp, split, then clean up
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            tmp_path = Path(tmp.name)
        try:
            parts = split_pdf_to_images(tmp_path, page_ranges)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
    else:
        parts = split_pdf(io.BytesIO(pdf_bytes), page_ranges)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, (part_bytes, (start, end)) in enumerate(zip(parts, page_ranges), start=1):
            rng = str(start) if start == end else f"{start}-{end}"
            zf.writestr(f"{stem}_{rng}.{ext}", part_bytes)
    zip_buf.seek(0)

    return Response(
        content=zip_buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=split.zip"},
    )


@app.post("/api/pdf/split-to-folder")
async def pdf_split_to_folder(body: dict) -> JSONResponse:
    """Split a PDF on disk into individual files in *output_dir*.

    Body JSON keys:
    - ``file_path``: source PDF path
    - ``output_dir``: destination folder
    - ``ranges``: optional range string; empty = one file per page
    - ``output_type``: ``pdf`` | ``jpeg``
    - ``dpi``: DPI for JPEG output (default 150)
    - ``confirmed``: if *true*, overwrite existing files

    When ``confirmed`` is falsy the endpoint only *checks* for
    conflicts and returns ``{"conflicts": [...]}``; if the list is
    empty the caller can proceed (or re-call with ``confirmed: true``).
    """
    file_path = Path(body.get("file_path", ""))
    output_dir = Path(body.get("output_dir", ""))
    ranges_str: str = body.get("ranges", "").strip()
    output_type: str = body.get("output_type", "pdf")
    dpi: int = int(body.get("dpi", 150))
    confirmed: bool = bool(body.get("confirmed", False))

    if not file_path.is_file():
        raise HTTPException(status_code=422, detail=f"PDF not found: {file_path}")
    if not output_dir.is_dir():
        raise HTTPException(status_code=422, detail=f"Output directory not found: {output_dir}")

    from pypdf import PdfReader as _Reader  # noqa: PLC0415

    total_pages = len(_Reader(str(file_path)).pages)
    if ranges_str:
        page_ranges = parse_page_ranges(ranges_str, total_pages)
    else:
        page_ranges = [(i, i) for i in range(1, total_pages + 1)]

    ext = "jpg" if output_type == "jpeg" else "pdf"
    stem = file_path.stem

    # Build output file names
    out_names: list[str] = []
    for start, end in page_ranges:
        rng = str(start) if start == end else f"{start}-{end}"
        out_names.append(f"{stem}_{rng}.{ext}")

    # Overwrite check
    conflicts = [n for n in out_names if (output_dir / n).exists()]
    if conflicts and not confirmed:
        return JSONResponse(content={"conflicts": conflicts})

    # Actually split
    try:
        if output_type == "jpeg":
            parts = split_pdf_to_images(file_path, page_ranges, dpi=dpi)
        else:
            parts = split_pdf(file_path, page_ranges)
    except (ValueError, ImportError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    written: list[str] = []
    for name, data in zip(out_names, parts):
        dest = output_dir / name
        dest.write_bytes(data)
        written.append(str(dest))

    return JSONResponse(content={"conflicts": [], "written": written})


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
# Deduplication
# ---------------------------------------------------------------------------

_dedup_db_url: str | None = None  # let DedupScanner use its default (temp dir)


@app.post("/api/dedup/scan")
async def dedup_scan(body: dict) -> JSONResponse:
    """Scan a directory for duplicate files and folders."""
    directory = body.get("directory", "")
    root = Path(directory)
    if not root.is_dir():
        raise HTTPException(status_code=422, detail=f"Not a directory: {directory}")

    scanner = DedupScanner(db_url=_dedup_db_url)
    result = scanner.scan(root)
    return JSONResponse(content=result)


@app.post("/api/dedup/delete")
async def dedup_delete(body: dict) -> JSONResponse:
    """Delete a file or directory (used by dedup UI)."""
    path_str = body.get("path", "")
    is_dir: bool = body.get("is_dir", False)
    target = Path(path_str)

    if is_dir and not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {path_str}")
    if not is_dir and not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path_str}")

    DedupScanner.delete_path(target)
    return JSONResponse(content={"deleted": path_str})


# ---------------------------------------------------------------------------
# Desktop mode check
# ---------------------------------------------------------------------------


@app.get("/api/mode")
async def mode_check() -> JSONResponse:
    """Return whether the app is running in desktop (pywebview) mode."""
    return JSONResponse(content={"desktop": _webview_window is not None})


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
        file_types=(
            "Supported Files (*.pdf;*.jpg;*.jpeg;*.png;*.bmp;*.tiff;*.tif;*.webp)",
            "PDF Files (*.pdf)",
            "Image Files (*.jpg;*.jpeg;*.png;*.bmp;*.tiff;*.tif;*.webp)",
            "All Files (*.*)",
        ),
    )
    return JSONResponse(content={"files": list(result) if result else []})


@app.get("/api/dialog/save")
async def dialog_save(filename: str = "merged.pdf") -> JSONResponse:
    """Open a native save-file dialog (desktop / pywebview mode only)."""
    if _webview_window is None:
        raise HTTPException(status_code=503, detail="Not running in desktop mode.")
    import webview as _wv  # noqa: PLC0415

    result = _webview_window.create_file_dialog(
        _wv.SAVE_DIALOG,
        save_filename=filename,
        file_types=("PDF Files (*.pdf)", "All Files (*.*)"),
    )
    path = result if isinstance(result, str) else (result[0] if result else None)
    return JSONResponse(content={"path": path})


@app.post("/api/file/save")
async def file_save(
    path: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> JSONResponse:
    """Save an uploaded file to *path* on disk (desktop mode helper)."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    dest.write_bytes(content)
    return JSONResponse(content={"saved": str(dest)})


@app.post("/api/file/open")
async def file_open(body: dict) -> JSONResponse:
    """Open a file or folder with the system default application."""
    import os
    import subprocess
    import sys

    path = body.get("path", "")
    fpath = Path(path)
    if not fpath.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    if sys.platform == "win32":
        os.startfile(str(fpath))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(fpath)])  # noqa: S603
    else:
        subprocess.Popen(["xdg-open", str(fpath)])  # noqa: S603

    return JSONResponse(content={"opened": str(fpath)})


@app.post("/api/pdf/upload-temp")
async def pdf_upload_temp(
    file: Annotated[UploadFile, File()],
) -> JSONResponse:
    """Save an uploaded PDF to a temporary file and return its path.

    Used in desktop mode when the user selected a file via the browser
    input instead of the native file dialog – the server needs a real
    path for the split-to-folder workflow.
    """
    import tempfile as _tf  # noqa: PLC0415

    data = await file.read()
    suffix = Path(file.filename or "upload.pdf").suffix or ".pdf"
    stem = Path(file.filename or "upload").stem
    with _tf.NamedTemporaryFile(delete=False, suffix=suffix, prefix=f"{stem}_") as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    return JSONResponse(content={"path": tmp_path})


@app.get("/api/dialog/directory")
async def dialog_directory(default_dir: str = "") -> JSONResponse:
    """Open a native folder-select dialog (desktop / pywebview mode only).

    *default_dir* sets the initial directory shown by the dialog.
    """
    if _webview_window is None:
        raise HTTPException(status_code=503, detail="Not running in desktop mode.")
    import webview as _wv  # noqa: PLC0415

    kwargs: dict = {}
    if default_dir and Path(default_dir).is_dir():
        kwargs["directory"] = default_dir

    result = _webview_window.create_file_dialog(_wv.FOLDER_DIALOG, **kwargs)
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
