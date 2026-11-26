"""State level: the planning code's sections and the pending bills that bear on each reform.

Plain-language summary
----------------------
``assumptions/law/state_provisions.yaml`` lists, for Pennsylvania, the
sections of the Municipalities Planning Code (MPC) and the bills of the current
session that matter for the five reforms, each with a summary in our own words.
This module turns that list into two pack tables and checks it against the
texts themselves:

- :func:`fetch_documents` downloads the MPC's full text from palegis.us (once,
  into the raw cache), turns it into plain text and keeps its SHA-256, so the
  pack says exactly which text was read.
- :func:`check_citations` fails when a provision cites an MPC section whose
  heading ("Section 503-A.") is not in that text: a typo or a renumbered
  section cannot slip through.
- :func:`bill_rows` reads each bill's page for its last action and date. A bill
  the page shows as signed is ``enacted``; one still open after the session's
  last day is ``lapsed``; otherwise ``pending``. When a page cannot be read,
  the hand-checked values in the YAML file are used and the build notes say so.
- :func:`provision_rows` and :func:`document_rows` return the rows of
  ``state_law_provisions`` and ``law_documents``.

Rule-change tracker. Every row also says when the rule was introduced,
enacted, last amended and took effect, read from the texts themselves:

- an MPC section: :func:`section_history` reads the compiler's notes that the
  code's text keeps after each section ("(503-A added Dec. 19, 1990, P.L.1343,
  No.209)", "((1) amended Oct. 24, 2012 ...)"). A section added by a later act
  is enacted on that date and its effective date stays unknown (the note does
  not give it); a section of the original act is enacted on the act's date
  (July 31, 1968) and took effect on the date of the code's own section 102
  (January 1, 1969). ``last_amended`` is the latest note in the section.
- a bill: ``introduced_on`` is the date of the first dated action on its page
  (in the General Assembly a bill is referred to committee the day it is
  introduced); ``enacted_on`` is the date of an action saying the Governor
  approved it. When the page cannot be read, the hand-checked values in the
  YAML file are used and ``verification`` says ``checked_values``.

The Pennsylvania Code and Bulletin site forbids automated reading in its robots
file, so it is a ``link_only`` document: listed, never fetched.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources import legislation
from visionhousing.law.schema import LawDocument, StateProvision, is_future
from visionhousing.law.text import text_sha256

__all__ = [
    "PROVISIONS_PATH",
    "FetchedDocument",
    "SectionHistory",
    "bill_rows",
    "bill_status",
    "check_citations",
    "document_rows",
    "fetch_documents",
    "load_provisions",
    "parse_note_date",
    "provision_rows",
    "section_history",
]

ROOT = Path(__file__).resolve().parents[3]
PROVISIONS_PATH = ROOT / "assumptions" / "law" / "state_provisions.yaml"
CACHE_FOLDER = ("local", "law")


def load_provisions(path: Path = PROVISIONS_PATH) -> dict[str, Any]:
    """Read ``assumptions/law/state_provisions.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class FetchedDocument:
    """A legal text read for this build: its plain text, cached file and fetch date."""

    doc_id: str
    text: str
    path: Path
    fetched_on: date


def _law_cache(cache: Path) -> Path:
    return Path(cache).joinpath(*CACHE_FOLDER)


def fetch_documents(
    data: dict[str, Any],
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> dict[str, FetchedDocument]:
    """Download (or reuse) every ``fetched`` document's text; return them by id."""
    out: dict[str, FetchedDocument] = {}
    for doc in data.get("documents", []):
        if doc["access"] != "fetched":
            continue
        dest = _law_cache(cache) / f"{doc['doc_id']}.htm"
        if refresh and dest.exists():
            dest.unlink()
        raw, fresh = legislation.fetch_page(doc["text_url"], dest, client=client)
        if fresh and manifest is not None:
            mf.record(
                mf.entry_for_file(doc["source_id"], doc["text_url"], dest, "current", root=mf.ROOT),
                manifest,
            )
        text = legislation.statute_text(raw)
        when = date.fromtimestamp(dest.stat().st_mtime)
        out[doc["doc_id"]] = FetchedDocument(doc["doc_id"], text, dest, when)
    return out


_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_HEAD = re.compile(r"Section (\d+(?:\.\d+)?(?:-[A-Z])?)\. ")
_NOTE = re.compile(
    r"\((\([\w.]+\)(?:\s*and\s*\([\w.]+\))?|[\d.]+(?:-[A-Z])?)\s+"
    r"(added|amended|reenacted and amended)\s+([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})"
)
_ART_ADDED = re.compile(r"\(Art\. added ([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})")
_ACT = re.compile(r"Act of ([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})")
_EFFECTIVE = re.compile(
    r"Section 102\. Effective Date\.--This act shall take effect ([A-Z][a-z]+ \d{1,2}, \d{4})"
)


def parse_note_date(text: str) -> date:
    """``"Sept. 24, 2014"`` or ``"January 1, 1969"`` as a date."""
    m = re.match(r"([A-Za-z]+)\.? (\d{1,2}), (\d{4})", text.strip())
    if not m or m.group(1)[:3].lower() not in _MONTHS:
        raise ValueError(f"not a date: {text!r}")
    return date(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))


@dataclass(frozen=True)
class SectionHistory:
    """What the code's text says about one section's dates (``None``: not stated)."""

    enacted_on: date | None
    last_amended: date | None
    effective_date: date | None
    notes: tuple[str, ...] = ()


def section_history(text: str, section: str) -> SectionHistory:
    """Dates of one MPC section from the compiler's notes in the fetched text.

    The section runs from its heading ("Section 503-A. ") to the next section
    heading. A note naming the section itself as ``added`` (or its article, as
    "(Art. added ...)") gives ``enacted_on``; otherwise the act's own date
    applies and the effective date is the one in section 102. ``last_amended``
    is the latest note date in the section (subsection notes included), when
    later than ``enacted_on``.
    """
    start = text.find(f"Section {section}. ")
    if start < 0:
        return SectionHistory(None, None, None)
    end = len(text)
    for m in _HEAD.finditer(text, start + 10):
        if m.group(1) != section:
            end = m.start()
            break
    seg = text[start:end]
    notes = [(who, kind, parse_note_date(when)) for who, kind, when in _NOTE.findall(seg)]
    added = [d for who, kind, d in notes if who == section and kind == "added"]
    article = text.rfind("ARTICLE ", 0, start)
    art = _ART_ADDED.search(text, article, start) if article >= 0 else None
    if art and not added:
        added = [parse_note_date(art.group(1))]
    act = _ACT.search(text)
    eff = _EFFECTIVE.search(text)
    if added:
        enacted, effective = min(added), None
    else:
        enacted = parse_note_date(act.group(1)) if act else None
        effective = parse_note_date(eff.group(1)) if eff else None
    latest = max((d for _w, _k, d in notes), default=None)
    amended = latest if latest is not None and (enacted is None or latest > enacted) else None
    return SectionHistory(enacted, amended, effective, tuple(f"{w} {k} {d}" for w, k, d in notes))


def check_citations(data: dict[str, Any], text: str) -> list[str]:
    """Check every provision's section heading is in the code's text.

    Returns:
        The section numbers checked.

    Raises:
        ValueError: A cited section's heading is not in the text.
    """
    checked = []
    for p in data.get("provisions", []):
        sec = str(p["section"])
        if f"Section {sec}." not in text:
            raise ValueError(
                f"provision {p['provision_id']} cites MPC section {sec}, "
                "which is not in the fetched text"
            )
        checked.append(sec)
    return checked


def bill_status(page: legislation.BillStatus | None, session_end: date, today: date) -> str:
    """``enacted``, ``lapsed`` or ``pending`` for a bill (``None``: page not read)."""
    if page is not None and page.enacted:
        return "enacted"
    return "lapsed" if today > session_end else "pending"


def bill_rows(
    data: dict[str, Any],
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
    today: Callable[[], date] = legislation.today,
    notes: list[str] | None = None,
) -> list[StateProvision]:
    """One provision row per bill, with status and last action from its page."""
    notes = notes if notes is not None else []
    end = _as_date(data["session_end"])
    now = today()
    rows = []
    for b in data.get("bills", []):
        source = get_source(b["source_id"])
        dest = _law_cache(cache) / "bills" / f"{b['source_id']}.html"
        page = None
        try:
            if refresh and dest.exists():
                dest.unlink()
            raw, fresh = legislation.fetch_page(source.url_pattern, dest, client=client)
            page = legislation.read_bill_status(raw)
            if fresh and manifest is not None:
                mf.record(
                    mf.entry_for_file(
                        b["source_id"], source.url_pattern, dest, "current", root=mf.ROOT
                    ),
                    manifest,
                )
        except Exception as err:  # a page that cannot be read falls back to the checked values
            notes.append(
                f"{b['bill_id']}: page not read ({type(err).__name__}); used checked values"
            )
        last, when = page.last_action if page is not None else ("", None)
        last = legislation.strip_date(last)
        dated = [(t, d) for t, d in (page.actions if page is not None else ()) if d is not None]
        introduced = dated[0][1] if dated else b.get("introduced_on")
        signed = [d for t, d in dated if re.search(r"(?i)approved by the governor|\bact no\.", t)]
        enacted = signed[0] if signed else b.get("enacted_on")
        effective = b.get("effective_date")
        rows.append(
            StateProvision(
                provision_id=b["provision_id"],
                citation=b["citation"],
                reform=b["reform"],
                status=bill_status(page, end, now),
                bill_id=b["bill_id"],
                last_action=last or b["last_action"],
                last_action_date=when or _as_date(b["last_action_date"]),
                applies_to=b["applies_to"],
                scope=b["scope"],
                summary_own_words=" ".join(str(b["summary_own_words"]).split()),
                source_id=b["source_id"],
                url=source.url_pattern,
                checked_on=now if page is not None else source.checked_on or now,
                introduced_on=introduced,
                enacted_on=enacted,
                effective_date=effective,
                binding_conditions=" ".join(str(b.get("binding_conditions") or "").split()),
                verification="verified" if page is not None else "checked_values",
                future_effective=is_future(_text_date(effective), now),
            )
        )
    return rows


def _text_date(v: Any) -> str:
    return v.isoformat() if isinstance(v, date) else str(v or "")


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def provision_rows(
    data: dict[str, Any], checked_on: date, text: str | None = None
) -> list[StateProvision]:
    """One row per planning-code provision (all ``enacted``), with its dates from ``text``.

    Without the code's text the dates stay empty (unknown).
    """
    doc = next(d for d in data["documents"] if d["access"] == "fetched")
    url = doc["url"]
    rows = []
    for p in data.get("provisions", []):
        h = section_history(text, str(p["section"])) if text else SectionHistory(None, None, None)
        rows.append(
            StateProvision(
                provision_id=p["provision_id"],
                citation=p["citation"],
                reform=p["reform"],
                status=p["status"],
                applies_to=data["mpc_applies_to"],
                scope="mpc",
                summary_own_words=" ".join(str(p["summary_own_words"]).split()),
                source_id=doc["source_id"],
                url=url,
                checked_on=checked_on,
                enacted_on=h.enacted_on,
                last_amended=h.last_amended,
                effective_date=h.effective_date,
                binding_conditions=" ".join(str(p.get("binding_conditions") or "").split()),
                verification="verified" if text else "checked_values",
                future_effective=is_future(_text_date(h.effective_date), checked_on),
            )
        )
    return rows


def document_rows(data: dict[str, Any], fetched: dict[str, FetchedDocument]) -> list[LawDocument]:
    """One ``law_documents`` row per state document (and per bill page)."""
    rows = []
    for d in data.get("documents", []):
        got = fetched.get(d["doc_id"])
        rows.append(
            LawDocument(
                doc_id=d["doc_id"],
                title=d["title"],
                level=d["level"],
                publisher=d["publisher"],
                url=d["url"],
                licence=d["licence"],
                access=d["access"],
                source_id=d["source_id"],
                sha256=text_sha256(got.text) if got else "",
                text_chars=len(got.text) if got else None,
                fetched_on=got.fetched_on if got else None,
            )
        )
    for b in data.get("bills", []):
        source = get_source(b["source_id"])
        rows.append(
            LawDocument(
                doc_id=b["source_id"],
                title=f"{b['bill_id']} (2025-2026 session), bill page",
                level="state",
                publisher=source.publisher,
                url=source.url_pattern,
                licence=source.licence,
                access="fetched",
                source_id=b["source_id"],
            )
        )
    return rows


@dataclass
class StateLaw:
    """The state tables' rows and the build notes."""

    provisions: list[StateProvision] = field(default_factory=list)
    documents: list[LawDocument] = field(default_factory=list)
    files: list[tuple[str, Path]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_state_law(
    cache: Path,
    *,
    path: Path = PROVISIONS_PATH,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
    today: Callable[[], date] = legislation.today,
) -> StateLaw:
    """Fetch the code and the bill pages, check the citations, and return the rows."""
    data = load_provisions(path)
    out = StateLaw()
    fetched = fetch_documents(data, cache, client=client, manifest=manifest, refresh=refresh)
    mpc = next(iter(fetched.values()))
    checked = check_citations(data, mpc.text)
    out.notes.append(f"law: {len(set(checked))} cited MPC sections found in the fetched text")
    out.provisions = provision_rows(data, mpc.fetched_on, mpc.text) + bill_rows(
        data, cache, client=client, manifest=manifest, refresh=refresh, today=today, notes=out.notes
    )
    out.documents = document_rows(data, fetched)
    for d in data["documents"]:
        if d["doc_id"] in fetched:
            out.files.append((d["source_id"], fetched[d["doc_id"]].path))
    for b in data.get("bills", []):
        p = _law_cache(cache) / "bills" / f"{b['source_id']}.html"
        if p.exists():
            out.files.append((b["source_id"], p))
    return out
