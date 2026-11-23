"""State data pack: turn the cached source extracts into small tables, shapes and a dictionary.

Plain-language summary
----------------------
A **state pack** is everything the engine and the interface need for one state,
in one folder, ``data/packs/{STATE}/``. It is small (under 10 MB) and can be
opened in a spreadsheet:

- gzipped CSV tables (``*.csv.gz``). The tables and columns the synthetic state
  Testland has are here under exactly the same names, so code tested on
  Testland runs on a real state unchanged. Extra columns come after them.
- ``boundaries/``: map shapes as GeoJSON, ``counties.geojson`` and one
  ``tracts/{county}.geojson`` per county.
- ``data_dictionary.json``: every table and column with its unit, a plain
  description, its source ids and its data tier.
- ``pack_manifest.json``: the checksum (SHA-256) and size of every file, the
  checksum of every input file it was built from, and one provenance row per
  source.

Tables
------
================== ===================================================== ======
table              one row per                                           tier
================== ===================================================== ======
counties           county                                                0
tracts             census tract (water-only tracts left out)             0
blocks             2020 census block with land or homes                  0
permits_county_year county and year, 1990 to the latest year             0
transit_stops      transit stop in the state (National Transit Map)      0/1
transit_feeds      transit feed: how its bus frequencies were counted    1
prices_county      county: house price changes                           0
prices_tract       tract with a published house price index              0
hpi_county_year    county and year: the house price index series         0
mortgage_rates_year calendar year: average offered mortgage rates        0
costs_county       county: construction wage index                       0
================== ===================================================== ======

Determinism
-----------
The same cache always gives byte-identical files: rows are sorted by their
keys, numbers are rounded the same way every time, gzip headers carry no time
stamp or file name, and JSON is written with fixed key order. The manifest
holds no build time. ``pack_hash`` is one SHA-256 over every file's path and
checksum, so two builds can be compared with one line.

Disk
----
The build refuses to start with less than 15 GB free. Large national files are
never kept: each fetcher saves only the state's rows (see the source modules),
so the cache for Pennsylvania is about 30 MB and the pack about 9 MB.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from visionhousing import __version__
from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient, check_free_space, sha256_file
from visionhousing.data.registry import load_registry
from visionhousing.data.sources import acs, blocks, boundaries, bps, costs, prices
from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.data.sources import transit as transit_src
from visionhousing.features import acs as fx
from visionhousing.features import density
from visionhousing.features import transit as transit_fx

__all__ = [
    "PACK_BUDGET_BYTES",
    "SCHEMA_VERSION",
    "TABLES",
    "PackInputs",
    "PackReport",
    "assemble_tables",
    "build_dictionary",
    "build_pack",
    "load_inputs",
    "load_pack",
    "size_report",
    "write_pack",
]

SCHEMA_VERSION = 1
PACK_BUDGET_BYTES = 10_000_000
"""The whole pack folder must be smaller than this (10 MB)."""
MIN_FREE_GB = 15.0

TESTLAND_COLUMNS: dict[str, tuple[str, ...]] = {
    "counties": ("geoid", "name", "population", "housing_units"),
    "tracts": ("geoid", "county", "area_type", "land_area_m2", *fx.COLUMNS[2:]),
    "blocks": blocks.LITE_COLUMNS,
    "permits_county_year": (
        "county",
        "year",
        "units_1",
        "units_2",
        "units_3_4",
        "units_5plus",
        "imputed",
    ),
    "transit_stops": ("stop_id", "lat", "lon", "mode", "weekday_departures"),
}
"""The Testland tables and their columns, in Testland order: the pack's fixed schema."""

TABLES: dict[str, dict[str, object]] = {
    "counties": {
        "key": ["geoid"],
        "sources": ["acs5_summary_file", "cb_2020_county", "tiger_2020_tabblock"],
        "description": "One row per county: name, people and housing units (sums of its tracts).",
    },
    "tracts": {
        "key": ["geoid"],
        "sources": ["acs5_summary_file", "tiger_2020_tabblock"],
        "description": (
            "One row per 2020 census tract: survey estimates (ACS 2020-2024) with margins of "
            "error, plus density and lot-size measures from 2020 Census blocks. Tracts with "
            "no land and no housing (open water) are left out."
        ),
    },
    "blocks": {
        "key": ["geoid"],
        "sources": ["tiger_2020_tabblock"],
        "description": (
            "One row per 2020 census block with land or housing: 2020 housing units, land "
            "area and the block's internal point. Used for distances to transit."
        ),
    },
    "permits_county_year": {
        "key": ["county", "year"],
        "sources": ["bps_county_annual"],
        "description": "Housing units authorized by building permits, by county, year and type.",
    },
    "transit_stops": {
        "key": ["stop_id"],
        "sources": ["ntm_stops", "gtfs_agency_feeds", "mdb_catalog", "cb_2020_tract"],
        "description": (
            "One row per transit stop or station inside the state (National Transit Map), with "
            "its mode, its weekday departures where an agency schedule feed could be read, and "
            "whether it is a major transit stop (decision D-07). Names are kept for major stops."
        ),
    },
    "transit_feeds": {
        "key": ["feed_id", "schedule_feed"],
        "sources": ["ntm_stops", "ntm_agencies", "mdb_catalog", "gtfs_agency_feeds"],
        "description": (
            "Coverage of the transit data: one row per National Transit Map feed with stops in "
            "the state (and per schedule feed that matched none), saying how many bus stops had "
            "their departures counted and from which schedule date."
        ),
    },
    "prices_county": {
        "key": ["county"],
        "sources": ["fhfa_hpi_county"],
        "description": "House price change per county from the FHFA annual county index.",
    },
    "prices_tract": {
        "key": ["tract"],
        "sources": ["fhfa_hpi_tract"],
        "description": "House price change per tract from the FHFA annual tract index; only "
        "tracts FHFA publishes are listed.",
    },
    "hpi_county_year": {
        "key": ["county", "year"],
        "sources": ["fhfa_hpi_county"],
        "description": "The FHFA annual county house price index series.",
    },
    "mortgage_rates_year": {
        "key": ["year"],
        "sources": ["freddie_pmms"],
        "description": "Calendar-year averages of the weekly national mortgage rate survey.",
    },
    "costs_county": {
        "key": ["county"],
        "sources": ["bls_qcew_construction"],
        "description": "Construction wage index per county, a proxy for local building costs.",
    },
}
"""Every pack table: its key columns, source ids and a one-line description."""

TIERS = {
    "0": "Available for every state from national files.",
    "1": "Available for many states; filled in where published.",
    "2": "Local refinement only (for example county parcel data).",
}

GZIP_LEVEL = 9


# --------------------------------------------------------------------------- inputs


@dataclass
class PackInputs:
    """Everything the pack is built from, already read into tables."""

    state: str
    fips: str
    acs_tracts: pd.DataFrame
    blocks: pd.DataFrame
    county_shapes: list[boundaries.Shape]
    tract_shapes: list[boundaries.Shape]
    permits: pd.DataFrame
    hpi_county: pd.DataFrame
    hpi_tract: pd.DataFrame
    pmms_weekly: pd.DataFrame
    qcew: pd.DataFrame
    input_files: list[tuple[str, Path]] = field(default_factory=list)
    cache: Path | None = None
    transit: transit_src.TransitInputs | None = None
    """Stops and schedule feeds; ``None`` gives empty transit tables and zero features."""
    notes: list[str] = field(default_factory=list)


def _cache_files(cache: Path, fips: str) -> list[tuple[str, Path]]:
    """The cached files a build reads, with their source ids, sorted."""
    files: list[tuple[str, Path]] = []
    for path in sorted((cache / "bps").glob("*a.txt")):
        files.append(
            ("bps_state_annual" if path.name.startswith("st") else "bps_county_annual", path)
        )
    acs_dir = cache / "acs" / str(acs.VINTAGE)
    for path in sorted(acs_dir.glob("*")):
        if path.name.startswith("Geos") and path.name.endswith(f"_{fips}.txt"):
            files.append(("acs5_geos", path))
        elif path.name.endswith(f"_{fips}.dat"):
            files.append(("acs5_summary_file", path))
        elif "Table_Shells" in path.name:
            files.append(("acs5_table_shells", path))
    files += [
        ("tiger_2020_tabblock", blocks.extract_path(cache, fips)),
        ("cb_2020_county", cache / "boundaries" / f"cb_2020_{fips}_county_500k.geojson.gz"),
        ("cb_2020_tract", cache / "boundaries" / f"cb_2020_{fips}_tract_500k.geojson.gz"),
        ("fhfa_hpi_county", cache / "prices" / f"hpi_at_county_{fips}.csv"),
        ("fhfa_hpi_tract", cache / "prices" / f"hpi_at_tract_{fips}.csv"),
        ("freddie_pmms", cache / "prices" / "PMMS_history.csv"),
        (
            "bls_qcew_construction",
            cache / "costs" / f"qcew_{costs.QCEW_YEAR}_naics23_{fips}.csv",
        ),
    ]
    return [(sid, p) for sid, p in files if p.exists()]


def load_inputs(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> PackInputs:
    """Read every input from the cache, fetching only what is missing.

    With a full cache no network is used. Each fetcher saves only the state's
    rows and deletes any large national file it downloaded.
    """
    fips = to_state_fips(state)
    cache = Path(cache)
    client = client or HttpClient()
    check_free_space(cache if cache.exists() else cache.parent, 0, min_gb=MIN_FREE_GB)
    notes = []
    raw = acs.fetch_acs(state, acs.VINTAGE, cache, client=client, manifest=manifest)
    acs_tracts = fx.tract_features(raw)
    block_table, info = blocks.fetch_blocks(state, cache, client=client, manifest=manifest)
    if not info.from_cache:
        notes.append(
            f"blocks: read {info.member_bytes / 1e6:.1f} MB of the {info.zip_bytes / 1e6:.1f} MB "
            f"zip ({info.share_read:.1%})"
        )
    county_shapes, tract_shapes = boundaries.fetch_boundaries(
        state, cache, client=client, manifest=manifest
    )
    transit = transit_src.fetch_transit(
        state, county_shapes, cache, client=client, manifest=manifest
    )
    tidy = bps.fetch_bps(
        state,
        range(bps.FIRST_YEAR, bps.LATEST_YEAR + 1),
        cache,
        client=client,
        include_state_totals=True,
        manifest=manifest,
    )
    return PackInputs(
        state=state.upper(),
        fips=fips,
        acs_tracts=acs_tracts,
        blocks=block_table,
        county_shapes=county_shapes,
        tract_shapes=tract_shapes,
        permits=bps.permits_county_year(tidy),
        hpi_county=prices.fetch_hpi_county(state, cache, client=client, manifest=manifest),
        hpi_tract=prices.fetch_hpi_tract(state, cache, client=client, manifest=manifest),
        pmms_weekly=prices.fetch_pmms(cache, client=client, manifest=manifest),
        qcew=costs.fetch_qcew_construction(state, cache, client=client, manifest=manifest),
        input_files=sorted([*_cache_files(cache, fips), *transit.files]),
        cache=cache,
        notes=notes,
        transit=transit,
    )


# --------------------------------------------------------------------------- tables


def _water_only(tract_density: pd.DataFrame) -> set[str]:
    none = (tract_density["block_land_area_m2"] <= 0) & (tract_density["housing_units_2020"] <= 0)
    return set(tract_density.loc[none, "tract"])


def _tracts(inputs: PackInputs, dens: pd.DataFrame, dropped: set[str]) -> pd.DataFrame:
    acs_t = inputs.acs_tracts[~inputs.acs_tracts["geoid"].isin(dropped)].copy()
    dens = dens[~dens["tract"].isin(dropped)].rename(columns={"tract": "geoid"})
    missing = sorted(set(acs_t["geoid"]) ^ set(dens["geoid"]))
    if missing:
        raise ValueError(f"tracts in only one of ACS and the blocks: {missing[:5]}")
    dens = dens.assign(area_type=density.area_type(dens), land_area_m2=dens["block_land_area_m2"])
    merged = acs_t.merge(dens, on="geoid", how="left", validate="one_to_one")
    front = list(TESTLAND_COLUMNS["tracts"])
    extra_acs = [c for c in inputs.acs_tracts.columns if c not in front]
    extra_density = [c for c in density.TRACT_COLUMNS if c not in ("tract", "block_land_area_m2")]
    return merged[[*front, *extra_acs, *extra_density]].sort_values("geoid")


def _counties(inputs: PackInputs, tracts: pd.DataFrame) -> pd.DataFrame:
    names = {s.geoid: s.name for s in inputs.county_shapes}
    sums = tracts.groupby("county")[["population", "housing_units"]].sum(min_count=1)
    dens = density.county_density(inputs.blocks)
    land = tracts.groupby("county")["land_area_m2"].sum()
    out = pd.DataFrame(
        {
            "geoid": sums.index,
            "name": [names.get(g) for g in sums.index],
            "population": sums["population"].to_numpy(),
            "housing_units": sums["housing_units"].to_numpy(),
            "land_area_m2": land.reindex(sums.index).to_numpy(),
        }
    )
    out = out.merge(
        dens[["county", "housing_units_2020", "population_2020", "housing_density_per_km2"]],
        left_on="geoid",
        right_on="county",
        how="left",
    ).drop(columns="county")
    return out.sort_values("geoid")


def _prices(changes: pd.DataFrame, key: str, ids: set[str]) -> pd.DataFrame:
    out = changes[changes["geoid"].isin(ids)].rename(columns={"geoid": key})
    return out.sort_values(key)


def assemble_tables(inputs: PackInputs) -> dict[str, pd.DataFrame]:
    """Build every pack table from the inputs (pure: no file or network access).

    Raises:
        ValueError: If a pack county or tract has no shape, or tracts and blocks
            disagree.
    """
    dens = density.tract_density(inputs.blocks)
    dropped = _water_only(dens)
    tracts = _tracts(inputs, dens, dropped)
    counties = _counties(inputs, tracts)
    county_ids, tract_ids = set(counties["geoid"]), set(tracts["geoid"])

    no_county = sorted(county_ids - {s.geoid for s in inputs.county_shapes})
    no_tract = sorted(tract_ids - {s.geoid for s in inputs.tract_shapes})
    if no_county or no_tract:
        raise ValueError(f"ids without a shape: counties {no_county[:5]}, tracts {no_tract[:5]}")

    lite = blocks.blocks_lite(inputs.blocks[~inputs.blocks["tract"].isin(dropped)])
    stops, feeds, tract_transit = _transit_tables(inputs, lite, tracts)
    tracts = tracts.merge(
        tract_transit.rename(columns={"tract": "geoid"}), on="geoid", how="left", validate="1:1"
    )
    permits = inputs.permits[inputs.permits["county"].isin(county_ids)]
    hpi_years = inputs.hpi_county[inputs.hpi_county["geoid"].isin(county_ids)].rename(
        columns={"geoid": "county"}
    )[["county", "year", "hpi", "annual_change_pct", "hpi_1990_base", "hpi_2000_base"]]
    tables = {
        "counties": counties,
        "tracts": tracts,
        "blocks": lite,
        "permits_county_year": permits.sort_values(["county", "year"]),
        "transit_stops": stops,
        "transit_feeds": feeds,
        "prices_county": _prices(prices.hpi_changes(inputs.hpi_county), "county", county_ids),
        "prices_tract": _prices(prices.hpi_changes(inputs.hpi_tract), "tract", tract_ids),
        "hpi_county_year": hpi_years.sort_values(["county", "year"]),
        "mortgage_rates_year": prices.pmms_annual(inputs.pmms_weekly),
        "costs_county": costs.cost_adjusters(inputs.qcew, inputs.fips, sorted(county_ids)),
    }
    note = f"tracts left out as open water: {', '.join(sorted(dropped))}"
    if dropped and note not in inputs.notes:
        inputs.notes.append(note)
    return {name: _tidy_numbers(t.reset_index(drop=True)) for name, t in tables.items()}


def _transit_tables(
    inputs: PackInputs, lite: pd.DataFrame, tracts: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """The stop and feed tables and the per-tract transit features (decision D-07).

    The D-07 numbers are the ledger's central values (``parking.transit_*``).
    Without transit inputs the tables are empty and every tract gets zeros.
    """
    from visionhousing.ledger import load_ledger

    rule = transit_fx.TransitRule.from_ledger(load_ledger())
    if inputs.transit is None:
        stops = pd.DataFrame(columns=list(transit_src.STOP_COLUMNS))
        feeds = pd.DataFrame(columns=list(transit_src.FEED_COLUMNS))
    else:
        kept = {str(t) for t in tracts["geoid"]}
        shapes = [s for s in inputs.tract_shapes if s.geoid in kept]
        stops, feeds = transit_src.build_stop_table(inputs.transit, shapes, rule, blocks=lite)
    features = transit_fx.tract_transit_features(
        lite, stops, rule, stop_tracts=stops["tract"].astype(str), tracts=tracts["geoid"]
    )
    features = features[features["tract"].isin(set(tracts["geoid"]))]
    return stops, feeds, features


def _tidy_numbers(frame: pd.DataFrame) -> pd.DataFrame:
    """Whole-number float columns become integers; other floats are rounded (lat/lon to 5)."""
    out = frame.copy()
    for name in out.columns:
        col = out[name]
        if not pd.api.types.is_float_dtype(col):
            continue
        values = col.dropna()
        if len(values) and np.all(np.mod(values.to_numpy(), 1) == 0):
            out[name] = col.round().astype("Int64")
        else:
            out[name] = col.round(blocks.COORD_DIGITS if name in ("lat", "lon") else 4)
    return out


# --------------------------------------------------------------------------- dictionary

_BASE: dict[str, tuple[str, str]] = {
    "geoid": ("code", "Area code"),
    "county": ("code", "Code of the county (2-digit state + 3-digit county)"),
    "tract": ("code", "Tract code: 5-digit county code + 6-digit tract code"),
    "name": ("text", "Display name"),
    "year": ("year", "Calendar year"),
    "area_type": (
        "category",
        "urban, suburban or rural, from the 2020 Census urban blocks and housing density",
    ),
    "land_area_m2": ("square metres", "Land area: the sum of the 2020 census blocks' land"),
    "housing_units": ("units", "All housing units, occupied and vacant (ACS 2020-2024)"),
    "population": ("persons", "Residents (ACS 2020-2024)"),
    "units_1_detached": ("units", "Housing units in 1-unit detached structures (B25024)"),
    "units_1_attached": ("units", "Housing units in 1-unit attached structures (B25024)"),
    "units_2": ("units", "Housing units in 2-unit structures (B25024)"),
    "units_3_4": ("units", "Housing units in 3- or 4-unit structures (B25024)"),
    "units_5_9": ("units", "Housing units in 5- to 9-unit structures (B25024)"),
    "units_10_19": ("units", "Housing units in 10- to 19-unit structures (B25024)"),
    "units_20_plus": ("units", "Housing units in structures of 20 or more units (B25024)"),
    "units_mobile": ("units", "Mobile homes (B25024)"),
    "units_boat_rv": ("units", "Boats, RVs and vans used as homes (B25024)"),
    "households_owner": ("households", "Owner-occupied housing units (B25003)"),
    "households_renter": ("households", "Renter-occupied housing units (B25003)"),
    "vacant_units": ("units", "Vacant housing units (B25002)"),
    "vacancy_rate": ("share", "Vacant units / all housing units (B25002)"),
    "median_household_income": ("dollars per year", "Median household income (B19013)"),
    "median_home_value": ("dollars", "Median value of owner-occupied units (B25077)"),
    "median_gross_rent": ("dollars per month", "Median gross rent (B25064)"),
    "share_age_65plus": ("share", "Share of residents aged 65 or older (B01001)"),
    "rent_no_cash": ("units", "Renter-occupied units paying no cash rent (B25063)"),
    "housing_units_2020": ("units", "Housing units in the 2020 Census: the sum of its blocks"),
    "population_2020": ("persons", "Residents in the 2020 Census: the sum of its blocks"),
    "blocks": ("blocks", "Number of 2020 census blocks"),
    "residential_blocks": ("blocks", "2020 census blocks with at least one housing unit"),
    "housing_density_per_km2": (
        "units per square kilometre",
        "2020 housing units / land area in square kilometres",
    ),
    "urban_unit_share": (
        "share",
        "Share of 2020 housing units in blocks the Census Bureau classed as urban "
        "(share of land when there are no units)",
    ),
    "lat": ("degrees", "Latitude of the point (WGS 84 / NAD 83, 5 decimals)"),
    "lon": ("degrees", "Longitude of the point (WGS 84 / NAD 83, 5 decimals)"),
    "units_1": ("units", "Units authorized in 1-unit buildings"),
    "units_5plus": ("units", "Units authorized in 5-or-more-unit buildings"),
    "imputed": ("flag", "1 if the county-year was partly estimated by the Census Bureau"),
    "stop_id": ("code", "Stop identifier: the map's feed id, a colon, the agency's stop id"),
    "mode": (
        "category",
        "rail (any fixed guideway: rail, subway, light rail, trolley, funicular), bus, other "
        "(ferry, aerial lift) or unknown",
    ),
    "weekday_departures": (
        "departures per day",
        "Scheduled departures on the feed's representative weekday (blank when not counted)",
    ),
    "major": (
        "flag",
        "1 if a major transit stop (D-07): any rail stop, or a bus stop with "
        "at least 64 counted weekday departures",
    ),
    "frequency": (
        "category",
        "mode_only (rail: major by mode), counted, stale (only an expired schedule), unknown "
        "(no keyless schedule), not_major (other modes)",
    ),
    "route_types": (
        "codes",
        "GTFS route types serving the stop, as the map lists them (0 tram, "
        "1 subway, 2 rail, 3 bus, 7 funicular, 11 trolleybus)",
    ),
    "ntd_id": ("code", "National Transit Database id of the agency"),
    "feed_id": ("code", "National Transit Map feed id"),
    "stop_name": ("text", "Stop name (major stops only)"),
    "agency": ("text", "Agency name (National Transit Map agencies)"),
    "stops": ("stops", "Stops of the feed inside the state"),
    "rail_stops": ("stops", "Of which rail (fixed guideway)"),
    "bus_stops": ("stops", "Of which bus"),
    "bus_stops_counted": (
        "stops",
        "Bus stops whose departures were counted from a current schedule",
    ),
    "major_bus_stops": ("stops", "Bus stops with at least 64 counted weekday departures"),
    "schedule_feed": ("code", "Mobility Database id of the schedule feed used"),
    "schedule_provider": ("text", "Provider of the schedule feed"),
    "schedule_date": ("date", "Representative weekday the departures were counted on"),
    "schedule_valid_from": ("date", "First date the schedule feed covers"),
    "schedule_valid_to": ("date", "Last date the schedule feed covers"),
    "transit_units_d07": (
        "units",
        "2020 housing units in blocks within the D-07 distance "
        "(1/2 mile of a rail stop or 1/4 mile of a frequent bus stop)",
    ),
    "transit_units_share_d07": ("share", "transit_units_d07 / the tract's 2020 block units"),
    "transit_land_m2_d07": ("square metres", "Block land within the D-07 distance"),
    "transit_land_share_d07": ("share", "transit_land_m2_d07 / the tract's block land"),
    "transit_major_stops": ("stops", "Major transit stops inside the tract"),
    "transit_rich": (
        "flag",
        "1 if at least half of the tract's 2020 units are within the D-07 "
        "distance, or a major stop is inside the tract (parking reform rule)",
    ),
    "hpi_year": ("year", "Latest year of the house price index used for the changes"),
    "hpi_annual_change_pct": ("percent", "House price index change in the latest year"),
    "hpi_change_5yr_pct": ("percent", "House price index change over the five years to hpi_year"),
    "hpi_change_10yr_pct": ("percent", "House price index change over the ten years to hpi_year"),
    "hpi": ("index", "House price index; only changes over time compare across areas"),
    "annual_change_pct": ("percent", "Published change in the index from the year before"),
    "hpi_1990_base": ("index (1990 = 100)", "House price index with 1990 = 100"),
    "hpi_2000_base": ("index (2000 = 100)", "House price index with 2000 = 100"),
    "rate_30yr_pct": ("percent per year", "Average offered rate, 30-year fixed-rate mortgage"),
    "rate_15yr_pct": ("percent per year", "Average offered rate, 15-year fixed-rate mortgage"),
    "weeks_30yr": ("weeks", "Weeks with a published 30-year rate in the year"),
    "weeks_15yr": ("weeks", "Weeks with a published 15-year rate in the year"),
    "construction_employment": ("jobs", "Average construction jobs at private employers"),
    "construction_avg_annual_pay": ("dollars per year", "Average pay per construction job"),
    "wage_index_county_vs_state": ("ratio", "County construction pay / state construction pay"),
    "wage_index_state_vs_us": ("ratio", "State construction pay / national construction pay"),
    "wage_index_method": ("category", "county, or state_value where BLS withholds the county"),
}

_TABLE_OVERRIDES: dict[tuple[str, str], tuple[str, str]] = {
    ("counties", "geoid"): ("code", "County code: 2-digit state code + 3-digit county code"),
    ("counties", "name"): ("text", "County name"),
    ("counties", "population"): ("persons", "Residents; the sum of the county's tracts (ACS)"),
    ("counties", "housing_units"): ("units", "Housing units; the sum of the county's tracts (ACS)"),
    ("counties", "land_area_m2"): ("square metres", "Land area; the sum of the county's tracts"),
    ("tracts", "geoid"): ("code", "Tract code: 5-digit county code + 6-digit tract code"),
    ("tracts", "county"): ("code", "Code of the county that contains the tract"),
    ("blocks", "geoid"): ("code", "Block code: 11-digit tract code + 4-digit block code"),
    ("blocks", "tract"): ("code", "Code of the tract that contains the block"),
    ("blocks", "housing_units"): ("units", "Housing units in the 2020 Census"),
    ("blocks", "land_area_m2"): ("square metres", "Land area of the block"),
    ("blocks", "lat"): ("degrees", "Latitude of the block's internal point"),
    ("blocks", "lon"): ("degrees", "Longitude of the block's internal point"),
    ("permits_county_year", "units_2"): ("units", "Units authorized in 2-unit buildings"),
    ("permits_county_year", "units_3_4"): ("units", "Units authorized in 3- and 4-unit buildings"),
    ("permits_county_year", "county"): ("code", "County code"),
    ("permits_county_year", "year"): ("year", "Calendar year of authorization"),
    ("costs_county", "year"): ("year", "Year of the wage averages"),
}

_TIER_NOTE = {"tier": "0"}
_TIER_ONE_COLUMNS = {"weekday_departures", "major", "frequency"}
"""Transit columns that rest on agency schedule feeds, which only some agencies publish."""


def _tier(table: str, name: str) -> dict[str, str]:
    if table == "transit_feeds":
        return {"tier": "1"}
    if table == "transit_stops" and name in _TIER_ONE_COLUMNS:
        return {"tier": "1"}
    if table == "tracts" and name.startswith("transit_"):
        return {"tier": "1"}
    return _TIER_NOTE


def _describe(table: str, name: str) -> tuple[str, str]:
    if (table, name) in _TABLE_OVERRIDES:
        return _TABLE_OVERRIDES[(table, name)]
    if name in _BASE:
        return _BASE[name]
    if name.endswith("_moe"):
        unit, text = _describe(table, name[: -len("_moe")])
        return unit, f"90% margin of error of: {text[0].lower()}{text[1:]}"
    if name.endswith("_method"):
        return (
            "category",
            f"How {name[: -len('_method')]} was filled: tract, county_ratio or missing",
        )
    if name.startswith("transit_") and name.endswith("mi"):
        what, miles = name[len("transit_") :].rsplit("_", 2)[0], name.rsplit("_", 2)[1:]
        dist = f"{miles[0]}.{miles[1][:-2]} mile"
        text = {
            "units": ("units", f"2020 housing units in blocks within {dist} of a major stop"),
            "units_share": ("share", f"Share of the tract's 2020 units within {dist}"),
            "land_m2": ("square metres", f"Block land within {dist} of a major stop"),
            "land_share": ("share", f"Share of the tract's block land within {dist}"),
        }
        return text[what]
    if name.startswith("median_rent_b"):
        k = name[-1]
        size = "3 bedrooms" if k == "3" else f"{k} bedroom{'s' if k != '1' else ''}"
        return "dollars per month", f"Median gross rent, units with {size} (B25031)"
    if name.startswith("lot_m2_per_unit_p"):
        p = name.rsplit("_p", 1)[1]
        return (
            "square metres per unit",
            f"Lot-size proxy, {p}th percentile weighted by units: block land / block units, "
            "over blocks with at least one unit",
        )
    if name.startswith("built_"):
        span = (
            name[len("built_") :]
            .replace("_or_earlier", " or earlier")
            .replace("_plus", " or later")
        )
        return "units", f"Housing units built {span.replace('_', '-')} (B25034)"
    for prefix, table_id, what in (
        ("value_", "B25075", "Owner-occupied units valued"),
        ("rent_", "B25063", "Renter-occupied units paying gross rent of"),
    ):
        if name.startswith(prefix):
            rest = name[len(prefix) :]
            per = " a month" if prefix == "rent_" else ""
            if rest.startswith("lt_"):
                return "units", f"{what} less than ${int(rest[3:]):,}{per} ({table_id})"
            if rest.endswith("_plus"):
                return "units", f"{what} ${int(rest[:-5]):,} or more{per} ({table_id})"
            low, high = rest.split("_")
            return "units", f"{what} ${int(low):,} to ${int(high):,}{per} ({table_id})"
    raise ValueError(f"no data-dictionary entry for {table}.{name}")


def _column_sources(table: str, name: str) -> list[str]:
    srcs = list(TABLES[table]["sources"])  # type: ignore[arg-type]
    if table == "tracts" and name.startswith("transit_"):
        return ["ntm_stops", "gtfs_agency_feeds", "tiger_2020_tabblock"]
    if table == "transit_stops" and name in ("county", "tract"):
        return ["cb_2020_tract"]
    if table == "tracts":
        from_blocks = name in ("area_type", "land_area_m2") or name in density.TRACT_COLUMNS
        return ["tiger_2020_tabblock"] if from_blocks else ["acs5_summary_file"]
    if table == "counties":
        if name == "name":
            return ["cb_2020_county"]
        if name in ("population", "housing_units"):
            return ["acs5_summary_file"]
        return ["tiger_2020_tabblock"]
    return srcs


def build_dictionary(tables: Mapping[str, pd.DataFrame], state: str) -> dict[str, object]:
    """Return the data dictionary: Testland's layout plus a table list and tier meanings."""
    columns = []
    for table, frame in tables.items():
        for name in frame.columns:
            unit, text = _describe(table, name)
            columns.append(
                {
                    "table": table,
                    "name": name,
                    "unit": unit,
                    "description": text,
                    "source": ", ".join(_column_sources(table, name)),
                    **_tier(table, name),
                }
            )
    for name, unit, text in (
        ("level", "category", "county or tract"),
        ("geoid", "code", "Code of the county or tract"),
        ("name", "text", "Display name"),
    ):
        columns.append(
            {
                "table": "boundaries",
                "name": name,
                "unit": unit,
                "description": text,
                "source": "cb_2020_county, cb_2020_tract",
                **_TIER_NOTE,
            }
        )
    table_list = [
        {
            "table": table,
            "file": f"{table}.csv.gz",
            "key": TABLES[table]["key"],
            "description": TABLES[table]["description"],
            "sources": TABLES[table]["sources"],
            "tier": "1" if table == "transit_feeds" else "0",
            "testland_columns": list(TESTLAND_COLUMNS.get(table, ())),
        }
        for table in tables
    ]
    table_list.append(
        {
            "table": "boundaries",
            "file": "boundaries/counties.geojson, boundaries/tracts/{county}.geojson",
            "key": ["level", "geoid"],
            "description": "Simplified county and tract shapes for maps (GeoJSON, 5 decimals).",
            "sources": ["cb_2020_county", "cb_2020_tract"],
            "tier": "0",
            "testland_columns": ["level", "geoid", "name"],
        }
    )
    return {
        "state": state,
        "synthetic": False,
        "schema": SCHEMA_VERSION,
        "tiers": TIERS,
        "tables": table_list,
        "columns": columns,
    }


# --------------------------------------------------------------------------- writing


@dataclass(frozen=True)
class PackFile:
    """One file in a pack: its path inside the pack, size, checksum and row count."""

    path: str
    bytes: int
    sha256: str
    rows: int | None = None


@dataclass
class PackReport:
    """What a build wrote: the folder, every file, the pack hash and notes."""

    state: str
    folder: Path
    files: list[PackFile]
    pack_hash: str
    notes: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        """Bytes of every file in the pack, the two JSON files included."""
        return sum(f.bytes for f in self.files)


def _write_gz_csv(frame: pd.DataFrame, path: Path) -> None:
    text = frame.to_csv(index=False, lineterminator="\n")
    with (
        path.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=GZIP_LEVEL) as gz,
    ):
        gz.write(text.encode("utf-8"))


def _json_text(data: object) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False) + "\n"


def _inputs_block(inputs: PackInputs, manifest: Path | None) -> list[dict[str, object]]:
    entries = mf.load_manifest(manifest) if manifest is not None and manifest.exists() else []
    accessed: dict[str, str] = {}
    for e in entries:
        base_name = e.file.rsplit("/", 1)[-1]
        accessed[base_name] = max(accessed.get(base_name, ""), e.accessed[:10])
    rows = []
    base = inputs.cache
    for source_id, path in inputs.input_files:
        shown = (
            path.relative_to(base).as_posix() if base and path.is_relative_to(base) else path.name
        )
        rows.append(
            {
                "source": source_id,
                "file": shown,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "accessed": accessed.get(path.name),
            }
        )
    return sorted(rows, key=lambda r: (r["source"], r["file"]))


def _provenance(inputs_block: list[dict[str, object]]) -> list[dict[str, object]]:
    sources = load_registry()
    used = {r["source"] for r in inputs_block}
    rows = []
    for table in TABLES.values():
        used.update(table["sources"])  # type: ignore[arg-type]
    for s in sources.values():
        if s.id not in used:
            continue
        dates = [str(r["accessed"]) for r in inputs_block if r["source"] == s.id and r["accessed"]]
        rows.append(
            {
                "id": s.id,
                "source": f"{s.name} ({s.publisher})",
                "version": s.vintage,
                "access_date": max(dates) if dates else mf.NOT_FETCHED,
                "licence": s.licence,
                "terms": s.terms_url,
                "cleaning": s.cleaning or "None beyond parsing.",
                "limits": s.limits,
                "tier": s.tier,
            }
        )
    return rows


def write_pack(
    inputs: PackInputs,
    out_root: Path,
    *,
    manifest: Path | None = mf.MANIFEST_PATH,
    budget: int = PACK_BUDGET_BYTES,
) -> PackReport:
    """Assemble the tables and write the pack to ``out_root/{STATE}/``.

    The pack is written to a temporary folder next to it and swapped in only when
    complete, so a failed build never leaves half a pack.

    Raises:
        ValueError: If the pack is over ``budget`` bytes (the old pack is kept).
    """
    tables = assemble_tables(inputs)
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    final = out_root / inputs.state
    work = out_root / f".{inputs.state}.building"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    try:
        files: list[PackFile] = []
        for name, frame in tables.items():
            path = work / f"{name}.csv.gz"
            _write_gz_csv(frame, path)
            files.append(PackFile(path.name, path.stat().st_size, sha256_file(path), len(frame)))
        shapes = boundaries.write_boundaries(
            inputs.county_shapes, _kept_tracts(inputs, tables), work / "boundaries"
        )
        for path in shapes:
            rel = path.relative_to(work).as_posix()
            files.append(PackFile(rel, path.stat().st_size, sha256_file(path)))
        dictionary = build_dictionary(tables, inputs.state)
        (work / "data_dictionary.json").write_text(_json_text(dictionary), encoding="utf-8")
        files.append(_file_entry(work, "data_dictionary.json"))
        files.sort(key=lambda f: f.path)
        digest = hashlib.sha256()
        for f in files:
            digest.update(f"{f.path}\t{f.sha256}\n".encode())
        pack_hash = digest.hexdigest()
        inputs_block = _inputs_block(inputs, manifest)
        pack_manifest = {
            "schema": SCHEMA_VERSION,
            "state": inputs.state,
            "state_fips": inputs.fips,
            "code_version": __version__,
            "pack_hash": pack_hash,
            "files": [f.__dict__ for f in files],
            "inputs": inputs_block,
            "provenance": _provenance(inputs_block),
        }
        (work / "pack_manifest.json").write_text(_json_text(pack_manifest), encoding="utf-8")
        all_files = [*files, _file_entry(work, "pack_manifest.json")]
        total = sum(f.bytes for f in all_files)
        if total >= budget:
            raise ValueError(f"pack is {total:,} bytes, over the {budget:,} byte budget")
        if final.exists():
            shutil.rmtree(final)
        work.replace(final)
    finally:
        if work.exists():
            shutil.rmtree(work)
    return PackReport(inputs.state, final, all_files, pack_hash, list(inputs.notes))


def _kept_tracts(inputs: PackInputs, tables: Mapping[str, pd.DataFrame]) -> list[boundaries.Shape]:
    ids = set(tables["tracts"]["geoid"])
    return [s for s in inputs.tract_shapes if s.geoid in ids]


def _file_entry(folder: Path, name: str) -> PackFile:
    path = folder / name
    return PackFile(name, path.stat().st_size, sha256_file(path))


def build_pack(
    state: str,
    cache: Path,
    out_root: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> PackReport:
    """Load the inputs (fetching what is missing) and write the state pack."""
    inputs = load_inputs(state, cache, client=client, manifest=manifest)
    return write_pack(inputs, out_root, manifest=manifest)


# --------------------------------------------------------------------------- reading


def load_pack(folder: Path) -> dict[str, pd.DataFrame]:
    """Read every table of a pack folder; code columns stay text."""
    folder = Path(folder)
    out = {}
    for path in sorted(folder.glob("*.csv.gz")):
        name = path.name[: -len(".csv.gz")]
        out[name] = pd.read_csv(
            path,
            dtype={c: str for c in ("geoid", "county", "tract", "stop_id")},
            compression="gzip",
        )
    return out


def size_report(report: PackReport, budget: int = PACK_BUDGET_BYTES) -> str:
    """Return a plain-text table of file sizes, grouped, with the total against the budget."""
    lines = [f"State pack {report.state}: {report.folder.name}/", ""]
    lines.append(f"{'file':<32} {'rows':>9} {'bytes':>12}")
    tract_files = [f for f in report.files if f.path.startswith("boundaries/tracts/")]
    for f in report.files:
        if f in tract_files:
            continue
        rows = "" if f.rows is None else f"{f.rows:,}"
        lines.append(f"{f.path:<32} {rows:>9} {f.bytes:>12,}")
    if tract_files:
        label = f"boundaries/tracts/ ({len(tract_files)} files)"
        lines.append(f"{label:<32} {'':>9} {sum(f.bytes for f in tract_files):>12,}")
    lines.append("-" * 55)
    total = report.total_bytes
    lines.append(f"{'total':<32} {'':>9} {total:>12,}")
    lines.append(
        f"budget {budget:,} bytes: {'within' if total < budget else 'OVER'} "
        f"({total / budget:.0%} used)"
    )
    lines.append(f"pack_hash {report.pack_hash}")
    lines += [f"note: {n}" for n in report.notes]
    return "\n".join(lines)
