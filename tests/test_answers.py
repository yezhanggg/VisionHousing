"""The question box's resolver (``visionhousing.api.answers`` and ``web/js/lib/answers.js``).

What is checked:

- the browser's intent table (``web/js/content/answers.json``) is the YAML, unchanged, and the
  table holds words and templates only (no number);
- the gold questions (PA, VT, MA, WY, over the committed static files) get the expected intent
  and status; every number in an answer appears in the records it cites; every cited run record
  carries exactly the shown values and source ids of the run payload; every cited law record
  carries the payload's dates and sources;
- the browser resolver, run in Node over the same files, gives the same answers;
- Testland, live against static: the in-memory interface and its static export give the same
  answers (apart from the download address), ``POST /api/v1/answer`` gives the live answer,
  and a custom scenario is "not in this run" on the static site instead of a silent default.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from visionhousing.api import create_app
from visionhousing.api.answers import (
    SHARED_PATH,
    LiveReader,
    StaticReader,
    app_caller,
    load_table,
    parse,
    resolve,
    shared_table_text,
)
from visionhousing.export_static import export_static, read_saved

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "public" / "data"
NODE_SCRIPT = ROOT / "tests" / "js" / "answers_parity.mjs"
NUM = re.compile(r"\d+(?:[.,]\d+)*")

# (state on screen, reforms on screen, overrides, question, intent, allowed statuses).
# Statuses that depend on records a later export may fill (law rows, measured transit) allow
# both outcomes; the record checks below hold either way.
GOLD = [
    ("PA", [], {}, "Why does the Pennsylvania baseline look high?", "baseline", {"answered"}),
    ("PA", ["adu"], {}, "How many homes would ADUs add in Pennsylvania?", "result", {"answered"}),
    ("PA", ["adu"], {}, "Is the Pennsylvania ADU bill in place?", "law", {"answered", "unknown"}),
    (
        "PA",
        ["adu"],
        {},
        "Will this reach 5,000 additional homes within our budget?",
        "target",
        {"answered", "out_of_range"},
    ),
    ("PA", ["adu"], {}, "Will ADUs reach 7,500 homes?", "target", {"out_of_range"}),
    (
        "PA",
        ["adu"],
        {},
        "Will ADUs reach 5,000 homes with a $20 million budget?",
        "target",
        {"answered", "out_of_range"},
    ),
    (
        "PA",
        ["missing_middle"],
        {},
        "Will missing middle reach 20,000 homes?",
        "target",
        {"out_of_range"},
    ),
    ("PA", ["adu"], {}, "Can I build a sixplex at 123 Main Street?", "address", {"unsupported"}),
    ("PA", ["adu"], {}, "Can I share the exact number I'm seeing?", "share", {"answered"}),
    (
        "PA",
        ["adu"],
        {"adu.takeup_feasible_annual": "h"},
        "Can I share the exact number I'm seeing?",
        "share",
        {"unavailable"},
    ),
    ("PA", [], {}, "How many homes would all five reforms add?", "result", {"answered"}),
    ("PA", ["adu", "fees"], {}, "How many homes would this add?", "result", {"unavailable"}),
    ("PA", [], {}, "What will rents be in Pittsburgh?", "outside", {"unsupported"}),
    ("PA", ["parking"], {}, "What drives the parking result?", "drivers", {"answered", "unknown"}),
    ("VT", [], {}, "Is the Vermont ADU rule already in place?", "law", {"answered", "unknown"}),
    ("VT", [], {}, "What drives the ADU result in Vermont?", "drivers", {"answered", "unknown"}),
    ("VT", [], {}, "How many homes would all five reforms add in Vermont?", "result", {"answered"}),
    ("VT", [], {}, "Why does the Vermont baseline look low?", "baseline", {"answered"}),
    (
        "MA",
        [],
        {},
        "Is the Massachusetts ADU reform already in place?",
        "law",
        {"answered", "unknown"},
    ),
    (
        "MA",
        [],
        {},
        "How many homes would transit-oriented density add in Massachusetts?",
        "result",
        {"answered"},
    ),
    ("MA", ["tod"], {}, "Can I share the exact number I'm seeing?", "share", {"answered"}),
    (
        "MA",
        [],
        {},
        "Will ADUs in Massachusetts reach 1,000 homes?",
        "target",
        {"answered", "out_of_range"},
    ),
    (
        "WY",
        [],
        {},
        "How many homes will Wyoming TOD add?",
        ("coverage", "result"),
        {"unknown", "answered"},
    ),
    ("WY", ["tod"], {}, "What data are missing for Wyoming TOD?", "coverage", {"unknown"}),
    ("WY", [], {}, "Tell me a joke", "unsupported", {"unsupported"}),
    ("WY", [], {}, "Is the Wyoming parking reform law?", "law", {"answered", "unknown"}),
    ("WY", [], {}, "Why does the Wyoming baseline look the way it does?", "baseline", {"answered"}),
]

TL_GOLD = [
    (["adu"], "How many homes would ADUs add?"),
    ([], "Why does the baseline look high?"),
    (["adu"], "What drives the ADU result?"),
    (["adu"], "Will ADUs reach 250 homes?"),
    (["adu"], "Will this reach 99 homes within our budget?"),
    (["adu"], "Will ADUs reach 500 homes with a $20 million budget?"),
    ([], "What data are missing for transit?"),
    (["adu"], "Is the ADU rule already in place?"),
    (["adu"], "Can I share the exact number I'm seeing?"),
    ([], "How many homes would all five reforms add?"),
    (["fees"], "Can I build a fourplex at my address?"),
    ([], "What will prices do?"),
    ([], "Hello"),
]


def ctx(state: str, reforms: list[str], overrides: dict | None = None) -> dict:
    return {"state": state, "reforms": reforms, "overrides": overrides or {}, "route": "state"}


def tokens(text: str) -> set[str]:
    return {m.replace(",", "") for m in NUM.findall(text)}


def template_tokens() -> set[str]:
    """Numbers written in the templates' own words ("P10"), placeholders left out."""
    return tokens(re.sub(r"\{\w+\}", "", json.dumps(load_table()["templates"])))


def assert_grounded(answer: dict) -> None:
    """Every number in the answer's text is in its cited records (or its link / run id)."""
    allowed = tokens(json.dumps(answer["records"], ensure_ascii=False))
    allowed |= tokens(" ".join([answer["link"], answer["run_id"], answer["download"]]))
    allowed |= template_tokens()
    stray = tokens(answer["text"]) - allowed
    assert not stray, f"numbers not in the cited records: {sorted(stray)}\n{answer['text']}"


def payload_checks(answer: dict, data: Path) -> None:
    """Cited records equal the payloads they came from: shown values, sources, dates."""
    if not answer["run_id"]:
        return
    st, key = answer["run_id"].split("/")[:2]
    run = read_saved(data / st / "runs" / f"{key}.json")
    numbers = run["explanation"]["numbers"]
    sources = run["explanation"]["sources"]
    impl = (
        read_saved(data / st / "implementation.json")
        if (data / st / "implementation.json").is_file()
        else {}
    )
    by_record = {
        (r["legal"].get("record") or {}).get("record_id") or r["provision_id"]: r
        for r in impl.get("rows") or []
    }
    for rec in answer["records"]:
        if rec["id"].startswith("run:") and rec["id"][4:] in numbers:
            want = numbers[rec["id"][4:]]
            assert rec["shown"] == want["shown"], rec["id"]
            assert rec["source_ids"] == (want.get("source_ids") or []), rec["id"]
            assert rec["p10"] == want["p10"] and rec["p90"] == want["p90"], rec["id"]
            if answer["intent"] in ("result", "share", "baseline"):
                assert (
                    want["shown"]["p10"] in answer["text"]
                    and want["shown"]["p90"] in answer["text"]
                )
        if rec["kind"] == "law" and rec["id"][4:] in by_record:
            row = by_record[rec["id"][4:]]
            record = row["legal"]["record"]
            assert rec["start"] == record.get("start") and rec["verified_on"] == record.get(
                "verified_on"
            )
            assert rec["urls"] == record.get("sources")
            assert rec["shown"] == row["line"] and rec["shown"] in answer["text"]
    for src in answer["sources"]:
        if src["id"] in sources:
            assert src["citation"] == sources[src["id"]]["citation"]
            assert src["url"] == (sources[src["id"]].get("url") or "")


def run_node(data: Path, cases: list[tuple[str, dict]]) -> list[dict]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    payload = {
        "data": str(data),
        "table": str(SHARED_PATH),
        "cases": [{"question": q, "context": c} for q, c in cases],
    }
    done = subprocess.run(  # noqa: S603 (fixed command, no shell)
        [node, str(NODE_SCRIPT)], input=json.dumps(payload), capture_output=True, text=True,
        check=True, timeout=300, cwd=ROOT,
    )  # fmt: skip
    return json.loads(done.stdout)


def without_download(answer: dict) -> dict:
    return {k: v for k, v in answer.items() if k != "download"}


# ------------------------------------------------------------------ the table


def test_browser_table_is_the_yaml_unchanged():
    assert SHARED_PATH.read_text(encoding="utf-8") == shared_table_text(), (
        "run: uv run python -m visionhousing.api.answers"
    )


def test_table_holds_words_and_templates_only():
    table = load_table()
    for intent in table["intents"]:
        for word in intent.get("words") or []:
            assert not re.search(r"\d", word), word
    for name, text in table["templates"].items():
        if isinstance(text, str):
            words = re.sub(r"\{\w+\}", "", text).replace("P10", "")
            assert not re.search(r"\d", words), name


@pytest.mark.parametrize(
    ("question", "intent", "state", "reforms"),
    [
        ("Why does the Pennsylvania baseline look high?", "baseline", "PA", []),
        ("Is the Massachusetts ADU reform already in place?", "law", "MA", ["adu"]),
        ("How many homes will Wyoming TOD add?", "result", "WY", ["tod"]),
        ("Will this reach 5,000 additional homes within our budget?", "target", "", []),
        ("Can I build a sixplex at this address?", "address", "", ["missing_middle"]),
        ("Can I share the exact number I'm seeing?", "share", "", []),
        ("How many homes would missing middle add?", "result", "", ["missing_middle"]),
        ("What will rents be?", "outside", "", []),
        ("Tell me a joke", "unsupported", "", []),
    ],
)
def test_questions_match_one_fixed_intent(question, intent, state, reforms):
    q = parse(question, load_table())
    assert (q["intent"], q["state"], q["reforms"]) == (intent, state, reforms)


# ------------------------------------------------------------------ gold questions


@pytest.fixture(scope="module")
def gold_answers():
    if not (DATA / "PA" / "index.json").is_file():
        pytest.skip("no static export in web/public/data")
    reader = StaticReader.from_folder(DATA)
    return [resolve(q, ctx(st, r, o), reader) for st, r, o, q, _i, _s in GOLD]


def test_gold_set_spans_the_four_states_and_cases():
    assert len(GOLD) >= 20
    assert {g[0] for g in GOLD} == {"PA", "VT", "MA", "WY"}
    intents = {i for g in GOLD for i in ([g[4]] if isinstance(g[4], str) else g[4])}
    assert {
        "baseline",
        "result",
        "law",
        "target",
        "coverage",
        "address",
        "share",
        "unsupported",
    } <= intents


@pytest.mark.parametrize("n", range(len(GOLD)), ids=[g[3][:40] for g in GOLD])
def test_gold_question(gold_answers, n):
    st, _reforms, _overrides, _question, intent, statuses = GOLD[n]
    answer = gold_answers[n]
    want = (intent,) if isinstance(intent, str) else intent
    assert answer["intent"] in want, answer["text"]
    assert answer["status"] in statuses, answer["text"]
    assert answer["state"] == st
    assert answer["text"] and answer["title"]
    assert_grounded(answer)
    payload_checks(answer, DATA)
    if answer["status"] == "answered" and answer["intent"] in (
        "result",
        "baseline",
        "share",
        "drivers",
    ):
        assert answer["run_id"].startswith(f"{st}/") and answer["period"] == "2027-2031"
        assert answer["records"]


def test_gold_specifics(gold_answers):
    by_q = {g[3]: a for g, a in zip(GOLD, gold_answers, strict=True)}
    # A custom scenario on the static site: its own status, never the default run's number.
    custom = next(a for g, a in zip(GOLD, gold_answers, strict=True) if g[2])
    assert custom["records"] == [] and "changed assumptions" in custom["text"]
    # A target that is not a precomputed cell is out of range, and says which cells exist.
    assert "precomputed targets" in by_q["Will ADUs reach 7,500 homes?"]["text"]
    # Budget: the committed export holds no budget grid yet, and the answer says so (with the
    # grid exported, the answer reads its cells: tests/test_budget_target.py).
    for q in (
        "Will this reach 5,000 additional homes within our budget?",
        "Will ADUs reach 5,000 homes with a $20 million budget?",
    ):
        a = by_q[q]
        assert "budget-and-target grid" in a["text"], q
        if "budget-and-target grid is not in this build" not in a["text"]:
            assert any(r["kind"] == "budget-grid" for r in a["records"]), q
    # Address: no parcel decision.
    assert (
        "no decision about what may be built"
        in by_q["Can I build a sixplex at 123 Main Street?"]["text"]
    )
    # Unsupported questions offer what the box can answer.
    assert by_q["Tell me a joke"]["offers"]
    # The PA baseline names 2021, the year that lifts its window.
    assert "2021:" in by_q["Why does the Pennsylvania baseline look high?"]["text"]


def test_browser_resolver_matches_python_on_the_gold_set(gold_answers):
    js = run_node(DATA, [(q, ctx(st, r, o)) for st, r, o, q, _i, _s in GOLD])
    for g, py, browser in zip(GOLD, gold_answers, js, strict=True):
        assert browser == py, g[3]


# ------------------------------------------------------------------ Testland: live and static


@pytest.fixture(scope="module")
def testland(tmp_path_factory):
    """The in-memory interface and a static export of Testland made from it."""
    from visionhousing.api.budget_grid import grid_payload
    from visionhousing.api.budget_view import write_static
    from visionhousing.engine.context import load_context

    folder = tmp_path_factory.mktemp("tl-data")
    export_static("TL", folder, packs_dir=tmp_path_factory.mktemp("tl-packs"))
    # The budget grid, as the static exporter writes it (the live app works out the same one).
    write_static(grid_payload(load_context("TL")), folder / "TL")
    app = create_app(web_dir=folder / ".no-web")
    call = app_caller(app)
    return folder, call


def test_testland_live_and_static_answers_are_the_same(testland):
    folder, call = testland
    live, static = LiveReader(call), StaticReader.from_folder(folder)
    for reforms, question in TL_GOLD:
        a = resolve(question, ctx("TL", reforms), live)
        b = resolve(question, ctx("TL", reforms), static)
        assert without_download(a) == without_download(b), question
        assert_grounded(a)
        payload_checks(b, folder)


def test_testland_browser_resolver_matches_python(testland):
    folder, _call = testland
    static = StaticReader.from_folder(folder)
    cases = [(q, ctx("TL", r)) for r, q in TL_GOLD]
    js = run_node(folder, cases)
    for (q, c), browser in zip(cases, js, strict=True):
        assert browser == resolve(q, c, static), q


def test_answer_address_gives_the_live_answer(testland):
    _folder, call = testland
    body = {"question": "How many homes would ADUs add?", "context": ctx("TL", ["adu"])}
    status, raw = call("POST", "/api/v1/answer", body)
    assert status == 200
    assert json.loads(raw) == resolve(body["question"], body["context"], LiveReader(call))


@pytest.mark.parametrize(
    "body",
    [
        {"question": "", "context": {"state": "TL"}},
        {"question": "x" * 501, "context": {"state": "TL"}},
        {"question": "Why?", "context": {"state": "Testland"}},
        {"question": "Why?", "context": {"state": "TL", "reforms": ["../etc"]}},
        {"question": "Why?", "context": []},
    ],
)
def test_answer_address_refuses_bad_input(testland, body):
    _folder, call = testland
    status, raw = call("POST", "/api/v1/answer", body)
    assert status == 400
    assert json.loads(raw)["error"] == "invalid_request"


def test_custom_scenario_is_not_replaced_by_the_default(testland):
    folder, call = testland
    c = ctx("TL", ["adu"], {"adu.takeup_feasible_annual": "h"})
    q = "How many homes would ADUs add?"
    static = resolve(q, c, StaticReader.from_folder(folder))
    assert static["status"] == "unavailable" and static["records"] == []
    live = resolve(q, c, LiveReader(call))
    default = resolve(q, ctx("TL", ["adu"]), LiveReader(call))
    assert live["status"] == "answered" and "a=adu.takeup_feasible_annual~h" in live["link"]
    assert live["records"][0]["shown"] != default["records"][0]["shown"]
