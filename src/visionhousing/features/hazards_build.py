"""Build the hazards and land tables for a state pack, one county at a time.

Plain-language summary
----------------------
``visionhousing hazards --state PA`` runs :func:`add_hazards`. For each county:

1. project its tract outlines and its 2020 Census block points to the 30 m
   Albers grid (:mod:`visionhousing.features.hazards`);
2. read the county's box from the three public services
   (:mod:`visionhousing.data.sources.hazards`): land cover and impervious
   surface for the configured year, elevation, FEMA flood-zone polygons and
   FEMA's map-availability polygons;
3. work out slope, burn the polygons onto the grid, and add up cells and homes
   per tract;
4. save only those per-tract counts, plus a note of what was read (each
   answer's checksum and size), in ``data/raw/hazards/<state code>/``.

A county already saved with the same rules is reused, so a run that stops can
be started again and picks up where it left off. When every county is done,
the counts become ``hazard_tract`` and ``hazard_county``, and the pack's data
dictionary and manifest are rewritten (the same retrofit the other added
tables use). Memory stays small: one county's grids at a time (well under
100 MB for the largest Pennsylvania county).
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.data import pack as pk
from visionhousing.data.http import HttpClient
from visionhousing.data.sources import hazards as src
from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.features import hazards as hz
from visionhousing.features import planar

__all__ = ["add_hazards", "county_counts", "hazards_cmd", "rules_hash"]

FOLDER = "hazards"


def rules_hash(rules: dict[str, Any]) -> str:
    """A short fingerprint of the rules, so a saved county is reused only under the same rules."""
    text = json.dumps(rules, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def county_counts(
    county: str,
    shapes: list[tuple[str, dict[str, Any]]],
    blocks: pd.DataFrame,
    client: HttpClient,
    rules: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read one county's grids and polygons and count cells and homes per tract.

    Args:
        county: County code.
        shapes: ``(tract code, GeoJSON geometry in lon/lat)`` for the county's tracts.
        blocks: The county's blocks with ``tract``, ``housing_units``, ``lat``, ``lon``.
        client: HTTP client.
        rules: ``assumptions/hazards.yaml``.

    Returns:
        The per-tract counts and a note of what was read.
    """
    g = rules["grid"]
    started = time.time()
    projected = [hz.project_geometry(geom) for _, geom in shapes]
    xmin, ymin, xmax, ymax = planar.rings_bounds([r for rings in projected for r in rings])
    grid = hz.grid_for_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        float(g["cell_m"]),
        (float(g["origin"][0]), float(g["origin"][1])),
        pad=float(g["pad_m"]),
    )
    labels = hz.label_tracts(grid, projected)
    reads = src.Reads()
    year = int(rules["landcover"]["year"])
    landcover = src.nlcd_grid(client, "landcover", year, grid, reads).values
    impervious = src.nlcd_grid(client, "impervious", year, grid, reads).values
    dem = src.dem_grid(client, grid, reads)
    slope = hz.slope_percent(dem.values, grid.cell, dem.nodata)
    del dem
    flood = src.flood_zones(client, grid, rules, reads)
    sfha = hz.burn_rings(grid, flood)
    available = src.nfhl_availability(client, grid, reads)
    nfhl = hz.burn_rings(grid, available)
    b = blocks.copy()
    b["x"], b["y"] = hz.to_albers(pd.to_numeric(b["lon"]), pd.to_numeric(b["lat"]))
    counts = hz.tract_counts(
        labels,
        [t for t, _ in shapes],
        landcover,
        impervious,
        slope,
        sfha,
        nfhl,
        rules,
        blocks=b,
        grid=grid,
    )
    note = {
        "county": county,
        "grid": {
            "x0": grid.x0,
            "y0": grid.y0,
            "cell": grid.cell,
            "rows": grid.rows,
            "cols": grid.cols,
        },
        "nlcd_year": year,
        "sfha_polygons": len(flood),
        "nfhl_availability_polygons": len(available),
        "read_on": time.strftime("%Y-%m-%d", time.gmtime()),
        "seconds": round(time.time() - started, 1),
        "rules": rules_hash(rules),
        "reads": reads.rows,
    }
    return counts, note


def _paths(cache: Path, fips: str, county: str) -> tuple[Path, Path]:
    folder = cache / FOLDER / fips
    return folder / f"{county}_counts.csv", folder / f"{county}_note.json"


def add_hazards(
    folder: Path,
    cache: Path,
    *,
    client: HttpClient | None = None,
    counties: list[str] | None = None,
    workers: int = 2,
    rules: dict[str, Any] | None = None,
    write: bool = True,
    log: Callable[[str], None] = print,
) -> dict[str, pd.DataFrame]:
    """Add (or refresh) ``hazard_tract`` and ``hazard_county`` in a built pack.

    Args:
        folder: The pack folder (``data/packs/{STATE}``), rewritten in place.
        cache: The raw-file cache (``data/raw``); county counts are saved under
            ``hazards/<state code>/``.
        client: HTTP client (default: a new one).
        counties: Only these counties (a partial table, for checks).
        workers: Counties read at the same time.
        rules: The rules (default: ``assumptions/hazards.yaml``).
        write: Write the tables into the pack (``False``: only fill the cache).
        log: Function that prints progress.

    Returns:
        The two tables.
    """
    folder, cache = Path(folder), Path(cache)
    rules = rules or hz.load_rules()
    client = client or HttpClient(timeout=180)
    old = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    fips = str(old.get("state_fips") or to_state_fips(folder.name.upper()))
    tables = pk.load_pack(folder)
    tract_ids = set(tables["tracts"]["geoid"].astype(str))
    shapes = [(t, s) for t, s in pk._tract_shapes(folder, cache, fips) if t in tract_ids]
    by_county: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for tract, geom in sorted(shapes):
        by_county.setdefault(tract[:5], []).append((tract, geom))
    wanted = sorted(by_county) if not counties else sorted(set(counties) & set(by_county))
    blocks = tables["blocks"].copy()
    blocks["tract"] = blocks["tract"].astype(str)
    blocks["county"] = blocks["tract"].str[:5]
    fingerprint = rules_hash(rules)

    def one(county: str) -> tuple[pd.DataFrame, dict[str, Any]]:
        counts_path, note_path = _paths(cache, fips, county)
        if counts_path.exists() and note_path.exists():
            note = json.loads(note_path.read_text(encoding="utf-8"))
            if note.get("rules") == fingerprint:
                saved = pd.read_csv(counts_path, dtype={"geoid": str})
                if set(hz.COUNT_COLUMNS) <= set(saved.columns):
                    return saved, note
        counts, note = county_counts(
            county, by_county[county], blocks[blocks["county"] == county], client, rules
        )
        counts_path.parent.mkdir(parents=True, exist_ok=True)
        counts.to_csv(counts_path, index=False, lineterminator="\n")
        note_path.write_text(json.dumps(note, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        log(
            f"hazards {county}: {len(counts)} tracts, {note['sfha_polygons']} flood polygons, "
            f"{note['seconds']} s"
        )
        return counts, note

    results: dict[str, tuple[pd.DataFrame, dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for county, result in zip(wanted, pool.map(one, wanted), strict=True):
            results[county] = result
    counts = pd.concat([results[c][0] for c in wanted], ignore_index=True)
    notes = {c: results[c][1] for c in wanted}
    tract = hz.tract_table(counts, rules)
    county_tab = hz.county_table(counts, notes, rules)
    index = cache / FOLDER / fips / "reads_index.csv"
    rows = [{"county": c, **r} for c in wanted for r in notes[c].get("reads", [])]
    pd.DataFrame(rows, columns=["county", "source", "request", "sha256", "bytes"]).to_csv(
        index, index=False, lineterminator="\n"
    )
    written = {"hazard_tract": tract, "hazard_county": county_tab}
    if write:
        new_inputs = [(s, index) for s in (src.SOURCE_NFHL, src.SOURCE_NLCD, src.SOURCE_DEM)]
        pk._rewrite(folder, cache, written, new_inputs)
    return written


def hazards_cmd(
    state: str, raw: Path, packs: Path, counties: list[str] | None = None, workers: int = 2
) -> int:
    """``visionhousing hazards``: add the hazard tables to a built pack and print sizes."""
    folder = Path(packs) / state.upper()
    if not (folder / "tracts.csv.gz").is_file():
        print(f"hazards: {folder} has no tracts table; build the pack first", file=sys.stderr)
        return 1
    written = add_hazards(folder, Path(raw), counties=counties, workers=workers)
    for name, frame in written.items():
        path = folder / f"{name}.csv.gz"
        print(f"{name}: {len(frame):,} rows, {path.stat().st_size:,} bytes")
    tract = written["hazard_tract"]
    blank = int(tract["sfha_share"].isna().sum())
    print(f"tracts: {len(tract):,}; flood shares blank (no digital FEMA map): {blank:,}")
    total = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    print(
        f"pack total: {total:,} of {pk.PACK_BUDGET_BYTES:,} bytes "
        f"({total / pk.PACK_BUDGET_BYTES:.1%})"
    )
    share = pd.to_numeric(tract["sfha_share"], errors="coerce")
    print(f"median tract SFHA home share: {np.nanmedian(share.to_numpy()):.3f}")
    return 0
