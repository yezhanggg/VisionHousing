"""Back-test error by kind of place: grouping, statistics, sentences and the limits line."""

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from visionhousing.baseline import baseline_from_ledger, rules_from_ledger, settings_from_ledger
from visionhousing.baseline.places import (
    county_errors,
    county_groups,
    error_by_group,
    findings,
    group_stats,
    limits_sentence,
    load_place_rules,
    place_backtest,
    place_backtest_for_context,
    scored_cases,
    weakest_groups,
)
from visionhousing.engine.context import load_context

TESTLAND = Path(__file__).parent / "fixtures" / "testland"
COUNTIES = ("99001", "99003", "99005", "99007")


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def rules():
    return load_place_rules()


@pytest.fixture(scope="module")
def loose(rules):
    """The ledger rules with minimums low enough that Testland's groups are not flagged."""
    return {**rules, "min_counties": 1, "min_cases": 1}


def _scored(rows):
    """Scored cases from (place, projected, actual, covered) tuples."""
    frame = pd.DataFrame(rows, columns=["place", "projected", "actual", "covered"])
    frame["signed_error"] = np.log(frame["projected"] / frame["actual"])
    return frame


# ---------------------------------------------------------------- cases


def test_signed_error_is_positive_when_the_rule_ran_high():
    cases = pd.DataFrame(
        {
            "place": [0, 0, 1, 1],
            "origin": [2000, 2001, 2000, 2001],
            "projected": [120.0, 80.0, 100.0, 0.0],
            "actual": [100.0, 100.0, 0.0, 50.0],
            "ratio": [100 / 120, 100 / 80, 0.0, np.nan],
        }
    )
    out = scored_cases(cases, np.array([0, 0]))
    assert len(out) == 3  # the zero projection has no ratio and is dropped
    assert out.loc[0, "signed_error"] == pytest.approx(math.log(1.2))
    assert out.loc[1, "signed_error"] == pytest.approx(math.log(0.8))
    assert math.isnan(out.loc[2, "signed_error"])  # actual zero: no log error
    # each county's range comes only from the other county's ratios
    assert set(out["covered"].dropna()) <= {0.0, 1.0}


def test_group_stats_by_hand():
    s = _scored([(0, 120, 100, 1.0), (0, 110, 100, 0.0), (1, 90, 100, 1.0)])
    st = group_stats(s)
    assert st["cases"] == 3
    assert st["median_signed"] == pytest.approx(math.log(1.1))
    assert st["typical_pct"] == pytest.approx(0.1)
    assert st["median_abs"] == pytest.approx(min(abs(math.log(0.9)), math.log(1.1)), rel=0.2)
    assert (st["covered_cases"], st["interval_cases"]) == (2, 3)
    assert st["coverage"] == pytest.approx(2 / 3)
    empty = group_stats(s.iloc[0:0])
    assert empty["cases"] == 0 and math.isnan(empty["median_signed"])


# ---------------------------------------------------------------- groups on Testland


def test_testland_groups(ctx, rules):
    classes = np.array([2, 1, 1, 0])
    incentives = pd.DataFrame(
        {"county": list(COUNTIES), "tracts": [10, 10, 10, 10], "qct_tracts": [5, 1, 2, 0]}
    )
    groups, dims = county_groups(
        COUNTIES, classes, 3, rules, state="TL", tracts=ctx.tracts, incentives=incentives
    )
    by_key = {d["key"]: d for d in dims}
    assert [d["key"] for d in dims] == list(rules["dimensions"])
    assert groups["size_class"].tolist() == ["large", "medium", "medium", "small"]
    # Testland has no urban-area share, so area_type decides: Prairie is the rural one
    assert groups["urban_rural"].to_dict() == {
        "99001": "urban",
        "99003": "urban",
        "99005": "urban",
        "99007": "rural",
    }
    # median QCT share is 0.15: above it "higher share"
    assert groups["qct"].tolist() == ["higher share", "lower share", "higher share", "lower share"]
    assert groups["named"].tolist() == ["Harbor County"] + ["rest of the state"] * 3
    assert by_key["named"]["phrases"]["rest of the state"] == "the rest of the state"
    # no typology table given: listed, not available, with the reason
    assert by_key["typology"]["available"] is False
    assert "typology" in by_key["typology"]["reason"]


def test_missing_tables_leave_groupings_out_not_the_rest(rules):
    groups, dims = county_groups(COUNTIES, np.zeros(4, dtype=int), 3, rules, state="ZZ")
    avail = {d["key"]: d["available"] for d in dims}
    assert avail == {
        "size_class": True,
        "typology": False,
        "urban_rural": False,
        "qct": False,
        "named": False,
    }
    assert list(groups.columns) == ["size_class"]


def test_groups_partition_the_cases(ctx, loose):
    pb = place_backtest_for_context(ctx, "last_5_mean")
    table = pb.groups
    total = table.iloc[0]
    assert total["dimension"] == "all"
    for d in pb.dimensions:
        if not d["available"]:
            continue
        part = table[table["dimension"] == d["key"]]
        assert part["cases"].sum() == total["cases"], d["key"]
        assert part["counties"].sum() == total["counties"], d["key"]
    # Testland made its own typology from the pack tables
    assert next(d for d in pb.dimensions if d["key"] == "typology")["available"]


def test_small_groups_are_flagged_not_hidden(ctx, rules, loose):
    s = settings_from_ledger(ctx.ledger)
    rule = rules_from_ledger(ctx.ledger)[0]
    strict = place_backtest(ctx.permits, rule, s, rules, state="TL", tracts=ctx.tracts)
    easy = place_backtest(ctx.permits, rule, s, loose, state="TL", tracts=ctx.tracts)
    assert len(strict.groups) == len(easy.groups)  # same groups either way
    assert strict.groups["small"].all()  # four counties: below every minimum
    assert not easy.groups["small"].any()
    assert all("Few cases" in f for f in strict.groups["finding"])
    assert strict.limits is None  # nothing safe to name
    assert easy.limits is not None


# ---------------------------------------------------------------- sentences


def test_findings_say_high_low_and_counts(loose):
    s = _scored([(0, 120, 100, 1.0), (0, 125, 100, 0.0), (1, 80, 100, 1.0), (1, 75, 100, 1.0)])
    groups = pd.DataFrame({"urban_rural": ["rural", "urban"]}, index=pd.Index(["a", "b"]))
    dims = [
        {
            "key": "urban_rural",
            "title": "Urban or rural",
            "available": True,
            "reason": "",
            "groups": ["urban", "rural"],
            "phrases": {"urban": "urban counties", "rural": "rural counties"},
        }
    ]
    table = error_by_group(s, groups, dims, ["a", "b"], loose)
    rural = table[table["group"] == "rural"].iloc[0]
    assert rural["finding"] == (
        "The baseline runs 22% high in rural counties; its ranges caught the real total "
        "in 1 of 2 back-test cases."
    )
    urban = table[table["group"] == "urban"].iloc[0]
    assert urban["finding"].startswith("The baseline runs 23% low in urban counties")
    lines = findings(table, loose)
    assert lines[0].startswith("The baseline is about right on average across all counties")
    assert any("least reliable in rural counties" in x and "1 of 2" in x for x in lines)


def test_county_map_classes(loose):
    s = _scored([(0, 150, 100, 1.0), (1, 105, 100, 1.0), (2, 80, 100, 1.0)])
    table, classes = county_errors(s, ["a", "b", "c", "d"], loose, {"a": "Alpha"})
    assert [c["id"] for c in classes] == ["well_low", "low", "near", "high", "well_high"]
    assert table["cls"].tolist() == ["well_high", "near", "low", "nodata"]
    assert table["name"].tolist() == ["Alpha", "b", "c", "d"]
    assert classes[0]["label"] == "runs 25% or more low"


def test_limits_sentence_names_the_worst_groups_without_figures(ctx, loose):
    s = settings_from_ledger(ctx.ledger)
    rule = rules_from_ledger(ctx.ledger)[0]
    pb = place_backtest(ctx.permits, rule, s, loose, state="TL", tracts=ctx.tracts)
    weak = weakest_groups(pb.groups, 2)
    assert len(weak) == 2
    assert weak["median_abs"].iloc[0] >= weak["median_abs"].iloc[1]
    rest = pb.groups[(pb.groups["dimension"] != "all")]
    assert weak["median_abs"].iloc[0] == rest["median_abs"].max()
    text = limits_sentence(weak)
    assert text is not None and not any(ch.isdigit() for ch in text)
    for phrase in weak["phrase"]:
        assert phrase in text


def test_limits_block_carries_the_sentence(monkeypatch):
    from visionhousing.baseline import places
    from visionhousing.engine import run
    from visionhousing.explain.payload import limits_block

    class Fake:
        limits = "Where the baseline itself has been least accurate in past years: here."

    monkeypatch.setattr(places, "place_backtest_for_context", lambda *a, **k: Fake())
    block = limits_block(run("TL", ["adu"], seed=0, draws=200))
    assert block["baseline"]["items"][-1]["text"] == Fake.limits
    assert block["reforms"]["adu"]["items"][-1]["text"] == Fake.limits
    assert all(i["text"] != Fake.limits for i in block["general"]["items"])


def test_validation_payload_has_the_breakdown(ctx):
    from visionhousing.api import service

    v = service.validation_table(ctx, TESTLAND)
    bp = v["by_place"]
    assert bp["rule"] == v["chosen_rule"]
    assert {c["county"] for c in bp["counties"]} == set(COUNTIES)
    assert bp["findings"] and bp["groups"][0]["dimension"] == "all"
    assert len(bp["map_classes"]) == 5
    _, skill = baseline_from_ledger(ctx.permits, ctx.ledger)
    chosen = skill[skill["chosen"]].iloc[0]
    # the overall row uses the same cases and ranges as the skill table
    assert bp["groups"][0]["cases"] == chosen["cases"]
    assert bp["groups"][0]["interval_cases"] == chosen["coverage_cases"]
    assert bp["groups"][0]["coverage"] == pytest.approx(chosen["coverage_80"])


# ---------------------------------------------------------------- county notes, far-off counties


def test_county_note_says_how_far_off_and_how_often_caught(loose):
    from visionhousing.baseline.places import county_note

    s = _scored([(0, 47, 100, 0.0), (0, 47, 100, 1.0), (1, 102, 100, 1.0)])
    table, _ = county_errors(s, ["a", "b", "c"], loose)
    assert county_note(table, "a", loose) == (
        "In past years, this tool's baseline for this county ran about 53% low; "
        "its ranges caught the real total in 1 of 2 years."
    )
    assert "about right on average" in county_note(table, "b", loose)
    assert county_note(table, "c", loose) is None  # no scored case
    assert county_note(table, "zz", loose) is None


def _named_table(pct, small=True, counties=1):
    row = {
        "dimension": "named",
        "group": "Big City",
        "phrase": "Big City",
        "counties": counties,
        "typical_pct": pct,
        "median_signed": math.log1p(pct),
        "median_abs": abs(math.log1p(pct)),
        "small": small,
    }
    return pd.DataFrame([row])


def test_a_far_off_named_county_is_named_even_with_few_cases(rules):
    from visionhousing.baseline.places import badly_off_named

    far = badly_off_named(_named_table(-0.53), rules)
    assert far["group"].tolist() == ["Big City"]
    text = limits_sentence(pd.DataFrame(columns=far.columns), far)
    assert "Big City (one county, but far off: it has tended to run too low)" in text
    assert not any(ch.isdigit() for ch in text)
    assert badly_off_named(_named_table(-0.10), rules).empty  # not far enough off
    assert badly_off_named(_named_table(-0.53, small=False), rules).empty  # already a group
    assert badly_off_named(_named_table(-0.53), {}).empty  # rule switched off


def test_testland_limits_name_harbor_county_when_far_off(ctx, rules):
    s = settings_from_ledger(ctx.ledger)
    rule = rules_from_ledger(ctx.ledger)[0]
    strict = {**rules, "name_few_cases_beyond": 0.0}
    pb = place_backtest(ctx.permits, rule, s, strict, state="TL", tracts=ctx.tracts)
    assert "Harbor County (one county, but far off" in pb.limits
