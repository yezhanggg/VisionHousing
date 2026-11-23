"""Assumption Ledger: loads, checks, names the bad parameter, samples, hashes, overrides."""

import copy
from pathlib import Path

import numpy as np
import pytest
import yaml

from visionhousing.ledger import (
    Ledger,
    LedgerError,
    apply_overrides,
    draw,
    load_ledger,
    parse_ledger,
    percentiles,
    sample,
    stream,
)
from visionhousing.ledger.loader import DEFAULT_PATH, read_yaml

GOOD = {
    "label": "A test share",
    "unit": "share",
    "central": 0.3,
    "low": 0.1,
    "high": 0.6,
    "shape": "pert",
    "kind": "judgement",
    "source": [],
    "rationale": "For tests.",
    "used_by": ["adu"],
    "revisit": "Never.",
    "status": "draft",
}


def tiny(**params) -> dict:
    return {"version": 1, "parameters": params or {"adu.test_share": dict(GOOD)}}


@pytest.fixture(scope="module")
def ledger() -> Ledger:
    return load_ledger()


# ------------------------------------------------------------------ load and validate


def test_real_ledger_loads(ledger):
    assert len(ledger) >= 60
    assert "fees.fee_total_per_unit" in ledger


def test_benchmarks_are_fixed_and_exact(ledger):
    expected = {
        "fees.fee_total_per_unit": 50000,
        "fees.fee_cut_share": 0.5,
        "adu.min_size_sqft": 600,
        "adu.added_parking_spaces": 0,
        "adu.units_per_lot": 1,
        "baseline.horizon_years": 5,
        "missing_middle.min_units": 2,
        "missing_middle.max_units": 6,
        "missing_middle.max_storeys": 3,
    }
    for pid, value in expected.items():
        p = ledger[pid]
        assert (p.kind, p.shape, p.status) == ("benchmark", "fixed", "sourced"), pid
        assert p.low == p.central == p.high == value, pid
    assert {pid for pid, p in ledger.items() if p.kind == "benchmark"} == set(expected)


def test_every_parameter_has_used_by(ledger):
    for pid, p in ledger.items():
        assert p.used_by, pid


def test_every_non_draft_parameter_has_a_source_with_url(ledger):
    for pid, p in ledger.items():
        if p.status != "draft":
            assert any(s.url and s.url.startswith("https://") and s.accessed for s in p.source), pid


def test_every_draft_says_what_to_revisit(ledger):
    for pid, p in ledger.items():
        if p.status == "draft":
            assert p.revisit.strip(), pid


def test_every_reform_in_scope_has_parameters(ledger):
    for user in ("baseline", "adu", "fees", "parking", "missing_middle"):
        assert ledger.used_by(user), user


def test_per_capita_evidence_is_two_facts_not_a_range(ledger):
    a = ledger["adu.evidence_permits_per_100k_permissive_all"]
    b = ledger["adu.evidence_permits_per_100k_permissive_outside_ca"]
    assert (a.central, b.central) == (81, 26)
    assert a.shape == b.shape == "fixed"


def test_tiny_file_parses():
    lg = parse_ledger(tiny())
    assert lg["adu.test_share"].central == 0.3


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"low": 0.9}, "low <= central <= high"),
        ({"shape": "normal"}, "shape"),
        ({"kind": "guess"}, "kind"),
        ({"used_by": []}, "used_by"),
        ({"used_by": ["adus"]}, "unknown names"),
        ({"centre": 0.3}, "centre"),
        ({"shape": "fixed"}, "fixed parameter"),
        ({"status": "sourced"}, "needs at least one source"),
        ({"revisit": ""}, "revisit"),
        ({"central": True}, "not true or false"),
        ({"source": [{"id": "x", "citation": "y", "url": "ftp://z"}]}, "url"),
    ],
)
def test_bad_entry_message_names_the_parameter(change, fragment):
    entry = {**GOOD, **change}
    with pytest.raises(LedgerError) as exc:
        parse_ledger(tiny(**{"adu.broken_one": entry}))
    message = str(exc.value)
    assert "parameter 'adu.broken_one'" in message
    assert fragment in message


def test_benchmark_must_be_fixed():
    entry = {**GOOD, "kind": "benchmark"}
    with pytest.raises(LedgerError, match=r"parameter 'adu.bench'.*benchmark"):
        parse_ledger(tiny(**{"adu.bench": entry}))


def test_every_bad_parameter_is_named_at_once():
    bad = {**GOOD, "low": 2.0}
    with pytest.raises(LedgerError) as exc:
        parse_ledger(tiny(**{"adu.first": bad, "adu.second": bad, "adu.fine": dict(GOOD)}))
    assert "adu.first" in str(exc.value) and "adu.second" in str(exc.value)
    assert "adu.fine" not in str(exc.value)


def test_bad_id_and_bad_top_level():
    with pytest.raises(LedgerError, match="parameter 'NoDots'"):
        parse_ledger(tiny(NoDots=dict(GOOD)))
    with pytest.raises(LedgerError, match="'parameters' mapping"):
        parse_ledger({"version": 1})
    with pytest.raises(LedgerError, match="unknown top-level"):
        parse_ledger({**tiny(), "extra": 1})


def test_bad_file_on_disk(tmp_path: Path):
    path = tmp_path / "parameters.yaml"
    data = tiny()
    data["parameters"]["adu.test_share"]["high"] = 0.2
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(LedgerError, match=r"parameters.yaml: parameter 'adu.test_share'"):
        load_ledger(path)


def test_duplicate_key_and_broken_yaml_fail(tmp_path: Path):
    dup = tmp_path / "dup.yaml"
    dup.write_text("parameters:\n  adu.a: {central: 1}\n  adu.a: {central: 2}\n", encoding="utf-8")
    with pytest.raises(LedgerError, match=r"'adu\.a' appears twice"):
        load_ledger(dup)
    broken = tmp_path / "broken.yaml"
    broken.write_text("parameters: [unclosed\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="not valid YAML"):
        load_ledger(broken)
    with pytest.raises(LedgerError, match="cannot read"):
        load_ledger(tmp_path / "missing.yaml")


# ------------------------------------------------------------------ sampling


def test_same_seed_same_draws(ledger):
    a = sample(ledger, n=500, seed=42)
    b = sample(ledger, n=500, seed=42)
    assert a.keys() == b.keys() == set(ledger)
    for pid in a:
        assert np.array_equal(a[pid], b[pid]), pid


def test_different_seed_different_draws(ledger):
    pid = "adu.takeup_feasible_annual"
    a = sample(ledger, n=200, seed=1, ids=[pid])[pid]
    b = sample(ledger, n=200, seed=2, ids=[pid])[pid]
    assert not np.array_equal(a, b)


def test_draws_do_not_depend_on_other_parameters(ledger):
    pid = "adu.hard_cost_per_sqft"
    alone = sample(ledger, n=300, seed=7, ids=[pid])[pid]
    together = sample(ledger, n=300, seed=7)[pid]
    assert np.array_equal(alone, together)


def test_draws_stay_in_range_and_quantiles_are_ordered(ledger):
    draws = sample(ledger, n=2000, seed=3)
    for pid, values in draws.items():
        p = ledger[pid]
        assert values.shape == (2000,)
        assert values.min() >= p.low and values.max() <= p.high, pid
        p10, p50, p90 = percentiles(values)
        assert p10 <= p50 <= p90, pid
        if p.shape != "fixed":
            assert p10 < p90, pid


def test_pert_mean_matches_formula():
    lg = parse_ledger(tiny())
    p = lg["adu.test_share"]
    values = draw(p, 200_000, stream(11, "adu.test_share"))
    expected = (p.low + 4 * p.central + p.high) / 6
    assert abs(values.mean() - expected) < 0.002


def test_triangular_mean_matches_formula():
    lg = parse_ledger(tiny(**{"adu.tri": {**GOOD, "shape": "triangular"}}))
    p = lg["adu.tri"]
    values = draw(p, 200_000, stream(11, "adu.tri"))
    assert abs(values.mean() - (p.low + p.central + p.high) / 3) < 0.002


def test_fixed_draws_are_constant(ledger):
    values = sample(ledger, n=10, seed=0, ids=["fees.fee_total_per_unit"])
    assert np.all(values["fees.fee_total_per_unit"] == 50000)


def test_bad_sample_arguments(ledger):
    with pytest.raises(ValueError, match="seed"):
        stream(-1, "x.y")
    with pytest.raises(ValueError, match="n must"):
        draw(ledger["adu.loan_share"], 0, stream(0, "adu.loan_share"))


# ------------------------------------------------------------------ hash


def test_hash_is_stable_and_order_free():
    data = tiny(**{"adu.one": dict(GOOD), "adu.two": {**GOOD, "central": 0.4}})
    flipped = {"version": 1, "parameters": dict(reversed(list(data["parameters"].items())))}
    h1 = parse_ledger(data).hash()
    assert h1 == parse_ledger(copy.deepcopy(data)).hash() == parse_ledger(flipped).hash()
    assert len(h1) == 64 and parse_ledger(data).short_hash() == h1[:12]


def test_hash_changes_with_any_value():
    data = tiny()
    base = parse_ledger(data).hash()
    changed = copy.deepcopy(data)
    changed["parameters"]["adu.test_share"]["high"] = 0.61
    assert parse_ledger(changed).hash() != base


def test_real_ledger_hash_is_reproducible(ledger):
    assert load_ledger().hash() == ledger.hash()
    assert parse_ledger(read_yaml(DEFAULT_PATH)).hash() == ledger.hash()


# ------------------------------------------------------------------ overrides


def test_override_number_pins_value(ledger):
    new = apply_overrides(ledger, {"adu.takeup_feasible_annual": 0.02})
    p = new["adu.takeup_feasible_annual"]
    assert (p.low, p.central, p.high, p.shape) == (0.02, 0.02, 0.02, "fixed")
    assert ledger["adu.takeup_feasible_annual"].shape == "pert"  # original untouched
    assert new.hash() != ledger.hash()


def test_override_low_high_words(ledger):
    new = apply_overrides(ledger, {"adu.loan_share": "high", "adu.soft_cost_share": "low"})
    assert new["adu.loan_share"].central == ledger["adu.loan_share"].high
    assert new["adu.soft_cost_share"].central == ledger["adu.soft_cost_share"].low


def test_override_mapping_merges_fields(ledger):
    new = apply_overrides(ledger, {"adu.loan_share": {"low": 0.6, "high": 0.9}})
    p, old = new["adu.loan_share"], ledger["adu.loan_share"]
    assert (p.low, p.high, p.central, p.label) == (0.6, 0.9, old.central, old.label)
    others = [pid for pid in ledger if pid != "adu.loan_share"]
    assert all(new[pid] == ledger[pid] for pid in others)


def test_no_overrides_returns_same_ledger(ledger):
    assert apply_overrides(ledger, None) is ledger
    assert apply_overrides(ledger, {}).hash() == ledger.hash()


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"adu.not_real": 1}, "'adu.not_real': not in the ledger"),
        ({"adu.loan_share": "max"}, "'adu.loan_share'"),
        ({"adu.loan_share": True}, "'adu.loan_share'"),
        ({"adu.loan_share": {"low": 0.95}}, "'adu.loan_share'"),
        ({"adu.loan_share": [1, 2]}, "'adu.loan_share'"),
    ],
)
def test_bad_override_names_parameter(ledger, overrides, fragment):
    with pytest.raises(LedgerError, match=fragment.replace(".", r"\.")):
        apply_overrides(ledger, overrides)


def test_unknown_parameter_lookup_names_it(ledger):
    with pytest.raises(LedgerError, match=r"'adu\.nope'"):
        ledger["adu.nope"]
