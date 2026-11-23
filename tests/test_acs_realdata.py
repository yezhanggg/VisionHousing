"""Real-data check: Pennsylvania ACS 2020-2024 tracts against the published state row.

Runs against the live summary files (the state's rows are cached in
``data/raw/acs``). Not part of ``make test``; run it with ``make test-realdata``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from visionhousing.data.sources import acs
from visionhousing.features import acs as fx

pytestmark = pytest.mark.realdata

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
PA_TRACTS_2020 = 3446  # official count of 2020 census tracts, water-only tracts included


@pytest.fixture(scope="module")
def raw():
    return acs.fetch_acs("PA", 2024, RAW)


def test_tract_and_county_counts(raw):
    counts = raw["level"].value_counts().to_dict()
    assert counts == {"tract": PA_TRACTS_2020, "county": 67, "state": 1}


def test_tracts_with_housing(raw):
    features = fx.tract_features(raw)
    with_housing = int((features["housing_units"] > 0).sum())
    print(f"PA tracts with housing units: {with_housing} of {len(features)}")
    assert 3350 < with_housing < PA_TRACTS_2020
    assert "42049990000" in set(features.loc[features["housing_units"] == 0, "geoid"])


@pytest.mark.parametrize(
    "variable",
    ["B01003_001E", "B25001_001E", "B25024_002E"],  # population, housing units, 1-unit detached
)
def test_tract_sums_match_the_state_table(raw, variable):
    state = raw.loc[raw.level == "state", variable].iloc[0]
    total = raw.loc[raw.level == "tract", variable].sum()
    assert abs(total - state) <= 0.0005 * state, (variable, total, state)


def test_published_state_values(raw):
    state = raw[raw.level == "state"].iloc[0]
    assert state["B01003_001E"] == 13018639
    assert state["B25001_001E"] == 5806452
    assert state["B25024_002E"] == 3287467


def test_every_rent_value_has_a_method(raw):
    features = fx.tract_features(raw)
    for k in range(4):
        methods = set(features[f"median_rent_b{k}_method"])
        assert methods <= {"tract", "county_ratio", "missing"}
    filled = features["median_rent_b1"].notna().mean()
    assert filled > 0.9
