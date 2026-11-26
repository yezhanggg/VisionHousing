"""The place-profile tables join the state pack: built from inputs, described in the dictionary."""

from __future__ import annotations

import pandas as pd

from tests import test_place_incentives as inc
from tests import test_place_jobs as jobs
from tests import test_place_safety as safety
from tests.test_pack import make_inputs
from visionhousing.data import pack
from visionhousing.data.sources import place

PLACE_TABLES = {
    "jobs_tract",
    "jobs_county",
    "jobs_county_sector",
    "incentives_tract",
    "incentives_county",
    "safety_county",
}


def with_place(tmp_path) -> pack.PackInputs:
    inputs = make_inputs(tmp_path)
    inputs.place = place.PlaceInputs(
        wac=jobs.wac(),
        od=jobs.od(),
        cbp=jobs.cbp(),
        incentives=inc.inputs(),
        safety=safety.agencies(),
    )
    return inputs


def test_pack_without_place_inputs_has_no_place_tables(tmp_path):
    assert not PLACE_TABLES & set(pack.assemble_tables(make_inputs(tmp_path)))


def test_place_tables_join_the_pack_and_the_dictionary(tmp_path):
    tables = pack.assemble_tables(with_place(tmp_path))
    assert set(tables) >= PLACE_TABLES
    tract_ids = set(tables["tracts"]["geoid"])
    assert set(tables["jobs_tract"]["tract"]) == tract_ids
    assert set(tables["incentives_tract"]["tract"]) == tract_ids
    assert set(tables["safety_county"]["county"]) == set(tables["counties"]["geoid"])
    dictionary = pack.build_dictionary(tables, "TL")
    cols = {(c["table"], c["name"]): c for c in dictionary["columns"]}
    for name in PLACE_TABLES:
        for col in tables[name].columns:
            entry = cols[(name, col)]
            assert entry["description"] and entry["unit"] and entry["source"], (name, col)
    assert cols[("incentives_tract", "koz_sites")]["tier"] == "2"
    assert cols[("incentives_tract", "qct")]["source"] == "hud_qct"
    assert cols[("jobs_county", "inflow")]["source"] == "lodes_od"
    listed = {t["table"] for t in dictionary["tables"]}
    assert listed >= PLACE_TABLES


def test_place_tables_need_registered_sources():
    from visionhousing.data.registry import load_registry

    ids = set(load_registry())
    for spec in place.TABLES.values():
        assert set(spec["sources"]) <= ids  # type: ignore[arg-type]
    for _, _, source in place.COLUMNS.values():
        assert source in ids


def test_place_tables_with_nothing_fetched_are_empty():
    t = pd.DataFrame({"geoid": ["99001000100"]})
    c = pd.DataFrame({"geoid": ["99001"], "name": ["Harbor County"], "population": [1]})
    assert place.place_tables(None, t, c, []) == {}
    assert place.place_tables(place.PlaceInputs(), t, c, []) == {}


def test_retrofit_adds_jobs_and_incentive_tables_like_a_full_build(tmp_path):
    import json

    from visionhousing.data.http import sha256_file

    bare = pack.write_pack(make_inputs(tmp_path), tmp_path / "bare", manifest=None)
    assert not PLACE_TABLES & {f.path[: -len(".csv.gz")] for f in bare.files}
    full = pack.write_pack(with_place(tmp_path), tmp_path / "full", manifest=None).folder
    written = pack.add_profile_tables(
        bare.folder, tmp_path / "cache", manifest=None, place=with_place(tmp_path).place
    )
    assert set(written) >= PLACE_TABLES | {"typology_county", "typology_tract"}
    tables = pack.load_pack(bare.folder)
    for name in PLACE_TABLES | {"typology_county", "typology_tract"}:
        file = f"{name}.csv.gz"
        assert (bare.folder / file).read_bytes() == (full / file).read_bytes(), name
    assert "jobs_per_resident" in tables["typology_tract"].columns
    assert {"jobs_per_resident", "qct_share"} <= set(tables["typology_county"].columns)
    manifest = json.loads((bare.folder / "pack_manifest.json").read_text(encoding="utf-8"))
    listed = {f["path"]: f for f in manifest["files"]}
    for name in PLACE_TABLES:
        path = bare.folder / f"{name}.csv.gz"
        assert listed[path.name]["sha256"] == sha256_file(path)
        assert listed[path.name]["rows"] == len(tables[name])
    assert manifest["pack_hash"] != bare.pack_hash
    dictionary = json.loads((bare.folder / "data_dictionary.json").read_text(encoding="utf-8"))
    assert {t["table"] for t in dictionary["tables"]} >= PLACE_TABLES
