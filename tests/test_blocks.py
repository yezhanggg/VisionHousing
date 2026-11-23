"""Census blocks: the attribute table is read by range request, checked and cached (no network)."""

from __future__ import annotations

import pandas as pd
import pytest

from tests.pack_fakes import FileServer, block_dbf, client, sample_blocks, zip_of
from visionhousing.data.registry import get_source
from visionhousing.data.sources import blocks


def rows(frame: pd.DataFrame) -> list[dict]:
    return frame.to_dict("records")


def pa_rows() -> list[dict]:
    return [
        {
            "geoid": "420010301001001",
            "urban": 1,
            "land_area_m2": 10_000,
            "water_area_m2": 0,
            "lat": 39.8,
            "lon": -77.2,
            "housing_units": 20,
            "population": 41,
        },
        {
            "geoid": "420010301001000",
            "urban": 0,
            "land_area_m2": 0,
            "water_area_m2": 5_000,
            "lat": 39.81,
            "lon": -77.21,
            "housing_units": 0,
            "population": 0,
        },
    ]


def test_read_block_table_names_sorts_and_flags():
    out = blocks.read_block_table(block_dbf(pa_rows()), "42")
    assert list(out.columns) == list(blocks.EXTRACT_COLUMNS)
    assert list(out["geoid"]) == ["420010301001000", "420010301001001"]  # sorted
    first, second = out.iloc[0], out.iloc[1]
    assert second["tract"] == "42001030100" and second["county"] == "42001"
    assert second["urban"] == 1 and first["urban"] == 0
    assert second["housing_units"] == 20 and second["population"] == 41
    assert second["lat"] == pytest.approx(39.8) and second["lon"] == pytest.approx(-77.2)


def test_read_block_table_refuses_bad_input():
    with pytest.raises(ValueError, match="HOUSING20"):
        blocks.read_block_table(block_dbf(pa_rows(), drop="HOUSING20"))
    with pytest.raises(ValueError, match="outside state"):
        blocks.read_block_table(block_dbf(pa_rows()), "36")
    dup = pa_rows()
    dup[1]["geoid"] = dup[0]["geoid"]
    with pytest.raises(ValueError, match="repeats"):
        blocks.read_block_table(block_dbf(dup))
    far = pa_rows()
    far[0]["lat"] = 95.0
    with pytest.raises(ValueError, match="lat"):
        blocks.read_block_table(block_dbf(far))


def test_extract_round_trip_is_byte_stable(tmp_path):
    frame = blocks.read_block_table(block_dbf(rows(sample_blocks())))
    a = blocks.write_extract(frame, tmp_path / "a.csv.gz")
    b = blocks.write_extract(frame, tmp_path / "b.csv.gz")
    assert a.read_bytes() == b.read_bytes()
    back = blocks.read_extract(a)
    pd.testing.assert_frame_equal(back, frame, check_dtype=False)
    assert back["housing_units"].sum() == sample_blocks()["housing_units"].sum()


def test_fetch_reads_only_the_dbf_member_then_reuses_the_cache(tmp_path):
    source = get_source("tiger_2020_tabblock")
    url = source.url(state_fips="42")
    dbf = block_dbf(pa_rows())
    geometry = bytes(range(256)) * 4000  # 1 MB stand-in for the block shapes
    blob = zip_of({"tl_2020_42_tabblock20.shp": geometry, source.member_name(state_fips="42"): dbf})
    server = FileServer({url: blob})
    manifest = tmp_path / "manifest.json"
    frame, info = blocks.fetch_blocks("PA", tmp_path, client=client(server), manifest=manifest)
    assert len(frame) == 2 and not info.from_cache
    assert info.member_bytes == len(dbf) and info.zip_bytes == len(blob)
    assert server.sent < len(geometry) / 4  # only the index and the .dbf were transferred
    assert info.path.exists() and not list(tmp_path.rglob("*.dbf"))
    assert "tiger_2020_tabblock" in manifest.read_text(encoding="utf-8")

    server.urls.clear()
    again, info2 = blocks.fetch_blocks("PA", tmp_path, client=client(server), manifest=manifest)
    assert info2.from_cache and server.urls == []
    pd.testing.assert_frame_equal(again, frame, check_dtype=False)


def test_blocks_lite_drops_water_only_blocks_and_keeps_testland_columns():
    frame = blocks.read_block_table(block_dbf(pa_rows()))
    lite = blocks.blocks_lite(frame)
    assert list(lite.columns) == list(blocks.LITE_COLUMNS)
    assert list(lite["geoid"]) == ["420010301001001"]
