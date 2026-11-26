"""Law schema, the assumption files, the four law tables, the state build and the hooks."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from visionhousing.data.registry import load_registry
from visionhousing.data.sources import legislation
from visionhousing.features.zoning_status import read_plan_citations
from visionhousing.law import hooks, state, tables
from visionhousing.law.schema import Evidence, LawDocument, MuniRule, OrdinanceFacts, StateProvision

MUNIS = pd.DataFrame(
    [
        {"county": "42999", "cousub": "00100", "name": "Alpha borough", "dced_id": "1",
         "zoning_regime": "own", "population": 6000},
        {"county": "42999", "cousub": "00200", "name": "Beta township", "dced_id": "2",
         "zoning_regime": "county", "population": 3000},
        {"county": "42999", "cousub": "00300", "name": "Gamma township", "dced_id": "3",
         "zoning_regime": "none", "population": 1000},
        {"county": "42997", "cousub": "00400", "name": "Delta city", "dced_id": "4",
         "zoning_regime": "own", "population": 10000},
    ]
)  # fmt: skip

HAND = {
    "documents": [
        {
            "doc_id": "alpha_zo",
            "title": "Alpha Borough Zoning Ordinance",
            "level": "municipality",
            "publisher": "Alpha Borough",
            "url": "https://alpha.example/zo.pdf",
            "licence": "Public ordinance",
            "access": "hand_read",
            "source_id": "pa_municipal_ordinances",
            "adopted_on": "2019-05-01",
        }
    ],
    "rules": [
        {
            "geoid": "4299900100",
            "doc_id": "alpha_zo",
            "section_cite": "§ 504",
            "adu_status": "by_right",
            "parking_min_per_unit_sf": 2,
            "evidence_quote": "  ADUs are   permitted by right ",
            "evidence_offset": 10,
            "method": "hand",
            "confidence": "high",
            "reviewed_on": date(2026, 11, 23),
            "status": "verified",
        }
    ],
}


# --------------------------------------------------------------------------- schema


def test_unknown_is_the_default_never_no():
    row = MuniRule(geoid="4299900100")
    assert row.adu_status == "unknown" and row.method == "unread" and row.status == "draft"
    assert row.adu_detached is None and row.parking_min_per_unit_sf is None
    assert OrdinanceFacts().known == []


@pytest.mark.parametrize(
    "bad",
    [
        {"adu_status": "by-right"},
        {"status": "verified", "method": "hand"},  # no document, section or date
        {"status": "verified", "method": "rule", "doc_id": "d", "section_cite": "s",
         "reviewed_on": date(2026, 1, 1)},  # the reader cannot verify
        {"method": "unread", "adu_status": "prohibited"},
        {"evidence_quote": "x" * 301},
        {"geoid": "42999"},
    ],
)  # fmt: skip
def test_bad_rows_are_refused(bad):
    with pytest.raises(ValidationError):
        MuniRule(**{"geoid": "4299900100", **bad})


def test_provision_and_document_values_are_checked():
    ok = {
        "provision_id": "x_1",
        "citation": "MPC § 1",
        "reform": "adu",
        "status": "enacted",
        "applies_to": "everyone",
        "summary_own_words": "A plain summary of a section.",
        "source_id": "pa_mpc",
        "checked_on": date(2026, 11, 23),
    }
    StateProvision(**ok)
    for k, v in (("reform", "housing"), ("status", "passed"), ("scope", "some")):
        with pytest.raises(ValidationError):
            StateProvision(**{**ok, k: v})
    with pytest.raises(ValidationError):
        LawDocument(
            doc_id="d",
            title="T t",
            level="city",
            publisher="p",
            url="https://x.example",
            licence="l",
            access="fetched",
            source_id="s",
        )
    with pytest.raises(ValidationError):
        Evidence(quote="q" * 301)


# --------------------------------------------------------------------------- shipped files


def test_shipped_municipal_rules_are_valid_and_cited():
    data = tables.load_municipal_rules()
    docs = tables.municipal_documents(data)
    registry = load_registry()
    assert {d.source_id for d in docs} <= set(registry)
    ids = {d.doc_id for d in docs}
    for r in data["rules"]:
        row = MuniRule(**{k: v for k, v in r.items()})
        assert row.doc_id in ids
        if row.status == "verified":
            assert row.method == "hand" and row.evidence_offset is not None
    blocked = ("ecode360", "municode", "amlegal", "generalcode")
    assert not any(b in d.url for d in docs for b in blocked)


def test_shipped_county_ordinance_rows_stay_in_their_county():
    # A county ordinance (MPC § 602) gives one row per municipality it covers,
    # written with YAML anchors. Each row must expand to the full first row,
    # keep its own geoid, and lie in the county whose document it cites.
    data = tables.load_municipal_rules()
    county_docs = {d["doc_id"] for d in data["documents"] if d["level"] == "county"}
    geoids = [str(r["geoid"]) for r in data["rules"]]
    assert len(geoids) == len(set(geoids)), "a municipality is listed twice"
    by_doc: dict[str, set[str]] = {}
    for r in data["rules"]:
        if r["doc_id"] in county_docs:
            by_doc.setdefault(r["doc_id"], set()).add(str(r["geoid"])[:5])
            assert r["status"] == "verified" and r["section_cite"] and r["evidence_quote"]
    assert by_doc, "no county ordinance rows shipped"
    assert all(len(c) == 1 for c in by_doc.values()), by_doc


def test_shipped_state_provisions_are_valid():
    data = state.load_provisions()
    registry = load_registry()
    rows = state.provision_rows(data, date(2026, 11, 23))
    assert {r.reform for r in rows} == {"adu", "missing_middle", "tod", "parking", "fees"}
    assert all(r.scope == "mpc" and r.source_id in registry for r in rows)
    for b in data["bills"]:
        assert b["source_id"] in registry
        assert registry[b["source_id"]].url_pattern.startswith("https://www.palegis.us/")
    assert data["outside_mpc"] == ["4210160000", "4200361000"]


def test_plans_cover_every_county_and_never_guess():
    plans = read_plan_citations()
    assert len(plans) == 67
    for county, p in plans.items():
        assert county.startswith("42") and len(county) == 5
        if p["verified"] and p.get("name"):
            assert p["adopted"] is not None and p["url"], county


# --------------------------------------------------------------------------- tables


def test_muni_zoning_rules_has_every_municipality():
    t = tables.muni_zoning_rules(MUNIS, HAND)
    assert len(t) == 4 and t["geoid"].is_unique
    alpha = t.set_index("geoid").loc["4299900100"]
    assert alpha["adu_status"] == "by_right" and alpha["status"] == "verified"
    assert alpha["evidence_quote"] == "ADUs are permitted by right"
    assert alpha["reviewed_on"] == "2026-11-23"
    rest = t[t["geoid"] != "4299900100"]
    assert (rest["adu_status"] == "unknown").all() and (rest["method"] == "unread").all()
    assert rest["parking_min_per_unit_sf"].isna().all()


def test_hand_rules_must_match_a_municipality_and_a_document():
    bad = {**HAND, "rules": [{**HAND["rules"][0], "geoid": "4299999999"}]}
    with pytest.raises(ValueError, match="not in the DCED table"):
        tables.muni_zoning_rules(MUNIS, bad)
    bad = {**HAND, "rules": [{**HAND["rules"][0], "doc_id": "nope"}]}
    with pytest.raises(ValueError, match="unknown document"):
        tables.muni_zoning_rules(MUNIS, bad)


def test_county_land_use():
    plans = {"42999": {"name": "Plan 2040", "adopted": 2020, "url": "https://p.example",
                       "commission_url": "https://c.example", "verified": True}}  # fmt: skip
    t = tables.county_land_use(MUNIS, plans).set_index("county")
    a = t.loc["42999"]
    assert a["municipalities"] == 3 and a["munis_under_county_zoning"] == 1
    assert a["residents_under_county_zoning"] == 3000 and a["munis_no_zoning"] == 1
    assert a["residents"] == 10000 and a["plan_name"] == "Plan 2040" and a["plan_verified"] == 1
    assert t.loc["42997", "plan_name"] == ""


def test_every_law_column_is_described():
    for name in [*MuniRule.model_fields, *StateProvision.model_fields, *LawDocument.model_fields]:
        unit, text = tables.describe(name)
        assert unit and text
    assert set(tables.TABLES) == {
        "law_documents",
        "state_law_provisions",
        "county_land_use",
        "muni_zoning_rules",
    }
    registry = load_registry()
    for spec in tables.TABLES.values():
        assert set(spec["sources"]) <= set(registry)


# --------------------------------------------------------------------------- state build

CODE_TEXT = "\n".join(
    f"Section {s}. Heading" for s in ("301", "503", "503-A", "504-A", "507-A", "603", "604",
                                      "605", "701-A")
)  # fmt: skip


def test_check_citations_finds_every_section():
    data = state.load_provisions()
    assert set(state.check_citations(data, CODE_TEXT)) == {
        "301", "503", "503-A", "504-A", "507-A", "603", "604", "605", "701-A"
    }  # fmt: skip
    with pytest.raises(ValueError, match="not in the fetched text"):
        state.check_citations(data, CODE_TEXT.replace("Section 503-A.", "Section 503-B."))


class _NoNetwork:
    def get_bytes(self, url, params=None):
        raise OSError("offline")


class _Pages:
    def __init__(self, html: str):
        self.html = html

    def get_bytes(self, url, params=None):
        return self.html.encode()


PAGE = (
    '<strong>Short Title</strong></div><div class="col-10">An Act.</div>'
    '<strong>Actions</strong></div><div class="col-10"><table><tr><td>1</td>'
    "<td>Referred to HOUSING, Jan. 26, 2026</td></tr></table>"
)


def test_bills_fall_back_to_checked_values_when_offline(tmp_path):
    data = state.load_provisions()
    notes: list[str] = []
    rows = state.bill_rows(
        data, tmp_path, client=_NoNetwork(), manifest=None,
        today=lambda: date(2026, 11, 23), notes=notes,
    )  # fmt: skip
    assert len(rows) == len(data["bills"]) and len(notes) == len(rows)
    assert all(r.status == "pending" for r in rows)
    hb = next(r for r in rows if r.bill_id == "HB 2186")
    assert hb.last_action_date == date(2026, 6, 5)


def test_bills_lapse_after_the_session_and_read_their_page(tmp_path):
    data = state.load_provisions()
    rows = state.bill_rows(
        data, tmp_path, client=_Pages(PAGE), manifest=None, today=lambda: date(2026, 12, 1)
    )
    assert all(r.status == "lapsed" for r in rows)
    assert rows[0].last_action == "Referred to HOUSING"
    assert rows[0].last_action_date == date(2026, 1, 26)
    enacted = legislation.read_bill_status(PAGE.replace("Referred to HOUSING", "Act No. 12"))
    assert state.bill_status(enacted, date(2026, 11, 30), date(2027, 1, 1)) == "enacted"


def test_documents_list_fetched_and_link_only(tmp_path):
    data = state.load_provisions()
    doc = state.FetchedDocument("pa_mpc_1968_247", CODE_TEXT, tmp_path / "x", date(2026, 11, 23))
    rows = {r.doc_id: r for r in state.document_rows(data, {"pa_mpc_1968_247": doc})}
    assert rows["pa_mpc_1968_247"].sha256 and rows["pa_mpc_1968_247"].access == "fetched"
    assert rows["pa_code_bulletin"].access == "link_only" and not rows["pa_code_bulletin"].sha256


# --------------------------------------------------------------------------- hooks

STATEWIDE = (0.09, 0.15, 0.22)


def test_adu_hook_falls_back_below_coverage():
    rules = tables.muni_zoning_rules(MUNIS, HAND)
    h = {"min_coverage": 0.7, "adu_legal_statuses": ["by_right"]}
    out = hooks.county_adu_legal_share(rules, MUNIS, STATEWIDE, h).set_index("county")
    assert out.loc["42999", "coverage"] == pytest.approx(0.6)
    assert out.loc["42999", "source"] == "ledger"
    assert out.loc["42999", "central"] == pytest.approx(0.15)


def test_adu_hook_weights_by_residents_when_covered():
    rules = tables.muni_zoning_rules(MUNIS, HAND)
    h = {"min_coverage": 0.5, "adu_legal_statuses": ["by_right"]}
    out = hooks.county_adu_legal_share(rules, MUNIS, STATEWIDE, h).set_index("county")
    a = out.loc["42999"]
    assert a["source"] == "verified_rules"
    # 6,000 read residents by right + 4,000 unread at the statewide share, over 10,000
    assert a["low"] == pytest.approx((6000 + 4000 * 0.09) / 10000)
    assert a["central"] == pytest.approx((6000 + 4000 * 0.15) / 10000)
    assert out.loc["42997", "source"] == "ledger"


def test_parking_and_fee_reach_only_count_known_answers():
    rules = tables.muni_zoning_rules(MUNIS, HAND)
    p = hooks.county_parking_reach(rules, MUNIS).set_index("county")
    assert p.loc["42999", "share_of_read"] == 1.0
    assert p.loc["42999", "coverage"] == pytest.approx(0.6)
    f = hooks.county_fee_reach(rules, MUNIS).set_index("county")
    assert f.loc["42999", "share_of_read"] is None  # fee answer unknown: no claim


def test_fee_hook_stays_off_and_only_the_engine_context_reads_the_hooks():
    h = hooks.load_hooks()
    assert h["engine_wired"] is True and h["wired"]["fees"] is False
    src = Path(__file__).resolve().parents[1] / "src" / "visionhousing"
    for path in (src / "scenarios").rglob("*.py"):
        assert "from visionhousing.law" not in path.read_text(encoding="utf-8"), path
    assert "county_fee_reach" not in (src / "engine" / "context.py").read_text(encoding="utf-8")


def test_hand_read_documents_carry_their_text_checksum():
    # Each hand-read ordinance names the exact text read: a SHA-256 of the
    # normalised text, its length and the download date, so a quote's offset
    # can be checked again.
    data = tables.load_municipal_rules()
    for d in data["documents"]:
        if d["access"] == "hand_read":
            assert len(str(d["sha256"])) == 64 and int(d["text_chars"]) > 0, d["doc_id"]
            assert d["fetched_on"], d["doc_id"]
    for r in data["rules"]:
        if r["status"] == "verified":
            assert len(r["evidence_quote"]) <= 300, r["geoid"]


def test_a_city_in_two_counties_gets_one_reading_per_part():
    # Bethlehem lies in Lehigh and Northampton; both parts cite the same
    # citywide ordinance with the same values.
    rules = {str(r["geoid"]): r for r in tables.load_municipal_rules()["rules"]}
    lehigh, northampton = rules["4207706088"], rules["4209506088"]
    for key in ("doc_id", "adu_status", "evidence_offset", "status"):
        assert lehigh[key] == northampton[key]


def test_verified_units_leave_the_review_list():
    # review_sources.yaml lists only places not (yet) verified.
    from visionhousing.law.priorities import load_review_sources

    verified = {
        str(r["geoid"]) for r in tables.load_municipal_rules()["rules"] if r["status"] == "verified"
    }
    assert not verified & set(load_review_sources())


def test_g2c_dated_rows_count_as_read_and_derry_counts_from_its_reading():
    # The G2c dates (read in the cited text or the municipality's own minutes)
    # put these eight verified rows in force on the run's as-of date; Derry's
    # text could not be re-read (robots.txt), so it stays unread, not "no".
    from visionhousing.law.inforce import in_force, run_as_of

    data = tables.load_municipal_rules()
    docs = {d["doc_id"]: d for d in data["documents"]}
    rules = {str(r["geoid"]): r for r in data["rules"]}
    as_of = run_as_of()

    def dated(geoid):
        r = rules[geoid]
        doc = docs[r["doc_id"]]
        row = {k: r.get(k) or doc.get(k) for k in ("adopted_on", "effective_date")}
        row["in_force_by"] = r.get("in_force_by")
        row["reviewed_on"] = r.get("reviewed_on")
        return in_force(row, as_of)

    for geoid in ("4208534064", "4200359032", "4200346256", "4210318704",
                  "4200379274", "4200384512", "4200325904", "4210349408"):  # fmt: skip
        assert rules[geoid]["status"] == "verified" and dated(geoid), geoid
    # Derry has no date at all: since adoption no longer stands in for effect, a
    # reading counts from the day it was seen in force (2026-11-23), so it is read
    assert dated("4204318936")
    assert not in_force({"adopted_on": "2019"}, as_of)  # adoption alone is not enough


def test_section_dates_are_in_force_by_not_adoption_dates():
    # The section-date rows keep the latest dated enactment inside the cited
    # section as in_force_by, never as adopted_on, and still count as read.
    from visionhousing.law.inforce import in_force, run_as_of

    data = tables.load_municipal_rules()
    by_doc = {}
    for r in data["rules"]:
        by_doc.setdefault(r["doc_id"], []).append(r)
    docs = {d["doc_id"]: d for d in data["documents"]}
    for doc_id in ("upper_st_clair_allegheny_ch130_8", "whitehall_allegheny_ch180_art4",
                   "findlay_allegheny_ch117_art4", "milford_twp_pike_zo_2026",
                   "buckingham_bucks_zo_2021"):  # fmt: skip
        for r in by_doc[doc_id]:
            assert r.get("in_force_by") and not r.get("adopted_on"), doc_id
            assert not docs[doc_id].get("adopted_on"), doc_id
            assert in_force({"in_force_by": r["in_force_by"]}, run_as_of()), doc_id


def test_relatives_only_rows_are_by_right_with_conditions():
    from visionhousing.law.coverage import adu_category

    data = tables.load_municipal_rules()
    rules = {str(r["geoid"]): r for r in data["rules"]}
    # Pine, Bethlehem (both parts), Palmer, Lebanon city, Buckingham
    for geoid in ("4200360272", "4207706088", "4209506088", "4209557672", "4207542168",
                  "4201709816"):  # fmt: skip
        r = rules[geoid]
        assert r["adu_status"] == "by_right" and r["occupancy_restriction"] == "relatives_only"
        row = {**r, "adopted_on": r.get("adopted_on") or "2020"}
        assert adu_category(row, hooks.load_hooks()) == "by_right_conditions", geoid


def test_g2e_palmyra_pike_reading_is_dated_and_in_force():
    # Palmyra township (Pike) was read from its own July 15, 2025 ordinance,
    # which takes effect on adoption: verified, in force on the run's as-of
    # date, and "prohibited" (not addressed) in the ADU categories.
    from visionhousing.law.coverage import adu_category
    from visionhousing.law.inforce import in_force, run_as_of

    data = tables.load_municipal_rules()
    docs = {d["doc_id"]: d for d in data["documents"]}
    r = {str(x["geoid"]): x for x in data["rules"]}["4210357728"]
    doc = docs[r["doc_id"]]
    assert r["status"] == "verified" and r["method"] == "hand"
    assert r["adu_status"] == "not_addressed" and not r.get("occupancy_restriction")
    assert str(doc["adopted_on"]) == "2025-07-15" == str(doc["effective_date"])
    assert "palmyrapike.org" in doc["url"]
    row = {"adopted_on": doc["adopted_on"], "effective_date": doc["effective_date"]}
    assert in_force(row, run_as_of())
    # not addressed, but an unlisted use may ask for a conditional use (§ 4.402)
    assert adu_category({**r, **row}, hooks.load_hooks()) == "discretionary"
