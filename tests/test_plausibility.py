"""Plausibility data (``validation/plausibility.py``) on Testland.

The model's rate must come from the run's own records (added units / eligible
lots / years x 1,000), observed rates must carry their caveats, the band must
be the lowest to highest observed rate in the same unit, and the sentence must
follow the stated rule when our range sits outside the band.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.engine.core import run
from visionhousing.validation.plausibility import (
    load_settings,
    observed_rates,
    plausibility,
    reform_rate,
)

API = "/api/v1"
DRAWS = 200


@pytest.fixture(scope="module")
def result():
    return run("TL", ["adu", "missing_middle", "tod", "parking", "fees"], seed=0, draws=DRAWS)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return AsgiClient(create_app(packs_dir=tmp_path_factory.mktemp("packs")))


def test_rate_is_added_over_eligible_lots_per_year_draw_by_draw(result):
    rate = reform_rate(result, "adu", "per_1000_lots_year")
    o = result.reforms["adu"]
    added = np.asarray(o.state_draws, float)
    lots = np.asarray(o.result.detail.stages["eligible"], float).sum(axis=1)
    assert np.percentile(lots, 50) == pytest.approx(o.records["eligible"].p50)
    per_draw = added / lots / 5 * 1000
    for q, want in zip(("p10", "p50", "p90"), np.percentile(per_draw, [10, 50, 90]), strict=True):
        assert rate[q] == pytest.approx(want, abs=1e-4)
    assert rate["state"] == "observed" and "eligible lots" in rate["formula"]
    assert "in each of" in rate["formula"]


def test_rate_is_not_a_ratio_of_percentiles():
    """Hand case: added and eligible lots move together, so the per-draw rate is flat."""
    from types import SimpleNamespace

    from visionhousing.engine.records import Record

    def rec(name, unit, lo, mid, hi):
        return Record(name=name, unit=unit, formula="x", p10=lo, p50=mid, p90=hi)

    added = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    lots = np.array([[500.0, 500.0], [1000.0, 1000.0], [1500.0, 1500.0], [2000.0, 2000.0],
                     [2500.0, 2500.0]])  # fmt: skip
    o = SimpleNamespace(
        added=rec("added units", "units", 14.0, 30.0, 46.0),
        records={"eligible": rec("eligible lots", "lots", 1400.0, 3000.0, 4600.0)},
        state_draws=added,
        result=SimpleNamespace(detail=SimpleNamespace(stages={"eligible": lots})),
    )
    run_ = SimpleNamespace(reforms={"adu": o}, to_dict=dict)
    rate = reform_rate(run_, "adu", "per_1000_lots_year")
    # every draw: added / lots = 10 / 1,000 -> 2 per 1,000 lots per year
    assert rate["p10"] == pytest.approx(2.0) and rate["p90"] == pytest.approx(2.0)
    # the old rule (P10 added / P50 lots) would have given 14 / 3,000 / 5 x 1,000 = 0.93
    assert rate["p10"] != pytest.approx(14 / 3000 / 5 * 1000)


def test_a_saved_run_dict_has_no_per_lot_rate(result):
    b = reform_rate(result.to_dict(), "adu", "per_1000_lots_year")
    assert b["state"] == "missing" and b["p50"] is None
    assert "draw by draw" in b["formula"]
    # the per-100,000 rate is the run's own record, so a saved run still has it
    c = reform_rate(result.to_dict(), "adu", "per_100k_residents_year")
    assert c["p50"] == pytest.approx(result.reforms["adu"].records["per_100k"].p50, abs=1e-4)


def test_per_100k_rate_reads_the_runs_own_record(result):
    rate = reform_rate(result, "adu", "per_100k_residents_year")
    assert rate["p50"] == pytest.approx(result.reforms["adu"].records["per_100k"].p50, abs=1e-4)


def test_missing_reform_and_cost_reforms_have_a_value_state(result):
    assert (
        reform_rate(run("TL", ["fees"], seed=0, draws=50), "adu", "per_1000_lots_year")["state"]
        == "missing"
    )
    card = plausibility(result)
    for key in ("parking", "fees"):
        assert card["reforms"][key]["state"] == "not_applicable"
        assert "no per-lot rate" in card["reforms"][key]["note"]


def test_observed_rates_carry_caveats_and_are_kept_once():
    obs = observed_rates("adu")
    lots = obs["per_1000_lots_year"]
    assert len(lots) >= 3
    assert [p["value"] for p in lots] == sorted(p["value"] for p in lots)
    assert all(p["caveat"] and p["source"] and p["place"] for p in lots)
    people = obs["per_100k_residents_year"]
    values = [p["value"] for p in people]
    assert len(values) == len(set(values))  # the Pew rate is in two files, kept once
    assert any(p.get("also_in") for p in people)
    assert observed_rates("missing_middle").get("per_1000_lots_year", []) == []


def test_band_and_rule_written_sentences(result):
    card = plausibility(result, ["adu"])
    rows = {m["measure"]: m for m in card["reforms"]["adu"]["measures"]}
    m = rows["per_1000_lots_year"]
    band = m["band"]
    assert band["low"] == min(p["value"] for p in m["observed"])
    assert band["high"] == max(p["value"] for p in m["observed"])
    assert band["position"] in {"below", "above", "partly_below", "partly_above", "inside"}
    assert m["sentence"].startswith("Our ")
    settings = load_settings()
    reason = " ".join(settings["reforms"]["adu"]["outside_band"]["below"].split())[:40]
    if band["position"] in ("below", "partly_below"):
        assert reason in m["sentence"]


def test_every_number_comes_with_its_server_text(result):
    from visionhousing.validation.plausibility import _fmt

    card = plausibility(result, ["adu"])
    for m in card["reforms"]["adu"]["measures"]:
        model = m["model"]
        if model["p50"] is not None:
            assert model["shown"] == {q: _fmt(model[q]) for q in ("p10", "p50", "p90")}
            assert model["shown"]["p50"] in m["sentence"]  # the strip and sentence agree
        for p in m["observed"]:
            assert p["shown"] == _fmt(p["value"])
        if m["band"].get("low") is not None:
            assert m["band"]["shown"] == {"low": _fmt(m["band"]["low"]),
                                          "high": _fmt(m["band"]["high"])}  # fmt: skip
        axis = m["axis"]
        vals = [v for v in (model["p10"], model["p90"], *(p["value"] for p in m["observed"]))
                if v is not None]  # fmt: skip
        if vals:
            assert axis["hi"] == max(vals) and axis["lo"] <= min(vals)
            assert axis["shown"] == {"lo": _fmt(axis["lo"]), "hi": _fmt(axis["hi"])}
            ratio = load_settings()["axis_log_ratio"]
            assert axis["log"] == (min(vals) > 0 and max(vals) / min(vals) > ratio)


@pytest.mark.parametrize(
    ("p10", "p50", "p90", "position", "words"),
    [
        (0.1, 0.2, 0.5, "below", "sits below all of"),
        (20.0, 30.0, 40.0, "above", "sits above all of"),
        (0.5, 0.9, 2.0, "partly_below", "middle estimate"),
        (1.5, 2.0, 3.0, "inside", "sits within"),
    ],
)
def test_sentence_rule_for_each_position(result, p10, p50, p90, position, words, monkeypatch):
    from visionhousing.validation import plausibility as pl

    real = pl.reform_rate

    def fake(run_, reform, measure, settings=None):
        out = real(run_, reform, measure, settings)
        if measure == "per_1000_lots_year":
            out = {**out, "p10": p10, "p50": p50, "p90": p90, "state": "observed"}
        return out

    monkeypatch.setattr(pl, "reform_rate", fake)
    card = plausibility(result, ["adu"])
    m = next(x for x in card["reforms"]["adu"]["measures"] if x["measure"] == "per_1000_lots_year")
    assert m["band"]["position"] == position
    assert words in m["sentence"]
    if position == "inside":
        assert "Why:" not in m["sentence"]
    else:
        assert "Why:" in m["sentence"]


def test_no_band_says_so(result):
    card = plausibility(result, ["missing_middle"])
    m = card["reforms"]["missing_middle"]["measures"][0]
    assert m["band"]["state"] == "missing" and m["band"]["n"] == 0
    assert "nothing to set it beside" in m["sentence"] or "no observed rate" in m["sentence"]


def test_plausibility_endpoint(client):
    reply = client.get(f"{API}/plausibility/TL?r=adu&draws={DRAWS}")
    assert reply.status == 200
    body = reply.json()
    assert body["state"] == "TL" and body["draws"] == DRAWS
    assert list(body["reforms"]) == ["adu"]
    m = body["reforms"]["adu"]["measures"][0]
    assert m["model"]["p50"] is not None and m["sentence"]
    assert client.get(f"{API}/plausibility/TL?r=nope").status == 400
    assert client.get(f"{API}/plausibility/TL?zz=1").status == 400
