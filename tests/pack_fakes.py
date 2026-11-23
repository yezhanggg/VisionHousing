"""Shared test helpers for the block, boundary and pack tests: synthetic inputs, fake servers.

Everything here is built from the committed Testland fixture, so the tests need no
network and no downloaded data.
"""

from __future__ import annotations

import email.message
import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import shapefile

from visionhousing.data.http import HttpClient
from visionhousing.data.sources import boundaries

TESTLAND = Path(__file__).resolve().parent / "fixtures" / "testland"
PLENTY = 10**13


class Response:
    """A urllib-like response with a status, headers, ``read`` and ``close``."""

    def __init__(self, status: int, body: bytes, length: int | None = None):
        self.status = status
        self._buf = io.BytesIO(body)
        self.headers = email.message.Message()
        self.headers["Content-Length"] = str(len(body) if length is None else length)

    def read(self, n=-1):
        return self._buf.read(n)

    def close(self):
        pass


class FileServer:
    """Serves in-memory files by URL; honours HEAD and Range; counts bytes sent."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.sent = 0
        self.urls: list[str] = []

    def open(self, req, data=None, timeout=None):
        url = req.full_url
        self.urls.append(url)
        blob = self.files[url]
        if req.get_method() == "HEAD":
            return Response(200, b"", length=len(blob))
        rng = req.get_header("Range")
        if rng:
            start, end = (int(x) for x in rng.removeprefix("bytes=").split("-"))
            part = blob[start : end + 1]
            self.sent += len(part)
            return Response(206, part)
        self.sent += len(blob)
        return Response(200, blob)


def client(server) -> HttpClient:
    """An HTTP client on a fake server, with no waiting and plenty of disk."""
    return HttpClient(opener=server, sleep=lambda s: None, free_bytes=lambda p: PLENTY)


# --------------------------------------------------------------------------- blocks


def sample_blocks() -> pd.DataFrame:
    """Testland's blocks with the extra block-file fields filled in deterministically."""
    b = pd.read_csv(TESTLAND / "blocks.csv", dtype={"geoid": str, "tract": str})
    b["population"] = (b["housing_units"] * 2.3).round().astype("int64")
    b["water_area_m2"] = 0
    b["urban"] = (b["housing_units"] >= 50).astype("int64")
    b["county"] = b["tract"].str[:5]
    return b


def block_dbf(rows: list[dict], *, drop: str | None = None) -> bytes:
    """Write a block attribute table (``.dbf``) in the TIGER field layout."""
    fields = [
        ("STATEFP20", "C", 2, 0),
        ("GEOID20", "C", 15, 0),
        ("UR20", "C", 5, 0),
        ("ALAND20", "N", 14, 0),
        ("AWATER20", "N", 14, 0),
        ("INTPTLAT20", "C", 11, 0),
        ("INTPTLON20", "C", 12, 0),
        ("HOUSING20", "N", 12, 0),
        ("POP20", "N", 12, 0),
    ]
    fields = [f for f in fields if f[0] != drop]
    buf = io.BytesIO()
    w = shapefile.Writer(dbf=buf)
    for f in fields:
        w.field(*f)
    for r in rows:
        values = {
            "STATEFP20": r["geoid"][:2],
            "GEOID20": r["geoid"],
            "UR20": "U" if r["urban"] else "R",
            "ALAND20": r["land_area_m2"],
            "AWATER20": r["water_area_m2"],
            "INTPTLAT20": f"{r['lat']:+011.7f}",
            "INTPTLON20": f"{r['lon']:+012.7f}",
            "HOUSING20": r["housing_units"],
            "POP20": r["population"],
        }
        w.record(*[values[f[0]] for f in fields])
    w.close()
    return buf.getvalue()


def zip_of(members: dict[str, bytes], stored: bool = True) -> bytes:
    """A zip holding the given members (stored, like the Census files, unless told)."""
    buf = io.BytesIO()
    method = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data, method)
    return buf.getvalue()


# --------------------------------------------------------------------------- shapes


def sample_features(level: str) -> list[dict]:
    """Testland's county or tract features from its committed GeoJSON."""
    data = json.loads((TESTLAND / "boundaries.geojson").read_text(encoding="utf-8"))
    return [f for f in data["features"] if f["properties"]["level"] == level]


def shape_zip(features: list[dict], name_field: str = "NAMELSAD") -> bytes:
    """Write features as a zipped polygon shapefile in the Census field layout.

    ``STATEFP`` is the first two digits of each feature's ``geoid``.
    """
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
    w.field("STATEFP", "C", 2)
    w.field("GEOID", "C", 11)
    w.field(name_field, "C", 40)
    w.field("ALAND", "N", 14)
    w.field("AWATER", "N", 14)
    for f in features:
        geom = f["geometry"]
        polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        rings = []
        for poly in polys:
            for i, ring in enumerate(poly):
                cw = boundaries.ring_area(ring) < 0
                want_cw = i == 0  # shapefiles: outer rings clockwise, holes counter-clockwise
                rings.append(ring if cw == want_cw else ring[::-1])
        w.poly(rings)
        geoid = f["properties"]["geoid"]
        w.record(geoid[:2], geoid, f["properties"]["name"], 1000, 0)
    w.close()
    return zip_of({"x.shp": shp.getvalue(), "x.shx": shx.getvalue(), "x.dbf": dbf.getvalue()})


def sample_shapes(level: str) -> list[boundaries.Shape]:
    """Testland's features as :class:`boundaries.Shape` objects."""
    out = []
    for f in sample_features(level):
        geom = f["geometry"]
        coords = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        out.append(
            boundaries.Shape(
                geoid=f["properties"]["geoid"],
                name=f["properties"]["name"],
                land_area_m2=1000,
                water_area_m2=0,
                geometry={"type": "MultiPolygon", "coordinates": coords},
            )
        )
    return sorted(out, key=lambda s: s.geoid)
