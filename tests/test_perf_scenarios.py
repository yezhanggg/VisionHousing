"""Speed work must never move a number: the fast bracket share against the slow one.

Plain-language summary
----------------------
The ADU, missing-middle, prototype and transit (TOD) funnels read "the share of
a tract's rents (or home values) above a break-even" from ACS bracket counts,
once for every draw and tract. The fast path
(:func:`visionhousing.features.brackets.share_above_grid`) works the share out
with a running sum per tract; the slow path copied the tract counts once per
draw and summed every bracket (:func:`~visionhousing.features.brackets.share_above_many`).

These tests check the two paths agree:

- **Function level.** Random counts and thresholds (including blanks, zero
  totals, thresholds on bracket edges, below the first edge and in the open top
  bracket), both top-bracket rules: equal within a relative 1e-12.
- **Package level (golden check).** The same reform package is run twice on
  Testland, seeds 0 and 1, with the benchmark ADU scenario and the two bill
  scenarios: once with the fast path and once with the slow path put back in.
  Every record (P10, P50, P90, value) must agree within a relative 1e-12. The
  slow path is rebuilt inside the test, so the check never needs a saved file
  and stays valid when a later change is *meant* to move results.
- **Transit flags.** The latitude-band speed-up of the transit-rich tract flags
  gives exactly the flags of measuring every block against every stop.

Comparison rule: ``np.allclose(rtol=1e-12, atol=0)``. In practice most values
match to the last digit; the tolerance only allows for the order in which one
fractional term is added.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from visionhousing import engine
from visionhousing.engine.core import _context
from visionhousing.features import brackets as br
from visionhousing.scenarios import parking as pk

RTOL = 1e-12
ADU_KEYS = ("adu", "pa_hb2186", "pa_sb1346")
OTHERS = ("missing_middle", "tod", "fees", "parking")


def _slow_above(counts, edges, threshold, *, top_rule="lower_bound", alpha=None):
    """The old path: copy the counts once per row of thresholds, then sum every bracket."""
    t = np.asarray(threshold, dtype=float)
    flat = t.reshape(-1, t.shape[-1])
    rows = flat.shape[0]
    res = br.share_above_many(
        np.tile(counts, (rows, 1)), edges, flat.reshape(-1), top_rule=top_rule, alpha=alpha
    )
    return res.value.reshape(t.shape)


def _slow_below(counts, edges, threshold, *, top_rule="lower_bound", alpha=None):
    return 1.0 - _slow_above(counts, edges, threshold, top_rule=top_rule, alpha=alpha)


def _close(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.allclose(a, b, rtol=RTOL, atol=0.0, equal_nan=True))


# ---------------------------------------------------------------- function level


@pytest.mark.parametrize("table", [br.RENT, br.VALUE], ids=["rent", "value"])
@pytest.mark.parametrize("rule", ["lower_bound", "pareto"])
def test_grid_share_equals_bracket_loop(table: br.BracketTable, rule: str) -> None:
    rng = np.random.default_rng(7)
    k = len(table.lows)
    counts = rng.integers(0, 60, size=(40, k)).astype(float)
    counts[3] = 0.0  # zero total
    counts[5, 2] = np.nan  # blank count
    lows = np.asarray(table.lows)
    t = rng.uniform(-100, lows[-1] * 1.6, size=(25, 40))
    t[0, :k] = lows[: min(k, 40)]  # thresholds exactly on edges
    t[1, 7] = np.nan  # blank threshold
    t[2] = lows[-1]  # right at the open bracket's edge
    alpha = 1.7 if rule == "pareto" else None
    fast = br.share_above_grid(counts, table, t, top_rule=rule, alpha=alpha)
    slow = _slow_above(counts, table, t, top_rule=rule, alpha=alpha)
    assert fast.shape == t.shape
    assert np.array_equal(np.isnan(fast), np.isnan(slow))
    assert _close(fast, slow)
    below = br.share_below_grid(counts, table, t, top_rule=rule, alpha=alpha)
    assert _close(below, _slow_below(counts, table, t, top_rule=rule, alpha=alpha))


def test_grid_share_checks_its_inputs() -> None:
    counts = np.ones((3, len(br.RENT.lows)))
    with pytest.raises(ValueError):
        br.share_above_grid(counts, br.RENT, np.ones((2, 4)))  # wrong tract axis
    with pytest.raises(ValueError):
        br.share_above_grid(-counts, br.RENT, np.ones((2, 3)))
    with pytest.raises(ValueError):
        br.share_above_grid(counts[:, :3], [[0, math.inf], [5, 9], [9, math.inf]], np.ones(3))


# ---------------------------------------------------------------- package level


def _records(adu_key: str, seed: int) -> list[list]:
    res = engine.run("TL", [adu_key, *OTHERS], seed=seed, use_cache=False)
    return [[r.name, r.p10, r.p50, r.p90, r.value] for r in res.records()]


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return bool(np.allclose(a, b, rtol=RTOL, atol=0.0))


@pytest.mark.parametrize("seed", [0, 1])
@pytest.mark.parametrize("adu_key", ADU_KEYS)
def test_package_records_match_slow_path(
    monkeypatch: pytest.MonkeyPatch, adu_key: str, seed: int
) -> None:
    """Every record of the Testland package is the same with the old bracket path."""
    fast = _records(adu_key, seed)
    monkeypatch.setattr(br, "share_above_grid", _slow_above)
    monkeypatch.setattr(br, "share_below_grid", _slow_below)
    slow = _records(adu_key, seed)
    assert [f[0] for f in fast] == [s[0] for s in slow]
    for f, s in zip(fast, slow, strict=True):
        for fv, sv in zip(f[1:], s[1:], strict=True):
            assert _same(fv, sv), f"{f[0]}: fast {f[1:]}, slow {s[1:]}"


# ---------------------------------------------------------------- transit flags


def _flags_every_stop(blocks: pd.DataFrame, stops: pd.DataFrame, ledger, boundaries) -> pd.Series:
    """Reference: every block measured against every major stop (no latitude band)."""
    min_dep, rail_r, bus_r, min_share = (ledger[i].central for i in pk.TRANSIT_IDS)
    major = pk.major_stops(stops, min_dep)
    near = np.zeros(len(blocks), dtype=bool)
    if len(major):
        radius = np.where(major["mode"].astype(str).str.lower() == "rail", rail_r, bus_r)
        d = pk._miles(
            blocks["lat"].to_numpy(float),
            blocks["lon"].to_numpy(float),
            major["lat"].to_numpy(float),
            major["lon"].to_numpy(float),
        )
        near = (d <= radius[None, :]).any(axis=1)
    units = blocks["housing_units"].to_numpy(float)
    frame = pd.DataFrame({"tract": blocks["tract"].astype(str), "u": units, "n": units * near})
    g = frame.groupby("tract")[["u", "n"]].sum()
    flag = (g["n"] / g["u"].where(g["u"] > 0)).fillna(0.0) >= min_share
    if boundaries is not None:
        lon, lat = major["lon"].to_numpy(float), major["lat"].to_numpy(float)
        inside = {}
        for feat in boundaries.get("features", []):
            props = feat.get("properties", {})
            if props.get("level") != "tract":
                continue
            hit = np.zeros(len(major), dtype=bool)
            for ring in pk._rings(feat.get("geometry", {})):
                hit |= pk._inside(ring, lon, lat)
            inside[str(props["geoid"])] = bool(hit.any())
        contains = pd.Series(inside, dtype=bool)
        flag = flag.reindex(flag.index.union(contains.index), fill_value=False)
        flag = flag | contains.reindex(flag.index, fill_value=False)
    return flag.astype(bool).rename("transit_rich")


@pytest.mark.parametrize("with_outlines", [True, False])
@pytest.mark.parametrize("chunk", [1, 3, 2_000])
def test_banded_transit_flags_are_exact(with_outlines: bool, chunk: int) -> None:
    ctx = _context("TL")
    rng = np.random.default_rng(3)
    blocks = ctx.blocks.copy()
    # Scatter extra blocks around the stops so some sit just inside or outside a radius.
    stops = ctx.stops
    extra = stops.sample(min(len(stops), 40), random_state=1, replace=True)
    scatter = pd.DataFrame(
        {
            "tract": rng.choice(blocks["tract"].astype(str).unique(), len(extra)),
            "housing_units": rng.integers(1, 50, len(extra)).astype(float),
            "lat": extra["lat"].to_numpy(float) + rng.normal(0, 0.01, len(extra)),
            "lon": extra["lon"].to_numpy(float) + rng.normal(0, 0.01, len(extra)),
        }
    )
    blocks = pd.concat([blocks, scatter], ignore_index=True)
    blocks.loc[0, "lat"] = np.nan  # a block with no centre is never near
    outlines = ctx.boundaries if with_outlines else None
    got = pk.transit_rich_tracts(blocks, stops, ctx.ledger, boundaries=outlines, chunk=chunk)
    want = _flags_every_stop(blocks, stops, ctx.ledger, outlines)
    assert got.equals(want)


# ---------------------------------------------------------------- slim result and cache


def test_finished_result_drops_unread_detail_and_shares_memory() -> None:
    from visionhousing.scenarios.adu import HEADLINE_STAGE as ADU_STAGE

    res = engine.run("TL", ["adu", "missing_middle", "tod"], seed=0, use_cache=False)
    adu = res.reforms["adu"].result
    assert adu.detail.feasible_share is None and adu.detail.breakeven_rent is None
    # The headline is a view of the funnel's stage, not a second copy.
    assert np.shares_memory(adu.added, adu.detail.stages[ADU_STAGE])
    mm = res.reforms["missing_middle"].result
    assert mm.detail.feasible_share is None and mm.detail.feasible_share_by_type == {}
    assert mm.detail.removed is None and mm.detail.baseline_on_sites is None
    assert mm.removed is not None  # the copy the outcome records read stays
    assert res.reforms["tod"].result.detail.eligible_homes is None
    # Every reader still works on the slim result.
    res.to_dict()
    for o in res.reforms.values():
        o.tract_steps()
        o.county_steps()


def test_engine_cache_is_capped_by_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    from visionhousing.engine import core

    core.clear_cache()
    monkeypatch.setenv(core.CACHE_BYTES_ENV, "1")  # every result is over the cap
    for seed in (0, 1, 2):
        engine.run("TL", ["adu"], seed=seed)
    info = core.cache_info()
    assert info["size"] == 1  # the newest result is always kept
    assert info["bytes_limit"] == 1 and info["bytes"] > 1
    monkeypatch.setenv(core.CACHE_BYTES_ENV, str(10 * 2**30))
    for seed in (3, 4):
        engine.run("TL", ["adu"], seed=seed)
    assert core.cache_info()["size"] == 3
    monkeypatch.setenv(core.CACHE_BYTES_ENV, "lots")
    with pytest.raises(ValueError):
        core.cache_bytes_limit()
    monkeypatch.delenv(core.CACHE_BYTES_ENV)
    assert core.cache_bytes_limit() == core.CACHE_BYTES_DEFAULT
    core.clear_cache()


def test_result_bytes_count_each_buffer_once() -> None:
    from visionhousing.engine.core import result_nbytes

    a = np.zeros((100, 10))
    assert result_nbytes({"a": a, "view": a.T, "again": [a]}) == a.nbytes
    assert result_nbytes({"a": a, "b": np.ones(5)}) == a.nbytes + 40
