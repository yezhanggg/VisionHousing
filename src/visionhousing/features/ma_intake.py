"""Massachusetts pack tables: 3A districts, 3A status by community, ADU survey by town.

Plain-language summary
----------------------
Turns what :mod:`visionhousing.data.sources.ma_intake` read into three small
pack tables (tier 2, Massachusetts only). Every function here is pure: no
file or network access.

- ``mbta_3a_districts``: one row per zoning district in the MBTA Communities
  (3A) District Atlas, with its county (from its town), the tract that holds
  its centre, its unit capacity, density, parking and height rules, the date
  the town submitted it, and its simplified outline as GeoJSON text.
- ``mbta_3a_status``: one row per MBTA community (177): category, deadline,
  compliance status and the capacity the law asks of it, next to what the
  atlas holds for it (districts, capacity, acres) and the housing HLC links to
  its 3A zoning (development tracker). The date of each list is in the row.
- ``ma_adu_survey``: one row per town and survey period: ADU applications,
  approvals (building permits issued) and certificates of occupancy, split
  detached / attached. The ``cohort`` column says which period a row covers,
  because towns answered different survey waves. A town with no answer for a
  period has no row: unknown is never written as zero.

:func:`survey_summary` and :func:`survey_context` turn that table into the
observed context the profile, the data report card and the plausibility card
show: per survey period, the towns that answered (of the state's
municipalities, ``adu_survey.municipalities`` in ``assumptions/ma_intake.yaml``),
applications, approvals and certificates of occupancy, detached and attached.
Every survey period is kept (the 2025 waves and the 2026 mid-year wave). It is
**evidence only**: never divided into a rate, never an input to a run. Without
the hand-saved file the context says "not supplied".

Counties come from the Census Bureau's list of county subdivisions (every
Massachusetts town lies in one county). A town name that cannot be matched is
kept with an empty county and named in the build notes.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.data.sources.ma_intake import muni_key
from visionhousing.features import ma_capacity as _capacity_fx

__all__ = [
    "STATUS_COLUMNS",
    "TABLES",
    "adu_survey_table",
    "atlas_status",
    "describe",
    "district_centres",
    "district_tract_links",
    "districts_table",
    "muni_lookup",
    "read_survey",
    "status_table",
    "survey_context",
    "survey_summary",
]

TABLES: dict[str, dict[str, object]] = {
    "mbta_3a_districts": {
        "key": ["district_id"],
        "sources": ["massgis_mbta3a_atlas", "census_subcounty_pop", "cb_2020_tract"],
        "description": (
            "One row per MBTA Communities (Section 3A) zoning district that HLC found "
            "compliant (MassGIS 3A District Atlas): town, county, centre tract, unit "
            "capacity, acres, density, parking and height rules, submission date and a "
            "simplified outline. Massachusetts only."
        ),
        "tier": "2",
    },
    "mbta_3a_district_tracts": {
        "key": ["district_id", "tract"],
        "sources": ["massgis_mbta3a_atlas", "cb_2020_tract"],
        "description": (
            "One row per Section 3A district and 2020 tract its simplified outline reaches: "
            "overlap acres and the share of the district in the tract (cells of a flat grid; "
            "settings in assumptions/ma_intake.yaml district_tracts). Massachusetts only."
        ),
        "tier": "2",
    },
    "mbta_3a_status": {
        "key": ["municipality"],
        "sources": [
            "ma_3a_compliance_status",
            "ma_3a_categories",
            "ma_3a_development_tracker",
            "massgis_mbta3a_atlas",
            "census_subcounty_pop",
        ],
        "description": (
            "One row per MBTA community (177): category, compliance deadline and status, "
            "required unit capacity and land, what the 3A atlas holds for it, and housing "
            "HLC links to its 3A zoning. Each list's date is in the row. Massachusetts only."
        ),
        "tier": "2",
    },
    "ma_adu_survey": {
        "key": ["muni_id", "cohort"],
        "sources": ["ma_adu_survey_file", "census_subcounty_pop"],
        "description": (
            "One row per town and ADU survey period (HLC survey of building officials): "
            "applications, approvals (building permits issued) and certificates of "
            "occupancy, detached and attached. Towns answered different waves, so the "
            "cohort column says which period a row covers. Massachusetts only."
        ),
        "tier": "2",
    },
}
"""The Massachusetts intake tables: key, source ids, description, tier."""

TABLES.update(_capacity_fx.TABLES)
"""Plus the existing 3A modeled capacity and MassDEP estimated-sewer tables (add-on step;
see :mod:`visionhousing.features.ma_capacity`)."""

_COLUMNS: dict[str, tuple[str, str]] = {
    "district_id": ("code", "Atlas feature id (OBJECTID) of the district"),
    "municipality": ("text", "Town or city, as the source writes it"),
    "muni_id": ("code", "MassGIS town id (1-351)"),
    "county": ("code", "County code (5 digits), from the town; empty when not matched"),
    "cousub": ("code", "Census county subdivision code of the town (5 digits)"),
    "tract": ("code", "2020 tract holding the district's centre; empty when outside all"),
    "lat": ("degrees", "Latitude of the centre of the district's outline"),
    "lon": ("degrees", "Longitude of the centre of the district's outline"),
    "zone_code": ("code", "Atlas zone code (town id and district abbreviation)"),
    "zone_name": ("text", "District name as the town submitted it"),
    "zone_abbr": ("text", "District abbreviation as the town submitted it"),
    "overlay": ("flag", "1 when the district is an overlay on base zoning, 0 when base zoning"),
    "status": ("category", "Compliance status in the atlas (only compliant districts are in it)"),
    "submitted_on": ("date", "Date on the town's submission; empty when the atlas has none"),
    "capacity_district": (
        "units",
        "Multi-family unit capacity the town reported for the district (atlas MFUC_DIST)",
    ),
    "capacity_cleaned": (
        "units",
        "Cleaned gross unit capacity (atlas MFUC_CLN); 0 often means not filled in",
    ),
    "capacity_parcels": (
        "units",
        "Capacity summed from the district's parcels (atlas MFUC_PARC); 0 often means not filled",
    ),
    "capacity_station_area": (
        "units",
        "Compliance-model unit capacity inside the half-mile station area (atlas DUC_HMB)",
    ),
    "acres": ("acres", "District area in the compliance model (atlas ACRS_CM)"),
    "density_denominator_acres": (
        "acres",
        "Acres counted for gross density after excluded land (atlas DNDN_CM)",
    ),
    "units_per_acre": ("units per acre", "Gross density of the district (atlas DUAC_CM)"),
    "parking_per_unit": ("spaces per unit", "Parking spaces required per home (atlas PRKNPDU)"),
    "max_stories": ("storeys", "Maximum storeys allowed (atlas STORIES; 0 = not limited)"),
    "max_lot_coverage_pct": ("share", "Maximum lot coverage (atlas LOTCOV; 0 = not limited)"),
    "max_far": ("ratio", "Maximum floor area ratio (atlas FAR; 0 = not limited)"),
    "lot_area_per_unit_sqft": (
        "square feet",
        "Lot area required per home (atlas AREAPDU; 0 = not limited)",
    ),
    "max_units_per_acre": ("units per acre", "Maximum homes per acre (atlas MXDUPAC)"),
    "district_unit_cap": ("units", "Cap on homes in the district (atlas DU_CAP; 0 = none)"),
    "open_space_share": ("share", "Required open space (atlas OSRQ_PC)"),
    "notes": ("text", "Atlas notes (for example a capacity that differs from HLC's letter)"),
    "geometry": (
        "GeoJSON",
        "District outline, simplified by the map service (about 20 m) - not a legal boundary",
    ),
    "atlas_read_on": ("date", "Date the atlas was read"),
    "category": (
        "category",
        "MBTA community category: Rapid Transit, Commuter Rail, Adjacent community or "
        "Adjacent small town",
    ),
    "deadline": ("date", "Deadline to submit a district compliance application"),
    "compliance_status": (
        "category",
        "HLC compliance status: Compliant, Conditional Compliance, Interim Compliance or "
        "Noncompliant",
    ),
    "compliance_details": ("text", "HLC's detail (for example an application in review)"),
    "pre_adoption_review": ("flag", "1 when the town submitted zoning for pre-adoption review"),
    "adopted_zoning": ("flag", "1 when the town adopted zoning meant to comply with 3A"),
    "housing_units_2020": ("units", "2020 Census housing units HLC used for the requirement"),
    "min_unit_capacity": (
        "units",
        "Minimum multi-family unit capacity the regulations ask of the town",
    ),
    "capacity_share": ("share", "That capacity as a share of 2020 housing units"),
    "min_land_acres": ("acres", "Minimum district land area"),
    "station_area_acres": (
        "acres",
        "Developable land within half a mile of a station; empty when the town has none",
    ),
    "station_area_share": ("share", "Share of the district that must lie in the station area"),
    "atlas_districts": ("districts", "Districts the atlas holds for the town"),
    "atlas_capacity": ("units", "Sum of the reported capacity of those districts"),
    "atlas_acres": ("acres", "Sum of the acres of those districts"),
    "atlas_first_submitted": ("date", "Earliest submission date among those districts"),
    "tracker_projects": ("projects", "Developments HLC links to the town's 3A zoning"),
    "tracker_units": ("units", "Homes in those developments (total, as listed)"),
    "tracker_net_units": ("units", "Net new homes in those developments, as listed"),
    "tracker_deed_restricted": ("units", "Deed-restricted (affordable) homes among them"),
    "status_source": (
        "category",
        "Where the status comes from: status sheet (HLC's list of all MBTA communities) or atlas "
        "(the 3A atlas alone: compliant towns only)",
    ),
    "status_as_of": (
        "date",
        "Edition date of the compliance status sheet read (or date of the atlas read); "
        "empty when a hand-saved copy did not say its edition",
    ),
    "categories_as_of": ("date", "Date of the community categories file read"),
    "tracker_as_of": ("date", "Date of the development tracker read"),
    "status_route": (
        "category",
        "How the status list was read: publisher, hand_saved, or atlas (map service)",
    ),
    "categories_route": (
        "category",
        "How the community categories file was read: publisher or hand_saved (empty: not read)",
    ),
    "tracker_route": (
        "category",
        "How the development tracker was read: publisher or hand_saved (empty: not read)",
    ),
    "cohort": (
        "category",
        "Survey period of the row: 2025 full year, 2025 first half or 2026 first half",
    ),
    "period_start": ("date", "First day the row's counts cover"),
    "period_end": ("date", "Last day the row's counts cover"),
    "answer": ("category", "The survey answer the counts come from (End-of-year or Mid-year)"),
    "applications": ("applications", "ADU building-permit applications submitted"),
    "applications_detached": ("applications", "Of those, for detached ADUs"),
    "applications_attached": ("applications", "Of those, for attached or interior ADUs"),
    "approvals": ("units", "ADUs approved: building permits issued"),
    "approvals_detached": ("units", "Of those, detached ADUs"),
    "approvals_attached": ("units", "Of those, attached or interior ADUs"),
    "certificates_of_occupancy": ("units", "ADUs given a certificate of occupancy (finished)"),
    "certificates_of_occupancy_detached": ("units", "Of those, detached ADUs"),
    "certificates_of_occupancy_attached": ("units", "Of those, attached or interior ADUs"),
    "survey_as_of": (
        "date",
        "Edition date of the ADU survey file read; empty when a hand-saved copy did not "
        "say its edition",
    ),
    "survey_route": ("category", "How the survey file was read: publisher or hand_saved"),
}


_LINK_COLUMNS: dict[str, tuple[str, str]] = {
    "district_id": ("code", "Atlas feature id (OBJECTID) of the district (mbta_3a_districts)"),
    "tract": ("code", "2020 tract the district's outline reaches"),
    "overlap_acres": (
        "acres",
        "Area of the district's simplified outline inside the tract (grid cells; not legal)",
    ),
    "district_share": ("share", "That area as a share of the district's outline"),
    "centre": ("flag", "1 when the tract holds the district's centre (mbta_3a_districts.tract)"),
}
"""Columns of ``mbta_3a_district_tracts`` (their meaning differs from the district table's)."""


def describe(table: str, name: str) -> tuple[str, str]:
    """Unit and plain description of a column of a Massachusetts table."""
    if table in _capacity_fx.TABLES:
        return _capacity_fx.describe(table, name)
    if table == "mbta_3a_district_tracts" and name in _LINK_COLUMNS:
        return _LINK_COLUMNS[name]
    if table in TABLES and name in _COLUMNS:
        return _COLUMNS[name]
    raise ValueError(f"no data-dictionary entry for {table}.{name}")


# --------------------------------------------------------------------------- towns


def muni_lookup(
    subdivisions: pd.DataFrame, overrides: Mapping[str, str] | None = None
) -> pd.DataFrame:
    """Census county subdivisions keyed for joining: ``key``, ``county``, ``cousub``.

    ``subdivisions`` is :func:`visionhousing.data.sources.munstats.parse_subdivision_population`
    for Massachusetts. Rows that are not towns (code 00000) are dropped; a key
    that occurs twice keeps the more populous row.
    """
    frame = subdivisions[subdivisions["cousub"].astype(str) != "00000"].copy()
    frame["key"] = frame["name"].map(lambda n: muni_key(n))
    frame = frame.sort_values("population", ascending=False, na_position="last")
    frame = frame.drop_duplicates("key")
    return frame[["key", "county", "cousub"]].reset_index(drop=True)


def _attach_county(
    frame: pd.DataFrame, lookup: pd.DataFrame, overrides: Mapping[str, str] | None
) -> tuple[pd.DataFrame, list[str]]:
    keys = frame["municipality"].map(lambda n: muni_key(n, dict(overrides or {})))
    out = frame.assign(_key=keys).merge(
        lookup.rename(columns={"key": "_key"}), on="_key", how="left", validate="m:1"
    )
    missing = sorted(set(out.loc[out["county"].isna(), "municipality"].astype(str)))
    out["county"] = out["county"].fillna("")
    out["cousub"] = out["cousub"].fillna("")
    return out.drop(columns="_key"), missing


# --------------------------------------------------------------------------- districts


def _ring_centre(ring: np.ndarray) -> tuple[float, float, float]:
    """Signed area and centroid (lon, lat) of one closed ring (planar, in degrees)."""
    x, y = ring[:, 0], ring[:, 1]
    cross = x[:-1] * y[1:] - x[1:] * y[:-1]
    area = cross.sum() / 2.0
    if abs(area) < 1e-15:
        return 0.0, float(x.mean()), float(y.mean())
    cx = ((x[:-1] + x[1:]) * cross).sum() / (6.0 * area)
    cy = ((y[:-1] + y[1:]) * cross).sum() / (6.0 * area)
    return float(area), float(cx), float(cy)


def district_centres(geometries: Iterable[str]) -> tuple[np.ndarray, np.ndarray]:
    """Latitude and longitude of the centre of each outline (largest part's centroid).

    Outlines are GeoJSON text; an empty outline gives NaN.
    """
    lats: list[float] = []
    lons: list[float] = []
    for text in geometries:
        geometry = json.loads(text) if text else {}
        kind, coords = geometry.get("type"), geometry.get("coordinates", [])
        parts = [coords] if kind == "Polygon" else coords if kind == "MultiPolygon" else []
        best = (0.0, float("nan"), float("nan"))
        for poly in parts:
            if not poly or len(poly[0]) < 4:
                continue
            area, cx, cy = _ring_centre(np.asarray(poly[0], dtype=float))
            if abs(area) >= abs(best[0]) or np.isnan(best[1]):
                best = (area, cx, cy)
        lons.append(round(best[1], 5) if not np.isnan(best[1]) else float("nan"))
        lats.append(round(best[2], 5) if not np.isnan(best[2]) else float("nan"))
    return np.asarray(lats, float), np.asarray(lons, float)


def districts_table(
    districts: pd.DataFrame,
    lookup: pd.DataFrame,
    tract_shapes: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    read_on: str,
    overrides: Mapping[str, str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """The ``mbta_3a_districts`` table and the town names that found no county.

    Args:
        districts: :func:`visionhousing.data.sources.ma_intake.parse_3a_districts`.
        lookup: :func:`muni_lookup`.
        tract_shapes: ``(tract code, GeoJSON geometry)`` pairs of the pack's tracts.
        read_on: Date the atlas was read.
        overrides: Name overrides from ``assumptions/ma_intake.yaml``.
    """
    from visionhousing.features.parcels import place_in_tracts

    frame = districts.rename(columns={"objectid": "district_id"}).copy()
    frame, missing = _attach_county(frame, lookup, overrides)
    lat, lon = district_centres(frame["geometry"].astype(str))
    frame["lat"], frame["lon"] = lat, lon
    ok = ~np.isnan(lat)
    tracts = np.full(len(frame), "", dtype=object)
    if ok.any():
        tracts[ok] = place_in_tracts(lat[ok], lon[ok], list(tract_shapes))
    frame["tract"] = tracts
    frame["atlas_read_on"] = read_on
    first = ["district_id", "municipality", "muni_id", "county", "cousub", "tract", "lat", "lon"]
    rest = [c for c in frame.columns if c not in first and c != "geometry"]
    frame = frame[[*first, *rest, "geometry"]]
    return frame.sort_values("district_id").reset_index(drop=True), missing


LINK_COLUMNS = ("district_id", "tract", "overlap_acres", "district_share", "centre")
"""Columns of ``mbta_3a_district_tracts``, in order."""

_M_PER_DEG_LAT = 110_574.0
_M_PER_DEG_LON = 111_320.0
_M2_PER_ACRE = 4_046.8564224


def _lonlat_bounds(geometry: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    from visionhousing.features.planar import geojson_polygons, rings_bounds

    rings = [r for poly in geojson_polygons(geometry) for r in poly]
    return rings_bounds(rings) if rings else None


def _link(district: object, tract: str, acres: float, share: float, centre: int) -> dict:
    return {"district_id": district, "tract": tract, "overlap_acres": acres,
            "district_share": share, "centre": centre}  # fmt: skip


def district_tract_links(
    districts: pd.DataFrame,
    tract_shapes: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    cell_m: float,
    min_share: float,
) -> pd.DataFrame:
    """Every 2020 tract each district's outline reaches: the ``mbta_3a_district_tracts`` table.

    Method (flat-map helpers in :mod:`visionhousing.features.planar`, no
    geometry library): each district and the tracts whose bounding box meets
    its own are projected onto a flat map in metres around the district (one
    degree of longitude shrinks by the cosine of the latitude; over a few
    kilometres the error is far below the outline's 20 m simplification). A
    grid of ``cell_m`` cells covers the district; a cell belongs to a shape
    when its centre is inside it. The overlap of a district and a tract is
    the count of cells inside both, times the cell area.

    A tract is kept when it holds at least ``min_share`` of the district: a
    thinner sliver is most likely the simplified outline crossing a tract line
    it follows in law. The centre tract (``tract`` of ``mbta_3a_districts``)
    is always kept, so a district too small for one cell still has its row
    (share 1, acres from the outline's own area).

    Args:
        districts: The ``mbta_3a_districts`` table (``district_id``, ``tract``,
            ``geometry`` as GeoJSON text).
        tract_shapes: ``(tract code, GeoJSON geometry)`` pairs of the pack's tracts.
        cell_m: Grid cell size in metres.
        min_share: Smallest share of a district a non-centre tract must hold.
    """
    from visionhousing.features.planar import (
        fill_rings,
        geojson_polygons,
        grid_for_bounds,
        polygon_area,
        rings_bounds,
    )

    shapes = [(str(g), geom, _lonlat_bounds(geom)) for g, geom in tract_shapes]
    shapes = [s for s in shapes if s[2] is not None]
    rows: list[dict[str, object]] = []
    for _, d in districts.iterrows():
        text = d.get("geometry")
        geometry = json.loads(text) if isinstance(text, str) and text else {}
        box = _lonlat_bounds(geometry)
        centre = "" if pd.isna(d.get("tract")) else str(d.get("tract") or "")
        if box is None:
            if centre:
                rows.append(_link(d["district_id"], centre, float("nan"), 1.0, 1))
            continue
        lon0, lat0 = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        kx = _M_PER_DEG_LON * math.cos(math.radians(lat0))

        def move(x: np.ndarray, y: np.ndarray, lon0=lon0, lat0=lat0, kx=kx):
            return (x - lon0) * kx, (y - lat0) * _M_PER_DEG_LAT

        polys = geojson_polygons(geometry, move)
        rings = [r for poly in polys for r in poly]
        grid = grid_for_bounds(*rings_bounds(rings), cell_m)
        inside = fill_rings(grid, rings)
        total = int(inside.sum())
        found: dict[str, int] = {}
        if total:
            for geoid, geom, tb in shapes:
                if tb[0] > box[2] or tb[2] < box[0] or tb[1] > box[3] or tb[3] < box[1]:
                    continue
                t_rings = [r for poly in geojson_polygons(geom, move) for r in poly]
                n = int((inside & fill_rings(grid, t_rings)).sum())
                if n:
                    found[geoid] = found.get(geoid, 0) + n
        cell_acres = grid.cell_area / _M2_PER_ACRE
        for geoid, n in sorted(found.items()):
            share = n / total
            if share >= min_share or geoid == centre:
                acres = round(n * cell_acres, 2)
                rows.append(
                    _link(d["district_id"], geoid, acres, round(share, 4), int(geoid == centre))
                )
        if centre and centre not in found:
            # no cell of the district lies in its centre tract (or the district is
            # smaller than a cell): keep the row; its acres are the outline's own area
            whole = round(polygon_area(polys) / _M2_PER_ACRE, 2)
            rows.append(_link(d["district_id"], centre, 0.0 if total else whole,
                              0.0 if total else 1.0, 1))  # fmt: skip
    out = pd.DataFrame(rows, columns=list(LINK_COLUMNS))
    out["centre"] = out["centre"].astype("int64")
    return out.sort_values(["district_id", "tract"]).reset_index(drop=True)


# --------------------------------------------------------------------------- status


STATUS_COLUMNS = (
    "municipality",
    "compliance_status",
    "compliance_details",
    "deadline",
    "pre_adoption_review",
    "adopted_zoning",
)
"""The columns of a parsed compliance status sheet."""


_DATE_KEYS = (
    "status_as_of",
    "categories_as_of",
    "tracker_as_of",
    "status_route",
    "categories_route",
    "tracker_route",
)
"""Edition and route columns written into every ``mbta_3a_status`` row."""


def _sum_known(series: pd.Series) -> float:
    """Sum of the filled values; missing (not 0) when every value is blank."""
    return series.sum(min_count=1)


def atlas_status(districts: pd.DataFrame) -> pd.DataFrame:
    """A stand-in status list from the 3A atlas alone: its towns, all "Compliant".

    Used when HLC's status sheet could not be read. Only towns HLC found
    compliant are in the atlas, so the other MBTA communities are missing
    (not "noncompliant"), and deadline and review columns are empty.
    """
    names = sorted(set(districts["municipality"].astype(str)) - {""})
    out = pd.DataFrame({"municipality": names})
    out["compliance_status"] = "Compliant"
    out["compliance_details"] = ""
    out["deadline"] = pd.NA
    out["pre_adoption_review"] = pd.array([pd.NA] * len(out), dtype="Int64")
    out["adopted_zoning"] = pd.array([1] * len(out), dtype="Int64")
    return out[list(STATUS_COLUMNS)]


def status_table(
    status: pd.DataFrame | None,
    categories: pd.DataFrame | None,
    tracker: pd.DataFrame | None,
    districts: pd.DataFrame | None,
    lookup: pd.DataFrame,
    *,
    dates: Mapping[str, str],
    overrides: Mapping[str, str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """The ``mbta_3a_status`` table and any names that did not join.

    Args:
        status: :func:`~visionhousing.data.sources.ma_intake.parse_compliance_status`,
            or None to build the list from the atlas alone (:func:`atlas_status`).
        categories: :func:`~visionhousing.data.sources.ma_intake.parse_categories`, or None.
        tracker: :func:`~visionhousing.data.sources.ma_intake.parse_development_tracker`,
            or None.
        districts: The ``mbta_3a_districts`` table, or None.
        lookup: :func:`muni_lookup`.
        dates: ``status_as_of``, ``categories_as_of``, ``tracker_as_of`` and
            ``status_route``, ``categories_route``, ``tracker_route`` (written
            into every row; empty when a list is missing or, for a hand-saved
            copy, when its edition is unknown).
        overrides: Name overrides from ``assumptions/ma_intake.yaml``.

    Returns:
        The table (one row per community in the status sheet) and notes on
        names in one list but not the other.
    """
    ov = dict(overrides or {})
    notes: list[str] = []
    if status is None:
        if districts is None or not len(districts):
            raise ValueError("3A status: neither the status sheet nor the atlas could be read")
        out = atlas_status(districts)
        out["status_source"] = "atlas"
        notes.append(
            "3A status: built from the atlas alone (compliant towns only; no categories, "
            "deadlines or towns in interim compliance or noncompliant)"
        )
    else:
        out = status.copy()
        out["status_source"] = "status sheet"
    out["_key"] = out["municipality"].map(lambda n: muni_key(n, ov))

    def join(other: pd.DataFrame | None, label: str, columns: list[str]) -> None:
        nonlocal out
        if other is None:
            for c in columns:
                out[c] = pd.NA
            return
        right = other.assign(_key=other["municipality"].map(lambda n: muni_key(n, ov)))
        extra = sorted(set(right["_key"]) - set(out["_key"]))
        if extra:
            names = right.loc[right["_key"].isin(extra), "municipality"]
            notes.append(f"{label}: not in the status sheet: {', '.join(sorted(names))}")
        out = out.merge(right[["_key", *columns]], on="_key", how="left", validate="1:1")

    join(
        categories,
        "categories",
        [
            "category",
            "housing_units_2020",
            "min_unit_capacity",
            "capacity_share",
            "min_land_acres",
            "station_area_acres",
            "station_area_share",
        ],
    )
    if districts is not None and len(districts):
        atlas = (
            districts.assign(_key=districts["municipality"].map(lambda n: muni_key(n, ov)))
            .assign(_date=districts["submitted_on"].replace("", pd.NA))
            .groupby("_key", as_index=False)
            .agg(
                atlas_districts=("district_id", "size"),
                atlas_capacity=("capacity_district", _sum_known),
                atlas_acres=("acres", _sum_known),
                atlas_first_submitted=("_date", "min"),
            )
            .assign(municipality="")
        )
        atlas["atlas_acres"] = atlas["atlas_acres"].round(1)
        join(
            atlas.assign(municipality=atlas["_key"]).drop(columns="_key"),
            "atlas",
            ["atlas_districts", "atlas_capacity", "atlas_acres", "atlas_first_submitted"],
        )
        out["atlas_districts"] = out["atlas_districts"].fillna(0).astype(int)
    else:
        join(None, "atlas", ["atlas_districts", "atlas_capacity", "atlas_acres"])
        out["atlas_first_submitted"] = pd.NA
    join(
        tracker,
        "development tracker",
        ["tracker_projects", "tracker_units", "tracker_net_units", "tracker_deed_restricted"],
    )
    if tracker is not None:
        # a town the tracker does not list has no linked developments (0); a town
        # it lists with every count blank stays unknown
        listed = set(tracker["municipality"].map(lambda n: muni_key(n, ov)))
        absent = ~out["_key"].isin(listed)
        for c in (
            "tracker_projects",
            "tracker_units",
            "tracker_net_units",
            "tracker_deed_restricted",
        ):
            out.loc[absent, c] = out.loc[absent, c].fillna(0)
    lookup_frame = lookup.rename(columns={"key": "_key"})
    out = out.merge(lookup_frame, on="_key", how="left", validate="m:1")
    unmatched = sorted(set(out.loc[out["county"].isna(), "municipality"]))
    if unmatched:
        notes.append(f"3A status: no Census county subdivision for {', '.join(unmatched)}")
    out["county"] = out["county"].fillna("")
    out["cousub"] = out["cousub"].fillna("")
    for key in _DATE_KEYS:
        out[key] = dates.get(key, "")
    first = ["municipality", "county", "cousub", "category", "deadline", "compliance_status"]
    rest = [c for c in out.columns if c not in first and c != "_key"]
    return out[[*first, *rest]].sort_values("municipality").reset_index(drop=True), notes


# --------------------------------------------------------------------------- ADU survey


def adu_survey_table(
    survey: pd.DataFrame,
    lookup: pd.DataFrame,
    *,
    as_of: str,
    route: str,
    overrides: Mapping[str, str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """The ``ma_adu_survey`` table (survey rows with county) and unmatched town names."""
    frame, missing = _attach_county(survey.copy(), lookup, overrides)
    frame["survey_as_of"] = as_of
    frame["survey_route"] = route
    first = ["muni_id", "municipality", "county", "cousub", "cohort"]
    rest = [c for c in frame.columns if c not in first]
    frame = frame[[*first, *rest]]
    return frame.sort_values(["muni_id", "period_start", "period_end"]).reset_index(
        drop=True
    ), missing


# --------------------------------------------------------------------------- survey context

SURVEY_TABLE = "ma_adu_survey"
"""Pack table name of the ADU survey."""

_SURVEY_SUMS = (
    "applications",
    "approvals",
    "approvals_detached",
    "approvals_attached",
    "certificates_of_occupancy",
)


def survey_summary(
    table: pd.DataFrame | None, *, county: str | None = None, municipalities: int | None = None
) -> list[dict[str, Any]]:
    """Per survey period: towns that answered and their counts (observed, not a rate).

    Args:
        table: The ``ma_adu_survey`` pack table (``None`` or empty gives ``[]``).
        county: Only the towns of this county (5-digit code); ``None`` = the state.
        municipalities: The number of municipalities the shares of towns are
            taken of (the state's; ``None`` leaves the share out).

    Returns:
        One dict per period, in date order: ``cohort``, ``period_start``,
        ``period_end``, ``towns`` (towns with a row for that period),
        ``towns_of`` and ``towns_share`` (when ``municipalities`` is given),
        the sums of ``applications``, ``approvals`` (detached, attached) and
        ``certificates_of_occupancy``, and ``as_of`` (the file's edition).
        A blank count stays out of a sum; a town with no row is not a zero.
    """
    keys = ["cohort", "period_start", "period_end"]
    if table is None or table.empty or not {*keys, "muni_id"} <= set(table.columns):
        return []
    frame = table
    if county is not None and "county" in frame.columns:
        frame = frame[frame["county"].astype(str) == str(county)]
    out: list[dict[str, Any]] = []
    for (cohort, start, end), part in frame.groupby(keys, sort=False):
        row: dict[str, Any] = {
            "cohort": str(cohort),
            "period_start": str(start),
            "period_end": str(end),
            "towns": int(part["muni_id"].nunique()),
        }
        if municipalities:
            row["towns_of"] = int(municipalities)
            row["towns_share"] = row["towns"] / float(municipalities)
        for col in _SURVEY_SUMS:
            if col in part.columns:
                total = pd.to_numeric(part[col], errors="coerce").sum(min_count=1)
                row[col] = 0 if pd.isna(total) else int(total)
        as_of = sorted({str(v) for v in part.get("survey_as_of", pd.Series(dtype=str)).dropna()})
        row["as_of"] = ", ".join(a for a in as_of if a and a != "nan")
        out.append(row)
    return sorted(out, key=lambda r: (r["period_start"], r["period_end"]))


def read_survey(folder: Any) -> pd.DataFrame | None:
    """The pack's ``ma_adu_survey`` table from a pack folder, or ``None`` when absent."""
    from pathlib import Path

    if folder is None:
        return None
    for cand in (Path(folder) / f"{SURVEY_TABLE}.csv.gz", Path(folder) / f"{SURVEY_TABLE}.csv"):
        if cand.is_file():
            return pd.read_csv(cand, dtype={"county": str, "cousub": str, "muni_id": str})
    return None


def survey_context(
    table: pd.DataFrame | None,
    *,
    county: str | None = None,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The ADU survey as observed context, with one plain sentence.

    Returns:
        ``{state: "observed" | "not_supplied", periods: [...], text, note}``.
        ``not_supplied`` when the table is absent (the hand-saved file was not
        supplied); a county with no answering town says so.
    """
    from visionhousing.data.sources.ma_intake import load_settings

    spec = (settings or load_settings())["adu_survey"]
    note = (
        "Observed context from the state's survey of local building officials (applications, "
        "approvals = ADU building permits issued, certificates of occupancy). Towns answered "
        "different survey waves and some did not answer; a town with no answer is unknown, not "
        "zero. Never a rate and never an input to an estimate."
    )
    if table is None:
        return {
            "state": "not_supplied",
            "periods": [],
            "text": (
                "not supplied: the state ADU survey is read only from a hand-saved copy "
                f"(data/raw/local/ma/{spec.get('file', 'ma_adu_survey.csv')}), and none was "
                "supplied"
            ),
            "note": note,
        }
    whole = int(spec.get("municipalities") or 0) or None
    periods = survey_summary(table, county=county, municipalities=None if county else whole)
    if not periods:
        return {"state": "observed", "periods": [], "note": note,
                "text": "no town in this county answered the ADU survey"}  # fmt: skip
    parts = []
    for p in periods:
        towns = f"{p['towns']:,}" + (f" of {p['towns_of']:,}" if "towns_of" in p else "")
        parts.append(
            f"{p['cohort']}: {p.get('approvals', 0):,} ADUs approved "
            f"({p.get('approvals_detached', 0):,} detached, {p.get('approvals_attached', 0):,} "
            f"attached or interior), {p.get('applications', 0):,} applications, "
            f"{p.get('certificates_of_occupancy', 0):,} certificates of occupancy, "
            f"in {towns} town{'s' * (p.get('towns_of', p['towns']) != 1)} that answered"
        )
    as_of = ", ".join(sorted({p["as_of"] for p in periods if p.get("as_of")}))
    text = "; ".join(parts) + (f" (survey file as of {as_of})" if as_of else "")
    return {"state": "observed", "periods": periods, "text": text, "note": note}
