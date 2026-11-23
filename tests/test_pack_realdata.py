"""Real-data checks for Pennsylvania: block totals, read volume, shape coverage, pack budget.

Runs against the live published files (the state's extracts are cached in
``data/raw``). Not part of ``make test``; run it with ``make test-realdata``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from visionhousing.data import pack
from visionhousing.data.http import HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources import blocks

pytestmark = pytest.mark.realdata

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
PA_HOUSING_UNITS_2020 = 5_742_828  # 2020 Census, Pennsylvania, total housing units
PA_BLOCKS_2020 = 336_985


@pytest.fixture(scope="module")
def inputs():
    return pack.load_inputs("PA", RAW)


@pytest.fixture(scope="module")
def built(inputs, tmp_path_factory):
    out = tmp_path_factory.mktemp("packs")
    first = pack.write_pack(inputs, out / "one")
    second = pack.write_pack(inputs, out / "two")
    print(pack.size_report(first))
    return first, second


def test_block_housing_units_equal_the_2020_census_count(inputs):
    assert len(inputs.blocks) == PA_BLOCKS_2020
    assert int(inputs.blocks["housing_units"].sum()) == PA_HOUSING_UNITS_2020


def test_only_a_small_part_of_the_block_zip_is_read():
    source = get_source("tiger_2020_tabblock")
    url = source.url(state_fips="42")
    client = HttpClient()
    members = client.remote_zip_members(url)
    share = members[source.member_name(state_fips="42")].compressed_size / client.remote_size(url)
    print(f"block attribute table: {share:.1%} of the zip")
    assert share < 0.15


def test_pack_totals_and_shape_coverage(built):
    report, _ = built
    tables = pack.load_pack(report.folder)
    assert int(tables["blocks"]["housing_units"].sum()) == PA_HOUSING_UNITS_2020
    assert int(tables["tracts"]["housing_units_2020"].sum()) == PA_HOUSING_UNITS_2020
    assert len(tables["counties"]) == 67

    shapes = report.folder / "boundaries"
    counties = json.loads((shapes / "counties.geojson").read_text(encoding="utf-8"))
    county_ids = {f["properties"]["geoid"] for f in counties["features"]}
    assert set(tables["counties"]["geoid"]) == county_ids
    tract_ids = set()
    for path in (shapes / "tracts").glob("*.geojson"):
        data = json.loads(path.read_text(encoding="utf-8"))
        tract_ids |= {f["properties"]["geoid"] for f in data["features"]}
    assert set(tables["tracts"]["geoid"]) <= tract_ids
    assert len(tables["tracts"]) >= 3440


def test_size_budgets_and_determinism(built):
    first, second = built
    assert first.total_bytes < pack.PACK_BUDGET_BYTES
    county_file = first.folder / "boundaries" / "counties.geojson"
    assert county_file.stat().st_size <= 300_000
    assert first.pack_hash == second.pack_hash
    assert [f.sha256 for f in first.files] == [f.sha256 for f in second.files]


def test_extract_is_cached_and_small():
    path = blocks.extract_path(RAW, "42")
    assert path.exists() and path.stat().st_size < 10_000_000
