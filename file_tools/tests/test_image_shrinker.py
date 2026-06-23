"""Tests for file_tools.tools.image_shrinker."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from file_tools.tools.image_shrinker import ImageShrinker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_image(path: Path, width: int = 800, height: int = 600, fmt: str = "JPEG") -> Path:
    """Create a minimal test image file."""
    mode = "RGBA" if fmt == "PNG" else "RGB"
    img = Image.new(mode, (width, height), (255, 0, 0))
    kwargs: dict = {}
    if fmt == "JPEG":
        kwargs["quality"] = 95
        if img.mode == "RGBA":
            img = img.convert("RGB")
    elif fmt == "PNG":
        kwargs["optimize"] = True
    elif fmt == "WEBP":
        kwargs["quality"] = 95
    img.save(path, format=fmt, **kwargs)
    img.close()
    return path


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_raises_when_no_option_set() -> None:
    with pytest.raises(ValueError, match="Exactly one"):
        ImageShrinker.shrink([])


def test_raises_when_multiple_options_set() -> None:
    with pytest.raises(ValueError, match="Exactly one"):
        ImageShrinker.shrink([], scale_percent=50, max_width=100)


def test_raises_when_all_options_set() -> None:
    with pytest.raises(ValueError, match="Exactly one"):
        ImageShrinker.shrink([], scale_percent=50, max_width=100, max_height=100)


def test_raises_when_scale_percent_exceeds_100() -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        ImageShrinker.shrink([], scale_percent=150)


def test_skip_when_scale_percent_is_100(tmp_path: Path) -> None:
    p = _make_image(tmp_path / "photo.jpg", 1000, 800)
    results = ImageShrinker.shrink([p], scale_percent=100)
    assert results == []
    # Original must be untouched
    img = Image.open(p)
    assert img.size == (1000, 800)
    img.close()


# ---------------------------------------------------------------------------
# Scale by percentage
# ---------------------------------------------------------------------------


def test_shrink_by_percent_suffix(tmp_path: Path) -> None:
    p = _make_image(tmp_path / "photo.jpg", 1000, 800)
    results = ImageShrinker.shrink([p], scale_percent=50)
    assert len(results) == 1
    assert results[0]["original_size"] == (1000, 800)
    assert results[0]["new_size"] == (500, 400)
    # Default (replace=False) writes a _shrunk file, original untouched
    shrunk = tmp_path / "photo_shrunk.jpg"
    assert shrunk.is_file()
    img = Image.open(shrunk)
    assert img.size == (500, 400)
    img.close()
    orig = Image.open(p)
    assert orig.size == (1000, 800)
    orig.close()


def test_shrink_by_percent_replace(tmp_path: Path) -> None:
    p = _make_image(tmp_path / "photo.jpg", 1000, 800)
    results = ImageShrinker.shrink([p], scale_percent=50, replace=True)
    assert len(results) == 1
    img = Image.open(p)
    assert img.size == (500, 400)
    img.close()


def test_shrink_by_percent_multiple(tmp_path: Path) -> None:
    p1 = _make_image(tmp_path / "a.jpg", 200, 100)
    p2 = _make_image(tmp_path / "b.png", 400, 300, fmt="PNG")
    results = ImageShrinker.shrink([p1, p2], scale_percent=25)
    assert len(results) == 2
    assert results[0]["new_size"] == (50, 25)
    assert results[1]["new_size"] == (100, 75)


# ---------------------------------------------------------------------------
# Max width
# ---------------------------------------------------------------------------


def test_shrink_by_max_width(tmp_path: Path) -> None:
    p = _make_image(tmp_path / "wide.jpg", 2000, 1000)
    results = ImageShrinker.shrink([p], max_width=1000)
    assert len(results) == 1
    assert results[0]["new_size"] == (1000, 500)


def test_skip_when_already_within_max_width(tmp_path: Path) -> None:
    p = _make_image(tmp_path / "small.jpg", 500, 300)
    results = ImageShrinker.shrink([p], max_width=800)
    assert len(results) == 0
    # File should be unchanged
    img = Image.open(p)
    assert img.size == (500, 300)
    img.close()


# ---------------------------------------------------------------------------
# Max height
# ---------------------------------------------------------------------------


def test_shrink_by_max_height(tmp_path: Path) -> None:
    p = _make_image(tmp_path / "tall.jpg", 1000, 2000)
    results = ImageShrinker.shrink([p], max_height=1000)
    assert len(results) == 1
    assert results[0]["new_size"] == (500, 1000)


def test_skip_when_already_within_max_height(tmp_path: Path) -> None:
    p = _make_image(tmp_path / "short.jpg", 300, 500)
    results = ImageShrinker.shrink([p], max_height=600)
    assert len(results) == 0


# ---------------------------------------------------------------------------
# Non-image / missing files
# ---------------------------------------------------------------------------


def test_skip_non_image_extension(tmp_path: Path) -> None:
    p = tmp_path / "readme.txt"
    p.write_text("hello")
    results = ImageShrinker.shrink([p], scale_percent=50)
    assert len(results) == 0


def test_skip_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "gone.jpg"
    results = ImageShrinker.shrink([p], scale_percent=50)
    assert len(results) == 0


# ---------------------------------------------------------------------------
# Format-specific behaviour
# ---------------------------------------------------------------------------


def test_png_format_preserved(tmp_path: Path) -> None:
    p = _make_image(tmp_path / "icon.png", 400, 400, fmt="PNG")
    results = ImageShrinker.shrink([p], scale_percent=50)
    assert len(results) == 1
    shrunk = tmp_path / "icon_shrunk.png"
    assert shrunk.is_file()
    img = Image.open(shrunk)
    assert img.size == (200, 200)
    img.close()


def test_bmp_format(tmp_path: Path) -> None:
    p = _make_image(tmp_path / "pic.bmp", 200, 100, fmt="BMP")
    results = ImageShrinker.shrink([p], scale_percent=50)
    assert len(results) == 1
    assert results[0]["new_size"] == (100, 50)


def test_webp_format(tmp_path: Path) -> None:
    p = _make_image(tmp_path / "pic.webp", 600, 400, fmt="WEBP")
    results = ImageShrinker.shrink([p], scale_percent=50)
    assert len(results) == 1
    assert results[0]["new_size"] == (300, 200)


def test_tiff_format(tmp_path: Path) -> None:
    p = _make_image(tmp_path / "pic.tiff", 600, 400, fmt="TIFF")
    results = ImageShrinker.shrink([p], scale_percent=50)
    assert len(results) == 1
    assert results[0]["new_size"] == (300, 200)


def test_jpeg_rgba_converts_to_rgb(tmp_path: Path) -> None:
    """RGBA images saved as JPEG must be converted to RGB first."""
    p = tmp_path / "rgba.jpg"
    img = Image.new("RGBA", (400, 300), (255, 0, 0, 128))
    img.save(p, format="PNG")          # save as PNG so Pillow can open it
    img.close()
    # Rename to .jpg so ImageShrinker treats it as JPEG output
    jpg_path = tmp_path / "rgba_img.jpg"
    p.rename(jpg_path)
    # Re-create as actual RGBA PNG with .jpg extension … instead just make it properly:
    p = tmp_path / "test_rgba.jpg"
    # Create a PNG with RGBA, then rename to .jpg to trigger the RGBA→RGB path
    png_tmp = tmp_path / "test_rgba.png"
    img = Image.new("RGBA", (400, 300), (255, 0, 0, 128))
    img.save(png_tmp, format="PNG")
    img.close()
    png_tmp.rename(p)

    results = ImageShrinker.shrink([p], scale_percent=50)
    assert len(results) == 1
    assert results[0]["new_size"] == (200, 150)
    # Verify _shrunk file saved successfully
    shrunk = tmp_path / "test_rgba_shrunk.jpg"
    assert shrunk.is_file()
    saved = Image.open(shrunk)
    assert saved.mode == "RGB"
    saved.close()


def test_empty_file_list() -> None:
    results = ImageShrinker.shrink([], scale_percent=50)
    assert results == []


def test_path_as_string(tmp_path: Path) -> None:
    """Paths passed as strings should also work."""
    p = _make_image(tmp_path / "str_path.jpg", 600, 400)
    results = ImageShrinker.shrink([str(p)], scale_percent=50)
    assert len(results) == 1
    assert results[0]["new_size"] == (300, 200)
