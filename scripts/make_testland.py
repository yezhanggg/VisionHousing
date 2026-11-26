"""Build Testland: a small, made-up state used by every offline test and the interface.

What it is: Testland (state code "TL") is a synthetic mini-state with four counties,
40 census-tract-like areas, 400 blocks, 30 transit stops and 30 years of building
permits. Nothing in it is real. Its tables use the same column names and shapes as a
real state pack, so code written against Testland runs unchanged on real data.

Why: tests and the interface must run with no downloads. A committed, tiny, fully
synthetic state makes that possible and keeps every test fast.

How it stays identical run to run:
- One fixed seed drives a Mersenne Twister generator from the standard library
  (``random.Random``). Only its ``random()`` method is used, whose output is fixed by
  the Python documentation, so the numbers do not change between Python versions.
- Every float is written with a fixed number of decimals.
- Rows are written in a fixed order, with Unix line endings.

Plausible shapes (not real values):
- The urban county is mostly attached (row) houses and renters; suburban and rural
  counties are mostly detached houses and owners; the rural county has many mobile homes.
- Home values and rents follow log-normal curves cut into the published Census
  bracket edges (ACS tables B25075 and B25063).
- Permits rise to a mid-2000s peak, fall hard in 2008-2011, then recover with a
  recent rise.

The collapsed survey bands behind the profile charts (``acs_bands.csv``, laid out as
:mod:`visionhousing.features.acs_bands` describes) use a second generator seeded with
``SEED + 1``, so adding them left every other file unchanged. A tract's bands add up to
its population, households and homes; owners, renters and building types copy the tract
table; county and state rows are the sums of their tracts, with margins combined by the
Bureau's square-root-of-squares rule.

Four optional tables (water and sewer readiness ``infra_tract``, hazard screening
``hazard_tract``, parcel land use ``parcels_area`` and HUD-style income limits
``afford_income_limits``) use a third generator seeded with ``SEED + 2``, so adding them
left every earlier file unchanged. They have the real pack's columns; readiness and
hazards go through the real pack's table functions. Their rules are the constants above
:func:`_infra_rows`. They let the extra scenarios run their "table present" path on
Testland; the benchmark reforms read none of them.

Usage: ``uv run python scripts/make_testland.py --out tests/fixtures/testland``.
"""

from __future__ import annotations

import argparse
import csv
import io
import itertools
import json
import math
import random
from collections.abc import Sequence
from pathlib import Path

SEED = 20261122
STATE_CODE = "TL"
STATE_FIPS = "99"  # not a real state code
YEARS = list(range(1996, 2026))
BLOCKS_PER_TRACT = 10  # laid out as a 5 x 2 grid inside each tract
STOPS = 30

# Counties: (county code, name, area type, tract count, tract grid columns x rows,
# bounding box west, south, east, north in degrees). The box sits in open ocean so it
# can never be mistaken for a real place.
COUNTIES = [
    ("001", "Harbor County", "urban", 14, (7, 2), (-59.00, 30.50, -58.80, 30.70)),
    ("003", "Maple County", "suburban", 10, (5, 2), (-59.40, 30.50, -59.00, 30.70)),
    ("005", "Brookside County", "suburban", 9, (3, 3), (-59.40, 30.20, -58.80, 30.50)),
    ("007", "Prairie County", "rural", 7, (7, 1), (-60.20, 30.20, -59.40, 30.70)),
]

STRUCTURE_COLUMNS = [
    "units_1_detached",
    "units_1_attached",
    "units_2",
    "units_3_4",
    "units_5_9",
    "units_10_19",
    "units_20_plus",
    "units_mobile",
]

# Typical shares of housing units by structure type, by area type.
STRUCTURE_SHARES = {
    "urban": [0.08, 0.55, 0.06, 0.07, 0.04, 0.04, 0.15, 0.01],
    "suburban": [0.62, 0.12, 0.03, 0.04, 0.05, 0.05, 0.06, 0.03],
    "rural": [0.74, 0.04, 0.03, 0.02, 0.02, 0.01, 0.01, 0.13],
}

# Area-type profiles: housing units per tract (low, high), renter share, vacancy
# rate, median household income, median gross rent, median home value, share 65+,
# persons per occupied unit.
PROFILES = {
    "urban": dict(
        hu=(1800, 2600),
        renter=0.48,
        vacancy=0.11,
        income=52000,
        rent=1150,
        value=210000,
        age65=0.14,
        hh=2.3,
    ),
    "suburban": dict(
        hu=(1500, 2200),
        renter=0.25,
        vacancy=0.06,
        income=88000,
        rent=1350,
        value=320000,
        age65=0.19,
        hh=2.6,
    ),
    "rural": dict(
        hu=(900, 1500),
        renter=0.22,
        vacancy=0.12,
        income=58000,
        rent=850,
        value=170000,
        age65=0.22,
        hh=2.4,
    ),
}

# ACS B25075 (value of owner-occupied housing units) bracket lower edges in dollars.
VALUE_EDGES = [
    0,
    10000,
    15000,
    20000,
    25000,
    30000,
    35000,
    40000,
    50000,
    60000,
    70000,
    80000,
    90000,
    100000,
    125000,
    150000,
    175000,
    200000,
    250000,
    300000,
    400000,
    500000,
    750000,
    1000000,
    1500000,
    2000000,
]

# ACS B25063 (gross rent, renter-occupied units paying cash rent) bracket lower edges.
RENT_EDGES = [
    0,
    100,
    150,
    200,
    250,
    300,
    350,
    400,
    450,
    500,
    550,
    600,
    650,
    700,
    750,
    800,
    900,
    1000,
    1250,
    1500,
    2000,
    2500,
    3000,
    3500,
]

# Rent by number of bedrooms relative to the tract's median gross rent (ACS B25031).
BEDROOM_RATIOS = [0.78, 0.90, 1.05, 1.30]

# Statewide permit cycle: a multiplier on each county's typical yearly permits.
CYCLE = {
    1996: 0.95, 1997: 0.97, 1998: 1.03, 1999: 1.06, 2000: 1.04,
    2001: 1.05, 2002: 1.12, 2003: 1.20, 2004: 1.28, 2005: 1.32,
    2006: 1.18, 2007: 0.92, 2008: 0.62, 2009: 0.45, 2010: 0.47,
    2011: 0.44, 2012: 0.55, 2013: 0.63, 2014: 0.68, 2015: 0.74,
    2016: 0.78, 2017: 0.82, 2018: 0.86, 2019: 0.88, 2020: 0.90,
    2021: 1.02, 2022: 1.08, 2023: 1.05, 2024: 1.10, 2025: 1.14,
}  # fmt: skip

# Typical permitted units per 1,000 existing housing units per year, and the split
# across building sizes (1 unit, 2 units, 3-4 units, 5+ units), by area type.
PERMIT_RATE = {"urban": 9.0, "suburban": 12.0, "rural": 6.0}
PERMIT_SPLIT = {
    "urban": [0.35, 0.03, 0.04, 0.58],
    "suburban": [0.72, 0.02, 0.03, 0.23],
    "rural": [0.90, 0.03, 0.02, 0.05],
}


def value_columns() -> list[str]:
    """Return the home-value bracket column names, one per B25075 bracket."""
    return _bracket_names("value", VALUE_EDGES)


def rent_columns() -> list[str]:
    """Return the gross-rent bracket column names (B25063), plus the no-cash-rent count."""
    return [*_bracket_names("rent", RENT_EDGES), "rent_no_cash"]


def _bracket_names(prefix: str, edges: Sequence[int]) -> list[str]:
    """Name brackets by their dollar edges, for example ``value_10000_14999``."""
    names = [f"{prefix}_lt_{edges[1]}"]
    for low, high in itertools.pairwise(edges[1:]):
        names.append(f"{prefix}_{low}_{high - 1}")
    names.append(f"{prefix}_{edges[-1]}_plus")
    return names


TRACT_COLUMNS = [
    "geoid",
    "county",
    "area_type",
    "land_area_m2",
    "housing_units",
    "population",
    *STRUCTURE_COLUMNS,
    "households_owner",
    "households_renter",
    "vacant_units",
    "vacancy_rate",
    "median_household_income",
    "median_home_value",
    "median_gross_rent",
    "median_rent_b0",
    "median_rent_b1",
    "median_rent_b2",
    "median_rent_b3",
    "share_age_65plus",
    *value_columns(),
    *rent_columns(),
]


class Rng:
    """A seeded generator that uses only ``random.random()`` so output never drifts."""

    def __init__(self, seed: int) -> None:
        self._r = random.Random(seed)  # noqa: S311 - test fixtures, not security

    def uniform(self, low: float, high: float) -> float:
        """Return a float drawn evenly between ``low`` and ``high``."""
        return low + (high - low) * self._r.random()

    def jitter(self, spread: float) -> float:
        """Return a multiplier near 1, between ``1 - spread`` and ``1 + spread``."""
        return self.uniform(1.0 - spread, 1.0 + spread)

    def integer(self, low: int, high: int) -> int:
        """Return an integer from ``low`` to ``high`` inclusive."""
        return low + min(int(self._r.random() * (high - low + 1)), high - low)


def allocate(total: int, weights: Sequence[float]) -> list[int]:
    """Split a whole number into whole parts proportional to ``weights``.

    Uses the largest-remainder rule, so the parts always add up to ``total``. Ties go
    to the earlier position, which keeps the result deterministic.
    """
    scale = sum(weights)
    raw = [total * w / scale for w in weights]
    parts = [math.floor(x) for x in raw]
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - parts[i]), i))
    for i in order[: total - sum(parts)]:
        parts[i] += 1
    return parts


def lognormal_shares(median: float, sigma: float, edges: Sequence[int]) -> list[float]:
    """Return the share of a log-normal distribution that falls in each bracket."""

    def cdf(x: float) -> float:
        if x <= 0:
            return 0.0
        return 0.5 * (1.0 + math.erf(math.log(x / median) / (sigma * math.sqrt(2.0))))

    bounds = [cdf(e) for e in edges] + [1.0]
    return [hi - lo for lo, hi in itertools.pairwise(bounds)]


def land_area_m2(west: float, south: float, east: float, north: float) -> int:
    """Return the area of a small longitude/latitude rectangle in square metres."""
    metres_per_degree = 111_320.0
    mid_lat = math.radians((south + north) / 2.0)
    width = (east - west) * metres_per_degree * math.cos(mid_lat)
    height = (north - south) * metres_per_degree
    return round(width * height)


def grid(box: Sequence[float], cols: int, rows: int) -> list[tuple[float, ...]]:
    """Split a rectangle into ``cols`` x ``rows`` cells, row by row from the south-west."""
    west, south, east, north = box
    dx, dy = (east - west) / cols, (north - south) / rows
    cells = []
    for r in range(rows):
        for c in range(cols):
            cells.append(
                tuple(
                    round(v, 6)
                    for v in (
                        west + c * dx,
                        south + r * dy,
                        west + (c + 1) * dx,
                        south + (r + 1) * dy,
                    )
                )
            )
    return cells


def build(seed: int = SEED) -> dict[str, object]:
    """Generate every Testland table in memory and return them by file name."""
    rng = Rng(seed)
    counties, tracts, blocks, shapes = [], [], [], []

    for code, name, area, n_tracts, (cols, rows), box in COUNTIES:
        county_geoid = STATE_FIPS + code
        prof = PROFILES[area]
        shapes.append(("county", county_geoid, name, box))
        county_pop = county_hu = 0
        for t, cell in enumerate(grid(box, cols, rows)[:n_tracts]):
            geoid = f"{county_geoid}{(t + 1) * 100:06d}"
            row = _tract_row(rng, geoid, county_geoid, area, prof, cell)
            tracts.append(row)
            shapes.append(("tract", geoid, f"Tract {t + 1}", cell))
            county_pop += row["population"]
            county_hu += row["housing_units"]
            blocks.extend(_block_rows(rng, geoid, area, row["housing_units"], cell))
        counties.append(
            {
                "geoid": county_geoid,
                "name": name,
                "population": county_pop,
                "housing_units": county_hu,
            }
        )

    permits = _permit_rows(rng, counties)
    stops = _stop_rows(rng)
    bands = _band_rows(Rng(seed + 1), tracts)
    extra = Rng(seed + 2)
    infra = _infra_rows(extra, tracts)
    hazard = _hazard_rows(extra, tracts)
    parcels = _parcel_rows(extra, tracts)
    limits = _income_limit_rows(extra, tracts)
    return {
        "counties.csv": _csv(["geoid", "name", "population", "housing_units"], counties),
        "tracts.csv": _csv(TRACT_COLUMNS, tracts),
        "blocks.csv": _csv(
            ["geoid", "tract", "housing_units", "land_area_m2", "lat", "lon"], blocks
        ),
        "permits_county_year.csv": _csv(
            ["county", "year", "units_1", "units_2", "units_3_4", "units_5plus", "imputed"],
            permits,
        ),
        "transit_stops.csv": _csv(["stop_id", "lat", "lon", "mode", "weekday_departures"], stops),
        "boundaries.geojson": _geojson(shapes),
        "data_dictionary.json": _dictionary(),
        "acs_bands.csv": _csv(["geoid", "level", *_band_names()], bands),
        "infra_tract.csv": _csv(list(_infra().TRACT_COLUMNS), infra),
        "hazard_tract.csv": _csv(list(_hazards().TRACT_COLUMNS), hazard),
        "parcels_area.csv": _csv(_parcel_columns(), parcels),
        "afford_income_limits.csv": _csv(_limit_columns(), limits),
    }


def _tract_row(rng: Rng, geoid: str, county: str, area: str, prof: dict, cell) -> dict:
    """Make one tract's housing, tenure, price and age fields."""
    hu = rng.integer(*prof["hu"])
    shares = [s * rng.jitter(0.25) for s in STRUCTURE_SHARES[area]]
    structure = allocate(hu, shares)

    vacancy = prof["vacancy"] * rng.jitter(0.35)
    occupied = round(hu * (1.0 - vacancy))
    renter_share = min(0.9, prof["renter"] * rng.jitter(0.35))
    renters = round(occupied * renter_share)
    owners = occupied - renters

    income = round(prof["income"] * rng.jitter(0.30), -2)
    value = round(prof["value"] * rng.jitter(0.30), -3)
    rent = round(prof["rent"] * rng.jitter(0.20))

    value_counts = allocate(owners, lognormal_shares(value, 0.6, VALUE_EDGES))
    no_cash = round(renters * rng.uniform(0.02, 0.06))
    rent_counts = allocate(renters - no_cash, lognormal_shares(rent, 0.4, RENT_EDGES))

    row = {
        "geoid": geoid,
        "county": county,
        "area_type": area,
        "land_area_m2": land_area_m2(*cell),
        "housing_units": hu,
        "population": round(occupied * prof["hh"] * rng.jitter(0.08)),
        **dict(zip(STRUCTURE_COLUMNS, structure, strict=True)),
        "households_owner": owners,
        "households_renter": renters,
        "vacant_units": hu - occupied,
        "vacancy_rate": f"{(hu - occupied) / hu:.4f}",
        "median_household_income": int(income),
        "median_home_value": int(value),
        "median_gross_rent": rent,
    }
    for b, ratio in enumerate(BEDROOM_RATIOS):
        row[f"median_rent_b{b}"] = round(rent * ratio * rng.jitter(0.05))
    row["share_age_65plus"] = f"{prof['age65'] * rng.jitter(0.25):.4f}"
    row.update(zip(value_columns(), value_counts, strict=True))
    row.update(zip(rent_columns(), [*rent_counts, no_cash], strict=True))
    return row


# Collapsed-band weights by area type (bands in the order of visionhousing.features.acs_bands).
BAND_WEIGHTS = {
    "age": {"urban": [0.20, 0.12, 0.18, 0.25, 0.12], "suburban": [0.23, 0.08, 0.12, 0.27, 0.14],
            "rural": [0.20, 0.08, 0.11, 0.25, 0.15]},
    "race": {"urban": [0.40, 0.35, 0.07, 0.13, 0.04, 0.01],
             "suburban": [0.78, 0.06, 0.06, 0.07, 0.025, 0.005],
             "rural": [0.92, 0.02, 0.01, 0.03, 0.015, 0.005]},
    "hhsize": {"urban": [0.36, 0.31, 0.15, 0.11, 0.07], "suburban": [0.24, 0.36, 0.16, 0.15, 0.09],
               "rural": [0.28, 0.38, 0.14, 0.12, 0.08]},
    "edu": {"urban": [0.13, 0.32, 0.24, 0.18, 0.13], "suburban": [0.06, 0.26, 0.25, 0.25, 0.18],
            "rural": [0.10, 0.44, 0.26, 0.12, 0.08]},
    "commute": {"urban": [0.52, 0.08, 0.20, 0.10, 0.03, 0.07],
                "suburban": [0.76, 0.07, 0.03, 0.02, 0.02, 0.10],
                "rural": [0.80, 0.10, 0.005, 0.03, 0.025, 0.04]},
    "built": {"urban": [0.06, 0.05, 0.09, 0.20, 0.25, 0.35],
              "suburban": [0.12, 0.13, 0.25, 0.25, 0.15, 0.10],
              "rural": [0.08, 0.10, 0.25, 0.22, 0.13, 0.22]},
    "burden": {"urban": [0.48, 0.24, 0.23, 0.05], "suburban": [0.55, 0.22, 0.18, 0.05],
               "rural": [0.56, 0.20, 0.18, 0.06]},
    "vacancy": {"urban": [0.30, 0.10, 0.08, 0.04, 0.48], "suburban": [0.25, 0.18, 0.10, 0.12, 0.35],
                "rural": [0.12, 0.12, 0.05, 0.40, 0.31]},
}  # fmt: skip
INCOME_EDGES = [0, 25000, 50000, 75000, 100000, 150000]


def _band_names() -> list[str]:
    from visionhousing.features import acs_bands as ab

    cols = ab.band_columns()
    return [*cols, *(f"{c}_moe" for c in cols)]


def _moe(value: float) -> int:
    """A made-up margin of error that grows with the square root of the count."""
    return round(4.0 * math.sqrt(max(value, 0.0))) + 6


def _band_rows(rng: Rng, tracts: list[dict]) -> list[dict]:
    """Collapsed survey bands for every tract, county and the state (synthetic)."""
    from visionhousing.features import acs_bands as ab

    full: list[dict] = []
    for t in tracts:
        area = t["area_type"]
        pop, hu = t["population"], t["housing_units"]
        owners, renters = t["households_owner"], t["households_renter"]
        hh = owners + renters
        share65 = float(t["share_age_65plus"])
        older = round(pop * share65)
        counts: dict[str, list[int]] = {}
        counts["age"] = [
            *allocate(pop - older, [w * rng.jitter(0.15) for w in BAND_WEIGHTS["age"][area]]),
            older,
        ]
        for key, total in (
            ("race", pop),
            ("hhsize", hh),
            ("edu", round(pop * 0.68)),
            ("commute", round(pop * 0.47)),
            ("built", hu),
            ("burden", renters),
            ("vacancy", t["vacant_units"]),
        ):
            weights = [w * rng.jitter(0.2) for w in BAND_WEIGHTS[key][area]]
            counts[key] = allocate(total, weights)
        income = float(t["median_household_income"])
        counts["income"] = allocate(hh, lognormal_shares(income, 0.75, INCOME_EDGES))
        counts["tenure"] = [owners, renters]
        u = [t[c] for c in STRUCTURE_COLUMNS]
        counts["units"] = [u[0], u[1], u[2] + u[3], u[4] + u[5], u[6], u[7]]
        row: dict[str, float] = {}
        for g in ab.GROUPS:
            values = counts[g.key]
            row[g.column("total")] = sum(values)
            row[f"{g.column('total')}_moe"] = _moe(sum(values)) // 3
            for band, v in zip(g.bands, values, strict=True):
                row[g.column(band.key)] = v
                row[f"{g.column(band.key)}_moe"] = _moe(v)
        full.append({"geoid": t["geoid"], "county": t["county"], "row": row,
                     "income": income, "rent": float(t["median_gross_rent"]),
                     "hh": hh, "renters": renters})  # fmt: skip

    def combine(level: str, geoid: str, members: list[dict]) -> dict:
        out: dict[str, object] = {"geoid": geoid, "level": level}
        for name in ab.band_columns():
            if name in ab.MEDIANS:
                weight = "hh" if name == "median_household_income" else "renters"
                key = "income" if name == "median_household_income" else "rent"
                total = sum(m[weight] for m in members)
                value = sum(m[key] * m[weight] for m in members) / total
                out[name] = int(round(value, -2)) if key == "income" else round(value)
                continue
            out[name] = sum(m["row"][name] for m in members)
        for name in ab.band_columns():
            if name in ab.MEDIANS:
                out[f"{name}_moe"] = round(float(out[name]) * 0.02)  # type: ignore[arg-type]
            else:
                out[f"{name}_moe"] = round(math.sqrt(sum(m["row"][f"{name}_moe"] ** 2
                                                         for m in members)))  # fmt: skip
        return out

    rows = [combine("state", STATE_FIPS, full)]
    for code, *_ in COUNTIES:
        county = STATE_FIPS + code
        rows.append(combine("county", county, [m for m in full if m["county"] == county]))
    kept = {c for g in ab.GROUPS if g.key in ab.TRACT_GROUPS for c in g.columns()}
    for m in full:
        row: dict[str, object] = {"geoid": m["geoid"], "level": "tract"}
        for name in ab.band_columns():
            if name in kept:
                row[name] = m["row"][name]
                row[f"{name}_moe"] = m["row"][f"{name}_moe"]
            else:
                row[name] = row[f"{name}_moe"] = ""
        rows.append(row)
    return rows


# ------------------------------------------------------------------ optional extra tables
#
# Four small tables the extra scenarios and the practical capacity screen read when a pack
# has them (``visionhousing.scenarios.extra_common.OPTIONAL_TABLES``). They are made by the
# rules below from a third generator seeded with ``SEED + 2``, so adding them left every
# earlier file unchanged. Each one goes through the same table function a real pack uses
# where one exists (readiness and hazards), so the columns and their meaning match.

# Water and sewer readiness, per county: how the made-up official record covers it.
# "statement": one utility serves the whole county; "map": a county service-area map
# (homes inside it verified, the rest outside); "none": no record (every home unknown).
SEWER_RECORD = {"001": "statement", "003": "map", "005": "none", "007": "map"}
SEWER_IN = {"003": (0.72, 0.97), "007": (0.05, 0.35)}  # share of homes inside the map
CONSTRAINT_TRACT = ("003", 3)  # one Maple tract (4th) has a documented moratorium area
CONSTRAINT_SHARE = (0.30, 0.50)  # share of that tract's homes under the moratorium

# Hazards, by area type: developed share of land, mean impervious percent of developed
# cells, steep share of land, and the usual share of homes in the flood zone. One tract in
# five sits on a river (FLOOD_RIVER). Half of Prairie County has no digital flood map.
DEVELOPED = {"urban": (0.85, 0.98), "suburban": (0.45, 0.75), "rural": (0.05, 0.20)}
IMPERVIOUS = {"urban": (55, 70), "suburban": (30, 45), "rural": (20, 35)}
STEEP = {"urban": (0.00, 0.03), "suburban": (0.02, 0.12), "rural": (0.03, 0.20)}
FLOOD = (0.00, 0.06)
FLOOD_RIVER = (0.10, 0.25)
FLOOD_UNMAPPED_COUNTY = "007"

# Parcel land use: measured in Harbor and Maple counties only (the others stay "not
# measured", as most real counties are). Parcels per home and land-use class weights
# (order of assumptions/parcels.yaml land_use_classes), by area type.
PARCEL_COUNTIES = ("001", "003")
PARCELS_PER_HOME = {"urban": 0.75, "suburban": 0.85}
LAND_USE_WEIGHTS = {
    "urban": [0.72, 0.08, 0.02, 0.03, 0.03, 0.06, 0.01, 0.03, 0.01, 0.00, 0.01],
    "suburban": [0.80, 0.03, 0.01, 0.03, 0.01, 0.04, 0.01, 0.04, 0.01, 0.01, 0.01],
}
LOT_SQFT = {"urban": (1100, 2000), "suburban": (6000, 14000)}
BUILT_WEIGHTS = {"urban": [0.45, 0.40, 0.08, 0.07], "suburban": [0.08, 0.37, 0.30, 0.25]}

# Income limits: HUD-style areas (code, name, counties). Area median family income =
# the area's household-weighted median household income x a drawn ratio in FAMILY_RATIO;
# the 4-person 50% limit is half of it, the 30% and 80% limits are 0.6x and 1.6x the 50%
# limit, other sizes use HUD's family-size factors, and every limit is rounded up to $50.
HUD_AREAS = [
    ("METRO99900M99900", "Harbor-Maple, TL Metro Area (synthetic)", ("001", "003")),
    ("METRO99910M99910", "Brookside, TL Metro Area (synthetic)", ("005",)),
    ("NCNTY99007N99007", "Prairie County, TL (synthetic)", ("007",)),
]
FAMILY_RATIO = (1.15, 1.35)
SIZE_FACTORS = [0.70, 0.80, 0.90, 1.00, 1.08, 1.16, 1.24, 1.32]
LIMIT_MULTIPLES = {30: 0.6, 50: 1.0, 80: 1.6}


def _infra():
    from visionhousing.features import infrastructure

    return infrastructure


def _hazards():
    from visionhousing.features import hazards

    return hazards


def _parcel_columns() -> list[str]:
    from visionhousing.features import parcels

    return parcels.parcel_columns(parcels.load_rules())


def _limit_columns() -> list[str]:
    from visionhousing.features.affordability import LIMIT_COLUMNS

    return ["county", "hud_area_code", "hud_area_name", "areas_in_county", "median",
            *LIMIT_COLUMNS]  # fmt: skip


def _cell(value: object) -> object:
    """A table cell for the CSV: blank for a missing value, plain Python numbers otherwise."""
    if value is None:
        return ""
    if isinstance(value, float) or hasattr(value, "dtype"):
        f = float(value)  # type: ignore[arg-type]
        if math.isnan(f):
            return ""
        return int(f) if f.is_integer() and not isinstance(value, float) else round(f, 3)
    return value


def _records(frame) -> list[dict]:
    """Rows of a pandas table as plain dictionaries for :func:`_csv`."""
    return [{k: _cell(v) for k, v in row.items()} for row in frame.to_dict("records")]


def _infra_rows(rng: Rng, tracts: list[dict]) -> list[dict]:
    """``infra_tract``: made-up sewer answers per tract, summed by the real table function.

    Water and EPA sewershed shares are drawn per tract and written in afterwards (the
    table function would need them block by block).
    """
    import pandas as pd

    fx = _infra()
    groups, water, epa = [], {}, {}
    for t in _county_order(tracts):
        code, homes = t["county"][2:], t["housing_units"]
        record = SEWER_RECORD[code]
        water[t["geoid"]] = (
            round(rng.uniform(0.85, 1.0), 3) if record != "none" else None
        )  # no statewide water map where the county has no record
        epa[t["geoid"]] = round(rng.uniform(0.6, 0.95), 3)
        if record == "statement":
            parts = {"in": homes}
        elif record == "map":
            inside = round(homes * rng.uniform(*SEWER_IN[code]))
            parts = {"in": inside, "out": homes - inside}
            if (code, t["index"]) == CONSTRAINT_TRACT:
                held = round(inside * rng.uniform(*CONSTRAINT_SHARE))
                parts = {"in": inside - held, "held": held, "out": homes - inside}
        else:
            parts = {"unknown": homes}
        for kind, units in parts.items():
            groups.append(
                {
                    "tract": t["geoid"],
                    "county": t["county"],
                    "units": units,
                    "sewer_in": kind in ("in", "held"),
                    "sewer_known": kind != "unknown",
                    "constraint": kind == "held",
                    "sewer_source": "synthetic" if kind != "unknown" else "",
                    "water_in": False,
                    "water_known": False,
                    "epa_in": False,
                }
            )
    table = fx.tract_table(
        pd.DataFrame(groups), [t["geoid"] for t in tracts], rules=fx.load_rules()
    )
    table["water_share"] = table["geoid"].map(water)
    table["epa_share"] = table["geoid"].map(epa)
    return _records(table)


def _county_order(tracts: list[dict]) -> list[dict]:
    """Tracts with their position inside their county (``index``), in file order."""
    seen: dict[str, int] = {}
    out = []
    for t in tracts:
        k = seen.get(t["county"], 0)
        seen[t["county"]] = k + 1
        out.append({**t, "index": k})
    return out


def _hazard_rows(rng: Rng, tracts: list[dict]) -> list[dict]:
    """``hazard_tract``: made-up 30 m cell and home counts, through the real table function."""
    import pandas as pd

    hz = _hazards()
    rows = []
    for t in _county_order(tracts):
        area, homes = t["area_type"], t["housing_units"]
        land = round(t["land_area_m2"] / 900.0)
        developed = round(land * rng.uniform(*DEVELOPED[area]))
        impervious = developed * rng.uniform(*IMPERVIOUS[area])
        steep = round(land * rng.uniform(*STEEP[area]))
        river = rng.uniform(0, 1) < 0.2
        flood = rng.uniform(*(FLOOD_RIVER if river else FLOOD))
        mapped = not (t["county"][2:] == FLOOD_UNMAPPED_COUNTY and t["index"] % 2 == 1)
        rows.append(
            {
                "geoid": t["geoid"],
                "homes": homes,
                "land_cells": land,
                "slope_cells": land,
                "steep_cells": steep,
                "developed_cells": developed,
                "impervious_cells": land,
                "impervious_sum": round(impervious),
                "nfhl_cells": land if mapped else 0,
                "nfhl_homes": homes if mapped else 0,
                "nfhl_developed_cells": developed if mapped else 0,
                "sfha_homes": round(homes * flood) if mapped else 0,
                "sfha_developed_cells": round(developed * flood * rng.jitter(0.2)) if mapped else 0,
            }
        )
    table = hz.tract_table(pd.DataFrame(rows), hz.load_rules())
    return _records(table)


def _parcel_rows(rng: Rng, tracts: list[dict]) -> list[dict]:
    """``parcels_area``: made-up parcel counts for the measured counties, tract and county rows."""
    from visionhousing.features import parcels as pc

    rules = pc.load_rules()
    classes = list(rules["land_use_classes"])
    groups = list(rules["year_built_groups"])
    rows: list[dict] = []
    for code in PARCEL_COUNTIES:
        county = STATE_FIPS + code
        mine = []
        for t in tracts:
            if t["county"] != county:
                continue
            area = t["area_type"]
            n = round(t["housing_units"] * PARCELS_PER_HOME[area] * rng.jitter(0.1))
            weights = [w * rng.jitter(0.25) for w in LAND_USE_WEIGHTS[area]]
            by_class = dict(zip(classes, allocate(n, weights), strict=True))
            sf = by_class["single_family"]
            unknown = round(sf * rng.uniform(0.0, 0.02))
            built = allocate(sf - unknown, [w * rng.jitter(0.2) for w in BUILT_WEIGHTS[area]])
            value = round(t["median_home_value"] * rng.jitter(0.1), -3)
            short = round(sf * rng.uniform(0.03, 0.05))
            sale = round(value * rng.uniform(1.0, 1.2), -3)
            mine.append(
                {
                    "level": "tract",
                    "geoid": t["geoid"],
                    "county": county,
                    "as_of": "synthetic",
                    "value_basis": "synthetic market value",
                    "parcels": n,
                    **{f"lu_{c}": by_class[c] for c in classes},
                    "sf_parcels": sf,
                    "sf_median_value": int(value),
                    "sf_median_lot_sqft": int(round(rng.uniform(*LOT_SQFT[area]), -1)),
                    **{f"sf_built_{g}": b for g, b in zip(groups, built, strict=True)},
                    "sf_built_unknown": unknown,
                    "sales_short": short,
                    "sales_long": round(short * rng.uniform(2.7, 3.1)),
                    "median_sale_short": int(sale),
                    "median_sale_long": int(round(sale * rng.uniform(0.88, 0.95), -3)),
                    "unplaced": "",
                }
            )
        total: dict[str, object] = {
            "level": "county",
            "geoid": county,
            "county": county,
            "as_of": "synthetic",
            "value_basis": "synthetic market value",
        }
        counts = [
            "parcels",
            *(f"lu_{c}" for c in classes),
            "sf_parcels",
            *(f"sf_built_{g}" for g in groups),
            "sf_built_unknown",
            "sales_short",
            "sales_long",
        ]
        for c in counts:
            total[c] = sum(int(r[c]) for r in mine)
        # County "medians" are the parcel-weighted means of the tract medians (synthetic).
        weights = (
            ("sf_median_value", "sf_parcels"),
            ("sf_median_lot_sqft", "sf_parcels"),
            ("median_sale_short", "sales_short"),
            ("median_sale_long", "sales_long"),
        )
        for c, w in weights:
            mean = sum(r[c] * r[w] for r in mine) / sum(r[w] for r in mine)
            total[c] = int(round(mean, -1 if c == "sf_median_lot_sqft" else -3))
        total["unplaced"] = rng.integer(0, 30)
        rows.append(total)
        rows.extend(mine)
    return rows


def _income_limit_rows(rng: Rng, tracts: list[dict]) -> list[dict]:
    """``afford_income_limits``: HUD-style limits per county, through the real table function."""
    import pandas as pd

    from visionhousing.features.affordability import income_limits_table

    raw = []
    for code, name, members in HUD_AREAS:
        mine = [t for t in tracts if t["county"][2:] in members]
        hh = [t["households_owner"] + t["households_renter"] for t in mine]
        income = sum(t["median_household_income"] * h for t, h in zip(mine, hh, strict=True))
        median = round(income / sum(hh) * rng.uniform(*FAMILY_RATIO), -2)
        for county in members:
            row: dict[str, object] = {"county": STATE_FIPS + county, "hud_area_code": code,
                                      "hud_area_name": name, "median": int(median)}  # fmt: skip
            for pct, multiple in LIMIT_MULTIPLES.items():
                for size, factor in enumerate(SIZE_FACTORS, start=1):
                    value = median * 0.5 * multiple * factor
                    row[f"l{pct}_{size}"] = int(math.ceil(value / 50.0) * 50)
            raw.append(row)
    return _records(income_limits_table(pd.DataFrame(raw)))


def _block_rows(rng: Rng, tract: str, area: str, hu: int, cell) -> list[dict]:
    """Split a tract into a 5 x 2 grid of blocks whose housing units sum to the tract."""
    weights = []
    for _ in range(BLOCKS_PER_TRACT):
        empty = area == "rural" and rng.uniform(0, 1) < 0.2
        weights.append(0.0 if empty else rng.uniform(0.3, 1.7))
    units = allocate(hu, weights)
    rows = []
    for b, (bcell, n) in enumerate(zip(grid(cell, 5, 2), units, strict=True)):
        west, south, east, north = bcell
        rows.append(
            {
                "geoid": f"{tract}{b // 5 + 1}{b % 5 + 1:03d}",
                "tract": tract,
                "housing_units": n,
                "land_area_m2": land_area_m2(*bcell),
                "lat": f"{(south + north) / 2:.6f}",
                "lon": f"{(west + east) / 2:.6f}",
            }
        )
    return rows


def _permit_rows(rng: Rng, counties: list[dict]) -> list[dict]:
    """Make yearly permitted units by building size for every county and year."""
    area_by_geoid = {STATE_FIPS + c[0]: c[2] for c in COUNTIES}
    rows = []
    for county in counties:
        area = area_by_geoid[county["geoid"]]
        typical = county["housing_units"] * PERMIT_RATE[area] / 1000.0
        for year in YEARS:
            total = round(typical * CYCLE[year] * rng.jitter(0.15))
            split = [s * rng.jitter(0.30) for s in PERMIT_SPLIT[area]]
            parts = allocate(total, split)
            # The rural county did not report every month in its early years; the
            # real permit survey marks such county-years as imputed.
            imputed = int(area == "rural" and (year <= 1998 or rng.uniform(0, 1) < 0.1))
            rows.append(
                {
                    "county": county["geoid"],
                    "year": year,
                    "units_1": parts[0],
                    "units_2": parts[1],
                    "units_3_4": parts[2],
                    "units_5plus": parts[3],
                    "imputed": imputed,
                }
            )
    return rows


def _stop_rows(rng: Rng) -> list[dict]:
    """Make 30 transit stops: a rail line through the urban county, and buses."""
    rows = []
    # Rail: 8 stations on an east-west line through the urban and one suburban county.
    for i in range(8):
        rows.append(
            {
                "stop_id": f"TL-R{i + 1:02d}",
                "lat": f"{30.60 + rng.uniform(-0.01, 0.01):.6f}",
                "lon": f"{-59.20 + i * 0.05:.6f}",
                "mode": "rail",
                "weekday_departures": rng.integer(60, 150),
            }
        )
    # Bus: 22 stops, most in the urban county, the rest in the suburbs.
    bus_boxes = [COUNTIES[0][5]] * 14 + [COUNTIES[1][5]] * 4 + [COUNTIES[2][5]] * 4
    for i, (west, south, east, north) in enumerate(bus_boxes):
        rows.append(
            {
                "stop_id": f"TL-B{i + 1:02d}",
                "lat": f"{rng.uniform(south, north):.6f}",
                "lon": f"{rng.uniform(west, east):.6f}",
                "mode": "bus",
                "weekday_departures": rng.integer(20, 120),
            }
        )
    return rows


def _csv(columns: list[str], rows: list[dict]) -> bytes:
    """Write rows as CSV text with a header and Unix line endings."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _geojson(shapes: list[tuple]) -> bytes:
    """Write county and tract rectangles as a GeoJSON feature collection."""
    features = []
    for level, geoid, name, (west, south, east, north) in shapes:
        ring = [[west, south], [east, south], [east, north], [west, north], [west, south]]
        features.append(
            {
                "type": "Feature",
                "properties": {"level": level, "geoid": geoid, "name": name},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    doc = {"type": "FeatureCollection", "name": f"{STATE_CODE} boundaries", "features": features}
    return (json.dumps(doc, separators=(",", ":")) + "\n").encode("utf-8")


def _dictionary() -> bytes:
    """Describe every column of every table: name, unit, description, source, tier."""
    entries: list[dict[str, str]] = []

    def add(table: str, name: str, unit: str, description: str) -> None:
        entries.append(
            {
                "table": table,
                "name": name,
                "unit": unit,
                "description": description,
                "source": "synthetic",
                "tier": "0",
            }
        )

    add("counties", "geoid", "code", "County code: 2-digit state code + 3-digit county code")
    add("counties", "name", "text", "County name")
    add("counties", "population", "persons", "Residents; the sum of the county's tracts")
    add("counties", "housing_units", "units", "Housing units; the sum of the county's tracts")

    add("tracts", "geoid", "code", "Tract code: 5-digit county code + 6-digit tract code")
    add("tracts", "county", "code", "Code of the county that contains the tract")
    add("tracts", "area_type", "category", "urban, suburban or rural")
    add("tracts", "land_area_m2", "square metres", "Land area")
    add("tracts", "housing_units", "units", "All housing units, occupied and vacant")
    add("tracts", "population", "persons", "Residents")
    labels = ["1 unit, detached", "1 unit, attached", "2 units", "3 or 4 units",
              "5 to 9 units", "10 to 19 units", "20 or more units", "mobile home"]  # fmt: skip
    for col, label in zip(STRUCTURE_COLUMNS, labels, strict=True):
        add("tracts", col, "units", f"Housing units in structures of type: {label} (as B25024)")
    add("tracts", "households_owner", "households", "Owner-occupied housing units")
    add("tracts", "households_renter", "households", "Renter-occupied housing units")
    add("tracts", "vacant_units", "units", "Vacant housing units")
    add("tracts", "vacancy_rate", "share", "Vacant units / housing units")
    add("tracts", "median_household_income", "dollars per year", "Median household income")
    add("tracts", "median_home_value", "dollars", "Median value of owner-occupied units")
    add("tracts", "median_gross_rent", "dollars per month", "Median gross rent (as B25064)")
    for b in range(4):
        rooms = f"{b}{'+' if b == 3 else ''}"
        text = f"Median gross rent, units with {rooms} bedrooms (as B25031)"
        add("tracts", f"median_rent_b{b}", "dollars per month", text)
    add("tracts", "share_age_65plus", "share", "Share of residents aged 65 or older")
    for col in value_columns():
        add("tracts", col, "households", f"Owner-occupied units in value bracket {col[6:]} dollars"
            " (B25075 edges); brackets sum to households_owner")  # fmt: skip
    for col in rent_columns()[:-1]:
        add("tracts", col, "households", f"Renter units paying gross rent {col[5:]} dollars a"
            " month (B25063 edges)")  # fmt: skip
    add("tracts", "rent_no_cash", "households", "Renter units paying no cash rent; rent "
        "brackets plus this sum to households_renter")  # fmt: skip

    add("blocks", "geoid", "code", "Block code: 11-digit tract code + 4-digit block code")
    add("blocks", "tract", "code", "Code of the tract that contains the block")
    add("blocks", "housing_units", "units", "Housing units; blocks sum to their tract")
    add("blocks", "land_area_m2", "square metres", "Land area")
    add("blocks", "lat", "degrees", "Latitude of the block centre")
    add("blocks", "lon", "degrees", "Longitude of the block centre")

    add("permits_county_year", "county", "code", "County code")
    add("permits_county_year", "year", "year", "Calendar year of authorization")
    sizes = [("units_1", "1-unit"), ("units_2", "2-unit"),
             ("units_3_4", "3- and 4-unit"), ("units_5plus", "5-or-more-unit")]  # fmt: skip
    for col, label in sizes:
        add("permits_county_year", col, "units", f"Units authorized in {label} buildings")
    add("permits_county_year", "imputed", "flag", "1 if the county-year was partly estimated")

    add("transit_stops", "stop_id", "code", "Stop identifier")
    add("transit_stops", "lat", "degrees", "Latitude")
    add("transit_stops", "lon", "degrees", "Longitude")
    add("transit_stops", "mode", "category", "rail or bus")
    add("transit_stops", "weekday_departures", "departures per day", "Scheduled weekday trips")

    from visionhousing.features import acs_bands as ab

    for name in ["geoid", "level", *_band_names()]:
        unit, text = ab.describe(name)
        add("acs_bands", name, unit, text)

    # The four optional tables reuse the real pack's column descriptions, marked synthetic.
    from visionhousing.data import local as local_tables
    from visionhousing.features import affordability as af

    tag = " (Testland: synthetic, made by the generator's stated rules)"
    for table, columns, describe in (
        ("infra_tract", _infra().TRACT_COLUMNS, _infra().describe),
        ("hazard_tract", _hazards().TRACT_COLUMNS, _hazards().describe),
        ("parcels_area", _parcel_columns(), local_tables.describe),
        ("afford_income_limits", _limit_columns(), af.describe),
    ):
        for name in columns:
            unit, text = describe(table, name)
            add(table, name, unit, text + tag)

    add("boundaries", "level", "category", "county or tract")
    add("boundaries", "geoid", "code", "Code of the county or tract")
    add("boundaries", "name", "text", "Display name")

    doc = {"state": STATE_CODE, "synthetic": True, "seed": SEED, "columns": entries}
    return (json.dumps(doc, indent=1, ensure_ascii=True) + "\n").encode("utf-8")


def write(out: Path, seed: int = SEED) -> list[Path]:
    """Write every Testland file into ``out`` and return the written paths."""
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, data in build(seed).items():
        path = out / name
        path.write_bytes(data)
        paths.append(path)
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``--out`` and write the Testland files there."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/testland"))
    args = parser.parse_args(argv)
    paths = write(args.out)
    total = sum(p.stat().st_size for p in paths)
    print(f"Testland: wrote {len(paths)} files, {total:,} bytes, to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
