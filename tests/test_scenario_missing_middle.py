"""Missing-middle scenario: hand-checked golden cases, hooks, records and sanity bands."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from visionhousing.features import brackets as br
from visionhousing.features.density import tract_density
from visionhousing.ledger import apply_overrides, load_ledger, load_scenarios, scenario_ledger
from visionhousing.scenarios import missing_middle as mm
from visionhousing.scenarios.adu import adu_funnel, load_fit_table, load_legal_share
from visionhousing.scenarios.adu import scope_from_scenario as adu_scope
from visionhousing.scenarios.missing_middle import (
    LOT_COLUMNS,
    SQ_M_PER_SQ_FT,
    STAGES,
    MiddleScope,
    missing_middle_funnel,
    scope_from_scenario,
    share_at_least,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "testland"
DRAWS = 400


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


@pytest.fixture(scope="module")
def scenarios(ledger):
    return load_scenarios(ledger)


@pytest.fixture(scope="module")
def tracts():
    return pd.read_csv(FIXTURES / "tracts.csv", dtype={"geoid": str, "county": str})


def run(tracts, ledger, scenarios, *, fit=None, seed=7, draws=DRAWS, overrides=None, **kw):
    scenario = scenarios["missing_middle"]
    led = apply_overrides(scenario_ledger(ledger, scenario), overrides)
    scope = scope_from_scenario("missing_middle", scenario)
    return missing_middle_funnel(tracts, led, fit, scope, draws=draws, seed=seed, **kw)


def pa_scaled(tracts, rent_multiplier=1.0):
    """Testland scaled to Pennsylvania's size (about 13 million residents)."""
    factor = 13_000_000 / tracts["population"].sum()
    cols = ["population", "housing_units", "units_1_detached", "units_1_attached"]
    big = tracts.copy()
    big[[*cols, *br.VALUE.columns]] = big[[*cols, *br.VALUE.columns]] * factor
    for c in ("median_rent_b2", "median_gross_rent"):
        big[c] = big[c] * rent_multiplier
    return big


# ---------------------------------------------------------------- golden cases

GOLDEN = {
    "adu.lot_proxy_adjustment": 0.9,
    "missing_middle.sf_only_zoned_share": 0.5,
    "missing_middle.fit_share": 0.4,
    "missing_middle.typology_weight_duplex": 0,
    "missing_middle.typology_weight_triplex": 0,
    "missing_middle.typology_weight_fourplex": 1,
    "missing_middle.typology_weight_fiveplex": 0,
    "missing_middle.typology_weight_sixplex": 0,
    "missing_middle.new_unit_rent_premium": 1.0,
    "missing_middle.rent_ratio_2br": 1.0,
    "missing_middle.acquisition_premium": 0.0,
    "missing_middle.unit_size_sqft": 1000,
    "missing_middle.hard_cost_per_sqft": 200,
    "missing_middle.soft_cost_share": 0.25,
    "missing_middle.developer_margin": 0.15,
    "missing_middle.cap_rate": 0.06,
    "missing_middle.operating_cost_share": 0.35,
    "parking.required_spaces_per_unit_attached": 1,
    "parking.cost_per_space_surface": 5000,
    "missing_middle.redevelopment_hazard_annual": 0.01,
    "missing_middle.sf_turnover_annual": 0.05,
    "missing_middle.ramp_first_year_share": 0.2,
    "missing_middle.ramp_years_to_full": 3,
    "missing_middle.displacement_share": 0.25,
}
"""Every input pinned, so each stage can be worked out by hand."""


def golden_tract(**changes):
    row = {
        "geoid": "99001000100",
        "county": "99001",
        "population": 2500,
        "units_1_detached": 1000,
        "units_1_attached": 0,
        "median_rent_b2": 3000,
        "median_gross_rent": 2800,
        "median_home_value": 150000,
        **{c: 0 for c in br.VALUE.columns},
        "value_125000_149999": 50,
        "value_150000_174999": 50,
    }
    row.update(changes)
    return pd.DataFrame([row])


def test_golden_fourplex_by_hand(ledger, scenarios):
    # Pro forma, 4 units: floor 4,000 sq ft; hard 800,000; soft 200,000; fees 200,000;
    # parking 4 x 5,000 = 20,000; cost 1,220,000; return 183,000; all in 1,403,000.
    # Value: 4 x 3,000 x 12 = 144,000 gross; NOI 93,600; / 0.06 = 1,560,000. RLV = 157,000.
    # Homes: 50 in [125k, 150k), 50 in [150k, 175k): below 157k = (50 + 50 x 7/25) / 100 = 0.64.
    res = run(golden_tract(), ledger, scenarios, overrides=GOLDEN, draws=3)
    assert res.residual_land_value[4][0] == pytest.approx(157_000)
    assert res.feasible_share_by_type[4][0, 0] == pytest.approx(0.64)
    s = {k: res.stages[k][0, 0] for k in STAGES}
    assert s["eligible"] == pytest.approx(1000 * 0.9 * 0.5)  # 450
    assert s["fit"] == pytest.approx(450 * 0.4)  # 180
    assert s["feasible"] == pytest.approx(180 * 0.64)  # 115.2
    ramp = 0.2 + 0.6 + 1 + 1 + 1  # 3.8
    assert s["redeveloped"] == pytest.approx(115.2 * 0.01 * ramp)  # 4.3776
    assert s["units"] == pytest.approx(4 * 4.3776)
    assert s["net_units"] == pytest.approx(3 * 4.3776)  # units - 1 per project
    assert s["net"] == pytest.approx(3 * 4.3776 * 0.75)  # 9.8496
    assert res.net_by_year[0] == pytest.approx(9.8496 * np.array([0.2, 0.6, 1, 1, 1]) / ramp)
    assert res.records["net"].p50 == pytest.approx(9.8496)
    assert res.records["per_100k"].p50 == pytest.approx(9.8496 / 5 / 2500 * 100_000)


def test_golden_turnover_caps_the_hazard(ledger, scenarios):
    changes = {**GOLDEN, "missing_middle.sf_turnover_annual": 0.004}
    res = run(golden_tract(), ledger, scenarios, overrides=changes, draws=2)
    assert res.stages["redeveloped"][0, 0] == pytest.approx(115.2 * 0.004 * 3.8)


def test_golden_acquisition_premium_and_duplex(ledger, scenarios):
    # Duplex: every line halves, RLV = 78,500. With a 25% premium the lot price
    # test is value <= 78,500 / 1.25 = 62,800: homes in [60k, 70k) split 28%.
    changes = {
        **GOLDEN,
        "missing_middle.typology_weight_fourplex": 0,
        "missing_middle.typology_weight_duplex": 1,
        "missing_middle.acquisition_premium": 0.25,
    }
    tract = golden_tract(value_125000_149999=0, value_150000_174999=0, value_60000_69999=100)
    res = run(tract, ledger, scenarios, overrides=changes, draws=2)
    assert res.residual_land_value[2][0] == pytest.approx(78_500)
    assert res.feasible_share[0, 0] == pytest.approx(0.28)
    assert res.stages["net_units"][0, 0] == pytest.approx(res.stages["redeveloped"][0, 0])


def test_golden_mix_weights_are_normalised(ledger, scenarios):
    # Equal raw weights on the duplex and the fourplex: half of each.
    changes = {
        **GOLDEN,
        "missing_middle.typology_weight_duplex": 3,
        "missing_middle.typology_weight_fourplex": 3,
    }
    res = run(golden_tract(), ledger, scenarios, overrides=changes, draws=2)
    assert res.mix_weights[2][0] == pytest.approx(0.5)
    assert res.mix_weights[4][0] == pytest.approx(0.5)
    p2 = res.feasible_share_by_type[2][0, 0]  # 78,500 is below every home here
    assert p2 == 0.0
    assert res.feasible_share[0, 0] == pytest.approx(0.5 * 0.64)
    red = res.stages["redeveloped"][0, 0]
    assert res.stages["units"][0, 0] == pytest.approx(4 * red)  # only fourplexes pencil


def test_max_units_switches_off_larger_buildings(ledger, scenarios):
    changes = {
        **GOLDEN,
        "missing_middle.typology_weight_sixplex": 1,
        "missing_middle.max_units": 4,
    }
    res = run(golden_tract(), ledger, scenarios, overrides=changes, draws=2)
    assert res.mix_weights[6][0] == 0.0
    assert res.mix_weights[4][0] == pytest.approx(1.0)


def test_negative_land_value_gives_zero_and_two_bedroom_fallback(ledger, scenarios):
    low_rent = golden_tract(median_rent_b2=900, median_gross_rent=900)
    res = run(low_rent, ledger, scenarios, overrides=GOLDEN, draws=2)
    assert res.residual_land_value[4][0] < 0
    assert res.stages["feasible"][0, 0] == 0.0
    # A blank two-bedroom rent falls back to gross rent x the two-bedroom ratio.
    blank = golden_tract(median_rent_b2=np.nan, median_gross_rent=3000)
    res = run(blank, ledger, scenarios, overrides=GOLDEN, draws=2)
    assert res.residual_land_value[4][0] == pytest.approx(157_000)


def test_no_owner_homes_falls_back_to_the_median(ledger, scenarios):
    tract = golden_tract(value_125000_149999=0, value_150000_174999=0, median_home_value=140000)
    res = run(tract, ledger, scenarios, overrides=GOLDEN, draws=2)
    assert res.feasible_share[0, 0] == 1.0
    tract = golden_tract(value_125000_149999=0, value_150000_174999=0, median_home_value=160000)
    res = run(tract, ledger, scenarios, overrides=GOLDEN, draws=2)
    assert res.feasible_share[0, 0] == 0.0


def test_top_bracket_is_never_cheap_enough(ledger, scenarios):
    rich = golden_tract(
        value_125000_149999=0, value_150000_174999=0, value_2000000_plus=10, value_lt_10000=10,
        median_rent_b2=20000,
    )  # fmt: skip
    res = run(rich, ledger, scenarios, overrides=GOLDEN, draws=2)
    assert res.residual_land_value[4][0] > 2_000_000
    assert res.feasible_share[0, 0] == pytest.approx(0.5)


# ---------------------------------------------------------------- the fit step


def test_share_at_least_by_hand():
    t = np.array([50, 100, 200, np.sqrt(200 * 400), 400, 800, 1600])
    got = share_at_least(100, 200, 400, t)
    assert got == pytest.approx([1.0, 0.75, 0.5, 0.375, 0.25, 0.0, 0.0])
    assert np.isnan(share_at_least(np.nan, 200, 400, 300))
    assert np.isnan(share_at_least(300, 200, 400, 300))  # out of order
    assert share_at_least(200, 200, 200, np.array([199, 201])) == pytest.approx([1.0, 0.0])


def test_lot_proxy_sets_fit(ledger, scenarios):
    changes = {
        **GOLDEN,
        "missing_middle.min_lot_sqft": 3000,
        "missing_middle.lot_proxy_net_share": 0.5,
    }
    tract = golden_tract(lot_m2_per_unit_p25=300, lot_m2_per_unit_p50=600, lot_m2_per_unit_p75=1200)
    res = run(tract, ledger, scenarios, overrides=changes, draws=2)
    needed = 3000 * SQ_M_PER_SQ_FT / 0.5  # 557.4 m2 of block land per home
    expected = share_at_least(300, 600, 1200, needed)
    assert res.fit_source == "lot_proxy"
    assert res.stages["fit"][0, 0] == pytest.approx(450 * expected)
    assert "missing_middle.min_lot_sqft" in res.records["fit"].assumption_ids
    assert "tiger_2020_tabblock" in res.records["fit"].source_ids
    # A tract with a blank proxy falls back to the ledger share.
    blank = golden_tract(lot_m2_per_unit_p25=np.nan, lot_m2_per_unit_p50=1, lot_m2_per_unit_p75=2)
    res = run(blank, ledger, scenarios, overrides=changes, draws=2)
    assert res.stages["fit"][0, 0] == pytest.approx(450 * 0.4)


def test_measured_fit_wins(ledger, scenarios):
    tract = golden_tract(lot_m2_per_unit_p25=300, lot_m2_per_unit_p50=600, lot_m2_per_unit_p75=1200)
    res = run(tract, ledger, scenarios, fit={"99001000100": 0.3}, overrides=GOLDEN, draws=2)
    assert res.fit_source == "measured"
    assert res.stages["fit"][0, 0] == pytest.approx(450 * 0.3)
    res = run(tract, ledger, scenarios, fit=pd.Series({"other": 0.9}), overrides=GOLDEN, draws=2)
    assert res.stages["fit"][0, 0] == pytest.approx(450 * 0.4)  # tract not measured


def test_testland_block_proxy_runs(ledger, scenarios, tracts):
    blocks = pd.read_csv(FIXTURES / "blocks.csv", dtype={"geoid": str, "tract": str})
    dens = tract_density(blocks.assign(population=0, urban=1))
    with_lots = tracts.merge(dens[["tract", *LOT_COLUMNS]], left_on="geoid", right_on="tract")
    res = run(with_lots, ledger, scenarios)
    assert res.fit_source == "lot_proxy"
    assert np.isfinite(res.stages["net"]).all()
    assert (res.stages["fit"] <= res.stages["eligible"] + 1e-9).all()
    assert run(tracts, ledger, scenarios).fit_source == "parameter"


# ---------------------------------------------------------------- hooks


def test_scope_from_the_scenario_file(scenarios):
    scope = scope_from_scenario("missing_middle", scenarios["missing_middle"])
    assert scope.building_types == ("sf_detached",)
    with pytest.raises(ValueError, match="does not turn on"):
        scope_from_scenario("adu", scenarios["adu"])
    with pytest.raises(ValueError, match="building types"):
        MiddleScope("x", ("five_plus",))


def test_attached_scope_adds_attached_homes(ledger, scenarios):
    tract = golden_tract(units_1_attached=200)
    led = apply_overrides(ledger, GOLDEN)
    res = missing_middle_funnel(
        tract, led, None, MiddleScope("x", ("sf_detached", "sf_attached")), draws=2
    )
    assert res.stages["eligible"][0, 0] == pytest.approx(1200 * 0.45)


def test_overlap_hook_removes_a_share_of_adu_lots(ledger, scenarios):
    changes = {**GOLDEN, "missing_middle.adu_lot_overlap_share": 0.5}
    tract = golden_tract()
    res = run(tract, ledger, scenarios, overrides=changes, draws=3, adu_lots=np.array([100.0]))
    assert res.stages["eligible"][0, 0] == pytest.approx(450 - 50)
    assert "missing_middle.adu_lot_overlap_share" in res.records["eligible"].assumption_ids
    res = run(
        tract, ledger, scenarios, overrides=changes, draws=3, adu_lots=np.full((3, 1), 5000.0)
    )
    assert (res.stages["net"] == 0).all()  # never below zero
    with pytest.raises(ValueError, match="adu_lots"):
        run(tract, ledger, scenarios, draws=3, adu_lots=np.ones(4))


def test_overlap_with_the_adu_funnel(ledger, scenarios, tracts):
    adu = adu_funnel(
        tracts,
        scenario_ledger(ledger, scenarios["adu"]),
        load_fit_table(),
        adu_scope("adu", scenarios["adu"], load_legal_share()),
        draws=DRAWS,
        seed=7,
    )
    alone = run(tracts, ledger, scenarios)
    shared = run(tracts, ledger, scenarios, adu_lots=adu.stages["built"])
    assert (shared.stages["eligible"] <= alone.stages["eligible"] + 1e-9).all()
    assert shared.records["eligible"].p50 < alone.records["eligible"].p50


# ---------------------------------------------------------------- records and tables


def test_records_carry_formula_ids_and_sources(ledger, scenarios, tracts):
    res = run(tracts, ledger, scenarios)
    assert next(r.name.split(" (")[0] for r in res.funnel()).startswith("eligible")
    for stage in STAGES:
        r = res.records[stage]
        assert "P10, P50, P90 of" in r.formula
        assert r.tier == 0
        assert "acs5_summary_file" in r.source_ids
    assert "missing_middle.sf_only_zoned_share" in res.records["eligible"].assumption_ids
    assert "missing_middle.fit_share" in res.records["fit"].assumption_ids
    assert "fees.fee_total_per_unit" in res.records["feasible"].assumption_ids
    assert "missing_middle.acquisition_premium" in res.records["feasible"].assumption_ids
    assert "missing_middle.demolished_per_project" in res.records["net_units"].assumption_ids
    assert "missing_middle.displacement_share" in res.records["net"].assumption_ids
    assert "missing_middle.units_per_project" not in res.records["net"].assumption_ids
    for pid in res.records["net"].assumption_ids:
        assert pid in ledger


def test_tract_table_and_counties_add_up(ledger, scenarios, tracts):
    res = run(pa_scaled(tracts, 2.0), ledger, scenarios)
    table = res.tract_table()
    assert len(table) == len(tracts)
    assert {f"{s}_p50" for s in STAGES} <= set(table.columns)
    counties = res.county_records("net")
    per_county = sum(res.stages["net"][:, res.counties == c].sum(axis=1) for c in counties)
    assert per_county == pytest.approx(res.stages["net"].sum(axis=1))
    assert res.net_by_year.sum(axis=1) == pytest.approx(res.stages["net"].sum(axis=1))


def test_result_shape_matches_the_adu_module(ledger, scenarios, tracts):
    res = run(tracts, ledger, scenarios)
    assert res.stages["net"].shape == (DRAWS, len(tracts))
    assert len(res.geoids) == len(res.counties) == len(tracts)
    assert res.years == (2027, 2028, 2029, 2030, 2031)
    assert res.funnel()[-1] is res.records["net"]


# ---------------------------------------------------------------- sanity against plan Part C


def test_testland_funnel_narrows_and_is_small(ledger, scenarios, tracts):
    res = run(tracts, ledger, scenarios, draws=2000, seed=0)
    r = res.records
    assert r["eligible"].p50 <= tracts["units_1_detached"].sum()
    assert r["fit"].p50 < r["eligible"].p50
    assert r["feasible"].p90 <= r["fit"].p90
    assert r["net"].p90 <= 0.01 * r["eligible"].p50  # five-year units are a sliver of lots


def test_pa_scaled_order_of_magnitude(ledger, scenarios, tracts):
    # Testland rents are low (two-bedroom medians $777-$1,650), so at benchmark fees
    # and costs redevelopment rarely pencils: a small number, never hundreds of thousands.
    res = run(pa_scaled(tracts), ledger, scenarios, draws=2000, seed=0)
    net = res.records["net"]
    assert 0 <= net.p10 <= net.p50 <= net.p90 <= 30_000
    assert res.records["per_100k"].p90 < 50
    # The same state with high-cost-metro rents (twice Testland's): thousands, not
    # hundreds, not hundreds of thousands, over five years.
    rich = run(pa_scaled(tracts, 2.0), ledger, scenarios, draws=2000, seed=0)
    assert 1_000 <= rich.records["net"].p50 <= 50_000
    assert rich.records["net"].p90 < 100_000
    assert rich.records["per_100k"].p50 < 50


def test_module_exports():
    assert set(mm.__all__) >= {"missing_middle_funnel", "scope_from_scenario", "MiddleResult"}
