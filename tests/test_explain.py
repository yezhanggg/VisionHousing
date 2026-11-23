"""Explanations: golden text on Testland, the must-cite check, verdicts, funnels, slides."""

import json
import math
from pathlib import Path

import pytest

from visionhousing.engine import run
from visionhousing.engine.records import Record
from visionhousing.explain import (
    VERDICT_WORDS,
    LimitsError,
    cited_texts,
    explain,
    load_limits,
    num,
    numbers_in,
    record_index,
    resolve,
    round_sig,
    show,
    uncited,
    verdict_word,
)
from visionhousing.explain import wording as w
from visionhousing.explain.numbers import count, normalise
from visionhousing.sensitivity import drivers

REFORMS = ["adu", "fees", "parking"]
CHARTS = {"interval", "funnel", "tornado", "waterfall"}


@pytest.fixture(scope="module")
def result():
    return run("TL", REFORMS, seed=0)


@pytest.fixture(scope="module")
def explained(result):
    return explain(result)


# ---------------------------------------------------------------- an independent resolver


def _driver_rows(result, key):
    d = drivers(result, key)
    rows = {str(r.parameter): r._asdict() for r in d.tornado.itertuples(index=False)}
    return [{**rows[x.parameter], **vars(x)} for x in d.top]


def make_resolver(result, explanation):
    """Look a cite up in the engine result itself (not in the explanation)."""
    data = result.to_dict()
    data["draws"] = result.draws
    data["seed"] = result.seed
    cache = {}

    def get(ref, field):
        if ref.startswith("ledger:"):
            owner, pid = ref[len("ledger:") :].split("/", 1)
            led = result.draws_obj.ledger if not owner else result.reforms[owner].result.ledger
            return getattr(led[pid], field)
        if ref.startswith("drivers."):
            _, key, i = ref.split(".")
            if key not in cache:
                cache[key] = _driver_rows(result, key)
            return cache[key][int(i)][field]
        if ref.startswith("county:"):
            geoid = ref.split(":", 1)[1]
            for f in result.run_context.state.boundaries["features"]:
                p = f["properties"]
                if p.get("level") == "county" and p.get("geoid") == geoid:
                    return p[field]
            raise KeyError(ref)
        if ref.startswith("source:"):
            return explanation["sources"][ref.split(":", 1)[1]][field]
        node = resolve(data, ref)
        return node if field == "" else node[field]

    return get


def consistent(token, value):
    """True when a written number is the engine value rounded the way the text layer rounds."""
    for v in (abs(value), 100.0 * abs(value)):
        for sig in range(2, 7):
            if token in (normalise(num(v, sig)), normalise(count(v, sig))):
                return True
        if token == normalise(str(round(v))):
            return True
    return False


def check_cites(texts, get):
    """The must-cite check: every number written is cited, and every cite is in the result."""
    checked = 0
    for t in texts:
        assert uncited(t["text"], t["cites"]) == [], t["text"]
        for c in t["cites"]:
            found = get(c["ref"], c["field"])
            if isinstance(c["value"], str):
                assert found == c["value"], c
                assert set(numbers_in(c["shown"])) <= set(numbers_in(found)), c
            else:
                assert math.isclose(float(found), c["value"], rel_tol=0, abs_tol=0), c
                tokens = numbers_in(c["shown"])
                assert len(tokens) == 1, c
                assert consistent(tokens[0], c["value"]), c
            checked += 1
    return checked


# ---------------------------------------------------------------- numbers


def test_rounding_and_units():
    assert round_sig(8010.5) == 8000.0
    assert num(8010.5) == "8,000"
    assert num(6.203) == "6.2"
    assert num(0.3333) == "0.33"
    assert num(99.6) == "100"
    assert num(25490) == "25,000"
    assert num(0) == "0"
    assert num(-1234, 3) == "\u22121,230"
    assert show(0.0987, "share") == "9.9%"
    assert show(0.01, "share per year") == "1% a year"
    assert show(6.2, "% of baseline") == "6.2%"
    assert show(25000, "$/unit") == "$25,000/unit"
    assert show(1.05, "ratio", 3) == "\u00d71.05"
    assert show(2030, "year") == "2030"
    assert count(34.22) == "34"
    assert count(0.587) == "0.59"
    assert count(41903.9) == "41,900"


def test_numbers_in_skips_words_and_hashes():
    text = "P10 of B25031 is 1,234.50 in 2027-2031; hash 4f2f5804b2d and 9.9% ok"
    assert numbers_in(text) == ["1234.5", "2027", "2031", "9.9"]


def test_uncited_catches_a_typed_number():
    cites = [{"shown": "8,000"}]
    assert uncited("about 8,000 homes", cites) == []
    assert uncited("about 8,000 homes, or 12% more", cites) == ["12"]


# ---------------------------------------------------------------- golden text on Testland


def test_golden_sentence_on_testland(explained):
    assert explained["headline"]["sentence"]["text"] == (
        "Over 2027\u20132031, Testland would likely permit between 190 and 610 more homes "
        "(5.7\u201315%) with accessory dwelling units (ADUs) allowed, development fees cut "
        "in half and parking minimums removed, on top of a baseline of 1,700\u20135,400 homes "
        "expected under today's rules."
    )


def test_golden_verdicts_on_testland(explained):
    words = {k: v["word"] for k, v in explained["verdicts"].items()}
    assert words == {"adu": "Adds some", "fees": "Adds a lot", "parking": "Adds some"}
    assert explained["verdicts"]["adu"]["why"]["text"] == (
        "Middle estimate: 2.1% more homes than the baseline. “Adds some” means 1% to under 5%."
    )


def test_three_numbers_are_the_engine_ranges(result, explained):
    numbers = {n["key"]: n for n in explained["headline"]["numbers"]}
    assert list(numbers) == ["baseline", "added", "pct_change"]
    assert numbers["baseline"]["p50"] == result.baseline.p50
    assert numbers["added"]["p10"] == result.package.combined.p10
    assert numbers["pct_change"]["p90"] == result.package.pct_change.p90


def test_baseline_only_sentence():
    e = explain(run("TL", [], seed=0))
    assert e["headline"]["sentence"]["text"].startswith("Over 2027\u20132031, Testland would")
    assert "under today's rules." in e["headline"]["sentence"]["text"]
    assert e["verdicts"] == {} and e["waterfall"] is None


# ---------------------------------------------------------------- the must-cite check


def test_every_number_in_every_text_is_in_the_result(result, explained):
    texts = list(cited_texts(explained))
    assert len(texts) > 50
    assert check_cites(texts, make_resolver(result, explained)) > 100


def test_every_slide_number_is_in_the_result(result, explained):
    get = make_resolver(result, explained)
    check_cites(list(cited_texts(explained["slides"])), get)
    for s in explained["slides"]:
        items = [] if s["chart"] is None else s["chart"]["items"]
        if s["big"]:
            items = [*items, s["big"]]
        for item in items:
            for f in ("p10", "p50", "p90", "value", "total_low", "total_high"):
                if item.get(f) is not None:
                    assert get(item["ref"], f) == item[f], (s["kind"], item["ref"], f)


def test_the_check_fails_on_a_hand_typed_number(result, explained):
    sentence = dict(explained["headline"]["sentence"])
    sentence["text"] = sentence["text"].replace("190", "200")
    with pytest.raises(AssertionError):
        check_cites([sentence], make_resolver(result, explained))
    bad = json.loads(json.dumps(explained["headline"]["sentence"]))
    bad["cites"][1]["value"] += 1.0
    with pytest.raises(AssertionError):
        check_cites([bad], make_resolver(result, explained))


# ---------------------------------------------------------------- verdicts


def test_verdict_word_thresholds():
    assert verdict_word(6.0, 5, 1) == "Adds a lot"
    assert verdict_word(5.0, 5, 1) == "Adds a lot"
    assert verdict_word(4.99, 5, 1) == "Adds some"
    assert verdict_word(1.0, 5, 1) == "Adds some"
    assert verdict_word(0.5, 5, 1) == "Little effect"
    assert verdict_word(8.0, 5, 1, overlap_share=0.6, overlap_limit=0.5) == "Overlaps"
    assert verdict_word(8.0, 5, 1, overlap_share=0.4, overlap_limit=0.5) == "Adds a lot"
    assert set(VERDICT_WORDS) == {"Adds a lot", "Adds some", "Little effect", "Overlaps"}


def test_verdict_thresholds_live_in_the_ledger(result):
    led = result.draws_obj.ledger
    for pid in ("explain.verdict_a_lot_pct", "explain.verdict_some_pct"):
        assert led[pid].kind == "judgement" and led[pid].shape == "fixed"
    moved = explain(
        run("TL", REFORMS, overrides={"explain.verdict_a_lot_pct": 2.0}, seed=0),
        with_drivers=False,
        numbers=False,
    )
    assert moved["verdicts"]["adu"]["word"] == "Adds a lot"


# ---------------------------------------------------------------- funnels, drivers, waterfall


def test_funnels_follow_the_engine_steps(result, explained):
    for k, o in result.reforms.items():
        f = explained["funnels"][k]
        extra = 1 if f["central_only"] else 0
        assert len(f["steps"]) == len(o.steps) + extra
        for step, rec in zip(f["steps"], o.steps, strict=False):
            assert step["formula"] == rec.formula
            assert step["meaning"]["text"] != w.step_meaning("unknown step")
        if f["central_only"]:
            assert f["steps"][-1]["ref"] == f"reform_results.{k}.added"
            assert f["steps"][-1]["p50"] == o.added.p50


def test_every_known_step_has_a_meaning_without_figures():
    for prefix, meaning in w.STEP_MEANINGS:
        assert not any(ch.isdigit() for ch in prefix + meaning), prefix
    r = run("TL", ["missing_middle", "pa_hb2186"], seed=0)
    for o in r.reforms.values():
        for s in o.steps:
            assert w.step_meaning(s.name) != w.step_meaning("unknown step"), s.name


def test_drivers_are_the_sensitivity_top_five(result, explained):
    for k in REFORMS:
        top = drivers(result, k).top
        items = explained["drivers"][k]["items"]
        assert [i["parameter"] for i in items] == [d.parameter for d in top]
        assert [i["total_high"] for i in items] == [d.total_high for d in top]


def test_waterfall_steps_are_the_package_records(result, explained):
    wf = explained["waterfall"]
    assert [s["kind"] for s in wf["steps"]] == ["start", "minus", "plus", "total"]
    assert wf["steps"][-1]["p50"] == result.package.combined.p50
    assert [p["ref"] for p in wf["parts"]] == [f"reform_results.{k}.added" for k in REFORMS]


# ---------------------------------------------------------------- explain this number


def test_every_record_has_an_explain_payload(result, explained):
    index = record_index(result)
    assert set(explained["numbers"]) == set(index)
    held = {id(r) for r in index.values()}
    assert all(id(r) in held for r in result.records())
    data = result.to_dict()
    for ref, rec in index.items():
        n = explained["numbers"][ref]
        assert Record.model_validate(resolve(data, ref)) == rec
        assert n["formula"] == rec.formula and n["tier"] == rec.tier
        assert [a["id"] for a in n["assumptions"]] == list(rec.assumption_ids)
        for a in n["assumptions"]:
            assert a["label"]
        if rec.is_range:
            assert (n["p10"], n["p50"], n["p90"]) == (rec.p10, rec.p50, rec.p90)
        assert n["tier_label"]


def test_assumption_labels_come_from_the_ledger(result, explained):
    led = result.reforms["adu"].result.ledger
    n = explained["numbers"]["reform_results.adu.added"]
    for a in n["assumptions"]:
        if a["id"] in led:
            assert a["label"] == led[a["id"]].label


# ---------------------------------------------------------------- slides


def test_slides_spec_shape(result, explained):
    slides = explained["slides"]
    kinds = [s["kind"] for s in slides]
    assert kinds[0] == "summary" and kinds[-1] == "limits"
    assert kinds.count("reform") == len(REFORMS) and "package" in kinds and "compare" in kinds
    assert [s["n"] for s in slides] == list(range(1, len(slides) + 1))
    for s in slides:
        assert s["title"]["text"] and s["big"] is not None
        assert s["chart"] is None or s["chart"]["type"] in CHARTS
        assert s["stamp"]["code"] == result.hashes.code
        assert s["stamp"]["data"] == result.hashes.data
        assert s["stamp"]["assumptions"] == result.hashes.assumptions
        assert isinstance(s["sources"], list)
    assert all(s["chart"] is not None for s in slides[:-1])
    assert any(src["id"] == "bps_county_annual" for src in slides[0]["sources"])


# ---------------------------------------------------------------- limits


def test_limits_file_loads_and_extends():
    lim = load_limits()
    for key in ("general", "baseline", "package", "adu", "fees", "parking", "missing_middle"):
        assert lim[key].items, key
    assert lim["pa_hb2186"].items[: len(lim["adu"].items)] == lim["adu"].items


def test_limits_file_refuses_figures(tmp_path: Path):
    bad = tmp_path / "limits.yaml"
    bad.write_text("limits:\n  adu:\n    title: ADUs\n    items: ['Take-up of 1% a year']\n")
    with pytest.raises(LimitsError, match="no figures"):
        load_limits(bad)
    loop = tmp_path / "loop.yaml"
    loop.write_text(
        "limits:\n"
        "  a: {title: A, items: [x], extends: b}\n"
        "  b: {title: B, items: [y], extends: a}\n"
    )
    with pytest.raises(LimitsError, match="circle"):
        load_limits(loop)


# ---------------------------------------------------------------- determinism and words


def test_explanation_is_deterministic(result):
    again = run("TL", REFORMS, seed=0, use_cache=False)
    a = json.dumps(explain(result), allow_nan=False)
    b = json.dumps(explain(again), allow_nan=False)
    assert a == b


def test_wording_helpers():
    assert w.state_name("pa") == "Pennsylvania"
    assert w.join_words(["a", "b", "c"]) == "a, b and c"
    assert w.reform_phrase("pa_hb2186", "Pennsylvania HB 2186: one ADU") == (
        "Pennsylvania HB 2186 passed"
    )
    assert w.step_label("eligible lots (adu: sf_detached)") == "eligible lots"
    assert w.step_scope("eligible lots (adu: sf_detached)") == "adu: sf_detached"
