"""Adapters for the four extra scenarios (beyond the five benchmark reforms).

Plain-language summary
----------------------
Each extra scenario is registered like a benchmark reform, under the same name
as its entry in ``assumptions/scenarios.yaml`` (kind ``extra``). The five
benchmark presets are untouched: these are separate reforms with their own
ledger entries, and each says which benchmark it builds on.

=========================================  ==========  ==================================
Key                                        Level       Cannot share a package with
=========================================  ==========  ==================================
``transit_zoning_funded_infrastructure``   county      ``tod`` (it *is* TOD plus funding)
``commercial_land_housing``                tract       ``tod`` and the funded-TOD scenario
                                                       (station-area commercial land is
                                                       already TOD land)
``adaptive_reuse``                         tract       nothing (adds no BPS permits)
``starter_home_lots``                      tract       ``adu``, ``missing_middle`` (same
                                                       single-family lots)
``fee_deferral_with_affordability``        county      ``fees`` (same fees)
=========================================  ==========  ==================================

Options:

- ``transit_zoning_funded_infrastructure``: ``funding`` (``grant`` default, or
  ``financed``), ``distance`` and ``walk`` as the TOD reform.
- ``starter_home_lots``: ``appraisal`` (``flag`` default, or ``binding``).
- ``fee_deferral_with_affordability``: ``mode`` (``deferral`` default, or ``waiver``).
- every one: ``variant`` (``assumptions/variants.yaml``).

Optional pack tables are read through ``scenarios.extra_common.pack_table``;
a state without one says so in the run notes and county notes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from visionhousing.engine import records as rec
from visionhousing.engine.adapters import (
    MF_COLUMN,
    OPTION_DISTANCE,
    OPTION_WALK,
    _channel_b_inputs,
    _tod_tracts,
    _variant_notes,
    reform_ledger,
    tod_by_right,
)
from visionhousing.engine.registry import Draws, ReformResult, RunContext, register
from visionhousing.engine.variants import expand_options
from visionhousing.scenarios.commercial_land import commercial_land_funnel, reuse_funnel
from visionhousing.scenarios.extra_common import pack_table, table_sources
from visionhousing.scenarios.fee_affordability import fee_affordability
from visionhousing.scenarios.starter_lots import starter_lots_funnel
from visionhousing.scenarios.tod import (
    DEFAULT_DISTANCE,
    NETWORK_DISTANCE,
    TodScope,
    distance_words,
)
from visionhousing.scenarios.tod_infrastructure import infrastructure_funnel, readiness_shares

__all__ = ["EXTRA_KEYS"]

EXTRA_KEYS = (
    "transit_zoning_funded_infrastructure",
    "commercial_land_housing",
    "adaptive_reuse",
    "starter_home_lots",
    "fee_deferral_with_affordability",
)
"""Registry keys of the extra scenarios, in the order of ``assumptions/scenarios.yaml``."""


def _draft_note(ids: Any, ledger: Any) -> list[str]:
    draft = sorted(i for i in ids if i in ledger and ledger[i].status == "draft")
    return (
        ["Draft inputs (team judgement, wide ranges): " + ", ".join(draft) + "."] if draft else []
    )


def _mf_inputs(context: RunContext, draws: Draws, tracts: Any) -> tuple[Any, np.ndarray]:
    order = sorted(set(tracts["county"].astype(str)))
    rows = [context.county_ids.index(c) for c in order]
    return context.baseline_types.get(MF_COLUMN), draws.baseline_scale[rows]


# ---------------------------------------------------------------- transit zoning + infrastructure

KEY_INFRA = "transit_zoning_funded_infrastructure"


@register(
    KEY_INFRA,
    "Transit zoning with funded infrastructure",
    family=KEY_INFRA,
    excludes=("tod",),
)
def infrastructure_adapter(
    context: RunContext, draws: Draws, options: Mapping[str, Any]
) -> ReformResult:
    """TOD upzoning with a funded water/sewer capacity lever (``scenarios.tod_infrastructure``)."""
    state = context.state
    scenario = state.scenarios[KEY_INFRA]
    options = expand_options(KEY_INFRA, options)
    ledger = reform_ledger(context, draws, KEY_INFRA, options, family=KEY_INFRA)
    has_walk = getattr(state, "transit_walk", None) is not None
    walk = str(options.get(OPTION_WALK, "straight"))
    if walk == "network" and not has_walk:
        walk = "straight"
    default = NETWORK_DISTANCE if walk == "network" else DEFAULT_DISTANCE
    scope = TodScope(name=KEY_INFRA, distance=options.get(OPTION_DISTANCE, default), walk=walk)
    tracts = _tod_tracts(context, ledger)
    infra = pack_table(state, "infra_tract")
    mf, scale = _mf_inputs(context, draws, tracts)
    funding = str(options.get("funding", "grant"))
    _, existing_for, zoned = tod_by_right(state, tracts, options)
    res = infrastructure_funnel(
        tracts, ledger, scope, infra, funding=funding, mf_baseline=mf, mf_scale=scale,
        draws=draws.n, seed=draws.seed, zoned_m2=zoned, existing=existing_for(scope.suffix),
    )  # fmt: skip
    head = res.records["total"]
    notes = [
        *scenario.notes,
        f"Distance: {distance_words(scope.distance, scope.walk)}; the TOD funnel and draws of "
        "the benchmark, run twice: builders paying for water and sewer capacity (zoning alone), "
        "and with the capacity "
        + ("granted by the public." if funding == "grant" else "financed (builders repay part)."),
        "The infrastructure lever is credited only with max(funded - zoning alone, 0) per county "
        "and draw, and only for permits on or after the in-service date (infra.in_service_year); "
        "a permit feasible without the money is never counted as caused by it.",
    ]
    if infra is None:
        notes.append(
            "This state's pack has no water and sewer readiness table (infra_tract): every "
            "station area is 'unknown' and uses infra.need_share_unknown."
        )
    notes += _variant_notes(KEY_INFRA, options)
    notes += _draft_note(head.assumption_ids, ledger)
    shares = readiness_shares(tracts, infra)
    county_notes = {}
    tc = tracts["county"].astype(str).to_numpy()
    homes = tracts[f"transit_units_{scope.suffix}"].fillna(0).to_numpy(dtype=float)
    for c in res.counties:
        m = tc == c
        tot = float(homes[m].sum())
        if tot <= 0:
            continue
        ver = float((shares["verified"].to_numpy()[m] * homes[m]).sum()) / tot
        unk = float((shares["unknown"].to_numpy()[m] * homes[m]).sum()) / tot
        county_notes[c] = (
            f"station-area homes with verified public sewer {ver:.0%}, unknown {unk:.0%} "
            "(infra_tract)",
            ("pa_montco_sewer_service_areas", "pa_adams_sewer_service_areas", "phl_pwd_wastewater")
            if infra is not None and ver > 0
            else (),
        )
    return ReformResult(
        reform=KEY_INFRA,
        title=scenario.title,
        level="county",
        geoids=res.counties,
        county_of=res.counties,
        added=np.ascontiguousarray(res.total.T),
        headline=head,
        steps=res.steps,
        records=dict(res.records),
        assumption_ids=head.assumption_ids,
        notes=tuple(notes),
        detail=res,
        ledger=ledger,
        county_notes=county_notes,
        year_share=res.year_share,
        net_stock=np.ascontiguousarray(res.net.T),
        removed=np.ascontiguousarray(res.removed.T),
    )


# ---------------------------------------------------------------- commercial land and reuse

KEY_COMMERCIAL = "commercial_land_housing"
KEY_REUSE = "adaptive_reuse"
NOT_MEASURED = (
    "eligible land not measured: no parcel land-use table for this county (needs local land "
    "data; no free statewide commercial-zoning layer exists), so no homes are counted"
)


def _land_notes(
    res: Any, counties: Any, sources: tuple[str, ...]
) -> dict[str, tuple[str, tuple[str, ...]]]:
    out = {}
    for c in sorted(set(counties)):
        if c in res.measured_counties:
            out[c] = (
                "commercial land from parcel land use (parcels_area lu_commercial)",
                sources,
            )
        else:
            out[c] = (NOT_MEASURED, ())
    return out


def _land_adapter(key: str):
    def adapter(context: RunContext, draws: Draws, options: Mapping[str, Any]) -> ReformResult:
        state = context.state
        scenario = state.scenarios[key]
        options = expand_options(key, options)
        ledger = reform_ledger(context, draws, key, options, family=key)
        tracts = state.tracts
        parcels = pack_table(state, "parcels_area")
        mf, scale = _mf_inputs(context, draws, tracts)
        funnel = commercial_land_funnel if key == KEY_COMMERCIAL else reuse_funnel
        res = funnel(
            tracts, ledger, parcels, mf_baseline=mf, mf_scale=scale, draws=draws.n, seed=draws.seed
        )
        notes = list(scenario.notes)
        n_meas = len(res.measured_counties)
        n_all = len(set(res.counties))
        notes.append(
            f"Eligible land is measured in {n_meas} of {n_all} counties (parcel land use); "
            "the others are marked 'eligible land not measured' and count no homes."
        )
        if key == KEY_REUSE:
            head = rec.from_draws(
                "added units authorized 2027-2031 by adaptive reuse (none: conversions are "
                "outside the Building Permits Survey measure; see conversion_homes)",
                np.zeros(draws.n),
                "units",
                expression="0 in every draw: a conversion is an alteration, not new construction",
                assumption_ids=res.records["conversions"].assumption_ids,
                source_ids=("bps_county_annual",),
                tier=0,
            )
            added = np.zeros((len(res.geoids), draws.n))
            notes.append(
                "Conversions are reported OUTSIDE the BPS-comparable total (record "
                "conversion_homes): the Building Permits Survey leaves out alterations of existing "
                "buildings, so this reform adds 0 to added units authorized and to any package."
            )
        else:
            head = res.records["authorized"]
            added = res.stages["authorized"].T
        notes += _variant_notes(key, options)
        notes += _draft_note(res.records[res.headline].assumption_ids, ledger)
        return ReformResult(
            reform=key,
            title=scenario.title,
            level="tract",
            geoids=tuple(str(g) for g in res.geoids),
            county_of=tuple(str(c) for c in res.counties),
            added=added,
            headline=head,
            steps=res.steps,
            records=dict(res.records),
            assumption_ids=res.records[res.headline].assumption_ids,
            notes=tuple(notes),
            detail=res,
            ledger=ledger,
            county_notes=_land_notes(res, res.counties, table_sources(parcels)),
            year_share=res.year_share,
        )

    return adapter


register(
    KEY_COMMERCIAL,
    "Housing by right on commercial land",
    family=KEY_COMMERCIAL,
    excludes=("tod", KEY_INFRA),
)(_land_adapter(KEY_COMMERCIAL))
register(KEY_REUSE, "Adaptive reuse (office and retail conversions)", family=KEY_REUSE)(
    _land_adapter(KEY_REUSE)
)


# ---------------------------------------------------------------- starter-home lots

KEY_STARTER = "starter_home_lots"


@register(
    KEY_STARTER,
    "Starter-home lots",
    family=KEY_STARTER,
    excludes=("adu", "missing_middle"),
)
def starter_adapter(context: RunContext, draws: Draws, options: Mapping[str, Any]) -> ReformResult:
    """Smaller minimum lots on single-family land (``scenarios.starter_lots``)."""
    state = context.state
    scenario = state.scenarios[KEY_STARTER]
    options = expand_options(KEY_STARTER, options)
    ledger = reform_ledger(context, draws, KEY_STARTER, options, family=KEY_STARTER)
    tracts = state.tracts
    infra = pack_table(state, "infra_tract")
    outside = None if infra is None else readiness_shares(tracts, infra)["outside"].to_numpy()
    res = starter_lots_funnel(
        tracts, ledger, outside_share=outside, sewer_sources=table_sources(infra),
        appraisal=str(options.get("appraisal", "flag")), draws=draws.n, seed=draws.seed,
    )  # fmt: skip
    head = res.records["authorized"]
    notes = [
        *scenario.notes,
        "Lot sizes from: "
        + (
            "block land per home (lot-size proxy)."
            if res.fit_source == "lot_proxy"
            else "the ledger (starter.newly_splittable_share; no lot-size proxy in this pack)."
        ),
        "Only splits today's minimum lot does not already allow are counted; existing homes "
        "torn down for a rebuild are reported (record replaced) and subtracted in net added homes.",
    ]
    if infra is None:
        notes.append(
            "No water and sewer readiness table in this pack: no lot is left out for "
            "lacking public sewer."
        )
    notes += _variant_notes(KEY_STARTER, options)
    notes += _draft_note(head.assumption_ids, ledger)
    return ReformResult(
        reform=KEY_STARTER,
        title=scenario.title,
        level="tract",
        geoids=tuple(str(g) for g in res.geoids),
        county_of=tuple(str(c) for c in res.counties),
        added=res.stages["authorized"].T,
        headline=head,
        steps=res.steps,
        records=dict(res.records),
        assumption_ids=head.assumption_ids,
        notes=tuple(notes),
        detail=res,
        ledger=ledger,
        year_share=res.year_share,
        net_stock=res.stages["net"].T,
        removed=res.stages["replaced"].T,
    )


# ---------------------------------------------------------------- fee deferral with affordability

KEY_FEES = "fee_deferral_with_affordability"


@register(
    KEY_FEES,
    "Fee deferral or waiver with affordability terms",
    family=KEY_FEES,
    excludes=("fees",),
)
def fee_affordability_adapter(
    context: RunContext, draws: Draws, options: Mapping[str, Any]
) -> ReformResult:
    """Fee relief tied to income-restricted homes (``scenarios.fee_affordability``)."""
    from visionhousing.features.affordability import load_settings

    state = context.state
    scenario = state.scenarios[KEY_FEES]
    options = expand_options(KEY_FEES, options)
    ledger = reform_ledger(context, draws, KEY_FEES, options, family=KEY_FEES)
    baseline, scale, _ = _channel_b_inputs(context, draws)
    limits = pack_table(state, "afford_income_limits")
    mode = str(options.get("mode", "deferral"))
    res = fee_affordability(
        baseline, state.tracts, ledger, limits=limits,
        rent_share=float(load_settings()["rent_share_of_income"]), mode=mode,
        n_draws=draws.n, seed=draws.seed, baseline_scale=scale,
    )  # fmt: skip
    head = res.records[f"{mode}_added"]
    notes = [
        *scenario.notes,
        f"Headline: fee {mode} tied to income-restricted homes; the other mode is reported on "
        "the same draws (records deferral_* and waiver_*).",
        "Only rental buildings of two or more homes may opt in; single-family homes are left out.",
    ]
    if res.limits_source != "hud":
        notes.append(
            "This pack has no HUD income-limits table: restricted rents use the county median "
            "household income x fee_aff.ami_fallback_ratio (a stated fallback)."
        )
    notes += _variant_notes(KEY_FEES, options)
    notes += _draft_note(head.assumption_ids, ledger)
    return ReformResult(
        reform=KEY_FEES,
        title=scenario.title,
        level="county",
        geoids=res.county_ids,
        county_of=res.county_ids,
        added=np.ascontiguousarray(res.added),
        headline=head,
        steps=res.steps,
        records=dict(res.records),
        assumption_ids=head.assumption_ids,
        notes=tuple(notes),
        detail=res,
        ledger=ledger,
        year_share=res.year_share,
    )
