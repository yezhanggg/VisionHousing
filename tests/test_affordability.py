"""Tests for the cost and affordability tables, the AMI helpers and the profile section.

Everything runs offline on small recorded slices of the published files in
``tests/fixtures/affordability/`` (HUD CHAS 2018-2022 rows and data dictionary
lines, FY2026 income-limit rows, and Vermont rows of the HUD assisted-housing
map services), plus small synthetic cases where an exact answer is needed.
"""

from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tests.pack_fakes import FileServer, client, zip_of
from visionhousing.data.registry import load_registry
from visionhousing.data.sources import affordability as src
from visionhousing.features import affordability as af
from visionhousing.profile.charts import SHARE_DIGITS
from visionhousing.profile.context import load_profile_context
from visionhousing.profile.registry import build_section

FIX = Path(__file__).resolve().parent / "fixtures" / "affordability"
SETTINGS = af.load_settings()


def _extract() -> pd.DataFrame:
    return pd.read_csv(FIX / "chas_extract_vt.csv", dtype={"geoid": str})


def _layer(name: str) -> pd.DataFrame:
    return src.read_layer(FIX / f"{name}_vt.csv")


def _inputs(read_on: str = "2026-11-24") -> src.AffordInputs:
    return src.AffordInputs(
        chas=_extract(),
        income_limits=None,
        mf_assisted=_layer("hud_mf_assisted"),
        public_housing=_layer("hud_public_housing"),
        hcv=_layer("hud_hcv_tract"),
        lihtc=pd.read_csv(FIX / "hud_lihtc_vt.csv", dtype={"hud_id": str, "county": str}),
        read_on=read_on,
    )


def _col(n: int) -> str:
    out = ""
    while n >= 0:
        out = chr(65 + n % 26) + out
        n = n // 26 - 1
    return out


def make_xlsx(rows: list[list]) -> bytes:
    """A minimal one-sheet workbook with inline text cells (enough for ``xlsx_rows``)."""
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    xml_rows = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{_col(c)}{r}"
            if isinstance(value, str):
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    sheet = f'<worksheet xmlns="{main}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
            f'<sheet name="s" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>',
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def _il_rows() -> list[list]:
    with (FIX / "hud_il_fy2026_rows.csv").open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    out = [rows[0]]
    for r in rows[1:]:
        out.append(
            [float(v) if v.replace(".", "", 1).isdigit() and i >= 9 else v for i, v in enumerate(r)]
        )
    return out


# --------------------------------------------------------------------------- CHAS cells


def _dictionary() -> dict[str, str]:
    with (FIX / "chas_dictionary_rows.csv").open(encoding="utf-8") as fh:
        return {r["column"]: r["description"] for r in csv.DictReader(fh)}


INCOME_WORDS = {
    "le30": "less than or equal to 30% of HAMFI",
    "30_50": "greater than 30% but less than or equal to 50% of HAMFI",
    "50_80": "greater than 50% but less than or equal to 80% of HAMFI",
    "80_100": "greater than 80% but less than or equal to 100% of HAMFI",
    "gt100": "greater than 100% of HAMFI",
}
BURDEN_WORDS = {
    "lt30": "Cost burden=less than or equal to 30%",
    "30_50": "Cost burden=greater than 30% but less than or equal to 50%",
    "gt50": "Cost burden=greater than 50%",
    "nc": "Cost burden=not computed",
}


def test_table8_cells_match_hud_dictionary():
    words = _dictionary()
    for name, cell in af.chas_cell_map().items():
        tenure, rest = name.split("_", 1)
        band = next(b for b, _ in af.INCOME_BANDS if rest.startswith(f"{b}_"))
        burden = rest[len(band) + 1 :]
        text = words[f"T8_est{cell}"]
        assert f"Tenure={tenure.title()} occupied" in text, name
        assert f"Household income={INCOME_WORDS[band]}" in text, name
        assert BURDEN_WORDS[burden] in text and "Facilities=All" in text, name


def test_table15c_and_12_cells_match_hud_dictionary():
    words = _dictionary()
    rents = [
        "less than or equal to RHUD30",
        "greater than RHUD30 but less than or equal to RHUD50",
        "greater than RHUD50 but less than or equal to RHUD80",
        "greater than RHUD80",
    ]
    for (_, _, cell), rent in zip(af.RENT_BANDS, rents, strict=True):
        text = words[f"T15C_est{cell}"]
        assert f"Rent={rent}" in text and "Household income=All" in text
    assert "lacks complete kitchen or plumbing" in words["T15C_est2"]
    built = {
        "1960_1979": "between 1960 and 1979",
        "1940_1959": "between 1940 and 1959",
        "pre1940": "1939 or earlier",
    }
    for key, every, low in af.OLDER:
        assert f"Year structure was built={built[key]}" in words[f"T12_est{every}"]
        assert "Household income=All" in words[f"T12_est{every}"]
        assert "less than or equal to 50% of HAMFI" in words[f"T12_est{low}"]
        assert "Renter occupied" in words[f"T12_est{low}"]
    assert "Renter occupied" in words["T12_est88"]


def test_read_chas_table_keeps_the_state_and_plain_codes():
    raw = (FIX / "chas_050_Table8.csv").read_bytes()
    frame = src.read_chas_table(raw, "8", "50", "county")
    assert list(frame["geoid"]) == ["50001", "50007"]
    assert set(frame["level"]) == {"county"}
    assert frame.columns.tolist() == ["level", "geoid", *src.chas_columns("8")]
    assert frame.loc[frame["geoid"] == "50007", "T8_est1"].iloc[0] > 60_000


def test_chas_bands_add_up_to_the_published_totals():
    raw = _extract()
    table = af.chas_table(raw)
    assert list(table["level"]) == ["state", "county"]
    for _, row in table.iterrows():
        published = raw[raw["geoid"] == row["geoid"]].iloc[0]
        for tenure, cell in af.TENURE_BASE.items():
            grid = sum(
                row[f"{tenure}_{b}_{c}"] for b, _ in af.INCOME_BANDS for c, _ in af.BURDEN_BANDS
            )
            # CHAS cells are rounded to 5 households, so sums drift a little
            assert abs(grid - published[f"T8_est{cell}"]) <= 5 * 20
        rents = sum(row[k] for k, _, _ in af.RENT_BANDS) + row["rent_lacking"]
        assert abs(rents - published["T15C_est1"]) <= 25
        moe = row["renter_le30_gt50_moe"]
        assert moe == published[f"T8_moe{af.chas_cell_map()['renter_le30_gt50']}"]


def test_tract_rows_use_the_county_collapse():
    raw = _extract()
    tract = af.chas_tract_table(raw, {"50007000100", "50007000200"})
    assert list(tract["geoid"]) == ["50007000100", "50007000200"]
    assert list(tract.columns) == ["geoid", *af.COLLAPSED]
    wide = af.chas_table(raw[raw["geoid"] == "50007000100"].assign(level="county"))
    expect = af.collapse_renters(wide.iloc[0].to_dict())
    got = tract.iloc[0].to_dict()
    for key, value in expect.items():
        assert got[key] == pytest.approx(value)
    # the three income groups cover every renter band once
    covered = sorted(
        c for k, cells in af.COLLAPSED.items() if k.startswith("renter_") for c in cells
    )
    assert covered == sorted(n for n in af.chas_cell_map(("renter",)))


# --------------------------------------------------------------------------- income limits


def test_read_income_limits_and_one_row_per_county():
    raw = src.read_income_limits(make_xlsx(_il_rows()), "VT")
    assert set(raw["county"]) == {"50007"} and len(raw) == 3
    table = af.income_limits_table(raw)
    assert len(table) == 1
    row = table.iloc[0]
    assert row["median"] == 123_400 and row["l50_4"] == 62_350 and row["l30_1"] == 26_200
    assert row["areas_in_county"] == 1 and row["hud_area_code"] == "METRO15540M15540"
    pa = src.read_income_limits(make_xlsx(_il_rows()), "PA")
    assert list(pa["county"]) == ["42101"] and pa["l80_4"].iloc[0] == 98_150


def test_new_england_county_takes_the_area_with_most_towns():
    base = {f"l{p}_{s}": 1000.0 * s for p in (30, 50, 80) for s in range(1, 9)}
    rows = [
        {
            "county": "50001",
            "hud_area_code": "B",
            "hud_area_name": "b",
            "town": "t1",
            "median": 2.0,
            **base,
        },
        {
            "county": "50001",
            "hud_area_code": "A",
            "hud_area_name": "a",
            "town": "t2",
            "median": 1.0,
            **base,
        },
        {
            "county": "50001",
            "hud_area_code": "B",
            "hud_area_name": "b",
            "town": "t3",
            "median": 2.0,
            **base,
        },
    ]
    table = af.income_limits_table(pd.DataFrame(rows))
    assert table["hud_area_code"].iloc[0] == "B" and table["areas_in_county"].iloc[0] == 2


def test_income_limit_published_derived_and_half_sizes():
    table = af.income_limits_table(src.read_income_limits(make_xlsx(_il_rows()), "PA"))
    row = table.iloc[0].to_dict()
    assert af.income_limit(table, 50, 4, county="42101", settings=SETTINGS) == 61_350
    assert af.income_limit(row, 80, 2, settings=SETTINGS) == 78_550
    assert af.income_limit(row, 60, 4, settings=SETTINGS) == pytest.approx(1.2 * 61_350)
    assert af.income_limit(row, 100, 4, settings=SETTINGS) == pytest.approx(2 * 61_350)
    assert af.income_limit(row, 50, 1.5, settings=SETTINGS) == pytest.approx((42_950 + 49_100) / 2)
    with pytest.raises(ValueError):
        af.income_limit(row, 50, 9, settings=SETTINGS)
    with pytest.raises(KeyError):
        af.income_limit(table, 50, 4, county="99999", settings=SETTINGS)


def test_max_rent_uses_thirty_percent_and_one_and_a_half_people_per_bedroom():
    row = af.income_limits_table(src.read_income_limits(make_xlsx(_il_rows()), "PA")).iloc[0]
    rent = af.max_rent(row.to_dict(), 50, 2, settings=SETTINGS)  # 2 bedrooms = 3 people
    assert rent == pytest.approx(0.30 * 55_250 / 12)
    studio = af.max_rent(row.to_dict(), 30, 0, settings=SETTINGS)  # 1 person
    assert studio == pytest.approx(0.30 * 25_800 / 12)
    one = af.max_rent(row.to_dict(), 80, 1, settings=SETTINGS)  # 1.5 people
    assert one == pytest.approx(0.30 * (68_750 + 78_550) / 2 / 12)


def test_ami_long_is_engine_ready():
    table = af.income_limits_table(src.read_income_limits(make_xlsx(_il_rows()), "PA"))
    long = af.ami_long(table, settings=SETTINGS)
    assert len(long) == 8 * 5
    assert set(long.columns) == {
        "county",
        "hud_area_code",
        "size",
        "pct",
        "income",
        "affordable_cost",
        "published",
    }
    hit = long[(long["size"] == 4) & (long["pct"] == 80)].iloc[0]
    assert hit["income"] == 98_150 and bool(hit["published"])
    assert hit["affordable_cost"] == round(0.3 * 98_150 / 12)
    assert not bool(long[long["pct"] == 60]["published"].iloc[0])


# --------------------------------------------------------------------------- assisted homes


def test_parse_expiration():
    assert src.parse_expiration("31-MAR-30") == date(2030, 3, 31)
    assert src.parse_expiration("28-FEB-43") == date(2043, 2, 28)
    for bad in (None, "", "2030-03-31", "31-XYZ-30", "31-FEB-30"):
        assert src.parse_expiration(bad) is None


SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
SQUARE2 = {"type": "Polygon", "coordinates": [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]]}


def test_assisted_windows_programs_and_vouchers():
    mf = pd.DataFrame(
        {
            "property_id": ["1", "2", "3"],
            "total_unit_count": [100, 50, 10],
            "total_assisted_unit_count": [120, 50, 10],
            "contract_count": [2, 1, 1],
            "units1": [60, 50, 10],
            "units2": [40, 0, 0],
            "expiration_date1": ["01-JAN-28", "30-JUN-34", "01-JAN-20"],
            "expiration_date2": ["01-JAN-40", None, None],
            "program_type1": ["x", "y", "z"],
            "county_level": ["99001", "99001", "99003"],
            "lat": [0.5, 0.5, 1.5],
            "lon": [0.5, 0.5, 1.5],
        }
    )
    ph = pd.DataFrame(
        {
            "development_code": ["d"],
            "total_dwelling_units": [30],
            "acc_units": [30],
            "total_occupied": [29],
            "county_level": ["99001"],
            "lat": [0.2],
            "lon": [1.5],
        }
    )
    lihtc = pd.DataFrame(
        {
            "hud_id": ["a", "b", "c"],
            "units": [40, 20, 10],
            "li_units": [36, None, 10],
            "year_pis": ["1998", "2000", "8888"],
            "county": ["99001", "99001", "99003"],
            "lat": [0.5, 0.5, 1.5],
            "lon": [1.5, 1.5, 0.5],
        }
    )
    hcv = pd.DataFrame(
        {"geoid": ["99001000100", "99003000100", "42001000100"], "hcv_public": [12, None, 5]}
    )
    inputs = src.AffordInputs(
        mf_assisted=mf, public_housing=ph, lihtc=lihtc, hcv=hcv, read_on="2026-11-24"
    )
    shapes = [("99001000100", SQUARE), ("99001000200", SQUARE2)]
    out = af.assisted_table(inputs, "99", shapes, ["99001", "99003"], SETTINGS)
    county = out[out["level"] == "county"].set_index("geoid")
    c1 = county.loc["99001"]
    assert c1["mf_properties"] == 2 and c1["mf_units"] == 100 + 50  # capped at total units
    assert c1["mf_units_exp_5y"] == 60  # 2028 is inside 5 years; 2034 is not
    assert c1["mf_units_exp_10y"] == 60 + 50  # 2040 is outside 10 years
    assert c1["ph_units"] == 30 and c1["lihtc_projects"] == 2
    assert c1["lihtc_li_units"] == 36 + 20  # blank low-income count -> all units
    # placed 1998 and 2000: restriction ends 2028 and 2030 (30 years), both within 5 years
    assert c1["lihtc_li_units_end_5y"] == 56 and c1["lihtc_li_units_end_10y"] == 56
    assert c1["hcv_households"] == 12
    c3 = county.loc["99003"]
    assert c3["mf_units_exp_past"] == 10 and c3["lihtc_year_unknown"] == 1
    assert math.isnan(c3["hcv_households"])  # suppressed by HUD, not zero
    state = out[out["level"] == "state"].iloc[0]
    assert state["geoid"] == "99" and state["mf_units"] == 160
    tracts = out[out["level"] == "tract"].set_index("geoid")
    assert tracts.loc["99001000100", "mf_units"] == 150
    assert tracts.loc["99001000200", "ph_units"] == 30
    assert tracts.loc["99001000200", "lihtc_li_units"] == 56
    assert set(out["as_of"]) == {"2026-11-24"}


def _overlap_inputs(lihtc_lat: float, lihtc_units: float | None, mf_lat: float | None = 0.5):
    mf = pd.DataFrame(
        {
            "property_id": ["1"],
            "total_unit_count": [100],
            "total_assisted_unit_count": [80],
            "contract_count": [1],
            "units1": [80],
            "units2": [0],
            "expiration_date1": ["01-JAN-28"],
            "expiration_date2": [None],
            "program_type1": ["x"],
            "county_level": ["99001"],
            "lat": [mf_lat],
            "lon": [0.5],
        }
    )
    lihtc = pd.DataFrame(
        {
            "hud_id": ["a", "b"],
            "units": [lihtc_units, 30],
            "li_units": [60, 30],
            # 1997 + 30 = 2027 (within 5 years); 1996 + 30 = 2026 = the read year
            "year_pis": ["1997", "1996"],
            "county": ["99001", "99001"],
            "lat": [lihtc_lat, 0.9],
            "lon": [0.5, 0.9],
        }
    )
    return src.AffordInputs(mf_assisted=mf, lihtc=lihtc, read_on="2026-11-24")


def test_at_risk_counts_a_property_on_both_lists_once():
    # 0.0003 degrees of latitude is about 33 m: inside the 50 m match distance
    inputs = _overlap_inputs(0.5003, 95)
    out = af.assisted_table(inputs, "99", [], ["99001"], SETTINGS)
    c = out[(out["level"] == "county")].iloc[0]
    assert c["mf_units_exp_5y"] == 80
    assert c["lihtc_li_units_end_5y"] == 60 + 30  # 2026 restriction counts (end >= read year)
    assert c["at_risk_overlap_5y"] == 60  # min(80 contract, 60 tax-credit) counted once
    assert c["at_risk_5y"] == 80 + 90 - 60
    assert c["at_risk_unchecked_5y"] == 0
    chas = af.chas_table(_extract())
    chas = chas[chas["level"] == "county"].head(1).assign(geoid="99001")
    counties = pd.DataFrame({"geoid": ["99001"], "housing_units": [1000]})
    ledger = af.stock_ledger(chas, out, counties, None, SETTINGS).set_index("geoid")
    assert ledger.loc["99001", "at_risk_5y"] == 110
    assert ledger.loc["99001", "at_risk_basis"] == "matched"


def test_at_risk_needs_both_distance_and_unit_count_to_match():
    far = af.assisted_table(_overlap_inputs(0.5010, 95), "99", [], ["99001"], SETTINGS)
    assert far[far["level"] == "county"].iloc[0]["at_risk_overlap_5y"] == 0  # ~111 m apart
    sizes = af.assisted_table(_overlap_inputs(0.5003, 60), "99", [], ["99001"], SETTINGS)
    assert sizes[sizes["level"] == "county"].iloc[0]["at_risk_overlap_5y"] == 0  # 60 vs 100


def test_at_risk_is_an_upper_bound_when_rows_cannot_be_matched():
    out = af.assisted_table(_overlap_inputs(0.5003, None), "99", [], ["99001"], SETTINGS)
    c = out[out["level"] == "county"].iloc[0]
    assert c["at_risk_overlap_5y"] == 0 and c["at_risk_unchecked_5y"] == 1
    out = af.assisted_table(_overlap_inputs(0.5003, 95, None), "99", [], ["99001"], SETTINGS)
    c = out[out["level"] == "county"].iloc[0]
    assert c["at_risk_unchecked_5y"] == 1  # the contract property has no map point
    chas = af.chas_table(_extract())
    chas = chas[chas["level"] == "county"].head(1).assign(geoid="99001")
    counties = pd.DataFrame({"geoid": ["99001"], "housing_units": [1000]})
    ledger = af.stock_ledger(chas, out, counties, None, SETTINGS).set_index("geoid")
    assert ledger.loc["99001", "at_risk_basis"] == "upper bound"


def test_assisted_on_recorded_vermont_rows_matches_hand_sums():
    inputs = _inputs()
    out = af.assisted_table(inputs, "50", [], ["50007"], SETTINGS)
    c = out[(out["level"] == "county") & (out["geoid"] == "50007")].iloc[0]
    mf = inputs.mf_assisted
    capped = [
        min(a, t) if t and t > 0 else a
        for a, t in zip(mf["total_assisted_unit_count"], mf["total_unit_count"], strict=True)
    ]
    assert c["mf_units"] == pytest.approx(sum(capped))
    assert c["mf_properties"] == len(mf)
    li = inputs.lihtc
    li_units = pd.to_numeric(li["li_units"]).fillna(pd.to_numeric(li["units"]))
    assert c["lihtc_li_units"] == pytest.approx(li_units.sum())
    assert c["hcv_households"] == pytest.approx(inputs.hcv["hcv_public"].sum())
    assert c["ph_units"] == 0  # Vermont's public housing developments are outside Chittenden
    assert (out["level"] == "tract").sum() > 0  # voucher tracts are kept even without points


# --------------------------------------------------------------------------- ledger


def _counties() -> pd.DataFrame:
    return pd.DataFrame(
        {"geoid": ["50007"], "name": ["Chittenden County"], "housing_units": [70_000]}
    )


def test_stock_ledger_columns_and_sums():
    inputs = _inputs()
    chas = af.chas_table(_extract())
    assisted = af.assisted_table(inputs, "50", [], ["50007"], SETTINGS)
    permits = pd.DataFrame(
        {
            "county": ["50007"] * 6,
            "year": range(2020, 2026),
            "units_1": [1] * 6,
            "units_2": [2] * 6,
            "units_3_4": [0] * 6,
            "units_5plus": [10] * 6,
        }
    )
    ledger = af.stock_ledger(chas, assisted, _counties(), permits, SETTINGS)
    row = ledger[ledger["geoid"] == "50007"].iloc[0]
    a = assisted[(assisted["level"] == "county")].iloc[0]
    assert row["authorized_recent"] == 5 * 13 and row["authorized_years"] == "2021-2025"
    assert row["assisted_units"] == a["mf_units"] + a["ph_units"] + a["lihtc_li_units"]
    assert row["at_risk_5y"] == (
        a["mf_units_exp_5y"] + a["lihtc_li_units_end_5y"] - a["at_risk_overlap_5y"]
    )
    assert row["at_risk_10y"] >= row["at_risk_5y"]
    wide = chas[chas["geoid"] == "50007"].iloc[0]
    assert row["lower_rent_units"] == wide["rent_le30"] + wide["rent_30_50"]
    assert row["severe_le50_renters"] == wide["renter_le30_gt50"] + wide["renter_30_50_gt50"]
    assert row["housing_units"] == 70_000
    assert "removed" not in " ".join(ledger.columns)  # not observed; documented, not invented
    assert ledger["level"].iloc[0] == "state"


def test_every_pack_column_is_described():
    inputs = _inputs()
    inputs.income_limits = src.read_income_limits(make_xlsx(_il_rows()), "VT")
    tables = {
        "tracts": pd.DataFrame({"geoid": ["50007000100", "50007000200"]}),
        "counties": _counties(),
    }
    out = af.affordability_tables(inputs, tables, "50", [], SETTINGS)
    assert set(out) == set(af.TABLES)
    registry = load_registry()
    for name, frame in out.items():
        assert all(s in registry for s in af.TABLES[name]["sources"])
        for col in frame.columns:
            unit, text = af.describe(name, col)
            assert unit and text, (name, col)


# --------------------------------------------------------------------------- fetch (offline)


def test_hud_client_names_itself():
    http = src.hud_client(client(FileServer({})))
    assert http.user_agent == src.HUD_USER_AGENT
    assert "visionhousing" in http.user_agent and http.user_agent.startswith(
        "Mozilla/5.0 (compatible;"
    )


def test_fetch_chas_and_limits_through_range_reads(tmp_path):
    reg = load_registry()
    members = {}
    for code in src.CHAS_LEVELS:
        tables = {
            f"{code}/Table{t}.csv": (FIX / f"chas_050_Table{t}.csv").read_bytes()
            for t in src.CHAS_COLUMNS
        }
        url = reg["hud_chas"].url(vintage=src.CHAS_VINTAGE, sumlevel=code, table="8")
        members[url] = zip_of(tables, stored=False)
    il_url = reg["hud_income_limits"].url(yy=f"{src.IL_FISCAL_YEAR % 100:02d}")
    members[il_url] = make_xlsx(_il_rows())
    http = src.hud_client(client(FileServer(members)))
    path = src._fetch_chas("50", tmp_path, http, None)
    frame = pd.read_csv(path, dtype={"geoid": str})
    # the fixture holds county rows only, so each level reads the same two counties,
    # cut to that level's code length
    assert set(frame["level"]) == {"state", "county", "tract"}
    assert sorted(frame.loc[frame["level"] == "county", "geoid"]) == ["50001", "50007"]
    assert set(frame.loc[frame["level"] == "state", "geoid"]) == {"50"}
    il = pd.read_csv(src._fetch_il("VT", "50", tmp_path, http, None), dtype={"county": str})
    assert set(il["county"]) == {"50007"}
    # cached extracts are reused without any request
    again = src.hud_client(client(FileServer({})))
    assert src._fetch_chas("50", tmp_path, again, None) == path


class _LayerClient:
    """Answers map-service queries from one recorded extract."""

    def __init__(self, frame: pd.DataFrame):
        self.features = [
            {
                "attributes": {
                    k.upper(): (None if pd.isna(v) else v) for k, v in row.items() if k != "read_on"
                }
            }
            for row in frame.to_dict("records")
        ]

    def get_bytes(self, url, params=None):
        if params and params.get("returnCountOnly"):
            return json.dumps({"count": len(self.features)}).encode()
        start = int(params["resultOffset"])
        size = int(params["resultRecordCount"])
        return json.dumps({"features": self.features[start : start + size]}).encode()


def test_fetch_layer_pages_and_writes_lower_case_columns(tmp_path):
    rec = pd.read_csv(FIX / "hud_mf_assisted_vt.csv", dtype=str)
    dest = tmp_path / "mf.csv"
    src._fetch_layer("hud_mf_assisted", "STD_ST='VT'", src.MF_FIELDS, dest, _LayerClient(rec), None)
    got = src.read_layer(dest)
    assert len(got) == len(rec)
    assert list(got.columns) == [c.lower() for c in src.MF_FIELDS] + ["read_on"]
    assert got["county_level"].str.len().eq(5).all()


# --------------------------------------------------------------------------- profile section


def _pack(tmp_path: Path) -> Path:
    folder = tmp_path / "VT"
    folder.mkdir()
    tracts = pd.DataFrame(
        {
            "geoid": ["50007000100", "50007000200"],
            "county": ["50007"] * 2,
            "population": [3000, 4000],
            "housing_units": [1500, 1800],
        }
    )
    tracts.to_csv(folder / "tracts.csv", index=False)
    _counties().to_csv(folder / "counties.csv", index=False)
    inputs = _inputs()
    inputs.income_limits = src.read_income_limits(make_xlsx(_il_rows()), "VT")
    out = af.affordability_tables(
        inputs, {"tracts": tracts, "counties": _counties()}, "50", [], SETTINGS
    )
    for name, frame in out.items():
        frame.to_csv(folder / f"{name}.csv", index=False)
    return folder


def _check_charts(section):
    for chart in section.charts:
        shares = [i.share for i in chart.items if i.share is not None]
        assert abs(sum(shares) - 1) <= 0.0001 * len(shares) + 10**-SHARE_DIGITS, chart.id
        for item in chart.items:
            if item.value is not None and chart.total:
                assert item.share == round(item.value / chart.total, SHARE_DIGITS)
        assert chart.kind in ("pie", "bar", "stack", "rank")


def test_affordability_section_for_a_county_and_a_tract(tmp_path):
    ctx = load_profile_context("VT", folder=_pack(tmp_path))
    registry = load_registry()
    county = build_section("affordability", ctx, "50007")
    assert county.status == "ok"
    assert county.headline.fact == "affordability.lowinc_renters_burdened_share"
    assert county.headline.words.endswith("the state")
    assert 2 <= len(county.summary_points) <= 3
    ids = {f.id.split(".")[1]: f for f in county.facts}
    for name in (
        "renters_burdened_share",
        "owners_burdened_share",
        "ami",
        "rent_2br_50",
        "assisted_units",
        "at_risk_5y",
        "at_risk_10y",
        "gap_per_100_eli",
    ):
        assert ids[name].value is not None, name
    assert ids["ami"].value == 123_400 and ids["limit50_4"].value == 62_350
    assert ids["rent_2br_50"].value == round(0.3 * 56_150 / 12)
    assert ids["gap_per_100_eli"].measured_at == "state"
    assert ids["renters_burdened_share"].moe is not None
    for f in county.facts:
        assert f.source in registry, f.id
    chart_ids = {c.id for c in county.charts}
    assert {
        "affordability.burden_le30",
        "affordability.rent_bands",
        "affordability.assisted_programs",
        "affordability.assisted_expiry",
    } <= chart_ids
    _check_charts(county)
    assert all(c.compare is not None for c in county.charts if "burden" in c.id)

    tract = build_section("affordability", ctx, "50007000100")
    assert tract.status == "ok"
    t = {f.id.split(".")[1]: f for f in tract.facts}
    assert "owners_burdened_share" not in t  # owners are county and state only
    assert t["ami"].measured_at == "county" and t["renters_burdened_share"].moe is None
    assert t["renters_burdened_share"].comparison.against == "county"
    _check_charts(tract)


def test_section_says_no_data_without_tables(tmp_path):
    folder = tmp_path / "VT"
    folder.mkdir()
    pd.DataFrame({"geoid": ["50007000100"], "county": ["50007"]}).to_csv(
        folder / "tracts.csv", index=False
    )
    _counties().to_csv(folder / "counties.csv", index=False)
    ctx = load_profile_context("VT", folder=folder)
    assert build_section("affordability", ctx, "50007").status == "no_data"
