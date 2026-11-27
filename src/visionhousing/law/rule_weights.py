"""Which homes lie under which zoning rule: the ``rule_area_weights`` table.

Plain-language summary
----------------------
A reviewed zoning rule (:mod:`visionhousing.law.zone_rules`) says what one
district allows. To know how many lots it reaches, the district map has to be
laid over where homes are. The finest public count of homes is the 2020
census **block**. This module:

1. **Gives each block a housing weight.** Its 2020 housing units, its land
   (Census land area) and its share of the tract's single-family lots: the
   ACS count of one-unit detached homes in the tract (the lots the benchmarks
   act on, as in :mod:`visionhousing.law.coverage`) split among the tract's
   blocks by their 2020 housing units.
2. **Splits each block by area among districts and overlays** (never by its
   centre point). The block, the district outlines and the overlay outlines
   are drawn on a small flat grid in metres over the block (one projection,
   centred on the jurisdiction, for all its shapes; :mod:`visionhousing.features.planar`
   does the drawing). A cell belongs to the district whose outline holds its
   centre. A block's share in each (district, overlay set) is its cells there
   over its cells inside any district. Cells inside no district (streets,
   water, land the map gives another municipality) are **unmatched**: the
   block's homes are spread over its zoned part, and the unmatched area is
   reported. A block with no zoned cell at all goes whole to an
   **unresolved** bucket. Pieces under ``min_piece_share`` of a block are
   **slivers**, folded into the block's largest piece and reported. A
   jurisdiction with no district map gets one whole-jurisdiction zone.
3. **Weighs each zone version.** A zone (district + the overlays that some
   rule names) is resolved to its rule on each interval of the window between
   rule changes (:func:`visionhousing.law.zone_rules.window_intervals`); a rule
   that starts mid-window gives each version its share of the window's days.
   Each version gets an opportunity class per benchmark.

Checks (:func:`check_weights`): for each jurisdiction, benchmark and map, the
weights over every zone version and class sum to 1, and so does each block's
split (pieces plus slivers). Results are counts of lots, homes and land, never
outlines, so the pack holds derived numbers only.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.features import planar
from visionhousing.law import zone_rules as zr

__all__ = [
    "WEIGHT_COLUMNS",
    "BlockSplit",
    "Projection",
    "ShapeIndex",
    "SplitRule",
    "block_weights",
    "check_weights",
    "district_change",
    "exposure_shares",
    "move_u13",
    "split_block",
    "split_blocks",
    "state_exposure",
    "u13_answers",
    "zone_weights",
]

_M_PER_DEG_LAT = 110_574.0
_M_PER_DEG_LON = 111_320.0
NO_MAP = "(no district map)"
UNMATCHED = "(outside every district)"
U13_ANSWER = {
    "known_by_right": "already_allowed",
    "conditions_relaxed": "changed_condition",
    "discretionary": "changed_condition",
    "newly_eligible": "newly_allowed",
    "unresolved": "unresolved",
}
"""How the opportunity classes map to the four answers of the law-changes view (U13)."""


@dataclass(frozen=True)
class Projection:
    """A flat map in metres centred on one jurisdiction (equirectangular)."""

    lon0: float
    lat0: float

    @classmethod
    def around(cls, rings: Iterable[np.ndarray]) -> Projection:
        """Centred on the bounding box of some lon/lat rings."""
        xmin, ymin, xmax, ymax = planar.rings_bounds(list(rings))
        return cls((xmin + xmax) / 2, (ymin + ymax) / 2)

    def __call__(self, ring: Any) -> np.ndarray:
        """A lon/lat ring as metres east and north of the centre."""
        r = np.asarray(ring, dtype=float)[:, :2]
        kx = _M_PER_DEG_LON * math.cos(math.radians(self.lat0))
        return np.column_stack(((r[:, 0] - self.lon0) * kx, (r[:, 1] - self.lat0) * _M_PER_DEG_LAT))


@dataclass
class ShapeIndex:
    """Coded shapes (districts or overlays) in metres, with bounding boxes for candidate search."""

    codes: list[str]
    rings: list[list[np.ndarray]]
    boxes: np.ndarray = field(repr=False)

    @classmethod
    def build(cls, shapes: Iterable[tuple[str, Sequence[Any]]], proj: Projection) -> ShapeIndex:
        """From ``(code, rings in lon/lat)`` pairs; shapes with no ring are skipped."""
        codes, rings, boxes = [], [], []
        for code, rr in shapes:
            r = [proj(x) for x in rr if len(x) >= 3]
            if not r:
                continue
            codes.append(str(code))
            rings.append(r)
            boxes.append(planar.rings_bounds(r))
        return cls(codes, rings, np.asarray(boxes, dtype=float).reshape(-1, 4))

    def candidates(self, box: tuple[float, float, float, float]) -> np.ndarray:
        """Indexes of shapes whose bounding box meets ``box``."""
        if not len(self.codes):
            return np.zeros(0, dtype=np.int64)
        b = self.boxes
        hit = (b[:, 0] <= box[2]) & (b[:, 2] >= box[0]) & (b[:, 1] <= box[3]) & (b[:, 3] >= box[1])
        return np.flatnonzero(hit)


@dataclass(frozen=True)
class SplitRule:
    """Grid and sliver settings (``split`` in ``assumptions/law/zone_weights.yaml``)."""

    cells_per_side: int = 40
    min_cell_m: float = 0.5
    min_piece_share: float = 0.02

    @classmethod
    def from_settings(cls, spec: Mapping[str, Any]) -> SplitRule:
        """From the ``split`` block."""
        return cls(
            int(spec["cells_per_side"]), float(spec["min_cell_m"]), float(spec["min_piece_share"])
        )


@dataclass(frozen=True)
class BlockSplit:
    """One block's pieces: ``{(district, overlays): share}``; shares sum to 1 when any is zoned.

    ``unmatched`` is the share of the block's area inside no district (before the
    homes are spread over the zoned part); ``sliver`` the share folded into the
    largest piece; ``overlap`` the share where two different district codes
    overlap (the later one in the layer is kept).
    """

    pieces: dict[tuple[str, tuple[str, ...]], float]
    unmatched: float
    sliver: float
    overlap: float
    method: str


def _grid(rings_m: Sequence[np.ndarray], rule: SplitRule, cell: float | None = None):
    xmin, ymin, xmax, ymax = planar.rings_bounds(rings_m)
    size = cell or max(max(xmax - xmin, ymax - ymin) / rule.cells_per_side, rule.min_cell_m)
    grid = planar.grid_for_bounds(xmin, ymin, xmax, ymax, size)
    return grid, (xmin, ymin, xmax, ymax)


def _fold(raw: dict[tuple[str, tuple[str, ...]], float], min_share: float):
    big = max(raw, key=lambda k: (raw[k], k))
    keep = {big: raw[big]}
    sliver = 0.0
    for k, s in raw.items():
        if k == big:
            continue
        if s < min_share:
            sliver += s
        else:
            keep[k] = s
    keep[big] += sliver
    total = sum(keep.values())
    return {k: v / total for k, v in keep.items()}, sliver / total if total else 0.0


def split_block(
    rings_m: Sequence[np.ndarray],
    districts: ShapeIndex | None,
    overlays: ShapeIndex | None,
    rule: SplitRule,
    *,
    not_a_district: Iterable[str] = (),
) -> BlockSplit:
    """Split one block (rings in metres, even-odd rule) among districts and overlays by area.

    With no district map (``districts is None``) the block is one piece of the
    whole-jurisdiction zone. A block too small to hold a cell centre at the
    finest cell (``min_cell_m``) goes to the district holding most of its
    corners (``method = corners``), never to a centre point.
    """
    rings_m = [np.asarray(r, dtype=float) for r in rings_m if len(r) >= 3]
    if not rings_m:
        return BlockSplit({}, 1.0, 0.0, 0.0, "empty")
    if districts is None:
        return BlockSplit({(NO_MAP, ()): 1.0}, 0.0, 0.0, 0.0, "whole_jurisdiction")
    skip = set(not_a_district)
    grid, box = _grid(rings_m, rule)
    inside = planar.fill_rings(grid, rings_m)
    if not inside.any():
        grid, box = _grid(rings_m, rule, cell=rule.min_cell_m)
        inside = planar.fill_rings(grid, rings_m)
    cand = districts.candidates(box)
    if not inside.any():
        pts = np.concatenate(rings_m)
        votes: dict[str, int] = defaultdict(int)
        for k in cand:
            if districts.codes[k] in skip:
                continue
            votes[districts.codes[k]] += int(
                planar.contains(pts[:, 0], pts[:, 1], districts.rings[k]).sum()
            )
        votes = {c: n for c, n in votes.items() if n}
        if not votes:
            return BlockSplit({}, 1.0, 0.0, 0.0, "corners")
        best = max(votes, key=lambda c: (votes[c], c))
        return BlockSplit({(best, ()): 1.0}, 0.0, 0.0, 0.0, "corners")
    total = int(inside.sum())
    label = np.full(inside.shape, -1, dtype=np.int32)
    names: list[str] = []
    overlap = np.zeros(inside.shape, dtype=bool)
    for k in cand:
        code = districts.codes[k]
        m = planar.fill_rings(grid, districts.rings[k]) & inside
        if not m.any():
            continue
        if code in skip:
            label[m] = -1
            continue
        if code not in names:
            names.append(code)
        idx = names.index(code)
        overlap |= m & (label >= 0) & (label != idx)
        label[m] = idx
    ov_codes: list[str] = []
    ov_masks: list[np.ndarray] = []
    if overlays is not None:
        for k in overlays.candidates(box):
            m = planar.fill_rings(grid, overlays.rings[k]) & inside
            if not m.any():
                continue
            code = overlays.codes[k]
            if code in ov_codes:
                ov_masks[ov_codes.index(code)] |= m
            else:
                ov_codes.append(code)
                ov_masks.append(m)
    zoned = inside & (label >= 0)
    n_zoned = int(zoned.sum())
    unmatched = 1.0 - n_zoned / total
    if not n_zoned:
        return BlockSplit({}, 1.0, 0.0, 0.0, "grid")
    order = sorted(range(len(ov_codes)), key=lambda i: ov_codes[i])
    lab = label[zoned]
    bits = np.zeros(lab.shape, dtype=np.int64)
    for j, i in enumerate(order):
        bits |= ov_masks[i][zoned].astype(np.int64) << j
    keys, counts = np.unique(np.column_stack([lab, bits]), axis=0, return_counts=True)
    raw: dict[tuple[str, tuple[str, ...]], float] = {}
    for (li, b), c in zip(keys, counts, strict=False):
        ovs = tuple(ov_codes[order[j]] for j in range(len(order)) if (int(b) >> j) & 1)
        raw[(names[int(li)], ovs)] = raw.get((names[int(li)], ovs), 0.0) + c / n_zoned
    pieces, sliver = _fold(raw, rule.min_piece_share)
    return BlockSplit(pieces, unmatched, sliver, float(overlap.sum()) / total, "grid")


def split_blocks(
    blocks: Mapping[str, Sequence[Any]],
    districts: Sequence[tuple[str, Sequence[Any]]] | None,
    overlays: Sequence[tuple[str, Sequence[Any]]] | None,
    rule: SplitRule,
    *,
    not_a_district: Iterable[str] = (),
    proj: Projection | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split many blocks (``{geoid: lon/lat rings}``) among districts and overlays.

    Returns ``(pieces, blocks)``: one row per block piece (``block``,
    ``district``, ``overlays`` joined by ``+``, ``share``) and one row per
    block (``block``, ``unmatched``, ``sliver``, ``overlap``, ``method``).
    ``districts`` and ``overlays`` are ``(code, lon/lat rings)`` pairs; with
    ``districts=None`` every block is one whole-jurisdiction piece.
    """
    if proj is None:
        allr = [np.asarray(r, dtype=float) for rr in blocks.values() for r in rr if len(r) >= 3]
        proj = Projection.around(allr) if allr else Projection(0.0, 0.0)
    d_idx = ShapeIndex.build(districts, proj) if districts is not None else None
    o_idx = ShapeIndex.build(overlays, proj) if overlays else None
    prow, brow = [], []
    for geoid in sorted(blocks):
        rings_m = [proj(r) for r in blocks[geoid] if len(r) >= 3]
        s = split_block(rings_m, d_idx, o_idx, rule, not_a_district=not_a_district)
        for (dist, ovs), share in sorted(s.pieces.items()):
            prow.append(
                {"block": geoid, "district": dist, "overlays": "+".join(ovs), "share": share}
            )
        if not s.pieces:
            prow.append({"block": geoid, "district": UNMATCHED, "overlays": "", "share": 1.0})
        brow.append(
            {
                "block": geoid,
                "unmatched": s.unmatched,
                "sliver": s.sliver,
                "overlap": s.overlap,
                "method": s.method,
            }
        )
    return (
        pd.DataFrame(prow, columns=["block", "district", "overlays", "share"]),
        pd.DataFrame(brow, columns=["block", "unmatched", "sliver", "overlap", "method"]),
    )


# --------------------------------------------------------------------------- housing weights


def block_weights(
    blocks: pd.DataFrame,
    tracts: pd.DataFrame,
    block_cousub: pd.DataFrame,
    jurisdictions: Iterable[str],
) -> pd.DataFrame:
    """Housing weights of the blocks of some jurisdictions.

    ``blocks``: the pack's ``blocks`` table (``geoid``, ``tract``,
    ``housing_units``, ``land_area_m2``). ``tracts``: ``geoid`` and
    ``units_1_detached``. ``block_cousub``: the 2020 block assignment
    (``block``, ``cousub``). A block's single-family lots are its tract's
    one-unit detached homes times its share of the tract's 2020 housing units.
    Returns ``block``, ``tract``, ``county``, ``jurisdiction``, ``sf_lots``,
    ``housing_units``, ``land_m2``.
    """
    want = {str(j) for j in jurisdictions}
    b = blocks[["geoid", "tract", "housing_units", "land_area_m2"]].copy()
    b["geoid"] = b["geoid"].astype(str).str.zfill(15)
    b["tract"] = b["tract"].astype(str).str.zfill(11)
    bc = block_cousub.rename(columns={"block": "geoid"}).copy()
    bc["geoid"] = bc["geoid"].astype(str).str.zfill(15)
    bc["cousub"] = bc["cousub"].astype(str).str.zfill(5)
    b = b.merge(bc[["geoid", "cousub"]], on="geoid", how="left")
    b["county"] = b["geoid"].str[:5]
    b["jurisdiction"] = b["county"] + b["cousub"].fillna("")
    hu_tract = b.groupby("tract")["housing_units"].sum()
    t = tracts[["geoid", "units_1_detached"]].copy()
    t["geoid"] = t["geoid"].astype(str).str.zfill(11)
    sf = t.set_index("geoid")["units_1_detached"].astype(float)
    b = b[b["jurisdiction"].isin(want)].copy()
    tract_hu = b["tract"].map(hu_tract).astype(float)
    share = np.where(tract_hu > 0, b["housing_units"].astype(float) / tract_hu.replace(0, 1), 0.0)
    b["sf_lots"] = b["tract"].map(sf).fillna(0.0).to_numpy() * share
    out = b.rename(columns={"geoid": "block", "land_area_m2": "land_m2"})
    return out[["block", "tract", "county", "jurisdiction", "sf_lots", "housing_units", "land_m2"]]


# --------------------------------------------------------------------------- zone versions

WEIGHT_COLUMNS: dict[str, tuple[str, str]] = {
    "level": ("category", "zone (one zone version), tract, or jurisdiction (totals and checks)"),
    "jurisdiction": ("code", "Municipality: 10-digit state + county + county subdivision code"),
    "county": ("code", "County code"),
    "tract": ("code", "2020 tract (tract rows only)"),
    "map_version": ("text", "District map used: its read date, or the date of a dated snapshot"),
    "period": ("category", "window (the 2027-2031 run) or map_history (an older map, same rules)"),
    "district": (
        "code",
        "District code on the map, or a bucket: (no district map), (outside every district)",
    ),
    "overlays": ("text", "Overlays that some rule names, joined by +"),
    "rule_ids": ("text", "Rule rows resolved for this zone version, joined by +"),
    "review_state": ("category", "Least certain review state among those rows"),
    "valid_from": ("date", "First day of this version inside the window"),
    "valid_to": ("date", "First day after it"),
    "window_share": ("share", "Share of the window's days this version is in force"),
    "benchmark": ("category", "adu, missing_middle, tod or parking"),
    "opportunity_class": (
        "category",
        "known_by_right, conditions_relaxed, newly_eligible, discretionary or unresolved",
    ),
    "why": ("text", "Why this class, in plain words"),
    "sf_lots": (
        "lots",
        "Single-family lots (ACS one-unit detached split by block housing units) x window share",
    ),
    "housing_units": ("units", "2020 housing units x window share"),
    "land_m2": ("m2", "Census land area of the block pieces x window share"),
    "weight": (
        "share",
        "Share of the jurisdiction's single-family lots (sums to 1 per jurisdiction, "
        "benchmark, period)",
    ),
    "method": (
        "category",
        "area_grid (blocks split by area), whole_jurisdiction (no map) or unresolved",
    ),
    "blocks": ("count", "Blocks with homes in the jurisdiction (jurisdiction rows)"),
    "unmatched_area_share": (
        "share",
        "Share of block land area inside no district, weighted by lots (jurisdiction rows)",
    ),
    "sliver_share": ("share", "Share of lots in pieces folded as slivers (jurisdiction rows)"),
    "overlap_area_share": (
        "share",
        "Share of block area under two different district codes (jurisdiction rows)",
    ),
    "unresolved_geometry_lots": (
        "lots",
        "Lots of blocks with no zoned cell at all (jurisdiction rows)",
    ),
    "district_changed_lots": (
        "lots",
        "Lots whose district differs between this map and the current one "
        "(map_history jurisdiction rows)",
    ),
    "denominator_vintage": ("text", "Vintage of the lot and home counts"),
}
"""Column units and descriptions of the ``rule_area_weights`` pack table."""

DENOMINATOR = "ACS 2020-2024 one-unit detached homes, split by 2020 Census block housing units"


def zone_weights(
    pieces: pd.DataFrame,
    weights: pd.DataFrame,
    rules: Sequence[zr.ZoneRule],
    *,
    jurisdiction: str,
    map_name: str | None,
    map_version: str,
    period: str,
    window: tuple[date, date],
    benches: Mapping[str, Any],
    benchmarks: Sequence[str] = zr.BENCHMARKS,
    on: date | None = None,
) -> pd.DataFrame:
    """Zone-version rows of one jurisdiction and one map, every benchmark.

    ``pieces``: from :func:`split_blocks`; ``weights``: from
    :func:`block_weights`. In the ``window`` period each zone is resolved on
    every interval between rule changes inside the window; with ``on`` given
    (a map-history comparison) once, on that date, with window share 1.
    """
    w = weights[weights["jurisdiction"] == jurisdiction]
    p = pieces.merge(w, on="block", how="inner")
    p = p[(p["sf_lots"] > 0) | (p["housing_units"] > 0)]
    keep = zr.relevant_overlays(rules, jurisdiction)
    p = p.assign(
        overlays=p["overlays"]
        .fillna("")
        .map(lambda s: "+".join(sorted(o for o in str(s).split("+") if o and o in keep)))
    )
    for col in ("sf_lots", "housing_units", "land_m2"):
        p[col] = p[col] * p["share"]
    zones = p.groupby(["district", "overlays"], as_index=False)[
        ["sf_lots", "housing_units", "land_m2"]
    ].sum()
    total = float(zones["sf_lots"].sum())
    intervals = zr.window_intervals(rules, jurisdiction, *window) if on is None else [(on, on, 1.0)]
    rows = []
    for z in zones.itertuples(index=False):
        ovs = [o for o in str(z.overlays).split("+") if o]
        for a, b, share in intervals:
            if z.district == UNMATCHED:
                res = zr.Resolved({k: zr.UNKNOWN for k in zr.RULE_FIELDS}, (), "unread", True)
                method = "unresolved"
            else:
                res = zr.resolve(rules, jurisdiction, z.district, ovs, a, map_name=map_name)
                method = "whole_jurisdiction" if z.district == NO_MAP else "area_grid"
            for bench in benchmarks:
                if z.district == UNMATCHED:
                    cls, why = "unresolved", "Block lies outside every mapped district"
                elif res.missing:
                    cls, why = "unresolved", "No reviewed rule for this district on this date"
                else:
                    cls, why = zr.classify(res.fields, bench, benches)
                rows.append(
                    {
                        "level": "zone",
                        "jurisdiction": jurisdiction,
                        "county": jurisdiction[:5],
                        "tract": "",
                        "map_version": map_version,
                        "period": period,
                        "district": z.district,
                        "overlays": z.overlays,
                        "rule_ids": res.key,
                        "review_state": res.review_state,
                        "valid_from": a.isoformat(),
                        "valid_to": b.isoformat() if on is None else "",
                        "window_share": share,
                        "benchmark": bench,
                        "opportunity_class": cls,
                        "why": why,
                        "sf_lots": z.sf_lots * share,
                        "housing_units": z.housing_units * share,
                        "land_m2": z.land_m2 * share,
                        "weight": (z.sf_lots * share / total) if total else 0.0,
                        "method": method,
                        "denominator_vintage": DENOMINATOR,
                    }
                )
    return pd.DataFrame(rows, columns=list(WEIGHT_COLUMNS))


def tract_rows(
    pieces: pd.DataFrame, weights: pd.DataFrame, zone_rows: pd.DataFrame
) -> pd.DataFrame:
    """Tract x benchmark x class rows of one jurisdiction's window period (lots only).

    Each block piece takes the classes of its zone versions (window shares
    applied), so a tract split between jurisdictions or districts is split by
    area, not assigned whole.
    """
    if zone_rows.empty:
        return zone_rows.iloc[0:0]
    j = str(zone_rows["jurisdiction"].iloc[0])
    w = weights[weights["jurisdiction"] == j]
    keep = {o for s in zone_rows["overlays"] for o in str(s).split("+") if o}
    p = pieces.merge(w, on="block", how="inner")
    p = p.assign(
        overlays=p["overlays"]
        .fillna("")
        .map(lambda s: "+".join(sorted(o for o in str(s).split("+") if o and o in keep))),
        lots=lambda d: d["sf_lots"] * d["share"],
    )
    zl = zone_rows.groupby(
        ["district", "overlays", "benchmark", "opportunity_class"], as_index=False
    )["window_share"].sum()
    m = p.merge(zl, on=["district", "overlays"], how="inner")
    m["sf_lots"] = m["lots"] * m["window_share"]
    t = m.groupby(["tract", "benchmark", "opportunity_class"], as_index=False)["sf_lots"].sum()
    tot = t.groupby(["tract", "benchmark"])["sf_lots"].transform("sum")
    t["weight"] = np.where(tot > 0, t["sf_lots"] / tot.replace(0, 1), 0.0)
    t["level"] = "tract"
    t["jurisdiction"] = j
    t["county"] = j[:5]
    t["map_version"] = zone_rows["map_version"].iloc[0]
    t["period"] = "window"
    t["method"] = "area_grid"
    t["denominator_vintage"] = DENOMINATOR
    return t.reindex(columns=list(WEIGHT_COLUMNS))


def jurisdiction_row(
    jurisdiction: str,
    map_version: str,
    period: str,
    weights: pd.DataFrame,
    block_info: pd.DataFrame,
    pieces: pd.DataFrame,
    *,
    district_changed_lots: float | None = None,
) -> dict[str, Any]:
    """Totals and checks of one jurisdiction and map: blocks, unmatched, slivers, overlaps."""
    w = weights[weights["jurisdiction"] == jurisdiction]
    w = w[(w["sf_lots"] > 0) | (w["housing_units"] > 0)]
    bi = block_info.merge(w[["block", "sf_lots"]], on="block", how="inner")
    lots = float(bi["sf_lots"].sum())

    def wavg(c: str) -> float:
        return float((bi[c] * bi["sf_lots"]).sum() / lots) if lots else 0.0

    unres = pieces[pieces["district"] == UNMATCHED].merge(w[["block", "sf_lots"]], on="block")
    return {
        "level": "jurisdiction",
        "jurisdiction": jurisdiction,
        "county": jurisdiction[:5],
        "map_version": map_version,
        "period": period,
        "sf_lots": lots,
        "housing_units": float(w["housing_units"].sum()),
        "land_m2": float(w["land_m2"].sum()),
        "weight": 1.0,
        "blocks": len(bi),
        "unmatched_area_share": wavg("unmatched"),
        "sliver_share": wavg("sliver"),
        "overlap_area_share": wavg("overlap"),
        "unresolved_geometry_lots": float(unres["sf_lots"].sum()),
        "district_changed_lots": district_changed_lots,
        "method": str(bi["method"].mode().iloc[0]) if len(bi) else "",
        "denominator_vintage": DENOMINATOR,
    }


def district_change(old: pd.DataFrame, new: pd.DataFrame, weights: pd.DataFrame) -> float:
    """Single-family lots whose district differs between two maps (block by block, by area).

    For each block the changed share is ``1 - sum over districts of min(old
    share, new share)``; lots are the block's lots times that share.
    """

    def by_block(p: pd.DataFrame) -> pd.DataFrame:
        return p.groupby(["block", "district"], as_index=False)["share"].sum()

    a, b = by_block(old), by_block(new)
    m = a.merge(b, on=["block", "district"], how="outer", suffixes=("_old", "_new")).fillna(0.0)
    m["same"] = np.minimum(m["share_old"], m["share_new"])
    same = m.groupby("block")["same"].sum()
    w = weights.set_index("block")["sf_lots"]
    blocks = sorted(set(a["block"]) | set(b["block"]))
    changed = [(1.0 - float(same.get(k, 0.0))) * float(w.get(k, 0.0)) for k in blocks]
    return float(sum(changed))


# --------------------------------------------------------------------------- checks and shares


def check_weights(
    table: pd.DataFrame,
    block_info: pd.DataFrame | None = None,
    pieces: pd.DataFrame | None = None,
    tol: float = 1e-9,
) -> list[str]:
    """Problems found (empty when the weights are sound).

    - Zone weights sum to 1 for each jurisdiction, map, period and benchmark
      (when it has lots). The pack keeps 4 decimals, so a table read back from a
      pack may miss 1 by up to half a unit of the 4th decimal per row; the
      tolerance allows that and no more.
    - The zone rows' lots add up to the jurisdiction row's lots (relative 1e-6),
      so no lot is lost or counted twice.
    - Every class is one of :data:`zone_rules.CLASSES`; the window shares of one
      zone sum to 1; each block's pieces sum to 1.
    """
    out = []
    z = table[table["level"] == "zone"]
    j = table[table["level"] == "jurisdiction"].set_index(["jurisdiction", "map_version"])
    for key, g in z.groupby(["jurisdiction", "map_version", "period", "benchmark"]):
        s = float(g["weight"].sum())
        allowed = max(1e-6, 0.5e-4 * len(g))
        if g["sf_lots"].sum() > 0 and abs(s - 1.0) > allowed:
            out.append(f"weights of {key} sum to {s:.9f}, not 1")
        if (key[0], key[1]) in j.index:
            want = float(j.loc[(key[0], key[1]), "sf_lots"])
            got = float(g["sf_lots"].sum())
            if abs(got - want) > max(1e-6 * want, 0.5e-4 * len(g)):
                out.append(f"lots of {key} add up to {got:.4f}, not {want:.4f}")
    bad = set(z["opportunity_class"]) - set(zr.CLASSES)
    if bad:
        out.append(f"unknown classes {sorted(bad)}")
    for key, g in z.groupby(
        ["jurisdiction", "map_version", "period", "district", "overlays", "benchmark"]
    ):
        s = float(g["window_share"].sum())
        if abs(s - 1.0) > max(1e-9, 0.5e-4 * len(g)):
            out.append(f"window shares of {key} sum to {s:.9f}")
    if pieces is not None:
        s = pieces.groupby("block")["share"].sum()
        off = s[(s - 1.0).abs() > tol]
        if len(off):
            out.append(f"{len(off)} blocks whose pieces do not sum to 1")
    return out


def exposure_shares(
    table: pd.DataFrame, benchmark: str, *, period: str = "window", by: Sequence[str] = ()
) -> pd.DataFrame:
    """Known-by-right, newly eligible, discretionary and unresolved shares, with denominators.

    Returns one row per group (``by``, e.g. ``["jurisdiction"]``; the whole
    sample when empty) with ``sf_lots`` (the denominator), the lots and the
    share in each class, and ``known_by_right_total`` (known by right plus by
    right with conditions relaxed).
    """
    z = table[
        (table["level"] == "zone") & (table["benchmark"] == benchmark) & (table["period"] == period)
    ]
    keys = list(by)
    if keys:
        g = z.groupby([*keys, "opportunity_class"])["sf_lots"].sum().unstack(fill_value=0.0)
    else:
        g = z.groupby("opportunity_class")["sf_lots"].sum().to_frame().T
    g = g.reindex(columns=list(zr.CLASSES), fill_value=0.0)
    g["sf_lots"] = g[list(zr.CLASSES)].sum(axis=1)
    for c in zr.CLASSES:
        g[f"share_{c}"] = np.where(g["sf_lots"] > 0, g[c] / g["sf_lots"].replace(0, 1), 0.0)
    g["known_by_right_total"] = g["known_by_right"] + g["conditions_relaxed"]
    g["share_known_by_right_total"] = g["share_known_by_right"] + g["share_conditions_relaxed"]
    g = g.rename(columns={c: f"lots_{c}" for c in zr.CLASSES})
    g["benchmark"] = benchmark
    g["denominator_vintage"] = DENOMINATOR
    return g.reset_index(drop=not keys)


def state_exposure(table: pd.DataFrame, benchmark: str, state_sf_lots: float) -> dict[str, float]:
    """The sample's lots in each class as a share of the state's single-family lots.

    The part of the state outside the sample is reported as ``outside_sample``
    (not resolved by this table).
    """
    s = exposure_shares(table, benchmark)
    out = {c: float(s[f"lots_{c}"].iloc[0]) / state_sf_lots for c in zr.CLASSES}
    out["sample_lots"] = float(s["sf_lots"].iloc[0])
    out["outside_sample"] = 1.0 - out["sample_lots"] / state_sf_lots
    return out


def u13_answers(
    table: pd.DataFrame, benchmark: str, *, include_guidance: bool = False, period: str = "window"
) -> pd.DataFrame:
    """Per sample jurisdiction, lots in each answer of the law-changes view (U13).

    The view counts only verified readings, so by default a class that rests on
    a ``guidance`` or ``unread`` row is ``unresolved``; ``include_guidance``
    lets guidance rows count (a sensitivity, not the shipped rule).
    """
    z = table[
        (table["level"] == "zone") & (table["benchmark"] == benchmark) & (table["period"] == period)
    ].copy()
    ok = z["review_state"].eq("verified_code") | (
        include_guidance & z["review_state"].eq("guidance")
    )
    z["answer"] = np.where(ok, z["opportunity_class"].map(U13_ANSWER), "unresolved")
    return z.groupby(["jurisdiction", "answer"], as_index=False)["sf_lots"].sum()


def move_u13(munis: pd.DataFrame, zone: pd.DataFrame) -> pd.DataFrame:
    """The law-changes view's state shares before and after the zone-level answers.

    ``munis``: one row per municipality with ``geoid``, ``answer`` and ``weight``
    (the view's own classification, :func:`visionhousing.api.law_changes.classify_munis`).
    ``zone``: :func:`u13_answers` (``jurisdiction``, ``answer``, ``sf_lots``).
    Each sample municipality's single answer is replaced by its zone-level mix,
    scaled to the municipality's own weight so the denominator does not change.
    Places outside a bill (``answer == "outside"``) stay out of the denominator.
    Returns one row per answer: ``before``, ``after`` (shares) and ``lots_before``,
    ``lots_after``.
    """
    m = munis[munis["answer"] != "outside"][["geoid", "answer", "weight"]].copy()
    m["geoid"] = m["geoid"].astype(str)
    before = m.groupby("answer")["weight"].sum()
    sample = set(zone["jurisdiction"].astype(str)) & set(m["geoid"])
    keep = m[~m["geoid"].isin(sample)]
    add = []
    for j in sorted(sample):
        own = float(m.loc[m["geoid"] == j, "weight"].sum())
        z = zone[zone["jurisdiction"].astype(str) == j]
        tot = float(z["sf_lots"].sum())
        for a, lots in z.groupby("answer")["sf_lots"].sum().items():
            add.append({"geoid": j, "answer": a, "weight": own * float(lots) / tot if tot else 0.0})
    after_df = pd.concat([keep, pd.DataFrame(add, columns=["geoid", "answer", "weight"])])
    after = after_df.groupby("answer")["weight"].sum()
    answers = ["already_allowed", "changed_condition", "newly_allowed", "unresolved"]
    total_b, total_a = float(before.sum()), float(after.sum())
    rows = [
        {
            "answer": a,
            "lots_before": float(before.get(a, 0.0)),
            "lots_after": float(after.get(a, 0.0)),
            "before": float(before.get(a, 0.0)) / total_b if total_b else 0.0,
            "after": float(after.get(a, 0.0)) / total_a if total_a else 0.0,
        }
        for a in answers
    ]
    return pd.DataFrame(rows)
