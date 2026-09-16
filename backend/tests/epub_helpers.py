"""Small EPUB fixtures for backend HTTP tests."""

from __future__ import annotations

import io
import zipfile


def minimal_epub_bytes(*, chapter_title: str = "Book 1, Chapter 1", body_extra: str = "") -> bytes:
    """Tiny EPUB3 package sufficient for ebooklib + our parser."""
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>API Smoke Title</dc:title>
    <dc:creator>Integration</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="chap1" href="chap1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chap1"/>
  </spine>
</package>"""
    xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>X</title></head>
<body><h1>{chapter_title}</h1><p>Hello.{body_extra} Next sentence.</p></body></html>"""
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zinfo = zipfile.ZipInfo("mimetype")
        zinfo.compress_type = zipfile.ZIP_STORED
        zf.writestr(zinfo, "application/epub+zip")
        zf.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/chap1.xhtml", xhtml, compress_type=zipfile.ZIP_DEFLATED)
    return bio.getvalue()
