"""Tests for file_tools.tools.duplex_scan."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader, PdfWriter

from file_tools.tools.duplex_scan import merge_duplex


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pdf(n_pages: int) -> bytes:
    """Return a minimal PDF with *n_pages* blank pages."""
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _page_count(pdf_bytes: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


def _make_tagged_pdf(n: int, start_width: int = 100) -> bytes:
    """Return a PDF with *n* pages; page i has mediabox width *start_width* + i."""
    writer = PdfWriter()
    for i in range(n):
        writer.add_blank_page(width=start_width + i, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _page_widths(pdf_bytes: bytes) -> list[int]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [int(p.mediabox.width) for p in reader.pages]


# ---------------------------------------------------------------------------
# Basic page-count checks
# ---------------------------------------------------------------------------


def test_even_pages_total_count() -> None:
    """4 front + 4 back -> 8 pages."""
    result = merge_duplex(_make_pdf(4), _make_pdf(4))
    assert _page_count(result) == 8


def test_odd_pages_front_more() -> None:
    """3 front + 2 back -> 5 pages (last front has no back)."""
    result = merge_duplex(_make_pdf(3), _make_pdf(2))
    assert _page_count(result) == 5


def test_single_front_no_back() -> None:
    """1 front + 0 back -> 1 page."""
    result = merge_duplex(_make_pdf(1), _make_pdf(0))
    assert _page_count(result) == 1


def test_single_front_single_back() -> None:
    """1 front + 1 back -> 2 pages."""
    result = merge_duplex(_make_pdf(1), _make_pdf(1))
    assert _page_count(result) == 2


def test_more_back_than_front() -> None:
    """2 front + 4 back -> 4 pages (extra back pages are ignored)."""
    result = merge_duplex(_make_pdf(2), _make_pdf(4))
    assert _page_count(result) == 4


# ---------------------------------------------------------------------------
# Page order verification
# ---------------------------------------------------------------------------


def test_page_order_even() -> None:
    """Verify interleaving order for 3 front + 3 back sheets.

    Front PDF pages: widths 100, 101, 102  (F1, F2, F3 in reading order).
    Back  PDF pages: widths 200, 201, 202  (B3, B2, B1 -- last sheet first
                                            because the stack was flipped).

    Expected merged order: F1(100), B1(202), F2(101), B2(201), F3(102), B3(200).
    """
    front = _make_tagged_pdf(3, start_width=100)  # widths: 100, 101, 102
    back  = _make_tagged_pdf(3, start_width=200)  # widths: 200, 201, 202 -> reversed: B1=202, B2=201, B3=200
    result_widths = _page_widths(merge_duplex(front, back))
    assert result_widths == [100, 202, 101, 201, 102, 200]


def test_page_order_odd_last_front_only() -> None:
    """3 front + 2 back: last front page has no back -- appears alone at end.

    Front: widths 100, 101, 102  (F1, F2, F3)
    Back:  widths 200, 201       (B2, B1 -- reversed: B1=201, B2=200)
    Merged: F1(100), B1(201), F2(101), B2(200), F3(102)
    """
    front = _make_tagged_pdf(3, start_width=100)
    back  = _make_tagged_pdf(2, start_width=200)
    result_widths = _page_widths(merge_duplex(front, back))
    assert result_widths == [100, 201, 101, 200, 102]


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


def test_returns_valid_pdf_bytes() -> None:
    result = merge_duplex(_make_pdf(2), _make_pdf(2))
    assert isinstance(result, bytes)
    assert result[:4] == b"%PDF"
