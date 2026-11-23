"""Reform 3, transit-oriented density: mid-rise apartments near major transit stops, as a funnel.

Plain-language summary
----------------------
The reform allows more floor area and height near major transit stops
(decision D-07 defines a stop: a rail station, or a bus stop with at least 64
weekday departures), and lets projects there build less parking. This module
estimates how many homes that adds in each census tract over 2027-2031, as a
likely range. All arrays are draws x tracts.

The funnel, one step after another:

1. **Eligible land** ``L``: block land within the chosen distance of a major
   stop (tract columns ``transit_land_m2_*``; ``0_25mi``, ``0_50mi``,
   ``0_75mi`` for any stop, or ``d07`` for the D-07 rule: 1/2 mile from rail,
   1/4 mile from frequent bus). The homes already there (``transit_units_*``)
   are kept for the site price.
2. **Developable land** = ``L x d``. ``d`` is the share of that land that
   could realistically become a mid-rise site (ledger, by area type:
   ``tod.developable_share_urban`` / ``_suburban`` / ``_rural``).
3. **Feasible land** = developable land ``x p``. The shared mid-rise pro forma
   (:func:`visionhousing.proforma.midrise_proforma`) gives residual land value
   per apartment ``RLV``, with parking at the D-08 transit-rich share of the old
   requirement (spaces per unit = ``parking.required_spaces_per_unit_multifamily
   x parking.share_still_built_transit_rich``), structured parking cost. At the
   upzoned intensity a site holds ``n`` apartments per square metre::

       FAR used = min(tod.upzoned_far, tod.upzoned_storeys x tod.lot_coverage)
       n        = FAR used x tod.net_floor_share / floor area per apartment

   so the site is worth ``RLV x n`` per square metre to a builder. Today the
   site costs its existing homes: ``V x h`` per square metre, with ``h`` the
   homes per square metre on the eligible land (never below
   ``tod.min_existing_homes_per_acre``) and ``V`` a home value. It pencils when
   ``V x h x (1 + tod.acquisition_premium) <= RLV x n``. ``p`` is the share of
   the tract's owner home values (ACS brackets) at or below that break-even
   value. The open top bracket is never counted as cheap enough (as for
   missing middle).
4. **Added capacity** = feasible land ``x (FAR used - tod.current_far_allowed)
   x net floor share / apartment size``, never below 0. Only floor area above
   what today's rules allow counts; what is allowed already is in the
   baseline. With no upzoning (FAR used at or below today's) the reform adds 0.
5. **Built** = capacity ``x min(1, h_y x sum of ramp(t))``, with ``h_y`` the
   yearly share of feasible sites permitted (``tod.redevelopment_hazard_annual``)
   and the ramp of ``tod.ramp_first_year_share`` and ``tod.ramp_years_to_full``.
6. **Absorbed**: new apartments must be rented, so the county's total is capped
   at ``tod.absorption_cap_share x`` the county's baseline 5+ unit permits over
   the five years (passed in by the engine, by draw). When a county is over the
   cap, every tract in it is scaled down by the same factor.
7. **Net added** = absorbed ``x tod.net_new_share``. Apartments that would have
   been built elsewhere in the region anyway are moved, not added, and are not
   counted. This share is wide and is a headline caveat.

Channels: A (new capacity near stops, steps 1-5) and B (the reduced parking
cost inside the pro forma, step 3).

What it does not do
-------------------
- No parcel data: "developable" is a share, and the site price uses the tract's
  home values, not assessed land values.
- Only rented apartments (income basis); condominiums are not priced.
- Fees enter through ``fees.fee_total_per_unit``; a fee cut in a package does
  not re-enter this pro forma (stated in the package notes).

Overlap hooks (for the engine)
------------------------------
- With missing middle: lots inside transit areas count under the denser reform,
  TOD. :func:`visionhousing.engine.packages.tod_middle_overlap` uses
  :attr:`TodResult.in_area_units_share` to take those lots out of missing middle.
- With the parking reform: the parking reduction near stops is already inside
  this pro forma, and the parking reform acts only on baseline production, so a
  unit is never counted twice (see ``docs/methods/tod.md``).

:func:`tod_funnel` is pure: tables and ledger in, result out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.features import brackets as br
from visionhousing.features.transit import DISTANCE_CHOICES_MILES, radius_label
from visionhousing.ledger.loader import Ledger
from visionhousing.ledger.sampling import sample
from visionhousing.ledger.scenarios import Scenario
from visionhousing.proforma import MIDRISE, midrise_proforma
from visionhousing.scenarios.adu import ramp_sum

__all__ = [
    "DISTANCES",
    "M2_PER_ACRE",
    "STAGES",
    "TodResult",
    "TodScope",
    "distance_suffix",
    "scope_from_scenario",
    "tod_funnel",
]

DISTANCES: tuple[Any, ...] = (*DISTANCE_CHOICES_MILES, "d07")
"""Distance choices: 1/4, 1/2 or 3/4 mile from any major stop, or the D-07 rule."""

DEFAULT_DISTANCE = "d07"
"""Decision D-07: 1/2 mile from rail, 1/4 mile from frequent bus."""

M2_PER_ACRE = 4046.8564224
"""Square metres in one acre (exact by definition)."""

SQ_M_PER_SQ_FT = 0.09290304
"""Square metres in one square foot (exact by definition)."""

STAGES = ("eligible", "developable", "feasible", "capacity", "built", "absorbed", "net")
"""Funnel stages, in order. Land stages are in acres, the rest in units."""

STAGE_NAMES = {
    "eligible": "land within {distance} of a major transit stop",
    "developable": "land that could become a mid-rise site",
    "feasible": "land where a mid-rise building pencils",
    "capacity": "added homes the upzoning allows on feasible land",
    "built": "mid-rise homes permitted {years}, before the absorption cap",
    "absorbed": "mid-rise homes permitted {years}, within regional absorption",
    "net": "net added homes {years} (new to the region)",
}

STAGE_UNITS = {
    "eligible": "acres",
    "developable": "acres",
    "feasible": "acres",
    "capacity": "units",
    "built": "units",
    "absorbed": "units",
    "net": "units",
}

AREA_PARAMS = {
    "urban": "tod.developable_share_urban",
    "suburban": "tod.developable_share_suburban",
    "rural": "tod.developable_share_rural",
}
"""Area type to the ledger id of its developable share. Unknown types use suburban."""

INTENSITY_PARAMS = (
    "tod.upzoned_far",
    "tod.upzoned_storeys",
    "tod.lot_coverage",
    "tod.net_floor_share",
)
CURRENT_FAR = "tod.current_far_allowed"
RENT_PARAMS = ("tod.new_unit_rent_premium", "tod.rent_ratio_2br")
PRICE_PARAMS = ("tod.acquisition_premium", "tod.min_existing_homes_per_acre")
PARKING_SHARE = "parking.share_still_built_transit_rich"
TAKEUP_PARAMS = (
    "tod.redevelopment_hazard_annual",
    "tod.ramp_first_year_share",
    "tod.ramp_years_to_full",
)
CAP_PARAM = "tod.absorption_cap_share"
NET_NEW_PARAM = "tod.net_new_share"
YEAR_PARAMS = ("baseline.first_year", "baseline.horizon_years")

PROFORMA_IDS = tuple(
    i.key
    for i in MIDRISE.inputs("income", with_acquisition=False)
    if i.is_parameter and i.key != MIDRISE.units.key
)
"""Ledger ids the mid-rise pro forma reads. Units per project is set to 1, so the
residual is per apartment (every line is proportional to units)."""

SPACES_ID = MIDRISE.spaces.key
UNIT_SIZE_ID = MIDRISE.unit_size.key
COST_FACTOR_COLUMN = "cost_factor"

DATA_SOURCES = {
    "eligible": ("tiger_2020_tabblock", "ntm_stops", "gtfs_agency_feeds"),
    "developable": ("acs5_summary_file",),
    "feasible": ("acs5_summary_file",),
    "absorbed": ("bps_county_annual",),
}
"""Registry ids of the data behind each stage (2020 blocks and stops; ACS area
type, rents and home values; Building Permits Survey 5+ unit baseline)."""

_CHUNK_ROWS = 200_000


# ---------------------------------------------------------------- scope


def distance_suffix(distance: Any) -> str:
    """Tract-column suffix for a distance choice: ``0.5`` -> ``"0_50mi"``, ``"d07"`` -> ``"d07"``.

    Raises:
        ValueError: Not one of :data:`DISTANCES`.
    """
    if isinstance(distance, str) and distance.lower() == "d07":
        return "d07"
    try:
        miles = float(distance)
    except (TypeError, ValueError):
        raise ValueError(f"TOD distance must be one of {DISTANCES}, got {distance!r}") from None
    if not any(abs(miles - m) < 1e-9 for m in DISTANCE_CHOICES_MILES):
        raise ValueError(f"TOD distance must be one of {DISTANCES}, got {distance!r}")
    return radius_label(miles)


def distance_words(distance: Any) -> str:
    """Plain words for a distance choice."""
    suffix = distance_suffix(distance)
    if suffix == "d07":
        return "1/2 mile (rail) or 1/4 mile (frequent bus)"
    words = {"0_25mi": "1/4 mile", "0_50mi": "1/2 mile", "0_75mi": "3/4 mile"}
    return words[suffix]


@dataclass(frozen=True)
class TodScope:
    """What a TOD scenario reaches.

    Attributes:
        name: Scenario id.
        distance: ``0.25``, ``0.5``, ``0.75`` (miles from any major stop) or
            ``"d07"`` (the D-07 rule).
    """

    name: str
    distance: Any = DEFAULT_DISTANCE

    def __post_init__(self) -> None:
        """Check the distance."""
        distance_suffix(self.distance)

    @property
    def suffix(self) -> str:
        """Tract-column suffix of the distance."""
        return distance_suffix(self.distance)


def scope_from_scenario(
    scenario_id: str, scenario: Scenario, distance: Any = DEFAULT_DISTANCE
) -> TodScope:
    """Turn a scenario from ``scenarios.yaml`` into a TOD scope.

    Raises:
        ValueError: The scenario does not turn on the TOD reform, or leaves out
            places (not supported yet), or the distance is not a choice.
    """
    if "tod" not in scenario.reforms:
        raise ValueError(f"scenario '{scenario_id}' does not turn on the TOD reform")
    if scenario.excluded_places:
        raise ValueError(f"scenario '{scenario_id}': excluded places are not supported yet")
    return TodScope(name=scenario_id, distance=distance)


# ---------------------------------------------------------------- the result


@dataclass(frozen=True)
class TodResult:
    """What :func:`tod_funnel` returns.

    Attributes:
        scope: The scenario's scope.
        geoids: Tract codes.
        counties: County code per tract.
        stages: Stage name to draws x tracts arrays (see :data:`STAGES`).
        eligible_homes: Existing homes within the distance, per tract (2020 blocks).
        in_area_units_share: Share of each tract's homes within the distance
            (the missing-middle overlap hook).
        feasible_share: ``p`` per draw and tract.
        residual_per_unit: Median over draws of ``RLV`` per apartment, per tract ($).
        capped_share: Share of draws in which each county hit the absorption cap.
        years: Calendar years counted.
        net_by_year: State net added units per draw and year.
        records: State-total records, one per stage, plus ``eligible_homes`` and ``per_100k``.
        seed: Seed used.
        capped: Whether an absorption cap was applied.
    """

    scope: TodScope
    geoids: np.ndarray
    counties: np.ndarray
    stages: dict[str, np.ndarray]
    eligible_homes: np.ndarray
    in_area_units_share: np.ndarray
    feasible_share: np.ndarray
    residual_per_unit: np.ndarray
    capped_share: dict[str, float]
    years: tuple[int, ...]
    net_by_year: np.ndarray
    records: dict[str, Record]
    seed: int
    capped: bool

    def funnel(self) -> list[Record]:
        """Return the state funnel, one record per stage in :data:`STAGES` order."""
        return [self.records[s] for s in STAGES]

    def tract_table(self) -> pd.DataFrame:
        """Return P10 / P50 / P90 of every stage for every tract."""
        out = pd.DataFrame({"geoid": self.geoids, "county": self.counties})
        for stage in STAGES:
            p10, p50, p90 = np.percentile(self.stages[stage], [10, 50, 90], axis=0)
            out[f"{stage}_p10"], out[f"{stage}_p50"], out[f"{stage}_p90"] = p10, p50, p90
        return out


_EXPRESSIONS = {
    "eligible": "eligible land = block land within {distance} of a major stop (D-07 stops)",
    "developable": "developable land = eligible land x developable share for the area type",
    "feasible": (
        "feasible land = developable land x p, where p = share of owner home values V with "
        "V x homes per m2 x (1 + acquisition premium) <= mid-rise residual land value per "
        "apartment x apartments per m2 at the upzoned FAR (parking at the D-08 transit-rich share)"
    ),
    "capacity": (
        "added capacity = feasible land x max(min(upzoned FAR, storeys x coverage) - today's "
        "FAR, 0) x net floor share / apartment floor area"
    ),
    "built": (
        "built = added capacity x min(1, yearly share permitted x sum over {years} of ramp(t))"
    ),
    "absorbed": (
        "absorbed = built x min(1, absorption cap share x county baseline 5+ unit permits "
        "{years} / county built)"
    ),
    "net": "net added = absorbed x net-new share (units moved within the region are not counted)",
}


def _period(years: Sequence[int]) -> str:
    return f"{years[0]}-{years[-1]}" if years else ""


def _column(tracts: pd.DataFrame, name: str) -> np.ndarray:
    if name not in tracts:
        raise KeyError(f"tract table needs column '{name}'")
    return tracts[name].to_numpy(dtype=float)


def _stage_ids(capped: bool) -> dict[str, list[str]]:
    eligible: list[str] = []
    developable = [*AREA_PARAMS.values()]
    feasible = [
        *developable,
        *INTENSITY_PARAMS,
        *RENT_PARAMS,
        *PRICE_PARAMS,
        PARKING_SHARE,
        *PROFORMA_IDS,
    ]
    capacity = [*feasible, CURRENT_FAR]
    built = [*capacity, *TAKEUP_PARAMS, *YEAR_PARAMS]
    absorbed = [*built, *([CAP_PARAM] if capped else [])]
    out = {
        "eligible": eligible,
        "developable": developable,
        "feasible": feasible,
        "capacity": capacity,
        "built": built,
        "absorbed": absorbed,
        "net": [*absorbed, NET_NEW_PARAM],
    }
    return {k: sorted(set(v)) for k, v in out.items()}


def _needed_ids(ledger: Ledger) -> list[str]:
    ids = [
        *AREA_PARAMS.values(),
        *INTENSITY_PARAMS,
        CURRENT_FAR,
        *RENT_PARAMS,
        *PRICE_PARAMS,
        PARKING_SHARE,
        *PROFORMA_IDS,
        *TAKEUP_PARAMS,
        CAP_PARAM,
        NET_NEW_PARAM,
    ]
    for pid in [*ids, *YEAR_PARAMS]:
        ledger[pid]  # raises a clear LedgerError if missing
    return list(dict.fromkeys(ids))


# ---------------------------------------------------------------- the steps


def _developable_share(tracts: pd.DataFrame, col: Mapping[str, np.ndarray]) -> np.ndarray:
    """Developable share per draw and tract, by area type."""
    areas = (
        tracts["area_type"].astype(str).str.lower().to_numpy() if "area_type" in tracts else None
    )
    n = len(tracts)
    out = np.broadcast_to(col[AREA_PARAMS["suburban"]], (col[CAP_PARAM].shape[0], n)).copy()
    if areas is None:
        return out
    for area, pid in AREA_PARAMS.items():
        mask = areas == area
        out[:, mask] = col[pid]
    return out


def _new_unit_rent(tracts: pd.DataFrame, col: Mapping[str, np.ndarray]) -> np.ndarray:
    """Monthly rent of a new mid-rise apartment, draws x tracts (blank rent gives 0)."""
    b2 = _column(tracts, "median_rent_b2")[None, :]
    gross = _column(tracts, "median_gross_rent")[None, :]
    base = np.where(np.isfinite(b2) & (b2 > 0), b2, gross * col["tod.rent_ratio_2br"])
    return np.nan_to_num(base * col["tod.new_unit_rent_premium"], nan=0.0)


def _residual_per_unit(
    tracts: pd.DataFrame, col: Mapping[str, np.ndarray], rent: np.ndarray, draws: int
) -> np.ndarray:
    """Mid-rise residual land value per apartment, draws x tracts ($)."""
    n = len(tracts)
    cost_factor = (
        _column(tracts, COST_FACTOR_COLUMN)[None, :] if COST_FACTOR_COLUMN in tracts else 1.0
    )
    out = np.empty((draws, n))
    step = max(1, _CHUNK_ROWS // max(n, 1))
    for start in range(0, draws, step):
        stop = min(start + step, draws)
        params = {pid: col[pid][start:stop] for pid in PROFORMA_IDS}
        params[MIDRISE.units.key] = 1.0
        params[SPACES_ID] = col[SPACES_ID][start:stop] * col[PARKING_SHARE][start:stop]
        context = {"rent_monthly": rent[start:stop], "cost_factor": cost_factor}
        rlv = midrise_proforma(params, context).lines["residual_land_value"]
        out[start:stop] = np.broadcast_to(rlv, (stop - start, n))
    return out


def _share_below(tracts: pd.DataFrame, threshold: np.ndarray, rows: int) -> np.ndarray:
    """Share of each tract's owner home values at or below ``threshold`` (draws x tracts)."""
    n = len(tracts)
    counts = tracts[list(br.VALUE.columns)].to_numpy(dtype=float)
    top_low = float(br.VALUE.lows[-1])
    median_value = _column(tracts, "median_home_value")
    t = np.minimum(threshold, top_low)
    out = np.empty((rows, n))
    step = max(1, _CHUNK_ROWS // max(n, 1))
    for start in range(0, rows, step):
        stop = min(start + step, rows)
        part = t[start:stop]
        res = br.share_below_many(np.tile(counts, (stop - start, 1)), br.VALUE, part.reshape(-1))
        p = np.where(part > 0, res.value.reshape(stop - start, n), 0.0)
        fallback = np.where(
            np.isfinite(median_value)[None, :] & (median_value[None, :] <= part) & (part > 0),
            1.0,
            0.0,
        )
        out[start:stop] = np.where(np.isnan(p), fallback, p)
    return out


def _absorption(
    built: np.ndarray,
    counties: np.ndarray,
    cap_share: np.ndarray,
    mf_baseline: pd.Series | Mapping[str, float] | None,
    mf_scale: np.ndarray | None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Scale built units down where a county is over its absorption cap."""
    if mf_baseline is None:
        return built.copy(), {}
    base = pd.Series(mf_baseline, dtype=float)
    base.index = base.index.astype(str)
    order = sorted(set(counties))
    out = built.copy()
    hit: dict[str, float] = {}
    for i, county in enumerate(order):
        mask = counties == county
        total = built[:, mask].sum(axis=1)  # (draws,)
        scale = 1.0 if mf_scale is None else np.asarray(mf_scale, dtype=float)[i]
        b5 = float(base.get(county, 0.0))
        cap = cap_share[:, 0] * max(b5, 0.0) * scale
        with np.errstate(divide="ignore", invalid="ignore"):
            factor = np.where(total > cap, cap / total, 1.0)
        factor = np.nan_to_num(factor, nan=0.0)
        out[:, mask] = built[:, mask] * factor[:, None]
        hit[county] = float(np.mean(total > cap + 1e-12))
    return out, hit


# ---------------------------------------------------------------- the funnel


def tod_funnel(
    tracts: pd.DataFrame,
    ledger: Ledger,
    scope: TodScope,
    *,
    mf_baseline: pd.Series | Mapping[str, float] | None = None,
    mf_scale: np.ndarray | None = None,
    draws: int = 2000,
    seed: int = 0,
) -> TodResult:
    """Run the TOD funnel for every tract and every Monte Carlo draw.

    Args:
        tracts: Tract table in the state-pack schema: ``geoid``, ``county``,
            ``area_type``, ``population``, ``median_rent_b2``,
            ``median_gross_rent``, ``median_home_value``, the ``value_*``
            brackets, and the transit columns for the scope's distance
            (``transit_land_m2_<s>``, ``transit_units_<s>``,
            ``transit_units_share_<s>``). Optional: ``cost_factor``.
        ledger: The ledger, with the scenario's overrides already applied.
        scope: What the scenario reaches (:func:`scope_from_scenario`).
        mf_baseline: Baseline 5+ unit permits over the five years, by county
            code (the absorption cap's scale). ``None`` applies no cap.
        mf_scale: Optional baseline multiplier per county and draw, shape
            ``(counties, draws)``, counties in sorted order of the tract table's
            county codes (1 = baseline P50).
        draws: Number of Monte Carlo draws.
        seed: Seed; the same seed gives the same result.

    Returns:
        A :class:`TodResult`.

    Raises:
        KeyError: A tract column is missing.
        LedgerError: A ledger parameter is missing.
    """
    ids = _needed_ids(ledger)
    v = sample(ledger, n=draws, seed=seed, ids=ids)
    col = {k: a[:, None] for k, a in v.items()}
    first_year = int(ledger[YEAR_PARAMS[0]].central)
    horizon = int(ledger[YEAR_PARAMS[1]].central)
    years = tuple(range(first_year, first_year + horizon))
    counties = tracts["county"].astype(str).to_numpy()
    s = scope.suffix

    # 1. Eligible land and the homes on it.
    land_m2 = np.clip(np.nan_to_num(_column(tracts, f"transit_land_m2_{s}")), 0, None)
    homes = np.clip(np.nan_to_num(_column(tracts, f"transit_units_{s}")), 0, None)
    in_share = np.clip(np.nan_to_num(_column(tracts, f"transit_units_share_{s}")), 0, 1)
    eligible_m2 = np.broadcast_to(land_m2[None, :], (draws, len(tracts)))

    # 2. Developable land.
    developable_m2 = eligible_m2 * _developable_share(tracts, col)

    # 3. Feasible land.
    far_used = np.minimum(
        col["tod.upzoned_far"], col["tod.upzoned_storeys"] * col["tod.lot_coverage"]
    )
    unit_m2 = col[UNIT_SIZE_ID] * SQ_M_PER_SQ_FT
    units_per_m2 = far_used * col["tod.net_floor_share"] / unit_m2  # (draws, 1)
    rent = _new_unit_rent(tracts, col)
    rlv_unit = _residual_per_unit(tracts, col, rent, draws)
    with np.errstate(divide="ignore", invalid="ignore"):
        density = np.where(land_m2 > 0, homes / land_m2, 0.0)[None, :]
    density = np.maximum(density, col["tod.min_existing_homes_per_acre"] / M2_PER_ACRE)
    break_even = rlv_unit * units_per_m2 / (density * (1.0 + col["tod.acquisition_premium"]))
    p = np.where(units_per_m2 > 0, _share_below(tracts, break_even, draws), 0.0)
    feasible_m2 = developable_m2 * p

    # 4. Added capacity above what today's rules allow.
    gain_far = np.maximum(far_used - col[CURRENT_FAR], 0.0)
    capacity = feasible_m2 * gain_far * col["tod.net_floor_share"] / unit_m2

    # 5. Take-up with the ramp.
    ramp = ramp_sum(v["tod.ramp_first_year_share"], v["tod.ramp_years_to_full"], horizon)
    over_period = np.minimum(col["tod.redevelopment_hazard_annual"] * ramp.sum(axis=1)[:, None], 1)
    built = capacity * over_period

    # 6. Regional absorption cap.
    absorbed, hit = _absorption(built, counties, col[CAP_PARAM], mf_baseline, mf_scale)

    # 7. Net new to the region.
    net = absorbed * col[NET_NEW_PARAM]
    with np.errstate(divide="ignore", invalid="ignore"):
        year_share = np.nan_to_num(ramp / ramp.sum(axis=1, keepdims=True))
    net_by_year = net.sum(axis=1)[:, None] * year_share

    stages = {
        "eligible": eligible_m2 / M2_PER_ACRE,
        "developable": developable_m2 / M2_PER_ACRE,
        "feasible": feasible_m2 / M2_PER_ACRE,
        "capacity": capacity,
        "built": built,
        "absorbed": absorbed,
        "net": net,
    }
    population = float(np.nansum(_column(tracts, "population")))
    records = _state_records(
        stages, ledger, scope, mf_baseline is not None, population, years, homes
    )
    return TodResult(
        scope=scope,
        geoids=tracts["geoid"].astype(str).to_numpy(),
        counties=counties,
        stages=stages,
        eligible_homes=homes,
        in_area_units_share=in_share,
        feasible_share=p,
        residual_per_unit=np.median(rlv_unit, axis=0),
        capped_share=hit,
        years=years,
        net_by_year=net_by_year,
        records=records,
        seed=seed,
        capped=mf_baseline is not None,
    )


def _state_records(
    stages: Mapping[str, np.ndarray],
    ledger: Ledger,
    scope: TodScope,
    capped: bool,
    population: float,
    years: Sequence[int],
    homes: np.ndarray,
) -> dict[str, Record]:
    horizon = len(years)
    words = distance_words(scope.distance)
    stage_ids = _stage_ids(capped)
    out: dict[str, Record] = {}
    data_sources: list[str] = []
    fmt = {"years": _period(years), "distance": words}
    for stage in STAGES:
        data_sources += DATA_SOURCES.get(stage, ())
        inputs = [rec.from_parameter(pid, ledger[pid]) for pid in stage_ids[stage]]
        out[stage] = rec.from_draws(
            f"{STAGE_NAMES[stage].format(**fmt)} ({scope.name}, {words})",
            stages[stage].sum(axis=1),
            STAGE_UNITS[stage],
            expression=f"the sum over tracts of {_EXPRESSIONS[stage].format(**fmt)}",
            inputs=inputs,
            source_ids=sorted(set(data_sources)),
            tier=0,
        )
    out["eligible_homes"] = rec.from_draws(
        f"existing homes within {words} of a major transit stop ({scope.name})",
        np.full(stages["eligible"].shape[0], float(homes.sum())),
        "units",
        expression="the sum over tracts of 2020 block housing units within the distance",
        source_ids=DATA_SOURCES["eligible"],
        tier=0,
    )
    if population > 0:
        net = out["net"]
        out["per_100k"] = rec.from_draws(
            f"net added TOD units per 100,000 residents per year ({scope.name})",
            stages["net"].sum(axis=1) / horizon / population * 100_000,
            "permits per 100k people per year",
            expression=(
                f"net added units / {horizon} years / residents ({population:,.0f}) x 100,000"
            ),
            assumption_ids=net.assumption_ids,
            source_ids=net.source_ids,
            tier=0,
        )
    return out
