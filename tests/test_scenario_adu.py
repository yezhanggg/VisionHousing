"""ADU scenario: calibration tables, scenario hooks, the funnel's working, and sanity bands."""

import itertools
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from visionhousing.features import brackets as br
from visionhousing.ledger import (
    LedgerError,
    apply_overrides,
    load_ledger,
    load_scenarios,
    scenario_ledger,
)
from visionhousing.scenarios import adu
from visionhousing.scenarios.adu import (
    AREA_TYPES,
    BUILDING_TYPES,
    STAGES,
    AduScope,
    adu_funnel,
    legal_share_override,
    load_fit_table,
    load_legal_share,
    parse_fit_table,
    ramp_sum,
    scope_from_scenario,
)

TRACTS = Path(__file__).resolve().parent / "fixtures" / "testland" / "tracts.csv"
DRAWS = 400


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


@pytest.fixture(scope="module")
def scenarios(ledger):
    return load_scenarios(ledger)


@pytest.fixture(scope="module")
def fit():
    return load_fit_table()


@pytest.fixture(scope="module")
def legal():
    return load_legal_share()


@pytest.fixture(scope="module")
def tracts():
    return pd.read_csv(TRACTS, dtype={"geoid": str, "county": str})


def run(tracts, ledger, scenarios, fit, legal, sid="adu", seed=7, draws=DRAWS, overrides=None):
    scenario = scenarios[sid]
    led = apply_overrides(scenario_ledger(ledger, scenario), overrides)
    return adu_funnel(
        tracts, led, fit, scope_from_scenario(sid, scenario, legal), draws=draws, seed=seed
    )


def pa_scaled(tracts):
    """Testland scaled to Pennsylvania's size: about 13 million residents."""
    factor = 13_000_000 / tracts["population"].sum()
    cols = [
        "population",
        "housing_units",
        "units_1_detached",
        "units_1_attached",
        "units_2",
        "units_3_4",
        *br.RENT.columns,
    ]
    big = tracts.copy()
    big[cols] = big[cols] * factor
    return big


# ---------------------------------------------------------------- fit table


def test_fit_table_has_every_cell_with_a_basis_and_range(fit):
    for btype in BUILDING_TYPES:
        for area in AREA_TYPES:
            cell = fit.cells[(btype, area)]
            assert 0 <= cell.low <= cell.central <= cell.high <= 1
            assert cell.basis in ("measured", "partly_measured", "extrapolated")
            assert cell.evidence


def test_fit_table_measured_values(fit):
    detached = fit.cells[("sf_detached", "urban")]
    assert detached.basis == "measured"
    assert detached.central == pytest.approx(0.54)
    assert detached.low <= 0.536 <= detached.high
    # Attached homes fit far less often than detached ones, in every area type.
    for area in AREA_TYPES:
        assert fit.cells[("sf_attached", area)].high < fit.cells[("sf_detached", area)].central
    # Rural cells are not measured and say so.
    assert all(fit.cells[(b, "rural")].basis == "extrapolated" for b in BUILDING_TYPES)


def test_fit_table_provenance_names_the_sources(fit):
    text = fit.provenance
    for word in ("NAIP", "Microsoft building footprints", "parcel", "20 x 30 ft"):
        assert word in text
    assert {"usda_naip_2022", "microsoft_building_footprints"} <= set(fit.source_ids)


def test_fit_position_maps_to_low_central_high(fit):
    cell = fit.cells[("sf_detached", "suburban")]
    shares = fit.shares("sf_detached", np.array(["suburban", "other"]), np.array([-1, 0, 1, 0.5]))
    assert shares[:, 0] == pytest.approx(
        [cell.low, cell.central, cell.high, (cell.central + cell.high) / 2]
    )
    assert np.isnan(shares[:, 1]).all()


def test_fit_table_rejects_a_bad_cell(fit):
    data = {
        "provenance": {"summary": "x"},
        "cells": {
            b: {a: {"central": 0.5, "low": 0.4, "high": 0.6, "basis": "measured", "evidence": "e"}
                for a in AREA_TYPES}
            for b in BUILDING_TYPES
        },
    }  # fmt: skip
    assert parse_fit_table(data).cells[("sf_detached", "urban")].central == 0.5
    data["cells"]["sf_attached"]["rural"]["low"] = 0.7
    with pytest.raises(LedgerError, match=r"sf_attached\.rural"):
        parse_fit_table(data)
    del data["cells"]["two_to_four"]
    with pytest.raises(LedgerError, match=r"two_to_four\.urban: missing"):
        parse_fit_table(data)


# ---------------------------------------------------------------- legal share


def test_legal_share_range_and_its_arithmetic(legal):
    assert (legal.low, legal.central, legal.high) == (0.09, 0.15, 0.22)
    data = adu.read_yaml(adu.LEGAL_PATH)
    s = data["shares"]
    pop = data["population"]
    by_right = (data["read_towns_by_status"]["by_right"] + pop["pittsburgh"]) / pop["pennsylvania"]
    assert s["known_by_right"] == pytest.approx(by_right, abs=0.001)
    central = (
        s["known_by_right"]
        + s["no_zoning"] / 2
        + s["unknown"] * s["by_right_rate_in_read_towns"] / 2
    )
    high = s["known_by_right"] + s["no_zoning"] + s["unknown"] * s["by_right_rate_in_read_towns"]
    assert legal.central == pytest.approx(central, abs=0.005)
    assert legal.high == pytest.approx(high, abs=0.005)
    assert legal.low == pytest.approx(s["known_by_right"], abs=0.005)


def test_legal_share_override_is_a_valid_ledger_change(ledger, legal):
    changed = apply_overrides(ledger, legal_share_override(legal))
    param = changed["adu.already_legal_share"]
    assert (param.low, param.central, param.high) == (0.09, 0.15, 0.22)
    assert param.kind == "calibrated"
    assert ledger["adu.already_legal_share"].kind == "judgement"  # the original is untouched


def test_place_scope_maps_both_cities(legal):
    assert legal.places["Philadelphia"].county == "42101"
    assert legal.places["Philadelphia"].tract_share == 1.0
    pgh = legal.places["Pittsburgh"]
    assert pgh.county == "42003"
    assert pgh.tract_share_parameter == "adu.pittsburgh_share_of_allegheny_lots"


# ---------------------------------------------------------------- scenario hooks


def test_scopes_from_the_scenario_file(scenarios, legal):
    bench = scope_from_scenario("adu", scenarios["adu"], legal)
    hb = scope_from_scenario("pa_hb2186", scenarios["pa_hb2186"], legal)
    sb = scope_from_scenario("pa_sb1346", scenarios["pa_sb1346"], legal)
    assert bench.building_types == ("sf_detached", "sf_attached")
    assert hb.building_types == ("sf_detached", "sf_attached", "two_to_four")
    assert sb.building_types == ("sf_detached",)
    assert {p.name for p in sb.excluded} == {"Philadelphia", "Pittsburgh"}
    assert scenarios["pa_sb1346"].overrides["adu.utility_connection_fee"] == 0


def test_scope_errors(scenarios):
    with pytest.raises(ValueError, match="does not turn on the ADU reform"):
        scope_from_scenario("missing_middle", scenarios["missing_middle"])
    with pytest.raises(ValueError, match="no tract mapping"):
        scope_from_scenario("pa_sb1346", scenarios["pa_sb1346"], None)
    with pytest.raises(ValueError):
        AduScope(name="x", building_types=("five_plus",))


# ---------------------------------------------------------------- the working


def test_ramp_sum():
    assert ramp_sum(0.5, 3, 5) == pytest.approx([0.5, 0.75, 1, 1, 1])
    assert ramp_sum(0.3, 1, 5) == pytest.approx([1, 1, 1, 1, 1])
    draws = ramp_sum(np.array([0.5, 0.8]), np.array([3.0, 5.0]), 5)
    assert draws.shape == (2, 5)
    assert draws[1] == pytest.approx([0.8, 0.85, 0.9, 0.95, 1.0])


def test_funnel_narrows_at_every_step(tracts, ledger, scenarios, fit, legal):
    r = run(tracts, ledger, scenarios, fit, legal)
    for a, b in itertools.pairwise(STAGES):
        assert np.all(r.stages[b] <= r.stages[a] + 1e-9), (a, b)
    assert r.years == (2027, 2028, 2029, 2030, 2031)
    assert r.net_by_year.sum(axis=1) == pytest.approx(r.stages["net"].sum(axis=1))


def test_feasible_share_uses_the_rent_brackets(tracts, ledger, scenarios, fit, legal):
    row = tracts.iloc[0]
    counts = br.counts_from_row(row, br.RENT)
    premium = ledger["adu.new_unit_rent_premium"]
    # Pin the premium so the threshold can be rebuilt by hand.
    r = run(
        tracts,
        ledger,
        scenarios,
        fit,
        legal,
        overrides={"adu.nonfinancial_share": 0.0, "adu.new_unit_rent_premium": premium.central},
    )
    ratio = row["median_rent_b1"] / row["median_gross_rent"]
    for d in (0, 5, 17):
        threshold = r.breakeven_rent[d, 0] / (ratio * premium.central)
        want = br.share_above(counts, br.RENT, threshold).value
        assert r.feasible_share[d, 0] == pytest.approx(want)


def test_family_use_share_is_added_on_top(tracts, ledger, scenarios, fit, legal):
    base = run(tracts, ledger, scenarios, fit, legal, overrides={"adu.nonfinancial_share": 0.0})
    fam = run(tracts, ledger, scenarios, fit, legal, overrides={"adu.nonfinancial_share": 0.1})
    want = base.feasible_share + 0.1 * (1 - base.feasible_share)
    assert fam.feasible_share == pytest.approx(want)


def test_no_cash_renters_falls_back_to_the_one_bedroom_median(
    tracts, ledger, scenarios, fit, legal
):
    t = tracts.iloc[:2].copy()
    t.loc[:, list(br.RENT.columns)] = 0
    t.loc[t.index[0], "median_rent_b1"] = 9000  # pays everywhere
    t.loc[t.index[1], "median_rent_b1"] = 100  # pays nowhere
    r = run(t, ledger, scenarios, fit, legal, overrides={"adu.nonfinancial_share": 0.0})
    assert np.all(r.feasible_share[:, 0] == 1.0)
    assert np.all(r.feasible_share[:, 1] == 0.0)


def test_sb1346_leaves_out_philadelphia_and_part_of_allegheny(
    tracts, ledger, scenarios, fit, legal
):
    t = tracts.copy()
    first, second = sorted(t["county"].unique())[:2]
    t["county"] = t["county"].replace({first: "42101", second: "42003"})
    sb = run(t, ledger, scenarios, fit, legal, sid="pa_sb1346")
    philly = (t["county"] == "42101").to_numpy()
    allegheny = (t["county"] == "42003").to_numpy()
    assert np.all(sb.stages["eligible"][:, philly] == 0)
    share = ledger["adu.pittsburgh_share_of_allegheny_lots"]
    keep = sb.stages["eligible"][:, allegheny]
    # Same tract on the original codes: no exclusion.
    full = run(tracts, ledger, scenarios, fit, legal, sid="pa_sb1346").stages["eligible"][
        :, allegheny
    ]
    ratio = keep / full
    assert np.all(ratio >= 1 - share.high - 1e-9)
    assert np.all(ratio <= 1 - share.low + 1e-9)


def test_records_carry_formulas_ids_and_sources(tracts, ledger, scenarios, fit, legal):
    r = run(tracts, ledger, scenarios, fit, legal)
    funnel = r.funnel()
    assert [rec.unit for rec in funnel] == ["lots", "lots", "lots", "units", "units"]
    words = ["eligible lots =", "fit share", "break-even rent", "take-up", "displacement"]
    for record, word in zip(funnel, words, strict=True):
        assert "seeded draws" in record.formula and word in record.formula
        assert record.tier == 0
        assert record.inputs
        assert "acs5_summary_file" in record.source_ids
    net = r.records["net"]
    for pid in (
        "adu.already_legal_share",
        "adu.fit_range_position",
        "adu.nonfinancial_share",
        "adu.takeup_feasible_annual",
        "adu.ramp_first_year_share",
        "adu.displacement_share",
        "adu.hard_cost_per_sqft",
    ):
        assert pid in net.assumption_ids
    assert fit.id in net.source_ids
    assert "adu.fit_range_position" not in r.records["eligible"].assumption_ids
    assert "2027-2031" in net.name
    assert "per 100k" in r.records["per_100k"].unit
    assert "26" in r.records["per_100k"].formula  # the evidence figure it is compared with


def test_tract_and_county_tables(tracts, ledger, scenarios, fit, legal):
    r = run(tracts, ledger, scenarios, fit, legal)
    table = r.tract_table()
    assert len(table) == len(tracts)
    for stage in STAGES:
        assert (table[f"{stage}_p10"] <= table[f"{stage}_p50"]).all()
        assert (table[f"{stage}_p50"] <= table[f"{stage}_p90"]).all()
    counties = r.county_records()
    assert set(counties) == set(tracts["county"])
    # Counties sum to the state, draw by draw.
    total = sum(r.stages["net"][:, r.counties == c].sum(axis=1) for c in counties)
    assert total == pytest.approx(r.stages["net"].sum(axis=1))


# ---------------------------------------------------------------- sanity bands


def test_testland_five_year_units_are_a_small_share_of_eligible(
    tracts, ledger, scenarios, fit, legal
):
    r = run(tracts, ledger, scenarios, fit, legal)
    eligible, net = r.records["eligible"], r.records["net"]
    assert net.p50 > 0
    assert net.p90 < 0.02 * eligible.p50


def test_pennsylvania_sized_testland_lands_in_the_plan_band(tracts, ledger, scenarios, fit, legal):
    """Sanity anchor: thousands to a few tens of thousands of ADUs over five years."""
    led = apply_overrides(ledger, legal_share_override(legal))
    big = pa_scaled(tracts)
    for sid in ("adu", "pa_hb2186", "pa_sb1346"):
        net = run(big, led, scenarios, fit, legal, sid=sid, draws=1000).records["net"]
        assert 1_000 <= net.p10 <= net.p50 <= net.p90 <= 50_000, sid
