"""State pack builder on Testland-shaped inputs: schema, dictionary, determinism, budgets."""

from __future__ import annotations

import gzip
import json

import pandas as pd
import pytest

from tests.pack_fakes import TESTLAND, sample_blocks, sample_shapes
from visionhousing import cli
from visionhousing.data import pack
from visionhousing.data.sources import blocks as bl


def fixture_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(TESTLAND / f"{name}.csv", dtype={"geoid": str, "county": str, "tract": str})


def make_inputs(tmp_path) -> pack.PackInputs:
    tracts = fixture_csv("tracts").drop(columns=["area_type", "land_area_m2"])
    counties = sorted(set(tracts["county"]))
    hpi_county = pd.DataFrame(
        [
            {"geoid": c, "year": y, "annual_change_pct": 2.0, "hpi": 100.0 + (y - 2015) * (i + 1),
             "hpi_1990_base": None, "hpi_2000_base": None}
            for i, c in enumerate(counties)
            for y in range(2015, 2026)
        ]
    )  # fmt: skip
    hpi_tract = hpi_county.assign(geoid=hpi_county["geoid"] + "000100")
    weekly = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-04", "2024-01-11", "2025-01-02"]),
            "rate_30yr_pct": [6.5, 6.7, 7.0],
            "rate_15yr_pct": [5.9, 6.1, 6.2],
        }
    )
    qcew = pd.DataFrame(
        [
            ("US000", "nation", 2025, "", 8_000_000, 80_000),
            ("99000", "state", 2025, "", 10_000, 72_000),
            *[(c, "county", 2025, "", 2_000, 70_000 + 1000 * i) for i, c in enumerate(counties)],
        ],
        columns=[
            "area_fips",
            "level",
            "year",
            "disclosure_code",
            "annual_avg_emplvl",
            "avg_annual_pay",
        ],
    )
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    extract = bl.write_extract(sample_blocks(), raw / "blocks" / "x.csv.gz")
    return pack.PackInputs(
        state="TL",
        fips="99",
        acs_tracts=tracts,
        blocks=bl.read_extract(extract),
        county_shapes=sample_shapes("county"),
        tract_shapes=sample_shapes("tract"),
        permits=fixture_csv("permits_county_year"),
        hpi_county=hpi_county,
        hpi_tract=hpi_tract,
        pmms_weekly=weekly,
        qcew=qcew,
        input_files=[("tiger_2020_tabblock", extract)],
        cache=raw,
    )


def test_tables_follow_the_testland_schema(tmp_path):
    tables = pack.assemble_tables(make_inputs(tmp_path))
    for name, columns in pack.TESTLAND_COLUMNS.items():
        header = list(pd.read_csv(TESTLAND / f"{name}.csv", nrows=0).columns)
        assert list(columns) == header, name  # the fixed schema is Testland's, in order
        assert list(tables[name].columns[: len(header)]) == header, name
    assert tables["transit_stops"].empty
    # Place-profile and local tables are built only when their inputs were fetched.
    optional = set(pack.place_src.TABLES) | set(pack.local_src.TABLES)
    assert set(pack.TABLES) - optional <= set(tables) <= set(pack.TABLES)
    tl = fixture_csv("tracts").set_index("geoid")
    tr = tables["tracts"].set_index("geoid")
    assert (tr["land_area_m2"] == tl["land_area_m2"].reindex(tr.index)).sum() > 0
    assert set(tr["area_type"]) <= {"urban", "suburban", "rural"}
    assert tables["counties"]["housing_units"].sum() == tl["housing_units"].sum()


def test_dictionary_describes_every_column_with_a_tier(tmp_path):
    tables = pack.assemble_tables(make_inputs(tmp_path))
    d = pack.build_dictionary(tables, "TL")
    described = {(c["table"], c["name"]) for c in d["columns"]}
    for name, frame in tables.items():
        assert {(name, col) for col in frame.columns} <= described
    for c in d["columns"]:
        assert c["unit"] and c["description"] and c["source"] and c["tier"] in d["tiers"]
    assert {t["table"] for t in d["tables"]} == {*tables, "boundaries"}
    assert all(t["sources"] for t in d["tables"])


def test_two_builds_from_the_same_inputs_are_identical(tmp_path):
    a = pack.write_pack(make_inputs(tmp_path), tmp_path / "one", manifest=None)
    b = pack.write_pack(make_inputs(tmp_path), tmp_path / "two", manifest=None)
    assert a.pack_hash == b.pack_hash
    for f in a.files:
        assert (a.folder / f.path).read_bytes() == (b.folder / f.path).read_bytes(), f.path
    with gzip.open(a.folder / "blocks.csv.gz") as fh:
        assert fh.readline().decode().strip() == ",".join(bl.LITE_COLUMNS)
    manifest = json.loads((a.folder / "pack_manifest.json").read_text(encoding="utf-8"))
    assert manifest["pack_hash"] == a.pack_hash
    assert manifest["inputs"][0]["source"] == "tiger_2020_tabblock"
    assert {r["id"] for r in manifest["provenance"]} >= {"tiger_2020_tabblock", "freddie_pmms"}
    assert (a.folder / "boundaries" / "counties.geojson").exists()


def test_every_pack_id_needs_a_shape(tmp_path):
    inputs = make_inputs(tmp_path)
    inputs.tract_shapes = inputs.tract_shapes[1:]
    with pytest.raises(ValueError, match="without a shape"):
        pack.assemble_tables(inputs)


def test_over_budget_build_keeps_the_old_pack(tmp_path):
    good = pack.write_pack(make_inputs(tmp_path), tmp_path / "packs", manifest=None)
    with pytest.raises(ValueError, match="budget"):
        pack.write_pack(make_inputs(tmp_path), tmp_path / "packs", manifest=None, budget=1000)
    assert (good.folder / "pack_manifest.json").exists()
    assert not list((tmp_path / "packs").glob(".*building"))


def test_load_pack_and_size_report(tmp_path):
    report = pack.write_pack(make_inputs(tmp_path), tmp_path / "packs", manifest=None)
    tables = pack.load_pack(report.folder)
    assert tables["counties"]["geoid"].str.len().eq(5).all()
    text = pack.size_report(report)
    assert "total" in text and "within" in text and report.pack_hash in text


def test_cli_build_and_fetch_print_reports(tmp_path, monkeypatch, capsys):
    inputs = make_inputs(tmp_path)
    monkeypatch.setattr(pack, "load_inputs", lambda state, cache, **kw: inputs)
    assert cli.main(["build", "--state", "TL", "--raw", str(tmp_path / "raw"),
                     "--out", str(tmp_path / "packs")]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "State pack TL" in out and "budget" in out
    assert (tmp_path / "packs" / "TL" / "data_dictionary.json").exists()
    assert cli.main(["fetch", "--state", "TL", "--raw", str(tmp_path / "raw")]) == 0
    assert "tiger_2020_tabblock" in capsys.readouterr().out
