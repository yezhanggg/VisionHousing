"""Jobs tables for the state pack: jobs by tract, commuting and businesses by county.

Plain-language summary
----------------------
This module turns the cached job extracts (:mod:`visionhousing.data.sources.jobs`)
into three small pack tables:

``jobs_tract``
    One row per tract in the pack: jobs located there (LODES workplace
    counts), split into three monthly-earnings bands and 20 industry sectors.
    Tracts with no jobs get zeros.

``jobs_county``
    One row per county: jobs located there, and commuting counts from the
    LODES home-to-work flows:

    - ``live_and_work`` = jobs in the county held by people who also live there;
    - ``inflow`` = jobs in the county held by people who live elsewhere
      (another county or another state), so ``jobs = live_and_work + inflow``;
    - ``outflow_in_state`` = county residents working in another county of the
      same state (residents working in other states are not in the state's
      files, so true out-commuting is higher);
    - ``employed_residents_in_state`` = ``live_and_work + outflow_in_state``.

    It also carries County Business Patterns totals: establishments,
    mid-March employment and annual payroll (thousands of dollars), all sectors.

``jobs_county_sector``
    County Business Patterns by county and two-digit NAICS sector.

All functions are pure: tables in, tables out.
"""

from __future__ import annotations

import pandas as pd

from visionhousing.data.sources import jobs as src

__all__ = ["COLUMNS", "TABLES", "jobs_tables"]

SECTOR_NAMES = {code: name for code, name in src.SECTORS.values()}
"""Two-digit NAICS code to sector name (LODES and CBP use the same sectors)."""

TABLES: dict[str, dict[str, object]] = {
    "jobs_tract": {
        "key": ["tract"],
        "sources": ["lodes_wac"],
        "description": (
            "Jobs located in each tract (LODES 8 workplace counts, all jobs), by monthly "
            "earnings band and by industry sector. Tracts with no jobs are zeros."
        ),
    },
    "jobs_county": {
        "key": ["county"],
        "sources": ["lodes_wac", "lodes_od", "cbp_county"],
        "description": (
            "Jobs located in each county, where their workers live (LODES home-to-work flows) "
            "and County Business Patterns totals."
        ),
    },
    "jobs_county_sector": {
        "key": ["county", "naics"],
        "sources": ["cbp_county"],
        "description": "County Business Patterns by county and two-digit NAICS sector.",
    },
}
"""The jobs pack tables: key, sources and a one-line description."""

_BAND_TEXT = {name: text for name, text in src.EARNINGS_BANDS.values()}

COLUMNS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("jobs_tract", "tract"): ("code", "Tract code", "lodes_wac"),
    ("jobs_tract", "jobs"): ("jobs", "Jobs located in the tract, all jobs (LODES C000)", "lodes_wac"),
    **{
        ("jobs_tract", name): ("jobs", f"Jobs paying {text}", "lodes_wac")
        for name, text in _BAND_TEXT.items()
    },
    **{
        ("jobs_tract", f"jobs_naics_{code}"): ("jobs", f"Jobs in {name} (NAICS {code})", "lodes_wac")
        for code, name in SECTOR_NAMES.items()
    },
    ("jobs_county", "county"): ("code", "County code", "lodes_wac"),
    ("jobs_county", "jobs"): ("jobs", "Jobs located in the county (sum of its blocks)", "lodes_wac"),
    ("jobs_county", "live_and_work"): (
        "jobs", "Jobs in the county held by people who live in the county", "lodes_od"),
    ("jobs_county", "inflow"): (
        "jobs", "Jobs in the county held by people who live in another county or state",
        "lodes_od"),
    ("jobs_county", "inflow_out_of_state"): (
        "jobs", "Of the inflow, jobs held by people who live in another state", "lodes_od"),
    ("jobs_county", "outflow_in_state"): (
        "jobs", "County residents with a job in another county of the same state", "lodes_od"),
    ("jobs_county", "employed_residents_in_state"): (
        "jobs", "County residents with a job anywhere in the state (live_and_work + "
        "outflow_in_state); jobs in other states are not counted", "lodes_od"),
    ("jobs_county", "cbp_establishments"): (
        "establishments", "Business establishments with paid employees (CBP, all sectors)",
        "cbp_county"),
    ("jobs_county", "cbp_employment"): (
        "jobs", "Mid-March employment at those establishments (CBP)", "cbp_county"),
    ("jobs_county", "cbp_annual_payroll_k"): (
        "thousand dollars per year", "Annual payroll (CBP)", "cbp_county"),
    ("jobs_county_sector", "county"): ("code", "County code", "cbp_county"),
    ("jobs_county_sector", "naics"): (
        "code", "Two-digit NAICS sector (00 = all sectors; 31 = 31-33, 44 = 44-45, 48 = 48-49)",
        "cbp_county"),
    ("jobs_county_sector", "establishments"): (
        "establishments", "Establishments with paid employees", "cbp_county"),
    ("jobs_county_sector", "employment"): ("jobs", "Mid-March employment", "cbp_county"),
    ("jobs_county_sector", "employment_flag"): (
        "category", "Census noise flag for employment: G low, H medium, J high noise; "
        "N not shown", "cbp_county"),
    ("jobs_county_sector", "annual_payroll_k"): (
        "thousand dollars per year", "Annual payroll", "cbp_county"),
}  # fmt: skip
"""``(table, column) -> (unit, description, source id)`` for the data dictionary."""


def tract_jobs(wac: pd.DataFrame, tracts: list[str]) -> pd.DataFrame:
    """Jobs per pack tract; tracts with no jobs get zeros (columns :data:`src.WAC_COLUMNS`)."""
    frame = wac.set_index("tract").reindex(sorted(tracts)).fillna(0).astype("int64")
    return frame.reset_index().rename(columns={"index": "tract"})[list(src.WAC_COLUMNS)]


def county_flows(od: pd.DataFrame) -> pd.DataFrame:
    """Commuting counts per county from the county-pair table (see the module text)."""
    same = od[od["work_county"] == od["home_county"]].groupby("work_county")["jobs"].sum()
    in_all = od[od["work_county"] != od["home_county"]].groupby("work_county")["jobs"].sum()
    in_out = od[od["home_county"] == src.OUT_OF_STATE].groupby("work_county")["jobs"].sum()
    in_state = od[od["home_county"] != src.OUT_OF_STATE]
    out = in_state[in_state["work_county"] != in_state["home_county"]]
    out = out.groupby("home_county")["jobs"].sum()
    counties = sorted(set(od["work_county"]) | set(in_state["home_county"]))
    frame = pd.DataFrame({"county": counties}).set_index("county")
    frame["live_and_work"] = same
    frame["inflow"] = in_all
    frame["inflow_out_of_state"] = in_out
    frame["outflow_in_state"] = out
    frame = frame.fillna(0).astype("int64")
    frame["employed_residents_in_state"] = frame["live_and_work"] + frame["outflow_in_state"]
    return frame.reset_index()


def jobs_tables(
    wac: pd.DataFrame | None,
    od: pd.DataFrame | None,
    cbp: pd.DataFrame | None,
    tracts: list[str],
    counties: list[str],
) -> dict[str, pd.DataFrame]:
    """Build the jobs pack tables; a missing input leaves its table (or columns) out."""
    out: dict[str, pd.DataFrame] = {}
    county = pd.DataFrame({"county": sorted(counties)})
    if wac is not None:
        per_tract = tract_jobs(wac, tracts)
        out["jobs_tract"] = per_tract
        sums = per_tract.assign(county=per_tract["tract"].str[:5]).groupby("county")["jobs"].sum()
        county["jobs"] = county["county"].map(sums).fillna(0).astype("int64")
    if od is not None:
        flows = county_flows(od)
        county = county.merge(flows, on="county", how="left")
    if cbp is not None:
        cbp = cbp[cbp["county"].isin(set(counties))]
        total = cbp[cbp["naics"] == "00"].set_index("county")
        county["cbp_establishments"] = county["county"].map(total["establishments"])
        county["cbp_employment"] = county["county"].map(total["employment"])
        county["cbp_annual_payroll_k"] = county["county"].map(total["annual_payroll_k"])
        out["jobs_county_sector"] = cbp.sort_values(["county", "naics"]).reset_index(drop=True)
    if len(county.columns) > 1:
        out["jobs_county"] = county
    return out
