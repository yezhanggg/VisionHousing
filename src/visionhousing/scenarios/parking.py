"""Benchmark reform 4: remove residential parking minimums.

Plain-language summary
----------------------
Most local codes require a set number of parking spaces per new home. When the
minimums go, builders still build most of those spaces (lenders, buyers and
tenants want them), but not all. Each space not built saves its cost, and on a
tight site the land it would have used can hold a few more homes.

The chain, for each county, structure type and Monte Carlo draw:

1. **Spaces required before the reform**, per unit, by structure type (ledger):
   apartments (5+ units) use the multifamily minimum; 2-unit and 3-4-unit
   buildings use the attached minimum. 1-unit buildings are left out (single
   homes keep their driveways and garages; the permit data cannot split
   townhouses from detached homes).
2. **Spaces still built** = required x the share still built (the benchmark parking
   rule: about 60% in transit-rich tracts, 85-100% elsewhere). Both shares are ledger
   parameters, so they are explicit and can be edited with an override.
3. **Spaces avoided** = required - still built.
4. **Cost per space** = a mix of surface, structured (above-ground garage) and
   underground spaces. Apartments use the tract class's mix; 2-4-unit buildings
   use a mostly-surface mix.
5. **Saving per unit (dC)** = spaces avoided x cost per space.
6. **Cost channel (Channel B)**: added = baseline x e x (dC / V) x k, with the
   shared formula in ``scenarios.fees``.
7. **Site yield**: added = baseline x site-yield gain x share of spaces avoided.
   The gain (ledger, small default) is the extra units a site holds if all of
   its parking land is freed; it scales with the share actually freed.

A county is a mix of transit-rich and other tracts. The county's saving for a
structure type is the two classes' savings weighted by the share of the
county's existing homes of that type that sit in transit-rich tracts (ACS
units-in-structure counts). Because everything after step 5 is linear in the
saving, weighting the saving is exact.

**Transit-rich tracts.** A tract is transit-rich when at least half of its
housing units (``parking.transit_rich_min_share``) are in census blocks within
the major-stop distance of a major transit stop: any rail station within 1/2 mile, or a
bus stop with at least 64 weekday departures within 1/4 mile, straight line. A
tract whose boundary contains a major stop also counts (urban tracts are small,
and the test state's blocks are too coarse for the distance test alone). For now
the stops come from the data pack's stop table (Testland in the tests).

**Where a minimum binds.** All responses are one rule, per matched design
(building type and transit class): spaces avoided =
max(v, m_before) - max(v, m_after), with ``v`` the spaces a project provides
with no minimum and ``m`` the legal minimum (the ledger default 1.5 / 2.0,
labelled as a default where no verified local schedule exists). A minimum
binds a project when m > v. The **binding share** is the share of baseline
homes whose project the minimum binds; it is reported in every draw at each
level of a partial cut (``binding_share_kept_<pct>``, levels in
``assumptions/cost_reforms.yaml``), beside the added homes at that level
(``added_kept_<pct>``). Where the old minimum binds nobody (m_before = 0, or
v >= m_before) nothing is avoided. Under the benchmark ``v`` is a share of the
old minimum (about 60% near transit, 85-100% elsewhere), so halving the
minimum leaves it binding almost nowhere and adds about as many homes as
removing it: the flat part of the curve is the binding share at zero.

**Verified local minimums** (variant ``parking_verified_minimums``,
:func:`minimum_mix`). Where a municipality's minimum is verified and in force
(:mod:`visionhousing.law.parking_rules`), its share of the county's homes uses
that minimum as ``m_before``; the rest keeps the labelled default. ``v`` stays
as set from the default minimum, so a verified minimum at or below ``v`` (or
zero) avoids nothing. Coverage (share of baseline homes under verified vs
default minimums) is reported in every draw (:func:`minimum_records`).

**The documented 50% bar** (variant ``parking_verified_minimums_bar``,
:func:`bar_minimums`). The law hooks let a county use its own read value only
where its verified rows cover at least half of its residents
(``assumptions/law/hooks.yaml``, ``min_coverage``; on 2026-11-26 Fayette, Pike
and Warren). This variant follows that rule for non-zero minimums too: a
verified minimum above zero replaces the default only in those counties;
everywhere else the default stays. Zeros work exactly as in the benchmark,
through the county reach factor, and never enter the mix (so nothing is
counted twice). When no county passes the bar the variant equals the
benchmark draw for draw.

**Local readings** (variants ``vt_parking_town_bylaws`` and
``wy_parking_local``). Vermont and Wyoming have no municipal rule table; their
town and city readings (``assumptions/law/local_readings.yaml``) give the
verified minimums instead (:func:`visionhousing.engine.law_floor.local_parking_minimums`),
mixed in by :func:`minimum_mix` the same way. Only a verified parking fact of
a place whose reading is in force on the run's as-of date counts; drafts,
flagged facts and places the pack cannot put on tracts keep the default, so
with nothing placed the variant equals the benchmark draw for draw. The county
reach factor stays as in the benchmark.

**Zero reduction gives zero.** If the share still built is 1 in both tract
classes, spaces avoided are 0, the saving is 0 and the site-yield share is 0, so
both parts are exactly zero.

All functions are pure: data frames and the ledger go in, records come out.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.baseline.panel import STRUCTURE_TYPES
from visionhousing.engine import records as rec
from visionhousing.ledger import DEFAULT_DRAWS, Ledger, apply_overrides
from visionhousing.scenarios.fees import (
    CROSSCHECK_IDS,
    ELASTICITY,
    VALUE_IDS,
    ChannelBResult,
    Market,
    Step,
    _central,
    _draws,
    _scaled,
    baseline_array,
    channel_b,
    crosscheck_records,
    funnel_steps,
    load_cost_settings,
    market_from_tracts,
    proforma_crosscheck,
    range_records,
    swing_table,
    value_per_unit,
)

__all__ = [
    "PARKING_IDS",
    "RESPONSES",
    "SPREAD",
    "TRANSIT_IDS",
    "VOLUNTARY_IDS",
    "MinimumMix",
    "bar_minimums",
    "binding_records",
    "binding_share",
    "expected_provided",
    "major_stops",
    "minimum_mix",
    "minimum_records",
    "parking_reform",
    "parking_saving",
    "share_below",
    "tracts_with_stop",
    "transit_rich_tracts",
    "transit_weights",
]

K = "supply.saving_passthrough_k"
REQ_MF = "parking.required_spaces_per_unit_multifamily"
REQ_ATT = "parking.required_spaces_per_unit_attached"
STILL_TR = "parking.share_still_built_transit_rich"
STILL_EL = "parking.share_still_built_elsewhere"
SURFACE = "parking.cost_per_space_surface"
STRUCTURED = "parking.cost_per_space_structured"
UNDERGROUND = "parking.cost_per_space_underground"
MIX_S_TR = "parking.mix_structured_transit_rich"
MIX_U_TR = "parking.mix_underground_transit_rich"
MIX_S_EL = "parking.mix_structured_elsewhere"
MIX_U_EL = "parking.mix_underground_elsewhere"
MIX_SURF_ATT = "parking.mix_surface_attached"
SITE_YIELD = "parking.site_yield_gain"

SAVING_IDS: tuple[str, ...] = (
    REQ_MF,
    REQ_ATT,
    STILL_TR,
    STILL_EL,
    SURFACE,
    STRUCTURED,
    UNDERGROUND,
    MIX_S_TR,
    MIX_U_TR,
    MIX_S_EL,
    MIX_U_EL,
    MIX_SURF_ATT,
)
"""Ledger ids behind the saving per unit."""

TRANSIT_IDS: tuple[str, ...] = (
    "parking.transit_bus_min_departures",
    "parking.transit_rail_radius_miles",
    "parking.transit_bus_radius_miles",
    "parking.transit_rich_min_share",
)
"""Ledger ids behind the transit-rich tract flag (fixed values of the major-stop rule)."""

PARKING_IDS: tuple[str, ...] = (*SAVING_IDS, ELASTICITY, K, SITE_YIELD, *VALUE_IDS)
"""Ledger ids sampled for the parking estimate."""

VOL_APT_TR = "parking.voluntary_spaces_apartment_transit_rich"
VOL_APT_EL = "parking.voluntary_spaces_apartment_elsewhere"
VOL_SMALL_TR = "parking.voluntary_spaces_small_transit_rich"
VOL_SMALL_EL = "parking.voluntary_spaces_small_elsewhere"
VOLUNTARY_IDS: tuple[str, ...] = (VOL_APT_TR, VOL_APT_EL, VOL_SMALL_TR, VOL_SMALL_EL)
"""Spaces per home builders provide with no minimum, by building type and transit access
(read only under the ``provided`` and ``voluntary`` responses, variants
``parking_provided_spaces`` and ``parking_voluntary_floor``)."""

SPREAD = "parking.voluntary_project_spread"
"""Spread of voluntary spaces across projects (``voluntary`` response only)."""

RESPONSES = ("share", "provided", "voluntary")
"""How the spaces still built are set. All three are the same rule,
spaces avoided = max(v, m_before) - max(v, m_after), with ``v`` the spaces a
project would provide with no minimum and ``m`` the legal minimum:

- ``share`` (benchmark): ``v`` = the old minimum x the share still built, so
  ``v`` is always at or below the old minimum (the minimum binds on every
  project unless the share is 1).
- ``provided``: ``v`` = one number per building type and transit class
  (:data:`VOLUNTARY_IDS`), the same for every project.
- ``voluntary`` (variant ``parking_voluntary_floor``): ``v`` varies across
  projects, evenly between average x (1 - spread) and average x (1 + spread)
  (:data:`SPREAD`), so some projects already provide more than the old minimum
  and save nothing."""

EARTH_RADIUS_MILES = 3958.8
"""Mean radius of the Earth, for straight-line (great-circle) distances."""

# Structure types the reform reaches, and which minimum each uses.
_REACHED = np.array([False, True, True, True])
_IS_APARTMENT = np.array([False, False, False, True])

# ACS units-in-structure columns that stand for each permit structure type.
_STOCK_COLUMNS: dict[str, tuple[str, ...]] = {
    "units_1": ("units_1_detached", "units_1_attached"),
    "units_2": ("units_2",),
    "units_3_4": ("units_3_4",),
    "units_5plus": ("units_5_9", "units_10_19", "units_20_plus"),
}


# ---------------------------------------------------------------- transit


def major_stops(stops: pd.DataFrame, min_bus_departures: float) -> pd.DataFrame:
    """Keep the major transit stops.

    Args:
        stops: Stop table with ``lat``, ``lon``, ``mode`` (``rail`` or ``bus``)
            and ``weekday_departures``.
        min_bus_departures: Weekday departures a bus stop needs to count.

    Returns:
        The rail stations and the frequent bus stops.
    """
    mode = stops["mode"].astype(str).str.lower()
    keep = (mode == "rail") | (
        (mode == "bus") & (stops["weekday_departures"] >= min_bus_departures)
    )
    return stops.loc[keep].reset_index(drop=True)


def _miles(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    p1, p2 = np.radians(lat1)[:, None], np.radians(lat2)[None, :]
    dphi = p2 - p1
    dlmb = np.radians(lon2)[None, :] - np.radians(lon1)[:, None]
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _rings(geometry: Mapping[str, Any]) -> list[np.ndarray]:
    """Return the outer rings of a GeoJSON Polygon or MultiPolygon as (lon, lat) arrays."""
    kind, coords = geometry.get("type"), geometry.get("coordinates", [])
    polygons = [coords] if kind == "Polygon" else coords if kind == "MultiPolygon" else []
    return [np.asarray(poly[0], dtype=float) for poly in polygons if poly]


def _inside(ring: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Even-odd (ray casting) test: which points lie inside one ring."""
    x1, y1 = ring[:-1, 0][None, :], ring[:-1, 1][None, :]
    x2, y2 = ring[1:, 0][None, :], ring[1:, 1][None, :]
    px, py = lon[:, None], lat[:, None]
    crosses = (y1 > py) != (y2 > py)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_at = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
    return (crosses & (px < x_at)).sum(axis=1) % 2 == 1


def tracts_with_stop(boundaries: Mapping[str, Any], stops: pd.DataFrame) -> pd.Series:
    """Flag the tracts whose boundary contains at least one of the given stops.

    Args:
        boundaries: GeoJSON feature collection; tract features carry
            ``properties.level == "tract"`` and ``properties.geoid``.
        stops: Stops with ``lat`` and ``lon``.

    Returns:
        Boolean series indexed by tract id (text).
    """
    lon = stops["lon"].to_numpy(dtype=float)
    lat = stops["lat"].to_numpy(dtype=float)
    out = {}
    for feature in boundaries.get("features", []):
        props = feature.get("properties", {})
        if props.get("level") != "tract":
            continue
        hit = False
        for ring in _rings(feature.get("geometry", {})):
            # Only stops inside the ring's bounding box can be inside the ring,
            # so the exact test runs on those few (same answer, far less work).
            box = (
                (lon >= ring[:, 0].min())
                & (lon <= ring[:, 0].max())
                & (lat >= ring[:, 1].min())
                & (lat <= ring[:, 1].max())
            )
            if box.any() and _inside(ring, lon[box], lat[box]).any():
                hit = True
                break
        out[str(props["geoid"])] = hit
    return pd.Series(out, dtype=bool, name="has_major_stop")


def transit_rich_tracts(
    blocks: pd.DataFrame,
    stops: pd.DataFrame,
    ledger: Ledger,
    *,
    boundaries: Mapping[str, Any] | None = None,
    chunk: int = 2_000,
) -> pd.Series:
    """Flag the tracts that are rich in frequent transit.

    Two tests; either one is enough:

    1. **Homes near a stop.** A block is near transit when its centre is within
       the rail radius of a rail station or the bus radius of a frequent bus
       stop. The tract passes when the share of its housing units in such blocks
       is at least the ledger's minimum.
    2. **A stop inside the tract** (only when ``boundaries`` is given). Urban
       tracts are small, so a tract that contains a major stop is mostly within
       walking distance of it. This also keeps the flag working where blocks are
       coarse (as in the synthetic test state).

    Args:
        blocks: Block table with ``tract``, ``housing_units``, ``lat``, ``lon``.
        stops: Stop table (see :func:`major_stops`).
        ledger: The ledger (``TRANSIT_IDS``, central values).
        boundaries: Optional GeoJSON with tract polygons.
        chunk: Blocks handled at a time, to bound memory.

    Speed: blocks and stops are both sorted by latitude. A great-circle
    distance is never shorter than the earth's radius times the latitude gap,
    so a block batch is only measured against the stops inside its latitude
    band (the batch's latitudes, widened by the largest radius). Stops outside
    the band cannot be within reach, so the flags are exactly the same as
    measuring every block against every stop.

    Returns:
        Boolean series indexed by tract id (text), named ``transit_rich``.
    """
    min_dep, rail_r, bus_r, min_share = (ledger[i].central for i in TRANSIT_IDS)
    major = major_stops(stops, min_dep)
    lat = blocks["lat"].to_numpy(dtype=float)
    lon = blocks["lon"].to_numpy(dtype=float)
    near = np.zeros(len(blocks), dtype=bool)
    if len(major):
        radius = np.where(major["mode"].astype(str).str.lower() == "rail", rail_r, bus_r)
        s_lat = major["lat"].to_numpy(dtype=float)
        s_lon = major["lon"].to_numpy(dtype=float)
        order = np.argsort(s_lat, kind="stable")
        s_lat, s_lon, radius = s_lat[order], s_lon[order], radius[order]
        # Latitude band (degrees) that holds every stop within reach; widened a
        # hair so rounding can never drop a stop right at the edge.
        band = np.degrees(float(np.max(radius)) / EARTH_RADIUS_MILES) * (1 + 1e-9) + 1e-12
        by_lat = np.argsort(lat, kind="stable")
        for start in range(0, len(blocks), chunk):
            idx = by_lat[start : start + chunk]
            idx = idx[np.isfinite(lat[idx])]  # a block with no centre is never near
            if not len(idx):
                continue
            lo = np.searchsorted(s_lat, lat[idx].min() - band, side="left")
            hi = np.searchsorted(s_lat, lat[idx].max() + band, side="right")
            if hi > lo:
                d = _miles(lat[idx], lon[idx], s_lat[lo:hi], s_lon[lo:hi])
                near[idx] = (d <= radius[None, lo:hi]).any(axis=1)
    units = blocks["housing_units"].to_numpy(dtype=float)
    frame = pd.DataFrame(
        {"tract": blocks["tract"].astype(str), "units": units, "near": units * near}
    )
    grouped = frame.groupby("tract")[["units", "near"]].sum()
    share = grouped["near"] / grouped["units"].where(grouped["units"] > 0)
    flag = share.fillna(0.0) >= min_share
    if boundaries is not None:
        contains = tracts_with_stop(boundaries, major)
        flag = flag.reindex(flag.index.union(contains.index), fill_value=False)
        flag = flag | contains.reindex(flag.index, fill_value=False)
    return flag.astype(bool).rename("transit_rich")


def transit_weights(tracts: pd.DataFrame, flags: pd.Series, counties: Sequence[str]) -> np.ndarray:
    """Share of each county's existing homes, by structure type, in transit-rich tracts.

    Args:
        tracts: Tract table with ``geoid``, ``county`` and the ACS
            units-in-structure columns.
        flags: Transit-rich flag by tract id.
        counties: County ids in array order.

    Returns:
        Array of shape (counties, 4), each value between 0 and 1. Where a county
        has none of a structure type, all its housing units are used instead.
    """
    t = tracts.assign(
        geoid=tracts["geoid"].astype(str), county=tracts["county"].astype(str)
    ).set_index("geoid")
    rich = flags.reindex(t.index).fillna(False).astype(bool).to_numpy()
    out = np.zeros((len(counties), len(STRUCTURE_TYPES)))
    all_units = t["housing_units"].to_numpy(dtype=float)
    for j, stype in enumerate(STRUCTURE_TYPES):
        stock = t[list(_STOCK_COLUMNS[stype])].sum(axis=1).to_numpy(dtype=float)
        frame = (
            pd.DataFrame(
                {
                    "county": t["county"].to_numpy(),
                    "stock": stock,
                    "rich": stock * rich,
                    "all": all_units,
                    "all_rich": all_units * rich,
                }
            )
            .groupby("county")[["stock", "rich", "all", "all_rich"]]
            .sum()
        )
        frame = frame.reindex(list(counties)).fillna(0.0)
        own = frame["rich"] / frame["stock"].where(frame["stock"] > 0)
        fallback = frame["all_rich"] / frame["all"].where(frame["all"] > 0)
        out[:, j] = own.fillna(fallback).fillna(0.0).to_numpy()
    return out


# ---------------------------------------------------------------- verified minimums


@dataclass(frozen=True)
class MinimumMix:
    """Where verified local parking minimums replace the ledger default, by county.

    Within each county, structure type and transit class, a share of the
    existing homes sits in municipalities whose minimum was read and verified;
    each such share has its own minimum (``levels``). The rest keeps the
    labelled ledger default (``parking.required_spaces_per_unit_*``).

    Attributes:
        levels: The distinct verified minimums, spaces per home, shape (K,).
        tr: Share of the county's homes of the type in transit-rich tracts that
            sit under each verified level, shape (counties, 4, K).
        el: The same for the other tracts.
        verified: Share of the county's homes of the type (both classes) under
            any verified minimum, shape (counties, 4).
        zero: Share under a verified minimum of zero, shape (counties, 4).
        munis: Municipalities with a verified minimum that hold homes here.
        source_ids: Registry ids of the readings used.
        assumption_id: Id of the settings block (``assumptions/cost_reforms.yaml``).
        weight_words: How the shares are weighted, in words (for records).
        bar_counties: ``None`` for every verified minimum (variant
            ``parking_verified_minimums``); else the counties that pass the
            50% resident bar (:func:`bar_minimums`, variant
            ``parking_verified_minimums_bar``): only their non-zero minimums
            are in the mix, and zeros keep the county reach factor.
        words: The run note for the old minimum, when the mix comes from
            elsewhere (the Vermont and Wyoming local readings, variants
            ``vt_parking_town_bylaws`` and ``wy_parking_local``); empty uses
            the words of the two verified-minimum variants.
    """

    levels: np.ndarray = field(repr=False)
    tr: np.ndarray = field(repr=False)
    el: np.ndarray = field(repr=False)
    verified: np.ndarray = field(repr=False)
    zero: np.ndarray = field(repr=False)
    munis: int = 0
    source_ids: tuple[str, ...] = ()
    assumption_id: str = ""
    weight_words: str = ""
    bar_counties: tuple[str, ...] | None = None
    words: str = ""

    @classmethod
    def default_only(cls, n_counties: int, **kw: Any) -> MinimumMix:
        """No verified minimum anywhere: every home keeps the ledger default."""
        z = np.zeros((n_counties, len(STRUCTURE_TYPES), 0))
        zz = np.zeros((n_counties, len(STRUCTURE_TYPES)))
        return cls(np.zeros(0), z, z.copy(), zz, zz.copy(), **kw)


def bar_minimums(minimums: pd.DataFrame | None, counties: Collection[str]) -> pd.DataFrame:
    """Keep the verified non-zero minimums of the counties that pass the 50% bar.

    The documented hook rule (``assumptions/law/hooks.yaml``): a county uses
    its own read value only where its verified rows cover at least
    ``min_coverage`` of its residents. ``counties`` are those counties (the
    engine passes the parking hook's qualifying counties). A minimum of zero
    is blanked (it stays on the default here, because the county reach factor
    already takes it out, as in the benchmark); a row left with no number is
    dropped. Blank is never zero.

    Args:
        minimums: :func:`visionhousing.law.parking_rules.verified_parking_minimums`,
            or ``None``.
        counties: County ids (5 digits) that pass the bar.

    Returns:
        The kept rows (same columns); empty when no county passes.
    """
    if minimums is None or minimums.empty:
        return pd.DataFrame(columns=getattr(minimums, "columns", None))
    keep = {str(c) for c in counties}
    m = minimums[minimums["geoid"].astype(str).str[:5].isin(keep)].copy()
    for c in ("min_apartment", "min_small"):
        vals = pd.to_numeric(m[c], errors="coerce")
        m[c] = vals.where(vals > 0)
    m = m[m["min_apartment"].notna() | m["min_small"].notna()]
    return m.reset_index(drop=True)


def minimum_mix(
    tracts: pd.DataFrame,
    tract_munis: pd.DataFrame | None,
    flags: pd.Series,
    minimums: pd.DataFrame | None,
    counties: Sequence[str],
    **kw: Any,
) -> MinimumMix:
    """Mix verified municipal parking minimums into each county, by type and transit class.

    Weight: a tract's existing homes of the structure type (ACS units in
    structure, the same counts as :func:`transit_weights`) are split among the
    municipalities the tract touches in proportion to their 2020 housing units
    in it (``tract_municipalities``). A municipality's verified minimum covers
    its part; every other part keeps the ledger default. Shares are taken
    within each transit class, so a city's minimum lands on its own
    transit-rich tracts, not spread over the county. Where a county has none
    of a structure type, all its housing units are used instead (as
    :func:`transit_weights` does).

    Apartments (5+ homes) use ``min_apartment``; 2-4 unit buildings use
    ``min_small``; a blank keeps the default. 1-unit buildings are not reached.

    Args:
        tracts: Tract table (``geoid``, ``county``, ``housing_units`` and the ACS
            units-in-structure columns).
        tract_munis: ``tract_municipalities`` (``tract``, ``county``, ``cousub``,
            ``housing_units_2020``), or ``None``.
        flags: Transit-rich flag by tract id.
        minimums: :func:`visionhousing.law.parking_rules.verified_parking_minimums`,
            or ``None``.
        counties: County ids in array order.
        **kw: Passed to :class:`MinimumMix` (``source_ids``, ``assumption_id``,
            ``weight_words``).

    Returns:
        The mix. With no table, or no verified minimum, every share is 0 and
        the reform equals the default-minimum run exactly.
    """
    n = len(counties)
    if tract_munis is None or minimums is None or minimums.empty:
        return MinimumMix.default_only(n, **kw)
    vals = pd.concat([minimums["min_apartment"], minimums["min_small"]]).dropna()
    levels = np.unique(vals.to_numpy(dtype=float))
    if not len(levels):
        return MinimumMix.default_only(n, **kw)
    cidx = {str(c): i for i, c in enumerate(counties)}
    t = tracts.assign(
        geoid=tracts["geoid"].astype(str), county=tracts["county"].astype(str)
    ).set_index("geoid")
    rich = flags.reindex(t.index).fillna(False).astype(bool)
    tm = tract_munis.copy()
    tm["tract"] = tm["tract"].astype(str)
    tm["muni"] = tm["county"].astype(str).str.zfill(5) + tm["cousub"].astype(str).str.zfill(5)
    tm["hu"] = pd.to_numeric(tm["housing_units_2020"], errors="coerce").fillna(0.0)
    total = tm.groupby("tract")["hu"].transform("sum")
    size = tm.groupby("tract")["hu"].transform("size")
    tm["part"] = (tm["hu"] / total).where(total > 0, 1.0 / size)
    tm = tm[tm["tract"].isin(t.index)]
    m = minimums.assign(geoid=minimums["geoid"].astype(str)).set_index("geoid")
    tm["m_apartment"] = tm["muni"].map(m["min_apartment"])
    tm["m_small"] = tm["muni"].map(m["min_small"])
    tm["county_t"] = tm["tract"].map(t["county"])
    tm["rich"] = tm["tract"].map(rich).astype(bool)
    k_of = {float(v): k for k, v in enumerate(levels)}
    shape = (n, len(STRUCTURE_TYPES), len(levels))
    num_tr, num_el = np.zeros(shape), np.zeros(shape)
    den_tr = np.zeros(shape[:2])
    den_el = np.zeros(shape[:2])
    all_units = pd.to_numeric(t["housing_units"], errors="coerce").fillna(0.0)
    for j, stype in enumerate(STRUCTURE_TYPES):
        if not _REACHED[j]:
            continue
        stock = t[list(_STOCK_COLUMNS[stype])].sum(axis=1).astype(float)
        by_county = stock.groupby(t["county"]).sum()
        empty = set(by_county.index[by_county <= 0])
        s = stock.where(~t["county"].isin(empty), all_units)
        for flag, den in ((True, den_tr), (False, den_el)):
            g = s[rich == flag].groupby(t["county"][rich == flag]).sum()
            for c, v in g.items():
                if str(c) in cidx:
                    den[cidx[str(c)], j] = float(v)
        col = "m_apartment" if _IS_APARTMENT[j] else "m_small"
        p = tm[tm[col].notna()]
        homes = p["tract"].map(s).to_numpy(dtype=float) * p["part"].to_numpy(dtype=float)
        for c, flag, lvl, h in zip(p["county_t"], p["rich"], p[col], homes, strict=True):
            i = cidx.get(str(c))
            if i is None or h <= 0:
                continue
            (num_tr if flag else num_el)[i, j, k_of[float(lvl)]] += h
    with np.errstate(divide="ignore", invalid="ignore"):
        tr = np.where(den_tr[..., None] > 0, num_tr / den_tr[..., None], 0.0)
        el = np.where(den_el[..., None] > 0, num_el / den_el[..., None], 0.0)
        den = den_tr + den_el
        verified = np.where(den > 0, (num_tr + num_el).sum(-1) / den, 0.0)
        zero_k = levels <= 0
        zero = np.where(den > 0, (num_tr + num_el)[..., zero_k].sum(-1) / den, 0.0)
    munis = int(tm.loc[tm["m_apartment"].notna() | tm["m_small"].notna(), "muni"].nunique())
    return MinimumMix(
        levels=levels,
        tr=np.clip(tr, 0.0, 1.0),
        el=np.clip(el, 0.0, 1.0),
        verified=verified,
        zero=zero,
        munis=munis,
        **kw,
    )


def _mixed_class(
    lo: np.ndarray,
    hi: np.ndarray,
    req: np.ndarray,
    kept: float,
    levels: np.ndarray,
    wts: np.ndarray,
) -> dict[str, np.ndarray]:
    """One transit class under a mix of default and verified minimums.

    ``lo``, ``hi`` (voluntary spaces) and ``req`` (the default minimum) have
    shape (types, draws); ``wts`` (counties, types, K) the share of homes under
    each verified level. Returns ``before`` (spaces provided before:
    mean of max(v, m)), ``after`` (mean of max(v, kept x m)), ``legal`` (the
    minimum, spaces per home) and ``binds`` (share of homes the minimum left in
    force binds), each (counties, types, draws).
    """
    d = (1.0 - wts.sum(-1))[..., None]
    lv = levels[:, None, None]
    lo_k, hi_k = lo[None], hi[None]

    def mix(default: np.ndarray, per_level: np.ndarray) -> np.ndarray:
        out = d * default[None] + np.einsum("ctk,ktd->ctd", wts, per_level)
        return np.where(_REACHED[None, :, None], out, 0.0)

    legal = mix(req, np.broadcast_to(lv, (len(levels), *req.shape)))
    before = mix(expected_provided(lo, hi, req), expected_provided(lo_k, hi_k, lv))
    after = mix(expected_provided(lo, hi, kept * req), expected_provided(lo_k, hi_k, kept * lv))
    binds = mix(
        np.where(req > 0, share_below(lo, hi, kept * req), 0.0),
        share_below(lo_k, hi_k, kept * lv),
    )
    return {"before": before, "after": after, "legal": legal, "binds": binds}


# ---------------------------------------------------------------- saving


def _share(params: Mapping[str, np.ndarray], pid: str) -> np.ndarray:
    v = np.asarray(params[pid], dtype=float)
    if np.any((v < 0) | (v > 1)):
        raise ValueError(f"{pid} must be a share between 0 and 1")
    return v


def expected_provided(lo: np.ndarray, hi: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Mean of max(v, m) when a project's voluntary spaces ``v`` are spread evenly on [lo, hi].

    ``max(v, m)`` is what a project provides when the legal minimum is ``m``.
    With ``lo == hi`` it is simply ``max(lo, m)``. Arrays broadcast.
    """
    lo, hi, m = (np.asarray(a, dtype=float) for a in (lo, hi, m))
    lo, hi, m = np.broadcast_arrays(lo, hi, m)
    width = hi - lo
    point = np.maximum(lo, m)
    with np.errstate(divide="ignore", invalid="ignore"):
        inside = (m * (m - lo) + (hi**2 - m**2) / 2.0) / np.where(width > 0, width, 1.0)
    spread = np.where(m <= lo, (lo + hi) / 2.0, np.where(m >= hi, m, inside))
    return np.where(width > 0, spread, point)


def share_below(lo: np.ndarray, hi: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Share of projects whose voluntary spaces (even on [lo, hi]) are below ``m``.

    That is the share the minimum ``m`` binds.

    With ``lo == hi`` it is 1 where ``lo < m`` and 0 otherwise. Arrays broadcast.
    """
    lo, hi, m = (np.asarray(a, dtype=float) for a in (lo, hi, m))
    lo, hi, m = np.broadcast_arrays(lo, hi, m)
    width = hi - lo
    with np.errstate(divide="ignore", invalid="ignore"):
        spread = np.clip((m - lo) / np.where(width > 0, width, 1.0), 0.0, 1.0)
    return np.where(width > 0, spread, (lo < m).astype(float))


def _voluntary(
    params: Mapping[str, np.ndarray], req: np.ndarray, response: str
) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    """Voluntary spaces per home: ``((lo, hi) transit-rich, (lo, hi) elsewhere)``.

    Each array has shape (types, draws).
    """
    if response == "share":
        s_tr, s_el = _share(params, STILL_TR), _share(params, STILL_EL)
        v_tr, v_el = req * s_tr[None, :], req * s_el[None, :]
        return (v_tr, v_tr), (v_el, v_el)
    vol = {pid: np.asarray(params[pid], dtype=float) for pid in VOLUNTARY_IDS}
    if any(np.any(a < 0) for a in vol.values()):
        raise ValueError("voluntary spaces per home cannot be negative")
    v_tr = np.where(_IS_APARTMENT[:, None], vol[VOL_APT_TR][None, :], vol[VOL_SMALL_TR][None, :])
    v_el = np.where(_IS_APARTMENT[:, None], vol[VOL_APT_EL][None, :], vol[VOL_SMALL_EL][None, :])
    if response == "provided":
        return (v_tr, v_tr), (v_el, v_el)
    h = np.asarray(params[SPREAD], dtype=float)
    if np.any((h < 0) | (h > 1)):
        raise ValueError(f"{SPREAD} must be a share between 0 and 1")
    h = h[None, :]
    return (v_tr * (1 - h), v_tr * (1 + h)), (v_el * (1 - h), v_el * (1 + h))


def binding_share(
    params: Mapping[str, np.ndarray],
    weights: np.ndarray,
    base: np.ndarray,
    response: str,
    kept: float,
    mix: MinimumMix | None = None,
) -> np.ndarray:
    """Share of baseline homes whose project the minimum binds, by draw.

    A minimum binds a project when it asks for more spaces than the project
    would provide with no minimum (``m > v``). ``kept`` is the share of today's
    minimum left in force (1: today's rule; 0: removed, so nothing binds).
    Weighted by baseline homes in the building types the reform reaches, and
    within a county by its transit-rich share.

    Args:
        params: Ledger draws (as for :func:`parking_saving`).
        weights: Transit-rich share by county and structure type, (counties, 4).
        base: Baseline homes, (counties, 4) or (counties, 4, draws).
        response: See :data:`RESPONSES`.
        kept: Share of today's minimum left in force, 0 to 1.
        mix: Verified local minimums (:func:`minimum_mix`); ``None`` uses the
            ledger default everywhere.

    Returns:
        Shape (draws,), each between 0 and 1 (0 where no home is reached).
    """
    if response not in RESPONSES:
        raise ValueError(f"parking response must be one of {RESPONSES}, got {response!r}")
    req_mf = np.asarray(params[REQ_MF], dtype=float)
    req_att = np.asarray(params[REQ_ATT], dtype=float)
    req = np.where(_IS_APARTMENT[:, None], req_mf[None, :], req_att[None, :])
    req = np.where(_REACHED[:, None], req, 0.0)
    (lo_tr, hi_tr), (lo_el, hi_el) = _voluntary(params, req, response)
    w = np.asarray(weights, dtype=float)[:, :, None]
    if mix is not None:
        b_tr = _mixed_class(lo_tr, hi_tr, req, float(kept), mix.levels, mix.tr)["binds"]
        b_el = _mixed_class(lo_el, hi_el, req, float(kept), mix.levels, mix.el)["binds"]
        bind = w * b_tr + (1 - w) * b_el
    else:
        m = float(kept) * req
        b_tr = np.where(req > 0, share_below(lo_tr, hi_tr, m), 0.0)
        b_el = np.where(req > 0, share_below(lo_el, hi_el, m), 0.0)
        bind = w * b_tr[None] + (1 - w) * b_el[None]
    b = np.asarray(base, dtype=float)
    if b.ndim == 2:
        b = b[:, :, None]
    homes = np.broadcast_to(b * _REACHED[None, :, None], bind.shape)
    total = homes.sum(axis=(0, 1))
    num = (homes * bind).sum(axis=(0, 1))
    return np.where(total > 0, num / np.where(total > 0, total, 1.0), 0.0)


def parking_saving(
    params: Mapping[str, np.ndarray],
    weights: np.ndarray,
    response: str = "share",
    min_share_kept: float = 0.0,
    mix: MinimumMix | None = None,
) -> dict[str, np.ndarray]:
    """Spaces avoided, cost per space and saving per unit, by county, type and draw.

    Spaces provided before the reform are the old minimum (it binds); after,
    the spaces still built. In a matched prototype (same building type, same
    transit access) the spaces avoided are before minus after:

    - ``response="share"`` (benchmark): after = minimum x share still built.
    - ``response="provided"``: after = min(minimum, spaces builders provide
      with no minimum), from :data:`VOLUNTARY_IDS`. Where builders would
      provide more than the minimum anyway, nothing is avoided.
    - ``response="voluntary"``: spaces avoided = mean over projects of
      max(v, m_before) - max(v, m_after), with ``v`` spread evenly around the
      :data:`VOLUNTARY_IDS` average (:func:`expected_provided`).

    Args:
        params: Ledger id to draws (shape (draws,)); needs ``SAVING_IDS`` (and
            :data:`VOLUNTARY_IDS` for the ``provided`` response).
        weights: Transit-rich share by county and structure type, shape (counties, 4).
        response: ``share`` or ``provided`` (see :data:`RESPONSES`).
        min_share_kept: A minimum that stays in force, as a share of the old
            minimum (0: removed, the benchmark; 0.5: halved; 1: unchanged). The
            spaces still built are never below it. Used by the policy dial
            ``parking_minimum`` (``engine.comparisons``).
        mix: Verified local minimums (:func:`minimum_mix`, variant
            ``parking_verified_minimums``). Each share of a county's homes
            uses its own old minimum ``m``; the rest keeps the ledger default.
            Spaces avoided = mean of max(v, m) - max(v, kept x m) over the
            shares, with ``v`` set as for ``response`` from the **default**
            minimum (a verified minimum changes the law, not what projects
            want). So a verified minimum at or below ``v`` avoids nothing, and
            a verified zero avoids nothing. ``required`` is then the legal
            minimum mix, ``provided_before`` the spaces provided before
            (mean of max(v, m)), and ``avoided_share`` is spaces avoided / the
            default minimum (the parking land a default site would free).
            ``None``: the ledger default everywhere (the benchmark).

    Returns:
        ``required``, ``still_built``, ``avoided`` (spaces per unit),
        ``avoided_share`` (avoided / required), ``cost_per_space`` ($) and
        ``saving`` ($ per unit), each of shape (counties, 4, draws). Types the
        reform does not reach are zero.

    Raises:
        ValueError: A share is outside 0-1, or a tract class's mix of structured
            and underground spaces is above 1.
    """
    floor = float(min_share_kept)
    if not 0.0 <= floor <= 1.0:
        raise ValueError("the minimum kept must be a share between 0 and 1")
    still_tr, still_el = _share(params, STILL_TR), _share(params, STILL_EL)
    still_tr, still_el = np.maximum(still_tr, floor), np.maximum(still_el, floor)
    shares = {pid: _share(params, pid) for pid in (MIX_S_TR, MIX_U_TR, MIX_S_EL, MIX_U_EL)}
    surf_att = _share(params, MIX_SURF_ATT)
    if np.any(shares[MIX_S_TR] + shares[MIX_U_TR] > 1) or np.any(
        shares[MIX_S_EL] + shares[MIX_U_EL] > 1
    ):
        raise ValueError("structured plus underground shares cannot exceed 1")
    surface, structured, underground = (
        np.asarray(params[p], dtype=float) for p in (SURFACE, STRUCTURED, UNDERGROUND)
    )

    def blend(s: np.ndarray, u: np.ndarray) -> np.ndarray:
        return surface * (1 - s - u) + structured * s + underground * u

    attached = surface * surf_att + structured * (1 - surf_att)
    req_mf = np.asarray(params[REQ_MF], dtype=float)
    req_att = np.asarray(params[REQ_ATT], dtype=float)
    req = np.where(_IS_APARTMENT[:, None], req_mf[None, :], req_att[None, :])
    req = np.where(_REACHED[:, None], req, 0.0)  # (types, draws)
    cost_tr = np.where(_IS_APARTMENT[:, None], blend(shares[MIX_S_TR], shares[MIX_U_TR]), attached)
    cost_el = np.where(_IS_APARTMENT[:, None], blend(shares[MIX_S_EL], shares[MIX_U_EL]), attached)
    cost_tr = np.where(_REACHED[:, None], cost_tr, 0.0)
    cost_el = np.where(_REACHED[:, None], cost_el, 0.0)
    w = np.asarray(weights, dtype=float)[:, :, None]
    if response not in RESPONSES:
        raise ValueError(f"parking response must be one of {RESPONSES}, got {response!r}")
    if response == "provided":
        (vol_tr, _), (vol_el, _) = _voluntary(params, req, response)
        avoided_tr = np.maximum(req - np.maximum(vol_tr, floor * req), 0.0)
        avoided_el = np.maximum(req - np.maximum(vol_el, floor * req), 0.0)
    elif response == "voluntary":
        # max(v, m_before) - max(v, m_after), averaged over projects (v spread evenly).
        (lo_tr, hi_tr), (lo_el, hi_el) = _voluntary(params, req, response)
        avoided_tr = np.maximum(
            expected_provided(lo_tr, hi_tr, req) - expected_provided(lo_tr, hi_tr, floor * req),
            0.0,
        )
        avoided_el = np.maximum(
            expected_provided(lo_el, hi_el, req) - expected_provided(lo_el, hi_el, floor * req),
            0.0,
        )
        avoided_tr = np.where(req > 0, avoided_tr, 0.0)
        avoided_el = np.where(req > 0, avoided_el, 0.0)
    else:
        avoided_tr = req * (1 - still_tr)[None, :]
        avoided_el = req * (1 - still_el)[None, :]
    if mix is not None:
        return _mixed_saving(params, w, req, response, floor, mix, cost_tr, cost_el)
    saving = w * (avoided_tr * cost_tr)[None] + (1 - w) * (avoided_el * cost_el)[None]
    avoided = w * avoided_tr[None] + (1 - w) * avoided_el[None]
    required = np.broadcast_to(req[None], avoided.shape)
    with np.errstate(divide="ignore", invalid="ignore"):
        avoided_share = np.where(required > 0, avoided / np.where(required > 0, required, 1), 0.0)
    cost_per_space = np.where(avoided > 0, saving / np.where(avoided > 0, avoided, 1), 0.0)
    return {
        "required": np.array(required),
        "provided_before": np.array(required),
        "still_built": np.array(required - avoided),
        "avoided": avoided,
        "avoided_share": avoided_share,
        "cost_per_space": cost_per_space,
        "saving": saving,
    }


def _mixed_saving(
    params: Mapping[str, np.ndarray],
    w: np.ndarray,
    req: np.ndarray,
    response: str,
    floor: float,
    mix: MinimumMix,
    cost_tr: np.ndarray,
    cost_el: np.ndarray,
) -> dict[str, np.ndarray]:
    """:func:`parking_saving` with verified local minimums mixed in (see its ``mix``)."""
    (lo_tr, hi_tr), (lo_el, hi_el) = _voluntary(params, req, response)
    c_tr = _mixed_class(lo_tr, hi_tr, req, floor, mix.levels, mix.tr)
    c_el = _mixed_class(lo_el, hi_el, req, floor, mix.levels, mix.el)
    av_tr = np.maximum(c_tr["before"] - c_tr["after"], 0.0)
    av_el = np.maximum(c_el["before"] - c_el["after"], 0.0)
    saving = w * av_tr * cost_tr[None] + (1 - w) * av_el * cost_el[None]
    avoided = w * av_tr + (1 - w) * av_el
    before = w * c_tr["before"] + (1 - w) * c_el["before"]
    legal = w * c_tr["legal"] + (1 - w) * c_el["legal"]
    ref = np.broadcast_to(req[None], avoided.shape)
    with np.errstate(divide="ignore", invalid="ignore"):
        avoided_share = np.where(ref > 0, avoided / np.where(ref > 0, ref, 1), 0.0)
    cost_per_space = np.where(avoided > 0, saving / np.where(avoided > 0, avoided, 1), 0.0)
    return {
        "required": legal,
        "provided_before": before,
        "still_built": before - avoided,
        "avoided": avoided,
        "avoided_share": avoided_share,
        "cost_per_space": cost_per_space,
        "saving": saving,
    }


def _parking_core(
    params: Mapping[str, np.ndarray],
    base: np.ndarray,
    market: Market,
    weights: np.ndarray,
    response: str = "share",
    min_share_kept: float = 0.0,
    mix: MinimumMix | None = None,
) -> dict[str, np.ndarray]:
    s = parking_saving(params, weights, response, min_share_kept, mix)
    value = value_per_unit(market, params)
    site_gain = np.asarray(params[SITE_YIELD], dtype=float)
    if np.any(site_gain < 0):
        raise ValueError("the site-yield gain cannot be negative")
    cost_channel = channel_b(
        base,
        np.asarray(params[ELASTICITY])[None, None, :],
        s["saving"],
        value,
        np.asarray(params[K])[None, None, :],
    )
    site = base * site_gain[None, None, :] * s["avoided_share"]
    return {
        **s,
        "value": value,
        "channel_b": cost_channel,
        "site_yield": site,
        "added": cost_channel + site,
    }


# ---------------------------------------------------------------- the reform


def parking_reform(
    baseline: pd.DataFrame,
    tracts: pd.DataFrame,
    blocks: pd.DataFrame,
    stops: pd.DataFrame,
    ledger: Ledger,
    *,
    n_draws: int = DEFAULT_DRAWS,
    seed: int = 0,
    overrides: Mapping[str, Any] | None = None,
    baseline_scale: np.ndarray | None = None,
    transit_rich: pd.Series | None = None,
    boundaries: Mapping[str, Any] | None = None,
    reach: np.ndarray | None = None,
    response: str = "share",
    min_share_kept: float = 0.0,
    minimums: MinimumMix | None = None,
) -> ChannelBResult:
    """Benchmark reform 4: added permitted units in 2027-2031 from removing parking minimums.

    Args:
        baseline: Five-year baseline units by county (index) and structure type
            (columns), see ``scenarios.fees.baseline_by_type``.
        tracts: Tract table (``geoid``, ``county``, units-in-structure and ACS
            bracket columns).
        blocks: Block table (``tract``, ``housing_units``, ``lat``, ``lon``).
        stops: Transit stop table (``lat``, ``lon``, ``mode``, ``weekday_departures``).
        ledger: The Assumption Ledger.
        n_draws: Monte Carlo draws.
        seed: Run seed; the same seed gives the same result.
        overrides: Ledger overrides for this run, for example
            ``{"parking.share_still_built_elsewhere": 1.0}``.
        baseline_scale: Optional baseline multipliers by county and draw, shape
            (counties, draws).
        transit_rich: Optional tract flags to use instead of computing them.
        boundaries: Optional GeoJSON with tract polygons, so a tract that
            contains a major stop also counts as transit-rich.
        reach: Optional share of each county's baseline (in ``baseline``'s
            order) that a parking minimum reaches, from read local law. The
            county's baseline is scaled by it before anything else, so every
            part of the estimate scales with it. ``None`` means 1 everywhere.
        response: How the spaces still built are set (:data:`RESPONSES`):
            ``share`` (benchmark) or ``provided`` (variant
            ``parking_provided_spaces``).
        min_share_kept: Share of the old minimum that stays in force (0, the
            benchmark, removes it; see :func:`parking_saving`).
        minimums: Verified local minimums (:func:`minimum_mix`; variant
            ``parking_verified_minimums``). ``None`` (the benchmark) uses the
            ledger default everywhere. Callers pass ``reach=None`` with it:
            verified zero minimums are already in the mix. With
            ``bar_counties`` set (variant ``parking_verified_minimums_bar``)
            the mix holds no zero, and callers keep ``reach`` as in the
            benchmark.

    Returns:
        The result. ``parts`` holds the cost channel and the site-yield term;
        ``records`` the matched-prototype spaces before and after, and the
        saving per home if every space avoided were surface or structured.
    """
    ledger = apply_overrides(ledger, overrides)
    counties = tuple(str(c) for c in baseline.index)
    base = baseline_array(baseline, counties)
    if reach is not None:
        base = base * np.asarray(reach, dtype=float)[:, None]
    market = market_from_tracts(tracts, counties)
    flags = (
        transit_rich
        if transit_rich is not None
        else transit_rich_tracts(blocks, stops, ledger, boundaries=boundaries)
    )
    weights = transit_weights(tracts, flags, counties)
    if response not in RESPONSES:
        raise ValueError(f"parking response must be one of {RESPONSES}, got {response!r}")
    model_ids = (
        *PARKING_IDS,
        *(VOLUNTARY_IDS if response in ("provided", "voluntary") else ()),
        *((SPREAD,) if response == "voluntary" else ()),
    )
    xids = tuple(sorted({*CROSSCHECK_IDS, *model_ids}))
    params = _draws(ledger, xids, n_draws, seed)
    based = _scaled(base, baseline_scale, n_draws)
    mix = minimums
    core = _parking_core(params, based, market, weights, response, min_share_kept, mix)
    ids = (*model_ids, *TRANSIT_IDS)
    expression = (
        "baseline units x e x (spaces avoided x cost per space / value per new unit) x k "
        "+ baseline units x site-yield gain x share of spaces avoided"
    )
    what = "added units from parking reform"
    counties_rec, state, by_type = range_records(
        core["added"], counties, what=what, expression=expression, ledger=ledger, ids=ids
    )
    reached = core["saving"] * np.asarray(params[K])[None, None, :]
    check = proforma_crosscheck(based, reached, market, params)
    cross, cross_share = crosscheck_records(
        check, based, counties, what=what, ledger=ledger, ids=(*xids, *TRANSIT_IDS)
    )

    central = _central(ledger, model_ids)
    c_core = _parking_core(
        central, base[:, :, None], market, weights, response, min_share_kept, mix
    )
    steps = _parking_steps(ledger, base, c_core, flags, mixed=mix is not None)
    swing = swing_table(
        lambda p: float(
            _parking_core(p, base[:, :, None], market, weights, response, min_share_kept, mix)[
                "added"
            ].sum()
        ),
        ledger,
        model_ids,
    )
    records = _matched_records(ledger, central, base, c_core, response)
    records.update(
        binding_records(
            params, weights, based, market, response, min_share_kept, ledger, model_ids, mix
        )
    )
    if mix is not None:
        records.update(minimum_records(mix, based, core, model_ids, ledger))
    draft = sorted(i for i in ids if ledger[i].status == "draft")
    notes = (
        "1-unit buildings are not reached: the permit data cannot separate townhouses "
        "from detached homes, so the estimate leaves townhouse parking savings out.",
        "Transit-rich tracts are flagged from the stop table in the data pack, straight-line "
        "distance (1/2 mile of rail, 1/4 mile of frequent bus).",
        *(
            (
                "Response: spaces still built = what builders would provide with no minimum "
                "(ledger parking.voluntary_spaces_*), never more than the old minimum.",
            )
            if response == "provided"
            else ()
        ),
        *(
            (
                "Response: spaces avoided = max(v, old minimum) - max(v, minimum left), averaged "
                "over projects whose voluntary spaces v spread evenly around the "
                f"parking.voluntary_spaces_* average ({SPREAD}); projects that would provide "
                "more than the old minimum save nothing.",
            )
            if response == "voluntary"
            else ()
        ),
        *(
            (
                "The old minimum is the ledger default (1.5 spaces per apartment, 2.0 per 2-4 "
                "unit home), labelled as a default; verified local rules enter only where they "
                "set the minimum to zero (the county reach factor).",
            )
            if mix is None
            else (mix.words,)
            if mix.words
            else (
                "The old minimum is the verified local minimum above zero only in the "
                "counties whose verified rows cover at least half of their residents (the "
                "documented hook rule, assumptions/law/hooks.yaml min_coverage; "
                f"{', '.join(mix.bar_counties) or 'none today'}; {mix.munis} municipalities; "
                "records parking_verified_share and parking_default_share give the split of "
                "baseline homes), and the ledger default (labelled as a default) everywhere "
                "else. Zero minimums enter only through the county reach factor, as in the "
                "benchmark, so nothing is counted twice.",
            )
            if mix.bar_counties is not None
            else (
                "The old minimum is the verified local minimum wherever a reading is verified "
                f"and in force ({mix.munis} municipalities; records parking_verified_share and "
                "parking_default_share give the split of baseline homes), and the ledger default "
                "(labelled as a default) everywhere else. Spaces avoided = max(v, m) - max(v, "
                "m left), with v from the default minimum, so a verified minimum at or below v "
                "(or zero) avoids nothing. The county reach factor is not applied: verified "
                "zeros are already in the mix.",
            )
        ),
        "Channel B and the pro-forma cross-check use the same saving and the same k; "
        "they are reported side by side, never added.",
        "Draft inputs (team judgement, wide ranges): " + ", ".join(draft) + ".",
    )
    return ChannelBResult(
        reform="parking",
        view="benchmark",
        counties=counties_rec,
        state=state,
        by_type=by_type,
        crosscheck=cross,
        crosscheck_share=cross_share,
        steps=steps,
        swing=swing,
        county_ids=counties,
        added=core["added"],
        parts={"channel_b": core["channel_b"], "site_yield": core["site_yield"]},
        notes=notes,
        per_unit={
            "base": np.broadcast_to(based, core["added"].shape),
            "saving": core["saving"],
            "avoided": core["avoided"],
            "required": core["required"],
        },
        records=records,
    )


def _matched_records(
    ledger: Ledger,
    central: Mapping[str, np.ndarray],
    base: np.ndarray,
    c: Mapping[str, np.ndarray],
    response: str,
) -> dict[str, rec.Record]:
    """Spaces before and after in matched prototypes, and the surface / structured bounds.

    At central values, for an apartment building (5+ homes) and a 2-4 unit
    building, near frequent transit and elsewhere: spaces provided per home
    before the reform (the old minimum, or more where builders would provide
    more anyway) and after it. Then the baseline-weighted saving per home if
    every space avoided were a surface space or a structured space.
    """
    req = {"apartment": float(central[REQ_MF][0]), "small": float(central[REQ_ATT][0])}
    out: dict[str, rec.Record] = {}
    for kind, words in (
        ("apartment", "an apartment building (5+ homes)"),
        ("small", "a 2-4 unit building"),
    ):
        for where, label in (("transit_rich", "near frequent transit"), ("elsewhere", "elsewhere")):
            r = req[kind]
            if response in ("provided", "voluntary"):
                vid = {
                    ("apartment", "transit_rich"): VOL_APT_TR,
                    ("apartment", "elsewhere"): VOL_APT_EL,
                    ("small", "transit_rich"): VOL_SMALL_TR,
                    ("small", "elsewhere"): VOL_SMALL_EL,
                }[(kind, where)]
                vol = float(central[vid][0])
                before, after = max(r, vol), min(r, vol)
                ids = (REQ_MF if kind == "apartment" else REQ_ATT, vid)
                how = f"after = min(minimum, provided with no minimum {vol:g})"
                if response == "voluntary":
                    h = float(central[SPREAD][0])
                    lo, hi = vol * (1 - h), vol * (1 + h)
                    before = float(expected_provided(lo, hi, r))
                    after = before - float(
                        expected_provided(lo, hi, r) - expected_provided(lo, hi, 0.0)
                    )
                    ids = (*ids, SPREAD)
                    how = (
                        f"after = mean over projects of v, v even on {lo:g}-{hi:g} "
                        "(no minimum left)"
                    )
            else:
                sid = STILL_TR if where == "transit_rich" else STILL_EL
                share = float(central[sid][0])
                before, after = r, r * share
                ids = (REQ_MF if kind == "apartment" else REQ_ATT, sid)
                how = f"after = minimum x share still built {share:g}"
            key = f"provided_{kind}_{where}"
            out[f"{key}_before"] = rec.quantity(
                f"spaces per home provided before the reform, {words} {label}",
                before,
                "spaces/unit",
                formula=(
                    f"before = the old minimum {r:g} spaces/unit (it binds) = {before:g}"
                    if response != "voluntary"
                    else f"before = mean over projects of max(v, old minimum {r:g}) = "
                    f"{rec.format_number(before)}"
                ),
                assumption_ids=ids,
            )
            out[f"{key}_after"] = rec.quantity(
                f"spaces per home provided after the reform, {words} {label}",
                after,
                "spaces/unit",
                formula=f"{how} = {rec.format_number(after)} spaces/unit",
                assumption_ids=ids,
            )
    reached = base * _REACHED[None, :]
    weight = float(reached.sum())
    avoided = float((reached[:, :, None] * c["avoided"]).sum() / weight) if weight > 0 else 0.0
    for kind, pid in (("surface", SURFACE), ("structured", STRUCTURED)):
        cost = float(central[pid][0])
        out[f"saving_all_{kind}"] = rec.quantity(
            f"saving per home if every space avoided were a {kind} space (central values)",
            avoided * cost,
            "$/unit",
            formula=(
                f"spaces avoided per home (baseline-weighted) {rec.format_number(avoided)} x "
                f"cost per {kind} space {rec.format_number(cost, '$/space')} = "
                f"{rec.format_number(avoided * cost, '$/unit')}"
            ),
            assumption_ids=(REQ_MF, REQ_ATT, STILL_TR, STILL_EL, pid),
            source_ids=ledger[pid].source_ids,
        )
    return out


def _parking_steps(
    ledger: Ledger,
    base: np.ndarray,
    c: dict[str, np.ndarray],
    flags: pd.Series,
    *,
    mixed: bool = False,
) -> tuple[Step, ...]:
    reached = base * _REACHED[None, :]
    weight = float(reached.sum())

    def avg(arr: np.ndarray) -> float:
        return float((reached[:, :, None] * arr).sum() / weight) if weight > 0 else 0.0

    n_rich, n_all = int(flags.sum()), len(flags)
    transit = rec.quantity(
        "transit-rich tracts",
        n_rich,
        "tracts",
        formula=(
            f"transit-rich tracts = {n_rich} of {n_all} (at least half of the tract's homes "
            "within 1/2 mile of rail or 1/4 mile of a bus stop with 64+ weekday departures, "
            "or a major stop inside the tract)"
        ),
        assumption_ids=TRANSIT_IDS,
        source_ids=("ntm_stops", "tiger_2020_tabblock"),
        tier=0,
    )
    required = rec.quantity(
        "spaces required per unit before the reform (baseline-weighted)",
        avg(c["required"]),
        "spaces/unit",
        formula=(
            "spaces required per unit, baseline-weighted over counties and 2-, 3-4- and "
            f"5+-unit buildings = {rec.format_number(avg(c['required']))} spaces/unit"
        ),
        assumption_ids=(REQ_MF, REQ_ATT),
    )
    if mixed:
        required = rec.quantity(
            "spaces provided per unit before the reform (baseline-weighted)",
            avg(c["provided_before"]),
            "spaces/unit",
            formula=(
                "spaces provided before = mean of max(v, old minimum), the old minimum verified "
                "where read and the ledger default elsewhere, baseline-weighted over counties "
                f"and 2-, 3-4- and 5+-unit buildings = "
                f"{rec.format_number(avg(c['provided_before']))} spaces/unit (legal minimum "
                f"{rec.format_number(avg(c['required']))} spaces/unit)"
            ),
            assumption_ids=(REQ_MF, REQ_ATT, STILL_TR, STILL_EL),
        )
    still = rec.quantity(
        "spaces still built per unit after the reform (baseline-weighted)",
        avg(c["still_built"]),
        "spaces/unit",
        formula=(
            "spaces still built per unit = required x share still built (transit-rich "
            f"{ledger[STILL_TR].central:g}, elsewhere {ledger[STILL_EL].central:g}), weighted "
            f"by the transit-rich share of each county's homes = "
            f"{rec.format_number(avg(c['still_built']))} spaces/unit"
        ),
        assumption_ids=(REQ_MF, REQ_ATT, STILL_TR, STILL_EL),
    )
    avoided = rec.sub(required, still, name="spaces avoided per unit")
    cost = rec.quantity(
        "cost per space avoided (baseline-weighted mix)",
        avg(c["saving"]) / avoided.point() if avoided.point() > 0 else 0.0,
        "$/space",
        formula=(
            "cost per space avoided = average saving / average spaces avoided, from the "
            "surface, structured and underground mix of each tract class"
        ),
        assumption_ids=(
            SURFACE,
            STRUCTURED,
            UNDERGROUND,
            MIX_S_TR,
            MIX_U_TR,
            MIX_S_EL,
            MIX_U_EL,
            MIX_SURF_ATT,
        ),
        source_ids=tuple(
            sorted({s for p in (SURFACE, STRUCTURED, UNDERGROUND) for s in ledger[p].source_ids})
        ),
    )
    saving = rec.mul(avoided, cost, name="parking saving per unit", unit="$/unit")
    value = c["value"]
    ratio = c["saving"] / np.where(value > 0, value, np.inf)
    weighted = avg(ratio)
    site_total = float(c["site_yield"].sum())
    site = rec.quantity(
        "added units from site yield, central values",
        site_total,
        "units",
        formula=(
            f"baseline units x site-yield gain ({ledger[SITE_YIELD].central:g}) x share of "
            f"spaces avoided, summed = {rec.format_number(site_total, 'units')}"
        ),
        assumption_ids=(SITE_YIELD, STILL_TR, STILL_EL),
    )
    steps = funnel_steps(
        baseline_total=weight,
        saving=saving,
        weighted_ratio=weighted,
        elasticity=rec.from_parameter(ELASTICITY, ledger[ELASTICITY]),
        passthrough=rec.from_parameter(K, ledger[K]),
        added_name="added units from the parking cost saving, central values",
        extra=(
            Step("transit", transit),
            Step("required", required),
            Step("still_built", still),
            Step("avoided", avoided),
            Step("cost_per_space", cost),
        ),
    )
    channel = steps[-1].record
    total = rec.add([channel, site], name="added units from parking reform, central values")
    return (*steps, Step("site_yield", site), Step("total", total))


def _level_key(kept: float) -> str:
    return f"{round(100 * float(kept)):03d}"


def binding_records(
    params: Mapping[str, np.ndarray],
    weights: np.ndarray,
    based: np.ndarray,
    market: Market,
    response: str,
    min_share_kept: float,
    ledger: Ledger,
    ids: Sequence[str],
    mix: MinimumMix | None = None,
) -> dict[str, rec.Record]:
    """The binding share of the minimum and the added homes, across partial cuts.

    For each level of ``binding_curve.kept_shares`` (``assumptions/cost_reforms.yaml``;
    the share of today's minimum left in force), in every draw:

    - ``binding_share_kept_<pct>``: share of baseline homes whose project the
      minimum left in force still binds (:func:`binding_share`);
    - ``added_kept_<pct>``: added homes if the minimum is cut to that level
      (the same draws, the same seed).

    Also ``binding_share_before`` (today's minimum) and ``binding_share_after``
    (the minimum this run leaves in force). Where the old minimum binds no
    project, no cut can save a space, so the added homes stay flat; that is how
    the curve explains a flat housing response.
    """
    cfg = load_cost_settings()["binding_curve"]
    levels = sorted({float(x) for x in cfg["kept_shares"]} | {1.0, float(min_share_kept)})
    rule = str(cfg["id"])
    r_ids = tuple(sorted(set(ids)))
    sources = tuple(sorted({s for i in r_ids for s in ledger[i].source_ids}))
    common = {"assumption_ids": r_ids, "source_ids": sources, "tier": 0}
    v_words = {
        "share": "v = old minimum x share still built (parking.share_still_built_*)",
        "provided": "v = spaces builders provide with no minimum (parking.voluntary_spaces_*)",
        "voluntary": (
            f"v spread evenly over projects around parking.voluntary_spaces_* (+/- {SPREAD})"
        ),
    }[response]
    out: dict[str, rec.Record] = {}
    for kept in levels:
        share = binding_share(params, weights, based, response, kept, mix)
        key = _level_key(kept)
        words = f"{kept:.0%} of today's minimum left in force"
        out[f"binding_share_kept_{key}"] = rec.from_draws(
            f"share of baseline homes the parking minimum binds, {words}",
            share,
            "share",
            expression=(
                f"baseline-weighted share of projects with minimum left (m = {kept:g} x old "
                f"minimum) > v, {v_words}; levels from {rule}"
            ),
            **common,
        )
        added = _parking_core(params, based, market, weights, response, kept, mix)["added"]
        out[f"added_kept_{key}"] = rec.from_draws(
            f"added units from parking reform 2027-2031, {words}, state",
            added.sum(axis=(0, 1)),
            "units",
            expression=(
                "baseline units x e x (spaces avoided x cost per space / value per new unit) x k "
                "+ baseline units x site-yield gain x share of spaces avoided, with spaces "
                f"avoided = max(v, old minimum) - max(v, {kept:g} x old minimum); {v_words}"
            ),
            **common,
        )
    out["binding_share_before"] = out[f"binding_share_kept_{_level_key(1.0)}"].model_copy(
        update={"name": "share of baseline homes the old parking minimum binds (before the reform)"}
    )
    out["binding_share_after"] = out[f"binding_share_kept_{_level_key(min_share_kept)}"].model_copy(
        update={
            "name": "share of baseline homes the parking minimum left in force binds "
            "(after the reform)"
        }
    )
    return out


def minimum_records(
    mix: MinimumMix,
    based: np.ndarray,
    core: Mapping[str, np.ndarray],
    ids: Sequence[str],
    ledger: Ledger,
) -> dict[str, rec.Record]:
    """Coverage of the verified minimums and the old minimum they give, in every draw.

    - ``parking_verified_share``: share of baseline homes (2-, 3-4- and
      5+-unit buildings) under a verified local minimum;
    - ``parking_default_share``: share still on the ledger default (1 - the above);
    - ``parking_verified_zero_share``: share under a verified minimum of zero;
    - ``parking_legal_minimum``: the old legal minimum per home, baseline-weighted
      (verified where read, the default elsewhere);
    - ``parking_provided_before``: spaces per home provided before the reform,
      mean of max(v, old minimum).
    """
    b = np.asarray(based, dtype=float)
    if b.ndim == 2:
        b = b[:, :, None]
    homes = b * _REACHED[None, :, None]
    total = homes.sum(axis=(0, 1))
    safe = np.where(total > 0, total, 1.0)

    def share(arr: np.ndarray) -> np.ndarray:
        a = np.broadcast_to(np.asarray(arr, dtype=float)[..., None], homes.shape)
        return np.where(total > 0, (homes * a).sum(axis=(0, 1)) / safe, 0.0)

    def mean(arr: np.ndarray) -> np.ndarray:
        return np.where(total > 0, (homes * arr).sum(axis=(0, 1)) / safe, 0.0)

    law_ids: tuple[str, ...] = ()  # ledger ids only; the settings block is named in words
    sources = tuple(sorted(set(mix.source_ids)))
    weight = mix.weight_words or "existing homes of the building type"
    if mix.assumption_id:
        weight += f"; settings {mix.assumption_id}"
    verified = share(mix.verified)
    out = {
        "parking_verified_share": rec.from_draws(
            "share of baseline homes under a verified local parking minimum "
            f"({mix.munis} municipalities)",
            verified,
            "share",
            expression=(
                "sum over counties and 2-, 3-4- and 5+-unit buildings of baseline homes x share "
                f"of the county's homes of that type under a verified minimum ({weight}) / "
                "baseline homes reached"
            ),
            assumption_ids=law_ids,
            source_ids=sources,
            tier=0,
        ),
        "parking_default_share": rec.from_draws(
            "share of baseline homes still on the default parking minimum (no verified reading)",
            1.0 - verified,
            "share",
            expression="1 - share of baseline homes under a verified local parking minimum",
            assumption_ids=(*law_ids, REQ_MF, REQ_ATT),
            source_ids=sources,
            tier=0,
        ),
        "parking_verified_zero_share": rec.from_draws(
            "share of baseline homes under a verified parking minimum of zero",
            share(mix.zero),
            "share",
            expression=(
                "baseline-weighted share of homes in municipalities whose verified, in-force "
                f"minimum is zero ({weight})"
            ),
            assumption_ids=law_ids,
            source_ids=sources,
            tier=0,
        ),
    }
    r_ids = tuple(sorted({*ids, *law_ids}))
    r_src = tuple(sorted({*sources, *(s for i in ids for s in ledger[i].source_ids)}))
    out["parking_legal_minimum"] = rec.from_draws(
        "old legal parking minimum per home, baseline-weighted (verified where read, default "
        "elsewhere)",
        mean(core["required"]),
        "spaces/unit",
        expression=(
            "baseline-weighted mean over counties, 2-, 3-4- and 5+-unit buildings and transit "
            "classes of the old minimum m (verified local minimum where read, else "
            "parking.required_spaces_per_unit_*)"
        ),
        assumption_ids=r_ids,
        source_ids=r_src,
        tier=0,
    )
    out["parking_provided_before"] = rec.from_draws(
        "spaces per home provided before the reform, baseline-weighted",
        mean(core["provided_before"]),
        "spaces/unit",
        expression="baseline-weighted mean of max(v, old minimum m) over the same homes",
        assumption_ids=r_ids,
        source_ids=r_src,
        tier=0,
    )
    return out
