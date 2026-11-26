"""Missing-middle prototypes: duplex, fourplex and six-unit building, compared lot by lot.

Plain-language summary
----------------------
The missing-middle benchmark allows 2 to 6 homes on a single-family lot. A
builder does not build "2 to 6 homes"; it builds one building. This module
compares three prototypes on the same lots and the same Monte Carlo draws:

=========== ====== ============================================================
prototype   homes  what changes with size
=========== ====== ============================================================
duplex      2      residential code (IRC), residential mortgage, 3-bedroom homes
fourplex    4      building code (IBC): sprinklers and rated walls; federal
                   accessible design for ground-floor homes (4+ homes)
six-unit    6      the same, plus commercial financing (5+ homes): a higher
                   required return
=========== ====== ============================================================

For each prototype ``k`` (one draw, one tract):

1. **Lot size.** The share of lots big enough for ``k``: the block lot-size
   proxy (tract columns ``lot_m2_per_unit_p25/p50/p75``) against the
   prototype's smallest lot (ledger ``missing_middle.prototype_<k>_min_lot_sqft``),
   as in the benchmark fit step. Bigger buildings need bigger lots, so
   ``fit(six) <= fit(four) <= fit(duplex)``.
2. **Land bid.** The shared redevelopment pro forma
   (:func:`visionhousing.proforma.redevelopment_proforma`, income basis) with
   the prototype's homes, floor area per home and rent, its construction cost
   (``missing_middle.hard_cost_per_sqft`` plus, above two homes,
   ``missing_middle.code_cost_ibc_per_sqft``) and its required return
   (``missing_middle.developer_margin`` plus, at five or more homes,
   ``missing_middle.commercial_finance_margin_add``). Its residual land value,
   minus demolition (``missing_middle.demolition_cost_per_lot``) and, at four
   or more homes, accessible design for the ground-floor third of the homes
   (``missing_middle.accessibility_cost_per_ground_unit``), is the most a
   builder of that prototype could pay for the lot: the **land bid**.
3. **Pencils alone.** A lot pencils for ``k`` when it is big enough and its
   price (owner home value x (1 + acquisition premium), from the tract's ACS
   value brackets) is at or below the land bid. The share of lots where each
   prototype pencils is a record, so a reader sees why a duplex pencils where
   a six-unit building does not (smaller lot, residential code and mortgage).
4. **Best choice.** Lots fall into size bands: big enough for six, for four
   but not six, for a duplex only. In each band the builder picks the
   prototype with the highest land bid among those that fit; the lot is
   feasible when its price is at or below that bid. This is the "most
   profitable feasible prototype per lot and draw": the price of the lot is
   the same whatever is built, so the highest bid is the highest profit.

The rent of a home: duplex homes rent like the tract's three-bedroom median
(``median_rent_b3``; where blank, the two-bedroom median x
``missing_middle.rent_ratio_3br_to_2br``); fourplex and six-unit homes like
the two-bedroom median. Both x ``missing_middle.new_unit_rent_premium``. Where
a bedroom median is blank, the tract's median gross rent x
``missing_middle.rent_ratio_2br`` stands in for the two-bedroom median.

The benchmark preset keeps its own mix of building types; this comparison is
records beside it, and the headline of the named variant
``missing_middle_prototype_choice`` (``assumptions/variants.yaml``).

Project choice (package option ``market``, ``engine.market``) adds two
optional tests to step 4: a rival use's value to the owner (an ADU: value-uplift
share x home value - its cost) raises the price the building must cover, and a
conventional single-family rebuild's bid (:func:`conventional_bid`) is a floor
the building must beat. Without them the comparison is unchanged.

:func:`prototype_choice` is pure: tables and draws in, arrays out.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
import pandas as pd

from visionhousing.features import brackets as br
from visionhousing.proforma import REDEVELOPMENT, redevelopment_proforma

__all__ = [
    "CONVENTIONAL_SIZE",
    "PROTOTYPES",
    "PROTOTYPE_IDS",
    "Prototype",
    "PrototypeResult",
    "conventional_bid",
    "prototype_choice",
]

SQ_M_PER_SQ_FT = 0.09290304
LOT_COLUMNS = ("lot_m2_per_unit_p25", "lot_m2_per_unit_p50", "lot_m2_per_unit_p75")
_CHUNK_ROWS = 200_000

IBC_COST = "missing_middle.code_cost_ibc_per_sqft"
ACCESS_COST = "missing_middle.accessibility_cost_per_ground_unit"
FINANCE_ADD = "missing_middle.commercial_finance_margin_add"
DEMOLITION = "missing_middle.demolition_cost_per_lot"
RATIO_3BR = "missing_middle.rent_ratio_3br_to_2br"
CONVENTIONAL_SIZE = "market.conventional_home_size_sqft"
"""Ledger id of the floor area of a conventional single-family rebuild (project choice only)."""
STOREYS = 3.0
"""Storeys of every prototype (the benchmark's limit); a third of its homes are on the ground."""

IBC_FROM_UNITS = 3
"""Homes per building from which the building code (not the residential code) applies."""
ACCESS_FROM_UNITS = 4
"""Homes per building from which federal accessible design applies to ground-floor homes."""
COMMERCIAL_FROM_UNITS = 5
"""Homes per building from which a commercial (multifamily) loan is needed."""


@dataclass(frozen=True)
class Prototype:
    """One building prototype.

    Attributes:
        key: Short name (``duplex``, ``fourplex``, ``sixplex``).
        label: Plain name.
        units: Homes in the building.
        min_lot: Ledger id of its smallest lot (sq ft).
        unit_size: Ledger id of its floor area per home (sq ft).
        bedrooms: ``3`` (rents like a three-bedroom) or ``2``.
    """

    key: str
    label: str
    units: int
    min_lot: str
    unit_size: str
    bedrooms: int

    @property
    def building_code(self) -> bool:
        """True when the building code applies (three or more homes)."""
        return self.units >= IBC_FROM_UNITS

    @property
    def accessible(self) -> bool:
        """True when ground-floor homes must meet federal accessible design."""
        return self.units >= ACCESS_FROM_UNITS

    @property
    def commercial(self) -> bool:
        """True when a commercial loan is needed."""
        return self.units >= COMMERCIAL_FROM_UNITS


PROTOTYPES: tuple[Prototype, ...] = (
    Prototype(
        "duplex",
        "duplex",
        2,
        "missing_middle.prototype_duplex_min_lot_sqft",
        "missing_middle.prototype_duplex_unit_size_sqft",
        3,
    ),
    Prototype(
        "fourplex",
        "fourplex",
        4,
        "missing_middle.prototype_fourplex_min_lot_sqft",
        "missing_middle.prototype_fourplex_unit_size_sqft",
        2,
    ),
    Prototype(
        "sixplex",
        "six-unit building",
        6,
        "missing_middle.prototype_sixplex_min_lot_sqft",
        "missing_middle.prototype_sixplex_unit_size_sqft",
        2,
    ),
)
"""The three prototypes, smallest first."""

SHARED_IDS = (IBC_COST, ACCESS_COST, FINANCE_ADD, DEMOLITION, RATIO_3BR)
PROTOTYPE_IDS: tuple[str, ...] = (
    *SHARED_IDS,
    *(p.min_lot for p in PROTOTYPES),
    *(p.unit_size for p in PROTOTYPES),
)
"""Ledger ids read only by the prototype comparison."""


@dataclass(frozen=True)
class PrototypeResult:
    """What :func:`prototype_choice` returns (arrays are draws x tracts).

    Attributes:
        fit: Prototype key to the share of eligible lots big enough for it.
        bid: Prototype key to its land bid per lot ($).
        alone: Prototype key to the share of eligible lots where it pencils alone.
        chosen: Prototype key to the share of eligible lots where it is the best
            feasible choice (the shares add up to :attr:`feasible`).
        feasible: Share of eligible lots where at least one fitting prototype pencils.
        units: Homes per eligible lot under the best choice (``sum over k of
            chosen_k x k``).
        lot_source: ``lot_proxy`` or ``parameter``.
    """

    fit: dict[str, np.ndarray]
    bid: dict[str, np.ndarray]
    alone: dict[str, np.ndarray]
    chosen: dict[str, np.ndarray]
    feasible: np.ndarray
    units: np.ndarray
    lot_source: str


def _col(tracts: pd.DataFrame, name: str) -> np.ndarray:
    if name not in tracts:
        return np.full(len(tracts), np.nan)
    return tracts[name].to_numpy(dtype=float)


def _fit_shares(
    tracts: pd.DataFrame, col: Mapping[str, np.ndarray], draws: int
) -> tuple[dict[str, np.ndarray], str]:
    """Share of lots big enough for each prototype (lot proxy), made monotone in size."""
    from visionhousing.scenarios.missing_middle import share_at_least

    n = len(tracts)
    fallback = np.broadcast_to(col["missing_middle.fit_share"], (draws, n))
    out: dict[str, np.ndarray] = {}
    if all(c in tracts for c in LOT_COLUMNS):
        q = [tracts[c].to_numpy(dtype=float)[None, :] for c in LOT_COLUMNS]
        for p in PROTOTYPES:
            needed = col[p.min_lot] * SQ_M_PER_SQ_FT / col["missing_middle.lot_proxy_net_share"]
            share = share_at_least(*q, needed)
            out[p.key] = np.where(np.isnan(share), fallback, share)
        source = "lot_proxy"
    else:
        for p in PROTOTYPES:
            out[p.key] = np.array(fallback)
        source = "parameter"
    # A bigger building never fits on more lots than a smaller one.
    keys = [p.key for p in PROTOTYPES]
    for small, big in pairwise(keys):
        out[big] = np.minimum(out[big], out[small])
    return out, source


def _rents(tracts: pd.DataFrame, col: Mapping[str, np.ndarray]) -> dict[int, np.ndarray]:
    """Monthly rent of a new home by bedrooms (2 or 3), draws x tracts."""
    b2 = _col(tracts, "median_rent_b2")[None, :]
    b3 = _col(tracts, "median_rent_b3")[None, :]
    gross = _col(tracts, "median_gross_rent")[None, :]
    two = np.where(np.isfinite(b2) & (b2 > 0), b2, gross * col["missing_middle.rent_ratio_2br"])
    three = np.where(np.isfinite(b3) & (b3 > 0), b3, two * col[RATIO_3BR])
    premium = col["missing_middle.new_unit_rent_premium"]
    return {
        2: np.nan_to_num(two * premium, nan=0.0),
        3: np.nan_to_num(three * premium, nan=0.0),
    }


def _bids(
    tracts: pd.DataFrame,
    col: Mapping[str, np.ndarray],
    proforma_ids: tuple[str, ...],
    draws: int,
) -> dict[str, np.ndarray]:
    """Land bid per lot of each prototype, draws x tracts ($)."""
    n = len(tracts)
    rents = _rents(tracts, col)
    cost_factor = (
        tracts["cost_factor"].to_numpy(dtype=float)[None, :] if "cost_factor" in tracts else 1.0
    )
    out: dict[str, np.ndarray] = {}
    for p in PROTOTYPES:
        params = {pid: col[pid] for pid in proforma_ids}
        params[REDEVELOPMENT.units.key] = float(p.units)
        params[REDEVELOPMENT.unit_size.key] = col[p.unit_size]
        params[REDEVELOPMENT.cost_psf.key] = col[REDEVELOPMENT.cost_psf.key] + (
            col[IBC_COST] if p.building_code else 0.0
        )
        params[REDEVELOPMENT.margin.key] = col[REDEVELOPMENT.margin.key] + (
            col[FINANCE_ADD] if p.commercial else 0.0
        )
        context = {"rent_monthly": rents[p.bedrooms], "cost_factor": cost_factor}
        rlv = redevelopment_proforma(params, context).lines["residual_land_value"]
        access = col[ACCESS_COST] * (p.units / STOREYS) if p.accessible else 0.0
        out[p.key] = np.broadcast_to(rlv - col[DEMOLITION] - access, (draws, n)).astype(float)
    return out


def _share_priced_below(tracts: pd.DataFrame, bid: np.ndarray, premium: np.ndarray) -> np.ndarray:
    """Share of the tract's owner home values with value x (1 + premium) <= bid (draws x tracts)."""
    draws, n = bid.shape
    counts = tracts[list(br.VALUE.columns)].to_numpy(dtype=float)
    top_low = float(br.VALUE.lows[-1])
    median_value = _col(tracts, "median_home_value")
    out = np.empty((draws, n))
    step = max(1, _CHUNK_ROWS // max(n, 1))
    for start in range(0, draws, step):
        stop = min(start + step, draws)
        pay = premium[start:stop]
        threshold = np.minimum(bid[start:stop] / (1.0 + pay), top_low)
        p = np.where(threshold > 0, br.share_below_grid(counts, br.VALUE, threshold), 0.0)
        fallback = np.where(
            np.isfinite(median_value)[None, :]
            & (median_value[None, :] * (1.0 + pay) <= bid[start:stop])
            & (bid[start:stop] > 0),
            1.0,
            0.0,
        )
        out[start:stop] = np.where(np.isnan(p), fallback, p)
    return out


def conventional_bid(
    tracts: pd.DataFrame,
    col: Mapping[str, np.ndarray],
    proforma_ids: tuple[str, ...],
    draws: int,
) -> np.ndarray:
    """Land bid per lot of a conventional single-family rebuild, draws x tracts ($).

    The same redevelopment pro forma with one home of
    ``market.conventional_home_size_sqft``, renting like the tract's
    three-bedroom home, residential code and mortgage (no building-code or
    commercial-finance add-on), minus demolition. Used only by project choice
    (``engine.market``): a builder who can put one new house on the lot bids
    this much, so a 2-6 unit building wins the lot only where its own bid is higher.
    """
    n = len(tracts)
    rents = _rents(tracts, col)
    cost_factor = (
        tracts["cost_factor"].to_numpy(dtype=float)[None, :] if "cost_factor" in tracts else 1.0
    )
    params = {pid: col[pid] for pid in proforma_ids}
    params[REDEVELOPMENT.units.key] = 1.0
    params[REDEVELOPMENT.unit_size.key] = col[CONVENTIONAL_SIZE]
    context = {"rent_monthly": rents[3], "cost_factor": cost_factor}
    rlv = redevelopment_proforma(params, context).lines["residual_land_value"]
    return np.broadcast_to(rlv - col[DEMOLITION], (draws, n)).astype(float)


def prototype_choice(
    tracts: pd.DataFrame,
    col: Mapping[str, np.ndarray],
    proforma_ids: tuple[str, ...],
    draws: int,
    *,
    rival: tuple[np.ndarray, np.ndarray] | None = None,
    floor: np.ndarray | None = None,
) -> PrototypeResult:
    """Compare the three prototypes on every tract and draw.

    Args:
        tracts: Tract table (state-pack schema): rent medians, ``median_home_value``,
            the ``value_*`` brackets; optional lot-size proxy columns and ``cost_factor``.
        col: Ledger id to draws as ``(draws, 1)`` columns: the redevelopment
            pro forma's ids, :data:`PROTOTYPE_IDS`, ``missing_middle.fit_share``,
            ``missing_middle.lot_proxy_net_share``, ``missing_middle.rent_ratio_2br``,
            ``missing_middle.new_unit_rent_premium`` and
            ``missing_middle.acquisition_premium``.
        proforma_ids: Ledger ids the redevelopment pro forma reads.
        draws: Number of draws.
        rival: Project choice only (``engine.market``): ``(u, C)``, a rival
            use worth ``max(u x home value - C, 0)`` to the owner on each lot
            (the ADU's value test: value-uplift share x home value - the ADU's
            total cost), each broadcastable to draws x tracts. A lot is
            feasible only where the best bid is at least price + that value,
            i.e. home value <= min(bid / (1 + premium), (bid + C) / (1 +
            premium + u)). ``None``: no rival.
        floor: Project choice only: the bid of a conventional rebuild, draws x
            tracts. Where the best prototype's bid is not above it, the
            conventional rebuild wins the lot and no prototype is chosen.
            ``None``: no floor.

    Returns:
        The comparison (:class:`PrototypeResult`). With ``rival`` and ``floor``
        both ``None`` the result is exactly the benchmark comparison.
    """
    fit, source = _fit_shares(tracts, col, draws)
    bid = _bids(tracts, col, proforma_ids, draws)
    premium = col["missing_middle.acquisition_premium"]
    alone = {
        p.key: fit[p.key] * _share_priced_below(tracts, bid[p.key], premium) for p in PROTOTYPES
    }

    keys = [p.key for p in PROTOTYPES]
    units = {p.key: float(p.units) for p in PROTOTYPES}
    # Size bands: lots that fit prototypes keys[:j+1] but not keys[j+1].
    chosen = {k: np.zeros_like(fit[k]) for k in keys}
    feasible = np.zeros_like(fit[keys[0]])
    per_lot_units = np.zeros_like(feasible)
    for j, k in enumerate(keys):
        nxt = fit[keys[j + 1]] if j + 1 < len(keys) else np.zeros_like(fit[k])
        band = np.clip(fit[k] - nxt, 0.0, None)
        allowed = keys[: j + 1]
        stack = np.stack([bid[a] for a in allowed])  # (allowed, draws, tracts)
        best = stack.argmax(axis=0)
        best_bid = np.take_along_axis(stack, best[None], axis=0)[0]
        test_bid = best_bid
        if rival is not None:
            u, cost = (np.asarray(x, dtype=float) for x in rival)
            # The same threshold on home value, written as a bid over (1 + premium).
            test_bid = np.minimum(
                best_bid, (1.0 + premium) * (best_bid + cost) / (1.0 + premium + u)
            )
        share = band * _share_priced_below(tracts, test_bid, premium)
        if floor is not None:
            share = np.where(best_bid > np.asarray(floor, dtype=float), share, 0.0)
        feasible = feasible + share
        for i, a in enumerate(allowed):
            pick = np.where(best == i, share, 0.0)
            chosen[a] = chosen[a] + pick
            per_lot_units = per_lot_units + pick * units[a]
    return PrototypeResult(
        fit=fit,
        bid=bid,
        alone=alone,
        chosen=chosen,
        feasible=feasible,
        units=per_lot_units,
        lot_source=source,
    )
