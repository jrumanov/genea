import pytest

from genea.exiftool import ExifToolError, exiftool_version, read_metadata, run_exiftool


def test_missing_binary():
    assert exiftool_version("definitely-not-exiftool-xyz") is None
    with pytest.raises(ExifToolError):
        run_exiftool(["-ver"], "definitely-not-exiftool-xyz")


@pytest.mark.exiftool
def test_read_metadata_of_plain_jpeg(tmp_path):
    from PIL import Image

    path = tmp_path / "ž.jpg"
    Image.new("RGB", (8, 8), "red").save(path)
    [record] = read_metadata([path])
    assert record["SourceFile"].endswith("ž.jpg")
