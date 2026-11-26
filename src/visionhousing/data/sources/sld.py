"""State legislative districts: which district every census block is in, and district outlines.

Plain-language summary
----------------------
A state legislature has two chambers. Census calls the districts of the upper
chamber (in Pennsylvania the Senate, 50 districts) **SLDU** and those of the lower
chamber (the House, 203 districts) **SLDL**. To show what a reform means in one
legislator's district, the tool needs to know which homes lie in which district.

Two Census products give that, both public and keyless:

1. **Block equivalency files** (``sldu_2022.zip`` and ``sldl_2022.zip`` from the
   Census Redistricting Data Program). One text file per state and chamber, one
   row per 2020 census block: ``GEOID`` (the 15-digit block code) and the
   district code (``SLDUST`` or ``SLDLST``). The district plans are the ones
   adopted after the 2020 Census and first used in the 2022 elections. The
   Bureau published later files (2024, 2026) only for states that redrew their
   districts again; Pennsylvania and Vermont are not among them (checked
   2026-11-24), so the 2022 files are the current plans for both. Only the
   state's member of each national zip is read, by HTTP range requests, and
   saved as a small gzipped extract in ``data/raw/districts/``.
2. **Cartographic boundary files** (``cb_2024_{state}_sldu_500k.zip`` and
   ``..._sldl_500k.zip``): district outlines generalized for maps, with each
   district's display name (``NAMELSAD``, for example "State House District
   12"). They are saved at full detail in ``data/raw/boundaries/`` and simplified
   when the pack is written (:mod:`visionhousing.districts.build`).

A code of ``ZZZ`` in a block equivalency file means "no district" (Census uses
it for blocks of open water). Such blocks are kept in the extract and dropped
when weights are made; a block with homes and ``ZZZ`` stops the build.

Checks
------
- Every block code is 15 digits and starts with the state code; none repeats.
- Every district code is 1 to 3 letters, digits or dashes.
"""

from __future__ import annotations

import gzip
import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources import STATE_FIPS
from visionhousing.data.sources import boundaries as bnd
from visionhousing.data.sources import state_fips as to_state_fips

__all__ = [
    "BEF_SOURCE",
    "CHAMBERS",
    "NO_DISTRICT",
    "SHAPE_SOURCE",
    "DistrictInputs",
    "fetch_districts",
    "read_block_equivalency",
    "read_district_shapes",
]

BEF_SOURCE = "census_sld_bef_2022"
"""Registry id of the block equivalency files."""
SHAPE_SOURCE = "cb_2024_sld"
"""Registry id of the district outlines."""
BEF_VINTAGE = "2022 state legislative district plans (2020 blocks)"
SHAPE_VINTAGE = "2024"
NO_DISTRICT = "ZZZ"
"""Census code for a block in no district (open water)."""

CHAMBERS = {"upper": "sldu", "lower": "sldl"}
"""Chamber name used here to the Census layer code."""


@dataclass(frozen=True)
class DistrictInputs:
    """Everything fetched for one state: block assignments and outlines per chamber."""

    state: str
    fips: str
    assignments: dict[str, pd.DataFrame]
    """Chamber (``upper``/``lower``) to a table ``geoid, district``."""
    shapes: dict[str, list[bnd.Shape]]
    """Chamber to its district outlines (``geoid`` = district code)."""
    files: list[tuple[str, Path]]
    """``(source id, cached file)`` pairs, for the pack manifest."""


def read_block_equivalency(
    source: bytes | str | Path, state_fips: str | None = None
) -> pd.DataFrame:
    """Read one block equivalency text file; return ``geoid, district`` sorted by block.

    Args:
        source: The file's bytes, or a path to it.
        state_fips: When given, every block must belong to this state.

    Raises:
        ValueError: A missing column, a malformed code, or a repeated block.
    """
    data = Path(source).read_bytes() if isinstance(source, str | Path) else bytes(source)
    frame = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    cols = [c.strip().upper() for c in frame.columns]
    frame.columns = cols
    code_col = next((c for c in cols if c in ("SLDUST", "SLDLST", "DISTRICT")), None)
    if "GEOID" not in cols or code_col is None:
        raise ValueError("block equivalency file needs GEOID and SLDUST or SLDLST columns")
    out = pd.DataFrame(
        {"geoid": frame["GEOID"].str.strip(), "district": frame[code_col].str.strip()}
    )
    if not out["geoid"].str.fullmatch(r"\d{15}").all():
        raise ValueError("block codes must be 15 digits")
    if state_fips is not None and not out["geoid"].str.startswith(state_fips).all():
        raise ValueError(f"block equivalency file holds blocks outside state {state_fips}")
    if out["geoid"].duplicated().any():
        raise ValueError("block equivalency file repeats a block")
    if not out["district"].str.fullmatch(r"[A-Za-z0-9-]{1,3}").all():
        raise ValueError("district codes must be 1 to 3 letters, digits or dashes")
    return out.sort_values("geoid").reset_index(drop=True)


def read_district_shapes(
    source: bytes | str | Path, state_fips: str | None = None
) -> list[bnd.Shape]:
    """Read a cartographic district file; ``geoid`` is the district code (state code removed)."""
    keep = None if state_fips is None else (lambda a: str(a.get("STATEFP")) == state_fips)
    out = []
    for attrs, geometry in bnd.read_shapes(source, keep):
        code = str(attrs.get("SLDUST") or attrs.get("SLDLST") or "").strip()
        if not code:
            raise ValueError("district file has no SLDUST or SLDLST field")
        out.append(
            bnd.Shape(
                geoid=code,
                name=str(attrs.get("NAMELSAD") or attrs.get("NAME") or code).strip(),
                land_area_m2=int(attrs.get("ALAND") or 0),
                water_area_m2=int(attrs.get("AWATER") or 0),
                geometry=geometry,
            )
        )
    return sorted(out, key=lambda s: s.geoid)


def _bef_path(cache: Path, fips: str, layer: str) -> Path:
    return Path(cache) / "districts" / f"bef_2022_{fips}_{layer}.csv.gz"


def _write_gz(frame: pd.DataFrame, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        with (
            part.open("wb") as raw,
            gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz,
        ):
            gz.write(frame.to_csv(index=False, lineterminator="\n").encode("utf-8"))
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)


def _fetch_bef(
    client: HttpClient, state: str, fips: str, layer: str, cache: Path, manifest: Path | None
) -> tuple[pd.DataFrame, Path]:
    dest = _bef_path(cache, fips, layer)
    if dest.exists():
        frame = pd.read_csv(dest, dtype=str, keep_default_na=False, compression="gzip")
        return frame, dest
    source = get_source(BEF_SOURCE)
    values = {"layer": layer, "LAYER": layer.upper(), "state_fips": fips, "state": state}
    url = source.url(**values)
    member = source.member_name(**values)
    if member is None:
        raise ValueError(f"source {BEF_SOURCE} names no zip member")
    frame = read_block_equivalency(client.read_zip_member(url, member), fips)
    _write_gz(frame, dest)
    if manifest is not None:
        mf.record(mf.entry_for_file(BEF_SOURCE, url, dest, BEF_VINTAGE, root=mf.ROOT), manifest)
    return frame, dest


def _fetch_shapes(
    client: HttpClient, fips: str, layer: str, cache: Path, manifest: Path | None
) -> tuple[list[bnd.Shape], Path]:
    dest = Path(cache) / "boundaries" / f"cb_2024_{fips}_{layer}_500k.geojson.gz"
    if dest.exists():
        return bnd._load_extract(dest), dest
    url = get_source(SHAPE_SOURCE).url(state_fips=fips, layer=layer)
    dest.parent.mkdir(parents=True, exist_ok=True)
    zip_path = dest.with_name(url.rsplit("/", 1)[-1])
    try:
        client.download(url, zip_path)
        shapes = read_district_shapes(zip_path, fips)
    finally:
        zip_path.unlink(missing_ok=True)  # raw zips are never kept
    if not shapes:
        raise ValueError(f"{url}: no district shapes for this state")
    bnd._save_extract(shapes, dest)
    if manifest is not None:
        mf.record(mf.entry_for_file(SHAPE_SOURCE, url, dest, SHAPE_VINTAGE, root=mf.ROOT), manifest)
    return shapes, dest


def fetch_districts(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> DistrictInputs:
    """Fetch (or reuse) both chambers' block assignments and outlines for ``state``.

    Cached extracts are reused with no network access.
    """
    fips = to_state_fips(state)
    code = next(k for k, v in STATE_FIPS.items() if v == fips)
    client = client or HttpClient()
    assignments, shapes, files = {}, {}, []
    for chamber, layer in CHAMBERS.items():
        frame, bef_path = _fetch_bef(client, code, fips, layer, cache, manifest)
        outlines, shape_path = _fetch_shapes(client, fips, layer, cache, manifest)
        assignments[chamber] = frame
        shapes[chamber] = outlines
        files += [(BEF_SOURCE, bef_path), (SHAPE_SOURCE, shape_path)]
    return DistrictInputs(code, fips, assignments, shapes, files)
