"""Batch image resizer that shrinks images in-place."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps


_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


class ImageShrinker:
    """Resize images by percentage, max width, or max height."""

    @staticmethod
    def shrink(
        paths: list[Path],
        *,
        scale_percent: int = 0,
        max_width: int = 0,
        max_height: int = 0,
        replace: bool = False,
    ) -> list[dict]:
        """Shrink the given images.

        When *replace* is ``True`` the originals are overwritten; otherwise
        the resized file is written next to the original with a ``_shrunk``
        suffix (e.g. ``photo_shrunk.jpg``).

        Exactly one of *scale_percent*, *max_width*, or *max_height* must be
        set to a positive value.

        Returns a list of dicts with keys ``path``, ``original_size``,
        ``new_size`` for each successfully processed file.
        """
        if sum(1 for v in (scale_percent, max_width, max_height) if v > 0) != 1:
            msg = "Exactly one of scale_percent, max_width, or max_height must be set."
            raise ValueError(msg)

        results: list[dict] = []
        for p in paths:
            p = Path(p)
            if not p.is_file():
                continue
            if p.suffix.lower() not in _IMAGE_EXTENSIONS:
                continue

            img = Image.open(p)
            img = ImageOps.exif_transpose(img)
            orig_w, orig_h = img.size

            if scale_percent > 0:
                new_w = max(1, int(orig_w * scale_percent / 100))
                new_h = max(1, int(orig_h * scale_percent / 100))
            elif max_width > 0:
                if orig_w <= max_width:
                    img.close()
                    continue
                ratio = max_width / orig_w
                new_w = max_width
                new_h = max(1, int(orig_h * ratio))
            else:
                if orig_h <= max_height:
                    img.close()
                    continue
                ratio = max_height / orig_h
                new_w = max(1, int(orig_w * ratio))
                new_h = max_height

            resized = img.resize((new_w, new_h), Image.LANCZOS)
            img.close()

            # Preserve original format
            fmt = p.suffix.lower()
            save_kwargs: dict = {}
            if fmt in (".jpg", ".jpeg"):
                save_kwargs["quality"] = 90
                if resized.mode == "RGBA":
                    resized = resized.convert("RGB")
            elif fmt == ".png":
                save_kwargs["optimize"] = True
            elif fmt == ".webp":
                save_kwargs["quality"] = 90

            if replace:
                out_path = p
            else:
                out_path = p.with_stem(p.stem + "_shrunk")

            resized.save(out_path, **save_kwargs)
            resized.close()

            results.append({
                "path": str(out_path),
                "original_size": (orig_w, orig_h),
                "new_size": (new_w, new_h),
            })

        return results
