"""Tract-to-district and county-to-district weights from 2020 blocks.

Plain-language summary
----------------------
The engine works out added homes per census tract (ADUs, missing middle,
transit-oriented density) or per county (the fee cut, parking, the baseline).
Legislative districts do not follow tract or county lines, so results are
shared out to districts by **weights**:

    weight(tract, district) = 2020 housing units in the tract's blocks that lie in the district
                              / 2020 housing units in the tract

A census block lies wholly inside one district (districts are drawn from whole
blocks), so this count is exact for 2020 homes. The same is done for counties.
The weights of a tract add up to 1, so every added home of a tract goes to some
district and the districts add up to the state in every draw.

Steps
-----
1. Join each block to its district (block equivalency file) and drop blocks in
   no district (code ``ZZZ``, open water; a block with homes and no district
   stops the build).
2. Add up housing units by tract and district, and by tract.
3. Divide. A tract with no 2020 homes (a park, an airport) is shared by block
   land area instead, so its weights still add up to 1.
4. Round to four decimals by the largest-remainder method: every weight is cut
   to whole ten-thousandths, then the missing ten-thousandths go to the rows
   with the largest remainders, so the rounded weights of a tract add up to
   exactly 1.0000. Rows whose weight rounds to zero are not stored (a sparse
   table). The aggregation divides each tract's stored weights by their sum,
   so the state total is kept exactly.
5. **Split share** of a district: the share of its 2020 homes that lie in tracts
   split across two or more districts. In those tracts the engine's tract
   result is shared out in proportion to 2020 homes, which assumes added homes
   follow existing homes inside the tract. The higher the split share, the more
   the district's number rests on that assumption. The county split share does
   the same for county-level results (the fee cut, parking, the baseline),
   which are shared out from whole counties.

Testland, the synthetic test state, has no real districts.
:func:`synthetic_assignment` draws made-up ones from its block points
(longitude bands for an upper chamber, a grid for a lower chamber) so the
same code and tests run without a download.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "DECIMALS",
    "DISTRICT_COLUMNS",
    "PACK_TABLES",
    "WEIGHT_COLUMNS",
    "describe",
    "district_table",
    "round_shares",
    "synthetic_assignment",
    "synthetic_outlines",
    "weight_table",
    "weights_for",
]

DECIMALS = 4
"""Decimal places kept for stored weights."""
_SCALE = 10**DECIMALS

WEIGHT_COLUMNS = ("chamber", "district", "{key}", "units", "weight")
"""Columns of a weight table; ``{key}`` is ``tract`` or ``county``."""

DISTRICT_COLUMNS = (
    "chamber",
    "district",
    "name",
    "units",
    "tracts",
    "split_share",
    "counties",
    "county_split_share",
)
"""Columns of the district list."""

NO_DISTRICT = "ZZZ"


def round_shares(shares: np.ndarray) -> np.ndarray:
    """Round one geography's shares to :data:`DECIMALS` places so they add up to exactly 1.

    Largest-remainder rounding: floor every share to whole ten-thousandths, then
    give the ten-thousandths still missing to the largest remainders (ties go to
    the earlier row).

    Args:
        shares: Non-negative shares that add up to 1 (up to float error).

    Returns:
        Whole ten-thousandths as integers; they add up to ``10**DECIMALS``.
    """
    scaled = np.asarray(shares, dtype=float) * _SCALE
    base = np.floor(scaled + 1e-9).astype(np.int64)
    short = _SCALE - int(base.sum())
    if short > 0:
        order = np.argsort(-(scaled - base), kind="stable")
        base[order[:short]] += 1
    elif short < 0:
        order = np.argsort(scaled - base, kind="stable")
        for i in order:
            if short == 0:
                break
            if base[i] > 0:
                base[i] -= 1
                short += 1
    return base


def _assigned_blocks(blocks: pd.DataFrame, assignment: pd.DataFrame) -> pd.DataFrame:
    """Blocks with their district; water-only blocks in no district are dropped."""
    merged = blocks.merge(assignment[["geoid", "district"]], on="geoid", how="left")
    missing = merged["district"].isna()
    if missing.any():
        raise ValueError(
            f"{int(missing.sum())} blocks have no row in the district file "
            f"(first {merged.loc[missing, 'geoid'].iloc[0]})"
        )
    none = merged["district"] == NO_DISTRICT
    homes_lost = int(merged.loc[none, "housing_units"].sum())
    if homes_lost:
        raise ValueError(f"{homes_lost} housing units lie in blocks with no district (ZZZ)")
    return merged.loc[~none]


def weight_table(
    blocks: pd.DataFrame,
    assignment: pd.DataFrame,
    chamber: str,
    key: str = "tract",
    keep: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Return the sparse weight table of one chamber, by tract or by county.

    Args:
        blocks: One row per block with ``geoid``, ``tract``, ``county``
            (or a ``geoid`` whose first five digits are the county),
            ``housing_units`` and ``land_area_m2``.
        assignment: ``geoid, district`` for every block.
        chamber: ``"upper"`` or ``"lower"`` (written in the table).
        key: ``"tract"`` or ``"county"``.
        keep: Only these tracts or counties (for example the pack's tracts);
            every one must have at least one block.

    Returns:
        Columns ``chamber, district, {key}, units, weight``; ``units`` are 2020
        housing units of the {key} in the district (whole numbers) and
        ``weight`` their share of the {key}'s units, rounded to four decimals.
        Rows whose weight rounds to zero are left out.
    """
    frame = blocks.copy()
    if "county" not in frame.columns:
        frame["county"] = frame["geoid"].str[:5]
    if "tract" not in frame.columns:
        frame["tract"] = frame["geoid"].str[:11]
    frame = _assigned_blocks(frame, assignment)
    if keep is not None:
        wanted = sorted(set(map(str, keep)))
        frame = frame[frame[key].isin(wanted)]
        absent = sorted(set(wanted) - set(frame[key]))
        if absent:
            raise ValueError(
                f"{len(absent)} {key}s have no block in a district (first {absent[0]})"
            )
    parts = (
        frame.groupby([key, "district"], sort=True)[["housing_units", "land_area_m2"]]
        .sum()
        .reset_index()
    )
    rows = []
    for geo, grp in parts.groupby(key, sort=True):
        units = grp["housing_units"].to_numpy(dtype=float)
        land = grp["land_area_m2"].to_numpy(dtype=float)
        if units.sum() > 0:
            share = units / units.sum()
        elif land.sum() > 0:
            share = land / land.sum()
        else:
            share = np.full(len(grp), 1.0 / len(grp))
        tenk = round_shares(share)
        for district, u, t in zip(grp["district"], units, tenk, strict=True):
            if t > 0:
                rows.append((chamber, str(district), str(geo), int(u), t / _SCALE))
    cols = [c.format(key=key) for c in WEIGHT_COLUMNS]
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values(["chamber", "district", key]).reset_index(drop=True)


def district_table(
    tract_w: pd.DataFrame,
    county_w: pd.DataFrame,
    names: Mapping[str, str],
    chamber: str,
) -> pd.DataFrame:
    """Return one chamber's district list with its split shares.

    Args:
        tract_w: The chamber's tract weight table (:func:`weight_table`).
        county_w: The chamber's county weight table.
        names: District code to display name (codes missing here get their code).
        chamber: ``"upper"`` or ``"lower"``.

    Returns:
        Columns :data:`DISTRICT_COLUMNS`: 2020 units in the district, number of
        tracts and counties it touches, and the two split shares (four decimals).
    """
    t = tract_w[tract_w["chamber"] == chamber]
    c = county_w[county_w["chamber"] == chamber]
    split_tracts = set(t.loc[t["tract"].duplicated(keep=False), "tract"])
    split_counties = set(c.loc[c["county"].duplicated(keep=False), "county"])
    rows = []
    for district in sorted(set(t["district"]) | set(names)):
        td = t[t["district"] == district]
        cd = c[c["district"] == district]
        units = int(td["units"].sum())
        split = int(td.loc[td["tract"].isin(split_tracts), "units"].sum())
        csplit = int(cd.loc[cd["county"].isin(split_counties), "units"].sum())
        rows.append(
            {
                "chamber": chamber,
                "district": district,
                "name": names.get(district, district),
                "units": units,
                "tracts": int(td["tract"].nunique()),
                "split_share": round(split / units, DECIMALS) if units else 0.0,
                "counties": int(cd["county"].nunique()),
                "county_split_share": round(csplit / units, DECIMALS) if units else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=list(DISTRICT_COLUMNS))


def weights_for(
    blocks: pd.DataFrame,
    assignments: Mapping[str, pd.DataFrame],
    names: Mapping[str, Mapping[str, str]],
    tract_ids: Iterable[str],
) -> dict[str, pd.DataFrame]:
    """Build the three district tables for every chamber given.

    Args:
        blocks: The state's blocks (see :func:`weight_table`).
        assignments: Chamber to its ``geoid, district`` table.
        names: Chamber to district code to display name.
        tract_ids: The tracts the engine works with (the pack's ``tracts``).

    Returns:
        ``{"districts", "district_tract_weights", "district_county_weights"}``.
    """
    tract_ids = sorted(set(map(str, tract_ids)))
    frame = blocks.copy()
    frame["tract"] = frame["geoid"].str[:11]
    frame["county"] = frame["geoid"].str[:5]
    frame = frame[frame["tract"].isin(tract_ids)]
    counties = sorted(set(frame["county"]))
    tw, cw, dl = [], [], []
    for chamber, assignment in assignments.items():
        t = weight_table(frame, assignment, chamber, "tract", tract_ids)
        c = weight_table(frame, assignment, chamber, "county", counties)
        tw.append(t)
        cw.append(c)
        dl.append(district_table(t, c, names.get(chamber, {}), chamber))
    return {
        "districts": pd.concat(dl, ignore_index=True),
        "district_tract_weights": pd.concat(tw, ignore_index=True),
        "district_county_weights": pd.concat(cw, ignore_index=True),
    }


def synthetic_assignment(
    blocks: pd.DataFrame,
) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, str]]]:
    """Made-up districts for Testland, drawn from block points.

    Upper chamber: three bands of longitude, cut where the cumulative housing
    units pass one third and two thirds. Lower chamber: each band cut in three
    by latitude the same way (nine districts). The cuts run through tracts, so
    some tracts are split, as in a real state.

    Returns:
        ``(assignments, names)`` in the shape :func:`weights_for` takes.
    """
    pts = blocks[["geoid", "housing_units", "lat", "lon"]].copy()

    def bands(frame: pd.DataFrame, col: str, n: int) -> pd.Series:
        order = frame.sort_values([col, "geoid"])
        cum = order["housing_units"].cumsum() / max(order["housing_units"].sum(), 1)
        band = np.minimum((cum.to_numpy() * n - 1e-9).astype(int), n - 1)
        return pd.Series(np.maximum(band, 0), index=order.index)

    pts["band"] = bands(pts, "lon", 3)
    pts["sub"] = 0
    for _band, grp in pts.groupby("band"):
        pts.loc[grp.index, "sub"] = bands(grp, "lat", 3)
    upper = pd.DataFrame({"geoid": pts["geoid"], "district": (pts["band"] + 1).astype(str)})
    lower = pd.DataFrame(
        {"geoid": pts["geoid"], "district": (pts["band"] * 3 + pts["sub"] + 1).astype(str)}
    )
    names = {
        "upper": {str(i): f"Testland Senate District {i}" for i in range(1, 4)},
        "lower": {str(i): f"Testland House District {i}" for i in range(1, 10)},
    }
    return {"upper": upper, "lower": lower}, names


def synthetic_outlines(
    blocks: pd.DataFrame, bbox: tuple[float, float, float, float]
) -> dict[str, dict[str, Any]]:
    """Outlines of Testland's made-up districts, as GeoJSON, one collection per chamber.

    Each made-up district is a band (upper) or a part of a band (lower) of block
    points, so its outline is a rectangle: the edge between two neighbouring bands
    sits half way between the last block of one and the first block of the next,
    and the outer edges are the state's own box (``bbox``: west, south, east, north).
    It draws the map layer only; no number comes from it.
    """
    assignments, names = synthetic_assignment(blocks)
    west, south, east, north = bbox
    pts = blocks[["geoid", "lat", "lon"]].copy()
    pts["geoid"] = pts["geoid"].astype(str)

    def cuts(lo: list[float], hi: list[float], first: float, last: float) -> list[float]:
        inner = [(hi[i] + lo[i + 1]) / 2 for i in range(len(lo) - 1)]
        return [first, *inner, last]

    lower = pts.merge(assignments["lower"], on="geoid")
    lower["n"] = lower["district"].astype(int)
    lower["band"] = (lower["n"] - 1) // 3
    bands = lower.groupby("band")["lon"].agg(["min", "max"]).sort_index()
    xs = cuts(list(bands["min"]), list(bands["max"]), west, east)

    def ring(x0: float, y0: float, x1: float, y1: float) -> list[list[list[float]]]:
        r = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
        return [[[round(x, 4), round(y, 4)] for x, y in r]]

    out: dict[str, dict[str, Any]] = {}
    upper = []
    for b in range(len(xs) - 1):
        d = str(b + 1)
        upper.append((d, ring(xs[b], south, xs[b + 1], north)))
    parts = []
    for b, grp in lower.groupby("band"):
        subs = grp.groupby("n")["lat"].agg(["min", "max"]).sort_index()
        ys = cuts(list(subs["min"]), list(subs["max"]), south, north)
        for j, n in enumerate(subs.index):
            parts.append((str(n), ring(xs[int(b)], ys[j], xs[int(b) + 1], ys[j + 1])))
    for chamber, level, shapes in (("upper", "sldu", upper), ("lower", "sldl", parts)):
        out[chamber] = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"level": level, "geoid": d, "name": names[chamber][d]},
                    "geometry": {"type": "Polygon", "coordinates": coords},
                }
                for d, coords in sorted(shapes, key=lambda s: int(s[0]))
            ],
        }
    return out


PACK_TABLES: dict[str, dict[str, object]] = {
    "districts": {
        "key": ["chamber", "district"],
        "sources": ["census_sld_bef_2022", "cb_2024_sld", "tiger_2020_tabblock"],
        "description": (
            "One row per state legislative district (upper and lower chamber, 2022 plans): "
            "name, 2020 housing units, tracts and counties touched, and split shares."
        ),
    },
    "district_tract_weights": {
        "key": ["chamber", "district", "tract"],
        "sources": ["census_sld_bef_2022", "tiger_2020_tabblock"],
        "description": (
            "Tract-to-district weights: 2020 housing units of the tract in the district "
            "over the tract's units; only weights above zero; a tract's weights add up to 1."
        ),
    },
    "district_county_weights": {
        "key": ["chamber", "district", "county"],
        "sources": ["census_sld_bef_2022", "tiger_2020_tabblock"],
        "description": (
            "County-to-district weights, made the same way, for county-level results "
            "(the baseline, the fee cut, parking)."
        ),
    },
}
"""The pack tables this module makes, in the layout of ``visionhousing.data.pack.TABLES``."""

_COLUMNS: dict[str, tuple[str, str]] = {
    "chamber": ("category", "upper (state senate) or lower (state house) chamber"),
    "district": ("code", "District code in the Census block equivalency file"),
    "name": ("text", "District display name (Census cartographic boundary file)"),
    "units": ("units", "2020 Census housing units (in the district, or in the row's piece)"),
    "tracts": ("count", "Tracts with part of their 2020 homes in the district"),
    "counties": ("count", "Counties with part of their 2020 homes in the district"),
    "split_share": (
        "share",
        "Share of the district's 2020 homes in tracts split across two or more districts",
    ),
    "county_split_share": (
        "share",
        "Share of the district's 2020 homes in counties split across two or more districts",
    ),
    "tract": ("code", "Tract code: 5-digit county code + 6-digit tract code"),
    "county": ("code", "County code (2-digit state + 3-digit county)"),
    "weight": (
        "share",
        "Share of the tract's (or county's) 2020 housing units in the district, 4 decimals",
    ),
}


def describe(table: str, name: str) -> tuple[str, str]:
    """``(unit, description)`` of one column of a district table, for the data dictionary."""
    if table not in PACK_TABLES or name not in _COLUMNS:
        raise ValueError(f"no data-dictionary entry for {table}.{name}")
    return _COLUMNS[name]
