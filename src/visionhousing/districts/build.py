"""Add the legislative district tables and outlines to a built state pack.

Plain-language summary
----------------------
:func:`add_districts` is a retrofit, like the other ``add_*`` steps of
:mod:`visionhousing.data.pack`: it adds to a pack that is already built,
without rebuilding the rest. It:

1. fetches (or reuses) both chambers' block equivalency files and district
   outlines (:mod:`visionhousing.data.sources.sld`) and the state's 2020 block
   table with housing units (:mod:`visionhousing.data.sources.blocks`);
2. makes the weights and the district list
   (:mod:`visionhousing.districts.weights`) for the pack's own tracts;
3. writes three small tables:

   ========================= ==============================================
   ``districts``             one row per district: name, 2020 units, tracts
                             and counties touched, split shares
   ``district_tract_weights`` one row per (district, tract) with a weight
                             above zero: 2020 units there, weight (4 places)
   ``district_county_weights`` the same per (district, county)
   ========================= ==============================================

4. writes ``boundaries/districts_upper.geojson`` and
   ``boundaries/districts_lower.geojson`` (outlines simplified with the same
   Douglas-Peucker method as the county outlines, coordinates to 4 decimals,
   about 10 m) and ``district_vintage.json`` (which plans and outlines);
5. rewrites the data dictionary and the pack manifest the same way the other
   retrofits do, and returns the new pack size.

A full build (``make data``) runs this as its first add-on step
(``visionhousing.data.pack.RETROFITS``); ``visionhousing districts --state PA``
refreshes the district files alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data import pack as pk
from visionhousing.data.http import HttpClient, sha256_file
from visionhousing.data.sources import blocks as blocks_src
from visionhousing.data.sources import boundaries as bnd
from visionhousing.data.sources import sld
from visionhousing.districts.results import BOUNDARY_FILES, VINTAGE_FILE
from visionhousing.districts.weights import weights_for

__all__ = ["OUTLINE_DIGITS", "OUTLINE_TOLERANCE", "DistrictBuild", "add_districts", "outlines_text"]

OUTLINE_TOLERANCE = {"upper": 0.003, "lower": 0.002}
"""Douglas-Peucker tolerance in degrees per chamber (0.001 degree is about 100 m)."""
OUTLINE_DIGITS = 4
"""Decimal places kept in district outline coordinates (about 10 m)."""


@dataclass(frozen=True)
class DistrictBuild:
    """What :func:`add_districts` wrote."""

    tables: dict[str, pd.DataFrame]
    files: dict[str, int]
    """Pack path to bytes, for every file written."""
    pack_bytes: int


def outlines_text(shapes: list[bnd.Shape], level: str, tolerance: float) -> str:
    """One chamber's outlines as compact GeoJSON (``level, geoid, name`` properties)."""
    features = []
    for s in sorted(shapes, key=lambda s: s.geoid):
        geom = bnd.simplify_geometry(s.geometry, tolerance, OUTLINE_DIGITS)
        features.append(
            {"type": "Feature", "properties": {"level": level, "geoid": s.geoid, "name": s.name},
             "geometry": geom}
        )  # fmt: skip
    body = {"type": "FeatureCollection", "features": features}
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False) + "\n"


def _pack_bytes(folder: Path) -> int:
    data = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    return (
        sum(int(f["bytes"]) for f in data["files"]) + (folder / "pack_manifest.json").stat().st_size
    )


def add_districts(
    folder: Path,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    inputs: sld.DistrictInputs | None = None,
    blocks: pd.DataFrame | None = None,
) -> DistrictBuild:
    """Add (or refresh) the district tables and outlines in a built pack.

    Args:
        folder: The pack folder (``data/packs/{STATE}``), rewritten in place.
        cache: The raw-file cache (``data/raw``).
        client: HTTP client for missing cache files.
        manifest: The download manifest to record fetches in.
        inputs: District inputs already read (tests); ``None`` fetches them.
        blocks: Block table already read (tests); ``None`` reads the cached extract.

    Returns:
        The tables written, every file's size and the pack's new total size.
    """
    folder, cache = Path(folder), Path(cache)
    state = folder.name.upper()
    if inputs is None:
        inputs = sld.fetch_districts(state, cache, client=client, manifest=manifest)
    new_inputs = list(inputs.files)
    if blocks is None:
        blocks, fetched = blocks_src.fetch_blocks(state, cache, client=client, manifest=manifest)
        new_inputs.append((blocks_src.SOURCE_ID, fetched.path))
    tracts = pk.load_pack(folder)["tracts"]
    names = {ch: {s.geoid: s.name for s in shapes} for ch, shapes in inputs.shapes.items()}
    tables = weights_for(blocks, inputs.assignments, names, tracts["geoid"].astype(str))

    files: dict[str, int] = {}
    entries = []
    for chamber, rel in BOUNDARY_FILES.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        layer = sld.CHAMBERS[chamber]
        path.write_text(
            outlines_text(inputs.shapes[chamber], layer, OUTLINE_TOLERANCE[chamber]),
            encoding="utf-8",
        )
        entries.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    vintage = {
        "plans": "2022 state legislative district plans (Census block equivalency files)",
        "plans_source": sld.BEF_SOURCE,
        "outlines": "Census cartographic boundary files, 2024",
        "outlines_source": sld.SHAPE_SOURCE,
        "housing_units": "2020 Census block housing units",
        "housing_units_source": blocks_src.SOURCE_ID,
        "note": (
            "Census published 2024 and 2026 district files only for states that redrew; "
            f"{state} is not among them (checked 2026-11-24)."
        ),
    }
    vpath = folder / VINTAGE_FILE
    vpath.write_text(json.dumps(vintage, indent=1) + "\n", encoding="utf-8")
    entries.append(
        {"path": VINTAGE_FILE, "bytes": vpath.stat().st_size, "sha256": sha256_file(vpath)}
    )

    mpath = folder / "pack_manifest.json"
    old = json.loads(mpath.read_text(encoding="utf-8"))
    mine = {e["path"] for e in entries}
    old["files"] = [f for f in old["files"] if f["path"] not in mine] + entries
    mpath.write_text(pk._json_text(old), encoding="utf-8")
    pk._rewrite(folder, cache, tables, new_inputs)

    for name in tables:
        files[f"{name}.csv.gz"] = (folder / f"{name}.csv.gz").stat().st_size
    for e in entries:
        files[e["path"]] = (folder / e["path"]).stat().st_size
    return DistrictBuild(tables, files, _pack_bytes(folder))
