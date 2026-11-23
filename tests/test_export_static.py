"""The static-site exporter (``visionhousing export-static``), on Testland.

Each saved file must hold exactly what the live interface answers, the index must list
what was saved, and the folder must stay inside the static site's size limit.
"""

from __future__ import annotations

import json

import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.cli import main
from visionhousing.export_static import (
    BENCHMARK_PACKAGE,
    SIZE_LIMIT_BYTES,
    ExportError,
    export_static,
    folder_size,
)

API = "/api/v1"
DRAWS = 100


@pytest.fixture(scope="module")
def packs(tmp_path_factory):
    return tmp_path_factory.mktemp("packs")


@pytest.fixture(scope="module")
def exported(tmp_path_factory, packs):
    out = tmp_path_factory.mktemp("site") / "data"
    report = export_static("tl", out, packs_dir=packs, draws=DRAWS)
    return out, report


@pytest.fixture(scope="module")
def client(packs):
    return AsgiClient(create_app(packs_dir=packs))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_index_lists_what_was_saved(exported):
    out, report = exported
    index = read(out / "TL" / "index.json")
    assert index["state"] == "TL" and index["seed"] == 0 and index["draws"] == DRAWS
    assert index["package"] == list(BENCHMARK_PACKAGE)
    assert index["runs"] == ["baseline", *BENCHMARK_PACKAGE, "package"]
    for key in index["runs"]:
        assert (out / "TL" / "runs" / f"{key}.json").is_file()
        assert (out / "TL" / "compare" / f"{key}.json").is_file()
    for fips in index["counties"]:
        assert (out / "TL" / "counties" / f"{fips}.json").is_file()
        assert (out / "TL" / "profiles" / f"{fips}.json").is_file()
    assert index["tract_profiles"] is True and report.tract_profiles
    assert index["explanations"] is True
    for name in ["summary", "ledger", "provenance", "validation"]:
        assert (out / "TL" / f"{name}.json").is_file()
    for name in ["states", "reforms", "ledger", "provenance"]:
        assert (out / f"{name}.json").is_file()
    assert [s["code"] for s in read(out / "states.json")["states"]] == ["TL"]


def test_files_equal_the_live_replies(exported, client):
    out, _ = exported
    pairs = {
        "TL/summary.json": f"{API}/states/TL/summary",
        "TL/validation.json": f"{API}/validation?state=TL",
        "TL/profiles/99001.json": f"{API}/profile/TL/99001",
        "TL/places/counties.json": f"{API}/places/TL?level=county&limit=500",
        "reforms/adu.json": f"{API}/reforms/adu",
        "TL/counties/99001.json": f"{API}/states/TL/counties/99001?r="
        + ",".join(BENCHMARK_PACKAGE)
        + f"&seed=0&draws={DRAWS}",
    }
    for rel, url in pairs.items():
        assert (out / rel).read_bytes() == client.get(url).body, rel
    # A reform alone is saved with its top drivers (the Reform screen shows them).
    body = {"state": "TL", "reforms": ["adu"], "seed": 0, "draws": DRAWS, "drivers": True}
    assert (out / "TL" / "runs" / "adu.json").read_bytes() == client.post(f"{API}/run", body).body
    run = read(out / "TL" / "runs" / "package.json")
    assert run["reforms"] == list(BENCHMARK_PACKAGE) and run["explanation"] is not None
    # The screens' other default replies: maps, outlines, evidence, report card, exports.
    query = f"s=TL&r=adu&seed=0&draws={DRAWS}"
    more = {
        "TL/maps/adu.json": f"{API}/states/TL/map?{query}",
        "TL/outlines.json": f"{API}/states/TL/outlines",
        "TL/report-card.json": f"{API}/report-card?state=TL",
        "evidence.json": f"{API}/evidence",
        "TL/exports/adu.csv": f"{API}/export/csv?{query}",
    }
    for rel, url in more.items():
        assert (out / rel).read_bytes() == client.get(url).body, rel
    index = read(out / "TL" / "index.json")
    assert index["extra_runs"] == ["pa_hb2186", "pa_sb1346"]
    for key in [*index["runs"], *index["extra_runs"]]:
        assert (out / "TL" / "maps" / f"{key}.json").is_file()
        assert (out / "TL" / "exports" / f"{key}.html").is_file()


def test_tract_profiles_are_saved_per_county(exported):
    out, _ = exported
    tracts = read(out / "TL" / "places" / "tracts-99001.json")["places"]
    assert tracts and all(t["geoid"].startswith("99001") for t in tracts)
    assert all((out / "TL" / "profiles" / f"{t['geoid']}.json").is_file() for t in tracts)


def test_size_report_and_limit(exported):
    out, report = exported
    assert report.total == folder_size(out) < SIZE_LIMIT_BYTES
    text = report.text()
    assert "runs" in text and "whole folder" in text


def test_tract_budget_and_no_explanations(tmp_path, packs):
    out = tmp_path / "data"
    report = export_static(
        "TL", out, packs_dir=packs, draws=DRAWS, tract_budget_mb=0.01, explain="no"
    )
    index = read(out / "TL" / "index.json")
    assert index["tract_profiles"] is False and index["explanations"] is False
    assert not list((out / "TL" / "profiles").glob("???????????.json"))
    assert not list((out / "TL" / "places").glob("tracts-*.json"))
    assert read(out / "TL" / "runs" / "adu.json")["explanation"] is None
    assert any("budget" in n for n in report.notes)


def test_rerun_replaces_the_state_and_refuses_foreign_folders(tmp_path, packs):
    out = tmp_path / "data"
    export_static("TL", out, packs_dir=packs, draws=DRAWS, tracts="no", explain="no")
    stray = out / "TL" / "runs" / "old.json"
    stray.write_text("{}", encoding="utf-8")
    export_static("TL", out, packs_dir=packs, draws=DRAWS, tracts="no", explain="no")
    assert not stray.exists()
    (out / "TL" / "index.json").unlink()
    with pytest.raises(ExportError, match="not an earlier export"):
        export_static("TL", out, packs_dir=packs, draws=DRAWS, tracts="no", explain="no")


def test_unknown_state_writes_nothing(tmp_path, packs):
    out = tmp_path / "data"
    with pytest.raises(ExportError, match="404"):
        export_static("OH", out, packs_dir=packs, draws=DRAWS)
    assert not out.exists()


def test_cli(tmp_path, capsys):
    out = tmp_path / "data"
    code = main(["export-static", "--state", "TL", "--out", str(out), "--draws", str(DRAWS),
                 "--tracts", "no", "--explain", "no"])  # fmt: skip
    assert code == 0
    assert "export-static TL" in capsys.readouterr().out
    assert (out / "TL" / "index.json").is_file()
