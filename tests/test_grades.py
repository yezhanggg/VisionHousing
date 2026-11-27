"""Evidence grades, number trace and the one-assumption switch, on Testland.

The grade rule must be deterministic and monotone (a better input never lowers
a grade), every trace must reach the number the run reports (to rounding), and
the three endpoints must answer with the engine's own numbers.
"""

from __future__ import annotations

import itertools
import math
import random

import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.engine.context import pack_dir
from visionhousing.engine.core import run
from visionhousing.grades import grade_run, load_rule, switch_payload, trace
from visionhousing.grades.rule import (
    COMPONENTS,
    applies,
    backtest_score,
    calibration_score,
    evidence_score,
    grade_from_components,
    law_score,
    letter,
    source_score,
)
from visionhousing.grades.trace import TraceError

API = "/api/v1"
DRAWS = 300
REFORMS = ["adu", "missing_middle", "tod", "parking", "fees"]
RANK = {"A": 4, "B": 3, "C": 2, "D": 1}


@pytest.fixture(scope="module")
def result():
    return run("TL", REFORMS, seed=0, draws=DRAWS)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return AsgiClient(create_app(packs_dir=tmp_path_factory.mktemp("packs")))


# ---------------------------------------------------------------- the rule


def test_rule_file_is_consistent():
    rule = load_rule()
    assert set(rule["weights"]) == set(COMPONENTS)
    assert math.isclose(sum(rule["weights"].values()), 1.0)
    assert [t["grade"] for t in rule["thresholds"]] == ["A", "B", "C", "D"]
    assert applies("fees") == ("sources", "backtest", "calibration", "evidence")
    assert applies("baseline") == ("sources", "backtest")


def test_grades_are_deterministic(result):
    ctx = result.run_context.state
    a = grade_run(ctx, result, "county", folder=pack_dir("TL"))
    b = grade_run(ctx, result, "county", folder=pack_dir("TL"))
    assert a == b
    for by_key in a["counties"].values():
        for g in by_key.values():
            assert g["grade"] in RANK
            assert g["sentence"].startswith(f"Grade {g['grade']}: ")
            assert 0.0 <= g["score"] <= 1.0
            assert math.isclose(sum(c["weight"] for c in g["components"]), 1.0)


def test_component_scores_are_monotone():
    rule = load_rule()
    order = ["not_in_pack", "study", "national", "multi_state", "local"]
    for lo, hi in itertools.combinations(order, 2):
        assert source_score([lo, "national"], rule) <= source_score([hi, "national"], rule)
    statuses = ["judgement", "sourced", "transferred", "local"]
    for lo, hi in itertools.combinations(statuses, 2):
        assert calibration_score([lo, "local"], rule) <= calibration_score([hi, "local"], rule)
    idents = ["none", "law_only", "descriptive", "matched", "causal"]
    for lo, hi in itertools.combinations(idents, 2):
        assert evidence_score(lo, rule) <= evidence_score(hi, rule)
    grid = [i / 20 for i in range(21)]
    for c1, c2 in itertools.combinations(grid, 2):
        assert backtest_score(c1, 20, rule) <= backtest_score(c2, 20, rule)
        assert law_score(c1, False, rule) <= law_score(c2, False, rule)
        assert law_score(c1, False, rule) <= law_score(c1, True, rule)
    for n1, n2 in itertools.combinations([1, 5, 10, 29, 30, 60], 2):
        assert backtest_score(0.7, n1, rule) <= backtest_score(0.7, n2, rule)
    assert backtest_score(None, 10, rule) == 0.0 <= backtest_score(0.1, 10, rule)


def test_better_inputs_never_lower_a_grade():
    rule = load_rule()
    rng = random.Random(7)  # noqa: S311 (a fixed test seed, not security)
    for key in ("adu", "fees", "baseline"):
        for _ in range(400):
            scores = {c: rng.random() for c in COMPONENTS}
            g = grade_from_components(key, scores, rule=rule)
            for c in COMPONENTS:
                better = dict(scores, **{c: min(1.0, scores[c] + rng.random() * 0.5)})
                h = grade_from_components(key, better, rule=rule)
                assert h.score >= g.score - 1e-12
                assert RANK[h.grade] >= RANK[g.grade]
    for s1, s2 in itertools.combinations([i / 100 for i in range(101)], 2):
        assert RANK[letter(s1, rule)] <= RANK[letter(s2, rule)]


# ---------------------------------------------------------------- the trace


def _ids(result):
    for key, o in result.reforms.items():
        yield f"reform_results.{key}.added"
        for c in o.counties:
            yield f"reform_results.{key}.counties.{c}"


def test_every_trace_reaches_the_reported_number(result):
    tol = load_rule()["trace"]["rounding_tolerance"]
    for rid in _ids(result):
        t = trace(result, rid)
        assert t["check"]["matches"], rid
        assert abs(t["check"]["last_step_p50"] - t["check"]["reported_p50"]) <= tol
        chain = t["check"]["chain_p50"]
        if t["kind"] == "central_chain":
            assert t["check"]["central_steps_recomputed"] >= 1
            assert t["check"]["central_steps_agree"], rid
        elif chain is not None:
            # the per-draw multipliers, applied draw by draw, rebuild the reported middle
            assert abs(chain - t["check"]["reported_p50"]) <= tol, rid
        for s in t["steps"]:
            assert s["unit"]
            if s["p50"] is not None:
                assert s["p10"] <= s["p50"] + 1e-9 <= s["p90"] + 2e-9


def test_adu_funnel_order_and_ids(result):
    t = trace(result, "reform_results.adu.added")
    assert t["kind"] == "funnel"
    assert t["steps"][0]["name"].startswith("eligible lots")
    assert t["steps"][-1]["name"].startswith("added units authorized")
    assert t["steps"][-1]["p50"] == pytest.approx(result.reforms["adu"].added.p50)
    assert all(s["assumption_ids"] or s["n"] == 1 for s in t["steps"])
    assert t["steps"][1]["record_id"] == "reform_results.adu.steps.1"
    mm = trace(result, "reform_results.missing_middle.added")
    assert mm["after"] and mm["after"][0]["name"].startswith("net added homes")


def test_trace_ratios_are_per_draw_not_ratios_of_percentiles(result):
    import numpy as np

    o = result.reforms["adu"]
    t = trace(result, "reform_results.adu.added")
    arrays = [np.asarray(a, float).sum(axis=1) for a in o.result.detail.stages.values()]
    for n, s in enumerate(t["steps"][1:], start=1):
        before, after = arrays[n - 1], arrays[n]
        ok = before > 0
        want = np.percentile(after[ok] / before[ok], [10, 50, 90])
        r = s["applied"]["ratio"]
        assert (r["p10"], r["p50"], r["p90"]) == pytest.approx(tuple(want), rel=1e-12)
        assert r["draws_defined"] == int(ok.sum())
        assert "draw by draw" in s["applied"]["text"]
    # the chain, multiplied draw by draw, rebuilds the reported middle value
    assert t["check"]["chain_p50"] == pytest.approx(o.added.p50, rel=1e-9)
    # a county trace uses the county's own draws
    county = next(iter(o.counties))
    tc = trace(result, f"reform_results.adu.counties.{county}")
    assert tc["check"]["chain_p50"] == pytest.approx(o.counties[county].p50, rel=1e-9)


def test_trace_rejects_bad_ids(result):
    for bad in ("baseline", "reform_results.nope.added", "reform_results.adu.steps.0",
                "reform_results.adu.counties.00000"):  # fmt: skip
        with pytest.raises(TraceError):
            trace(result, bad)


def test_switch_from_the_tornado(result):
    body = switch_payload(result, ["adu", "fees"])
    for key in ("adu", "fees"):
        rows = body["reforms"][key]["drivers"]
        assert len(rows) == 3
        assert [r["rank"] for r in rows] == [1, 2, 3]
        assert rows[0]["swing"] >= rows[1]["swing"] >= rows[2]["swing"]
        central = body["reforms"][key]["central_total"]
        for r in rows:
            assert r["settings"]["central"]["total"] == pytest.approx(central)
            assert (
                r["settings"]["low"]["input"]
                <= r["settings"]["central"]["input"]
                <= (r["settings"]["high"]["input"])
            )


# ---------------------------------------------------------------- endpoints


def _get(client, url):
    reply = client.get(url)
    return reply.status, (reply.json() if reply.body else None)


def test_grades_endpoint(client):
    status, body = _get(client, f"{API}/grades/TL?r=adu,fees&draws={DRAWS}")
    assert status == 200, body
    assert set(body["grades"]) == {"baseline", "adu", "fees"}
    assert body["rule"]["file"] == "assumptions/grades.yaml"
    assert {c["id"] for c in body["grades"]["fees"]["components"]} == set(applies("fees"))
    status, county = _get(client, f"{API}/grades/TL?r=adu&level=county&county=99001&draws={DRAWS}")
    assert status == 200
    assert list(county["counties"]) == ["99001"]
    assert _get(client, f"{API}/grades/TL?r=adu&level=tract")[0] == 400
    assert _get(client, f"{API}/grades/TL?r=adu&level=county&county=12345")[0] == 404


def test_trace_endpoint(client):
    q = f"draws={DRAWS}"
    status, body = _get(client, f"{API}/trace/TL?r=adu&id=reform_results.adu.added&{q}")
    assert status == 200, body
    assert body["check"]["matches"] and body["state"] == "TL"
    receipt = body["reproduce"]
    if receipt is not None:  # the intake lane's lookup is installed
        assert receipt["path"] == "reform_results.adu.added"
        assert receipt["record"]["p50"] == pytest.approx(body["reported"]["p50"])
    status, short = _get(client, f"{API}/trace/TL?r=adu&id=state&{q}")
    assert short["steps"] == body["steps"]
    status, one = _get(client, f"{API}/trace/TL?r=fees&id=99003&{q}")
    assert status == 200 and one["kind"] == "county_arrays" and one["check"]["matches"]
    status, every = _get(client, f"{API}/trace/TL?r=parking&id=counties&{q}")
    assert status == 200 and all(t["check"]["matches"] for t in every["counties"].values())
    assert _get(client, f"{API}/trace/TL?r=adu,fees&id=state")[0] == 400
    assert _get(client, f"{API}/trace/TL?r=adu&id=reform_results.adu.steps.0")[0] == 404
    assert _get(client, f"{API}/trace/TL?r=adu")[0] == 400


def test_switch_endpoint(client):
    status, body = _get(client, f"{API}/switch/TL?r=adu&draws={DRAWS}")
    assert status == 200, body
    rows = body["reforms"]["adu"]["drivers"]
    assert len(rows) == 3 and body["reforms"]["adu"]["from"] == "live"
    assert set(rows[0]["settings"]) == {"low", "central", "high"}


# ---------------------------------------------------------------- retrospective panel file


def _retro_ids(monkeypatch, audit_path):
    import visionhousing.grades.inputs as gi

    monkeypatch.setattr(gi, "RETRO_AUDIT_PATH", audit_path)
    gi._evidence.cache_clear()
    try:
        return {r.id: r for r in gi._retro_records()}, gi.retro_source()
    finally:
        gi._evidence.cache_clear()


def test_grades_read_the_audited_retro_results(monkeypatch):
    import visionhousing.grades.inputs as gi

    assert gi.RETRO_AUDIT_PATH.name == "retro_audit_results.yaml"
    records, src = _retro_ids(monkeypatch, gi.RETRO_AUDIT_PATH)
    assert {k: src[k] for k in ("file", "variant", "fallback")} == {
        "file": "assumptions/evidence/retro_audit_results.yaml",
        "variant": "audited",
        "fallback": False,
    }
    # The audited variant moves Saint Paul's first treated year from 2022 to 2024.
    sp = records["mn.retro.saint_paul_1to4_units.units_2_4"]
    assert "from 2024" in sp.horizon
    scores = load_rule()["components"]["evidence"]["identification_scores"]
    ev = gi.best_evidence("missing_middle", scores)
    assert ev["retro"]["variant"] == "audited"


def test_grades_fall_back_to_the_original_retro_run(monkeypatch, tmp_path, caplog):
    import visionhousing.grades.inputs as gi

    with caplog.at_level("WARNING", logger=gi.LOG.name):
        records, src = _retro_ids(monkeypatch, tmp_path / "absent.yaml")
    assert src["variant"] == "original" and src["fallback"] is True
    assert src["file"] == "assumptions/evidence/retro_results.yaml"
    assert "from 2022" in records["mn.retro.saint_paul_1to4_units.units_2_4"].horizon
    assert any("original run" in r.getMessage() for r in caplog.records)
    empty = tmp_path / "empty.yaml"
    empty.write_text("records: []\n", encoding="utf-8")
    assert _retro_ids(monkeypatch, empty)[1]["variant"] == "original"


def test_evidence_payload_labels_both_retro_pools():
    import visionhousing.grades.inputs as gi

    pools = {p["pool"]: p for p in gi.retro_source()["pools"]}
    assert pools["original"]["label"] == "original pool (81, as pre-registered)"
    assert pools["original"]["graded"] is True
    assert pools["original"]["file"] == "assumptions/evidence/retro_audit_results.yaml"
    assert pools["current"]["label"] == "current pool (68, update)"
    assert pools["current"]["graded"] is False
    assert pools["current"]["file"] == "assumptions/evidence/retro_current_pool_results.yaml"
    scores = load_rule()["components"]["evidence"]["identification_scores"]
    ev = gi.best_evidence("missing_middle", scores)
    assert ev["retro"]["variant"] == "audited"  # grades still read the original pool
    assert [p["pool"] for p in ev["retro"]["pools"]] == ["original", "current"]
