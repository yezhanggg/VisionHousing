"""Building Permits Survey parser: both header layouts, flags, state files, fetch (no network)."""

from __future__ import annotations

import email.message
import io
from pathlib import Path

import pandas as pd
import pytest

from visionhousing.data.http import HttpClient
from visionhousing.data.sources import bps, state_fips

SAMPLES = Path(__file__).resolve().parent / "fixtures" / "samples"


def sample(name: str) -> str:
    return (SAMPLES / name).read_bytes().decode("latin-1")


class Response:
    def __init__(self, body: bytes):
        self.status = 200
        self._buf = io.BytesIO(body)
        self.headers = email.message.Message()
        self.headers["Content-Length"] = str(len(body))

    def read(self, n=-1):
        return self._buf.read(n)

    def close(self):
        pass


class Opener:
    """Serves fixture files by the last part of the URL; records requested URLs."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.urls: list[str] = []

    def open(self, req, data=None, timeout=None):
        self.urls.append(req.full_url)
        return Response(self.files[req.full_url.rsplit("/", 1)[1]])


def client(files: dict[str, bytes]) -> HttpClient:
    return HttpClient(opener=Opener(files), sleep=lambda s: None, free_bytes=lambda p: 10**13)


# ---------------------------------------------------------------- header


def test_header_names_both_layouts():
    for name in ("bps_co1990a_sample.txt", "bps_co2024a_sample.txt"):
        lines = sample(name).splitlines()
        names = bps.header_names(bps._cells(lines[0]), bps._cells(lines[1]))
        names = [n for n in names if not n.startswith("_blank")]  # 1990 has a trailing comma
        assert names[:6] == [
            "survey_date",
            "state_fips",
            "county_fips",
            "region",
            "division",
            "name",
        ]
        assert names[6:9] == ["est_1_buildings", "est_1_units", "est_1_value"]
        assert names[15:18] == ["est_5+_buildings", "est_5+_units", "est_5+_value"]
        assert names[24:27] == ["rep_3-4_buildings", "rep_3-4_units", "rep_3-4_value"]
        assert names[-1] == "rep_5+_value" and len(names) == 30


def test_state_header_has_one_identity_column_fewer():
    lines = sample("bps_st2024a_sample.txt").splitlines()
    names = bps.header_names(bps._cells(lines[0]), bps._cells(lines[1]))
    assert names[:5] == ["survey_date", "state_fips", "region", "division", "name"]
    assert len(names) == 29


@pytest.mark.parametrize("label", ["34unit", "3-4 units", "3-4 units rep", "34-unit rep"])
def test_structure_labels(label):
    assert bps._structure(label)[0] == "3-4"
    assert bps._structure(label)[1] == label.endswith("rep")


def test_unknown_header_stops_the_run():
    with pytest.raises(ValueError, match="unknown column"):
        bps.header_names(["Survey", "Mystery"], ["Date", "Thing"])
    with pytest.raises(ValueError, match="missing or repeated"):
        bps.header_names(["Survey", "", "1-unit", ""], ["Date", "Bldgs", "Units", "Value"])
    with pytest.raises(ValueError, match="structure group"):
        bps._structure("6-units")


def test_survey_year_codes():
    assert bps.survey_year("9099") == 1990
    assert bps.survey_year("199999") == 1999
    assert bps.survey_year("2000") == 2000
    with pytest.raises(ValueError):
        bps.survey_year("20001")


# ---------------------------------------------------------------- county files


def test_1990_padded_layout():
    wide = bps.parse_county_annual(sample("bps_co1990a_sample.txt"), 1990)
    assert list(wide["geoid"]) == ["01001", "42001", "42003"]
    adams = wide.set_index("geoid").loc["42001"]
    assert adams["name"] == "Adams County"
    assert adams["est_1_units"] == 654 and adams["est_1_value"] == 45911670
    allegheny = wide.set_index("geoid").loc["42003"]
    assert allegheny["est_5+_units"] == 527 and allegheny["rep_1_units"] == 1851


def test_2024_layout_and_imputed_flag():
    table = bps.tidy(bps.parse_county_annual(sample("bps_co2024a_sample.txt"), 2024))
    row = table[(table.geoid == "42003") & (table.structure == "1")].iloc[0]
    assert (row.units, row.units_reported, row.units_imputed) == (1356, 1110, 246)
    assert bool(row.imputed)
    adams = table[table.geoid == "42001"]
    assert not adams["imputed"].any()
    assert list(adams["structure"]) == list(bps.STRUCTURES)
    assert list(table.columns) == list(bps.TIDY_COLUMNS)


def test_survey_date_must_match_the_file_year():
    with pytest.raises(ValueError, match="survey dates"):
        bps.parse_county_annual(sample("bps_co2024a_sample.txt"), 2023)


# ---------------------------------------------------------------- state files


def test_state_file_keeps_states_and_converts_value():
    wide = bps.parse_state_annual(sample("bps_st2024a_sample.txt"), 2024)
    assert list(wide["geoid"]) == ["42"]  # US and division rows dropped
    pa = wide.iloc[0]
    assert pa["est_1_units"] == 16348
    assert pa["est_1_value"] == 5352419 * 1000  # thousands -> dollars


def test_state_file_blank_territory_cells_are_missing():
    wide = bps.parse_state_annual(sample("bps_st2010a_sample.txt"), 2010).set_index("geoid")
    assert set(wide.index) == {"42", "43"}
    assert pd.isna(wide.loc["43", "rep_1_units"])
    tidy = bps.tidy(wide.reset_index())
    assert tidy["imputed"].dtype == bool


def test_1990_state_file():
    wide = bps.parse_state_annual(sample("bps_st1990a_sample.txt"), 1990).set_index("geoid")
    assert wide.loc["42", "est_1_units"] == 31037


# ---------------------------------------------------------------- outputs


def test_permits_county_year_matches_testland_columns():
    table = bps.tidy(bps.parse_county_annual(sample("bps_co2024a_sample.txt"), 2024))
    wide = bps.permits_county_year(table)
    testland = pd.read_csv(SAMPLES.parent / "testland" / "permits_county_year.csv", nrows=1)
    assert list(wide.columns) == list(testland.columns)
    row = wide.set_index("county").loc["42003"]
    assert (row.units_1, row.units_2, row.units_3_4, row.units_5plus, row.imputed) == (
        1356, 18, 22, 1603, 1,
    )  # fmt: skip


def test_county_state_check():
    county = bps.parse_county_annual(sample("bps_co2024a_sample.txt"), 2024)
    state = bps.parse_state_annual(sample("bps_st2024a_sample.txt"), 2024)
    table = bps.tidy(pd.concat([county[county.state_fips == "42"], state]))
    check = bps.county_state_check(table).set_index("structure")
    assert check.loc["1", "county_sum"] == 403 + 1356
    assert check.loc["1", "state_total"] == 16348
    assert check.loc["1", "difference"] == 403 + 1356 - 16348


# ---------------------------------------------------------------- fetch


def test_fetch_bps_downloads_once_and_filters_the_state(tmp_path):
    files = {
        "co2024a.txt": (SAMPLES / "bps_co2024a_sample.txt").read_bytes(),
        "st2024a.txt": (SAMPLES / "bps_st2024a_sample.txt").read_bytes(),
    }
    c = client(files)
    manifest = tmp_path / "manifest.json"
    table = bps.fetch_bps("PA", [2024], tmp_path, client=c, manifest=manifest)
    assert set(table["geoid"]) == {"42001", "42003", "42"}
    assert set(table["level"]) == {"county", "state"}
    assert (tmp_path / "bps" / "co2024a.txt").exists()
    assert "bps_state_annual" in manifest.read_text()
    again = bps.fetch_bps("42", [2024], tmp_path, client=c, manifest=manifest)
    assert len(c.opener.urls) == 2  # second call used the cache
    pd.testing.assert_frame_equal(table, again)


def test_fetch_bps_rejects_years_outside_the_files(tmp_path):
    with pytest.raises(ValueError, match="1990-2025"):
        bps.fetch_bps("PA", [1989], tmp_path, client=client({}), manifest=None)


def test_state_codes():
    assert state_fips("pa") == "42" and state_fips(42) == "42" and state_fips("6") == "06"
    with pytest.raises(ValueError):
        state_fips("ZZ")
