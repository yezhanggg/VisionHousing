"""SB 1346 leaves Pittsburgh out tract by tract (engine.place_scope), with a flat fallback."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from visionhousing.engine.place_scope import measured_scope
from visionhousing.ledger import load_ledger, load_scenarios, scenario_ledger
from visionhousing.scenarios.adu import adu_funnel, load_fit_table, load_legal_share
from visionhousing.scenarios.adu import scope_from_scenario as adu_scope

TRACTS = Path(__file__).resolve().parent / "fixtures" / "testland" / "tracts.csv"
DRAWS = 200
PARAM = "adu.pittsburgh_share_of_allegheny_lots"


@pytest.fixture(scope="module")
def env():
    ledger = load_ledger()
    scenarios = load_scenarios(ledger)
    t = pd.read_csv(TRACTS, dtype={"geoid": str, "county": str})
    first, second = sorted(t["county"].unique())[:2]
    t["county"] = t["county"].replace({first: "42101", second: "42003"})
    return SimpleNamespace(
        ledger=scenario_ledger(ledger, scenarios["pa_sb1346"]),
        scenario=scenarios["pa_sb1346"],
        fit=load_fit_table(),
        legal=load_legal_share(),
        tracts=t,
    )


def _munis(tracts: pd.DataFrame) -> pd.DataFrame:
    """Allegheny tracts: the first two wholly in Pittsburgh, the third split 30/70, rest out."""
    alle = tracts.loc[tracts["county"] == "42003", "geoid"].tolist()
    rows = []
    for i, g in enumerate(alle):
        if i < 2:
            rows.append((g, "42003", "61000", 500))
        elif i == 2:
            rows += [(g, "42003", "61000", 300), (g, "42003", "00105", 700)]
        else:
            rows.append((g, "42003", "00105", 400))
    return pd.DataFrame(rows, columns=["tract", "county", "cousub", "housing_units_2020"])


def _state(table):
    return SimpleNamespace(derived={("pack_table", "tract_municipalities"): table})


def _run(env, scope, seed=3):
    return adu_funnel(env.tracts, env.ledger, env.fit, scope, draws=DRAWS, seed=seed)


def test_place_scope_names_pittsburgh_by_county_subdivision(env):
    pgh = env.legal.places["Pittsburgh"]
    assert (pgh.county, pgh.cousub, pgh.tract_share_parameter) == ("42003", "61000", PARAM)
    assert env.legal.places["Philadelphia"].cousub is None


def test_exact_tract_exclusion(env):
    table = _munis(env.tracts)
    table.attrs["source_ids"] = ("census_baf_2020_mcd",)
    scope, notes = measured_scope(_state(table), adu_scope("pa_sb1346", env.scenario, env.legal))
    pgh = next(p for p in scope.excluded if p.name == "Pittsburgh")
    assert pgh.by_tract is not None and pgh.share_sources == ("census_baf_2020_mcd",)
    assert "2 of county 42003's" in notes[0] and "1 are split" in notes[0]

    measured = _run(env, scope)
    full = _run(env, replace(scope, excluded=()))  # same types, nothing left out
    alle = np.flatnonzero((env.tracts["county"] == "42003").to_numpy())
    got = measured.stages["eligible"][:, alle]
    want = full.stages["eligible"][:, alle]
    assert np.all(got[:, :2] == 0)  # wholly inside: left out
    assert got[:, 2] == pytest.approx(0.7 * want[:, 2])  # split tract keeps 70%
    assert got[:, 3:] == pytest.approx(want[:, 3:])  # suburbs untouched
    # County total = (1 - measured share of detached homes) x county lots.
    homes = env.tracts.loc[env.tracts["county"] == "42003", "units_1_detached"].to_numpy(float)
    share = (homes[0] + homes[1] + 0.3 * homes[2]) / homes.sum()
    rec = measured.records["place_pittsburgh_share"]
    assert rec.p50 == pytest.approx(share)
    assert "census_baf_2020_mcd" in rec.source_ids
    # Lot proxy and legal share are per draw but the same across a county's tracts here.
    assert got.sum(axis=1) == pytest.approx((1 - share) * want.sum(axis=1))
    # The drawn fallback share is no longer an input; the block file is a source.
    assert PARAM not in measured.records["eligible"].assumption_ids
    assert "census_baf_2020_mcd" in measured.records["eligible"].source_ids
    assert np.all(measured.stages["eligible"][:, env.tracts["county"] == "42101"] == 0)


def test_fallback_without_the_pack_table(env):
    base = adu_scope("pa_sb1346", env.scenario, env.legal)
    scope, notes = measured_scope(_state(None), base)
    assert scope == base
    assert PARAM in notes[0] and "no tract-to-municipality table" in notes[0]
    res = _run(env, scope)
    assert PARAM in res.records["eligible"].assumption_ids
    assert "place_pittsburgh_share" not in res.records
    alle = (env.tracts["county"] == "42003").to_numpy()
    led = env.ledger[PARAM]
    full = _run(env, replace(base, excluded=()))
    ratio = res.stages["eligible"][:, alle] / full.stages["eligible"][:, alle]
    assert np.all(ratio >= 1 - led.high - 1e-9) and np.all(ratio <= 1 - led.low + 1e-9)


def test_scopes_without_a_county_place_pass_through(env):
    hb = adu_scope("pa_hb2186", load_scenarios(load_ledger())["pa_hb2186"], env.legal)
    assert measured_scope(_state(None), hb) == (hb, ())
