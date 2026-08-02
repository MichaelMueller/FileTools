"""Duplex scan combiner – merges front and back PDF scans into one document."""

from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter


def merge_duplex(front: bytes, back: bytes) -> bytes:
    """Interleave front and back PDF scans into a correctly ordered document.

    *front* contains the front sides scanned in reading order (sheet 1, 2, … N).
    *back* contains the back sides scanned after flipping the stack: because the
    last sheet is now on top, the back PDF page order is back-N, back-(N-1), …,
    back-1.

    The result interleaves them as F1, B1, F2, B2, … FN, BN.
    If *front* has more pages than *back* (odd-page document) the trailing
    front pages are appended without a matching back.
    """
    front_reader = PdfReader(io.BytesIO(front))
    back_reader = PdfReader(io.BytesIO(back))

    n_front = len(front_reader.pages)
    n_back = len(back_reader.pages)

    writer = PdfWriter()
    for i in range(n_front):
        writer.add_page(front_reader.pages[i])
        back_idx = n_back - 1 - i
        if back_idx >= 0:
            writer.add_page(back_reader.pages[back_idx])

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
