"""Construction cost adjusters by county, from construction wages (BLS QCEW).

Plain-language summary
----------------------
Building costs differ from place to place, mostly because labour costs differ.
The model's cost per square foot is a ledger parameter with a range; this module
gives the regional adjustment that scales it by county.

Which source, and why
---------------------
Two public, key-free sources were checked on 2026-11-22:

- BEA regional price parities (``SARPP.zip``, ``MARPP.zip``) answered, but only
  for states and metro areas, and they measure consumer prices, not building
  costs.
- BLS Quarterly Census of Employment and Wages (QCEW) answered with one CSV per
  industry and year for every county. It counts jobs and pay from unemployment
  insurance records, so it covers almost all employers.

QCEW was chosen: it is by county and it is about construction. The file used is
the annual average for NAICS sector 23 (construction), private employers
(ownership code 5), at county level (aggregation level 74), state level (54) and
national level (14).

What the pack holds (``costs_county``)
--------------------------------------
- ``construction_employment``: average jobs in construction in the year.
- ``construction_avg_annual_pay``: average pay per job, dollars.
- ``wage_index_county_vs_state`` = county pay / state pay.
- ``wage_index_state_vs_us`` = state pay / national pay (the same for every
  county; it lets one formula scale national costs to the state and then the
  county).
- ``wage_index_method``: ``county`` where BLS publishes the county figure, and
  ``state_value`` where it does not (BLS withholds figures that would reveal a
  single employer; these are flagged "N"). There the county index is 1.0.
  Where BLS also withholds the **state** figure (small states, such as
  Vermont), the state pay is the job-weighted mean of the published counties
  and withheld counties are marked ``state_from_counties`` (or
  ``national_value`` when no county is published either).

The model turns the wage index into a cost index with a labour share from the
ledger (only part of a building's cost is labour); that step is not done here.
Small counties with few construction jobs have noisy pay; their index should be
read with care.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.data.sources.acs import stream_filter

__all__ = [
    "COLUMNS",
    "QCEW_YEAR",
    "cost_adjusters",
    "fetch_qcew_construction",
    "read_qcew",
]

QCEW_YEAR = 2025
"""Latest full year of QCEW annual averages (checked 2026-11-22)."""
SOURCE_ID = "bls_qcew_construction"
PRIVATE = "5"
LEVELS = {"14": "nation", "54": "state", "74": "county"}

COLUMNS = (
    "county",
    "year",
    "construction_employment",
    "construction_avg_annual_pay",
    "wage_index_county_vs_state",
    "wage_index_state_vs_us",
    "wage_index_method",
)
"""Columns of the ``costs_county`` table."""

_NEEDED = (
    "area_fips",
    "own_code",
    "industry_code",
    "agglvl_code",
    "year",
    "qtr",
    "disclosure_code",
    "annual_avg_emplvl",
    "avg_annual_pay",
)


def read_qcew(source: str | Path) -> pd.DataFrame:
    """Read a QCEW annual industry CSV; keep private-employer rows at nation, state, county level.

    Returns:
        Columns ``area_fips, level, year, disclosure_code, annual_avg_emplvl,
        avg_annual_pay``.
    """
    frame = pd.read_csv(source, dtype=str, keep_default_na=False)
    missing = [c for c in _NEEDED if c not in frame.columns]
    if missing:
        raise ValueError(f"QCEW file lacks {missing}")
    frame = frame[(frame["own_code"] == PRIVATE) & frame["agglvl_code"].isin(list(LEVELS))].copy()
    if (frame["qtr"] != "A").any():
        raise ValueError("QCEW file is not an annual-average file (qtr must be 'A')")
    frame["level"] = frame["agglvl_code"].map(LEVELS)
    frame["year"] = frame["year"].astype("int64")
    for name in ("annual_avg_emplvl", "avg_annual_pay"):
        frame[name] = pd.to_numeric(frame[name], errors="raise").astype("int64")
    cols = ["area_fips", "level", "year", "disclosure_code", "annual_avg_emplvl", "avg_annual_pay"]
    return frame[cols].sort_values(["level", "area_fips"]).reset_index(drop=True)


def _state_pay(
    qcew: pd.DataFrame, state_fips: str, state: pd.Series, nation_pay: float
) -> tuple[float, str]:
    """The state's construction pay and how it was found.

    BLS withholds the statewide private figure in some small states (Vermont in
    2025) to protect a large employer's data. The model then degrades instead of
    failing: the job-weighted mean pay of the counties BLS does publish
    (``state_from_counties``), or, with none published, the national figure
    (``national_value``).
    """
    if state["disclosure_code"] != "N" and state["avg_annual_pay"] > 0:
        return float(state["avg_annual_pay"]), "state"
    counties = qcew[
        (qcew["level"] == "county")
        & qcew["area_fips"].str.startswith(state_fips)
        & ~qcew["area_fips"].str.endswith("999")  # "county unknown" rows
        & (qcew["disclosure_code"] != "N")
        & (qcew["avg_annual_pay"] > 0)
        & (qcew["annual_avg_emplvl"] > 0)
    ]
    if len(counties):
        jobs = counties["annual_avg_emplvl"].astype(float)
        return float((counties["avg_annual_pay"] * jobs).sum() / jobs.sum()), "state_from_counties"
    return nation_pay, "national_value"


def cost_adjusters(qcew: pd.DataFrame, state_fips: str, counties: list[str]) -> pd.DataFrame:
    """Build the ``costs_county`` table for the given county codes.

    Args:
        qcew: Output of :func:`read_qcew`.
        state_fips: Two-digit state code.
        counties: Five-digit county codes the pack holds; each gets a row.

    Raises:
        ValueError: If the state or national row is missing, or the national pay is withheld.
    """
    rows = qcew.set_index(["level", "area_fips"])
    try:
        state = rows.loc[("state", f"{state_fips}000")]
        nation = rows.loc[("nation", "US000")]
    except KeyError as err:
        raise ValueError(f"QCEW file lacks the state or national row: {err}") from None
    if nation["disclosure_code"] == "N" or nation["avg_annual_pay"] <= 0:
        raise ValueError("QCEW nation construction pay is withheld")
    year = int(state["year"])
    state_pay, state_method = _state_pay(qcew, state_fips, state, float(nation["avg_annual_pay"]))
    out = []
    for county in sorted(counties):
        key = ("county", county)
        shown = key in rows.index and rows.loc[key, "disclosure_code"] != "N"
        shown = shown and rows.loc[key, "avg_annual_pay"] > 0
        if shown:
            jobs = int(rows.loc[key, "annual_avg_emplvl"])
            pay = int(rows.loc[key, "avg_annual_pay"])
            index, method = round(pay / state_pay, 4), "county"
        else:
            jobs = pay = None
            index, method = 1.0, "state_value"
        if state_method != "state" and method == "state_value":
            method = state_method  # the stand-in for the state is itself an estimate
        out.append(
            {
                "county": county,
                "year": year,
                "construction_employment": jobs,
                "construction_avg_annual_pay": pay,
                "wage_index_county_vs_state": index,
                "wage_index_state_vs_us": round(state_pay / float(nation["avg_annual_pay"]), 4),
                "wage_index_method": method,
            }
        )
    frame = pd.DataFrame(out, columns=list(COLUMNS))
    return frame.astype(
        {"construction_employment": "Int64", "construction_avg_annual_pay": "Int64"}
    )


def fetch_qcew_construction(
    state: str,
    cache: Path,
    *,
    year: int = QCEW_YEAR,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Stream the national QCEW construction file; keep the state's and the U.S. rows."""
    fips = to_state_fips(state)
    dest = Path(cache) / "costs" / f"qcew_{year}_naics23_{fips}.csv"
    if not dest.exists():
        url = get_source(SOURCE_ID).url(year=year)
        wanted = (f'"{fips}'.encode("ascii"), b'"US000"')

        def make_keep(header: bytes):
            if not header.startswith(b'"area_fips"'):
                raise ValueError("QCEW file does not start with an area_fips column")
            return lambda line: line.startswith(wanted)

        stream_filter(client or HttpClient(), url, dest, make_keep)
        if manifest is not None:
            mf.record(mf.entry_for_file(SOURCE_ID, url, dest, str(year), root=mf.ROOT), manifest)
    return read_qcew(dest)
