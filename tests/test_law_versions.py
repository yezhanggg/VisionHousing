"""Law layer L1: provision versions, the date rules, Vermont's § 4412 versions and place weights.

Fixture cases follow the plan's Vermont calendar test: a duplex right in year-round districts;
the four-or-fewer-unit right only where municipal water AND sewer serve; the January 1, 2028
exception for districts that require more than four units; the incremental comparison with a
district's existing rights; unknown service staying unknown; and the ADU's flood/fluvial
qualification. Everything here is offline: shipped YAML and small hand-made frames.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from pydantic import ValidationError

from visionhousing.law import inforce
from visionhousing.law import timeline as tl
from visionhousing.law.places import (
    block_place_shares,
    county_place_share,
    place_tract_shares,
    points_in_feature,
)
from visionhousing.law.rights import District, freeze_difference, legal_increment, state_floor

SERVED = dict(year_round_residential=True, allows_residential=True,
              municipal_water=True, municipal_sewer=True)  # fmt: skip


@pytest.fixture(scope="module")
def shipped():
    return tl.load_timeline()


# --------------------------------------------------------------------------- provision versions


def test_provision_version_table_keeps_the_dates_apart(shipped):
    t = tl.provision_versions(shipped).set_index("record_id")
    assert list(t.reset_index().columns) == list(tl.PROVISION_VERSION_COLUMNS)
    pgh = t.loc["pgh_ord31_adu_effective"]
    assert pgh["effective_start"] == date(2026, 10, 29)
    assert pgh["adopted_on"] == date(2026, 10, 27)  # adoption kept, not used as effect
    assert pgh["version_state"] == "final" and pgh["source_fingerprint"].startswith("sha256:")
    assert pgh["excerpt_locator"] and pgh["source_url"].startswith("https://pittsburgh.legistar")
    drafts = t[t["provision_id"] == "pgh_ord_2025_1545"]
    assert set(drafts["version_state"]) == {"draft", "final"}
    assert (drafts.loc[drafts["version_state"] == "draft", "status"] == "pending").all()


def test_a_draft_text_cannot_be_in_force():
    with pytest.raises(ValidationError):
        tl.TimelineRecord(record_id="x", citation="Bill X", jurisdiction="PA",
                          status="legally_effective", start=date(2026, 1, 1),
                          version_state="draft", verified_on=date(2026, 1, 2),
                          sources=("https://example.org",))  # fmt: skip


def test_every_shipped_final_or_draft_version_names_its_source(shipped):
    for r in shipped:
        if r.version_state:
            assert r.sources and r.source_fingerprint.startswith("sha256:"), r.record_id
            assert r.excerpt_locator, r.record_id


def test_pa_bills_checked_vintage_and_still_pending(shipped):
    by = {r.record_id: r for r in shipped}
    for rid in ("pa_hb2186_pending", "pa_sb1346_pending"):
        assert by[rid].status == "pending" and by[rid].last_checked_on == date(2026, 11, 25)
    assert "June 5, 2026" in by["pa_hb2186_pending"].note
    assert "November 17, 2026" in by["pa_sb1346_pending"].note
    e = tl.eligibility("PA", "adu", date(2027, 6, 1), geoid="4212300000")
    assert e.in_force is not True  # a pending bill never changes the baseline


# --------------------------------------------------------------------------- date rules


def test_adoption_alone_never_puts_a_rule_in_force():
    row = {"adopted_on": "2015-01-01", "reviewed_on": "2026-11-23"}
    assert inforce.in_force(row, date(2026, 11, 22), "read_from_reviewed_on") is False
    assert inforce.in_force(row, date(2026, 11, 24), "read_from_reviewed_on") is True
    assert (
        inforce.in_force({"adopted_on": "2015"}, date(2026, 11, 24), "read_from_reviewed_on")
        is False
    )
    assert inforce.in_force({**row, "effective_date": "2027-01-01"}, date(2026, 11, 24)) is False
    assert inforce.in_force(row, date(2026, 11, 24), "unread") is False


def test_not_addressed_needs_a_clause_on_unlisted_uses():
    assert inforce.adu_answered("not_addressed") is False
    assert inforce.adu_answered("not_addressed", "") is False
    for clause in ("prohibited", "conditional", "special_exception"):
        assert inforce.adu_answered("not_addressed", clause) is True
    assert inforce.adu_answered("by_right") is True and inforce.adu_answered("unknown") is False


# --------------------------------------------------------------------------- Vermont § 4412


def test_vt_2027_duplex_in_any_year_round_district():
    f = state_floor(District(year_round_residential=True, allows_residential=True,
                             municipal_water=False, municipal_sewer=True,
                             local_max_units=1), date(2027, 6, 1))  # fmt: skip
    assert f.version == "vt_4412_1d_act179"
    assert f.duplex is True and f.small_multiunit is False  # water missing
    assert f.floor_units == 2 and f.max_units_by_right == 2


def test_vt_four_units_need_water_and_sewer_and_unknown_service_stays_unknown():
    served = state_floor(District(**SERVED, required_min_units=0, local_max_units=1),
                         date(2027, 6, 1))  # fmt: skip
    assert served.small_multiunit is True and served.floor_units == 4
    unknown = state_floor(District(year_round_residential=True, allows_residential=True,
                                   municipal_water=None, municipal_sewer=True,
                                   local_max_units=1), date(2027, 6, 1))  # fmt: skip
    assert unknown.small_multiunit is None and unknown.floor_units is None
    assert unknown.max_units_by_right is None  # never taken as "no right"


def test_vt_2028_exception_is_a_dated_change_with_a_scope_test():
    big = District(**SERVED, required_min_units=5, local_max_units=1)
    before = state_floor(big, date(2027, 12, 31))
    after = state_floor(big, date(2028, 1, 1))
    assert before.version == "vt_4412_1d_act179" and before.small_multiunit is True
    assert after.version == "vt_4412_1d_act179_9a" and after.small_multiunit is False
    assert after.exception_applied and after.duplex is True
    # outside the exception's scope the 2028 text changes nothing
    plain = District(**SERVED, required_min_units=0, local_max_units=1)
    assert state_floor(plain, date(2028, 6, 1)).floor_units == 4
    # scope not known: unknown, not lost
    open_q = District(**SERVED, required_min_units=None, local_max_units=1)
    assert state_floor(open_q, date(2028, 6, 1)).small_multiunit is None
    assert state_floor(open_q, date(2027, 6, 1)).small_multiunit is True  # no exception yet


def test_vt_incremental_comparison_with_existing_four_unit_rights():
    f = state_floor(District(**SERVED, required_min_units=0, local_max_units=4), date(2027, 6, 1))
    assert legal_increment(f.max_units_by_right, 6) == (5, 6)
    assert legal_increment(f.max_units_by_right, 4) == ()  # pure legalization increment zero
    assert legal_increment(None, 6) is None


def test_vt_adu_flood_and_fluvial_qualification():
    inside = state_floor(District(flood_or_fluvial_bylaw=True), date(2027, 6, 1))
    outside = state_floor(District(flood_or_fluvial_bylaw=False), date(2027, 6, 1))
    unknown = state_floor(District(), date(2027, 6, 1))
    assert outside.adu_protected is True and inside.adu_protected is False
    assert unknown.adu_protected is None
    assert outside.adu_version == "vt_4412_adu_act179"


def test_current_law_in_both_arms_and_freeze_today_is_separate(shipped):
    big = District(**SERVED, required_min_units=5, local_max_units=1)
    rows = freeze_difference(big, [date(2027, 6, 1), date(2028, 6, 1)],
                             freeze_on=date(2026, 11, 25))  # fmt: skip
    assert [r["differs"] for r in rows] == [False, True]
    assert rows[1]["current_law"].max_units_by_right == 2  # 2028 text from its date
    assert rows[1]["frozen"].max_units_by_right == 4  # today's text held throughout
    sched = tl.schedule_comparison(shipped, state="VT", housing_type="missing_middle",
                                   dates=[date(2027, 1, 1), date(2028, 1, 1)],
                                   freeze_on=date(2026, 11, 25))  # fmt: skip
    assert [s.current_record for s in sched] == ["vt_4412_1d_act179", "vt_4412_1d_act179_9a"]
    assert [s.differs for s in sched] == [False, True]


# --------------------------------------------------------------------------- place weights

TM = pd.DataFrame(
    {
        "tract": ["99003000100", "99003000200", "99003000200", "99003000300"],
        "county": ["99003"] * 4,
        "cousub": ["61000", "61000", "12345", "12345"],
        "housing_units_2020": [100, 30, 70, 50],
    }
)
SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}


def test_place_tract_shares_from_the_block_assignment():
    s = place_tract_shares(TM, "99003", "61000").set_index("tract")
    assert s.loc["99003000100", "share"] == 1.0 and not s.loc["99003000100", "split"]
    assert s.loc["99003000200", "share"] == pytest.approx(0.3) and s.loc["99003000200", "split"]
    assert s.loc["99003000300", "share"] == 0.0


def test_block_points_in_a_boundary_give_the_same_kind_of_share():
    blocks = pd.DataFrame(
        {
            "tract": ["99003000200"] * 3 + ["99003000300"],
            "housing_units": [30, 50, 20, 9],
            "lon": [1.0, 3.0, 2.5, 5.0],
            "lat": [1.0, 1.0, 0.5, 5.0],
        }
    )
    assert points_in_feature(blocks["lon"], blocks["lat"], SQUARE).tolist() == [
        True, False, False, False]  # fmt: skip
    s = block_place_shares(blocks, SQUARE).set_index("tract")
    assert list(s.index) == ["99003000200"]
    assert s.loc["99003000200", "share"] == pytest.approx(0.3)


def test_county_share_weighs_tracts_by_detached_homes():
    tracts = pd.DataFrame(
        {"geoid": ["99003000100", "99003000200", "99003000300"], "units_1_detached": [10, 100, 90]}
    )
    shares = place_tract_shares(TM, "99003", "61000")
    out = county_place_share(tracts, shares, "99003")
    assert out["share"] == pytest.approx((10 + 30) / 200)
    assert out["tracts_split"] == 1 and out["tracts_in_place"] == 2
