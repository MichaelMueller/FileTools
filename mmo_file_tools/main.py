"""FastAPI application for MMO FileTools."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Tool imports are deferred to the endpoint functions that need them to
# keep application startup fast (avoids loading PIL, pypdf, pydicom, … eagerly).

# File types the /api/file/open endpoint is permitted to open with the OS shell.
_SAFE_OPEN_EXTENSIONS = frozenset({
    ".pdf", ".dcm",
    ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp",
    ".zip",
})

# Optional pywebview window – set by desktop.py at runtime
_webview_window = None  # type: ignore[assignment]

app = FastAPI(title="MMO FileTools", version="1.5.0")

_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ---------------------------------------------------------------------------
# HTML entry-point
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    return FileResponse(
        str(_static_dir / "index.html"),
        headers={"Cache-Control": "no-cache"},
    )


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
    """Merge uploaded PDF/image files into one PDF and return the result."""
    from mmo_file_tools.tools.pdf_tools import merge_pdfs  # noqa: PLC0415

    if len(files) < 1:  # pragma: no cover – FastAPI validates before this
        raise HTTPException(status_code=422, detail="At least one file is required.")

    tmp_paths: list[Path] = []
    try:
        import tempfile

        for upload in files:
            suffix = Path(upload.filename or "file.pdf").suffix or ".pdf"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                content = await upload.read()
                tmp.write(content)
                tmp_paths.append(Path(tmp.name))

        try:
            merged_bytes = merge_pdfs(
                tmp_paths, dpi=dpi, max_side_px=max_side_px, margin_mm=margin_mm,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid input: {exc}") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=422, detail=f"Permission denied: {exc}") from exc
        except OSError as exc:
            raise HTTPException(status_code=422, detail=f"Merge failed: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Merge failed: {exc}") from exc
    finally:
        for p in tmp_paths:
            p.unlink(missing_ok=True)

    return Response(
        content=merged_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=merged.pdf"},
    )


@app.post("/api/pdf/merge-by-path")
async def pdf_merge_by_path(
    file_paths: Annotated[str, Form()],
    dpi: Annotated[int, Form()] = 150,
    max_side_px: Annotated[int, Form()] = 0,
    margin_mm: Annotated[float, Form()] = 0.0,
) -> Response:
    """Merge PDF/image files by filesystem path (desktop mode)."""
    from mmo_file_tools.tools.pdf_tools import merge_pdfs  # noqa: PLC0415

    paths = [Path(p.strip()) for p in file_paths.strip().split("\n") if p.strip()]
    if len(paths) < 1:
        raise HTTPException(status_code=422, detail="At least one file is required.")
    for p in paths:
        if not p.is_file():
            raise HTTPException(status_code=404, detail=f"File not found: {p}")

    try:
        merged_bytes = merge_pdfs(
            paths, dpi=dpi, max_side_px=max_side_px, margin_mm=margin_mm,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid input: {exc}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=f"Permission denied: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Merge failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Merge failed: {exc}") from exc

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
    from mmo_file_tools.tools.pdf_tools import (  # noqa: PLC0415
        parse_page_ranges, split_pdf, split_pdf_to_images,
    )

    pdf_bytes = await file.read()

    from pypdf import PdfReader as _Reader  # noqa: PLC0415

    try:
        total_pages = len(_Reader(io.BytesIO(pdf_bytes)).pages)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot read PDF: {exc}") from exc

    if ranges.strip():
        try:
            page_ranges = parse_page_ranges(ranges, total_pages)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        page_ranges = [(i, i) for i in range(1, total_pages + 1)]

    ext = "jpg" if output_type == "jpeg" else "pdf"
    stem = Path(file.filename or "file").stem

    try:
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
                except OSError:  # pragma: no cover – defensive cleanup
                    pass
        else:
            parts = split_pdf(io.BytesIO(pdf_bytes), page_ranges)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Split failed: {exc}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=f"Permission denied: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Split failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Split failed: {exc}") from exc

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

    from mmo_file_tools.tools.pdf_tools import (  # noqa: PLC0415
        parse_page_ranges, split_pdf, split_pdf_to_images,
    )

    if not file_path.is_file():
        raise HTTPException(status_code=422, detail=f"PDF not found: {file_path}")
    if not output_dir.is_dir():
        raise HTTPException(status_code=422, detail=f"Output directory not found: {output_dir}")

    from pypdf import PdfReader as _Reader  # noqa: PLC0415

    try:
        total_pages = len(_Reader(str(file_path)).pages)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot read PDF: {exc}") from exc
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
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=f"PDF file no longer exists: {file_path}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=f"Permission denied: {file_path}") from exc
    except (ValueError, ImportError, OSError) as exc:
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
    from mmo_file_tools.tools.dir_compare import compare_directories  # noqa: PLC0415

    src = Path(source)
    tgt = Path(target)
    if not src.is_dir():
        raise HTTPException(status_code=422, detail=f"Source is not a directory: {source}")
    if not tgt.is_dir():
        raise HTTPException(status_code=422, detail=f"Target is not a directory: {target}")
    try:
        result = compare_directories(src, tgt)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=f"Directory no longer exists: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Cannot read directory: {exc}") from exc
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
    from mmo_file_tools.tools.dir_compare import sync_directories  # noqa: PLC0415

    src = Path(source)
    tgt = Path(target)
    if not src.is_dir():
        raise HTTPException(status_code=422, detail=f"Source is not a directory: {source}")
    if not tgt.is_dir():
        raise HTTPException(status_code=422, detail=f"Target is not a directory: {target}")

    files_list: list[str] | None = None
    if files:
        files_list = [f.strip() for f in files.splitlines() if f.strip()]

    try:
        copied = sync_directories(src, tgt, files_list)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=f"File or directory no longer exists: {exc}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=f"Permission denied: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Sync failed: {exc}") from exc
    return JSONResponse(content={"copied": copied})


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

_dedup_db_url: str | None = None  # let DedupScanner use its default (temp dir)


@app.post("/api/dedup/scan")
async def dedup_scan(body: dict) -> StreamingResponse:
    """Scan a directory for duplicate files and folders.

    Returns a Server-Sent Events stream:
    - ``{"type": "progress", "files": N, "dirs": N}`` during scanning
    - ``{"type": "result", ...}`` with the final result
    - ``{"type": "error", "detail": "..."}`` on failure
    """
    directory = body.get("directory", "")
    root = Path(directory)
    if not root.is_dir():
        raise HTTPException(status_code=422, detail=f"Not a directory: {directory}")

    from mmo_file_tools.tools.dedup_scanner import DedupScanner  # noqa: PLC0415

    scanner = DedupScanner(db_url=_dedup_db_url)

    async def _event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _progress(files: int, dirs: int) -> None:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "progress", "files": files, "dirs": dirs},
            )

        def _do_scan() -> dict:
            return scanner.scan(root, progress_callback=_progress)

        future = loop.run_in_executor(None, _do_scan)

        while not future.done():
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.15)
                yield f"data: {json.dumps(msg)}\n\n"
            except asyncio.TimeoutError:
                pass

        # Drain remaining progress messages
        while not queue.empty():  # pragma: no cover – race-condition safety net
            msg = await queue.get()
            yield f"data: {json.dumps(msg)}\n\n"

        try:
            result = future.result()
            yield f"data: {json.dumps({'type': 'result', **result})}\n\n"
        except FileNotFoundError as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': f'Directory no longer exists: {exc}'})}\n\n"
        except PermissionError as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': f'Permission denied: {exc}'})}\n\n"
        except OSError as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': f'Scan failed: {exc}'})}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

    from mmo_file_tools.tools.dedup_scanner import DedupScanner  # noqa: PLC0415

    try:
        DedupScanner.delete_path(target)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Path no longer exists: {path_str}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=f"Permission denied – cannot delete: {path_str}") from exc
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Delete failed: {exc}") from exc
    return JSONResponse(content={"deleted": path_str})


# ---------------------------------------------------------------------------
# Date Sorter
# ---------------------------------------------------------------------------


@app.post("/api/date-sort/preview")
async def date_sort_preview(body: dict) -> JSONResponse:
    """Preview how files would be sorted into year/month folders."""
    directory = body.get("directory", "")
    root = Path(directory)
    if not root.is_dir():
        raise HTTPException(status_code=422, detail=f"Not a directory: {directory}")

    from mmo_file_tools.tools.date_sorter import DateSorter  # noqa: PLC0415

    sorter = DateSorter()
    try:
        plan = sorter.preview(root)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=f"Permission denied: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Preview failed: {exc}") from exc

    return JSONResponse(content={"plan": plan, "total": len(plan)})


@app.post("/api/date-sort/execute")
async def date_sort_execute(body: dict) -> JSONResponse:
    """Execute a previously previewed date-sort plan (move files)."""
    plan = body.get("plan", [])
    if not plan:
        raise HTTPException(status_code=422, detail="Empty plan – nothing to move.")

    from mmo_file_tools.tools.date_sorter import DateSorter  # noqa: PLC0415

    sorter = DateSorter()
    try:
        moved = sorter.execute(plan)
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=f"Permission denied: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Move failed: {exc}") from exc

    return JSONResponse(content={"moved": len(moved), "details": moved})


# ---------------------------------------------------------------------------
# PDF 2 DCM
# ---------------------------------------------------------------------------

_pdf2dcm_db_url: str | None = None  # let Pdf2Dcm use its default


@app.get("/api/pdf2dcm/tags")
async def pdf2dcm_tags() -> JSONResponse:
    """Return the list of common DICOM tags for the frontend dropdown."""
    from mmo_file_tools.tools.pdf2dcm import Pdf2Dcm  # noqa: PLC0415

    return JSONResponse(content={"tags": Pdf2Dcm.common_tags()})


@app.get("/api/pdf2dcm/configs")
async def pdf2dcm_configs() -> JSONResponse:
    """Return all saved tag configurations."""
    from mmo_file_tools.tools.pdf2dcm import Pdf2Dcm  # noqa: PLC0415

    p = Pdf2Dcm(db_url=_pdf2dcm_db_url)
    return JSONResponse(content={"configs": p.get_configs()})


@app.post("/api/pdf2dcm/configs")
async def pdf2dcm_save_config(body: dict) -> JSONResponse:
    """Save or update a named tag configuration.

    Body: ``{name: str, tags: {keyword: value, ...}}``
    """
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required.")
    tags = body.get("tags") or {}
    from mmo_file_tools.tools.pdf2dcm import Pdf2Dcm  # noqa: PLC0415

    p = Pdf2Dcm(db_url=_pdf2dcm_db_url)
    cfg = p.save_config(name, tags)
    return JSONResponse(content=cfg)


@app.delete("/api/pdf2dcm/configs/{config_id}")
async def pdf2dcm_delete_config(config_id: int) -> JSONResponse:
    """Delete a tag configuration by ID."""
    from mmo_file_tools.tools.pdf2dcm import Pdf2Dcm  # noqa: PLC0415

    p = Pdf2Dcm(db_url=_pdf2dcm_db_url)
    if not p.delete_config(config_id):
        raise HTTPException(status_code=404, detail="Config not found.")
    return JSONResponse(content={"deleted": config_id})

@app.post("/api/pdf2dcm/convert")
async def pdf2dcm_convert(
    pdf: Annotated[UploadFile, File()],
    tags_json: Annotated[str, Form()] = "{}",
    template: Annotated[UploadFile | None, File()] = None,
) -> Response:
    """Convert an uploaded PDF to a DICOM Encapsulated PDF.

    ``tags_json`` is a JSON-encoded dict of keyword→value pairs.
    ``template`` is an optional DICOM file used as a dataset template.
    """
    import tempfile as _tf  # noqa: PLC0415

    # Parse tags
    try:
        tags: dict[str, str] = json.loads(tags_json) if tags_json else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid tags JSON: {exc}") from exc

    # Save PDF to temp file
    pdf_data = await pdf.read()
    if not pdf_data:
        raise HTTPException(status_code=422, detail="Empty PDF file.")

    pdf_suffix = Path(pdf.filename or "upload.pdf").suffix or ".pdf"
    pdf_stem = Path(pdf.filename or "upload").stem
    with _tf.NamedTemporaryFile(delete=False, suffix=pdf_suffix, prefix=f"{pdf_stem}_") as tmp_pdf:
        tmp_pdf.write(pdf_data)
        tmp_pdf_path = Path(tmp_pdf.name)

    # Save template to temp file if provided
    tmp_tmpl_path: Path | None = None
    if template is not None:
        tmpl_data = await template.read()
        if tmpl_data:
            with _tf.NamedTemporaryFile(delete=False, suffix=".dcm", prefix="tmpl_") as tmp_tmpl:
                tmp_tmpl.write(tmpl_data)
                tmp_tmpl_path = Path(tmp_tmpl.name)

    from mmo_file_tools.tools.pdf2dcm import Pdf2Dcm  # noqa: PLC0415

    try:
        dcm_bytes = Pdf2Dcm.convert(
            tmp_pdf_path,
            template_path=tmp_tmpl_path,
            tags=tags,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversion failed: {exc}") from exc
    finally:
        tmp_pdf_path.unlink(missing_ok=True)
        if tmp_tmpl_path:
            tmp_tmpl_path.unlink(missing_ok=True)

    out_name = f"{pdf_stem}.dcm"
    return Response(
        content=dcm_bytes,
        media_type="application/dicom",
        headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
    )


@app.post("/api/pdf2dcm/convert-desktop")
async def pdf2dcm_convert_desktop(body: dict) -> JSONResponse:
    """Convert a PDF on disk to DICOM and save to *output_path*.

    Body: ``{pdf_path, output_path, template_path?, tags?}``
    """
    pdf_path = body.get("pdf_path", "")
    output_path = body.get("output_path", "")
    template_path = body.get("template_path") or None
    tags = body.get("tags") or {}

    if not pdf_path:
        raise HTTPException(status_code=422, detail="pdf_path is required.")
    if not output_path:
        raise HTTPException(status_code=422, detail="output_path is required.")

    from mmo_file_tools.tools.pdf2dcm import Pdf2Dcm  # noqa: PLC0415

    try:
        dcm_bytes = Pdf2Dcm.convert(
            Path(pdf_path),
            template_path=Path(template_path) if template_path else None,
            tags=tags,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversion failed: {exc}") from exc

    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(dcm_bytes)
    return JSONResponse(content={"saved": str(dest)})


# ---------------------------------------------------------------------------
# Duplex Scan Simulator
# ---------------------------------------------------------------------------


@app.post("/api/pdf/duplex-merge")
async def pdf_duplex_merge(
    front: Annotated[UploadFile, File()],
    back: Annotated[UploadFile, File()],
) -> Response:
    """Merge front and back PDF scans into a duplex document (browser upload).

    *front*: PDF with front sides in reading order.
    *back*:  PDF with back sides scanned after flipping the stack (last sheet first).
    """
    from mmo_file_tools.tools.duplex_scan import merge_duplex  # noqa: PLC0415

    front_bytes = await front.read()
    back_bytes = await back.read()

    if not front_bytes:
        raise HTTPException(status_code=422, detail="Front PDF is empty.")
    if not back_bytes:
        raise HTTPException(status_code=422, detail="Back PDF is empty.")

    try:
        merged = merge_duplex(front_bytes, back_bytes)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Merge failed: {exc}") from exc

    return Response(
        content=merged,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=duplex_merged.pdf"},
    )


@app.post("/api/pdf/duplex-merge-by-path")
async def pdf_duplex_merge_by_path(
    front_path: Annotated[str, Form()],
    back_path: Annotated[str, Form()],
    output_path: Annotated[str, Form()] = "",
) -> JSONResponse:
    """Merge front and back PDF scans by filesystem path (desktop mode).

    If *output_path* is empty the result is saved next to *front_path* with a
    ``_duplex`` suffix.
    """
    from mmo_file_tools.tools.duplex_scan import merge_duplex  # noqa: PLC0415

    front = Path(front_path.strip())
    back = Path(back_path.strip())

    if not front.is_file():
        raise HTTPException(status_code=404, detail=f"Front PDF not found: {front_path}")
    if not back.is_file():
        raise HTTPException(status_code=404, detail=f"Back PDF not found: {back_path}")

    try:
        merged = merge_duplex(front.read_bytes(), back.read_bytes())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Merge failed: {exc}") from exc

    dest = Path(output_path.strip()) if output_path.strip() else front.with_stem(front.stem + "_duplex")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(merged)
    return JSONResponse(content={"saved": str(dest)})


# ---------------------------------------------------------------------------
# Image Shrinker
# ---------------------------------------------------------------------------


@app.post("/api/image/shrink-by-path")
async def image_shrink_by_path(
    file_paths: Annotated[str, Form()],
    scale_percent: Annotated[int, Form()] = 0,
    max_width: Annotated[int, Form()] = 0,
    max_height: Annotated[int, Form()] = 0,
    replace: Annotated[bool, Form()] = False,
) -> JSONResponse:
    """Shrink images by filesystem path (desktop mode)."""
    from mmo_file_tools.tools.image_shrinker import ImageShrinker  # noqa: PLC0415

    paths = [Path(p.strip()) for p in file_paths.strip().split("\n") if p.strip()]
    if not paths:
        raise HTTPException(status_code=422, detail="At least one file is required.")
    try:
        results = ImageShrinker.shrink(
            paths,
            scale_percent=scale_percent,
            max_width=max_width,
            max_height=max_height,
            replace=replace,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Shrink failed: {exc}") from exc
    return JSONResponse(content={"results": results, "count": len(results)})


@app.post("/api/image/shrink")
async def image_shrink(
    files: Annotated[list[UploadFile], File()],
    scale_percent: Annotated[int, Form()] = 0,
    max_width: Annotated[int, Form()] = 0,
    max_height: Annotated[int, Form()] = 0,
) -> Response:
    """Shrink uploaded images, return a zip of the resized files."""
    import tempfile
    from mmo_file_tools.tools.image_shrinker import ImageShrinker  # noqa: PLC0415

    tmp_paths: list[Path] = []
    try:
        for upload in files:
            suffix = Path(upload.filename or "image.jpg").suffix or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                content = await upload.read()
                tmp.write(content)
                tmp_paths.append(Path(tmp.name))

        try:
            shrink_results = ImageShrinker.shrink(
                tmp_paths,
                scale_percent=scale_percent,
                max_width=max_width,
                max_height=max_height,
                replace=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Shrink failed: {exc}") from exc

        processed = {r["path"] for r in shrink_results}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for orig_upload, tmp_p in zip(files, tmp_paths):
                if str(tmp_p) in processed:
                    zf.writestr(orig_upload.filename or tmp_p.name, tmp_p.read_bytes())
        buf.seek(0)
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=shrinked.zip"},
        )
    finally:
        for p in tmp_paths:
            p.unlink(missing_ok=True)


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
async def dialog_files(multiple: bool = True, file_types: str = "") -> JSONResponse:
    """Open a native file-open dialog (desktop / pywebview mode only).

    *file_types* is a pipe-separated list of filter strings.
    If empty, defaults to PDF + Image + All.
    """
    if _webview_window is None:
        raise HTTPException(status_code=503, detail="Not running in desktop mode.")
    import webview as _wv  # noqa: PLC0415

    if file_types:
        ft = tuple(file_types.split("|"))
    else:
        ft = (
            "Supported Files (*.pdf;*.jpg;*.jpeg;*.png;*.bmp;*.tiff;*.tif;*.webp)",
            "PDF Files (*.pdf)",
            "Image Files (*.jpg;*.jpeg;*.png;*.bmp;*.tiff;*.tif;*.webp)",
            "All Files (*.*)",
        )

    result = _webview_window.create_file_dialog(
        _wv.FileDialog.OPEN,
        allow_multiple=multiple,
        file_types=ft,
    )
    return JSONResponse(content={"files": list(result) if result else []})


@app.get("/api/dialog/save")
async def dialog_save(
    filename: str = "merged.pdf",
    file_types: str = "",
) -> JSONResponse:
    """Open a native save-file dialog (desktop / pywebview mode only).

    *file_types* is a pipe-separated list of filter strings.
    If empty, defaults to PDF + All.
    """
    if _webview_window is None:
        raise HTTPException(status_code=503, detail="Not running in desktop mode.")
    import webview as _wv  # noqa: PLC0415

    if file_types:
        ft = tuple(file_types.split("|"))
    else:
        ft = ("PDF Files (*.pdf)", "All Files (*.*)")

    result = _webview_window.create_file_dialog(
        _wv.FileDialog.SAVE,
        save_filename=filename,
        file_types=ft,
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
    if fpath.is_file() and fpath.suffix.lower() not in _SAFE_OPEN_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"File type not supported: {fpath.suffix or '(no extension)'}",
        )

    if sys.platform == "win32":
        os.startfile(str(fpath))  # noqa: S606
    elif sys.platform == "darwin":  # pragma: no cover
        subprocess.Popen(["open", str(fpath)])  # noqa: S603
    else:  # pragma: no cover
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

    result = _webview_window.create_file_dialog(_wv.FileDialog.FOLDER, **kwargs)
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
    from mmo_file_tools.diagnostics import Diagnostics  # noqa: PLC0415

    Diagnostics.breadcrumb(f"web mode starting on {host}:{port}")
    Diagnostics.mark_started()
    uvicorn.run(app, host=host, port=port)
