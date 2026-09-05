"""Dash and arrow hygiene INSIDE generated office files.

Scott's standing rule bans em and en dashes as punctuation in everything
GhostPour serves. The chat answer has had a server-side backstop since
2026-08-12 (text_hygiene.normalize_dashes), but the files the sandbox
builds did not: on 2026-09-05 a 3-page docx shipped six em dashes and
two en dashes in its title line, status table and bullets, written by
the model through python-docx. The build-leg instruction now says the
rule applies inside files too, and this module is the belt-and-suspenders
half: it rewrites the text nodes of the Office XML before the file is
staged, applies the same substitution policy as the chat backstop, and
returns counts so the route can log a hit with the file name and the
generation id.

Scope is text nodes only: `<w:t>` in Word parts (document, headers,
footers, footnotes), `<t>` in a workbook's shared strings and inline
strings, `<a:t>` in slides. Nothing structural is touched, and any
failure returns the ORIGINAL bytes: never lose the artifact over
punctuation.
"""

from __future__ import annotations

import html
import logging
import re
import zipfile
from io import BytesIO
from xml.sax.saxutils import escape as _xml_escape

from app.services.text_hygiene import normalize_dashes

logger = logging.getLogger("ghostpour.artifact_hygiene")

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

_GLYPHS = "–—→←⇒➔➜"

# part name predicate, text element regex
_PARTS = {
    DOCX: (lambda n: n.startswith("word/") and n.endswith(".xml")
           and (n == "word/document.xml" or "/header" in n or "/footer" in n
                or "footnotes" in n or "endnotes" in n),
           re.compile(rb"(<w:t(?:\s[^>]*)?>)([^<]*)(</w:t>)")),
    XLSX: (lambda n: n == "xl/sharedStrings.xml" or (n.startswith("xl/worksheets/") and n.endswith(".xml")),
           re.compile(rb"(<t(?:\s[^>]*)?>)([^<]*)(</t>)")),
    PPTX: (lambda n: n.startswith("ppt/slides/") and n.endswith(".xml") or n.startswith("ppt/notesSlides/"),
           re.compile(rb"(<a:t(?:\s[^>]*)?>)([^<]*)(</a:t>)")),
}


def _count(text: str) -> tuple[int, int]:
    return text.count("—") + text.count("–"), sum(text.count(a) for a in "→←⇒➔➜")


def scrub_office_text(content: bytes, mime: str) -> tuple[bytes, dict]:
    """(possibly rewritten bytes, {"dashes": n, "arrows": m, "parts": k}).

    Counts are of glyphs found and rewritten. All zeros means the bytes
    came back untouched, byte for byte.
    """
    spec = _PARTS.get(mime)
    zero = {"dashes": 0, "arrows": 0, "parts": 0}
    if spec is None:
        return content, zero
    wants, rx = spec
    try:
        src = zipfile.ZipFile(BytesIO(content))
        dashes = arrows = parts = 0
        rewritten: dict[str, bytes] = {}
        for name in src.namelist():
            if not wants(name):
                continue
            data = src.read(name)
            # openpyxl writes non-ASCII as numeric character references
            # (&#8212;), python-docx writes the raw glyph; unescape before
            # looking, and again per text node before rewriting.
            if not any(g in html.unescape(data.decode("utf-8", "replace")) for g in _GLYPHS):
                continue

            def _fix(m: re.Match) -> bytes:
                nonlocal dashes, arrows
                text = html.unescape(m.group(2).decode("utf-8"))
                d, a = _count(text)
                if not (d or a):
                    return m.group(0)
                dashes += d; arrows += a
                return m.group(1) + _xml_escape(normalize_dashes(text)).encode("utf-8") + m.group(3)

            new = rx.sub(_fix, data)
            if new != data:
                rewritten[name] = new; parts += 1
        if not rewritten:
            return content, zero
        out = BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for info in src.infolist():
                data = rewritten.get(info.filename, src.read(info.filename))
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(zi, data)
        return out.getvalue(), {"dashes": dashes, "arrows": arrows, "parts": parts}
    except Exception as e:  # noqa: BLE001 — never lose the artifact over punctuation
        logger.warning("artifact_hygiene: could not scrub %s: %s", mime, e)
        return content, zero
