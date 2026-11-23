"""Evidence library: schema checks, citations on every entry, and ledger links that resolve."""

import copy
from collections import Counter

import pytest

from visionhousing.ledger import LedgerError, load_ledger, parse_ledger
from visionhousing.ledger.evidence import (
    DEFAULT_EVIDENCE_PATH,
    EVIDENCE_PREFIX,
    evidence_links,
    load_evidence,
    parse_evidence,
    unresolved_links,
)
from visionhousing.ledger.loader import read_yaml

REFORMS = {"adu", "missing_middle", "tod", "parking", "fees"}


@pytest.fixture(scope="module")
def library():
    return load_evidence()


@pytest.fixture(scope="module")
def raw():
    return read_yaml(DEFAULT_EVIDENCE_PATH)


GOOD_SOURCE = {
    "id": "doc",
    "citation": "A report",
    "url": "https://example.org/report.pdf",
    "accessed": "2026-11-22",
    "locator": "Table 1",
    "verification": "primary_document",
}

GOOD = {
    "place": "Testville",
    "reform": "adu",
    "entry_type": "outcome",
    "date_of_change": "2019-08",
    "what_changed": "ADUs allowed by right.",
    "before": [
        {"measure": "ADUs", "value": 10, "unit": "per year", "period": "2018", "source": "doc"}
    ],
    "after": [
        {"measure": "ADUs", "value": 40, "unit": "per year", "period": "2021", "source": "doc"}
    ],
    "sources": [GOOD_SOURCE],
    "verification": "primary_document",
    "comparability": "A test town.",
    "used_for": ["hindcast"],
}


def tiny(**changes) -> dict:
    entry = copy.deepcopy(GOOD)
    entry.update(changes)
    return {"version": 1, "entries": {"ev.adu.testville": entry}}


def problem(data) -> str:
    with pytest.raises(LedgerError) as info:
        parse_evidence(data)
    return str(info.value)


# ------------------------------------------------------------------ the real file


def test_real_library_loads_with_enough_entries(library):
    assert len(library) >= 15


def test_every_reform_has_evidence(library):
    counts = Counter(e.reform for _, e in library.items())
    assert set(counts) == REFORMS
    assert counts["adu"] >= 5


def test_every_entry_and_source_has_url_and_access_date(library):
    for eid, entry in library.items():
        assert entry.sources, eid
        for s in entry.sources:
            assert s.url.startswith("https://") or s.url.startswith("http://"), (eid, s.id)
            assert s.accessed is not None, (eid, s.id)
            assert s.locator.strip(), (eid, s.id)


def test_every_figure_has_a_unit_period_and_cited_source(library):
    for eid, entry in library.items():
        ids = {s.id for s in entry.sources}
        for obs in entry.observations():
            assert obs.unit.strip() and obs.period.strip(), (eid, obs.measure)
            assert obs.source in ids, (eid, obs.measure)


def test_outcome_entries_have_before_and_after(library):
    for eid, entry in library.items():
        if entry.entry_type == "outcome":
            assert entry.after, eid
            assert entry.before or entry.before_note.strip(), eid


def test_every_entry_says_what_it_is_used_for(library):
    for eid, entry in library.items():
        assert entry.used_for, eid
    assert library.used_for("hindcast"), "T4 needs hindcast entries"
    assert library.used_for("calibration"), "T2 needs calibration entries"


def test_entry_verification_is_never_stronger_than_its_sources(library):
    order = ["primary_document", "primary_data", "abstract_only", "secondary_summary"]
    order.append("excerpt_only")
    for eid, entry in library.items():
        weakest = max(order.index(s.verification) for s in entry.sources)
        assert order.index(entry.verification) >= weakest, eid


def test_file_has_no_unknown_fields(raw):
    # Pydantic forbids extra fields; this pins that the real file is read strictly.
    entry = next(iter(raw["entries"].values()))
    bad = {"version": 1, "entries": {"ev.adu.x": {**entry, "notes": "x"}}}
    assert "notes" in problem(bad)


# ------------------------------------------------------------------ ledger links


def test_every_ledger_link_to_evidence_resolves(library):
    assert unresolved_links(load_ledger(), library) == []


def test_ledger_links_exist_for_the_adu_takeup_entry(library):
    links = evidence_links(load_ledger())
    assert "adu.takeup_feasible_annual" in links
    assert all(sid in library for sid in links["adu.takeup_feasible_annual"])


def test_a_broken_ledger_link_is_reported(library):
    data = read_yaml(DEFAULT_EVIDENCE_PATH.parent / "parameters.yaml")
    data = copy.deepcopy(data)
    pid = "adu.takeup_feasible_annual"
    data["parameters"][pid]["source"] = [
        {"id": f"{EVIDENCE_PREFIX}adu.not_there", "citation": "Missing entry"}
    ]
    assert unresolved_links(parse_ledger(data), library) == [f"{pid} -> ev.adu.not_there"]


# ------------------------------------------------------------------ schema checks


def test_a_good_entry_parses():
    lib = parse_evidence(tiny())
    assert list(lib) == ["ev.adu.testville"]
    assert lib.by_reform("adu") == ["ev.adu.testville"]


def test_a_source_without_url_fails():
    src = {k: v for k, v in GOOD_SOURCE.items() if k != "url"}
    assert "url" in problem(tiny(sources=[src]))


def test_a_source_without_access_date_fails():
    src = {k: v for k, v in GOOD_SOURCE.items() if k != "accessed"}
    assert "accessed" in problem(tiny(sources=[src]))


def test_a_url_must_be_a_web_address():
    assert "https://" in problem(tiny(sources=[{**GOOD_SOURCE, "url": "file:///tmp/x.pdf"}]))


def test_an_entry_without_sources_fails():
    assert "sources" in problem(tiny(sources=[]))


def test_a_figure_citing_an_unknown_source_fails():
    after = [{"measure": "ADUs", "value": 1, "unit": "u", "period": "2021", "source": "nope"}]
    assert "unknown source 'nope'" in problem(tiny(after=after))


def test_an_outcome_needs_after_figures():
    assert "'after'" in problem(tiny(after=[]))


def test_an_outcome_needs_before_figures_or_a_note():
    assert "before_note" in problem(tiny(before=[]))
    assert parse_evidence(tiny(before=[], before_note="No earlier count exists."))


def test_a_rule_needs_a_summary():
    assert "rule_summary" in problem(tiny(entry_type="rule", before=[], after=[]))


def test_a_cross_section_needs_findings():
    assert "finding" in problem(tiny(entry_type="cross_section", before=[], after=[]))


def test_an_unknown_reform_or_use_fails():
    assert "reform" in problem(tiny(reform="rent_control"))
    assert "used_for" in problem(tiny(used_for=["forecast"]))


def test_used_for_cannot_be_empty():
    assert "used_for" in problem(tiny(used_for=[]))


def test_claimed_verification_cannot_beat_the_sources():
    src = {**GOOD_SOURCE, "verification": "excerpt_only"}
    assert "stronger than its weakest source" in problem(tiny(sources=[src]))


def test_bad_ids_and_mismatched_reforms_fail():
    data = tiny()
    data["entries"] = {"adu.testville": data["entries"]["ev.adu.testville"]}
    assert "ev.reform.short_name" in problem(data)
    data = tiny()
    data["entries"] = {"ev.parking.testville": data["entries"]["ev.adu.testville"]}
    assert "other than 'adu'" in problem(data)


def test_bad_dates_fail():
    assert "date_of_change" in problem(tiny(date_of_change="Aug 2019"))
    assert parse_evidence(tiny(date_of_change=2019))


def test_a_boolean_is_not_a_number():
    after = [{"measure": "ADUs", "value": True, "unit": "u", "period": "2021", "source": "doc"}]
    assert "not true or false" in problem(tiny(after=after))


def test_top_level_shape_is_checked():
    assert "'entries'" in problem({"version": 1})
    assert "unknown top-level" in problem({**tiny(), "extra": 1})
    assert "empty" in problem({"version": 1, "entries": {}})


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(LedgerError, match="cannot read"):
        load_evidence(tmp_path / "evidence.yaml")
