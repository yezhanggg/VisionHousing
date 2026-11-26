"""Extra scenario: fee deferral or fee waiver in exchange for income-restricted homes.

Plain-language summary
----------------------
The fee benchmark cuts every fee for every home. Many places instead offer
fee relief only to projects that set aside some homes for lower-income
households, and they choose between **deferring** the fee (paid at occupancy
instead of at permit) and **waiving** it. This scenario compares the two on the
same draws and says what each costs the public and what each buys.

A rental project (2+ homes; the BPS types ``units_2``, ``units_3_4``,
``units_5plus``) may opt in. In return it keeps ``fee_aff.restricted_share`` of
its homes at a rent affordable at ``fee_aff.ami_level`` of area median income for
``fee_aff.duration_years`` years. Per home, in each county and draw::

    restricted rent  = 30% x income limit(AMI level, 3 people) / 12
                       (HUD limits table afford_income_limits; level / 50% x the 50% limit,
                        HUD's rule for the 60% tax-credit limit)
    rent given up    = max(new-unit market rent - restricted rent, 0)
    affordability cost = restricted share x rent given up x 12
                         x (1 - (1 + cap rate) ^ -duration) / cap rate
    relief           = deferral: fee x construction-loan rate x deferral months / 12
                       waiver:   fee x fee_aff.waiver_share
    net gain         = relief - affordability cost

Where the net gain is positive the deal is worth taking:

- **induced homes** (added units authorized) = baseline units x elasticity x
  (net gain / value of a new home) x pass-through (the fee channel B);
- **opted-in baseline homes** = baseline units x ``fee_aff.optin_share`` (projects
  that would be built anyway and take the deal);
- **restricted homes** = restricted share x (opted-in baseline homes + induced homes).

Where it is not positive nobody opts in: no added homes, no restricted homes,
no public cost.

Fiscal lines (both modes always reported; the option ``mode`` picks the headline):

- waiver: revenue given up (gross) = waived fee x (opted-in baseline + induced
  homes); on homes built anyway = waived fee x opted-in baseline homes; new
  revenue from induced projects = (fee - waived fee) x induced homes;
- deferral: each deferred fee loses c = d + loss - d x loss of its present value,
  where d = 1 - 1 / (1 + public rate) ^ (months / 12) and loss = share never
  collected; revenue given up (gross) = fee x c x (opted-in baseline + induced
  homes); on homes built anyway = fee x c x opted-in baseline homes; new revenue
  (present value) = fee x induced homes x (1 - loss) x (1 - d);
- net public cost = revenue given up on homes built anyway - new revenue. The
  induced homes would not exist without the deal, so relief on them is not money
  the public had before; only the fee they still pay counts, once (the same rule
  as the fee benchmark's fiscal split). In both modes net = gross - fee x induced
  homes;
- net public cost per added home and per restricted home (defined when at least
  ``fees.cost_per_added_min_share`` of draws add homes);
- cash-flow timing: fees collected per year 2027-2031 at permit (today) and
  under the deferral (each year's fees move ``months / 12`` years later); the
  part pushed past 2031 is reported.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from visionhousing.baseline.panel import STRUCTURE_TYPES
from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.ledger.loader import Ledger
from visionhousing.ledger.sampling import sample
from visionhousing.scenarios.extra_common import per_draw_ratio, ramp_years, table_sources
from visionhousing.scenarios.fees import (
    ELASTICITY,
    FEE_K,
    VALUE_IDS,
    baseline_array,
    channel_b,
    market_from_tracts,
    value_per_unit,
)

__all__ = ["IDS", "MODES", "FeeAffordabilityResult", "fee_affordability"]

MODES = ("deferral", "waiver")
FEE = "fees.fee_total_per_unit"
MONTHS = "fees.deferral_months"
LOAN = "fees.construction_loan_rate"
PUBLIC_RATE = "fees.public_discount_rate"
LOSS = "fees.deferral_collection_loss_share"
MIN_SHARE = "fees.cost_per_added_min_share"
PREMIUM = "supply.new_construction_value_premium"
CAPRATE = "multifamily.cap_rate"
SHARE = "fee_aff.restricted_share"
AMI = "fee_aff.ami_level"
DURATION = "fee_aff.duration_years"
WAIVER = "fee_aff.waiver_share"
OPTIN = "fee_aff.optin_share"
FALLBACK = "fee_aff.ami_fallback_ratio"
FIRST = "fees.ramp_first_year_share"
FULL = "fees.ramp_years_to_full"
DELAY = "fees.start_delay_years"
IDS = (
    FEE, MONTHS, LOAN, PUBLIC_RATE, LOSS, ELASTICITY, FEE_K, *VALUE_IDS, CAPRATE, SHARE, AMI,
    DURATION, WAIVER, OPTIN, FALLBACK, FIRST, FULL, DELAY,
)  # fmt: skip
"""Ledger ids behind the scenario."""

RENTAL = ("units_2", "units_3_4", "units_5plus")
"""Structure types that may opt in (rental buildings of two or more homes)."""
DATA_SOURCES = ("acs5_summary_file", "bps_county_annual")
HOUSEHOLD = 3
"""Household size for a two-bedroom home (1.5 people per bedroom; affordability.yaml)."""


@dataclass(frozen=True)
class FeeAffordabilityResult:
    """What :func:`fee_affordability` returns.

    Attributes:
        county_ids: County order of the arrays.
        added: Induced homes (the headline mode), ``(counties, draws)``.
        added_by_mode: Mode to induced homes ``(counties, draws)``.
        restricted_by_mode: Mode to restricted homes ``(counties, draws)``.
        records: State records by name.
        steps: Funnel records in order.
        year_share: ``(draws, years)`` of the induced homes.
        mode: The headline mode.
        limits_source: ``hud`` (the pack's income limits) or ``fallback``.
    """

    county_ids: tuple[str, ...]
    added: np.ndarray
    added_by_mode: dict[str, np.ndarray]
    restricted_by_mode: dict[str, np.ndarray]
    records: dict[str, Record]
    steps: tuple[Record, ...]
    year_share: np.ndarray
    mode: str
    limits_source: str


def _limits(
    tracts: pd.DataFrame, counties: Sequence[str], limits: pd.DataFrame | None, rent_share: float
) -> tuple[np.ndarray | None, np.ndarray, str]:
    """Per county: the 50% limit for 3 people (or None) and a fallback median income."""
    t = tracts.assign(county=tracts["county"].astype(str))
    hh = pd.to_numeric(t.get("households_owner", 0), errors="coerce").fillna(0) + pd.to_numeric(
        t.get("households_renter", 0), errors="coerce"
    ).fillna(0)
    inc = pd.to_numeric(t["median_household_income"], errors="coerce")
    w = (inc * hh).groupby(t["county"]).sum() / hh.where(inc.notna(), 0).groupby(t["county"]).sum()
    median = np.array([float(w.get(c, np.nan)) for c in counties])
    if limits is None or limits.empty or f"l50_{HOUSEHOLD}" not in limits:
        return None, median, "fallback"
    lt = limits.assign(county=limits["county"].astype(str)).set_index("county")
    l50 = np.array(
        [
            float(pd.to_numeric(lt[f"l50_{HOUSEHOLD}"], errors="coerce").get(c, np.nan))
            for c in counties
        ]
    )
    if np.isnan(l50).all():
        return None, median, "fallback"
    return l50, median, "hud"


def fee_affordability(
    baseline: pd.DataFrame,
    tracts: pd.DataFrame,
    ledger: Ledger,
    *,
    limits: pd.DataFrame | None,
    rent_share: float,
    mode: str = "deferral",
    n_draws: int = 2000,
    seed: int = 0,
    baseline_scale: np.ndarray | None = None,
) -> FeeAffordabilityResult:
    """Run fee deferral and fee waiver, each tied to income-restricted homes, on the same draws.

    Args:
        baseline: Five-year baseline units by county (index) and structure type.
        tracts: Tract table (rent and value brackets, incomes, households).
        ledger: The ledger.
        limits: The pack's ``afford_income_limits`` table, or ``None``.
        rent_share: Share of income a home may cost (``assumptions/affordability.yaml``).
        mode: ``deferral`` (default) or ``waiver``: which one is the headline.
        n_draws: Draws.
        seed: Seed.
        baseline_scale: ``(counties, draws)`` baseline multiplier, or ``None``.

    Returns:
        A :class:`FeeAffordabilityResult`.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    for pid in (*IDS, MIN_SHARE, "baseline.first_year", "baseline.horizon_years"):
        ledger[pid]
    counties = tuple(str(c) for c in baseline.index)
    v = sample(ledger, n=n_draws, seed=seed, ids=list(dict.fromkeys(IDS)))
    base = baseline_array(baseline, counties)[:, :, None]  # (c, 4, 1)
    scale = np.ones((len(counties), n_draws)) if baseline_scale is None else baseline_scale
    base = base * np.asarray(scale, dtype=float)[:, None, :]  # (c, 4, d)
    rental = np.array([t in RENTAL for t in STRUCTURE_TYPES], dtype=float)[None, :, None]
    base_r = base * rental
    market = market_from_tracts(tracts, counties)
    value = value_per_unit(market, v)  # (c, 4, d)

    # Restricted rent and the affordability cost per home.
    l50, median_income, limits_source = _limits(tracts, counties, limits, rent_share)
    ami = v[AMI][None, :]
    if l50 is not None:
        income = np.nan_to_num(l50)[:, None] * ami / 0.5
    else:  # 3-person limit = 90% of the 4-person (100%) figure, HUD's family-size rule
        income = np.nan_to_num(median_income)[:, None] * v[FALLBACK][None, :] * 0.9 * ami
    restricted_rent = rent_share * income / 12.0  # (c, d)
    market_rent = market.median_rent[:, None] * (1.0 + v[PREMIUM][None, :])
    gap = np.maximum(market_rent - restricted_rent, 0.0)
    c = v[CAPRATE][None, :]
    annuity = (1.0 - (1.0 + c) ** (-v[DURATION][None, :])) / c
    aff_cost = v[SHARE][None, :] * gap * 12.0 * annuity  # $ per home in an opted-in project

    relief = {
        "deferral": v[FEE] * v[LOAN] * v[MONTHS] / 12.0,
        "waiver": v[FEE] * v[WAIVER],
    }
    years_n = int(ledger["baseline.horizon_years"].central)
    first = int(ledger["baseline.first_year"].central)
    years = tuple(range(first, first + years_n))
    _ramp, year_share = ramp_years(v, FIRST, FULL, DELAY, years_n)

    added_by: dict[str, np.ndarray] = {}
    restricted_by: dict[str, np.ndarray] = {}
    optin_by: dict[str, np.ndarray] = {}
    gain_by: dict[str, np.ndarray] = {}
    for m in MODES:
        gain = relief[m][None, :] - aff_cost  # (c, d)
        takes = gain > 0
        add = channel_b(
            base_r, v[ELASTICITY][None, None, :], np.maximum(gain, 0.0)[:, None, :],
            np.where(value > 0, value, 1.0), v[FEE_K][None, None, :],
        ).sum(axis=1)  # fmt: skip
        add = np.where(takes, add, 0.0)
        optin = np.where(takes, base_r.sum(axis=1) * v[OPTIN][None, :], 0.0)
        added_by[m] = add
        optin_by[m] = optin
        restricted_by[m] = v[SHARE][None, :] * (optin + add)
        gain_by[m] = gain

    records, steps = _records(
        ledger, v, mode, added_by, optin_by, restricted_by, gain_by, aff_cost, restricted_rent,
        market_rent, base_r, year_share, years, limits_source, table_sources(limits),
    )  # fmt: skip
    return FeeAffordabilityResult(
        county_ids=counties,
        added=added_by[mode],
        added_by_mode=added_by,
        restricted_by_mode=restricted_by,
        records=records,
        steps=steps,
        year_share=year_share,
        mode=mode,
        limits_source=limits_source,
    )


def _deferred_cash(units_by_year: np.ndarray, fee: np.ndarray, months: np.ndarray) -> np.ndarray:
    """Fees collected per year when each year's fees move ``months / 12`` years later.

    ``units_by_year`` is ``(draws, years)``; the result has one more column: the
    fees collected after the last year.
    """
    d, y = units_by_year.shape
    out = np.zeros((d, y + 1))
    shift = np.asarray(months, dtype=float) / 12.0
    whole = np.floor(shift).astype(int)
    frac = shift - whole
    cash = units_by_year * fee[:, None]
    for j in range(y):
        for part, extra in ((1.0 - frac, 0), (frac, 1)):
            target = np.minimum(j + whole + extra, y)
            np.add.at(out, (np.arange(d), target), cash[:, j] * part)
    return out


def _records(
    ledger: Ledger,
    v: Mapping[str, np.ndarray],
    mode: str,
    added_by: Mapping[str, np.ndarray],
    optin_by: Mapping[str, np.ndarray],
    restricted_by: Mapping[str, np.ndarray],
    gain_by: Mapping[str, np.ndarray],
    aff_cost: np.ndarray,
    restricted_rent: np.ndarray,
    market_rent: np.ndarray,
    base_r: np.ndarray,
    year_share: np.ndarray,
    years: Sequence[int],
    limits_source: str,
    limit_sources: Sequence[str] = (),
) -> tuple[dict[str, Record], tuple[Record, ...]]:
    period = f"{years[0]}-{years[-1]}"
    # The income-limits table's sources come from the pack (extra_common.table_sources).
    src = tuple(sorted({*DATA_SOURCES, *(limit_sources if limits_source == "hud" else ())}))
    aff_ids = (
        SHARE,
        AMI,
        DURATION,
        CAPRATE,
        PREMIUM,
        *((FALLBACK,) if limits_source != "hud" else ()),
    )
    mode_ids = {
        "deferral": (FEE, MONTHS, LOAN, PUBLIC_RATE, LOSS),
        "waiver": (FEE, WAIVER),
    }
    channel = (ELASTICITY, FEE_K, *VALUE_IDS)
    base_state = base_r.sum(axis=(0, 1))
    limit_words = (
        "HUD income limit (afford_income_limits, 3 people, AMI level / 50% x the 50% limit)"
        if limits_source == "hud"
        else "fallback income limit (county median household income x fee_aff.ami_fallback_ratio "
        "x 0.9 x AMI level; this pack has no HUD income-limits table)"
    )

    def r(name: str, arr: np.ndarray, unit: str, expr: str, ids: Sequence[str]) -> Record:
        return rec.from_draws(
            name, np.asarray(arr, dtype=float), unit, expression=expr,
            assumption_ids=sorted(set(ids)), source_ids=src, tier=0,
        )  # fmt: skip

    w = np.maximum(base_r.sum(axis=1), 1e-12)  # baseline rental homes per county, weights
    out: dict[str, Record] = {}
    out["eligible_baseline"] = r(
        f"baseline rental homes (2+ units) permitted {period}, eligible to opt in",
        base_state,
        "units",
        "the sum over counties of baseline units_2 + units_3_4 + units_5plus",
        (),
    )
    out["restricted_rent"] = r(
        "affordable monthly rent of a restricted two-bedroom home (baseline-weighted mean)",
        (restricted_rent * w).sum(axis=0) / w.sum(axis=0),
        "$/month",
        f"30% x {limit_words} / 12",
        aff_ids,
    )
    out["market_rent"] = r(
        "monthly market rent of a new home (baseline-weighted mean of counties)",
        (market_rent * w).sum(axis=0) / w.sum(axis=0),
        "$/month",
        "county median gross rent x (1 + new-construction premium)",
        (PREMIUM,),
    )
    out["affordability_cost"] = r(
        "affordability cost per home in an opted-in project (present value, weighted mean)",
        (aff_cost * w).sum(axis=0) / w.sum(axis=0),
        "$/unit",
        "restricted share x max(market rent - restricted rent, 0) x 12 x (1 - (1 + cap rate) ^ "
        "-duration) / cap rate",
        aff_ids,
    )
    minimum = float(ledger[MIN_SHARE].central)
    fee, loss = v[FEE], v[LOSS]
    discount = 1.0 - 1.0 / (1.0 + v[PUBLIC_RATE]) ** (v[MONTHS] / 12.0)
    for m in MODES:
        ids = (*mode_ids[m], *aff_ids, *channel, OPTIN)
        add = added_by[m].sum(axis=0)
        optin = optin_by[m].sum(axis=0)
        restricted = restricted_by[m].sum(axis=0)
        gain = (gain_by[m] * w).sum(axis=0) / w.sum(axis=0)
        participating = optin + add
        if m == "waiver":
            waived = fee * v[WAIVER]
            given_up = waived * participating
            on_base = waived * optin
            new_rev = (fee - waived) * add
            cost_words = "waived fee x (opted-in baseline homes + induced homes)"
            base_words = "waived fee x opted-in baseline homes"
            new_words = "(fee - waived fee) x induced homes"
        else:
            lost = discount + loss - discount * loss  # share of each deferred fee's value lost
            given_up = fee * participating * lost
            on_base = fee * optin * lost
            new_rev = fee * add * (1.0 - loss) * (1.0 - discount)
            share_words = (
                "c = d + share never collected - d x share never collected, "
                "d = 1 - 1 / (1 + public rate) ^ (months / 12)"
            )
            cost_words = f"fee x c x (opted-in baseline + induced homes); {share_words}"
            base_words = f"fee x c x opted-in baseline homes; {share_words}"
            new_words = "fee x induced homes x (1 - share never collected) x (1 - d)"
        net = on_base - new_rev
        label = "fee deferral" if m == "deferral" else "fee waiver"
        out[f"{m}_net_gain"] = r(
            f"{label}: builder's net gain per home after the affordability terms (weighted mean)",
            gain, "$/unit",
            "relief per home - affordability cost per home; relief = "
            + ("fee x construction-loan rate x deferral months / 12" if m == "deferral"
               else "fee x fee_aff.waiver_share"),
            (*mode_ids[m], *aff_ids),
        )  # fmt: skip
        out[f"{m}_added"] = r(
            f"{label} with affordability terms: added units authorized {period} (induced homes)",
            add,
            "units",
            "the sum over counties and rental types, where the net gain > 0, of baseline units x "
            "elasticity x (net gain / value of a new home) x pass-through k",
            ids,
        )
        out[f"{m}_optin_baseline"] = r(
            f"{label}: baseline homes in projects that opt in anyway {period}",
            optin,
            "units",
            "the sum over counties, where the net gain > 0, of baseline rental homes x "
            "fee_aff.optin_share",
            ids,
        )
        out[f"{m}_restricted"] = r(
            f"{label}: income-restricted homes {period}",
            restricted,
            "units",
            "restricted share x (opted-in baseline homes + induced homes)",
            ids,
        )
        out[f"{m}_revenue_given_up"] = r(
            f"{label}: public revenue given up on all participating homes (gross) {period}"
            + (" (time value and collection loss)" if m == "deferral" else ""),
            given_up,
            "$",
            cost_words,
            ids,
        )
        out[f"{m}_given_up_on_baseline"] = r(
            f"{label}: public revenue given up on homes that would be built anyway {period}",
            on_base,
            "$",
            base_words,
            ids,
        )
        out[f"{m}_new_revenue"] = r(
            f"{label}: fee revenue from induced projects {period}"
            + (" (present value)" if m == "deferral" else ""),
            new_rev,
            "$",
            new_words,
            ids,
        )
        out[f"{m}_net_public_cost"] = r(
            f"{label}: net public cost {period}",
            net,
            "$",
            "revenue given up on homes that would be built anyway - fee revenue from induced "
            "projects (relief on induced homes is not counted: they would not exist without "
            "the deal)",
            ids,
        )
        ratio, share = per_draw_ratio(net, add, minimum)
        if ratio is not None:
            out[f"{m}_cost_per_added"] = r(
                f"{label}: net public cost per added home "
                f"(in the {share:.0%} of draws that add homes)",
                ratio,
                "$/unit",
                "net public cost / induced homes",
                (*ids, MIN_SHARE),
            )
        ratio, share = per_draw_ratio(net, restricted, minimum)
        if ratio is not None:
            out[f"{m}_cost_per_restricted"] = r(
                f"{label}: net public cost per restricted home "
                f"(in the {share:.0%} of draws with any)",
                ratio,
                "$/unit",
                "net public cost / income-restricted homes",
                (*ids, MIN_SHARE),
            )

    # Cash-flow timing of the deferral: participating homes spread by year.
    part = (optin_by["deferral"].sum(axis=0)[:, None] / len(years)) + added_by["deferral"].sum(
        axis=0
    )[:, None] * year_share
    at_permit = part * fee[:, None]
    deferred = _deferred_cash(part, fee * (1.0 - loss), v[MONTHS])  # less the share never collected
    ids_t = (*mode_ids["deferral"], *aff_ids, *channel, OPTIN, FIRST, FULL, DELAY)
    for j, y in enumerate(years):
        out[f"cash_{y}_at_permit"] = r(
            f"fees from participating homes due in {y} if paid at permit",
            at_permit[:, j],
            "$",
            "fee x participating homes permitted that year (opted-in baseline spread evenly, "
            "induced homes by the fee ramp)",
            ids_t,
        )
        out[f"cash_{y}_deferred"] = r(
            f"fees from participating homes collected in {y} under the deferral",
            deferred[:, j],
            "$",
            "fee x (1 - share never collected) x participating homes, each year's fees moved "
            "deferral months / 12 years later (split between the two years it straddles)",
            ids_t,
        )
    out["cash_after_window"] = r(
        f"deferred fees collected after {years[-1]}",
        deferred[:, -1],
        "$",
        "fee x (1 - share never collected) x homes permitted in the last deferral-months of "
        "the window",
        ids_t,
    )
    steps = tuple(
        out[k]
        for k in (
            "eligible_baseline", "affordability_cost", f"{mode}_net_gain", f"{mode}_added",
            f"{mode}_restricted", f"{mode}_net_public_cost",
        )
    )  # fmt: skip
    return out, steps
