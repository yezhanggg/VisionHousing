"""The ADU two-lever case: predictable approval (timing) and financing access.

Plain-language summary
----------------------
The ADU benchmark (:mod:`visionhousing.scenarios.adu`) says how many ADU
permits a legal change adds in 2027-2031. This module asks two follow-up
questions on the *same draws*:

1. **Timing ("predictable approval").** If the slowest approvals got faster,
   how many permits that would have come after 2031 move into the window?
2. **Financing access.** If a state program paid part of the owner's cash
   share, and lenders counted part of the ADU's rent, how many more ADUs
   become feasible?

The two answers are kept apart: *authorizations moved into 2027-2031*
(timing) versus *newly feasible authorizations* (financing). A timing change
never adds an application; it only changes when permits are issued.

The timing lever: a lag model
-----------------------------
adopted law -> local implementation date -> applications -> permit issued.

- **Implementation date.** Applications start after the ADU start delay
  (``adu.start_delay_years``; 0 for the benchmark: in force January 1, 2027).
- **Applications.** The benchmark's take-up is counted in permits issued, so
  it already carries today's approval times. Applications are filed through
  each year in proportion to the ramp ``ramp(t)`` (full rate after 2031), and
  scaled so that, under today's lag, the permits issued in 2027-2031 equal the
  benchmark's added units in that draw. Applications filed in 2027-2031 that
  are still waiting at the end of 2031 are the projects "beyond 2031"; the
  model keeps simulating them (``adu.timing_simulation_years_beyond``) and
  reports those still waiting at the end as pending (right-censored), never
  dropped.
- **Lag.** Days from application to permit are lognormal with median ``m``
  (``adu.permit_lag_median_days``) and log spread ``s``
  (``adu.permit_lag_log_sd``). Pennsylvania publishes no application dates, so
  the range is illustrative/transferred: Philadelphia's observed zoning-to-
  building stage lag and California's ADU application-to-permit ranges
  (:func:`lag_anchors` reads both calibration files on every run).
- **Lever.** Every lag above the median moves a share ``pi`` of the way to the
  median (``adu.timing_tail_pull_share``; 0 for the benchmark):
  ``lag' = m + (1 - pi) (lag - m)`` above ``m``, unchanged below. So P75 and P90
  fall toward the median, the median stays, and no lag gets longer. With the
  lognormal CDF ``F``, the new CDF is ``F'(x) = F(m + (x - m) / (1 - pi))`` for
  ``x >= m``.
- **Moved into the window** = (permits issued 2027-2031 under ``F'`` minus under
  ``F``), scaled as above. The five-year-plus-beyond total (applications filed
  2027-2031) is the same in both, so timing changes only the in-window share.

The financing lever
-------------------
From the pro forma (:mod:`visionhousing.proforma.adu`): owner cash
``C0 = total cost x (1 - loan share)``.

- A program offers up to the program amount at the program rate
  (``adu.financing_program_amount``, ``adu.financing_program_rate``; 0 for the
  benchmark).
- A lender that counts a share ``r`` of the expected ADU rent
  (``adu.financing_rent_income_share``; Fannie Mae-style rule, judgement
  range) offers up to ``r x rent / loan payment per $`` more.
- Each dollar of ``C0`` comes from the cheapest offer first: the source with
  the lower monthly payment per dollar fills first (program money on a tie),
  the other covers what is left, so ``P + R <= C0``.
- New owner cash ``C1 = C0 - P - R``. An owner uses each new dollar only where
  it costs no more per month than own cash, so the monthly cost falls by
  ``P (c - min(p, c)) + R (c - min(l, c))`` (``c``, ``p``, ``l``: payment per $
  of own cash, program money and the loan) and the break-even rent by
  ``coverage x that / (1 - running-cost share)``. More lots pass the rent test.
- The yearly split of "moved into the window" is scaled so the years add up
  to the moved total kept after the feasible-lot cap.
- Owners who could not raise the cash: take-up rises by ``A(C1) / A(C0)``,
  ``A(C) = 1 / (1 + (C / C50)^k)`` (``adu.owner_cash_capacity_median``,
  ``adu.owner_cash_capacity_shape``). Today's take-up already reflects today's
  cash need, so the factor is exactly 1 when nothing changes.
- **Newly feasible** = added units with the lever minus without, both under
  today's approval times.

Both levers together: financing first (more applications), then timing on
that larger pool. ``paired change = newly feasible + moved into the window``
in every draw.

Purity
------
:func:`apply_levers` is pure (ledger and arrays in, result out). It returns
``None`` when every lever setting is at its benchmark value, so a benchmark
run is unchanged to the last digit. The lever card reads
:func:`card_fields`; the outcome contract reads :func:`outcome_timing`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.ledger.loader import ROOT, Ledger, apply_overrides, read_yaml
from visionhousing.ledger.sampling import sample
from visionhousing.proforma._calc import payment_factor
from visionhousing.scenarios import adu_budget

__all__ = [
    "FINANCING_TARGETS",
    "LEVER_SOURCES",
    "TIMING_TARGETS",
    "LeverResult",
    "apply_levers",
    "card_fields",
    "cash_access",
    "issuance_by_year",
    "lag_anchors",
    "lag_cdf",
    "outcome_timing",
]

PHL_LAG_PATH = ROOT / "assumptions" / "calibration" / "permit_lag_phl.yaml"
CA_DONOR_PATH = ROOT / "assumptions" / "calibration" / "apr_timing_donor_ca.yaml"

MEDIAN = "adu.permit_lag_median_days"
LOG_SD = "adu.permit_lag_log_sd"
PULL = "adu.timing_tail_pull_share"
BEYOND = "adu.timing_simulation_years_beyond"
PROGRAM = "adu.financing_program_amount"
PROGRAM_RATE = "adu.financing_program_rate"
RENT_SHARE = "adu.financing_rent_income_share"
CASH_MEDIAN = "adu.owner_cash_capacity_median"
CASH_SHAPE = "adu.owner_cash_capacity_shape"
BINDING = "adu.lever_binding_min_share"
RAMP_IDS = ("adu.ramp_first_year_share", "adu.ramp_years_to_full")

TIMING_TARGETS = (PULL,)
"""Lever settings of the timing lever (0 = benchmark)."""
FINANCING_TARGETS = (PROGRAM, RENT_SHARE)
"""Lever settings of the financing lever (0 = benchmark)."""
LEVER_SOURCES = {
    PULL: "adu.timing_reliable_tail_pull_share",
    PROGRAM: "adu.financing_access_program_amount",
    RENT_SHARE: "adu.financing_access_rent_income_share",
}
"""Each lever setting and the ledger entry its variant takes the range of."""
TIMING_IDS = (MEDIAN, LOG_SD, PULL, BEYOND, *RAMP_IDS, "adu.start_delay_years")
FINANCING_IDS = (PROGRAM, PROGRAM_RATE, RENT_SHARE, CASH_MEDIAN, CASH_SHAPE)

STEPS_PER_YEAR = 12
"""Applications are filed at mid-month points (12 a year, evenly within a year)."""
DAYS_PER_YEAR = 365.25
Z90 = 1.2815515655446004
Z75 = 0.6744897501960817

LABEL = "illustrative/transferred"
"""Label on every timing number: no Pennsylvania application dates exist."""


# ---------------------------------------------------------------- lag model


def _norm_cdf(z: np.ndarray) -> np.ndarray:
    """Standard normal CDF via erf (Abramowitz and Stegun 7.1.26, error below 1.5e-7)."""
    z = np.asarray(z, dtype=float)
    x = np.abs(z) / np.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * x)
    poly = t * (
        0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))
    )
    erf = 1.0 - poly * np.exp(-x * x)
    return 0.5 * (1.0 + np.sign(z) * erf)


def lag_cdf(x_days: np.ndarray, median: Any, log_sd: Any, pull: Any = 0.0) -> np.ndarray:
    """Share of issued applications whose lag is at most ``x`` days.

    Today: lognormal, ``F(x) = Phi(ln(x / m) / s)``. With the tail pulled by
    ``pi``: ``F'(x) = F(m + (x - m) / (1 - pi))`` for ``x >= m``, ``F(x)`` below.
    ``pi = 1`` puts every slow case at the median (``F' = 1`` from ``m`` on).

    Args:
        x_days: Days (any shape; values at or below 0 give 0).
        median: ``m`` in days (broadcasts against ``x_days``).
        log_sd: ``s``.
        pull: ``pi`` in [0, 1].

    Returns:
        The CDF, same shape as the broadcast inputs.
    """
    x = np.asarray(x_days, dtype=float)
    m = np.asarray(median, dtype=float)
    s = np.asarray(log_sd, dtype=float)
    pi = np.clip(np.asarray(pull, dtype=float), 0.0, 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        stretched = np.where(pi < 1.0, m + (x - m) / np.where(pi < 1.0, 1.0 - pi, 1.0), np.inf)
        y = np.where(x >= m, stretched, x)
        z = np.where(y > 0, np.log(np.where(y > 0, y, 1.0) / m) / s, -np.inf)
    out = np.where(np.isposinf(z), 1.0, _norm_cdf(np.where(np.isfinite(z), z, 0.0)))
    return np.where(np.isneginf(z) | (x <= 0), 0.0, out)


def issuance_by_year(filings: np.ndarray, median: Any, log_sd: Any, pull: Any = 0.0) -> np.ndarray:
    """Permits issued per calendar year from applications filed per year.

    Applications of year ``t`` are spread over 12 mid-month filing times
    ``s``; an application filed at ``s`` (years since January 1, 2027) is
    issued in year ``y`` with probability ``F((y + 1 - s) x 365.25) - F((y - s) x 365.25)``.

    Args:
        filings: ``(draws, years)`` applications filed per year.
        median: Lag median ``m`` in days, per draw ``(draws,)`` or a number.
        log_sd: Log spread ``s``, per draw or a number.
        pull: Tail pull ``pi``, per draw or a number.

    Returns:
        ``(draws, years)`` permits issued per year over the same span. What is
        not issued by the end of the span is still pending (right-censored).
    """
    a = np.asarray(filings, dtype=float)
    draws, n_years = a.shape
    m = np.broadcast_to(np.asarray(median, dtype=float), (draws,))[:, None, None]
    s = np.broadcast_to(np.asarray(log_sd, dtype=float), (draws,))[:, None, None]
    pi = np.broadcast_to(np.asarray(pull, dtype=float), (draws,))[:, None, None]
    times = (np.arange(n_years * STEPS_PER_YEAR) + 0.5) / STEPS_PER_YEAR  # filing times
    per_step = np.repeat(a, STEPS_PER_YEAR, axis=1) / STEPS_PER_YEAR  # (draws, steps)
    edges = np.arange(n_years + 1, dtype=float)
    gap = (edges[None, None, :] - times[None, :, None]) * DAYS_PER_YEAR  # (1, steps, edges)
    cdf = lag_cdf(gap, m, s, pi)  # (draws, steps, edges)
    by_year = np.diff(cdf, axis=2)  # (draws, steps, years)
    return np.einsum("ds,dsy->dy", per_step, by_year)


@lru_cache(maxsize=2)
def _anchors(phl: str, ca: str) -> dict[str, Any]:
    p = read_yaml(Path(phl))
    c = read_yaml(Path(ca))
    new1 = p["stage_lag_issued"]["new_1"]["by_year"]["all"]
    allnew = p["stage_lag_issued"]["all_new_homes"]["by_year"]["all"]
    adu = c["ranges"]["adu"]["application_to_permit"]
    return {
        "phl_id": str(p["id"]),
        "ca_id": str(c["id"]),
        "phl_new1_p50": float(new1["p50"]),
        "phl_new1_p90": float(new1["p90"]),
        "phl_all_p50": float(allnew["p50"]),
        "phl_all_p90": float(allnew["p90"]),
        "phl_cohort_censored_share": float(
            p["stage_lag_cohort"]["by_zoning_year"]["all"]["censored_share"]
        ),
        "ca_median_low": float(adu["median_days"]["low"]),
        "ca_median_high": float(adu["median_days"]["high"]),
        "ca_p75_low": float(adu["p75_days"]["low"]),
        "ca_p75_high": float(adu["p75_days"]["high"]),
    }


def lag_anchors() -> dict[str, Any]:
    """The observed and transferred lag figures behind the lag ranges.

    Read from ``assumptions/calibration/permit_lag_phl.yaml`` (Philadelphia
    zoning-to-building stage lag, Kaplan-Meier) and
    ``apr_timing_donor_ca.yaml`` (California ADU application to permit).
    Also gives the log spreads they imply: ``ln(P90 / P50) / 1.2816`` and
    ``ln(P75 / P50) / 0.6745``.
    """
    a = dict(_anchors(str(PHL_LAG_PATH), str(CA_DONOR_PATH)))
    a["phl_new1_log_sd"] = float(np.log(a["phl_new1_p90"] / a["phl_new1_p50"]) / Z90)
    a["phl_all_log_sd"] = float(np.log(a["phl_all_p90"] / a["phl_all_p50"]) / Z90)
    a["ca_log_sd_low"] = float(np.log(a["ca_p75_high"] / a["ca_median_high"]) / Z75)
    a["ca_log_sd_high"] = float(np.log(a["ca_p75_low"] / a["ca_median_low"]) / Z75)
    return a


# ---------------------------------------------------------------- financing


def cash_access(cash: Any, median: Any, shape: Any) -> np.ndarray:
    """Share of would-be builders whose savings or equity cover ``cash``.

    ``A(C) = 1 / (1 + (C / C50)^k)``.
    """
    c = np.maximum(np.asarray(cash, dtype=float), 0.0)
    return 1.0 / (1.0 + (c / np.asarray(median, dtype=float)) ** np.asarray(shape, dtype=float))


@dataclass(frozen=True)
class _Financing:
    breakeven: np.ndarray
    access_ratio: np.ndarray
    cash_before: np.ndarray
    cash_after: np.ndarray


def _financing(
    v: Mapping[str, np.ndarray],
    col: Mapping[str, np.ndarray],
    lines: Mapping[str, Any],
    breakeven: np.ndarray,
    rent: np.ndarray,
    program_payment: Any = None,
) -> _Financing:
    """Break-even rent and take-up factor with the financing lever (draws x tracts).

    ``program_payment`` replaces the program money's monthly payment per dollar
    (``payment_factor(program rate, term)``); the budget-capped program passes
    ``loan share x payment factor`` so that a grant costs the owner nothing a month.
    """
    shape = breakeven.shape
    cash0 = np.broadcast_to(np.asarray(lines["owner_cash"], dtype=float), shape)
    cash_f = np.broadcast_to(np.asarray(lines["cash_cost_factor"], dtype=float), shape)
    loan_f = np.broadcast_to(np.asarray(lines["loan_payment_factor"], dtype=float), shape)
    kept = np.broadcast_to(np.asarray(lines["share_kept"], dtype=float), shape)
    term = col["adu.loan_term_years"]
    if program_payment is None:
        program_payment = payment_factor(np.maximum(v[PROGRAM_RATE], 0.0)[:, None], term)
    prog_f = np.broadcast_to(np.asarray(program_payment, dtype=float), shape)
    program_cap = np.broadcast_to(np.maximum(v[PROGRAM][:, None], 0.0), shape)
    loan_cap = (
        np.maximum(v[RENT_SHARE][:, None], 0.0) * np.maximum(np.nan_to_num(rent), 0.0) / loan_f
    )
    # Each dollar of the cash need comes from the cheapest source first: the
    # source with the lower monthly payment per dollar fills first, the other
    # covers what is left (ties: program money first). A source dearer than the
    # owner's own cash saves nothing per dollar (min(cost, c)), but still lowers
    # the cash the owner must raise.
    program_first = prog_f <= loan_f
    first_cap = np.where(program_first, program_cap, loan_cap)
    second_cap = np.where(program_first, loan_cap, program_cap)
    first = np.minimum(first_cap, cash0)
    second = np.minimum(second_cap, cash0 - first)
    program = np.where(program_first, first, second)
    rent_loan = np.where(program_first, second, first)
    cash1 = cash0 - program - rent_loan
    saving = program * (cash_f - np.minimum(prog_f, cash_f)) + rent_loan * (
        cash_f - np.minimum(loan_f, cash_f)
    )
    new_breakeven = np.maximum(breakeven - col["adu.required_coverage"] * saving / kept, 0.0)
    a0 = cash_access(cash0, v[CASH_MEDIAN][:, None], v[CASH_SHAPE][:, None])
    a1 = cash_access(cash1, v[CASH_MEDIAN][:, None], v[CASH_SHAPE][:, None])
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(a0 > 0, a1 / np.where(a0 > 0, a0, 1.0), 1.0)
    return _Financing(new_breakeven, np.maximum(ratio, 1.0), cash0, cash1)


# ---------------------------------------------------------------- result


@dataclass(frozen=True)
class LeverResult:
    """What :func:`apply_levers` returns.

    Attributes:
        timing_active: The timing lever moves approval times in this run.
        financing_active: The financing lever changes cash or loans in this run.
        feasible, built, net: The stages with the levers, draws x tracts.
        p: Feasible share with the levers.
        year_share: ``(draws, years)`` year shares of the lever run.
        takeup_factor: Take-up x ramp sum x cash-access factor x timing factor.
        bench_net: State benchmark added units per draw (no lever).
        moved: State authorizations moved into the window per draw.
        newly: State newly feasible authorizations per draw.
        moved_by_year, newly_by_year: ``(draws, years)``.
        moved_county, newly_county: ``(counties, draws)``, rows in ``county_ids``.
        county_ids: County ids of those rows.
        records: State records (keys start ``lever_``).
        binding: ``{"factor", "text", "timing_p50", "financing_p50", ...}``.
        assumption_ids: Lever ledger ids used.
        extra: Hazard-method details (empty for the linear lever runs).
    """

    timing_active: bool
    financing_active: bool
    feasible: np.ndarray
    built: np.ndarray
    net: np.ndarray
    p: np.ndarray
    year_share: np.ndarray
    takeup_factor: np.ndarray
    bench_net: np.ndarray
    moved: np.ndarray
    newly: np.ndarray
    moved_by_year: np.ndarray
    newly_by_year: np.ndarray
    moved_county: np.ndarray
    newly_county: np.ndarray
    county_ids: tuple[str, ...]
    records: dict[str, Record] = field(default_factory=dict)
    binding: dict[str, Any] = field(default_factory=dict)
    assumption_ids: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)
    """Hazard-method runs (:mod:`visionhousing.scenarios.adu_budget`): ``method``,
    the hazard reference, the program's per-draw totals and, inside
    ``adu_budget.keep_inputs()``, the simulation inputs."""


def _active(ledger: Ledger, ids: Sequence[str]) -> bool:
    for pid in ids:
        if pid in ledger:
            e = ledger[pid]
            if e.low != 0 or e.central != 0 or e.high != 0:
                return True
    return False


def _with_sources(ledger: Ledger) -> Ledger:
    """The ledger with every lever setting at its variant range (for the binding text)."""
    over = {}
    for target, source in LEVER_SOURCES.items():
        if target in ledger and source in ledger and not _active(ledger, [target]):
            s = ledger[source]
            over[target] = {
                "central": s.central, "low": s.low, "high": s.high, "shape": s.shape,
                "kind": s.kind, "rationale": f"Takes the range of {source}.",
            }  # fmt: skip
    return apply_overrides(ledger, over) if over else ledger


def _timing_factor(
    v: Mapping[str, np.ndarray], horizon: int, delay: np.ndarray, ramp_fn: Callable[..., np.ndarray]
) -> dict[str, np.ndarray]:
    """Lag-model quantities per draw, per unit of benchmark in-window permits."""
    beyond = round(float(np.max(v[BEYOND])))
    span = horizon + max(beyond, 0)
    filings = ramp_fn(v[RAMP_IDS[0]], v[RAMP_IDS[1]], span, delay)  # (draws, span)
    window = filings.copy()
    window[:, horizon:] = 0.0  # applications filed 2027-2031 only
    i0 = issuance_by_year(window, v[MEDIAN], v[LOG_SD], 0.0)
    i1 = issuance_by_year(window, v[MEDIAN], v[LOG_SD], v[PULL])
    w0 = i0[:, :horizon].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(w0 > 0, 1.0 / np.where(w0 > 0, w0, 1.0), 0.0)  # per unit of benchmark
    apps = window.sum(axis=1) * scale
    return {
        "i0": i0[:, :horizon] * scale[:, None],
        "i1": i1[:, :horizon] * scale[:, None],
        "tau": i1[:, :horizon].sum(axis=1) * scale,
        "apps": apps,
        "after0": apps - 1.0,
        "after1": apps - i1[:, :horizon].sum(axis=1) * scale,
        "pending0": apps - i0.sum(axis=1) * scale,
        "pending1": apps - i1.sum(axis=1) * scale,
        "p90_0": np.exp(Z90 * v[LOG_SD]) * v[MEDIAN],
        "p90_1": v[MEDIAN]
        + (1.0 - np.clip(v[PULL], 0, 1)) * (np.exp(Z90 * v[LOG_SD]) - 1.0) * v[MEDIAN],
    }


def apply_levers(
    ledger: Ledger,
    *,
    draws: int,
    seed: int,
    years: Sequence[int],
    counties: np.ndarray,
    col: Mapping[str, np.ndarray],
    lines: Mapping[str, Any],
    breakeven: np.ndarray,
    rent: np.ndarray,
    fit_lots: np.ndarray,
    p_at: Callable[[np.ndarray], np.ndarray],
    p: np.ndarray,
    yearly: np.ndarray,
    ramp: np.ndarray,
    delay: np.ndarray,
    ramp_fn: Callable[..., np.ndarray],
    base_year_share: np.ndarray,
    net: np.ndarray,
    fit_lots_old: Callable[[], np.ndarray] | np.ndarray | None = None,
    comparable_share: np.ndarray | None = None,
) -> LeverResult | None:
    """Apply the timing and financing levers to one ADU run (``None`` when neither is on).

    Args:
        ledger: The run's ledger.
        draws: Number of draws.
        seed: The run's seed (each id has its own stream, so draws pair with the benchmark).
        years: Window years.
        counties: County per tract.
        col: The funnel's sampled ``(draws, 1)`` columns.
        lines: Pro forma lines (``owner_cash``, ``cash_cost_factor``, ...).
        breakeven: Benchmark break-even rent, draws x tracts.
        rent: ADU market rent, draws x tracts.
        fit_lots: Lots with room, draws x tracts.
        p_at: Feasible share for a given break-even rent (the funnel's rent test).
        p: Benchmark feasible share.
        yearly: Take-up x units per lot, ``(draws, 1)``.
        ramp: ``(draws, years)`` ramp factors.
        delay: Start delay per draw.
        ramp_fn: :func:`visionhousing.scenarios.adu.ramp_sum`.
        base_year_share: Benchmark year shares.
        net: Benchmark added units, draws x tracts.
        fit_lots_old: Lots with room where an ADU is already legal (draws x
            tracts), or a function that works them out; read only by the
            budget-capped program (:mod:`visionhousing.scenarios.adu_budget`).
        comparable_share: Each tract's detached (survey-comparable) share of
            its ADUs, draws x tracts; read only by the hazard-method runs.

    Returns:
        The lever result, or ``None`` for a benchmark run.
    """
    timing_on = _active(ledger, TIMING_TARGETS)
    fin_on = _active(ledger, FINANCING_TARGETS)
    budget_on = _active(ledger, adu_budget.BUDGET_TARGETS)
    hazard_on = budget_on or _active(ledger, (adu_budget.HAZARD,))
    if not (timing_on or fin_on or hazard_on):
        return None
    if hazard_on and fin_on:
        raise ValueError(
            "the hazard-method ADU runs (budget-capped program, conserved timing) replace the "
            "uncapped financing-access lever: leave adu.financing_program_amount and "
            "adu.financing_rent_income_share at 0 in these runs"
        )
    horizon = len(years)
    kept = 1.0 - col["adu.displacement_share"]
    ids = [*TIMING_IDS, *FINANCING_IDS, BINDING]
    if hazard_on:
        ids += [adu_budget.HAZARD, *adu_budget.BUDGET_IDS]
    missing = [i for i in ids if i not in ledger]
    if missing:
        raise KeyError(f"the ADU levers need ledger entries {missing}")
    # The run's own settings drive every applied number. The binding text alone
    # compares each lever at its variant range, so it samples a second ledger
    # where the levers that are off take those ranges (same seed, same streams).
    v = sample(ledger, n=draws, seed=seed, ids=ids)
    lever_ledger = _with_sources(ledger)
    v_pot = sample(lever_ledger, n=draws, seed=seed, ids=ids)
    delay = np.asarray(delay, dtype=float)
    take = yearly * ramp.sum(axis=1)[:, None]

    def fin_pool(values: Mapping[str, np.ndarray]) -> tuple[_Financing, np.ndarray, np.ndarray]:
        f = _financing(values, col, lines, breakeven, rent)
        p_f = p_at(f.breakeven)
        feas = fit_lots * p_f
        return f, p_f, np.minimum(feas * take * f.access_ratio, feas) * kept

    if hazard_on:
        bench_state = net.sum(axis=1)
        t_pot = _timing_factor(v_pot, horizon, delay, ramp_fn)
        binding = _binding(
            float(np.median(bench_state * (t_pot["tau"] - 1.0))),
            float(np.median(fin_pool(v_pot)[2].sum(axis=1) - bench_state)),
            float(np.median(bench_state)), float(ledger[BINDING].central),
        )  # fmt: skip
        used = _hazard_ids(ledger, timing_on, budget_on)
        inp = _hazard_inputs(
            v, col=col, lines=lines, breakeven=breakeven, rent=rent, p_at=p_at, p=p,
            fit_lots=fit_lots, fit_lots_old=fit_lots_old if budget_on else None,
            comparable_share=comparable_share, yearly=yearly, delay=delay, ramp_fn=ramp_fn,
            years=years, counties=counties, kept=kept, ids=used,
        )  # fmt: skip
        a = lag_anchors()
        led_src = {s for i in used if i in ledger for s in ledger[i].source_ids}
        srcs = sorted({a["phl_id"], a["ca_id"], "phl_li_permits", "hcd_apr_table_a", *led_src})
        return adu_budget.hazard_variant(
            inp, ledger=ledger, timing_on=timing_on, budget_on=budget_on, bench_net_tract=net,
            binding=binding, ids=used, sources=srcs,
        )  # fmt: skip

    # Financing (applied only when on).
    fin, p_fin, net_fin = fin_pool(v)
    feasible_fin = fit_lots * p_fin
    if fin_on:
        feasible, p_use, net_pool = feasible_fin, p_fin, net_fin
        access = fin.access_ratio
    else:
        feasible, p_use, net_pool = fit_lots * p, p, net
        access = np.ones_like(fit_lots)

    # Timing.
    t = _timing_factor(v, horizon, delay, ramp_fn)
    tau = t["tau"] if timing_on else np.ones(draws)
    built = np.minimum(net_pool / np.where(kept > 0, kept, 1.0) * tau[:, None], feasible)
    final = built * kept

    bench_state = net.sum(axis=1)
    pool_state = net_pool.sum(axis=1)
    final_state = final.sum(axis=1)
    newly = pool_state - bench_state
    moved = final_state - pool_state
    rs = np.asarray(base_year_share, dtype=float)
    diff_year = (t["i1"] - t["i0"]) if timing_on else np.zeros_like(rs)
    raw_by_year = pool_state[:, None] * diff_year
    # A tract cannot build more than its feasible lots, so the moved total can
    # be below the lag model's pool x (W'/W - 1). The yearly split is scaled to
    # the moved total actually kept, so the years add up to the paired change.
    raw = raw_by_year.sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        keep = np.where(raw != 0, moved / np.where(raw != 0, raw, 1.0), 0.0)
    moved_by_year = raw_by_year * keep[:, None]
    newly_by_year = newly[:, None] * rs
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(
            final_state[:, None] > 0,
            (pool_state[:, None] * rs + moved_by_year)
            / np.where(final_state > 0, final_state, 1.0)[:, None],
            rs,
        )
    share = np.clip(share, 0.0, None)
    share = share / np.where(
        share.sum(axis=1, keepdims=True) > 0, share.sum(axis=1, keepdims=True), 1.0
    )

    cids = tuple(sorted(set(str(c) for c in counties)))
    cmask = [np.asarray(counties).astype(str) == c for c in cids]
    moved_c = np.stack([(final - net_pool)[:, m].sum(axis=1) for m in cmask])
    newly_c = np.stack([(net_pool - net)[:, m].sum(axis=1) for m in cmask])

    used = sorted(
        {*(i for i in TIMING_IDS if timing_on and i in ledger),
         *(i for i in FINANCING_IDS if fin_on and i in ledger)}
    )  # fmt: skip
    used += [LEVER_SOURCES[k] for k in LEVER_SOURCES if k in used and LEVER_SOURCES[k] in ledger]
    records = _records(
        ledger, years, used, timing_on, fin_on, bench_state, final_state, moved, newly,
        moved_by_year, newly_by_year, t, fin, feasible_fin, pool_state,
    )  # fmt: skip

    # Binding factor: the potential of each lever on these draws (variant ranges).
    t_pot = _timing_factor(v_pot, horizon, delay, ramp_fn)
    timing_pot = bench_state * (t_pot["tau"] - 1.0)  # on the benchmark pool, same in every run
    fin_pot = fin_pool(v_pot)[2].sum(axis=1) - bench_state
    binding = _binding(
        float(np.median(timing_pot)), float(np.median(fin_pot)), float(np.median(bench_state)),
        float(ledger[BINDING].central),
    )  # fmt: skip
    return LeverResult(
        timing_active=timing_on,
        financing_active=fin_on,
        feasible=feasible,
        built=built,
        net=final,
        p=p_use,
        year_share=share,
        takeup_factor=np.asarray(take * access * tau[:, None], dtype=float),
        bench_net=bench_state,
        moved=moved,
        newly=newly,
        moved_by_year=moved_by_year,
        newly_by_year=newly_by_year,
        moved_county=moved_c,
        newly_county=newly_c,
        county_ids=cids,
        records=records,
        binding=binding,
        assumption_ids=tuple(sorted(set(used))),
    )


HAZARD_FUNNEL_IDS = (
    "adu.takeup_feasible_annual",
    "adu.units_per_lot",
    "adu.displacement_share",
    "adu.loan_term_years",
)
"""Funnel ids the hazard simulation reads directly (take-up, units, displacement, loan term)."""


def _hazard_ids(ledger: Ledger, timing_on: bool, budget_on: bool) -> list[str]:
    """Ledger ids behind a hazard-method run's numbers."""
    ids = {*TIMING_IDS, adu_budget.HAZARD, adu_budget.SOURCES[adu_budget.HAZARD],
           *HAZARD_FUNNEL_IDS}  # fmt: skip
    if timing_on:
        ids.add(LEVER_SOURCES[PULL])
    if budget_on:
        ids |= {*adu_budget.BUDGET_IDS, PROGRAM_RATE, CASH_MEDIAN, CASH_SHAPE}
        ids |= {adu_budget.SOURCES[k] for k in adu_budget.BUDGET_TARGETS}
        ids.add(adu_budget.SOURCES[adu_budget.LOAN])
    return sorted(i for i in ids if i in ledger)


def _hazard_inputs(
    v: Mapping[str, np.ndarray],
    *,
    col: Mapping[str, np.ndarray],
    lines: Mapping[str, Any],
    breakeven: np.ndarray,
    rent: np.ndarray,
    p_at: Callable[[np.ndarray], np.ndarray],
    p: np.ndarray,
    fit_lots: np.ndarray,
    fit_lots_old: Callable[[], np.ndarray] | np.ndarray | None,
    comparable_share: np.ndarray | None,
    yearly: np.ndarray,
    delay: np.ndarray,
    ramp_fn: Callable[..., np.ndarray],
    years: Sequence[int],
    counties: np.ndarray,
    kept: np.ndarray,
    ids: Sequence[str],
) -> adu_budget.HazardInputs:
    """The hazard simulation's inputs from one ADU run (same draws as the benchmark)."""
    horizon = len(years)
    span = horizon + max(round(float(np.max(v[BEYOND]))), 0)
    d = int(np.asarray(yearly).shape[0])
    ramp = ramp_fn(v[RAMP_IDS[0]], v[RAMP_IDS[1]], span, delay)
    ramp0 = ramp_fn(v[RAMP_IDS[0]], v[RAMP_IDS[1]], span, 0.0)
    k_new, k_old = adu_budget.calibration(ramp0, v[MEDIAN], v[LOG_SD], horizon, span)
    old = fit_lots_old() if callable(fit_lots_old) else fit_lots_old
    term = np.broadcast_to(np.asarray(col["adu.loan_term_years"], dtype=float), (d, 1))
    rate = np.maximum(np.asarray(v[PROGRAM_RATE], dtype=float), 0.0)
    shape = np.broadcast_to(np.asarray(p, dtype=float), np.shape(fit_lots)).shape

    def program(award: float, loan_share: float, appropriation: Any) -> adu_budget.Program:
        vv = dict(v)
        vv[PROGRAM] = np.full(d, float(award))
        vv[RENT_SHARE] = np.zeros(d)
        pay = float(loan_share) * payment_factor(rate[:, None], term)
        f = _financing(vv, col, lines, breakeven, rent, program_payment=pay)
        p_award = np.maximum(np.broadcast_to(p_at(f.breakeven), shape), p)
        cash0 = np.broadcast_to(np.asarray(f.cash_before, dtype=float), shape)
        return adu_budget.Program(
            appropriation=np.broadcast_to(np.asarray(appropriation, dtype=float), (d,)).copy(),
            award=float(award),
            loan_share=float(loan_share),
            p_award=p_award,
            ratio=np.broadcast_to(f.access_ratio, shape),
            spend_per_award=np.minimum(float(award), cash0),
            eligible=np.asarray(v[adu_budget.ELIGIBLE], dtype=float),
            takeup=np.asarray(v[adu_budget.TAKEUP], dtype=float),
            admin=np.asarray(v[adu_budget.ADMIN], dtype=float),
            loss=np.asarray(v[adu_budget.LOSS], dtype=float),
            discount=np.asarray(v[adu_budget.DISCOUNT], dtype=float),
            rate=rate,
            term=term[:, 0],
        )

    return adu_budget.HazardInputs(
        years=tuple(years),
        span=span,
        fit_new=np.asarray(fit_lots, dtype=float),
        fit_old=None if old is None else np.asarray(old, dtype=float),
        p=np.broadcast_to(np.asarray(p, dtype=float), shape),
        comparable_share=comparable_share,
        hazard=np.asarray(yearly, dtype=float),
        ramp=ramp,
        kappa_new=k_new,
        kappa_old=k_old,
        kept=np.asarray(kept, dtype=float),
        median=np.asarray(v[MEDIAN], dtype=float),
        log_sd=np.asarray(v[LOG_SD], dtype=float),
        counties=np.asarray(counties),
        values=v,
        program=program,
        assumption_ids=tuple(ids),
    )


def _binding(timing: float, financing: float, bench: float, floor: float) -> dict[str, Any]:
    """Which factor binds: timing, financing, or eligibility when neither lever moves much."""
    limit = floor * max(bench, 0.0)
    if max(timing, financing) < limit or max(timing, financing) <= 0:
        factor = "eligibility"
        text = (
            f"Neither faster approval (P50 {timing:,.0f} authorizations moved into the window) "
            f"nor financing access (P50 {financing:,.0f} newly feasible) reaches "
            f"{floor:.0%} of the benchmark's P50 ({bench:,.0f}). The largest remaining "
            "constraint in this group is eligibility: who gains a legal right to build an ADU "
            "and has room for one."
        )
    elif financing >= timing:
        factor = "financing"
        text = (
            "This case estimates which authorizations move into the five-year window when "
            f"review is faster (P50 {timing:,.0f}) and which become newly feasible with "
            f"financing access (P50 {financing:,.0f}). The largest remaining constraint in "
            "this group is household financing under the selected assumptions."
        )
    else:
        factor = "timing"
        text = (
            "This case estimates which authorizations move into the five-year window when "
            f"review is faster (P50 {timing:,.0f}) and which become newly feasible with "
            f"financing access (P50 {financing:,.0f}). The largest remaining constraint in "
            "this group is approval timing under the selected assumptions (illustrative/"
            "transferred lag)."
        )
    return {
        "factor": factor,
        "text": text,
        "timing_p50": timing,
        "financing_p50": financing,
        "benchmark_p50": bench,
        "floor_share": floor,
        "assumption_ids": sorted({BINDING, *TIMING_IDS, *FINANCING_IDS, *LEVER_SOURCES.values()}),
        "rule": (
            "binding = the lever with the larger P50 effect on these draws (timing: benchmark "
            "authorizations moved into the window; financing: newly feasible), each at its "
            "variant range; eligibility "
            "when both are below adu.lever_binding_min_share x the benchmark P50"
        ),
    }


def _records(
    ledger: Ledger,
    years: Sequence[int],
    used: Sequence[str],
    timing_on: bool,
    fin_on: bool,
    bench: np.ndarray,
    final: np.ndarray,
    moved: np.ndarray,
    newly: np.ndarray,
    moved_by_year: np.ndarray,
    newly_by_year: np.ndarray,
    t: Mapping[str, np.ndarray],
    fin: _Financing,
    feasible_fin: np.ndarray,
    pool: np.ndarray,
) -> dict[str, Record]:
    period = f"{years[0]}-{years[-1]}"
    ids = sorted(set(used))
    a = lag_anchors()
    srcs = sorted({a["phl_id"], a["ca_id"], "phl_li_permits", "hcd_apr_table_a"})
    t_ids = sorted({*ids, *(i for i in TIMING_IDS if i in ledger)})
    out: dict[str, Record] = {}

    def put(key: str, name: str, draws: np.ndarray, unit: str, expr: str, aids=ids, sources=()):
        out[key] = rec.from_draws(
            name, np.asarray(draws, dtype=float), unit, expression=expr,
            assumption_ids=aids, source_ids=sources, tier=0,
        )  # fmt: skip

    put(
        "lever_benchmark_net",
        f"benchmark added units authorized {period} (same draws)",
        bench,
        "units",
        "the sum over tracts of the benchmark's added units, before any lever",
    )
    put(
        "lever_net",
        f"added units authorized {period} with the levers",
        final,
        "units",
        "(benchmark added units + newly feasible) x timing factor W'/W, summed over tracts",
    )
    put(
        "lever_paired_change",
        f"change in added units authorized {period} vs the benchmark",
        final - bench,
        "units",
        "lever added units minus benchmark added units in the same draw "
        "= newly feasible + moved into the window",
    )
    put("lever_moved_into_window", f"authorizations moved into {period} (timing, {LABEL})",
        moved, "units",
        "(added units with financing, under today's lag) x (W'/W - 1), where W and W' are the "
        "permits issued in the window under today's lag F and the pulled lag F' "
        "(F'(x) = F(m + (x - m)/(1 - pi)) above the median m)"
        + ("" if timing_on else "; 0, timing lever off"),
        t_ids if timing_on else ids, srcs if timing_on else ())  # fmt: skip
    put(
        "lever_newly_feasible",
        f"newly feasible authorizations {period} (financing)",
        newly,
        "units",
        "added units with the financing lever minus the benchmark's, both under today's lag: "
        "lots with room x p(break-even rent lowered by the program and counted rent) x take-up "
        "x A(C1)/A(C0) x (1 - displacement)" + ("" if fin_on else "; 0, financing lever off"),
    )
    for h, year in enumerate(years):
        put(
            f"lever_change_{year}",
            f"change in added units authorized in {year} vs the benchmark",
            moved_by_year[:, h] + newly_by_year[:, h],
            "units",
            f"newly feasible x the benchmark's year share for {year} + pool x (I'({year}) - "
            f"I({year})) x kept, I = lag-model permits issued per unit of benchmark in-window "
            "permits, kept = moved into the window / (pool x (W'/W - 1)) (the feasible-lot cap); "
            "the years add up to the paired change",
        )
    if timing_on:
        apps = pool * t["apps"]
        put(
            "lever_window_applications",
            f"applications filed {period} that are eventually issued ({LABEL})",
            apps,
            "units",
            "permits in the window under today's lag x (applications filed in the window / "
            "permits they give in the window under today's lag); the same with or without the "
            "timing lever (applicants and the issue chance are fixed)",
            t_ids,
            srcs,
        )
        put(
            "lever_after_window_benchmark",
            f"permits after {years[-1]} from applications filed {period}, today's lag",
            pool * t["after0"],
            "units",
            "applications filed in the window - permits issued in "
            "the window, today's lag (includes those still pending at the end)",
            t_ids,
            srcs,
        )
        put(
            "lever_after_window",
            f"permits after {years[-1]} from applications filed {period}, with the timing lever",
            pool * t["after1"],
            "units",
            "applications filed in the window - permits issued in "
            "the window, pulled lag (includes those still pending at the end)",
            t_ids,
            srcs,
        )
        put(
            "lever_pending_at_end",
            "applications filed in the window still pending at the end of the simulation "
            "(right-censored), with the timing lever",
            pool * t["pending1"],
            "units",
            "applications filed in the window - permits issued by the end of the simulation "
            "(adu.timing_simulation_years_beyond after the window)",
            t_ids,
            srcs,
        )
        put(
            "lever_lag_p90_days_today",
            f"P90 days application to permit, today ({LABEL})",
            t["p90_0"],
            "days",
            "m x exp(1.2816 x s)",
            t_ids,
            srcs,
        )
        put(
            "lever_lag_p90_days_reliable",
            f"P90 days application to permit, predictable approval ({LABEL})",
            t["p90_1"],
            "days",
            "m + (1 - pi) x (m x exp(1.2816 x s) - m)",
            t_ids,
            srcs,
        )
    if fin_on:
        w = np.where(feasible_fin.sum(axis=1, keepdims=True) > 0, feasible_fin, 1.0)
        put(
            "lever_owner_cash_before",
            "owner cash per ADU before the financing lever (feasible-lot weighted)",
            (fin.cash_before * w).sum(axis=1) / w.sum(axis=1),
            "$/unit",
            "total cost x (1 - loan share), averaged over tracts weighted by feasible lots",
        )
        put(
            "lever_owner_cash_after",
            "owner cash per ADU with the financing lever (feasible-lot weighted)",
            (fin.cash_after * w).sum(axis=1) / w.sum(axis=1),
            "$/unit",
            "C0 - program money - counted-rent loan, each dollar of C0 from the cheaper offer "
            "first (program amount; counted rent share x rent / loan payment per $), averaged "
            "over tracts weighted by feasible lots",
        )
        put(
            "lever_cash_access_factor",
            "take-up factor from lower owner cash A(C1)/A(C0) (feasible-lot weighted)",
            (fin.access_ratio * w).sum(axis=1) / w.sum(axis=1),
            "ratio",
            "A(C) = 1 / (1 + (C / C50)^k), ratio at the new and old cash need",
        )
    return out


def lever_stage_records(
    records: Mapping[str, Record], lv: LeverResult, ledger: Ledger
) -> dict[str, Record]:
    """The funnel's feasible, built and net records again, naming the levers, plus lever records.

    The draws are already the lever run's (the stages were replaced); this adds
    the lever ids and words to the formula so the headline is traceable.
    """
    out: dict[str, Record] = dict(lv.records)
    words = []
    if lv.extra.get("method") == "hazard":
        words.append(
            f"yearly hazard on surviving eligible sites and the application-to-permit lag ({LABEL})"
        )
        if lv.financing_active:
            words.append("a budget-capped program (awards first come, first served)")
        if lv.timing_active:
            words.append("faster review (slow tail pulled to the median)")
    elif lv.financing_active:
        words.append("financing lever (program cash, counted rent, A(C1)/A(C0))")
    if lv.timing_active:
        words.append(f"timing factor W'/W ({LABEL} lag, slow tail pulled to the median)")
    for key in ("feasible", "built", "net"):
        if key not in records or (
            key == "feasible" and (not lv.financing_active or lv.extra.get("method") == "hazard")
        ):
            continue
        old = records[key]
        ids = sorted({*old.assumption_ids, *lv.assumption_ids})
        out[key] = old.model_copy(
            update={
                "formula": f"{old.formula}; with the {' and '.join(words)}",
                "assumption_ids": tuple(ids),
                "source_ids": tuple(
                    sorted(
                        {
                            *old.source_ids,
                            *(
                                s
                                for i in lv.assumption_ids
                                if i in ledger
                                for s in ledger[i].source_ids
                            ),
                        }
                    )
                ),
            }
        )
    return out


# ---------------------------------------------------------------- readers


def lever_of(result: Any) -> LeverResult | None:
    """The lever result inside an engine reform outcome or result (or ``None``)."""
    for obj in (result, getattr(result, "result", None)):
        detail = getattr(obj, "detail", None)
        lv = getattr(detail, "levers", None)
        if isinstance(lv, LeverResult):
            return lv
    return None


def outcome_timing(result: Any, label: str, years: Sequence[int]) -> dict[str, Any] | None:
    """The outcome contract's timing split for a lever run (``None`` otherwise).

    ``shifted_into_window`` = authorizations moved into the window by the timing
    lever; ``newly_feasible`` = the five-year total minus those.
    """
    lv = lever_of(result)
    if lv is None:
        return None
    total = lv.net.sum(axis=1)
    ids = list(lv.assumption_ids)
    shifted = rec.from_draws(
        f"{label}: authorizations shifted into {years[0]}-{years[-1]}, state",
        lv.moved, "units",
        expression="permits issued in the window under the pulled lag minus under today's lag "
        f"({LABEL}); applications and the issue chance fixed",
        assumption_ids=ids, tier=0,
    )  # fmt: skip
    newly = rec.from_draws(
        f"{label}: newly feasible authorizations {years[0]}-{years[-1]}, state",
        total - lv.moved, "units",
        expression="the five-year total less authorizations shifted into the window",
        assumption_ids=ids, tier=0,
    )  # fmt: skip
    note = (
        "The timing lever moves permits that would have been issued after the window into it; "
        "it adds no application. Shifted and newly feasible are reported apart."
        if lv.timing_active
        else "Only the financing lever is on: no permit moves in time."
    )
    return {
        "mechanism": bool(lv.timing_active),
        "shifted_into_window": shifted,
        "newly_feasible": newly,
        "note": note,
    }


def card_fields(variant_outcome: Any, county: str | None = None) -> dict[str, Any]:
    """Extra lever-card fields for an ADU lever variant run (empty for other runs).

    Returns ``{"lever": {"moved", "newly", "binding", "county_moved", "county_newly"}}``
    with P10 / P50 / P90 records (``model_dump`` form).
    """
    lv = lever_of(variant_outcome)
    if lv is None:
        return {}
    out: dict[str, Any] = {
        "moved": lv.records["lever_moved_into_window"].model_dump(mode="json"),
        "newly": lv.records["lever_newly_feasible"].model_dump(mode="json"),
        "binding": dict(lv.binding),
        "years": {
            k.removeprefix("lever_change_"): r.model_dump(mode="json")
            for k, r in lv.records.items()
            if k.startswith("lever_change_")
        },
    }
    if county is not None and str(county) in lv.county_ids:
        cid = str(county)
        row = lv.county_ids.index(cid)
        for key, arr in (("county_moved", lv.moved_county), ("county_newly", lv.newly_county)):
            what = "moved into the window" if key == "county_moved" else "newly feasible"
            out[key] = rec.from_draws(
                f"authorizations {what}, county {cid}",
                arr[row],
                "units",
                expression=f"the sum over the county's tracts of the authorizations {what}",
                assumption_ids=lv.assumption_ids,
                tier=0,
            ).model_dump(mode="json")
    return {"lever": out}
