"""Fetch the hazard-screen grids and flood zones for one area, keyless, from the public services.

Plain-language summary
----------------------
Three public services answer for any box on the map; this module asks each of
them for one county's box at a time (see :mod:`visionhousing.features.hazards_build`):

- **FEMA National Flood Hazard Layer** (``fema_nfhl``), FEMA's public map
  service: the flood-zone polygons inside the Special Flood Hazard Area
  (``SFHA_TF = 'T'``), page by page, and the "NFHL availability" polygons that
  show where FEMA has a digital flood map at all. FEMA's server returns them in
  the national Albers projection, with vertices closer than a few metres
  thinned (``maxAllowableOffset``), which is far finer than the 30 m grid they
  are burned onto.
- **Annual NLCD** (``usgs_annual_nlcd``), USGS land cover and fractional
  impervious surface for one year, through the MRLC coverage service (WCS 1.0)
  as an uncompressed GeoTIFF on the NLCD's own 30 m grid.
- **3DEP elevation** (``usgs_3dep_dem``), the USGS bare-earth elevation image
  service, resampled by the service to the same 30 m grid (bilinear), as 32-bit
  floats.

Nothing is saved but the answers' checksums: grids and polygons are used in
memory and dropped, and only per-tract counts are kept
(:func:`visionhousing.features.hazards.tract_counts`). Each answer is checked:
a grid must sit exactly where it was asked for, and the polygon pages must add
up to the count the server gives.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from visionhousing.data.http import FetchError, HttpClient
from visionhousing.data.sources.geotiff import GeoTiffError, Grid, read_geotiff
from visionhousing.features.hazards import GridSpec

__all__ = [
    "DEM_URL",
    "NFHL_URL",
    "NLCD_COVERAGES",
    "WCS_URL",
    "Reads",
    "dem_grid",
    "flood_zones",
    "nfhl_availability",
    "nlcd_grid",
]

NFHL_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
FLOOD_LAYER = 28
AVAILABILITY_LAYER = 0
WCS_URL = "https://www.mrlc.gov/geoserver/ows"
NLCD_COVERAGES = {
    "landcover": "mrlc_Land-Cover-Native_conus_year_data:Land-Cover-Native_conus_year_data",
    "impervious": (
        "mrlc_Fractional-Impervious-Surface-Native_conus_year_data:"
        "Fractional-Impervious-Surface-Native_conus_year_data"
    ),
}
DEM_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
PAGE = 1000
SOURCE_NFHL = "fema_nfhl"
SOURCE_NLCD = "usgs_annual_nlcd"
SOURCE_DEM = "usgs_3dep_dem"


@dataclass
class Reads:
    """A log of what was read: one row per answer (source, request, checksum, bytes)."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def add(self, source: str, what: str, data: bytes) -> None:
        """Record one answer."""
        self.rows.append(
            {
                "source": source,
                "request": what,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )


def _bbox_text(grid: GridSpec) -> str:
    xmin, ymin, xmax, ymax = grid.bbox
    return f"{xmin:.0f},{ymin:.0f},{xmax:.0f},{ymax:.0f}"


def _check_grid(got: Grid, grid: GridSpec, what: str) -> Grid:
    tol = grid.cell * 0.01
    if got.shape != (grid.rows, grid.cols):
        raise FetchError(f"{what}: got a {got.shape} grid, asked for {(grid.rows, grid.cols)}")
    if abs(got.x0 - grid.x0) > tol or abs(got.y0 - grid.y0) > tol:
        raise FetchError(
            f"{what}: grid placed at ({got.x0}, {got.y0}), asked for ({grid.x0}, {grid.y0})"
        )
    if abs(got.dx - grid.cell) > tol or abs(got.dy - grid.cell) > tol:
        raise FetchError(f"{what}: cell size {got.dx} x {got.dy}, asked for {grid.cell}")
    return got


def nlcd_grid(
    client: HttpClient, product: str, year: int, grid: GridSpec, reads: Reads | None = None
) -> Grid:
    """One Annual NLCD grid (``landcover`` or ``impervious``) for ``year`` over ``grid``.

    Raises:
        FetchError: The service answered with an error or a grid placed elsewhere.
    """
    params = {
        "service": "WCS",
        "version": "1.0.0",
        "request": "GetCoverage",
        "coverage": NLCD_COVERAGES[product],
        "crs": "EPSG:5070",
        "bbox": _bbox_text(grid),
        "resx": f"{grid.cell:g}",
        "resy": f"{grid.cell:g}",
        "format": "GeoTIFF",
        "time": f"{int(year)}-01-01T00:00:00.000Z",
    }
    data = client.get_bytes(WCS_URL, params)
    if reads is not None:
        reads.add(SOURCE_NLCD, f"{product} {year} bbox={params['bbox']}", data)
    try:
        got = read_geotiff(data)
    except ValueError as err:
        raise FetchError(f"NLCD {product} {year}: {err}") from None
    return _check_grid(got, grid, f"NLCD {product} {year}")


DEM_TILE = 2000
"""Largest side (cells) of one elevation request. The service allows 8,000 x 8,000
(``maxImageWidth`` / ``maxImageHeight`` in its JSON), but big boxes sometimes come
back short, so a county box is asked for in tiles of at most 2,000 x 2,000 cells."""
DEM_TRIES = 3
"""Times one elevation tile is asked for before the build stops."""


def _dem_tile(client: HttpClient, grid: GridSpec, reads: Reads | None) -> Grid:
    params = {
        "bbox": _bbox_text(grid),
        "bboxSR": "5070",
        "imageSR": "5070",
        "size": f"{grid.cols},{grid.rows}",
        "format": "tiff",
        "pixelType": "F32",
        "noDataInterpretation": "esriNoDataMatchAny",
        "interpolation": "RSP_BilinearInterpolation",
        "compression": "None",
        "f": "image",
    }
    # The reply's size is not checked against rows x cols x 4: the service
    # leaves out tiles with no land (offset and byte count 0, "sparse"), which
    # the reader fills as no data. A cut-off reply fails to read and is retried.
    last = ""
    for _ in range(DEM_TRIES):
        data = client.get_bytes(f"{DEM_URL}/exportImage", params)
        if len(data) < 8:
            last = f"short reply: {len(data):,} bytes"
            continue
        try:
            got = read_geotiff(data)
        except (GeoTiffError, ValueError, struct.error) as err:  # a cut-off file
            last = str(err)
            continue
        if reads is not None:
            reads.add(SOURCE_DEM, f"elevation bbox={params['bbox']}", data)
        return _check_grid(got, grid, "3DEP elevation")
    raise FetchError(f"3DEP elevation: {last} (bbox {params['bbox']}, {DEM_TRIES} tries)")


def dem_grid(client: HttpClient, grid: GridSpec, reads: Reads | None = None) -> Grid:
    """3DEP bare-earth elevation (metres) resampled by the service onto ``grid``.

    A large box is asked for in tiles of at most :data:`DEM_TILE` cells a side,
    each on the same aligned grid, and joined. A tile whose reply is not a
    readable GeoTIFF (for example cut off) is asked for again (up to
    :data:`DEM_TRIES` times). Tiles the service leaves out because they hold
    no land (over the sea) read as no data.

    Raises:
        FetchError: The service kept answering short, with an error, or with a
            grid placed elsewhere.
    """
    values = np.empty((grid.rows, grid.cols), dtype=np.float32)
    nodata = None
    for r0 in range(0, grid.rows, DEM_TILE):
        for c0 in range(0, grid.cols, DEM_TILE):
            rows = slice(r0, min(r0 + DEM_TILE, grid.rows))
            cols = slice(c0, min(c0 + DEM_TILE, grid.cols))
            tile = _dem_tile(client, grid.sub(rows, cols), reads)
            values[rows, cols] = tile.values
            nodata = tile.nodata if nodata is None else nodata
    return Grid(values, grid.x0, grid.y0, grid.cell, grid.cell, nodata)


def _query(
    client: HttpClient,
    layer: int,
    grid: GridSpec,
    where: str,
    offset_m: float,
    reads: Reads | None,
    label: str,
) -> list[list[list[list[float]]]]:
    url = f"{NFHL_URL}/{layer}/query"
    base = {
        "where": where,
        "geometry": _bbox_text(grid),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "5070",
        "spatialRel": "esriSpatialRelIntersects",
        "f": "json",
    }
    raw = client.get_bytes(url, {**base, "returnCountOnly": "true"})
    count = json.loads(raw.decode("utf-8"))
    if "error" in count:
        raise FetchError(f"FEMA NFHL layer {layer}: {count['error']}")
    expected = int(count.get("count", 0))
    found: dict[int, list[list[list[float]]]] = {}
    offset = 0
    while True:
        params = {
            **base,
            "outFields": "OBJECTID",
            "returnGeometry": "true",
            "outSR": "5070",
            "maxAllowableOffset": f"{offset_m:g}",
            "geometryPrecision": "1",
            "orderByFields": "OBJECTID",
            "resultOffset": str(offset),
            "resultRecordCount": str(PAGE),
        }
        data = client.get_bytes(url, params)
        if reads is not None:
            reads.add(SOURCE_NFHL, f"{label} offset={offset} bbox={base['geometry']}", data)
        doc = json.loads(data.decode("utf-8"))
        if "error" in doc:
            raise FetchError(f"FEMA NFHL layer {layer}: {doc['error']}")
        page = doc.get("features", [])
        for k, f in enumerate(page):
            oid = (f.get("attributes") or {}).get("OBJECTID")
            key = int(oid) if oid is not None else -(offset + k + 1)
            found[key] = (f.get("geometry") or {}).get("rings", [])
        offset += len(page)
        if not page or (len(page) < PAGE and not doc.get("exceededTransferLimit")):
            break
    # The service's count can be one or two short of the polygons it then returns (its
    # count and its pages test the box against slightly different shapes); fewer is an error.
    if len(found) < expected:
        raise FetchError(f"FEMA NFHL layer {layer}: got {len(found)} of {expected} polygons")
    features = [found[k] for k in sorted(found)]
    return features


def flood_zones(
    client: HttpClient, grid: GridSpec, rules: dict[str, Any], reads: Reads | None = None
) -> list[list[list[list[float]]]]:
    """Special Flood Hazard Area polygons touching the grid, as Albers rings per polygon."""
    flood = rules["flood"]
    return _query(
        client,
        FLOOD_LAYER,
        grid,
        str(flood["where"]),
        float(flood["max_offset_m"]),
        reads,
        "flood zones",
    )


def nfhl_availability(
    client: HttpClient, grid: GridSpec, reads: Reads | None = None
) -> list[list[list[list[float]]]]:
    """FEMA's "NFHL availability" polygons (where a digital flood map exists) touching the grid."""
    return _query(client, AVAILABILITY_LAYER, grid, "1=1", grid.cell, reads, "availability")
