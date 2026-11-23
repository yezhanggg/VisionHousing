"""Tract density, the lot-size proxy and area types, checked on hand-made blocks."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from tests.pack_fakes import TESTLAND, sample_blocks
from visionhousing.features import density


def test_weighted_quantile_by_hand():
    values = np.array([100.0, 400.0, 900.0])
    weights = np.array([1.0, 1.0, 2.0])  # running weights 1, 2, 4
    assert density.weighted_quantile(values, weights, 0.25) == 100.0  # reaches 1 of 4
    assert density.weighted_quantile(values, weights, 0.50) == 400.0  # reaches 2 of 4
    assert density.weighted_quantile(values, weights, 0.75) == 900.0
    assert density.weighted_quantile(values[::-1], weights[::-1], 0.5) == 400.0  # order-free
    assert math.isnan(density.weighted_quantile(values, np.zeros(3), 0.5))
    with pytest.raises(ValueError):
        density.weighted_quantile(values, weights, 0.0)
    with pytest.raises(ValueError):
        density.weighted_quantile(values, -weights, 0.5)


def blocks(rows):
    return pd.DataFrame(
        rows, columns=["tract", "housing_units", "population", "land_area_m2", "urban"]
    )


def test_tract_density_by_hand():
    b = blocks(
        [
            # tract A: 1 km2 of land, 300 units; lots 1000 m2 (100 units) and 250 m2 (200 units)
            ("A", 100, 250, 100_000, 1),
            ("A", 200, 400, 50_000, 1),
            ("A", 0, 0, 850_000, 0),  # a park: land but no homes
            # tract B: water only
            ("B", 0, 0, 0, 0),
        ]
    )
    out = density.tract_density(b).set_index("tract")
    a = out.loc["A"]
    assert a["blocks"] == 3 and a["residential_blocks"] == 2
    assert a["housing_units_2020"] == 300 and a["block_land_area_m2"] == 1_000_000
    assert a["housing_density_per_km2"] == 300.0
    # unit-weighted: 200 of the 300 homes are on 250 m2 lots
    assert a["lot_m2_per_unit_p25"] == 250.0
    assert a["lot_m2_per_unit_p50"] == 250.0
    assert a["lot_m2_per_unit_p75"] == 1000.0
    assert a["urban_unit_share"] == 1.0
    water = out.loc["B"]
    assert pd.isna(water["housing_density_per_km2"]) and pd.isna(water["lot_m2_per_unit_p50"])
    assert list(out.reset_index().columns) == list(density.TRACT_COLUMNS)


def test_area_type_rule():
    per_km2_at_threshold = density.URBAN_UNITS_PER_SQ_MILE / density.SQ_M_PER_SQ_MILE * 1e6
    d = pd.DataFrame(
        {
            "housing_density_per_km2": [per_km2_at_threshold + 1, 100.0, 5000.0, np.nan],
            "urban_unit_share": [0.9, 0.9, 0.4, np.nan],
        }
    )
    assert list(density.area_type(d)) == ["urban", "suburban", "rural", "rural"]


def test_testland_sums_and_types():
    b = sample_blocks()
    out = density.tract_density(b)
    tracts = pd.read_csv(TESTLAND / "tracts.csv", dtype={"geoid": str})
    merged = out.merge(tracts, left_on="tract", right_on="geoid")
    assert len(merged) == len(tracts)
    assert (merged["housing_units_2020"] == merged["housing_units"]).all()
    assert set(density.area_type(out)) <= set(density.AREA_TYPES)
    county = density.county_density(b)
    assert county["housing_units_2020"].sum() == b["housing_units"].sum()
