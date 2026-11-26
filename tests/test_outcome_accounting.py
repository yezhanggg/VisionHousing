"""The five record types: event accounting fixtures, ADU work types, every reform's records."""

from __future__ import annotations

import json

import numpy as np
import pytest

from tests.test_scenario_missing_middle import GOLDEN, golden_tract
from visionhousing.engine import run
from visionhousing.engine.outcome import load_rules, site_account
from visionhousing.explain.ids import record_index, resolve
from visionhousing.ledger import apply_overrides, load_ledger, load_scenarios, scenario_ledger
from visionhousing.scenarios.adu import WORK_STAGES, WORK_TYPES
from visionhousing.scenarios.missing_middle import missing_middle_funnel, scope_from_scenario

ALL = ["adu", "missing_middle", "tod", "fees", "parking"]
DRAWS = 200

# ---------------------------------------------------------------- the accounting fixture


@pytest.mark.parametrize(
    ("policy", "counterfactual", "gross", "net"),
    [
        ((4, 1), (1, 1), 3, 3),  # four units and a demolition vs a one-house rebuild
        ((4, 1), (0, 0), 4, 3),  # vs a house left untouched
        ((2, 1), (2, 1), 0, 0),  # an already-permitted duplex under a two-unit ceiling
        ((4, 1), (1, 1), 3, 3),  # a fourplex replacing a baseline single-unit rebuild
    ],
)
def test_site_account_worked_cases(policy, counterfactual, gross, net):
    a = site_account(policy[0], policy[1], counterfactual[0], counterfactual[1])
    assert float(a.gross_additional) == gross
    assert float(a.net_if_completed) == net
    assert float(a.demolitions_caused) == policy[1] - counterfactual[1]


def test_site_account_displacement_moves_no_demolition():
    a = site_account(np.array([4.0]), np.array([1.0]), np.array([1.0]), np.array([0.5]), 0.25)
    assert a.gross_additional[0] == pytest.approx(3 * 0.75)
    assert a.net_if_completed[0] == pytest.approx(3 * 0.75 - 0.5)


# ---------------------------------------------------------------- the funnel uses the events


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


@pytest.fixture(scope="module")
def scenarios(ledger):
    return load_scenarios(ledger)


def middle(ledger, scenarios, tract, **changes):
    scenario = scenarios["missing_middle"]
    led = apply_overrides(scenario_ledger(ledger, scenario), {**GOLDEN, **changes})
    scope = scope_from_scenario("missing_middle", scenario)
    return missing_middle_funnel(tract, led, None, scope, draws=2, seed=1)


@pytest.mark.parametrize(("q", "gross_per_lot", "net_per_lot"), [(1, 3, 3), (0, 4, 3)])
def test_fourplex_fixture_through_the_funnel(ledger, scenarios, q, gross_per_lot, net_per_lot):
    res = middle(
        ledger,
        scenarios,
        golden_tract(),
        **{
            "missing_middle.baseline_replacement_probability": q,
            "missing_middle.displacement_share": 0,
        },
    )
    lots = res.stages["redeveloped"][0, 0]
    assert lots > 0
    assert res.stages["authorized"][0, 0] == pytest.approx(gross_per_lot * lots)
    assert res.stages["net"][0, 0] == pytest.approx(net_per_lot * lots)
    assert res.records["demolitions_caused"].p50 == pytest.approx((1 - q) * lots)
    assert res.records["counterfactual_removed"].p50 == pytest.approx(q * lots)


def test_already_permitted_duplex_gains_nothing(ledger, scenarios):
    # Every lot already allows two homes (no single-family-only zone) and the ceiling is two.
    res = middle(
        ledger,
        scenarios,
        golden_tract(),
        **{
            "missing_middle.sf_only_zoned_share": 0,
            "missing_middle.max_units": 2,
            "missing_middle.typology_weight_duplex": 1,
        },
    )
    assert res.stages["authorized"].sum() == 0
    assert res.stages["net"].sum() == 0


def test_middle_work_types_add_up_to_the_headline(ledger, scenarios):
    tract = golden_tract(units_1_attached=500)
    res = middle(ledger, scenarios, tract)
    total = sum(res.work_types.values())
    assert np.allclose(total, res.stages["authorized"].sum(axis=1))
    # The benchmark reaches detached lots only, so every unit is a stand-alone building.
    assert np.allclose(res.work_types["new_attached"], 0)


# ---------------------------------------------------------------- whole runs


@pytest.fixture(scope="module")
def result():
    return run("TL", ALL, seed=5, draws=DRAWS)


def test_every_reform_carries_the_five_record_types(result):
    for key, o in result.reforms.items():
        books = o.accounting
        assert books is not None, key
        assert set(books.kinds) == {"a", "b", "c", "d", "e"}
        recs = books.records
        for name in ("baseline_bps", "comparable", "local_outside_bps", "net_if_completed"):
            assert name in recs, (key, name)
        assert recs["baseline_bps"].p50 == result.baseline.p50
        assert books.status["observed_completions"]
        assert books.status["modelled_deliveries"]
        for name, r in recs.items():
            assert r.formula, (key, name)


def test_work_types_add_up_to_the_headline_in_every_draw(result):
    o = result.reforms["adu"]
    work = o.result.detail.work_types
    assert set(work) == set(WORK_TYPES)
    assert np.allclose(sum(work.values()), o.state_draws)


def test_adu_headline_is_kept_and_the_comparable_record_sits_beside_it(result):
    o = result.reforms["adu"]
    books = o.accounting
    assert books.headline["is_comparable"] is False
    recs = books.records
    detail = o.result.detail
    detached = detail.work_types["new_standalone"]
    assert recs["comparable"].p50 == pytest.approx(np.percentile(detached, 50))
    diff = o.state_draws - detail.work_types["new_standalone"]
    assert recs["headline_minus_comparable"].p50 == pytest.approx(np.percentile(diff, 50))
    assert recs["local_outside_bps"].p50 == pytest.approx(
        np.percentile(detail.work_types["conversion"], 50)
    )
    assert recs["comparable"].p50 <= o.added.p50
    for other in ("fees", "parking", "tod", "missing_middle"):
        b = result.reforms[other].accounting
        assert b.headline["is_comparable"] is True, other
        assert b.records["comparable"].p50 == pytest.approx(result.reforms[other].added.p50)
        assert b.records["local_outside_bps"].p50 == 0


def test_adu_stages_split_by_work_type(result):
    recs = result.reforms["adu"].records
    for stage in WORK_STAGES:
        parts = [recs[f"work_{w}_{stage}"] for w in WORK_TYPES]
        assert all(p.p50 >= 0 for p in parts)
    assert recs["legal_size_condition"].p10 == recs["legal_size_condition"].p90 == 600
    assert recs["prototype_floor_area"].p10 >= 600
    assert "adu.unit_size_sqft" in recs["prototype_floor_area"].assumption_ids


def test_all_conversion_case_has_no_comparable_units():
    changes = {
        "adu.detached_form_share_detached_lots": 0,
        "adu.detached_form_share_attached_lots": 0,
        "adu.conversion_share_of_attached_forms": 1,
    }
    res = run("TL", ["adu"], overrides=changes, seed=5, draws=DRAWS)
    o = res.reforms["adu"]
    books = o.accounting
    assert books.records["comparable"].p90 == 0
    assert books.records["local_outside_bps"].p50 == pytest.approx(o.added.p50)
    assert o.added.p50 > 0
    assert "Bound" in books.status["local_outside_bps"]


def test_net_if_completed_reconciles_with_demolitions(result):
    for key, o in result.reforms.items():
        recs = o.accounting.records
        assert recs["net_if_completed"].p50 == o.gross_net["net_stock"].p50, key
        assert recs["demolitions_caused"].p10 >= -1e-9, key


def test_records_have_ids_and_serialise(result):
    index = record_index(result)
    data = json.loads(json.dumps(result.to_dict()))
    ref = "reform_results.adu.accounting.records.comparable"
    assert ref in index
    assert resolve(data, ref)["p50"] == pytest.approx(index[ref].p50)
    assert data["reform_results"]["adu"]["accounting"]["headline"]["scope"] == "all work types"


def test_rules_name_every_work_type_once():
    rules = load_rules()
    types = rules["work_types"]
    assert {t["bps"] for t in types.values()} <= {"comparable", "outside", "uncertain"}
    assert set(WORK_TYPES) <= set(types)
    assert set(rules["family_work_type"].values()) <= set(types)
