"""Parking: verified minimums only where 50% of residents are covered.

Variant parking_verified_minimums_bar, option minimums: verified_bar.

The documented hook rule (assumptions/law/hooks.yaml, min_coverage): a county
uses read local values only when its verified rows cover at least half of its
residents. The variant applies verified non-zero minimums only in those
counties; zero minimums keep working through the county reach factor, as in
the benchmark.
"""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from visionhousing.engine.context import load_context
from visionhousing.engine.core import run
from visionhousing.law.hooks import LocalLaw
from visionhousing.scenarios.parking import bar_minimums

DRAWS = 300
# Same numbers up to floating-point rounding (the mixed path sums in another order).


def minimums(rows):
    return pd.DataFrame(
        [
            {"geoid": g, "county": g[:5], "name": g, "min_apartment": a, "min_small": s}
            for g, a, s in rows
        ]
    )


def test_bar_keeps_only_qualifying_counties_and_blanks_zeros():
    t = minimums(
        [
            ("4205100001", 2.0, 2.25),  # Fayette-like county, over the bar
            ("4205100002", 0.0, 2.0),  # zero for apartments: blank (default + reach)
            ("4205100003", 0.0, 0.0),  # all zero: left to the reach factor
            ("4200300001", 3.0, 3.0),  # county under the bar
        ]
    )
    got = bar_minimums(t, ["42051"]).set_index("geoid")
    assert list(got.index) == ["4205100001", "4205100002"]
    assert pd.isna(got.loc["4205100002", "min_apartment"])
    assert got.loc["4205100002", "min_small"] == 2.0
    assert bar_minimums(t, []).empty
    assert bar_minimums(None, ["42051"]).empty


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


def law_ctx(ctx, level, qualifying, factor=1.0):
    """Testland with one municipality per county at ``level`` and a parking hook."""
    law = None
    if qualifying is not None:
        parking = pd.DataFrame({"county": list(qualifying), "factor": factor, "coverage": 1.0})
        adu = pd.DataFrame(columns=["county", "a", "b", "coverage"])
        law = LocalLaw(adu=adu, parking=parking)
    c = replace(ctx, local_law=law)  # a fresh derived cache
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
        [
            {
                "geoid": str(cty).zfill(5) + "00001",
                "name": "m",
                "doc_id": "d",
                "section_cite": "s",
                "status": "verified",
                "parking_min_per_unit_mf": level,
                "parking_min_per_unit_sf": level,
                "effective_date": "",
                "adopted_on": "",
                "in_force_by": "",
                "reviewed_on": "2026-11-23",
            }
            for cty in tm["county"].unique()
        ]
    )
    c.derived[("parking_law_table", "muni_zoning_rules")] = rules
    c.derived[("parking_law_table", "tract_municipalities")] = tm
    c.derived[("parking_law_table", "law_documents")] = None
    return c


def parking_run(c, variant=None):
    opts = {} if variant is None else {"variant": variant}
    return run("TL", {"parking": opts}, draws=DRAWS, context=c, use_cache=False).reforms["parking"]


def test_no_county_over_the_bar_equals_the_benchmark_draw_for_draw(ctx):
    c = law_ctx(ctx, 3.0, None)
    a = parking_run(c)
    b = parking_run(c, "parking_verified_minimums_bar")
    np.testing.assert_allclose(b.result.added, a.result.added, rtol=1e-12, atol=1e-9)
    assert b.result.records["parking_verified_share"].p50 == 0.0
    # The all-verified variant does move (a 3.0 minimum is above the default).
    assert parking_run(c, "parking_verified_minimums").added.p50 > a.added.p50


def test_only_counties_over_the_bar_change(ctx):
    counties = sorted(ctx.tracts["county"].astype(str).unique())
    first = counties[0]
    c = law_ctx(ctx, 3.0, [first])
    a = parking_run(c)
    b = parking_run(c, "parking_verified_minimums_bar")
    rows = list(a.result.geoids)
    i = rows.index(first)
    others = [j for j in range(len(rows)) if j != i]
    np.testing.assert_allclose(
        b.result.added[others], a.result.added[others], rtol=1e-12, atol=1e-9
    )
    assert b.result.added[i].mean() > a.result.added[i].mean()
    assert b.result.records["parking_verified_share"].p50 > 0
    assert any("half of their residents" in n for n in b.result.notes)


def test_zero_minimums_stay_on_the_reach_factor(ctx):
    counties = sorted(ctx.tracts["county"].astype(str).unique())
    c = law_ctx(ctx, 0.0, [counties[0]], factor=0.4)
    a = parking_run(c)  # benchmark: the reach factor scales the county down
    b = parking_run(c, "parking_verified_minimums_bar")
    np.testing.assert_allclose(b.result.added, a.result.added, rtol=1e-12, atol=1e-9)


def test_unknown_minimums_option_is_refused(ctx):
    with pytest.raises(ValueError, match="minimums"):
        run("TL", {"parking": {"minimums": "half"}}, draws=50, context=ctx, use_cache=False)
