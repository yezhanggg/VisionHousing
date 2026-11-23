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
