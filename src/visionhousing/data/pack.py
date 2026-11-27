"""State data pack: turn the cached source extracts into small tables, shapes and a dictionary.

Plain-language summary
----------------------
A **state pack** is everything the engine and the interface need for one state,
in one folder, ``data/packs/{STATE}/``. It is small (under 12 MB) and can be
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
transit_walk_tract tract near a major stop: walkshed acres (retrofit)     1
transit_walk_county county: peak-rule coverage, map tiles (retrofit)    1
mbta_3a_capacity_tract tract: existing 3A modeled capacity, MA (retrofit) 2
mbta_3a_capacity_district 3A district: capacity counted, MA (retrofit)  2
ma_sewer_estimated_tract tract: MassDEP estimated sewer, MA (retrofit)  2
prices_county      county: house price changes                           0
prices_tract       tract with a published house price index              0
hpi_county_year    county and year: the house price index series         0
mortgage_rates_year calendar year: average offered mortgage rates        0
costs_county       county: construction wage index                       0
jobs_tract         tract: jobs located there, by earnings and sector     0
jobs_county        county: jobs, commuting flows, business totals        0
jobs_county_sector county and NAICS sector: business patterns            0
incentives_tract   tract: tax-credit and incentive areas                 0/2
incentives_county  county: tax-credit and incentive totals               0/2
safety_county      county: reported offenses per 1,000 residents         0
safety_local       tract/county, year: city safety and distress records  2
typology_county    county: group of similar counties, nearest counties   0
typology_tract     tract: group of similar tracts, nearest tracts        0
acs_bands          state, county, tract: ACS tables collapsed to bands   0
afford_chas        state, county: CHAS cost burden by income             0
afford_chas_tract  tract: CHAS renter cost burden, 3 income groups       0
afford_income_limits county: HUD AMI and 30/50/80% limits, sizes 1-8     0
afford_assisted    state, county, tract: assisted homes, expirations     0
stock_ledger_county state, county: homes, permits, assisted, at risk     0
municipal_planning municipality part: zoning regime, plans (DCED)        2
tract_municipalities tract piece per municipality: 2020 housing units    2
parcels_area       tract and county: parcel attributes (42101, 42003)    2
owners_area        tract and county: single-family owner types           2
owners_deeds_year  year: single-family deeds to entities (42101)         2
pipeline_tract_year tract, year: city new-home permits (Phila., Pitt.)   2
lihtc_awards       development awarded PHFA tax credits                  2
law_documents      legal text read or linked (law tables)                2
state_law_provisions state code section or bill bearing on a reform    2
law_provision_version one version of one provision, dated (add-on step)  2
county_land_use    county: county zoning reach and county plan           2
muni_zoning_rules  municipality part: ADU, lot, parking rules            2
zone_rule          reviewed rule by district, overlay, date (PA sample)  2
rule_area_weights  lots by jurisdiction, zone version, class (PA sample) 2
================== ===================================================== ======

The jobs, incentive and safety tables feed the place profile's market
research sections; they are built only when their inputs could be fetched
(see :mod:`visionhousing.data.sources.place`; ``safety_county`` needs an
api.data.gov key in the environment, ``safety_local`` needs none). The two
typology tables are worked out from the tables above
(:mod:`visionhousing.features.typology`). ``acs_bands`` holds the collapsed
survey tables behind the profile charts (:mod:`visionhousing.features.acs_bands`);
:func:`add_acs_bands` adds it to a pack already built.

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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing import __version__
from visionhousing.data import local as local_src
from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient, check_free_space, sha256_file
from visionhousing.data.registry import load_registry
from visionhousing.data.sources import acs, blocks, boundaries, bps, costs, prices
from visionhousing.data.sources import place as place_src
from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.data.sources import transit as transit_src
from visionhousing.districts import weights as district_fx
from visionhousing.features import acs as fx
from visionhousing.features import acs_bands as bands_fx
from visionhousing.features import affordability as afford_fx
from visionhousing.features import density
from visionhousing.features import hazards as hazard_fx
from visionhousing.features import infrastructure as infra_fx
from visionhousing.features import transit as transit_fx
from visionhousing.features import transit_walk as walk_fx
from visionhousing.features import typology as typology_fx
from visionhousing.law import tables as law_tables
from visionhousing.law import zone_build as zone_fx

__all__ = [
    "PACK_BUDGET_BYTES",
    "RETROFITS",
    "SCHEMA_VERSION",
    "TABLES",
    "PackInputs",
    "PackReport",
    "add_3a_district_tracts",
    "assemble_tables",
    "build_dictionary",
    "build_pack",
    "crosswalk_states",
    "load_inputs",
    "load_pack",
    "report_from_folder",
    "size_report",
    "write_pack",
]

SCHEMA_VERSION = 1
PACK_BUDGET_BYTES = 12_000_000
"""The whole pack folder must be smaller than this (12 MB).

Raised from 10 MB when Pennsylvania reached 10.42 MB (district outlines, census
bands, affordability and law columns), with the transit walk tables still to
come. Packs are git-ignored and built locally by ``make data``; the static site
has its own 30 MB budget (``export_static.SIZE_LIMIT_BYTES``), so this cap only
keeps a pack quick to load. The two largest files, ``blocks.csv.gz`` (about
4.6 MB) and the tract outlines (about 2.6 MB), are already at their stated
precision. See ``docs/modules/pack.md`` (Size budget).
"""
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
            "whether it is a major transit stop. Names are kept for major stops."
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

TABLES.update(place_src.TABLES)
"""Plus the place-profile tables (jobs, incentive areas, safety): see
:mod:`visionhousing.data.sources.place`. They are built only when their inputs were fetched."""

TABLES.update(typology_fx.TABLES)
"""Plus the "places like this one" tables, derived from the tables above: see
:mod:`visionhousing.features.typology`."""

TABLES.update(bands_fx.PACK_TABLE)
"""Plus the collapsed ACS bands behind the place-profile charts: see
:mod:`visionhousing.features.acs_bands`."""

TABLES.update(local_src.TABLES)
"""Plus the local refinement tables (zoning status, plans, parcels, owners, city permits,
LIHTC awards): see :mod:`visionhousing.data.local`."""

TABLES.update(walk_fx.TABLES)
"""Plus the transit walkshed tables (frequent-bus peak rule, network walksheds per tract):
see :mod:`visionhousing.features.transit_walk`. Added by :func:`add_transit_walk`."""

TABLES.update(district_fx.PACK_TABLES)
"""Plus the state legislative district list and its tract and county weights: see
:mod:`visionhousing.districts`. Added by :func:`visionhousing.districts.build.add_districts`."""

TABLES.update(afford_fx.TABLES)
"""Plus the cost and affordability tables (CHAS cost burden, income limits, assisted homes,
stock ledger): see :mod:`visionhousing.features.affordability`. Added by
:func:`visionhousing.features.affordability.add_affordability`."""

TABLES.update(infra_fx.TABLES)
"""Plus the water and sewer readiness tables (official service areas, Act 537 plan age, EPA
context): see :mod:`visionhousing.features.infrastructure`. Added by
:func:`visionhousing.features.infrastructure_build.add_infrastructure`."""

TABLES.update(hazard_fx.TABLES)
"""Plus the hazards and land screen (flood zones, land cover, impervious cover, slope): see
:mod:`visionhousing.features.hazards`. Added by
:func:`visionhousing.features.hazards_build.add_hazards`."""

TABLES.update(zone_fx.TABLES)
"""Plus ``zone_rule`` and ``rule_area_weights`` (Pennsylvania's zoning-district pilot sample):
see :func:`visionhousing.law.zone_build.add_zone_tables`."""

TABLES.update(law_tables.VERSION_TABLE)
"""Plus ``law_provision_version`` (every state; one version of one operative provision, from
``assumptions/law/timeline.yaml``): see :func:`visionhousing.law.tables.add_provision_versions`."""

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
    place: place_src.PlaceInputs | None = None
    """Place-profile inputs (jobs, incentive areas, safety); ``None`` leaves their tables out."""
    local: local_src.LocalTables | None = None
    """Local refinement tables (:mod:`visionhousing.data.local`); ``None`` leaves them out."""
    acs_bands: pd.DataFrame | None = None
    """Collapsed ACS bands (:mod:`visionhousing.features.acs_bands`); ``None`` leaves them out."""


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
        elif path.name == acs.PROFILE_EXTRACT.format(fips=fips):
            files.append(("acs5_profile_tables", path))
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
    acs_bands = None
    try:
        acs_bands = acs_band_rows(raw, state, cache, client=client, manifest=manifest)
    except (FetchError, ValueError) as err:
        notes.append(f"acs_bands left out: {err}")
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
    place = place_src.fetch_place(state, cache, client=client, manifest=manifest)
    notes += place.notes
    local = local_src.fetch_local_tables(
        state,
        cache,
        blocks=block_table,
        tract_shapes=tract_shapes,
        client=client,
        manifest=manifest,
    )
    notes += local.notes
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
        input_files=sorted(
            [*_cache_files(cache, fips), *transit.files, *place.files, *local.files]
        ),
        cache=cache,
        notes=notes,
        transit=transit,
        place=place,
        local=local,
        acs_bands=acs_bands,
    )


def acs_band_rows(
    raw: pd.DataFrame,
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    method: str = "auto",
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """The ``acs_bands`` rows of every tract (before water-only tracts are dropped).

    ``raw`` is the answer of :func:`visionhousing.data.sources.acs.fetch_acs`; the extra
    tables come from :func:`~visionhousing.data.sources.acs.fetch_acs_profile`
    (the API when ``CENSUS_API_KEY`` is set and ``method`` is ``"auto"``, else the
    keyless summary files; read from the cache when already fetched).
    """
    extra = acs.fetch_acs_profile(state, cache, client=client, method=method, manifest=manifest)
    merged = raw.merge(extra.drop(columns=["level", "geoid"]), on="GEO_ID", how="left")
    return bands_fx.band_table(merged)


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
    kept_shapes = [(s.geoid, s.geometry) for s in inputs.tract_shapes if s.geoid in tract_ids]
    tables.update(place_src.place_tables(inputs.place, tracts, counties, kept_shapes, lite))
    if inputs.local is not None:
        tables.update(inputs.local.tables)
        tables.update(law_tables.weigh_law_tables(tables))  # lot weights need tracts, blocks
    if inputs.acs_bands is not None:
        tables["acs_bands"] = _bands_for(inputs.acs_bands, tract_ids)
    tables.update(typology_fx.typology_tables(tables))
    note = f"tracts left out as open water: {', '.join(sorted(dropped))}"
    if dropped and note not in inputs.notes:
        inputs.notes.append(note)
    return {name: _tidy_numbers(t.reset_index(drop=True)) for name, t in tables.items()}


def _bands_for(rows: pd.DataFrame, tract_ids: set[str]) -> pd.DataFrame:
    """The ``acs_bands`` rows of the state, its counties and the pack's tracts."""
    keep = (rows["level"] != "tract") | rows["geoid"].astype(str).isin(tract_ids)
    return rows[keep].sort_values("geoid", kind="stable").reset_index(drop=True)


def _transit_tables(
    inputs: PackInputs, lite: pd.DataFrame, tracts: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """The stop and feed tables and the per-tract transit features.

    The major-stop rule's numbers are the ledger's central values (``parking.transit_*``).
    Without transit inputs the tables are empty and every tract gets zeros.
    """
    return _transit_tables_from(inputs.transit, inputs.tract_shapes, lite, tracts)


def _transit_tables_from(
    transit: transit_src.TransitInputs | None,
    tract_shapes: list[boundaries.Shape],
    lite: pd.DataFrame,
    tracts: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """:func:`_transit_tables` from the transit inputs and tract shapes alone."""
    from visionhousing.ledger import load_ledger

    rule = transit_fx.TransitRule.from_ledger(load_ledger())
    if transit is None:
        stops = pd.DataFrame(columns=list(transit_src.STOP_COLUMNS))
        feeds = pd.DataFrame(columns=list(transit_src.FEED_COLUMNS))
    else:
        kept = {str(t) for t in tracts["geoid"]}
        shapes = [s for s in tract_shapes if s.geoid in kept]
        stops, feeds = transit_src.build_stop_table(transit, shapes, rule, blocks=lite)
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
        "1 if a major transit stop: any rail stop, or a bus stop with "
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
    "paired_by": (
        "text",
        "How the map feed was paired with its schedule feed: id (same stop ids) or place "
        "(renumbered stops at the same places, agency names and the reverse check agree)",
    ),
    "schedule_provider": ("text", "Provider of the schedule feed"),
    "schedule_date": ("date", "Representative weekday the departures were counted on"),
    "schedule_valid_from": ("date", "First date the schedule feed covers"),
    "schedule_valid_to": ("date", "Last date the schedule feed covers"),
    "transit_units_d07": (
        "units",
        "2020 housing units in blocks within the major-stop distance "
        "(1/2 mile of a rail stop or 1/4 mile of a frequent bus stop)",
    ),
    "transit_units_share_d07": ("share", "transit_units_d07 / the tract's 2020 block units"),
    "transit_land_m2_d07": ("square metres", "Block land within the major-stop distance"),
    "transit_land_share_d07": ("share", "transit_land_m2_d07 / the tract's block land"),
    "transit_major_stops": ("stops", "Major transit stops inside the tract"),
    "transit_rich": (
        "flag",
        "1 if at least half of the tract's 2020 units are within the "
        "major-stop distance, or a major stop is inside the tract (parking reform rule)",
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
    "wage_index_method": (
        "category",
        (
            "county; state_value where BLS withholds the county; state_from_counties or "
            "national_value where it also withholds the state"
        ),
    ),
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
    if table in local_src.TABLES or table in law_tables.VERSION_TABLE:
        return {"tier": "2"}
    if (table, name) in place_src.TIER_TWO:
        return {"tier": "2"}
    if table == "transit_feeds" or table in walk_fx.TABLES:
        return {"tier": "1"}
    if table in infra_fx.TABLES:
        return {"tier": "1"}
    if table == "transit_stops" and name in _TIER_ONE_COLUMNS:
        return {"tier": "1"}
    if table == "tracts" and name.startswith("transit_"):
        return {"tier": "1"}
    return _TIER_NOTE


def _describe(table: str, name: str) -> tuple[str, str]:
    if table in bands_fx.PACK_TABLE:
        return bands_fx.describe(name)
    if table in afford_fx.TABLES:
        return afford_fx.describe(table, name)
    if table in infra_fx.TABLES:
        return infra_fx.describe(table, name)
    if table in hazard_fx.TABLES:
        return hazard_fx.describe(table, name)
    if table in typology_fx.TABLES:
        return typology_fx.describe(table, name)
    if table in district_fx.PACK_TABLES:
        return district_fx.describe(table, name)
    if table in walk_fx.TABLES:
        return walk_fx.describe(table, name)
    if table in local_src.TABLES:
        return local_src.describe(table, name)
    if table in zone_fx.TABLES:
        return zone_fx.describe(table, name)
    if table in law_tables.VERSION_TABLE:
        if name not in law_tables.VERSION_COLUMNS:
            raise ValueError(f"no data-dictionary entry for {table}.{name}")
        return law_tables.VERSION_COLUMNS[name]
    if (table, name) in place_src.COLUMNS:
        unit, text, _ = place_src.COLUMNS[(table, name)]
        return unit, text
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
    if (table, name) in place_src.COLUMNS:
        return [place_src.COLUMNS[(table, name)][2]]
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
            "tier": str(TABLES[table].get("tier", "1" if table == "transit_feeds" else "0")),
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
    retrofits: bool = True,
) -> PackReport:
    """Load the inputs (fetching what is missing), write the state pack, then add the add-on tables.

    ``retrofits`` runs the add-on steps (:data:`RETROFITS`) on the new pack in
    order, so a full build (``make data``) gives the same tables as building and
    then running each add-on command. A step that fails is noted and skipped.
    The returned report lists the files after the add-ons; the caller checks it
    against the budget (``visionhousing build`` exits 1 when it is over).
    """
    inputs = load_inputs(state, cache, client=client, manifest=manifest)
    report = write_pack(inputs, out_root, manifest=manifest)
    if not retrofits:
        return report
    notes = list(report.notes)
    for name, _tables, step in RETROFITS:
        try:
            step(report.folder, Path(cache), client=client, manifest=manifest)
        except Exception as err:  # one add-on failing must not lose the rest
            notes.append(f"{name} not added ({type(err).__name__}: {err})"[:300])
    return report_from_folder(report.folder, notes)


def report_from_folder(folder: Path, notes: list[str] | None = None) -> PackReport:
    """A :class:`PackReport` read back from a pack folder's manifest (after add-on steps)."""
    folder = Path(folder)
    data = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    files = [
        PackFile(f["path"], int(f["bytes"]), f["sha256"], f.get("rows"))
        for f in data.get("files", [])
    ]
    files.append(_file_entry(folder, "pack_manifest.json"))
    return PackReport(
        str(data.get("state") or folder.name), folder, files, data["pack_hash"], notes or []
    )


def _step_districts(folder: Path, cache: Path, **kw: Any) -> None:
    from visionhousing.districts.build import add_districts

    add_districts(folder, cache, client=kw.get("client"), manifest=kw.get("manifest"))


def _step_affordability(folder: Path, cache: Path, **kw: Any) -> None:
    afford_fx.add_affordability(folder, cache, client=kw.get("client"), manifest=kw.get("manifest"))


def _step_infrastructure(folder: Path, cache: Path, **kw: Any) -> None:
    from visionhousing.features.infrastructure_build import add_infrastructure

    add_infrastructure(folder, cache, client=kw.get("client"), manifest=kw.get("manifest"))


def _step_hazards(folder: Path, cache: Path, **kw: Any) -> None:
    from visionhousing.features.hazards_build import add_hazards

    add_hazards(folder, cache, client=kw.get("client"))


def _step_transit_walk(folder: Path, cache: Path, **kw: Any) -> None:
    add_transit_walk(folder, cache, client=kw.get("client"), manifest=kw.get("manifest"))


def _step_3a_tracts(folder: Path, cache: Path, **kw: Any) -> None:
    add_3a_district_tracts(folder, cache)


def _step_ma_capacity(folder: Path, cache: Path, **kw: Any) -> None:
    from visionhousing.features.ma_capacity_build import add_ma_capacity

    add_ma_capacity(folder, cache, client=kw.get("client"), manifest=kw.get("manifest"))


def _step_crosswalks(folder: Path, cache: Path, **kw: Any) -> None:
    add_tract_crosswalks(folder, cache, client=kw.get("client"), manifest=kw.get("manifest"))


def _step_law_versions(folder: Path, cache: Path, **kw: Any) -> None:
    law_tables.add_provision_versions(folder, cache)


def _step_zone_rules(folder: Path, cache: Path, **kw: Any) -> None:
    zone_fx.add_zone_tables(folder, cache)


RETROFITS: tuple[tuple[str, tuple[str, ...], Callable[..., None]], ...] = (
    ("districts", tuple(district_fx.PACK_TABLES), _step_districts),
    ("affordability", tuple(afford_fx.TABLES), _step_affordability),
    ("infrastructure", tuple(infra_fx.TABLES), _step_infrastructure),
    ("hazards", tuple(hazard_fx.TABLES), _step_hazards),
    ("transit walk", tuple(walk_fx.TABLES), _step_transit_walk),
    ("3a district tracts", ("mbta_3a_district_tracts",), _step_3a_tracts),
    (
        "3a capacity and estimated sewer",
        ("mbta_3a_capacity_tract", "mbta_3a_capacity_district", "ma_sewer_estimated_tract"),
        _step_ma_capacity,
    ),
    ("tract crosswalks", ("tract_municipalities", "tract_places"), _step_crosswalks),
    ("law provision versions", tuple(law_tables.VERSION_TABLE), _step_law_versions),
    ("zoning districts", tuple(zone_fx.TABLES), _step_zone_rules),
)
"""The add-on steps a full build runs after writing the pack, in order: name, tables, step.

Each is also its own command (``visionhousing districts``, ``affordability``,
``infrastructure``, ``hazards``, ``transit-walk``, ``ma-3a-tracts``, ``ma-capacity``,
``tract-crosswalks``, ``law-tables``, ``zone-rules``) for refreshing one part of a built
pack; ``law-tables`` refreshes
``law_provision_version`` in every state. The law lot weights need no step:
:func:`assemble_tables` fills them. The two 3A steps do nothing in a pack without
Massachusetts' ``mbta_3a_districts`` table; the zoning-district step does nothing outside
Pennsylvania (the only state with a pilot sample).
"""


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


# --------------------------------------------------------------------------- retrofit


def add_profile_tables(
    folder: Path,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    place: place_src.PlaceInputs | None = None,
) -> dict[str, pd.DataFrame]:
    """Add the place-profile tables and the two typology tables to an already built pack.

    A full build (:func:`build_pack`) makes these tables too; this saves
    rebuilding the whole pack when only they changed. The place-profile inputs
    (jobs, incentive areas, safety; :func:`visionhousing.data.sources.place.fetch_place`)
    are read from the cache, fetching only a file that is missing, and turned into
    tables by the same function the full build uses
    (:func:`visionhousing.data.sources.place.place_tables`). Tract shapes come from
    the cached full-detail boundary file when there is one, else from the pack's
    own boundaries. The typology is then worked out from the pack's tables, and
    the data dictionary and the pack manifest (file checksums, pack hash, inputs,
    provenance) are rewritten from every table now in the folder, the same way
    the full build writes them.

    Args:
        folder: The pack folder (``data/packs/{STATE}``), rewritten in place.
        cache: The raw-file cache the full build reads (``data/raw``).
        client: HTTP client for a missing cache file.
        manifest: The download manifest to record any fetch in.
        place: Inputs already read (tests); ``None`` fetches them from ``cache``.

    Returns:
        The tables written.
    """
    folder = Path(folder)
    cache = Path(cache)
    state = folder.name.upper()
    manifest_path = folder / "pack_manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8"))
    tables = load_pack(folder)
    if place is None:
        place = place_src.fetch_place(state, cache, client=client, manifest=manifest)
    tract_ids = set(tables["tracts"]["geoid"].astype(str))
    shapes = [
        (g, s) for g, s in _tract_shapes(folder, cache, old.get("state_fips")) if g in tract_ids
    ]
    written = place_src.place_tables(
        place, tables["tracts"], tables["counties"], shapes, tables.get("blocks")
    )
    new_inputs: list[tuple[str, Path]] = list(place.files)
    tables.update(written)
    derived = typology_fx.typology_tables(tables)
    written.update(derived)
    _rewrite(folder, cache, written, new_inputs)
    return written


def _rewrite(
    folder: Path,
    cache: Path,
    written: Mapping[str, pd.DataFrame],
    new_inputs: list[tuple[str, Path]],
) -> None:
    """Write retrofitted tables, then rewrite the dictionary and the pack manifest.

    The manifest's file list, pack hash, inputs and provenance are rebuilt from
    every table now in the folder, the same way the full build writes them.
    """
    state = folder.name.upper()
    manifest_path = folder / "pack_manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, frame in written.items():
        _write_gz_csv(_tidy_numbers(frame.reset_index(drop=True)), folder / f"{name}.csv.gz")
    tables = load_pack(folder)
    dictionary = build_dictionary(tables, state)
    (folder / "data_dictionary.json").write_text(_json_text(dictionary), encoding="utf-8")
    files = [
        f
        for f in old["files"]
        if not f["path"].endswith(".csv.gz") and f["path"] != "data_dictionary.json"
    ]
    for name, frame in tables.items():
        path = folder / f"{name}.csv.gz"
        files.append(
            {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path),
             "rows": len(frame)}
        )  # fmt: skip
    files.append(_file_entry(folder, "data_dictionary.json").__dict__)
    files.sort(key=lambda f: f["path"])
    digest = hashlib.sha256()
    for f in files:
        digest.update(f"{f['path']}\t{f['sha256']}\n".encode())
    replaced = {sid for sid, _ in new_inputs}
    inputs = [r for r in old.get("inputs", []) if r.get("source") not in replaced]
    for sid, path in new_inputs:
        if path.exists():
            inputs.append(
                {"source": sid, "file": path.relative_to(cache).as_posix(),
                 "sha256": sha256_file(path), "bytes": path.stat().st_size,
                 "accessed": _read_on(path)}
            )  # fmt: skip
    inputs.sort(key=lambda r: (r["source"], r["file"]))
    old.update(
        {"pack_hash": digest.hexdigest(), "files": files, "inputs": inputs,
         "provenance": _provenance(inputs)}
    )  # fmt: skip
    manifest_path.write_text(_json_text(old), encoding="utf-8")


def add_acs_bands(
    folder: Path,
    cache: Path,
    *,
    client: HttpClient | None = None,
    method: str = "auto",
    manifest: Path | None = mf.MANIFEST_PATH,
    rows: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Add (or refresh) the ``acs_bands`` table in an already built pack.

    The ACS cells are read from the cache (fetching only what is missing: the API
    when ``CENSUS_API_KEY`` is set and ``method`` is ``"auto"``, else the keyless
    summary files), collapsed into bands, cut to the pack's tracts, and written;
    then the dictionary and the manifest are rewritten as a full build would.

    Args:
        folder: The pack folder (``data/packs/{STATE}``), rewritten in place.
        cache: The raw-file cache (``data/raw``).
        client: HTTP client for a missing cache file.
        method: ``"auto"``, ``"summary"`` or ``"api"`` (see
            :func:`visionhousing.data.sources.acs.fetch_acs_profile`).
        manifest: The download manifest to record any fetch in.
        rows: Band rows already made by
            :func:`visionhousing.features.acs_bands.band_table` (tests); ``None``
            reads the ACS cells from ``cache``.

    Returns:
        The table written.
    """
    folder, cache = Path(folder), Path(cache)
    state = folder.name.upper()
    if rows is None:
        base = acs.fetch_acs(state, acs.VINTAGE, cache, client=client, manifest=manifest)
        rows = acs_band_rows(base, state, cache, client=client, method=method, manifest=manifest)
    tracts = load_pack(folder)["tracts"]
    table = _bands_for(rows, set(tracts["geoid"].astype(str)))
    old = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    fips = str(old.get("state_fips") or to_state_fips(state))
    extract = cache / "acs" / str(acs.VINTAGE) / acs.PROFILE_EXTRACT.format(fips=fips)
    _rewrite(folder, cache, {"acs_bands": table}, [("acs5_profile_tables", extract)])
    return table


def add_transit_stops(
    folder: Path,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> dict[str, pd.DataFrame]:
    """Rebuild ``transit_stops``, ``transit_feeds`` and the tracts' ``transit_*`` columns in a pack.

    For a pack built before a schedule feed was added (for example a feed from
    ``assumptions/transit_feeds.yaml``): the transit inputs are read from the
    cache, fetching only what is missing (:func:`transit_src.fetch_transit`),
    the stop and feed tables are made exactly as a full build makes them
    (:func:`_transit_tables`), and the tracts' transit columns are replaced.
    Every other table stays as it is. Run :func:`add_transit_walk` afterwards,
    since the walkshed tables read the stop table.

    Returns:
        The tables written (``transit_stops``, ``transit_feeds``, ``tracts``).
    """
    folder, cache = Path(folder), Path(cache)
    state = folder.name.upper()
    tracts = load_pack(folder)["tracts"]
    block_table, _info = blocks.fetch_blocks(state, cache, client=client, manifest=manifest)
    dropped = set(block_table["tract"].astype(str)) - set(tracts["geoid"].astype(str))
    lite = blocks.blocks_lite(block_table[~block_table["tract"].astype(str).isin(dropped)])
    county_shapes, tract_shapes = boundaries.fetch_boundaries(
        state, cache, client=client, manifest=manifest
    )
    transit = transit_src.fetch_transit(
        state, county_shapes, cache, client=client, manifest=manifest
    )
    stops, feeds, features = _transit_tables_from(transit, tract_shapes, lite, tracts)
    keep = [c for c in tracts.columns if c not in set(features.columns) - {"tract"}]
    tracts = tracts[keep].merge(
        features.rename(columns={"tract": "geoid"}), on="geoid", how="left", validate="1:1"
    )
    written = {"transit_stops": stops, "transit_feeds": feeds, "tracts": tracts}
    _rewrite(folder, cache, written, list(transit.files))
    return written


def add_transit_walk(
    folder: Path,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    counties: list[str] | None = None,
    stop_report: Path | None = None,
    log: object = print,
) -> dict[str, pd.DataFrame]:
    """Add (or refresh) ``transit_walk_tract`` and ``transit_walk_county`` in a built pack.

    Steps (method: :mod:`visionhousing.features.transit_walk`): count peak bus
    departures from each paired, current schedule feed (cached in
    ``data/raw/transit/gtfs_<id>_peaks*``); flag rail stops and frequent bus
    stops; fetch (or reuse) the OpenStreetMap walking network tiles around them
    (``data/raw/osm/``); draw the dissolved network and straight-line walksheds
    county by county; cut them by the tract outlines; then rewrite the data
    dictionary and the pack manifest as a full build would.

    Args:
        folder: The pack folder (``data/packs/{STATE}``), rewritten in place.
        cache: The raw-file cache (``data/raw``).
        client: HTTP client for missing cache files.
        manifest: The download manifest to record fetches in.
        counties: Only these counties (a partial table; for checks).
        stop_report: Write each rail stop's own 1/2-mile walkshed to this CSV.
        log: Function that prints progress.

    Returns:
        The tables written.
    """
    from visionhousing.data.sources import osm
    from visionhousing.data.sources import transit_peaks as peaks_src
    from visionhousing.features import walkshed as ws
    from visionhousing.ledger import load_ledger

    folder, cache = Path(folder), Path(cache)
    say = log if callable(log) else (lambda _m: None)
    old = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    fips = str(old.get("state_fips") or to_state_fips(folder.name.upper()))
    tables = load_pack(folder)
    stops, feeds = tables["transit_stops"], tables["transit_feeds"]
    ledger = load_ledger()
    rules = walk_fx.load_rules()
    peak_rule = walk_fx.PeakRule.from_ledger(ledger)
    walk_rule = ws.WalkRule.from_ledger(ledger)
    feeds = feeds.fillna({"feed_id": "", "schedule_feed": "", "frequency": ""})
    wanted = sorted(
        {
            str(s).split(".")[0]
            for f, s, q, b in zip(
                feeds["feed_id"], feeds["schedule_feed"], feeds["frequency"], feeds["bus_stops"],
                strict=True,
            )
            if str(f) and str(s) and not str(q).startswith("unknown") and int(b or 0) > 0
        }
    )  # fmt: skip
    peaks, notes, peak_files = {}, {}, []
    for mdb in wanted:
        table, note = peaks_src.fetch_peaks(mdb, cache, client=client, manifest=manifest)
        notes[mdb] = note
        say(f"peaks {mdb}: {note.get('status')} {note.get('date')} ({note.get('day_rule')})")
        if table is not None:
            peaks[mdb] = table
        for name in (f"gtfs_{mdb}_peaks.csv.gz", f"gtfs_{mdb}_peaks_note.json"):
            if (cache / "transit" / name).exists():
                peak_files.append(cache / "transit" / name)
    stops = walk_fx.frequent_bus_stops(stops, feeds, peaks, peak_rule)
    tract_ids = set(tables["tracts"]["geoid"].astype(str))
    shapes = [(g, s) for g, s in _tract_shapes(folder, cache, fips) if g in tract_ids]
    tiles_seen: set[Path] = set()

    def fetch(tiles: list[tuple[int, int]]) -> tuple[list[Path], list[tuple[int, int]]]:
        got, failed = osm.fetch_tiles(
            tiles, cache, rules["osm"], client=client, manifest=manifest, log=say
        )
        tiles_seen.update(got)
        return got, failed

    tract, county_net, report = walk_fx.build_transit_walk(
        stops, shapes, peak_rule, walk_rule, rules, fetch, osm.read_tiles,
        counties=counties, stop_report=stop_report is not None, log=say,
    )  # fmt: skip
    county = walk_fx.county_coverage(stops, notes, feeds).merge(county_net, on="county", how="left")
    for col in ("fallback_stops", "osm_tiles", "osm_tiles_failed"):
        county[col] = county[col].fillna(0).astype("int64")
    county = county[list(walk_fx.COUNTY_COLUMNS)].sort_values("county").reset_index(drop=True)
    if report is not None and stop_report is not None:
        Path(stop_report).parent.mkdir(parents=True, exist_ok=True)
        (report.sort_values("ratio") if len(report) else report).to_csv(stop_report, index=False)
    index = cache / "osm" / f"tiles_index_{fips}.csv"
    index.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted((p.relative_to(cache).as_posix(), sha256_file(p)) for p in tiles_seen)
    index.write_text("file,sha256\n" + "".join(f"{a},{b}\n" for a, b in rows), encoding="utf-8")
    gtfs_old = [
        cache / r["file"]
        for r in old.get("inputs", [])
        if r.get("source") == transit_src.SOURCE_GTFS
    ]
    new_inputs = [(transit_src.SOURCE_GTFS, p) for p in sorted(set(gtfs_old) | set(peak_files))]
    new_inputs.append((osm.SOURCE, index))
    written = {"transit_walk_tract": tract, "transit_walk_county": county}
    _rewrite(folder, cache, written, new_inputs)
    return written


def add_3a_district_tracts(
    folder: Path,
    cache: Path,
    *,
    settings: Mapping[str, Any] | None = None,
    lock: Callable[[], Any] | None = None,
) -> pd.DataFrame | None:
    """Add (or refresh) ``mbta_3a_district_tracts``: every tract each 3A district reaches.

    Reads the pack's ``mbta_3a_districts`` outlines and the tract outlines
    (the cached full-detail file, else the pack's boundaries), links them with
    :func:`visionhousing.features.ma_intake.district_tract_links` (settings:
    ``district_tracts`` in ``assumptions/ma_intake.yaml``), then rewrites the
    data dictionary and the pack manifest as a full build would. No download.

    Args:
        folder: The pack folder (``data/packs/{STATE}``), rewritten in place.
        cache: The raw-file cache (``data/raw``).
        settings: The ``district_tracts`` block (tests); ``None`` reads the file.
        lock: Optional context-manager factory held only while files are written.

    Returns:
        The table written, or ``None`` when the pack has no district outlines
        (every state but Massachusetts): nothing is written then.
    """
    import contextlib

    from visionhousing.data.sources.ma_intake import load_settings as ma_settings
    from visionhousing.features import ma_intake as ma_fx

    folder, cache = Path(folder), Path(cache)
    path = folder / "mbta_3a_districts.csv.gz"
    if not path.is_file():
        return None
    districts = pd.read_csv(path, dtype={"tract": str, "county": str}, compression="gzip")
    if districts.empty or "geometry" not in districts.columns:
        return None
    rule = dict(settings or ma_settings()["district_tracts"])
    old = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    fips = str(old.get("state_fips") or to_state_fips(folder.name.upper()))
    tract_ids = set(pd.read_csv(folder / "tracts.csv.gz", dtype={"geoid": str})["geoid"])
    shapes = [(g, s) for g, s in _tract_shapes(folder, cache, fips) if g in tract_ids]
    table = ma_fx.district_tract_links(
        districts, shapes, cell_m=float(rule["cell_m"]), min_share=float(rule["min_share"])
    )
    with lock() if lock is not None else contextlib.nullcontext():
        _rewrite(folder, cache, {"mbta_3a_district_tracts": table}, [])
    return table


def add_tract_crosswalks(
    folder: Path,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    states: tuple[str, ...] | None = None,
    lock: Callable[[], Any] | None = None,
) -> dict[str, pd.DataFrame]:
    """Add ``tract_municipalities`` and ``tract_places`` to a pack whose towns are read locally.

    Acts in two kinds of state (:func:`crosswalk_states`):

    - a state with places in ``assumptions/law/local_readings.yaml`` (Vermont,
      Wyoming) gets both tables;
    - a state in the ``ma_adu_application`` block of
      ``assumptions/law/statutory_rights.yaml`` (Massachusetts) gets
      ``tract_municipalities`` only, so the named variant ``ma_adu_state_floor``
      measures each tract's Boston share (a Massachusetts town is a county
      subdivision) instead of the judged Suffolk County share.

    For a state with local readings, the named variants place each reading on tracts by
    2020 housing units, the same method Pennsylvania's build uses
    (:func:`visionhousing.features.zoning_status.tract_municipalities`): the
    pack's blocks are joined to the Census Bureau's 2020 block assignment
    files, county subdivisions (:func:`munstats.fetch_block_subdivisions`)
    and places (:func:`munstats.fetch_block_places`), and their housing units
    summed per tract piece. Split tracts keep one row per piece. A Vermont
    town is a county subdivision; a Wyoming city is a Census place, so
    ``tract_places`` is written for both. ``tract_municipalities`` is skipped
    where the zip has no county subdivision member (Wyoming's subdivisions are
    statistical census county divisions, not governments), and kept as it is
    in a pack that already has it (Pennsylvania's comes with its local tables).

    Args:
        folder: The pack folder, rewritten in place (dictionary and manifest too).
        cache: The raw-file cache (``data/raw``).
        client: HTTP client.
        manifest: Download manifest, or ``None``.
        states: The states to act on (tests; both tables); ``None`` =
            :func:`crosswalk_states`.
        lock: Optional context-manager factory held only while files are written.

    Returns:
        The tables written (empty for any other state).
    """
    import contextlib

    from visionhousing.data.sources import munstats
    from visionhousing.features import zoning_status as zoning_fx

    folder, cache = Path(folder), Path(cache)
    state = folder.name.upper()
    if states is None:
        towns, places = crosswalk_states()
    else:
        towns = places = tuple(s.upper() for s in states)
    if state not in towns and state not in places:
        return {}
    blocks_t = pd.read_csv(
        folder / "blocks.csv.gz",
        dtype={"geoid": str, "tract": str},
        usecols=["geoid", "tract", "housing_units"],
    )
    fips = to_state_fips(state)
    written: dict[str, pd.DataFrame] = {}
    inputs: list[tuple[str, Path]] = []
    census = cache / "local" / "census"
    if not (folder / "tract_municipalities.csv.gz").is_file():
        try:
            baf = munstats.fetch_block_subdivisions(state, cache, client=client, manifest=manifest)
        except LookupError:
            baf = None  # census county divisions only (Wyoming): no municipal table
        if baf is not None:
            written["tract_municipalities"] = zoning_fx.tract_municipalities(blocks_t, baf)
            inputs.append((munstats.BAF_SOURCE, census / f"block_cousub_2020_{fips}.csv.gz"))
    if state in places:
        bap = munstats.fetch_block_places(state, cache, client=client, manifest=manifest)
        written["tract_places"] = zoning_fx.tract_places(blocks_t, bap, munstats.NO_PLACE)
        inputs.append((munstats.BAF_PLACE_SOURCE, census / f"block_place_2020_{fips}.csv.gz"))
    if not written:
        return {}
    with lock() if lock is not None else contextlib.nullcontext():
        _rewrite(folder, cache, written, inputs)
    return written


def crosswalk_states() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The states the tract-crosswalk step acts on: ``(town table states, place table states)``.

    Both come from the assumption files, not from code: the states with local
    readings (``assumptions/law/local_readings.yaml``) get both tables; the
    states of the Massachusetts ADU-rights application
    (``ma_adu_application.states`` in ``assumptions/law/statutory_rights.yaml``)
    get the town table too, for the Boston carve-out.
    """
    from visionhousing.law.local_readings import load_local_readings
    from visionhousing.law.ma_adu import application

    places = tuple(sorted({p.state.upper() for p in load_local_readings().places}))
    rights = tuple(str(s).upper() for s in application().get("states") or ())
    return tuple(sorted({*places, *rights})), places


def _tract_shapes(
    folder: Path, cache: Path, fips: str | None
) -> list[tuple[str, dict[str, object]]]:
    """``(tract code, geometry)`` from the cached full-detail file, else the pack's boundaries."""
    full = cache / "boundaries" / f"cb_2020_{fips}_tract_500k.geojson.gz"
    if fips and full.exists():
        return [(s.geoid, s.geometry) for s in boundaries._load_extract(full)]
    out: list[tuple[str, dict[str, object]]] = []
    for path in sorted((folder / "boundaries" / "tracts").glob("*.geojson")):
        for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
            out.append((str(f["properties"]["geoid"]), f["geometry"]))
    return out


def _read_on(path: Path) -> str | None:
    """The ``read_on`` date written in a cached extract, if it has one.

    A CSV extract carries it in a ``read_on`` column; a cached JSON layer
    (``*.json`` or ``*.json.gz``, one long line that must not be parsed as CSV)
    as a top-level ``read_on`` key.
    """
    name = Path(path).name
    if name.endswith((".json", ".json.gz")):
        try:
            opener = gzip.open if name.endswith(".gz") else open
            with opener(path, "rt", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return None
        value = doc.get("read_on") if isinstance(doc, dict) else None
        return str(value) if value else None
    try:
        first = pd.read_csv(path, nrows=1, dtype=str)
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return None
    return str(first["read_on"].iloc[0]) if "read_on" in first.columns and len(first) else None
