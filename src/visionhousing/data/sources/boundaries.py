"""County and tract boundaries: read the Census cartographic files, simplify, write small GeoJSON.

Plain-language summary
----------------------
The interface draws counties and tracts on a map. The Census Bureau publishes
"cartographic boundary" files for this: shapes already generalized for maps at a
scale of 1:500,000. They come as shapefiles (a ``.shp`` file of shapes, a
``.shx`` index and a ``.dbf`` table of names and codes) inside zip files:

- ``cb_2020_{state}_tract_500k.zip``: the state's 2020 census tracts.
- ``cb_2020_us_county_500k.zip``: every county in the country; the state's are kept.

The 2020 shapes match the tracts of the 2020-2024 American Community Survey.

Steps
-----
1. **Fetch.** Download the zip, read it with pyshp (a pure-Python shapefile
   reader), keep only the state's shapes, and save them at full detail as a small
   gzipped GeoJSON extract in ``data/raw/boundaries/``. The zip is then deleted;
   the national county zip (12.6 MB) is never kept.
2. **Simplify.** Each ring (the closed outline of one polygon or hole) is
   simplified with the Douglas-Peucker method: keep the two ends of a stretch of
   outline; find the point farthest from the straight line between them; if it is
   farther than the tolerance, keep it and repeat on both halves; otherwise drop
   every point in between. The tolerance is in degrees (0.001 degrees is about
   110 m north-south and 84 m east-west in Pennsylvania).
3. **Round** coordinates to 5 decimal places (about 1 m) and drop repeated points.
4. **Orient** rings as the GeoJSON standard (RFC 7946) asks: outer rings
   counter-clockwise, holes clockwise.
5. **Write** ``boundaries/counties.geojson`` (one file, at most 300 KB) and
   ``boundaries/tracts/{county code}.geojson`` (one file per county, loaded by the
   interface only when that county is opened).

Rings that shrink to fewer than three distinct points are dropped (tiny islands
and holes), except the largest outer ring of a shape, which is kept at a finer
tolerance so no county or tract ever loses its shape. Neighbouring shapes are
simplified one at a time, so their shared edges can differ by up to the
tolerance; at map zoom levels this is not visible. These shapes are for maps
only, never for measuring area (areas come from the Census tables).
"""

from __future__ import annotations

import gzip
import io
import json
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import shapefile

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources import state_fips as to_state_fips

__all__ = [
    "COUNTY_TOLERANCE",
    "DIGITS",
    "TRACT_TOLERANCE",
    "Shape",
    "douglas_peucker",
    "feature_collection",
    "fetch_boundaries",
    "read_counties",
    "read_shapes",
    "read_tracts",
    "ring_area",
    "simplify_geometry",
    "simplify_ring",
    "write_boundaries",
]

DIGITS = 5
"""Decimal places kept in GeoJSON coordinates (about 1 m)."""
COUNTY_TOLERANCE = 0.001
"""Douglas-Peucker tolerance for county outlines, in degrees (about 100 m)."""
TRACT_TOLERANCE = 0.0004
"""Douglas-Peucker tolerance for tract outlines, in degrees (about 40 m)."""
COUNTY_BUDGET_BYTES = 300_000
"""Largest allowed size of ``counties.geojson``."""
VINTAGE = "2020"


@dataclass(frozen=True)
class Shape:
    """One county or tract: its code, display name, land and water area, and geometry."""

    geoid: str
    name: str
    land_area_m2: int
    water_area_m2: int
    geometry: dict[str, Any]


# --------------------------------------------------------------------------- reading


def _shapefile_parts(source: bytes | str | Path) -> dict[str, io.BytesIO]:
    data = Path(source).read_bytes() if isinstance(source, str | Path) else bytes(source)
    parts: dict[str, io.BytesIO] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            ext = info.filename.rsplit(".", 1)[-1].lower()
            if ext in ("shp", "shx", "dbf") and ext not in parts:
                parts[ext] = io.BytesIO(zf.read(info))
    missing = [e for e in ("shp", "shx", "dbf") if e not in parts]
    if missing:
        raise ValueError(f"boundary zip lacks a .{', .'.join(missing)} file")
    return parts


def read_shapes(
    source: bytes | str | Path, keep: Callable[[Mapping[str, Any]], bool] | None = None
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Read a zipped shapefile; return ``(attributes, GeoJSON geometry)`` pairs.

    Args:
        source: The zip's bytes or a path to it.
        keep: Optional test on a record's attributes; rows it rejects are skipped
            before their shapes are converted.
    """
    parts = _shapefile_parts(source)
    reader = shapefile.Reader(shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"])
    try:
        out = []
        for item in reader.iterShapeRecords():
            attrs = item.record.as_dict()
            if keep is not None and not keep(attrs):
                continue
            geometry = item.shape.__geo_interface__
            if geometry["type"] not in ("Polygon", "MultiPolygon"):
                raise ValueError(f"{attrs.get('GEOID')}: expected a polygon shape")
            out.append((attrs, _plain(geometry)))
    finally:
        reader.close()
    return out


def _plain(geometry: Mapping[str, Any]) -> dict[str, Any]:
    """Turn pyshp's tuples into lists so the geometry is plain JSON."""
    polys = geometry["coordinates"]
    if geometry["type"] == "Polygon":
        polys = [polys]
    return {
        "type": "MultiPolygon",
        "coordinates": [[[[float(x), float(y)] for x, y in ring] for ring in p] for p in polys],
    }


def _to_shape(attrs: Mapping[str, Any], geometry: dict[str, Any]) -> Shape:
    return Shape(
        geoid=str(attrs["GEOID"]).strip(),
        name=str(attrs.get("NAMELSAD") or attrs.get("NAME") or "").strip(),
        land_area_m2=int(attrs.get("ALAND") or 0),
        water_area_m2=int(attrs.get("AWATER") or 0),
        geometry=geometry,
    )


def read_counties(source: bytes | str | Path, state_fips: str) -> list[Shape]:
    """Read the national county file and return the state's counties, sorted by code."""
    rows = read_shapes(source, keep=lambda a: str(a.get("STATEFP")) == state_fips)
    return sorted((_to_shape(a, g) for a, g in rows), key=lambda s: s.geoid)


def read_tracts(source: bytes | str | Path, state_fips: str | None = None) -> list[Shape]:
    """Read a state tract file and return its tracts, sorted by code."""
    keep = None if state_fips is None else (lambda a: str(a.get("STATEFP")) == state_fips)
    return sorted((_to_shape(a, g) for a, g in read_shapes(source, keep)), key=lambda s: s.geoid)


# --------------------------------------------------------------------------- simplify


def douglas_peucker(points: np.ndarray, tolerance: float) -> np.ndarray:
    """Return the indices of the points kept by Douglas-Peucker simplification.

    Args:
        points: Array of shape ``(n, 2)``: an open line from its first to last point.
        tolerance: Largest allowed distance, in the points' own units, between a
            dropped point and the simplified line.

    Returns:
        Sorted indices; always includes the first and last point.
    """
    pts = np.asarray(points, dtype=float)
    n = len(pts)
    if n <= 2:
        return np.arange(n)
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue
        a, b = pts[start], pts[end]
        inner = pts[start + 1 : end]
        ab = b - a
        length = float(np.hypot(*ab))
        if length == 0.0:
            dist = np.hypot(*(inner - a).T)
        else:
            dist = np.abs(ab[0] * (inner[:, 1] - a[1]) - ab[1] * (inner[:, 0] - a[0])) / length
        at = int(np.argmax(dist))
        if dist[at] > tolerance:
            mid = start + 1 + at
            keep[mid] = True
            stack.append((start, mid))
            stack.append((mid, end))
    return np.flatnonzero(keep)


def ring_area(ring: Iterable[Iterable[float]]) -> float:
    """Signed area of a closed ring (shoelace formula): positive when counter-clockwise."""
    pts = np.asarray(list(ring), dtype=float)
    if len(pts) < 3:
        return 0.0
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.sum(x[:-1] * y[1:] - x[1:] * y[:-1]) + (x[-1] * y[0] - x[0] * y[-1]))


def _round_ring(pts: np.ndarray, digits: int) -> list[list[float]]:
    out: list[list[float]] = []
    for x, y in np.round(pts, digits):
        point = [float(x), float(y)]
        if not out or out[-1] != point:
            out.append(point)
    if out and out[0] != out[-1]:
        out.append(out[0])
    return out


def simplify_ring(
    ring: Iterable[Iterable[float]], tolerance: float, digits: int = DIGITS
) -> list[list[float]] | None:
    """Simplify one closed ring; return it rounded and closed, or ``None`` if it collapses.

    The ring is cut at its first point and at the point farthest from it, and each
    half is simplified on its own, so the two cut points are always kept.
    """
    pts = np.asarray(list(ring), dtype=float)
    if len(pts) and np.array_equal(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    far = int(np.argmax(np.hypot(*(pts - pts[0]).T)))
    if far == 0:
        return None
    first = pts[: far + 1]
    second = np.vstack([pts[far:], pts[:1]])
    kept_a = first[douglas_peucker(first, tolerance)]
    kept_b = second[douglas_peucker(second, tolerance)]
    out = _round_ring(np.vstack([kept_a, kept_b[1:]]), digits)
    if len(out) < 4 or ring_area(out) == 0.0:
        return None
    return out


def _orient(ring: list[list[float]], outer: bool) -> list[list[float]]:
    ccw = ring_area(ring) > 0
    return ring if ccw == outer else ring[::-1]


def simplify_geometry(
    geometry: Mapping[str, Any], tolerance: float, digits: int = DIGITS
) -> dict[str, Any]:
    """Simplify a Polygon or MultiPolygon; return a Polygon or MultiPolygon in RFC 7946 order.

    Collapsed rings are dropped. If every outer ring collapses, the largest one is
    simplified again at finer tolerances (down to none) so the shape is never empty.
    """
    polys = geometry["coordinates"]
    if geometry["type"] == "Polygon":
        polys = [polys]
    out = []
    for poly in polys:
        outer = simplify_ring(poly[0], tolerance, digits)
        if outer is None:
            continue
        holes = [h for h in (simplify_ring(r, tolerance, digits) for r in poly[1:]) if h]
        out.append([_orient(outer, True), *(_orient(h, False) for h in holes)])
    if not out:
        largest = max(polys, key=lambda p: abs(ring_area(p[0])))
        for tol in (tolerance / 4, tolerance / 16, 0.0):
            outer = simplify_ring(largest[0], tol, digits)
            if outer is not None:
                out = [[_orient(outer, True)]]
                break
        else:
            raise ValueError("a shape collapsed to nothing even without simplification")
    if len(out) == 1:
        return {"type": "Polygon", "coordinates": out[0]}
    return {"type": "MultiPolygon", "coordinates": out}


# --------------------------------------------------------------------------- writing


def feature_collection(shapes: Iterable[Shape], level: str, tolerance: float) -> str:
    """Return a compact GeoJSON FeatureCollection text with ``level, geoid, name`` properties."""
    features = [
        {
            "type": "Feature",
            "properties": {"level": level, "geoid": s.geoid, "name": s.name},
            "geometry": simplify_geometry(s.geometry, tolerance),
        }
        for s in sorted(shapes, key=lambda s: s.geoid)
    ]
    text = json.dumps(
        {"type": "FeatureCollection", "features": features},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return text + "\n"


def write_boundaries(
    counties: Iterable[Shape],
    tracts: Iterable[Shape],
    out_dir: Path,
    *,
    county_tolerance: float = COUNTY_TOLERANCE,
    tract_tolerance: float = TRACT_TOLERANCE,
) -> list[Path]:
    """Write ``counties.geojson`` and one ``tracts/{county}.geojson`` per county.

    Raises:
        ValueError: If the county file is over :data:`COUNTY_BUDGET_BYTES`.

    Returns:
        The files written, sorted.
    """
    out_dir = Path(out_dir)
    (out_dir / "tracts").mkdir(parents=True, exist_ok=True)
    written = []
    county_text = feature_collection(counties, "county", county_tolerance)
    if len(county_text.encode("utf-8")) > COUNTY_BUDGET_BYTES:
        raise ValueError(
            f"counties.geojson is {len(county_text.encode('utf-8')):,} bytes, "
            f"over the {COUNTY_BUDGET_BYTES:,} byte budget; raise the tolerance"
        )
    path = out_dir / "counties.geojson"
    path.write_text(county_text, encoding="utf-8")
    written.append(path)
    by_county: dict[str, list[Shape]] = {}
    for t in tracts:
        by_county.setdefault(t.geoid[:5], []).append(t)
    for county, shapes in sorted(by_county.items()):
        path = out_dir / "tracts" / f"{county}.geojson"
        path.write_text(feature_collection(shapes, "tract", tract_tolerance), encoding="utf-8")
        written.append(path)
    return sorted(written)


# --------------------------------------------------------------------------- fetch


def _extract_path(cache: Path, name: str) -> Path:
    return Path(cache) / "boundaries" / f"{name}.geojson.gz"


def _save_extract(shapes: list[Shape], dest: Path) -> None:
    rows = [
        {
            "geoid": s.geoid,
            "name": s.name,
            "land_area_m2": s.land_area_m2,
            "water_area_m2": s.water_area_m2,
            "geometry": s.geometry,
        }
        for s in shapes
    ]
    data = json.dumps(rows, separators=(",", ":")).encode("utf-8")
    part = dest.with_name(dest.name + ".part")
    try:
        with (
            part.open("wb") as raw,
            gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz,
        ):
            gz.write(data)
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)


def _load_extract(path: Path) -> list[Shape]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [Shape(**row) for row in json.load(fh)]


def _fetch_one(
    client: HttpClient,
    source_id: str,
    url: str,
    dest: Path,
    read: Callable[[Path], list[Shape]],
    manifest: Path | None,
) -> list[Shape]:
    if dest.exists():
        return _load_extract(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    zip_path = dest.with_name(url.rsplit("/", 1)[-1])
    try:
        client.download(url, zip_path)
        shapes = read(zip_path)
    finally:
        zip_path.unlink(missing_ok=True)  # raw zips are never kept
    if not shapes:
        raise ValueError(f"{url}: no shapes for this state")
    _save_extract(shapes, dest)
    if manifest is not None:
        mf.record(mf.entry_for_file(source_id, url, dest, VINTAGE, root=mf.ROOT), manifest)
    return shapes


def fetch_boundaries(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> tuple[list[Shape], list[Shape]]:
    """Fetch (or reuse) the state's county and tract shapes at full detail.

    Returns:
        ``(counties, tracts)``, each sorted by code.
    """
    fips = to_state_fips(state)
    client = client or HttpClient()
    county_url = get_source("cb_2020_county").url()
    counties = _fetch_one(
        client,
        "cb_2020_county",
        county_url,
        _extract_path(cache, f"cb_2020_{fips}_county_500k"),
        lambda p: read_counties(p, fips),
        manifest,
    )
    tract_url = get_source("cb_2020_tract").url(state_fips=fips)
    tracts = _fetch_one(
        client,
        "cb_2020_tract",
        tract_url,
        _extract_path(cache, f"cb_2020_{fips}_tract_500k"),
        lambda p: read_tracts(p, fips),
        manifest,
    )
    return counties, tracts
