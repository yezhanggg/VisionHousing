"""The decision view (``api/decision.py``) on Testland.

The binding constraint must be the funnel stage that loses the largest share of the step
before it (the engine's own middle estimates), every stage and actor must come from
``assumptions/decision.yaml``, the evidence-coverage class must follow its three checks, the
map's other measures must follow their fixed breaks, and the map reply must carry the
decision block for a run of exactly one reform (and only then).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.api.decision import (
    MEASURE_CLASSES,
    binding_constraint,
    county_coverage,
    load_decision,
    measure_classes,
)
from visionhousing.engine.context import load_context
from visionhousing.engine.core import run

API = "/api/v1"
DRAWS = 300


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return AsgiClient(create_app(packs_dir=tmp_path_factory.mktemp("packs")))


@pytest.fixture(scope="module")
def result():
    return run("TL", ["adu", "missing_middle", "tod", "parking", "fees"], seed=0, draws=DRAWS)


def test_decision_file_names_known_actors_and_unique_ids():
    cfg = load_decision()
    ids = []
    for family, stages in cfg["stages"].items():
        assert stages, family
        for st in stages:
            assert st["actor"] in cfg["actors"], st["id"]
            assert st["id"].startswith(f"decision.stage.{family}."), st["id"]
            assert st["question"].endswith("?"), st["id"]
            assert ("lever" in st) != ("from" in st and "match" in st), st["id"]
            ids.append(st["id"])
    assert len(ids) == len(set(ids))
    assert set(cfg["actors"]) == {"state", "municipality", "utility", "builder", "household"}


@pytest.mark.parametrize("key", ["adu", "missing_middle", "tod"])
def test_binding_stage_is_the_largest_loss_of_the_engine_steps(result, key):
    o = result.reforms[key]
    bc = binding_constraint(o, key)
    assert bc is not None and bc["kind"] == "loss"
    names = [s.name.split(" (")[0] for s in o.steps]
    stages = list(o.result.detail.stages.values())
    for st in bc["stages"]:
        # 1 - after / before in each draw (tracts summed), then the P50: never P50 / P50
        before = np.asarray(stages[names.index(st["from_label"])], float).sum(axis=1)
        after = np.asarray(stages[names.index(st["to_label"])], float).sum(axis=1)
        ok = before > 0
        lost = 1 - np.clip(after[ok] / before[ok], 0, 1)
        p10, p50, p90 = np.percentile(lost, [10, 50, 90])
        assert math.isclose(st["lost_p10"], p10, rel_tol=1e-12, abs_tol=1e-15)
        assert math.isclose(st["lost"], p50, rel_tol=1e-12, abs_tol=1e-15)
        assert st["lost_p10"] <= st["lost"] <= st["lost_p90"]
        assert math.isclose(st["lost_p90"], p90, rel_tol=1e-12, abs_tol=1e-15)
        assert st["draws_defined"] == int(ok.sum())
        assert st["lost_text"].endswith("%") and " to " in st["lost_range_text"]
        assert "in each of" in st["formula"]
    top = max(s["lost"] for s in bc["stages"])
    assert bc["binding"]["lost"] == top
    assert bc["binding"]["question"]
    assert bc["binding"]["lost_range_text"] in bc["binding"]["sentence"]
    # Grouped by actor, largest loss first.
    losses = [g["lost"] for g in bc["by_actor"] if g["lost"] is not None]
    assert losses == sorted(losses, reverse=True)


def test_loss_is_worked_out_per_draw_not_from_percentiles():
    """Two draws where the ratio of middles and the middle of ratios differ."""
    from types import SimpleNamespace

    from visionhousing.engine import records as rec

    def step(name, unit):
        return rec.Record(name=name, unit=unit, formula="x", p10=1.0, p50=1.0, p90=1.0)

    cfg = {
        "actors": {"builder": {"word": "Builder", "who": "builders"}},
        "stages": {"adu": [{"id": "decision.stage.adu.x", "name": "Fit", "actor": "builder",
                            "why": "w", "question": "q?", "from": "eligible", "match": "fit"}]},
        "binding": {"rule": "r", "id": "decision.binding"},
    }  # fmt: skip
    # draws x tracts: before 100 / 10 / 50, after 10 / 9 / 25 -> kept 0.1 / 0.9 / 0.5
    eligible = np.array([[60.0, 40.0], [5.0, 5.0], [25.0, 25.0]])
    fit = np.array([[6.0, 4.0], [4.5, 4.5], [12.5, 12.5]])
    detail = SimpleNamespace(stages={"eligible": eligible, "fit": fit}, counties=["1", "1"])
    o = SimpleNamespace(
        steps=(step("eligible lots", "lots"), step("fit lots", "lots")),
        level="tract",
        result=SimpleNamespace(detail=detail),
    )
    bc = binding_constraint(o, "adu", cfg)
    lost = bc["stages"][0]["lost"]
    assert math.isclose(lost, 0.5)  # middle of 0.9 / 0.1 / 0.5 lost per draw
    # the old rule (1 - P50(after) / P50(before) = 1 - 10 / 50) would give 0.8
    assert not math.isclose(lost, 1 - 10 / 50)


@pytest.mark.parametrize("key", ["fees", "parking"])
def test_reforms_without_a_funnel_name_their_lever(result, key):
    bc = binding_constraint(result.reforms[key], key)
    assert bc is not None and bc["kind"] == "lever"
    assert bc["binding"]["value"]["shown"]


def test_coverage_class_follows_its_checks(result):
    ctx = load_context("TL")
    cov = county_coverage(ctx, result, list(result.county_ids))
    cfg = load_decision()["coverage"]
    floors = {c["id"]: int(c["min_checks"]) for c in cfg["classes"]}
    assert set(cov) == set(result.county_ids)
    for c, v in cov.items():
        assert [k["id"] for k in v["checks"]] == ["local_data", "rules_read", "backtest"], c
        assert v["met"] == sum(k["met"] for k in v["checks"])
        want = max((f, k) for k, f in floors.items() if v["met"] >= f)[1]
        assert v["class"] == want


def test_measure_classes_follow_the_fixed_breaks():
    rows = [
        {"fips": "1", "per_1000": {"p50": 0.1}, "added": {"p50": 5}},
        {"fips": "2", "per_1000": {"p50": 2}, "added": {"p50": 100}},
        {"fips": "3", "per_1000": {"p50": 50}, "added": {"p50": 5000}},
        {"fips": "4", "per_1000": None, "added": None},
    ]
    measures, by = measure_classes(rows)
    assert [m["id"] for m in measures] == ["per_1000", "added"]
    assert [c["id"] for c in measures[0]["classes"]] == list(MEASURE_CLASSES)
    assert by["1"] == {"per_1000": "q1", "added": "q1"}
    assert by["2"] == {"per_1000": "q3", "added": "q3"}
    assert by["3"] == {"per_1000": "q4", "added": "q4"}
    assert by["4"] == {"per_1000": "nodata", "added": "nodata"}


def test_map_reply_carries_the_decision_for_one_reform(client):
    one = client.get(f"{API}/states/TL/map?r=adu&seed=0&draws={DRAWS}").json()
    d = one["decision"]
    assert d["reform"] == "adu" and d["question"]["window"]
    assert d["binding"]["binding"]["id"].startswith("decision.stage.adu.")
    assert sum(c["count"] for c in d["coverage"]["counts"]) == len(one["counties"])
    top = max(one["counties"], key=lambda c: c["added"]["p50"])
    assert d["top_county"]["fips"] == top["fips"]
    assert [m["id"] for m in one["measures"]] == ["per_1000", "added", "coverage"]
    for c in one["counties"]:
        assert c["coverage"]["class"] in {"cov_good", "cov_partial", "cov_thin"}
        assert set(c["measure_class"]) == {"per_1000", "added"}
    if d["annual"] is not None:
        run_reply = client.post(
            f"{API}/run",
            {"state": "TL", "reforms": ["adu"], "seed": 0, "draws": DRAWS, "explain": False},
        ).json()
        years = run_reply["reform_results"]["adu"]["annual"]["state"]
        assert [y["p50"] for y in d["annual"]["years"]] == [y["p50"] for y in years]
        assert all(y["shown"]["p50"] for y in d["annual"]["years"])
    two = client.get(f"{API}/states/TL/map?r=adu,fees&seed=0&draws={DRAWS}").json()
    assert two["decision"] is None
    base = client.get(f"{API}/states/TL/map?r=&seed=0&draws={DRAWS}").json()
    assert base["decision"] is None
    assert all(c["coverage"] for c in base["counties"])
