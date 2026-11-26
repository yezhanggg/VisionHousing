"""OpenStreetMap walking network around transit stops, fetched from the Overpass API.

Plain-language summary
----------------------
The network walksheds (:mod:`visionhousing.features.walkshed`) need the streets,
sidewalks and paths a person can walk on. They come from **OpenStreetMap**
(OSM), the open map built by volunteers, through the **Overpass API**, a public
read-only query service that needs no key.

How it is fetched
~~~~~~~~~~~~~~~~~
- The map is cut into square **tiles** of ``tile_deg`` degrees (0.1 degree,
  about 11 by 8.5 km in Pennsylvania). Only tiles that touch the largest walking
  circle around a major transit stop are asked for, so most of a rural state is
  never downloaded.
- One query per tile asks for every way tagged ``highway`` whose type is
  walkable and that is not closed to people on foot (the filter is in
  ``assumptions/transit_walk.yaml``), plus the ways' nodes. A way that crosses
  the tile edge comes back whole, so tiles join without gaps.
- Each answer is saved, gzipped, as ``data/raw/osm/tiles_<deg>/<row>_<col>.json.gz``
  and reused on the next build. A tile that fails on every server is not saved,
  and the stops that needed it fall back to straight-line circles (flagged).
  The main Overpass server is tried first, then a mirror.

Licence
~~~~~~~
OSM data are © OpenStreetMap contributors and available under the Open Database
Licence (ODbL 1.0). The raw tiles stay in the local cache and are never
published. The pack keeps only **derived aggregates** (acres per tract), with
the attribution "© OpenStreetMap contributors, ODbL" in the source registry.
Under the ODbL a "produced work" made from the data needs attribution; if the
derived database itself were shared, it would be shared under the ODbL too
(see ``DATA_PROVENANCE.md``).
"""

from __future__ import annotations

import gzip
import itertools
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient

__all__ = [
    "SOURCE",
    "OsmWays",
    "fetch_tiles",
    "overpass_query",
    "parse_overpass",
    "read_tiles",
    "tile_bbox",
    "tiles_for_points",
]

SOURCE = "osm_overpass_walk"
"""Source registry id of the Overpass walking-network tiles."""


def tile_bbox(tile: tuple[int, int], deg: float) -> tuple[float, float, float, float]:
    """``(south, west, north, east)`` of a tile ``(row, col)``."""
    row, col = tile
    return (row * deg, col * deg, (row + 1) * deg, (col + 1) * deg)


def tiles_for_points(
    lat: Sequence[float], lon: Sequence[float], radius_m: float, deg: float
) -> list[tuple[int, int]]:
    """Every tile that touches the box of ``radius_m`` metres around any point, sorted."""
    out: set[tuple[int, int]] = set()
    for la, lo in zip(lat, lon, strict=True):
        dlat = radius_m / 111_000.0
        dlon = radius_m / (111_000.0 * max(math.cos(math.radians(la)), 0.1))
        r0, r1 = math.floor((la - dlat) / deg), math.floor((la + dlat) / deg)
        c0, c1 = math.floor((lo - dlon) / deg), math.floor((lo + dlon) / deg)
        out.update((r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1))
    return sorted(out)


def overpass_query(bbox: tuple[float, float, float, float], rules: Mapping[str, Any]) -> str:
    """The Overpass QL text for one tile's walkable ways and their nodes.

    ``rules`` is the ``osm`` part of ``assumptions/transit_walk.yaml``: the
    excluded ``highway`` values and the access tags that close a way to walkers.
    """
    s, w, n, e = bbox
    box = f"{s:.4f},{w:.4f},{n:.4f},{e:.4f}"
    excluded = "|".join(rules["exclude_highway"])
    closed = "|".join(rules["closed_access"])
    timeout = int(rules.get("timeout_s", 180))
    return (
        f"[out:json][timeout:{timeout}];"
        f'way["highway"]["highway"!~"^({excluded})$"]["area"!="yes"]'
        f'["foot"!~"^({closed})$"]["access"!~"^({closed})$"]({box});'
        "(._;>;);out skel qt;"
    )


@dataclass
class OsmWays:
    """Nodes and ways read from Overpass answers.

    Attributes:
        node_id: OSM node ids, sorted.
        lat: Node latitudes, aligned with ``node_id``.
        lon: Node longitudes.
        ways: Each way as an array of positions in ``node_id``.
        way_ids: OSM way ids, aligned with ``ways``.
    """

    node_id: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    ways: list[np.ndarray] = field(default_factory=list)
    way_ids: list[int] = field(default_factory=list)


def parse_overpass(answers: Iterable[Mapping[str, Any]]) -> OsmWays:
    """Join Overpass JSON answers into one set of nodes and ways (each kept once)."""
    nodes: dict[int, tuple[float, float]] = {}
    ways: dict[int, list[int]] = {}
    for answer in answers:
        for el in answer.get("elements", []):
            kind = el.get("type")
            if kind == "node":
                nodes[int(el["id"])] = (float(el["lat"]), float(el["lon"]))
            elif kind == "way" and el.get("nodes"):
                ways.setdefault(int(el["id"]), [int(n) for n in el["nodes"]])
    ids = np.array(sorted(nodes), dtype=np.int64)
    lat = np.array([nodes[i][0] for i in ids.tolist()], dtype=float)
    lon = np.array([nodes[i][1] for i in ids.tolist()], dtype=float)
    out_ways, out_ids = [], []
    for wid in sorted(ways):
        refs = np.asarray(ways[wid], dtype=np.int64)
        if not len(ids):
            continue
        pos = np.clip(np.searchsorted(ids, refs), 0, len(ids) - 1)
        ok = ids[pos] == refs
        # A way with a node missing from the answer is split at the gap.
        bounds = [-1, *np.flatnonzero(~ok).tolist(), len(pos)]
        for a, b in itertools.pairwise(bounds):
            part = pos[a + 1 : b]
            if len(part) >= 2:
                out_ways.append(part)
                out_ids.append(wid)
    return OsmWays(node_id=ids, lat=lat, lon=lon, ways=out_ways, way_ids=out_ids)


def _tile_path(cache: Path, tile: tuple[int, int], deg: float) -> Path:
    return Path(cache) / "osm" / f"tiles_{deg:g}" / f"{tile[0]}_{tile[1]}.json.gz"


def fetch_tiles(
    tiles: Sequence[tuple[int, int]],
    cache: Path,
    rules: Mapping[str, Any],
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
    log: Any = None,
) -> tuple[list[Path], list[tuple[int, int]]]:
    """Fetch (or reuse) every tile; return the cached files and the tiles that failed."""
    deg = float(rules["tile_deg"])
    got: list[Path] = []
    failed: list[tuple[int, int]] = []
    for tile in tiles:
        path = _tile_path(cache, tile, deg)
        if path.exists():
            got.append(path)
            continue
        client = client or HttpClient(timeout=float(rules.get("timeout_s", 180)) + 60)
        query = overpass_query(tile_bbox(tile, deg), rules)
        data = None
        for url in rules["endpoints"]:
            try:
                data = client.get_bytes(url, {"data": query})
                answer = json.loads(data)
                if "elements" not in answer:
                    raise ValueError("answer has no elements")
                if answer.get("remark", "").lower().startswith("runtime error"):
                    raise ValueError(answer["remark"][:200])
                break
            except (FetchError, OSError, ValueError) as err:
                data = None
                if log:
                    log(f"osm tile {tile}: {url}: {type(err).__name__}: {str(err)[:120]}")
        if data is None:
            failed.append(tile)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.GzipFile(path, "wb", mtime=0) as fh:
            fh.write(data)
        got.append(path)
        if manifest is not None:
            mf.record(
                mf.entry_for_file(
                    SOURCE, f"{rules['endpoints'][0]}?bbox={tile_bbox(tile, deg)}", path,
                    date.today().isoformat(), root=mf.ROOT,
                ),
                manifest,
            )  # fmt: skip
        if log:
            log(f"osm tile {tile}: {path.stat().st_size / 1e6:.1f} MB")
    return got, failed


def read_tiles(paths: Iterable[Path]) -> OsmWays:
    """Read cached tiles into one :class:`OsmWays` (tiles are read one at a time)."""

    def answers() -> Iterable[Mapping[str, Any]]:
        for p in paths:
            with gzip.open(p, "rb") as fh:
                yield json.loads(fh.read())

    return parse_overpass(answers())
