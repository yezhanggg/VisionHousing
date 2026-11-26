"""The transit status view (api.status_view) and where it is read.

- a run whose TOD status hides the statewide range gets a view that says so: the completeness
  label, the measured contribution (the explanation's own shown middle), the package note when
  the run holds the package, and the ladder for the state and every county, with "unknown"
  (not measured) and "measured: none" as different kinds;
- a partial footprint that is still shown gets the label beside its range and no package note;
- a reform with no status gets no view;
- the hearing brief shows the status text and no range for a hidden reform, and the package
  note for a package;
- the question box (Python and the browser's resolver in Node, over the same saved files)
  answers a hidden reform's result question with exactly the status text.

Every reply here is written by hand (no state pack needed).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from visionhousing.api.answers import SHARED_PATH, StaticReader, resolve
from visionhousing.api.status_view import KINDS, status_views, view_of
from visionhousing.report.hearing import hearing_doc

ROOT = Path(__file__).resolve().parents[1]
NODE_SCRIPT = ROOT / "tests" / "js" / "answers_parity.mjs"
STAGES = ["discovered", "legal", "coverage", "headway", "headway_peak", "feasible"]
LABEL = "partial footprint: 135 of 349 bus stops (38.6%) from 2 of 7 agencies have measured service"


def ladder(geoid: str, level: str, measured: int, bus: int, **stages: str) -> dict:
    row = {s: "yes" for s in STAGES}
    row.update(stages)
    done = measured >= bus
    return {
        "level": level,
        "geoid": geoid,
        "bus_stops": bus,
        "bus_stops_measured": measured,
        "completeness": "complete: every stop's service measured" if done else LABEL,
        **row,
    }


def status(shown: bool) -> dict:
    return {
        "stages": STAGES,
        "state": ladder(
            "WY",
            "state",
            135,
            349,
            discovered="partial",
            coverage="partial",
            legal="partial",
            feasible="no",
        ),
        "counties": {
            "56001": ladder(
                "56001",
                "county",
                0,
                26,
                coverage="no",
                legal="unknown",
                headway="unknown",
                headway_peak="unknown",
                feasible="not_reached",
            ),
            "56039": ladder("56039", "county", 40, 40, feasible="no"),
            "56045": ladder(
                "56045",
                "county",
                12,
                12,
                headway="measured_zero",
                headway_peak="measured_zero",
                feasible="not_reached",
            ),
        },
        "statewide": {"shown": shown, "min_measured_share": 0.5, "label": LABEL},
        "ledger_ids": ["tod.status_min_measured_bus_share"],
        "source_ids": ["ntm_stops"],
    }


def record(name: str, p: tuple[float, float, float], shown: tuple[str, str, str]) -> dict:
    return {
        "name": name,
        "unit": "units",
        "kind": "range",
        "p10": p[0],
        "p50": p[1],
        "p90": p[2],
        "shown": dict(zip(("p10", "p50", "p90"), shown, strict=True)),
        "formula": "f",
        "assumptions": [],
        "source_ids": [],
    }


def reply(shown: bool = False, package: bool = False) -> dict:
    reforms = ["adu", "tod"] if package else ["tod"]
    tod_added = record("added units, TOD, state", (0, 0, 0), ("0 units", "0 units", "0 units"))
    if shown:
        tod_added = record("added units, TOD", (4, 367, 1720), ("4", "370", "1,700"))
    numbers = {
        "baseline": record("baseline", (4934, 10090, 17764), ("4,900", "10,000", "18,000")),
        "reform_results.tod.added": tod_added,
        "reform_results.tod.pct_change": record("pct", (0, 0, 0), ("0%", "0%", "0%")),
    }
    if package:
        numbers["package.combined"] = record("pkg", (465, 842, 1398), ("470", "840", "1,400"))
        numbers["package.pct_change"] = record("pkg pct", (5, 8, 14), ("5%", "8%", "14%"))
    out = {
        "state": "WY",
        "reforms": reforms,
        "overrides": {},
        "seed": 0,
        "draws": 2000,
        "hashes": {"code": "c", "data": "d", "assumptions": "a"},
        "contract": {"window": "2027-2031"},
        "reform_results": {
            "tod": {
                "title": "Transit-oriented density",
                "added": tod_added,
                "notes": [],
                "status": status(shown),
            },
            **({"adu": {"title": "Accessory dwelling units", "notes": []}} if package else {}),
        },
        "package": {"reforms": reforms} if package else None,
        "explanation": {
            "stamp": {"state_name": "Wyoming", "disclaimer": ""},
            "span": "2027-2031",
            "headline": {
                "sentence": {"text": "Wyoming would likely permit few or no more homes."},
                "frequency": {"text": "In eight of ten futures, it adds few or no homes."},
                "numbers": [
                    {"key": "baseline", "label": {"text": "Baseline"}, **numbers["baseline"]},
                    {
                        "key": "added",
                        "label": {"text": "Homes added"},
                        **numbers["package.combined" if package else "reform_results.tod.added"],
                    },
                    {
                        "key": "pct_change",
                        "label": {"text": "Change"},
                        **numbers[
                            "package.pct_change" if package else "reform_results.tod.pct_change"
                        ],
                    },
                ],
            },
            "numbers": numbers,
            "sources": {},
        },
    }
    out["status_views"] = status_views(out)
    return out


def test_hidden_view_says_so_and_names_the_measured_contribution():
    v = view_of(reply(), "tod")
    assert v["shown"] is False and v["partial"] is True
    assert v["label"] == LABEL[0].upper() + LABEL[1:]
    assert v["measured"]["text"] == "Measured contribution only: middle 0 units"
    assert v["text"].startswith("Transit-oriented density: the statewide range is not shown.")
    assert LABEL[1:] in v["text"] and " to " not in v["text"].split("(tod.")[0].split("middle")[0]
    assert v["package_note"] is None  # a reform alone has no package
    assert [c["stage"] for c in v["state"]["cells"]] == STAGES
    assert set(v["counties"]) == {"56001", "56039", "56045"}


def test_unknown_and_measured_zero_are_different_kinds():
    v = view_of(reply(), "tod")
    unknown = {c["stage"]: c for c in v["counties"]["56001"]["cells"]}
    zero = {c["stage"]: c for c in v["counties"]["56045"]["cells"]}
    assert unknown["headway"]["kind"] == "unknown" and unknown["headway"]["word"].startswith(
        "Unknown"
    )
    assert zero["headway"]["kind"] == "measured_zero" and zero["headway"]["word"].startswith(
        "Measured"
    )
    assert unknown["headway"]["word"] != zero["headway"]["word"]
    assert {k for k, _ in KINDS} == {x["kind"] for x in v["legend"]}
    assert v["counties"]["56039"]["partial"] is False
    assert v["counties"]["56001"]["partial"] is True


def test_package_note_when_hidden_in_a_package():
    v = status_views(reply(package=True))["tod"]
    assert (
        "package total counts transit-oriented density by its measured contribution only"
        in (v["package_note"])
    )
    assert "statewide range is not shown" in v["package_note"]


def test_partial_but_shown_keeps_the_range_with_its_label():
    v = view_of(reply(shown=True), "tod")
    assert v["shown"] is True and v["partial"] is True
    assert v["measured"] is None and v["package_note"] is None
    assert v["text"].startswith("Transit-oriented density: a likely range of 4 to 1,700 added")
    assert v["text"].endswith(LABEL + ".")


def test_no_status_no_view():
    r = reply(package=True)
    assert set(status_views(r)) == {"tod"}
    assert view_of(r, "adu") is None


def test_hearing_brief_hides_the_range():
    r = reply()
    doc = hearing_doc(r)
    v = r["status_views"]["tod"]
    assert doc["sentence"] == v["text"]
    assert doc["frequency"] == ""
    added = next(row for row in doc["answer_rows"] if row[0] == "Homes added")
    assert added[1] == "Statewide range not shown" and added[2] == v["measured"]["text"]
    change = next(row for row in doc["answer_rows"] if row[0] == "Change")
    assert change[1:] == ["Not shown", "Not shown"]
    assert not re.search(r"\b0 units to 0 units\b", json.dumps(doc))


def test_hearing_brief_package_note():
    r = reply(package=True)
    doc = hearing_doc(r)
    assert r["status_views"]["tod"]["package_note"] in doc["scope"]


def run_node(data: Path, cases: list[tuple[str, dict]]) -> list[dict]:
    """The browser's resolver (web/js/lib/answers.js) in Node over the same saved files."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    payload = {
        "data": str(data),
        "table": str(SHARED_PATH),
        "cases": [{"question": q, "context": c} for q, c in cases],
    }
    done = subprocess.run(  # noqa: S603 (fixed command, no shell)
        [node, str(NODE_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
        cwd=ROOT,
    )
    return json.loads(done.stdout)


def save_static(folder: Path, r: dict, key: str, package: list[str]) -> None:
    st = folder / "WY"
    (st / "runs").mkdir(parents=True)
    index = {"seed": 0, "draws": 2000, "runs": [key], "package": package}
    (st / "index.json").write_text(json.dumps(index), encoding="utf-8")
    (st / "runs" / f"{key}.json").write_text(json.dumps(r), encoding="utf-8")


QUESTION = "How many homes would transit-oriented density add in Wyoming?"
BENCH = ["adu", "missing_middle", "tod", "parking", "fees"]


def test_question_box_answers_with_the_status_text(tmp_path):
    r = reply()
    save_static(tmp_path, r, "tod", BENCH)
    context = {"state": "WY", "reforms": ["tod"], "overrides": {}, "route": "state"}
    answer = resolve(QUESTION, context, StaticReader.from_folder(tmp_path))
    assert answer["intent"] == "result"
    assert answer["text"] == r["status_views"]["tod"]["text"]
    (browser,) = run_node(tmp_path, [(QUESTION, context)])
    assert browser["text"] == answer["text"]
    assert browser["lines"] == answer["lines"]


@pytest.mark.parametrize("hidden", [True, False])
def test_question_box_partial_label(tmp_path, hidden):
    r = reply(shown=not hidden)
    save_static(tmp_path, r, "tod", BENCH)
    context = {"state": "WY", "reforms": ["tod"], "overrides": {}, "route": "state"}
    answer = resolve(QUESTION, context, StaticReader.from_folder(tmp_path))
    v = r["status_views"]["tod"]
    if hidden:
        assert answer["lines"] == [v["text"]]
    else:
        assert answer["lines"][-1] == f"{v['title']}: {v['label']}."
        assert "4 to 1,700" in answer["text"]
