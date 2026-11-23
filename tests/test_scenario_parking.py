"""Parking reform (benchmark 4): golden case, invariants, transit flag, records, site yield."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from visionhousing.baseline import baseline_from_ledger
from visionhousing.baseline.panel import STRUCTURE_TYPES
from visionhousing.features.brackets import RENT, VALUE
from visionhousing.ledger import load_ledger
from visionhousing.scenarios.fees import baseline_by_type
from visionhousing.scenarios.parking import (
    PARKING_IDS,
    major_stops,
    parking_reform,
    parking_saving,
    tracts_with_stop,
    transit_rich_tracts,
    transit_weights,
)

TESTLAND = Path(__file__).parent / "fixtures" / "testland"
DRAWS = 400
NO_REFORM = {
    "parking.share_still_built_transit_rich": 1.0,
    "parking.share_still_built_elsewhere": 1.0,
}


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


@pytest.fixture(scope="module")
def land():
    return {
        "baseline": baseline_by_type(
            baseline_from_ledger(pd.read_csv(TESTLAND / "permits_county_year.csv"), load_ledger())[
                0
            ]
        ),
        "tracts": pd.read_csv(TESTLAND / "tracts.csv"),
        "blocks": pd.read_csv(TESTLAND / "blocks.csv"),
        "stops": pd.read_csv(TESTLAND / "transit_stops.csv"),
        "boundaries": json.loads((TESTLAND / "boundaries.geojson").read_text(encoding="utf-8")),
    }


def run(land, ledger, **kw):
    kw.setdefault("n_draws", DRAWS)
    kw.setdefault("seed", 3)
    kw.setdefault("boundaries", land["boundaries"])
    return parking_reform(
        land["baseline"], land["tracts"], land["blocks"], land["stops"], ledger, **kw
    )


@pytest.fixture(scope="module")
def central(land, ledger):
    return run(land, ledger)


# ---------------------------------------------------------------- tiny hand-checkable state


def tiny(transit_rich: bool):
    row = dict.fromkeys([*VALUE.columns, *RENT.columns, "rent_no_cash"], 0)
    stock = dict.fromkeys(
        [
            "units_1_detached",
            "units_1_attached",
            "units_2",
            "units_3_4",
            "units_5_9",
            "units_10_19",
            "units_20_plus",
        ],
        0,
    )
    stock["units_20_plus"] = 100
    row.update(stock, geoid="01001000100", county="01001", housing_units=100)
    row["value_250000_299999"] = 100
    row["rent_1000_1249"] = 100  # median rent $1,125
    tracts = pd.DataFrame([row])
    baseline = pd.DataFrame(
        [[500.0, 0.0, 0.0, 1000.0]], index=["01001"], columns=list(STRUCTURE_TYPES)
    )
    flags = pd.Series({"01001000100": transit_rich})
    return baseline, tracts, flags


PINS = {
    "supply.elasticity_medium_run": 1.0,
    "supply.saving_passthrough_k": 0.5,
    "supply.new_construction_value_premium": 0.2,
    "multifamily.cap_rate": 0.055,
    "multifamily.operating_cost_share": 0.38,
    "parking.required_spaces_per_unit_multifamily": 1.5,
    "parking.share_still_built_transit_rich": 0.6,
    "parking.share_still_built_elsewhere": 0.9,
    "parking.mix_structured_transit_rich": 0.4,
    "parking.mix_underground_transit_rich": 0.1,
    "parking.mix_structured_elsewhere": 0.0,
    "parking.mix_underground_elsewhere": 0.0,
    "parking.cost_per_space_surface": 8000.0,
    "parking.cost_per_space_structured": 52000.0,
    "parking.cost_per_space_underground": 73000.0,
    "parking.site_yield_gain": 0.05,
}

EMPTY = pd.DataFrame(columns=["tract", "housing_units", "lat", "lon"])
NO_STOPS = pd.DataFrame(columns=["lat", "lon", "mode", "weekday_departures"])


@pytest.mark.parametrize("rich", [True, False])
def test_golden_parking_case(ledger, rich):
    baseline, tracts, flags = tiny(rich)
    r = parking_reform(
        baseline, tracts, EMPTY, NO_STOPS, ledger, n_draws=20, overrides=PINS, transit_rich=flags
    )
    value = 1125 * 1.2 * 12 * (1 - 0.38) / 0.055  # $182,618.18 per new apartment
    if rich:
        avoided = 1.5 * (1 - 0.6)  # 0.6 spaces per unit
        cost = 0.5 * 8000 + 0.4 * 52000 + 0.1 * 73000  # $32,100 per space
        share_avoided = 0.4
    else:
        avoided = 1.5 * (1 - 0.9)  # 0.15 spaces per unit
        cost = 8000.0  # all surface
        share_avoided = 0.1
    saving = avoided * cost
    channel = 1000 * 1.0 * (saving / value) * 0.5
    site = 1000 * 0.05 * share_avoided
    assert r.state.p10 == r.state.p50 == r.state.p90
    assert r.state.p50 == pytest.approx(channel + site, rel=1e-12)
    assert r.parts["channel_b"].sum(axis=(0, 1))[0] == pytest.approx(channel, rel=1e-12)
    assert r.parts["site_yield"].sum(axis=(0, 1))[0] == pytest.approx(site, rel=1e-12)
    assert r.by_type["units_1"].p50 == 0.0  # 1-unit buildings are not reached
    steps = {s.id: s.record for s in r.steps}
    assert steps["saving"].point() == pytest.approx(saving, rel=1e-9)
    assert steps["total"].point() == pytest.approx(channel + site, rel=1e-9)
    if rich:
        assert channel == pytest.approx(52.73297, rel=1e-6)  # 1,000 x 19,260 / 182,618.18 x 0.5


def test_saving_by_hand_with_mixed_county():
    params = {k: np.array([v]) for k, v in PINS.items()}
    params.update(
        {
            "parking.required_spaces_per_unit_attached": np.array([2.0]),
            "parking.mix_surface_attached": np.array([1.0]),
        }
    )
    weights = np.array([[0.0, 0.25, 0.25, 0.25]])
    s = parking_saving(params, weights)
    # Apartments: 0.25 x 0.6 x $32,100 + 0.75 x 0.15 x $8,000 = $5,715
    assert s["saving"][0, 3, 0] == pytest.approx(0.25 * 0.6 * 32100 + 0.75 * 0.15 * 8000)
    # 2-unit buildings: 2 spaces, all surface: 0.25 x 0.8 x 8,000 + 0.75 x 0.2 x 8,000
    assert s["saving"][0, 1, 0] == pytest.approx(0.25 * 0.8 * 8000 + 0.75 * 0.2 * 8000)
    assert s["saving"][0, 0, 0] == 0.0
    assert s["still_built"][0, 3, 0] == pytest.approx(1.5 - (0.25 * 0.6 + 0.75 * 0.15))


def test_bad_shares_are_refused():
    params = {k: np.array([v]) for k, v in PINS.items()}
    params.update(
        {
            "parking.required_spaces_per_unit_attached": np.array([2.0]),
            "parking.mix_surface_attached": np.array([1.0]),
            "parking.share_still_built_elsewhere": np.array([1.2]),
        }
    )
    with pytest.raises(ValueError, match="between 0 and 1"):
        parking_saving(params, np.zeros((1, 4)))


# ---------------------------------------------------------------- method invariants


def test_zero_reduction_gives_exactly_zero(land, ledger):
    r = run(land, ledger, overrides=NO_REFORM)
    assert np.all(r.added == 0.0)
    assert np.all(r.parts["site_yield"] == 0.0)
    assert r.state.p90 == 0.0
    assert r.crosscheck["state"].p90 == 0.0
    base = land["baseline"].to_numpy()[:, :, None]
    assert np.array_equal(base + r.added, np.broadcast_to(base, r.added.shape))


@pytest.mark.parametrize("pid", ["supply.saving_passthrough_k", "supply.elasticity_medium_run"])
def test_zero_k_or_zero_elasticity_zeroes_the_cost_channel(land, ledger, pid):
    r = run(land, ledger, overrides={pid: 0.0})
    assert np.all(r.parts["channel_b"] == 0.0)
    both = run(land, ledger, overrides={pid: 0.0, "parking.site_yield_gain": 0.0})
    assert np.all(both.added == 0.0)


def test_spaces_still_built_is_editable_and_less_parking_never_reduces_units(land, ledger):
    previous = None
    for share in (1.0, 0.9, 0.75, 0.5, 0.25, 0.0):
        r = run(
            land,
            ledger,
            overrides={
                "parking.share_still_built_transit_rich": share,
                "parking.share_still_built_elsewhere": share,
            },
        )
        if previous is not None:
            assert np.all(r.added >= previous.added)
            assert r.crosscheck["state"].p50 >= previous.crosscheck["state"].p50
        previous = r


def test_dearer_spaces_never_reduce_units(land, ledger):
    cheap = run(land, ledger, overrides={"parking.cost_per_space_structured": 29000.0})
    dear = run(land, ledger, overrides={"parking.cost_per_space_structured": 99000.0})
    assert np.all(dear.added >= cheap.added)


def test_ranges_are_ordered(central):
    for rec in [
        *central.counties.values(),
        central.state,
        *central.by_type.values(),
        *central.crosscheck.values(),
        central.crosscheck_share,
    ]:
        assert rec.p10 <= rec.p50 <= rec.p90, rec.name


def test_same_seed_same_result(land, ledger, central):
    again = run(land, ledger)
    assert np.array_equal(again.added, central.added)
    assert again.state == central.state
    assert not np.array_equal(run(land, ledger, seed=4).added, central.added)


def test_parts_add_up(central):
    assert np.allclose(central.parts["channel_b"] + central.parts["site_yield"], central.added)
    assert central.added.shape == (4, 4, DRAWS)


# ---------------------------------------------------------------- transit flag


def test_major_stops_follow_d07():
    stops = pd.DataFrame(
        {
            "lat": [0, 0, 0],
            "lon": [0, 0, 0],
            "mode": ["rail", "bus", "bus"],
            "weekday_departures": [10, 63, 64],
        }
    )
    assert list(major_stops(stops, 64)["weekday_departures"]) == [10, 64]


def test_distance_rule_flags_tracts_by_share_of_homes(ledger):
    # One degree of latitude is about 69.1 miles: 0.004 deg = 0.28 mi, 0.01 deg = 0.69 mi.
    blocks = pd.DataFrame(
        {
            "tract": ["A", "A", "B", "B"],
            "housing_units": [60, 40, 30, 70],
            "lat": [40.004, 40.02, 40.004, 40.02],
            "lon": [-75.0, -75.0, -75.1, -75.1],
        }
    )
    stops = pd.DataFrame(
        {
            "lat": [40.0, 40.0],
            "lon": [-75.0, -75.1],
            "mode": ["rail", "rail"],
            "weekday_departures": [100, 100],
        }
    )
    flags = transit_rich_tracts(blocks, stops, ledger)
    assert flags.to_dict() == {"A": True, "B": False}  # 60% vs 30% of homes near
    bus = stops.assign(mode="bus")  # 0.28 mi is beyond the 1/4-mile bus radius
    assert not transit_rich_tracts(blocks, bus, ledger).any()


def test_stop_inside_tract_counts():
    square = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    gj = {
        "features": [
            {"properties": {"level": "tract", "geoid": "T1"}, "geometry": square},
            {"properties": {"level": "county", "geoid": "C"}, "geometry": square},
        ]
    }
    inside = pd.DataFrame({"lat": [0.5], "lon": [0.5]})
    outside = pd.DataFrame({"lat": [1.5], "lon": [0.5]})
    assert tracts_with_stop(gj, inside).to_dict() == {"T1": True}
    assert tracts_with_stop(gj, outside).to_dict() == {"T1": False}


def test_testland_has_transit_rich_tracts_in_the_urban_counties(land, ledger):
    flags = transit_rich_tracts(
        land["blocks"], land["stops"], ledger, boundaries=land["boundaries"]
    )
    assert len(flags) == 40
    assert 0 < flags.sum() < 40
    assert not flags[flags.index.str.startswith("99007")].any()  # the rural county
    weights = transit_weights(land["tracts"], flags, ["99001", "99003", "99005", "99007"])
    assert weights.shape == (4, 4)
    assert np.all((weights >= 0) & (weights <= 1))
    assert weights[0, 3] > weights[3, 3]


# ---------------------------------------------------------------- records


def test_records_and_funnel(central):
    rec = central.state
    assert set(PARKING_IDS) <= set(rec.assumption_ids)
    assert "parking.transit_rail_radius_miles" in rec.assumption_ids
    assert {"ucla_its_parking_2026", "vtpi_parking_costs_2022", "bps_county_annual"} <= set(
        rec.source_ids
    )
    assert rec.tier == 0
    ids = [s.id for s in central.steps]
    assert ids[:5] == ["transit", "required", "still_built", "avoided", "cost_per_space"]
    assert ids[-3:] == ["added", "site_yield", "total"]
    assert all(s.record.formula for s in central.steps)


def test_site_yield_is_small_and_swing_table_is_sorted(central, land):
    site = central.parts["site_yield"].sum() / DRAWS
    reached = land["baseline"][["units_2", "units_3_4", "units_5plus"]].to_numpy().sum()
    assert 0 < site < 0.05 * reached
    assert list(central.swing["swing"]) == sorted(central.swing["swing"], reverse=True)
    assert central.crosscheck["state"].p50 > 0
