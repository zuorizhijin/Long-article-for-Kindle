"""Pure-Python stdlib EPUB3 builder with zero third-party dependencies."""

from __future__ import annotations

import datetime as _dt
import html
import io
import mimetypes
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


DEFAULT_CSS = """\
@charset "UTF-8";

body {
  font-family: "Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", Georgia, serif;
  line-height: 1.75;
  margin: 0;
  padding: 0 0.4em;
  color: #111111;
  word-wrap: break-word;
  overflow-wrap: break-word;
}

h1, h2, h3, h4, h5, h6 {
  font-family: "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", sans-serif;
  line-height: 1.35;
  margin-top: 1.4em;
  margin-bottom: 0.6em;
  font-weight: 700;
  page-break-after: avoid;
}

h1 {
  font-size: 1.55em;
  border-bottom: 1px solid #cccccc;
  padding-bottom: 0.3em;
}

h2 { font-size: 1.3em; }
h3 { font-size: 1.15em; }

.article-meta {
  font-size: 0.85em;
  color: #555555;
  margin-bottom: 1.4em;
  padding-bottom: 0.8em;
  border-bottom: 1px solid #dddddd;
}

.article-meta a {
  color: #333333;
  word-break: break-all;
}

p {
  margin: 0.75em 0;
  text-align: justify;
}

blockquote {
  margin: 1em 0;
  padding: 0.4em 0.9em;
  border-left: 3px solid #666666;
  color: #333333;
  background-color: #f5f5f5;
}

pre {
  font-family: "JetBrains Mono", Consolas, "Courier New", monospace;
  font-size: 0.85em;
  line-height: 1.45;
  background-color: #f4f4f4;
  border: 1px solid #dddddd;
  padding: 0.75em;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 1em 0;
}

code {
  font-family: "JetBrains Mono", Consolas, "Courier New", monospace;
  font-size: 0.9em;
  background-color: #f0f0f0;
  padding: 0.1em 0.3em;
}

pre code {
  background-color: transparent;
  padding: 0;
}

ul, ol {
  margin: 0.75em 0;
  padding-left: 1.6em;
}

li {
  margin: 0.3em 0;
}

img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 1em auto;
}

figure {
  margin: 1em 0;
  text-align: center;
}

figcaption {
  font-size: 0.82em;
  color: #555555;
  margin-top: 0.35em;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 1em 0;
  font-size: 0.9em;
}

th, td {
  border: 1px solid #bbbbbb;
  padding: 0.4em 0.55em;
  text-align: left;
  vertical-align: top;
}

th {
  background-color: #eeeeee;
  font-weight: 700;
}

hr {
  border: none;
  border-top: 1px solid #cccccc;
  margin: 1.5em 0;
}

.missing-image {
  font-size: 0.85em;
  color: #777777;
  border: 1px dashed #aaaaaa;
  padding: 0.4em 0.6em;
  margin: 0.8em 0;
  text-align: center;
}
"""

_IMAGE_MIME_OVERRIDES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
}


def guess_image_mime(filename: str) -> str:
    """Return a valid EPUB image MIME type for a filename."""
    ext = Path(filename).suffix.lower()
    if ext in _IMAGE_MIME_OVERRIDES:
        return _IMAGE_MIME_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


@dataclass
class EpubImage:
    """An embedded image asset inside OEBPS/images/."""

    href: str  # Relative to OEBPS/, e.g. "images/img_001.png"
    data: bytes
    media_type: str = ""

    def __post_init__(self) -> None:
        if not self.media_type:
            self.media_type = guess_image_mime(self.href)


@dataclass
class EpubChapter:
    """A single XHTML content document inside OEBPS/."""

    title: str
    body_xhtml: str
    filename: str = "chapter1.xhtml"


@dataclass
class EpubBook:
    """In-memory representation of an EPUB3 book."""

    title: str
    author: str = "Unknown"
    language: str = "zh-CN"
    source_url: str = ""
    book_id: str = field(default_factory=lambda: f"urn:uuid:{uuid.uuid4()}")
    chapters: list[EpubChapter] = field(default_factory=list)
    images: list[EpubImage] = field(default_factory=list)
    css: str = DEFAULT_CSS


def _wrap_chapter_xhtml(title: str, body_xhtml: str, css_href: str = "style.css") -> str:
    esc_title = html.escape(title, quote=True)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN" lang="zh-CN">\n'
        "<head>\n"
        '  <meta charset="UTF-8"/>\n'
        f"  <title>{esc_title}</title>\n"
        f'  <link rel="stylesheet" type="text/css" href="{html.escape(css_href, quote=True)}"/>\n'
        "</head>\n"
        "<body>\n"
        f"{body_xhtml}\n"
        "</body>\n"
        "</html>\n"
    )


def _build_container_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        "  <rootfiles>\n"
        '    <rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/>\n'
        "  </rootfiles>\n"
        "</container>\n"
    )


def _build_nav_xhtml(book: EpubBook) -> str:
    esc_title = html.escape(book.title, quote=True)
    items = "\n".join(
        f'      <li><a href="{html.escape(ch.filename, quote=True)}">'
        f"{html.escape(ch.title)}</a></li>"
        for ch in book.chapters
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops">\n'
        "<head>\n"
        '  <meta charset="UTF-8"/>\n'
        f"  <title>{esc_title}</title>\n"
        '  <link rel="stylesheet" type="text/css" href="style.css"/>\n'
        "</head>\n"
        "<body>\n"
        '  <nav epub:type="toc" id="toc">\n'
        f"    <h1>{esc_title}</h1>\n"
        "    <ol>\n"
        f"{items}\n"
        "    </ol>\n"
        "  </nav>\n"
        "</body>\n"
        "</html>\n"
    )


def _build_toc_ncx(book: EpubBook) -> str:
    esc_title = html.escape(book.title)
    esc_id = html.escape(book.book_id, quote=True)
    nav_points = []
    for idx, ch in enumerate(book.chapters, start=1):
        nav_points.append(
            f'    <navPoint id="navPoint-{idx}" playOrder="{idx}">\n'
            f"      <navLabel><text>{html.escape(ch.title)}</text></navLabel>\n"
            f'      <content src="{html.escape(ch.filename, quote=True)}"/>\n'
            "    </navPoint>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        "  <head>\n"
        f'    <meta name="dtb:uid" content="{esc_id}"/>\n'
        '    <meta name="dtb:depth" content="1"/>\n'
        '    <meta name="dtb:totalPageCount" content="0"/>\n'
        '    <meta name="dtb:maxPageNumber" content="0"/>\n'
        "  </head>\n"
        f"  <docTitle><text>{esc_title}</text></docTitle>\n"
        "  <navMap>\n"
        + "\n".join(nav_points)
        + "\n  </navMap>\n"
        "</ncx>\n"
    )


def _build_content_opf(book: EpubBook) -> str:
    modified = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    esc_title = html.escape(book.title)
    esc_author = html.escape(book.author or "Unknown")
    esc_lang = html.escape(book.language or "zh-CN")
    esc_id = html.escape(book.book_id)

    source_tag = ""
    if book.source_url:
        source_tag = f"    <dc:source>{html.escape(book.source_url)}</dc:source>\n"

    manifest_items = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '    <item id="css" href="style.css" media-type="text/css"/>',
    ]
    spine_items = []
    for idx, ch in enumerate(book.chapters, start=1):
        item_id = f"chap_{idx}"
        manifest_items.append(
            f'    <item id="{item_id}" href="{html.escape(ch.filename, quote=True)}" '
            'media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'    <itemref idref="{item_id}"/>')

    seen_hrefs: set[str] = set()
    for idx, img in enumerate(book.images, start=1):
        if img.href in seen_hrefs:
            continue
        seen_hrefs.add(img.href)
        manifest_items.append(
            f'    <item id="img_{idx}" href="{html.escape(img.href, quote=True)}" '
            f'media-type="{html.escape(img.media_type, quote=True)}"/>'
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="bookid">{esc_id}</dc:identifier>\n'
        f"    <dc:title>{esc_title}</dc:title>\n"
        f"    <dc:creator>{esc_author}</dc:creator>\n"
        f"    <dc:language>{esc_lang}</dc:language>\n"
        f"{source_tag}"
        f'    <meta property="dcterms:modified">{modified}</meta>\n'
        "  </metadata>\n"
        "  <manifest>\n"
        + "\n".join(manifest_items)
        + "\n  </manifest>\n"
        '  <spine toc="ncx">\n'
        + "\n".join(spine_items)
        + "\n  </spine>\n"
        "</package>\n"
    )


def build_epub_bytes(book: EpubBook) -> bytes:
    """Serialize an EpubBook into valid EPUB3 bytes."""
    if not book.chapters:
        raise ValueError("EpubBook must contain at least one chapter")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # 1. mimetype MUST be the first entry and MUST be uncompressed (ZIP_STORED)
        mime_info = zipfile.ZipInfo("mimetype")
        mime_info.compress_type = zipfile.ZIP_STORED
        zf.writestr(mime_info, b"application/epub+zip")

        # 2. META-INF/container.xml
        zf.writestr(
            "META-INF/container.xml",
            _build_container_xml().encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )

        # 3. OEBPS metadata & navigation
        zf.writestr(
            "OEBPS/content.opf",
            _build_content_opf(book).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        zf.writestr(
            "OEBPS/nav.xhtml",
            _build_nav_xhtml(book).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        zf.writestr(
            "OEBPS/toc.ncx",
            _build_toc_ncx(book).encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        zf.writestr(
            "OEBPS/style.css",
            book.css.encode("utf-8"),
            compress_type=zipfile.ZIP_DEFLATED,
        )

        # 4. Chapters
        for ch in book.chapters:
            xhtml = _wrap_chapter_xhtml(ch.title, ch.body_xhtml)
            zf.writestr(
                f"OEBPS/{ch.filename}",
                xhtml.encode("utf-8"),
                compress_type=zipfile.ZIP_DEFLATED,
            )

        # 5. Images
        seen_hrefs: set[str] = set()
        for img in book.images:
            if img.href in seen_hrefs:
                continue
            seen_hrefs.add(img.href)
            zf.writestr(
                f"OEBPS/{img.href}",
                img.data,
                compress_type=zipfile.ZIP_DEFLATED,
            )

    return buf.getvalue()


def write_epub_file(book: EpubBook, output_path: Path | str) -> Path:
    """Build and write an EPUB file to disk."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build_epub_bytes(book))
    return out
