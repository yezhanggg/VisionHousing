"""Where a place sits inside a county's tracts: block housing weights for a city boundary.

Plain-language summary
----------------------
Some bills leave a city out. Pennsylvania's SB 1346 amends the Municipalities
Planning Code, which does not reach Pittsburgh, but Pittsburgh is a city inside
Allegheny County and its tracts are mixed with the suburbs' at the edge. The
first version of that scenario removed a flat, judged share of every Allegheny
tract. This module replaces the guess with a measurement, tract by tract.

Two ways, which should agree:

- :func:`place_tract_shares` uses the Census Bureau's 2020 block assignment
  file, already in the pack as ``tract_municipalities`` (2020 housing units of
  each tract in each county subdivision). For Pittsburgh, county subdivision
  61000 of county 42003. A tract's share is its housing units inside the city
  over all its housing units.
- :func:`block_place_shares` puts each census block's internal point (the
  pack's ``blocks`` table: ``lat``, ``lon``, ``housing_units``) inside or
  outside a boundary polygon (for example the Census TIGER/Line county
  subdivision outline, or the City's own boundary) and sums housing units the
  same way. It is the check on the first.

:func:`county_place_share` then weighs the tract shares by a column of the
tracts table (single-family detached homes for the ADU benchmark) to give the
county-wide share the flat assumption stood in for. Inside a tract that the
boundary splits, homes of every type are assumed to split like all 2020
housing units: the block file has no building type.

Places that are not county subdivisions. Wyoming's cities (Laramie, place
``5645050``) are Census places inside statistical census county divisions, so the
municipal table cannot find them. The pack's ``tract_places`` table (same
2020 block assignment, place member; same housing-unit weights) can.
:func:`place_pieces` gives it in the shape of ``tract_municipalities`` (the
place code in the ``cousub`` column) and :func:`readings_on_places` rewrites
place-level readings (7-digit ``geoid``: state + place) to one row per county
the place touches (``county`` + place), so the code that mixes local values
into tracts reads both the same way. :func:`place_pieces` is never mixed
with the municipal table: a tract's pieces add up to 1 in each.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "block_place_shares",
    "county_place_share",
    "place_pieces",
    "place_tract_shares",
    "points_in_feature",
    "readings_on_places",
]

PLACE_GEOID_DIGITS = 7
"""A place ``geoid`` is state (2) + place (5) digits; a county subdivision's is 10."""


def place_tract_shares(tract_munis: pd.DataFrame, county: str, cousub: str) -> pd.DataFrame:
    """Share of each of a county's tracts inside one county subdivision (2020 housing units).

    Args:
        tract_munis: The pack's ``tract_municipalities`` (``tract``, ``county``,
            ``cousub``, ``housing_units_2020``).
        county: Five-digit county code (``"42003"``).
        cousub: Five-digit county subdivision code (``"61000"``, Pittsburgh).

    Returns:
        One row per tract of the county: ``tract``, ``units_in_place``,
        ``units_total``, ``share`` (0 to 1; tracts with no 2020 housing units
        get 1 when wholly in the place, else 0) and ``split`` (True when the
        tract lies partly in and partly out).
    """
    t = tract_munis.copy()
    t["county"] = t["county"].astype(str).str.zfill(5)
    t["cousub"] = t["cousub"].astype(str).str.zfill(5)
    t = t[t["county"] == str(county).zfill(5)]
    t["units"] = pd.to_numeric(t["housing_units_2020"], errors="coerce").fillna(0.0)
    t["inside"] = t["cousub"] == str(cousub).zfill(5)
    g = t.groupby(t["tract"].astype(str))
    out = pd.DataFrame(
        {
            "units_in_place": g.apply(lambda x: float(x.loc[x["inside"], "units"].sum())),
            "units_total": g["units"].sum().astype(float),
            "any_in": g["inside"].any(),
            "all_in": g["inside"].all(),
        }
    ).reset_index(names="tract")
    share = np.where(
        out["units_total"] > 0,
        out["units_in_place"] / out["units_total"].where(out["units_total"] > 0, 1.0),
        out["all_in"].astype(float),
    )
    out["share"] = np.clip(share, 0.0, 1.0)
    out["split"] = out["any_in"] & ~out["all_in"]
    return out[["tract", "units_in_place", "units_total", "share", "split"]]


def _ring_contains(x: np.ndarray, y: np.ndarray, ring: list[list[float]]) -> np.ndarray:
    """Even-odd ray casting for many points against one ring."""
    r = np.asarray(ring, dtype=float)
    x1, y1 = r[:, 0], r[:, 1]
    x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
    inside = np.zeros(x.shape, dtype=bool)
    for a, b, c, d in zip(x1, y1, x2, y2, strict=True):
        if b == d:
            continue
        crosses = (b > y) != (d > y)
        xc = a + (y - b) * (c - a) / (d - b)
        inside ^= crosses & (x < xc)
    return inside


def points_in_feature(lon: np.ndarray, lat: np.ndarray, feature: Mapping[str, Any]) -> np.ndarray:
    """True for each ``(lon, lat)`` inside a GeoJSON Polygon or MultiPolygon (holes respected)."""
    geom = feature.get("geometry", feature)
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    x, y = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
    out = np.zeros(x.shape, dtype=bool)
    for poly in polys:
        # bounding-box filter first: only points near the polygon are tested
        ring = np.asarray(poly[0], dtype=float)
        near = (
            (x >= ring[:, 0].min()) & (x <= ring[:, 0].max())
            & (y >= ring[:, 1].min()) & (y <= ring[:, 1].max())
        )  # fmt: skip
        if not near.any():
            continue
        xs, ys = x[near], y[near]
        hit = _ring_contains(xs, ys, poly[0])
        for hole in poly[1:]:
            hit &= ~_ring_contains(xs, ys, hole)
        out[np.flatnonzero(near)[hit]] = True
    return out


def block_place_shares(blocks: pd.DataFrame, feature: Mapping[str, Any]) -> pd.DataFrame:
    """Tract shares from block internal points inside a boundary polygon.

    Args:
        blocks: ``tract``, ``housing_units``, ``lat``, ``lon`` (the pack's ``blocks``).
        feature: The place's GeoJSON feature or geometry (WGS 84 longitude/latitude).

    Returns:
        ``tract``, ``units_in_place``, ``units_total``, ``share``, ``split``,
        over the tracts that have at least one block inside.
    """
    b = blocks.copy()
    b["units"] = pd.to_numeric(b["housing_units"], errors="coerce").fillna(0.0)
    b["inside"] = points_in_feature(b["lon"].to_numpy(), b["lat"].to_numpy(), feature)
    b["tract"] = b["tract"].astype(str)
    touched = set(b.loc[b["inside"], "tract"])
    b = b[b["tract"].isin(touched)]
    g = b.groupby("tract")
    out = pd.DataFrame(
        {
            "units_in_place": g.apply(lambda x: float(x.loc[x["inside"], "units"].sum())),
            "units_total": g["units"].sum().astype(float),
            "all_in": g["inside"].all(),
        }
    ).reset_index()
    total = out["units_total"].where(out["units_total"] > 0, 1.0)
    out["share"] = np.where(out["units_total"] > 0, out["units_in_place"] / total, 1.0)
    out["split"] = ~out["all_in"]
    return out[["tract", "units_in_place", "units_total", "share", "split"]]


def county_place_share(
    tracts: pd.DataFrame, shares: pd.DataFrame, county: str, weight: str = "units_1_detached"
) -> dict[str, float]:
    """County-wide share of ``weight`` inside the place, from tract shares.

    Returns ``share`` (weighted), ``weight_in_place``, ``weight_total``,
    ``tracts`` (in the county), ``tracts_in_place`` (share > 0) and
    ``tracts_split``.
    """
    t = tracts.copy()
    t["geoid"] = t["geoid"].astype(str)
    t = t[t["geoid"].str[:5] == str(county).zfill(5)]
    w = pd.to_numeric(t[weight], errors="coerce").fillna(0.0)
    s = t["geoid"].map(shares.set_index("tract")["share"]).fillna(0.0)
    inside = float((w * s).sum())
    total = float(w.sum())
    split = shares.loc[shares["split"], "tract"]
    return {
        "share": inside / total if total > 0 else 0.0,
        "weight_in_place": inside,
        "weight_total": total,
        "tracts": float(len(t)),
        "tracts_in_place": float((s > 0).sum()),
        "tracts_split": float(t["geoid"].isin(set(split)).sum()),
    }


def place_pieces(tract_places: pd.DataFrame) -> pd.DataFrame:
    """The pack's ``tract_places`` in the shape of ``tract_municipalities``.

    Returns ``tract``, ``county``, ``cousub`` (holding the 5-digit place code;
    ``99999`` = outside every place) and ``housing_units_2020``, so a reader
    of municipal pieces (for example
    :func:`visionhousing.scenarios.parking.minimum_mix`) keys each piece as
    county + place. Every tract's pieces still add up to all its 2020 units.
    """
    t = tract_places.copy()
    return pd.DataFrame(
        {
            "tract": t["tract"].astype(str),
            "county": t["county"].astype(str).str.zfill(5),
            "cousub": t["place"].astype(str).str.zfill(5),
            "housing_units_2020": pd.to_numeric(t["housing_units_2020"], errors="coerce"),
        }
    )


def readings_on_places(rows: pd.DataFrame, tract_places: pd.DataFrame) -> pd.DataFrame:
    """Place-level rows (7-digit ``geoid``) restated per county piece of the place.

    Each row whose ``geoid`` is state + place becomes one row per county the
    place has a piece in (``geoid`` = county + place, ``county`` = that
    county), matching the keys of :func:`place_pieces`. Rows of a place with
    no piece in the table are dropped (it cannot be placed); other rows are
    returned unchanged.
    """
    if rows.empty:
        return rows.copy()
    tp = tract_places.assign(
        county=tract_places["county"].astype(str).str.zfill(5),
        place=tract_places["place"].astype(str).str.zfill(5),
    )
    tp["key"] = tp["county"].str[:2] + tp["place"]
    counties = tp.groupby("key")["county"].apply(lambda c: sorted(set(c)))
    out: list[dict[str, Any]] = []
    for row in rows.to_dict("records"):
        g = str(row["geoid"])
        if len(g) != PLACE_GEOID_DIGITS:
            out.append(row)
            continue
        for county in counties.get(g, []):
            out.append({**row, "geoid": county + g[2:], "county": county})
    return pd.DataFrame(out, columns=rows.columns).reset_index(drop=True)
