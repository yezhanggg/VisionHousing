"""Practical capacity screen: joint-footprint bounds and no double exclusion."""

import numpy as np
import pytest

from tests.test_capacity import CHEAP, DRAWS, KEYS, SEED, with_tables
from visionhousing.engine.capacity import (
    PARAMS,
    SCREENS,
    _frechet,
    load_settings,
    multipliers,
    parse_settings,
    screen_arrays,
    tract_inputs,
)
from visionhousing.engine.core import compute
from visionhousing.ledger import LedgerError, load_ledger, sample
from visionhousing.ledger.loader import read_yaml


@pytest.fixture(scope="module")
def ctx():
    return with_tables()


@pytest.fixture(scope="module")
def v():
    return sample(load_ledger(), n=DRAWS, seed=SEED, ids=PARAMS)


def test_frechet_bounds_hold_the_product_and_match_by_hand():
    m = {"sewer": np.array([0.9, 0.5]), "flood": np.array([0.8, 0.5]), "steep": np.array([0.7, 1])}
    m["wildfire"] = np.ones(2)
    lo, hi = _frechet(m)
    np.testing.assert_allclose(lo, [0.4, 0.0])  # max(0, a + b + c - 2)
    np.testing.assert_allclose(hi, [0.7, 0.5])  # min(a, b, c)
    prod = m["sewer"] * m["flood"] * m["steep"]
    assert np.all(lo <= prod) and np.all(prod <= hi)
    # two screens, the plan's form: overlap of pass shares a, b in [max(0, a+b-1), min(a, b)]
    lo2, hi2 = _frechet({"sewer": np.array([0.6]), "flood": np.array([0.7]),
                         "steep": np.ones(1), "wildfire": np.ones(1)})  # fmt: skip
    assert lo2[0] == pytest.approx(0.3) and hi2[0] == pytest.approx(0.6)


@pytest.mark.parametrize("level,family", [("tract", "adu"), ("county", "fees")])
def test_screened_range_brackets_the_independent_case(ctx, v, level, family):
    inp = tract_inputs(ctx)
    geo = (
        list(inp.geoids[:-1]) if level == "tract" else sorted(set(ctx.tracts["county"].astype(str)))
    )
    added = np.full((len(geo), DRAWS), 10.0)
    arrs = screen_arrays(added, level, geo, inp, v, family)
    lo, hi = arrs.bounds["disjoint"], arrs.bounds["nested"]
    assert np.all(lo <= arrs.screened + 1e-9) and np.all(arrs.screened <= hi + 1e-9)
    assert np.all(hi <= added + 1e-9) and np.all(lo >= -1e-9)
    assert (hi - lo).sum() > 0


def test_one_screen_only_collapses_the_range(v):
    m = {k: np.ones((3, DRAWS)) for k in SCREENS}
    m["flood"] = np.full((3, DRAWS), 0.8)
    lo, hi = _frechet(m)
    np.testing.assert_allclose(lo, 0.8)
    np.testing.assert_allclose(hi, 0.8)


@pytest.mark.filterwarnings("ignore:invalid value")
def test_starter_lots_are_not_excluded_twice_for_sewer(ctx, v):
    inp = tract_inputs(ctx)
    rows = np.arange(len(inp.geoids) - 1)
    starter = multipliers(inp, rows, v, "starter_home_lots")["sewer"]
    adu = multipliers(inp, rows, v, "adu")["sewer"]  # same septic share (single)
    outside = inp.outside[rows][:, None]
    whole = outside >= 1 - 1e-12
    # lots wholly outside a mapped area were already dropped by the rule: nothing left to screen
    assert np.all(starter[whole[:, 0]] == 1.0)
    keep = ~whole[:, 0]
    want = (adu - outside * sample(load_ledger(), n=DRAWS, seed=SEED, ids=PARAMS)[
        "capacity.septic_buildable_share_single"
    ][None, :]) / (1 - outside)  # fmt: skip
    np.testing.assert_allclose(starter[keep], np.clip(want[keep], 0, 1), atol=1e-12)


def test_settings_name_the_rules_and_refuse_unknown_parts():
    s = load_settings()
    assert "capacity.legal_exclusions" in s.rule_ids() and "capacity.dependence" in s.rule_ids()
    assert s.legal_exclusions["starter_home_lots"] == ("sewer_outside",)
    raw = read_yaml(
        __import__("visionhousing.engine.capacity", fromlist=["SETTINGS_PATH"]).SETTINGS_PATH
    )
    raw["legal_exclusions"] = {"id": "x", "families": {"adu": ["flood_everything"]}}
    with pytest.raises(LedgerError):
        parse_settings(raw)


def test_run_reports_the_range_and_its_sentence(ctx):
    res = compute(ctx, KEYS, CHEAP, seed=SEED, draws=DRAWS, capacity_screen=True)
    cap = res.capacity
    for k in KEYS:
        r = cap.reforms[k]
        assert r["screened_disjoint"].p50 <= r["screened"].p50 + 1e-9
        assert r["screened"].p50 <= r["screened_nested"].p50 + 1e-9
        assert "never coincide" in cap.binding[k]["dependence_text"]
    body = res.to_dict()["capacity"]
    assert "screened_nested" in body["reforms"]["adu"]
