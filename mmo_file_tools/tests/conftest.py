"""Shared test fixtures for MMO FileTools tests."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pypdf import PdfWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pdf(page_count: int = 1) -> bytes:
    """Return a minimal valid PDF with *page_count* blank pages."""
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_pdf(tmp_path: Path) -> Path:
    """A temporary single-page PDF file."""
    p = tmp_path / "test.pdf"
    p.write_bytes(_make_pdf(1))
    return p


@pytest.fixture()
def tmp_pdf3(tmp_path: Path) -> Path:
    """A temporary 3-page PDF file."""
    p = tmp_path / "test3.pdf"
    p.write_bytes(_make_pdf(3))
    return p


@pytest.fixture()
def two_pdfs(tmp_path: Path) -> list[Path]:
    """Two temporary PDF files."""
    paths = []
    for i in range(2):
        p = tmp_path / f"file{i}.pdf"
        p.write_bytes(_make_pdf(i + 1))
        paths.append(p)
    return paths


@pytest.fixture()
def src_dir(tmp_path: Path) -> Path:
    """Source directory with a few files."""
    d = tmp_path / "source"
    d.mkdir()
    (d / "a.txt").write_text("hello")
    (d / "b.txt").write_text("world")
    sub = d / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("deep")
    return d


@pytest.fixture()
def tgt_dir(tmp_path: Path) -> Path:
    """Empty target directory."""
    d = tmp_path / "target"
    d.mkdir()
    return d
