"""The ADU budget-and-target panel's server text and its browser twin.

What is checked (Testland, a small grid on the same draws):

- ``budget_view.grid_view`` writes every number of every cell as text: each cell's key is the
  key the browser builds, each target's share text is the engine's ``share_percent`` with its
  Monte Carlo standard error, and the moved-in-time note follows the sign of its record;
- the key lever of a "both" cell is the lever with the larger middle (law alone vs the program
  on top of it), from the cell's own records;
- ``gridLookup`` in ``web/js/lib/budget.js`` (run in Node) gives exactly what the server's
  ``grid_lookup`` gives, over every combination of tested, bracketed, outside and missing inputs,
  so the panel never answers differently from the API; a target between two tested ones is a
  bracket and no cell is interpolated;
- ``write_static`` saves ``{grid, view}`` and marks the index, and the question box reads the
  grid's cells (Python and the browser resolver agree; every number is in a cited record);
- the API reply carries the view.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from visionhousing.api import budget_view as bv
from visionhousing.engine.context import load_context
from visionhousing.engine.readouts import share_percent
from visionhousing.scenarios import adu_budget as ab

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / "tests" / "js" / "budget_parity.mjs"
DRAWS = 200


@pytest.fixture(scope="module")
def grid():
    settings = ab.load_grid_settings()
    settings = {**settings, "appropriations": [2_000_000, 20_000_000], "awards": [10_000, 20_000],
                "targets": [10, 50, 100]}  # fmt: skip
    return json.loads(
        json.dumps(ab.budget_grid(load_context("TL"), draws=DRAWS, settings=settings))
    )


@pytest.fixture(scope="module")
def view(grid):
    return bv.grid_view(grid)


def node(payload: dict, script: Path) -> list:
    exe = shutil.which("node")
    if not exe:
        pytest.skip("node not installed")
    done = subprocess.run(  # noqa: S603 (fixed command, no shell)
        [exe, str(script)], input=json.dumps(payload), capture_output=True, text=True,
        check=True, timeout=120, cwd=ROOT,
    )  # fmt: skip
    return json.loads(done.stdout)


def test_view_writes_every_number_as_text(grid, view):
    assert len(view["cells"]) == len(grid["cells"])
    for c, v in zip(grid["cells"], view["cells"], strict=True):
        assert v["key"] == bv.cell_key(c) and "e+" not in v["key"]
        assert (
            v["added"]["likely"] == f"{v['added']['shown']['p10']} to {v['added']['shown']['p90']}"
        )
        for t in grid["settings"]["targets"]:
            k = f"{t:g}"
            m, tv = c["target_met"][k], v["targets"][k]
            assert tv["share_text"] == share_percent(m["share"], grid["draws"])
            assert tv["se_text"] == f"{100 * m['mc_se']:.1f} percentage points"
            assert tv["pareto"] == bool(c["pareto"][k])
        if c["arm"] == "law_only":
            assert v["appropriation_text"] == "none" and v["key_lever"]["id"] == "law"
        else:
            assert v["appropriation_text"].startswith("$")
        moved = c["moved_from_later"]["p50"]
        assert ("Negative" in v["moved_note"]) == (moved < 0)
    assert [o["text"] for o in view["options"]["targets"]] == ["10", "50", "100"]
    assert view["accept_levels"] and all(
        "modeled draws" in a["text"] for a in view["accept_levels"]
    )
    assert set(view["flags"]) == {
        "input_missing", "outside_tested_range", "between_tested_values", "budget_exceeded",
        "no_tested_option_meets_both",
    }  # fmt: skip
    assert "no appropriation limit" in view["uncapped_note"]


def test_key_lever_compares_the_cells_own_middles(grid, view):
    law = next(c for c in grid["cells"] if c["arm"] == "law_only")
    for c, v in zip(grid["cells"], view["cells"], strict=True):
        if c["arm"] != "both":
            continue
        want = "law" if law["added"]["p50"] >= c["program_added"]["p50"] else "program"
        assert v["key_lever"]["id"] == want


CASES = [
    {"target": t, "budget": b, "award": a, "accept": acc}
    for t in (None, 5, 10, 30, 50, 100, 1_000_000)
    for b in (None, 0, 2_000_000, 10_000_000, 50_000_000)
    for a in (None, 10_000, 15_000, 1_000_000)
    for acc in (None, 0.0, 0.5, 1.0)
]


def test_browser_lookup_equals_the_server(grid):
    js = node({"grid": grid, "cases": CASES}, NODE)
    for case, got in zip(CASES, js, strict=True):
        want = ab.grid_lookup(grid, **case)
        want.pop("text", None)
        assert got["lookup"] == want, case
        for k in ("options", "pareto", "over_budget"):
            if k in want:
                assert got["keys"][k] == [bv.cell_key(o) for o in want[k]], case


def test_a_threshold_between_tested_cells_is_a_bracket(grid):
    q = ab.grid_lookup(grid, target=30)
    assert q["flags"] == ["between_tested_values"] and q["brackets"]["target"] == [10, 50]
    assert "options" not in q  # no cell is made up between the two tested ones
    lo, hi = (ab.grid_lookup(grid, target=t) for t in (10, 50))
    for qq, k in ((lo, "10"), (hi, "50")):
        cells = {bv.cell_key(c): c for c in grid["cells"]}
        for o in qq["options"]:
            assert o["share_meeting_target"] == cells[bv.cell_key(o)]["target_met"][k]["share"]


def test_static_file_and_index_flag(tmp_path, grid):
    st = tmp_path / "TL"
    st.mkdir()
    (st / "index.json").write_text(json.dumps({"state": "TL"}), encoding="utf-8")
    size = bv.write_static(grid, st)
    saved = json.loads((st / bv.STATIC_FILE).read_text(encoding="utf-8"))
    assert size == (st / bv.STATIC_FILE).stat().st_size
    assert saved["grid"]["cells"] == grid["cells"] and saved["view"]["cells"][0]["key"]
    assert json.loads((st / "index.json").read_text(encoding="utf-8"))["adu_budget_grid"] is True


# ------------------------------------------------------------------ the question box


@pytest.fixture(scope="module")
def tl_static(tmp_path_factory, grid):
    """A Testland static export holding the small grid (as the exporter would write it)."""
    from visionhousing.export_static import export_static

    folder = tmp_path_factory.mktemp("tl-budget")
    export_static("TL", folder, packs_dir=tmp_path_factory.mktemp("tl-packs"), draws=100)
    bv.write_static(grid, folder / "TL")
    return folder


QUESTIONS = [
    "Will ADUs reach 50 homes within our budget?",
    "Will ADUs reach 50 homes with a $2 million budget?",
    "Will ADUs reach 30 homes with a program budget?",
    "Will ADUs reach 5000 homes with a grant program?",
    "What budget would ADUs need?",
    "Will ADUs reach 100 homes with a $10 million budget?",
]


def test_question_box_reads_the_grid(tl_static):
    from tests.test_answers import assert_grounded, ctx, run_node
    from visionhousing.api.answers import StaticReader, resolve

    reader = StaticReader.from_folder(tl_static)
    answers = [resolve(q, ctx("TL", ["adu"]), reader) for q in QUESTIONS]
    for q, a in zip(QUESTIONS, answers, strict=True):
        assert a["intent"] == "target", q
        assert "budget-and-target grid" in a["text"], q
        assert_grounded(a)
    by = dict(zip(QUESTIONS, answers, strict=True))
    assert by["Will ADUs reach 30 homes with a program budget?"]["status"] == "out_of_range"
    assert "between the tested targets 10 and 50" in by[QUESTIONS[2]]["text"]
    assert "outside the tested targets" in by[QUESTIONS[3]]["text"]
    assert by["What budget would ADUs need?"]["status"] == "unknown"
    assert "Budget ceiling: $2,000,000" in by[QUESTIONS[1]]["text"]
    assert "not a tested value" in by[QUESTIONS[5]]["text"]
    assert any(r["kind"] == "budget-grid" for r in by[QUESTIONS[0]]["records"])
    js = run_node(tl_static, [(q, ctx("TL", ["adu"])) for q in QUESTIONS])
    for q, py, browser in zip(QUESTIONS, answers, js, strict=True):
        assert browser == py, q


def test_api_reply_carries_the_view(tmp_path, monkeypatch):
    from tests.asgi_client import AsgiClient
    from visionhousing.api import create_app

    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DIR", "none")
    client = AsgiClient(create_app(packs_dir=tmp_path))
    body = client.get("/api/v1/states/TL/adu-budget-grid?draws=60&target=500").json()
    assert [c["key"] for c in body["view"]["cells"]] == [
        bv.cell_key(c) for c in body["grid"]["cells"]
    ]
    assert body["view"]["draws"] == 60
