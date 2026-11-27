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
  It saves rebuilding the whole pack when only these sources changed, and is
  safe to re-run after later add-on steps: it changes only the tables it
  writes (``tract_municipalities`` outside Pennsylvania and ``tract_places``
  come from the tract-crosswalk add-on step in :mod:`visionhousing.data.pack`).

Each source is fetched on its own. When one fails (a city service is down)
the others still go in, and the failure is written in the build notes.

Tables (all tier 2, "local refinement only"):

====================== ============================================== ==========
table                  one row per                                    places
====================== ============================================== ==========
municipal_planning     county subdivision (municipality) part         PA
tract_municipalities   tract piece inside one municipality            PA, VT, WY, MA
tract_places           tract piece inside one Census place            VT, WY
parcels_area           tract, and county                              42101, 42003
owners_area            tract, and county                              42101, 42003
owners_deeds_year      year                                           42101
pipeline_tract_year    tract, year and city permit source             42101, 42003
lihtc_awards           development awarded tax credits                PA
law_documents          legal text read or linked                      PA
state_law_provisions   state code section or bill bearing on a reform PA
county_land_use        county: county zoning reach, county plan       PA
muni_zoning_rules      municipality part: ADU, lot, parking rules     PA
mbta_3a_districts      MBTA Communities (3A) zoning district          MA
mbta_3a_status         MBTA community: 3A category, deadline, status  MA
ma_adu_survey          town and survey period: ADU applications,      MA
                       approvals and completions
====================== ============================================== ==========

The four law tables are built by :mod:`visionhousing.law.tables`; the three
Massachusetts tables by :mod:`visionhousing.features.ma_intake` from the files
:mod:`visionhousing.data.sources.ma_intake` reads.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.sources import alle_assess, local_permits, munstats, phfa, phl_opa
from visionhousing.data.sources import ma_intake as ma_src
from visionhousing.features import ma_intake as ma_fx
from visionhousing.features import owners as owners_fx
from visionhousing.features import parcels as parcels_fx
from visionhousing.features import pipeline_local as pipeline_fx
from visionhousing.features import zoning_status as zoning_fx
from visionhousing.law import tables as law_tables

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
STATES = ("PA", "MA")
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
    "tract_places": {
        "key": ["tract", "place"],
        "sources": ["census_baf_2020_place", "tiger_2020_tabblock"],
        "description": (
            "2020 housing units of each tract, split by the Census place (city, town or CDP) of "
            "its blocks; 99999 = outside every place. For cities that are not county "
            "subdivisions (Wyoming)."
        ),
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
TABLES.update(law_tables.TABLES)
TABLES.update(ma_fx.TABLES)
"""Every local table: key, source ids, description and tier (law tables included:
see :mod:`visionhousing.law.tables`; Massachusetts tables:
:mod:`visionhousing.features.ma_intake`)."""

_COMMON: dict[str, tuple[str, str]] = {
    "county": ("code", "County code (5 digits)"),
    "cousub": ("code", "Census county subdivision code (5 digits, within the county)"),
    "place": (
        "code",
        "Census place code (5 digits, within the state; 99999 = not in any place)",
    ),
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
    if table in law_tables.TABLES:
        return law_tables.describe(name)
    if table in ma_fx.TABLES:
        return ma_fx.describe(table, name)
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
    if state.upper() == "MA":
        _massachusetts(out, cache, shapes, client=client, manifest=manifest, refresh=refresh)
        _cached_files(out, cache, state)
        return out

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

    def law() -> None:
        built = law_tables.build_law_tables(
            cache,
            out.tables["municipal_planning"],
            client=client,
            manifest=manifest,
            refresh=refresh,
        )
        out.tables.update(built.tables)
        notes.extend(built.notes)

    _try(notes, "zoning status (DCED Munstats)", zoning)
    if "municipal_planning" in out.tables:
        _try(notes, "law, zoning and policy tables", law)
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
    _cached_files(out, cache, state)
    return out


def _cached_files(out: LocalTables, cache: Path, state: str) -> None:
    """List the cached local files a build read, with their source ids, in ``out.files``.

    Census files count only for their own state (``..._{fips}...``); the ``ma``
    folder counts only for Massachusetts, and for Massachusetts only the census
    and ``ma`` folders count.
    """
    from visionhousing.data.sources import state_fips

    local_dir = Path(cache) / "local"
    if not local_dir.is_dir():
        return
    code = state.upper()
    fips = state_fips(code)
    for path in sorted(p for p in local_dir.rglob("*") if p.is_file()):
        folder = path.parent.name
        if folder == "census" and f"_{fips}." not in path.name:
            continue
        keep = folder in ("ma", "census") if code == "MA" else folder != "ma"
        if not keep:
            continue
        sid = _source_of(path)
        if sid:
            out.files.append((sid, path))


def _massachusetts(
    out: LocalTables,
    cache: Path,
    shapes: list[tuple[str, Mapping[str, Any]] | Any],
    *,
    client: HttpClient,
    manifest: Path | None,
    refresh: bool,
) -> None:
    """The three Massachusetts intake tables; each source is tried on its own.

    A list that cannot be read leaves its table (or columns) out and says why in
    the notes; the other tables still go in.
    """
    notes = out.notes
    settings = ma_src.load_settings()
    overrides = dict(settings.get("name_overrides") or {})
    pairs = [
        (str(s.geoid), s.geometry) if hasattr(s, "geoid") else (str(s[0]), s[1]) for s in shapes
    ]
    subdivisions = _try(
        notes,
        "Massachusetts tables (Census county subdivisions)",
        lambda: munstats.fetch_subdivision_population(
            "MA", cache, client=client, manifest=manifest
        ),
    )
    if subdivisions is None:
        return
    lookup = ma_fx.muni_lookup(subdivisions)
    kw = {"client": client, "manifest": manifest, "refresh": refresh}
    copies: dict[str, ma_src.Copy] = {}

    def copy(name: str) -> bytes | None:
        try:
            got = ma_src.fetch_copy(name, cache, settings, **kw)
        except Exception as err:
            notes.append(
                f"Massachusetts {name}: not added ({type(err).__name__}: {str(err)[:240]})"
            )
            return None
        copies[name] = got
        if got.route != "publisher":
            if got.as_of:
                where = {"sidecar": "its edition note", "file": "the file"}.get(
                    got.edition_from, got.edition_from
                )
                notes.append(
                    f"Massachusetts {name}: read the {got.route} copy "
                    f"(as of {got.as_of}, from {where})"
                )
            else:
                notes.append(
                    f"Massachusetts {name}: read the {got.route} copy (edition unknown; "
                    f"write 'as_of: YYYY-MM-DD' in {got.path.name}{ma_src.EDITION_SUFFIX} "
                    "next to it)"
                )
        return got.path.read_bytes()

    def districts() -> None:
        collection = ma_src.fetch_3a_districts(cache, settings, **kw)
        path = ma_src.cache_dir(cache) / "mbta3a_districts.geojson.gz"
        read_on = date.fromtimestamp(path.stat().st_mtime).isoformat() if path.exists() else ""
        table, missing = ma_fx.districts_table(
            ma_src.parse_3a_districts(collection),
            lookup,
            pairs,
            read_on=read_on,
            overrides=overrides,
        )
        out.tables["mbta_3a_districts"] = table
        if missing:
            notes.append("3A districts: no county for " + ", ".join(missing))

    _try(notes, "Massachusetts 3A district atlas", districts)
    raw_status = copy("compliance_status")
    raw_cat = copy("categories")
    raw_trk = copy("development_tracker")

    def status() -> None:
        atlas = out.tables.get("mbta_3a_districts")
        first = "" if atlas is None or not len(atlas) else str(atlas["atlas_read_on"].iloc[0])
        sheet = copies.get("compliance_status")
        dates = {
            "status_as_of": sheet.as_of if sheet else first,
            "status_route": sheet.route if sheet else "atlas",
            "categories_as_of": copies["categories"].as_of if raw_cat else "",
            "categories_route": copies["categories"].route if raw_cat else "",
            "tracker_as_of": copies["development_tracker"].as_of if raw_trk else "",
            "tracker_route": copies["development_tracker"].route if raw_trk else "",
        }
        table, more = ma_fx.status_table(
            ma_src.parse_compliance_status(raw_status) if raw_status else None,
            ma_src.parse_categories(raw_cat) if raw_cat else None,
            ma_src.parse_development_tracker(raw_trk) if raw_trk else None,
            atlas,
            lookup,
            dates=dates,
            overrides=overrides,
        )
        out.tables["mbta_3a_status"] = table
        notes.extend(more)

    _try(notes, "Massachusetts 3A status", status)
    raw_survey = copy("adu_survey")
    if raw_survey is not None:

        def survey() -> None:
            got = copies["adu_survey"]
            table, missing = ma_fx.adu_survey_table(
                ma_src.parse_adu_survey(raw_survey, settings),
                lookup,
                as_of=got.as_of,
                route=got.route,
                overrides=overrides,
            )
            out.tables["ma_adu_survey"] = table
            if missing:
                notes.append("ADU survey: no county for " + ", ".join(missing))
            odd = ma_src.survey_totals_check(raw_survey, settings)
            if odd:
                notes.append("ADU survey: totals differ from the periods for " + ", ".join(odd))

        _try(notes, "Massachusetts ADU survey", survey)


_MA_FILES = {
    "mbta3a_districts.geojson.gz": "massgis_mbta3a_atlas",
    "mbta3a_compliance_status.csv": "ma_3a_compliance_status",
    "mbta3a_categories.csv": "ma_3a_categories",
    "mbta3a_development_tracker.csv": "ma_3a_development_tracker",
    "ma_adu_survey.csv": "ma_adu_survey_file",
}
"""Massachusetts intake files in ``data/raw/local/ma`` and their source ids."""


def _source_of(path: Path) -> str | None:
    """The source id of a cached local file, from its folder and name."""
    folder, name = path.parent.name, path.name
    if folder == "ma":
        return _MA_FILES.get(name)
    if folder == "munstats":
        return "pa_dced_munstats"
    if folder == "law":
        return "pa_mpc" if name.endswith(".htm") else None
    if folder == "bills" and path.parent.parent.name == "law":
        return path.stem
    if folder == "census":
        if name.startswith("block_place"):
            return "census_baf_2020_place"
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
    tables: Iterable[str] | None = None,
    lock: Callable[[], Any] | None = None,
) -> LocalTables:
    """Add the local tables to an already built pack and rewrite its dictionary and manifest.

    Safe to re-run on a pack that later add-on steps have extended: it
    changes only the local tables it writes, and leaves every other table,
    every other file-list entry and every other step's inputs as they were.

    - Tract outlines are read the way the full build reads them (the cached
      full-detail file, else the pack's simplified boundaries), so a 3A
      district keeps the centre tract the build gave it.
    - A table whose new content equals the file in the pack is not rewritten.
    - Only the inputs of the tables written are replaced in the manifest
      (matched by source id); inputs other steps recorded (for example the
      3A capacity step's parcels, station radii and sewer files) are kept.
    - When nothing changed, no file is touched.

    Args:
        folder: The pack folder (``data/packs/{STATE}``), rewritten in place.
        cache: The raw-file cache (``data/raw``).
        client: HTTP client for a missing cache file.
        manifest: The download manifest to record any fetch in, or ``None``.
        refresh: Download again even when the cache has the files.
        tables: Write only these local tables (for example ``["ma_adu_survey"]``);
            ``None`` = every local table the state has.
        lock: Optional context-manager factory held only while files are written.

    Returns:
        What :func:`fetch_local_tables` read; ``tables`` keeps only the tables
        that were written (changed or new).
    """
    import contextlib

    from visionhousing.data import pack

    folder, cache = Path(folder), Path(cache)
    state = folder.name.upper()
    old = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    fips = str(old.get("state_fips") or pack.to_state_fips(state))
    blocks = pd.read_csv(
        folder / "blocks.csv.gz",
        dtype={"geoid": str, "tract": str},
        usecols=["geoid", "tract", "housing_units"],
    )
    local = fetch_local_tables(
        state,
        cache,
        blocks=blocks,
        tract_shapes=pack._tract_shapes(folder, cache, fips),
        client=client,
        manifest=manifest,
        refresh=refresh,
    )
    wanted = None if tables is None else set(tables)
    written = {
        name: frame
        for name, frame in local.tables.items()
        if (wanted is None or name in wanted) and _changed(folder / f"{name}.csv.gz", frame)
    }
    local.tables = written
    if not written:
        return local
    owned = {str(s) for name in written for s in TABLES.get(name, {}).get("sources", [])}
    inputs = _changed_inputs([(sid, path) for sid, path in local.files if sid in owned], old, cache)
    with lock() if lock is not None else contextlib.nullcontext():
        pack._rewrite(folder, cache, written, inputs)
    return local


def _changed(path: Path, frame: pd.DataFrame) -> bool:
    """True when ``frame``, written as a pack writes it, differs from ``path`` (or is new)."""
    import gzip

    from visionhousing.data import pack

    if not path.is_file():
        return True
    text = pack._tidy_numbers(frame.reset_index(drop=True)).to_csv(index=False, lineterminator="\n")
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return fh.read() != text


def _changed_inputs(
    files: list[tuple[str, Path]], old: Mapping[str, Any], cache: Path
) -> list[tuple[str, Path]]:
    """The input files of every source whose recorded files differ from ``files``.

    A source whose files and checksums are already in the manifest is left out,
    so its records (and their read dates) stay exactly as they are.
    """
    from visionhousing.data import pack

    recorded: dict[str, set[tuple[str, str]]] = {}
    for r in old.get("inputs", []):
        recorded.setdefault(str(r.get("source")), set()).add(
            (str(r.get("file")), str(r.get("sha256")))
        )
    now: dict[str, set[tuple[str, str]]] = {}
    for sid, path in files:
        if path.exists():
            rel = path.relative_to(cache).as_posix()
            now.setdefault(sid, set()).add((rel, pack.sha256_file(path)))
    return [(sid, path) for sid, path in files if now.get(sid) != recorded.get(sid)]
