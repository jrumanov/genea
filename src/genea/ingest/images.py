"""Image metadata (EXIF/IPTC/XMP + sidecar) via ExifTool."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from genea.exiftool import ExifToolError, read_metadata

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".tif", ".tiff", ".png", ".heic"})


@dataclass
class ImageRecord:
    sidecar: Path | None
    title: str | None = None
    description: str | None = None
    date_taken: str | None = None
    keywords: list[str] = field(default_factory=list)
    persons_shown: list[str] = field(default_factory=list)
    place: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None


def sidecar_for(image: Path) -> Path | None:
    """Existing sidecar: <stem>.xmp (Lightroom) first, then <name>.<ext>.xmp (darktable)."""
    for candidate in (image.with_suffix(".xmp"), image.with_name(image.name + ".xmp")):
        if candidate.is_file():
            return candidate
    return None


def default_sidecar(image: Path) -> Path:
    return sidecar_for(image) or image.with_suffix(".xmp")


def normalize_exif_date(value) -> str | None:
    """'1932:06:12 10:00:00' -> '1932-06-12'; zero month/day parts are dropped."""
    if value is None:
        return None
    match = re.match(r"^(\d{4})(?:[:\-](\d{2}))?(?:[:\-](\d{2}))?", str(value).strip())
    if not match:
        return None
    year, month, day = match.groups()
    if not month or month == "00":
        return year
    if not day or day == "00":
        return f"{year}-{month}"
    return f"{year}-{month}-{day}"


def _first(md: dict, *keys: str):
    for key in keys:
        value = md.get(key)
        if value not in (None, "", []):
            return value
    return None


def _as_list(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [str(v).strip() for v in items if str(v).strip()]


def _text(value) -> str | None:
    return None if value is None else str(value)


TITLE = ("XMP-dc:Title", "IPTC:ObjectName")
DESCRIPTION = ("XMP-dc:Description", "IPTC:Caption-Abstract", "IFD0:ImageDescription")
DATE = ("XMP-exif:DateTimeOriginal", "XMP-photoshop:DateCreated", "ExifIFD:DateTimeOriginal", "IPTC:DateCreated")
KEYWORDS = ("XMP-dc:Subject", "IPTC:Keywords")
PERSONS = ("XMP-iptcExt:PersonInImage",)
PLACE_PARTS = (
    ("XMP-iptcCore:Location", "IPTC:Sub-location"),
    ("XMP-photoshop:City", "IPTC:City"),
    ("XMP-photoshop:Country", "IPTC:Country-PrimaryLocationName"),
)


def _present(md: dict, keys: tuple[str, ...]) -> bool:
    return any(key in md for key in keys)


def map_fields(md: dict) -> dict:
    """The ImageRecord fields this metadata provides. A text or list tag that is present but empty
    counts as provided (it clears the field); dates and GPS count only when they hold a value.
    Place parts are kept separately ('place_0'..'place_2') so each can be overridden on its own."""
    fields: dict = {}
    if _present(md, TITLE):
        fields["title"] = _text(_first(md, *TITLE))
    if _present(md, DESCRIPTION):
        fields["description"] = _text(_first(md, *DESCRIPTION))
    date = normalize_exif_date(_first(md, *DATE))
    if date:
        fields["date_taken"] = date
    if _present(md, KEYWORDS):
        fields["keywords"] = list(dict.fromkeys(k for key in KEYWORDS for k in _as_list(md.get(key))))
    if _present(md, PERSONS):
        fields["persons_shown"] = _as_list(md.get(PERSONS[0]))
    for i, keys in enumerate(PLACE_PARTS):
        if _present(md, keys):
            fields[f"place_{i}"] = _first(md, *keys)
    for name, keys in (("gps_lat", ("Composite:GPSLatitude", "XMP-exif:GPSLatitude")),
                       ("gps_lon", ("Composite:GPSLongitude", "XMP-exif:GPSLongitude"))):
        value = _first(md, *keys)
        if value is not None:
            fields[name] = float(value)
    return fields


def _record(fields: dict, sidecar: Path | None) -> ImageRecord:
    parts = [fields.get(f"place_{i}") for i in range(len(PLACE_PARTS))]
    return ImageRecord(
        sidecar=sidecar,
        title=fields.get("title"),
        description=fields.get("description"),
        date_taken=fields.get("date_taken"),
        keywords=fields.get("keywords", []),
        persons_shown=fields.get("persons_shown", []),
        place=", ".join(str(p) for p in parts if p) or None,
        gps_lat=fields.get("gps_lat"),
        gps_lon=fields.get("gps_lon"),
    )


def map_metadata(md: dict, sidecar: Path | None) -> ImageRecord:
    """Map the metadata of one file (tag order decides between tags of the same field)."""
    return _record(map_fields(md), sidecar)


def merge_metadata(embedded: dict, sidecar_md: dict | None, sidecar: Path | None) -> ImageRecord:
    """The sidecar is authoritative per field: every field it provides (even as an empty value)
    replaces the embedded one; keywords come only from the sidecar if it has a keyword list."""
    fields = map_fields(embedded)
    if sidecar_md is not None:
        fields.update(map_fields(sidecar_md))
    return _record(fields, sidecar)


def extract_images(paths: list[Path], exe: str = "exiftool") -> dict[Path, ImageRecord]:
    """Like extract_image for many images, with a single ExifTool run. Raises ExifToolError if the run
    fails (one bad file fails the whole run), so a caller can fall back to extract_image per file."""
    sidecars = [sidecar_for(path) for path in paths]
    files = [f for path, sidecar in zip(paths, sidecars) for f in (path, sidecar) if f is not None]
    records = read_metadata(files, exe)
    if len(records) != len(files):
        raise ExifToolError(f"ExifTool returned {len(records)} records for {len(files)} files")
    found = iter(records)
    result = {}
    for path, sidecar in zip(paths, sidecars):
        embedded = next(found)
        result[path] = merge_metadata(embedded, next(found) if sidecar else None, sidecar)
    return result


def extract_image(path: Path, exe: str = "exiftool") -> ImageRecord:
    """Read the image and its sidecar; the sidecar wins per field."""
    sidecar = sidecar_for(path)
    records = read_metadata([path] + ([sidecar] if sidecar else []), exe)
    return merge_metadata(records[0], records[1] if sidecar else None, sidecar)
