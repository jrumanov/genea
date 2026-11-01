import json
from pathlib import Path

import pytest

from genea.ingest.images import (FaceRegion, default_sidecar, extract_image, map_metadata, merge_metadata,
                                 normalize_exif_date, sidecar_for)


def test_map_metadata_prefers_xmp_and_merges_keywords():
    md = {
        "XMP-dc:Title": "Svatba", "IPTC:ObjectName": "old title",
        "XMP-dc:Description": "Wedding", "ExifIFD:DateTimeOriginal": "1932:06:12 10:00:00",
        "XMP-dc:Subject": ["svatba", "Brno"], "IPTC:Keywords": "svatba",
        "XMP-iptcExt:PersonInImage": ["Jan Novák", "Marie Dvořáková"],
        "XMP-photoshop:City": "Brno", "Composite:GPSLatitude": 49.19, "Composite:GPSLongitude": 16.6,
    }
    rec = map_metadata(md, None)
    assert rec.title == "Svatba"
    assert rec.description == "Wedding"
    assert rec.date_taken == "1932-06-12"
    assert rec.keywords == ["svatba", "Brno"]
    assert rec.persons_shown == ["Jan Novák", "Marie Dvořáková"]
    assert rec.place == "Brno"
    assert (rec.gps_lat, rec.gps_lon) == (49.19, 16.6)


def test_map_metadata_single_values_and_numbers():
    rec = map_metadata({"XMP-dc:Title": 1932, "IPTC:Keywords": "rodina",
                        "IFD0:ImageDescription": "Anna"}, None)
    assert rec.title == "1932"
    assert rec.keywords == ["rodina"]
    assert rec.description == "Anna"
    assert rec.date_taken is None


@pytest.mark.parametrize("value, expected", [
    ("1932:06:12 10:00:00", "1932-06-12"), ("1932-06-12", "1932-06-12"),
    ("1932:06:00 00:00:00", "1932-06"), ("1932:00:00", "1932"), ("1932", "1932"),
    ("unknown", None), (None, None),
])
def test_normalize_exif_date(value, expected):
    assert normalize_exif_date(value) == expected


def test_sidecar_conventions(tmp_path):
    image = tmp_path / "a.jpg"
    image.write_bytes(b"x")
    assert sidecar_for(image) is None
    assert default_sidecar(image) == tmp_path / "a.xmp"
    (tmp_path / "a.jpg.xmp").write_text("x", encoding="utf-8")
    assert sidecar_for(image) == tmp_path / "a.jpg.xmp"
    (tmp_path / "a.xmp").write_text("x", encoding="utf-8")
    assert sidecar_for(image) == tmp_path / "a.xmp"


@pytest.mark.exiftool
def test_extract_fixture_images(sample_root):
    photos = sample_root / "photos"
    wedding = extract_image(photos / "svatba_1932.jpg")
    assert wedding.sidecar == photos / "svatba_1932.xmp"
    assert wedding.title == "Svatba Jana a Marie"
    assert wedding.persons_shown == ["Jan Novák", "Marie Dvořáková"]
    assert wedding.place == "Brno"
    assert wedding.date_taken == "1932-06-12"
    anna = extract_image(photos / "anna_1935.jpg")
    assert anna.date_taken == "1935-05-01"
    assert anna.description == "Anna Novakova 1935"
    house = extract_image(photos / "dum.jpg")
    assert (house.title, house.keywords, house.persons_shown) == (None, [], [])


def test_map_metadata_xmp_date_beats_exif_date():
    both = {"ExifIFD:DateTimeOriginal": "1932:06:12 10:00:00", "XMP-photoshop:DateCreated": "1933-07-01"}
    assert map_metadata(both, None).date_taken == "1933-07-01"
    both = {"ExifIFD:DateTimeOriginal": "1932:06:12 10:00:00", "XMP-exif:DateTimeOriginal": "1934:08:02 00:00:00"}
    assert map_metadata(both, None).date_taken == "1934-08-02"
    assert map_metadata({"ExifIFD:DateTimeOriginal": "1932:06:12 10:00:00"}, None).date_taken == "1932-06-12"


EMBEDDED = {
    "XMP-dc:Title": "Old title", "IPTC:Caption-Abstract": "Old caption", "XMP-dc:Description": "Old caption",
    "XMP-dc:Subject": ["rodina", "Brno"], "IPTC:Keywords": ["rodina", "svatba"],
    "XMP-exif:DateTimeOriginal": "1935:05:01 12:00:00", "ExifIFD:DateTimeOriginal": "1935:05:01 12:00:00",
    "XMP-photoshop:City": "Brno", "IPTC:Country-PrimaryLocationName": "Czechia",
    "XMP-iptcExt:PersonInImage": ["Anna"], "Composite:GPSLatitude": 49.2, "Composite:GPSLongitude": 16.6,
}


def test_merge_without_sidecar_is_map_metadata():
    assert merge_metadata(EMBEDDED, None, None) == map_metadata(EMBEDDED, None)


def test_sidecar_wins_per_field_and_embedded_fills_the_rest(tmp_path):
    sidecar = tmp_path / "a.xmp"
    rec = merge_metadata(EMBEDDED, {"XMP-dc:Title": "New title", "XMP-photoshop:City": "Tišnov"}, sidecar)
    assert rec.sidecar == sidecar
    assert rec.title == "New title"
    assert rec.description == "Old caption"
    assert rec.place == "Tišnov, Czechia"
    assert rec.keywords == ["rodina", "Brno", "svatba"]
    assert rec.persons_shown == ["Anna"]
    assert rec.date_taken == "1935-05-01"
    assert (rec.gps_lat, rec.gps_lon) == (49.2, 16.6)


def test_sidecar_keyword_list_replaces_all_embedded_keywords():
    assert merge_metadata(EMBEDDED, {"XMP-dc:Subject": ["Brno"]}, None).keywords == ["Brno"]
    assert merge_metadata(EMBEDDED, {"XMP-dc:Subject": ""}, None).keywords == []


def test_present_but_empty_sidecar_text_clears_the_field():
    rec = merge_metadata(EMBEDDED, {"XMP-dc:Description": "", "XMP-dc:Title": "",
                                    "XMP-iptcExt:PersonInImage": "", "XMP-photoshop:City": ""}, None)
    assert (rec.title, rec.description, rec.persons_shown) == (None, None, [])
    assert rec.place == "Czechia"


def test_sidecar_date_beats_every_embedded_date():
    rec = merge_metadata(EMBEDDED, {"XMP-photoshop:DateCreated": "1936:07:01"}, None)
    assert rec.date_taken == "1936-07-01"
    both = {"XMP-exif:DateTimeOriginal": "1937:01:02 00:00:00", "XMP-photoshop:DateCreated": "1936:07:01"}
    assert merge_metadata(EMBEDDED, both, None).date_taken == "1937-01-02"  # order inside the sidecar is kept


def _regions_md() -> dict:
    return json.loads((Path(__file__).parent / "fixtures" / "regions_sample.json").read_text(encoding="utf-8"))


def test_regions_parsed_from_mwg_metadata():
    rec = map_metadata(_regions_md(), None)
    assert rec.regions == [
        FaceRegion("Novak Jan 1932", 0.59375 - 0.0322266 / 2, 0.348307 - 0.0533854 / 2, 0.0322266, 0.0533854),
        FaceRegion("Novakova Marie", 0.32959 - 0.0273438 / 2, 0.367839 - 0.0559896 / 2, 0.0273438, 0.0559896),
        FaceRegion("Dvorak Petr", 0.488281 - 0.0292969 / 2, 0.330404 - 0.0488281 / 2, 0.0292969, 0.0488281),
    ]


def test_single_region_is_a_scalar_in_flat_output():
    md = {"XMP-mwg-rs:RegionName": "Novak Jan", "XMP-mwg-rs:RegionAreaX": 0.5, "XMP-mwg-rs:RegionAreaY": 0.5,
          "XMP-mwg-rs:RegionAreaW": 0.2, "XMP-mwg-rs:RegionAreaH": 0.4}
    [region] = map_metadata(md, None).regions
    assert (region.name, region.x, region.y, region.w, region.h) == ("Novak Jan", 0.4, 0.3, 0.2, 0.4)


def test_regions_skip_bad_entries():
    md = {
        "XMP-mwg-rs:RegionName": ["Good One", "", "No Area", "Too Big", "Negative", "Text", "Good Two"],
        "XMP-mwg-rs:RegionAreaX": [0.5, 0.5, 0.5, 1.5, -0.1, "abc", 0.25],
        "XMP-mwg-rs:RegionAreaY": [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.75],
        "XMP-mwg-rs:RegionAreaW": [0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.1],
        "XMP-mwg-rs:RegionAreaH": [0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.1],
    }
    md["XMP-mwg-rs:RegionAreaX"][2] = None
    regions = map_metadata(md, None).regions
    assert [r.name for r in regions] == ["Good One", "Good Two"]
    assert regions[0] == FaceRegion("Good One", 0.4, 0.4, 0.2, 0.2)


def test_regions_skip_boxes_leaving_the_image_and_short_lists():
    md = {
        "XMP-mwg-rs:RegionName": ["Edge", "Outside", "Missing Values"],
        "XMP-mwg-rs:RegionAreaX": [0.05, 0.5],
        "XMP-mwg-rs:RegionAreaY": [0.5, 0.5],
        "XMP-mwg-rs:RegionAreaW": [0.2, 0.2],
        "XMP-mwg-rs:RegionAreaH": [0.2, 0.2],
    }
    assert [r.name for r in map_metadata(md, None).regions] == ["Outside"]


def test_sidecar_regions_replace_embedded():
    sidecar_md = {"XMP-mwg-rs:RegionName": ["Dvorakova Eva"], "XMP-mwg-rs:RegionAreaX": [0.5],
                  "XMP-mwg-rs:RegionAreaY": [0.5], "XMP-mwg-rs:RegionAreaW": [0.2], "XMP-mwg-rs:RegionAreaH": [0.2]}
    rec = merge_metadata(_regions_md(), sidecar_md, Path("a.xmp"))
    assert [r.name for r in rec.regions] == ["Dvorakova Eva"]
    assert len(merge_metadata(_regions_md(), {"XMP-dc:Title": "T"}, Path("a.xmp")).regions) == 3
