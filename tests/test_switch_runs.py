"""Switch runs on Testland: one input fixed, every other input drawn as in the run.

Checks that a fixed input no longer moves the answer, that the draws are
paired with the run's (same seed, same draws), that the central setting is
close to the run, that every record carries a formula and ids, and that the
caches and the switch endpoint serve the ranges once they exist.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.engine.core import run
from visionhousing.engine.montecarlo import BASELINE_STREAM
from visionhousing.grades import switch_payload as grade_switch
from visionhousing.sensitivity import drivers, input_draws
from visionhousing.sensitivity.switch_runs import (
    SETTINGS,
    _one_setting,
    cached_switch_runs,
    clear_switch_cache,
    switch_payload,
    switch_runs,
)
from visionhousing.sensitivity.variance import first_order_share

DRAWS = 1000
REFORMS = ["adu", "fees", "parking"]


@pytest.fixture(scope="module")
def result():
    return run("TL", REFORMS, seed=0, draws=DRAWS)


@pytest.fixture(scope="module")
def adu(result):
    clear_switch_cache()
    return switch_runs(result, "adu")


def _state_draws(result, key, parameter, setting):
    """The fixed run's state draws, worked out again (the records hold only P10/P50/P90)."""
    d = drivers(result, key)
    row = d.tornado.set_index("parameter").loc[parameter]
    value = float(row[setting])
    body = _one_setting(result, key, parameter, str(row.label), str(row.unit), setting, value)
    return body, value


def test_shape_follows_the_tornado(result, adu):
    top = drivers(result, "adu").tornado.head(3)
    assert list(adu) == list(top["parameter"])
    for by in adu.values():
        assert tuple(by) == SETTINGS
        for body in by.values():
            assert set(body["counties"]) == set(result.county_ids)
            for rec in (body["state"], *body["counties"].values()):
                assert rec.p10 <= rec.p50 <= rec.p90
                assert "fixed at its" in rec.formula and "same draws" in rec.formula
                assert rec.assumption_ids


def test_counties_add_up_in_the_middle(adu):
    for by in adu.values():
        for body in by.values():
            assert sum(r.p50 for r in body["counties"].values()) >= 0
            assert body["state"].p90 >= max(r.p90 for r in body["counties"].values()) - 1e-9


def test_fixed_input_explains_no_variance(result, adu):
    """With the input fixed, the run's own draws of it say nothing about the answer."""
    draws, _ = input_draws(result, "adu")
    run_state = np.asarray(result.reforms["adu"].state_draws)
    pid = next(p for p in adu if p != BASELINE_STREAM)
    before = first_order_share(draws[pid], run_state, bins=10)
    rerun = _rerun_state(result, "adu", pid, "central")
    after = first_order_share(draws[pid], rerun, bins=10)
    assert before > 0.05
    assert after < 0.02  # only bin noise is left


def _rerun_state(result, key, pid, setting):
    from dataclasses import replace

    from visionhousing.engine.adapters import OPTION_REPLACE
    from visionhousing.engine.registry import get_reform
    from visionhousing.ledger import apply_overrides

    res = result.reforms[key].result
    p = res.ledger[pid]
    v = getattr(p, setting)
    led = apply_overrides(res.ledger, {pid: {"central": v, "low": v, "high": v, "shape": "fixed"}})
    rr = get_reform(key).adapter(
        result.run_context, replace(result.draws_obj), {OPTION_REPLACE: led}
    )
    return rr.county_draws(tuple(result.county_ids)).sum(axis=0)


def test_paired_draws(result, adu):
    """Low and high share every other draw: the paired gap has one sign in almost every draw."""
    pid = next(p for p in adu if p != BASELINE_STREAM)
    low = _rerun_state(result, "adu", pid, "low")
    high = _rerun_state(result, "adu", pid, "high")
    gap = high - low
    sign = np.sign(np.median(gap))
    assert sign != 0
    assert np.mean(np.sign(gap) == sign) > 0.9
    row = drivers(result, "adu").tornado.set_index("parameter").loc[pid]
    assert sign == np.sign(row.total_high - row.total_low)
    # the records agree with the arrays
    assert adu[pid]["low"]["state"].p50 == pytest.approx(float(np.median(low)))
    assert adu[pid]["high"]["state"].p50 == pytest.approx(float(np.median(high)))


def test_fixing_an_unused_input_changes_nothing(result):
    """Fixing an input the reform never reads leaves every draw as in the run."""
    res = result.reforms["adu"].result
    used = set(res.assumption_ids)
    unused = next(pid for pid, p in res.ledger.items() if pid not in used and p.shape != "fixed")
    body = _one_setting(result, "adu", unused, "x", "", "high", float(res.ledger[unused].high))
    run_rec = result.reforms["adu"].added
    assert body["state"].p10 == pytest.approx(run_rec.p10)
    assert body["state"].p50 == pytest.approx(run_rec.p50)
    assert body["state"].p90 == pytest.approx(run_rec.p90)


def test_central_fixed_is_close_to_the_run(result, adu):
    run_rec = result.reforms["adu"].added
    for pid, by in adu.items():
        if pid == BASELINE_STREAM:
            continue
        mid = by["central"]["state"]
        assert mid.p50 == pytest.approx(run_rec.p50, rel=0.25)
        # one input's spread is gone, so the range is not wider than the run's by much
        assert (mid.p90 - mid.p10) <= 1.1 * (run_rec.p90 - run_rec.p10)


def test_baseline_position_switch(result):
    runs = switch_runs(result, "fees")
    if BASELINE_STREAM not in runs:
        pytest.skip("the baseline position is not a top driver of fees on Testland")
    by = runs[BASELINE_STREAM]
    assert by["low"]["state"].p50 < by["central"]["state"].p50 < by["high"]["state"].p50
    assert BASELINE_STREAM not in by["low"]["state"].assumption_ids


def test_cache_and_grades_read_the_ranges(result, adu):
    assert cached_switch_runs(result, "adu") is adu
    body = grade_switch(result, ["adu"], county="99001")
    rows = body["reforms"]["adu"]["drivers"]
    assert body["reforms"]["adu"]["ranges_from"] == "engine"
    for row in rows:
        ranges = row["ranges"]
        assert set(ranges) == set(SETTINGS)
        assert ranges["low"]["state"]["p50"] == pytest.approx(
            adu[row["parameter"]]["low"]["state"].p50
        )
        assert "county" in ranges["central"]


def test_disk_cache_round_trip(result, tmp_path, monkeypatch):
    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DIR", str(tmp_path / "cache"))
    clear_switch_cache()
    first = switch_runs(result, "parking")
    clear_switch_cache()
    again = cached_switch_runs(result, "parking")
    assert again is not None
    pid = next(iter(first))
    assert again[pid]["high"]["state"] == first[pid]["high"]["state"]
    assert list(again) == list(first)
    clear_switch_cache()


def test_export_payload(adu):
    body = switch_payload("TL", "adu", draws=DRAWS)
    assert body is not None and body["reform"] == "adu" and body["draws"] == DRAWS
    assert [d["rank"] for d in body["drivers"]] == [1, 2, 3]
    one = body["drivers"][0]
    assert set(one["settings"]) == set(SETTINGS)
    assert one["inputs"]["low"] <= one["inputs"]["central"] <= one["inputs"]["high"]
    assert "counties" in one["settings"]["low"]
    assert switch_payload("TL", "fees", draws=DRAWS - 1, compute=False) is None


def test_endpoint_serves_ranges_once_cached(tmp_path_factory):
    clear_switch_cache()
    client = AsgiClient(create_app(packs_dir=tmp_path_factory.mktemp("packs")))
    reply = client.get(f"/api/v1/switch/TL?r=adu&draws={DRAWS}")
    assert reply.status == 200
    assert reply.json()["reforms"]["adu"]["ranges_from"] is None
    switch_runs(run("TL", ["adu"], seed=0, draws=DRAWS), "adu")
    reply = client.get(f"/api/v1/switch/TL?r=adu&draws={DRAWS}&county=99003")
    body = reply.json()["reforms"]["adu"]
    assert body["ranges_from"] == "engine"
    assert "county" in body["drivers"][0]["ranges"]["high"]
    assert client.get(f"/api/v1/switch/TL?r=adu&draws={DRAWS}&county=12345").status == 404
    clear_switch_cache()
