"""D7: re-running the local-tables add-on on a fully built pack touches only its own tables.

``visionhousing.data.local.add_to_pack`` adds the local tables to a built pack.
A pack that later add-on steps have extended (3A district tracts, 3A capacity,
tract crosswalks) must come out of a re-run with every other table, every
other file-list entry and every other step's inputs exactly as they were:

- tract outlines are read the way the full build reads them (the cached
  full-detail file first), so 3A district centre tracts do not move;
- a table whose content did not change is not rewritten;
- only the inputs of the tables written are replaced.

The first tests use a small hand-made pack (offline). The last one copies the
built Massachusetts pack when it is present and is skipped otherwise.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from visionhousing.data import local, pack

PACKS = Path(__file__).resolve().parents[1] / "data" / "packs"
RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


def _hashes(folder: Path) -> dict[str, str]:
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in folder.rglob("*")
        if p.is_file()
    }


def _gz(frame: pd.DataFrame, path: Path) -> None:
    pack._write_gz_csv(pack._tidy_numbers(frame), path)


def _districts() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "district_id": ["1", "2"],
            "municipality": ["Acton", "Acton"],
            "tract": ["25017000100"] * 2,
        }
    )


def _survey(approvals: int) -> pd.DataFrame:
    return pd.DataFrame({"muni_id": ["2"], "cohort": ["2025"], "approvals": [approvals]})


def _built_pack(root: Path) -> tuple[Path, Path]:
    """A small MA pack with a local table, an add-on table and inputs from both."""
    folder, cache = root / "packs" / "MA", root / "raw"
    (folder / "boundaries" / "tracts").mkdir(parents=True)
    (cache / "local" / "ma").mkdir(parents=True)
    for name, text in {
        "ma_adu_survey.csv": "old survey\n",
        "mbta3a_districts.geojson.gz": "atlas\n",
        "mbta3a_parcels.json.gz": "parcels\n",
    }.items():
        (cache / "local" / "ma" / name).write_text(text, encoding="utf-8")
    _gz(pd.DataFrame({"geoid": ["250170001001000"], "tract": ["25017000100"],
                      "housing_units": [5]}), folder / "blocks.csv.gz")  # fmt: skip
    _gz(_districts(), folder / "mbta_3a_districts.csv.gz")
    _gz(_survey(3), folder / "ma_adu_survey.csv.gz")
    _gz(pd.DataFrame({"district_id": ["1"], "tract": ["25017000100"]}),
        folder / "mbta_3a_district_tracts.csv.gz")  # fmt: skip
    (folder / "pack_manifest.json").write_text(
        json.dumps({"state": "MA", "state_fips": "25", "files": [], "inputs": []}),
        encoding="utf-8",
    )
    ma = cache / "local" / "ma"
    pack._rewrite(folder, cache, {}, [
        ("ma_adu_survey_file", ma / "ma_adu_survey.csv"),
        ("massgis_mbta3a_atlas", ma / "mbta3a_districts.geojson.gz"),
        ("massgis_mbta3a_parcels", ma / "mbta3a_parcels.json.gz"),  # the capacity step's input
    ])  # fmt: skip
    return folder, cache


def _fake_fetch(survey: pd.DataFrame, seen: dict):
    def fetch(state, cache, *, blocks, tract_shapes, **kw):
        seen["state"], seen["shapes"] = state, tract_shapes
        ma = Path(cache) / "local" / "ma"
        return local.LocalTables(
            tables={"mbta_3a_districts": _districts(), "ma_adu_survey": survey},
            files=[
                ("census_subcounty_pop", Path(cache) / "local" / "census" / "none.csv"),
                ("ma_adu_survey_file", ma / "ma_adu_survey.csv"),
                ("massgis_mbta3a_atlas", ma / "mbta3a_districts.geojson.gz"),
            ],
        )

    return fetch


def test_unchanged_local_tables_leave_the_pack_byte_identical(tmp_path, monkeypatch):
    folder, cache = _built_pack(tmp_path)
    before = _hashes(folder)
    monkeypatch.setattr(local, "fetch_local_tables", _fake_fetch(_survey(3), {}))
    got = local.add_to_pack(folder, cache, manifest=None)
    assert got.tables == {}
    assert _hashes(folder) == before


def test_a_changed_table_is_the_only_table_and_input_that_change(tmp_path, monkeypatch):
    folder, cache = _built_pack(tmp_path)
    before = _hashes(folder)
    old = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    (cache / "local" / "ma" / "ma_adu_survey.csv").write_text("new survey\n", encoding="utf-8")
    seen: dict = {}
    monkeypatch.setattr(local, "fetch_local_tables", _fake_fetch(_survey(4), seen))
    monkeypatch.setattr(pack, "_tract_shapes", lambda f, c, fips: [("full-detail", fips)])
    got = local.add_to_pack(folder, cache, manifest=None)
    assert sorted(got.tables) == ["ma_adu_survey"]
    assert seen["shapes"] == [("full-detail", "25")]  # outlines read as the full build reads them
    after = _hashes(folder)
    changed = sorted(k for k in after if before.get(k) != after[k])
    assert changed == [
        "ma_adu_survey.csv.gz",
        "pack_manifest.json",
    ]  # same columns: same dictionary
    new = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    files_old = {f["path"]: f for f in old["files"]}
    files_new = {f["path"]: f for f in new["files"]}
    assert set(files_new) == set(files_old)  # nothing dropped from the file list
    assert [k for k in files_new if files_new[k] != files_old[k]] == ["ma_adu_survey.csv.gz"]
    inputs_old = {r["source"]: r for r in old["inputs"]}
    inputs_new = {r["source"]: r for r in new["inputs"]}
    assert set(inputs_new) == set(inputs_old)  # the capacity step's parcels input is kept
    assert [s for s in inputs_new if inputs_new[s] != inputs_old[s]] == ["ma_adu_survey_file"]


def test_tables_names_the_only_tables_written(tmp_path, monkeypatch):
    folder, cache = _built_pack(tmp_path)
    before = _hashes(folder)
    changed = _districts().assign(tract="25017000200")
    seen: dict = {}

    def fetch(*a, **k):
        out = _fake_fetch(_survey(3), seen)(*a, **k)
        out.tables["mbta_3a_districts"] = changed
        return out

    monkeypatch.setattr(local, "fetch_local_tables", fetch)
    assert local.add_to_pack(folder, cache, manifest=None, tables=["ma_adu_survey"]).tables == {}
    assert _hashes(folder) == before


def _offline_client():
    from visionhousing.data.http import HttpClient

    class Refuse:
        def open(self, request, data=None, timeout=None):
            raise OSError("offline test: no request leaves the machine")

    return HttpClient(opener=Refuse(), retries=0, sleep=lambda s: None)


@pytest.mark.realdata
@pytest.mark.skipif(not (PACKS / "MA" / "pack_manifest.json").is_file(), reason="no MA pack")
def test_rerun_on_the_built_massachusetts_pack_keeps_every_other_table(tmp_path):
    folder = tmp_path / "MA"
    shutil.copytree(PACKS / "MA", folder)
    before = _hashes(folder)
    old = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    got = local.add_to_pack(folder, RAW, client=_offline_client(), manifest=None)
    owned = {f"{n}.csv.gz" for n in local.TABLES}
    after = _hashes(folder)
    changed = {k for k in after if before.get(k) != after[k]}
    assert set(before) == set(after)
    assert changed <= owned | {"data_dictionary.json", "pack_manifest.json"}
    assert {f"{n}.csv.gz" for n in got.tables} == changed - {
        "data_dictionary.json",
        "pack_manifest.json",
    }
    for name in ("mbta_3a_districts", "ma_adu_survey"):  # centre tracts did not move
        assert f"{name}.csv.gz" not in changed
    new = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    files_old = {f["path"]: f for f in old["files"]}
    files_new = {f["path"]: f for f in new["files"]}
    assert set(files_new) == set(files_old)
    for path, entry in files_old.items():
        if path not in changed:
            assert files_new[path] == entry
    inputs = {(r["source"], r["file"]) for r in new["inputs"]}
    assert {(r["source"], r["file"]) for r in old["inputs"]} <= inputs


def test_massachusetts_gets_the_town_table_only(tmp_path, monkeypatch):
    """D7: MA towns are county subdivisions; the step writes tract_municipalities, not places."""
    from visionhousing.data.sources import munstats

    towns, places = pack.crosswalk_states()
    assert "MA" in towns and "MA" not in places
    assert {"VT", "WY"} <= set(places)
    folder, cache = _built_pack(tmp_path)
    asked: list[str] = []

    def cousubs(state, cache, **kw):
        asked.append("mcd")
        return pd.DataFrame({"block": ["250170001001000"], "cousub": ["00380"]})

    def no_places(*a, **k):
        raise AssertionError("Massachusetts needs no tract_places")

    monkeypatch.setattr(munstats, "fetch_block_subdivisions", cousubs)
    monkeypatch.setattr(munstats, "fetch_block_places", no_places)
    before = _hashes(folder)
    written = pack.add_tract_crosswalks(folder, cache, manifest=None)
    assert sorted(written) == ["tract_municipalities"] and asked == ["mcd"]
    t = written["tract_municipalities"]
    assert t.to_dict("list") == {"tract": ["25017000100"], "county": ["25017"],
                                 "cousub": ["00380"], "housing_units_2020": [5]}  # fmt: skip
    after = _hashes(folder)
    assert {k for k in before if before[k] != after[k]} <= {"data_dictionary.json",
                                                           "pack_manifest.json"}  # fmt: skip
    assert "tract_municipalities.csv.gz" in after
    assert pack.add_tract_crosswalks(folder, cache, states=("VT",)) == {}  # not acted on
