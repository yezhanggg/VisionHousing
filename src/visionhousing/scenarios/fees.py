"""Benchmark reform 5: cut development fees by half, and the shared cost-saving math.

Plain-language summary
----------------------
A fee cut does not make new building types legal. It makes homes that are
already legal a little cheaper to build, so a few more of them get built. The
model calls this **Channel B** (the cost channel) and writes it as one line::

    added units = baseline units x e x (dC / V) x k

- ``baseline units``: permitted units in 2027-2031 with no reform, by county and
  structure type (1-unit, 2-unit, 3-4-unit and 5+-unit buildings).
- ``dC``: cost saved per unit. Benchmark: $50,000 of fees x a 50% cut = $25,000.
- ``V``: value of a new unit in that county and building type. For 1-unit
  buildings it is the county median home value; for rented building types it is
  the county median gross rent turned into a building value (rent x 12 x the
  share kept after running costs, divided by the cap rate). Both get a
  new-construction premium, because new homes are worth more than the median
  existing home. Medians are read from the ACS bracket counts summed over the
  county's tracts.
- ``e`` (epsilon): the medium-run supply elasticity, the percent change in
  permits for a 1% change in the cost-to-value margin.
- ``k``: the share of the saving that reaches the decision to build. The rest
  is captured by landowners as higher land prices. Nobody knows ``k`` well, so
  its range is the widest of the fee inputs and it leads the fee tornado.

Every input is drawn from the Assumption Ledger at once for all counties,
building types and Monte Carlo draws (numpy arrays of shape counties x types x
draws). The result is a P10 / P50 / P90 record per county and for the state,
with the formula and the ledger ids behind it.

The pro-forma cross-check
-------------------------
The same saving is also run through the development pro formas
(``visionhousing.proforma``). For each county and building type the pro forma
gives a break-even: the rent (or, for 1-unit homes, the sale value) a new unit
needs to cover construction, soft costs, fees, parking, the builder's return and
land. The share of the county's homes or rents above that break-even is the
share of sites where a project pencils (from ACS brackets; the open top bracket
follows a Pareto tail with a ledger shape value). The saving (times the same
``k``) lowers the break-even, so more sites pencil. The relative rise in that
share, times baseline units, is the cross-check estimate. It is reported next to
Channel B, not added to it.

The second view
---------------
``view="state"`` replaces the $50,000 benchmark with an assumed Pennsylvania
fee level (``fees.pa_assumed_fee_per_unit_judgement``). That parameter is a
draft judgement with a wide range until per-unit impact, tap and permit fees are
sourced; the result says so.

Shared helpers
--------------
The Channel B formula, the value of a new unit, the market brackets and the
cross-check are also used by the parking reform (``scenarios.parking``), so they
live here and are public.

All functions are pure: data frames and the ledger go in, records come out.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.baseline.panel import STRUCTURE_LABELS, STRUCTURE_TYPES
from visionhousing.baseline.project import BaselineResult
from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.features.brackets import RENT, VALUE, median_from_brackets
from visionhousing.ledger import DEFAULT_DRAWS, Ledger, LedgerError, apply_overrides, sample
from visionhousing.ledger.loader import ROOT, read_yaml
from visionhousing.proforma import MIDRISE, REDEVELOPMENT, Intensity, residual_proforma

__all__ = [
    "CAPACITY_CASES",
    "COST_SETTINGS_PATH",
    "CROSSCHECK_IDS",
    "FEE_IDS",
    "VIEWS",
    "ChannelBResult",
    "Market",
    "Step",
    "baseline_array",
    "baseline_by_type",
    "channel_b",
    "fee_reform",
    "fee_saving",
    "load_cost_settings",
    "market_from_tracts",
    "proforma_crosscheck",
    "swing_table",
    "value_per_unit",
    "window_factor",
]

TIER = 0
"""Fees use only national data (permits and ACS), available for every state."""

DATA_SOURCES: tuple[str, ...] = ("bps_county_annual", "acs5_summary_file")
"""Registry ids behind the baseline and the county medians."""

VIEWS = ("benchmark", "state", "deferral")
"""``benchmark``: $50,000 per unit cut by 50%. ``state``: an assumed state fee level
(team judgement).
``deferral`` (variant ``fees_deferral``): the $50,000 is paid at occupancy instead
of at permit; the builder saves the construction-loan interest on it."""

ELASTICITY = "supply.elasticity_medium_run"
PREMIUM = "supply.new_construction_value_premium"
LAND_SHARE = "supply.crosscheck_land_share"
PARETO_ALPHA = "supply.crosscheck_pareto_alpha"
FEE_K = "fees.passthrough_k"
FEE_TOTAL = "fees.fee_total_per_unit"
FEE_CUT = "fees.fee_cut_share"
FEE_STATE = "fees.pa_assumed_fee_per_unit_judgement"
DEFER_MONTHS = "fees.deferral_months"
LOAN_RATE = "fees.construction_loan_rate"
EARMARK = "fees.utility_earmark_share"
DELAYED = "fees.delayed_capacity_share"
DELAYED_IDS = (EARMARK, DELAYED)
"""Ledger ids of the delayed-capacity case (variant ``fees_delayed_capacity``)."""

CAPACITY_CASES = ("none", "delayed")
"""Fee-funded capacity: ``none`` (benchmark: capacity timing unchanged) or
``delayed`` (variant ``fees_delayed_capacity``: the earmarked water and sewer
share of the revenue given up is not replaced, so capacity comes later and a
share of the added homes that need it are not permitted inside 2027-2031).
The funded-backfill case (variant ``fees_funded_backfill``) is a ledger swap
of the replacement share, read by the fiscal screen (``engine.readouts``)."""

COST_SETTINGS_PATH = ROOT / "assumptions" / "cost_reforms.yaml"
"""Non-numeric settings of the parking and fee reforms (curve levels, windows, fiscal words)."""

DEFERRAL_IDS = (FEE_TOTAL, DEFER_MONTHS, LOAN_RATE)
"""Ledger ids behind the deferral saving per unit."""

VALUE_IDS: tuple[str, ...] = (
    PREMIUM,
    REDEVELOPMENT.opex.key,
    REDEVELOPMENT.cap_rate.key,
    MIDRISE.opex.key,
    MIDRISE.cap_rate.key,
)
"""Ledger ids that set the value of a new unit."""

FEE_IDS: dict[str, tuple[str, ...]] = {
    "benchmark": (FEE_TOTAL, FEE_CUT, ELASTICITY, FEE_K, *VALUE_IDS),
    "state": (FEE_STATE, FEE_CUT, ELASTICITY, FEE_K, *VALUE_IDS),
    "deferral": (*DEFERRAL_IDS, ELASTICITY, FEE_K, *VALUE_IDS),
}
"""Ledger ids behind the Channel B fee estimate, by view."""

# Which pro forma stands in for each structure type in the cross-check.
_CROSSCHECK_SPECS: dict[str, tuple[Intensity, str]] = {
    "units_1": (REDEVELOPMENT, "sale"),
    "units_2": (REDEVELOPMENT, "income"),
    "units_3_4": (REDEVELOPMENT, "income"),
    "units_5plus": (MIDRISE, "income"),
}


def _proforma_ids() -> tuple[str, ...]:
    ids: set[str] = set()
    for spec, basis in _CROSSCHECK_SPECS.values():
        ids.update(i.key for i in spec.inputs(basis, False) if i.is_parameter)  # type: ignore[arg-type]
    return tuple(sorted(ids))


CROSSCHECK_IDS: tuple[str, ...] = (*_proforma_ids(), PREMIUM, LAND_SHARE, PARETO_ALPHA)
"""Ledger ids read by the pro-forma cross-check (besides the saving itself)."""


# ---------------------------------------------------------------- settings


_COST_CACHE: dict[tuple[str, int], dict[str, Any]] = {}


def load_cost_settings(path: Any = None) -> dict[str, Any]:
    """Read ``assumptions/cost_reforms.yaml`` (once per file version) and check it.

    Raises:
        LedgerError: A block or field is missing, or a curve level is outside 0-1.
    """
    from pathlib import Path

    p = Path(path) if path is not None else COST_SETTINGS_PATH
    key = (str(p), p.stat().st_mtime_ns)
    if key in _COST_CACHE:
        return _COST_CACHE[key]
    data = read_yaml(p)
    problems = []
    curve = (data or {}).get("binding_curve") or {}
    levels = curve.get("kept_shares") or []
    if not curve.get("id") or not levels:
        problems.append("binding_curve needs id and kept_shares")
    elif any(not 0.0 <= float(x) <= 1.0 for x in levels):
        problems.append("binding_curve.kept_shares must be shares between 0 and 1")
    windows = (data or {}).get("baseline_windows") or {}
    if not windows.get("id") or not windows.get("file"):
        problems.append("baseline_windows needs id and file")
    fiscal = (data or {}).get("fiscal") or {}
    if not fiscal.get("id") or not fiscal.get("recipients_text") or not fiscal.get("years_text"):
        problems.append("fiscal needs id, recipients_text and years_text")
    if problems:
        raise LedgerError("cost_reforms.yaml: " + " | ".join(problems))
    _COST_CACHE[key] = data
    return data


def window_factor(name: str, state: str | None = None) -> tuple[float, dict[str, Any]]:
    """Baseline factor of a named baseline-window sensitivity: five-year total / shipped total.

    Reads the diagnostic that ``visionhousing baseline-card`` writes
    (``assumptions/calibration/baseline_reconciliation.yaml``). The fee and
    parking estimates are linear in the baseline, so scaling every county's
    baseline by this factor re-runs them under that window exactly (the
    state's rule keeps each county's share; only the state level changes).

    Args:
        name: The diagnostic id, for example ``baseline_pa_window_2022_2025``.
        state: The run's state code; the diagnostic must belong to it.

    Returns:
        ``(factor, the diagnostic entry)``.

    Raises:
        ValueError: The name is not allowed, not in the file, or for another state.
    """
    cfg = load_cost_settings()["baseline_windows"]
    if name not in (cfg.get("allowed") or []):
        raise ValueError(f"baseline window '{name}' is not allowed (assumptions/cost_reforms.yaml)")
    doc = read_yaml(ROOT / str(cfg["file"])) or {}
    entry = (doc.get("diagnostics") or {}).get(name)
    if not entry:
        raise ValueError(f"baseline window '{name}' is not in {cfg['file']}")
    if state is not None and str(entry.get("state", "")).upper() != str(state).upper():
        raise ValueError(f"baseline window '{name}' belongs to {entry.get('state')}, not {state}")
    shipped = float(entry["shipped_state_component"])
    if shipped <= 0:
        raise ValueError(f"baseline window '{name}': shipped total must be above 0")
    return float(entry["five_year"]) / shipped, dict(entry)


# ---------------------------------------------------------------- results


@dataclass(frozen=True)
class Step:
    """One step of the explanation funnel, worked at central values.

    Attributes:
        id: Short stable id for the explain layer, for example ``"saving"``.
        record: The step's record, with its formula and ledger ids.
    """

    id: str
    record: Record


@dataclass(frozen=True)
class ChannelBResult:
    """What a cost-saving reform returns.

    Attributes:
        reform: ``"fees"`` or ``"parking"``.
        view: Which view ran (fees: ``benchmark`` or ``state``; parking: ``benchmark``).
        counties: County id to its five-year added-units record (P10 / P50 / P90).
        state: The state's five-year added-units record.
        by_type: Structure type to the state's added units in that type.
        crosscheck: Pro-forma cross-check: county id or ``"state"`` to implied
            added units.
        crosscheck_share: Percentage points of sites that become feasible
            (baseline-weighted, state).
        steps: The explanation funnel at central values.
        swing: One-at-a-time swing table (tornado), largest first.
        county_ids: County ids in the order of the arrays.
        added: Added units by draw, shape (counties, structure types, draws).
        parts: Named components of ``added`` (for parking: cost channel and site
            yield), same shape.
        notes: Plain statements a reader needs, for example draft inputs.
        per_unit: Per-draw inputs behind the cost readouts (``engine.readouts``):
            ``base`` (baseline units the saving reaches, by county, type and
            draw), ``saving`` ($ saved per unit, same shape) and, for parking,
            ``avoided`` (spaces avoided per unit, same shape).
        records: Other records by name (parking: spaces before and after in
            matched prototypes; fee deferral: the carry saving).
    """

    reform: str
    view: str
    counties: dict[str, Record]
    state: Record
    by_type: dict[str, Record]
    crosscheck: dict[str, Record]
    crosscheck_share: Record
    steps: tuple[Step, ...]
    swing: pd.DataFrame
    county_ids: tuple[str, ...]
    added: np.ndarray = field(repr=False)
    parts: dict[str, np.ndarray] = field(default_factory=dict, repr=False)
    notes: tuple[str, ...] = ()
    per_unit: dict[str, np.ndarray] = field(default_factory=dict, repr=False, compare=False)
    records: dict[str, Record] = field(default_factory=dict, repr=False, compare=False)

    def state_draws(self) -> np.ndarray:
        """Return the state's added units in each draw (sum over counties and types)."""
        return self.added.sum(axis=(0, 1))


# ---------------------------------------------------------------- baseline


def baseline_by_type(result: BaselineResult) -> pd.DataFrame:
    """Five-year baseline units by county and structure type, scaled to each county's P50.

    The baseline's ``annual`` table holds point projections by structure type.
    Each county's types are summed over the five years and scaled so they add up
    to the county's P50 (which includes the housing-cycle choice).

    Args:
        result: A projected baseline.

    Returns:
        A frame indexed by county id (text), one column per structure type.
    """
    annual = result.annual.assign(county=result.annual["county"].astype(str))
    table = annual.pivot_table(
        index="county", columns="structure_type", values="units", aggfunc="sum", fill_value=0.0
    ).reindex(columns=list(STRUCTURE_TYPES), fill_value=0.0)
    point = table.sum(axis=1)
    p50 = pd.Series({str(c): float(r.p50) for c, r in result.counties.items()})  # type: ignore[arg-type]
    factor = (p50.reindex(table.index) / point.where(point > 0)).fillna(0.0)
    return table.mul(factor, axis=0)


def baseline_array(baseline: pd.DataFrame, counties: Sequence[str]) -> np.ndarray:
    """Turn a county x structure-type frame into a (counties, types) array.

    Args:
        baseline: Frame indexed by county id with one column per structure type.
        counties: County ids, in the order wanted.

    Returns:
        Float array of shape (counties, 4).

    Raises:
        ValueError: A structure type is missing, a county is missing, or a value
            is negative or not a number.
    """
    missing = [t for t in STRUCTURE_TYPES if t not in baseline.columns]
    if missing:
        raise ValueError(f"baseline is missing structure types {missing}")
    frame = baseline.copy()
    frame.index = frame.index.astype(str)
    absent = [c for c in counties if c not in frame.index]
    if absent:
        raise ValueError(f"baseline has no row for counties {absent}")
    arr = frame.loc[list(counties), list(STRUCTURE_TYPES)].to_numpy(dtype=float)
    if not np.all(np.isfinite(arr)) or np.any(arr < 0):
        raise ValueError("baseline units must be finite and not negative")
    return arr


# ---------------------------------------------------------------- market and value


@dataclass(frozen=True)
class Market:
    """County home-value and rent brackets, and the medians read from them.

    Attributes:
        counties: County ids, in array order.
        value_counts: Owner homes per value bracket, shape (counties, 26).
        rent_counts: Cash renters per rent bracket, shape (counties, 24).
        median_value: County median home value, $ (from the summed brackets).
        median_rent: County median gross rent, $/month (from the summed brackets).
    """

    counties: tuple[str, ...]
    value_counts: np.ndarray
    rent_counts: np.ndarray
    median_value: np.ndarray
    median_rent: np.ndarray


def market_from_tracts(tracts: pd.DataFrame, counties: Sequence[str]) -> Market:
    """Sum each county's tract bracket counts and read the county medians.

    Args:
        tracts: Tract table with a ``county`` column and the ACS bracket columns
            (``value_*`` from B25075, ``rent_*`` from B25063).
        counties: County ids, in the order wanted.

    Returns:
        The county market.

    Raises:
        ValueError: A bracket column is missing, or a county has no tracts.
    """
    needed = [*VALUE.columns, *RENT.columns]
    missing = [c for c in needed if c not in tracts.columns]
    if missing:
        raise ValueError(f"tracts are missing bracket columns, first: {missing[:3]}")
    grouped = tracts.assign(county=tracts["county"].astype(str)).groupby("county")[needed].sum()
    absent = [c for c in counties if c not in grouped.index]
    if absent:
        raise ValueError(f"no tracts for counties {absent}")
    grouped = grouped.loc[list(counties)]
    value_counts = grouped[list(VALUE.columns)].to_numpy(dtype=float)
    rent_counts = grouped[list(RENT.columns)].to_numpy(dtype=float)
    median_value = np.array([median_from_brackets(row, VALUE).value for row in value_counts])
    median_rent = np.array([median_from_brackets(row, RENT).value for row in rent_counts])
    return Market(tuple(counties), value_counts, rent_counts, median_value, median_rent)


def value_per_unit(market: Market, params: Mapping[str, np.ndarray]) -> np.ndarray:
    """Value of a new unit by county, structure type and draw.

    - 1-unit buildings: median home value x (1 + premium).
    - 2- and 3-4-unit buildings: median rent x (1 + premium) x 12 x (1 - running-cost
      share) / cap rate, with the small-building (missing-middle) ledger values.
    - 5+-unit buildings: the same with the mid-rise ledger values.

    Args:
        market: County medians.
        params: Ledger id to draws (shape (draws,)); needs ``VALUE_IDS``.

    Returns:
        Array of shape (counties, 4, draws), in dollars per unit.
    """
    premium = 1.0 + np.asarray(params[PREMIUM], dtype=float)
    rent = market.median_rent[:, None] * premium[None, :] * 12.0

    def capitalised(spec: Intensity) -> np.ndarray:
        kept = 1.0 - np.asarray(params[spec.opex.key], dtype=float)
        return rent * kept[None, :] / np.asarray(params[spec.cap_rate.key], dtype=float)[None, :]

    owner = market.median_value[:, None] * premium[None, :]
    small = capitalised(REDEVELOPMENT)
    return np.stack([owner, small, small, capitalised(MIDRISE)], axis=1)


# ---------------------------------------------------------------- Channel B


def channel_b(
    baseline: np.ndarray,
    elasticity: np.ndarray,
    saving: np.ndarray,
    value: np.ndarray,
    passthrough: np.ndarray,
) -> np.ndarray:
    """Added units from a cost saving: baseline x e x (dC / V) x k.

    All inputs broadcast together (normally to counties x types x draws).

    Args:
        baseline: Baseline units.
        elasticity: Medium-run supply elasticity ``e`` (not negative).
        saving: Cost saved per unit ``dC`` in $ (not negative).
        value: Value per new unit ``V`` in $ (more than zero where baseline > 0).
        passthrough: Share ``k`` of the saving that reaches the build decision.

    Returns:
        Added units, broadcast shape.

    Raises:
        ValueError: A saving or elasticity is negative, ``k`` is outside 0-1, or
            a value is not positive where there is baseline production.
    """
    base = np.asarray(baseline, dtype=float)
    eps = np.asarray(elasticity, dtype=float)
    dc = np.asarray(saving, dtype=float)
    v = np.asarray(value, dtype=float)
    k = np.asarray(passthrough, dtype=float)
    if np.any(dc < 0):
        raise ValueError("the cost saving dC cannot be negative (this channel models cost cuts)")
    if np.any(eps < 0):
        raise ValueError("the supply elasticity cannot be negative")
    if np.any((k < 0) | (k > 1)):
        raise ValueError("the pass-through share k must be between 0 and 1")
    shape = np.broadcast_shapes(base.shape, eps.shape, dc.shape, v.shape, k.shape)
    base_b = np.broadcast_to(base, shape)
    v_b = np.broadcast_to(v, shape)
    bad = (base_b > 0) & ~(v_b > 0)
    if np.any(bad):
        raise ValueError("value per new unit V must be positive wherever baseline units exist")
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(base_b > 0, np.broadcast_to(dc, shape) / np.where(v_b > 0, v_b, 1.0), 0.0)
    return base_b * eps * ratio * k


def fee_saving(params: Mapping[str, np.ndarray], view: str) -> np.ndarray:
    """Cost saved per unit by the fee cut, by draw: fees per unit x cut share.

    Under ``deferral`` the saving is the carry on fees paid at permit instead
    of at occupancy: fees per unit x construction-loan rate x deferral months / 12.

    Args:
        params: Ledger id to draws.
        view: ``benchmark`` ($50,000), ``state`` (assumed state fee level, a judgement)
            or ``deferral``.

    Returns:
        Array of shape (draws,), $ per unit.
    """
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}, got {view!r}")
    if view == "deferral":
        months = np.asarray(params[DEFER_MONTHS], dtype=float)
        rate = np.asarray(params[LOAN_RATE], dtype=float)
        if np.any(months < 0) or np.any(rate < 0):
            raise ValueError("deferral months and the loan rate cannot be negative")
        return np.asarray(params[FEE_TOTAL], dtype=float) * rate * months / 12.0
    fee = FEE_TOTAL if view == "benchmark" else FEE_STATE
    cut = np.asarray(params[FEE_CUT], dtype=float)
    if np.any((cut < 0) | (cut > 1)):
        raise ValueError("the fee cut share must be between 0 and 1")
    return np.asarray(params[fee], dtype=float) * cut


# ---------------------------------------------------------------- pro-forma cross-check


def _pareto_shares(
    threshold: np.ndarray,
    counts: np.ndarray,
    table: Any,
    alpha: np.ndarray,
    chunk: int = 256,
) -> np.ndarray:
    """County shares above thresholds, all draws at once (Pareto top bracket, alpha per draw).

    The same sum, bracket by bracket, as
    :func:`~visionhousing.features.brackets.share_above_many` with
    ``top_rule="pareto"``, done for a block of draws at a time instead of one
    draw at a time, so the numbers are the same to the last digit. Blank shares
    (no units in the brackets) count as 0, as before.

    Args:
        threshold: Thresholds, shape (counties, types, draws).
        counts: County bracket counts, shape (counties, brackets).
        table: The bracket table (``VALUE`` or ``RENT``).
        alpha: Pareto shape per draw, shape (draws,).
        chunk: Draws handled at a time, to bound memory.

    Returns:
        Shares, shaped like ``threshold``.
    """
    lows = np.asarray(table.lows, dtype=float)
    highs = np.asarray(table.highs, dtype=float)
    finite_high = np.where(np.isfinite(highs), highs, 0.0)
    width = np.where(np.isfinite(highs), highs - lows, 1.0)
    top = np.isinf(highs)
    c = np.asarray(counts, dtype=float)
    if np.any(c < 0):
        raise ValueError("bracket counts cannot be negative")
    total = c.sum(axis=1)
    bad = (np.isnan(c).any(axis=1) | ~(total > 0))[:, None, None]
    out = np.empty(threshold.shape)
    n = threshold.shape[-1]
    for start in range(0, n, chunk):
        t = threshold[:, :, start : start + chunk]
        tt = t[..., None]  # (counties, types, draws, 1) against brackets
        a = np.asarray(alpha[start : start + chunk], dtype=float)[None, None, :, None]
        with np.errstate(invalid="ignore", divide="ignore"):
            frac = np.clip((finite_high - tt) / width, 0.0, 1.0)
            frac = np.where(top, np.where(tt <= lows, 1.0, (lows / tt) ** a), frac)
            value = (c[:, None, None, :] * frac).sum(axis=-1) / total[:, None, None]
        out[:, :, start : start + chunk] = np.nan_to_num(
            np.where(bad | np.isnan(t), np.nan, value), nan=0.0
        )
    return out


def proforma_crosscheck(
    baseline: np.ndarray,
    saving: np.ndarray,
    market: Market,
    params: Mapping[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Share of sites where a project pencils, before and after a saving.

    For each structure type the stand-in pro forma (1-unit: small building sold
    unit by unit; 2-4 units: small rental building; 5+: mid-rise) gives the cost
    per unit including the builder's return and land (land = land share x
    development cost). For rented types the break-even rent is that cost divided
    by the value of one dollar of monthly rent; for 1-unit homes it is the
    break-even sale value. The saving per unit (already times ``k``), plus the
    builder's return on it, comes off the cost. Break-evens are for new units, so
    they are divided by (1 + premium) before being compared with the county's
    existing homes or rents.

    Args:
        baseline: Baseline units, shape (counties, 4) or (counties, 4, draws).
        saving: Saving per unit that reaches the build decision, broadcastable to
            (counties, 4, draws).
        market: County brackets.
        params: Ledger id to draws; needs ``CROSSCHECK_IDS``.

    Returns:
        ``share_before``, ``share_after``, ``uplift`` (relative rise in the
        feasible share; 0 where nothing pencils before) and ``added`` (baseline x
        uplift), each of shape (counties, 4, draws).
    """
    n = int(np.size(params[PREMIUM]))
    n_c = len(market.counties)
    premium = 1.0 + np.asarray(params[PREMIUM], dtype=float)
    land = np.asarray(params[LAND_SHARE], dtype=float)
    alpha = np.broadcast_to(np.asarray(params[PARETO_ALPHA], dtype=float), (n,))
    save = np.broadcast_to(np.asarray(saving, dtype=float), (n_c, len(STRUCTURE_TYPES), n))
    context = {"cost_factor": 1.0, "rent_monthly": 1.0, "sale_value_per_unit": 1.0}
    before = np.empty((n_c, len(STRUCTURE_TYPES), n))
    after = np.empty_like(before)
    for j, t in enumerate(STRUCTURE_TYPES):
        spec, basis = _CROSSCHECK_SPECS[t]
        lines = residual_proforma(spec, params, context, basis=basis).lines  # type: ignore[arg-type]
        units = lines["units"]
        cost_unit = (lines["cost_with_return"] + land * lines["development_cost"]) / units
        per_dollar = lines["stabilised_value"] / units  # value of $1 of rent (or of sale value)
        margin = np.asarray(params[spec.margin.key], dtype=float)
        cut = save[:, j, :] * (1.0 + margin)[None, :]
        be_old = np.broadcast_to(cost_unit / per_dollar / premium, (n_c, n))
        be_new = np.maximum(cost_unit[None, :] - cut, 0.0) / per_dollar / premium
        before[:, j, :] = be_old
        after[:, j, :] = be_new
    share_before = np.empty_like(before)
    share_after = np.empty_like(after)
    for idx, counts, table in (
        ([0], market.value_counts, VALUE),  # 1-unit homes: sale values
        ([1, 2, 3], market.rent_counts, RENT),  # rented types: rents
    ):
        share_before[:, idx, :] = _pareto_shares(before[:, idx, :], counts, table, alpha)
        share_after[:, idx, :] = _pareto_shares(after[:, idx, :], counts, table, alpha)
    with np.errstate(divide="ignore", invalid="ignore"):
        uplift = np.where(share_before > 0, share_after / share_before - 1.0, 0.0)
    uplift = np.maximum(uplift, 0.0)
    base = np.asarray(baseline, dtype=float)
    if base.ndim == 2:
        base = base[:, :, None]
    return {
        "share_before": share_before,
        "share_after": share_after,
        "uplift": uplift,
        "added": base * uplift,
    }


# ---------------------------------------------------------------- sampling and swings


def _draws(ledger: Ledger, ids: Sequence[str], n: int, seed: int) -> dict[str, np.ndarray]:
    return sample(ledger, n=n, seed=seed, ids=sorted(set(ids)))


def _central(ledger: Ledger, ids: Sequence[str]) -> dict[str, np.ndarray]:
    return {pid: np.array([ledger[pid].central], dtype=float) for pid in set(ids)}


def swing_table(
    total: Callable[[dict[str, np.ndarray]], float],
    ledger: Ledger,
    ids: Sequence[str],
) -> pd.DataFrame:
    """One-at-a-time swings: each parameter at its low and high, others central.

    Args:
        total: Function from parameter arrays (shape (1,)) to the state total.
        ledger: The ledger (central, low and high values).
        ids: Parameters to swing; fixed parameters are skipped.

    Returns:
        Columns ``parameter``, ``label``, ``low``, ``high``, ``total_low``,
        ``total_high``, ``swing`` (absolute difference), sorted by swing, largest first.
    """
    base = _central(ledger, ids)
    rows = []
    for pid in sorted(set(ids)):
        p = ledger[pid]
        if p.shape == "fixed":
            continue
        at = {}
        for end in ("low", "high"):
            params = dict(base)
            params[pid] = np.array([getattr(p, end)], dtype=float)
            at[end] = float(total(params))
        rows.append(
            {
                "parameter": pid,
                "label": p.label,
                "low": p.low,
                "high": p.high,
                "total_low": at["low"],
                "total_high": at["high"],
                "swing": abs(at["high"] - at["low"]),
            }
        )
    columns = ["parameter", "label", "low", "high", "total_low", "total_high", "swing"]
    frame = pd.DataFrame(rows, columns=columns)
    return frame.sort_values("swing", ascending=False, kind="mergesort").reset_index(drop=True)


# ---------------------------------------------------------------- records


def _sources(ledger: Ledger, ids: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({*DATA_SOURCES, *(s for i in ids for s in ledger[i].source_ids)}))


def range_records(
    added: np.ndarray,
    counties: Sequence[str],
    *,
    what: str,
    expression: str,
    ledger: Ledger,
    ids: Sequence[str],
) -> tuple[dict[str, Record], Record, dict[str, Record]]:
    """Summarise added-unit draws as county, state and structure-type records.

    Args:
        added: Added units, shape (counties, 4, draws).
        counties: County ids in array order.
        what: Name stem, for example "added units from the fee cut".
        expression: The per-draw formula, in words.
        ledger: The ledger, for source ids.
        ids: Ledger ids that were sampled.

    Returns:
        ``(by county, state, by structure type)``.
    """
    ids = tuple(sorted(set(ids)))
    sources = _sources(ledger, ids)
    common = {"assumption_ids": ids, "source_ids": sources, "tier": TIER}
    span = "2027-2031"
    by_county = {
        c: rec.from_draws(
            f"{what} {span}, county {c}",
            added[i].sum(axis=0),
            "units",
            expression=f"{expression}, summed over structure types",
            **common,
        )
        for i, c in enumerate(counties)
    }
    state = rec.from_draws(
        f"{what} {span}, state",
        added.sum(axis=(0, 1)),
        "units",
        expression=f"{expression}, summed over counties and structure types in each draw",
        **common,
    )
    by_type = {
        t: rec.from_draws(
            f"{what} {span}, {STRUCTURE_LABELS[t]}, state",
            added[:, j, :].sum(axis=0),
            "units",
            expression=f"{expression}, {STRUCTURE_LABELS[t]}, summed over counties",
            **common,
        )
        for j, t in enumerate(STRUCTURE_TYPES)
    }
    return by_county, state, by_type


def crosscheck_records(
    check: dict[str, np.ndarray],
    baseline: np.ndarray,
    counties: Sequence[str],
    *,
    what: str,
    ledger: Ledger,
    ids: Sequence[str],
) -> tuple[dict[str, Record], Record]:
    """Records for the pro-forma cross-check: implied added units and newly feasible share.

    Args:
        check: Output of :func:`proforma_crosscheck`.
        baseline: Baseline units, shape (counties, 4) or (counties, 4, draws).
        counties: County ids in array order.
        what: Name stem.
        ledger: The ledger, for source ids.
        ids: Ledger ids behind the cross-check.

    Returns:
        ``(county id or "state" to implied added units, newly feasible share)``.
    """
    ids = tuple(sorted(set(ids)))
    common = {"assumption_ids": ids, "source_ids": _sources(ledger, ids), "tier": TIER}
    expression = (
        "baseline units x (share of sites above the break-even after the saving / "
        "share before - 1), break-evens from the pro formas, shares from ACS brackets"
    )
    out = {
        c: rec.from_draws(
            f"{what}, pro-forma cross-check 2027-2031, county {c}",
            check["added"][i].sum(axis=0),
            "units",
            expression=expression,
            **common,
        )
        for i, c in enumerate(counties)
    }
    out["state"] = rec.from_draws(
        f"{what}, pro-forma cross-check 2027-2031, state",
        check["added"].sum(axis=(0, 1)),
        "units",
        expression=f"{expression}, summed over counties and structure types",
        **common,
    )
    base = np.asarray(baseline, dtype=float)
    if base.ndim == 2:
        base = base[:, :, None]
    weight = np.broadcast_to(base, check["share_before"].shape)
    total = weight.sum(axis=(0, 1))
    gain = ((check["share_after"] - check["share_before"]) * weight).sum(axis=(0, 1))
    points = np.where(total > 0, 100.0 * gain / np.where(total > 0, total, 1.0), 0.0)
    share = rec.from_draws(
        f"{what}: sites that become feasible, percentage points (baseline-weighted)",
        points,
        "percentage points",
        expression=(
            "100 x sum over counties and types of baseline units x (share of sites "
            "feasible after - before) / baseline units"
        ),
        **common,
    )
    return out, share


def funnel_steps(
    *,
    baseline_total: float,
    saving: Record,
    weighted_ratio: float,
    elasticity: Record,
    passthrough: Record,
    added_name: str,
    extra: Sequence[Step] = (),
) -> tuple[Step, ...]:
    """Write the Channel B funnel at central values, ending in the added units.

    The value per unit shown is the effective value: the average saving divided
    by the baseline-weighted average of saving / value. With it, the product of
    the steps equals the central-value total exactly.

    Args:
        baseline_total: Baseline units of the types the reform reaches.
        saving: Record of the (average) saving per unit.
        weighted_ratio: Baseline-weighted average of dC / V.
        elasticity: Record for ``e``.
        passthrough: Record for ``k``.
        added_name: Name of the final step.
        extra: Steps placed before the saving (for example the parking spaces).

    Returns:
        The steps, in order.
    """
    base = rec.quantity(
        "baseline units 2027-2031, building types reached",
        baseline_total,
        "units",
        formula=(
            f"baseline units 2027-2031 in the building types this reform reaches = "
            f"{rec.format_number(baseline_total, 'units')} (projected from building permits)"
        ),
        source_ids=("bps_county_annual",),
        tier=TIER,
    )
    v_eff = saving.point() / weighted_ratio if weighted_ratio > 0 else 0.0
    value = rec.quantity(
        "value per new unit (effective)",
        v_eff,
        "$/unit",
        formula=(
            "value per new unit, effective = average saving / baseline-weighted average "
            f"of (saving / value) over counties and building types = "
            f"{rec.format_number(v_eff, '$/unit')} (county medians from ACS brackets x "
            "(1 + new-construction premium); rented types capitalised at the cap rate)"
        ),
        assumption_ids=VALUE_IDS,
        source_ids=("acs5_summary_file",),
        tier=TIER,
    )
    if v_eff > 0:
        ratio = rec.div(saving, value, name="saving as a share of value", unit="share")
    else:
        ratio = rec.quantity("saving as a share of value", 0.0, "share")
    added = rec.product([base, elasticity, ratio, passthrough], name=added_name, unit="units")
    steps = [
        *extra,
        Step("saving", saving),
        Step("baseline", base),
        Step("value", value),
        Step("ratio", ratio),
        Step("elasticity", elasticity),
        Step("passthrough", passthrough),
        Step("added", added),
    ]
    return tuple(steps)


# ---------------------------------------------------------------- the reform


def _fee_core(
    params: Mapping[str, np.ndarray],
    base: np.ndarray,
    market: Market,
    view: str,
    capacity_case: str = "none",
) -> dict[str, np.ndarray]:
    saving = fee_saving(params, view)[None, None, :]
    value = value_per_unit(market, params)
    added = channel_b(
        base,
        np.asarray(params[ELASTICITY])[None, None, :],
        saving,
        value,
        np.asarray(params[FEE_K])[None, None, :],
    )
    out = {"saving": saving, "value": value, "added": added}
    if capacity_case == "delayed":
        earmark = np.asarray(params[EARMARK], dtype=float)
        delayed = np.asarray(params[DELAYED], dtype=float)
        if np.any((earmark < 0) | (earmark > 1)) or np.any((delayed < 0) | (delayed > 1)):
            raise ValueError("the earmarked and delayed shares must be between 0 and 1")
        lost = (earmark * delayed)[None, None, :]
        out["delayed"] = added * lost
        out["added"] = added * (1.0 - lost)
    return out


def _scaled(base: np.ndarray, scale: np.ndarray | None, n: int) -> np.ndarray:
    if scale is None:
        return base[:, :, None]
    s = np.asarray(scale, dtype=float)
    if s.shape != (base.shape[0], n):
        raise ValueError(f"baseline_scale must have shape (counties, draws) = {(base.shape[0], n)}")
    if np.any(s < 0) or not np.all(np.isfinite(s)):
        raise ValueError("baseline_scale must be finite and not negative")
    return base[:, :, None] * s[:, None, :]


def fee_reform(
    baseline: pd.DataFrame,
    tracts: pd.DataFrame,
    ledger: Ledger,
    *,
    view: str = "benchmark",
    n_draws: int = DEFAULT_DRAWS,
    seed: int = 0,
    overrides: Mapping[str, Any] | None = None,
    baseline_scale: np.ndarray | None = None,
    capacity_case: str = "none",
) -> ChannelBResult:
    """Benchmark reform 5: added permitted units in 2027-2031 from a 50% fee cut.

    Args:
        baseline: Five-year baseline units, indexed by county id, one column per
            structure type (see :func:`baseline_by_type`).
        tracts: Tract table with ``county`` and the ACS bracket columns.
        ledger: The Assumption Ledger.
        view: ``benchmark`` ($50,000 x 50%) or ``state`` (assumed state fees x 50%).
        n_draws: Monte Carlo draws.
        seed: Run seed; the same seed gives the same result.
        overrides: Ledger overrides for this run (see ``ledger.apply_overrides``).
        baseline_scale: Optional baseline multipliers by county and draw, shape
            (counties, draws), so baseline uncertainty is carried through.
        capacity_case: ``none`` (benchmark) or ``delayed`` (see
            :data:`CAPACITY_CASES`): added units x (1 - earmarked share x
            delayed share).

    Returns:
        The result: records by county and state, the cross-check, the funnel and
        the swing table.
    """
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}, got {view!r}")
    if capacity_case not in CAPACITY_CASES:
        raise ValueError(f"capacity case must be one of {CAPACITY_CASES}, got {capacity_case!r}")
    if capacity_case == "delayed" and view == "deferral":
        raise ValueError("the delayed-capacity case applies to a fee cut, not to deferral")
    ledger = apply_overrides(ledger, overrides)
    counties = tuple(str(c) for c in baseline.index)
    base = baseline_array(baseline, counties)
    market = market_from_tracts(tracts, counties)
    ids = (*FEE_IDS[view], *(DELAYED_IDS if capacity_case == "delayed" else ()))
    xids = tuple(sorted({*CROSSCHECK_IDS, *ids}))
    params = _draws(ledger, xids, n_draws, seed)
    based = _scaled(base, baseline_scale, n_draws)
    core = _fee_core(params, based, market, view, capacity_case)
    expression = "baseline units x e x (fees per unit x cut share / value per new unit) x k"
    if capacity_case == "delayed":
        expression += " x (1 - earmarked water and sewer share x delayed-capacity share)"
    if view == "deferral":
        expression = (
            "baseline units x e x (fees per unit x construction-loan rate x deferral months / 12 "
            "/ value per new unit) x k"
        )
    what = "added units from the fee cut" + (" (state fee levels)" if view == "state" else "")
    if view == "deferral":
        what = "added units from fee deferral"
    counties_rec, state, by_type = range_records(
        core["added"], counties, what=what, expression=expression, ledger=ledger, ids=ids
    )

    # Pro-forma cross-check: the same saving, times the same k, through the pro formas.
    pf_params = dict(params)
    if view == "state":
        pf_params[FEE_TOTAL] = params[FEE_STATE]
    reached = core["saving"] * np.asarray(params[FEE_K])[None, None, :]
    check = proforma_crosscheck(based, reached, market, pf_params)
    cross, cross_share = crosscheck_records(
        check, based, counties, what=what, ledger=ledger, ids=xids
    )

    # Funnel and swings at central values.
    central = _central(ledger, ids)
    c_core = _fee_core(central, base[:, :, None], market, view, capacity_case)
    fee_id = FEE_STATE if view == "state" else FEE_TOTAL
    if view == "deferral":
        saving_rec = rec.div(
            rec.product(
                [
                    rec.from_parameter(FEE_TOTAL, ledger[FEE_TOTAL]),
                    rec.from_parameter(LOAN_RATE, ledger[LOAN_RATE]),
                    rec.from_parameter(DEFER_MONTHS, ledger[DEFER_MONTHS]),
                ],
                name="fees x loan rate x months",
            ),
            rec.quantity("months per year", 12.0, "months/year", formula="12 months per year"),
            name="fee deferral saving per unit (carry not paid)",
            unit="$/unit",
        )
    else:
        saving_rec = rec.mul(
            rec.from_parameter(fee_id, ledger[fee_id]),
            rec.from_parameter(FEE_CUT, ledger[FEE_CUT]),
            name="fee saving per unit",
            unit="$/unit",
        )
    steps = _fee_steps(ledger, base, c_core, saving_rec)
    swing = swing_table(
        lambda p: float(_fee_core(p, base[:, :, None], market, view, capacity_case)["added"].sum()),
        ledger,
        ids,
    )
    notes = [
        "Channel B and the pro-forma cross-check use the same saving and the same k; "
        "they are two estimates of one effect, reported side by side, never added.",
        "The pro-forma cross-check uses small-building and mid-rise pro formas as stand-ins "
        "for every structure type; a 1-unit home is costed as one unit of a small building.",
    ]
    if view == "state":
        notes.append(
            f"State view: the fee level is {FEE_STATE}, an assumed statewide figure (a draft "
            "team judgement with a wide range), not a surveyed one: no reproducible inventory of "
            "Pennsylvania fee schedules stands behind it yet."
        )
    records: dict[str, Record] = {}
    if capacity_case == "delayed":
        notes.append(
            "Delayed-capacity case (a policy option, not the benchmark): the earmarked water "
            f"and sewer share ({EARMARK}) of the revenue given up is not replaced, so capacity "
            f"comes later and a share ({DELAYED}) of the added homes that need it are not "
            "permitted inside 2027-2031."
        )
        records["delayed_by_capacity"] = rec.from_draws(
            "added homes the cut would make that wait for delayed capacity (past 2031), state",
            core["delayed"].sum(axis=(0, 1)),
            "units",
            expression=(
                "baseline units x e x (fees per unit x cut share / value per new unit) x k x "
                "earmarked water and sewer share x delayed-capacity share, summed"
            ),
            assumption_ids=tuple(sorted(set(ids))),
            source_ids=_sources(ledger, ids),
            tier=TIER,
        )
    if view == "deferral":
        notes.append(
            "Deferral view: fees are not cut; they are paid at occupancy instead of at permit. "
            "The builder saves the interest on carrying them through construction; the public "
            "gives up only the time value of the delay and a small collection loss."
        )
    draft = sorted(i for i in ids if ledger[i].status == "draft")
    if draft:
        notes.append("Draft inputs (team judgement, wide ranges): " + ", ".join(draft) + ".")
    return ChannelBResult(
        reform="fees",
        view=view,
        counties=counties_rec,
        state=state,
        by_type=by_type,
        crosscheck=cross,
        crosscheck_share=cross_share,
        steps=steps,
        swing=swing,
        county_ids=counties,
        added=core["added"],
        parts={"channel_b": core["added"]},
        notes=tuple(notes),
        per_unit={
            "base": based,
            "saving": np.broadcast_to(core["saving"], core["added"].shape),
        },
        records=records,
    )


def _fee_steps(
    ledger: Ledger, base: np.ndarray, c_core: dict[str, np.ndarray], saving_rec: Record
) -> tuple[Step, ...]:
    weight = base.sum()
    ratio = c_core["saving"] / np.where(c_core["value"] > 0, c_core["value"], np.inf)
    weighted = float((base[:, :, None] * ratio).sum() / weight) if weight > 0 else 0.0
    return funnel_steps(
        baseline_total=float(weight),
        saving=saving_rec,
        weighted_ratio=weighted,
        elasticity=rec.from_parameter(ELASTICITY, ledger[ELASTICITY]),
        passthrough=rec.from_parameter(FEE_K, ledger[FEE_K]),
        added_name="added units from the fee cut, central values",
    )
