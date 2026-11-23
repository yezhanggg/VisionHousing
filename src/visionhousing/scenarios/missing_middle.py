"""Reform 2, missing middle: 2-6 unit buildings on single-family lots, as a funnel.

Plain-language summary
----------------------
The reform lets a builder put 2 to 6 homes, up to three storeys, on a lot that
today allows only one house. To use it, a builder must buy the house, tear it
down and build. This module estimates how many homes that adds in each census
tract over 2027-2031, as a likely range.

It works as a funnel. Each step keeps a share of the lots from the step before:

1. **Eligible lots** ``E``: single-family homes (census units in structure,
   ``1, detached``; the scenario says which kinds), scaled by the
   census-to-real-lot correction, times the share of those lots in zones that
   allow only one home (``missing_middle.sf_only_zoned_share``, a ledger
   parameter at Tier 0). In a package with the ADU reform, the engine may pass
   the lots ADUs used; a share of them is taken out here (see "Overlap hook").
2. **Lots large enough** = ``E x f``. ``f`` comes from the best data present:
   a measured share per tract (Tier 2, the ``fit`` argument); else the block
   lot-size proxy (tract columns ``lot_m2_per_unit_p25/p50/p75``, see
   :mod:`visionhousing.features.density`); else the ledger's
   ``missing_middle.fit_share``.
3. **Feasible lots** = lots large enough ``x p``. For each building type ``k``
   from a duplex (2) to a sixplex (6) the redevelopment pro forma gives the
   residual land value ``RLV_k``: what a builder could pay for the lot. A lot
   pencils when its home value x (1 + acquisition premium) is at or below
   ``RLV_k``, so ``p_k`` is the share of the tract's owner home values (ACS
   brackets) below ``RLV_k / (1 + premium)``. The mix weights ``w_k`` (ledger)
   give ``p = sum of w_k x p_k``.
4. **Lots redeveloped** = feasible lots ``x min(h, turnover) x sum of ramp(t)``.
   ``h`` is the yearly redevelopment hazard among feasible lots; it can never
   be above the yearly sale rate of single-family homes (``turnover``), since a
   lot must be sold first.
5. **New units** = lots redeveloped by type ``x k``. **Net units** = new units
   ``-`` homes demolished (one per lot): ``units - 1`` per project.
6. **Net added units** = net units ``x (1 - x)``; ``x`` is the share that would
   have been built anyway (displacement).

Every ledger value is drawn many times (Monte Carlo, seeded), so every step is
a likely range: P10, P50 and P90. All arrays are draws x tracts.

The value test, in detail
-------------------------
- Rent for a new unit = tract two-bedroom median (``median_rent_b2``) x the
  new-unit premium. Where the two-bedroom median is blank, the tract's median
  gross rent x ``missing_middle.rent_ratio_2br`` stands in.
- The pro forma is the shared one (:func:`visionhousing.proforma.redevelopment_proforma`),
  income basis, run once per building type with ``units = k``. Its fees
  (``fees.fee_total_per_unit``) and parking lines are ledger values, so the fee
  and parking reforms reach this funnel through ledger overrides.
- The open top value bracket ($2,000,000 or more) has no upper edge. The test
  never counts it as cheap enough to buy: the threshold is capped at that
  bracket's lower edge. This can only lower feasibility.
- A tract with no owner homes in the brackets falls back to its median home
  value: ``p_k`` is 1 if ``median x (1 + premium) <= RLV_k``, else 0.

Overlap hook (for the engine)
-----------------------------
ADUs and missing middle compete for the same lots (plan section 4.5). Pass
``adu_lots``, the lots already used by ADUs (for example the ADU funnel's
``built`` stage, draws x tracts, or one number per tract); eligible lots become
``max(E - s x adu_lots, 0)`` with ``s = missing_middle.adu_lot_overlap_share``.
How lots are split between the two reforms is the engine's choice; this
function only takes out what it is given.

Files and purity
----------------
:func:`missing_middle_funnel` is pure: tables and ledger in, result out. Its
signature and result follow :func:`visionhousing.scenarios.adu.adu_funnel`, so
the engine registers it the same way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.features import brackets as br
from visionhousing.ledger.loader import Ledger
from visionhousing.ledger.sampling import sample
from visionhousing.ledger.scenarios import Scenario
from visionhousing.proforma import REDEVELOPMENT, redevelopment_proforma
from visionhousing.scenarios.adu import ramp_sum

__all__ = [
    "BUILDING_TYPES",
    "FIT_SOURCES",
    "LOT_COLUMNS",
    "SQ_M_PER_SQ_FT",
    "STAGES",
    "TYPOLOGIES",
    "MiddleResult",
    "MiddleScope",
    "missing_middle_funnel",
    "scope_from_scenario",
    "share_at_least",
]

BUILDING_TYPES = ("sf_detached", "sf_attached")
"""Kinds of existing lots a missing-middle scenario can reach (names as in scenarios.yaml)."""

HOME_COLUMNS = {"sf_detached": "units_1_detached", "sf_attached": "units_1_attached"}
"""Census units-in-structure column for each kind of lot."""

TYPOLOGIES = {
    2: "missing_middle.typology_weight_duplex",
    3: "missing_middle.typology_weight_triplex",
    4: "missing_middle.typology_weight_fourplex",
    5: "missing_middle.typology_weight_fiveplex",
    6: "missing_middle.typology_weight_sixplex",
}
"""Units per building to the ledger id of its weight in the mix."""

LOT_COLUMNS = ("lot_m2_per_unit_p25", "lot_m2_per_unit_p50", "lot_m2_per_unit_p75")
"""Tract columns of the block lot-size proxy (square metres of block land per home)."""

SQ_M_PER_SQ_FT = 0.09290304
"""Square metres in one square foot (exact by definition)."""

FIT_SOURCES = ("measured", "lot_proxy", "parameter")
"""Where the fit share came from, best first."""

STAGES = ("eligible", "fit", "feasible", "redeveloped", "units", "net_units", "net")
"""Funnel stages, in order."""

STAGE_NAMES = {
    "eligible": "eligible single-family lots in single-family-only zones",
    "fit": "lots large enough for a 2-6 unit building",
    "feasible": "lots where redevelopment pencils",
    "redeveloped": "lots redeveloped {years}",
    "units": "new units in 2-6 unit buildings {years}",
    "net_units": "net new units after demolition {years}",
    "net": "net added units {years}",
}

STAGE_UNITS = {
    "eligible": "lots",
    "fit": "lots",
    "feasible": "lots",
    "redeveloped": "lots",
    "units": "units",
    "net_units": "units",
    "net": "units",
}

DATA_SOURCES = {
    "eligible": ("acs5_summary_file",),
    "feasible": ("acs5_summary_file",),
}
"""Registry ids of the data behind each stage (ACS units in structure B25024;
home value B25075; rent by bedrooms B25031; gross rent B25064)."""

LOT_SOURCE = "tiger_2020_tabblock"
"""Registry id of the 2020 census blocks behind the lot-size proxy."""

LOT_PARAM = "adu.lot_proxy_adjustment"
ZONED_PARAM = "missing_middle.sf_only_zoned_share"
OVERLAP_PARAM = "missing_middle.adu_lot_overlap_share"
FIT_PARAM = "missing_middle.fit_share"
PROXY_PARAMS = ("missing_middle.min_lot_sqft", "missing_middle.lot_proxy_net_share")
RANGE_PARAMS = ("missing_middle.min_units", "missing_middle.max_units")
RENT_PARAMS = ("missing_middle.new_unit_rent_premium", "missing_middle.rent_ratio_2br")
PREMIUM_PARAM = "missing_middle.acquisition_premium"
TAKEUP_PARAMS = (
    "missing_middle.redevelopment_hazard_annual",
    "missing_middle.sf_turnover_annual",
    "missing_middle.ramp_first_year_share",
    "missing_middle.ramp_years_to_full",
)
DEMOLISHED_PARAM = "missing_middle.demolished_per_project"
DISPLACEMENT_PARAM = "missing_middle.displacement_share"
YEAR_PARAMS = ("baseline.first_year", "baseline.horizon_years")

PROFORMA_IDS = tuple(
    i.key
    for i in REDEVELOPMENT.inputs("income", with_acquisition=False)
    if i.is_parameter and i.key != REDEVELOPMENT.units.key
)
"""Ledger ids the redevelopment pro forma reads. Units per building is not among
them: the funnel sets it to each building type ``k`` of the mix instead."""

COST_FACTOR_COLUMN = "cost_factor"
"""Optional tract column: regional construction cost factor (1 = average)."""

_CHUNK_ROWS = 200_000
"""Largest draws x tracts block handed to the pro forma and bracket functions at once."""


# ---------------------------------------------------------------- scenario hooks


@dataclass(frozen=True)
class MiddleScope:
    """What a missing-middle scenario reaches.

    Attributes:
        name: Scenario id.
        building_types: Kinds of single-family lots reached.
    """

    name: str
    building_types: tuple[str, ...]

    def __post_init__(self) -> None:
        """Check the building types."""
        unknown = sorted(set(self.building_types) - set(BUILDING_TYPES))
        if unknown or not self.building_types:
            raise ValueError(f"missing-middle scope needs building types from {BUILDING_TYPES}")


def scope_from_scenario(scenario_id: str, scenario: Scenario) -> MiddleScope:
    """Turn a scenario from ``scenarios.yaml`` into a missing-middle scope.

    Args:
        scenario_id: The scenario id, for example ``"missing_middle"``.
        scenario: The checked scenario.

    Returns:
        The scope. Only single-family lots are reached.

    Raises:
        ValueError: The scenario does not turn on the reform, reaches no
            single-family lots, or leaves out places (not supported yet).
    """
    if "missing_middle" not in scenario.reforms:
        raise ValueError(f"scenario '{scenario_id}' does not turn on the missing-middle reform")
    if scenario.excluded_places:
        raise ValueError(f"scenario '{scenario_id}': excluded places are not supported yet")
    types = tuple(t for t in scenario.building_types if t in BUILDING_TYPES)
    return MiddleScope(name=scenario_id, building_types=types)


# ---------------------------------------------------------------- the fit step


def share_at_least(
    p25: np.ndarray, p50: np.ndarray, p75: np.ndarray, threshold: np.ndarray
) -> np.ndarray:
    """Share of homes whose land per home is at least ``threshold``.

    The tract's distribution of land per home is known only at three points:
    25% of homes are below ``p25``, half below ``p50``, 75% below ``p75``. In
    between (on a log scale) the share climbs in a straight line. Beyond the
    quartiles the nearest line is extended until it reaches 0 or 1.

    Args:
        p25: 25th percentile per tract, square metres (any broadcastable shape).
        p50: Median.
        p75: 75th percentile.
        threshold: Land per home needed, same units.

    Returns:
        Shares in ``[0, 1]``. ``NaN`` where a percentile is blank or not positive.
    """
    q25, q50, q75, t = np.broadcast_arrays(
        *(np.asarray(a, dtype=float) for a in (p25, p50, p75, threshold))
    )
    bad = ~(np.isfinite(q25) & np.isfinite(q50) & np.isfinite(q75)) | (q25 <= 0)
    bad = bad | (q50 < q25) | (q75 < q50) | ~(t > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        x25, x50, x75, xt = (np.log(np.where(bad, 1.0, a)) for a in (q25, q50, q75, t))
    tiny = 1e-12
    low_w = np.maximum(x50 - x25, tiny)
    high_w = np.maximum(x75 - x50, tiny)
    below = np.where(
        xt <= x50,
        0.25 + 0.25 * (xt - x25) / low_w,  # also the extension below p25
        0.5 + 0.25 * (xt - x50) / high_w,  # also the extension above p75
    )
    share = 1.0 - np.clip(below, 0.0, 1.0)
    return np.where(bad, np.nan, share)


def _fit_share(
    tracts: pd.DataFrame,
    fit: pd.Series | Mapping[str, float] | None,
    col: Mapping[str, np.ndarray],
    draws: int,
) -> tuple[np.ndarray, str]:
    """Fit share per draw and tract, and which source gave it."""
    n = len(tracts)
    fallback = np.broadcast_to(col[FIT_PARAM], (draws, n))
    if fit is not None:
        measured = pd.Series(fit, dtype=float)
        values = tracts["geoid"].astype(str).map(measured).to_numpy(dtype=float)
        values = np.clip(values, 0.0, 1.0)
        return np.where(np.isnan(values)[None, :], fallback, values[None, :]), "measured"
    if all(c in tracts for c in LOT_COLUMNS):
        q = [tracts[c].to_numpy(dtype=float)[None, :] for c in LOT_COLUMNS]
        lot_m2 = col["missing_middle.min_lot_sqft"] * SQ_M_PER_SQ_FT
        needed = lot_m2 / col["missing_middle.lot_proxy_net_share"]  # in block land per home
        share = share_at_least(*q, needed)
        return np.where(np.isnan(share), fallback, share), "lot_proxy"
    return np.array(fallback), "parameter"


# ---------------------------------------------------------------- the feasible step


def _mix_weights(v: Mapping[str, np.ndarray], ledger: Ledger) -> dict[int, np.ndarray]:
    """Mix weights per draw, divided by their sum; types outside min..max units get 0."""
    lo = ledger[RANGE_PARAMS[0]].central
    hi = ledger[RANGE_PARAMS[1]].central
    raw = {k: (v[pid] if lo <= k <= hi else np.zeros_like(v[pid])) for k, pid in TYPOLOGIES.items()}
    total = sum(raw.values())
    if np.any(total <= 0):
        raise ValueError("missing-middle mix weights must not all be zero")
    return {k: w / total for k, w in raw.items()}


def _new_unit_rent(tracts: pd.DataFrame, col: Mapping[str, np.ndarray]) -> np.ndarray:
    """Monthly rent of a new missing-middle unit, draws x tracts (blank rent gives 0)."""
    b2 = _column(tracts, "median_rent_b2")[None, :]
    gross = _column(tracts, "median_gross_rent")[None, :]
    base = np.where(np.isfinite(b2) & (b2 > 0), b2, gross * col["missing_middle.rent_ratio_2br"])
    return np.nan_to_num(base * col["missing_middle.new_unit_rent_premium"], nan=0.0)


def _feasible_shares(
    tracts: pd.DataFrame,
    col: Mapping[str, np.ndarray],
    rent: np.ndarray,
    draws: int,
) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    """``p_k`` and ``RLV_k`` per building type, each draws x tracts."""
    n = len(tracts)
    counts = tracts[list(br.VALUE.columns)].to_numpy(dtype=float)
    top_low = float(br.VALUE.lows[-1])
    median_value = _column(tracts, "median_home_value")
    cost_factor = (
        _column(tracts, COST_FACTOR_COLUMN)[None, :] if COST_FACTOR_COLUMN in tracts else 1.0
    )
    premium = col[PREMIUM_PARAM]
    shares = {k: np.empty((draws, n)) for k in TYPOLOGIES}
    residual = {k: np.empty((draws, n)) for k in TYPOLOGIES}
    step = max(1, _CHUNK_ROWS // max(n, 1))
    for start in range(0, draws, step):
        stop = min(start + step, draws)
        rows = stop - start
        params = {pid: col[pid][start:stop] for pid in PROFORMA_IDS}
        context = {"rent_monthly": rent[start:stop], "cost_factor": cost_factor}
        for k in TYPOLOGIES:
            params[REDEVELOPMENT.units.key] = float(k)
            rlv = redevelopment_proforma(params, context).lines["residual_land_value"]
            rlv = np.broadcast_to(rlv, (rows, n))
            pay = premium[start:stop]
            threshold = np.minimum(rlv / (1.0 + pay), top_low)
            res = br.share_below_many(np.tile(counts, (rows, 1)), br.VALUE, threshold.reshape(-1))
            p = np.where(threshold > 0, res.value.reshape(rows, n), 0.0)
            fallback = np.where(
                np.isfinite(median_value)[None, :]
                & (median_value[None, :] * (1.0 + pay) <= rlv)
                & (rlv > 0),
                1.0,
                0.0,
            )
            shares[k][start:stop] = np.where(np.isnan(p), fallback, p)
            residual[k][start:stop] = rlv
    return shares, residual


# ---------------------------------------------------------------- the result


@dataclass(frozen=True)
class MiddleResult:
    """What :func:`missing_middle_funnel` returns.

    Attributes:
        scope: The scenario's scope.
        geoids: Tract codes, shape ``(tracts,)``.
        counties: County code per tract.
        stages: Stage name to draws x tracts arrays (see :data:`STAGES`).
        feasible_share: ``p`` (mix-weighted) per draw and tract.
        feasible_share_by_type: ``p_k`` per building type, draws x tracts.
        residual_land_value: Median over draws of ``RLV_k`` per tract, by building type ($).
        mix_weights: Mix weight per building type, per draw.
        fit_source: ``measured``, ``lot_proxy`` or ``parameter``.
        years: Calendar years counted.
        net_by_year: State net added units per draw and year, ``(draws, years)``.
        records: State-total records: one per stage, plus ``per_100k``.
        seed: Seed used.
    """

    scope: MiddleScope
    geoids: np.ndarray
    counties: np.ndarray
    stages: dict[str, np.ndarray]
    feasible_share: np.ndarray
    feasible_share_by_type: dict[int, np.ndarray]
    residual_land_value: dict[int, np.ndarray]
    mix_weights: dict[int, np.ndarray]
    fit_source: str
    years: tuple[int, ...]
    net_by_year: np.ndarray
    records: dict[str, Record]
    seed: int

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

    def county_records(self, stage: str = "net") -> dict[str, Record]:
        """Return one record per county for a stage, summed over its tracts per draw."""
        base = self.records[stage]
        out = {}
        for county in sorted(set(self.counties)):
            draws = self.stages[stage][:, self.counties == county].sum(axis=1)
            out[county] = rec.from_draws(
                f"{_label(stage, self.years)}, county {county}",
                draws,
                base.unit,
                expression=f"the sum over the county's tracts of {_expression(stage, self.years)}",
                assumption_ids=base.assumption_ids,
                source_ids=base.source_ids,
                tier=base.tier,
            )
        return out


_EXPRESSIONS = {
    "eligible": (
        "eligible lots = max(census single-family homes x lot-count correction "
        "x single-family-only zoned share - ADU overlap share x lots used by ADUs, 0)"
    ),
    "fit": (
        "lots large enough = eligible lots x f, where f is the measured share, else the "
        "share of homes whose block land per home x lot share is at least the smallest "
        "lot for a 2-6 unit building, else the ledger fit share"
    ),
    "feasible": (
        "feasible lots = lots large enough x sum over k = 2..6 of mix weight w_k x p_k, "
        "where p_k = share of the tract's owner home values at or below residual land "
        "value of a k-unit building / (1 + acquisition premium)"
    ),
    "redeveloped": (
        "lots redeveloped = feasible lots x min(yearly hazard h, yearly turnover) "
        "x sum over {years} of ramp(t)"
    ),
    "units": "new units = sum over k of lots redeveloped with k-unit buildings x k",
    "net_units": "net units = new units - lots redeveloped x homes demolished per lot (1)",
    "net": "net added units = net units x (1 - displacement share x)",
}


def _period(years: Sequence[int]) -> str:
    return f"{years[0]}-{years[-1]}" if years else ""


def _label(stage: str, years: Sequence[int]) -> str:
    return STAGE_NAMES[stage].format(years=_period(years))


def _expression(stage: str, years: Sequence[int]) -> str:
    return _EXPRESSIONS[stage].format(years=_period(years))


def _column(tracts: pd.DataFrame, name: str) -> np.ndarray:
    if name not in tracts:
        raise KeyError(f"tract table needs column '{name}'")
    return tracts[name].to_numpy(dtype=float)


def _stage_ids(fit_source: str, with_overlap: bool) -> dict[str, list[str]]:
    eligible = [LOT_PARAM, ZONED_PARAM, *([OVERLAP_PARAM] if with_overlap else [])]
    fit_ids = {"measured": [FIT_PARAM], "lot_proxy": [*PROXY_PARAMS, FIT_PARAM]}
    fit = [*eligible, *fit_ids.get(fit_source, [FIT_PARAM])]
    feasible = [
        *fit,
        *RANGE_PARAMS,
        *TYPOLOGIES.values(),
        *RENT_PARAMS,
        PREMIUM_PARAM,
        *PROFORMA_IDS,
    ]
    redeveloped = [*feasible, *TAKEUP_PARAMS, *YEAR_PARAMS]
    net_units = [*redeveloped, DEMOLISHED_PARAM]
    out = {
        "eligible": eligible,
        "fit": fit,
        "feasible": feasible,
        "redeveloped": redeveloped,
        "units": redeveloped,
        "net_units": net_units,
        "net": [*net_units, DISPLACEMENT_PARAM],
    }
    return {k: sorted(set(v)) for k, v in out.items()}


def _needed_ids(ledger: Ledger) -> list[str]:
    ids = [
        LOT_PARAM,
        ZONED_PARAM,
        OVERLAP_PARAM,
        FIT_PARAM,
        *PROXY_PARAMS,
        *RANGE_PARAMS,
        *TYPOLOGIES.values(),
        *RENT_PARAMS,
        PREMIUM_PARAM,
        *PROFORMA_IDS,
        *TAKEUP_PARAMS,
        DEMOLISHED_PARAM,
        DISPLACEMENT_PARAM,
    ]
    for pid in [*ids, *YEAR_PARAMS]:
        ledger[pid]  # raises a clear LedgerError if missing
    return list(dict.fromkeys(ids))


# ---------------------------------------------------------------- the funnel


def missing_middle_funnel(
    tracts: pd.DataFrame,
    ledger: Ledger,
    fit: pd.Series | Mapping[str, float] | None,
    scope: MiddleScope,
    *,
    draws: int = 2000,
    seed: int = 0,
    adu_lots: np.ndarray | None = None,
) -> MiddleResult:
    """Run the missing-middle funnel for every tract and every Monte Carlo draw.

    Args:
        tracts: Tract table in the state-pack schema (Testland column names):
            ``geoid``, ``county``, ``population``, ``units_1_detached`` (and
            ``units_1_attached`` if the scope reaches attached homes),
            ``median_gross_rent``, ``median_rent_b2``, ``median_home_value``
            and the ``value_*`` bracket columns. Optional: ``cost_factor`` and
            the lot-size proxy columns of :data:`LOT_COLUMNS`.
        ledger: The ledger, with the scenario's overrides already applied
            (``scenario_ledger``).
        fit: Measured share of eligible lots large enough, by tract code (Tier 2
            data), or ``None`` to use the lot-size proxy or the ledger. Tracts
            missing from it use the ledger value.
        scope: What the scenario reaches (:func:`scope_from_scenario`).
        draws: Number of Monte Carlo draws.
        seed: Seed; the same seed gives the same result.
        adu_lots: Optional overlap hook: lots already used by ADUs, shape
            ``(draws, tracts)`` or ``(tracts,)``. A share of them
            (``missing_middle.adu_lot_overlap_share``) leaves eligible lots.

    Returns:
        A :class:`MiddleResult`.

    Raises:
        KeyError: A tract column is missing.
        LedgerError: A ledger parameter is missing.
        ValueError: ``adu_lots`` has the wrong shape, or the mix weights are all zero.
    """
    ids = _needed_ids(ledger)
    v = sample(ledger, n=draws, seed=seed, ids=ids)
    col = {k: a[:, None] for k, a in v.items()}  # (draws, 1) columns
    n_tracts = len(tracts)
    first_year = int(ledger[YEAR_PARAMS[0]].central)
    horizon = int(ledger[YEAR_PARAMS[1]].central)
    years = tuple(range(first_year, first_year + horizon))
    counties = tracts["county"].astype(str).to_numpy()

    # 1. Eligible lots.
    homes = sum(np.clip(_column(tracts, HOME_COLUMNS[t]), 0, None) for t in scope.building_types)
    eligible = homes[None, :] * col[LOT_PARAM] * col[ZONED_PARAM]
    if adu_lots is not None:
        used = np.asarray(adu_lots, dtype=float)
        if used.shape not in ((n_tracts,), (draws, n_tracts)):
            raise ValueError(f"adu_lots must have shape ({n_tracts},) or ({draws}, {n_tracts})")
        eligible = np.maximum(eligible - col[OVERLAP_PARAM] * np.nan_to_num(used), 0.0)

    # 2. Lots large enough.
    f, fit_source = _fit_share(tracts, fit, col, draws)
    fit_lots = eligible * f

    # 3. Feasible share, by building type.
    weights = _mix_weights(v, ledger)
    rent = _new_unit_rent(tracts, col)
    p_by_type, rlv = _feasible_shares(tracts, col, rent, draws)
    feasible_by_type = {k: fit_lots * weights[k][:, None] * p_by_type[k] for k in TYPOLOGIES}
    feasible = sum(feasible_by_type.values())
    p = sum(weights[k][:, None] * p_by_type[k] for k in TYPOLOGIES)

    # 4. Take-up, bounded by turnover, with the ramp.
    ramp = ramp_sum(
        v["missing_middle.ramp_first_year_share"], v["missing_middle.ramp_years_to_full"], horizon
    )
    hazard = np.minimum(
        col["missing_middle.redevelopment_hazard_annual"], col["missing_middle.sf_turnover_annual"]
    )
    over_period = np.minimum(hazard * ramp.sum(axis=1)[:, None], 1.0)  # (draws, 1)
    redeveloped_by_type = {k: lots * over_period for k, lots in feasible_by_type.items()}
    redeveloped = sum(redeveloped_by_type.values())

    # 5. Units, net of the demolished home.
    units = sum(lots * float(k) for k, lots in redeveloped_by_type.items())
    net_units = units - redeveloped * col[DEMOLISHED_PARAM]

    # 6. Displacement.
    net = net_units * (1.0 - col[DISPLACEMENT_PARAM])
    with np.errstate(divide="ignore", invalid="ignore"):
        year_share = np.nan_to_num(ramp / ramp.sum(axis=1, keepdims=True))
    net_by_year = net.sum(axis=1)[:, None] * year_share

    stages = {
        "eligible": eligible,
        "fit": fit_lots,
        "feasible": feasible,
        "redeveloped": redeveloped,
        "units": units,
        "net_units": net_units,
        "net": net,
    }
    population = float(np.nansum(_column(tracts, "population")))
    records = _state_records(
        stages, ledger, scope, fit_source, adu_lots is not None, population, years
    )
    return MiddleResult(
        scope=scope,
        geoids=tracts["geoid"].astype(str).to_numpy(),
        counties=counties,
        stages=stages,
        feasible_share=p,
        feasible_share_by_type=p_by_type,
        residual_land_value={k: np.median(a, axis=0) for k, a in rlv.items()},
        mix_weights=weights,
        fit_source=fit_source,
        years=years,
        net_by_year=net_by_year,
        records=records,
        seed=seed,
    )


def _state_records(
    stages: Mapping[str, np.ndarray],
    ledger: Ledger,
    scope: MiddleScope,
    fit_source: str,
    with_overlap: bool,
    population: float,
    years: Sequence[int],
) -> dict[str, Record]:
    horizon = len(years)
    stage_ids = _stage_ids(fit_source, with_overlap)
    out: dict[str, Record] = {}
    data_sources: list[str] = []
    types = ", ".join(scope.building_types)
    for stage in STAGES:
        data_sources += DATA_SOURCES.get(stage, ())
        if stage == "fit" and fit_source == "lot_proxy":
            data_sources.append(LOT_SOURCE)
        inputs = [rec.from_parameter(pid, ledger[pid]) for pid in stage_ids[stage]]
        out[stage] = rec.from_draws(
            f"{_label(stage, years)} ({scope.name}: {types})",
            stages[stage].sum(axis=1),
            STAGE_UNITS[stage],
            expression=f"the sum over tracts of {_expression(stage, years)}",
            inputs=inputs,
            source_ids=sorted(set(data_sources)),
            tier=0,
        )
    if population > 0:
        net = out["net"]
        out["per_100k"] = rec.from_draws(
            f"net added missing-middle units per 100,000 residents per year ({scope.name})",
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
