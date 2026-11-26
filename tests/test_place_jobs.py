"""Jobs data for place profiles: LODES and CBP readers, the jobs tables and the ``jobs`` section.

Everything runs on small hand-made files in ``tests/fixtures/place`` and the Testland
pack; no network is used.
"""

from __future__ import annotations

import gzip
import io
import shutil
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from tests.pack_fakes import FileServer, client
from visionhousing.data.registry import get_source
from visionhousing.data.sources import jobs as src
from visionhousing.engine.context import TESTLAND_DIR
from visionhousing.features import jobs as fx
from visionhousing.profile import load_profile_context
from visionhousing.profile.registry import build_section

FIX = Path(__file__).resolve().parent / "fixtures" / "place"


def wac() -> pd.DataFrame:
    with (FIX / "lodes_wac_sample.csv").open(newline="") as fh:
        return src.sum_wac_to_tracts(fh)


def od() -> pd.DataFrame:
    counts: dict[tuple[str, str], int] = {}
    for name, aux in (("lodes_od_main_sample.csv", False), ("lodes_od_aux_sample.csv", True)):
        with (FIX / name).open(newline="") as fh:
            src.sum_od_to_counties(fh, counts, aux=aux)
    rows = [(w, h, n) for (w, h), n in sorted(counts.items())]
    return pd.DataFrame(rows, columns=list(src.OD_COLUMNS))


def cbp() -> pd.DataFrame:
    return src.read_cbp(FIX / "cbp_county_sample.txt", "99")


# --------------------------------------------------------------------------- readers


def test_wac_blocks_sum_to_tracts():
    t = wac().set_index("tract")
    assert list(t.columns) == list(src.WAC_COLUMNS[1:])
    assert t.loc["99001000100", "jobs"] == 16
    assert t.loc["99001000100", "jobs_earn_high"] == 8
    assert t.loc["99001000100", "jobs_naics_62"] == 5
    assert t.loc["99001000100", "jobs_naics_44"] == 6
    assert t.loc["99003000100", "jobs"] == 20
    bands = [name for name, _ in src.EARNINGS_BANDS.values()]
    assert (t[bands].sum(axis=1) == t["jobs"]).all()


def test_wac_needs_its_columns():
    with pytest.raises(ValueError, match="lacks"):
        src.sum_wac_to_tracts(["w_geocode,C000\n", "990010001001000,3\n"])


def test_od_counts_by_county_pair_and_out_of_state():
    pairs = od().set_index(["work_county", "home_county"])["jobs"]
    assert pairs[("99001", "99001")] == 6 + 6 + 3
    assert pairs[("99001", "99003")] == 4
    assert pairs[("99001", src.OUT_OF_STATE)] == 1
    assert pairs[("99003", src.OUT_OF_STATE)] == 2
    # every job in the workplace file appears once in the flow files
    assert od()["jobs"].sum() == wac()["jobs"].sum()


def test_cbp_keeps_state_sector_rows_and_drops_statewide():
    t = cbp()
    assert list(t.columns) == list(src.CBP_COLUMNS)
    assert set(t["county"]) == {"99001", "99003"}
    assert set(t["naics"]) == {"00", "23", "62", "44"}  # the 6-digit row is dropped
    total = t[(t.county == "99001") & (t.naics == "00")].iloc[0]
    assert total["establishments"] == 30 and total["employment"] == 120
    assert total["annual_payroll_k"] == 6000 and total["employment_flag"] == "G"


# --------------------------------------------------------------------------- fetchers


def _gz(path: Path) -> bytes:
    return gzip.compress(path.read_bytes(), mtime=0)


def test_fetchers_keep_only_sums_and_reuse_the_cache(tmp_path):
    wac_url = get_source("lodes_wac").url(st="pa", year=src.LODES_YEAR)
    main = get_source("lodes_od").url(st="pa", part="main", year=src.LODES_YEAR)
    aux = get_source("lodes_od").url(st="pa", part="aux", year=src.LODES_YEAR)
    cbp_url = get_source("cbp_county").url(year=src.CBP_YEAR, yy=f"{src.CBP_YEAR % 100:02d}")
    text = (FIX / "cbp_county_sample.txt").read_text().replace('"99"', '"42"')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("cbp23co.txt", text)
    pa = {
        p: f.read_text().replace(",99", ",42").replace("\n99", "\n42")
        for p, f in (
            ("wac", FIX / "lodes_wac_sample.csv"),
            ("main", FIX / "lodes_od_main_sample.csv"),
            ("aux", FIX / "lodes_od_aux_sample.csv"),
        )
    }
    server = FileServer(
        {
            wac_url: gzip.compress(pa["wac"].encode(), mtime=0),
            main: gzip.compress(pa["main"].encode(), mtime=0),
            aux: gzip.compress(pa["aux"].encode(), mtime=0),
            cbp_url: buf.getvalue(),
        }
    )
    c = client(server)
    w = src.fetch_wac_tracts("PA", tmp_path, client=c, manifest=None)
    o = src.fetch_od_county("PA", tmp_path, client=c, manifest=None)
    b = src.fetch_cbp("PA", tmp_path, client=c, manifest=None)
    assert w["jobs"].sum() == 40 and w["tract"].str.startswith("42").all()
    assert o["jobs"].sum() == 40
    assert set(b["county"]) == {"42001", "42003"}
    kept = sorted(p.name for p in (tmp_path / "jobs").iterdir())
    assert kept == [
        f"cbp_{src.CBP_YEAR}_county_42.csv",
        f"lodes_od_{src.LODES_YEAR}_counties_42.csv",
        f"lodes_wac_{src.LODES_YEAR}_tracts_42.csv",
    ]
    calls = len(server.urls)
    src.fetch_wac_tracts("PA", tmp_path, client=c, manifest=None)
    src.fetch_cbp("PA", tmp_path, client=c, manifest=None)
    assert len(server.urls) == calls  # read from the cache


# --------------------------------------------------------------------------- tables


TRACTS = ["99001000100", "99001000200", "99001000300", "99003000100"]
COUNTIES = ["99001", "99003"]


def test_jobs_tables_fill_zeros_and_balance_flows():
    tables = fx.jobs_tables(wac(), od(), cbp(), TRACTS, COUNTIES)
    t = tables["jobs_tract"].set_index("tract")
    assert list(t.index) == TRACTS
    assert t.loc["99001000300"].sum() == 0  # a tract with no jobs is all zeros
    c = tables["jobs_county"].set_index("county")
    # jobs located = held by residents + held by people living elsewhere
    assert (c["jobs"] == c["live_and_work"] + c["inflow"]).all()
    assert c.loc["99001", "inflow"] == 4 + 1 and c.loc["99001", "inflow_out_of_state"] == 1
    assert c.loc["99003", "outflow_in_state"] == 4
    assert c.loc["99001", "outflow_in_state"] == 6
    assert (c["employed_residents_in_state"] == c["live_and_work"] + c["outflow_in_state"]).all()
    assert c.loc["99001", "cbp_establishments"] == 30
    assert set(tables["jobs_county_sector"]["county"]) == set(COUNTIES)
    for table, frame in tables.items():
        for col in frame.columns:
            assert (table, col) in fx.COLUMNS, (table, col)


def test_jobs_tables_without_inputs_are_left_out():
    assert fx.jobs_tables(None, None, None, TRACTS, COUNTIES) == {}
    only = fx.jobs_tables(wac(), None, None, TRACTS, COUNTIES)
    assert set(only) == {"jobs_tract", "jobs_county"}
    assert list(only["jobs_county"].columns) == ["county", "jobs"]


# --------------------------------------------------------------------------- section


@pytest.fixture
def pack(tmp_path):
    """Testland's pack plus jobs tables built from the fixtures."""
    folder = tmp_path / "TL"
    shutil.copytree(TESTLAND_DIR, folder)
    tracts = pd.read_csv(folder / "tracts.csv", dtype={"geoid": str})["geoid"].tolist()
    counties = pd.read_csv(folder / "counties.csv", dtype={"geoid": str})["geoid"].tolist()
    for name, frame in fx.jobs_tables(wac(), od(), cbp(), tracts, counties).items():
        frame.to_csv(folder / f"{name}.csv", index=False)
    return load_profile_context("TL", folder=folder)


def test_jobs_section_county_numbers_trace_to_the_tables(pack):
    sec = build_section("jobs", pack, "99001")
    assert sec.status == "ok"
    assert sec.fact("jobs.jobs_located").value == 20
    people = pd.read_csv(TESTLAND_DIR / "tracts.csv", dtype={"county": str})
    residents = people.loc[people.county == "99001", "population"].sum()
    assert sec.fact("jobs.jobs_per_resident").value == pytest.approx(20 / residents)
    assert sec.fact("jobs.high_earning_share").value == pytest.approx(8 / 20)
    assert sec.fact("jobs.inflow_share").value == pytest.approx(5 / 20)
    assert sec.fact("jobs.avg_pay").value == pytest.approx(6000 / 120 * 1000)
    assert sec.fact("jobs.jobs_per_resident").comparison.against == "state"
    assert "Harbor County has 20 jobs" in sec.summary
    assert sec.limits and set(sec.sources) <= {
        "lodes_wac",
        "lodes_od",
        "cbp_county",
        "acs5_profile_tables",
    }


def test_jobs_section_tract_uses_county_for_flows(pack):
    sec = build_section("jobs", pack, "99001000100")
    assert sec.fact("jobs.jobs_located").value == 16
    assert sec.fact("jobs.jobs_per_resident").comparison.against == "county"
    flow = sec.fact("jobs.inflow_share")
    assert flow.measured_at == "county" and flow.comparison.against == "state"
    top = sec.fact("jobs.sector_1")
    assert "Retail" in top.label and top.value == pytest.approx(6 / 16)


def test_jobs_section_without_table_is_no_data():
    ctx = load_profile_context("TL")
    assert build_section("jobs", ctx, "99001").status == "no_data"
