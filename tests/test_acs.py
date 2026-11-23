"""ACS summary files and API: streaming filter, codes, labels, features (no network)."""

from __future__ import annotations

import email.message
import io
import json
import math
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from visionhousing.data.http import FetchError, HttpClient
from visionhousing.data.sources import acs
from visionhousing.features import acs as fx
from visionhousing.features import brackets as br

SAMPLES = Path(__file__).resolve().parent / "fixtures" / "samples"
TESTLAND = SAMPLES.parent / "testland"
OTHER_STATE = b"0400000US41|"  # a row the filter must drop


class Response:
    def __init__(self, body: bytes, fail_after: int | None = None):
        self.status = 200
        self._buf = io.BytesIO(body)
        self.headers = email.message.Message()
        self.headers["Content-Length"] = str(len(body))
        self.fail_after = fail_after

    def read(self, n=-1):
        if self.fail_after is not None and self._buf.tell() >= self.fail_after:
            raise TimeoutError("read timed out")
        if self.fail_after is not None:
            n = min(n if n >= 0 else 10**12, self.fail_after - self._buf.tell())
        return self._buf.read(n)

    def close(self):
        pass


class Opener:
    """Serves bodies by URL path name (or by a function for API calls)."""

    def __init__(self, files=None, api=None, fail_first=()):
        self.files = files or {}
        self.api = api
        self.fail_first = set(fail_first)
        self.urls: list[str] = []

    def open(self, req, data=None, timeout=None):
        url = req.full_url
        self.urls.append(url)
        if self.api and url.startswith("https://api.census.gov"):
            return Response(self.api(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)))
        name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[1]
        body = self.files[name]
        if name in self.fail_first:
            self.fail_first.discard(name)
            return Response(body, fail_after=len(body) // 2)
        return Response(body)


def client(opener: Opener) -> HttpClient:
    return HttpClient(opener=opener, sleep=lambda s: None, free_bytes=lambda p: 10**13)


def national(body: bytes, other: bytes) -> bytes:
    """Make a 'national' file: the sample plus a row from another state, in the middle."""
    lines = body.splitlines(keepends=True)
    return b"".join([lines[0], other, *lines[1:]])


def national_files() -> dict[str, bytes]:
    files = {}
    for path in SAMPLES.glob("acsdt5y2024-*_sample.dat"):
        body = path.read_bytes()
        width = body.splitlines()[0].count(b"|")
        other = OTHER_STATE + b"|".join([b"5"] * width) + b"\n"
        files[path.name.replace("_sample", "")] = national(body, other)
    geos = (SAMPLES / "acs_geos_sample.txt").read_bytes()
    width = geos.splitlines()[0].count(b"|")
    cells = [b""] * (width + 1)
    cells[2], cells[3], cells[40] = b"040", b"00", b"0400000US41"
    files["Geos20245YR.txt"] = national(geos, b"|".join(cells) + b"\n")
    shells = (SAMPLES / "acs_shells_sample.txt").read_bytes()
    files["ACS20245YR_Table_Shells.txt"] = national(
        shells, b"B99999|1.0|0|B99999_001|Total:|Other|Other|int\n"
    )
    return files


def sample_raw() -> pd.DataFrame:
    parts = [acs.parse_geos(SAMPLES / "acs_geos_sample.txt").set_index("GEO_ID")]
    for path in sorted(SAMPLES.glob("acsdt5y2024-*_sample.dat")):
        parts.append(acs.parse_summary_table(path).set_index("GEO_ID"))
    out = pd.concat(parts, axis=1)
    out.index.name = "GEO_ID"
    return out.reset_index()


# ---------------------------------------------------------------- parsers


def test_summary_columns_renamed_and_codes_missing():
    table = acs.parse_summary_table(SAMPLES / "acsdt5y2024-b25031_sample.dat").set_index("GEO_ID")
    assert list(table.columns[:4]) == ["B25031_001E", "B25031_001M", "B25031_002E", "B25031_002M"]
    water = table.loc["1400000US42049990000"]
    assert water.isna().all()  # -666666666 and -222222222 codes
    assert (table.select_dtypes("number").fillna(0) >= 0).all().all()


def test_summary_unexpected_column_fails():
    with pytest.raises(ValueError, match="unexpected column"):
        acs.parse_summary_table("GEO_ID|B25031_X001\n0400000US42|1\n")


def test_geos_levels_and_ids():
    text = (SAMPLES / "acs_geos_sample.txt").read_text()
    extra = text.splitlines()[1].replace("|040|", "|060|").replace("0400000US42", "0600000US42x")
    geos = acs.parse_geos(text + extra + "\n")
    assert geos["level"].value_counts().to_dict() == {"tract": 3, "county": 2, "state": 1}
    assert set(geos.loc[geos.level == "tract", "geoid"].str.len()) == {11}


def test_table_shell_labels_pass_the_checks():
    labels = acs.parse_table_shells(SAMPLES / "acs_shells_sample.txt")
    assert labels["B25024_002E"] == "1, detached"
    assert acs.check_shell_labels(labels) == []


def test_label_checks_catch_renumbering():
    labels = acs.parse_table_shells(SAMPLES / "acs_shells_sample.txt")
    labels["B25024_002E"] = "1, attached"
    labels["B25075_027E"] = "$1,000,000 or more"
    problems = acs.check_shell_labels(labels)
    assert any("B25024_002E" in p for p in problems)
    assert any("B25075_027E" in p for p in problems)
    del labels["B25064_001E"]
    assert any("B25064_001E: missing" in p for p in acs.check_shell_labels(labels))


def test_api_rows_parse_like_summary_files():
    rows = [
        ["GEO_ID", "NAME", "B25064_001E", "B25064_001M", "state"],
        ["0400000US42", "Pennsylvania", "1209", "-555555555", "42"],
    ]
    table = acs.parse_api_rows(rows)
    assert list(table.columns) == ["GEO_ID", "NAME", "B25064_001E", "B25064_001M"]
    assert table.loc[0, "B25064_001E"] == 1209 and math.isnan(table.loc[0, "B25064_001M"])


def test_variable_list_and_batches():
    variables = acs.api_variables()
    assert len(variables) == len(set(variables)) == 2 * 143
    assert variables[:2] == ["B01001_001E", "B01001_001M"]


# ---------------------------------------------------------------- streaming


def test_stream_filter_keeps_only_the_state(tmp_path):
    files = national_files()
    c = client(Opener(files))
    dest = tmp_path / "b25024.dat"
    kept, seen = acs.stream_filter(
        c,
        "https://example.test/x/acsdt5y2024-b25024.dat",
        dest,
        acs._geo_id_keep(acs.geo_prefixes("42")),
    )
    assert kept == 6 and seen == len(files["acsdt5y2024-b25024.dat"])
    assert OTHER_STATE not in dest.read_bytes()
    assert dest.read_bytes() == (SAMPLES / "acsdt5y2024-b25024_sample.dat").read_bytes()


def test_stream_filter_on_geo_column_not_first(tmp_path):
    c = client(Opener(national_files()))
    dest = tmp_path / "geos.txt"
    kept, _ = acs.stream_filter(
        c, "https://example.test/Geos20245YR.txt", dest, acs._geo_id_keep(acs.geo_prefixes("42"))
    )
    assert kept == 6 and b"0400000US41" not in dest.read_bytes()


def test_stream_filter_restarts_after_a_dropped_connection(tmp_path):
    opener = Opener(national_files(), fail_first={"acsdt5y2024-b25031.dat"})
    c = client(opener)
    dest = tmp_path / "b25031.dat"
    acs.stream_filter(
        c,
        "https://example.test/acsdt5y2024-b25031.dat",
        dest,
        acs._geo_id_keep(acs.geo_prefixes("42")),
    )
    assert len(opener.urls) == 2 and c.waits == [1.0]
    assert not dest.with_name(dest.name + ".part").exists()
    assert dest.read_bytes() == (SAMPLES / "acsdt5y2024-b25031_sample.dat").read_bytes()


def test_stream_filter_without_geo_id_fails(tmp_path):
    c = client(Opener({"a.dat": b"X|Y\n1|2\n"}))
    with pytest.raises(FetchError, match="GEO_ID"):
        acs.stream_filter(c, "https://example.test/a.dat", tmp_path / "a", acs._geo_id_keep(("1",)))
    assert not (tmp_path / "a.part").exists()


# ---------------------------------------------------------------- fetch


def test_fetch_acs_summary_path(tmp_path):
    opener = Opener(national_files())
    raw = acs.fetch_acs("PA", 2024, tmp_path, client=client(opener), manifest=tmp_path / "m.json")
    assert raw["level"].value_counts().to_dict() == {"tract": 3, "county": 2, "state": 1}
    assert list(raw.columns[:4]) == ["GEO_ID", "level", "geoid", "name"]
    assert len(raw.columns) == 4 + 286
    assert "0400000US41" not in set(raw["GEO_ID"])
    n = len(opener.urls)
    assert n == 2 + len(acs.TABLES)  # shells, geos, one file per table
    acs.fetch_acs("PA", 2024, tmp_path, client=client(opener), manifest=None)
    assert len(opener.urls) == n  # filtered files were reused
    assert "acs5_geos" in (tmp_path / "m.json").read_text()


def test_fetch_acs_rejects_other_vintages_and_methods(tmp_path):
    with pytest.raises(ValueError, match="pinned to 2024"):
        acs.fetch_acs("PA", 2023, tmp_path, client=client(Opener()))
    with pytest.raises(ValueError, match="method"):
        acs.fetch_acs("PA", 2024, tmp_path, client=client(Opener()), method="ftp")


def test_fetch_acs_api_needs_a_key(tmp_path):
    c = client(Opener(national_files()))
    with pytest.raises(ValueError, match="CENSUS_API_KEY"):
        acs.fetch_acs("PA", 2024, tmp_path, client=c, method="api", env={}, manifest=None)


def test_fetch_acs_api_path_batches_and_matches_summary(tmp_path):
    summary = sample_raw().set_index("GEO_ID")
    calls = []

    def api(query):
        names = query["get"][0].split(",")
        calls.append((query["for"][0], len(names), query["key"][0]))
        level = query["for"][0].split(":")[0]
        code = {"state": "040", "county": "050", "tract": "140"}[level]
        rows = [names]
        for geo_id, row in summary.iterrows():
            if not geo_id.startswith(code):
                continue
            out = []
            for n in names:
                if n == "GEO_ID":
                    out.append(geo_id)
                elif n == "NAME":
                    out.append(row["name"])
                else:
                    v = row[n]
                    out.append("-666666666" if pd.isna(v) else str(int(v)))
            rows.append(out)
        return json.dumps(rows).encode()

    opener = Opener(national_files(), api=api)
    raw = acs.fetch_acs(
        "PA", 2024, tmp_path, client=client(opener), method="api",
        env={"CENSUS_API_KEY": "testkey"}, manifest=None,
    )  # fmt: skip
    assert all(n <= acs.API_BATCH for _, n, _ in calls)
    assert len(calls) == 3 * math.ceil(286 / 48)
    assert {k for _, _, k in calls} == {"testkey"}
    expected = sample_raw()
    expected = expected[raw.columns].sort_values(["level", "geoid"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(raw, expected, check_dtype=False)


# ---------------------------------------------------------------- features


def test_features_use_testland_column_names():
    features = fx.tract_features(sample_raw())
    testland = pd.read_csv(TESTLAND / "tracts.csv", nrows=1).columns
    from_acs = [c for c in testland if c not in ("area_type", "land_area_m2")]
    assert list(features.columns[: len(from_acs)]) == from_acs
    assert len(features) == 3


def test_feature_values_from_cells():
    raw = sample_raw()
    features = fx.tract_features(raw).set_index("geoid")
    tract = raw.set_index("geoid").loc["42001030901"]
    row = features.loc["42001030901"]
    assert row["units_1_detached"] == tract["B25024_002E"]
    assert row["units_20_plus"] == tract["B25024_008E"] + tract["B25024_009E"]
    assert row["units_20_plus_moe"] == pytest.approx(
        math.hypot(tract["B25024_008M"], tract["B25024_009M"])
    )
    assert row["vacancy_rate"] == pytest.approx(tract["B25002_003E"] / tract["B25002_001E"])
    older = sum(tract[f"B01001_{i:03d}E"] for i in (*range(20, 26), *range(44, 50)))
    assert row["share_age_65plus"] == pytest.approx(older / tract["B01001_001E"])
    assert 0 < row["share_age_65plus_moe"] < 1
    brackets = row[list(br.VALUE.columns)].sum()
    assert brackets == tract["B25075_001E"]
    assert row[[*br.RENT.columns, "rent_no_cash"]].sum() == tract["B25063_001E"]


def test_rent_by_bedrooms_methods():
    raw = sample_raw()
    features = fx.tract_features(raw).set_index("geoid")
    by_geo = raw.set_index("geoid")
    reported = features.loc["42001030901"]
    assert [reported[f"median_rent_b{k}_method"] for k in range(4)] == ["tract"] * 4
    assert reported["median_rent_b1"] == by_geo.loc["42001030901", "B25031_003E"]

    ratio = features.loc["42001030803"]
    county = by_geo.loc["42001"]
    assert ratio["median_rent_b0_method"] == "county_ratio"
    want = round(
        by_geo.loc["42001030803", "B25064_001E"] * county["B25031_002E"] / county["B25064_001E"]
    )
    assert ratio["median_rent_b0"] == want
    assert math.isnan(ratio["median_rent_b0_moe"])

    water = features.loc["42049990000"]
    assert [water[f"median_rent_b{k}_method"] for k in range(4)] == ["missing"] * 4
    assert np.isnan(water["median_rent_b2"]) and water["housing_units"] == 0


def test_county_rent_ratios():
    ratios = fx.county_rent_ratios(sample_raw())
    assert list(ratios.index) == ["42001", "42049"]
    assert ratios.loc["42001", "ratio_b2"] > ratios.loc["42001", "ratio_b1"]
