"""Implementation timeline: pending, effective, superseded, mapped area, unknown, review queue."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from visionhousing.data.sources.legislation import BillStatus
from visionhousing.law import timeline as tl

FIX = Path(__file__).parent / "fixtures" / "law"


@pytest.fixture(scope="module")
def shipped():
    return tl.load_timeline()


@pytest.fixture(scope="module")
def ma():
    return tl.load_timeline(FIX / "timeline_ma.yaml"), tl.load_areas(FIX / "ma_3a_berkley.geojson")


# --------------------------------------------------------------------------- PA bills


@pytest.mark.parametrize("bill", ["pa_hb2186_pending", "pa_sb1346_pending"])
def test_pa_adu_bills_are_pending_and_leave_the_baseline_unchanged(shipped, bill):
    for on in (date(2026, 11, 24), date(2027, 6, 1), date(2031, 12, 31)):
        e = tl.eligibility_status(
            shipped, state="PA", geoid="4204318936", housing_type="adu", on=on
        )
        assert e.status == "pending" and e.in_force is False
        assert bill in e.considered


def test_pa_bill_facts_as_read(shipped):
    by_id = {r.record_id: r for r in shipped}
    assert by_id["pa_sb1346_pending"].note.endswith("November 17, 2026.")
    assert "June 5, 2026" in by_id["pa_hb2186_pending"].note
    assert by_id["pa_hb2186_pending"].start == date(2026, 2, 2)


def test_a_local_effective_rule_beats_a_pending_state_bill(shipped):
    e = tl.eligibility_status(
        shipped, state="PA", geoid="4207702000", housing_type="adu", on=date(2026, 6, 1)
    )
    assert e.status == "legally_effective" and e.in_force is True and e.level == "municipality"
    before = tl.eligibility_status(
        shipped, state="PA", geoid="4207702000", housing_type="adu", on=date(2025, 12, 31)
    )
    # before it took effect and before either ADU bill was introduced: nothing is known
    assert before.status == "unknown" and before.in_force is None


def test_no_record_is_unknown_not_no(shipped):
    e = tl.eligibility_status(
        shipped, state="PA", geoid="4204318936", housing_type="tod", on=date(2027, 1, 1)
    )
    assert e.status == "unknown" and e.in_force is None and e.record_id == ""


# --------------------------------------------------------------------------- VT supersession


def test_vt_act152_supersedes_on_its_date(shipped):
    old = tl.eligibility_status(
        shipped, state="VT", housing_type="multifamily", on=date(2026, 6, 30)
    )
    new = tl.eligibility_status(
        shipped, state="VT", housing_type="multifamily", on=date(2026, 7, 1)
    )
    # the earlier version's start is unknown, so it counts only from its verified_on
    # (2026-11-24), after its own end: before Act 152 nothing is known, never "in force"
    assert old.status == "unknown" and old.in_force is None
    assert new.record_id == "vt_6081dd_act152" and new.in_force
    after = tl.eligibility_status(
        shipped, state="VT", housing_type="multifamily", on=date(2028, 1, 1)
    )
    assert after.status == "unknown"  # the exemption's own sunset


def test_vt_act179_ends_the_owner_occupancy_adu_rule(shipped):
    before = tl.eligibility_status(shipped, state="VT", housing_type="adu", on=date(2026, 1, 1))
    after = tl.eligibility_status(shipped, state="VT", housing_type="adu", on=date(2027, 1, 1))
    assert before.status == "unknown"  # start_unknown, verified after its end
    assert after.record_id == "vt_4412_adu_act179"


def test_older_runs_reproduce_what_was_known_then():
    old = _rec("old", start=date(2020, 1, 1), end=date(2026, 7, 1), verified_on=date(2025, 1, 1),
               end_verified_on=date(2026, 11, 24))  # fmt: skip
    new = _rec("new", start=date(2026, 7, 1), verified_on=date(2026, 11, 24))
    recs = [old, new]
    # a run as of 2026-09-01 knew only the old rule and not its end
    e = tl.eligibility_status(recs, state="VT", housing_type="adu", on=date(2027, 1, 1),
                              as_of=date(2026, 9, 1))  # fmt: skip
    assert e.record_id == "old"
    # today's run sees the supersession
    e = tl.eligibility_status(recs, state="VT", housing_type="adu", on=date(2027, 1, 1))
    assert e.record_id == "new"


# --------------------------------------------------------------------------- MA mapped area


def test_ma_district_changes_only_its_area_and_time(ma):
    recs, areas = ma
    inside, outside = (-71.0110, 41.8372), (-71.0828, 41.8459)
    e = tl.eligibility_status(recs, state="MA", housing_type="multifamily", on=date(2026, 1, 1),
                              point=inside, areas=areas)  # fmt: skip
    assert e.status == "locally_implemented" and e.in_force and e.level == "area"
    e = tl.eligibility_status(recs, state="MA", housing_type="multifamily", on=date(2026, 1, 1),
                              point=outside, areas=areas)  # fmt: skip
    assert e.status == "unknown"
    e = tl.eligibility_status(recs, state="MA", housing_type="multifamily", on=date(2025, 1, 1),
                              point=inside, areas=areas)  # fmt: skip
    assert e.status == "unknown"
    e = tl.eligibility_status(recs, state="MA", housing_type="adu", on=date(2026, 1, 1),
                              point=inside, areas=areas)  # fmt: skip
    assert e.status == "unknown"
    # without a point, an area rule is not assumed to cover the place
    e = tl.eligibility_status(recs, state="MA", housing_type="multifamily", on=date(2026, 1, 1),
                              areas=areas)  # fmt: skip
    assert e.status == "unknown"


def test_point_in_area_respects_holes():
    feat = {"type": "Polygon", "coordinates": [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
        [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
    ]}  # fmt: skip
    assert tl.point_in_area((1, 1), feat)
    assert not tl.point_in_area((5, 5), feat)
    assert not tl.point_in_area((11, 5), feat)


# --------------------------------------------------------------------------- stays, review


def _rec(rid, **kw):
    base = {
        "record_id": rid,
        "citation": "Test rule",
        "jurisdiction": "VT",
        "housing_type": "adu",
        "status": "legally_effective",
        "verified_on": date(2026, 1, 1),
        "sources": ("https://example.org/rule",),
    }
    base.update(kw)
    return tl.TimelineRecord(**base)


def test_a_stayed_rule_is_not_in_force():
    stay = _rec("stay", start=date(2026, 3, 1), status="stayed", court_status="stayed")
    recs = [_rec("law", start=date(2026, 1, 1)), stay]
    e = tl.eligibility_status(recs, state="VT", housing_type="adu", on=date(2026, 4, 1))
    assert e.status == "stayed" and e.in_force is False
    e = tl.eligibility_status(recs, state="VT", housing_type="adu", on=date(2026, 2, 1))
    assert e.in_force is True


def test_unverified_records_change_nothing():
    rec = _rec("auto", start=date(2026, 1, 1), review="unverified", verified_on=None, sources=())
    e = tl.eligibility_status([rec], state="VT", housing_type="adu", on=date(2026, 6, 1))
    assert e.status == "unknown"


def test_records_are_checked():
    with pytest.raises(ValidationError):
        _rec("bad", status="maybe", start=date(2026, 1, 1))
    with pytest.raises(ValidationError):
        _rec("bad", start=date(2026, 2, 1), end=date(2026, 1, 1), end_verified_on=date(2026, 3, 1))
    with pytest.raises(ValidationError):
        _rec("bad", sources=(), start=date(2026, 1, 1))  # verified without a source
    with pytest.raises(ValidationError):
        # an end's verification date, no end
        _rec("bad", start_unknown=True, end_verified_on=date(2027, 1, 1))
    # an end with no end_verified_on is a scheduled sunset (known when verified): allowed
    assert _rec("sunset", start_unknown=True, end=date(2027, 1, 1)).end == date(2027, 1, 1)


def test_a_scheduled_sunset_is_seen_by_a_run_before_it():
    """A sunset written in the provision is known from verified_on, not from the end date."""
    rule = _rec("sunset", start=date(2025, 1, 1), end=date(2028, 7, 1),
                verified_on=date(2026, 1, 15))  # fmt: skip
    asked = {"state": "VT", "housing_type": "adu", "as_of": date(2026, 11, 24)}
    during = tl.eligibility_status([rule], on=date(2027, 6, 1), **asked)
    assert during.record_id == "sunset" and during.in_force is True
    after = tl.eligibility_status([rule], on=date(2029, 1, 1), **asked)
    assert after.status == "unknown"  # the sunset applies although as_of < end
    # an end found later (a repeal, end_verified_on after as_of) is still ignored
    repeal = rule.model_copy(update={"end_verified_on": date(2027, 1, 1)})
    e = tl.eligibility_status([repeal], on=date(2029, 1, 1), **asked)
    assert e.record_id == "sunset"


def test_eligibility_passes_the_state_fips_code(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text(
        "records:\n"
        "  - record_id: pa_by_code\n"
        "    citation: Act 1 of 2026\n"
        "    jurisdiction: '42'\n"
        "    housing_type: adu\n"
        "    status: legally_effective\n"
        "    start: 2026-01-01\n"
        "    verified_on: 2026-02-01\n"
        "    sources: [pa_code]\n",
        encoding="utf-8",
    )
    e = tl.eligibility("PA", "adu", date(2026, 6, 1), path=path)
    assert e.record_id == "pa_by_code" and e.in_force is True
    assert tl.eligibility("VT", "adu", date(2026, 6, 1), path=path).status == "unknown"


def test_bill_review_queue_lands_unverified():
    rec = _rec("pa_x_pending", provision_id="pa_x", jurisdiction="PA", status="pending",
               start=date(2026, 2, 1), verified_on=date(2026, 11, 24))  # fmt: skip
    quiet = BillStatus("x", "", (("Referred to X, Feb. 1, 2026", date(2026, 2, 1)),))
    assert tl.bill_review_queue([rec], {"pa_x": quiet}, checked_on=date(2026, 12, 1)) == []
    signed = BillStatus("x", "", (
        ("Referred to X, Feb. 1, 2026", date(2026, 2, 1)),
        ("Approved by the Governor, Dec. 10, 2026", date(2026, 12, 10)),
    ))  # fmt: skip
    (q,) = tl.bill_review_queue([rec], {"pa_x": signed}, checked_on=date(2026, 12, 11))
    assert q.review == "unverified" and q.status == "enacted" and q.start == date(2026, 12, 10)
    # the queued record does not change eligibility until verified
    e = tl.eligibility_status([rec, q], state="PA", housing_type="adu", on=date(2027, 1, 1))
    assert e.record_id == "pa_x_pending" and e.in_force is False
    moved = BillStatus("x", "", (
        ("Referred to X, Feb. 1, 2026", date(2026, 2, 1)),
        ("Laid on the table, Dec. 5, 2026", date(2026, 12, 5)),
    ))  # fmt: skip
    (q,) = tl.bill_review_queue([rec], {"pa_x": moved}, checked_on=date(2026, 12, 6))
    assert q.status == "pending" and "Laid on the table" in q.note


def test_shipped_timeline_loads_and_links(shipped):
    ids = {r.record_id for r in shipped}
    for r in shipped:
        for link in (r.supersedes, r.superseded_by):
            assert not link or link in ids
        assert r.review == "verified"


def test_engine_call_uses_the_shipped_timeline():
    # Pittsburgh Ordinance 31: enacted October 27, effective October 29, 2026
    e = tl.eligibility("PA", "adu", date(2026, 10, 28), geoid="4200361000")
    assert e.record_id == "pgh_ord_31_2026_enacted" and e.status == "enacted"
    assert e.in_force is False  # passed, not yet effective
    e = tl.eligibility("PA", "adu", date(2027, 1, 1), geoid="4200361000")
    assert e.record_id == "pgh_ord31_adu_effective" and e.in_force is True
    # a run frozen before the effective date was verified (2026-11-25) still sees "enacted"
    old = tl.eligibility(
        "PA", "adu", date(2027, 1, 1), geoid="4200361000", as_of=date(2026, 11, 24)
    )
    assert old.record_id == "pgh_ord_31_2026_enacted"


# --------------------------------------------------------------------------- start unknown


def test_a_verified_record_with_no_start_must_say_start_unknown():
    with pytest.raises(ValidationError):
        _rec("blank")
    with pytest.raises(ValidationError):
        _rec("both", start=date(2020, 1, 1), start_unknown=True)
    assert _rec("ok", start_unknown=True).in_force_from == date(2026, 1, 1)
    assert _rec("dated", start=date(2020, 1, 1)).in_force_from == date(2020, 1, 1)


def test_start_unknown_counts_only_from_verified_on():
    rec = _rec("local", jurisdiction="4204332800", start_unknown=True,
               verified_on=date(2026, 11, 24))  # fmt: skip
    ask = {"state": "PA", "geoid": "4204332800", "housing_type": "adu"}
    before = tl.eligibility_status([rec], on=date(2026, 11, 23), **ask)
    assert before.status == "unknown" and before.in_force is None
    on = tl.eligibility_status([rec], on=date(2026, 11, 24), **ask)
    assert on.record_id == "local" and on.in_force is True
    # a dated state rule is not outranked before the local record is known to hold
    state = _rec("state", jurisdiction="PA", start=date(2020, 1, 1), status="pending")
    e = tl.eligibility_status([rec, state], on=date(2025, 1, 1), **ask)
    assert e.record_id == "state"


def test_start_unknown_ranks_below_a_dated_record_at_the_same_level():
    """verified_on gates an undated record; it does not make it newer than a dated amendment."""
    undated = _rec("undated", jurisdiction="4204332800", start_unknown=True,
                   verified_on=date(2026, 6, 1))  # fmt: skip
    amended = _rec("amended", jurisdiction="4204332800", start=date(2025, 3, 1),
                   status="pending", verified_on=date(2026, 1, 1))  # fmt: skip
    ask = {"state": "PA", "geoid": "4204332800", "housing_type": "adu"}
    e = tl.eligibility_status([undated, amended], on=date(2026, 7, 1), **ask)
    assert e.record_id == "amended" and set(e.considered) == {"undated", "amended"}
    # Before its verified_on the undated record is not considered at all.
    e = tl.eligibility_status([undated, amended], on=date(2026, 5, 1), **ask)
    assert e.considered == ("amended",)
    # Alone, it still applies from its verified_on.
    assert tl.eligibility_status([undated], on=date(2026, 7, 1), **ask).record_id == "undated"


@pytest.mark.parametrize(
    ("rid", "geoid"),
    [("harrisburg_adu_by_right_2021", "4204332800"),
     ("lackawaxen_accessory_apartment_cu", "4210340776")],
)  # fmt: skip
def test_shipped_records_without_a_start_are_marked_and_wait_for_verified_on(shipped, rid, geoid):
    rec = {r.record_id: r for r in shipped}[rid]
    assert rec.start is None and rec.start_unknown and "start_unknown" in rec.note
    ask = {"state": "PA", "geoid": geoid, "housing_type": "adu"}
    early = tl.eligibility_status(shipped, on=date(2021, 7, 19), **ask)
    assert rid not in early.considered
    later = tl.eligibility_status(shipped, on=rec.verified_on, **ask)
    assert later.record_id == rid and later.in_force is True


def test_every_shipped_record_without_a_start_is_start_unknown(shipped):
    for r in shipped:
        if r.review == "verified" and r.start is None:
            assert r.start_unknown, r.record_id
