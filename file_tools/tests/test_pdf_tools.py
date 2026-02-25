"""Tests for file_tools.tools.pdf_tools."""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader

from file_tools.tools.pdf_tools import merge_pdfs, parse_page_ranges, split_pdf


# ---------------------------------------------------------------------------
# merge_pdfs
# ---------------------------------------------------------------------------


def test_merge_pdfs_produces_valid_pdf(two_pdfs: list[Path]) -> None:
    result = merge_pdfs(two_pdfs)
    assert result[:4] == b"%PDF"
    merged_path = two_pdfs[0].parent / "merged.pdf"
    merged_path.write_bytes(result)
    r = PdfReader(str(merged_path))
    # two_pdfs[0] has 1 page, two_pdfs[1] has 2 pages → 3 total
    assert len(r.pages) == 3


def test_merge_pdfs_single_file(tmp_pdf: Path) -> None:
    result = merge_pdfs([tmp_pdf])
    merged_path = tmp_pdf.parent / "single_merged.pdf"
    merged_path.write_bytes(result)
    r = PdfReader(str(merged_path))
    assert len(r.pages) == 1


# ---------------------------------------------------------------------------
# split_pdf
# ---------------------------------------------------------------------------


def test_split_pdf_single_range(tmp_pdf3: Path) -> None:
    parts = split_pdf(tmp_pdf3, [(1, 2)])
    assert len(parts) == 1
    p = tmp_pdf3.parent / "part.pdf"
    p.write_bytes(parts[0])
    r = PdfReader(str(p))
    assert len(r.pages) == 2


def test_split_pdf_multiple_ranges(tmp_pdf3: Path) -> None:
    parts = split_pdf(tmp_pdf3, [(1, 1), (2, 3)])
    assert len(parts) == 2
    for idx, part in enumerate(parts):
        p = tmp_pdf3.parent / f"p{idx}.pdf"
        p.write_bytes(part)
        r = PdfReader(str(p))
        expected = 1 if idx == 0 else 2
        assert len(r.pages) == expected


def test_split_pdf_range_clamped(tmp_pdf3: Path) -> None:
    """Ranges exceeding total pages are clamped without error."""
    parts = split_pdf(tmp_pdf3, [(2, 99)])
    assert len(parts) == 1
    p = tmp_pdf3.parent / "clamped.pdf"
    p.write_bytes(parts[0])
    r = PdfReader(str(p))
    assert len(r.pages) == 2  # pages 2 and 3


def test_split_pdf_empty_ranges(tmp_pdf3: Path) -> None:
    parts = split_pdf(tmp_pdf3, [])
    assert parts == []


# ---------------------------------------------------------------------------
# parse_page_ranges
# ---------------------------------------------------------------------------


def test_parse_ranges_single_pages() -> None:
    assert parse_page_ranges("1,3,5", 5) == [(1, 1), (3, 3), (5, 5)]


def test_parse_ranges_ranges() -> None:
    assert parse_page_ranges("1-3,4-6", 6) == [(1, 3), (4, 6)]


def test_parse_ranges_mixed() -> None:
    assert parse_page_ranges("1-3,5,7-9", 9) == [(1, 3), (5, 5), (7, 9)]


def test_parse_ranges_with_spaces() -> None:
    assert parse_page_ranges(" 1 - 3 , 5 ", 5) == [(1, 3), (5, 5)]


def test_parse_ranges_empty_string() -> None:
    assert parse_page_ranges("", 5) == []


def test_parse_ranges_invalid_raises() -> None:
    with pytest.raises(ValueError):
        parse_page_ranges("abc", 5)
