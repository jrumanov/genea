"""TIFF scans as JPG for the browser, converted on first view and cached beside the index."""

from __future__ import annotations

import hashlib
import os
import threading
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
    except Exception as exc:
        raise ConvertError(str(exc)) from exc


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 20):
            sha.update(block)
    return sha.hexdigest()[:16]


def jpeg_for(kb: KnowledgeBase, rel: str, page: int = 0) -> bytes:
    """The page of this KB file as JPEG bytes (EXIF-rotated, longer side capped); cached on disk."""
    temp = None
    try:
        source = kb.resolve_inside(rel)
        cache = kb.index_path.parent / "viewer-cache" / f"{_digest(source)}-{page}.jpg"
        try:
            return cache.read_bytes()
        except OSError:
            pass
        with Image.open(source) as image:
            if page < 0 or page >= getattr(image, "n_frames", 1):
                raise ConvertError(f"page {page} is out of range")
            image.seek(page)
            frame = ImageOps.exif_transpose(image).convert("RGB")
            frame.thumbnail((MAX_SIDE, MAX_SIDE))
            cache.parent.mkdir(parents=True, exist_ok=True)
            temp = cache.with_name(f"{cache.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            frame.save(temp, "JPEG", quality=QUALITY)
        os.replace(temp, cache)
        return cache.read_bytes()
    except ConvertError:
        raise
    except Exception as exc:  # Pillow's TIFF decoder raises many types on malformed data
        raise ConvertError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
