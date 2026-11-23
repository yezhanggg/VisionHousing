"""Local refinement tables (tier 2): zoning, plans, parcels, owners, local permit pipeline.

Plain-language summary
----------------------
Some data exist only for one state or one city: Pennsylvania's municipal
zoning record, Philadelphia's and Allegheny County's parcel files, the two
cities' permit files and PHFA's tax-credit award lists. This module fetches
them (through the source modules in :mod:`visionhousing.data.sources`), turns
them into small pack tables (through :mod:`visionhousing.features`), and
describes every table and column for the pack's data dictionary.

- :func:`fetch_local_tables` is called by the pack build
  (:func:`visionhousing.data.pack.load_inputs`). For a state with no local
  sources it returns nothing, and the profile sections say "no data".
- :func:`add_to_pack` adds the tables to a pack that is already built,
  rewriting its data dictionary and manifest so the pack stays consistent.
  It saves rebuilding the whole pack when only these sources changed.

Each source is fetched on its own. When one fails (a city service is down)
the others still go in, and the failure is written in the build notes.

Tables (all tier 2, "local refinement only"):

====================== ============================================== ==========
table                  one row per                                    places
====================== ============================================== ==========
municipal_planning     county subdivision (municipality) part         PA
tract_municipalities   tract piece inside one municipality            PA
parcels_area           tract, and county                              42101, 42003
owners_area            tract, and county                              42101, 42003
owners_deeds_year      year                                           42101
pipeline_tract_year    tract, year and city permit source             42101, 42003
lihtc_awards           development awarded tax credits                PA
====================== ============================================== ==========
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.sources import alle_assess, local_permits, munstats, phfa, phl_opa
from visionhousing.features import owners as owners_fx
from visionhousing.features import parcels as parcels_fx
from visionhousing.features import pipeline_local as pipeline_fx
from visionhousing.features import zoning_status as zoning_fx

__all__ = [
    "COLUMNS",
    "TABLES",
    "LocalTables",
    "add_to_pack",
    "describe",
    "fetch_local_tables",
]

ROOT = Path(__file__).resolve().parents[3]
PIPELINE_RULES = ROOT / "assumptions" / "pipeline_local.yaml"
STATES = ("PA",)
"""States with local sources."""

TABLES: dict[str, dict[str, object]] = {
    "municipal_planning": {
        "key": ["county", "cousub"],
        "sources": ["pa_dced_munstats", "census_subcounty_pop", "census_baf_2020_mcd"],
        "description": (
            "One row per county subdivision (municipality) part: who regulates zoning there "
            "(own, joint, county, none), comprehensive plan, other planning tools (PA DCED "
            "Municipal Statistics), 2024 population estimate and 2020 housing units."
        ),
        "tier": "2",
    },
    "tract_municipalities": {
        "key": ["tract", "cousub"],
        "sources": ["census_baf_2020_mcd", "tiger_2020_tabblock"],
        "description": "2020 housing units of each tract, split by the municipality of its blocks.",
        "tier": "2",
    },
    "parcels_area": {
        "key": ["level", "geoid"],
        "sources": [
            "phl_opa_properties",
            "alle_assessments",
            "alle_parcel_centroids",
            "alle_property_sales",
            "cb_2020_tract",
        ],
        "description": (
            "Parcel counts by land use, single-family values, lot sizes, year built and recent "
            "sales, per tract and county, for Philadelphia and Allegheny only."
        ),
        "tier": "2",
    },
    "owners_area": {
        "key": ["level", "geoid"],
        "sources": ["phl_opa_properties", "alle_assessments", "alle_parcel_centroids"],
        "description": (
            "Single-family parcels owned by an entity, without a homestead exemption, or billed "
            "outside the state, per tract and county (Philadelphia and Allegheny). Counts only."
        ),
        "tier": "2",
    },
    "owners_deeds_year": {
        "key": ["county", "year"],
        "sources": ["phl_rtt_summary", "phl_opa_properties"],
        "description": "Arm's-length single-family deeds a year; those to entities (Philadelphia).",
        "tier": "2",
    },
    "pipeline_tract_year": {
        "key": ["tract", "year", "source"],
        "sources": ["phl_li_permits", "pgh_pli_permits", "cb_2020_tract"],
        "description": (
            "City new-home building permits per 2020 tract and year: Philadelphia with homes, "
            "Pittsburgh permits only."
        ),
        "tier": "2",
    },
    "lihtc_awards": {
        "key": ["year", "development"],
        "sources": ["phfa_lihtc_awards", "census_geocoder"],
        "description": "Developments awarded Low-Income Housing Tax Credits by PHFA.",
        "tier": "2",
    },
}
"""Every local table: key, source ids, description and tier."""

_COMMON: dict[str, tuple[str, str]] = {
    "county": ("code", "County code (5 digits)"),
    "cousub": ("code", "Census county subdivision code (5 digits, within the county)"),
    "tract": ("code", "2020 census tract code (11 digits); empty when not placed in a tract"),
    "geoid": ("code", "County (5 digits) or tract (11 digits) code"),
    "level": ("category", "county or tract"),
    "year": ("year", "Calendar year"),
    "as_of": ("date", "Date the records were read; sales windows count back from it"),
    "name": ("text", "Census name of the municipality"),
    "dced_id": ("code", "DCED municipality id (empty when not matched)"),
    "zoning_regime": ("category", "Who regulates zoning: own, joint, county or none"),
    "comprehensive_plan": ("flag", "1 when the municipality has a comprehensive plan"),
    "multi_municipal_plan": ("flag", "1 when that plan covers two or more municipalities"),
    "subdivision_ordinance": ("flag", "1 when it has its own subdivision ordinance"),
    "planning_commission": ("flag", "1 when it has a planning commission"),
    "zoning_hearing_board": ("flag", "1 when it has a zoning hearing board"),
    "ucc_code": ("flag", "1 when it has opted in to enforce the Uniform Construction Code"),
    "population": ("persons", "Census population estimate, July 1, 2024"),
    "housing_units_2020": ("units", "2020 Census housing units (sum of blocks)"),
    "value_basis": ("text", "Which assessed value the medians use"),
    "parcels": ("parcels", "Parcels in the assessment file"),
    "sf_parcels": ("parcels", "Single-family parcels"),
    "sf_median_value": ("dollars", "Median assessed value of single-family parcels"),
    "sf_median_lot_sqft": ("square feet", "Median lot area of single-family parcels"),
    "sf_built_unknown": ("parcels", "Single-family parcels with no year built"),
    "sales_short": (
        "sales",
        "Single-family sales in the short window (months in assumptions/parcels.yaml)",
    ),
    "sales_long": ("sales", "Single-family sales in the long window"),
    "median_sale_short": ("dollars", "Median single-family sale price, short window"),
    "median_sale_long": ("dollars", "Median single-family sale price, long window"),
    "unplaced": ("parcels", "Parcels not placed in any tract (county rows)"),
    "entity_basis": ("text", "How an owner is judged to be an entity"),
    "sf_entity": ("parcels", "Single-family parcels owned by an entity"),
    "sf_non_homestead": ("parcels", "Single-family parcels without a homestead exemption"),
    "sf_out_of_state": (
        "parcels",
        "Single-family parcels with a tax-bill address outside the state",
    ),
    "deeds": ("deeds", "Arm's-length single-family deeds"),
    "entity_deeds": ("deeds", "Of those, deeds to an entity grantee"),
    "partial_year": ("flag", "1 for the year the records were read (not a full year)"),
    "source": ("code", "Source id in sources.yaml"),
    "permits": ("permits", "New-home building permits issued"),
    "units": ("units", "Homes on the permits or in the development"),
    "development": ("text", "Development name as listed by PHFA"),
    "city": ("text", "City as listed by PHFA"),
    "lihtc_award": ("dollars", "Federal LIHTC amount awarded (annual credit), as listed"),
}


def describe(table: str, name: str) -> tuple[str, str]:
    """Unit and plain description of a local table's column (for the data dictionary)."""
    if name in _COMMON:
        return _COMMON[name]
    if name.startswith("lu_"):
        label = parcels_fx.load_rules()["land_use_classes"].get(name[3:], name[3:])
        return "parcels", f"Parcels in land-use class: {label}"
    if name.startswith("sf_built_"):
        lo, hi = parcels_fx.load_rules()["year_built_groups"][name[len("sf_built_") :]]
        span = f"{hi} or earlier" if lo == 0 else (f"{lo} or later" if hi >= 9999 else f"{lo}-{hi}")
        return "parcels", f"Single-family parcels built {span}"
    raise ValueError(f"no data-dictionary entry for {table}.{name}")


COLUMNS = _COMMON
"""Shared column descriptions (see :func:`describe` for generated ones)."""


@dataclass
class LocalTables:
    """What :func:`fetch_local_tables` produced."""

    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    files: list[tuple[str, Path]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _shapes_for(shapes: Iterable[Any], county: str) -> list[tuple[str, Mapping[str, Any]]]:
    out = []
    for s in shapes:
        geoid, geometry = (s.geoid, s.geometry) if hasattr(s, "geoid") else s
        if str(geoid).startswith(county):
            out.append((str(geoid), geometry))
    return out


def _try(notes: list[str], label: str, step: Callable[[], Any]) -> Any:
    try:
        return step()
    except Exception as err:
        notes.append(f"{label}: not added ({type(err).__name__}: {str(err)[:200]})")
        return None


def fetch_local_tables(
    state: str,
    cache: Path,
    *,
    blocks: pd.DataFrame,
    tract_shapes: Iterable[Any],
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> LocalTables:
    """Fetch every local source for the state and build its tables.

    Args:
        state: Postal code; only ``PA`` has local sources.
        cache: The raw-data cache (``data/raw``).
        blocks: Pack blocks (``geoid``, ``tract``, ``housing_units``).
        tract_shapes: 2020 tract shapes (:class:`~visionhousing.data.sources.boundaries.Shape`
            or ``(geoid, geometry)`` pairs).
        client: HTTP client.
        manifest: Download manifest, or ``None``.
        refresh: Download again even when the cache has the files.
    """
    out = LocalTables()
    if state.upper() not in STATES:
        return out
    cache = Path(cache)
    client = client or HttpClient(timeout=300)
    shapes = list(tract_shapes)
    notes = out.notes

    def zoning() -> None:
        rules = zoning_fx.load_rules()
        dced = munstats.fetch_munstats(cache, manifest=manifest, refresh=refresh)
        pop = munstats.fetch_subdivision_population(state, cache, client=client, manifest=manifest)
        baf = munstats.fetch_block_subdivisions(state, cache, client=client, manifest=manifest)
        cw = munstats.crosswalk(dced, munstats.census_subdivisions(pop), rules["name_overrides"])
        pieces = zoning_fx.tract_municipalities(blocks, baf)
        out.tables["municipal_planning"] = zoning_fx.municipal_planning(
            cw, pieces, int(rules["multi_municipal_min_members"])
        )
        out.tables["tract_municipalities"] = pieces
        notes.append(f"munstats: {cw.match_rate:.1%} of DCED municipalities matched to Census")
        if len(cw.unmatched_dced):
            notes.append("munstats unmatched DCED: " + ", ".join(cw.unmatched_dced["name"]))
        if len(cw.unmatched_census):
            notes.append("munstats unmatched Census: " + ", ".join(cw.unmatched_census["name"]))

    p_rules, o_rules = parcels_fx.load_rules(), owners_fx.load_rules()
    parcel_parts: list[pd.DataFrame] = []
    owner_parts: list[pd.DataFrame] = []

    def philadelphia() -> None:
        parcels, read_on = phl_opa.fetch_opa_parcels(
            cache,
            entity_words=o_rules["entity_words"],
            home_state=o_rules["home_state"],
            condo_word=p_rules["philadelphia"]["condo_word"],
            client=client,
            manifest=manifest,
            refresh=refresh,
        )
        tracts = parcels_fx.place_in_tracts(
            parcels["lat"], parcels["lon"], _shapes_for(shapes, parcels_fx.PHILADELPHIA)
        )
        parcel_parts.append(parcels_fx.philadelphia_parcels(parcels, tracts, read_on, p_rules))
        owner_parts.append(owners_fx.philadelphia_owners(parcels, tracts, read_on, p_rules))
        d = o_rules["philadelphia_deeds"]
        flow = phl_opa.fetch_deed_flow(
            cache,
            entity_words=o_rules["entity_words"],
            document_types=d["document_types"],
            min_consideration=d["min_consideration"],
            max_property_count=d["max_property_count"],
            first_year=d["first_year"],
            condo_word=p_rules["philadelphia"]["condo_word"],
            client=client,
            manifest=manifest,
            refresh=refresh,
        )
        out.tables["owners_deeds_year"] = owners_fx.deeds_year(flow)

    def allegheny() -> None:
        sf_uses = parcels_fx.single_family_uses(p_rules)
        kw = {"client": client, "manifest": manifest, "refresh": refresh}
        uses = alle_assess.fetch_use_counts(cache, **kw)
        sf = alle_assess.fetch_single_family(
            cache,
            single_family_uses=sf_uses,
            year_groups=p_rules["year_built_groups"],
            entity_owner_types=o_rules["allegheny"]["entity_owner_types"],
            home_state=o_rules["home_state"],
            **kw,
        )
        sales = alle_assess.fetch_sales(
            cache,
            single_family_uses=sf_uses,
            valid_codes=p_rules["allegheny"]["valid_sale_codes"],
            windows=tuple(p_rules["sales_windows_months"]),
            **kw,
        )
        parcel_parts.append(parcels_fx.allegheny_parcels(uses, sf, sales, p_rules))
        owner_parts.append(owners_fx.allegheny_owners(sf))

    pipe_rules = yaml.safe_load(PIPELINE_RULES.read_text(encoding="utf-8"))
    pipe_parts: list[pd.DataFrame] = []

    def phl_permits() -> None:
        r = pipe_rules["philadelphia"]
        permits = local_permits.fetch_phl_permits(
            cache,
            permit_types=r["permit_types"],
            work_types=r["work_types"],
            first_year=pipe_rules["first_year"],
            client=client,
            manifest=manifest,
            refresh=refresh,
        )
        pipe_parts.append(
            pipeline_fx.permits_by_tract_year(
                permits,
                _shapes_for(shapes, parcels_fx.PHILADELPHIA),
                parcels_fx.PHILADELPHIA,
                local_permits.PHL_SOURCE,
                has_units=True,
            )
        )

    def pgh_permits() -> None:
        r = pipe_rules["pittsburgh"]
        permits = local_permits.fetch_pgh_permits(
            cache,
            permit_types=r["permit_types"],
            work_type_like=r["work_type_like"],
            use=r["use"],
            first_year=pipe_rules["first_year"],
            client=client,
            manifest=manifest,
            refresh=refresh,
        )
        pipe_parts.append(
            pipeline_fx.permits_by_tract_year(
                permits,
                _shapes_for(shapes, parcels_fx.ALLEGHENY),
                parcels_fx.ALLEGHENY,
                local_permits.PGH_SOURCE,
                has_units=False,
            )
        )

    def lihtc() -> None:
        r = pipe_rules["phfa"]
        got = phfa.fetch_awards(
            cache,
            index_url=r["index_url"],
            link_pattern=r["link_pattern"],
            first_year=r["first_year"],
            client=client,
            manifest=manifest,
            refresh=refresh,
        )
        out.tables["lihtc_awards"] = pipeline_fx.lihtc_table(got.awards)
        for year, why in got.skipped.items():
            notes.append(f"PHFA {year} award list skipped: {why}")

    _try(notes, "zoning status (DCED Munstats)", zoning)
    _try(notes, "Philadelphia parcels and owners", philadelphia)
    _try(notes, "Allegheny parcels and owners", allegheny)
    _try(notes, "Philadelphia permits", phl_permits)
    _try(notes, "Pittsburgh permits", pgh_permits)
    _try(notes, "PHFA LIHTC awards", lihtc)
    if parcel_parts:
        out.tables["parcels_area"] = pd.concat(parcel_parts, ignore_index=True)
    if owner_parts:
        out.tables["owners_area"] = pd.concat(owner_parts, ignore_index=True)
    if pipe_parts:
        out.tables["pipeline_tract_year"] = pd.concat(pipe_parts, ignore_index=True)
    local_dir = cache / "local"
    if local_dir.is_dir():
        for path in sorted(p for p in local_dir.rglob("*") if p.is_file()):
            sid = _source_of(path)
            if sid:
                out.files.append((sid, path))
    return out


def _source_of(path: Path) -> str | None:
    """The source id of a cached local file, from its folder and name."""
    folder, name = path.parent.name, path.name
    if folder == "munstats":
        return "pa_dced_munstats"
    if folder == "census":
        return "census_baf_2020_mcd" if name.startswith("block_cousub") else "census_subcounty_pop"
    if folder == "phl":
        return {
            "opa_parcels.csv.gz": "phl_opa_properties",
            "deed_flow.csv": "phl_rtt_summary",
            "new_home_permits.csv": "phl_li_permits",
        }.get(name)
    if folder == "allegheny":
        return (
            "alle_property_sales"
            if name == "sales.csv"
            else ("alle_assessments" if name.endswith(".csv") else None)
        )
    if folder == "pittsburgh":
        return "pgh_pli_permits"
    if folder == "phfa" and name.endswith(".csv"):
        return "phfa_lihtc_awards"
    return None


# --------------------------------------------------------------------------- retrofit


def add_to_pack(
    folder: Path,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> LocalTables:
    """Add the local tables to an already built pack and rewrite its dictionary and manifest.

    The pack's blocks and tract outlines are read from the pack itself. The
    new tables are written next to the old ones; the data dictionary and the
    pack manifest (file checksums, pack hash, provenance) are rebuilt from
    every table now in the folder, the same way the full build writes them.
    """
    from visionhousing.data import pack

    folder = Path(folder)
    state = folder.name.upper()
    manifest_path = folder / "pack_manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8"))
    blocks = pd.read_csv(
        folder / "blocks.csv.gz",
        dtype={"geoid": str, "tract": str},
        usecols=["geoid", "tract", "housing_units"],
    )
    shapes: list[tuple[str, Mapping[str, Any]]] = []
    for path in sorted((folder / "boundaries" / "tracts").glob("*.geojson")):
        for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
            shapes.append((str(f["properties"]["geoid"]), f["geometry"]))
    local = fetch_local_tables(
        state,
        cache,
        blocks=blocks,
        tract_shapes=shapes,
        client=client,
        manifest=manifest,
        refresh=refresh,
    )
    for name, frame in local.tables.items():
        pack._write_gz_csv(
            pack._tidy_numbers(frame.reset_index(drop=True)), folder / f"{name}.csv.gz"
        )
    tables = pack.load_pack(folder)
    dictionary = pack.build_dictionary(tables, state)
    (folder / "data_dictionary.json").write_text(pack._json_text(dictionary), encoding="utf-8")
    files = [
        f
        for f in old["files"]
        if not f["path"].endswith(".csv.gz") and f["path"] != "data_dictionary.json"
    ]
    for name, frame in tables.items():
        path = folder / f"{name}.csv.gz"
        files.append(
            {
                "path": path.name,
                "bytes": path.stat().st_size,
                "sha256": pack.sha256_file(path),
                "rows": len(frame),
            }
        )
    entry = pack._file_entry(folder, "data_dictionary.json")
    files.append(entry.__dict__)
    files.sort(key=lambda f: f["path"])
    import hashlib

    digest = hashlib.sha256()
    for f in files:
        digest.update(f"{f['path']}\t{f['sha256']}\n".encode())
    inputs = [r for r in old.get("inputs", []) if not str(r.get("file", "")).startswith("local/")]
    for sid, path in local.files:
        inputs.append(
            {
                "source": sid,
                "file": path.relative_to(cache).as_posix(),
                "sha256": pack.sha256_file(path),
                "bytes": path.stat().st_size,
                "accessed": None,
            }
        )
    inputs.sort(key=lambda r: (r["source"], r["file"]))
    old.update(
        {
            "pack_hash": digest.hexdigest(),
            "files": files,
            "inputs": inputs,
            "provenance": pack._provenance(inputs),
        }
    )
    manifest_path.write_text(pack._json_text(old), encoding="utf-8")
    return local
