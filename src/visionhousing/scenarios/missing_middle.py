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
   lot must be sold first. The ramp is 0 before ``missing_middle.start_delay_years``.
5. **New units authorized** = lots redeveloped by type ``x k``: every unit in
   the new building needs a permit (the reform's gross authorizations on the lots).
6-7. **Events on the same lots in both arms.** Policy: new units authorized
   (stage 5) and homes demolished (lots redeveloped ``x d``,
   ``missing_middle.demolished_per_project``). Counterfactual (the baseline):
   with chance ``q`` (``missing_middle.baseline_replacement_probability``)
   the lot is torn down anyway, the same homes demolished, and rebuilt with
   ``r`` homes (``missing_middle.baseline_units_per_replacement``). One
   function, :func:`visionhousing.engine.outcome.site_account`, turns these
   four fields into both accounts:

   - **Added units authorized** (the headline, :data:`HEADLINE_STAGE`) =
     (policy new units ``-`` counterfactual new units) ``x (1 - x)``; ``x`` is
     the share that would have been permitted elsewhere anyway (displacement).
   - **Net added homes if completed** (:data:`NET_STOCK_STAGE`) = added units
     authorized ``-`` (policy demolitions ``-`` counterfactual demolitions).

   Records ``removed``, ``baseline_on_sites``, ``counterfactual_removed`` and
   ``demolitions_caused`` keep every field.

Worked cases: a fourplex replacing a house where the baseline would have
rebuilt one house (``q = 1``) is 3 added authorizations and +3 homes; where the
baseline would have left the house alone (``q = 0``) it is 4 and +3. An
already-permitted duplex gains nothing from a two-unit ceiling: its lot is not
in a single-family-only zone, so it is not eligible (both arms build the
duplex). Added units authorized are never below net added homes.

Years. The five-year total is split into years by the ramp
(``year share(t) = ramp(t) / sum of ramp``, :attr:`MiddleResult.year_share`),
the same in every tract of a draw, so the years add up to the total in every draw.

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
function only takes out what it is given. Under the package option ``market``
(project choice, ``engine.market``) the engine passes ``rivals`` instead: each
lot goes to the best of the prototypes, a conventional rebuild or an ADU, and
the funnel keeps the lot shares with and without each rival (``MiddleChoice``).

State floor by date (Vermont)
-----------------------------
Where a state law already guarantees some of what the reform allows, the
reform adds only the rest. Pass ``floor`` (:class:`MiddleFloor`, built by the
engine, :func:`visionhousing.engine.law_floor.middle_floor_for`): Vermont's
24 V.S.A. § 4412(1)(D) already makes a duplex a permitted use on every
year-round residential lot and up to four units where municipal water **and**
sewer serve. Under the current-law convention the dated versions (the text
effective January 1, 2028 adds an exception for districts that require more
than four units) apply in the same way in both arms, year by year. Then, in the
mix method:

- a ``k``-unit building counts only where ``k`` is above what is already
  allowed: on served lots 5 and 6, on unserved lots 3 to 6. The served share of
  a tract's lots is ``s x a + (1 - s) x b``, with ``s`` the pack's mapped sewer
  share (``infra_tract.sewer_share``), ``a`` = ``missing_middle.floor_water_share_in_sewer_area``
  and ``b`` = ``missing_middle.floor_served_share_no_sewer_record`` (unknown
  service becomes a likely range, never "no right");
- "already allowed" is the larger of the rights in force that year and the
  rights the baseline's permit history already reflects (since July 1, 2023),
  so production the baseline already holds is not counted twice;
- on a lot where the already-allowed design (a fourplex or duplex) also
  pencils, the bigger building adds only its extra homes (``k - m``);
- "freeze today's rules" (the version in force on the freeze date held
  throughout) is a separate record, ``floor_freeze_difference``, never a forecast arm.

The prototype method is not changed by the floor (the engine applies the floor
to the mix method only).

Files and purity
----------------
:func:`missing_middle_funnel` is pure: tables and ledger in, result out. Its
signature and result follow :func:`visionhousing.scenarios.adu.adu_funnel`, so
the engine registers it the same way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.engine import records as rec
from visionhousing.engine.outcome import site_account
from visionhousing.engine.records import Record
from visionhousing.features import brackets as br
from visionhousing.ledger.loader import Ledger
from visionhousing.ledger.sampling import sample
from visionhousing.ledger.scenarios import Scenario
from visionhousing.proforma import REDEVELOPMENT, redevelopment_proforma
from visionhousing.scenarios.adu import ramp_sum, year_shares
from visionhousing.scenarios.prototypes import (
    CONVENTIONAL_SIZE,
    PROTOTYPE_IDS,
    PROTOTYPES,
    conventional_bid,
    prototype_choice,
)

__all__ = [
    "BUILDING_TYPES",
    "FIT_SOURCES",
    "HEADLINE_STAGE",
    "HOME_COLUMNS",
    "LOT_COLUMNS",
    "NET_STOCK_STAGE",
    "SQ_M_PER_SQ_FT",
    "STAGES",
    "TYPOLOGIES",
    "MiddleChoice",
    "MiddleFloor",
    "MiddleResult",
    "MiddleRivals",
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

STAGES = ("eligible", "fit", "feasible", "redeveloped", "units", "authorized", "net")
"""Funnel stages, in order."""

HEADLINE_STAGE = "authorized"
"""The stage that is the headline: added units authorized by building permits."""

NET_STOCK_STAGE = "net"
"""The stage that is the net change in the housing stock (after demolition)."""

STAGE_NAMES = {
    "eligible": "eligible single-family lots in single-family-only zones",
    "fit": "lots large enough for a 2-6 unit building",
    "feasible": "lots where redevelopment pencils",
    "redeveloped": "lots redeveloped {years}",
    "units": "new units authorized in 2-6 unit buildings {years}",
    "authorized": "added units authorized by building permits {years}",
    "net": "net added homes after demolition {years}",
}

STAGE_UNITS = {
    "eligible": "lots",
    "fit": "lots",
    "feasible": "lots",
    "redeveloped": "lots",
    "units": "units",
    "authorized": "units",
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
DELAY_PARAM = "missing_middle.start_delay_years"
REPLACE_PARAM = "missing_middle.baseline_replacement_probability"
REPLACE_UNITS_PARAM = "missing_middle.baseline_units_per_replacement"
OPTIONAL_PARAMS = (DELAY_PARAM, REPLACE_PARAM, REPLACE_UNITS_PARAM)
"""Read when the ledger has them (delay 0, replacement chance 0 and one home otherwise)."""
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

METHODS = ("mix", "prototypes")
"""How the feasible step is worked out: the benchmark's ledger mix of building
types (``mix``), or the most profitable feasible prototype on each lot
(``prototypes``, the named variant ``missing_middle_prototype_choice``)."""

PROTOTYPE_EXPRESSIONS = {
    "fit": (
        "lots large enough = eligible lots x share of homes whose block land per home x lot "
        "share is at least the smallest duplex lot"
    ),
    "feasible": (
        "feasible lots = sum over lot-size bands (fits six / fits four / fits a duplex only) of "
        "eligible lots in the band x share of owner home values x (1 + acquisition premium) at "
        "or below the highest land bid among the prototypes that fit (land bid = residual land "
        "value - demolition - accessible design)"
    ),
    "units": (
        "new units authorized = sum over bands of lots redeveloped x homes of the prototype "
        "with the highest land bid there (2, 4 or 6)"
    ),
}
"""Stage formulas that change under the prototype method."""

CHOICE_EXPRESSIONS = {
    "feasible": (
        "feasible lots = eligible lots x ((1 - a) x f_conv + a x f_win), where a = share of "
        "eligible lots where an ADU also pencils, f_conv = share where the best prototype's "
        "land bid is above a conventional single-family rebuild's bid and at or above the lot "
        "price, f_win = the same with the price raised by the ADU's value to the owner "
        "(max(value-uplift share x home value - ADU total cost, 0))"
    ),
    "units": (
        "new units authorized = eligible lots x ((1 - a) x homes per lot under f_conv "
        "+ a x homes per lot under f_win) x min(h, turnover) x sum of ramp"
    ),
}
"""Stage formulas that change under project choice (``rivals``; ``engine.market``)."""


FLOOR_EXPRESSIONS = {
    "feasible": (
        "feasible lots = lots large enough x sum over k = 2..6 of mix weight w_k x p_k x n_k, "
        "where n_k = share of the lots where a k-unit building is newly allowed: served share "
        "x [k above the units already allowed with water and sewer] + (1 - served share) x "
        "[k above the units already allowed without], averaged over the years by their share "
        "of the five-year total; already allowed = the larger of the state floor in force "
        "that year and the rights the baseline history reflects"
    ),
    "units": (
        "new units authorized = sum over k of lots redeveloped with k-unit buildings x "
        "(k - m x share of those lots where the already-allowed m-unit design also pencils, "
        "min(p_m, p_k) / p_k)"
    ),
    "authorized": (
        "added units authorized = (new units authorized above the already-allowed design - "
        "baseline replacement chance q x baseline homes per replaced lot r x lots redeveloped "
        "where only the reform's design pencils) x (1 - displacement share x); on the lots "
        "where the already-allowed m-unit design also pencils the counterfactual is that "
        "building (m units, the same demolition), with no q x r rebuild on top"
    ),
    "net": (
        "net added homes if completed = added units authorized - (policy demolitions, lots "
        "redeveloped x homes demolished per lot d - counterfactual demolitions on the same "
        "lots: lots where the already-allowed design also pencils x d + q x the other lots "
        "redeveloped x d)"
    ),
}
"""Stage formulas that change when a dated state floor applies (``floor``)."""


@dataclass(frozen=True)
class MiddleFloor:
    """A dated state floor on the reform's lots (Vermont's § 4412(1)(D)).

    Attributes:
        schedule: The units already allowed by service class and year, a
            :class:`visionhousing.law.rights.MiddleFloorSchedule` (the engine
            builds it, :func:`visionhousing.engine.law_floor.middle_floor_for`;
            scenario modules never read the law files themselves).
        sewer_share: Per tract, the share of homes inside a mapped municipal
            sewer service area (``infra_tract.sewer_share``; 0 where no record).
        source_ids: Registry ids behind the schedule and the sewer map.
    """

    schedule: Any
    sewer_share: np.ndarray
    source_ids: tuple[str, ...] = ()


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
            p = np.where(threshold > 0, br.share_below_grid(counts, br.VALUE, threshold), 0.0)
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
class MiddleRivals:
    """Project choice (``engine.market``): the other uses competing for the same lots.

    Attributes:
        adu_share: Share of each tract's eligible lots where an ADU also pencils
            (ADU-exposed share x share where the ADU pays), draws x tracts.
        adu_uplift: The ADU's value-uplift share (value added / host home
            value), per draw.
        adu_cost: The ADU's total cost per lot ($), draws x tracts. On a lot
            with home value ``V`` the ADU is worth ``max(uplift x V - cost, 0)``
            to the owner; a 2-6 unit building wins such a lot only where its
            land bid is at least the lot price plus that.
        conventional: True to compare with a conventional single-family
            rebuild (``prototypes.conventional_bid``) on every lot.
    """

    adu_share: np.ndarray
    adu_uplift: np.ndarray
    adu_cost: np.ndarray
    conventional: bool = True


@dataclass(frozen=True)
class MiddleChoice:
    """Lot shares and units of the project-choice rerun (draws x tracts).

    Every share is per eligible lot. ``feasible_alone >= feasible_conventional
    >= win_share``.

    Attributes:
        feasible_alone: Share where the best prototype pencils alone.
        feasible_conventional: Share where it pencils and beats the conventional rebuild.
        win_share: Share where it pencils and beats the rebuild and an ADU.
        adu_share: The rivals' ADU share (as passed).
        authorized_alone: Added units authorized if no rival competed.
        authorized_conventional: Added units authorized with only the conventional rival.
        lots_to_conventional: Lots where the building pencils but a conventional
            rebuild bids more (and no ADU pencils), over the period's take-up.
        lots_to_adu: Lots where the building pencils but the ADU is worth more
            to the owner, over the period's take-up.
        redevelopment_rate: Share of chosen lots redeveloped within the
            window (``min(h, turnover) x sum of ramp``), shape ``(draws, 1)``.
    """

    feasible_alone: np.ndarray
    feasible_conventional: np.ndarray
    win_share: np.ndarray
    adu_share: np.ndarray
    authorized_alone: np.ndarray
    authorized_conventional: np.ndarray
    lots_to_conventional: np.ndarray
    lots_to_adu: np.ndarray
    redevelopment_rate: np.ndarray


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
        net_by_year: State added units authorized per draw and year, ``(draws, years)``.
        records: State-total records: one per stage, plus ``per_100k``,
            ``removed`` (homes demolished) and ``baseline_on_sites`` (baseline
            authorizations on the redeveloped lots).
        seed: Seed used.
        year_share: Each year's share of the five-year total per draw, ``(draws, years)``.
        removed: Homes demolished per draw and tract (lots redeveloped x d).
        baseline_on_sites: Baseline authorizations on the redeveloped lots per
            draw and tract (q x r x lots redeveloped; under a state floor, the
            already-allowed m-unit buildings on lots where that design also
            pencils, plus q x r on the other lots).
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
    year_share: np.ndarray | None = None
    removed: np.ndarray | None = None
    baseline_on_sites: np.ndarray | None = None
    kept_share: np.ndarray | None = None
    """Per draw, 1 - displacement share, shape ``(draws, 1)``."""
    choice: MiddleChoice | None = None
    """Project choice detail (only when the funnel ran with ``rivals``)."""
    work_shares: Mapping[str, np.ndarray] = field(default_factory=dict)
    """Work type to its share of each tract's units, per tract ``(tracts,)`` (they add
    to 1); lets the practical capacity screen split screened units by work type exactly."""
    work_types: Mapping[str, np.ndarray] = field(default_factory=dict)
    """Work type to its added units authorized per draw, state, ``(draws,)``: a new
    building on a detached lot is stand-alone new construction (``new_standalone``),
    on a semi-detached or row lot a new attached structure (``new_attached``). They
    add up to the headline in every draw."""

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
    "units": "new units authorized = sum over k of lots redeveloped with k-unit buildings x k",
    "authorized": (
        "added units authorized = (policy new units authorized - counterfactual new units on "
        "the same lots, baseline replacement chance q x baseline homes per replaced lot r x "
        "lots redeveloped) x (1 - displacement share x)"
    ),
    "net": (
        "net added homes if completed = added units authorized - (policy demolitions, lots "
        "redeveloped x homes demolished per lot d - counterfactual demolitions on the same "
        "lots, q x lots redeveloped x d)"
    ),
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
    redeveloped = [*feasible, *TAKEUP_PARAMS, DELAY_PARAM, *YEAR_PARAMS]
    authorized = [*redeveloped, REPLACE_PARAM, REPLACE_UNITS_PARAM, DISPLACEMENT_PARAM]
    out = {
        "eligible": eligible,
        "fit": fit,
        "feasible": feasible,
        "redeveloped": redeveloped,
        "units": redeveloped,
        "authorized": authorized,
        "net": [*authorized, DEMOLISHED_PARAM],
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
    ids += [pid for pid in OPTIONAL_PARAMS if pid in ledger]
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
    method: str = "mix",
    rivals: MiddleRivals | None = None,
    floor: MiddleFloor | None = None,
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
        method: ``"mix"`` (the benchmark: the ledger mix of building types) or
            ``"prototypes"`` (each lot takes the most profitable feasible of a
            duplex, fourplex and six-unit building; see
            :mod:`visionhousing.scenarios.prototypes`). The prototype
            comparison is reported as records under both methods whenever
            the ledger has its entries.
        rivals: Project choice only (``engine.market``; needs ``method=
            "prototypes"``): on each lot the best prototype must also beat a
            conventional single-family rebuild and, where an ADU pencils, the
            ADU's value to the owner. Feasible lots become ``(1 - a) x f_conv
            + a x f_win`` with ``a`` the ADU share, and the choice detail is
            kept (:class:`MiddleChoice`). ``None``: no rival (the benchmark).
        floor: A dated state floor on the reform's lots (:class:`MiddleFloor`;
            mix method only): only building sizes above what is already
            allowed count, net of the already-allowed design where it also
            pencils. ``None``: no state floor (every state but Vermont).

    Returns:
        A :class:`MiddleResult`.

    Raises:
        KeyError: A tract column is missing.
        LedgerError: A ledger parameter is missing.
        ValueError: ``adu_lots`` has the wrong shape, or the mix weights are all zero.
    """
    if method not in METHODS:
        raise ValueError(f"missing-middle method must be one of {METHODS}, got {method!r}")
    ids = _needed_ids(ledger)
    has_protos = all(pid in ledger for pid in PROTOTYPE_IDS)
    if method == "prototypes" and not has_protos:
        raise ValueError("the prototype method needs the prototype entries in the ledger")
    if rivals is not None and method != "prototypes":
        raise ValueError("project choice (rivals) needs the prototype method")
    if rivals is not None and adu_lots is not None:
        raise ValueError("project choice replaces the ADU-lot overlap rule; pass one of them")
    if floor is not None and method != "mix":
        raise ValueError("the state floor applies to the mix method only")
    floor_ids = tuple(floor.schedule.service_params.values()) if floor is not None else ()
    for pid in floor_ids:
        ledger[pid]  # raises a clear LedgerError if missing
    sampled = [*ids, *(PROTOTYPE_IDS if has_protos else ()), *floor_ids]
    if rivals is not None and rivals.conventional:
        sampled.append(CONVENTIONAL_SIZE)
    v = sample(ledger, n=draws, seed=seed, ids=sampled)
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
    ramp = ramp_sum(
        v["missing_middle.ramp_first_year_share"],
        v["missing_middle.ramp_years_to_full"],
        horizon,
        v.get(DELAY_PARAM, 0.0),
    )
    raw_by_type = {k: fit_lots * weights[k][:, None] * p_by_type[k] for k in TYPOLOGIES}
    floor_view = None
    if floor is not None:
        floor_view = _floor_view(floor, col, p_by_type, year_shares(ramp), n_tracts)
        legal = floor_view["current"][0]
        feasible_by_type = {k: raw_by_type[k] * legal[k] for k in TYPOLOGIES}
    else:
        feasible_by_type = raw_by_type
    feasible = sum(feasible_by_type.values())
    p = sum(weights[k][:, None] * p_by_type[k] for k in TYPOLOGIES)
    proto = prototype_choice(tracts, col, PROFORMA_IDS, draws) if has_protos else None
    rival_view = None
    if rivals is not None and proto is not None:
        rival_view = _rival_view(tracts, col, draws, rivals, proto)
    if method == "prototypes" and proto is not None:
        fit_lots = eligible * proto.fit[PROTOTYPES[0].key]
        feasible = eligible * (proto.feasible if rival_view is None else rival_view["feasible"])
        with np.errstate(divide="ignore", invalid="ignore"):
            p = np.where(fit_lots > 0, feasible / np.where(fit_lots > 0, fit_lots, 1.0), 0.0)

    # 4. Take-up, bounded by turnover, with the ramp.
    hazard = np.minimum(
        col["missing_middle.redevelopment_hazard_annual"], col["missing_middle.sf_turnover_annual"]
    )
    over_period = np.minimum(hazard * ramp.sum(axis=1)[:, None], 1.0)  # (draws, 1)
    redeveloped_by_type = {k: lots * over_period for k, lots in feasible_by_type.items()}
    redeveloped = sum(redeveloped_by_type.values())

    # 5. Units authorized in the new buildings.
    units = sum(lots * float(k) for k, lots in redeveloped_by_type.items())
    if floor_view is not None:
        alt = floor_view["current"][1]
        units = units - sum(raw_by_type[k] * over_period * alt[k] for k in TYPOLOGIES)
    if method == "prototypes" and proto is not None:
        redeveloped = feasible * over_period
        per_lot = proto.units if rival_view is None else rival_view["units"]
        units = eligible * per_lot * over_period

    # 6-7. Events on the redeveloped lots in both arms, then the gross and net accounts.
    # Policy: the new units and the homes demolished. Counterfactual (the baseline):
    # with chance q the lot is torn down anyway (the same homes demolished) and
    # rebuilt with r homes.
    # Under a state floor, on the lots where the already-allowed m-unit design also
    # pencils (alt lots) the counterfactual is that building: m units and the same
    # demolition, and no q x r rebuild on top. The m units are already taken off
    # ``units`` above, so those lots add only their demolition to the counterfactual.
    q = col.get(REPLACE_PARAM, np.zeros((draws, 1)))
    r = col.get(REPLACE_UNITS_PARAM, np.ones((draws, 1)))
    d = col[DEMOLISHED_PARAM]
    alt_lots = np.zeros_like(redeveloped)
    alt_units = np.zeros_like(redeveloped)
    if floor_view is not None:
        share = floor_view["alt_lots"]["current"]
        alt_lots = sum(raw_by_type[k] * over_period * share[k] for k in TYPOLOGIES)
        alt_units = sum(raw_by_type[k] * over_period * alt[k] for k in TYPOLOGIES)
    rebuild_lots = redeveloped - alt_lots
    baseline_on_sites = alt_units + rebuild_lots * q * r
    removed = redeveloped * d
    removed_anyway = (alt_lots + q * rebuild_lots) * d
    account = site_account(
        units + alt_units, removed, baseline_on_sites, removed_anyway, col[DISPLACEMENT_PARAM]
    )
    authorized = account.gross_additional
    net = account.net_if_completed
    frozen_authorized = None
    if floor is not None and _same_schedule(floor.schedule):
        frozen_authorized = authorized  # the same rights every year: no difference, exactly
    elif floor_view is not None:
        legal_f, alt_f = floor_view["frozen"]
        share_f = floor_view["alt_lots"]["frozen"]
        red_f = sum(raw_by_type[k] * legal_f[k] for k in TYPOLOGIES) * over_period
        policy_f = sum(raw_by_type[k] * over_period * legal_f[k] * k for k in TYPOLOGIES)
        alt_units_f = sum(raw_by_type[k] * over_period * alt_f[k] for k in TYPOLOGIES)
        alt_lots_f = sum(raw_by_type[k] * over_period * share_f[k] for k in TYPOLOGIES)
        rebuild_f = red_f - alt_lots_f
        frozen_authorized = site_account(
            policy_f,
            red_f * d,
            alt_units_f + rebuild_f * q * r,
            (alt_lots_f + q * rebuild_f) * d,
            col[DISPLACEMENT_PARAM],
        ).gross_additional
    choice = None
    if rival_view is not None and proto is not None:
        kept = 1.0 - col[DISPLACEMENT_PARAM]

        def added(f: np.ndarray, u: np.ndarray) -> np.ndarray:
            return eligible * over_period * (u - q * r * f) * kept

        a = rival_view["adu_share"]
        choice = MiddleChoice(
            feasible_alone=proto.feasible,
            feasible_conventional=rival_view["conv"].feasible,
            win_share=rival_view["win"].feasible,
            adu_share=a,
            authorized_alone=added(proto.feasible, proto.units),
            authorized_conventional=added(rival_view["conv"].feasible, rival_view["conv"].units),
            lots_to_conventional=eligible
            * (1.0 - a)
            * np.clip(proto.feasible - rival_view["conv"].feasible, 0.0, None)
            * over_period,
            lots_to_adu=eligible
            * a
            * np.clip(rival_view["conv"].feasible - rival_view["win"].feasible, 0.0, None)
            * over_period,
            redevelopment_rate=np.asarray(over_period, dtype=float),
        )
    year_share = year_shares(ramp)
    net_by_year = authorized.sum(axis=1)[:, None] * year_share

    stages = {
        "eligible": eligible,
        "fit": fit_lots,
        "feasible": feasible,
        "redeveloped": redeveloped,
        "units": units,
        "authorized": authorized,
        "net": net,
    }
    population = float(np.nansum(_column(tracts, "population")))
    expressions = PROTOTYPE_EXPRESSIONS if method == "prototypes" else None
    if floor is not None:
        expressions = FLOOR_EXPRESSIONS
    extra_ids: tuple[str, ...] = PROTOTYPE_IDS if method == "prototypes" else ()
    if choice is not None:
        expressions = {**PROTOTYPE_EXPRESSIONS, **CHOICE_EXPRESSIONS}
        extra_ids = (*extra_ids, *((CONVENTIONAL_SIZE,) if rivals and rivals.conventional else ()))
    records = _state_records(
        stages,
        ledger,
        scope,
        fit_source,
        adu_lots is not None,
        population,
        years,
        expressions=expressions,
        extra_ids=extra_ids,
        floor_ids=floor_ids,
        floor_sources=floor.source_ids if floor is not None else (),
    )
    if floor is not None and floor_view is not None and frozen_authorized is not None:
        records.update(
            _floor_records(
                floor, floor_view, raw_by_type, fit_lots, eligible, over_period,
                authorized, frozen_authorized, ledger, years, floor_ids, weights,
            )
        )  # fmt: skip
    on_floor = floor_view is not None
    records.update(
        _site_records(removed, baseline_on_sites, records, ledger, years, floor=on_floor)
    )
    records.update(
        _event_records(units, removed, removed_anyway, records, ledger, years, floor=on_floor)
    )
    if proto is not None:
        records.update(_prototype_records(proto, eligible, tracts, col, ledger, records))
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
        year_share=year_share,
        removed=removed,
        baseline_on_sites=baseline_on_sites,
        kept_share=np.asarray(1.0 - col[DISPLACEMENT_PARAM], dtype=float),
        choice=choice,
        work_types=_work_types(tracts, scope, authorized),
        work_shares=_work_shares(tracts, scope),
    )


def _rival_view(
    tracts: pd.DataFrame,
    col: Mapping[str, np.ndarray],
    draws: int,
    rivals: MiddleRivals,
    proto: Any,
) -> dict[str, Any]:
    """The prototype comparison against the rivals: feasible share and homes per lot.

    ``conv``: prototypes that beat the conventional rebuild; ``win``: that also
    beat the ADU's value to the owner. A lot where an ADU pencils (share ``a``)
    is won by the building only in ``win``; any other lot in ``conv``.
    """
    n = len(tracts)
    floor = conventional_bid(tracts, col, PROFORMA_IDS, draws) if rivals.conventional else None
    a = np.clip(np.broadcast_to(np.asarray(rivals.adu_share, dtype=float), (draws, n)), 0.0, 1.0)
    rival = (np.asarray(rivals.adu_uplift, dtype=float), np.asarray(rivals.adu_cost, dtype=float))
    # Keep only the two arrays read from each comparison (memory on a large state).
    full = prototype_choice(tracts, col, PROFORMA_IDS, draws, floor=floor)
    conv = SimpleNamespace(feasible=full.feasible, units=full.units)
    full = prototype_choice(tracts, col, PROFORMA_IDS, draws, rival=rival, floor=floor)
    win = SimpleNamespace(feasible=full.feasible, units=full.units)
    del full
    return {
        "conv": conv,
        "win": win,
        "adu_share": a,
        "feasible": (1.0 - a) * conv.feasible + a * win.feasible,
        "units": (1.0 - a) * conv.units + a * win.units,
    }


def _same_schedule(sched: Any) -> bool:
    """True when "freeze today's rules" gives the reform's lots the same comparators."""
    return all(
        sched.comparator(c) == sched.comparator(c, frozen=True) for c in ("served", "unserved")
    )


def _floor_view(
    floor: MiddleFloor,
    col: Mapping[str, np.ndarray],
    p_by_type: Mapping[int, np.ndarray],
    year_share: np.ndarray,
    n_tracts: int,
) -> dict[str, Any]:
    """Newly-allowed shares ``n_k`` and existing-alternative units, current law and frozen.

    For service class ``c`` (served, unserved) and year ``i`` the comparator
    ``m`` is the units already allowed (:meth:`MiddleFloorSchedule.comparator`).
    ``n_k = sum over i of year share x [k > m]``, mixed over the classes by the
    served share ``s x a + (1 - s) x b``. The alternative units per lot are
    ``sum over i of year share x [k > m] x m x min(p_m, p_k) / p_k`` (zero when
    ``m`` is one home: no already-allowed building to compare with).
    ``alt_lots`` holds the matching share of the lots (the same sum without the
    ``m``): on those lots the counterfactual builds the ``m``-unit design and
    demolishes the existing house; on the rest it keeps the ``q x r`` rebuild.
    """
    sched = floor.schedule
    sewer = np.clip(np.nan_to_num(np.asarray(floor.sewer_share, dtype=float)), 0.0, 1.0)
    if sewer.shape != (n_tracts,):
        raise ValueError(f"floor sewer_share must have shape ({n_tracts},)")
    pa, pb = (
        sched.service_params[k]
        for k in ("water_share_in_sewer_area", "served_share_no_sewer_record")
    )
    served = np.clip(sewer[None, :] * col[pa] + (1.0 - sewer[None, :]) * col[pb], 0.0, 1.0)
    ys = np.asarray(year_share, dtype=float)
    if ys.shape[1] != len(sched.years):
        raise ValueError("floor schedule years do not match the funnel's years")

    def one(
        frozen: bool,
    ) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray], dict[int, np.ndarray]]:
        legal: dict[int, np.ndarray] = {}
        alt: dict[int, np.ndarray] = {}
        alt_lots: dict[int, np.ndarray] = {}
        for k in TYPOLOGIES:
            parts_l, parts_a, parts_s = [], [], []
            for c in ("served", "unserved"):
                comp = sched.comparator(c, frozen)
                if any(m is None for m in comp):
                    raise ValueError(f"state floor for {c} lots is unknown; cannot apply it")
                lk = np.zeros((ys.shape[0], 1))
                ak = np.zeros((ys.shape[0], n_tracts))
                sk = np.zeros((ys.shape[0], n_tracts))
                for i, m in enumerate(comp):
                    if k <= int(m):
                        continue
                    lk = lk + ys[:, i : i + 1]
                    if int(m) in p_by_type:
                        pk, pm = p_by_type[k], p_by_type[int(m)]
                        with np.errstate(divide="ignore", invalid="ignore"):
                            both = np.where(
                                pk > 0, np.minimum(pm, pk) / np.where(pk > 0, pk, 1.0), 0.0
                            )
                        ak = ak + ys[:, i : i + 1] * float(m) * both
                        sk = sk + ys[:, i : i + 1] * both
                parts_l.append(lk)
                parts_a.append(ak)
                parts_s.append(sk)
            legal[k] = served * parts_l[0] + (1.0 - served) * parts_l[1]
            alt[k] = served * parts_a[0] + (1.0 - served) * parts_a[1]
            alt_lots[k] = served * parts_s[0] + (1.0 - served) * parts_s[1]
        return legal, alt, alt_lots

    cur, frz = one(False), one(True)
    return {
        "current": cur[:2],
        "frozen": frz[:2],
        "alt_lots": {"current": cur[2], "frozen": frz[2]},
        "served": served,
    }


def _floor_records(
    floor: MiddleFloor,
    view: Mapping[str, Any],
    raw_by_type: Mapping[int, np.ndarray],
    fit_lots: np.ndarray,
    eligible: np.ndarray,
    over_period: np.ndarray,
    authorized: np.ndarray,
    frozen_authorized: np.ndarray,
    ledger: Ledger,
    years: Sequence[int],
    floor_ids: Sequence[str],
    weights: Mapping[int, np.ndarray],
) -> dict[str, Record]:
    """Records of the state floor: served share, newly allowed share, alternative, freeze."""
    sched = floor.schedule
    period = _period(years)
    src = sorted(set(floor.source_ids))
    served_inputs = [rec.from_parameter(pid, ledger[pid]) for pid in floor_ids]
    legal, alt = view["current"]
    elig = eligible.sum(axis=1)
    fit = fit_lots.sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        served = np.where(
            elig > 0, (eligible * view["served"]).sum(axis=1) / np.where(elig > 0, elig, 1), 0.0
        )
        newly = sum(fit_lots * weights[k][:, None] * legal[k] for k in TYPOLOGIES).sum(axis=1)
        newly = np.where(fit > 0, newly / np.where(fit > 0, fit, 1), 0.0)
    alt_units = sum(raw_by_type[k] * over_period * alt[k] for k in TYPOLOGIES).sum(axis=1)
    mix_ids = sorted({*floor_ids, *TYPOLOGIES.values()})
    versions = ", ".join(dict.fromkeys(v or "none" for v in sched.versions))
    out = {
        "floor_served_share": rec.from_draws(
            f"share of eligible lots with municipal water and sewer ({sched.state} § 4412 floor)",
            served,
            "share",
            expression=(
                "the sum over tracts of eligible lots x (mapped sewer share x water share in "
                "sewer areas + (1 - mapped sewer share) x served share with no sewer record) / "
                "eligible lots"
            ),
            inputs=served_inputs,
            source_ids=src,
            tier=1,
        ),
        "floor_newly_legal_share": rec.from_draws(
            f"share of the building mix newly allowed by the reform above the state floor "
            f"({sched.state}, {period}, versions {versions})",
            newly,
            "share",
            expression=(
                "the sum over tracts and k of lots large enough x w_k x n_k / lots large "
                "enough, n_k = share of the lots where a k-unit building is newly allowed "
                "(above the larger of the state floor that year and the rights the baseline "
                "history reflects), averaged over the years"
            ),
            inputs=[rec.from_parameter(pid, ledger[pid]) for pid in mix_ids],
            source_ids=src,
            tier=1,
        ),
        "floor_existing_alternative": rec.from_draws(
            f"units not counted because the already-allowed design also pencils ({period})",
            alt_units,
            "units",
            expression=(
                "the sum over tracts and k of lots redeveloped with k-unit buildings where k is "
                "newly allowed x m x min(p_m, p_k) / p_k, m = units already allowed (a fourplex "
                "with water and sewer, a duplex without)"
            ),
            inputs=[rec.from_parameter(pid, ledger[pid]) for pid in mix_ids],
            source_ids=src,
            tier=1,
        ),
        "floor_freeze_difference": rec.from_draws(
            f'"Freeze today\'s rules" ({sched.frozen_version or "none"} held from '
            f"{sched.freeze_on}) minus current law: added units authorized {period} "
            "(a separate comparison, not a forecast arm)",
            frozen_authorized.sum(axis=1) - authorized.sum(axis=1),
            "units",
            expression=(
                "added units authorized with the state floor in force on the freeze date held "
                "in every year - added units authorized under current law (each version from "
                "its own date, the same in both forecast arms)"
            ),
            inputs=[rec.from_parameter(pid, ledger[pid]) for pid in mix_ids],
            source_ids=src,
            tier=1,
        ),
    }
    return out


def _site_records(
    removed: np.ndarray,
    baseline_on_sites: np.ndarray,
    records: Mapping[str, Record],
    ledger: Ledger,
    years: Sequence[int],
    floor: bool = False,
) -> dict[str, Record]:
    """State records of the homes demolished and the baseline's permits on the redeveloped lots."""
    period = _period(years)
    base = records["redeveloped"]
    ids = sorted({*base.assumption_ids, DEMOLISHED_PARAM})
    on_ids = sorted({*base.assumption_ids, *(p for p in OPTIONAL_PARAMS[1:] if p in ledger)})
    return {
        "removed": rec.from_draws(
            f"existing homes demolished on redeveloped lots {period}",
            removed.sum(axis=1),
            "units",
            expression="the sum over tracts of lots redeveloped x homes demolished per lot d",
            assumption_ids=ids,
            source_ids=base.source_ids,
            tier=0,
        ),
        "baseline_on_sites": rec.from_draws(
            f"baseline authorizations on the redeveloped lots {period}",
            baseline_on_sites.sum(axis=1),
            "units",
            expression=(
                "the sum over tracts of lots redeveloped x baseline replacement chance q "
                "x baseline homes per replaced lot r"
                + (
                    ", where on the lots where the already-allowed m-unit design also pencils "
                    "the baseline builds that design instead (m units, no q x r rebuild)"
                    if floor
                    else ""
                )
            ),
            assumption_ids=on_ids,
            source_ids=base.source_ids,
            tier=0,
        ),
    }


def _event_records(
    units: np.ndarray,
    removed: np.ndarray,
    removed_anyway: np.ndarray,
    records: Mapping[str, Record],
    ledger: Ledger,
    years: Sequence[int],
    floor: bool = False,
) -> dict[str, Record]:
    """The four event fields behind the gross and net accounts, as state records.

    Policy new units (the ``units`` stage) and policy demolitions (``removed``)
    are already records; this adds the counterfactual's demolitions on the
    same lots and the demolitions the reform causes (policy - counterfactual).
    The counterfactual's new units are ``baseline_on_sites``.
    """
    period = _period(years)
    base = records["removed"]
    ids = sorted({*base.assumption_ids, *(p for p in (REPLACE_PARAM,) if p in ledger)})
    return {
        "counterfactual_removed": rec.from_draws(
            f"existing homes the baseline would have demolished on the same lots {period}",
            removed_anyway.sum(axis=1),
            "units",
            expression="the sum over tracts of baseline replacement chance q x homes "
            "demolished on the redeveloped lots"
            + (
                ", where on the lots where the already-allowed m-unit design also pencils "
                "the baseline demolishes the same homes (chance 1, not q)"
                if floor
                else ""
            ),
            assumption_ids=ids,
            source_ids=base.source_ids,
            tier=0,
        ),
        "demolitions_caused": rec.from_draws(
            f"existing homes demolished because of the reform {period}",
            (removed - removed_anyway).sum(axis=1),
            "units",
            expression="the sum over tracts of homes demolished on the redeveloped lots - "
            "homes the baseline would have demolished there",
            assumption_ids=ids,
            source_ids=base.source_ids,
            tier=0,
        ),
    }


def _work_shares(tracts: pd.DataFrame, scope: MiddleScope) -> dict[str, np.ndarray]:
    """Each work type's share of a tract's units, per tract (see :func:`_work_types`)."""
    homes = {
        t: np.clip(np.nan_to_num(_column(tracts, HOME_COLUMNS[t])), 0, None)
        for t in scope.building_types
    }
    total = sum(homes.values())
    with np.errstate(divide="ignore", invalid="ignore"):
        det = (
            np.where(total > 0, homes["sf_detached"] / np.where(total > 0, total, 1.0), 1.0)
            if "sf_detached" in homes
            else np.zeros(len(tracts))
        )
    return {"new_standalone": det, "new_attached": 1.0 - det}


def _work_types(
    tracts: pd.DataFrame, scope: MiddleScope, authorized: np.ndarray
) -> dict[str, np.ndarray]:
    """Added units authorized by work type, state per draw (see :attr:`MiddleResult.work_types`).

    In each tract the units are split by the scope's single-family homes of
    each kind (detached lots give stand-alone buildings, semi-detached and row
    lots attached ones), so the two add up to the headline in every draw.
    """
    homes = {
        t: np.clip(np.nan_to_num(_column(tracts, HOME_COLUMNS[t])), 0, None)
        for t in scope.building_types
    }
    total = sum(homes.values())
    with np.errstate(divide="ignore", invalid="ignore"):
        det = (
            np.where(total > 0, homes["sf_detached"] / np.where(total > 0, total, 1.0), 1.0)
            if "sf_detached" in homes
            else np.zeros(len(tracts))
        )
    standalone = (authorized * det[None, :]).sum(axis=1)
    return {"new_standalone": standalone, "new_attached": authorized.sum(axis=1) - standalone}


def _prototype_records(
    proto: Any,
    eligible: np.ndarray,
    tracts: pd.DataFrame,
    col: Mapping[str, np.ndarray],
    ledger: Ledger,
    records: Mapping[str, Record],
) -> dict[str, Record]:
    """State records of the prototype comparison: fit, pencils alone, best choice, land bid."""
    base = records["feasible"]
    feasible_ids = set(base.assumption_ids)
    shared = {i for i in PROTOTYPE_IDS if not i.endswith(("_min_lot_sqft", "_unit_size_sqft"))}
    sources = base.source_ids
    lots_total = eligible.sum(axis=1)
    value = np.nan_to_num(_column(tracts, "median_home_value"), nan=0.0)[None, :]
    price = (eligible * value).sum(axis=1) * (1.0 + col[PREMIUM_PARAM][:, 0])
    with np.errstate(divide="ignore", invalid="ignore"):
        avg_price = np.where(lots_total > 0, price / np.where(lots_total > 0, lots_total, 1), 0.0)
    out: dict[str, Record] = {
        "proto_lot_price": rec.from_draws(
            "price of an eligible lot (median owner home value x (1 + acquisition premium)), "
            "eligible-lot-weighted",
            avg_price,
            "$/lot",
            expression=(
                "the sum over tracts of eligible lots x tract median home value (ACS) x (1 + "
                "acquisition premium) / eligible lots"
            ),
            assumption_ids=sorted({PREMIUM_PARAM, LOT_PARAM, ZONED_PARAM}),
            source_ids=("acs5_summary_file",),
            tier=0,
        )
    }
    for pr in PROTOTYPES:
        ids = sorted(feasible_ids | shared | {pr.min_lot, pr.unit_size})
        code = []
        if pr.building_code:
            code.append("building code (sprinklers, rated walls)")
        if pr.accessible:
            code.append("accessible ground-floor homes")
        if pr.commercial:
            code.append("commercial financing")
        terms = ", ".join(code) if code else "residential code and mortgage"
        bid_avg = (eligible * proto.bid[pr.key]).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            bid_avg = np.where(lots_total > 0, bid_avg / np.where(lots_total > 0, lots_total, 1), 0)
        for stat, arr, name, expression in (
            (
                "fit",
                proto.fit[pr.key],
                f"eligible lots big enough for a {pr.label} ({pr.units} homes)",
                f"the sum over tracts of eligible lots x share of homes whose block land per home "
                f"x lot share is at least the {pr.label}'s smallest lot",
            ),
            (
                "alone",
                proto.alone[pr.key],
                f"eligible lots where a {pr.label} pencils on its own ({terms})",
                f"the sum over tracts of eligible lots x fit share x share of owner home values "
                f"x (1 + acquisition premium) at or below the {pr.label}'s land bid",
            ),
            (
                "chosen",
                proto.chosen[pr.key],
                f"eligible lots where a {pr.label} is the most profitable feasible choice",
                "the sum over lot-size bands of eligible lots where the lot fits it, its land bid "
                "is the highest of the prototypes that fit, and the lot price is at or below it",
            ),
        ):
            out[f"proto_{pr.key}_{stat}"] = rec.from_draws(
                name,
                (eligible * arr).sum(axis=1),
                "lots",
                expression=expression,
                assumption_ids=ids,
                source_ids=sources,
                tier=0,
            )
        out[f"proto_{pr.key}_bid"] = rec.from_draws(
            f"land bid per lot for a {pr.label} ({terms}), eligible-lot-weighted",
            np.asarray(bid_avg, dtype=float),
            "$/lot",
            expression=(
                f"residual land value of a {pr.units}-home building (rent x 12 x (1 - operating "
                "share) / cap rate - (hard + code + soft + fees + parking) x (1 + required "
                "return)) - demolition - accessible design, averaged over eligible lots"
            ),
            assumption_ids=ids,
            source_ids=sources,
            tier=0,
        )
    return out


def _state_records(
    stages: Mapping[str, np.ndarray],
    ledger: Ledger,
    scope: MiddleScope,
    fit_source: str,
    with_overlap: bool,
    population: float,
    years: Sequence[int],
    *,
    expressions: Mapping[str, str] | None = None,
    extra_ids: Sequence[str] = (),
    floor_ids: Sequence[str] = (),
    floor_sources: Sequence[str] = (),
) -> dict[str, Record]:
    horizon = len(years)
    stage_ids = _stage_ids(fit_source, with_overlap)
    if extra_ids:
        later = ("fit", "feasible", "redeveloped", "units", "authorized", "net")
        stage_ids = {k: sorted({*v, *extra_ids}) if k in later else v for k, v in stage_ids.items()}
    floor_stages = ("feasible", "redeveloped", "units", "authorized", "net")
    if floor_ids:
        stage_ids = {
            k: sorted({*v, *floor_ids}) if k in floor_stages else v for k, v in stage_ids.items()
        }
    out: dict[str, Record] = {}
    data_sources: list[str] = []
    types = ", ".join(scope.building_types)
    for stage in STAGES:
        data_sources += DATA_SOURCES.get(stage, ())
        if stage == "fit" and fit_source == "lot_proxy":
            data_sources.append(LOT_SOURCE)
        if stage == "feasible":
            data_sources += list(floor_sources)
        inputs = [rec.from_parameter(pid, ledger[pid]) for pid in stage_ids[stage] if pid in ledger]
        expression = (expressions or {}).get(stage) or _expression(stage, years)
        out[stage] = rec.from_draws(
            f"{_label(stage, years)} ({scope.name}: {types})",
            stages[stage].sum(axis=1),
            STAGE_UNITS[stage],
            expression=f"the sum over tracts of {expression}",
            inputs=inputs,
            source_ids=sorted(set(data_sources)),
            tier=0,
        )
    if population > 0:
        net = out["authorized"]
        out["per_100k"] = rec.from_draws(
            f"added missing-middle units authorized per 100,000 residents per year ({scope.name})",
            stages["authorized"].sum(axis=1) / horizon / population * 100_000,
            "permits per 100k people per year",
            expression=(
                f"added units authorized / {horizon} years / residents ({population:,.0f}) "
                "x 100,000"
            ),
            assumption_ids=net.assumption_ids,
            source_ids=net.source_ids,
            tier=0,
        )
    return out
