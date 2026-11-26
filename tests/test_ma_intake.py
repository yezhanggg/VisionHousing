"""Massachusetts intake: 3A lists, the 3A atlas, the ADU survey and the pack tables they make.

No network. The CSV fixtures in ``tests/fixtures/ma/`` are format fixtures: the
column headers exactly as HLC publishes them, with a few made-up rows (real town
names, invented numbers) small enough to check by hand. The atlas and the map
service are faked in memory.
"""

from __future__ import annotations

import gzip
import json
import urllib.parse
from pathlib import Path

import pandas as pd
import pytest

from tests.pack_fakes import Response, client
from visionhousing.data import local
from visionhousing.data.http import FetchError
from visionhousing.data.sources import ma_intake as ma
from visionhousing.features import ma_intake as fx

FIX = Path(__file__).resolve().parent / "fixtures" / "ma"
ROOT = Path(__file__).resolve().parents[1]


def raw(name: str) -> bytes:
    return (FIX / name).read_bytes()


def subdivisions() -> pd.DataFrame:
    """A few Massachusetts county subdivisions as the Census Bureau names them."""
    return pd.DataFrame(
        {
            "county": ["25023", "25017", "25013", "25003", "25023", "25009", "25023"],
            "cousub": ["00170", "00380", "00840", "00555", "11525", "38155", "00000"],
            "name": [
                "Abington town",
                "Acton town",
                "Agawam Town city",
                "Adams town",
                "Carver town",
                "Manchester-by-the-Sea town",
                "County subdivisions not defined",
            ],
            "population": [17000, 24000, 28000, 8000, 12000, 5000, 0],
        }
    )


# --------------------------------------------------------------------------- names


def test_town_names_join_census_names_with_type_words_and_overrides():
    assert ma.muni_key("Agawam Town city") == ma.muni_key("Agawam") == "agawam"
    assert ma.muni_key("Abington town") == "abington"
    over = {"Manchester": "Manchester-by-the-Sea"}
    assert ma.muni_key("Manchester", over) == ma.muni_key("Manchester-by-the-Sea town")
    lookup = fx.muni_lookup(subdivisions())
    assert "00000" not in set(lookup["cousub"])
    assert lookup.set_index("key").loc["agawam", "county"] == "25013"


# --------------------------------------------------------------------------- 3A lists


def test_compliance_status_is_trimmed_and_typed():
    s = ma.parse_compliance_status(raw("compliance_status.csv")).set_index("municipality")
    assert list(s.index) == ["Abington", "Agawam", "Carver", "Manchester"]
    assert s.loc["Abington", "compliance_status"] == "Compliant"  # trailing space gone
    assert s.loc["Abington", "compliance_details"] == ""  # "-" means none
    assert s.loc["Agawam", "compliance_details"].startswith("District Compliance")
    assert s.loc["Carver", "deadline"] == "2025-12-31"
    assert s.loc["Abington", "pre_adoption_review"] == 1
    assert s.loc["Carver", "adopted_zoning"] == 0


def test_compliance_status_without_its_columns_is_refused():
    with pytest.raises(ValueError, match="lacks columns"):
        ma.parse_compliance_status(b"Town,Status\nA,B\n")


def test_categories_read_commas_percents_and_dashes():
    c = ma.parse_categories(raw("categories.csv")).set_index("municipality")
    assert c.loc["Abington", "housing_units_2020"] == 6000
    assert c.loc["Abington", "min_unit_capacity"] == 1000
    assert c.loc["Abington", "capacity_share"] == pytest.approx(0.15)
    assert c.loc["Abington", "station_area_share"] == pytest.approx(0.40)
    assert pd.isna(c.loc["Agawam", "station_area_acres"])  # " -   " = no station area
    assert c.loc["Carver", "category"] == "Adjacent small town"


def test_development_tracker_is_summed_per_town_without_names_or_addresses():
    t = ma.parse_development_tracker(raw("development_tracker.csv")).set_index("municipality")
    assert t.loc["Abington"].to_dict() == {
        "tracker_projects": 2,
        "tracker_units": 160,
        "tracker_net_units": 158,
        "tracker_deed_restricted": 16,
    }
    assert t.loc["Manchester", "tracker_units"] == 1000
    assert not any("developer" in c or "address" in c for c in t.columns)


# --------------------------------------------------------------------------- ADU survey


def test_adu_survey_gives_one_row_per_answered_period_with_its_cohort():
    s = ma.parse_adu_survey(raw("adu_survey.csv"))
    rows = {(r.municipality, r.cohort): r for r in s.itertuples()}
    assert ("Acton", "2025 first half") not in rows and ("Acton", "2025 full year") not in rows
    assert ("Agawam", "2026 first half") not in rows  # no answer is not zero
    ab = rows[("Abington", "2025 first half")]
    assert (ab.period_start, ab.period_end) == ("2025-01-01", "2025-06-30")
    assert (ab.applications, ab.approvals, ab.certificates_of_occupancy) == (6, 4, 1)
    assert (ab.approvals_detached, ab.approvals_attached) == (3, 1)
    ag = rows[("Agawam", "2025 full year")]
    assert ag.period_end == "2025-12-31" and ag.answer == "End-of-year"
    assert rows[("Adams", "2026 first half")].approvals == 0  # a reported zero stays zero
    assert len(s) == 8
    assert s["approvals"].sum() == 15


def test_adu_survey_totals_check_names_towns_whose_totals_disagree():
    assert ma.survey_totals_check(raw("adu_survey.csv")) == ["Carver"]


def test_adu_survey_totals_check_reads_its_periods_from_the_settings():
    settings = ma.load_settings()
    only_25 = {
        **settings,
        "adu_survey": {**settings["adu_survey"], "periods": settings["adu_survey"]["periods"][:1]},
    }
    # with the 2026 wave left out, every town that answered it disagrees with its total
    assert "Abington" in ma.survey_totals_check(raw("adu_survey.csv"), only_25)
    renamed = {
        **settings,
        "adu_survey": {
            **settings["adu_survey"],
            "periods": [*settings["adu_survey"]["periods"], {"suffix": "27"}],
        },
    }
    with pytest.raises(ValueError, match="Submitted_27"):
        ma.survey_totals_check(raw("adu_survey.csv"), renamed)


def test_adu_survey_totals_check_skips_a_town_with_nothing_filled():
    text = raw("adu_survey.csv").decode("utf-8-sig").splitlines()
    head = text[0].split(",")
    blank = ["99", "Blankville", *[""] * (len(head) - 2)]
    blank[head.index("Source_25")] = "No response"
    blank[head.index("Source_26")] = "No response"
    body = "\n".join([*text, ",".join(blank)]) + "\n"
    assert "Blankville" not in ma.survey_totals_check(body)


def test_adu_survey_refuses_an_answer_it_does_not_know():
    text = raw("adu_survey.csv").decode().replace("End-of-year", "Late", 1)
    with pytest.raises(ValueError, match="unknown answers"):
        ma.parse_adu_survey(text)


def test_adu_survey_table_gets_counties_and_its_edition():
    table, missing = fx.adu_survey_table(
        ma.parse_adu_survey(raw("adu_survey.csv")),
        fx.muni_lookup(subdivisions()),
        as_of="2026-08-12",
        route="hand_saved",
    )
    assert missing == []
    assert set(table.loc[table["municipality"] == "Agawam", "county"]) == {"25013"}
    assert set(table["survey_route"]) == {"hand_saved"}
    assert list(table.columns[:5]) == ["muni_id", "municipality", "county", "cousub", "cohort"]


# --------------------------------------------------------------------------- copies


class Refuses:
    """A server that answers every request with HTTP 403, as www.mass.gov does scripts."""

    def __init__(self):
        self.calls = 0

    def open(self, req, data=None, timeout=None):
        self.calls += 1
        return Response(403, b"<!DOCTYPE html><title>Not allowed</title>")


class Serves:
    """A server that answers every request with the same body."""

    def __init__(self, body: bytes):
        self.body, self.calls = body, 0

    def open(self, req, data=None, timeout=None):
        self.calls += 1
        return Response(200, self.body)


def test_a_refused_file_says_where_to_save_it_by_hand(tmp_path):
    server = Refuses()
    with pytest.raises(FetchError, match=r"HTTP 403.*data/raw/local/ma/ma_adu_survey\.csv"):
        ma.fetch_copy("adu_survey", tmp_path, client=client(server), manifest=None)
    assert server.calls == 1  # asked once, not retried


def test_a_hand_saved_copy_is_read_when_the_publisher_refuses(tmp_path):
    folder = ma.cache_dir(tmp_path)
    folder.mkdir(parents=True)
    (folder / "ma_adu_survey.csv").write_bytes(raw("adu_survey.csv"))
    got = ma.fetch_copy("adu_survey", tmp_path, client=client(Refuses()), manifest=None)
    # the edition the settings list for the publisher's address is not assumed
    assert got.route == "hand_saved" and got.as_of == "" and got.edition_from == "unknown"
    assert json.loads((folder / "ma_adu_survey.csv.source.json").read_text())["route"] == (
        "hand_saved"
    )


def test_a_hand_saved_copy_takes_its_edition_from_the_persons_note(tmp_path):
    folder = ma.cache_dir(tmp_path)
    folder.mkdir(parents=True)
    (folder / "ma_adu_survey.csv").write_bytes(raw("adu_survey.csv"))
    ma.fetch_copy("adu_survey", tmp_path, client=client(Refuses()), manifest=None)
    (folder / "ma_adu_survey.csv.edition.yaml").write_text(
        "as_of: 2027-01-15\nurl: https://example.org/survey.csv\n"
    )
    got = ma.fetch_copy("adu_survey", tmp_path, client=client(Refuses()), manifest=None)
    assert (got.as_of, got.edition_from, got.url) == (
        "2027-01-15",
        "sidecar",
        "https://example.org/survey.csv",
    )
    (folder / "ma_adu_survey.csv.edition.yaml").write_text("as_of: soon\n")
    with pytest.raises(ValueError, match="as_of must be a date"):
        ma.fetch_copy("adu_survey", tmp_path, client=client(Refuses()), manifest=None)


def test_a_hand_saved_copy_reads_the_edition_written_in_the_file(tmp_path):
    folder = ma.cache_dir(tmp_path)
    folder.mkdir(parents=True)
    dest = folder / "mbta3a_compliance_status.csv"
    dest.write_bytes(raw("compliance_status.csv"))
    first = ma.fetch_copy("compliance_status", tmp_path, client=client(Refuses()), manifest=None)
    assert first.as_of == ""
    # replaced by a newer copy whose title row gives its date: read afresh
    dest.write_bytes(b"Compliance Status Sheet as of 3-2-27\n" + raw("compliance_status.csv"))
    got = ma.fetch_copy("compliance_status", tmp_path, client=client(Refuses()), manifest=None)
    assert (got.as_of, got.edition_from) == ("2027-03-02", "file")


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Compliance Status Sheet as of 11-24-26\nMunicipality", "2026-11-24"),
        ("Tracker as of 9/11/2026,,\n", "2026-09-11"),
        ("Categories, as of June 9, 2025\n", "2025-06-09"),
        ("as of 2026-08-12\n", "2026-08-12"),
        ("Municipality,Status\nA,as of 1-1-25\n", ""),  # a date in the data, not the title
        ("Title\nMunicipality,Status as of 2-3-26\nA,as of 1-1-25\n", "2026-02-03"),
        ("Status as of 13-45-26\n", ""),
        ("muni_id,muni_name\n", ""),
    ],
)
def test_edition_in_text_reads_only_a_title_date(text, want):
    assert ma.edition_in_text(text) == want


def test_a_publisher_copy_is_saved_unpacked_and_reused(tmp_path):
    server = Serves(gzip.compress(raw("compliance_status.csv")))
    got = ma.fetch_copy("compliance_status", tmp_path, client=client(server), manifest=None)
    assert got.route == "publisher" and got.path.read_bytes() == raw("compliance_status.csv")
    again = ma.fetch_copy("compliance_status", tmp_path, client=client(server), manifest=None)
    assert server.calls == 1 and again.route == "publisher"


def test_a_web_page_is_not_taken_for_a_csv(tmp_path):
    server = Serves(b"<!doctype html><p>moved</p>")
    with pytest.raises(FetchError, match="not a CSV"):
        ma.fetch_copy("categories", tmp_path, client=client(server), manifest=None)


# --------------------------------------------------------------------------- atlas


def square(west: float, south: float, size: float) -> dict:
    ring = [[west, south], [west + size, south], [west + size, south + size],
            [west, south + size], [west, south]]  # fmt: skip
    return {"type": "Polygon", "coordinates": [ring]}


def atlas_features() -> list[dict]:
    def props(oid, muni, code, date_ms, cap):
        return {"OBJECTID": oid, "ZONECOD": code, "MUNI_ID": 1, "MUNI": muni, "ZONENAM": "Dist",
                "ZONABBR": code[-2:], "DATE_": date_ms, "ISOVRLY": 1, "STATUS": "Compliant",
                "MFUC_DIST": cap, "MFUC_CLN": 0, "MFUC_PARC": 0, "ACRS_CM": 20.0,
                "DNDN_CM": 18.0, "DUAC_CM": 15.5, "DUC_HMB": 100, "PRKNPDU": 1.0,
                "STORIES": 3, "LOTCOV": 0, "FAR": 0, "AREAPDU": 0, "MXDUPAC": 15,
                "DU_CAP": 0, "OSRQ_PC": 0.2, "NOTES": " "}  # fmt: skip

    return [
        {"type": "Feature", "geometry": square(-71.0, 42.0, 0.01),
         "properties": props(2, "Abington", "1MF", 1721779200000, 300)},
        {"type": "Feature", "geometry": square(-71.0, 42.5, 0.01),
         "properties": props(1, "Manchester", "166D", None, 200)},
    ]  # fmt: skip


class MapService:
    """Answers the atlas queries: a count, then GeoJSON pages by resultOffset."""

    def __init__(self, features: list[dict], page: int = 1):
        self.features, self.page, self.calls = features, page, 0

    def open(self, req, data=None, timeout=None):
        self.calls += 1
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))
        if q.get("returnCountOnly") == "true":
            return Response(200, json.dumps({"count": len(self.features)}).encode())
        assert q["outSR"] == "4326" and q["f"] == "geojson"
        start = int(q["resultOffset"])
        part = self.features[start : start + self.page]
        return Response(200, json.dumps({"type": "FeatureCollection", "features": part}).encode())


def test_atlas_pages_are_joined_cached_and_parsed(tmp_path):
    server = MapService(atlas_features())
    col = ma.fetch_3a_districts(tmp_path, client=client(server), manifest=None)
    calls = server.calls
    again = ma.fetch_3a_districts(tmp_path, client=client(server), manifest=None)
    assert server.calls == calls and again == col  # read from the cache
    d = ma.parse_3a_districts(col).set_index("objectid")
    assert d.loc[2, "submitted_on"] == "2024-07-24"  # epoch milliseconds -> date
    assert d.loc[1, "submitted_on"] == ""  # none in the atlas
    assert d.loc[1, "notes"] == ""  # blank text trimmed
    assert d.loc[2, "capacity_district"] == 300
    assert json.loads(d.loc[2, "geometry"])["type"] == "Polygon"


def test_districts_get_their_town_county_and_centre_tract():
    d = ma.parse_3a_districts({"features": atlas_features()})
    tracts = [("25023000100", square(-71.1, 41.9, 0.3)), ("25009000100", square(-71.1, 42.4, 0.3))]
    table, missing = fx.districts_table(
        d,
        fx.muni_lookup(subdivisions()),
        tracts,
        read_on="2026-11-24",
        overrides={"Manchester": "Manchester-by-the-Sea"},
    )
    t = table.set_index("district_id")
    assert missing == []
    assert t.loc[2, "county"] == "25023" and t.loc[2, "tract"] == "25023000100"
    assert t.loc[1, "county"] == "25009" and t.loc[1, "tract"] == "25009000100"
    assert t.loc[2, "lat"] == pytest.approx(42.005) and t.loc[2, "lon"] == pytest.approx(-70.995)
    assert list(table.columns)[-1] == "geometry"


def test_centre_of_an_empty_outline_is_missing():
    lat, lon = fx.district_centres(["", json.dumps(square(0, 0, 2))])
    assert pd.isna(lat[0]) and (lat[1], lon[1]) == (1.0, 1.0)


def test_a_district_is_linked_to_every_tract_its_outline_reaches():
    # district: 0.01 deg square; tract A holds 70% of it (and its centre), B 29%, C a 1% sliver
    d = pd.DataFrame(
        [{"district_id": 7, "tract": "25001000100",
          "geometry": json.dumps(square(-71.0, 42.0, 0.01))}]
    )  # fmt: skip
    a = {
        "type": "Polygon",
        "coordinates": [
            [[-71.1, 41.9], [-70.993, 41.9], [-70.993, 42.1], [-71.1, 42.1], [-71.1, 41.9]]
        ],
    }
    b = {
        "type": "Polygon",
        "coordinates": [
            [[-70.993, 41.9], [-70.9901, 41.9], [-70.9901, 42.1], [-70.993, 42.1], [-70.993, 41.9]]
        ],
    }
    c = {
        "type": "Polygon",
        "coordinates": [
            [[-70.9901, 41.9], [-70.9, 41.9], [-70.9, 42.1], [-70.9901, 42.1], [-70.9901, 41.9]]
        ],
    }
    far = square(-72.0, 41.0, 0.1)
    tracts = [("25001000100", a), ("25001000200", b), ("25001000300", c), ("25001000400", far)]
    links = fx.district_tract_links(d, tracts, cell_m=10, min_share=0.02)
    got = links.set_index("tract")
    assert list(links.columns) == list(fx.LINK_COLUMNS)
    assert set(got.index) == {"25001000100", "25001000200"}  # the 1% sliver and far tract left out
    assert got.loc["25001000100", "district_share"] == pytest.approx(0.70, abs=0.01)
    assert got.loc["25001000200", "district_share"] == pytest.approx(0.29, abs=0.01)
    assert got.loc["25001000100", "centre"] == 1 and got.loc["25001000200", "centre"] == 0
    # 0.01 deg x 0.01 deg at 42 N is about 828 m x 1,106 m = about 226 acres in all
    total = got["overlap_acres"].sum() / got["district_share"].sum()
    assert total == pytest.approx(226, rel=0.02)
    for col in links.columns:
        unit, text = local.describe("mbta_3a_district_tracts", col)
        assert unit and text
    assert local.TABLES["mbta_3a_district_tracts"]["tier"] == "2"


def test_a_district_too_small_for_a_cell_keeps_its_centre_tract():
    d = pd.DataFrame(
        [{"district_id": 1, "tract": "25001000100",
          "geometry": json.dumps(square(-71.0, 42.0, 0.00002))},
         {"district_id": 2, "tract": "25001000100", "geometry": ""}]
    )  # fmt: skip
    links = fx.district_tract_links(
        d, [("25001000100", square(-71.1, 41.9, 0.3))], cell_m=10, min_share=0.02
    )
    assert list(links["district_id"]) == [1, 2]
    assert (links["district_share"] == 1.0).all() and (links["centre"] == 1).all()


def test_the_3a_retrofit_does_nothing_without_district_outlines(tmp_path):
    from visionhousing.data import pack

    assert pack.add_3a_district_tracts(tmp_path / "VT", tmp_path / "raw") is None
    assert "3a district tracts" in [name for name, _, _ in pack.RETROFITS]


# --------------------------------------------------------------------------- status


def _districts() -> pd.DataFrame:
    d = ma.parse_3a_districts({"features": atlas_features()})
    table, _ = fx.districts_table(
        d,
        fx.muni_lookup(subdivisions()),
        [],
        read_on="2026-11-24",
        overrides={"Manchester": "Manchester-by-the-Sea"},
    )
    return table


def test_status_table_joins_the_lists_the_atlas_and_census_towns():
    table, notes = fx.status_table(
        ma.parse_compliance_status(raw("compliance_status.csv")),
        ma.parse_categories(raw("categories.csv")),
        ma.parse_development_tracker(raw("development_tracker.csv")),
        _districts(),
        fx.muni_lookup(subdivisions()),
        dates={"status_as_of": "2026-11-24", "status_route": "hand_saved"},
        overrides={"Manchester": "Manchester-by-the-Sea"},
    )
    t = table.set_index("municipality")
    assert notes == []
    assert len(t) == 4 and set(t["status_source"]) == {"status sheet"}
    assert t.loc["Abington", "category"] == "Commuter Rail"
    assert t.loc["Abington", "atlas_districts"] == 1 and t.loc["Abington", "atlas_capacity"] == 300
    assert t.loc["Abington", "atlas_first_submitted"] == "2024-07-24"
    assert t.loc["Carver", "atlas_districts"] == 0  # noncompliant: nothing in the atlas
    assert t.loc["Carver", "tracker_units"] == 0
    assert t.loc["Manchester", "county"] == "25009"  # by the name override
    assert t.loc["Manchester", "tracker_units"] == 1000
    assert set(t["status_as_of"]) == {"2026-11-24"}


def test_all_blank_counts_stay_unknown_not_zero():
    districts = _districts()
    abington = districts["municipality"] == "Abington"
    districts.loc[abington, ["capacity_district", "acres"]] = float("nan")
    tracker = ma.parse_development_tracker(
        "Municipality,# Total Units,Net Units,Deed_Restricted\nAbington,,,\nAbington,-,,\n"
    )
    assert pd.isna(tracker.loc[0, "tracker_units"]) and tracker.loc[0, "tracker_projects"] == 2
    table, _ = fx.status_table(
        ma.parse_compliance_status(raw("compliance_status.csv")),
        None,
        tracker,
        districts,
        fx.muni_lookup(subdivisions()),
        dates={"status_as_of": "2026-11-24", "status_route": "hand_saved"},
        overrides={"Manchester": "Manchester-by-the-Sea"},
    )
    t = table.set_index("municipality")
    assert t.loc["Abington", "atlas_districts"] == 1
    assert pd.isna(t.loc["Abington", "atlas_capacity"]) and pd.isna(
        t.loc["Abington", "atlas_acres"]
    )
    assert pd.isna(t.loc["Abington", "tracker_units"])  # listed, every count blank: unknown
    assert t.loc["Abington", "tracker_projects"] == 2
    assert t.loc["Carver", "tracker_units"] == 0  # not in the tracker: no linked housing
    assert set(t["categories_route"]) == {""} and set(t["tracker_route"]) == {""}


def test_without_the_status_sheet_the_atlas_gives_compliant_towns_only():
    table, notes = fx.status_table(
        None,
        None,
        None,
        _districts(),
        fx.muni_lookup(subdivisions()),
        dates={"status_as_of": "2026-11-24", "status_route": "atlas"},
        overrides={"Manchester": "Manchester-by-the-Sea"},
    )
    assert list(table["municipality"]) == ["Abington", "Manchester"]
    assert set(table["compliance_status"]) == {"Compliant"}
    assert set(table["status_source"]) == {"atlas"}
    assert table["category"].isna().all()
    assert any("atlas alone" in n for n in notes)


def test_without_sheet_or_atlas_there_is_no_status_table():
    with pytest.raises(ValueError, match="neither"):
        fx.status_table(None, None, None, None, fx.muni_lookup(subdivisions()), dates={})


def test_every_column_has_a_dictionary_entry():
    table, _ = fx.status_table(
        ma.parse_compliance_status(raw("compliance_status.csv")),
        ma.parse_categories(raw("categories.csv")),
        ma.parse_development_tracker(raw("development_tracker.csv")),
        _districts(),
        fx.muni_lookup(subdivisions()),
        dates={},
    )
    survey, _ = fx.adu_survey_table(
        ma.parse_adu_survey(raw("adu_survey.csv")),
        fx.muni_lookup(subdivisions()),
        as_of="",
        route="",
    )
    for name, frame in (
        ("mbta_3a_status", table),
        ("mbta_3a_districts", _districts()),
        ("ma_adu_survey", survey),
    ):
        for col in frame.columns:
            unit, text = local.describe(name, col)
            assert unit and text
        assert local.TABLES[name]["tier"] == "2"


# --------------------------------------------------------------------------- local tables


def test_massachusetts_local_tables_build_with_refused_lists(tmp_path, monkeypatch):
    monkeypatch.setattr(
        local.munstats, "fetch_subdivision_population", lambda *a, **k: subdivisions()
    )
    monkeypatch.setattr(
        local.ma_src, "fetch_3a_districts", lambda *a, **k: {"features": atlas_features()}
    )
    tracts = [("25023000100", square(-71.1, 41.9, 0.3))]
    out = local.fetch_local_tables(
        "MA",
        tmp_path,
        blocks=pd.DataFrame(columns=["geoid", "tract", "housing_units"]),
        tract_shapes=tracts,
        client=client(Refuses()),
        manifest=None,
    )
    assert set(out.tables) == {"mbta_3a_districts", "mbta_3a_status"}
    assert set(out.tables["mbta_3a_status"]["status_source"]) == {"atlas"}
    assert any("adu_survey: not added" in n and "HTTP 403" in n for n in out.notes)


def test_cached_local_files_belong_to_their_own_state(tmp_path):
    files = (
        "census/sub-est2024_42.csv",
        "census/sub-est2024_25.csv",
        "ma/mbta3a_categories.csv",
        "phfa/awards_2024.csv",
    )
    for rel in files:
        path = tmp_path / "local" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    pa, ma_out = local.LocalTables(), local.LocalTables()
    local._cached_files(pa, tmp_path, "PA")
    local._cached_files(ma_out, tmp_path, "MA")
    assert {p.name for _, p in pa.files} == {"sub-est2024_42.csv", "awards_2024.csv"}
    assert {p.name for _, p in ma_out.files} == {"sub-est2024_25.csv", "mbta3a_categories.csv"}


# --------------------------------------------------------------------------- law rows


def test_massachusetts_law_rows_load_as_timeline_records():
    from visionhousing.law.timeline import load_timeline

    records = {r.record_id: r for r in load_timeline(ROOT / "assumptions/law/states/ma.yaml")}
    adu = records["ma_adu_by_right_effective"]
    assert adu.status == "legally_effective" and adu.start.isoformat() == "2025-02-02"
    assert records["ma_adu_by_right_enacted"].end == adu.start
    assert records["ma_3a_effective"].court_status == "upheld"


def test_massachusetts_already_legal_range_applies_to_massachusetts_only():
    from visionhousing.scenarios.adu import legal_share_override, load_legal_shares

    tables = load_legal_shares()
    ma_table = tables["MA"]
    assert 0 < ma_table.low <= ma_table.central <= ma_table.high < 1
    assert ma_table.state == "MA" and ma_table.also == {}
    change = legal_share_override(ma_table)["adu.already_legal_share"]
    assert change["central"] == ma_table.central and "already_legal_ma" in change["rationale"]
