"""Incentive areas for place profiles: readers, the .xlsb reader, tract placement and the section.

All inputs are built in the test or read from Testland; a fake map server stands in for
the ArcGIS services, so no network is used.
"""

from __future__ import annotations

import email.message
import io
import json
import shutil
import struct
import urllib.parse
import zipfile

import pandas as pd
import pytest

from tests.pack_fakes import client
from tests.test_prices_costs import make_xlsx
from visionhousing.data.registry import get_source
from visionhousing.data.sources import incentives as src
from visionhousing.data.sources import xlsb
from visionhousing.engine.context import TESTLAND_DIR
from visionhousing.features import incentives as fx
from visionhousing.profile import load_profile_context
from visionhousing.profile.registry import build_section

# --------------------------------------------------------------------------- .xlsb writer


def _varint(n: int, limit: int) -> bytes:
    out = bytearray()
    for _ in range(limit):
        byte = n & 0x7F
        n >>= 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            break
    return bytes(out)


def _record(rtype: int, body: bytes) -> bytes:
    return _varint(rtype, 2) + _varint(len(body), 4) + body


def _wide(text: str) -> bytes:
    return struct.pack("<I", len(text)) + text.encode("utf-16-le")


def make_xlsb(rows: list[list]) -> bytes:
    """A minimal .xlsb: text cells use shared strings, whole numbers RK, others reals."""
    strings: list[str] = []
    sheet = bytearray()
    for r, row in enumerate(rows):
        sheet += _record(xlsb.ROW, struct.pack("<I", r) + bytes(13))
        for c, value in enumerate(row):
            head = struct.pack("<II", c, 0)
            if value is None:
                continue
            if isinstance(value, str):
                if value not in strings:
                    strings.append(value)
                sheet += _record(xlsb.SHARED, head + struct.pack("<I", strings.index(value)))
            elif float(value).is_integer() and abs(value) < 2**29:
                sheet += _record(
                    xlsb.RK, head + struct.pack("<I", ((int(value) << 2) | 2) & 0xFFFFFFFF)
                )
            else:
                sheet += _record(xlsb.REAL, head + struct.pack("<d", float(value)))
    sst = b"".join(_record(xlsb.SST_ITEM, b"\x00" + _wide(s)) for s in strings)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/sharedStrings.bin", sst)
        zf.writestr("xl/worksheets/sheet1.bin", bytes(sheet))
        zf.writestr("xl/worksheets/sheet2.bin", _record(xlsb.ROW, bytes(17)))
    return buf.getvalue()


def test_xlsb_reader_reads_text_rk_and_reals():
    rows = list(xlsb.xlsb_rows(make_xlsb([["a", "b"], [1, 2.5], [None, "a"], [-7, 0.25]])))
    assert rows == [["a", "b"], [1.0, 2.5], [None, "a"], [-7.0, 0.25]]


def test_xlsb_rk_number_forms():
    assert xlsb._rk((123 << 2) | 2) == 123
    assert xlsb._rk((12345 << 2) | 3) == pytest.approx(123.45)
    packed = struct.unpack("<Q", struct.pack("<d", 2.5))[0] >> 32
    assert xlsb._rk(packed & 0xFFFFFFFC) == 2.5


def test_xlsb_record_running_past_the_end_is_refused():
    with pytest.raises(ValueError, match="past the end"):
        list(xlsb.records(_record(5, b"12345678")[:-2]))


# --------------------------------------------------------------------------- spreadsheets

NMTC_HEADER = [
    "2020 Census Tract Number FIPS code. GEOID",
    "OMB Metro/Non-metro Designation, March 2020 (OMB Bulletin No. 20-01)",
    "Does Census Tract Qualify For NMTC Low-Income Community (LIC) on Poverty or Income Criteria?",
    "Census Tract Poverty Rate % (2016-2020 ACS)",
    "Does Census Tract Qualify on Poverty Criteria>=20%?",
    "Census Tract Percent of Benchmarked Median Family Income (%) 2016-2020 ACS",
]


def test_nmtc_reader_finds_columns_by_header_and_scales_income():
    book = make_xlsb(
        [
            ["NMTC file"],
            NMTC_HEADER,
            ["99001000100", "Metro", "YES", 24.5, "YES", 0.7125],
            ["99001000200", "Metro", "NO", 8.0, "NO", 1.1],
            ["98001000100", "Non-Metro", "YES", 30.0, "YES", 0.5],
        ]
    )
    t = src.read_nmtc(book, "99")
    assert list(t["tract"]) == ["99001000100", "99001000200"]
    assert list(t["nmtc_lic"]) == [1, 0]
    assert t["nmtc_mfi_pct"].iloc[0] == pytest.approx(71.25)
    assert t["nmtc_poverty_rate"].iloc[0] == 24.5
    with pytest.raises(ValueError, match="header"):
        src.read_nmtc(make_xlsb([["no", "header"]]))


def test_oz_reader_keeps_state_tracts_and_rural_flag():
    book = make_xlsx(
        [
            ["Census Tract Number", "QOZID", "State or Territory", "County", "Rural QOZ"],
            ["99001000100", "R99001000100-27", "Testland", "Harbor", "Rural"],
            ["99003000100", "N99003000100-27", "Testland", "Maple", "Non-Rural"],
            ["98001000100", "N98001000100-27", "Elsewhere", "X", "Non-Rural"],
        ]
    )
    t = src.read_oz2027(book, "99")
    assert t.to_dict("list") == {"tract": ["99001000100", "99003000100"], "rural": [1, 0]}


# --------------------------------------------------------------------------- map services


class FakeMap:
    """Answers ArcGIS ``query`` requests from in-memory feature lists, page by page."""

    def __init__(self, layers: dict[str, list[dict]], files: dict[str, bytes] | None = None):
        self.layers, self.files, self.urls = layers, files or {}, []

    def open(self, req, data=None, timeout=None):
        from tests.pack_fakes import Response

        url = req.full_url
        self.urls.append(url)
        base, _, query = url.partition("?")
        if base in self.files:
            return Response(200, self.files[base])
        q = dict(urllib.parse.parse_qsl(query))
        feats = self.layers[base]
        if q.get("returnCountOnly") == "true":
            body = {"count": len(feats)}
        else:
            start, size = int(q["resultOffset"]), int(q["resultRecordCount"])
            body = {"features": feats[start : start + size]}
        return Response(200, json.dumps(body).encode())


def _feat(attrs: dict, x: float | None = None, y: float | None = None) -> dict:
    out: dict = {"attributes": attrs}
    if x is not None:
        out["geometry"] = {"x": x, "y": y}
    return out


def test_fetch_features_pages_and_checks_the_count():
    url = "https://map.test/q"
    fake = FakeMap({url: [_feat({"GEOID": str(i)}) for i in range(5)]})
    got = src.fetch_features(client(fake), url, "1=1", "GEOID", page=2)
    assert [f["attributes"]["GEOID"] for f in got] == ["0", "1", "2", "3", "4"]
    assert sum("resultOffset" in u for u in fake.urls) == 3


def test_map_service_error_is_raised():
    with pytest.raises(Exception, match="map service error"):
        src.read_feature_page(b'{"error": {"code": 400}}')


# --------------------------------------------------------------------------- tables


def tract_shapes() -> list[tuple[str, dict]]:
    doc = json.loads((TESTLAND_DIR / "boundaries.geojson").read_text())
    return [
        (f["properties"]["geoid"], f["geometry"])
        for f in doc["features"]
        if f["properties"]["level"] == "tract"
    ]


TRACT1 = (-58.99, 30.55)  # a point inside tract 99001000100
TRACT2 = (-58.96, 30.55)  # a point inside tract 99001000200
DISTRICT = {  # a square over the lower half of tract 99001000100
    "type": "Polygon",
    "coordinates": [[[-59.0, 30.5], [-58.9715, 30.5], [-58.9715, 30.55], [-59.0, 30.55],
                     [-59.0, 30.5]]],
}  # fmt: skip


def inputs() -> src.IncentiveInputs:
    return src.IncentiveInputs(
        qct=pd.DataFrame({"tract": ["99001000100"]}),
        dda=pd.DataFrame(
            {
                "code": ["11111", "99003"],
                "dda_type": ["SA", "NM"],
                "dda_code": ["a", "b"],
                "dda_name": ["metro", "Maple County"],
            }
        ),
        zcta_tract=pd.DataFrame(
            {
                "zcta": ["11111", "22222", "22222"],
                "tract": ["99001000100", "99001000100", "99001000200"],
                "land_m2": [300, 100, 50],
            }
        ),
        oz2027=pd.DataFrame({"tract": ["99001000200"], "rural": [1]}),
        nmtc=pd.DataFrame(
            {
                "tract": ["99001000100", "99001000200"],
                "nmtc_lic": [1, 0],
                "nmtc_poverty_rate": [25.0, 5.0],
                "nmtc_mfi_pct": [60.0, 120.0],
                "nmtc_metro": ["Metro", "Metro"],
            }
        ),
        lihtc=pd.DataFrame(
            {
                "hud_id": ["A", "B", "C"],
                "units": [40, 10, 5],
                "li_units": [40, 8, None],
                "year_pis": ["2015", "8888", "2019"],
                "county": ["99001", "99001", "99001"],
                "lat": [TRACT1[1], TRACT1[1], None],
                "lon": [TRACT1[0], TRACT1[0], None],
            }
        ),
        koz=pd.DataFrame(
            {
                "county_name": ["Harbor"],
                "acres": [12.5],
                "developed": ["No"],
                "occupied": ["No"],
                "expires": ["2030"],
                "lat": [TRACT2[1]],
                "lon": [TRACT2[0]],
            }
        ),
        nrhp_points=pd.DataFrame(
            {
                "refnum": ["1", "2", "3"],
                "res_type": ["building", "building", "building"],
                "status": ["Listed", "Removed", "Listed"],
                "lat": [TRACT1[1], TRACT1[1], TRACT2[1]],
                "lon": [TRACT1[0], TRACT1[0], TRACT2[0]],
            }
        ),
        nrhp_districts=[("9", DISTRICT)],
    )


TRACTS = pd.read_csv(TESTLAND_DIR / "tracts.csv", dtype={"geoid": str})["geoid"].tolist()
COUNTIES = {"99001": "Harbor County", "99003": "Maple County", "99005": "Brookside County",
            "99007": "Prairie County"}  # fmt: skip
BLOCKS = pd.read_csv(TESTLAND_DIR / "blocks.csv", dtype={"geoid": str, "tract": str})


@pytest.fixture(scope="module")
def tables():
    return fx.incentive_tables(inputs(), TRACTS, COUNTIES, tract_shapes(), BLOCKS)


def test_tract_flags_and_dda_share(tables):
    t = tables["incentives_tract"].set_index("tract")
    assert t.loc["99001000100", "qct"] == 1 and t.loc["99001000200", "qct"] == 0
    assert t.loc["99001000100", "dda_land_share"] == pytest.approx(0.75)
    assert t.loc["99001000100", "dda"] == 1 and t.loc["99001000200", "dda"] == 0
    maple = t.index.str.startswith("99003")
    assert (t.loc[maple, "dda_land_share"] == 1).all()  # a non-metro county DDA
    assert t.loc["99001000200", "oz2027"] == 1 and t.loc["99001000200", "oz2027_rural"] == 1
    assert t.loc["99001000100", "nmtc_lic"] == 1
    assert pd.isna(t.loc["99001000300", "nmtc_lic"])  # not in the file: unknown, not "no"


def test_points_are_placed_in_tracts(tables):
    t = tables["incentives_tract"].set_index("tract")
    assert t.loc["99001000100", "lihtc_projects"] == 2
    assert t.loc["99001000100", "lihtc_units"] == 50
    assert t.loc["99001000100", "lihtc_li_units"] == 48
    assert t.loc["99001000200", "koz_sites"] == 1 and t.loc["99001000200", "koz_acres"] == 12.5
    # listing 2 was removed; district 9 has no point, so its polygon's centre is used
    assert t.loc["99001000100", "nrhp_listings"] == 2
    assert t.loc["99001000200", "nrhp_listings"] == 1


def test_homes_in_districts_are_block_homes_inside(tables):
    t = tables["incentives_tract"].set_index("tract")
    lat, lon = BLOCKS["lat"], BLOCKS["lon"]
    inside = (lat < 30.55) & (lon < -58.9715) & (lat > 30.5) & (lon > -59.0)
    assert t.loc["99001000100", "nrhp_district_units"] == BLOCKS.loc[inside, "housing_units"].sum()
    assert t["nrhp_district_units"].sum() == t.loc["99001000100", "nrhp_district_units"]


def test_county_table_counts_records_and_tracts(tables):
    c = tables["incentives_county"].set_index("county")
    harbor = c.loc["99001"]
    assert harbor["qct_tracts"] == 1 and harbor["oz2027_tracts"] == 1
    assert harbor["lihtc_projects"] == 3  # by HUD's county, the unplaced point included
    assert harbor["lihtc_units"] == 55 and harbor["lihtc_latest_year"] == 2019
    assert harbor["koz_sites"] == 1 and harbor["nrhp_districts"] == 1
    assert c.loc["99003", "dda_nonmetro"] == 1 and harbor["dda_nonmetro"] == 0
    assert (c["tracts"].sum()) == len(TRACTS)
    for table, frame in tables.items():
        for col in frame.columns:
            assert (table, col) in fx.COLUMNS, (table, col)


def test_missing_inputs_leave_blank_columns():
    out = fx.incentive_tables(src.IncentiveInputs(), TRACTS, COUNTIES, tract_shapes())
    t = out["incentives_tract"]
    assert t["qct"].isna().all() and t["nmtc_lic"].isna().all()
    assert "lihtc_projects" not in t.columns


# --------------------------------------------------------------------------- fetch


def test_fetch_incentives_end_to_end_with_a_fake_server(tmp_path):
    rel = (
        "﻿OID_ZCTA5_20|GEOID_ZCTA5_20|NAMELSAD_ZCTA5_20|AREALAND_ZCTA5_20|AREAWATER_ZCTA5_20|"
        "MTFCC_ZCTA5_20|CLASSFP_ZCTA5_20|FUNCSTAT_ZCTA5_20|OID_TRACT_20|GEOID_TRACT_20|"
        "NAMELSAD_TRACT_20|AREALAND_TRACT_20|AREAWATER_TRACT_20|MTFCC_TRACT_20|FUNCSTAT_TRACT_20|"
        "AREALAND_PART|AREAWATER_PART\n"
        "1|15001|ZCTA5 15001|9|0|G6350|B5|S|2|42003010100|Tract 101|9|0|G5020|S|9|0\n"
        "1|19001|ZCTA5 19001|9|0|G6350|B5|S|2|34003010100|Tract 101|9|0|G5020|S|9|0\n"
    ).encode()
    oz = make_xlsx([["Census Tract Number", "Rural QOZ"], ["42003010100", "Non-Rural"]])
    nmtc = make_xlsb([NMTC_HEADER, ["42003010100", "Metro", "YES", 30.0, "YES", 0.5]])
    nr = get_source("nps_nrhp")
    ring = [[-80.0, 40.4], [-79.9, 40.4], [-79.9, 40.5], [-80.0, 40.4]]
    fake = FakeMap(
        {
            get_source("hud_qct").url(): [_feat({"GEOID": "42003010100"})],
            get_source("hud_dda").url(): [
                _feat({"ZCTA5": "15001", "DDA_CODE": "M", "DDA_TYPE": "SA", "DDA_NAME": "m"}),
                _feat({"ZCTA5": "42089", "DDA_CODE": "N", "DDA_TYPE": "NM", "DDA_NAME": "n"}),
                _feat({"ZCTA5": "07001", "DDA_CODE": "X", "DDA_TYPE": "SA", "DDA_NAME": "x"}),
            ],
            get_source("hud_lihtc").url(): [
                _feat(
                    {
                        "HUD_ID": "PA1",
                        "N_UNITS": 10,
                        "LI_UNITS": 10,
                        "YR_PIS": "2010",
                        "COUNTY_LEVEL": "42003",
                        "LAT": 40.4,
                        "LON": -80.0,
                    }
                )
            ],
            get_source("dced_koz_sites").url(): [
                _feat(
                    {
                        "county": "Allegheny",
                        "acreage": 3.0,
                        "developed_y_n": "No",
                        "occupied_y_n": "No",
                        "expiration_date": "2030",
                        "coordinator_email": "x@y.test",
                    },
                    -80.0,
                    40.4,
                )
            ],
            nr.url(layer=0): [
                _feat({"NRIS_Refnum": "1", "ResType": "building", "STATUS": "Listed"}, -80.0, 40.4)
            ],
            nr.url(layer=1): [
                {
                    "attributes": {"NRIS_Refnum": "2", "STATUS": "Listed"},
                    "geometry": {"rings": [ring]},
                }
            ],
        },
        files={
            get_source("census_zcta_tract_rel").url(): rel,
            get_source("treasury_oz2027").url(): oz,
            get_source("cdfi_nmtc_lic").url(): nmtc,
        },
    )
    c = client(fake)
    got = src.fetch_incentives("PA", tmp_path, client=c, manifest=None)
    assert got.notes == []
    assert list(got.qct["tract"]) == ["42003010100"]
    assert list(got.zcta_tract["tract"]) == ["42003010100"]  # other states' rows dropped
    assert sorted(got.dda["code"]) == ["15001", "42089"]
    assert list(got.oz2027["tract"]) == ["42003010100"]
    assert list(got.nmtc["nmtc_lic"]) == [1]
    assert got.lihtc.loc[0, "units"] == 10
    assert "coordinator_email" not in got.koz.columns
    assert len(got.nrhp_districts) == 1 and got.nrhp_points.loc[0, "refnum"] == "1"
    folder = tmp_path / "incentives"
    assert not list(folder.glob("*.xls*"))  # national spreadsheets are deleted
    calls = len(fake.urls)
    src.fetch_incentives("PA", tmp_path, client=c, manifest=None)
    assert len(fake.urls) == calls  # everything read from the cache


def test_a_failing_source_becomes_a_note(tmp_path):
    class Down:
        def open(self, req, data=None, timeout=None):
            from tests.pack_fakes import Response

            resp = Response(404, b"gone")
            resp.headers = email.message.Message()
            return resp

    got = src.fetch_incentives("PA", tmp_path, client=client(Down()), manifest=None)
    assert got.qct is None and got.nmtc is None
    assert any("hud_qct not fetched" in n for n in got.notes)


# --------------------------------------------------------------------------- section


@pytest.fixture
def pack(tmp_path, tables):
    folder = tmp_path / "TL"
    shutil.copytree(TESTLAND_DIR, folder)
    for name, frame in tables.items():
        frame.to_csv(folder / f"{name}.csv", index=False)
    return load_profile_context("TL", folder=folder)


def test_incentives_section_tract(pack):
    sec = build_section("incentives", pack, "99001000100")
    assert sec.status == "ok"
    assert sec.fact("incentives.qct").value == "yes"
    assert sec.fact("incentives.oz2027").value == "no"
    assert sec.fact("incentives.nmtc_lic").value == "yes"
    assert sec.fact("incentives.dda_land_share").value == pytest.approx(0.75)
    assert sec.fact("incentives.lihtc_units").value == 50
    assert "a Qualified Census Tract" in sec.summary
    with pytest.raises(KeyError):
        sec.fact("incentives.koz_sites")  # Keystone sites are shown for Pennsylvania only


def test_incentives_section_county_compares_with_state(pack):
    sec = build_section("incentives", pack, "99001")
    assert sec.fact("incentives.qct_tracts").value == 1
    share = sec.fact("incentives.qct_share")
    harbor = [t for t in TRACTS if t.startswith("99001")]
    assert share.value == pytest.approx(1 / len(harbor))
    assert share.comparison.against == "state"
    assert share.comparison.value == pytest.approx(1 / len(TRACTS))
    homes = pd.read_csv(TESTLAND_DIR / "tracts.csv", dtype={"county": str})
    per = 55 - 5  # the unplaced project is not in any tract
    expected = per / homes.loc[homes.county == "99001", "housing_units"].sum() * 1000
    assert sec.fact("incentives.lihtc_per_1000").value == pytest.approx(expected)
    assert sec.limits and "eligible" in sec.limits


def test_incentives_section_without_table_is_no_data():
    assert build_section("incentives", load_profile_context("TL"), "99001").status == "no_data"
