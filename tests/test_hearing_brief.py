"""The baseline card rows, the two-page hearing brief and the static file layout version.

- ``api.baseline_view``: the card's rows in order; the reconciliation says "matched" only when
  the rule's state total equals the baseline middle value (PA, MA, WY), VT is an outside
  control; the PA window row is labelled a disclosed sensitivity, never subtracted; every
  number in a row is in the card record; the committed static ``baseline-card.json`` files
  equal what the server would write.
- ``report.hearing``: the brief of a Testland run holds its run id and the reproduce command;
  every number in it is a number of the run reply or the card (none typed by hand); the answer
  table equals the headline's shown text; the ADU headline scope and the Census-comparable
  range are the accounting records'; the law section shows only when its payload is given;
  HTML is self-contained with two-page print rules; ``GET /export/hearing`` equals the CLI.
- The static index is versioned: the exporter writes ``schema_version`` and the browser's
  ``STATIC_SCHEMA`` matches it; the committed exports list hearing briefs that exist.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.api.answers import run_id
from visionhousing.api.baseline_view import card_view, view_for, write_static
from visionhousing.baseline.card import card_for
from visionhousing.explain.numbers import numbers_in
from visionhousing.export_static import STATIC_SCHEMA_VERSION, read_saved
from visionhousing.report import hearing

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "public" / "data"
SHIPPED = {"PA": 151882, "MA": 66715, "WY": 10090}
ORDER = [
    "historical_years",
    "unit_fields",
    "data_release",
    "information_cutoff",
    "forecast_window",
    "baseline_bridge_nowcast_2026",
]


def _all_numbers(*docs) -> set[str]:
    out: set[str] = set()
    for d in docs:
        text = json.dumps(d, ensure_ascii=False)
        out.update(numbers_in(text))
        # Raw values as the server stores them (151882, 0.1442 -> 14.4 is in the formula).
        for m in re.finditer(r"-?\d+(?:\.\d+)?", text):
            out.update(numbers_in(m.group(0).lstrip("-")))
    return out


# ------------------------------------------------------------------ baseline card rows


@pytest.mark.parametrize("state", sorted(SHIPPED))
def test_reconciled_states_match_the_shipped_middle_value(state):
    view = card_view(card_for(state), {"p50": float(SHIPPED[state])})
    ids = [r["id"] for r in view["rows"]]
    assert ids[: len(ORDER)] == ORDER
    assert ids[-1] == "reconciliation"
    assert view["reconciliation"] == {
        "role": "reconciled",
        "state_component": SHIPPED[state],
        "baseline_p50": float(SHIPPED[state]),
        "matched": True,
    }
    assert f"{SHIPPED[state]:,}" in view["rows"][-1]["text"]


def test_a_different_baseline_is_not_matched_and_no_baseline_is_not_compared():
    card = card_for("PA")
    off = card_view(card, {"p50": 140000.0})
    assert off["reconciliation"]["matched"] is False
    assert "does NOT match" in off["rows"][-1]["text"]
    none = card_view(card, None)
    assert none["reconciliation"]["matched"] is None
    assert none["rows"][-1]["status"] == "not compared"


def test_vermont_is_an_outside_control_and_testland_has_no_card():
    vt = card_view(card_for("VT"), {"p50": 12000.0})
    assert vt["reconciliation"]["role"] == "control"
    assert vt["reconciliation"]["matched"] is None
    assert card_view(None) is None
    assert view_for("TL") is None


def test_pa_window_row_is_a_disclosed_sensitivity_never_subtracted():
    view = card_view(card_for("PA"), {"p50": 151882.0})
    row = next(r for r in view["rows"] if r["id"] == "baseline_pa_window_2022_2025")
    assert row["kind"] == "sensitivity"
    assert "never subtracted" in row["label"]
    assert "never subtracted" in row["text"]
    assert "129,985" in row["text"]
    assert "baseline_pa_window_2022_2025" in row["assumption_ids"]
    assert all(r["id"] != "baseline_pa_window_2022_2025" for r in card_view(card_for("MA"))["rows"])


@pytest.mark.parametrize("state", ["PA", "MA", "VT", "WY"])
def test_every_number_in_a_row_is_in_the_card_record(state):
    card = card_for(state)
    allowed = _all_numbers(card) | {"5", "100"}
    for row in card_view(card, {"p50": float(SHIPPED.get(state, 1))})["rows"]:
        if row["id"] == "reconciliation":
            continue
        for n in numbers_in(row["text"]):
            assert n in allowed, f"{state} {row['id']}: {n} is not in the card record"


@pytest.mark.parametrize("state", ["PA", "MA", "VT", "WY"])
def test_committed_static_card_equals_the_server_view(state):
    path = DATA / state / "baseline-card.json"
    if not path.is_file():
        pytest.skip(f"no static export of {state}")
    summary = read_saved(DATA / state / "summary.json")
    assert read_saved(path) == json.loads(json.dumps(view_for(state, summary["baseline"])))


def test_write_static_reads_the_saved_summary(tmp_path):
    (tmp_path / "PA").mkdir()
    (tmp_path / "PA" / "summary.json").write_text(json.dumps({"baseline": {"p50": 151882.0}}))
    (tmp_path / "TL").mkdir()
    (tmp_path / "TL" / "summary.json").write_text(json.dumps({"baseline": {"p50": 1.0}}))
    written = write_static(tmp_path)
    assert written == [tmp_path / "PA" / "baseline-card.json"]
    out = json.loads(written[0].read_text())
    assert out["view"]["reconciliation"]["matched"] is True


# ------------------------------------------------------------------ hearing brief


@pytest.fixture(scope="module")
def client():
    return AsgiClient(create_app())


@pytest.fixture(scope="module")
def adu_run(client):
    body = {"state": "TL", "reforms": ["adu"], "seed": 0, "explain": True, "drivers": True}
    r = client.post("/api/v1/run", json_body=body)
    assert r.status == 200
    return r.json()


def _text_of(html: str) -> str:
    class Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts: list[str] = []
            self.skip = False

        def handle_starttag(self, tag, attrs):
            self.skip = tag in ("style", "title")

        def handle_endtag(self, tag):
            self.skip = False

        def handle_data(self, data):
            if not self.skip:
                self.parts.append(data)

    p = Text()
    p.feed(html)
    return " ".join(p.parts)


def test_brief_holds_the_run_id_and_reproduce_command(adu_run):
    doc = hearing.hearing_doc(adu_run)
    rid = run_id(adu_run, "adu")
    assert doc["stamp"]["run_id"] == rid
    html = hearing.to_html(doc)
    assert rid in html
    assert "visionhousing hearing-brief --state TL --reforms adu --seed 0 --draws 2000" in html
    assert "#/state?v=1&amp;s=TL&amp;r=adu" in html


def test_every_number_in_the_brief_comes_from_the_run(adu_run):
    doc = hearing.hearing_doc(adu_run)
    allowed = _all_numbers(adu_run)
    text = _text_of(hearing.to_html(doc))
    for n in numbers_in(text):
        assert n in allowed, f"{n} in the hearing brief is not a number of the run"
    md = hearing.to_markdown(doc)
    assert sorted(numbers_in(md)) == sorted(numbers_in(text))


def test_answer_rows_and_scope_are_the_records_text(adu_run):
    doc = hearing.hearing_doc(adu_run)
    head = adu_run["explanation"]["headline"]["numbers"]
    assert [r[1] for r in doc["answer_rows"]] == [
        f"{n['shown']['p10']} to {n['shown']['p90']}" for n in head
    ]
    assert [r[2] for r in doc["answer_rows"]] == [n["shown"]["p50"] for n in head]
    acc = adu_run["reform_results"]["adu"]["accounting"]
    comp = adu_run["explanation"]["numbers"]["reform_results.adu.accounting.records.comparable"]
    (line,) = doc["scope"]
    assert f"headline counts {acc['headline']['scope']}" in line
    assert f"{comp['shown']['p10']} to {comp['shown']['p90']}" in line
    assert doc["drivers"] == [
        x["text"]["text"] for x in adu_run["explanation"]["drivers"]["adu"]["items"][:3]
    ]
    assert doc["law"] is None and "What the law changes" not in hearing.to_html(doc)


def test_law_section_shows_only_with_its_payload(adu_run):
    law = {
        "classes": [{"id": "newly_allowed", "word": "newly allowed"}],
        "denominator": {"label": "single-family lots"},
        "statewide": {
            "classes": {"newly_allowed": {"share": {"shown": "40%"}, "count": {"shown": "1,200"}}},
            "unresolved_share": {"shown": "35%"},
        },
        "provision": {"citation": "Benchmark reform", "status": "hypothetical"},
    }
    html = hearing.render(adu_run, "html", law=law)
    assert "What the law changes" in html and "newly allowed" in html and "1,200" in html
    assert "Local rules not yet read (unresolved): 35%" in html


def test_html_is_self_contained_with_two_page_print_rules(adu_run):
    html = hearing.render(adu_run, "html", run_date="2026-11-25")
    for banned in ("<script", "<link", "<img", "@import", "http://", "https://"):
        assert banned not in html
    assert "@page { size: letter" in html and ".page-two { break-before: page; }" in html
    assert "Run date: 2026-11-25" in html
    assert html.count("<h1>") == 1 and html.count("<table>") == html.count("</table>")


def test_package_brief_lists_the_waterfall(client):
    body = {"state": "TL", "reforms": list(hearing.PACKAGE), "seed": 0, "explain": True}
    run = client.post("/api/v1/run", json_body=body).json()
    doc = hearing.hearing_doc(run)
    assert doc["key"] == "package"
    assert doc["title"].startswith("Hearing brief: All five benchmark reforms together")
    assert len(doc["drivers"]) == len(run["explanation"]["waterfall"]["steps"])
    assert len(doc["scope"]) == len(hearing.PACKAGE)


def test_api_hearing_export_equals_the_run_reply(client, adu_run):
    r = client.get("/api/v1/export/hearing?s=TL&r=adu")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/html")
    # Served under the app's style policy: the page links its rules, it has no inline style.
    assert r.text == hearing.render(adu_run, "html", css_href=hearing.API_CSS_HREF)
    assert "<style>" not in r.text and '<link rel="stylesheet" href="hearing.css">' in r.text
    css = client.get("/api/v1/export/hearing.css")
    assert css.status == 200 and css.headers["content-type"].startswith("text/css")
    assert css.text == hearing.CSS + "\n"
    assert client.get("/api/v1/export/pdf?s=TL&r=adu").status == 400


def test_cli_writes_the_brief_from_a_static_export(tmp_path, capsys):
    from visionhousing import cli

    if not (DATA / "PA" / "runs" / "adu.json").is_file():
        pytest.skip("no PA static export")
    out = tmp_path / "pa-adu.md"
    assert cli.main(["hearing-brief", "--state", "PA", "--reforms", "adu", "--static", str(DATA),
                     "--format", "md", "--out", str(out)]) == 0  # fmt: skip
    text = out.read_text()
    run = read_saved(DATA / "PA" / "runs" / "adu.json")
    assert run_id(run, "adu") in text
    assert "matches the baseline middle value 151,882" in text


def test_cli_runs_the_engine_for_testland(tmp_path):
    from visionhousing import cli

    out = tmp_path / "tl.html"
    assert cli.main(["hearing-brief", "--state", "TL", "--reforms", "package", "--draws", "200",
                     "--out", str(out)]) == 0  # fmt: skip
    text = out.read_text()
    assert "TL/package/" in text and "--draws 200" in text
    assert "No baseline card for this state" in text


# ------------------------------------------------------------------ static index version


def test_static_schema_version_matches_the_browser():
    api_js = (ROOT / "web" / "js" / "lib" / "api.js").read_text()
    m = re.search(r"export const STATIC_SCHEMA = (\d+);", api_js)
    assert m and int(m.group(1)) == STATIC_SCHEMA_VERSION


@pytest.mark.parametrize("state", ["PA", "MA", "VT", "WY"])
def test_committed_exports_list_hearing_briefs_that_exist(state):
    idx_path = DATA / state / "index.json"
    if not idx_path.is_file():
        pytest.skip(f"no static export of {state}")
    idx = json.loads(idx_path.read_text())
    if idx.get("schema_version", 1) < 2:
        pytest.skip("export predates schema 2")
    assert idx["baseline_card"] is True
    for key in idx["hearing_briefs"]:
        path = DATA / state / "exports" / f"{key}.hearing.html"
        assert path.is_file()
        run = read_saved(DATA / state / "runs" / f"{key}.json")
        text = path.read_text()
        assert run_id(run, key) in text
        assert '<link rel="stylesheet" href="../../hearing.css">' in text
    assert (DATA / "hearing.css").read_text() == hearing.CSS + "\n"


def test_cli_law_section_reads_the_loaded_contexts_pack_folder(tmp_path):
    """The brief's law tables come from the folder the context was read from, not pack_dir."""
    from types import SimpleNamespace

    from visionhousing.engine.context import PACK_FOLDER_KEY, pack_dir

    copy = tmp_path / "packs" / "PA"
    ctx = SimpleNamespace(state="PA", derived={PACK_FOLDER_KEY: copy})
    assert hearing.context_folder(ctx) == copy
    bare = SimpleNamespace(state="PA", derived={})
    assert hearing.context_folder(bare) == pack_dir("PA")
