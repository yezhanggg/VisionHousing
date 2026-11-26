"""Validation cards: traceable coverage counts and fuller scores."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest
import yaml

from visionhousing.validation import cards
from visionhousing.validation.preregistration import PreregistrationError, verify


def test_interval_score_penalises_width_and_misses():
    s = cards.interval_score(np.array([90.0]), np.array([110.0]), np.array([100.0]), 0.2)
    assert s["score"][0] == pytest.approx(0.2) and s["miss"][0] == 0
    # A miss of 10 above the top costs 2 / 0.2 * 10 = 100, relative to y = 120.
    s = cards.interval_score(np.array([90.0]), np.array([110.0]), np.array([120.0]), 0.2)
    assert s["miss"][0] == pytest.approx(100 / 120)
    assert s["score"][0] == pytest.approx(20 / 120 + 100 / 120)


def _rows(origins, inside):
    return pd.DataFrame(
        {
            "origin": origins,
            "low": 90.0,
            "high": 110.0,
            "point": 100.0,
            "actual": [100.0 if i else 150.0 for i in inside],
        }
    )


def test_non_overlapping_subsets_use_every_anchor():
    rows = _rows(list(range(2012, 2020)), [True] * 7 + [False])
    rows["inside"] = rows["actual"] <= rows["high"]
    out = cards.non_overlapping(rows, 5)
    assert out["primary"]["origins"] == [2014, 2019]
    assert out["primary"]["covered"] == 1 and out["primary"]["cases"] == 2
    assert [a["anchor"] for a in out["anchors"]] == [2019, 2018, 2017, 2016, 2015]
    assert out["min"] == 0.5 and out["max"] == 1.0


def test_case_metrics_counts_every_case_and_scores_positive_ones():
    rows = _rows([2010, 2011, 2012], [True, False, True])
    rows.loc[2, "actual"] = 0.0  # a county that permitted nothing: counted, not scored
    rows.loc[2, "low"] = 0.0
    m = cards.case_metrics(rows, 0.2, 5)
    assert (m["cases"], m["covered"], m["above"]) == (3, 2, 1)
    assert m["excluded_from_relative_scores"] == 1
    assert m["signed_log_error_median"] == pytest.approx(np.median([0.0, np.log(1.5)]))


def test_fold_timeline_flags_selection_overlap():
    t = cards.fold_timeline([2012], 2, 5, 1990, 2025)[0]
    assert t["evaluation"] == [2014, 2018] and t["gap"] == [2013, 2013]
    assert t["train"] == [1990, 2012] and t["train_errors_from_origins_up_to"] == 2006
    assert t["selection_overlaps_evaluation"] is True


def test_block_fingerprint_and_refusal():
    settings = cards.load_settings()
    verify(cards.BLOCK, settings)
    changed = copy.deepcopy(settings)
    changed[cards.BLOCK]["interval_level"] = 0.9
    with pytest.raises(PreregistrationError):
        verify(cards.BLOCK, changed)


def test_parity_failure_withholds_new_scores():
    settings = cards.load_settings()
    rows = _rows([2012, 2013], [True, True])
    card = cards._card(
        cid="x",
        key="state_holdouts",
        text={},
        spec={"results": "r.yaml", "block": "b"},
        results={},
        geography={"level": "state", "area": "ZZ"},
        published={"cases": 2, "covered": 1, "coverage": 0.5},
        rows=rows,
        info={"rule": "r", "lead": 2, "horizon": 5, "first_year": 1990, "last_year": 2025},
        settings=settings,
        present_day_types=False,
    )
    assert card["parity"]["matches"] is False and "scores" not in card
    assert card["quote"] == "1/2"


def test_repository_cards_are_traceable():
    data = yaml.safe_load(cards.OUT_PATH.read_text(encoding="utf-8"))
    quotes = {c["id"]: c["quote"] for c in data["cards"]}
    assert quotes["baseline_reliability.state.PA"] == "8/8"
    assert quotes["state_holdouts.state.WY"] == "10/11"
    assert quotes["state_holdouts.state.MA"] == "6/11"
    for c in data["cards"]:
        for key in ("study", "results_file", "geography", "target", "release", "origin_set"):
            assert c.get(key), (c["id"], key)
        assert c["disclosures"]["selection_full_record"]
        if not c.get("listed_only"):
            assert c["parity"]["matches"] is True, c["id"]
            assert c["scores"]["non_overlapping"]["primary"]["cases"] >= 1
            assert c["fold_timeline"]
    pa = [c for c in cards.cards_for("pa")]
    assert {c["geography"]["area"] for c in pa} == {"PA"}


def test_validation_api_payload_carries_the_state_cards():
    from types import SimpleNamespace

    from visionhousing.api.service import _validation_cards

    assert _validation_cards(SimpleNamespace(synthetic=True, state="PA")) == []
    got = _validation_cards(SimpleNamespace(synthetic=False, state="MA"))
    assert "state_holdouts.state.MA" in {c["id"] for c in got}
    assert all(c["geography"]["area"] == "MA" for c in got)
