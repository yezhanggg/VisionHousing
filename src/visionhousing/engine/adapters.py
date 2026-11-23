"""Thin adapters that put each reform module behind the registry's common signature.

Plain-language summary
----------------------
Each adapter takes the run context, the run's draws and the reform's options,
calls the reform module in ``scenarios/`` exactly as that module documents, and
repackages its output as a :class:`~visionhousing.engine.registry.ReformResult`.
No reform arithmetic happens here; the modules are not changed.

Which ledger a reform sees, in order:

1. the base ledger, with the state's calibration (``StateContext.ledger``);
2. the reform's scenario overrides from ``assumptions/scenarios.yaml``
   (for example SB 1346 sets the ADU utility connection fee to 0);
3. the user's overrides for this run (they win over the scenario);
4. for the ADU reform inside a package: the fee cut and the parking reform
   applied to the ADU's own fee and parking lines (see ``engine.packages``).

Registered reforms:

- ``adu``: benchmark reform 1 (``scenarios.adu``), by tract.
- ``pa_hb2186`` and ``pa_sb1346``: the two Pennsylvania ADU bills (same module,
  their own scope); same family as ``adu``, so only one of the three can be in a
  package.
- ``fees``: benchmark reform 5 (``scenarios.fees``), by county. Option ``view``:
  ``"benchmark"`` (default) or ``"state"``.
- ``parking``: benchmark reform 4 (``scenarios.parking``), by county.
- ``missing_middle``: benchmark reform 2 (``scenarios.missing_middle``), by
  tract. Option ``adu_lots`` (draws x tracts): lots already used by ADUs, set
  by the package when an ADU reform is on too. No measured Tier-2 fit shares
  exist yet, so the funnel's ``fit`` is ``None`` (lot-size proxy or ledger).

- ``tod``: benchmark reform 3 (``scenarios.tod``), by tract. Option
  ``distance``: ``0.25``, ``0.5``, ``0.75`` (miles from any major stop) or
  ``"d07"`` (default: 1/2 mile rail, 1/4 mile frequent bus). The absorption cap
  is the county's baseline 5+ unit permits, by draw. Tract transit columns come
  from the state pack; where the pack has none (Testland), they are worked out
  from blocks and stops once per run context.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.engine.registry import Draws, ReformResult, RunContext, register
from visionhousing.ledger import Ledger, apply_overrides, scenario_ledger
from visionhousing.ledger.schema import Parameter
from visionhousing.scenarios.adu import adu_funnel, scope_from_scenario
from visionhousing.scenarios.fees import VIEWS, ChannelBResult, fee_reform
from visionhousing.scenarios.missing_middle import missing_middle_funnel
from visionhousing.scenarios.missing_middle import scope_from_scenario as middle_scope
from visionhousing.scenarios.parking import TRANSIT_IDS, parking_reform, transit_rich_tracts
from visionhousing.scenarios.tod import DEFAULT_DISTANCE, tod_funnel
from visionhousing.scenarios.tod import scope_from_scenario as tod_scope

__all__ = [
    "ADU_FEE_IDS",
    "ADU_PARKING_ID",
    "adu_package_changes",
    "package_changes",
    "reform_ledger",
    "scaled_entry",
]

ADU_FEE_IDS: tuple[str, ...] = ("adu.permit_fee", "adu.utility_connection_fee")
"""The ADU pro forma's fee lines. A fee cut in the same package scales both."""

ADU_PARKING_ID = "adu.added_parking_spaces"
"""The ADU pro forma's parking line. A parking reform in the same package scales it."""

MIDDLE_FEE_IDS: tuple[str, ...] = ("fees.fee_total_per_unit",)
"""The 2-6 unit pro forma's fee line (the $50,000 benchmark). A fee cut scales it."""

MIDDLE_PARKING_IDS: tuple[str, ...] = ("parking.required_spaces_per_unit_attached",)
"""The 2-6 unit pro forma's parking line. A parking reform scales it by the share still built."""

OPTION_ADU_LOTS = "adu_lots"

OPTION_FEE_CUT = "package_fee_cut_share"
OPTION_PARKING_KEPT = "package_parking_share_still_built"
OPTION_LEDGER = "ledger_overrides"
OPTION_REPLACE = "ledger_replace"


def scaled_entry(param: Parameter, factor: float) -> dict[str, Any]:
    """Return the override that multiplies a parameter's whole range by ``factor``.

    Scaling ``central``, ``low`` and ``high`` together keeps the shape of the
    range, so with the same seed every draw is scaled by exactly ``factor``.

    Args:
        param: The ledger entry.
        factor: Multiplier, at least 0.

    Returns:
        Fields to replace (``shape`` becomes ``fixed`` if the range collapses).
    """
    if factor < 0:
        raise ValueError("a scaling factor cannot be negative")
    entry: dict[str, Any] = {
        "central": param.central * factor,
        "low": param.low * factor,
        "high": param.high * factor,
    }
    if entry["low"] == entry["high"]:
        entry["shape"] = "fixed"
    return entry


def reform_ledger(
    context: RunContext, draws: Draws, scenario_id: str, options: Mapping[str, Any]
) -> Ledger:
    """Return the ledger one reform sees (base, scenario, user, package changes).

    Args:
        context: The run context.
        draws: The run's draws (holds the user's overrides).
        scenario_id: Scenario id in ``assumptions/scenarios.yaml``.
        options: Reform options; may hold extra ledger overrides.

    Returns:
        The ledger. When ``options`` holds ``ledger_replace`` (a whole ledger,
        used by the tornado with every input pinned), that ledger is returned
        as is.

    Raises:
        KeyError: The scenario is not in the scenario file.
    """
    if options.get(OPTION_REPLACE) is not None:
        return options[OPTION_REPLACE]
    scenarios = context.state.scenarios
    if scenario_id not in scenarios:
        raise KeyError(f"scenario '{scenario_id}' is not in scenarios.yaml")
    ledger = scenario_ledger(context.state.ledger, scenarios[scenario_id])
    ledger = apply_overrides(ledger, draws.overrides)
    return apply_overrides(ledger, options.get(OPTION_LEDGER))


PACKAGE_LINES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "adu": (ADU_FEE_IDS, (ADU_PARKING_ID,)),
    "missing_middle": (MIDDLE_FEE_IDS, MIDDLE_PARKING_IDS),
}
"""Reform family to (fee lines, parking lines) of its pro forma that a package scales."""


def package_changes(family: str, ledger: Ledger, options: Mapping[str, Any]) -> dict[str, Any]:
    """Return the ledger changes a package makes to a reform's fee and parking lines.

    Args:
        family: Reform family (``"adu"`` or ``"missing_middle"``).
        ledger: The ledger the reform uses.
        options: Adapter options (the package's fee cut and parking share).

    Returns:
        Parameter id to the fields to replace (empty when nothing applies).
    """
    fee_ids, parking_ids = PACKAGE_LINES.get(family, ((), ()))
    changes: dict[str, Any] = {}
    cut = options.get(OPTION_FEE_CUT)
    if cut is not None:
        for pid in fee_ids:
            changes[pid] = scaled_entry(ledger[pid], 1.0 - float(cut))
    kept = options.get(OPTION_PARKING_KEPT)
    if kept is not None:
        for pid in parking_ids:
            changes[pid] = scaled_entry(ledger[pid], float(kept))
    return changes


def adu_package_changes(ledger: Ledger, options: Mapping[str, Any]) -> dict[str, Any]:
    """Return the ledger changes a package makes to the ADU's fee and parking lines."""
    return package_changes("adu", ledger, options)


def _adu_adapter(scenario_id: str):
    def adapter(context: RunContext, draws: Draws, options: Mapping[str, Any]) -> ReformResult:
        state = context.state
        scenario = state.scenarios[scenario_id]
        ledger = reform_ledger(context, draws, scenario_id, options)
        ledger = apply_overrides(ledger, adu_package_changes(ledger, options))
        scope = scope_from_scenario(scenario_id, scenario, state.legal)
        res = adu_funnel(state.tracts, ledger, state.fit, scope, draws=draws.n, seed=draws.seed)
        head = res.records["net"]
        notes = [*scenario.notes]
        draft = sorted(
            i for i in head.assumption_ids if i in ledger and ledger[i].status == "draft"
        )
        if draft:
            notes.append("Draft inputs (team judgement, wide ranges): " + ", ".join(draft) + ".")
        return ReformResult(
            reform=scenario_id,
            title=scenario.title,
            level="tract",
            geoids=tuple(str(g) for g in res.geoids),
            county_of=tuple(str(c) for c in res.counties),
            added=np.ascontiguousarray(res.stages["net"].T),
            headline=head,
            steps=tuple(res.funnel()),
            records=dict(res.records),
            assumption_ids=head.assumption_ids,
            notes=tuple(notes),
            detail=res,
            ledger=ledger,
        )

    return adapter


def _channel_b_inputs(
    context: RunContext, draws: Draws
) -> tuple[pd.DataFrame, np.ndarray, tuple[str, ...]]:
    """Baseline rows and per-draw scale for the counties that have tracts."""
    with_tracts = set(context.state.tracts["county"].astype(str))
    counties = tuple(c for c in context.county_ids if c in with_tracts)
    rows = [context.county_ids.index(c) for c in counties]
    frame = context.baseline_types.reindex(list(counties)).fillna(0.0)
    return frame, draws.baseline_scale[rows], counties


def _channel_b_result(
    key: str, title: str, res: ChannelBResult, ledger: Ledger, notes: tuple[str, ...]
) -> ReformResult:
    county_ids = tuple(str(c) for c in res.county_ids)
    records = {"state": res.state, "crosscheck_share": res.crosscheck_share}
    if "state" in res.crosscheck:
        records["crosscheck_state"] = res.crosscheck["state"]
    records.update({f"type_{t}": r for t, r in res.by_type.items()})
    return ReformResult(
        reform=key,
        title=title,
        level="county",
        geoids=county_ids,
        county_of=county_ids,
        added=np.ascontiguousarray(res.added.sum(axis=1)),
        headline=res.state,
        steps=tuple(s.record for s in res.steps),
        records=records,
        assumption_ids=res.state.assumption_ids,
        notes=notes,
        detail=res,
        ledger=ledger,
    )


@register("fees", "Development fee reduction")
def fees_adapter(context: RunContext, draws: Draws, options: Mapping[str, Any]) -> ReformResult:
    """Benchmark reform 5 (fee cut) through ``scenarios.fees.fee_reform``.

    Args:
        context: The run context.
        draws: The run's draws; the baseline scale carries baseline uncertainty.
        options: ``view``: ``"benchmark"`` (default) or ``"state"``.

    Returns:
        Added units by county and draw.
    """
    view = str(options.get("view", "benchmark"))
    if view not in VIEWS:
        raise ValueError(f"fees view must be one of {VIEWS}, got {view!r}")
    ledger = reform_ledger(context, draws, "fees", options)
    baseline, scale, _ = _channel_b_inputs(context, draws)
    res = fee_reform(
        baseline,
        context.state.tracts,
        ledger,
        view=view,
        n_draws=draws.n,
        seed=draws.seed,
        baseline_scale=scale,
    )
    title = context.state.scenarios["fees"].title
    return _channel_b_result("fees", title, res, ledger, res.notes)


def _transit_flags(context: RunContext, ledger: Ledger) -> pd.Series:
    key = ("transit_rich", tuple(ledger[i].central for i in TRANSIT_IDS))
    if key not in context.cache:
        state = context.state
        context.cache[key] = transit_rich_tracts(
            state.blocks, state.stops, ledger, boundaries=state.boundaries
        )
    return context.cache[key]


@register("parking", "Parking requirement reduction")
def parking_adapter(context: RunContext, draws: Draws, options: Mapping[str, Any]) -> ReformResult:
    """Benchmark reform 4 (parking minimums removed) through ``scenarios.parking``.

    The transit-rich tract flags depend only on fixed ledger values (decision
    D-07), so they are worked out once per run context and reused.

    Args:
        context: The run context.
        draws: The run's draws; the baseline scale carries baseline uncertainty.
        options: No options yet.

    Returns:
        Added units by county and draw.
    """
    ledger = reform_ledger(context, draws, "parking", options)
    baseline, scale, _ = _channel_b_inputs(context, draws)
    state = context.state
    res = parking_reform(
        baseline,
        state.tracts,
        state.blocks,
        state.stops,
        ledger,
        n_draws=draws.n,
        seed=draws.seed,
        baseline_scale=scale,
        transit_rich=_transit_flags(context, ledger),
    )
    title = state.scenarios["parking"].title
    return _channel_b_result("parking", title, res, ledger, res.notes)


def _middle_adapter(scenario_id: str):
    def adapter(context: RunContext, draws: Draws, options: Mapping[str, Any]) -> ReformResult:
        state = context.state
        scenario = state.scenarios[scenario_id]
        ledger = reform_ledger(context, draws, scenario_id, options)
        ledger = apply_overrides(ledger, package_changes("missing_middle", ledger, options))
        scope = middle_scope(scenario_id, scenario)
        res = missing_middle_funnel(
            state.tracts,
            ledger,
            None,
            scope,
            draws=draws.n,
            seed=draws.seed,
            adu_lots=options.get(OPTION_ADU_LOTS),
        )
        head = res.records["net"]
        notes = [*scenario.notes, f"Lot-size fit from: {res.fit_source}."]
        draft = sorted(
            i for i in head.assumption_ids if i in ledger and ledger[i].status == "draft"
        )
        if draft:
            notes.append("Draft inputs (team judgement, wide ranges): " + ", ".join(draft) + ".")
        return ReformResult(
            reform=scenario_id,
            title=scenario.title,
            level="tract",
            geoids=tuple(str(g) for g in res.geoids),
            county_of=tuple(str(c) for c in res.counties),
            added=np.ascontiguousarray(res.stages["net"].T),
            headline=head,
            steps=tuple(res.funnel()),
            records=dict(res.records),
            assumption_ids=head.assumption_ids,
            notes=tuple(notes),
            detail=res,
            ledger=ledger,
        )

    return adapter


register("missing_middle", "Missing middle", family="missing_middle")(
    _middle_adapter("missing_middle")
)
register("adu", "Accessory dwelling units", family="adu")(_adu_adapter("adu"))
register("pa_hb2186", "Pennsylvania HB 2186 (ADUs)", family="adu", uses="pa_hb2186")(
    _adu_adapter("pa_hb2186")
)
register("pa_sb1346", "Pennsylvania SB 1346 (ADUs)", family="adu", uses="pa_sb1346")(
    _adu_adapter("pa_sb1346")
)


# ---------------------------------------------------------------- transit-oriented density

OPTION_DISTANCE = "distance"
"""TOD option: the distance choice (see ``scenarios.tod.DISTANCES``)."""

MF_COLUMN = "units_5plus"
"""Baseline structure-type column whose county totals cap TOD absorption."""


def _tod_tracts(context: RunContext, ledger: Ledger) -> pd.DataFrame:
    """The tract table with the transit columns, worked out from blocks if the pack lacks them."""
    from visionhousing.features.transit import TransitRule, tract_transit_features

    state = context.state
    tracts = state.tracts
    if "transit_land_m2_0_50mi" in tracts:
        return tracts
    rule = TransitRule.from_ledger(ledger)
    key = ("tod_tracts", tuple(ledger[i].central for i in TransitRule.LEDGER_IDS))
    if key not in context.cache:
        feats = tract_transit_features(
            state.blocks, state.stops, rule, tracts=tracts["geoid"].astype(str)
        )
        feats = feats.drop(columns=["transit_major_stops", "transit_rich"]).set_index("tract")
        geoids = tracts["geoid"].astype(str)
        merged = tracts.copy()
        for name in feats.columns:
            merged[name] = geoids.map(feats[name]).fillna(0.0).to_numpy()
        context.cache[key] = merged.reset_index(drop=True)
    return context.cache[key]


@register("tod", "Transit-oriented density", family="tod")
def tod_adapter(context: RunContext, draws: Draws, options: Mapping[str, Any]) -> ReformResult:
    """Benchmark reform 3 (transit-oriented density) through ``scenarios.tod.tod_funnel``.

    Args:
        context: The run context.
        draws: The run's draws; the baseline scale sets the absorption cap by draw.
        options: ``distance``: ``0.25``, ``0.5``, ``0.75`` or ``"d07"`` (default).

    Returns:
        Added units by tract and draw.
    """
    state = context.state
    scenario = state.scenarios["tod"]
    ledger = reform_ledger(context, draws, "tod", options)
    scope = tod_scope("tod", scenario, options.get(OPTION_DISTANCE, DEFAULT_DISTANCE))
    tracts = _tod_tracts(context, ledger)
    order = sorted(set(tracts["county"].astype(str)))
    rows = [context.county_ids.index(c) for c in order]
    mf = context.baseline_types.get(MF_COLUMN)
    res = tod_funnel(
        tracts,
        ledger,
        scope,
        mf_baseline=mf,
        mf_scale=draws.baseline_scale[rows],
        draws=draws.n,
        seed=draws.seed,
    )
    head = res.records["net"]
    notes = [
        *(n for n in scenario.notes if "added when this scenario is built" not in n),
        f"Distance: {scope.distance}. Net-new share (tod.net_new_share) is wide: homes "
        "moved within the region are not counted.",
        "Parking near stops is already reduced inside the TOD pro forma (D-08 transit-rich "
        "share); the parking reform counts baseline production only, so no unit is counted twice.",
    ]
    if mf is None:
        notes.append("No baseline 5+ unit permits: no absorption cap applied.")
    draft = sorted(i for i in head.assumption_ids if i in ledger and ledger[i].status == "draft")
    if draft:
        notes.append("Draft inputs (team judgement, wide ranges): " + ", ".join(draft) + ".")
    return ReformResult(
        reform="tod",
        title=scenario.title,
        level="tract",
        geoids=tuple(str(g) for g in res.geoids),
        county_of=tuple(str(c) for c in res.counties),
        added=np.ascontiguousarray(res.stages["net"].T),
        headline=head,
        steps=tuple(res.funnel()),
        records=dict(res.records),
        assumption_ids=head.assumption_ids,
        notes=tuple(notes),
        detail=res,
        ledger=ledger,
    )
