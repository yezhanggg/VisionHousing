"""Incentive-area tables for the state pack: which tracts and counties carry which programs.

Plain-language summary
----------------------
This module places every incentive source (:mod:`visionhousing.data.sources.incentives`)
on the pack's 2020 tracts and adds them up by county.

``incentives_tract`` (one row per pack tract)
    - ``qct``: 1 if HUD lists the tract as a Qualified Census Tract.
    - ``dda_land_share``: share of the tract's land inside a Difficult
      Development Area. Metro DDAs are ZIP Code Tabulation Areas (ZCTAs), so
      the share is the tract land shared with DDA ZCTAs / all its land shared
      with ZCTAs; a non-metro DDA is a whole county, so its tracts get 1.
      ``dda`` is 1 when that share is at least one half.
    - ``oz2027`` and ``oz2027_rural``: 1 if Treasury designated the tract an
      Opportunity Zone from 2027 (and whether it is a rural zone).
    - ``nmtc_lic``: 1 if the tract meets the New Markets Tax Credit poverty or
      income test; ``nmtc_poverty_rate`` (percent) and ``nmtc_mfi_pct`` (tract
      median family income as a percent of its benchmark) are the test inputs.
    - ``lihtc_projects``, ``lihtc_units``, ``lihtc_li_units``: tax-credit
      projects whose point falls in the tract, their units and low-income units.
    - ``nrhp_listings``: National Register listings whose point (or district
      polygon's centre of vertices) falls in the tract; ``nrhp_district_units``:
      2020 Census homes in blocks whose centre is inside a listed historic
      district.
    - ``koz_sites`` and ``koz_acres``: Keystone Opportunity Zone sites
      (Pennsylvania only) whose point falls in the tract.

``incentives_county`` (one row per county)
    Sums of the tract columns, plus counts taken straight from the records
    (LIHTC by the county HUD records, KOZ by the county DCED names), so a point
    with a wrong location still counts in its county. ``dda_nonmetro`` is 1 for
    a county that is a non-metro DDA as a whole.

Points are placed in tracts with the even-odd point-in-polygon test of
:func:`visionhousing.features.transit.points_in_polygons`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.data.sources.incentives import IncentiveInputs
from visionhousing.features.transit import points_in_polygons

__all__ = ["COLUMNS", "DDA_MAJORITY", "TABLES", "incentive_tables"]

DDA_MAJORITY = 0.5
"""A tract counts as in a DDA when at least this share of its land is (a definition, not a
model parameter: HUD designates ZCTAs, not tracts)."""
UNKNOWN_YEARS = {"8888", "9999", ""}
"""HUD's codes for an unknown placed-in-service year."""

TABLES: dict[str, dict[str, object]] = {
    "incentives_tract": {
        "key": ["tract"],
        "sources": [
            "hud_qct", "hud_dda", "census_zcta_tract_rel", "treasury_oz2027", "cdfi_nmtc_lic",
            "hud_lihtc", "nps_nrhp", "dced_koz_sites",
        ],
        "description": (
            "Incentive areas per tract: Qualified Census Tract, Difficult Development Area, "
            "2027 Opportunity Zone, New Markets Tax Credit test, tax-credit projects, "
            "National Register listings and Keystone Opportunity Zone sites."
        ),
    },
    "incentives_county": {
        "key": ["county"],
        "sources": ["hud_qct", "hud_dda", "treasury_oz2027", "cdfi_nmtc_lic", "hud_lihtc",
                    "nps_nrhp", "dced_koz_sites"],
        "description": "Incentive areas per county: tract counts and program totals.",
    },
}  # fmt: skip
"""The incentive pack tables: key, sources and a one-line description."""

_T, _C = "incentives_tract", "incentives_county"
COLUMNS: dict[tuple[str, str], tuple[str, str, str]] = {
    (_T, "tract"): ("code", "Tract code", "hud_qct"),
    (_T, "qct"): ("flag", "1 if a HUD Qualified Census Tract", "hud_qct"),
    (_T, "dda_land_share"): (
        "share", "Share of the tract's land in a Difficult Development Area", "hud_dda"),
    (_T, "dda"): ("flag", "1 if at least half the tract's land is in a DDA", "hud_dda"),
    (_T, "oz2027"): ("flag", "1 if a Qualified Opportunity Zone from 2027", "treasury_oz2027"),
    (_T, "oz2027_rural"): ("flag", "1 if a rural Opportunity Zone from 2027", "treasury_oz2027"),
    (_T, "nmtc_lic"): (
        "flag", "1 if the tract meets the NMTC low-income community poverty or income test",
        "cdfi_nmtc_lic"),
    (_T, "nmtc_poverty_rate"): ("percent", "Poverty rate, 2016-2020 ACS (NMTC file)",
                                "cdfi_nmtc_lic"),
    (_T, "nmtc_mfi_pct"): (
        "percent", "Tract median family income as a percent of its benchmark (NMTC file)",
        "cdfi_nmtc_lic"),
    (_T, "lihtc_projects"): ("projects", "LIHTC projects located in the tract", "hud_lihtc"),
    (_T, "lihtc_units"): ("units", "Units in those projects", "hud_lihtc"),
    (_T, "lihtc_li_units"): ("units", "Low-income units in those projects", "hud_lihtc"),
    (_T, "nrhp_listings"): ("listings", "National Register listings located in the tract",
                            "nps_nrhp"),
    (_T, "nrhp_district_units"): (
        "units", "2020 Census homes in blocks whose centre is in a listed historic district",
        "nps_nrhp"),
    (_T, "koz_sites"): ("sites", "Keystone Opportunity Zone sites (PA only)", "dced_koz_sites"),
    (_T, "koz_acres"): ("acres", "Acres of those sites", "dced_koz_sites"),
    (_C, "county"): ("code", "County code", "hud_qct"),
    (_C, "tracts"): ("tracts", "Tracts in the county (pack tracts)", "hud_qct"),
    (_C, "qct_tracts"): ("tracts", "Qualified Census Tracts", "hud_qct"),
    (_C, "dda_tracts"): ("tracts", "Tracts at least half in a DDA", "hud_dda"),
    (_C, "dda_nonmetro"): ("flag", "1 if the whole county is a non-metro DDA", "hud_dda"),
    (_C, "oz2027_tracts"): ("tracts", "Opportunity Zone tracts from 2027", "treasury_oz2027"),
    (_C, "nmtc_lic_tracts"): ("tracts", "Tracts meeting the NMTC poverty or income test",
                              "cdfi_nmtc_lic"),
    (_C, "lihtc_projects"): ("projects", "LIHTC projects HUD records in the county", "hud_lihtc"),
    (_C, "lihtc_units"): ("units", "Units in those projects", "hud_lihtc"),
    (_C, "lihtc_li_units"): ("units", "Low-income units in those projects", "hud_lihtc"),
    (_C, "lihtc_latest_year"): ("year", "Latest known placed-in-service year", "hud_lihtc"),
    (_C, "nrhp_listings"): ("listings", "National Register listings located in the county",
                            "nps_nrhp"),
    (_C, "nrhp_districts"): ("districts", "Listed historic districts with homes in the county",
                             "nps_nrhp"),
    (_C, "nrhp_district_units"): ("units", "2020 Census homes inside listed historic districts",
                                  "nps_nrhp"),
    (_C, "koz_sites"): ("sites", "Keystone Opportunity Zone sites (PA only)", "dced_koz_sites"),
    (_C, "koz_acres"): ("acres", "Acres of those sites", "dced_koz_sites"),
}  # fmt: skip
"""``(table, column) -> (unit, description, source id)`` for the data dictionary."""


def _flag(tracts: list[str], listed: pd.Series | None) -> pd.Series:
    if listed is None:
        return pd.Series(pd.NA, index=tracts, dtype="Int64")
    return pd.Series(np.isin(tracts, listed.astype(str)).astype("int64"), index=tracts)


def dda_shares(tracts: list[str], dda: pd.DataFrame, zcta_tract: pd.DataFrame) -> pd.Series:
    """Share of each tract's land in a DDA (non-metro county DDAs count as 1)."""
    metro = set(dda.loc[dda["dda_type"] == "SA", "code"].astype(str))
    rel = zcta_tract[zcta_tract["tract"].isin(set(tracts))]
    land = rel.groupby("tract")["land_m2"].sum()
    inside = rel[rel["zcta"].isin(metro)].groupby("tract")["land_m2"].sum()
    share = (inside.reindex(land.index).fillna(0) / land.where(land > 0)).fillna(0)
    share = share.reindex(tracts).fillna(0.0)
    counties = set(dda.loc[dda["dda_type"] == "NM", "code"].astype(str))
    whole = np.array([t[:5] in counties for t in tracts])
    share[whole] = 1.0
    return share.clip(0, 1)


def _in_tracts(lat: Any, lon: Any, shapes: Sequence[tuple[str, dict[str, Any]]]) -> np.ndarray:
    lat = pd.to_numeric(pd.Series(lat), errors="coerce").to_numpy(dtype=float)
    lon = pd.to_numeric(pd.Series(lon), errors="coerce").to_numpy(dtype=float)
    ok = ~(np.isnan(lat) | np.isnan(lon))
    out = np.full(len(lat), "", dtype=object)
    if ok.any():
        out[ok] = points_in_polygons(lat[ok], lon[ok], shapes)
    return out


def _centre(geometry: dict[str, Any]) -> tuple[float, float]:
    ring = np.asarray(geometry["coordinates"][0], dtype=float)
    return float(ring[:, 1].mean()), float(ring[:, 0].mean())


def nrhp_listing_tracts(
    points: pd.DataFrame,
    districts: Sequence[tuple[str, dict[str, Any]]],
    shapes: Sequence[tuple[str, dict[str, Any]]],
) -> pd.DataFrame:
    """One row per listed property (``refnum``, ``tract``): its point, else its polygon's centre."""
    listed = points[points["status"].fillna("Listed") != "Removed"]
    rows = listed[["refnum", "lat", "lon"]].copy()
    have = set(rows["refnum"].astype(str))
    extra = [(ref, *_centre(geo)) for ref, geo in districts if ref not in have]
    if extra:
        rows = pd.concat([rows, pd.DataFrame(extra, columns=["refnum", "lat", "lon"])])
    rows = rows.drop_duplicates("refnum").reset_index(drop=True)
    rows["tract"] = _in_tracts(rows["lat"], rows["lon"], shapes)
    return rows[["refnum", "tract"]]


def district_blocks(
    blocks: pd.DataFrame, districts: Sequence[tuple[str, dict[str, Any]]]
) -> pd.Series:
    """For each block, the historic district its centre is in (``""`` when none)."""
    return pd.Series(
        points_in_polygons(blocks["lat"].to_numpy(float), blocks["lon"].to_numpy(float),
                           districts),
        index=blocks.index,
    )  # fmt: skip


def _norm_county(name: object) -> str:
    text = str(name or "").strip().upper()
    return text.removesuffix(" COUNTY").strip()


def incentive_tables(
    inputs: IncentiveInputs,
    tracts: list[str],
    county_names: dict[str, str],
    tract_shapes: Sequence[tuple[str, dict[str, Any]]],
    blocks: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """Build ``incentives_tract`` and ``incentives_county`` (pure: no file or network access).

    Args:
        inputs: The fetched extracts; a ``None`` extract leaves its columns blank.
        tracts: The pack's tract codes.
        county_names: County code to name (for KOZ county names).
        tract_shapes: ``(tract code, GeoJSON geometry)`` pairs, for placing points.
        blocks: Blocks with ``tract``, ``housing_units``, ``lat``, ``lon`` (for
            homes in historic districts); ``None`` leaves that column blank.
    """
    tracts = sorted(tracts)
    t = pd.DataFrame(index=pd.Index(tracts, name="tract"))
    t["qct"] = _flag(tracts, None if inputs.qct is None else inputs.qct["tract"])
    if inputs.dda is not None and inputs.zcta_tract is not None:
        t["dda_land_share"] = dda_shares(tracts, inputs.dda, inputs.zcta_tract).round(4)
        t["dda"] = (t["dda_land_share"] >= DDA_MAJORITY).astype("int64")
    else:
        t["dda_land_share"], t["dda"] = np.nan, pd.NA
    oz = inputs.oz2027
    t["oz2027"] = _flag(tracts, None if oz is None else oz["tract"])
    t["oz2027_rural"] = _flag(tracts, None if oz is None else oz.loc[oz["rural"] == 1, "tract"])
    if inputs.nmtc is not None:
        nm = inputs.nmtc.set_index("tract")
        t["nmtc_lic"] = nm["nmtc_lic"].reindex(tracts).astype("Int64")
        t["nmtc_poverty_rate"] = nm["nmtc_poverty_rate"].reindex(tracts)
        t["nmtc_mfi_pct"] = nm["nmtc_mfi_pct"].reindex(tracts)
    else:
        t["nmtc_lic"], t["nmtc_poverty_rate"], t["nmtc_mfi_pct"] = pd.NA, np.nan, np.nan

    county = pd.DataFrame(index=pd.Index(sorted(county_names), name="county"))
    tract_county = pd.Series([x[:5] for x in tracts], index=tracts)

    lihtc = inputs.lihtc
    if lihtc is not None:
        lihtc = lihtc.copy()
        lihtc["tract"] = _in_tracts(lihtc["lat"], lihtc["lon"], tract_shapes)
        for col in ("units", "li_units"):
            lihtc[col] = pd.to_numeric(lihtc[col], errors="coerce")
        by_tract = lihtc[lihtc["tract"] != ""].groupby("tract")
        t["lihtc_projects"] = by_tract.size().reindex(tracts).fillna(0).astype("int64")
        t["lihtc_units"] = by_tract["units"].sum().reindex(tracts).fillna(0).astype("int64")
        t["lihtc_li_units"] = by_tract["li_units"].sum().reindex(tracts).fillna(0).astype("int64")
        lihtc["county"] = lihtc["county"].astype(str).str.zfill(5)
        by_county = lihtc.groupby("county")
        county["lihtc_projects"] = by_county.size().reindex(county.index).fillna(0).astype("int64")
        for col in ("units", "li_units"):
            county[f"lihtc_{col}"] = (
                by_county[col].sum().reindex(county.index).fillna(0).astype("int64")
            )
        years = pd.to_numeric(
            lihtc["year_pis"].astype(str).where(~lihtc["year_pis"].astype(str).isin(UNKNOWN_YEARS)),
            errors="coerce",
        )
        county["lihtc_latest_year"] = (
            years.groupby(lihtc["county"]).max().reindex(county.index).astype("Int64")
        )

    if inputs.nrhp_points is not None and inputs.nrhp_districts is not None:
        listings = nrhp_listing_tracts(inputs.nrhp_points, inputs.nrhp_districts, tract_shapes)
        per = listings[listings["tract"] != ""].groupby("tract").size()
        t["nrhp_listings"] = per.reindex(tracts).fillna(0).astype("int64")
        if blocks is not None and len(blocks):
            b = blocks.copy()
            b["district"] = district_blocks(b, inputs.nrhp_districts)
            inside = b[b["district"] != ""]
            units = pd.to_numeric(inside["housing_units"], errors="coerce").fillna(0)
            t["nrhp_district_units"] = (
                units.groupby(inside["tract"]).sum().reindex(tracts).fillna(0).astype("int64")
            )
            homes = inside[units > 0]
            per_county = homes.groupby(homes["tract"].str[:5])["district"].nunique()
            county["nrhp_districts"] = per_county.reindex(county.index).fillna(0).astype("int64")

    if inputs.koz is not None:
        koz = inputs.koz.copy()
        koz["tract"] = _in_tracts(koz["lat"], koz["lon"], tract_shapes)
        koz["acres"] = pd.to_numeric(koz["acres"], errors="coerce").fillna(0)
        per = koz[koz["tract"] != ""].groupby("tract")
        t["koz_sites"] = per.size().reindex(tracts).fillna(0).astype("int64")
        t["koz_acres"] = per["acres"].sum().reindex(tracts).fillna(0).round(2)
        by_name = {_norm_county(n): g for g, n in county_names.items()}
        koz["county"] = koz["county_name"].map(lambda n: by_name.get(_norm_county(n), ""))
        per_c = koz[koz["county"] != ""].groupby("county")
        county["koz_sites"] = per_c.size().reindex(county.index).fillna(0).astype("int64")
        county["koz_acres"] = per_c["acres"].sum().reindex(county.index).fillna(0).round(2)

    groups = t.groupby(tract_county)
    county["tracts"] = groups.size().reindex(county.index).fillna(0).astype("int64")
    for flag, name in (("qct", "qct_tracts"), ("dda", "dda_tracts"),
                       ("oz2027", "oz2027_tracts"), ("nmtc_lic", "nmtc_lic_tracts")):  # fmt: skip
        values = pd.to_numeric(t[flag], errors="coerce").groupby(tract_county).sum(min_count=1)
        county[name] = values.reindex(county.index).astype("Int64")
    if inputs.dda is not None:
        nonmetro = set(inputs.dda.loc[inputs.dda["dda_type"] == "NM", "code"].astype(str))
        county["dda_nonmetro"] = [int(c in nonmetro) for c in county.index]
    for col in ("nrhp_listings", "nrhp_district_units"):
        if col in t.columns:
            county[col] = groups[col].sum().reindex(county.index).fillna(0).astype("int64")
    t_order = [c for (tab, c) in COLUMNS if tab == _T and c in t.columns]
    c_order = [c for (tab, c) in COLUMNS if tab == _C and c in county.columns]
    return {
        "incentives_tract": t.reset_index()[["tract", *t_order]],
        "incentives_county": county.reset_index()[["county", *c_order]],
    }
