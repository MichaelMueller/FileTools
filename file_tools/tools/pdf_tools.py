"""PDF merge and split utilities."""

from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def merge_pdfs(input_paths: list[Path]) -> bytes:
    """Merge multiple PDF files into a single PDF and return its bytes."""
    writer = PdfWriter()
    for path in input_paths:
        reader = PdfReader(str(path))
        for page in reader.pages:
            writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def split_pdf(input_path: Path, page_ranges: list[tuple[int, int]]) -> list[bytes]:
    """Split a PDF into multiple PDFs according to the given page ranges.

    Each tuple in *page_ranges* is (start, end) using 1-based inclusive page
    numbers.  Pages out of range are silently clamped.
    """
    reader = PdfReader(str(input_path))
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
