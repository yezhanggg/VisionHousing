"""Build the demo state: a tiny, made-up state for the decision-demo reconciliation test.

What it is: the demo state (state code "DM", made-up state number "98") is a synthetic
state with two counties, six tracts and 54 blocks. Nothing in it is real. It is separate
from Testland (which stays byte-identical) and holds the hard cases a public decision
demo must survive, each made by a stated rule below:

1. **Two overlapping transit districts.** Two rail stations about 0.4 mile apart, so
   their half-mile station areas overlap. Land near either station must be counted once.
2. **Four existing legal units.** One station-area tract already allows four homes by
   right (pack table ``mbta_3a_capacity_tract``, the engine's generic "already zoned by
   right near transit" table). The transit reform may only add capacity above them:
   additional legal capacity = max(0, proposed - existing).
3. **A service area with unknown connection capacity.** One Valley County tract sits in
   a sewer service area whose connection capacity no record states. Its readiness is
   ``unknown`` (``infra_tract``), never zero and never "verified".
4. **One appeal.** The dated test law change (below) is under appeal: its timeline record
   has ``court_status: challenged`` and no stay, so it is in force while the appeal runs.
5. **A no-change parking minimum.** Valley township (all of Valley County) already has no
   parking minimum, so a state parking reform changes nothing there.
6. **One unobserved permit stage.** ``permit_stages.csv`` lists the approval path; the
   application stage has no published date, so it is unobserved (blank, not zero).

The **frozen law snapshot** (``muni_zoning_rules.csv``, read 2026-07-15, and
``law_timeline.yaml``) holds the original rules. The **dated test law change**
(``law_change.yaml``) cuts Ridge city's parking minimum to zero from 2026-09-01.
:func:`apply_law_change` writes a copy of the state with the change applied, so a test
can show the original rule beside the revised result.

How it stays identical run to run: one fixed seed drives the standard-library Mersenne
Twister through Testland's ``Rng`` (only ``random()`` is used); floats have fixed
decimals; rows are in a fixed order; gzip files carry no time stamp.

Usage: ``uv run python scripts/make_demo_fixture.py --out tests/fixtures/demo_state``.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import yaml

import make_testland as tl

SEED = 20261126
STATE_CODE = "DM"
STATE_FIPS = "98"  # not a real state code
YEARS = list(range(1996, 2026))
SNAPSHOT_READ_ON = "2026-07-15"  # the frozen law snapshot was read on this date

# Counties: (county code, name, area type, bounding box west, south, east, north).
# Three tracts each, side by side; each tract a 3 x 3 grid of blocks. The box sits in
# open ocean, away from Testland's box, so it cannot be mistaken for a real place.
COUNTIES = [
    ("001", "Ridge County", "urban", (-45.000, 20.000, -44.952, 20.030)),
    ("003", "Valley County", "suburban", (-44.952, 20.000, -44.904, 20.030)),
]
TRACTS_PER_COUNTY = 3
BLOCK_GRID = (3, 3)

# Area profiles: Testland's, with higher rents, values and incomes, so the transit and
# missing-middle reforms add homes in some draws (on Testland's rents they add none).
PROFILES = {
    "urban": {**tl.PROFILES["urban"], "rent": 2700, "value": 560000, "income": 105000},
    "suburban": {**tl.PROFILES["suburban"], "rent": 2200, "value": 480000, "income": 118000},
}

# Two rail stations 0.006 degrees of longitude apart (about 0.39 mile at this latitude):
# their half-mile station areas overlap. One bus stop below the major-stop frequency.
STOPS = [
    ("DM-R01", 20.0150, -44.9870, "rail", (100, 140)),
    ("DM-R02", 20.0150, -44.9810, "rail", (100, 140)),
    ("DM-B01", 20.0100, -44.9300, "bus", (10, 20)),
]

# The tract (county, position) that already allows four homes by right near transit.
EXISTING_TRACT = ("001", 0)
EXISTING_UNITS = 4
EXISTING_ACRES = 1.2

# Sewer records: Ridge County's utility states it serves everyone; Valley County has a
# service-area map, but one tract's service area has no stated connection capacity.
SEWER_IN = (0.80, 0.95)  # share of a mapped Valley tract's homes inside the map
UNKNOWN_TRACT = ("003", 2)

# District cuts (blocks are placed by their centre). Upper chamber: two bands of latitude.
# Lower chamber: three bands of longitude, the middle one crossing the county line.
UPPER_CUT_LAT = 20.012
LOWER_CUTS_LON = (-44.968, -44.936)

# Municipalities: (county, cousub, name, positions of the county's tracts it holds).
# Each tract lies in one municipality, so residents and homes add up exactly.
MUNIS = [
    ("001", "00100", "Ridge city", (0, 1)),
    ("001", "00200", "Brook township", (2,)),
    ("003", "00300", "Valley township", (0, 1, 2)),
]
PLANNING_COLUMNS = [
    "county",
    "cousub",
    "name",
    "dced_id",
    "zoning_regime",
    "comprehensive_plan",
    "multi_municipal_plan",
    "subdivision_ordinance",
    "planning_commission",
    "zoning_hearing_board",
    "ucc_code",
    "population",
    "housing_units_2020",
]

# Rules read in the frozen snapshot, per municipality code.
RULES = {
    "00100": dict(
        adu_status="by_right",
        parking_min_per_unit_sf=2,
        parking_min_per_unit_mf=1.5,
        adopted_on="2015-02-10",
        effective_date="2015-03-01",
    ),
    "00200": dict(
        adu_status="prohibited",
        parking_min_per_unit_sf=1,
        parking_min_per_unit_mf=1,
        adopted_on="2011-05-02",
        effective_date="2011-06-01",
    ),
    "00300": dict(
        adu_status="conditional",
        parking_min_per_unit_sf=0,
        parking_min_per_unit_mf=0,
        adopted_on="2019-09-09",
        effective_date="2019-10-01",
    ),
}

# The dated test law change (written to law_change.yaml).
LAW_CHANGE = {
    "change_id": "dm_ridge_parking_2026",
    "geoid": STATE_FIPS + "00100100",
    "name": "Ridge city",
    "fields": {
        "parking_min_per_unit_sf": {"original": 2, "revised": 0},
        "parking_min_per_unit_mf": {"original": 1.5, "revised": 0},
    },
    "adopted_on": "2026-08-04",
    "effective_date": "2026-09-01",
    "reviewed_on": "2026-11-20",
    "appeal": {"filed_on": "2026-09-20", "court_status": "challenged", "stayed": False},
    "note": "Synthetic. Ridge city removes its parking minimums; one appeal is pending, "
    "with no stay, so the new rule is in force.",
}

# The approval path of a new home (written to permit_stages.csv).
PERMIT_STAGES = [
    ("application", 1, 0, "", "no application date is published (unobserved)"),
    ("zoning_permit", 2, 1, "zoning_issued_on", "zoning permit issue date"),
    (
        "building_permit",
        3,
        1,
        "permit_issued_on",
        "building permit issue date: the outcome measure",
    ),
    ("completion", 4, 1, "completed_on", "certificate of occupancy date"),
]


def _box_cells(box: Sequence[float], cols: int, rows: int) -> list[tuple[float, ...]]:
    return tl.grid(box, cols, rows)


def _blocks(rng: tl.Rng, tract: str, hu: int, cell) -> list[dict]:
    """Split a tract into a 3 x 3 grid of blocks whose homes add up to the tract."""
    cols, rows = BLOCK_GRID
    weights = [rng.uniform(0.4, 1.6) for _ in range(cols * rows)]
    units = tl.allocate(hu, weights)
    out = []
    for b, (bcell, n) in enumerate(zip(_box_cells(cell, cols, rows), units, strict=True)):
        west, south, east, north = bcell
        out.append(
            {
                "geoid": f"{tract}{b // cols + 1}{b % cols + 1:03d}",
                "tract": tract,
                "housing_units": n,
                "land_area_m2": tl.land_area_m2(*bcell),
                "lat": f"{(south + north) / 2:.6f}",
                "lon": f"{(west + east) / 2:.6f}",
            }
        )
    return out


def _permits(rng: tl.Rng, counties: list[dict], areas: dict[str, str]) -> list[dict]:
    rows = []
    for county in counties:
        area = areas[county["geoid"]]
        typical = county["housing_units"] * tl.PERMIT_RATE[area] / 1000.0
        for year in YEARS:
            total = round(typical * tl.CYCLE[year] * rng.jitter(0.15))
            parts = tl.allocate(total, [s * rng.jitter(0.30) for s in tl.PERMIT_SPLIT[area]])
            rows.append(
                {
                    "county": county["geoid"],
                    "year": year,
                    "units_1": parts[0],
                    "units_2": parts[1],
                    "units_3_4": parts[2],
                    "units_5plus": parts[3],
                    "imputed": 0,
                }
            )
    return rows


def _stops(rng: tl.Rng) -> list[dict]:
    return [
        {
            "stop_id": sid,
            "lat": f"{lat:.6f}",
            "lon": f"{lon:.6f}",
            "mode": mode,
            "weekday_departures": rng.integer(*deps),
        }
        for sid, lat, lon, mode, deps in STOPS
    ]


def _infra(rng: tl.Rng, tracts: list[dict], blocks: list[dict]):
    """``infra_tract`` and ``infra_county`` through the real pack's table functions."""
    import pandas as pd

    from visionhousing.features import infrastructure as fx

    groups = []
    for t in tracts:
        code, homes = t["county"][2:], t["housing_units"]
        if code == "001":
            parts = {"in": homes}
        elif (code, t["index"]) == UNKNOWN_TRACT:
            parts = {"unknown": homes}
        else:
            inside = round(homes * rng.uniform(*SEWER_IN))
            parts = {"in": inside, "out": homes - inside}
        for kind, units in parts.items():
            groups.append(
                {
                    "tract": t["geoid"],
                    "county": t["county"],
                    "units": units,
                    "sewer_in": kind == "in",
                    "sewer_known": kind != "unknown",
                    "constraint": False,
                    "sewer_source": "synthetic" if kind != "unknown" else "",
                    "water_in": False,
                    "water_known": False,
                    "epa_in": False,
                }
            )
    answers = pd.DataFrame(groups)
    rules = fx.load_rules()
    tract_ids = [t["geoid"] for t in tracts]
    table = fx.tract_table(answers, tract_ids, rules=rules)
    table["water_share"] = None
    table["epa_share"] = None
    counties = sorted({t["county"] for t in tracts})
    county = fx.county_table(answers, table, counties, {}, None, None, None)
    return tl._records(table), list(fx.TRACT_COLUMNS), tl._records(county), list(county.columns)


def _districts(blocks: list[dict], tract_ids: list[str]) -> dict[str, bytes]:
    """The three district tables (real pack layout) from the stated block cuts."""
    import pandas as pd

    from visionhousing.districts.weights import weights_for

    frame = pd.DataFrame(blocks)
    frame["housing_units"] = frame["housing_units"].astype(int)
    frame["land_area_m2"] = frame["land_area_m2"].astype(float)
    lat = frame["lat"].astype(float)
    lon = frame["lon"].astype(float)
    upper = pd.DataFrame({"geoid": frame["geoid"], "district": (lat >= UPPER_CUT_LAT) + 1})
    lower = pd.DataFrame(
        {
            "geoid": frame["geoid"],
            "district": 1 + (lon >= LOWER_CUTS_LON[0]).astype(int) + (lon >= LOWER_CUTS_LON[1]),
        }
    )
    for a in (upper, lower):
        a["district"] = a["district"].astype(int).astype(str)
    names = {
        "upper": {str(i): f"Demo Senate District {i}" for i in (1, 2)},
        "lower": {str(i): f"Demo House District {i}" for i in (1, 2, 3)},
    }
    tables = weights_for(frame, {"upper": upper, "lower": lower}, names, tract_ids)
    out = {}
    for name, table in tables.items():
        text = table.to_csv(index=False, lineterminator="\n", float_format="%.4f")
        out[f"{name}.csv.gz"] = gzip.compress(text.encode("utf-8"), mtime=0)
    return out


def _district_outlines(box: Sequence[float]) -> dict[str, bytes]:
    west, south, east, north = box
    lower_edges = [west, *LOWER_CUTS_LON, east]
    shapes = {
        "upper": [
            ("1", (west, south, east, UPPER_CUT_LAT)),
            ("2", (west, UPPER_CUT_LAT, east, north)),
        ],
        "lower": [
            (str(i + 1), (lower_edges[i], south, lower_edges[i + 1], north)) for i in range(3)
        ],
    }
    out = {}
    for chamber, items in shapes.items():
        feats = []
        for code, (w, s, e, n) in items:
            ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
            word = "Senate" if chamber == "upper" else "House"
            feats.append(
                {
                    "type": "Feature",
                    "properties": {"district": code, "name": f"Demo {word} District {code}"},
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                }
            )
        doc = {"type": "FeatureCollection", "features": feats}
        out[f"boundaries/districts_{chamber}.geojson"] = (
            json.dumps(doc, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    return out


def _geojson(shapes: list[tuple]) -> bytes:
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


def _rule_rows(read_on: str) -> list[dict]:
    from visionhousing.law.schema import MuniRule

    rows = []
    for county, cousub, name, _held in MUNIS:
        r = RULES[cousub]
        rows.append(
            MuniRule(
                geoid=STATE_FIPS + county + cousub,
                name=name,
                zoning_regime="own",
                doc_id=f"dm_{cousub}_zoning",
                section_cite="§ 4 (synthetic)",
                method="hand",
                confidence="high",
                status="verified",
                reviewed_on=read_on,
                note="Synthetic demo-state rule; not a real ordinance.",
                **r,
            ).row()
        )
    return rows


def _munis(tracts: list[dict]) -> tuple[list[dict], list[dict]]:
    """``municipal_planning`` and ``tract_municipalities`` from the tracts each one holds."""
    rows, pieces = [], []
    for county, cousub, name, positions in MUNIS:
        held = [t for t in tracts if t["county"] == STATE_FIPS + county and t["index"] in positions]
        rows.append(
            {
                "county": STATE_FIPS + county,
                "cousub": cousub,
                "name": name,
                "dced_id": "",
                "zoning_regime": "own",
                "comprehensive_plan": 1,
                "multi_municipal_plan": 0,
                "subdivision_ordinance": 1,
                "planning_commission": 1,
                "zoning_hearing_board": 1,
                "ucc_code": 1,
                "population": sum(t["population"] for t in held),
                "housing_units_2020": sum(t["housing_units"] for t in held),
            }
        )
        pieces += [
            {
                "tract": t["geoid"],
                "county": t["county"],
                "cousub": cousub,
                "housing_units_2020": t["housing_units"],
            }
            for t in held
        ]
    pieces.sort(key=lambda p: p["tract"])
    return rows, pieces


def _timeline() -> dict:
    """The frozen timeline: the original parking rule, and the dated change under appeal."""
    c = LAW_CHANGE
    src = ["synthetic:demo_state/law_change.yaml"]
    common = {
        "citation": "Ridge city zoning code § 4 (synthetic)",
        "jurisdiction": c["geoid"],
        "housing_type": "parking",
        "review": "verified",
        "sources": src,
        "provision_id": "dm_ridge_parking",
    }
    return {
        "version": 1,
        "snapshot": {"read_on": SNAPSHOT_READ_ON, "synthetic": True},
        "records": [
            {
                **common,
                "record_id": "dm_ridge_parking_original",
                "status": "legally_effective",
                "effect": "restricts",
                "start": RULES["00100"]["effective_date"],
                "end": c["effective_date"],
                "verified_on": SNAPSHOT_READ_ON,
                "end_verified_on": c["reviewed_on"],
                "superseded_by": "dm_ridge_parking_revised",
                "scope": "Off-street parking: 2 spaces per single-family home, 1.5 per apartment",
            },
            {
                **common,
                "record_id": "dm_ridge_parking_revised",
                "status": "legally_effective",
                "effect": "allows",
                "start": c["effective_date"],
                "verified_on": c["reviewed_on"],
                "adopted_on": c["adopted_on"],
                "supersedes": "dm_ridge_parking_original",
                "court_status": c["appeal"]["court_status"],
                "scope": "No off-street parking minimum",
                "note": f"Appeal filed {c['appeal']['filed_on']}; no stay.",
            },
        ],
    }


def _dictionary(extra: dict[str, list[str]]) -> bytes:
    """Testland's column descriptions for the shared tables, plus the demo-only tables."""
    base = json.loads(tl._dictionary())
    keep = {
        "counties",
        "tracts",
        "blocks",
        "permits_county_year",
        "transit_stops",
        "infra_tract",
        "boundaries",
    }
    entries = []
    for e in base["columns"]:
        if e["table"] in keep:
            e = {**e, "description": e["description"].replace("Testland", "demo state")}
            entries.append(e)
    for table, columns in extra.items():
        for name in columns:
            entries.append(
                {
                    "table": table,
                    "name": name,
                    "unit": "",
                    "description": f"{table}.{name} (demo state: synthetic, made by "
                    "scripts/make_demo_fixture.py's stated rules)",
                    "source": "synthetic",
                    "tier": "0",
                }
            )
    doc = {"state": STATE_CODE, "synthetic": True, "seed": SEED, "columns": entries}
    return (json.dumps(doc, indent=1, ensure_ascii=True) + "\n").encode("utf-8")


def _csv(columns: list[str], rows: list[dict]) -> bytes:
    return tl._csv(columns, rows)


def build(seed: int = SEED) -> dict[str, bytes]:
    """Generate every demo-state file in memory and return them by relative path."""
    rng = tl.Rng(seed)
    counties, tracts, blocks, shapes, areas = [], [], [], [], {}
    for code, name, area, box in COUNTIES:
        cg = STATE_FIPS + code
        areas[cg] = area
        shapes.append(("county", cg, name, box))
        pop = hu = 0
        for t, cell in enumerate(_box_cells(box, TRACTS_PER_COUNTY, 1)):
            geoid = f"{cg}{(t + 1) * 100:06d}"
            row = tl._tract_row(rng, geoid, cg, area, PROFILES[area], cell)
            tracts.append({**row, "index": t})
            shapes.append(("tract", geoid, f"Tract {t + 1}", cell))
            pop += row["population"]
            hu += row["housing_units"]
            blocks.extend(_blocks(rng, geoid, row["housing_units"], cell))
        counties.append({"geoid": cg, "name": name, "population": pop, "housing_units": hu})
    permits = _permits(rng, counties, areas)
    stops = _stops(rng)
    infra_rows, infra_cols, county_rows, county_cols = _infra(tl.Rng(seed + 1), tracts, blocks)
    tract_ids = [t["geoid"] for t in tracts]
    ex_county, ex_pos = EXISTING_TRACT
    existing = [
        {
            "geoid": t["geoid"],
            "capacity_units": EXISTING_UNITS,
            "capacity_parcel_units": EXISTING_UNITS,
            "station_area_units": EXISTING_UNITS,
            "parcel_acres": EXISTING_ACRES,
        }
        for t in tracts
        if t["county"] == STATE_FIPS + ex_county and t["index"] == ex_pos
    ]
    rules = _rule_rows(SNAPSHOT_READ_ON)
    munis, pieces = _munis(tracts)
    rule_cols = list(rules[0])
    stage_cols = ["stage", "order", "observed", "date_column", "note"]
    stage_rows = [dict(zip(stage_cols, s, strict=True)) for s in PERMIT_STAGES]
    box = (COUNTIES[0][3][0], COUNTIES[0][3][1], COUNTIES[-1][3][2], COUNTIES[-1][3][3])
    extra_cols = {
        "infra_county": county_cols,
        "mbta_3a_capacity_tract": list(existing[0]),
        "muni_zoning_rules": rule_cols,
        "municipal_planning": PLANNING_COLUMNS,
        "tract_municipalities": ["tract", "county", "cousub", "housing_units_2020"],
        "permit_stages": stage_cols,
    }
    files = {
        "counties.csv": _csv(["geoid", "name", "population", "housing_units"], counties),
        "tracts.csv": _csv(
            tl.TRACT_COLUMNS, [{k: v for k, v in t.items() if k != "index"} for t in tracts]
        ),
        "blocks.csv": _csv(
            ["geoid", "tract", "housing_units", "land_area_m2", "lat", "lon"], blocks
        ),
        "permits_county_year.csv": _csv(
            ["county", "year", "units_1", "units_2", "units_3_4", "units_5plus", "imputed"],
            permits,
        ),
        "transit_stops.csv": _csv(["stop_id", "lat", "lon", "mode", "weekday_departures"], stops),
        "boundaries.geojson": _geojson(shapes),
        "infra_tract.csv": _csv(infra_cols, infra_rows),
        "infra_county.csv": _csv(county_cols, county_rows),
        "mbta_3a_capacity_tract.csv": _csv(list(existing[0]), existing),
        "muni_zoning_rules.csv": _csv(rule_cols, rules),
        "municipal_planning.csv": _csv(PLANNING_COLUMNS, munis),
        "tract_municipalities.csv": _csv(extra_cols["tract_municipalities"], pieces),
        "permit_stages.csv": _csv(stage_cols, stage_rows),
        "law_timeline.yaml": yaml.safe_dump(_timeline(), sort_keys=False).encode("utf-8"),
        "law_change.yaml": yaml.safe_dump(LAW_CHANGE, sort_keys=False).encode("utf-8"),
        "district_vintage.json": (
            json.dumps(
                {
                    "plans": "synthetic (demo state)",
                    "outlines": "synthetic rectangles (demo state)",
                    "synthetic": True,
                },
                indent=1,
            )
            + "\n"
        ).encode(),
        "data_dictionary.json": _dictionary(extra_cols),
        **_districts(blocks, tract_ids),
        **_district_outlines(box),
    }
    return files


def write(out: Path, seed: int = SEED) -> list[Path]:
    """Write every demo-state file into ``out`` and return the written paths."""
    paths = []
    for name, data in build(seed).items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        paths.append(path)
    return paths


def apply_law_change(src: Path, dst: Path) -> Path:
    """Copy the demo state from ``src`` to ``dst`` with the dated test law change applied.

    The changed municipality's row in ``muni_zoning_rules.csv`` takes the revised values,
    the change's adoption and effective dates, the date it was read and a note naming the
    appeal. Every other file is copied unchanged. Returns ``dst``.
    """
    import pandas as pd

    src, dst = Path(src), Path(dst)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    change = yaml.safe_load((src / "law_change.yaml").read_text(encoding="utf-8"))
    rules = pd.read_csv(src / "muni_zoning_rules.csv", dtype=str, keep_default_na=False)
    hit = rules["geoid"] == str(change["geoid"])
    if hit.sum() != 1:
        raise ValueError(f"law change {change['change_id']}: no single rule row to change")
    for field, values in change["fields"].items():
        rules.loc[hit, field] = str(values["revised"])
    appeal = change.get("appeal") or {}
    rules.loc[hit, "adopted_on"] = str(change["adopted_on"])
    rules.loc[hit, "effective_date"] = str(change["effective_date"])
    rules.loc[hit, "reviewed_on"] = str(change["reviewed_on"])
    rules.loc[hit, "note"] = (
        f"Synthetic test law change {change['change_id']}; appeal filed "
        f"{appeal.get('filed_on', '')}, no stay."
    )
    (dst / "muni_zoning_rules.csv").write_text(
        rules.to_csv(index=False, lineterminator="\n"), encoding="utf-8"
    )
    return dst


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``--out`` (and optionally ``--revised``) and write the demo-state files."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/demo_state"))
    parser.add_argument(
        "--revised",
        type=Path,
        default=None,
        help="also write a copy with the dated test law change applied here",
    )
    args = parser.parse_args(argv)
    paths = write(args.out)
    total = sum(p.stat().st_size for p in paths)
    print(f"demo state: wrote {len(paths)} files, {total:,} bytes, to {args.out}")
    if args.revised is not None:
        apply_law_change(args.out, args.revised)
        print(f"demo state with the test law change: {args.revised}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
