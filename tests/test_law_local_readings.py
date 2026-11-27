"""Vermont and Wyoming local readings (law/local_readings.py) and the 2026-11-26 law merge.

Offline: the shipped YAML files and small hand-made rows. The rules checked: a verified
fact keeps its proof; drafts, guidance and superseded texts are never verified; a place
that could not be read has no facts; adoption, amendment and effective dates stay apart
and a reading with no effective date counts from its review date; unknown service stays
unknown when the districts go into the Vermont state floor.
"""

from __future__ import annotations

from datetime import date

import pytest
import yaml
from pydantic import ValidationError

from visionhousing.law import local_readings as lr
from visionhousing.law import tables
from visionhousing.law.rights import District, state_floor
from visionhousing.law.states import load_state_file

DOC = {
    "doc_id": "town_bylaw",
    "state": "VT",
    "title": "Town bylaw",
    "publisher": "Town",
    "url": "https://town.example/bylaw.pdf",
    "retrieved_on": "2026-11-26",
    "sha256": "a" * 64,
}
EVIDENCE = {"doc_id": "town_bylaw", "locator": "Sec. 1, p. 2", "quote": "Duplexes are permitted.",
            "offset": 10}  # fmt: skip
FACT = {"topic": "duplex", "value": "permitted", "review": "verified", "evidence": [EVIDENCE]}
PLACE = {
    "place_id": "vt_town",
    "state": "VT",
    "name": "Town",
    "geoid": "5000700000",
    "status": "verified",
    "reviewed_on": "2026-11-26",
    "facts": [FACT],
}


def readings(docs=(DOC,), places=(PLACE,)):
    return lr.LocalReadings(documents=tuple(docs), places=tuple(places))


@pytest.fixture(scope="module")
def shipped():
    return lr.load_local_readings()


# --------------------------------------------------------------------------- shipped file


def test_shipped_readings_load_and_cite_hashed_text(shipped):
    docs = {d.doc_id: d for d in shipped.documents}
    vt = lr.places_for("VT", shipped)
    assert len([p for p in vt if p.status == "verified"]) == 11
    assert {p.place_id for p in vt if p.status == "not_read"} == {
        "vt_williston", "vt_st_albans_city", "vt_brattleboro"}  # fmt: skip
    for p in shipped.places:
        for f in p.facts:
            if f.review in ("verified", "superseded"):
                for e in f.evidence:
                    assert len(docs[e.doc_id].sha256) == 64
                    assert len(e.quote) <= lr.QUOTE_MAX
                    assert e.offset is not None or e.page is not None
    blocked = ("ecode360", "municode", "amlegal", "generalcode")
    assert not any(b in d.url for d in shipped.documents for b in blocked)


def test_shipped_wyoming_teton_is_superseded_and_laramie_has_no_dates(shipped):
    wy = {p.place_id: p for p in lr.places_for("WY", shipped)}
    teton, laramie = wy["wy_teton_county"], wy["wy_laramie"]
    assert teton.status == "superseded" and lr.counts_from(teton) is None
    assert {f.review for f in teton.facts} == {"superseded"}
    assert not (laramie.adopted_on or laramie.effective_date or laramie.in_force_by)
    assert lr.counts_from(laramie) == laramie.reviewed_on == date(2026, 11, 26)


def test_hartford_guidance_facts_stay_draft(shipped):
    hartford = next(p for p in shipped.places if p.place_id == "vt_hartford")
    docs = {d.doc_id: d for d in shipped.documents}
    for f in hartford.facts:
        if any(docs[e.doc_id].kind == "guidance" for e in f.evidence):
            assert f.review == "draft"


# --------------------------------------------------------------------------- loader rules


def test_a_verified_fact_needs_evidence_and_no_open_check():
    with pytest.raises(ValidationError, match="needs evidence"):
        lr.LocalFact(topic="duplex", value="permitted", review="verified")
    with pytest.raises(ValidationError, match="open check"):
        lr.LocalFact(**{**FACT, "check": "confirm the table columns"})
    assert lr.LocalFact(**{**FACT, "review": "draft", "check": "confirm"}).review == "draft"


def test_quotes_are_short_and_topics_fixed():
    with pytest.raises(ValidationError):
        lr.LocalFact(**{**FACT, "evidence": [{**EVIDENCE, "quote": "x" * 301}]})
    with pytest.raises(ValidationError, match="topic"):
        lr.LocalFact(**{**FACT, "topic": "anything"})


def test_verified_facts_need_final_bylaw_text_with_a_hash():
    assert readings().places[0].status == "verified"
    for change, words in (
        ({"kind": "guidance"}, "final bylaw"),
        ({"version_state": "draft"}, "final bylaw"),
        ({"version_state": "superseded", "valid_until": "2026-07-01"}, "final bylaw"),
        ({"sha256": ""}, "SHA-256"),
    ):
        with pytest.raises(ValidationError, match=words):
            readings(docs=({**DOC, **change},))


def test_superseded_texts_need_an_end_and_superseded_facts():
    with pytest.raises(ValidationError, match="valid_until"):
        lr.LocalDocument(**{**DOC, "version_state": "superseded"})
    old = {**DOC, "version_state": "superseded", "valid_until": "2026-07-01"}
    fact = {**FACT, "review": "superseded"}
    place = {**PLACE, "status": "superseded", "facts": [fact]}
    assert lr.counts_from(readings(docs=(old,), places=(place,)).places[0]) is None
    with pytest.raises(ValidationError, match="not a superseded text"):
        readings(places=(place,))


def test_place_status_matches_its_facts():
    with pytest.raises(ValidationError, match="not read has no facts"):
        lr.LocalPlace(**{**PLACE, "status": "not_read", "not_read_reason": "robots"})
    with pytest.raises(ValidationError, match="say why"):
        lr.LocalPlace(**{**PLACE, "status": "not_read", "facts": []})
    with pytest.raises(ValidationError, match="draft place"):
        lr.LocalPlace(**{**PLACE, "status": "draft"})
    draft = {**FACT, "review": "draft"}
    with pytest.raises(ValidationError, match="at least one verified"):
        lr.LocalPlace(**{**PLACE, "facts": [draft]})


def test_unknown_doc_and_wrong_district_fields_fail():
    with pytest.raises(ValidationError, match="unknown doc_id"):
        readings(docs=({**DOC, "doc_id": "other"},))
    with pytest.raises(ValidationError, match="not District fields"):
        lr.LocalPlace(**{**PLACE, "districts": {"R1": {"sewer": True}}})


# --------------------------------------------------------------------------- dates


def test_dates_stay_apart_and_adoption_never_counts():
    base = lr.LocalPlace(**PLACE)
    assert lr.counts_from(base) == date(2026, 11, 26)  # no date: from the review date
    adopted = lr.LocalPlace(**{**PLACE, "adopted_on": "2019-06-11"})
    assert lr.counts_from(adopted) == date(2026, 11, 26)  # adoption never stands in
    assert lr.counts_from(lr.LocalPlace(**{**PLACE, "in_force_by": "2023-03"})) == date(2023, 3, 31)
    eff = lr.LocalPlace(**{**PLACE, "effective_date": "2024", "in_force_by": "2020-01-01"})
    assert lr.counts_from(eff) == date(2024, 12, 31)  # a known effective date decides
    assert lr.LocalDocument(**{**DOC, "adopted_on": "unknown"}).adopted_on == ""


# --------------------------------------------------------------------------- outputs


def test_districts_feed_the_state_floor_and_unknown_service_stays_unknown():
    place = lr.LocalPlace(
        **{
            **PLACE,
            "districts": {
                "served": {
                    "year_round_residential": True,
                    "allows_residential": True,
                    "municipal_water": True,
                    "municipal_sewer": True,
                    "required_min_units": 0,
                    "local_max_units": 1,
                },
                "unknown": {
                    "year_round_residential": True,
                    "allows_residential": True,
                    "municipal_water": None,
                    "municipal_sewer": True,
                    "required_min_units": 0,
                    "local_max_units": 1,
                },
            },
        }
    )
    d = lr.district_inputs(place)
    assert isinstance(d["served"], District)
    on = date(2026, 11, 25)
    assert state_floor(d["served"], on).max_units_by_right == 4
    unknown = state_floor(d["unknown"], on)
    assert unknown.duplex is True and unknown.small_multiunit is None
    assert unknown.max_units_by_right is None


def test_montpelier_served_district_gets_the_four_unit_right(shipped):
    mpv = next(p for p in shipped.places if p.place_id == "vt_montpelier")
    d = lr.district_inputs(mpv)
    on = date(2026, 11, 25)
    assert state_floor(d["RES 3"], on).small_multiunit is True
    assert state_floor(d["RES 6"], on).small_multiunit is None  # "could be served" is unknown
    assert state_floor(d["RL"], on).small_multiunit is False


def test_readings_table_and_resident_coverage():
    r = readings(places=(PLACE, {**PLACE, "place_id": "vt_other", "geoid": "5000711111",
                                 "effective_date": "2027-01-01"}))  # fmt: skip
    table = lr.readings_table("VT", r)
    assert list(table.columns) == list(lr.READING_COLUMNS) and len(table) == 2
    assert lr.readings_table("WY", r).empty
    pop = {"5000700000": 300.0, "5000711111": 700.0}
    cov = lr.resident_coverage("VT", pop, total=1000.0, on=date(2026, 11, 26), readings=r)
    assert cov["share"] == pytest.approx(0.3) and cov["places"] == ["vt_town"]
    later = lr.resident_coverage("VT", pop, total=1000.0, on=date(2027, 1, 1), readings=r)
    assert later["share"] == pytest.approx(1.0)
    before = lr.resident_coverage("VT", pop, total=1000.0, on=date(2026, 11, 25), readings=r)
    assert before["residents_read"] == 0.0
    adu = lr.resident_coverage("VT", pop, total=1000.0, on=date(2027, 1, 1), readings=r,
                               topics=["adu"])  # fmt: skip
    assert adu["residents_read"] == 0.0  # no verified ADU fact


# --------------------------------------------------------------------------- state and PA rows


def test_wyoming_fast_track_act_is_kept_out_of_the_reform_provisions():
    data = load_state_file("WY")
    assert data["records"] == []
    ids = {p["provision_id"] for p in data["provisions"]}
    act = data["non_benchmark_enacted"][0]
    assert act["provision_id"] == "wy_hb0002_2026_fast_track_permits"
    assert act["provision_id"] not in ids
    assert str(act["effective_date"]) == "2026-07-01" and str(act["signed_on"]) == "2026-03-05"
    bills = {b["bill"]: b for b in data["bills_not_enacted"]}
    assert bills["SF0040"]["last_action"] == "S postponed indefinitely"
    assert "effective_date" not in bills["HB0197"]


def test_wyoming_evidence_records_load():
    from visionhousing.evidence.records import load_panel

    recs = {r.id: r for r in load_panel().records if r.state == "WY"}
    act = recs["wy.hb0002_2026_fast_track_permits"]
    assert act.implementation_status == "legally_effective"
    assert act.effective_dates[0].date == date(2026, 7, 1)
    assert recs["wy.hb0197_2025_exaction_fee_limits"].enacted_date is None


def test_pa_rows_of_2026_11_26():
    data = tables.load_municipal_rules()
    rules = {str(r["geoid"]): r for r in data["rules"]}
    assert rules["4207933408"]["status"] == "draft"  # Hazleton: blank enactment date
    lyco = [r for r in data["rules"] if r["doc_id"] == "lycoming_county_zo_2021"]
    assert len(lyco) == 16 and all(str(r["geoid"]).startswith("42081") for r in lyco)
    pending = {"4208104424", "4208152280", "4208160128", "4208160328", "4208161640"}
    assert not pending & set(rules)  # not confirmed: stay unknown
    assert rules["4204315640"]["adu_status"] == "not_addressed"
    assert not rules["4204315640"].get("unlisted_uses")  # no clause read: ADU unknown
    assert rules["4204366560"]["unlisted_uses"] == "special_exception"
    assert not rules["4204366560"].get("adopted_on")  # Royalton: no date printed
    docs = yaml.safe_load(tables.MUNI_RULES_PATH.read_text(encoding="utf-8"))["documents"]
    upax = next(d for d in docs if d["doc_id"] == "upper_paxton_dauphin_ch27_2019")
    assert str(upax["effective_date"]) == str(upax["adopted_on"]) == "2019-06-11"
