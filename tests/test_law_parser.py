"""The rule-based ordinance reader, on the fictional Testland ordinance (text and PDF)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from visionhousing.data.sources import legislation
from visionhousing.law.extract import (
    BlockedSourceError,
    check_source,
    draft_rule,
    extract_facts,
    rank_sections,
    write_review_csv,
)
from visionhousing.law.text import DocText, normalise, pdf_text, split_sections, text_sha256

FIXTURE = Path(__file__).parent / "fixtures" / "law" / "testland_ordinance.txt"


@pytest.fixture
def text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def make_pdf(lines: list[str], path: Path, per_page: int = 20) -> Path:
    """Write a minimal PDF (Helvetica text, one line per row) without any PDF library."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    pages = [lines[i : i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    objs: list[bytes] = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i, page in enumerate(pages):
        body = "BT /F1 10 Tf 12 TL 40 760 Td " + " ".join(f"({esc(x)}) Tj T*" for x in page)
        stream = (body + " ET").encode("latin-1")
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for k, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{k} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


# --------------------------------------------------------------------------- text


def test_split_sections_keeps_offsets(text):
    sections = split_sections(text)
    numbers = [s.number for s in sections]
    assert "504" in numbers and "ARTICLE III" in numbers and "701" in numbers
    for s in sections:
        assert text[s.start : s.end] == s.text
    assert sections[0].number == ""  # the title block before the first heading
    adu = next(s for s in sections if s.number == "504")
    assert adu.heading == "Accessory Dwelling Units" and adu.cite == "§ 504"


def test_pdf_text_reads_pages_and_offsets(tmp_path, text):
    pdf = make_pdf(text.splitlines(), tmp_path / "testland.pdf")
    doc = pdf_text(pdf)
    assert len(doc.page_starts) == 2
    assert "Accessory Dwelling Units" in doc.text
    assert doc.page_of(0) == 1 and doc.page_of(len(doc.text) - 1) == 2
    assert doc.unreadable_pages == ()


def test_pdf_text_skips_a_page_the_reader_fails_on(tmp_path, text, monkeypatch):
    """A damaged page (as in one borough's ordinance PDF) is left empty and listed."""
    from pypdf import PageObject

    pdf = make_pdf(text.splitlines(), tmp_path / "testland.pdf", per_page=10)
    good = pdf_text(pdf)
    real = PageObject.extract_text
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyError("/Filter")  # what pypdf raises on a broken content stream
        return real(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", flaky)
    doc = pdf_text(pdf)
    assert doc.unreadable_pages == (2,)
    assert len(doc.page_starts) == len(good.page_starts)
    assert doc.page_starts[1] == doc.page_starts[2] - 1  # page 2 is empty text
    assert "Accessory Dwelling Units" in good.text


def test_pdf_text_refuses_a_file_that_is_not_a_pdf(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf at all")
    with pytest.raises(ValueError, match="cannot read the PDF"):
        pdf_text(bad)


def test_text_hash_ignores_layout():
    assert text_sha256("a  b\n c") == text_sha256("a b c")
    assert normalise(" a\n\tb ") == "a b"


# --------------------------------------------------------------------------- reader


def test_rank_sections_puts_the_adu_section_first(text):
    ranked = rank_sections(split_sections(text), "adu")
    assert ranked[0][1].number == "504"


def test_extract_facts_from_the_testland_ordinance(text):
    facts = extract_facts(text)
    assert facts.adu_status == "by_right"
    assert facts.adu_detached is True
    assert facts.adu_owner_occupancy is True
    assert facts.min_lot_sf_smallest_res_district == 6000
    assert facts.smallest_res_district == "R-2"
    assert facts.parking_min_per_unit_sf == 2
    assert facts.parking_min_per_unit_mf == 1.5
    assert facts.max_units_by_right_sf_district == 1
    assert set(facts.permitted_types) >= {"single_family_detached", "two_family", "multifamily"}
    assert facts.transport_impact_fee_ordinance is None  # not stated: unknown, not "no"
    for name, ev in facts.evidence.items():
        assert len(ev.quote) <= 300
        assert normalise(text[ev.offset : ev.offset + 400]).startswith(ev.quote[:20]), name


def test_extract_from_pdf_matches_text(tmp_path, text):
    pdf = make_pdf(text.splitlines(), tmp_path / "t.pdf")
    facts = extract_facts(pdf_text(pdf))
    assert facts.adu_status == "by_right" and facts.parking_min_per_unit_mf == 1.5


def test_no_adu_words_means_not_addressed(text):
    cut = text.split("ARTICLE V Supplemental")[0]
    assert extract_facts(cut).adu_status == "not_addressed"


@pytest.mark.parametrize(
    ("phrase", "status"),
    [
        ("is permitted by special exception", "special_exception"),
        ("is permitted as a conditional use", "conditional"),
        ("are not permitted", "prohibited"),
    ],
)
def test_adu_status_phrases(text, phrase, status):
    changed = text.replace("is permitted by right", phrase)
    assert extract_facts(changed).adu_status == status


def test_adu_keyword_without_rule_stays_unknown():
    doc = "Section 1. Definitions\nAccessory dwelling unit means a second home on a lot.\n"
    assert extract_facts(doc).adu_status == "unknown"


def test_impact_fee_is_read_when_named():
    doc = "Section 9. Fees\nThe Borough has adopted a transportation impact fee ordinance.\n"
    facts = extract_facts(DocText(doc))
    assert facts.transport_impact_fee_ordinance is True


def test_draft_rule_is_never_verified(text, tmp_path):
    facts = extract_facts(text)
    row = draft_rule(facts, geoid="4299900100", doc_id="testland_zo_2026")
    assert row.status == "draft" and row.method == "rule"
    assert row.adu_status == "by_right" and row.section_cite == "§ 504"
    assert row.confidence in ("high", "medium", "low")
    out = write_review_csv([(row, facts)], tmp_path / "review.csv")
    lines = list(csv.DictReader(out.open(encoding="utf-8")))
    assert {r["field"] for r in lines} >= {"adu_status", "parking_min_per_unit_mf"}
    assert all(r["status"] == "draft" for r in lines)


@pytest.mark.parametrize(
    "url",
    [
        "https://ecode360.com/12345",
        "https://library.municode.com/pa/x",
        "https://codelibrary.amlegal.com/codes/x",
        "https://www.pacodeandbulletin.gov/Display",
    ],
)
def test_commercial_code_hosts_are_refused(url):
    with pytest.raises(BlockedSourceError):
        check_source(url)


def test_own_site_is_allowed():
    assert check_source("https://www.testlandborough.example/zoning.pdf") == (
        "www.testlandborough.example"
    )


# --------------------------------------------------------------------------- bill pages

BILL_PAGE = """
<div class="row"><div class="col-2"><strong>Short Title</strong></div>
<div class="col-10 text-justify">An Act providing for testland porches.</div></div>
<div class="row"><div class="col-2"><strong>Memo Subject</strong></div>
<div class="col-10">Porches for All</div></div>
<div class="row"><div class="col-2"><strong>Actions</strong></div><div class="col-10">
<table class="table"><tr><td>101</td><td>Referred to PORCHES, Feb.&#160;2,&#160;2026</td></tr>
<tr><td></td><td>In the Senate </td></tr>
<tr><td></td><td>Third consideration and final passage, June&#160;1,&#160;2026 (10-2)</td></tr>
</table></div></div>
"""


def test_read_bill_status_from_a_fictional_page():
    s = legislation.read_bill_status(BILL_PAGE)
    assert s.short_title == "An Act providing for testland porches."
    assert s.memo_subject == "Porches for All"
    assert len(s.actions) == 3 and s.actions[1][1] is None
    text, when = s.last_action
    assert when.isoformat() == "2026-06-01" and not s.enacted
    assert legislation.strip_date(text) == "Third consideration and final passage (10-2)"


def test_bill_page_without_title_is_an_error():
    with pytest.raises(ValueError, match="Short Title"):
        legislation.read_bill_status("<html></html>")


def test_statute_text_keeps_paragraphs():
    page = "<html><head><title>x</title></head><body><p>Section 1. A.</p><p>B&amp;C</p></body>"
    assert legislation.statute_text(page) == "Section 1. A.\nB&C"
