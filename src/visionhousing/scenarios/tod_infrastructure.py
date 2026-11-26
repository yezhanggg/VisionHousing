"""Extra scenario: transit-oriented zoning plus funded water and sewer capacity.

Plain-language summary
----------------------
The TOD benchmark asks what more height and floor area near major transit stops
would add. It quietly assumes every station area already has the water, sewer
and power capacity for many more apartments. Often it does not: a builder may
have to pay for a capacity upgrade, or a sewer moratorium may stop building.
This scenario makes that cost explicit and then asks what a **funded
infrastructure lever** adds on top of the zoning change.

Two TOD runs on the same draws (the same funnel as the benchmark,
``scenarios.tod.tod_funnel``):

1. **Zoning alone** (``A``): the upzoning, but each apartment in a station area
   pays a capacity cost ``c x need``, where ``need`` is the tract's share of
   homes whose service is not known to have room (read from the pack's
   water-and-sewer readiness table ``infra_tract``: verified, outside a mapped
   service area, documented constraint, unknown). Land under a documented
   constraint (moratorium) cannot be used at all.
2. **Zoning with funded capacity** (``B``): the public pays the capacity cost
   (option ``funding="grant"``, the default) or finances it so a builder pays
   only ``infra.financed_builder_share`` of it (``funding="financed"``), and the
   constraint is lifted.

What the infrastructure lever is credited with is only ``B - A`` per county
and draw, never below zero, so a permit that was feasible without the money is
never counted as caused by it. Those infrastructure-dependent permits also count
only on or after the capacity's **in-service date** (``infra.in_service_year``):
permits in earlier years are not issued in the window (reported apart as
"waiting for service").

    total (county) = A + max(B - A, 0) x share of B's permits on or after the in-service date

With ``funding="grant"`` and service on January 1, 2027, ``B`` is the TOD
benchmark run itself, so the total can never exceed the benchmark on the same
draws; a later service date only lowers it.

Public cost: the capacity paid for apartments permitted after the service date
in tracts that need it, ``c x (1 - builder share) x need x B``; the part spent
on apartments that were feasible anyway (``A``) is reported as the windfall.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.ledger.loader import Ledger
from visionhousing.ledger.sampling import sample
from visionhousing.scenarios.extra_common import in_service_weight, per_draw_ratio, table_sources
from visionhousing.scenarios.tod import (
    DATA_SOURCES,
    HEADLINE_STAGE,
    NET_STOCK_STAGE,
    ExistingCapacity,
    TodResult,
    TodScope,
    distance_words,
    tod_funnel,
)

__all__ = [
    "FUNDING",
    "IDS",
    "InfraResult",
    "infrastructure_funnel",
    "readiness_shares",
]

COST = "infra.capacity_cost_per_apartment"
NEED_VERIFIED = "infra.need_share_verified"
NEED_UNKNOWN = "infra.need_share_unknown"
NEED_OUTSIDE = "infra.need_share_outside"
SERVICE = "infra.in_service_year"
FINANCED = "infra.financed_builder_share"
MIN_SHARE = "fees.cost_per_added_min_share"
IDS = (COST, NEED_VERIFIED, NEED_UNKNOWN, NEED_OUTSIDE, SERVICE, FINANCED)
"""Ledger ids this scenario adds to the TOD funnel's."""

FUNDING = ("grant", "financed")
"""``grant``: the public pays the capacity cost; ``financed``: the builder repays part of it."""


STATUSES = ("verified", "outside", "constraint", "unknown")


def readiness_shares(tracts: pd.DataFrame, infra: pd.DataFrame | None) -> pd.DataFrame:
    """Each tract's share of homes by readiness answer (rows add to 1).

    From ``infra_tract`` (``features.infrastructure.tract_table``):
    ``sewer_share`` is every home inside a public-sewer service area, **including**
    homes under a documented constraint there (a moratorium); ``constraint_share``
    is every home under a constraint; ``sewer_known`` covers both. So, with
    S = sewer_share, C = constraint_share, K = sewer_known::

        constraint = C
        verified   = S - min(C, S)        (served homes not under a constraint)
        outside    = K - S - (C - min(C, S)), never below 0
        unknown    = the rest

    A constraint is read as falling inside the service area first (moratoria
    apply to served areas), the same reading as the place profile's
    infrastructure section. A tract the table does not list, or a state
    without the table, is wholly unknown.

    Returns:
        Frame indexed like ``tracts`` with columns :data:`STATUSES`.
    """
    n = len(tracts)
    out = pd.DataFrame(
        {"verified": np.zeros(n), "outside": np.zeros(n), "constraint": np.zeros(n)},
        index=tracts.index,
    )
    if infra is not None and not infra.empty:
        t = infra.assign(geoid=infra["geoid"].astype(str)).set_index("geoid")
        geo = tracts["geoid"].astype(str)

        def col(name: str) -> np.ndarray:
            if name not in t:
                return np.zeros(n)
            vals = pd.to_numeric(t[name], errors="coerce")
            return np.clip(np.nan_to_num(geo.map(vals).to_numpy(dtype=float)), 0.0, 1.0)

        served, known, cons = col("sewer_share"), col("sewer_known"), col("constraint_share")
        inside = np.minimum(cons, served)  # constraint homes inside the service area
        out["verified"] = served - inside
        out["constraint"] = cons
        out["outside"] = np.clip(known - served - (cons - inside), 0.0, 1.0)
    out["unknown"] = np.clip(1.0 - out[["verified", "outside", "constraint"]].sum(axis=1), 0, 1)
    return out[list(STATUSES)]


@dataclass(frozen=True)
class InfraResult:
    """What :func:`infrastructure_funnel` returns (county level, draws x counties arrays).

    Attributes:
        counties: County ids (sorted), the columns of every array.
        zoning_alone: ``A``: added units authorized with builders paying for capacity.
        funded: ``B``: the same with the capacity funded (before the service date).
        infra_raw: ``max(B - A, 0)``: permits that need the funded capacity.
        infra_in: The part of ``infra_raw`` permitted on or after the in-service date.
        total: ``A + infra_in`` (the headline).
        net: Net change in the housing stock on the same basis.
        removed: Existing homes demolished on the same basis.
        year_share: ``(draws, years)`` yearly split of the state total.
        records: State records, by name.
        steps: The funnel records in order.
        a, b: The two TOD runs (for the explanation layer).
        funding: ``grant`` or ``financed``.
    """

    counties: tuple[str, ...]
    zoning_alone: np.ndarray
    funded: np.ndarray
    infra_raw: np.ndarray
    infra_in: np.ndarray
    total: np.ndarray
    net: np.ndarray
    removed: np.ndarray
    year_share: np.ndarray
    records: dict[str, Record]
    steps: tuple[Record, ...]
    a: TodResult = field(repr=False)
    b: TodResult = field(repr=False)
    funding: str = "grant"


def _by_county(arr: np.ndarray, counties: np.ndarray, order: Sequence[str]) -> np.ndarray:
    """Sum a draws x tracts array to draws x counties (in ``order``)."""
    out = np.zeros((arr.shape[0], len(order)))
    for i, c in enumerate(order):
        mask = counties == c
        if mask.any():
            out[:, i] = arr[:, mask].sum(axis=1)
    return out


def infrastructure_funnel(
    tracts: pd.DataFrame,
    ledger: Ledger,
    scope: TodScope,
    infra: pd.DataFrame | None,
    *,
    funding: str = "grant",
    mf_baseline: pd.Series | Mapping[str, float] | None = None,
    mf_scale: np.ndarray | None = None,
    draws: int = 2000,
    seed: int = 0,
    zoned_m2: np.ndarray | None = None,
    existing: ExistingCapacity | None = None,
) -> InfraResult:
    """Run zoning alone and zoning with funded capacity on the same draws, and combine them.

    Args:
        tracts: The TOD tract table (with the transit columns for the scope).
        ledger: The ledger (scenario, variant and user overrides applied).
        scope: The TOD scope (distance and walk rule).
        infra: The pack's ``infra_tract`` table, or ``None`` (every tract unknown).
        funding: ``grant`` or ``financed``.
        mf_baseline: County baseline 5+ unit permits, the absorption cap's scale.
        mf_scale: Baseline multiplier per county and draw (as ``tod_funnel``).
        draws: Monte Carlo draws.
        seed: Seed.
        zoned_m2: Land already zoned by right (as ``tod_funnel``).
        existing: Existing by-right capacity (as ``tod_funnel``).

    Returns:
        An :class:`InfraResult`.
    """
    if funding not in FUNDING:
        raise ValueError(f"funding must be one of {FUNDING}, got {funding!r}")
    v = sample(ledger, n=draws, seed=seed, ids=list(IDS))
    shares = readiness_shares(tracts, infra)
    ver, out_, cons, unk = (shares[s].to_numpy()[None, :] for s in STATUSES)
    need = (
        ver * v[NEED_VERIFIED][:, None]
        + out_ * v[NEED_OUTSIDE][:, None]
        + unk * v[NEED_UNKNOWN][:, None]
    ) / np.maximum(1.0 - cons, 1e-12)  # need share of the usable (non-constrained) land
    need = np.clip(need, 0.0, 1.0)
    cost = v[COST][:, None] * need  # $ per apartment, draws x tracts
    need_b = np.clip(need * (1.0 - cons) + cons, 0.0, 1.0)  # constrained land needs it all
    builder = np.zeros(draws) if funding == "grant" else v[FINANCED]
    kw = dict(
        mf_baseline=mf_baseline,
        mf_scale=mf_scale,
        draws=draws,
        seed=seed,
        zoned_m2=zoned_m2,
        existing=existing,
    )
    a = tod_funnel(tracts, ledger, scope, extra_cost_per_unit=cost, land_factor=1.0 - cons[0], **kw)
    b = tod_funnel(
        tracts,
        ledger,
        scope,
        extra_cost_per_unit=v[COST][:, None] * need_b * builder[:, None]
        if funding == "financed"
        else None,
        **kw,
    )
    county_of = tracts["county"].astype(str).to_numpy()
    order = tuple(sorted(set(county_of)))
    a_auth = _by_county(a.stages[HEADLINE_STAGE], county_of, order)
    b_auth = _by_county(b.stages[HEADLINE_STAGE], county_of, order)
    a_net = _by_county(a.stages[NET_STOCK_STAGE], county_of, order)
    b_net = _by_county(b.stages[NET_STOCK_STAGE], county_of, order)
    a_rem = _by_county(a.removed, county_of, order)
    b_rem = _by_county(b.removed, county_of, order)
    years = a.years
    w, counted = in_service_weight(b.year_share, years, v[SERVICE])
    raw = np.maximum(b_auth - a_auth, 0.0)
    gate = (b_auth - a_auth > 0).astype(float)
    infra_in = raw * w[:, None]
    total = a_auth + infra_in
    net = a_net + gate * (b_net - a_net) * w[:, None]
    removed = a_rem + gate * np.maximum(b_rem - a_rem, 0.0) * w[:, None]
    a_state, in_state = a_auth.sum(axis=1), infra_in.sum(axis=1)
    raw_state = raw.sum(axis=1)
    tot_state = total.sum(axis=1)
    mix = a_state[:, None] * a.year_share + raw_state[:, None] * counted
    with np.errstate(divide="ignore", invalid="ignore"):
        year_share = np.where(
            tot_state[:, None] > 0, mix / np.where(tot_state > 0, tot_state, 1.0)[:, None], 0.0
        )
    flat = np.full_like(year_share, 1.0 / year_share.shape[1])
    year_share = np.where(tot_state[:, None] > 0, year_share, flat)

    # Public capital cost: capacity for apartments permitted after service, where needed.
    public_share = 1.0 - builder
    b_t = b.stages[HEADLINE_STAGE]
    a_t = a.stages[HEADLINE_STAGE]
    cost_all = v[COST] * public_share * (need_b * b_t).sum(axis=1) * w
    cost_windfall = v[COST] * public_share * (need_b * np.minimum(a_t, b_t)).sum(axis=1) * w

    records = _records(
        ledger, scope, funding, shares, b, a_state, raw_state, in_state, tot_state,
        b_auth.sum(axis=1), cost_all, cost_windfall, net.sum(axis=1), w, infra is not None,
        table_sources(infra),
    )  # fmt: skip
    steps = tuple(
        records[k] for k in ("zoning_alone", "funded_no_date", "infra_raw", "infra_in", "total")
    )
    return InfraResult(
        counties=order,
        zoning_alone=a_auth,
        funded=b_auth,
        infra_raw=raw,
        infra_in=infra_in,
        total=total,
        net=net,
        removed=removed,
        year_share=year_share,
        records=records,
        steps=steps,
        a=a,
        b=b,
        funding=funding,
    )


def _records(
    ledger: Ledger,
    scope: TodScope,
    funding: str,
    shares: pd.DataFrame,
    b: TodResult,
    a_state: np.ndarray,
    raw_state: np.ndarray,
    in_state: np.ndarray,
    tot_state: np.ndarray,
    b_state: np.ndarray,
    cost_all: np.ndarray,
    cost_windfall: np.ndarray,
    net_state: np.ndarray,
    w: np.ndarray,
    has_table: bool,
    infra_sources: Sequence[str] = (),
) -> dict[str, Record]:
    words = distance_words(scope.distance, scope.walk)
    tod_ids = tuple(b.records[HEADLINE_STAGE].assumption_ids)
    infra_ids = (COST, NEED_VERIFIED, NEED_UNKNOWN, NEED_OUTSIDE)
    fin_ids = (FINANCED,) if funding == "financed" else ()
    src = tuple(sorted({*b.records[HEADLINE_STAGE].source_ids, *infra_sources}))
    ids_all = tuple(sorted({*tod_ids, *infra_ids, SERVICE, *fin_ids}))
    payer = (
        "the public pays the capacity cost (grant)"
        if funding == "grant"
        else "the capacity cost is financed; builders repay infra.financed_builder_share of it"
    )

    def r(name: str, arr: np.ndarray, unit: str, expr: str, ids: Sequence[str]) -> Record:
        return rec.from_draws(
            name, arr, unit, expression=expr, assumption_ids=sorted(set(ids)),
            source_ids=src, tier=0,
        )  # fmt: skip

    out: dict[str, Record] = {}
    out["zoning_alone"] = r(
        f"added units authorized 2027-2031 by the zoning change alone, builders paying for "
        f"water and sewer capacity ({words})",
        a_state, "units",
        "the TOD funnel with the residual land value per apartment lowered by capacity cost "
        "x need share (need = verified share x infra.need_share_verified + outside share x "
        "infra.need_share_outside + unknown share x infra.need_share_unknown, of the land not "
        "under a documented constraint) and land under a documented constraint removed",
        (*tod_ids, *infra_ids),
    )  # fmt: skip
    out["funded_no_date"] = r(
        f"added units authorized 2027-2031 with funded capacity, if it served from 2027 ({words})",
        b_state, "units",
        f"the TOD funnel when {payer}, constraint lifted (the TOD benchmark when a grant)",
        (*tod_ids, *fin_ids),
    )  # fmt: skip
    out["infra_raw"] = r(
        "permits that need the funded capacity (never credited when feasible without it)",
        raw_state, "units",
        "the sum over counties of max(funded - zoning alone, 0)",
        ids_all,
    )  # fmt: skip
    out["infra_in"] = r(
        "permits that need the funded capacity and are issued on or after its in-service date",
        in_state, "units",
        "the sum over counties of max(funded - zoning alone, 0) x share of the funded run's "
        "permits in years on or after infra.in_service_year (a year counts in part when "
        "service starts during it)",
        ids_all,
    )  # fmt: skip
    out["waiting_for_service"] = r(
        "permits that need the funded capacity but fall before its in-service date "
        "(not issued in 2027-2031)",
        raw_state - in_state, "units",
        "infra_raw - infra_in",
        ids_all,
    )  # fmt: skip
    out["total"] = r(
        f"added units authorized 2027-2031: transit zoning with funded infrastructure ({words})",
        tot_state, "units",
        "zoning alone + permits that need the funded capacity issued on or after its "
        "in-service date (county by county)",
        ids_all,
    )  # fmt: skip
    out["net"] = r(
        "net added homes after demolition 2027-2031 (transit zoning with funded infrastructure)",
        net_state, "units",
        "zoning alone net + (funded net - zoning alone net) x in-service share, where the "
        "funded run adds permits",
        ids_all,
    )  # fmt: skip
    out["in_service_share"] = r(
        "share of the funded run's permits on or after the in-service date",
        w, "share",
        "the sum over years of the funded run's year share x "
        "clip(year + 1 - in-service year, 0, 1)",
        (SERVICE, *tod_ids),
    )  # fmt: skip
    out["public_capital_cost"] = r(
        "public capacity cost for station-area apartments permitted after the service date",
        cost_all, "$",
        "capacity cost per apartment x (1 - builder share) x the sum over tracts of need share "
        "x funded-run permits x in-service share",
        ids_all,
    )  # fmt: skip
    out["public_cost_windfall"] = r(
        "part of that cost spent on apartments that were feasible without the money",
        cost_windfall, "$",
        "capacity cost per apartment x (1 - builder share) x the sum over tracts of need share "
        "x min(zoning-alone permits, funded permits) x in-service share",
        ids_all,
    )  # fmt: skip
    minimum = float(ledger[MIN_SHARE].central) if MIN_SHARE in ledger else 0.5
    ratio, share = per_draw_ratio(cost_all, in_state, minimum)
    if ratio is not None:
        out["public_cost_per_infra_home"] = r(
            f"public capacity cost per permit the funding adds (in the {share:.0%} of draws "
            "where it adds any)",
            ratio, "$/unit",
            "public capacity cost / permits that need the funded capacity issued after service",
            (*ids_all, MIN_SHARE),
        )  # fmt: skip
    homes = float(np.nansum(b.eligible_homes))
    for status in STATUSES:
        sh = shares[status].to_numpy()
        val = float(np.nansum(sh * b.eligible_homes))
        out[f"readiness_{status}"] = rec.quantity(
            f"existing homes within {words} of a major stop, sewer answer '{status}'",
            val,
            "units",
            formula=(
                f"the sum over tracts of 2020 homes within the distance x the tract's share of "
                f"homes with answer '{status}' (infra_tract) = {rec.format_number(val, 'units')} "
                f"of {rec.format_number(homes, 'units')}"
                + (
                    ""
                    if has_table
                    else "; this state's pack has no readiness table, so every tract is unknown"
                )
            ),
            source_ids=tuple(sorted({*DATA_SOURCES["eligible"], *infra_sources})),
            tier=0,
        )
    return out
