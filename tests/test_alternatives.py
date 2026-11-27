"""Alternatives at a glance (api/alternatives.py): rows equal the API, paired diffs from draws.

Runs on Testland with few draws. Checks that every built row's paired difference is
worked out draw by draw from the two runs (never from percentiles), that the address
returns exactly the table the builder writes, that every named variant of a family has
a row, that absent variants show "not built" with a reason, and that the static file
and the hearing appendix read the same rows.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import alternatives as alt
from visionhousing.api import create_app
from visionhousing.engine.context import load_context
from visionhousing.engine.core import run
from visionhousing.engine.variants import load_variants

DRAWS = 120


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture(scope="module")
def table(ctx):
    return alt._clean(alt.build_alternatives(ctx, seed=0, draws=DRAWS))


def test_paired_row_takes_differences_draw_by_draw():
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([3.0, 1.0, 2.0])
    row = alt.paired_row(a, b)
    diff = a - b
    assert row["diff"]["p50"] == pytest.approx(float(np.percentile(diff, 50)))
    # Not the difference of the two middles (2.0 - 2.0 = 0.0).
    assert row["diff"]["p50"] != pytest.approx(np.percentile(a, 50) - np.percentile(b, 50))
    assert row["below"]["share"] == pytest.approx(1 / 3)
    assert row["above"]["share"] == pytest.approx(2 / 3)
    assert row["below"]["se"] == pytest.approx(np.sqrt((1 / 3) * (2 / 3) / 3))
    assert not row["same_every_draw"]
    with pytest.raises(ValueError):
        alt.paired_row(a, b[:2])


def test_hidden_rows_write_no_range():
    row = alt.paired_row(np.arange(10.0), np.zeros(10), hidden=True)
    assert row["added"]["range"] == "not shown"
    assert row["diff"]["range"] == "not shown"
    assert row["added"]["mid"]


def test_variant_rows_match_their_own_runs(ctx, table):
    known = load_variants()
    adu = table["reforms"]["adu"]
    bench = run("TL", ["adu"], seed=0, draws=DRAWS, context=ctx).reforms["adu"].state_draws
    assert adu["headline"]["p50"] == pytest.approx(float(np.percentile(bench, 50)))
    for row in adu["rows"]:
        if row["kind"] != "variant" or row["status"] != "built":
            continue
        assert row["id"] in known
        v = run("TL", {"adu": {"variant": row["id"]}}, seed=0, draws=DRAWS, context=ctx)
        d = np.asarray(v.reforms["adu"].state_draws) - np.asarray(bench)
        assert row["diff"]["p10"] == pytest.approx(float(np.percentile(d, 10)))
        assert row["diff"]["p50"] == pytest.approx(float(np.percentile(d, 50)))
        assert row["diff"]["p90"] == pytest.approx(float(np.percentile(d, 90)))
        assert row["below"]["share"] == pytest.approx(float((d < -1e-9).mean()))
        assert row["above"]["share"] == pytest.approx(float((d > 1e-9).mean()))
        assert f"variants.{row['id']}" in row["assumption_ids"]


def test_every_named_variant_has_a_row(table):
    known = load_variants()
    cfg = alt.load_settings()
    for key, part in table["reforms"].items():
        ids = {r["id"] for r in part["rows"]}
        rows = cfg["reforms"].get(key, {}).get("rows", [])
        elsewhere = {
            r["variant"] for r in rows if r.get("variant") and "TL" not in r.get("states", ["TL"])
        }
        for name, v in known.items():
            if v.reform != key or name.endswith("_capacity_screen") or name in elsewhere:
                continue
            assert name in ids, (key, name)
        assert "capacity_screen" in ids
        assert {"market_choice", "market_choice_capacity"} <= ids


def test_absent_rows_say_why(table):
    for part in table["reforms"].values():
        for row in part["rows"]:
            assert row["status"] in ("built", "not_built")
            if row["status"] == "not_built":
                assert row["absent"]
                assert "diff" not in row
            else:
                assert row["reason"] and row["added"]["mid"] and row["diff"]["mid"]
    planned = [r for r in table["reforms"]["parking"]["rows"] if r["kind"] == "planned"]
    assert all(r["status"] == "not_built" for r in planned)


def test_correlated_row_pairs_the_sensitivity_run(ctx, table):
    from visionhousing.engine import correlated

    res, _ = correlated.run_scenario(
        ctx, "correlated", list(table["reforms"]), seed=0, draws=DRAWS,
        settings=correlated.load_settings(),
    )  # fmt: skip
    bench = run("TL", ["fees"], seed=0, draws=DRAWS, context=ctx).reforms["fees"].state_draws
    row = next(r for r in table["reforms"]["fees"]["rows"] if r["id"] == "correlated_stress")
    assert row["status"] == "built"
    d = np.asarray(res.reforms["fees"].state_draws) - np.asarray(bench)
    assert row["diff"]["p50"] == pytest.approx(float(np.percentile(d, 50)))


def test_capacity_row_carries_joint_bounds(table):
    row = next(r for r in table["reforms"]["adu"]["rows"] if r["id"] == "capacity_screen")
    assert row["status"] == "built"
    assert row["bounds"]["low_p50"] <= row["bounds"]["high_p50"]
    assert row["bounds"]["text"].startswith("joint bounds")
    assert row["diff"]["p50"] <= 0  # the screen only removes homes


def test_summary_counts_rows(table):
    for part in table["reforms"].values():
        s = part["summary"]
        built = sum(r["status"] == "built" for r in part["rows"])
        assert s["built"] == built
        assert s["built"] + s["not_built"] == len(part["rows"])
        if s["widest"]:
            assert any(r["id"] == s["widest"] for r in part["rows"])


def test_address_rows_equal_the_builder(tmp_path, monkeypatch, table):
    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DIR", str(tmp_path / "cmp"))
    client = AsgiClient(create_app(packs_dir=tmp_path))
    reply = client.get(f"/api/v1/states/TL/alternatives?r=adu&draws={DRAWS}")
    assert reply.status == 200
    body = reply.json()
    assert body["rows"] == table["reforms"]["adu"]["rows"]
    assert body["headline"] == table["reforms"]["adu"]["headline"]
    assert body["formula"] == table["formula"]
    # Kept on disk: the second ask reads the same rows back.
    again = client.get(f"/api/v1/states/TL/alternatives?r=adu&draws={DRAWS}").json()
    assert again["rows"] == body["rows"]
    assert client.get("/api/v1/states/TL/alternatives?r=nope").status == 400
    assert client.get("/api/v1/states/TL/alternatives?zz=1").status == 400


def test_static_file_and_hearing_rows(tmp_path, table):
    folder = tmp_path / "TL"
    folder.mkdir()
    (folder / "index.json").write_text(json.dumps({"state": "TL"}), encoding="utf-8")
    alt.write_static(table, folder, small=False)
    assert json.loads((folder / alt.STATIC_FILE).read_text(encoding="utf-8")) == table
    size = alt.write_static(table, folder)
    saved = json.loads((folder / alt.STATIC_FILE).read_text(encoding="utf-8"))
    assert saved == alt.compact(table)
    assert "p90" not in json.dumps(saved) and "assumption_ids" in json.dumps(saved)
    assert json.loads((folder / "index.json").read_text())["alternatives"] is True
    assert size == len((folder / alt.STATIC_FILE).read_bytes())
    view = alt.reform_view(saved, "parking")
    brief = alt.hearing_rows(view)
    assert len(brief["rows"]) == len(view["rows"])
    built = next(r for r in view["rows"] if r["status"] == "built")
    line = next(x for x in brief["rows"] if x[0] == built["title"])
    assert line[1] == f"{built['added']['mid']} ({built['added']['range']})"
    assert line[2] == f"{built['diff']['mid']} ({built['diff']['range']})"


def test_hearing_brief_appendix(table):
    from visionhousing.report.hearing import _alternatives_html

    view = alt.reform_view(table, "fees")
    html = _alternatives_html(alt.hearing_rows(view))
    assert "alternatives at a glance" in html
    for row in view["rows"]:
        assert row["title"].split("(")[0].strip()[:20] in html.replace("&#x27;", "'")


def test_deck_summary_slide_reads_the_saved_table(tmp_path, table):
    import build_deck as bd

    folder = tmp_path / "TL"
    folder.mkdir()
    alt.write_static(table, folder)
    deck = bd.Deck()
    bd._alternatives_slide(bd.Data(tmp_path, "TL"), deck, "TL")
    slide = deck.slides[-1]
    assert slide.kicker == "Alternatives"
    for key, part in table["reforms"].items():
        s = part["summary"]
        assert bd.esc(s["count_text"]) in slide.body, key
        assert bd.esc(s["widest_text"]) in slide.body, key
    assert all(n.source for n in deck.numbers)
    empty = bd.Deck()
    bd._alternatives_slide(bd.Data(tmp_path / "none", "TL"), empty, "TL")
    assert "not in this build" in empty.slides[-1].body
