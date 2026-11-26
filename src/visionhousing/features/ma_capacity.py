"""Massachusetts: existing Section 3A modeled capacity by tract, and MassDEP estimated sewer.

Plain-language summary
----------------------
Two questions, answered from what the state itself publishes:

1. **How many homes does zoning that Massachusetts towns adopted under
   Section 3A already allow, and in which census tracts?** The state's
   compliance model gives every parcel in a compliant 3A district a unit
   capacity (``F_L_M__``, "Final Lot Multi-Family Unit Capacity": gross homes,
   before subtracting the homes already there). Each parcel's capacity is
   split across 2020 tracts by the share of its area in each tract, never by
   its centre point. A district the atlas holds no parcel for counts its own
   reported capacity (``MFUC_DIST``) instead, split by its outline. A district
   counts its parcels **or** its own total, never both: summed over a
   district's parcels, ``F_L_M__`` already equals the district total.
2. **Which homes lie inside an area MassDEP estimates a sewer system
   serves?** The share of each tract's 2020 homes inside MassDEP's estimated
   sewer service polygons. This is its own evidence class, "estimated
   service": it is never mixed with verified service (``infra_tract``) and the
   capacity screen does not read it. Placeholder circles MassDEP draws for
   systems it has not mapped are kept apart and marked uncertain.

Pure functions, no file or network access: the fetches are in
:mod:`visionhousing.data.sources.ma_capacity`, the pack step in
:func:`visionhousing.features.ma_capacity_build.add_ma_capacity`. Flat-map
geometry comes from :mod:`visionhousing.features.planar` (plain NumPy).
Settings: ``capacity`` and ``sewer_estimated`` in ``assumptions/ma_intake.yaml``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.features import planar

__all__ = [
    "CAPACITY_FIELDS",
    "TABLES",
    "SplitRule",
    "TractIndex",
    "capacity_tables",
    "circle_ratio",
    "dedupe_parcels",
    "describe",
    "parcel_frame",
    "sewer_tract_table",
    "split_shape",
]

_M_PER_DEG_LAT = 110_574.0
_M_PER_DEG_LON = 111_320.0
EVIDENCE_CLASS = "estimated service"
"""The evidence class of every MassDEP-derived share (never "verified")."""

CAPACITY_FIELDS: dict[str, str] = {
    "OBJECTID": "parcel_id",
    "LOC_ID": "loc_id",
    "ZONECOD": "zone_code",
    "MUNIID": "muni_id",
    "MUNI": "municipality",
    "STATUS": "status",
    "TRNSTST": "station_area",
    "PRCLACR": "parcel_acres",
    "RB_RSDN": "existing_units",
    "MDL_U_C": "modeled_units_before_limits",
    "F_L_M__": "capacity_units",
}
"""Atlas parcel field -> our column. ``F_L_M__`` is the common gross unit counted."""

TABLES: dict[str, dict[str, object]] = {
    "mbta_3a_capacity_tract": {
        "key": ["geoid"],
        "sources": ["massgis_mbta3a_parcels", "massgis_mbta3a_atlas", "cb_2020_tract"],
        "description": (
            "One row per 2020 tract holding existing Section 3A modeled capacity (compliance "
            "model, gross homes): parcel capacity split across tracts by area, plus districts "
            "with no parcel in the atlas (their own total, split by outline); never both for "
            "one district. A tract not listed holds no district of a town HLC found "
            "compliant. Massachusetts only."
        ),
        "tier": "2",
    },
    "mbta_3a_capacity_district": {
        "key": ["district_id", "zone_code"],
        "sources": ["massgis_mbta3a_parcels", "massgis_mbta3a_atlas"],
        "description": (
            "One row per Section 3A district (and per zone code of parcels the district "
            "layer lacks): reported district capacity, the sum of its parcels, which of the "
            "two is counted, and the capacity left outside every tract or folded from "
            "slivers. The coverage record of mbta_3a_capacity_tract. Massachusetts only."
        ),
        "tier": "2",
    },
    "ma_sewer_estimated_tract": {
        "key": ["geoid"],
        "sources": ["massdep_sewer_service_areas", "tiger_2020_tabblock"],
        "description": (
            "One row per 2020 tract with homes: share of 2020 homes inside a MassDEP "
            "estimated sewer service area (evidence class 'estimated service', kept apart "
            "from verified service in infra_tract and not read by the capacity screen), "
            "confirmed and public-plant parts, and homes only inside a placeholder circle "
            "(uncertain). Massachusetts only."
        ),
        "tier": "2",
    },
}
"""The pack tables this module makes: key, source ids, description, tier."""

_COLUMNS: dict[str, dict[str, tuple[str, str]]] = {
    "mbta_3a_capacity_tract": {
        "geoid": ("code", "2020 census tract (11 digits)"),
        "county": ("code", "County code (5 digits)"),
        "capacity_units": (
            "units",
            "Existing 3A modeled capacity in the tract, gross homes: capacity_parcel_units + "
            "capacity_district_units",
        ),
        "capacity_parcel_units": (
            "units",
            "Parcel capacity (atlas F_L_M__) times each parcel's area share in the tract",
        ),
        "capacity_district_units": (
            "units",
            "Reported capacity (MFUC_DIST) of districts with no parcel in the atlas, times the "
            "district's area share in the tract",
        ),
        "station_area_units": (
            "units",
            "Of capacity_parcel_units, parcels the compliance model places in a half-mile "
            "station area (atlas TRNSTST = Y)",
        ),
        "modeled_units_before_limits": (
            "units",
            "Parcel capacity before the lot limits (atlas MDL_U_C), split the same way; context",
        ),
        "existing_units": (
            "units",
            "Homes already on the parcels (atlas RB_RSDN), split the same way; context, not "
            "subtracted",
        ),
        "parcel_acres": ("acres", "Parcel acres (atlas PRCLACR) split the same way"),
        "parcels": ("parcels", "3A parcels with a piece in the tract"),
        "districts": ("districts", "3A districts with capacity in the tract"),
        "atlas_read_on": ("date", "Date the parcel layer was read"),
    },
    "mbta_3a_capacity_district": {
        "district_id": (
            "code",
            "Atlas district id (mbta_3a_districts.district_id); empty for parcels whose zone "
            "code no district carries",
        ),
        "zone_code": ("code", "Atlas zone code"),
        "municipality": ("text", "Town, as the atlas writes it"),
        "capacity_district": ("units", "Reported district capacity (atlas MFUC_DIST)"),
        "parcel_capacity": ("units", "Sum of the district's parcel capacity (atlas F_L_M__)"),
        "parcels": ("parcels", "Parcels of the district in the atlas (after duplicates)"),
        "basis": (
            "category",
            "What is counted: parcels, district_outline (no parcels: MFUC_DIST spread over the "
            "outline), parcels_no_district, duplicate_code (another district with the same "
            "code and town counts the parcels) or none",
        ),
        "capacity_counted": ("units", "Capacity this row puts into mbta_3a_capacity_tract"),
        "difference": ("units", "parcel_capacity - capacity_district (empty without parcels)"),
        "station_area_capacity": ("units", "Of parcel_capacity, parcels in a station area"),
        "unmatched_units": ("units", "Of capacity_counted, the part outside every tract"),
        "sliver_units": (
            "units",
            "Of capacity_counted, pieces under the sliver share folded into a larger piece",
        ),
    },
    "ma_sewer_estimated_tract": {
        "geoid": ("code", "2020 census tract (11 digits)"),
        "homes": ("units", "2020 Census housing units in the tract"),
        "est_share": (
            "share",
            "Share of homes inside a MassDEP estimated sewer service polygon (placeholder "
            "circles left out)",
        ),
        "est_confirmed_share": (
            "share",
            "Of those, inside an area the system confirmed (SERVCECONF = Confirmed)",
        ),
        "est_potw_share": (
            "share",
            "Share of homes inside a public plant's municipal, regional or district area",
        ),
        "circle_only_share": (
            "share",
            "Share of homes inside only a placeholder circle (not a service area; uncertain)",
        ),
        "geometry_quality": (
            "category",
            "polygon, approximate_circle (only placeholder circles), mixed, or none",
        ),
        "evidence_class": ("category", "Always 'estimated service': not verified service"),
        "edition": ("text", "MassDEP edition read"),
        "read_on": ("date", "Date the layers were read"),
    },
}


def describe(table: str, name: str) -> tuple[str, str]:
    """Unit and plain description of a column of one of this module's tables."""
    try:
        return _COLUMNS[table][name]
    except KeyError:
        raise ValueError(f"no data-dictionary entry for {table}.{name}") from None


# --------------------------------------------------------------------------- shapes


def _rings_of(geometry: Mapping[str, Any] | Sequence[Any] | None) -> list[np.ndarray]:
    """Rings (lon/lat arrays) of a GeoJSON geometry or of an esri ``rings`` list."""
    if not geometry:
        return []
    if isinstance(geometry, Mapping):
        return [r for poly in planar.geojson_polygons(geometry) for r in poly if len(r) >= 3]
    return [np.asarray(r, dtype=float)[:, :2] for r in geometry if len(r) >= 3]


@dataclass
class TractIndex:
    """Tract outlines in lon/lat with their bounding boxes, for fast candidate search."""

    geoids: list[str]
    rings: list[list[np.ndarray]]
    boxes: np.ndarray = field(repr=False)

    @classmethod
    def build(cls, tract_shapes: Iterable[tuple[str, Mapping[str, Any]]]) -> TractIndex:
        """From ``(tract code, GeoJSON geometry)`` pairs."""
        geoids, rings, boxes = [], [], []
        for geoid, geometry in tract_shapes:
            r = _rings_of(geometry)
            if not r:
                continue
            geoids.append(str(geoid))
            rings.append(r)
            boxes.append(planar.rings_bounds(r))
        return cls(geoids, rings, np.asarray(boxes, dtype=float).reshape(-1, 4))

    def candidates(self, box: tuple[float, float, float, float]) -> np.ndarray:
        """Indexes of tracts whose bounding box meets ``box``."""
        b = self.boxes
        hit = (b[:, 0] <= box[2]) & (b[:, 2] >= box[0]) & (b[:, 1] <= box[3]) & (b[:, 3] >= box[1])
        return np.flatnonzero(hit)


@dataclass(frozen=True)
class SplitRule:
    """How shapes are split across tracts (``capacity.split`` in the settings)."""

    cells_per_side: int = 40
    min_cell_m: float = 0.5
    min_piece_share: float = 0.05

    @classmethod
    def from_settings(cls, spec: Mapping[str, Any]) -> SplitRule:
        """From the ``capacity.split`` block."""
        return cls(int(spec["cells_per_side"]), float(spec["min_cell_m"]),
                   float(spec["min_piece_share"]))  # fmt: skip


@dataclass(frozen=True)
class Split:
    """One shape's pieces: ``{tract: share}`` (shares and ``unmatched`` sum to 1)."""

    shares: dict[str, float]
    unmatched: float
    sliver: float
    method: str


def _fold(raw: dict[str, float], unmatched: float, min_share: float, method: str) -> Split:
    """Fold pieces under ``min_share`` (and a small unmatched part) into the largest piece."""
    if not raw:
        return Split({}, 1.0, 0.0, method)
    big = max(raw, key=lambda t: (raw[t], t))
    shares = {big: raw[big]}
    sliver = 0.0
    for t, s in raw.items():
        if t == big:
            continue
        if s < min_share:
            sliver += s
        else:
            shares[t] = s
    if 0 < unmatched < min_share:
        sliver += unmatched
        unmatched = 0.0
    shares[big] += sliver
    total = sum(shares.values()) + unmatched
    if total > 0 and abs(total - 1.0) > 1e-12:
        shares = {t: s / total for t, s in shares.items()}
        unmatched /= total
    return Split(shares, unmatched, sliver, method)


def split_shape(rings: Sequence[np.ndarray], index: TractIndex, rule: SplitRule) -> Split:
    """Split one shape (lon/lat rings, even-odd rule) across tracts by area.

    1. Tracts whose bounding box meets the shape's are the candidates.
    2. When every corner of the shape is inside one candidate tract, the
       shape goes wholly to it (``method = corners``).
    3. Otherwise the shape and the candidate tracts are drawn on a flat grid
       in metres over the shape's bounding box (``rule.cells_per_side`` cells a
       side, none smaller than ``rule.min_cell_m``); a cell belongs to a shape
       when its centre is inside it. A tract's share is the shape's cells
       inside it over all the shape's cells; the rest is ``unmatched`` (outside
       every tract) (``method = grid``). A shape too thin to hold a cell centre
       goes to the tract holding most of its corners (``method = corners_majority``).
    4. Pieces under ``rule.min_piece_share`` are folded into the largest piece
       (``sliver``), so shares always sum to 1 with ``unmatched``.
    """
    rings = [np.asarray(r, dtype=float)[:, :2] for r in rings if len(r) >= 3]
    if not rings:
        return Split({}, 1.0, 0.0, "empty")
    box = planar.rings_bounds(rings)
    cand = index.candidates(box)
    if not len(cand):
        return Split({}, 1.0, 0.0, "outside")
    pts = np.concatenate(rings)
    votes: dict[str, int] = {}
    for k in cand:
        inside = planar.contains(pts[:, 0], pts[:, 1], index.rings[k])
        n = int(inside.sum())
        if n == len(pts):
            return Split({index.geoids[k]: 1.0}, 0.0, 0.0, "corners")
        if n:
            votes[index.geoids[k]] = n
    lon0, lat0 = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    kx = _M_PER_DEG_LON * math.cos(math.radians(lat0))

    def move(r: np.ndarray) -> np.ndarray:
        return np.column_stack(((r[:, 0] - lon0) * kx, (r[:, 1] - lat0) * _M_PER_DEG_LAT))

    shape_m = [move(r) for r in rings]
    xmin, ymin, xmax, ymax = planar.rings_bounds(shape_m)
    cell = max(max(xmax - xmin, ymax - ymin) / rule.cells_per_side, rule.min_cell_m)
    grid = planar.grid_for_bounds(xmin, ymin, xmax, ymax, cell)
    inside = planar.fill_rings(grid, shape_m)
    total = int(inside.sum())
    if not total:
        if not votes:
            return Split({}, 1.0, 0.0, "corners_majority")
        best = max(votes, key=lambda t: (votes[t], t))
        return Split({best: 1.0}, 0.0, 0.0, "corners_majority")
    raw: dict[str, float] = {}
    for k in cand:
        n = int((inside & planar.fill_rings(grid, [move(r) for r in index.rings[k]])).sum())
        if n:
            raw[index.geoids[k]] = raw.get(index.geoids[k], 0.0) + n / total
    unmatched = max(0.0, 1.0 - sum(raw.values()))
    return _fold(raw, unmatched, rule.min_piece_share, "grid")


# --------------------------------------------------------------------------- 3A capacity


def parcel_frame(features: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Parcel attributes (renamed by :data:`CAPACITY_FIELDS`) plus their ``rings``.

    ``station_area`` is 1 for "Y", 0 for "N", empty otherwise. Numbers that are
    missing stay missing (never 0).
    """
    rows = []
    for f in features:
        a = f.get("attributes") or {}
        row = {ours: a.get(theirs) for theirs, ours in CAPACITY_FIELDS.items()}
        row["rings"] = f.get("rings") or []
        rows.append(row)
    frame = pd.DataFrame(rows, columns=[*CAPACITY_FIELDS.values(), "rings"])
    flag = frame["station_area"].astype(str).str.strip().str.upper()
    frame["station_area"] = pd.array(
        [1 if v == "Y" else 0 if v == "N" else pd.NA for v in flag], dtype="Int64"
    )
    for c in ("parcel_acres", "existing_units", "modeled_units_before_limits", "capacity_units"):
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    for c in ("loc_id", "zone_code", "municipality"):
        frame[c] = frame[c].fillna("").astype(str).str.strip()
    frame["muni_id"] = pd.to_numeric(frame["muni_id"], errors="coerce").astype("Int64")
    return frame


def dedupe_parcels(parcels: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep one row per parcel id (``loc_id``): the one with the larger capacity.

    A parcel listed in two overlapping districts would otherwise count the
    same land twice. Rows without a ``loc_id`` are all kept. Returns the kept
    rows and the dropped ones.
    """
    named = parcels["loc_id"] != ""
    order = parcels.assign(_cap=parcels["capacity_units"].fillna(-1)).sort_values(
        ["loc_id", "_cap", "parcel_id"], ascending=[True, False, True]
    )
    dup = order.duplicated("loc_id", keep="first") & named.loc[order.index]
    dropped = order[dup].drop(columns="_cap")
    kept = parcels.drop(index=dropped.index)
    return kept.sort_values("parcel_id").reset_index(drop=True), dropped.reset_index(drop=True)


def _district_rows(districts: pd.DataFrame, parcels: pd.DataFrame) -> pd.DataFrame:
    """Which districts count their parcels, which their own total (:func:`capacity_tables`)."""
    d = districts.copy()
    d["zone_code"] = d["zone_code"].fillna("").astype(str).str.strip()
    d["muni_id"] = pd.to_numeric(d["muni_id"], errors="coerce").astype("Int64")
    d = d.sort_values("district_id")
    p = parcels.assign(_station=parcels["capacity_units"].where(parcels["station_area"] == 1, 0))
    sums = (
        p.groupby(["zone_code", "muni_id"], dropna=False)
        .agg(
            parcel_capacity=("capacity_units", lambda s: s.sum(min_count=1)),
            parcels=("parcel_id", "size"),
            station_area_capacity=("_station", "sum"),
        )
        .reset_index()
    )
    first = ~d.duplicated(["zone_code", "muni_id"], keep="first")
    d["_first"] = first
    out = d.merge(sums, on=["zone_code", "muni_id"], how="left")
    out["parcels"] = out["parcels"].fillna(0).astype("int64")
    has = (out["parcels"] > 0) & out["_first"]
    shared = (out["parcels"] > 0) & ~out["_first"]
    known = out["capacity_district"].notna()
    out["basis"] = np.select(
        [has, shared, known], ["parcels", "duplicate_code", "district_outline"], "none"
    )
    out["capacity_counted"] = np.select(
        [has, shared, known], [out["parcel_capacity"].fillna(0), 0.0,
                               out["capacity_district"]], 0.0,
    )  # fmt: skip
    for c in ("parcel_capacity", "station_area_capacity"):
        out.loc[shared, c] = np.nan
    out.loc[shared, "parcels"] = 0
    out["difference"] = (out["parcel_capacity"] - out["capacity_district"]).where(has)
    # parcels whose (zone code, town) no district carries: counted on their own
    keys = set(zip(d["zone_code"], d["muni_id"], strict=True))
    orphan = sums[[(z, m) not in keys for z, m in zip(sums["zone_code"], sums["muni_id"],
                                                         strict=True)]].copy()  # fmt: skip
    if len(orphan):
        names = p.groupby(["zone_code", "muni_id"], dropna=False)["municipality"].first()
        pairs = zip(orphan["zone_code"], orphan["muni_id"], strict=True)
        orphan["municipality"] = [names.get((z, m), "") for z, m in pairs]
        orphan["district_id"] = pd.NA
        orphan["capacity_district"] = np.nan
        orphan["basis"] = "parcels_no_district"
        orphan["capacity_counted"] = orphan["parcel_capacity"].fillna(0)
        orphan["difference"] = np.nan
        out = pd.concat([out, orphan], ignore_index=True)
    return out


def capacity_tables(
    parcels: pd.DataFrame,
    districts: pd.DataFrame,
    tract_shapes: Iterable[tuple[str, Mapping[str, Any]]],
    rule: SplitRule,
    *,
    read_on: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """The ``mbta_3a_capacity_tract`` and ``mbta_3a_capacity_district`` tables and a summary.

    Rules (no double counting):

    - every parcel row (after :func:`dedupe_parcels`) is counted once, split
      across tracts by :func:`split_shape`;
    - a district whose zone code and town match parcels counts nothing of its
      own (its ``MFUC_DIST`` is the same capacity, summed);
    - only a district with no parcel counts its ``MFUC_DIST``, split by its
      outline;
    - two districts with the same code and town: the parcels count once, under
      the lower district id.

    Args:
        parcels: :func:`parcel_frame` after :func:`dedupe_parcels`.
        districts: The pack's ``mbta_3a_districts`` table (``district_id``,
            ``zone_code``, ``muni_id``, ``municipality``, ``county``,
            ``capacity_district``, ``geometry`` as GeoJSON text).
        tract_shapes: ``(tract code, GeoJSON geometry)`` pairs of the pack's tracts.
        rule: How shapes are split.
        read_on: Date the parcel layer was read.

    Returns:
        The tract table, the district table and a summary dict (totals,
        unmatched and sliver units, split methods, coverage).
    """
    index = TractIndex.build(tract_shapes)
    rows = _district_rows(districts, parcels)
    pieces: list[dict[str, Any]] = []
    methods: dict[str, int] = {}
    unmatched_by: dict[tuple[str, str], float] = {}
    sliver_by: dict[tuple[str, str], float] = {}
    zkey = {
        (z, m): (str(i) if pd.notna(i) else "")
        for z, m, i, b in zip(rows["zone_code"], rows["muni_id"], rows["district_id"],
                              rows["basis"], strict=True)
        if b in ("parcels", "parcels_no_district")
    }  # fmt: skip
    values = ("capacity_units", "modeled_units_before_limits", "existing_units", "parcel_acres")
    for p in parcels.itertuples(index=False):
        split = split_shape(_rings_of(p.rings), index, rule)
        methods[split.method] = methods.get(split.method, 0) + 1
        owner = (zkey.get((p.zone_code, p.muni_id), ""), p.zone_code)
        cap = 0.0 if pd.isna(p.capacity_units) else float(p.capacity_units)
        unmatched_by[owner] = unmatched_by.get(owner, 0.0) + cap * split.unmatched
        sliver_by[owner] = sliver_by.get(owner, 0.0) + cap * split.sliver
        for tract, share in split.shares.items():
            station = bool(pd.notna(p.station_area) and int(p.station_area) == 1)
            piece = {"geoid": tract, "kind": "parcel", "owner": owner[0] or f"z:{owner[1]}",
                     "parcel": p.parcel_id, "share": share, "station": station}  # fmt: skip
            for v in values:
                x = getattr(p, v)
                piece[v] = np.nan if pd.isna(x) else float(x) * share
            pieces.append(piece)
    fallback = rows[rows["basis"] == "district_outline"]
    shapes = districts.set_index("district_id")["geometry"]
    for r in fallback.itertuples(index=False):
        text = shapes.get(r.district_id)
        geometry = json.loads(text) if isinstance(text, str) and text else None
        split = split_shape(_rings_of(geometry), index, rule)
        methods[f"district_{split.method}"] = methods.get(f"district_{split.method}", 0) + 1
        owner = (str(r.district_id), r.zone_code)
        cap = float(r.capacity_district)
        unmatched_by[owner] = unmatched_by.get(owner, 0.0) + cap * split.unmatched
        sliver_by[owner] = sliver_by.get(owner, 0.0) + cap * split.sliver
        for tract, share in split.shares.items():
            pieces.append({"geoid": tract, "kind": "district", "owner": str(r.district_id),
                           "parcel": None, "share": share, "station": False,
                           "district_units": cap * share})  # fmt: skip
    frame = pd.DataFrame(pieces)
    if frame.empty:
        frame = pd.DataFrame(columns=["geoid", "kind", "owner", "parcel", "share", "station",
                                      *values, "district_units"])  # fmt: skip
    for c in (*values, "district_units"):
        if c not in frame.columns:
            frame[c] = np.nan
    par = frame[frame["kind"] == "parcel"]
    tract = (
        frame.assign(
            _par=frame["capacity_units"].where(frame["kind"] == "parcel"),
            _sta=frame["capacity_units"].where((frame["kind"] == "parcel") & frame["station"]),
        )
        .groupby("geoid")
        .agg(
            capacity_parcel_units=("_par", lambda s: s.sum(min_count=1)),
            capacity_district_units=("district_units", lambda s: s.sum(min_count=1)),
            station_area_units=("_sta", "sum"),
            modeled_units_before_limits=("modeled_units_before_limits", "sum"),
            existing_units=("existing_units", "sum"),
            parcel_acres=("parcel_acres", "sum"),
            districts=("owner", "nunique"),
        )
        .reset_index()
    )
    tract["parcels"] = tract["geoid"].map(par.groupby("geoid")["parcel"].nunique()).fillna(0)
    for c in ("capacity_parcel_units", "capacity_district_units"):
        tract[c] = tract[c].fillna(0.0)
    tract["capacity_units"] = tract["capacity_parcel_units"] + tract["capacity_district_units"]
    tract["county"] = tract["geoid"].str[:5]
    tract["atlas_read_on"] = read_on
    for c in ("capacity_units", "capacity_parcel_units", "capacity_district_units",
              "station_area_units", "modeled_units_before_limits", "existing_units"):  # fmt: skip
        tract[c] = tract[c].round(1)
    tract["parcel_acres"] = tract["parcel_acres"].round(2)
    tract["parcels"] = tract["parcels"].astype("int64")
    tract["districts"] = tract["districts"].astype("int64")
    tract = tract[list(_COLUMNS["mbta_3a_capacity_tract"])].sort_values("geoid")
    rows["unmatched_units"] = [
        round(unmatched_by.get((str(i) if pd.notna(i) else "", z), 0.0), 1)
        for i, z in zip(rows["district_id"], rows["zone_code"], strict=True)
    ]
    rows["sliver_units"] = [
        round(sliver_by.get((str(i) if pd.notna(i) else "", z), 0.0), 1)
        for i, z in zip(rows["district_id"], rows["zone_code"], strict=True)
    ]
    district = rows[list(_COLUMNS["mbta_3a_capacity_district"])].copy()
    district["district_id"] = district["district_id"].astype("Int64")
    district = district.sort_values(["district_id", "zone_code"], na_position="last")
    counted = float(district["capacity_counted"].sum())
    summary = {
        "capacity_counted": round(counted, 1),
        "capacity_in_tracts": round(float(tract["capacity_units"].sum()), 1),
        "unmatched_units": round(float(district["unmatched_units"].sum()), 1),
        "sliver_units": round(float(district["sliver_units"].sum()), 1),
        "reported_district_total": float(districts["capacity_district"].sum()),
        "parcels": len(parcels),
        "methods": dict(sorted(methods.items())),
        "basis": district["basis"].value_counts().sort_index().to_dict(),
        "municipalities": int(districts["municipality"].nunique()),
        "tracts": len(tract),
    }
    return tract.reset_index(drop=True), district.reset_index(drop=True), summary


# --------------------------------------------------------------------------- sewer


def circle_ratio(rings: Sequence[np.ndarray]) -> tuple[float, float]:
    """Isoperimetric ratio (4 pi area / perimeter^2; 1 for a circle) and area in acres.

    Rings are lon/lat; they are put on a flat map in metres around the shape.
    The area is the absolute sum of the rings' signed areas (outer rings and
    holes wind opposite ways in the map service's answers), the perimeter the
    length of every ring.
    """
    rings = [np.asarray(r, dtype=float)[:, :2] for r in rings if len(r) >= 3]
    if not rings:
        return 0.0, 0.0
    box = planar.rings_bounds(rings)
    lon0, lat0 = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    kx = _M_PER_DEG_LON * math.cos(math.radians(lat0))
    signed = perim = 0.0
    for r in rings:
        x, y = (r[:, 0] - lon0) * kx, (r[:, 1] - lat0) * _M_PER_DEG_LAT
        xn, yn = np.roll(x, -1), np.roll(y, -1)
        signed += float((x * yn - xn * y).sum()) / 2.0
        perim += float(np.hypot(xn - x, yn - y).sum())
    area = abs(signed)
    if perim <= 0:
        return 0.0, 0.0
    return 4 * math.pi * area / perim**2, area / 4_046.8564224


def sewer_tract_table(
    blocks: pd.DataFrame,
    layers: Mapping[str, Mapping[str, Any]],
    *,
    circle_min_ratio: float,
    circle_max_acres: float,
    edition: str,
    read_on: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """The ``ma_sewer_estimated_tract`` table and a summary.

    Each 2020 block with homes is placed by its internal point
    (:func:`visionhousing.features.infrastructure.feature_index`) in, in order:
    a confirmed polygon, an unconfirmed polygon, a placeholder circle. A
    polygon is a placeholder circle when :func:`circle_ratio` is at least
    ``circle_min_ratio`` and its area under ``circle_max_acres``.

    Args:
        blocks: The pack's ``blocks`` table (``tract``, ``housing_units``, ``lat``, ``lon``).
        layers: Polygon layers from the cached MassDEP file (``non_potw``, ``potw``).
        circle_min_ratio: Smallest isoperimetric ratio of a placeholder circle.
        circle_max_acres: Largest area of a placeholder circle.
        edition: MassDEP edition text written into every row.
        read_on: Read date written into every row.
    """
    from visionhousing.features.infrastructure import feature_index

    groups: dict[str, list[dict[str, Any]]] = {"confirmed": [], "unconfirmed": [], "circle": []}
    potw: list[dict[str, Any]] = []
    circles = 0
    for name, layer in layers.items():
        for f in layer.get("features") or []:
            rings = [np.asarray(r, dtype=float) for r in f.get("rings") or [] if len(r) >= 4]
            if not rings:
                continue
            a = f.get("attributes") or {}
            ratio, acres = circle_ratio(rings)
            if ratio >= circle_min_ratio and acres < circle_max_acres:
                groups["circle"].append(f)
                circles += 1
                continue
            conf = str(a.get("SERVCECONF") or "").strip().lower() == "confirmed"
            groups["confirmed" if conf else "unconfirmed"].append(f)
            if name == "potw" and str(a.get("SECTOR") or "") == "Municipal/Regional/District":
                potw.append(f)
    b = blocks[pd.to_numeric(blocks["housing_units"], errors="coerce").fillna(0) > 0].copy()
    lon, lat = b["lon"].to_numpy(float), b["lat"].to_numpy(float)
    answer = np.full(len(b), "", dtype=object)
    for key in ("confirmed", "unconfirmed", "circle"):
        free = answer == ""
        if not free.any() or not groups[key]:
            continue
        hit = feature_index(lon[free], lat[free], groups[key]) >= 0
        idx = np.flatnonzero(free)[hit]
        answer[idx] = key
    b["_ans"] = answer
    b["_potw"] = feature_index(lon, lat, potw) >= 0 if potw else False
    b["tract"] = b["tract"].astype(str)
    hu = b["housing_units"].astype(float)
    g = (
        b.assign(
            _hu=hu,
            _conf=hu.where(b["_ans"] == "confirmed", 0.0),
            _unc=hu.where(b["_ans"] == "unconfirmed", 0.0),
            _circ=hu.where(b["_ans"] == "circle", 0.0),
            _pot=hu.where(b["_potw"], 0.0),
        )
        .groupby("tract")[["_hu", "_conf", "_unc", "_circ", "_pot"]]
        .sum()
    )
    out = pd.DataFrame({"geoid": g.index.astype(str)})
    homes = g["_hu"].to_numpy()
    out["homes"] = homes.astype("int64")
    out["est_share"] = np.round((g["_conf"] + g["_unc"]).to_numpy() / homes, 3)
    out["est_confirmed_share"] = np.round(g["_conf"].to_numpy() / homes, 3)
    out["est_potw_share"] = np.round(g["_pot"].to_numpy() / homes, 3)
    out["circle_only_share"] = np.round(g["_circ"].to_numpy() / homes, 3)
    est, circ = out["est_share"] > 0, out["circle_only_share"] > 0
    out["geometry_quality"] = np.select(
        [est & circ, circ, est], ["mixed", "approximate_circle", "polygon"], "none"
    )
    out["evidence_class"] = EVIDENCE_CLASS
    out["edition"] = edition
    out["read_on"] = read_on
    total = float(hu.sum())
    summary = {
        "polygons": sum(len(v) for v in groups.values()),
        "placeholder_circles": circles,
        "homes": int(total),
        "homes_in_estimated_area": round(float((g["_conf"] + g["_unc"]).sum()) / total, 3)
        if total
        else None,
        "homes_confirmed": round(float(g["_conf"].sum()) / total, 3) if total else None,
        "homes_circle_only": round(float(g["_circ"].sum()) / total, 3) if total else None,
        "tracts": len(out),
        "tracts_by_quality": out["geometry_quality"].value_counts().sort_index().to_dict(),
    }
    return out.sort_values("geoid").reset_index(drop=True), summary
