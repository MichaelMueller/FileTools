"""PDF merge and split utilities."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}

# 1 inch = 25.4 mm, 1 PDF point = 1/72 inch
_MM_TO_PT = 72.0 / 25.4


def image_to_pdf(
    image_path: Path,
    *,
    dpi: int = 150,
    max_side_px: int = 0,
    margin_mm: float = 0.0,
) -> bytes:
    """Convert a single image file to a one-page PDF and return its bytes.

    Parameters
    ----------
    dpi:
        Resolution to embed the image at (affects print quality).
    max_side_px:
        If > 0, shrink the image so that neither width nor height exceeds
        this value (in pixels), preserving aspect ratio.
    margin_mm:
        Margin around the image in millimetres.
    """
    img = Image.open(io.BytesIO(image_path.read_bytes()))
    # Apply EXIF orientation so landscape photos stay landscape
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # Shrink if requested
    if max_side_px > 0:
        w, h = img.size
        if w > max_side_px or h > max_side_px:
            ratio = min(max_side_px / w, max_side_px / h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    # Save to PDF at the requested DPI
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=dpi)
    pdf_bytes = buf.getvalue()

    # Apply margin via pypdf (shift media-box)
    if margin_mm > 0:
        margin_pt = margin_mm * _MM_TO_PT
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter()
        for page in reader.pages:
            mb = page.mediabox
            new_w = float(mb.width) + 2 * margin_pt
            new_h = float(mb.height) + 2 * margin_pt
            page.mediabox = RectangleObject([0, 0, new_w, new_h])
            page.add_transformation([1, 0, 0, 1, margin_pt, margin_pt])
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        pdf_bytes = out.getvalue()

    return pdf_bytes


def merge_pdfs(
    input_paths: list[Path],
    *,
    dpi: int = 150,
    max_side_px: int = 0,
    margin_mm: float = 0.0,
) -> bytes:
    """Merge multiple PDF and image files into a single PDF and return its bytes.

    Image files (jpg, png, bmp, tiff, webp) are automatically converted
    to single-page PDFs before merging.  *dpi*, *max_side_px* and
    *margin_mm* only affect image inputs.
    """
    writer = PdfWriter()
    for path in input_paths:
        if path.suffix.lower() in _IMAGE_EXTENSIONS:
            pdf_bytes = image_to_pdf(
                path, dpi=dpi, max_side_px=max_side_px, margin_mm=margin_mm,
            )
            reader = PdfReader(io.BytesIO(pdf_bytes))
        else:
            reader = PdfReader(io.BytesIO(path.read_bytes()))
        for page in reader.pages:
            writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def split_pdf(input_source: Path | io.BytesIO, page_ranges: list[tuple[int, int]]) -> list[bytes]:
    """Split a PDF into multiple PDFs according to the given page ranges.

    *input_source* can be a file ``Path`` or an in-memory ``BytesIO``.
    Each tuple in *page_ranges* is (start, end) using 1-based inclusive page
    numbers.  Pages out of range are silently clamped.
    """
    if isinstance(input_source, io.BytesIO):
        reader = PdfReader(input_source)
    else:
        reader = PdfReader(io.BytesIO(input_source.read_bytes()))
    total = len(reader.pages)
    results: list[bytes] = []
    for start, end in page_ranges:
        writer = PdfWriter()
        for i in range(max(0, start - 1), min(end, total)):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        results.append(buf.getvalue())
    return results


def split_pdf_to_images(
    input_path: Path,
    page_ranges: list[tuple[int, int]],
    *,
    dpi: int = 150,
) -> list[bytes]:
    """Render each page range of a PDF to JPEG bytes using pypdfium2.

    Falls back to a simple PDF→Pillow pipeline via pdf2image or
    pypdfium2.  Each range becomes one JPEG (for single-page ranges)
    or is skipped if the range covers multiple pages (only the first
    page of each range is rendered).
    """
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        msg = "pypdfium2 is required for JPEG output. Install it with:  pip install pypdfium2"
        raise ImportError(msg) from exc

    pdf = pdfium.PdfDocument(input_path.read_bytes())
    total = len(pdf)
    results: list[bytes] = []
    for start, end in page_ranges:
        # Render every page in the range and stack them vertically
        images: list[Image.Image] = []
        for i in range(max(0, start - 1), min(end, total)):
            page = pdf[i]
            scale = dpi / 72.0
            bitmap = page.render(scale=scale)
            pil_img = bitmap.to_pil()
            images.append(pil_img)

        if not images:
            results.append(b"")
            continue

        # Combine into one image (vertical stack)
        if len(images) == 1:
            combined = images[0]
        else:
            total_w = max(im.width for im in images)
            total_h = sum(im.height for im in images)
            combined = Image.new("RGB", (total_w, total_h), (255, 255, 255))
            y = 0
            for im in images:
                combined.paste(im, (0, y))
                y += im.height

        buf = io.BytesIO()
        combined.save(buf, format="JPEG", quality=92)
        results.append(buf.getvalue())
    return results


def parse_page_ranges(ranges_str: str, total_pages: int) -> list[tuple[int, int]]:
    """Parse a comma-separated page-range string such as ``'1-3,5,7-9'``.

    Single page numbers are returned as ``(n, n)`` tuples.  Raises
    ``ValueError`` for malformed input.
    """
    ranges: list[tuple[int, int]] = []
    for part in ranges_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            ranges.append((int(left.strip()), int(right.strip())))
        else:
            page = int(part)
            ranges.append((page, page))
    return ranges
