"""Hazards and land screen per census tract: flood zones, developed land, impervious cover, slope.

Plain-language summary
----------------------
For each census tract this module works out a few **screening shares**: rough
measures of how much of a place is practically buildable, kept apart from
what the zoning rules allow. They are inputs for a later "practical versus
nominal capacity" step; nothing here changes a model estimate.

- ``sfha_share``: the share of the tract's homes in FEMA's **Special Flood
  Hazard Area** (SFHA: the 1% annual chance floodplain, flood zones A and V
  and their subtypes, as FEMA flags them with ``SFHA_TF = 'T'``), out of the
  homes inside FEMA's digital flood map (``nfhl_homes``), so homes FEMA has not
  mapped are not counted as dry. Homes are 2020 Census housing units by block;
  a block counts as in the SFHA when its internal point lies on a grid cell
  whose centre is inside an SFHA polygon.
- ``sfha_dev_share``: the share of the tract's **developed land** inside the
  digital flood map that is in the SFHA.
- ``developed_share``: developed land / all land. Developed land is the four
  Annual NLCD developed classes (21 open space, 22 low, 23 medium, 24 high
  intensity); land is every cell that is not open water (class 11) or empty.
- ``impervious_share``: the mean of Annual NLCD's fractional impervious
  surface (0-100%) over the tract's land cells that have a valid value, as a
  share.
- ``steep_share``: the share of land cells whose slope is above the threshold
  (15% by default, ``assumptions/hazards.yaml``). Slope is worked out from a
  30 m elevation grid (USGS 3DEP) with central differences:
  ``slope = sqrt((dz/dx)^2 + (dz/dy)^2) * 100``.

How a tract is measured
~~~~~~~~~~~~~~~~~~~~~~~
All grids use one 30 m grid in the national Albers equal-area projection
(EPSG:5070), aligned with the NLCD cells, so every cell has the same area
(900 square metres). Tract outlines are projected with :func:`to_albers` (the
Albers formulas for the GRS 1980 ellipsoid; checked against FEMA's own
server to well under a millimetre). A cell belongs to a tract when its centre
is inside the tract outline; flood-zone polygons are burned in the same way.
Counts are added per tract with ``numpy.bincount``, so each tract's shares are
plain ratios of cell counts.

Where FEMA has no digital flood map (outside the "NFHL availability"
polygons), the flood shares are left blank rather than shown as zero: a tract
is blank when less than ``min_nfhl_coverage`` of its homes are inside the map
(of its land, for a tract with no homes).

The work runs one county at a time (see :mod:`visionhousing.features.hazards_build`);
this module only holds the arithmetic, so it can be tested on tiny synthetic
grids without a network.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.features import planar

__all__ = [
    "COUNTY_COLUMNS",
    "COUNT_COLUMNS",
    "TABLES",
    "TRACT_COLUMNS",
    "GridSpec",
    "burn",
    "burn_rings",
    "county_table",
    "describe",
    "flood_mapped",
    "grid_for_bounds",
    "label_tracts",
    "load_rules",
    "project_geometry",
    "slope_percent",
    "to_albers",
    "tract_counts",
    "tract_table",
]

ROOT = Path(__file__).resolve().parents[3]
ASSUMPTIONS = ROOT / "assumptions" / "hazards.yaml"

# --------------------------------------------------------------------------- projection

_A = 6378137.0
_F = 1 / 298.257222101
_E2 = 2 * _F - _F * _F
_E = math.sqrt(_E2)


def _q(phi: np.ndarray) -> np.ndarray:
    s = np.sin(phi)
    return (1 - _E2) * (
        s / (1 - _E2 * s * s) - (1 / (2 * _E)) * np.log((1 - _E * s) / (1 + _E * s))
    )


def _m(phi: float) -> float:
    s = math.sin(phi)
    return math.cos(phi) / math.sqrt(1 - _E2 * s * s)


_P1, _P2, _P0, _L0 = (math.radians(v) for v in (29.5, 45.5, 23.0, -96.0))
_N = (_m(_P1) ** 2 - _m(_P2) ** 2) / float(_q(np.array(_P2)) - _q(np.array(_P1)))
_C = _m(_P1) ** 2 + _N * float(_q(np.array(_P1)))
_RHO0 = _A * math.sqrt(_C - _N * float(_q(np.array(_P0)))) / _N


def to_albers(lon: Any, lat: Any) -> tuple[np.ndarray, np.ndarray]:
    """Project longitude/latitude (NAD83, degrees) to Conus Albers (EPSG:5070) metres.

    Standard parallels 29.5 and 45.5 degrees north, origin 23 degrees north,
    96 degrees west, GRS 1980 ellipsoid, no false easting or northing (the
    formulas in Snyder, *Map Projections: A Working Manual*, 1987, p. 101).
    """
    lam = np.radians(np.asarray(lon, dtype=float))
    phi = np.radians(np.asarray(lat, dtype=float))
    rho = _A * np.sqrt(_C - _N * _q(phi)) / _N
    theta = _N * (lam - _L0)
    return rho * np.sin(theta), _RHO0 - rho * np.cos(theta)


# --------------------------------------------------------------------------- grid

# The grid and the polygon arithmetic live in visionhousing.features.planar (plain NumPy).
GridSpec = planar.GridSpec
grid_for_bounds = planar.grid_for_bounds


# --------------------------------------------------------------------------- burning shapes


def project_geometry(geometry: dict[str, Any]) -> list[np.ndarray]:
    """A GeoJSON polygon or multipolygon (lon/lat) as its rings in Albers metres.

    All rings (outer rings and holes of every part) are returned together; they
    are combined by the even-odd rule when burned, so holes come out right.
    """
    polys = planar.geojson_polygons(geometry, to_albers)
    return [ring for rings in polys for ring in rings]


def _burn_one(grid: GridSpec, rings: Sequence[Any], target: np.ndarray, value: int) -> None:
    rings = [np.asarray(r, dtype=float) for r in rings if len(r) >= 3]
    if not rings:
        return
    rows, cols = grid.window(*planar.rings_bounds(rings))
    if rows.stop <= rows.start or cols.stop <= cols.start:
        return
    inside = planar.fill_rings(grid.sub(rows, cols), rings)
    if inside.any():
        target[rows, cols][inside] = value


def burn(
    grid: GridSpec,
    shapes: Iterable[Sequence[Any]],
    *,
    out: np.ndarray | None = None,
    value: int = 1,
) -> np.ndarray:
    """Set ``value`` on every cell whose centre is inside any of the shapes.

    Args:
        grid: The grid.
        shapes: Each shape's rings (``(n, 2)`` arrays in the grid's coordinates),
            combined by the even-odd rule, as :func:`project_geometry` gives them.
        out: Array to write into (default: a new zero ``int32`` array).
        value: The value written.

    Returns:
        The array.
    """
    target = np.zeros((grid.rows, grid.cols), dtype=np.int32) if out is None else out
    for rings in shapes:
        if rings is not None:
            _burn_one(grid, rings, target, value)
    return target


def burn_rings(
    grid: GridSpec,
    features: Iterable[Sequence[Sequence[Sequence[float]]]],
    *,
    out: np.ndarray | None = None,
) -> np.ndarray:
    """Mark cells inside map-service polygons given as lists of rings (Esri JSON ``rings``).

    A feature's rings are combined by the even-odd rule: a cell centre inside an
    odd number of its rings is inside the feature (so holes and islands come out
    right without reading ring direction). Features are joined (a cell inside
    any feature is marked). Coordinates must already be in the grid's projection.

    Returns:
        A ``uint8`` array: 1 inside any feature, else 0 (or ``out`` updated).
    """
    target = np.zeros((grid.rows, grid.cols), dtype=np.uint8) if out is None else out
    for rings in features:
        _burn_one(grid, [r for r in rings if len(r) >= 4], target, 1)
    return target


def label_tracts(grid: GridSpec, shapes: Sequence[Any]) -> np.ndarray:
    """Label each cell with ``1 + index`` of the tract whose outline holds its centre (0 = none).

    Where outlines overlap (they should not), the later tract wins.
    """
    labels = np.zeros((grid.rows, grid.cols), dtype=np.int32)
    for k, rings in enumerate(shapes):
        _burn_one(grid, rings, labels, k + 1)
    return labels


# --------------------------------------------------------------------------- slope


def slope_percent(dem: np.ndarray, cell: float, nodata: float | None = None) -> np.ndarray:
    """Slope in percent from an elevation grid in metres, by central differences.

    ``slope = 100 * sqrt((dz/dx)^2 + (dz/dy)^2)``; edge cells use one-sided
    differences (``numpy.gradient``). Cells with no elevation, or next to one,
    are ``NaN``.
    """
    z = np.asarray(dem, dtype=np.float64)
    bad = ~np.isfinite(z)
    if nodata is not None:
        bad |= z == nodata
    bad |= z < -1000  # service fill values such as -3.4e38
    z = np.where(bad, np.nan, z)
    gy, gx = np.gradient(z, cell, cell)
    return 100.0 * np.hypot(gx, gy)


# --------------------------------------------------------------------------- counting

COUNT_COLUMNS: tuple[str, ...] = (
    "geoid",
    "land_cells",
    "developed_cells",
    "impervious_sum",
    "impervious_cells",
    "steep_cells",
    "slope_cells",
    "nfhl_cells",
    "sfha_cells",
    "sfha_developed_cells",
    "nfhl_developed_cells",
    "homes",
    "sfha_homes",
    "nfhl_homes",
)
"""Per-tract cell and home counts kept in the county cache files."""


def tract_counts(
    labels: np.ndarray,
    tract_ids: Sequence[str],
    landcover: np.ndarray,
    impervious: np.ndarray,
    slope: np.ndarray,
    sfha: np.ndarray,
    nfhl: np.ndarray,
    rules: dict[str, Any],
    blocks: pd.DataFrame | None = None,
    grid: GridSpec | None = None,
) -> pd.DataFrame:
    """Add up cells (and homes) per tract.

    Args:
        labels: Tract label per cell (``1 + index`` into ``tract_ids``; 0 = none).
        tract_ids: Tract codes in label order.
        landcover: NLCD land-cover class per cell.
        impervious: NLCD fractional impervious surface (0-100) per cell.
        slope: Slope in percent per cell (``NaN`` where unknown).
        sfha: 1 where the cell centre is in a Special Flood Hazard Area.
        nfhl: 1 where FEMA has a digital flood map.
        rules: ``assumptions/hazards.yaml`` (classes and thresholds).
        blocks: 2020 blocks with ``tract``, ``housing_units``, ``x``, ``y`` (Albers metres).
        grid: The grid (needed with ``blocks``).

    Returns:
        One row per tract with the columns in :data:`COUNT_COLUMNS`.
    """
    lc_rules = rules["landcover"]
    n = len(tract_ids) + 1
    lab = labels.ravel()
    lc = landcover.ravel()
    land = ~np.isin(lc, [*lc_rules["water_classes"], *lc_rules["empty_classes"]])
    developed = np.isin(lc, lc_rules["developed_classes"])
    imp = impervious.ravel().astype(np.float64)
    imp_ok = land & (imp <= 100)
    sl = slope.ravel()
    slope_ok = land & np.isfinite(sl)
    steep = slope_ok & (sl > float(rules["slope"]["steep_percent"]))
    flood = sfha.ravel().astype(bool) & land
    mapped = nfhl.ravel().astype(bool) & land

    def count(mask: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
        w = None if weights is None else np.where(mask, weights, 0.0)
        return np.bincount(
            lab, weights=w if w is not None else mask.astype(np.float64), minlength=n
        )

    out = pd.DataFrame(
        {
            "geoid": list(tract_ids),
            "land_cells": count(land)[1:],
            "developed_cells": count(land & developed)[1:],
            "impervious_sum": count(imp_ok, imp)[1:],
            "impervious_cells": count(imp_ok)[1:],
            "steep_cells": count(steep)[1:],
            "slope_cells": count(slope_ok)[1:],
            "nfhl_cells": count(mapped)[1:],
            "sfha_cells": count(flood)[1:],
            "sfha_developed_cells": count(flood & developed)[1:],
            "nfhl_developed_cells": count(mapped & developed)[1:],
        }
    )
    homes = pd.Series(0.0, index=out["geoid"])
    in_flood = pd.Series(0.0, index=out["geoid"])
    in_map = pd.Series(0.0, index=out["geoid"])
    if blocks is not None and grid is not None and len(blocks):
        b = blocks[blocks["tract"].isin(set(tract_ids))]
        units = pd.to_numeric(b["housing_units"], errors="coerce").fillna(0).to_numpy(float)
        row, col, ok = grid.cell_of(b["x"].to_numpy(float), b["y"].to_numpy(float))
        hit = np.zeros(len(b), dtype=bool)
        covered = np.zeros(len(b), dtype=bool)
        hit[ok] = sfha[row[ok], col[ok]].astype(bool)
        covered[ok] = nfhl[row[ok], col[ok]].astype(bool)
        tracts = b["tract"].astype(str).to_numpy()
        frame = pd.DataFrame({"tract": tracts, "u": units, "f": units * hit, "m": units * covered})
        sums = frame.groupby("tract")[["u", "f", "m"]].sum()
        homes = homes.add(sums["u"], fill_value=0).reindex(out["geoid"])
        in_flood = in_flood.add(sums["f"], fill_value=0).reindex(out["geoid"])
        in_map = in_map.add(sums["m"], fill_value=0).reindex(out["geoid"])
    out["homes"] = homes.to_numpy()
    out["sfha_homes"] = in_flood.to_numpy()
    out["nfhl_homes"] = in_map.to_numpy()
    for col in COUNT_COLUMNS[1:]:
        if col != "impervious_sum":
            out[col] = out[col].round().astype("int64")
    out["impervious_sum"] = out["impervious_sum"].round().astype("int64")
    return out


# --------------------------------------------------------------------------- tables

TRACT_COLUMNS: tuple[str, ...] = (
    "geoid",
    "sfha_share",
    "sfha_dev_share",
    "developed_share",
    "impervious_share",
    "steep_share",
)
"""Columns of ``hazard_tract``, in order."""

COUNTY_COLUMNS: tuple[str, ...] = (
    "county",
    "tracts",
    "tracts_flood_unmapped",
    "homes",
    "nfhl_homes",
    "sfha_homes",
    "land_acres",
    "developed_acres",
    "nfhl_developed_acres",
    "sfha_share",
    "sfha_dev_share",
    "developed_share",
    "impervious_share",
    "steep_share",
    "nfhl_coverage",
    "sfha_polygons",
    "nlcd_year",
    "read_on",
)
"""Columns of ``hazard_county``, in order."""

_SOURCES = [
    "fema_nfhl",
    "usgs_annual_nlcd",
    "usgs_3dep_dem",
    "tiger_2020_tabblock",
    "cb_2020_tract",
]

TABLES: dict[str, dict[str, object]] = {
    "hazard_tract": {
        "key": ["geoid"],
        "sources": _SOURCES,
        "tier": "0",
        "description": (
            "Screening shares per tract: homes and developed land in FEMA's Special Flood "
            "Hazard Area, developed land and mean impervious cover (Annual NLCD), and land "
            "steeper than the slope threshold (USGS 3DEP, 30 m). Shares of cell or home "
            "counts; flood shares blank where FEMA has no digital map."
        ),
    },
    "hazard_county": {
        "key": ["county"],
        "sources": _SOURCES,
        "tier": "0",
        "description": (
            "The same screening shares per county (sums of the tract counts), with the "
            "share of land FEMA has mapped, the number of flood-zone polygons read, the land "
            "cover year and the date the services were read."
        ),
    },
}
"""The two pack tables: key, sources, tier and description."""

_CELL_ACRES = 900.0 / 4046.8564224

_COLUMNS: dict[str, tuple[str, str]] = {
    "geoid": ("code", "Tract code: 5-digit county code + 6-digit tract code"),
    "county": ("code", "County code (5-digit FIPS)"),
    "sfha_share": (
        "share",
        "Share of 2020 homes (block counts) inside FEMA's digital flood map whose block point "
        "lies in a Special Flood Hazard Area (FEMA NFHL, SFHA_TF = T); blank where less than "
        "the minimum share of homes is mapped",
    ),
    "sfha_dev_share": (
        "share",
        "Share of developed land (NLCD classes 21-24) inside FEMA's digital flood map that is "
        "in a Special Flood Hazard Area; blank where less than the minimum share of homes is "
        "mapped",
    ),
    "developed_share": ("share", "Developed land (NLCD classes 21-24) / all land"),
    "impervious_share": (
        "share",
        "Mean fractional impervious surface over land cells with a valid value (NLCD), 0-1",
    ),
    "steep_share": ("share", "Share of land with slope above the threshold (3DEP 30 m grid)"),
    "tracts": ("tracts", "Tracts measured in the county"),
    "tracts_flood_unmapped": (
        "tracts",
        "Tracts left blank for flood shares (less than the minimum share of homes inside "
        "FEMA's digital flood map)",
    ),
    "homes": ("units", "2020 housing units (block counts) in the measured tracts"),
    "nfhl_homes": (
        "units",
        "2020 housing units inside FEMA's digital flood map, in tracts with flood shares "
        "(the denominator of sfha_share)",
    ),
    "sfha_homes": (
        "units",
        "2020 housing units whose block point lies in an SFHA, in tracts with flood shares",
    ),
    "land_acres": ("acres", "Land cells x 0.2224 acres (30 m cells)"),
    "developed_acres": ("acres", "Developed land cells x 0.2224 acres"),
    "nfhl_developed_acres": (
        "acres",
        "Developed land inside FEMA's digital flood map, in tracts with flood shares "
        "(the denominator of sfha_dev_share)",
    ),
    "nfhl_coverage": ("share", "Share of land inside FEMA's digital flood map (NFHL availability)"),
    "sfha_polygons": ("polygons", "Flood-zone polygons read for the county (SFHA only)"),
    "nlcd_year": ("year", "Annual NLCD year used"),
    "read_on": ("date", "Date the map services were read"),
}


def describe(table: str, name: str) -> tuple[str, str]:
    """Unit and plain description of one column, for the pack's data dictionary."""
    if name not in _COLUMNS:
        raise ValueError(f"no data-dictionary entry for {table}.{name}")
    return _COLUMNS[name]


def _share(num: pd.Series, den: pd.Series) -> pd.Series:
    num = pd.to_numeric(num, errors="coerce")
    den = pd.to_numeric(den, errors="coerce")
    return (num / den.where(den > 0)).round(3)


def _num(counts: pd.DataFrame, column: str) -> pd.Series:
    if column not in counts.columns:
        raise ValueError(f"hazard counts have no {column} column; rebuild the county counts")
    return pd.to_numeric(counts[column], errors="coerce").fillna(0)


def flood_mapped(counts: pd.DataFrame, rules: dict[str, Any]) -> pd.Series:
    """True for tracts with enough homes inside FEMA's digital flood map to show flood shares.

    The test is on homes (``nfhl_homes / homes >= min_nfhl_coverage``), since
    the flood share is a share of homes. A tract with no homes is judged by
    its land instead (``nfhl_cells / land_cells``).
    """
    min_cover = float(rules["flood"]["min_nfhl_coverage"])
    homes = _num(counts, "homes")
    land = _num(counts, "land_cells")
    by_homes = _num(counts, "nfhl_homes") / homes.where(homes > 0)
    by_land = _num(counts, "nfhl_cells") / land.where(land > 0)
    cover = by_homes.where(homes > 0, by_land)
    return cover.fillna(0) >= min_cover


def tract_table(counts: pd.DataFrame, rules: dict[str, Any]) -> pd.DataFrame:
    """``hazard_tract`` from per-tract counts (3-decimal shares; flood blank when unmapped)."""
    c = counts.copy()
    mapped = flood_mapped(c, rules)
    out = pd.DataFrame({"geoid": c["geoid"].astype(str)})
    out["sfha_share"] = _share(c["sfha_homes"], _num(c, "nfhl_homes")).where(mapped)
    out["sfha_dev_share"] = _share(
        c["sfha_developed_cells"], _num(c, "nfhl_developed_cells")
    ).where(mapped)
    out["developed_share"] = _share(c["developed_cells"], c["land_cells"])
    out["impervious_share"] = _share(c["impervious_sum"] / 100.0, _num(c, "impervious_cells"))
    out["steep_share"] = _share(c["steep_cells"], c["slope_cells"])
    return out[list(TRACT_COLUMNS)].sort_values("geoid").reset_index(drop=True)


def county_table(
    counts: pd.DataFrame, notes: dict[str, dict[str, Any]], rules: dict[str, Any]
) -> pd.DataFrame:
    """``hazard_county``: the tract counts added up per county, with the reading notes.

    Flood shares for a county use only its tracts with flood shares, and in
    them only the homes and developed land inside the digital flood map.
    """
    c = counts.copy()
    c["county"] = c["geoid"].astype(str).str[:5]
    c["mapped"] = flood_mapped(c, rules).to_numpy()
    rows = []
    for county, g in c.groupby("county", sort=True):
        m = g[g["mapped"]]
        note = notes.get(county, {})
        rows.append(
            {
                "county": county,
                "tracts": len(g),
                "tracts_flood_unmapped": int((~g["mapped"]).sum()),
                "homes": int(g["homes"].sum()),
                "nfhl_homes": int(m["nfhl_homes"].sum()),
                "sfha_homes": int(m["sfha_homes"].sum()),
                "land_acres": round(float(g["land_cells"].sum()) * _CELL_ACRES),
                "developed_acres": round(float(g["developed_cells"].sum()) * _CELL_ACRES),
                "nfhl_developed_acres": round(float(m["nfhl_developed_cells"].sum()) * _CELL_ACRES),
                "sfha_share": _ratio(m["sfha_homes"].sum(), m["nfhl_homes"].sum()),
                "sfha_dev_share": _ratio(
                    m["sfha_developed_cells"].sum(), m["nfhl_developed_cells"].sum()
                ),
                "developed_share": _ratio(g["developed_cells"].sum(), g["land_cells"].sum()),
                "impervious_share": _ratio(
                    g["impervious_sum"].sum() / 100, g["impervious_cells"].sum()
                ),
                "steep_share": _ratio(g["steep_cells"].sum(), g["slope_cells"].sum()),
                "nfhl_coverage": _ratio(g["nfhl_cells"].sum(), g["land_cells"].sum()),
                "sfha_polygons": int(note.get("sfha_polygons", 0)),
                "nlcd_year": int(note.get("nlcd_year", rules["landcover"]["year"])),
                "read_on": str(note.get("read_on", "")),
            }
        )
    return pd.DataFrame(rows, columns=list(COUNTY_COLUMNS))


def _ratio(num: float, den: float) -> float | None:
    return None if not den or den <= 0 else round(float(num) / float(den), 3)


def load_rules(path: Path = ASSUMPTIONS) -> dict[str, Any]:
    """Read ``assumptions/hazards.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
