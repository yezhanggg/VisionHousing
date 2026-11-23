"""2020 Census blocks: housing units, people, land area and an inside point for every block.

Plain-language summary
----------------------
A census block is the smallest area the Census Bureau counts: usually a city
block in a town, or a larger piece of land bounded by roads and streams in the
country. Pennsylvania has 336,985 of them. The 2020 Census gives, for each one,
the number of housing units (``HOUSING20``), the number of people (``POP20``),
its land and water area in square metres (``ALAND20``, ``AWATER20``), whether
the Bureau classed it as urban or rural (``UR20``), and a point that lies inside
it (``INTPTLAT20`` / ``INTPTLON20``).

These numbers sit in the block file's attribute table: the ``.dbf`` file inside
``tl_2020_{state}_tabblock20.zip``. The zip also holds the block shapes, which
are large (360 MB of the 409 MB Pennsylvania zip) and which this tool never
needs. So the ``.dbf`` alone is read out of the remote zip with HTTP range
requests (asking the server for one byte range rather than the whole file):
:meth:`visionhousing.data.http.HttpClient.read_zip_member` reads the zip's index
and then only the bytes of the ``.dbf``.

The ``.dbf`` (a dBASE table, the format shapefiles use for attributes) is read
in memory with pyshp's table reader and is never written to disk. What is kept
in the cache is a small state extract, ``blocks_{fips}.csv.gz``: one row per
block with the columns of :data:`EXTRACT_COLUMNS`. The pack builder reads that
extract, so a rebuild needs no network.

Checks
------
- Every row's ``GEOID20`` is 15 digits and starts with the state code.
- Housing units, people and areas are whole numbers of zero or more.
- The internal point lies on Earth (latitude -90..90, longitude -180..180).
- The real-data test checks that Pennsylvania's blocks add up to the 2020
  Census count of 5,742,828 housing units.
"""

from __future__ import annotations

import gzip
import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import shapefile

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient, check_free_space
from visionhousing.data.registry import get_source
from visionhousing.data.sources import state_fips as to_state_fips

__all__ = [
    "DBF_FIELDS",
    "EXTRACT_COLUMNS",
    "LITE_COLUMNS",
    "BlockFetch",
    "blocks_lite",
    "fetch_blocks",
    "read_block_table",
    "read_extract",
    "write_extract",
]

SOURCE_ID = "tiger_2020_tabblock"
VINTAGE = "2020 Census"

DBF_FIELDS = {
    "GEOID20": "geoid",
    "HOUSING20": "housing_units",
    "POP20": "population",
    "ALAND20": "land_area_m2",
    "AWATER20": "water_area_m2",
    "UR20": "urban",
    "INTPTLAT20": "lat",
    "INTPTLON20": "lon",
}
"""Block-file field name to the name used here. Other fields are not read."""

EXTRACT_COLUMNS = (
    "geoid",
    "tract",
    "county",
    "housing_units",
    "population",
    "land_area_m2",
    "water_area_m2",
    "urban",
    "lat",
    "lon",
)
"""Columns of the cached state extract. ``urban`` is 1 for a Census-urban block, else 0."""

LITE_COLUMNS = ("geoid", "tract", "housing_units", "land_area_m2", "lat", "lon")
"""The Testland ``blocks`` columns: the pack's blocks table."""

COORD_DIGITS = 5
"""Decimal places kept for latitude and longitude (about 1 metre)."""

_INTEGERS = ("housing_units", "population", "land_area_m2", "water_area_m2")


@dataclass(frozen=True)
class BlockFetch:
    """What :func:`fetch_blocks` did: the extract, and how much of the zip was read."""

    path: Path
    rows: int
    member_bytes: int
    zip_bytes: int
    from_cache: bool

    @property
    def share_read(self) -> float:
        """The ``.dbf`` size as a share of the whole zip (0 when read from cache)."""
        return self.member_bytes / self.zip_bytes if self.zip_bytes else 0.0


def read_block_table(source: bytes | str | Path, state_fips: str | None = None) -> pd.DataFrame:
    """Read a block ``.dbf`` and return one row per block, sorted by ``geoid``.

    Args:
        source: The ``.dbf`` file's bytes, or a path to it.
        state_fips: When given, every block must belong to this state.

    Returns:
        The columns of :data:`EXTRACT_COLUMNS`.

    Raises:
        ValueError: If a field is missing or a value fails a check.
    """
    data = Path(source).read_bytes() if isinstance(source, str | Path) else bytes(source)
    reader = shapefile.Reader(dbf=io.BytesIO(data))
    try:
        names = [f[0] for f in reader.fields[1:]]
        missing = [f for f in DBF_FIELDS if f not in names]
        if missing:
            raise ValueError(f"block table has no field(s) {', '.join(missing)}")
        wanted = list(DBF_FIELDS)
        rows = [rec.as_dict() for rec in reader.iterRecords(fields=wanted)]
    finally:
        reader.close()
    frame = pd.DataFrame(rows, columns=wanted).rename(columns=DBF_FIELDS)
    return _clean(frame, state_fips)


def _clean(frame: pd.DataFrame, state_fips: str | None) -> pd.DataFrame:
    frame["geoid"] = frame["geoid"].astype(str).str.strip()
    bad = ~frame["geoid"].str.fullmatch(r"\d{15}")
    if bad.any():
        raise ValueError(f"block ids must be 15 digits; first bad: {frame['geoid'][bad].iloc[0]!r}")
    if state_fips is not None and not frame["geoid"].str.startswith(state_fips).all():
        raise ValueError(f"block table holds blocks outside state {state_fips}")
    if frame["geoid"].duplicated().any():
        raise ValueError("block table repeats a block id")
    for name in _INTEGERS:
        values = pd.to_numeric(frame[name], errors="raise")
        if values.isna().any() or (values < 0).any() or (values % 1 != 0).any():
            raise ValueError(f"block column {name} must hold whole numbers of zero or more")
        frame[name] = values.astype("int64")
    frame["urban"] = (frame["urban"].astype(str).str.strip().str.upper() == "U").astype("int64")
    for name, limit in (("lat", 90.0), ("lon", 180.0)):
        values = pd.to_numeric(frame[name].astype(str).str.strip(), errors="raise")
        if values.isna().any() or (values.abs() > limit).any():
            raise ValueError(f"block column {name} is outside -{limit:g}..{limit:g}")
        frame[name] = values.round(COORD_DIGITS)
    frame["tract"] = frame["geoid"].str[:11]
    frame["county"] = frame["geoid"].str[:5]
    return frame[list(EXTRACT_COLUMNS)].sort_values("geoid").reset_index(drop=True)


def write_extract(blocks: pd.DataFrame, dest: Path) -> Path:
    """Write the state extract as gzipped CSV, byte-for-byte the same for the same table.

    The gzip header's time stamp is set to zero and no file name is stored, so the
    same blocks always give the same file and the same checksum.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = blocks[list(EXTRACT_COLUMNS)].to_csv(
        index=False, lineterminator="\n", float_format=f"%.{COORD_DIGITS}f"
    )
    part = dest.with_name(dest.name + ".part")
    try:
        with (
            part.open("wb") as raw,
            gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz,
        ):
            gz.write(text.encode("utf-8"))
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)
    return dest


def read_extract(path: Path) -> pd.DataFrame:
    """Read a state extract written by :func:`write_extract`."""
    frame = pd.read_csv(
        path,
        dtype={"geoid": str, "tract": str, "county": str},
        compression="gzip",
    )
    missing = [c for c in EXTRACT_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"{Path(path).name}: block extract lacks {', '.join(missing)}")
    return frame[list(EXTRACT_COLUMNS)]


def extract_path(cache: Path, fips: str) -> Path:
    """Where the state's block extract lives in the cache."""
    return Path(cache) / "blocks" / f"tl_2020_{fips}_tabblock20_attributes.csv.gz"


def fetch_blocks(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> tuple[pd.DataFrame, BlockFetch]:
    """Read (or reuse) the state's block attribute table.

    The ``.dbf`` member is read from the remote zip by range requests, checked,
    and saved as the gzipped extract in ``cache/blocks/``. The ``.dbf`` itself is
    never written to disk. An existing extract is reused with no network access.

    Returns:
        The blocks table (:data:`EXTRACT_COLUMNS`) and a :class:`BlockFetch` record.
    """
    fips = to_state_fips(state)
    dest = extract_path(cache, fips)
    if dest.exists():
        frame = read_extract(dest)
        return frame, BlockFetch(dest, len(frame), 0, 0, from_cache=True)
    client = client or HttpClient()
    source = get_source(SOURCE_ID)
    url = source.url(state_fips=fips)
    member = source.member_name(state_fips=fips)
    members = client.remote_zip_members(url)
    if member not in members:
        raise ValueError(f"{url}: the zip has no member {member}")
    zip_bytes = client.remote_size(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    check_free_space(
        dest.parent, members[member].size, min_gb=client.min_free_gb, free_bytes=client.free_bytes
    )
    data = client.read_zip_member(url, member)
    frame = read_block_table(data, fips)
    del data
    write_extract(frame, dest)
    if manifest is not None:
        mf.record(mf.entry_for_file(SOURCE_ID, url, dest, VINTAGE, root=mf.ROOT), manifest)
    return frame, BlockFetch(dest, len(frame), members[member].compressed_size, zip_bytes, False)


def blocks_lite(blocks: pd.DataFrame) -> pd.DataFrame:
    """Return the pack's ``blocks`` table: the Testland columns, water-only blocks dropped.

    A block with no land and no housing units (a piece of river or lake) adds
    nothing to density or to distances from transit, so it is left out.
    """
    keep = (blocks["land_area_m2"] > 0) | (blocks["housing_units"] > 0)
    return blocks.loc[keep, list(LITE_COLUMNS)].sort_values("geoid").reset_index(drop=True)
