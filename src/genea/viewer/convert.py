"""TIFF scans as JPG for the browser, converted on first view and cached beside the index."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from PIL import Image, ImageOps

from genea.kb import KnowledgeBase

MAX_SIDE = 4000
QUALITY = 88


class ConvertError(Exception):
    pass


def tiff_page_count(path: Path) -> int:
    try:
        with Image.open(path) as image:
            return getattr(image, "n_frames", 1)
    except (Image.DecompressionBombError, OSError, EOFError, ValueError) as exc:
        raise ConvertError(str(exc)) from exc


def jpeg_for(kb: KnowledgeBase, rel: str, page: int = 0) -> bytes:
    """The page of this KB file as JPEG bytes (EXIF-rotated, longer side capped); cached on disk."""
    try:
        source = kb.resolve_inside(rel)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    except (ValueError, OSError) as exc:
        raise ConvertError(str(exc)) from exc
    cache = kb.index_path.parent / "viewer-cache" / f"{digest}-{page}.jpg"
    try:
        return cache.read_bytes()
    except OSError:
        pass
    try:
        with Image.open(source) as image:
            if page < 0 or page >= getattr(image, "n_frames", 1):
                raise ConvertError(f"page {page} is out of range")
            image.seek(page)
            frame = ImageOps.exif_transpose(image)
            frame = frame.convert("RGB")
            frame.thumbnail((MAX_SIDE, MAX_SIDE))
            cache.parent.mkdir(parents=True, exist_ok=True)
            temp = cache.with_name(f"{cache.name}.{os.getpid()}.tmp")
            frame.save(temp, "JPEG", quality=QUALITY)
    except (Image.DecompressionBombError, OSError, EOFError, ValueError) as exc:
        raise ConvertError(str(exc)) from exc
    os.replace(temp, cache)
    return cache.read_bytes()
