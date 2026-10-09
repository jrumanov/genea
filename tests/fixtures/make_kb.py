"""Build the sample knowledge base used across the test suite."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import docx
from PIL import Image, ExifTags
from reportlab.pdfgen import canvas

FIXTURES = Path(__file__).parent

KRONIKA = (
    "Kronika obce Brno.\n\n"
    "Josef Novák, sedlák v Brně, se roku 1845 oženil s Annou Svobodovou. "
    "Jejich syn Jan Novák se narodil roku 1846.\n"
)
POZNAMKY = "# Poznámky\n\nTerezie Dvořáková se narodila roku 1851 v Tišnově.\n"
DOPIS = "Milý Karle Nováku, blahopřeji ke svatbě v roce 1895. Karel Novák a Rosalie Krejčí."

XMP = """<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/'>
 <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>
  <rdf:Description rdf:about=''
    xmlns:dc='http://purl.org/dc/elements/1.1/'
    xmlns:Iptc4xmpExt='http://iptc.org/std/Iptc4xmpExt/2008-02-29/'
    xmlns:photoshop='http://ns.adobe.com/photoshop/1.0/'>
   <dc:title><rdf:Alt><rdf:li xml:lang='x-default'>Svatba Jana a Marie</rdf:li></rdf:Alt></dc:title>
   <dc:description><rdf:Alt><rdf:li xml:lang='x-default'>Svatba Jana Nováka a Marie Dvořákové, Brno 1932</rdf:li></rdf:Alt></dc:description>
   <dc:subject><rdf:Bag><rdf:li>svatba</rdf:li><rdf:li>Brno</rdf:li></rdf:Bag></dc:subject>
   <Iptc4xmpExt:PersonInImage><rdf:Bag><rdf:li>Jan Novák</rdf:li><rdf:li>Marie Dvořáková</rdf:li></rdf:Bag></Iptc4xmpExt:PersonInImage>
   <photoshop:City>Brno</photoshop:City>
   <photoshop:DateCreated>1932-06-12</photoshop:DateCreated>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end='w'?>
"""


def _pdf(path: Path, pages: list[list[str]]) -> None:
    pdf = canvas.Canvas(str(path))
    for lines in pages:
        y = 720
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 18
        pdf.showPage()
    pdf.save()


def build_sample_kb(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "kb_sample" / "tree.ged", root / "tree.ged")

    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "kronika.txt").write_text(KRONIKA, encoding="utf-8")
    (docs / "poznamky.md").write_text(POZNAMKY, encoding="utf-8")
    letter = docx.Document()
    letter.add_paragraph(DOPIS)
    letter.save(str(docs / "dopis.docx"))
    # Built-in PDF fonts cannot show Czech háčeks, so the PDF text is plain ASCII.
    _pdf(docs / "matrika.pdf", [["Matrika Brno 1846: Jan Novak, syn Josefa Novaka."],
                                ["1875 pokrten Vaclav Novak v Brne."]])
    scan = canvas.Canvas(str(docs / "sken.pdf"))  # an "image only" page: no text layer
    scan.rect(50, 50, 200, 200, fill=1)
    scan.showPage()
    scan.save()

    photos = root / "photos"
    photos.mkdir(exist_ok=True)
    Image.new("RGB", (16, 16), "white").save(photos / "svatba_1932.jpg")
    (photos / "svatba_1932.xmp").write_text(XMP, encoding="utf-8")
    image = Image.new("RGB", (16, 16), "gray")
    exif = image.getexif()
    exif[ExifTags.Base.ImageDescription] = "Anna Novakova 1935"
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = "1935:05:01 12:00:00"
    image.save(photos / "anna_1935.jpg", exif=exif)
    Image.new("RGB", (16, 16), "blue").save(photos / "dum.jpg")
    return root


def damage_pdf(path: Path) -> None:
    """Point every xref entry at offset 0, like the broken PDFs pypdf repairs with 'wrong pointing object'."""
    path.write_bytes(re.sub(rb"\d{10} 00000 n", b"0000000000 00000 n", path.read_bytes()))
