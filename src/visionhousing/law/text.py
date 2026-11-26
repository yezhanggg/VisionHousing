"""Read an ordinance's text and split it into sections, keeping character offsets.

Plain-language summary
----------------------
A zoning ordinance is a long document cut into numbered sections ("Section
402. Accessory Dwelling Units"). To read it by rule, we first need its plain
text and where each section starts and ends.

- :func:`pdf_text` reads a PDF with pypdf (BSD licence), page by page, and
  joins the pages into one text, remembering where each page starts. A page
  pypdf cannot read (a damaged or unusual page) gives empty text and is listed
  in ``unreadable_pages``, so one bad page never stops the whole document.
- :func:`split_sections` finds every line that looks like a section or
  article heading (patterns in ``assumptions/law/parser.yaml``) and cuts the
  text there. Each :class:`SectionText` keeps its start and end offset in the
  whole text, so any quote taken from it can be found again at exactly that
  place, and a person checking a draft row can jump straight to it.
- :func:`normalise` collapses white space; its SHA-256 (:func:`text_sha256`)
  identifies a text even when the publisher's page changes its layout.

Nothing here judges what a section says; that is
:mod:`visionhousing.law.extract`.
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "RULES_PATH",
    "DocText",
    "SectionText",
    "load_parser_rules",
    "normalise",
    "pdf_text",
    "split_sections",
    "text_sha256",
]

RULES_PATH = Path(__file__).resolve().parents[3] / "assumptions" / "law" / "parser.yaml"


def load_parser_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    """Read ``assumptions/law/parser.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class DocText:
    """The plain text of one document and where each page starts in it.

    Attributes:
        text: All pages joined by a newline.
        page_starts: Character offset where each page begins (first is 0).
        unreadable_pages: Pages (1-based) whose text could not be read because
            the reader failed on them; their text is empty. Empty pages that
            read without error (scans) are not listed.
    """

    text: str
    page_starts: tuple[int, ...] = (0,)
    unreadable_pages: tuple[int, ...] = ()

    def page_of(self, offset: int) -> int:
        """The page number (1-based) holding character ``offset``."""
        return max(bisect_right(list(self.page_starts), offset), 1)


@dataclass(frozen=True)
class SectionText:
    """One section of a document.

    Attributes:
        number: The section number as printed (``"402"``, ``"27-402.1"``,
            ``"ARTICLE IV"``), or ``""`` for text before the first heading.
        heading: The words after the number on the heading line.
        start: Offset of the heading line in the whole text.
        end: Offset where the next section starts (or the text ends).
        text: The section's text, heading included.
    """

    number: str
    heading: str
    start: int
    end: int
    text: str = field(repr=False)

    @property
    def cite(self) -> str:
        """How to cite it: ``"Section 402"`` or ``"ARTICLE IV"``."""
        if not self.number:
            return ""
        return self.number if self.number.upper().startswith("ARTICLE") else f"§ {self.number}"


def pdf_text(path: Path | str) -> DocText:
    """The text of a PDF, page by page, with page start offsets.

    Pages whose text cannot be read (scanned images) give empty text; such a
    document needs a person or optical character recognition, which this tool
    does not do. A page on which pypdf fails also gives empty text and is
    recorded in :attr:`DocText.unreadable_pages`; the other pages are still read.
    The reader runs in pypdf's lenient mode, which repairs small faults in the
    file's structure.

    Raises:
        ValueError: The file cannot be opened as a PDF, or its page list
            cannot be read.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path), strict=False)
        count = len(reader.pages)
    except Exception as err:  # pypdf raises many types on a damaged file
        raise ValueError(f"cannot read the PDF {path}: {type(err).__name__}: {err}") from err
    parts: list[str] = []
    starts: list[int] = []
    bad: list[int] = []
    pos = 0
    for i in range(count):
        starts.append(pos)
        try:
            chunk = reader.pages[i].extract_text() or ""
        except Exception:  # one damaged page must not stop the document
            chunk = ""
            bad.append(i + 1)
        chunk = chunk.replace("\r\n", "\n").replace("\r", "\n")
        parts.append(chunk)
        pos += len(chunk) + 1
    return DocText("\n".join(parts), tuple(starts) or (0,), tuple(bad))


def split_sections(text: str, rules: dict[str, Any] | None = None) -> list[SectionText]:
    """Cut ``text`` at every heading line; return the sections in order.

    Text before the first heading becomes a section with an empty number.
    Offsets are into ``text`` itself, so ``text[s.start:s.end] == s.text``.
    """
    rules = rules or load_parser_rules()
    patterns = [re.compile(p) for p in rules["heading_patterns"]]
    heads: list[tuple[int, str, str]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\n")
        for pat in patterns:
            m = pat.match(bare)
            if m:
                heads.append((pos, m.group(1).strip(), m.group(2).strip()))
                break
        pos += len(line)
    out: list[SectionText] = []
    first = heads[0][0] if heads else len(text)
    if first > 0 and text[:first].strip():
        out.append(SectionText("", "", 0, first, text[:first]))
    for i, (start, number, heading) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        out.append(SectionText(number, heading, start, end, text[start:end]))
    return out


def normalise(text: str) -> str:
    """The text with every run of white space made one space, trimmed."""
    return re.sub(r"\s+", " ", text).strip()


def text_sha256(text: str) -> str:
    """SHA-256 of the normalised text (stable across page-layout changes)."""
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()
