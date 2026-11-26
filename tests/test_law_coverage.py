"""Coverage three ways (residents, lots, land), the ADU categories and the tracker fields."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from visionhousing.law import coverage, hooks, state, tables
from visionhousing.law.schema import MuniRule, StateProvision, is_future

# Two tracts. Tract A (100 detached homes) lies in municipalities 1 and 2 (60 and 40
# housing units in 2020); tract B (50 detached homes) lies wholly in municipality 3.
TRACTS = pd.DataFrame(
    [
        {"geoid": "99001000100", "units_1_detached": 100, "land_area_m2": 2_000_000},
        {"geoid": "99001000200", "units_1_detached": 50, "land_area_m2": 9_000_000},
    ]
)
TRACT_MUNIS = pd.DataFrame(
    [
        {"tract": "99001000100", "county": "99001", "cousub": "00100", "housing_units_2020": 60},
        {"tract": "99001000100", "county": "99001", "cousub": "00200", "housing_units_2020": 40},
        {"tract": "99001000200", "county": "99001", "cousub": "00300", "housing_units_2020": 50},
    ]
)
# Tract A: one block of 100 homes on 200,000 m2. Tract B: one farm block (1 home on
# 5,000,000 m2, capped at 1,000 m2) and one block with no homes (counts nothing).
BLOCKS = pd.DataFrame(
    [
        {"tract": "99001000100", "housing_units": 100, "land_area_m2": 200_000},
        {"tract": "99001000200", "housing_units": 1, "land_area_m2": 5_000_000},
        {"tract": "99001000200", "housing_units": 0, "land_area_m2": 4_000_000},
    ]
)
MUNIS = pd.DataFrame(
    [
        {"county": "99001", "cousub": "00100", "population": 600},
        {"county": "99001", "cousub": "00200", "population": 300},
        {"county": "99001", "cousub": "00300", "population": 100},
    ]
)


def _rule(geoid, adu, status="verified", **kw):
    # adopted in 2019 and seen in force on 2026-11-01 unless a test says otherwise, so the row
    # is in force on the run's date (adoption alone would not be enough)
    row = {"geoid": geoid, "adu_status": adu, "status": status, "adopted_on": "2019",
           "reviewed_on": "2026-11-01"}  # fmt: skip
    return {**row, **kw}


def test_weights_split_tracts_by_housing_units_and_cap_land_per_home():
    w = coverage.muni_weights(TRACTS, TRACT_MUNIS, BLOCKS, max_m2_per_home=1000).set_index("geoid")
    assert w.loc["9900100100", "sf_lots"] == pytest.approx(60)
    assert w.loc["9900100200", "sf_lots"] == pytest.approx(40)
    assert w.loc["9900100300", "sf_lots"] == pytest.approx(50)
    # 100 homes x 1,000 m2 cap = 100,000 m2 of the block's 200,000 m2, split 60/40
    assert w.loc["9900100100", "res_land_ha"] == pytest.approx(6.0)
    assert w.loc["9900100200", "res_land_ha"] == pytest.approx(4.0)
    # the farm block counts one home's cap; the empty block counts nothing
    assert w.loc["9900100300", "res_land_ha"] == pytest.approx(0.1)
    # every detached home is counted once
    assert w["sf_lots"].sum() == pytest.approx(TRACTS["units_1_detached"].sum())


def test_weights_without_blocks_cap_tract_land():
    w = coverage.muni_weights(TRACTS, TRACT_MUNIS, None, max_m2_per_home=1000).set_index("geoid")
    assert w.loc["9900100300", "res_land_ha"] == pytest.approx(50 * 1000 / 10_000)


def test_tract_with_no_homes_splits_evenly():
    tm = TRACT_MUNIS.assign(housing_units_2020=[0, 0, 50])
    w = coverage.muni_weights(TRACTS, tm, None).set_index("geoid")
    assert w.loc["9900100100", "sf_lots"] == pytest.approx(50)


def _rules_with_weights(rows):
    w = coverage.muni_weights(TRACTS, TRACT_MUNIS, BLOCKS, max_m2_per_home=1000)
    return pd.DataFrame(rows).merge(w, on="geoid", how="right")


def test_coverage_three_ways_and_switch_stays_on_residents():
    rules = _rules_with_weights(
        [_rule("9900100200", "by_right"), _rule("9900100300", "unknown", status="draft")]
    )
    c = coverage.county_coverage(rules, MUNIS, {"min_coverage": 0.5}).iloc[0]
    assert c["coverage_residents"] == pytest.approx(0.3)  # 300 of 1,000 residents
    assert c["coverage_lots"] == pytest.approx(40 / 150)
    assert c["coverage_land"] == pytest.approx(4.0 / 10.1)
    assert not c["qualifies"]
    rules = _rules_with_weights([_rule("9900100100", "by_right")])
    c = coverage.county_coverage(rules, MUNIS, {"min_coverage": 0.5}).iloc[0]
    assert c["coverage_residents"] == pytest.approx(0.6) and c["qualifies"]


def test_coverage_without_weights_reports_residents_only():
    rules = pd.DataFrame([_rule("9900100100", "by_right")])
    c = coverage.county_coverage(rules, MUNIS).iloc[0]
    assert c["coverage_residents"] == pytest.approx(0.6)
    assert c["coverage_lots"] is None or pd.isna(c["coverage_lots"])


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (_rule("x", "by_right", adu_detached=True, adu_owner_occupancy=False), "already_permitted"),
        (_rule("x", "by_right", adu_detached=1.0, adu_owner_occupancy=0.0), "already_permitted"),
        (_rule("x", "by_right", adu_detached=1, adu_owner_occupancy=1), "by_right_conditions"),
        (_rule("x", "by_right", adu_detached=False), "by_right_conditions"),
        (_rule("x", "by_right"), "by_right_conditions"),  # detached rule not read
        (_rule("x", "conditional"), "discretionary"),
        (_rule("x", "special_exception"), "discretionary"),
        (_rule("x", "prohibited"), "prohibited"),
        (_rule("x", "not_addressed"), "unknown"),  # no clause on unlisted uses read
        (_rule("x", "not_addressed", unlisted_uses="prohibited"), "prohibited"),  # exclusivity
        (_rule("x", "not_addressed", unlisted_uses="conditional"), "discretionary"),
        (_rule("x", "not_addressed", unlisted_uses="special_exception"), "discretionary"),
        (_rule("x", "unknown"), "unknown"),
        (_rule("x", "by_right", status="draft", adu_detached=True), "unknown"),
        (_rule("x", "by_right", adu_detached=True, adu_owner_occupancy=False,
               effective_date="2099-01-01"), "unknown"),  # not in force yet
        (_rule("x", "by_right", adu_detached=True, adu_owner_occupancy=False,
               adopted_on=None, reviewed_on=None), "unknown"),  # no date known: not in force
        (_rule("x", "by_right", adu_detached=True, adu_owner_occupancy=False,
               reviewed_on="2026-12-01"), "unknown"),  # adoption alone is not effectiveness
        (_rule("x", "by_right", adu_detached=True, adu_owner_occupancy=False,
               adopted_on=None), "already_permitted"),  # seen in force on 2026-11-01
        (_rule("x", "by_right", adu_detached=True, adu_owner_occupancy=False,
               occupancy_restriction="relatives_only"), "by_right_conditions"),  # relatives only
        (_rule("x", "by_right", adu_detached=True, adu_owner_occupancy=False,
               occupancy_restriction=""), "already_permitted"),  # no restriction read
        (_rule("x", "special_exception", occupancy_restriction="relatives_only"),
         "discretionary"),  # the field changes nothing off by-right rows
        (_rule("x", "by_right", adu_detached=True, adu_owner_occupancy=False, adopted_on=None,
               in_force_by="2015-06-01"), "already_permitted"),  # in force by a section date
        (_rule("x", "by_right", adu_detached=True, adu_owner_occupancy=False, adopted_on=None,
               in_force_by="2099-01-01", reviewed_on=None), "unknown"),  # date after as-of
        (_rule("x", "", adu_detached=True), "unknown"),  # blank ADU answer
        (_rule("x", None), "unknown"),
    ],
)  # fmt: skip
def test_adu_category_mapping(row, expected):
    assert coverage.adu_category(row, hooks.load_hooks()) == expected


def test_shipped_category_map_uses_only_known_categories():
    mapping = hooks.load_hooks()["adu_categories"]
    assert set(mapping.values()) <= set(coverage.CATEGORIES)
    assert set(mapping) | {"by_right"} == set(
        s for s in ("by_right", "conditional", "special_exception", "prohibited",
                    "not_addressed", "unknown")
    )  # fmt: skip


def test_county_categories_and_exposure_by_lots():
    rules = _rules_with_weights(
        [
            _rule("9900100100", "by_right", adu_detached=True, adu_owner_occupancy=False),
            _rule("9900100200", "special_exception"),
        ]
    )
    c = coverage.county_adu_categories(rules, MUNIS, hooks.load_hooks()).iloc[0]
    assert c["weight"] == "sf_lots"
    assert c["lots_already_permitted"] == pytest.approx(60)
    assert c["lots_discretionary"] == pytest.approx(40)
    assert c["lots_unknown"] == pytest.approx(50)
    assert c["lots_changed"] == pytest.approx(40) and c["lots_equivalent"] == pytest.approx(60)
    assert c["share_lots_changed_of_read"] == pytest.approx(0.4)
    shares = [c[f"share_lots_{k}"] for k in coverage.CATEGORIES]
    assert sum(shares) == pytest.approx(1.0)
    assert c["share_residents_already_permitted"] == pytest.approx(0.6)


def test_local_law_carries_coverage_and_categories_and_names_lots():
    h = {**hooks.load_hooks(), "engine_wired": True}
    rules = _rules_with_weights(
        [_rule("9900100100", "by_right", doc_id="d", section_cite="§ 1", reviewed_on="2026-11-23",
               name="One", adopted_on="2019")]
    )  # fmt: skip
    law = hooks.local_law_for_engine(rules, MUNIS, h)
    assert law.lot_coverage("99001") == pytest.approx(60 / 150)
    assert law.adu_categories("99001")["unknown"] == pytest.approx(90 / 150)
    assert "40% of its single-family lots" in law.adu_note("99001")
    assert "40% of its single-family lots" in law.plain_words("99001")


def test_future_effective_rule_counts_as_not_read():
    rules = pd.DataFrame([_rule("9900100100", "by_right", effective_date="2099-01-01")])
    c = coverage.county_coverage(rules, MUNIS).iloc[0]
    assert c["residents_read"] == 0
    assert hooks.county_adu_coefficients(rules, MUNIS, hooks.load_hooks()).empty


def test_build_flag_is_not_used_the_dates_are():
    # future_effective said "later" at build time, but by the run's date it is in force
    rules = pd.DataFrame(
        [_rule("9900100100", "by_right", effective_date="2020-03", future_effective=1)]
    )
    assert coverage.county_coverage(rules, MUNIS).iloc[0]["residents_read"] == 600
    assert coverage.adu_category(rules.iloc[0], hooks.load_hooks()) == "by_right_conditions"


# Mixed dates on one county of five municipalities (1,000 residents each), as of 2026-11-24:
MIXED_MUNIS = pd.DataFrame(
    [{"county": "99002", "cousub": f"00{i}00", "population": 1000} for i in range(1, 6)]
)
MIXED_RULES = pd.DataFrame(
    [
        # effective earlier in the year: in force
        _rule("9900200100", "by_right", effective_date="2026-01-01", adu_detached=True,
              adu_owner_occupancy=False, parking_min_per_unit_sf=2),
        # partial date "2026-11" counts from November 30: not in force on November 24
        _rule("9900200200", "by_right", effective_date="2026-11", adu_detached=True,
              adu_owner_occupancy=False),
        # no effective date, adopted 2024: in force
        _rule("9900200300", "special_exception", adopted_on="2024-06-01",
              parking_min_per_unit_sf=0),
        # no date but seen in force on 2026-11-01: read; not addressed with no clause on
        # unlisted uses read, so its ADU answer is unknown
        _rule("9900200400", "not_addressed", adopted_on=None),
        # in force but no ADU answer: read for parking, unread for the ADU hook
        _rule("9900200500", "unknown", adopted_on="2015", parking_min_per_unit_sf=1),
    ]
).assign(doc_id="d", section_cite="§ 1", reviewed_on="2026-11-01", name="M")  # fmt: skip


@pytest.mark.parametrize("as_of", ["2026-11-24", "2026-12-01", "2025-01-01"])
def test_hooks_and_tables_agree_on_mixed_dates(as_of):
    h = {**hooks.load_hooks(), "engine_wired": True, "min_coverage": 0.2}
    j = hooks._joined(MIXED_RULES, MIXED_MUNIS, as_of, h).set_index("geoid")
    w = coverage._weights(MIXED_RULES, MIXED_MUNIS, as_of, h).set_index("geoid")
    assert (j["read"] == w["read"]).all() and (j["adu_read"] == w["adu_read"]).all()
    cov = coverage.county_coverage(MIXED_RULES, MIXED_MUNIS, h, as_of=as_of).iloc[0]
    assert cov["residents_read"] == float(j.loc[j["read"], "population"].sum())
    cats = coverage.county_adu_categories(MIXED_RULES, MIXED_MUNIS, h, as_of=as_of).iloc[0]
    known = 5000 * (1 - cats["share_residents_unknown"])
    coef = hooks.county_adu_coefficients(MIXED_RULES, MIXED_MUNIS, h, as_of=as_of)
    adu_read = float(j.loc[j["adu_read"], "population"].sum())
    assert known == pytest.approx(adu_read)
    if len(coef):
        assert coef.iloc[0]["coverage"] == pytest.approx(adu_read / 5000)
        assert coef.iloc[0]["b"] == pytest.approx(cats["share_residents_unknown"])
        assert coef.iloc[0]["a"] == pytest.approx(cats["share_residents_already_permitted"])
    law = hooks.local_law_for_engine(MIXED_RULES, MIXED_MUNIS, h, as_of=as_of)
    assert law.coverage.iloc[0]["residents_read"] == cov["residents_read"]
    assert law.adu_categories("99002")["unknown"] == pytest.approx(cats["share_lots_unknown"])


def test_mixed_dates_on_the_shipped_as_of():
    h = hooks.load_hooks()
    on = "2026-11-24"
    cov = coverage.county_coverage(MIXED_RULES, MIXED_MUNIS, h, as_of=on).iloc[0]
    assert cov["residents_read"] == 4000  # 100, 300, 400, 500
    cats = coverage.county_adu_categories(MIXED_RULES, MIXED_MUNIS, h, as_of=on).iloc[0]
    assert cats["share_residents_already_permitted"] == pytest.approx(0.2)  # 100 only
    assert cats["share_residents_discretionary"] == pytest.approx(0.2)  # 300
    assert cats["share_residents_unknown"] == pytest.approx(0.6)  # 200, 400, 500
    later = coverage.county_adu_categories(MIXED_RULES, MIXED_MUNIS, h, as_of="2026-12-01")
    assert later.iloc[0]["share_residents_already_permitted"] == pytest.approx(0.4)


# --------------------------------------------------------------------------- tracker


def test_is_future_needs_a_full_date():
    assert is_future("2027-01-01", date(2026, 11, 24)) is True
    assert is_future("2026-01-01", date(2026, 11, 24)) is False
    assert is_future("2027", date(2026, 11, 24)) is None
    assert is_future("", date(2026, 11, 24)) is None


def test_tracker_dates_accept_partial_dates_and_refuse_bad_ones():
    r = MuniRule(geoid="9900100100", adopted_on=date(2024, 5, 20), effective_date="2023")
    assert r.adopted_on == "2024-05-20" and r.effective_date == "2023"
    with pytest.raises(ValueError):
        MuniRule(geoid="9900100100", adopted_on="May 2024")
    with pytest.raises(ValueError):
        StateProvision(provision_id="x", citation="abc", reform="adu", status="pending",
                       applies_to="all", summary_own_words="ten chars long",
                       source_id="s", checked_on=date(2026, 1, 1),
                       verification="maybe")  # fmt: skip


def test_rules_take_document_dates_and_flag_future_effect():
    data = {
        "documents": [
            {"doc_id": "d1", "title": "Doc one", "level": "municipality", "publisher": "P",
             "url": "https://p.example/a.pdf", "licence": "Public", "access": "hand_read",
             "source_id": "pa_municipal_ordinances", "adopted_on": date(2026, 10, 1),
             "effective_date": date(2027, 1, 1)},
        ],
        "rules": [
            {"geoid": "9900100100", "doc_id": "d1", "section_cite": "§ 1", "adu_status": "by_right",
             "method": "hand", "reviewed_on": date(2026, 11, 23), "status": "verified",
             "binding_conditions": "  owner   lives on the lot "},
        ],
    }  # fmt: skip
    munis = MUNIS.assign(name=["One", "Two", "Three"], dced_id="1", zoning_regime="own")
    w = coverage.muni_weights(TRACTS, TRACT_MUNIS, BLOCKS)
    t = tables.muni_zoning_rules(munis, data, w, today=date(2026, 11, 24)).set_index("geoid")
    one = t.loc["9900100100"]
    assert one["adopted_on"] == "2026-10-01" and one["effective_date"] == "2027-01-01"
    assert one["future_effective"] == 1
    assert one["binding_conditions"] == "owner lives on the lot"
    assert one["sf_lots"] == pytest.approx(60)
    assert pd.isna(t.loc["9900100200", "future_effective"])
    later = tables.muni_zoning_rules(munis, data, w, today=date(2027, 2, 1)).set_index("geoid")
    assert later.loc["9900100100", "future_effective"] == 0


def test_county_land_use_reports_lots_and_land_read():
    w = coverage.muni_weights(TRACTS, TRACT_MUNIS, BLOCKS, max_m2_per_home=1000)
    rules = tables.muni_zoning_rules(
        MUNIS.assign(name=["One", "Two", "Three"], dced_id="1", zoning_regime="own"),
        {"documents": [], "rules": []},
        w,
    )
    assert rules["res_land_ha"].isna().all()  # land is kept per county only
    cl = tables.county_land_use(MUNIS.assign(zoning_regime="own"), {}, None, rules, w).iloc[0]
    assert cl["residents_read"] == 0 and cl["sf_lots"] == 150 and cl["sf_lots_read"] == 0
    assert cl["res_land_ha"] == pytest.approx(10.1)


MPC_TEXT = (
    "Act of Jul. 31, 1968, P.L. 805, No. 247 "
    "Section 102. Effective Date.--This act shall take effect January 1, 1969. "
    "Section 503. Applicability.--(1) fees ((1) amended Oct. 24, 2012, P.L.1258, No.154) "
    "(503 amended Sept. 24, 2014, P.L.1, No.2) "
    "ARTICLE V-A Municipal Capital Improvement (Art. added Dec. 19, 1990, P.L.1343, No.209) "
    "Section 503-A. Grant of Power.--text (503-A added Dec. 19, 1990, P.L.1343, No.209) "
    "Section 504-A. Committee.--text (504-A amended June 22, 2000, P.L.495, No.68) "
    "ARTICLE VI Zoning Section 604. Purposes.--text Section 605. Classifications.--text"
)


def test_section_history_reads_the_compiler_notes():
    h = state.section_history(MPC_TEXT, "503")
    assert h.enacted_on == date(1968, 7, 31) and h.effective_date == date(1969, 1, 1)
    assert h.last_amended == date(2014, 9, 24)
    h = state.section_history(MPC_TEXT, "503-A")
    assert h.enacted_on == date(1990, 12, 19) and h.effective_date is None
    assert h.last_amended is None
    h = state.section_history(MPC_TEXT, "504-A")  # its article was added in 1990
    assert h.enacted_on == date(1990, 12, 19) and h.last_amended == date(2000, 6, 22)
    h = state.section_history(MPC_TEXT, "604")  # no notes: the original act
    assert h.enacted_on == date(1968, 7, 31) and h.last_amended is None
    assert state.section_history(MPC_TEXT, "999") == state.SectionHistory(None, None, None)


def test_provision_rows_fill_tracker_fields_and_stay_unknown_without_text():
    data = state.load_provisions()
    rows = {r.provision_id: r for r in state.provision_rows(data, date(2026, 11, 23), MPC_TEXT)}
    assert rows["mpc_503_a"].enacted_on == "1990-12-19"
    assert rows["mpc_503_a"].effective_date == "" and rows["mpc_503_a"].future_effective is None
    assert rows["mpc_604_4"].effective_date == "1969-01-01"
    assert rows["mpc_604_4"].future_effective is False
    bare = state.provision_rows(data, date(2026, 11, 23))
    assert all(r.enacted_on == "" and r.verification == "checked_values" for r in bare)


def test_shipped_bills_carry_introduction_dates_and_conditions():
    for b in state.load_provisions()["bills"]:
        assert b.get("introduced_on"), b["provision_id"]
        assert b.get("binding_conditions"), b["provision_id"]


def test_in_force_by_is_sufficient_on_its_own():
    from visionhousing.law.inforce import in_force

    as_of = date(2026, 1, 1)
    assert in_force({"in_force_by": "2015-06-01"}, as_of)
    # A known future effective date wins over in_force_by: not law yet.
    assert not in_force({"in_force_by": "2015", "effective_date": "2027-01-01"}, as_of)
    assert in_force({"in_force_by": "2027", "effective_date": "2025-01-01"}, as_of)
    assert not in_force({"in_force_by": "2026-03-16"}, as_of)
    # adoption never stands in for effectiveness; a reading on or before as-of does
    assert not in_force({"in_force_by": "2026-03-16", "adopted_on": "2019"}, as_of)
    assert in_force({"in_force_by": "2026-03-16", "reviewed_on": "2025-12-01"}, as_of)
    assert not in_force({"in_force_by": None, "adopted_on": None}, as_of)


def test_in_force_by_is_a_schema_date_and_restriction_is_checked():
    r = MuniRule(geoid="9900100100", in_force_by=date(2015, 6, 1),
                 occupancy_restriction="relatives_only")  # fmt: skip
    assert r.in_force_by == "2015-06-01" and r.occupancy_restriction == "relatives_only"
    with pytest.raises(ValueError):
        MuniRule(geoid="9900100100", occupancy_restriction="family")
    with pytest.raises(ValueError):
        MuniRule(geoid="9900100100", in_force_by="June 2015")


def test_relatives_only_moves_lots_out_of_already_permitted():
    rules = _rules_with_weights(
        [
            _rule("9900100100", "by_right", adu_detached=True, adu_owner_occupancy=False),
            _rule("9900100200", "by_right", adu_detached=True, adu_owner_occupancy=False,
                  occupancy_restriction="relatives_only"),
        ]
    )  # fmt: skip
    cats = coverage.county_adu_categories(rules, MUNIS, hooks.load_hooks(), as_of="2026-11-24")
    row = cats[cats["county"] == "99001"].iloc[0]
    plain = coverage.county_adu_categories(
        rules.assign(occupancy_restriction=""), MUNIS, hooks.load_hooks(), as_of="2026-11-24"
    )
    p = plain[plain["county"] == "99001"].iloc[0]
    assert row["lots_by_right_conditions"] > p["lots_by_right_conditions"]
    assert row["lots_already_permitted"] < p["lots_already_permitted"]
    assert row["lots_changed"] > p["lots_changed"]
