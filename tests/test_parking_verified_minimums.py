"""Parking: verified local minimums replace the default (variant parking_verified_minimums)."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from visionhousing.engine.context import load_context
from visionhousing.engine.core import run
from visionhousing.law.parking_rules import verified_parking_minimums
from visionhousing.ledger import load_ledger, sample
from visionhousing.scenarios.parking import (
    REQ_ATT,
    REQ_MF,
    SAVING_IDS,
    SPREAD,
    STILL_EL,
    STILL_TR,
    VOLUNTARY_IDS,
    MinimumMix,
    binding_share,
    minimum_mix,
    parking_saving,
)

N = 40
DRAWS = 300


def params(**fixed):
    p = sample(load_ledger(), n=N, seed=5, ids=(*SAVING_IDS, *VOLUNTARY_IDS, SPREAD))
    for k, v in fixed.items():
        p[k] = np.full(N, float(v))
    return p


def rule(geoid, mf=None, sf=None, status="verified", effective="", reviewed="2026-11-23"):
    return {
        "geoid": geoid,
        "name": geoid,
        "doc_id": "d" + geoid,
        "section_cite": "s",
        "status": status,
        "parking_min_per_unit_mf": mf,
        "parking_min_per_unit_sf": sf,
        "effective_date": effective,
        "adopted_on": "",
        "in_force_by": "",
        "reviewed_on": reviewed,
    }


PITTSBURGH = rule("4200361000", mf=0, sf=0, effective="2026-10-29")


# ------------------------------------------------------------ the law accessor


def test_pittsburgh_zero_minimum_counts_only_from_its_effective_date():
    rules = pd.DataFrame([PITTSBURGH])
    before = verified_parking_minimums(rules, as_of="2026-10-28")
    after = verified_parking_minimums(rules, as_of="2026-10-29")
    assert before.empty
    assert len(after) == 1 and after.iloc[0]["min_apartment"] == 0
    assert after.iloc[0]["min_small"] == 0


def test_only_verified_rows_with_a_number_are_kept_and_blank_stays_blank():
    rules = pd.DataFrame(
        [
            rule("4200100001", mf=1.5, sf=2),
            rule("4200100002", sf=2),  # no apartment figure: apartments keep the default
            rule("4200100003", mf=1.0, status="draft"),
            rule("4200100004"),  # verified, no parking answer
        ]
    )
    t = verified_parking_minimums(rules, as_of="2026-11-24").set_index("geoid")
    assert list(t.index) == ["4200100001", "4200100002"]
    assert t.loc["4200100001", "min_apartment"] == 1.5
    assert t.loc["4200100001", "min_small"] == 1.5  # multi-family figure first
    assert pd.isna(t.loc["4200100002", "min_apartment"])
    assert t.loc["4200100002", "min_small"] == 2.0  # single-family figure as fallback


# ------------------------------------------------------------ the mix

TRACTS = pd.DataFrame(
    {
        "geoid": ["42001000100", "42001000200"],
        "county": ["42001", "42001"],
        "housing_units": [100, 100],
        "units_1_detached": [10, 10],
        "units_1_attached": [0, 0],
        "units_2": [10, 10],
        "units_3_4": [10, 10],
        "units_5_9": [20, 20],
        "units_10_19": [0, 0],
        "units_20_plus": [0, 0],
    }
)
# Tract 1 split evenly between municipalities A and B; tract 2 all in C.
TRACT_MUNIS = pd.DataFrame(
    {
        "tract": ["42001000100", "42001000100", "42001000200"],
        "county": ["42001"] * 3,
        "cousub": ["00001", "00002", "00003"],
        "housing_units_2020": [50, 50, 100],
    }
)
FLAGS = pd.Series({"42001000100": True, "42001000200": False})


def _mix(rows):
    table = verified_parking_minimums(pd.DataFrame(rows), as_of="2026-11-24")
    return minimum_mix(TRACTS, TRACT_MUNIS, FLAGS, table, ["42001"])


def test_mix_weights_by_homes_within_each_transit_class_and_unknown_stays_default():
    mix = _mix([rule("4200100001", mf=0)])  # A: half of the transit-rich tract
    k0 = list(mix.levels).index(0.0)
    assert mix.tr[0, 3, k0] == pytest.approx(0.5)
    assert mix.el[0, 3].sum() == 0  # tract 2 (C) unread: all default
    assert mix.verified[0, 3] == pytest.approx(0.25)
    assert mix.zero[0, 3] == pytest.approx(0.25)
    assert np.all(mix.tr[0, 0] == 0)  # 1-unit homes are not reached


def test_no_verified_rows_is_the_benchmark_exactly():
    p = params()
    w = np.array([[0.0, 0.4, 0.4, 0.6]])
    empty = _mix([])
    assert empty.levels.size == 0
    for response in ("share", "provided", "voluntary"):
        for kept in (0.0, 0.5):
            a = parking_saving(p, w, response, kept)
            b = parking_saving(p, w, response, kept, empty)
            np.testing.assert_allclose(a["avoided"], b["avoided"])
            np.testing.assert_allclose(a["saving"], b["saving"])


def _all_verified(m):
    """One county, every reached home under a verified minimum m (elsewhere tracts)."""
    levels = np.array([m])
    el = np.zeros((1, 4, 1))
    el[0, 1:, 0] = 1.0
    return MinimumMix(levels, np.zeros((1, 4, 1)), el, el[..., 0], el[..., 0] * (m == 0))


@pytest.mark.parametrize("response", ["share", "provided"])
def test_verified_minimum_below_voluntary_spaces_avoids_nothing(response):
    # v elsewhere: share 0.9 x default (1.5 / 2.0) = 1.35 / 1.8; provided 1.2 / 1.5.
    p = params(
        **{STILL_EL: 0.9, REQ_MF: 1.5, REQ_ATT: 2.0},
        **{"parking.voluntary_spaces_apartment_elsewhere": 1.2},
        **{"parking.voluntary_spaces_small_elsewhere": 1.5},
    )
    w = np.zeros((1, 4))
    base = np.array([[50.0, 10.0, 10.0, 30.0]])
    low = _all_verified(1.0)  # below v: never binds
    out = parking_saving(p, w, response, 0.0, low)
    assert np.all(out["avoided"] == 0) and np.all(out["saving"] == 0)
    assert np.all(binding_share(p, w, base, response, 1.0, low) == 0)
    zero = _all_verified(0.0)
    assert np.all(parking_saving(p, w, response, 0.0, zero)["avoided"] == 0)
    # A verified minimum above v binds and saves more than the default would.
    high = _all_verified(2.5)
    assert np.all(parking_saving(p, w, response, 0.0, high)["avoided"][0, 1:] > 0)
    assert np.all(binding_share(p, w, base, response, 1.0, high) == 1)


def test_half_verified_zero_halves_the_spaces_avoided():
    p = params(**{STILL_TR: 0.6, STILL_EL: 0.9})
    w = np.zeros((1, 4))
    mix = MinimumMix(
        np.array([0.0]),
        np.zeros((1, 4, 1)),
        np.array([[[0.0], [0.5], [0.5], [0.5]]]),
        np.array([[0.0, 0.5, 0.5, 0.5]]),
        np.array([[0.0, 0.5, 0.5, 0.5]]),
    )
    a = parking_saving(p, w, "share", 0.0)["avoided"]
    b = parking_saving(p, w, "share", 0.0, mix)["avoided"]
    np.testing.assert_allclose(b, 0.5 * a)


# ------------------------------------------------------------ the engine


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


def test_variant_without_law_tables_equals_the_benchmark(ctx):
    a = run("TL", {"parking": {}}, draws=DRAWS, context=ctx, use_cache=False)
    b = run(
        "TL",
        {"parking": {"variant": "parking_verified_minimums"}},
        draws=DRAWS,
        context=replace(ctx),
        use_cache=False,
    )
    pa, pb = a.reforms["parking"], b.reforms["parking"]
    assert pb.added.p50 == pytest.approx(pa.added.p50)
    recs = pb.result.records
    assert recs["parking_default_share"].p50 == 1.0
    assert recs["parking_verified_share"].p50 == 0.0


def test_variant_with_every_municipality_at_zero_adds_nothing(ctx):
    c = replace(ctx)
    tracts = c.tracts.assign(geoid=c.tracts["geoid"].astype(str))
    tm = pd.DataFrame(
        {
            "tract": tracts["geoid"],
            "county": tracts["county"].astype(str),
            "cousub": "00001",
            "housing_units_2020": 1,
        }
    )
    rules = pd.DataFrame(
        [rule(str(cty).zfill(5) + "00001", mf=0, sf=0) for cty in tm["county"].unique()]
    )
    c.derived[("parking_law_table", "muni_zoning_rules")] = rules
    c.derived[("parking_law_table", "tract_municipalities")] = tm
    c.derived[("parking_law_table", "law_documents")] = None
    b = run(
        "TL",
        {"parking": {"variant": "parking_verified_minimums"}},
        draws=DRAWS,
        context=c,
        use_cache=False,
    )
    recs = b.reforms["parking"].result.records
    assert recs["parking_verified_share"].p50 == pytest.approx(1.0)
    assert recs["parking_verified_zero_share"].p50 == pytest.approx(1.0)
    assert b.reforms["parking"].added.p90 == 0
    assert recs["binding_share_before"].p90 == 0
