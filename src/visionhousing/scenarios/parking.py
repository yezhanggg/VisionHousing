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
2. **Spaces still built** = required x the share still built (decision D-08:
   about 60% in transit-rich tracts, 85-100% elsewhere). Both shares are ledger
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
the D-07 distance of a major transit stop: any rail station within 1/2 mile, or a
bus stop with at least 64 weekday departures within 1/4 mile, straight line. A
tract whose boundary contains a major stop also counts (urban tracts are small,
and the test state's blocks are too coarse for the distance test alone). For now
the stops come from the data pack's stop table (Testland in the tests).

**Zero reduction gives zero.** If the share still built is 1 in both tract
classes, spaces avoided are 0, the saving is 0 and the site-yield share is 0, so
both parts are exactly zero.

All functions are pure: data frames and the ledger go in, records come out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
    market_from_tracts,
    proforma_crosscheck,
    range_records,
    swing_table,
    value_per_unit,
)

__all__ = [
    "PARKING_IDS",
    "TRANSIT_IDS",
    "major_stops",
    "parking_reform",
    "parking_saving",
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
"""Ledger ids behind the transit-rich tract flag (fixed values from decision D-07)."""

PARKING_IDS: tuple[str, ...] = (*SAVING_IDS, ELASTICITY, K, SITE_YIELD, *VALUE_IDS)
"""Ledger ids sampled for the parking estimate."""

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
    """Keep the major transit stops (decision D-07).

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
        hit = np.zeros(len(stops), dtype=bool)
        for ring in _rings(feature.get("geometry", {})):
            hit |= _inside(ring, lon, lat)
        out[str(props["geoid"])] = bool(hit.any())
    return pd.Series(out, dtype=bool, name="has_major_stop")


def transit_rich_tracts(
    blocks: pd.DataFrame,
    stops: pd.DataFrame,
    ledger: Ledger,
    *,
    boundaries: Mapping[str, Any] | None = None,
    chunk: int = 20_000,
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
        for start in range(0, len(blocks), chunk):
            sl = slice(start, start + chunk)
            d = _miles(lat[sl], lon[sl], s_lat, s_lon)
            near[sl] = (d <= radius[None, :]).any(axis=1)
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


# ---------------------------------------------------------------- saving


def _share(params: Mapping[str, np.ndarray], pid: str) -> np.ndarray:
    v = np.asarray(params[pid], dtype=float)
    if np.any((v < 0) | (v > 1)):
        raise ValueError(f"{pid} must be a share between 0 and 1")
    return v


def parking_saving(params: Mapping[str, np.ndarray], weights: np.ndarray) -> dict[str, np.ndarray]:
    """Spaces avoided, cost per space and saving per unit, by county, type and draw.

    Args:
        params: Ledger id to draws (shape (draws,)); needs ``SAVING_IDS``.
        weights: Transit-rich share by county and structure type, shape (counties, 4).

    Returns:
        ``required``, ``still_built``, ``avoided`` (spaces per unit),
        ``avoided_share`` (avoided / required), ``cost_per_space`` ($) and
        ``saving`` ($ per unit), each of shape (counties, 4, draws). Types the
        reform does not reach are zero.

    Raises:
        ValueError: A share is outside 0-1, or a tract class's mix of structured
            and underground spaces is above 1.
    """
    still_tr, still_el = _share(params, STILL_TR), _share(params, STILL_EL)
    mix = {pid: _share(params, pid) for pid in (MIX_S_TR, MIX_U_TR, MIX_S_EL, MIX_U_EL)}
    surf_att = _share(params, MIX_SURF_ATT)
    if np.any(mix[MIX_S_TR] + mix[MIX_U_TR] > 1) or np.any(mix[MIX_S_EL] + mix[MIX_U_EL] > 1):
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
    cost_tr = np.where(_IS_APARTMENT[:, None], blend(mix[MIX_S_TR], mix[MIX_U_TR]), attached)
    cost_el = np.where(_IS_APARTMENT[:, None], blend(mix[MIX_S_EL], mix[MIX_U_EL]), attached)
    cost_tr = np.where(_REACHED[:, None], cost_tr, 0.0)
    cost_el = np.where(_REACHED[:, None], cost_el, 0.0)
    w = np.asarray(weights, dtype=float)[:, :, None]
    avoided_tr = req * (1 - still_tr)[None, :]
    avoided_el = req * (1 - still_el)[None, :]
    saving = w * (avoided_tr * cost_tr)[None] + (1 - w) * (avoided_el * cost_el)[None]
    avoided = w * avoided_tr[None] + (1 - w) * avoided_el[None]
    required = np.broadcast_to(req[None], avoided.shape)
    with np.errstate(divide="ignore", invalid="ignore"):
        avoided_share = np.where(required > 0, avoided / np.where(required > 0, required, 1), 0.0)
    cost_per_space = np.where(avoided > 0, saving / np.where(avoided > 0, avoided, 1), 0.0)
    return {
        "required": np.array(required),
        "still_built": np.array(required - avoided),
        "avoided": avoided,
        "avoided_share": avoided_share,
        "cost_per_space": cost_per_space,
        "saving": saving,
    }


def _parking_core(
    params: Mapping[str, np.ndarray], base: np.ndarray, market: Market, weights: np.ndarray
) -> dict[str, np.ndarray]:
    s = parking_saving(params, weights)
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

    Returns:
        The result. ``parts`` holds the cost channel and the site-yield term.
    """
    ledger = apply_overrides(ledger, overrides)
    counties = tuple(str(c) for c in baseline.index)
    base = baseline_array(baseline, counties)
    market = market_from_tracts(tracts, counties)
    flags = (
        transit_rich
        if transit_rich is not None
        else transit_rich_tracts(blocks, stops, ledger, boundaries=boundaries)
    )
    weights = transit_weights(tracts, flags, counties)
    xids = tuple(sorted({*CROSSCHECK_IDS, *PARKING_IDS}))
    params = _draws(ledger, xids, n_draws, seed)
    based = _scaled(base, baseline_scale, n_draws)
    core = _parking_core(params, based, market, weights)
    ids = (*PARKING_IDS, *TRANSIT_IDS)
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

    central = _central(ledger, PARKING_IDS)
    c_core = _parking_core(central, base[:, :, None], market, weights)
    steps = _parking_steps(ledger, base, c_core, flags)
    swing = swing_table(
        lambda p: float(_parking_core(p, base[:, :, None], market, weights)["added"].sum()),
        ledger,
        PARKING_IDS,
    )
    draft = sorted(i for i in ids if ledger[i].status == "draft")
    notes = (
        "1-unit buildings are not reached: the permit data cannot separate townhouses "
        "from detached homes, so the estimate leaves townhouse parking savings out.",
        "Transit-rich tracts are flagged from the stop table in the data pack, straight-line "
        "distance (decision D-07).",
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
    )


def _parking_steps(
    ledger: Ledger, base: np.ndarray, c: dict[str, np.ndarray], flags: pd.Series
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
