"""House prices, mortgage rates and construction wages: readers and summaries (no network)."""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from tests.pack_fakes import FileServer, client
from visionhousing.data.registry import get_source
from visionhousing.data.sources import costs, prices

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def make_xlsx(rows: list[list]) -> bytes:
    """A minimal workbook: text cells go to the shared strings, numbers stay inline."""
    strings: list[str] = []
    cells_xml = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if value is None:
                cells.append(f'<c r="{ref}"/>')
            elif isinstance(value, str):
                if value not in strings:
                    strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{strings.index(value)}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
        cells_xml.append(f'<row r="{r}">{"".join(cells)}</row>')
    sheet = f'<worksheet xmlns="{MAIN}"><sheetData>{"".join(cells_xml)}</sheetData></worksheet>'
    sst = "".join(f"<si><t>{s}</t></si>" for s in strings)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{MAIN}" xmlns:r="{RELS}"><sheets>'
            f'<sheet name="county" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        zf.writestr("xl/sharedStrings.xml", f'<sst xmlns="{MAIN}">{sst}</sst>')
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


COUNTY_ROWS = [
    ["HPI for Counties"],
    [None],
    ["State", "County", "FIPS code", "Year", "Annual Change (%)", "HPI", "HPI with 1990 base",
     "HPI with 2000 base"],
    ["PA", "Adams", "42001", 2015, None, 100, 150.5, 120.0],
    ["PA", "Adams", "42001", 2020, 4.0, 125, ".", 150.0],
    ["PA", "Adams", "42001", 2025, 6.5, 150, 225.75, 180.0],
    ["PA", "Cameron", "42023", 2025, ".", ".", ".", "."],
    ["NY", "Albany", "36001", 2025, 3.0, 200, 300, 250],
]  # fmt: skip


def test_xlsx_rows_reads_shared_strings_numbers_and_blanks():
    rows = list(prices.xlsx_rows(make_xlsx(COUNTY_ROWS)))
    assert rows[0] == ["HPI for Counties"]
    assert rows[3][:4] == ["PA", "Adams", "42001", 2015.0]
    assert rows[3][4] is None


def test_read_hpi_county_finds_header_and_filters():
    out = prices.read_hpi_county(make_xlsx(COUNTY_ROWS), "42")
    assert list(out.columns) == list(prices.HPI_COLUMNS)
    assert sorted(set(out["geoid"])) == ["42001", "42023"]
    adams = out[out["geoid"] == "42001"].set_index("year")
    assert adams.loc[2025, "hpi"] == 150 and pd.isna(adams.loc[2020, "hpi_1990_base"])
    assert out[out["geoid"] == "42023"]["hpi"].isna().all()
    with pytest.raises(ValueError, match="FIPS code"):
        prices.read_hpi_county(make_xlsx([["no header here"]]))


def test_hpi_changes_by_hand():
    long = prices.read_hpi_county(make_xlsx(COUNTY_ROWS), "42")
    out = prices.hpi_changes(long).set_index("geoid")
    adams = out.loc["42001"]
    assert adams["hpi_year"] == 2025
    assert adams["hpi_change_5yr_pct"] == 20.0  # 150 / 125 - 1
    assert adams["hpi_change_10yr_pct"] == 50.0  # 150 / 100 - 1
    assert adams["hpi_annual_change_pct"] == 6.5
    assert out.loc["42023"].drop("hpi_year").isna().all()


def test_read_hpi_tract_and_fetch_keeps_state_rows(tmp_path):
    body = (
        b"tract,state_abbr,year,annual_change,hpi,hpi1990,hpi2000\n"
        b"36001000100,NY,2025,1.0,110.0,,\n"
        b"42001030101,PA,2024,,100.00,,90.0\n"
        b"42001030101,PA,2025,5.00,105.00,,94.5\n"
    )
    url = get_source("fhfa_hpi_tract").url()
    server = FileServer({url: body})
    out = prices.fetch_hpi_tract("PA", tmp_path, client=client(server), manifest=None)
    assert list(out["geoid"]) == ["42001030101", "42001030101"]
    assert (tmp_path / "prices" / "hpi_at_tract_42.csv").read_bytes().count(b"\n") == 3


def test_fetch_hpi_county_deletes_the_workbook(tmp_path):
    url = get_source("fhfa_hpi_county").url()
    server = FileServer({url: make_xlsx(COUNTY_ROWS)})
    out = prices.fetch_hpi_county("PA", tmp_path, client=client(server), manifest=None)
    assert set(out["geoid"]) == {"42001", "42023"}
    assert sorted(p.name for p in (tmp_path / "prices").iterdir()) == ["hpi_at_county_42.csv"]
    again = prices.fetch_hpi_county("PA", tmp_path, client=client(FileServer({})), manifest=None)
    pd.testing.assert_frame_equal(again, out)


def test_pmms_annual_average(tmp_path):
    path = tmp_path / "PMMS_history.csv"
    path.write_text(
        "date,pmms30,pmms30p,pmms15,pmms15p\n"
        "12/26/2024,6.85, ,6.00,\n"
        "1/2/2025,7.00, ,6.20,\n"
        "1/9/2025,6.00, ,,\n"
        ",\n",
        encoding="utf-8",
    )
    annual = prices.pmms_annual(prices.read_pmms(path)).set_index("year")
    assert annual.loc[2025, "rate_30yr_pct"] == 6.5
    assert annual.loc[2025, "rate_15yr_pct"] == 6.2
    assert annual.loc[2025, "weeks_30yr"] == 2 and annual.loc[2025, "weeks_15yr"] == 1
    assert annual.loc[2024, "rate_30yr_pct"] == 6.85


QCEW = (
    '"area_fips","own_code","industry_code","agglvl_code","size_code","year","qtr",'
    '"disclosure_code","annual_avg_estabs","annual_avg_emplvl","total_annual_wages",'
    '"avg_annual_pay"\n'
    '"US000","5","23","14","0","2025","A","","1","8000000","1","80000"\n'
    '"42000","5","23","54","0","2025","A","","1","260000","1","84000"\n'
    '"42000","2","23","54","0","2025","A","","1","5000","1","60000"\n'
    '"42001","5","23","74","0","2025","A","","1","1500","1","75600"\n'
    '"42003","5","23","74","0","2025","A","N","0","0","0","0"\n'
    '"36001","5","23","74","0","2025","A","","1","9000","1","90000"\n'
)


def test_cost_adjusters_by_hand(tmp_path):
    path = tmp_path / "q.csv"
    path.write_text(QCEW, encoding="utf-8")
    q = costs.read_qcew(path)
    assert set(q["level"]) == {"nation", "state", "county"}
    out = costs.cost_adjusters(q, "42", ["42003", "42001", "42005"]).set_index("county")
    assert list(out.reset_index().columns) == list(costs.COLUMNS)
    assert out.loc["42001", "wage_index_county_vs_state"] == 0.9  # 75,600 / 84,000
    assert out.loc["42001", "wage_index_method"] == "county"
    assert out.loc["42001", "wage_index_state_vs_us"] == 1.05  # 84,000 / 80,000
    for withheld in ("42003", "42005"):  # flagged N, and not listed at all
        assert out.loc[withheld, "wage_index_county_vs_state"] == 1.0
        assert out.loc[withheld, "wage_index_method"] == "state_value"
        assert pd.isna(out.loc[withheld, "construction_avg_annual_pay"])
    with pytest.raises(ValueError, match="state or national"):
        costs.cost_adjusters(q[q["level"] != "nation"], "42", ["42001"])


def test_cost_adjusters_when_the_state_pay_is_withheld(tmp_path):
    """Small states: BLS withholds the statewide figure; the build degrades, it does not fail."""
    path = tmp_path / "q.csv"
    path.write_text(QCEW, encoding="utf-8")
    q = costs.read_qcew(path)
    state = q["level"] == "state"
    hidden = q.copy()
    hidden.loc[state, "disclosure_code"] = "N"
    hidden.loc[state, "avg_annual_pay"] = 0
    out = costs.cost_adjusters(hidden, "42", ["42001", "42003"]).set_index("county")
    # Only 42001 is published, so the state stand-in is its pay and its index is 1.0.
    assert out.loc["42001", "wage_index_county_vs_state"] == 1.0
    assert out.loc["42001", "wage_index_method"] == "county"
    assert out.loc["42003", "wage_index_method"] == "state_from_counties"
    assert out.loc["42001", "wage_index_state_vs_us"] == 0.945  # 75,600 / 80,000
    no_counties = hidden[hidden["level"] != "county"]
    out = costs.cost_adjusters(no_counties, "42", ["42001"]).set_index("county")
    assert out.loc["42001", "wage_index_method"] == "national_value"
    assert out.loc["42001", "wage_index_state_vs_us"] == 1.0


def test_fetch_qcew_keeps_state_and_us_rows(tmp_path):
    url = get_source("bls_qcew_construction").url(year=2025)
    server = FileServer({url: QCEW.encode()})
    q = costs.fetch_qcew_construction("PA", tmp_path, client=client(server), manifest=None)
    assert set(q["area_fips"]) == {"US000", "42000", "42001", "42003"}
