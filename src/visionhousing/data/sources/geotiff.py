"""A small GeoTIFF reader: enough to read the single-band grids the hazard screen asks for.

Plain-language summary
----------------------
The hazard screen (:mod:`visionhousing.features.hazards`) asks two public map
services for grids of numbers over one county at a time: the USGS land-cover
and impervious-surface grids (Annual NLCD, through the MRLC coverage service)
and a USGS elevation grid (3DEP, through its image service). Both answer with a
**GeoTIFF**: a TIFF image whose tags also say where on the map the grid sits.

The usual libraries for this (GDAL, rasterio) are large compiled packages. This
module reads only what those two services send, in plain Python and NumPy:

- byte order: little-endian (``II``) or big-endian (``MM``), classic TIFF only;
- one band of 8-bit or 16-bit integers or 32-bit floats;
- the pixels stored in strips or in tiles, uncompressed or Deflate-compressed;
  a tile or strip left out ("sparse", byte count 0) reads as no data
  (TIFF compression codes 1, 8 and 32946) with no predictor;
- where the grid sits: the ``ModelTransformationTag`` (34264), or the
  ``ModelTiepointTag`` (33922) with the ``ModelPixelScaleTag`` (33550);
- the "no data" value from the ``GDAL_NODATA`` tag (42113), when present.

Anything else (LZW or JPEG compression, predictors, several bands, BigTIFF)
raises :class:`GeoTiffError`, so an unexpected answer stops the build instead
of being misread. The hazard sources ask the services for uncompressed files.

:class:`Grid` holds the result: the array (row 0 is the northern edge), the
map coordinates of the grid's top-left corner, the pixel size, and the no-data
value. Pixel ``(row, col)`` covers ``x0 + col*dx`` to ``x0 + (col+1)*dx`` and
``y0 - row*dy`` to ``y0 - (row+1)*dy``.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

import numpy as np

__all__ = ["GeoTiffError", "Grid", "read_geotiff", "write_geotiff"]

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8}
_TYPE_CHAR = {1: "B", 3: "H", 4: "I", 6: "b", 7: "B", 8: "h", 9: "i", 11: "f", 12: "d", 16: "Q"}
_NO_COMPRESSION = 1
_DEFLATE = (8, 32946)


class GeoTiffError(ValueError):
    """The file is not a GeoTIFF this reader understands."""


@dataclass(frozen=True)
class Grid:
    """One band of a GeoTIFF and where it sits on the map.

    Attributes:
        values: The pixels, shape ``(rows, cols)``; row 0 is the northern edge.
        x0: Map x of the grid's left edge.
        y0: Map y of the grid's top edge.
        dx: Pixel width in map units (positive).
        dy: Pixel height in map units (positive; rows run south).
        nodata: The "no data" value, or ``None``.
    """

    values: np.ndarray
    x0: float
    y0: float
    dx: float
    dy: float
    nodata: float | None = None

    @property
    def shape(self) -> tuple[int, int]:
        """``(rows, cols)``."""
        return self.values.shape  # type: ignore[return-value]


def _tags(data: bytes) -> tuple[str, dict[int, tuple[int, tuple]]]:
    if len(data) < 8:
        raise GeoTiffError("file too short to be a TIFF")
    order = {b"II": "<", b"MM": ">"}.get(data[:2])
    if order is None:
        start = data[:60].decode("latin-1", "replace")
        raise GeoTiffError(f"not a TIFF (starts with {start!r})")
    (magic,) = struct.unpack(order + "H", data[2:4])
    if magic != 42:
        raise GeoTiffError(f"unsupported TIFF magic number {magic} (BigTIFF is not read)")
    (offset,) = struct.unpack(order + "I", data[4:8])
    (count,) = struct.unpack(order + "H", data[offset : offset + 2])
    tags: dict[int, tuple[int, tuple]] = {}
    for i in range(count):
        entry = data[offset + 2 + 12 * i : offset + 14 + 12 * i]
        tag, kind, n = struct.unpack(order + "HHI", entry[:8])
        size = _TYPE_SIZE.get(kind, 1) * n
        if size <= 4:
            raw = entry[8 : 8 + size]
        else:
            (where,) = struct.unpack(order + "I", entry[8:12])
            raw = data[where : where + size]
        if kind == 2:
            tags[tag] = (kind, (raw.rstrip(b"\x00").decode("latin-1"),))
        elif kind in (5, 10):
            char = "I" if kind == 5 else "i"
            pairs = struct.unpack(order + char * (2 * n), raw)
            tags[tag] = (kind, tuple(pairs[2 * k] / pairs[2 * k + 1] for k in range(n)))
        elif kind in _TYPE_CHAR:
            tags[tag] = (kind, struct.unpack(order + _TYPE_CHAR[kind] * n, raw))
    return order, tags


def _one(tags: dict[int, tuple[int, tuple]], tag: int, default: object = None) -> object:
    if tag not in tags:
        if default is None:
            raise GeoTiffError(f"TIFF tag {tag} is missing")
        return default
    return tags[tag][1][0]


def _dtype(order: str, bits: int, sample_format: int) -> np.dtype:
    kinds = {
        (8, 1): "u1",
        (8, 2): "i1",
        (16, 1): "u2",
        (16, 2): "i2",
        (32, 1): "u4",
        (32, 2): "i4",
        (32, 3): "f4",
        (64, 3): "f8",
    }
    code = kinds.get((bits, sample_format))
    if code is None:
        raise GeoTiffError(f"unsupported pixel type: {bits} bits, sample format {sample_format}")
    return np.dtype(order + code)


def read_geotiff(data: bytes) -> Grid:
    """Read the first band of a GeoTIFF held in memory.

    Args:
        data: The whole file.

    Returns:
        The grid and its placement.

    Raises:
        GeoTiffError: The file uses a feature this reader does not handle.
    """
    order, tags = _tags(data)
    width = int(_one(tags, 256))  # type: ignore[arg-type]
    height = int(_one(tags, 257))  # type: ignore[arg-type]
    bits = int(_one(tags, 258, 1))  # type: ignore[arg-type]
    compression = int(_one(tags, 259, 1))  # type: ignore[arg-type]
    samples = int(_one(tags, 277, 1))  # type: ignore[arg-type]
    predictor = int(_one(tags, 317, 1))  # type: ignore[arg-type]
    sample_format = int(_one(tags, 339, 1))  # type: ignore[arg-type]
    planar = int(_one(tags, 284, 1))  # type: ignore[arg-type]
    if samples != 1 and planar != 2:
        raise GeoTiffError(f"{samples} bands interleaved; only one band is read")
    if compression != _NO_COMPRESSION and compression not in _DEFLATE:
        raise GeoTiffError(f"unsupported TIFF compression {compression}")
    if predictor != 1:
        raise GeoTiffError(f"unsupported TIFF predictor {predictor}")
    dtype = _dtype(order, bits, sample_format)

    def unpack(offset: int, count: int) -> bytes:
        raw = data[offset : offset + count]
        return zlib.decompress(raw) if compression in _DEFLATE else raw

    nodata = None
    if 42113 in tags:
        text = str(tags[42113][1][0]).strip()
        try:
            nodata = float(text)
        except ValueError:
            nodata = None
    # A tile or strip with no bytes (offset and count 0) is "sparse": the writer
    # left it out because every cell is empty. It reads as the no-data value,
    # else NaN for floating-point grids (0 for whole numbers).
    empty = nodata if nodata is not None else np.nan if dtype.kind == "f" else 0
    out = np.full((height, width), empty, dtype=dtype.newbyteorder("="))
    if 322 in tags:  # tiles
        tw, th = int(_one(tags, 322)), int(_one(tags, 323))  # type: ignore[arg-type]
        offsets, counts = tags[324][1], tags[325][1]
        across = -(-width // tw)
        for k, (off, cnt) in enumerate(zip(offsets, counts, strict=True)):
            row0, col0 = (k // across) * th, (k % across) * tw
            if row0 >= height:
                break  # further tiles belong to other bands
            if cnt == 0:
                continue  # sparse tile: left as no data
            tile = np.frombuffer(unpack(off, cnt), dtype=dtype, count=tw * th).reshape(th, tw)
            rows, cols = min(th, height - row0), min(tw, width - col0)
            out[row0 : row0 + rows, col0 : col0 + cols] = tile[:rows, :cols]
    else:  # strips
        per_strip = int(_one(tags, 278, height))  # type: ignore[arg-type]
        offsets, counts = tags[273][1], tags[279][1]
        row = 0
        for off, cnt in zip(offsets, counts, strict=True):
            if row >= height:
                break
            rows = min(per_strip, height - row)
            if cnt == 0:
                row += rows
                continue  # sparse strip: left as no data
            strip = np.frombuffer(unpack(off, cnt), dtype=dtype, count=rows * width)
            out[row : row + rows] = strip.reshape(rows, width)
            row += rows
    if 34264 in tags:
        m = tags[34264][1]
        if abs(m[1]) > 1e-9 or abs(m[4]) > 1e-9:
            raise GeoTiffError("rotated grids are not read")
        dx, x0, dy, y0 = float(m[0]), float(m[3]), -float(m[5]), float(m[7])
    elif 33922 in tags and 33550 in tags:
        tie, scale = tags[33922][1], tags[33550][1]
        dx, dy = float(scale[0]), float(scale[1])
        x0 = float(tie[3]) - float(tie[0]) * dx
        y0 = float(tie[4]) + float(tie[1]) * dy
    else:
        raise GeoTiffError("no map placement tags (34264, or 33922 with 33550)")
    return Grid(out, x0, y0, dx, dy, nodata)


def write_geotiff(grid: Grid, *, tile: int | None = None, deflate: bool = False) -> bytes:
    """Write a one-band little-endian GeoTIFF (used by tests to make small fixtures).

    Args:
        grid: The values and placement.
        tile: Tile size in pixels, or ``None`` for one strip per row.
        deflate: Deflate-compress the pixel blocks.

    Returns:
        The file's bytes.
    """
    values = np.ascontiguousarray(grid.values)
    dtype = values.dtype.newbyteorder("<")
    values = values.astype(dtype)
    kind = {"u": 1, "i": 2, "f": 3}[dtype.kind]
    height, width = values.shape
    blocks: list[bytes] = []
    if tile:
        for r in range(0, height, tile):
            for c in range(0, width, tile):
                block = np.zeros((tile, tile), dtype=dtype)
                part = values[r : r + tile, c : c + tile]
                block[: part.shape[0], : part.shape[1]] = part
                blocks.append(block.tobytes())
    else:
        blocks = [values[r].tobytes() for r in range(height)]
    if deflate:
        blocks = [zlib.compress(b) for b in blocks]
    entries: list[tuple[int, int, tuple]] = [
        (256, 3, (width,)),
        (257, 3, (height,)),
        (258, 3, (dtype.itemsize * 8,)),
        (259, 3, (8 if deflate else 1,)),
        (262, 3, (1,)),
        (277, 3, (1,)),
        (339, 3, (kind,)),
        (33550, 12, (grid.dx, grid.dy, 0.0)),
        (33922, 12, (0.0, 0.0, 0.0, grid.x0, grid.y0, 0.0)),
    ]
    if tile:
        entries += [(322, 3, (tile,)), (323, 3, (tile,))]
    else:
        entries += [(278, 3, (1,))]
    if grid.nodata is not None:
        entries.append((42113, 2, (f"{grid.nodata:g}",)))
    entries.sort()
    # Layout: header, pixel blocks, then the offset/count arrays, extra values, the directory.
    head = b"II" + struct.pack("<HI", 42, 0)
    body = bytearray(head)
    offsets = []
    for b in blocks:
        offsets.append(len(body))
        body += b
    off_tag, cnt_tag = (324, 325) if tile else (273, 279)
    entries += [(off_tag, 4, tuple(offsets)), (cnt_tag, 4, tuple(len(b) for b in blocks))]
    entries.sort()
    extra = bytearray()
    packed: list[tuple[int, int, int, bytes]] = []
    for tag, typ, vals in entries:
        if typ == 2:
            raw = str(vals[0]).encode("latin-1") + b"\x00"
            n = len(raw)
        else:
            raw = struct.pack("<" + _TYPE_CHAR[typ] * len(vals), *vals)
            n = len(vals)
        packed.append((tag, typ, n, raw))
    extra_start = len(body)
    directory_entries = []
    for tag, typ, n, raw in packed:
        if len(raw) <= 4:
            value = raw.ljust(4, b"\x00")
        else:
            value = struct.pack("<I", extra_start + len(extra))
            extra += raw
            if len(extra) % 2:
                extra += b"\x00"
        directory_entries.append(struct.pack("<HHI", tag, typ, n) + value)
    body += extra
    ifd = len(body)
    body += struct.pack("<H", len(directory_entries)) + b"".join(directory_entries)
    body += struct.pack("<I", 0)
    body[4:8] = struct.pack("<I", ifd)
    return bytes(body)
