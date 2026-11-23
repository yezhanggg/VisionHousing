"""Tract housing density, a lot-size proxy, and an area type, from 2020 Census blocks.

Plain-language summary
----------------------
Every function here is pure: a blocks table goes in, a tract table comes out.
The blocks table has one row per 2020 census block with its housing units,
people, land area in square metres and an urban flag (see
:mod:`visionhousing.data.sources.blocks`).

Features per tract
------------------
- ``housing_units_2020``, ``population_2020``: the sums of the tract's blocks.
  These are 2020 Census counts. The tract's ``housing_units`` column in the pack
  is the newer survey estimate (ACS 2020-2024), so the two differ a little.
- ``block_land_area_m2``: the sum of the blocks' land area.
- ``housing_density_per_km2`` = ``housing_units_2020`` / (land area in km²).
  Blank when the tract has no land.
- ``residential_blocks``: blocks with at least one housing unit.
- **Lot-size proxy.** For each block with at least one unit,
  ``land per unit = block land area / block housing units`` (square metres).
  This stands in for "residential land per housing unit": a block's land also
  holds parks, shops, farms and half of the streets around it (block edges run
  along street centre lines), so the proxy is an upper bound on the typical
  lot, most of all in rural blocks. Per tract the code reports the
  25th, 50th and 75th percentiles of land per unit, **weighted by housing
  units**: ``lot_m2_per_unit_p50`` is the land per unit of the block that the
  middle home of the tract sits in. A block of 200 flats counts 200 times; a
  farm block with one house counts once.
- ``urban_unit_share``: the share of the tract's 2020 housing units that sit in
  blocks the Census Bureau classed as urban. For a tract with no housing it is
  the urban share of land.

Weighted percentile rule
------------------------
Sort the values; the ``q`` percentile is the first value at which the running
total of weights reaches ``q`` times the total weight. No interpolation, so the
answer is always a value that occurs in the data and the result is the same on
every machine.

Area type (``area_type``: urban, suburban or rural)
---------------------------------------------------
1. **rural** when less than half of the tract's housing units are in
   Census-urban blocks (:data:`RURAL_URBAN_SHARE_BELOW`).
2. Otherwise **urban** when housing density is at least
   :data:`URBAN_UNITS_PER_SQ_MILE` (2,000 housing units per square mile of
   land, about 772 per km²: the density of row-house and small-lot city
   neighbourhoods).
3. Otherwise **suburban**.

Step 1 uses the Bureau's own 2020 urban classification. The density threshold
in step 2 is a judgement, stated here so it can be checked and changed in one
place; it only labels tracts and changes no count.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "AREA_TYPES",
    "QUANTILES",
    "RURAL_URBAN_SHARE_BELOW",
    "SQ_M_PER_SQ_MILE",
    "TRACT_COLUMNS",
    "URBAN_UNITS_PER_SQ_MILE",
    "area_type",
    "county_density",
    "tract_density",
    "weighted_quantile",
]

QUANTILES = {"p25": 0.25, "p50": 0.50, "p75": 0.75}
"""Percentiles of the lot-size proxy reported per tract."""

SQ_M_PER_SQ_MILE = 2_589_988.110336
"""Square metres in one square mile (exact by definition of the international mile)."""

URBAN_UNITS_PER_SQ_MILE = 2000.0
"""Housing units per square mile of land at or above which a Census-urban tract is "urban"."""

RURAL_URBAN_SHARE_BELOW = 0.5
"""A tract is "rural" when less than this share of its housing units is in urban blocks."""

AREA_TYPES = ("urban", "suburban", "rural")

TRACT_COLUMNS = (
    "tract",
    "blocks",
    "residential_blocks",
    "housing_units_2020",
    "population_2020",
    "block_land_area_m2",
    "housing_density_per_km2",
    "lot_m2_per_unit_p25",
    "lot_m2_per_unit_p50",
    "lot_m2_per_unit_p75",
    "urban_unit_share",
)
"""Columns returned by :func:`tract_density`, in order."""


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """Return the first sorted value at which the running weight reaches ``q`` x total.

    Args:
        values: Numbers to take the percentile of.
        weights: Non-negative weights, one per value.
        q: The percentile as a share, 0 < q <= 1.

    Returns:
        A value from ``values``, or ``nan`` when the total weight is zero.
    """
    if not 0 < q <= 1:
        raise ValueError("q must be in (0, 1]")
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    if values.shape != weights.shape:
        raise ValueError("values and weights must have the same length")
    if (weights < 0).any():
        raise ValueError("weights must not be negative")
    total = weights.sum()
    if total <= 0:
        return float("nan")
    order = np.argsort(values, kind="stable")
    running = np.cumsum(weights[order])
    at = int(np.searchsorted(running, q * total - 1e-9 * total, side="left"))
    return float(values[order][min(at, len(values) - 1)])


def _lot_quantiles(blocks: pd.DataFrame) -> pd.DataFrame:
    homes = blocks[blocks["housing_units"] >= 1]
    per_unit = homes["land_area_m2"].to_numpy(float) / homes["housing_units"].to_numpy(float)
    weights = homes["housing_units"].to_numpy(float)
    tracts = homes["tract"].to_numpy()
    order = np.argsort(tracts, kind="stable")
    tracts, per_unit, weights = tracts[order], per_unit[order], weights[order]
    names, starts = np.unique(tracts, return_index=True)
    ends = [*starts[1:], len(tracts)]
    rows = {f"lot_m2_per_unit_{k}": [] for k in QUANTILES}
    for start, end in zip(starts, ends, strict=True):
        for k, q in QUANTILES.items():
            value = weighted_quantile(per_unit[start:end], weights[start:end], q)
            rows[f"lot_m2_per_unit_{k}"].append(round(value, 1))
    return pd.DataFrame(rows, index=pd.Index(names, name="tract"))


def tract_density(blocks: pd.DataFrame) -> pd.DataFrame:
    """Return one row per tract with the features of :data:`TRACT_COLUMNS`.

    Args:
        blocks: Columns ``tract``, ``housing_units``, ``population``,
            ``land_area_m2`` and ``urban`` (1 for a Census-urban block).

    Returns:
        Sorted by ``tract``. Tracts with no land have a blank density; tracts with
        no housing have blank lot-size percentiles.
    """
    b = blocks
    urban_units = b["housing_units"] * b["urban"]
    urban_land = b["land_area_m2"] * b["urban"]
    grouped = (
        pd.DataFrame(
            {
                "tract": b["tract"],
                "blocks": 1,
                "residential_blocks": (b["housing_units"] >= 1).astype("int64"),
                "housing_units_2020": b["housing_units"],
                "population_2020": b["population"],
                "block_land_area_m2": b["land_area_m2"],
                "_urban_units": urban_units,
                "_urban_land": urban_land,
            }
        )
        .groupby("tract", sort=True)
        .sum()
    )
    land_km2 = grouped["block_land_area_m2"] / 1e6
    units = grouped["housing_units_2020"]
    grouped["housing_density_per_km2"] = (units / land_km2.where(land_km2 > 0)).round(2)
    by_units = grouped["_urban_units"] / units.where(units > 0)
    land = grouped["block_land_area_m2"]
    by_land = grouped["_urban_land"] / land.where(land > 0)
    grouped["urban_unit_share"] = by_units.fillna(by_land).round(4)
    out = grouped.join(_lot_quantiles(b), how="left").reset_index()
    return out[list(TRACT_COLUMNS)]


def area_type(density: pd.DataFrame) -> pd.Series:
    """Label each tract urban, suburban or rural by the rule in the module notes.

    Args:
        density: The output of :func:`tract_density`.

    Returns:
        Text labels, aligned with ``density``.
    """
    per_sq_mile = density["housing_density_per_km2"] * SQ_M_PER_SQ_MILE / 1e6
    share = density["urban_unit_share"].fillna(0.0)
    rural = share < RURAL_URBAN_SHARE_BELOW
    urban = ~rural & (per_sq_mile.fillna(0.0) >= URBAN_UNITS_PER_SQ_MILE)
    labels = np.select([rural, urban], ["rural", "urban"], "suburban")
    return pd.Series(labels, index=density.index, name="area_type")


def county_density(blocks: pd.DataFrame) -> pd.DataFrame:
    """Return county sums of 2020 housing units, people and land, and housing density."""
    grouped = (
        blocks.assign(county=blocks["tract"].str[:5])
        .groupby("county", sort=True)[["housing_units", "population", "land_area_m2"]]
        .sum()
        .rename(
            columns={
                "housing_units": "housing_units_2020",
                "population": "population_2020",
                "land_area_m2": "block_land_area_m2",
            }
        )
    )
    land_km2 = grouped["block_land_area_m2"] / 1e6
    grouped["housing_density_per_km2"] = (
        grouped["housing_units_2020"] / land_km2.where(land_km2 > 0)
    ).round(2)
    return grouped.reset_index()
