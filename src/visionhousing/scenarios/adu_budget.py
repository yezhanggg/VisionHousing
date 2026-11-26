"""ADU program budget, predictable review and take-up on a finite stock of sites.

Plain-language summary
----------------------
The ADU two-lever case (:mod:`visionhousing.scenarios.adu_levers`) asks what
faster review or easier financing would add. Its financing case has no budget:
every owner who wants help gets it. This module adds the three things a
committee needs to make that a real choice:

1. **A budget-capped program.** An appropriation pays a fixed award per project
   (a grant or a loan). Part of the money runs the program (administration).
   Only eligible owners can apply and only some of them do (award take-up).
   Awards go first come, first served within each year (pro rata when a year's
   requests exceed what is left), to owners who would have built anyway as
   well as to owners the award brings in. When the money is gone, no further
   award is made; what is never asked for is reported as unused funds.
2. **Take-up on a finite stock.** Each year a share of the eligible sites that
   still have no ADU application files one: ``projects_t = remaining_sites_t x
   hazard_t``, then ``remaining_sites_(t+1) = remaining_sites_t - projects_t``.
   A site used early cannot be used again later, so a program that speeds
   owners up also takes projects from later years.
3. **Applications reach a permit after a lag.** Applications are convolved with
   the application-to-permit lag (:func:`visionhousing.scenarios.adu_levers.lag_cdf`),
   so projects filed late in the window pass 2031. The simulation runs
   ``adu.timing_simulation_years_beyond`` more years; projects still waiting at
   the end are reported as pending (right-censored), never dropped.

The benchmark keeps its linear take-up (feasible lots x take-up x sum of the
ramp, capped at the feasible lots). Only the new variants use this method
(``adu.lever_hazard_method``). The yearly hazard is the funnel's take-up x ramp
scaled once per draw (``kappa``) so that, with no depletion and today's lag,
permits in 2027-2031 equal the benchmark's (the take-up evidence is counted in
permits). ``kappa`` is set with no start delay and today's lag, so a delayed
start or a longer lag lowers in-window permits instead of being normalised
away. The difference between the hazard reference and the benchmark is
reported as a disclosed method sensitivity (``hazard_method_difference``).

Pools of sites
--------------
- **Newly legal sites** (the benchmark's feasible lots): ``lots with room x p``
  on lots the law change reaches, with the ramp after enactment.
- **Already legal sites**: the same tracts' lots where an ADU is already
  legal (the already-legal share), at the full take-up rate. Their projects are
  in today's baseline; they matter only because a program pays them too.
- In each pool, **group 1** sites are feasible without help; **group 2** sites
  pass the rent test only with the award (``lots with room x (p_award - p)``).
  An award raises the yearly hazard by ``r = A(C1) / A(C0)`` (owners who could
  not raise the cash). Group 2 sites build only with an award.

A year with the program: ``q = eligible x award take-up x funded share`` of
the remaining sites are funded applicants; ``applications = R1 x (hazard x (1 - q)
+ min(hazard x r, 1) x q) + R2 x min(hazard x r, 1) x q``. Awards made
``= q x min(hazard x r, 1) x (R1 + R2)``; those to owners who would have filed
that year anyway ``= q x hazard x R1``. Spending ``= awards x min(award, owner
cash)``.

Paired arms
-----------
Every arm uses the same draws and the same candidate cells (tracts and their
sites): **no change** (already legal sites, no program), **law only**,
**finance only** (today's law and the program), **both**. Added units against
no change = ``(1 - displacement) x (permits in the arm - permits with no change)``.
A program's own effect is ``both - law only`` on the same draws.

Records (per draw, P10 / P50 / P90 with formula and ids)
--------------------------------------------------------
Applicants served, awards to owners who would have built anyway, additional
in-window authorizations (headline scope and the survey-comparable detached
scope), units moved into the window from later years (the same sites, paired
across arms, so never negative), new projects whose permits land after the
window, units newly created,
net public cost, unused funds, loan repayments, cost per additional comparable
unit (only in draws with a gain; the share of zero-gain draws is reported
beside it, never folded into a ratio of percentiles), and the awards, award
take-up and appropriation a target would need.

:func:`budget_grid` runs a small grid of appropriations x awards x arms on the
same draws for the budget-and-target comparison (``assumptions/adu_budget_grid.yaml``);
:func:`grid_lookup` answers a query against it without interpolating.
"""

from __future__ import annotations

import contextlib
import contextvars
import itertools
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.ledger.loader import ROOT, Ledger, read_yaml
from visionhousing.proforma._calc import payment_factor

__all__ = [
    "APPROPRIATION",
    "AWARD",
    "BUDGET_IDS",
    "BUDGET_TARGETS",
    "GRID_PATH",
    "HAZARD",
    "HazardInputs",
    "Program",
    "Sim",
    "budget_grid",
    "grid_from_inputs",
    "grid_lookup",
    "in_window_share",
    "keep_inputs",
    "load_grid_settings",
    "permits_by_year",
    "simulate",
]

HAZARD = "adu.lever_hazard_method"
APPROPRIATION = "adu.budget_appropriation"
AWARD = "adu.budget_award_amount"
LOAN = "adu.budget_loan_share"
ELIGIBLE = "adu.budget_eligible_share"
TAKEUP = "adu.budget_award_takeup"
ADMIN = "adu.budget_admin_share"
LOSS = "adu.budget_loan_loss_share"
DISCOUNT = "adu.budget_public_discount_rate"
TARGET = "adu.budget_target_additional_units"
MIN_GAIN = "adu.budget_min_gain_units"
PROGRAM_RATE = "adu.financing_program_rate"

BUDGET_TARGETS = (APPROPRIATION, AWARD)
"""Lever settings that turn the budget-capped program on (0 = benchmark)."""
BUDGET_IDS = (APPROPRIATION, AWARD, LOAN, ELIGIBLE, TAKEUP, ADMIN, LOSS, DISCOUNT, TARGET, MIN_GAIN)
"""Ledger ids the program reads."""
SOURCES = {
    HAZARD: "adu.lever_hazard_method_on",
    APPROPRIATION: "adu.budget_appropriation_illustrative",
    AWARD: "adu.budget_award_amount_illustrative",
    LOAN: "adu.budget_loan_share_loan",
}
"""Each lever setting and the ledger entry its variants take the value of."""

GRID_PATH = ROOT / "assumptions" / "adu_budget_grid.yaml"
ARMS = ("law_only", "finance_only", "both")
LABEL = "illustrative/transferred"
"""Label on every lag-dependent number: no Pennsylvania application dates exist."""

_KEEP = contextvars.ContextVar("adu_budget_keep_inputs", default=False)


@contextlib.contextmanager
def keep_inputs() -> Iterator[None]:
    """Keep the hazard inputs on a run's lever result (``extra["inputs"]``) for the grid.

    Off by default, so a cached run does not hold the extra tract arrays.
    """
    token = _KEEP.set(True)
    try:
        yield
    finally:
        _KEEP.reset(token)


def keeping_inputs() -> bool:
    """Whether :func:`keep_inputs` is active."""
    return bool(_KEEP.get())


# ---------------------------------------------------------------- inputs


@dataclass(frozen=True)
class Program:
    """One program setting worked out on the run's draws.

    Attributes:
        appropriation: Dollars per draw ``(draws,)``.
        award: Award per project ($).
        loan_share: Share of each award paid as a loan.
        p_award: Feasible share with the award, draws x tracts (at least ``p``).
        ratio: Hazard multiplier with the award ``A(C1) / A(C0)``, draws x tracts.
        spend_per_award: ``min(award, owner cash)``, draws x tracts.
        eligible, takeup, admin, loss, discount, rate: Per draw ``(draws,)``.
        term: Loan term in years per draw ``(draws,)``.
    """

    appropriation: np.ndarray
    award: float
    loan_share: float
    p_award: np.ndarray
    ratio: np.ndarray
    spend_per_award: np.ndarray
    eligible: np.ndarray
    takeup: np.ndarray
    admin: np.ndarray
    loss: np.ndarray
    discount: np.ndarray
    rate: np.ndarray
    term: np.ndarray

    @property
    def funds(self) -> np.ndarray:
        """Money for awards: appropriation x (1 - administration share)."""
        return self.appropriation * (1.0 - self.admin)

    def used(self, spend: np.ndarray) -> np.ndarray:
        """Appropriation used: award spending / (1 - administration share)."""
        return spend / np.where(self.admin < 1.0, 1.0 - self.admin, 1.0)

    def admin_cost(self, spend: np.ndarray) -> np.ndarray:
        """Administration on the money used: appropriation used x administration share."""
        return self.used(spend) * self.admin

    def unused(self, spend: np.ndarray) -> np.ndarray:
        """Appropriation never used (returned): appropriation - appropriation used."""
        return np.maximum(self.appropriation - self.used(spend), 0.0)

    @property
    def capacity_before_admin(self) -> np.ndarray:
        """Awards the appropriation could pay with no administration: appropriation / award."""
        return self.appropriation / self.award if self.award > 0 else np.zeros_like(self.admin)

    def loan_subsidy_share(self) -> np.ndarray:
        """Share of repaid loan principal the state gives up in interest.

        ``max(0, 1 - payment factor at the program rate / payment factor at the
        public discount rate)``: the present value of the payments, per dollar.
        """
        pf_r = payment_factor(np.maximum(self.rate, 0.0), self.term)
        pf_d = payment_factor(np.maximum(self.discount, 0.0), self.term)
        return np.clip(1.0 - pf_r / pf_d, 0.0, 1.0)

    def net_cost(self, spend: np.ndarray) -> np.ndarray:
        """Net public cost of a draw's spending (administration included).

        ``administration + grants + loans x (loss + (1 - loss) x interest subsidy)``.
        """
        loans = spend * self.loan_share
        grants = spend - loans
        per_loan = self.loss + (1.0 - self.loss) * self.loan_subsidy_share()
        return self.admin_cost(spend) + grants + loans * per_loan

    def repayments(self, spend: np.ndarray) -> np.ndarray:
        """Loan principal repaid: loans x (1 - loss share)."""
        return spend * self.loan_share * (1.0 - self.loss)


@dataclass(frozen=True)
class HazardInputs:
    """Everything the hazard simulation needs from one ADU run (same draws).

    Attributes:
        years: Window years (2027-2031).
        span: Years simulated (window + ``adu.timing_simulation_years_beyond``).
        fit_new: Lots with room on newly legal lots, draws x tracts.
        fit_old: Lots with room on already legal lots, draws x tracts (or ``None``).
        p: Feasible share without a program, draws x tracts.
        comparable_share: Share of each tract's ADUs that are detached (the
            survey-comparable work type), draws x tracts (or ``None``).
        hazard: Yearly take-up x units per lot, ``(draws, 1)``.
        ramp: Ramp factors over the span with the run's start delay, ``(draws, span)``.
        kappa_new, kappa_old: Calibration per draw ``(draws,)`` (see the module summary).
        kept: 1 - displacement share, ``(draws, 1)``.
        median, log_sd: Today's lag per draw ``(draws,)``.
        counties: County per tract.
        values: The run's sampled lever and budget ids, per draw.
        program: ``(award, loan_share, appropriation) -> Program`` on these draws.
        assumption_ids: Ledger ids behind every number.
    """

    years: tuple[int, ...]
    span: int
    fit_new: np.ndarray
    fit_old: np.ndarray | None
    p: np.ndarray
    comparable_share: np.ndarray | None
    hazard: np.ndarray
    ramp: np.ndarray
    kappa_new: np.ndarray
    kappa_old: np.ndarray
    kept: np.ndarray
    median: np.ndarray
    log_sd: np.ndarray
    counties: np.ndarray
    values: Mapping[str, np.ndarray]
    program: Callable[[float, float, Any], Program]
    assumption_ids: tuple[str, ...] = ()

    @property
    def draws(self) -> int:
        """Number of draws."""
        return int(self.hazard.shape[0])

    @property
    def horizon(self) -> int:
        """Window length in years."""
        return len(self.years)


def in_window_share(
    median: Any, log_sd: Any, pull: Any, horizon: int, span: int, draws: int
) -> np.ndarray:
    """Share of a year's applications issued inside the window, ``(draws, span)``.

    Applications of year ``t`` are filed at 12 mid-month times ``s``; one filed
    at ``s`` is issued by the end of the window with probability
    ``F((horizon - s) x 365.25)``. Years after the window give 0.
    """
    from visionhousing.scenarios.adu_levers import DAYS_PER_YEAR, STEPS_PER_YEAR, lag_cdf

    m = np.broadcast_to(np.asarray(median, dtype=float), (draws,))[:, None]
    s = np.broadcast_to(np.asarray(log_sd, dtype=float), (draws,))[:, None]
    pi = np.broadcast_to(np.asarray(pull, dtype=float), (draws,))[:, None]
    times = (np.arange(span * STEPS_PER_YEAR) + 0.5) / STEPS_PER_YEAR
    gap = (horizon - times)[None, :] * DAYS_PER_YEAR
    cdf = lag_cdf(gap, m, s, pi)  # (draws, steps)
    return cdf.reshape(draws, span, STEPS_PER_YEAR).mean(axis=2)


def permits_by_year(series: np.ndarray, inp: HazardInputs, pull: Any = 0.0) -> np.ndarray:
    """Permits issued per year from applications per year (``(draws, span)``), lag convolved."""
    from visionhousing.scenarios.adu_levers import issuance_by_year

    return issuance_by_year(series, inp.median, inp.log_sd, pull)


def calibration(
    ramp0: np.ndarray, median: np.ndarray, log_sd: np.ndarray, horizon: int, span: int
) -> tuple[np.ndarray, np.ndarray]:
    """``kappa`` for newly legal and already legal sites, per draw.

    ``kappa_new = sum over window of ramp0(t) / sum of ramp0(t) x w0(t)`` and
    ``kappa_old = horizon / sum of w0(t)``, with ``ramp0`` the ramp with no start
    delay and ``w0`` the in-window issue share under today's lag. With no
    depletion, permits in the window then equal take-up x the ramp's sum.
    """
    d = ramp0.shape[0]
    w0 = in_window_share(median, log_sd, 0.0, horizon, span, d)[:, :horizon]
    r = ramp0[:, :horizon]
    with np.errstate(divide="ignore", invalid="ignore"):
        k_new = np.where((r * w0).sum(1) > 0, r.sum(1) / (r * w0).sum(1), 1.0)
        k_old = np.where(w0.sum(1) > 0, horizon / w0.sum(1), 1.0)
    return k_new, k_old


# ---------------------------------------------------------------- simulation


@dataclass(frozen=True)
class Sim:
    """One arm of the hazard simulation, ``(draws, span)`` state series.

    Attributes:
        apps: Applications per year (both pools; eventually issued).
        apps_group1: Applications from sites feasible without the award.
        apps_comparable: Applications x the tract's detached share.
        awards: Awards made per year.
        windfall: Awards to owners who would have filed that year anyway.
        demand: Awards asked for per year (before the money runs out).
        spend: Dollars paid in awards per year.
        tract_window: In-window permits per tract, draws x tracts (when kept).
        remaining_end: Sites still without an application at the end, per draw.
        sites_start: Sites at the start (both groups), per draw.
        groups: Applications per year by site group (``g1_new``, ``g2_new``,
            ``g1_old``, ``g2_old``: pool x group); a group the arm does not
            have is absent. Used to pair the same sites across arms.
    """

    apps: np.ndarray
    apps_group1: np.ndarray
    apps_comparable: np.ndarray
    awards: np.ndarray
    windfall: np.ndarray
    demand: np.ndarray
    spend: np.ndarray
    tract_window: np.ndarray | None = None
    remaining_end: np.ndarray | None = None
    sites_start: np.ndarray | None = None
    groups: Mapping[str, np.ndarray] | None = None


def simulate(
    inp: HazardInputs,
    *,
    law: bool,
    program: Program | None = None,
    pull: Any = 0.0,
    old_pool: bool = True,
    keep_tracts: bool = False,
    lag_median: Any = None,
) -> Sim:
    """Run one arm: hazard on surviving sites, year by year, over the span.

    Args:
        inp: The run's inputs.
        law: The law change is on (newly legal sites file applications).
        program: A capped program, or ``None``.
        pull: Tail pull of the lag (``adu.timing_tail_pull_share``); only the
            in-window tract permits depend on it, never the applications.
        old_pool: Include the already legal sites.
        keep_tracts: Keep in-window permits per tract.
        lag_median: Another lag median for the in-window tract permits (a
            prolonged-lag check); ``kappa`` stays at today's.

    Returns:
        The arm's :class:`Sim`.
    """
    d, span, h = inp.draws, inp.span, inp.horizon
    zeros = np.zeros((d, span))
    out = {k: zeros.copy() for k in ("apps", "g1", "cmp", "awards", "windfall", "demand", "spend")}
    median = inp.median if lag_median is None else lag_median
    w = in_window_share(median, inp.log_sd, pull, h, span, d) if keep_tracts else None
    cs = inp.comparable_share
    lam_new = np.clip(inp.kappa_new[:, None] * inp.hazard * inp.ramp, 0.0, 1.0)
    lam_old = np.broadcast_to(np.clip(inp.kappa_old[:, None] * inp.hazard, 0.0, 1.0), (d, span))
    pools: list[dict[str, Any]] = []
    groups: dict[str, np.ndarray] = {}
    for name, on, fit, lam in (
        ("new", law, inp.fit_new, lam_new),
        ("old", old_pool, inp.fit_old, lam_old),
    ):
        if not on or fit is None:
            continue
        r1 = fit * inp.p
        r2 = fit * np.maximum(program.p_award - inp.p, 0.0) if program is not None else None
        pools.append({"r1": r1, "r2": r2, "lam": lam, "name": name})
        groups[f"g1_{name}"] = zeros.copy()
        if program is not None:
            groups[f"g2_{name}"] = zeros.copy()
    n_tracts = inp.p.shape[1]
    tract = np.zeros((d, n_tracts)) if keep_tracts else None
    start = sum((pl["r1"].sum(1) for pl in pools), np.zeros(d))
    if program is not None:
        start = start + sum((pl["r2"].sum(1) for pl in pools), np.zeros(d))
    funds = program.funds.astype(float).copy() if program is not None else np.zeros(d)
    eu = program.eligible * program.takeup if program is not None else np.zeros(d)
    for t in range(span):
        rr: list[np.ndarray] = []
        q = np.zeros((d, 1))
        if program is not None:
            asked = np.zeros(d)
            dollars = np.zeros(d)
            for pl in pools:
                pl_rr = np.minimum(pl["lam"][:, t : t + 1] * program.ratio, 1.0)
                rr.append(pl_rr)
                base = pl_rr * (pl["r1"] + pl["r2"])
                asked += base.sum(1)
                dollars += (base * program.spend_per_award).sum(1)
            if t < h:
                with np.errstate(divide="ignore", invalid="ignore"):
                    phi = np.where(
                        dollars * eu > 0,
                        np.minimum(1.0, funds / np.where(dollars * eu > 0, dollars * eu, 1.0)),
                        0.0,
                    )
                q = (eu * phi)[:, None]
                out["demand"][:, t] = eu * asked
        spent = np.zeros(d)
        for i, pl in enumerate(pools):
            lam = pl["lam"][:, t : t + 1]
            a1 = pl["r1"] * lam
            if program is not None:
                a1 = pl["r1"] * (lam * (1.0 - q) + rr[i] * q)
                a2 = pl["r2"] * rr[i] * q
                awarded = q * rr[i] * (pl["r1"] + pl["r2"])
                out["awards"][:, t] += awarded.sum(1)
                out["windfall"][:, t] += (q * lam * pl["r1"]).sum(1)
                spent += (awarded * program.spend_per_award).sum(1)
                pl["r2"] = pl["r2"] - a2
                groups[f"g2_{pl['name']}"][:, t] += a2.sum(1)
            else:
                a2 = 0.0
            groups[f"g1_{pl['name']}"][:, t] += a1.sum(1)
            pl["r1"] = pl["r1"] - a1
            apps = a1 + a2
            out["apps"][:, t] += apps.sum(1)
            out["g1"][:, t] += a1.sum(1)
            if cs is not None:
                out["cmp"][:, t] += (apps * cs).sum(1)
            if tract is not None and t < h:
                tract += apps * w[:, t : t + 1]
        if program is not None:
            out["spend"][:, t] = spent
            funds = np.maximum(funds - spent, 0.0)
    end = sum((pl["r1"].sum(1) for pl in pools), np.zeros(d))
    if program is not None:
        end = end + sum((pl["r2"].sum(1) for pl in pools), np.zeros(d))
    return Sim(
        apps=out["apps"],
        apps_group1=out["g1"],
        apps_comparable=out["cmp"],
        awards=out["awards"],
        windfall=out["windfall"],
        demand=out["demand"],
        spend=out["spend"],
        tract_window=tract,
        remaining_end=end,
        sites_start=start,
        groups=groups,
    )


def window_total(series: np.ndarray, inp: HazardInputs, pull: Any = 0.0) -> np.ndarray:
    """Permits issued inside the window from an application series, per draw."""
    w = in_window_share(inp.median, inp.log_sd, pull, inp.horizon, inp.span, inp.draws)
    return (series * w).sum(axis=1)


# ---------------------------------------------------------------- a whole arm, summarised


@dataclass(frozen=True)
class ArmResult:
    """One arm against no change, per draw (all ``(draws,)`` unless noted).

    Attributes:
        added: Added units authorized in the window (headline scope).
        comparable: The same, detached ADUs only (survey-comparable).
        lifetime: Added applications over the extended span (eventually issued).
        moved_from_later: Projects the reference files after the window that the
            arm files inside it: the same site groups paired across the two arms
            (>= 0 by construction; a group the reference lacks adds nothing).
        new_after_window: Projects from site groups the reference does not have
            (sites the law or the award creates) whose permits land after the
            window; they are in ``lifetime`` but not in ``added``.
        awards, windfall, demand, spend: Program totals over the window.
        net_cost, unused, repayments: Program dollars.
        by_year: Added units per window year ``(draws, years)``.
    """

    added: np.ndarray
    comparable: np.ndarray
    lifetime: np.ndarray
    moved_from_later: np.ndarray
    new_after_window: np.ndarray
    awards: np.ndarray
    windfall: np.ndarray
    demand: np.ndarray
    spend: np.ndarray
    net_cost: np.ndarray
    unused: np.ndarray
    repayments: np.ndarray
    by_year: np.ndarray


def arm_against(
    inp: HazardInputs,
    arm: Sim,
    ref: Sim,
    program: Program | None,
    pull: Any = 0.0,
) -> ArmResult:
    """Summarise ``arm`` against ``ref`` on the same draws (both under the lag with ``pull``).

    ``added = kept x (in-window permits of arm - of ref)``.

    ``moved_from_later = kept x sum over the years after the window and over the
    site groups both arms have of max(applications of ref - of arm, 0)``: the
    same sites (same pool, same group, same draws) that the reference uses after
    2031 and the arm uses early. Each year's term is >= 0 by construction (the
    arm's hazard is never lower in the window and identical after it, so fewer
    sites remain); ``max`` only removes rounding. A group the reference does not
    have (the newly legal pool against no change, group 2 without a program)
    files nothing there, so it moves nothing: its projects are new. A project
    that would only have come after the extended span is not counted (lower
    bound).

    ``new_after_window = kept x sum over years of (applications of the groups
    the reference lacks) x (1 - in-window share)``: projects the law or the
    award creates whose permits land after the window.
    """
    kept = inp.kept[:, 0]
    h = inp.horizon
    w = in_window_share(inp.median, inp.log_sd, pull, h, inp.span, inp.draws)
    perm_arm = permits_by_year(arm.apps, inp, pull)
    perm_ref = permits_by_year(ref.apps, inp, pull)
    added = kept * ((arm.apps - ref.apps) * w).sum(1)
    comparable = kept * ((arm.apps_comparable - ref.apps_comparable) * w).sum(1)
    lifetime = kept * (arm.apps.sum(1) - ref.apps.sum(1))
    ag, rg = arm.groups or {}, ref.groups or {}
    moved = np.zeros(inp.draws)
    new_apps = np.zeros_like(arm.apps)
    for key, series in ag.items():
        if key in rg:
            moved += np.maximum(rg[key] - series, 0.0)[:, h:].sum(1)
        else:
            new_apps = new_apps + series
    moved = kept * moved
    new_after = kept * (new_apps * (1.0 - w)).sum(1)
    spend = arm.spend.sum(1)
    if program is not None:
        net_cost = program.net_cost(spend)
        unused = program.unused(spend)
        repay = program.repayments(spend)
    else:
        net_cost = unused = repay = np.zeros(inp.draws)
    return ArmResult(
        added=added,
        comparable=comparable,
        lifetime=lifetime,
        moved_from_later=moved,
        new_after_window=new_after,
        awards=arm.awards.sum(1),
        windfall=arm.windfall.sum(1),
        demand=arm.demand.sum(1),
        spend=spend,
        net_cost=net_cost,
        unused=unused,
        repayments=repay,
        by_year=kept[:, None] * (perm_arm - perm_ref)[:, :h],
    )


# ---------------------------------------------------------------- the variant run


def _rec(
    out: dict[str, Record],
    key: str,
    name: str,
    draws: np.ndarray,
    unit: str,
    expr: str,
    ids: Sequence[str],
    sources: Sequence[str] = (),
) -> None:
    out[key] = rec.from_draws(
        name,
        np.asarray(draws, dtype=float),
        unit,
        expression=expr,
        assumption_ids=sorted(set(ids)),
        source_ids=sorted(set(sources)),
        tier=0,
    )


def _point(
    out: dict[str, Record], key: str, name: str, value: float, unit: str, formula: str, ids
) -> None:
    out[key] = rec.quantity(name, float(value), unit, formula=formula, assumption_ids=ids, tier=0)


def hazard_variant(
    inp: HazardInputs,
    *,
    ledger: Ledger,
    timing_on: bool,
    budget_on: bool,
    bench_net_tract: np.ndarray,
    binding: Mapping[str, Any],
    ids: Sequence[str],
    sources: Sequence[str],
) -> Any:
    """The lever result of a hazard-method variant (reference, timing, budget).

    Paired arms on the same draws: no change (``Z``), the law with today's lag
    (``L``, the hazard reference) and the variant (``V``: the law with the
    pulled lag and/or the program). Every number is ``V`` against ``L`` or
    against ``Z``; the linear benchmark is reported beside it.
    """
    from visionhousing.scenarios.adu_levers import LeverResult

    v = inp.values
    d, h = inp.draws, inp.horizon
    pull = v["adu.timing_tail_pull_share"] if timing_on else np.zeros(d)
    program = None
    if budget_on:
        program = inp.program(
            float(np.median(v[AWARD])), float(np.median(v[LOAN])), v[APPROPRIATION]
        )
    old = program is not None and inp.fit_old is not None
    z = simulate(inp, law=False, old_pool=old, keep_tracts=old)
    ref = simulate(inp, law=True, old_pool=old, keep_tracts=True)
    var = simulate(inp, law=True, program=program, pull=pull, old_pool=old, keep_tracts=True)
    kept = inp.kept
    z_tract = z.tract_window if old else 0.0
    ref_tract = (ref.tract_window - z_tract) * kept  # law only, net, today's lag
    var_tract = (var.tract_window - z_tract) * kept
    bench_state = bench_net_tract.sum(1)
    ref_state = ref_tract.sum(1)
    var_state = var_tract.sum(1)
    total = var_state - ref_state  # the lever's paired change (hazard method on both sides)

    # Timing on the reference applications; finance on top.
    w0 = in_window_share(inp.median, inp.log_sd, 0.0, h, inp.span, d)
    w1 = in_window_share(inp.median, inp.log_sd, pull, h, inp.span, d)
    k = kept[:, 0]
    moved_in = k * (var.apps * np.maximum(w1 - w0, 0.0)).sum(1)
    moved_out = k * (var.apps * np.maximum(w0 - w1, 0.0)).sum(1)
    timing = k * ((ref.apps) * (w1 - w0)).sum(1)
    fin = total - timing
    vs = arm_against(inp, var, ref, program, pull)
    fin_moved = vs.moved_from_later if program is not None else np.zeros(d)
    moved = timing + fin_moved
    newly = fin - fin_moved

    perm_ref0 = permits_by_year(ref.apps, inp, 0.0)
    perm_ref1 = permits_by_year(ref.apps, inp, pull)
    perm_var = permits_by_year(var.apps, inp, pull)
    timing_year = k[:, None] * (perm_ref1 - perm_ref0)[:, :h]
    fin_year = k[:, None] * (perm_var - perm_ref1)[:, :h]
    with np.errstate(divide="ignore", invalid="ignore"):
        fin_moved_share = np.where(np.abs(fin) > 0, fin_moved / np.where(fin != 0, fin, 1.0), 0.0)
    moved_by_year = timing_year + fin_year * fin_moved_share[:, None]
    newly_by_year = fin_year - fin_year * fin_moved_share[:, None]

    # Stages and year shares of the variant (against no change).
    built = np.maximum(var_tract / np.where(kept > 0, kept, 1.0), 0.0)
    feasible = inp.fit_new * inp.p
    perm_z = permits_by_year(z.apps, inp, pull) if old else 0.0
    add_year = k[:, None] * (perm_var - perm_z)[:, :h]
    yt = add_year.sum(1, keepdims=True)
    share = np.where(yt > 0, add_year / np.where(yt > 0, yt, 1.0), 1.0 / h)
    share = np.clip(share, 0.0, None)
    share = share / np.where(share.sum(1, keepdims=True) > 0, share.sum(1, keepdims=True), 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        tf = np.where(feasible > 0, built / np.where(feasible > 0, feasible, 1.0), 0.0)

    cids = tuple(sorted({str(c) for c in inp.counties}))
    cmask = [np.asarray(inp.counties).astype(str) == c for c in cids]
    change_c = np.stack([(var_tract - ref_tract)[:, m].sum(1) for m in cmask])
    with np.errstate(divide="ignore", invalid="ignore"):
        msh = np.where(np.abs(total) > 0, moved / np.where(total != 0, total, 1.0), 0.0)
    moved_c = change_c * msh[None, :]
    newly_c = change_c - moved_c

    # The timing ledger compares the variant's own applications at the pulled lag
    # with the same applications at today's lag. Without a program that is the
    # reference arm; with one it is the program arm at today's lag, so the
    # timing records never hold the program's effect.
    timing_base = ref
    if timing_on and program is not None:
        timing_base = simulate(inp, law=True, program=program, pull=np.zeros(d), old_pool=old)
    records = _variant_records(
        inp,
        ledger=ledger,
        ids=ids,
        sources=sources,
        timing_on=timing_on,
        program=program,
        bench=bench_state,
        ref=ref_state,
        var=var_state,
        total=total,
        timing=timing,
        moved=moved,
        newly=newly,
        moved_in=moved_in,
        moved_out=moved_out,
        fin_moved=fin_moved,
        moved_by_year=moved_by_year,
        newly_by_year=newly_by_year,
        ref_sim=ref,
        var_sim=var,
        z_sim=z,
        pull=pull,
        vs=vs,
        timing_base=timing_base,
        has_comparable=inp.comparable_share is not None,
    )
    extra: dict[str, Any] = {
        "method": "hazard",
        "reference_net": ref_state,
        "method_difference": ref_state - bench_state,
        "lifetime_reference": k * (ref.apps - z.apps).sum(1),
        "lifetime_variant": k * (var.apps - z.apps).sum(1),
    }
    if program is not None:
        extra["program"] = {
            "awards": vs.awards,
            "windfall": vs.windfall,
            "spend": vs.spend,
            "net_cost": vs.net_cost,
            "unused": vs.unused,
            "funds": program.funds,
            "added": vs.added,
            "comparable": vs.comparable,
        }
    if keeping_inputs():
        extra["inputs"] = inp
    return LeverResult(
        timing_active=timing_on,
        financing_active=budget_on,
        feasible=feasible,
        built=built,
        net=var_tract,
        p=inp.p,
        year_share=share,
        takeup_factor=tf,
        bench_net=bench_state,
        moved=moved,
        newly=newly,
        moved_by_year=moved_by_year,
        newly_by_year=newly_by_year,
        moved_county=moved_c,
        newly_county=newly_c,
        county_ids=cids,
        records=records,
        binding=dict(binding),
        assumption_ids=tuple(sorted(set(ids))),
        extra=extra,
    )


def _variant_records(
    inp: HazardInputs,
    *,
    ledger: Ledger,
    ids: Sequence[str],
    sources: Sequence[str],
    timing_on: bool,
    program: Program | None,
    bench: np.ndarray,
    ref: np.ndarray,
    var: np.ndarray,
    total: np.ndarray,
    timing: np.ndarray,
    moved: np.ndarray,
    newly: np.ndarray,
    moved_in: np.ndarray,
    moved_out: np.ndarray,
    fin_moved: np.ndarray,
    moved_by_year: np.ndarray,
    newly_by_year: np.ndarray,
    ref_sim: Sim,
    z_sim: Sim,
    var_sim: Sim,
    pull: np.ndarray,
    vs: ArmResult,
    timing_base: Sim,
    has_comparable: bool,
) -> dict[str, Record]:
    years = inp.years
    period = f"{years[0]}-{years[-1]}"
    end = years[0] + inp.span - 1
    k = inp.kept[:, 0]
    out: dict[str, Record] = {}
    hz = (
        "hazard on surviving eligible sites: applications(t) = remaining sites(t) x "
        "min(kappa x take-up x ramp(t), 1), remaining sites(t+1) = remaining sites(t) - "
        "applications(t); permits = applications convolved with the application-to-permit lag "
        f"({LABEL})"
    )
    put = _rec
    put(
        out,
        "lever_benchmark_net",
        f"benchmark added units authorized {period} (linear take-up, same draws)",
        bench,
        "units",
        "the benchmark funnel's added units, summed over tracts (feasible lots x take-up x sum "
        "of the ramp, capped at the feasible lots, x (1 - displacement))",
        ids,
        sources,
    )
    put(
        out,
        "hazard_reference_net",
        f"added units authorized {period}, law only, hazard method (same draws)",
        ref,
        "units",
        f"(1 - displacement) x permits issued in the window, {hz}",
        ids,
        sources,
    )
    put(
        out,
        "hazard_method_difference",
        f"hazard method minus the benchmark's linear take-up, "
        f"{period} (a disclosed method sensitivity; the benchmark headline is unchanged)",
        ref - bench,
        "units",
        "hazard reference minus benchmark added units in the same draw: the cost of using up "
        "sites (no site builds twice) and of late applications passing the window",
        ids,
        sources,
    )
    put(
        out,
        "lever_net",
        f"added units authorized {period} with the levers (hazard method)",
        var,
        "units",
        f"(1 - displacement) x (permits in the window with the levers - permits on "
        f"already legal sites with no change), {hz}",
        ids,
        sources,
    )
    put(
        out,
        "lever_paired_change",
        f"change in added units authorized {period} vs the hazard reference (same draws)",
        total,
        "units",
        "lever added units minus hazard-reference added units in the same draw = moved into "
        "the window + newly created",
        ids,
        sources,
    )
    put(
        out,
        "lever_paired_change_vs_benchmark",
        f"change in added units authorized {period} vs "
        "the benchmark headline (lever + method difference)",
        var - bench,
        "units",
        "lever added units minus the benchmark's linear added units in the same draw = paired "
        "change + hazard method difference",
        ids,
        sources,
    )
    put(
        out,
        "lever_moved_into_window",
        f"authorizations moved into {period} from later years",
        moved,
        "units",
        "timing: (1 - displacement) x applications x (in-window issue share with the pulled lag - "
        "today's), per filing year; plus, with a program, sites feasible without help that are "
        "used early: (1 - displacement) x sum over the years after the window of (their "
        "applications without the program - with it)",
        ids,
        sources,
    )
    put(
        out,
        "lever_newly_feasible",
        f"authorizations newly created in {period} (not taken from later years)",
        newly,
        "units",
        "paired change minus authorizations moved into the window",
        ids,
        sources,
    )
    for i, year in enumerate(years):
        put(
            out,
            f"lever_change_{year}",
            f"change in added units authorized in {year} vs the hazard reference",
            moved_by_year[:, i] + newly_by_year[:, i],
            "units",
            f"(1 - displacement) x (permits issued in {year} with the levers - in the hazard "
            "reference), lag convolved; the years add up to the paired change",
            ids,
            sources,
        )
    put(
        out,
        "hazard_lifetime_reference",
        f"added ADU projects filed {years[0]}-{end} that are eventually issued, law only "
        "(extended horizon, against no change)",
        k * (ref_sim.apps - z_sim.apps).sum(1),
        "units",
        "(1 - displacement) x (applications over the extended span - with no change); every "
        "application is issued in the end (permits issued + pending at the end)",
        ids,
        sources,
    )
    put(
        out,
        "hazard_lifetime_variant",
        f"added ADU projects filed {years[0]}-{end} that are eventually issued, with the "
        "levers (extended horizon, against no change)",
        k * (var_sim.apps - z_sim.apps).sum(1),
        "units",
        "(1 - displacement) x (applications over the extended span - with no change), newly "
        "and already legal sites",
        ids,
        sources,
    )
    if timing_on:
        perm0 = permits_by_year(timing_base.apps, inp, 0.0)
        perm1 = permits_by_year(var_sim.apps, inp, pull)
        h = inp.horizon
        put(
            out,
            "timing_moved_into_window",
            f"permits moved into {period} by faster review ({LABEL})",
            moved_in,
            "units",
            "(1 - displacement) x sum over filing years of applications x max(in-window "
            "issue share "
            "with the pulled lag - today's, 0)",
            ids,
            sources,
        )
        put(
            out,
            "timing_moved_out_of_window",
            f"permits moved past {years[-1]} by the review change",
            moved_out,
            "units",
            "(1 - displacement) x sum over filing years of applications x max(today's "
            "in-window issue "
            "share - with the pulled lag, 0); 0 when review only gets faster",
            ids,
            sources,
        )
        put(
            out,
            "timing_after_window_reference",
            f"permits after {years[-1]} up to {end}, today's lag",
            k * perm0[:, h:].sum(1),
            "units",
            "(1 - displacement) x permits issued after the window, today's lag",
            ids,
            sources,
        )
        put(
            out,
            "timing_after_window_variant",
            f"permits after {years[-1]} up to {end}, faster review",
            k * perm1[:, h:].sum(1),
            "units",
            "(1 - displacement) x permits issued after the window, pulled lag",
            ids,
            sources,
        )
        put(
            out,
            "timing_pending_reference",
            f"applications still pending at the end of {end}, today's lag (right-censored)",
            k * (timing_base.apps.sum(1) - perm0.sum(1)),
            "units",
            "(1 - displacement) x (applications - permits issued by the end)",
            ids,
            sources,
        )
        put(
            out,
            "timing_pending_variant",
            f"applications still pending at the end of {end}, faster review (right-censored)",
            k * (var_sim.apps.sum(1) - perm1.sum(1)),
            "units",
            "(1 - displacement) x (applications - permits issued by the end)",
            ids,
            sources,
        )
        put(
            out,
            "timing_lifetime_difference",
            f"change in projects filed {years[0]}-{end} (timing only: 0, projects are conserved)",
            k * (var_sim.apps.sum(1) - timing_base.apps.sum(1)),
            "units",
            "(1 - displacement) x (applications with faster review - without), over the extended "
            "span, the same program setting in both; review speed changes when a permit is "
            "issued, never whether an application is filed",
            ids,
            sources,
        )
    if program is not None:
        _program_records(out, inp, ledger, ids, sources, program, vs, has_comparable, period)
    return out


def _program_records(
    out: dict[str, Record],
    inp: HazardInputs,
    ledger: Ledger,
    ids: Sequence[str],
    sources: Sequence[str],
    program: Program,
    vs: ArmResult,
    has_comparable: bool,
    period: str,
) -> None:
    put = _rec
    d = inp.draws
    award = program.award
    min_gain = float(ledger[MIN_GAIN].central) if MIN_GAIN in ledger else 0.5
    target = float(ledger[TARGET].central) if TARGET in ledger else 0.0
    _point(
        out,
        "budget_award_capacity_before_admin",
        "awards the appropriation could pay before administration",
        float(np.median(program.capacity_before_admin)),
        "awards",
        f"appropriation / award = {float(np.median(program.appropriation)):,.0f} / "
        f"{award:,.0f} (a ceiling on funded awards, not induced ADUs)",
        [APPROPRIATION, AWARD],
    )
    put(
        out,
        "budget_admin_cost",
        "program administration cost",
        program.admin_cost(vs.spend),
        "$",
        "appropriation used x administration share, appropriation used = award spending / "
        "(1 - administration share)",
        ids,
        sources,
    )
    put(
        out,
        "budget_funds_for_awards",
        "money left for awards after administration",
        program.funds,
        "$",
        "appropriation x (1 - administration share)",
        ids,
        sources,
    )
    put(
        out,
        "budget_applicants_served",
        f"owners funded {period} (applicants served)",
        vs.awards,
        "awards",
        "sum over window years and tracts of q x min(hazard x r, 1) x remaining sites, q = "
        "eligible share x award take-up x funded share (funded share = min(1, money left / "
        "dollars asked) each year, first come first served)",
        ids,
        sources,
    )
    put(
        out,
        "budget_awards_requested",
        f"awards eligible owners asked for {period}",
        vs.demand,
        "awards",
        "sum over window years of eligible share x award take-up x min(hazard x r, 1) x "
        "remaining sites (before the money runs out)",
        ids,
        sources,
    )
    put(
        out,
        "budget_awards_to_owners_building_anyway",
        f"awards to owners who would have filed anyway {period}",
        vs.windfall,
        "awards",
        "sum over window years of q x hazard x group-1 sites (owners whose application would have "
        "come that year without the award)",
        ids,
        sources,
    )
    put(
        out,
        "budget_award_spending",
        f"dollars paid in awards {period}",
        vs.spend,
        "$",
        "awards x min(award, owner cash share); never more than the money for awards",
        ids,
        sources,
    )
    put(
        out,
        "budget_unused_funds",
        "appropriation never used (returned)",
        vs.unused,
        "$",
        "appropriation - award spending / (1 - administration share)",
        ids,
        sources,
    )
    if program.loan_share > 0:
        put(
            out,
            "budget_loan_repayments",
            "loan principal repaid",
            vs.repayments,
            "$",
            "award spending x loan share x (1 - loan loss share)",
            ids,
            sources,
        )
    put(
        out,
        "budget_net_public_cost",
        "net public cost of the program",
        vs.net_cost,
        "$",
        "administration + grants + loans x (loss share + (1 - loss share) x (1 - payment factor "
        "at the program rate / payment factor at the public discount rate)); administration is "
        "charged on the money used; unused money is returned, not a cost",
        ids,
        sources,
    )
    put(
        out,
        "budget_additional_units",
        f"additional ADU authorizations from the program {period} (headline scope)",
        vs.added,
        "units",
        "(1 - displacement) x (permits in the window with the program - without), law on in both, "
        "same draws and candidate cells",
        ids,
        sources,
    )
    if has_comparable:
        gain_cmp = vs.comparable
        put(
            out,
            "budget_additional_comparable",
            f"additional comparable (detached) ADU authorizations from the program {period}",
            gain_cmp,
            "units",
            "(1 - displacement) x the sum over tracts of (permits in the window with the "
            "program - without) x the tract's detached share (adu.detached_form_share_*), law "
            "on and the same review lag in both arms; the survey-comparable scope of B2's "
            "'comparable' record",
            ids,
            sources,
        )
    else:
        gain_cmp = None
    put(
        out,
        "budget_moved_from_later_years",
        f"program authorizations in {period} taken from later years",
        vs.moved_from_later,
        "units",
        "(1 - displacement) x sum over the years after the window (to the end of the extended "
        "span) and over the site groups both arms have of max(applications without the "
        "program - with it, 0): the same sites, used early; owners the award only speeds up",
        ids,
        sources,
    )
    put(
        out,
        "budget_lifetime_additional",
        "additional ADU projects over the extended horizon (the program's lasting effect)",
        vs.lifetime,
        "units",
        "(1 - displacement) x (applications with the program - without) over the extended span",
        ids,
        sources,
    )
    gain = gain_cmp if gain_cmp is not None else vs.added
    scope = "comparable (detached)" if gain_cmp is not None else "headline-scope"
    ok = gain >= min_gain
    zero_share = float(np.mean(~ok))
    _point(
        out,
        "budget_zero_gain_share",
        f"share of draws where the program adds less than {min_gain:g} {scope} unit",
        zero_share,
        "share",
        f"{int((~ok).sum()):,} of {d:,} draws have a gain below adu.budget_min_gain_units; "
        "they are left out of the cost per unit, never averaged into it",
        [MIN_GAIN],
    )
    if ok.any():
        put(
            out,
            "budget_cost_per_additional_comparable_unit",
            f"net public cost per additional {scope} ADU authorization (draws with a gain)",
            vs.net_cost[ok] / gain[ok],
            "$/unit",
            "in each draw with a gain, net public cost / additional units (a ratio per "
            "draw, never a ratio of percentiles)",
            ids,
            sources,
        )
    # What a target would need (linear in awards; depletion makes it approximate).
    pos = vs.added >= min_gain
    if target > 0 and pos.any():
        per_award = np.where(pos, vs.added / np.where(vs.awards > 0, vs.awards, 1.0), np.nan)
        need = target / per_award[pos]
        spend_per = np.where(
            vs.awards > 0, vs.spend / np.where(vs.awards > 0, vs.awards, 1.0), award
        )
        put(
            out,
            "budget_awards_needed_for_target",
            f"awards needed for {target:,.0f} additional authorizations (draws with a gain)",
            need,
            "awards",
            "target / (additional units per funded award) in each draw; linear in awards, so it "
            "ignores the extra depletion a larger program causes",
            [*ids, TARGET],
            sources,
        )
        put(
            out,
            "budget_appropriation_needed_for_target",
            f"appropriation needed for {target:,.0f} additional authorizations (draws with a gain)",
            need * spend_per[pos] / (1.0 - program.admin[pos]),
            "$",
            "awards needed x dollars per award / (1 - administration share)",
            [*ids, TARGET],
            sources,
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            ask = np.where(vs.demand > 0, vs.demand, np.nan)[pos]
        uptake = program.takeup[pos] * need / ask
        put(
            out,
            "budget_uptake_needed_for_target",
            f"award take-up needed for {target:,.0f} "
            "additional authorizations (draws with a gain; above 1 means take-up alone cannot "
            "reach it)",
            np.nan_to_num(uptake, nan=np.inf, posinf=1e9),
            "share",
            "award take-up x awards needed / awards asked for at today's take-up, if the money "
            "were there",
            [*ids, TARGET],
            sources,
        )
        met = float(np.mean(vs.added >= target))
        _point(
            out,
            "budget_target_met_share",
            f"share of draws where the program adds at least {target:,.0f} authorizations",
            float(np.mean(vs.added >= target)),
            "share",
            f"{int((vs.added >= target).sum()):,} of {d:,} draws; Monte Carlo standard error "
            f"sqrt(s(1 - s)/n) = {np.sqrt(met * (1 - met) / d):.3f}",
            [*ids, TARGET],
        )


# ---------------------------------------------------------------- the grid


def load_grid_settings(path: Path | str | None = None) -> dict[str, Any]:
    """Read and check ``assumptions/adu_budget_grid.yaml``."""
    data = read_yaml(Path(path) if path is not None else GRID_PATH)
    for key in ("appropriations", "awards", "targets", "arms"):
        vals = data.get(key)
        if not isinstance(vals, list) or not vals:
            raise ValueError(f"adu_budget_grid.yaml: '{key}' must be a non-empty list")
    if any(a not in ARMS for a in data["arms"]):
        raise ValueError(f"adu_budget_grid.yaml: arms must be among {ARMS}")
    for key in ("appropriations", "awards", "targets"):
        if sorted(float(x) for x in data[key]) != [float(x) for x in data[key]] or any(
            float(x) <= 0 for x in data[key]
        ):
            raise ValueError(f"adu_budget_grid.yaml: '{key}' must be positive and increasing")
    return data


def _r(name: str, draws: np.ndarray, unit: str, expr: str, ids: Sequence[str]) -> dict[str, Any]:
    return rec.from_draws(
        name, np.asarray(draws, dtype=float), unit, expression=expr, assumption_ids=ids, tier=0
    ).model_dump(mode="json")


def grid_from_inputs(
    inp: HazardInputs, settings: Mapping[str, Any], *, bench_net: np.ndarray
) -> dict[str, Any]:
    """The budget-and-target grid on one run's draws (pure: inputs in, JSON-ready dict out).

    Every cell is against no change on the same draws: ``law_only`` (no
    money), ``finance_only`` (today's law with the program) and ``both``. For
    each target the share of draws that meet it, its Monte Carlo standard
    error and a Pareto flag (no other tested cell has a lower median net cost
    and an equal or higher share, or an equal cost and a higher share).
    """
    ids = sorted({*inp.assumption_ids, "adu_budget_grid"})
    d = inp.draws
    targets = [float(t) for t in settings["targets"]]
    loan = float(settings.get("loan_share", 0) or 0)
    arms = list(settings["arms"])
    min_gain = (
        float(inp.values.get(MIN_GAIN, np.array([0.5]))[0]) if MIN_GAIN in inp.values else 0.5
    )
    has_old = inp.fit_old is not None
    z = simulate(inp, law=False, old_pool=has_old)
    law = simulate(inp, law=True, old_pool=has_old)
    law_arm = arm_against(inp, law, z, None)
    cells: list[dict[str, Any]] = []

    def cell(arm: str, budget: float, award: float, res: ArmResult, extra: dict) -> None:
        met = {}
        for t in targets:
            s = float(np.mean(res.added >= t))
            met[f"{t:g}"] = {
                "share": s,
                "mc_se": float(np.sqrt(s * (1 - s) / d)),
                "draws_met": int((res.added >= t).sum()),
            }
        ok = res.comparable >= min_gain
        what = {
            "law_only": "the law change, no program",
            "finance_only": f"today's law and a ${budget:,.0f} program of ${award:,.0f} awards",
            "both": f"the law change and a ${budget:,.0f} program of ${award:,.0f} awards",
        }[arm]
        c: dict[str, Any] = {
            "arm": arm,
            "appropriation": budget,
            "award": award,
            "label": what,
            "added": _r(
                f"added ADU authorizations 2027-2031, {what}",
                res.added,
                "units",
                "(1 - displacement) x (permits in the window in this arm - with no change), "
                "hazard on surviving sites, same draws",
                ids,
            ),
            "added_comparable": _r(
                f"added comparable (detached) ADU authorizations, {what}",
                res.comparable,
                "units",
                "the same x each tract's detached share",
                ids,
            ),
            "moved_from_later": _r(
                f"authorizations moved in from later years, {what}",
                res.moved_from_later,
                "units",
                "(1 - displacement) x sum over 2032 to the end of the extended span and over "
                "the site groups no change also has of max(applications with no change - in "
                "this arm, 0): the same sites used before 2032 instead of after (>= 0)",
                ids,
            ),
            "new_after_window": _r(
                f"new projects after 2031 (created, not moved), {what}",
                res.new_after_window,
                "units",
                "(1 - displacement) x sum over years of applications from sites no change "
                "does not have (newly legal lots, sites that build only with an award) x "
                "(1 - share issued by the end of 2031)",
                ids,
            ),
            "net_public_cost": _r(
                f"net public cost, {what}",
                res.net_cost,
                "$",
                "administration + grants + loans x (loss + (1 - loss) x interest "
                "subsidy); 0 with no program",
                ids,
            ),
            "applicants_served": _r(
                f"owners funded, {what}",
                res.awards,
                "awards",
                "awards made in the window (first come, first served)",
                ids,
            ),
            "awards_to_owners_building_anyway": _r(
                f"awards to owners who would have filed anyway, {what}",
                res.windfall,
                "awards",
                "q x hazard x group-1 sites, summed over the window",
                ids,
            ),
            "unused_funds": _r(
                f"appropriation never used (returned), {what}",
                res.unused,
                "$",
                "appropriation - award spending / (1 - administration share)",
                ids,
            ),
            "target_met": met,
            "zero_gain_share": float(np.mean(~ok)),
            "cost_per_added_comparable": (
                _r(
                    f"net public cost per added comparable ADU (draws with a gain), {what}",
                    res.net_cost[ok] / res.comparable[ok],
                    "$/unit",
                    "net public cost / added comparable units in each draw with a gain",
                    ids,
                )
                if ok.any() and budget > 0
                else None
            ),
            **extra,
        }
        cells.append(c)

    if "law_only" in arms:
        cell("law_only", 0.0, 0.0, law_arm, {"program_added": None})
    for award in settings["awards"]:
        award = float(award)
        for budget in settings["appropriations"]:
            budget = float(budget)
            prog = inp.program(award, loan, np.full(d, budget))
            if "finance_only" in arms:
                f = simulate(inp, law=False, program=prog, old_pool=has_old)
                cell(
                    "finance_only",
                    budget,
                    award,
                    arm_against(inp, f, z, prog),
                    {"program_added": None},
                )
            if "both" in arms:
                b = simulate(inp, law=True, program=prog, old_pool=has_old)
                both = arm_against(inp, b, z, prog)
                own = arm_against(inp, b, law, prog)
                cell(
                    "both",
                    budget,
                    award,
                    both,
                    {
                        "program_added": _r(
                            "added ADU authorizations from the program on top of the law, "
                            f"${budget:,.0f}"
                            f" of ${award:,.0f} awards",
                            own.added,
                            "units",
                            "both minus law only in the same draw",
                            ids,
                        )
                    },
                )
    _pareto(cells, targets)
    monotone = _monotone(cells, targets)
    return {
        "id": str(settings.get("id", "adu_budget_grid")),
        "label": str(settings.get("label", "")),
        "settings": {
            "arms": arms,
            "appropriations": [float(x) for x in settings["appropriations"]],
            "awards": [float(x) for x in settings["awards"]],
            "targets": targets,
            "loan_share": loan,
            "illustrative": True,
        },
        "draws": d,
        "reference": {
            "benchmark_linear": _r(
                "benchmark added ADU authorizations 2027-2031 (linear take-up)",
                bench_net,
                "units",
                "the benchmark funnel's added units",
                ids,
            ),
            "law_only_hazard": _r(
                "law only, hazard on surviving sites",
                law_arm.added,
                "units",
                "(1 - displacement) x in-window permits, hazard method",
                ids,
            ),
            "method_difference": _r(
                "hazard method minus the benchmark (disclosed sensitivity)",
                law_arm.added - bench_net,
                "units",
                "law only (hazard) minus the benchmark in the same draw",
                ids,
            ),
        },
        "cells": cells,
        "monotone": monotone,
        "notes": [
            "Illustrative scenario settings: the appropriations, awards and targets are choices "
            "to test, not estimates.",
            "Every cell uses the same draws; a share of draws is the middle 80% of model draws' "
            "logic applied to a yes/no question, conditional on the assumptions.",
            "Nothing is interpolated between tested values: a value between two tested ones is "
            "a bracket, and a value outside them is 'outside the tested range'.",
            "Awards go to owners who would have built anyway as well as to owners the award "
            "brings in; a fully used budget funds no further award.",
        ],
    }


def _pareto(cells: list[dict[str, Any]], targets: Sequence[float]) -> None:
    for t in targets:
        key = f"{t:g}"
        pts = [(c["net_public_cost"]["p50"], c["target_met"][key]["share"]) for c in cells]
        for i, c in enumerate(cells):
            ci, si = pts[i]
            if si <= 0:
                c.setdefault("pareto", {})[key] = False
                continue
            dominated = any(
                (cj <= ci and sj >= si) and (cj < ci or sj > si)
                for j, (cj, sj) in enumerate(pts)
                if j != i
            )
            c.setdefault("pareto", {})[key] = not dominated


def _monotone(cells: list[dict[str, Any]], targets: Sequence[float]) -> dict[str, Any]:
    """Whether the share meeting each target rises with the appropriation, per arm and award."""
    out: dict[str, Any] = {}
    groups: dict[tuple[str, float], list[dict[str, Any]]] = {}
    for c in cells:
        if c["arm"] != "law_only":
            groups.setdefault((c["arm"], c["award"]), []).append(c)
    for (arm, award), cs in groups.items():
        cs = sorted(cs, key=lambda c: c["appropriation"])
        for t in targets:
            key = f"{t:g}"
            s = [c["target_met"][key]["share"] for c in cs]
            out[f"{arm}|{award:g}|{key}"] = all(b >= a - 1e-12 for a, b in itertools.pairwise(s))
    return out


def grid_lookup(
    grid: Mapping[str, Any],
    *,
    target: float | None = None,
    budget: float | None = None,
    award: float | None = None,
    accept: float | None = None,
) -> dict[str, Any]:
    """Answer a budget-and-target question from the grid, without interpolating.

    Args:
        grid: :func:`grid_from_inputs` output.
        target: Additional authorizations wanted (must be a tested target).
        budget: Budget ceiling ($).
        award: Award size of interest (a tested one, or bracketed).
        accept: Smallest acceptable share of draws meeting the target.

    Returns:
        ``{"flags": [...], "target": ..., "options": [cells within the ceiling that
        meet the accepted share], "pareto": [...], "brackets": {...}}``. Flags:
        ``input_missing``, ``outside_tested_range``, ``between_tested_values``,
        ``budget_exceeded``, ``no_tested_option_meets_both``.
    """
    s = grid["settings"]
    flags: list[str] = []
    brackets: dict[str, Any] = {}
    out: dict[str, Any] = {"flags": flags, "brackets": brackets}
    if target is None:
        flags.append("input_missing")
        out["text"] = (
            "Give a target (additional ADU authorizations in 2027-2031) to compare options."
        )
        return out
    tested = s["targets"]
    key = f"{float(target):g}"
    if float(target) not in tested:
        if float(target) < tested[0] or float(target) > tested[-1]:
            flags.append("outside_tested_range")
        else:
            flags.append("between_tested_values")
            lo = max(t for t in tested if t < float(target))
            hi = min(t for t in tested if t > float(target))
            brackets["target"] = [lo, hi]
        out["text"] = (
            f"The target {float(target):,.0f} was not tested (tested: "
            + ", ".join(f"{t:,.0f}" for t in tested)
            + "). The grid does not interpolate; pick a tested target."
        )
        return out
    for name, value, grid_vals in (
        ("budget", budget, s["appropriations"]),
        ("award", award, s["awards"]),
    ):
        if value is None:
            continue
        v = float(value)
        if v < grid_vals[0] or v > grid_vals[-1]:
            if name == "award" or v < grid_vals[0]:
                flags.append("outside_tested_range")
        elif v not in grid_vals:
            flags.append("between_tested_values")
            brackets[name] = [
                max(g for g in grid_vals if g < v),
                min(g for g in grid_vals if g > v),
            ]
    cells = [
        c
        for c in grid["cells"]
        if award is None
        or c["arm"] == "law_only"
        or float(award) not in s["awards"]
        or c["award"] == float(award)
    ]
    within = [c for c in cells if budget is None or c["appropriation"] <= float(budget)]
    if budget is not None and len(within) < len(cells):
        flags.append("budget_exceeded")
    acc = 0.0 if accept is None else float(accept)
    meets = [c for c in within if c["target_met"][key]["share"] >= acc]
    if not meets:
        flags.append("no_tested_option_meets_both")
    out.update(
        target=float(target),
        accept=acc,
        options=[_brief(c, key) for c in sorted(meets, key=lambda c: c["net_public_cost"]["p50"])],
        pareto=[_brief(c, key) for c in within if c.get("pareto", {}).get(key)],
        over_budget=[_brief(c, key) for c in cells if c not in within],
    )
    return out


def _brief(c: Mapping[str, Any], key: str) -> dict[str, Any]:
    return {
        "arm": c["arm"],
        "appropriation": c["appropriation"],
        "award": c["award"],
        "label": c["label"],
        "share_meeting_target": c["target_met"][key]["share"],
        "mc_se": c["target_met"][key]["mc_se"],
        "net_public_cost_p50": c["net_public_cost"]["p50"],
        "added_p10": c["added"]["p10"],
        "added_p50": c["added"]["p50"],
        "added_p90": c["added"]["p90"],
        "pareto": bool(c.get("pareto", {}).get(key)),
    }


def budget_grid(
    state: Any,
    *,
    seed: int = 0,
    draws: int | None = None,
    settings: Mapping[str, Any] | None = None,
    bench: Any = None,
) -> dict[str, Any]:
    """Compute the grid for a state: one ADU variant run, then every cell on its draws."""
    from visionhousing.engine.comparisons.bench import Bench
    from visionhousing.ledger import DEFAULT_DRAWS
    from visionhousing.scenarios.adu_levers import lever_of

    cfg = dict(settings or load_grid_settings())
    b = bench or Bench.build(state, seed=seed, draws=draws or DEFAULT_DRAWS)
    with keep_inputs():
        res = b.alone(
            "adu", {"variant": str(cfg.get("variant", "adu_finance_budget"))}, remember=False
        )
    lv = lever_of(res)
    if lv is None or "inputs" not in lv.extra:
        raise RuntimeError("the ADU budget variant did not return hazard inputs")
    out = grid_from_inputs(lv.extra["inputs"], cfg, bench_net=lv.bench_net)
    out.update(
        state=b.context.state,
        seed=b.draws.seed,
        synthetic=b.context.synthetic,
        variant=str(cfg.get("variant", "adu_finance_budget")),
    )
    return out
