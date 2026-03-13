"""Tests for file_tools.tools.pdf_tools."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from file_tools.tools.pdf_tools import (
    image_to_pdf,
    merge_pdfs,
    parse_page_ranges,
    split_pdf,
    split_pdf_to_images,
)


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


def test_merge_pdfs_no_open_handles(two_pdfs: list[Path]) -> None:
    """Source files must not be locked after merge."""
    merge_pdfs(two_pdfs)
    for p in two_pdfs:
        p.unlink()
        assert not p.exists()


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


# ---------------------------------------------------------------------------
# image_to_pdf
# ---------------------------------------------------------------------------


def _make_image(tmp_path: Path, name: str = "test.png", mode: str = "RGB",
                size: tuple[int, int] = (200, 100)) -> Path:
    """Create a simple test image and return its path."""
    img = Image.new(mode, size, color=(255, 0, 0))
    p = tmp_path / name
    img.save(p)
    return p


def test_image_to_pdf_basic(tmp_path: Path) -> None:
    img_path = _make_image(tmp_path)
    result = image_to_pdf(img_path)
    assert result[:4] == b"%PDF"
    r = PdfReader(io.BytesIO(result))
    assert len(r.pages) == 1


def test_image_to_pdf_rgba(tmp_path: Path) -> None:
    """RGBA images get converted to RGB."""
    img_path = _make_image(tmp_path, name="rgba.png", mode="RGBA")
    result = image_to_pdf(img_path)
    assert result[:4] == b"%PDF"


def test_image_to_pdf_palette(tmp_path: Path) -> None:
    """Palette-mode images get converted to RGB."""
    img = Image.new("P", (100, 100))
    p = tmp_path / "palette.png"
    img.save(p)
    result = image_to_pdf(p)
    assert result[:4] == b"%PDF"


def test_image_to_pdf_resize(tmp_path: Path) -> None:
    """max_side_px shrinks large images."""
    img_path = _make_image(tmp_path, size=(1000, 500))
    result = image_to_pdf(img_path, max_side_px=200)
    assert result[:4] == b"%PDF"


def test_image_to_pdf_no_resize_when_small(tmp_path: Path) -> None:
    """max_side_px does nothing if already under the limit."""
    img_path = _make_image(tmp_path, size=(100, 50))
    result = image_to_pdf(img_path, max_side_px=200)
    assert result[:4] == b"%PDF"


def test_image_to_pdf_margin(tmp_path: Path) -> None:
    """margin_mm adds extra space around the image."""
    img_path = _make_image(tmp_path)
    no_margin = image_to_pdf(img_path, margin_mm=0)
    with_margin = image_to_pdf(img_path, margin_mm=10.0)
    # The margined PDF should be larger (page is bigger)
    r_no = PdfReader(io.BytesIO(no_margin))
    r_mg = PdfReader(io.BytesIO(with_margin))
    w_no = float(r_no.pages[0].mediabox.width)
    w_mg = float(r_mg.pages[0].mediabox.width)
    assert w_mg > w_no


# ---------------------------------------------------------------------------
# merge_pdfs with images
# ---------------------------------------------------------------------------


def test_merge_pdfs_with_image(tmp_path: Path, tmp_pdf: Path) -> None:
    """Merging a PDF and an image file works."""
    img_path = _make_image(tmp_path, name="photo.jpg")
    result = merge_pdfs([tmp_pdf, img_path])
    r = PdfReader(io.BytesIO(result))
    assert len(r.pages) == 2


# ---------------------------------------------------------------------------
# split_pdf_to_images
# ---------------------------------------------------------------------------


def test_split_pdf_to_images_single_pages(tmp_pdf3: Path) -> None:
    """Each single-page range produces a JPEG."""
    parts = split_pdf_to_images(tmp_pdf3, [(1, 1), (2, 2), (3, 3)])
    assert len(parts) == 3
    for part in parts:
        # JPEG files start with 0xFFD8
        assert part[:2] == b"\xff\xd8"


def test_split_pdf_to_images_multi_page_range(tmp_pdf3: Path) -> None:
    """A multi-page range produces a single stacked JPEG."""
    parts = split_pdf_to_images(tmp_pdf3, [(1, 3)])
    assert len(parts) == 1
    assert parts[0][:2] == b"\xff\xd8"
    # Stacked image should be taller than a single page
    single = split_pdf_to_images(tmp_pdf3, [(1, 1)])
    img_single = Image.open(io.BytesIO(single[0]))
    img_multi = Image.open(io.BytesIO(parts[0]))
    assert img_multi.height > img_single.height


def test_split_pdf_to_images_empty_range(tmp_pdf3: Path) -> None:
    """Out-of-range pages produce empty bytes."""
    parts = split_pdf_to_images(tmp_pdf3, [(99, 100)])
    assert len(parts) == 1
    assert parts[0] == b""


def test_split_pdf_to_images_custom_dpi(tmp_pdf3: Path) -> None:
    """DPI parameter affects image resolution."""
    parts_low = split_pdf_to_images(tmp_pdf3, [(1, 1)], dpi=72)
    parts_high = split_pdf_to_images(tmp_pdf3, [(1, 1)], dpi=300)
    img_low = Image.open(io.BytesIO(parts_low[0]))
    img_high = Image.open(io.BytesIO(parts_high[0]))
    assert img_high.width > img_low.width
