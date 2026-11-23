"""Shares and medians from ACS bracket counts: known answers, edges, bounds, vectorised."""

import csv
import math
from pathlib import Path

import numpy as np
import pytest

from visionhousing.features import brackets as br

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "testland"

# Three closed brackets and an open top: [0,10) [10,20) [20,40) [40,inf)
EDGES = np.array([[0, 10], [10, 20], [20, 40], [40, math.inf]], dtype=float)


def tracts() -> list[dict]:
    with (FIXTURES / "tracts.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- edge tables


def test_edge_tables_match_testland_columns():
    header = list(tracts()[0].keys())
    value_cols = [c for c in header if c.startswith("value_")]
    rent_cols = [c for c in header if c.startswith("rent_")]
    assert value_cols == list(br.VALUE.columns)
    assert rent_cols == [*br.RENT.columns, "rent_no_cash"]
    assert len(br.VALUE.columns) == 26 and len(br.RENT.columns) == 24


def test_parse_bracket():
    assert br.parse_bracket("value_lt_10000") == (0.0, 10000.0)
    assert br.parse_bracket("rent_800_899") == (800.0, 900.0)
    assert br.parse_bracket("value_2000000_plus") == (2000000.0, math.inf)
    with pytest.raises(ValueError):
        br.parse_bracket("rent_no_cash")


def test_edges_from_columns_equal_table_edges():
    for table in (br.VALUE, br.RENT):
        np.testing.assert_array_equal(br.edges_from_columns(table.columns), table.edges)


def test_edges_must_be_contiguous():
    with pytest.raises(ValueError):
        br.edges_from_columns(["rent_lt_100", "rent_150_199"])
    with pytest.raises(ValueError):
        br.share_above([1, 1], [[0, math.inf], [10, 20]], 5)


def test_counts_from_row_skips_no_cash_rent():
    row = tracts()[0]
    counts = br.counts_from_row(row, br.RENT)
    assert counts.sum() == int(row["households_renter"]) - int(row["rent_no_cash"])


# ---------------------------------------------------------------- known answers


def test_uniform_over_closed_brackets():
    # 10 units per $10 of width: a uniform distribution on [0, 40).
    counts = [10, 10, 20, 0]
    for t, want in [(0, 1.0), (5, 0.875), (15, 0.625), (30, 0.25), (40, 0.0)]:
        r = br.share_above(counts, EDGES, t)
        assert r.value == pytest.approx(want), t
        assert br.share_below(counts, EDGES, t).value == pytest.approx(1 - want)
        assert not r.in_top_bracket and r.bound is None


def test_threshold_exactly_on_an_edge():
    counts = [1, 2, 3, 4]
    assert br.share_above(counts, EDGES, 10).value == pytest.approx(9 / 10)
    assert br.share_above(counts, EDGES, 20).value == pytest.approx(7 / 10)
    # On the top bracket's low edge: all of the top bracket is above, no bound.
    r = br.share_above(counts, EDGES, 40)
    assert r.value == pytest.approx(4 / 10)
    assert not r.in_top_bracket and r.bound is None


def test_threshold_below_everything():
    r = br.share_above([1, 2, 3, 4], EDGES, -100)
    assert r.value == 1.0 and r.bound is None
    assert br.share_below([1, 2, 3, 4], EDGES, -100).value == 0.0


def test_threshold_above_everything_lower_bound_rule():
    r = br.share_above([1, 2, 3, 4], EDGES, 1e9)
    assert r.value == 0.0
    assert r.in_top_bracket and r.bound == "lower" and r.rule == "lower_bound"
    b = br.share_below([1, 2, 3, 4], EDGES, 1e9)
    assert b.value == 1.0 and b.bound == "upper"


def test_open_top_bracket_flag_and_rules():
    counts = [0, 0, 0, 10]
    low = br.share_above(counts, EDGES, 80)
    assert low.value == 0.0 and low.in_top_bracket and low.bound == "lower"
    par = br.share_above(counts, EDGES, 80, top_rule="pareto", alpha=2.0)
    assert par.value == pytest.approx((40 / 80) ** 2)
    assert par.in_top_bracket and par.bound is None
    # The lower-bound answer never exceeds the Pareto one.
    assert low.value <= par.value


def test_pareto_needs_alpha_and_rule_must_be_known():
    with pytest.raises(ValueError):
        br.share_above([1, 1, 1, 1], EDGES, 50, top_rule="pareto")
    with pytest.raises(ValueError):
        br.share_above([1, 1, 1, 1], EDGES, 50, top_rule="guess")


def test_zero_totals_give_nan_and_reason():
    r = br.share_above([0, 0, 0, 0], EDGES, 15)
    assert math.isnan(r.value) and r.reason and "zero total" in r.reason
    b = br.share_below([0, 0, 0, 0], EDGES, 15)
    assert math.isnan(b.value) and b.reason
    m = br.median_from_brackets([0, 0, 0, 0], EDGES)
    assert math.isnan(m.value) and m.reason


def test_missing_inputs_give_nan_and_reason():
    assert br.share_above([1, math.nan, 1, 1], EDGES, 15).reason == "missing bracket count"
    assert br.share_above([1, 1, 1, 1], EDGES, math.nan).reason == "missing threshold"


def test_bad_counts_raise():
    with pytest.raises(ValueError):
        br.share_above([1, 2, 3], EDGES, 15)
    with pytest.raises(ValueError):
        br.share_above([1, -2, 3, 4], EDGES, 15)


def test_no_cash_rent_left_out_of_denominator():
    row = dict(tracts()[0])
    before = br.share_above(br.counts_from_row(row, br.RENT), br.RENT, 1200)
    row["rent_no_cash"] = "100000"
    after = br.share_above(br.counts_from_row(row, br.RENT), br.RENT, 1200)
    assert before == after


# ---------------------------------------------------------------- vectorised


@pytest.mark.parametrize(("rule", "alpha"), [("lower_bound", None), ("pareto", 1.7)])
def test_vectorised_equals_scalar_loop(rule, alpha):
    rows = tracts()
    counts = np.array([br.counts_from_row(r, br.VALUE) for r in rows])
    counts[3] = 0  # one zero-total tract
    rng = np.random.default_rng(7)
    thresholds = rng.uniform(-1e4, 3e6, len(rows))
    thresholds[5] = 200_000  # exactly on an edge
    thresholds[6] = 2_500_000  # inside the open top bracket
    for fn, many in [(br.share_above, br.share_above_many), (br.share_below, br.share_below_many)]:
        v = many(counts, br.VALUE, thresholds, top_rule=rule, alpha=alpha)
        for i in range(len(rows)):
            s = fn(counts[i], br.VALUE, thresholds[i], top_rule=rule, alpha=alpha)
            if math.isnan(s.value):
                assert math.isnan(v.value[i]) and v.reason[i] == s.reason
            else:
                assert v.value[i] == pytest.approx(s.value, abs=1e-12)
                assert v.reason[i] == ""
            assert bool(v.in_top_bracket[i]) == s.in_top_bracket
            assert (v.bound[i] or None) == s.bound
    assert v.in_top_bracket[6]


def test_vectorised_scalar_threshold_broadcasts():
    counts = np.array([[10, 10, 20, 0], [0, 0, 0, 0]], dtype=float)
    v = br.share_above_many(counts, EDGES, 15)
    assert v.value[0] == pytest.approx(0.625)
    assert math.isnan(v.value[1]) and "zero total" in v.reason[1]


# ---------------------------------------------------------------- median


def test_median_known_answer():
    assert br.median_from_brackets([10, 10, 20, 0], EDGES).value == pytest.approx(20.0)
    assert br.median_from_brackets([0, 10, 0, 0], EDGES).value == pytest.approx(15.0)
    top = br.median_from_brackets([0, 0, 1, 5], EDGES)
    assert top.value == 40.0 and top.bound == "lower" and top.bracket == 3


def test_median_on_testland_lands_in_published_bracket():
    for row in tracts():
        for table, col in [(br.VALUE, "median_home_value"), (br.RENT, "median_gross_rent")]:
            m = br.median_from_brackets(br.counts_from_row(row, table), table)
            low, high = table.edges[m.bracket]
            published = float(row[col])
            assert low <= published <= high, (row["geoid"], col, m.value, published)
            assert published == pytest.approx(m.value, rel=0.02)


# ---------------------------------------------------------------- labels


def api_labels(table: br.BracketTable) -> dict[str, str]:
    out = {f"{table.table}_001E": "Estimate!!Total:"}
    for i, label in enumerate(table.labels):
        out[f"{table.table}_{table.first_var + i:03d}E"] = f"Estimate!!Total:!!{label}"
    if table is br.RENT:
        out["B25063_002E"] = "Estimate!!Total:!!With cash rent:"
        for i, label in enumerate(table.labels):
            out[f"B25063_{3 + i:03d}E"] = f"Estimate!!Total:!!With cash rent:!!{label}"
        out["B25063_027E"] = "Estimate!!Total:!!No cash rent"
    return out


def test_check_labels_accepts_correct_labels():
    assert br.check_labels(api_labels(br.VALUE), br.VALUE, 2023) == []
    assert br.check_labels(api_labels(br.RENT), br.RENT, 2023) == []
    assert br.RENT.labels[0] == "Less than $100"
    assert br.VALUE.labels[-1] == "$2,000,000 or more"


def test_check_labels_catches_renumbering():
    labels = api_labels(br.VALUE)
    labels["B25075_027E"] = "Estimate!!Total:!!$1,000,000 or more"
    del labels["B25075_002E"]
    labels["B25075_028E"] = "Estimate!!Total:!!$3,000,000 or more"
    problems = br.check_labels(labels, br.VALUE, 2023)
    assert len(problems) == 3
    assert any("B25075_027E" in p for p in problems)
    assert any("B25075_002E" in p and "missing" in p for p in problems)
    assert any("B25075_028E" in p for p in problems)


def test_check_labels_rejects_old_vintage():
    with pytest.raises(ValueError):
        br.check_labels(api_labels(br.VALUE), br.VALUE, 2012)


def test_variables_map_to_columns():
    v = br.RENT.variables()
    assert v["B25063_003E"] == "rent_lt_100"
    assert v["B25063_026E"] == "rent_3500_plus"
    assert v["B25063_027E"] == "rent_no_cash"
    assert br.VALUE.variables()["B25075_027E"] == "value_2000000_plus"
