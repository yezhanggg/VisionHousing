"""Profile charts, summary points and county ranks; the ACS bands behind them (no network)."""

from __future__ import annotations

import gzip
import json
import random
import shutil
import urllib.parse

import numpy as np
import pandas as pd
import pytest

from tests.test_acs import SAMPLES, Opener, client, national
from tests.test_pack import make_inputs
from visionhousing.data import pack
from visionhousing.data.sources import acs
from visionhousing.features import acs_bands as ab
from visionhousing.profile import build_profile, load_profile_context, render
from visionhousing.profile.charts import items_from_counts, share_of
from visionhousing.profile.model import CHART_KINDS
from visionhousing.profile.ranks import county_ranks, ordinal, rank_values

TESTLAND = SAMPLES.parent / "testland"

# --------------------------------------------------------------- synthetic ACS cells

# The ACS table structure, written down independently of the band definitions:
# leaf cells, and each parent cell as the sum of its children.
LEAVES = {
    "B01001": [*range(3, 26), *range(27, 50)],
    "B03002": [3, 4, 5, 6, 7, 8, 10, 11, 13, 14, 15, 16, 17, 18, 20, 21],
    "B08301": [3, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
    "B11016": [3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 16],
    "B15003": list(range(2, 26)),
    "B19001": list(range(2, 18)),
    "B25003": [2, 3],
    "B25004": list(range(2, 9)),
    "B25024": list(range(2, 12)),
    "B25034": list(range(2, 12)),
    "B25070": list(range(2, 12)),
}
PARENTS = {
    "B01001": {2: range(3, 26), 26: range(27, 50), 1: (2, 26)},
    "B03002": {9: (10, 11), 19: (20, 21), 12: range(13, 20), 2: range(3, 10), 1: (2, 12)},
    "B08301": {4: range(5, 10), 2: (3, 4), 10: range(11, 16), 1: (2, 10, *range(16, 22))},
    "B11016": {2: range(3, 9), 9: range(10, 17), 1: (2, 9)},
}
GEO_IDS = ["0400000US42", "0500000US42001", "0500000US42049",
           "1400000US42001030803", "1400000US42001030901", "1400000US42049990000"]  # fmt: skip


def raw_cells(seed: int = 7) -> pd.DataFrame:
    """Raw API-style cells for a state, two counties and three tracts."""
    rng = random.Random(seed)  # noqa: S311 - test data, not security
    rows = []
    for geo in GEO_IDS:
        level = {"04": "state", "05": "county", "14": "tract"}[geo[:2]]
        row: dict[str, object] = {"GEO_ID": geo, "level": level, "geoid": geo.split("US")[1]}
        scale = {"state": 400, "county": 60, "tract": 3}[level]
        for table, leaves in LEAVES.items():
            cells = {c: float(rng.randint(0, 50) * scale) for c in leaves}
            for parent, kids in PARENTS.get(table, {}).items():
                cells[parent] = sum(cells[k] for k in kids)
            if 1 not in cells:
                cells[1] = sum(cells[c] for c in leaves)
            for c, v in cells.items():
                row[f"{table}_{c:03d}E"] = v
                row[f"{table}_{c:03d}M"] = float(rng.randint(5, 90))
        row["B19013_001E"], row["B19013_001M"] = 60000.0 + rng.randint(0, 9000), 900.0
        row["B25064_001E"], row["B25064_001M"] = 1000.0 + rng.randint(0, 300), 25.0
        rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------- bands


def test_bands_cover_each_universe_and_sum_to_the_published_total():
    raw = raw_cells()
    table = ab.band_table(raw, tract_groups=tuple(g.key for g in ab.GROUPS))
    for g in ab.GROUPS:
        bands = sum(table[g.column(b.key)] for b in g.bands)
        assert np.allclose(bands, table[g.column("total")]), g.key
    # Every leaf cell is in exactly one band.
    for g in ab.GROUPS:
        used = [c for b in g.bands for c in b.cells]
        assert len(used) == len(set(used)), g.key


def test_band_margins_follow_the_bureau_rule():
    raw = raw_cells()
    table = ab.band_table(raw)
    first = raw.iloc[0]
    g = ab.group("income")
    band = g.bands[0]  # several cells
    want = np.sqrt(sum(first[f"B19001_{c:03d}M"] ** 2 for c in band.cells))
    assert table.loc[0, f"{g.column(band.key)}_moe"] == round(want)
    single = ab.group("tenure").bands[0]
    assert table.loc[0, "tenure_owner_moe"] == first[f"B25003_{single.cells[0]:03d}M"]


def test_tract_rows_keep_only_the_tract_groups():
    table = ab.band_table(raw_cells())
    tracts = table[table["level"] == "tract"]
    counties = table[table["level"] == "county"]
    for g in ab.GROUPS:
        kept = g.key in ab.TRACT_GROUPS
        assert tracts[g.columns()].notna().all().all() == kept, g.key
        assert counties[g.columns()].notna().all().all(), g.key
    assert tracts["median_household_income"].isna().all()
    assert counties["median_gross_rent"].notna().all()
    assert list(table["geoid"]) == sorted(table["geoid"])  # state, counties, tracts


def test_a_missing_cell_blanks_its_band_only():
    raw = raw_cells()
    raw.loc[1, "B19001_017E"] = np.nan
    table = ab.band_table(raw)
    assert pd.isna(table.loc[1, "income_150k_plus"])
    assert table.loc[1, "income_lt_25k"] > 0


def test_testland_bands_add_up():
    table = pd.read_csv(TESTLAND / "acs_bands.csv", dtype={"geoid": str})
    tracts = pd.read_csv(TESTLAND / "tracts.csv", dtype={"geoid": str, "county": str})
    for g in ab.GROUPS:
        rows = table if g.key in ab.TRACT_GROUPS else table[table["level"] != "tract"]
        bands = sum(rows[g.column(b.key)] for b in g.bands)
        assert np.allclose(bands, rows[g.column("total")]), g.key
    t = table[table["level"] == "tract"].set_index("geoid")
    pop = tracts.set_index("geoid")["population"]
    assert (t["age_total"] == pop.reindex(t.index)).all()
    county = table[table["level"] == "county"].set_index("geoid")
    sums = t.groupby(t.index.str[:5])["age_total"].sum()
    assert (county["age_total"] == sums.reindex(county.index)).all()


def test_dictionary_describes_every_band_column():
    for name in ["geoid", "level", *ab.band_columns(), *(f"{c}_moe" for c in ab.band_columns())]:
        unit, text = ab.describe(name)
        assert unit and text
    with pytest.raises(ValueError):
        ab.describe("nope")


# --------------------------------------------------------------- fetching: keyless and API


def _shells() -> bytes:
    lines = ["Table ID|Line|Indent|Unique ID|Label|Title|Universe|Type"]
    for table, labels in acs.PROFILE_LABELS.items():
        for i, label in enumerate(labels, start=1):
            text = label or "Light rail, streetcar or trolley"
            lines.append(f"{table}|{i}.0|0|{table}_{i:03d}|{text}:|T|U|int")
    return ("\n".join(lines) + "\n").encode()


def _national_profile_files(raw: pd.DataFrame) -> dict[str, bytes]:
    files = {}
    for table in acs.PROFILE_TABLES:
        n = len(acs.PROFILE_LABELS[table])
        head = ["GEO_ID"] + [f"{table}_{k}{i:03d}" for i in range(1, n + 1) for k in "EM"]
        rows = ["|".join(head)]
        for _, r in raw.iterrows():
            cells = [r["GEO_ID"]]
            for i in range(1, n + 1):
                for k in "EM":
                    v = r.get(f"{table}_{i:03d}{k}", 1.0)
                    cells.append(str(int(v)) if pd.notna(v) else "-666666666")
            rows.append("|".join(cells))
        body = ("\n".join(rows) + "\n").encode()
        other = ("0400000US41|" + "|".join(["5"] * (len(head) - 1)) + "\n").encode()
        files[f"acsdt5y2024-{table.lower()}.dat"] = national(body, other)
    geos = (SAMPLES / "acs_geos_sample.txt").read_bytes()
    files["Geos20245YR.txt"] = geos
    files["ACS20245YR_Table_Shells.txt"] = _shells()
    return files


def _api(raw: pd.DataFrame):
    indexed = raw.set_index("GEO_ID")

    def answer(query):
        names = query["get"][0].split(",")
        level = query["for"][0].split(":")[0]
        code = {"state": "040", "county": "050", "tract": "140"}[level]
        rows = [names]
        for geo, r in indexed.iterrows():
            if not geo.startswith(code):
                continue
            out = []
            for n in names:
                if n == "GEO_ID":
                    out.append(geo)
                elif n == "NAME":
                    out.append("x")
                else:
                    v = r.get(n, 1.0)
                    out.append("-666666666" if pd.isna(v) else str(int(v)))
            rows.append(out)
        return json.dumps(rows).encode()

    return answer


def test_keyless_path_is_the_default_without_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    assert acs.choose_method() == "summary"
    assert acs.choose_method("auto", {"CENSUS_API_KEY": "  "}) == "summary"
    assert acs.choose_method("auto", {"CENSUS_API_KEY": "k"}) == "api"
    with pytest.raises(ValueError):
        acs.choose_method("ftp")
    raw = raw_cells()
    opener = Opener(_national_profile_files(raw))
    out = acs.fetch_acs_profile("PA", tmp_path, client=client(opener), manifest=tmp_path / "m.json")
    assert not any(urllib.parse.urlsplit(u).hostname == "api.census.gov" for u in opener.urls)
    assert out["level"].value_counts().to_dict() == {"tract": 3, "county": 2, "state": 1}
    assert "0400000US41" not in set(out["GEO_ID"])
    assert list(out.columns[3:]) == acs.profile_variables()
    assert "acs5_profile_tables" in (tmp_path / "m.json").read_text()
    n = len(opener.urls)
    again = acs.fetch_acs_profile("PA", tmp_path, client=client(opener), manifest=None)
    assert len(opener.urls) == n  # the saved extract is read, no network
    pd.testing.assert_frame_equal(out, again)


def test_api_path_gives_identical_cells(tmp_path):
    raw = raw_cells()
    files = _national_profile_files(raw)
    summary = acs.fetch_acs_profile(
        "PA", tmp_path / "s", client=client(Opener(files)), method="summary", env={},
        manifest=None,
    )  # fmt: skip
    opener = Opener(files, api=_api(raw))
    api = acs.fetch_acs_profile(
        "PA", tmp_path / "a", client=client(opener), env={"CENSUS_API_KEY": "testkey"},
        manifest=None,
    )  # fmt: skip
    assert any(urllib.parse.urlsplit(u).hostname == "api.census.gov" for u in opener.urls)
    for path in (tmp_path / "a").rglob("*"):
        assert "testkey" not in urllib.parse.unquote(str(path))
        if path.is_file():
            data = path.read_bytes()
            text = gzip.decompress(data) if path.suffix == ".gz" else data
            assert b"testkey" not in text
    pd.testing.assert_frame_equal(summary, api)
    assert (tmp_path / "s" / "acs" / "2024" / "acs5_profile_42.csv.gz").read_bytes() == (
        tmp_path / "a" / "acs" / "2024" / "acs5_profile_42.csv.gz"
    ).read_bytes()


def test_api_path_without_a_key_fails_clearly(tmp_path):
    files = _national_profile_files(raw_cells())
    with pytest.raises(ValueError, match="CENSUS_API_KEY"):
        acs.fetch_acs_profile(
            "PA", tmp_path, client=client(Opener(files)), method="api", env={}, manifest=None
        )


def test_profile_labels_are_checked():
    labels = acs.parse_table_shells(_shells().decode(), acs.PROFILE_TABLES)
    assert acs.check_profile_labels(labels) == []
    labels["B25070_011E"] = "Something else"
    assert any("B25070_011E" in p for p in acs.check_profile_labels(labels))


# --------------------------------------------------------------- pack


def test_bands_join_the_pack_and_the_retrofit_matches_a_full_build(tmp_path):
    rows = pd.read_csv(TESTLAND / "acs_bands.csv", dtype={"geoid": str})
    bare = pack.write_pack(make_inputs(tmp_path), tmp_path / "bare", manifest=None)
    assert not (bare.folder / "acs_bands.csv.gz").exists()
    inputs = make_inputs(tmp_path)
    inputs.acs_bands = rows
    full = pack.write_pack(inputs, tmp_path / "full", manifest=None).folder
    pack.add_acs_bands(bare.folder, tmp_path / "raw", manifest=None, rows=rows)
    name = "acs_bands.csv.gz"
    assert (bare.folder / name).read_bytes() == (full / name).read_bytes()
    dictionary = json.loads((bare.folder / "data_dictionary.json").read_text(encoding="utf-8"))
    described = {c["name"] for c in dictionary["columns"] if c["table"] == "acs_bands"}
    assert described == set(pack.load_pack(bare.folder)["acs_bands"].columns)
    manifest = json.loads((bare.folder / "pack_manifest.json").read_text(encoding="utf-8"))
    assert name in {f["path"] for f in manifest["files"]}


# --------------------------------------------------------------- charts and summaries


@pytest.fixture(scope="module")
def ctx():
    return load_profile_context("TL")


def _charts(profile):
    return [c for s in profile.sections for c in s.charts]


@pytest.mark.parametrize("geoid", ["99001", "99007", "99001000100", "99007000300"])
def test_chart_shares_add_up_to_one(ctx, geoid):
    profile = build_profile("TL", geoid, ctx=ctx)
    charts = _charts(profile)
    assert charts
    for c in charts:
        assert c.kind in CHART_KINDS
        shares = [i.share for i in c.items]
        assert all(s is not None and 0 <= s <= 1 for s in shares), c.id
        assert sum(shares) == pytest.approx(1, abs=0.0001 * len(shares)), c.id
        assert c.total == pytest.approx(sum(i.value for i in c.items))
        for i in c.items:
            assert i.share == round(i.value / c.total, 4)
        assert c.compare is not None and c.compare.items
        other = [i.share for i in c.compare.items]
        assert sum(other) == pytest.approx(1, abs=0.0001 * len(other)), c.id
        assert [i.label for i in c.compare.items] == [i.label for i in c.items]
        assert c.source_ids and c.vintage and c.note


def test_county_and_tract_chart_sets(ctx):
    county = {c.id for c in _charts(build_profile("TL", "99001", ctx=ctx))}
    assert {"people.age", "people.race", "people.income", "people.household_size",
            "people.education", "housing.tenure", "housing.building_type",
            "housing.year_built", "housing.rent_burden",
            "housing.vacancy_status"} <= county  # fmt: skip
    tract = {c.id for c in _charts(build_profile("TL", "99001000100", ctx=ctx))}
    assert {"people.age", "people.race", "people.income", "housing.tenure",
            "housing.building_type", "housing.rent_burden"} <= tract  # fmt: skip
    assert "people.education" not in tract and "housing.vacancy_status" not in tract


def test_rent_burden_chart_leaves_out_not_computed(ctx):
    housing = build_profile("TL", "99001", ctx=ctx).section("housing")
    chart = next(c for c in housing.charts if c.id == "housing.rent_burden")
    assert chart.kind == "stack"
    assert [i.key for i in chart.items] == ["lt_30", "30_49", "50_plus"]
    bands = pd.read_csv(TESTLAND / "acs_bands.csv", dtype={"geoid": str}).set_index("geoid")
    row = bands.loc["99001"]
    burdened = row["burden_30_49"] + row["burden_50_plus"]
    fact = housing.fact("housing.rent_burden_share")
    assert fact.value == pytest.approx(burdened / (burdened + row["burden_lt_30"]))
    assert fact.moe is not None and fact.moe > 0


def test_tract_building_types_come_from_the_tract_table(ctx):
    housing = build_profile("TL", "99001000100", ctx=ctx).section("housing")
    chart = next(c for c in housing.charts if c.id == "housing.building_type")
    tract = pd.read_csv(TESTLAND / "tracts.csv", dtype={"geoid": str}).set_index("geoid")
    row = tract.loc["99001000100"]
    assert chart.total == row["housing_units"]
    two_four = next(i for i in chart.items if i.key == "2_4")
    assert two_four.value == row["units_2"] + row["units_3_4"]
    assert "acs5_summary_file" in chart.source_ids


def test_headline_and_summary_points(ctx):
    profile = build_profile("TL", "99001", ctx=ctx)
    people = profile.section("people")
    assert people.headline is not None and people.headline.fact == "people.population"
    assert people.headline.text == people.fact("people.population").text
    for s in profile.sections:
        assert len(s.summary_points) <= 3
        assert all(isinstance(p, str) and p for p in s.summary_points)
    assert any("Largest age group" in p for p in people.summary_points)


def test_published_county_median_is_used(ctx):
    people = build_profile("TL", "99001", ctx=ctx).section("people")
    bands = pd.read_csv(TESTLAND / "acs_bands.csv", dtype={"geoid": str}).set_index("geoid")
    fact = people.fact("people.median_household_income")
    assert fact.value == bands.loc["99001", "median_household_income"]
    assert fact.comparison.value == bands.loc["99", "median_household_income"]
    assert "published" in fact.note


def test_profile_without_bands_still_builds(tmp_path):
    folder = tmp_path / "TL"
    shutil.copytree(TESTLAND, folder)
    (folder / "acs_bands.csv").unlink()
    ctx = load_profile_context("TL", folder=folder)
    profile = build_profile("TL", "99001", ctx=ctx)
    ids = {c.id for c in _charts(profile)}
    assert not ids
    assert "approximate" in profile.section("people").fact("people.median_household_income").note


def test_json_and_renderers_carry_charts_and_ranks(ctx):
    profile = build_profile("TL", "99001", ctx=ctx)
    data = json.loads(json.dumps(profile.to_dict(), allow_nan=False))
    chart = data["sections"][0]["charts"][0]
    assert set(chart) == {"id", "kind", "title", "unit", "items", "total", "compare",
                          "source_ids", "vintage", "note"}  # fmt: skip
    assert set(chart["items"][0]) == {"key", "label", "value", "share", "moe"}
    assert set(chart["compare"]) == {"label", "items"}
    assert set(chart["compare"]["items"][0]) == {"key", "share"}
    assert isinstance(chart["items"][0]["value"], int)
    assert data["ranks"] and set(data["ranks"][0]) >= {"rank", "of", "text_rank", "words"}
    assert "headline" in data["sections"][0] and "summary_points" in data["sections"][0]
    md = render(profile, "md")
    assert "Rank among the state's counties" in md and "Residents by age" in md
    html = render(profile, "html")
    assert 'class="bar"' in html and "<script" not in html
    for src in {s for c in _charts(profile) for s in c.source_ids}:
        assert src in profile.sources


# --------------------------------------------------------------- ranks


def test_rank_values_ties_share_the_best_rank():
    values = {"a": 1.0, "b": 2.0, "c": 2.0, "d": None, "e": 3.0, "f": float("nan")}
    ranked = rank_values(values)
    assert ranked == {
        "e": (1, 4, False),
        "b": (2, 4, True),
        "c": (2, 4, True),
        "a": (4, 4, False),
    }


def test_rank_values_ignore_row_order_and_float_noise():
    values = {"x": 0.1 + 0.2, "y": 0.3, "z": 0.5}
    first = rank_values(values)
    shuffled = dict(reversed(list(values.items())))
    assert rank_values(shuffled) == first
    assert first["x"] == first["y"] == (2, 3, True)


def test_ordinal():
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 67, 101, 111)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "67th",
        "101st", "111th",
    ]  # fmt: skip


def test_county_ranks_are_deterministic_and_worded(ctx):
    ranks = county_ranks(ctx, "99001")
    again = county_ranks(load_profile_context("TL"), "99001")
    assert ranks == again
    ids = [r.id for r in ranks]
    assert "rank.median_household_income" in ids and "rank.permits_per_1000" in ids
    for r in ranks:
        assert 1 <= r.rank <= r.of <= 4
        assert r.text_rank == f"{ordinal(r.rank)} of {r.of}"
        assert ("highest of" in r.words or "lowest of" in r.words) and "counties in" in r.words
        assert r.direction == ("highest" if r.rank <= (r.of + 1) / 2 else "lowest")
    all_income = [county_ranks(ctx, g)[0] for g in ("99001", "99003", "99005", "99007")]
    assert sorted(r.rank for r in all_income) == [1, 2, 3, 4]
    order = sorted(all_income, key=lambda r: -r.value)
    assert [r.rank for r in order] == [1, 2, 3, 4]
    assert build_profile("TL", "99001000100", ctx=ctx).ranks == ()


def test_share_helpers():
    assert share_of(1, 3) == 0.3333
    assert share_of(1, 0) is None and share_of(None, 3) is None
    items, total = items_from_counts([("a", "A", 1.0, None), ("b", "B", None, None)])
    assert total == 1.0 and items[0].share == 1.0 and items[1].share is None
