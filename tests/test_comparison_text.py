"""The comparisons reply carries a text form beside every number (api.comparison_text)."""

from __future__ import annotations

import copy

import pytest

from visionhousing.api.comparison_text import comparison_texts, homes_text
from visionhousing.engine.comparisons import comparisons


@pytest.fixture(scope="module")
def raw():
    return comparisons(
        "TL", ["interactions", "dials", "matrix", "ranking", "ablation"], seed=0, draws=200
    )


def _ranges(node, path=""):
    if isinstance(node, dict):
        if all(isinstance(node.get(k), float | int) for k in ("p10", "p50", "p90")):
            yield path, node
        for k, v in node.items():
            yield from _ranges(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _ranges(v, f"{path}[{i}]")


def test_every_range_has_text_and_no_value_changes(raw):
    before = copy.deepcopy(raw)
    out = comparison_texts(raw)
    assert raw == before  # the input is not changed
    found = list(_ranges(out))
    assert found
    for path, r in found:
        assert set(r["shown"]) == {"p10", "p50", "p90"}, path
        assert r["likely"] == f"{r['shown']['p10']} to {r['shown']['p90']}", path
    # values are copied, never altered
    assert (
        out["interactions"]["state"]["package"]["p50"]
        == raw["interactions"]["state"]["package"]["p50"]
    )
    pkg = out["interactions"]["state"]["package"]
    assert pkg["shown"]["p50"] == homes_text(pkg["p50"])


def test_headlines_and_row_texts(raw):
    out = comparison_texts(raw)
    for part in ("interactions", "matrix", "ranking"):
        assert out[part]["headline"], part
    for dial in out["dials"]["dials"].values():
        assert dial["headline"].startswith("At the benchmark")
        for row in dial["targets"]["rows"]:
            assert row["homes_text"] and row["p50"]["text"] and row["p10"]["text"]
            if row["p50"]["status"] == "out_of_reach":
                assert row["p50"]["text"] == "out of reach"
    for row in out["dials"]["adoption"]["rows"]:
        assert row["p50"]["sentence"] and row["p10"]["sentence"]
    assert len(out["matrix"]["rows"]["multipliers_text"]) == len(
        out["matrix"]["rows"]["multipliers"]
    )
    assert out["ablation"]["single_input"]["sentence"]
    assert out["ablation"]["property"]["sentence"]
    for pair in out["ranking"]["pairs"]:
        assert pair["share_text"].endswith("%")


def test_homes_text_noise_and_sign():
    assert homes_text(7e-16) == "0"
    assert homes_text(-12.4) == "\u221212"
    assert homes_text(1234.5) == "1,230"
