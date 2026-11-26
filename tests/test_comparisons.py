"""Comparisons on Testland: interactions, policy dials, matrix, data ablation, ranking.

The checks follow docs/methods/comparisons.md: the bench reproduces the engine's
own runs draw by draw; the waterfall and the ordered allocation add up exactly;
every dial's benchmark level is the headline exactly; generous levels never give
fewer homes on the same seed; cached late stages equal a full rerun; a lower data
tier is never narrower on the Testland fixture with all three tiers.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.engine import run
from visionhousing.engine.adapters import lot_reach
from visionhousing.engine.comparisons import (
    BENCHMARKS,
    Bench,
    comparisons,
    data_ablation,
    interaction_accounting,
    masked_context,
    matrix,
    policy_dials,
    ranking_stability,
)
from visionhousing.engine.comparisons.ablation import (
    context_at_tier,
    measures,
    substantive_masks,
)
from visionhousing.engine.comparisons.dials import dial_draws, invert_curve
from visionhousing.engine.comparisons.settings import (
    load_comparisons,
    load_data_tiers,
    parse_comparisons,
)
from visionhousing.engine.context import load_context, local_law_override
from visionhousing.law.hooks import LocalLaw
from visionhousing.ledger import LedgerError, apply_overrides

SEED, DRAWS = 4, 300


@pytest.fixture(scope="module")
def bench():
    return Bench.build("TL", seed=SEED, draws=DRAWS)


@pytest.fixture(scope="module")
def headline():
    return run("TL", list(BENCHMARKS), seed=SEED, draws=DRAWS)


@pytest.fixture(scope="module")
def cfg():
    return load_comparisons()


# ---------------------------------------------------------------- the bench


def test_bench_reproduces_engine_draw_by_draw(bench, headline):
    for k in BENCHMARKS:
        assert np.array_equal(bench.alone_county(k), headline.reforms[k].county_draws)
    pkg = bench.package(BENCHMARKS)
    assert np.array_equal(pkg.combined, headline.package.county_draws)
    assert np.array_equal(pkg.overlap.sum(axis=0), headline.package.overlap_draws)


def test_package_of_one_is_the_reform_alone(bench):
    for k in BENCHMARKS:
        assert np.array_equal(bench.package((k,)).combined, bench.alone_county(k))


# ---------------------------------------------------------------- interactions


def test_interactions_add_up(bench, headline, cfg):
    out = interaction_accounting(bench, cfg)
    assert out["check"]["waterfall_max_gap"] < 1e-6
    assert out["check"]["allocation_max_gap"] < 1e-6
    assert out["state"]["package"]["p50"] == pytest.approx(headline.package.combined.p50)
    solo = sum(np.asarray(headline.reforms[k].state_draws) for k in BENCHMARKS)
    net = np.asarray(headline.package.state_draws) - solo
    assert out["state"]["net_interaction"]["p50"] == pytest.approx(np.percentile(net, 50))
    assert set(out["last_in"]) == set(BENCHMARKS)
    assert [a["reform"] for a in out["allocation"]] == list(cfg.ordering)
    steps = out["waterfall"]["steps"]
    assert steps[0]["kind"] == "start" and steps[-1]["kind"] == "end"
    rows = out["counties"]["net_interaction"]["rows"]
    assert [r[0] for r in rows] == list(bench.county_ids)


def test_last_in_of_a_reform_without_partners_equals_alone(bench, cfg):
    out = interaction_accounting(bench, cfg)
    tod = out["last_in"]["tod"]
    assert tod["last_in"]["p50"] == pytest.approx(tod["solo"]["p50"])
    assert tod["interaction_share"]["p50"] == pytest.approx(0.0, abs=1e-9)


def test_mechanisms_are_named_and_parking_tod_counts_nothing_twice(bench, cfg):
    out = interaction_accounting(bench, cfg)
    by = {m["name"]: m for m in out["mechanisms"]}
    assert {"fees -> adu", "parking -> adu", "overlap", "parking_tod"} <= set(by)
    assert by["parking_tod"]["record"]["p50"] == pytest.approx(0.0, abs=1e-9)
    assert by["parking_tod"]["in_package"] is False
    assert "about zero" in by["parking -> adu"]["sentence"]
    fee = by["fees -> adu"]
    assert fee["record"]["p50"] > 0 and fee["sentence"].startswith("The fee cut")


# ---------------------------------------------------------------- dials


def test_every_dial_benchmark_reproduces_the_headline_exactly(bench, headline, cfg):
    for dial in cfg.dials.values():
        county, _ids = dial_draws(bench, dial, dial.level(dial.benchmark))
        assert np.array_equal(county, headline.reforms[dial.reform].county_draws), dial.key


@pytest.mark.parametrize("key", ["adu_lot_share", "adu_owner_rule", "fees_cut", "parking_minimum"])
def test_more_generous_level_never_gives_fewer_homes(bench, cfg, key):
    dial = cfg.dials[key]
    states = [dial_draws(bench, dial, lv)[0].sum(axis=0) for lv in dial.levels]
    for lo, hi in pairwise(states):
        assert np.all(hi >= lo - 1e-9)


def test_parking_minimum_unchanged_adds_nothing(bench, cfg):
    dial = cfg.dials["parking_minimum"]
    county, _ = dial_draws(bench, dial, dial.level("current"))
    assert np.allclose(county, 0.0)


@pytest.mark.parametrize("reach", [0.5, "owner_occupied"])
def test_late_stage_cache_equals_full_rerun(bench, reach):
    res = bench.alone("adu")
    rerun = bench.alone("adu", {"lot_reach": reach})
    r = lot_reach(bench.context.tracts, reach)
    assert np.allclose(np.asarray(res.added) * r[:, None], rerun.added, rtol=1e-12, atol=1e-9)


def test_late_stages_at_one_equal_the_net_stage(bench):
    detail = bench.alone("adu").detail
    assert np.allclose(detail.late_stages(1.0), detail.stages["net"], rtol=1e-12, atol=1e-12)
    assert np.all(detail.late_stages(2.0) >= detail.late_stages(1.0) - 1e-12)


def test_invert_curve():
    xs, vals, keys = [0.25, 0.5, 1.0], [10.0, 20.0, 40.0], ["a", "b", "c"]
    assert invert_curve(xs, vals, keys, 5) == {"status": "at_lowest", "level": "a", "x": 0.25}
    got = invert_curve(xs, vals, keys, 30)
    assert got["status"] == "between" and got["level"] == "c" and got["x"] == pytest.approx(0.75)
    assert invert_curve(xs, vals, keys, 41)["status"] == "out_of_reach"


def test_policy_dials_readouts(bench, cfg):
    out = policy_dials(bench, cfg)
    fees = out["dials"]["fees_cut"]
    assert fees["monotone_p50"] is True
    assert "each step adds about" in fees["each_step"]["sentence"]
    assert [lv["benchmark"] for lv in fees["levels"]] == [False, True, False, False]
    assert len(fees["targets"]["rows"]) == len(cfg.targets)
    park = out["dials"]["parking_minimum"]
    assert park["diminishing"]["level"] == "half"  # halving does nearly all of it
    for row in out["adoption"]["rows"]:
        hit = row["p50"]
        if hit["status"] == "reached":
            res = bench.alone("adu").detail
            got = np.percentile(res.late_stages(hit["multiplier"]).sum(axis=1), 50)
            assert got >= row["homes"] - 1e-6
            assert hit["rate"] == pytest.approx(hit["multiplier"] * out["adoption"]["central_rate"])
    assert out["adoption"]["rows"][-1]["p50"]["status"] in ("reached", "out_of_reach")


def test_adoption_out_of_reach_above_the_ceiling(bench, cfg):
    from visionhousing.engine.comparisons.dials import adoption_threshold

    big = replace(cfg, targets=(1e12,))
    row = adoption_threshold(bench, big)["rows"][0]
    assert row["p50"]["status"] == "out_of_reach" and row["p10"]["status"] == "out_of_reach"


# ---------------------------------------------------------------- matrix


def test_matrix(bench, headline, cfg):
    out = matrix(bench, cfg)
    t, c = cfg.matrix_takeup[1], cfg.matrix_cost[1]
    i, j = t.index(1.0), c.index(1.0)
    assert out["cells"][i][j]["benchmark"]
    assert out["cells"][i][j]["p50"] == headline.reforms["adu"].added.p50
    assert out["funnel_runs"] == len(c) - 1
    p50 = np.array([[cell["p50"] for cell in row] for row in out["cells"]])
    assert np.all(np.diff(p50, axis=0) >= -1e-9)  # more take-up, more homes
    assert np.all(np.diff(p50, axis=1) <= 1e-9)  # dearer building, fewer homes
    assert all(cell["p10"] <= cell["p50"] <= cell["p90"] for row in out["cells"] for cell in row)


# ---------------------------------------------------------------- data ablation


def _fake_law(ctx) -> LocalLaw:
    """Read local law for Testland's first county.

    The readings confirm the statewide middle of the already-legal ADU share but
    narrow its range (share = a + b x s with b = 0.5 and a = (1 - b) x the
    central share), as verified readings do; parking reach 0.8.
    """
    county = sorted(ctx.tracts["county"].astype(str).unique())[0]
    b = 0.5
    a = (1 - b) * ctx.ledger["adu.already_legal_share"].central
    adu = pd.DataFrame(
        [
            {
                "county": county,
                "a": a,
                "b": b,
                "coverage": 0.6,
                "legal_residents": 100.0,
                "residents": 1000.0,
            }
        ]
    )
    parking = pd.DataFrame([{"county": county, "factor": 0.8, "coverage": 0.6}])
    return LocalLaw(adu=adu, parking=parking, citations={county: ["test reading"]})


@pytest.fixture(scope="module")
def full_ctx():
    """Testland with all three tiers: read local law (tier 2) on top of the national tables."""
    ctx = load_context("TL")
    law = _fake_law(ctx)
    ledger = apply_overrides(ctx.ledger, local_law_override(ctx.ledger, law))
    return replace(ctx, local_law=law, ledger=ledger, data_hash="tl-all-tiers")


def test_masks_change_what_they_say(full_ctx):
    tiers = load_data_tiers()
    assert masked_context(full_ctx, []) is full_ctx
    t1 = masked_context(full_ctx, tiers.masked_at(1), tiers)
    assert t1.local_law is None and full_ctx.local_law is not None
    assert t1.ledger["adu.fit_range_position"].low < full_ctx.ledger["adu.fit_range_position"].low
    assert t1.calibration_hash != full_ctx.calibration_hash
    assert set(substantive_masks(full_ctx, tiers.masked_at(0), tiers)) == {
        "local_law",
        "lot_measurements",
    }
    with pytest.raises(KeyError):
        masked_context(full_ctx, ["no_such_input"], tiers)


def test_middle_shift_is_paired_per_draw_not_a_difference_of_medians():
    # Two counties, four draws. The ablated run equals the full run with draws
    # reordered, so the medians agree but no draw is unchanged.
    full = np.array([[10.0, 20.0, 30.0, 40.0], [0.0, 0.0, 0.0, 0.0]])
    ablated = np.array([[40.0, 30.0, 20.0, 10.0], [0.0, 0.0, 0.0, 0.0]])
    m = measures(full, ablated)
    per_draw = ablated.sum(axis=0) / full.sum(axis=0) - 1
    assert m["middle_shift"] == pytest.approx(np.percentile(per_draw, 50))
    assert m["middle_shift"] != 0.0  # a difference of medians would say 0
    assert m["paired_difference"]["p10"] < 0 < m["paired_difference"]["p90"]
    assert m["ratio"]["p10"] <= m["ratio"]["p50"] <= m["ratio"]["p90"]
    with pytest.raises(ValueError):
        measures(full, ablated[:, :3])


def test_measures_against_itself():
    arr = np.arange(30, dtype=float).reshape(3, 10) + 1.0
    m = measures(arr, arr)
    assert m["middle_shift"] == 0 and m["range_widening"] == 1
    assert m["paired_difference"] == {"p10": 0.0, "p50": 0.0, "p90": 0.0}
    assert m["rank_agreement"] == pytest.approx(1.0) and m["coverage"] == 1.0


def test_ablation_tiers_on_testland_fixture(full_ctx):
    # The default 2,000 draws: with a few hundred, percentile noise can hide a small widening.
    b = Bench.build(full_ctx, seed=SEED, draws=2000)
    out = data_ablation(b)
    tiers = out["tiers"]
    assert set(tiers) == {"2", "1", "0"}
    for row in tiers["2"]["reforms"].values():
        assert row["middle_shift"] in (0.0, None) and row["coverage"] == 1.0
    assert tiers["1"]["masked_with_effect"] == ["local_law", "lot_measurements"]
    adu0 = tiers["0"]["reforms"]["adu"]
    assert "With national data only" in adu0["sentence"]
    # Property: a lower tier is never narrower, reform by reform.
    for k in BENCHMARKS:
        assert out["property"]["never_narrower"][k], k
    ranked = out["single_input"]["rows"]
    assert [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1))
    none = [r for r in ranked if r["effect"] == "none"]
    assert {r["input"] for r in none} >= {"county_construction_pay", "network_walksheds"}
    # Ranked on one paired measure: the package's per-draw middle shift.
    for r in ranked:
        if r["effect"] != "none":
            assert r["ranked_on"] == "package"
            assert r["score"] == pytest.approx(abs(r["reforms"]["package"]["middle_shift"] or 0))


def test_lower_tier_never_narrower_across_seeds(full_ctx):
    """Property: at the default 2,000 draws a lower tier's state range is never narrower."""
    for seed in (0, 1, 2):
        widths = {}
        for t in (2, 1, 0):
            bt = Bench.build(context_at_tier(full_ctx, t), seed=seed, draws=2000)
            s = bt.alone_county("adu").sum(axis=0)
            widths[t] = np.percentile(s, 90) - np.percentile(s, 10)
        assert widths[0] >= widths[1] - 1e-6 and widths[1] >= widths[2] - 1e-6, (seed, widths)


def test_run_at_tier_two_is_the_headline():
    full = run("TL", ["adu"], seed=SEED, draws=DRAWS)
    same = run("TL", ["adu"], seed=SEED, draws=DRAWS, context=context_at_tier("TL", 2))
    assert np.array_equal(full.reforms["adu"].state_draws, same.reforms["adu"].state_draws)


# ---------------------------------------------------------------- ranking


def test_ranking_stability(bench, cfg):
    out = ranking_stability(bench, cfg)
    order = out["benchmark"]["order"]
    p50 = [out["benchmark"]["ranges"][k]["p50"] for k in order]
    assert p50 == sorted(p50, reverse=True)
    assert len(out["cases"]) == 2 * len(out["inputs"]) > 0
    for case in out["cases"]:
        assert case["changed"] == (case["order"] != order)
        assert case["rerun"]
    assert len(out["pairs"]) == len(BENCHMARKS) * (len(BENCHMARKS) - 1) // 2
    assert out["sentence"].startswith("Across")


# ---------------------------------------------------------------- settings and API


def test_settings_reject_bad_dials():
    good = {
        "interactions": {"ordering": list(BENCHMARKS), "mechanisms": {}, "zero_homes": 0.5},
        "adoption": {"parameter": "p", "max_multiplier": 100, "tolerance_homes": 0.5},
        "dials": {
            "d": {
                "reform": "fees",
                "cache": "reform",
                "benchmark": "a",
                "title": "t",
                "unit": "u",
                "levels": {
                    "a": {"x": 0, "label": "a"},
                    "b": {"x": 1, "label": "b", "overrides": {"fees.fee_cut_share": 1.0}},
                },
            }
        },
        "readouts": {"diminishing_step_share": {"value": 0.5}, "targets": {"homes": [1, 2]}},
        "matrix": {
            "takeup": {"parameter": "p", "multipliers": [1.0]},
            "cost": {"parameter": "q", "multipliers": [1.0]},
            "reform": "adu",
        },
        "ranking": {"top_per_reform": 1},
    }
    assert parse_comparisons(good).dials["d"].benchmark == "a"
    # Every setting lives in the file: none has a code default.
    for block, key in (("interactions", "zero_homes"), ("adoption", "tolerance_homes"),
                       ("matrix", "reform")):  # fmt: skip
        gone = {**good, block: {k: v for k, v in good[block].items() if k != key}}
        with pytest.raises(LedgerError, match=f"{block}.{key} is missing"):
            parse_comparisons(gone)
    bad = {**good, "dials": {"d": {**good["dials"]["d"], "benchmark": "b"}}}
    with pytest.raises(LedgerError, match="must change nothing"):
        parse_comparisons(bad)
    other = {**good}
    other["dials"] = {"d": {**good["dials"]["d"]}}
    other["dials"]["d"]["levels"] = {
        "a": {"x": 0, "label": "a"},
        "b": {"x": 1, "label": "b", "overrides": {"adu.permit_fee": 0}},
    }
    with pytest.raises(LedgerError, match="another reform"):
        parse_comparisons(other)


def test_comparisons_parts_and_unknown_part():
    out = comparisons("TL", ["interactions", "matrix"], seed=1, draws=100)
    assert set(out) >= {"interactions", "matrix", "hashes", "county_ids"}
    with pytest.raises(ValueError):
        comparisons("TL", ["nope"], draws=50)


def test_api_comparisons(tmp_path):
    client = AsgiClient(create_app(packs_dir=tmp_path))
    r = client.get("/api/v1/states/TL/comparisons?part=interactions,matrix&draws=150&seed=2")
    assert r.status == 200
    body = r.json()
    assert body["state"] == "TL" and body["draws"] == 150
    assert body["interactions"]["check"]["waterfall_max_gap"] < 1e-6
    assert client.get("/api/v1/states/TL/comparisons?part=nope").status == 400
    assert client.get("/api/v1/states/TL/comparisons?draws=0").status == 400


# ---------------------------------------------------------------- review fixes


def test_each_step_is_the_paired_first_to_last_change_per_step(bench, cfg):
    from visionhousing.engine.comparisons.dials import dial_curve, dial_draws

    dial = cfg.dials["fees_cut"]
    out = dial_curve(bench, dial, cfg)
    first = dial_draws(bench, dial, dial.levels[0])[0].sum(axis=0)
    last = dial_draws(bench, dial, dial.levels[-1])[0].sum(axis=0)
    want = float(np.median((last - first) / (len(dial.levels) - 1)))
    assert out["each_step"]["homes"] == pytest.approx(want)
    assert "P50 over draws" in out["each_step"]["formula"]


def test_adoption_ceiling_is_every_feasible_lot_and_answer_is_smallest(bench, cfg):
    from visionhousing.engine.comparisons.dials import adoption_threshold

    detail = bench.alone("adu").detail
    top = (np.asarray(detail.stages["feasible"]) * detail.kept_share).sum(axis=1)
    out = adoption_threshold(bench, cfg)
    assert out["ceiling"]["p50"] == pytest.approx(np.percentile(top, 50))
    # A target just under the ceiling is still found by bisection, well below the bound.
    near = replace(cfg, targets=(0.99 * out["ceiling"]["p50"],))
    hit = adoption_threshold(bench, near)["rows"][0]["p50"]
    assert hit["status"] == "reached"
    assert hit["multiplier"] < cfg.adoption_max_multiplier
    below = np.percentile(detail.late_stages(hit["multiplier"] * 0.98).sum(axis=1), 50)
    assert below < near.targets[0] + cfg.adoption_tolerance


def test_masked_context_starts_from_the_contexts_own_ledger(full_ctx):
    assert full_ctx.base_ledger is not None
    tiers = load_data_tiers()
    marker = apply_overrides(
        full_ctx.base_ledger,
        {"adu.ramp_years_to_full": {"central": 3.5, "low": 3.0, "high": 4.0, "shape": "pert"}},
    )
    ctx = replace(full_ctx, base_ledger=marker)
    t1 = masked_context(ctx, tiers.masked_at(1), tiers)
    assert t1.ledger["adu.ramp_years_to_full"].central == 3.5
