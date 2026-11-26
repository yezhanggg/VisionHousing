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
- ``parking``: benchmark reform 4 (``scenarios.parking``), by county. Where
  verified municipal rules cover enough of a county's residents, the county's
  estimate is scaled by the share of residents where a parking minimum above
  zero applies (``StateContext.local_law``).
- the ADU reforms use each such county's already-legal share from read local
  law (``a + b x`` the statewide draw) and cite the readings in the county's
  record.
- ``missing_middle``: benchmark reform 2 (``scenarios.missing_middle``), by
  tract. Option ``adu_lots`` (draws x tracts): lots already used by ADUs, set
  by the package when an ADU reform is on too. No measured Tier-2 fit shares
  exist yet, so the funnel's ``fit`` is ``None`` (lot-size proxy or ledger).
  Option ``rivals`` (project choice, ``engine.market``): the ADU and the
  conventional rebuild compete for each lot; the ADU adapters' option
  ``lot_detail`` keeps the per-lot shares that choice reads.

- ``tod``: benchmark reform 3 (``scenarios.tod``), by tract. Option
  ``distance``: ``0.25``, ``0.5``, ``0.75`` (miles from any major stop) or
  ``"d07"`` (default: 1/2 mile rail, 1/4 mile frequent bus). The absorption cap
  is the county's baseline 5+ unit permits, by draw. Tract transit columns come
  from the state pack; where the pack has none (Testland), they are worked out
  from blocks and stops once per run context.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.engine import records as rec
from visionhousing.engine.law_floor import middle_floor_for
from visionhousing.engine.place_scope import measured_scope
from visionhousing.engine.records import Record
from visionhousing.engine.registry import Draws, ReformResult, RunContext, register
from visionhousing.engine.variants import OPTION_VARIANT, expand_options, variant_overrides
from visionhousing.ledger import Ledger, apply_overrides, scenario_ledger
from visionhousing.ledger.schema import Parameter
from visionhousing.scenarios.adu import HEADLINE_STAGE as ADU_HEADLINE
from visionhousing.scenarios.adu import adu_funnel, scope_from_scenario
from visionhousing.scenarios.fees import VIEWS, ChannelBResult, fee_reform, window_factor
from visionhousing.scenarios.missing_middle import HEADLINE_STAGE as MIDDLE_HEADLINE
from visionhousing.scenarios.missing_middle import NET_STOCK_STAGE as MIDDLE_STOCK
from visionhousing.scenarios.missing_middle import missing_middle_funnel
from visionhousing.scenarios.missing_middle import scope_from_scenario as middle_scope
from visionhousing.scenarios.parking import (
    TRANSIT_IDS,
    MinimumMix,
    minimum_mix,
    parking_reform,
    transit_rich_tracts,
)
from visionhousing.scenarios.tod import (
    DEFAULT_DISTANCE,
    EXISTING_METHODS,
    EXISTING_SOURCES,
    NETWORK_DISTANCE,
    NETWORK_SOURCES,
    decomposition_verdict,
    distance_words,
    existing_capacity,
    network_columns,
    tod_funnel,
    zero_decomposition,
    zoned_land_m2,
)
from visionhousing.scenarios.tod import HEADLINE_STAGE as TOD_HEADLINE
from visionhousing.scenarios.tod import NET_STOCK_STAGE as TOD_STOCK
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

OPTION_LOT_REACH = "lot_reach"
"""ADU option: share of each tract's eligible lots the rule reaches (a number for
every tract, ``"owner_occupied"`` for the tract's owner-occupied share of
households, or ``None``/1 for all; used by the ADU policy dials)."""

OPTION_MIN_KEPT = "minimum_share_kept"
OPTION_MINIMUMS = "minimums"
"""Parking option: ``default`` (the benchmark: the ledger default minimum, verified
zeros through the county reach factor) or ``verified`` (variant
``parking_verified_minimums``: every verified, in-force local minimum)."""
OPTION_WINDOW = "baseline_window"
"""Fee and parking option: re-run under a named baseline-window sensitivity
(``assumptions/cost_reforms.yaml``, ``baseline_windows``)."""
OPTION_CAPACITY_CASE = "capacity_case"
"""Fee option: ``none`` (benchmark) or ``delayed`` (variant ``fees_delayed_capacity``)."""
"""Parking option: share of the old minimum that stays in force (0 = removed)."""

OPTION_LOT_DETAIL = "lot_detail"
"""ADU option (project choice, ``engine.market``): keep the per-lot shares; no stage changes."""
OPTION_RIVALS = "rivals"
"""Missing-middle option (project choice): a ``MiddleRivals``; the lot goes to the best use."""
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
    context: RunContext,
    draws: Draws,
    scenario_id: str,
    options: Mapping[str, Any],
    family: str | None = None,
) -> Ledger:
    """Return the ledger one reform sees (base, scenario, variant, user, package changes).

    Args:
        context: The run context.
        draws: The run's draws (holds the user's overrides).
        scenario_id: Scenario id in ``assumptions/scenarios.yaml``.
        options: Reform options; may hold extra ledger overrides and a named
            ``variant`` (``assumptions/variants.yaml``).
        family: Reform family, to check a named variant belongs to it
            (defaults to the scenario id).

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
    ledger = apply_overrides(ledger, variant_overrides(family or scenario_id, options, ledger))
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


def adu_lot_categories(
    law: Any, qualifying: Mapping[str, Any] | None = None
) -> dict[str, dict[str, float]]:
    """Lot-weighted ADU legal-status shares for the counties that use read local law.

    Feature-detected: a county is listed only when the law hooks give its
    category shares weighted by single-family lots (``weight == "sf_lots"``)
    and the county passes the hooks' coverage rule (it is in ``qualifying``,
    the counties with ADU coefficients). Otherwise the county keeps the
    resident-weighted ``a + b x s`` rule, unchanged.

    Args:
        law: ``StateContext.local_law`` (or ``None``).
        qualifying: Counties that use read local law (``LocalLaw.adu_coefficients``).

    Returns:
        County to ``{category: share of single-family lots}``.
    """
    cats = getattr(law, "categories", None)
    if law is None or cats is None or getattr(cats, "empty", True) or not qualifying:
        return {}
    if "weight" not in cats.columns:
        return {}
    out: dict[str, dict[str, float]] = {}
    for row in cats.itertuples(index=False):
        county = str(row.county)
        if county not in qualifying or getattr(row, "weight", "") != "sf_lots":
            continue
        shares = law.adu_categories(county)
        if not shares or any(v is None for v in shares.values()):
            continue
        out[county] = {k: float(v) for k, v in shares.items()}
    return out


def lot_reach(tracts: pd.DataFrame, reach: Any) -> np.ndarray | None:
    """Share of each tract's eligible ADU lots a rule reaches, or ``None`` (all of them).

    Args:
        tracts: The tract table.
        reach: ``None`` (all lots), a share for every tract, or
            ``"owner_occupied"``: the tract's owner-occupied households divided
            by all occupied households (ACS tenure), a proxy for the lots left
            open by an owner-occupancy rule; a tract with no households gets the
            state's share.

    Returns:
        One share per tract (between 0 and 1), or ``None``.
    """
    if reach is None:
        return None
    if isinstance(reach, str):
        if reach != "owner_occupied":
            raise ValueError(f"lot_reach must be a share or 'owner_occupied', got {reach!r}")
        own = pd.to_numeric(tracts["households_owner"], errors="coerce").to_numpy(dtype=float)
        rent = pd.to_numeric(tracts["households_renter"], errors="coerce").to_numpy(dtype=float)
        total = own + rent
        state = np.nansum(own) / np.nansum(total) if np.nansum(total) > 0 else 1.0
        with np.errstate(divide="ignore", invalid="ignore"):
            share = np.where(total > 0, own / np.where(total > 0, total, 1.0), state)
        return np.clip(np.nan_to_num(share, nan=state), 0.0, 1.0)
    value = float(reach)
    if not 0.0 <= value <= 1.0:
        raise ValueError("lot_reach must be a share between 0 and 1")
    return np.full(len(tracts), value)


def _variant_notes(family: str, options: Mapping[str, Any]) -> list[str]:
    """A plain note naming the variant a run used (empty for the benchmark preset)."""
    name = options.get(OPTION_VARIANT)
    if not name:
        return []
    from visionhousing.engine.variants import load_variants

    v = load_variants().get(str(name))
    if v is None:
        return []
    swaps = ", ".join(f"{t} takes the range of {s}" for t, s in v.overrides.items())
    opts = ", ".join(f"{k} = {val}" for k, val in v.options.items())
    parts = [p for p in (swaps, opts) if p]
    return [
        f"Variant '{v.name}' ({v.title}), not the benchmark preset: {v.question} "
        + ("Changes: " + "; ".join(parts) + "." if parts else "")
    ]


def _adu_adapter(scenario_id: str):
    def adapter(context: RunContext, draws: Draws, options: Mapping[str, Any]) -> ReformResult:
        state = context.state
        scenario = state.scenarios[scenario_id]
        options = expand_options("adu", options)
        ledger = reform_ledger(context, draws, scenario_id, options, family="adu")
        ledger = apply_overrides(ledger, adu_package_changes(ledger, options))
        scope = scope_from_scenario(scenario_id, scenario, state.legal)
        scope, place_notes = measured_scope(state, scope)
        law = state.local_law
        local = law.adu_coefficients() if law is not None else {}
        categories = adu_lot_categories(law, local) if options.get("categories", True) else {}
        res = adu_funnel(
            state.tracts,
            ledger,
            state.fit,
            scope,
            draws=draws.n,
            seed=draws.seed,
            local_legal=local or None,
            local_categories=categories or None,
            lot_reach=lot_reach(state.tracts, options.get(OPTION_LOT_REACH)),
            lot_detail=bool(options.get(OPTION_LOT_DETAIL, False)),
        )
        head = res.records[ADU_HEADLINE]
        notes = [*scenario.notes, *place_notes]
        notes += _variant_notes("adu", options)
        county_notes = {}
        if law is not None and local:
            for c in sorted(local):
                text = str(law.adu_note(c))
                if c in categories:
                    sh = categories[c]
                    text += (
                        "; refined by lot-weighted legal-status categories: already permitted "
                        f"{sh['already_permitted']:.0%}, by right with conditions "
                        f"{sh['by_right_conditions']:.0%}, discretionary "
                        f"{sh['discretionary']:.0%}, "
                        f"prohibited {sh['prohibited']:.0%}, unknown {sh['unknown']:.0%} of "
                        "single-family lots, so share = already permitted + (1 - "
                        "adu.conditions_exposure_share) x by right with conditions + unknown x "
                        "statewide share"
                    )
                county_notes[c] = (text, (law.source_id,))
            notes.append(
                "In counties where verified municipal rules cover enough residents ("
                + ", ".join(sorted(local))
                + "), the already-legal ADU share comes from read local law "
                "(assumptions/law/hooks.yaml); each county's number cites the readings."
            )
            if categories:
                notes.append(
                    "There, lots count by legal-status category, weighted by single-family "
                    "lots: only lots where the benchmark changes the status quo (discretionary, "
                    "prohibited, and a ledger share of 'by right with conditions') stay "
                    "exposed; lots with an unknown status keep the statewide range."
                )
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
            added=_by_tract(res.stages[ADU_HEADLINE]),
            headline=head,
            steps=tuple(res.funnel()),
            records=dict(res.records),
            assumption_ids=head.assumption_ids,
            notes=tuple(notes),
            detail=res,
            ledger=ledger,
            county_notes=county_notes,
            year_share=res.year_share,
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
    records.update(getattr(res, "records", None) or {})
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
        options: ``view``: ``"benchmark"`` (default), ``"state"`` or
            ``"deferral"``; ``variant``: a named fee variant.

    Returns:
        Added units by county and draw.
    """
    options = expand_options("fees", options)
    view = str(options.get("view", "benchmark"))
    if view not in VIEWS:
        raise ValueError(f"fees view must be one of {VIEWS}, got {view!r}")
    ledger = reform_ledger(context, draws, "fees", options, family="fees")
    baseline, scale, _ = _channel_b_inputs(context, draws)
    baseline, window = _baseline_window(context, baseline, options)
    res = fee_reform(
        baseline,
        context.state.tracts,
        ledger,
        view=view,
        n_draws=draws.n,
        seed=draws.seed,
        baseline_scale=scale,
        capacity_case=str(options.get(OPTION_CAPACITY_CASE, "none")),
    )
    title = context.state.scenarios["fees"].title
    notes = (*res.notes, *_variant_notes("fees", options), *window[1])
    out = _channel_b_result("fees", title, res, ledger, notes)
    return _with_window(out, window[0])


def _baseline_window(
    context: RunContext, baseline: pd.DataFrame, options: Mapping[str, Any]
) -> tuple[pd.DataFrame, tuple[dict[str, Record], tuple[str, ...]]]:
    """Scale the county baseline by a named baseline-window sensitivity (``baseline_window``).

    The fee and parking estimates are linear in the baseline, so scaling it by
    the window's factor (``scenarios.fees.window_factor``) re-runs them under
    that window. Shown beside the headline as a sensitivity; never subtracted.
    """
    name = options.get(OPTION_WINDOW)
    if not name:
        return baseline, ({}, ())
    factor, entry = window_factor(str(name), context.state.state)
    record = rec.quantity(
        f"baseline factor of the window sensitivity {name}",
        factor,
        "ratio",
        formula=(
            f"five-year baseline under the window {entry['five_year']:,.0f} / shipped state "
            f"baseline {entry['shipped_state_component']:,.0f} = {factor:.4f} "
            "(assumptions/calibration/baseline_reconciliation.yaml)"
        ),
        assumption_ids=tuple(entry.get("assumption_ids") or (str(name),)),
        source_ids=tuple(entry.get("sources") or ()),
    )
    note = (
        f"Baseline-window sensitivity '{name}' (not the benchmark preset): every county's "
        f"baseline is scaled by {factor:.4f} ({entry.get('label', name)}). Shown beside the "
        "headline as a sensitivity; it is never subtracted from any result.",
    )
    return baseline * factor, ({"baseline_window_factor": record}, note)


def _with_window(out: ReformResult, records: Mapping[str, Record]) -> ReformResult:
    return replace(out, records={**out.records, **records}) if records else out


def _transit_flags(context: RunContext, ledger: Ledger) -> pd.Series:
    """Transit-rich tract flags, worked out once per state and reused.

    The flags depend only on the state's blocks, stops and outlines and on the
    fixed major-stop values, so they are kept on the state context (keyed by
    those values) and every later run of the same state reads them back.
    """
    key = ("transit_rich", tuple(ledger[i].central for i in TRANSIT_IDS))
    store = context.state.derived
    if key not in store:
        state = context.state
        store[key] = transit_rich_tracts(
            state.blocks, state.stops, ledger, boundaries=state.boundaries
        )
    return store[key]


@register("parking", "Parking requirement reduction")
def parking_adapter(context: RunContext, draws: Draws, options: Mapping[str, Any]) -> ReformResult:
    """Benchmark reform 4 (parking minimums removed) through ``scenarios.parking``.

    The transit-rich tract flags depend only on fixed ledger values (the
    major-stop rule), so they are worked out once per run context and reused.

    Args:
        context: The run context.
        draws: The run's draws; the baseline scale carries baseline uncertainty.
        options: ``response``: ``"share"`` (default: the benchmark parking rule,
            about 60% of the old minimum still built in transit-rich tracts,
            85-100% elsewhere) or
            ``"provided"``; ``variant``: a named parking variant;
            ``minimum_share_kept``: share of the old minimum left in force
            (0, the benchmark; used by the policy dial ``parking_minimum``).

    Returns:
        Added units by county and draw.
    """
    options = expand_options("parking", options)
    ledger = reform_ledger(context, draws, "parking", options, family="parking")
    baseline, scale, _ = _channel_b_inputs(context, draws)
    baseline, window = _baseline_window(context, baseline, options)
    state = context.state
    flags = _transit_flags(context, ledger)
    which = str(options.get(OPTION_MINIMUMS, "default"))
    if which not in ("default", "verified"):
        raise ValueError(f"parking option minimums must be default or verified, got {which!r}")
    mix = (
        _verified_minimums(state, tuple(str(c) for c in baseline.index), flags)
        if which == "verified"
        else None
    )
    res = parking_reform(
        baseline,
        state.tracts,
        state.blocks,
        state.stops,
        ledger,
        n_draws=draws.n,
        seed=draws.seed,
        baseline_scale=scale,
        transit_rich=flags,
        reach=None if mix is not None else _parking_reach(state, baseline.index),
        response=str(options.get("response", "share")),
        min_share_kept=float(options.get(OPTION_MIN_KEPT, 0.0)),
        minimums=mix,
    )
    title = state.scenarios["parking"].title
    notes = (*res.notes, *_variant_notes("parking", options), *window[1])
    out = _with_window(_channel_b_result("parking", title, res, ledger, notes), window[0])
    law = state.local_law
    if law is None or law.parking.empty:
        return out
    county_notes = {
        c: (str(law.parking_note(c)), (law.source_id,))
        for c in sorted(law.parking_factors())
        if c in out.geoids
    }
    return replace(out, county_notes=county_notes)


_MIN_TABLES = {
    "muni_zoning_rules": {"geoid": str},
    "tract_municipalities": {"tract": str, "county": str, "cousub": str},
    "law_documents": {"doc_id": str, "source_id": str},
}


def _law_table(state: Any, name: str) -> pd.DataFrame | None:
    """A law table of the state's pack, read once; ``None`` when absent.

    A table placed in ``state.derived[("parking_law_table", name)]`` (tests)
    is used as is.
    """
    from visionhousing.scenarios.extra_common import _folder

    key = ("parking_law_table", name)
    if key in state.derived:
        return state.derived[key]
    table = None
    folder = _folder(state) if not getattr(state, "synthetic", False) else None
    if folder is not None:
        for cand in (folder / f"{name}.csv.gz", folder / f"{name}.csv"):
            if cand.is_file():
                table = pd.read_csv(cand, dtype=_MIN_TABLES[name])
                break
    state.derived[key] = table
    return table


def _verified_minimums(state: Any, counties: tuple[str, ...], flags: pd.Series) -> MinimumMix:
    """Verified, in-force local parking minimums mixed into each county (option ``minimums``).

    Reads ``muni_zoning_rules`` and ``tract_municipalities`` from the state's
    pack (read-only, through :mod:`visionhousing.law.parking_rules`); the
    fields and words come from ``verified_minimums`` in
    ``assumptions/cost_reforms.yaml``. With no tables every home keeps the
    ledger default.
    """
    from visionhousing.law.parking_rules import verified_parking_minimums
    from visionhousing.scenarios.fees import load_cost_settings

    cfg = load_cost_settings()["verified_minimums"]
    rules = _law_table(state, "muni_zoning_rules")
    table = verified_parking_minimums(
        rules,
        apartment_fields=tuple(cfg["apartment_fields"]),
        small_fields=tuple(cfg["small_building_fields"]),
    )
    docs = _law_table(state, "law_documents")
    sources: set[str] = set()
    if docs is not None and len(table):
        by_doc = dict(zip(docs["doc_id"], docs["source_id"], strict=False))
        sources = {str(by_doc[d]) for d in table["doc_id"] if d in by_doc}
    if not sources and len(table):
        law = state.local_law
        sources = {law.source_id if law is not None else "pa_municipal_ordinances"}
    return minimum_mix(
        state.tracts,
        _law_table(state, "tract_municipalities"),
        flags,
        table,
        counties,
        source_ids=tuple(sorted(sources)),
        assumption_id=str(cfg["id"]),
        weight_words=" ".join(str(cfg.get("weight", "")).split()),
    )


def _parking_reach(state: Any, counties: pd.Index) -> np.ndarray | None:
    """County parking reach from read local law (1 where nothing is read), or ``None``."""
    law = state.local_law
    if law is None or law.parking.empty:
        return None
    factors = law.parking_factors()
    return np.array([factors.get(str(c), 1.0) for c in counties], dtype=float)


def _middle_adapter(scenario_id: str):
    def adapter(context: RunContext, draws: Draws, options: Mapping[str, Any]) -> ReformResult:
        state = context.state
        scenario = state.scenarios[scenario_id]
        options = expand_options("missing_middle", options)
        ledger = reform_ledger(context, draws, scenario_id, options, family="missing_middle")
        ledger = apply_overrides(ledger, package_changes("missing_middle", ledger, options))
        scope = middle_scope(scenario_id, scenario)
        method = str(options.get("method", "mix"))
        floor, floor_notes = middle_floor_for(state, ledger, method)
        res = missing_middle_funnel(
            state.tracts,
            ledger,
            None,
            scope,
            draws=draws.n,
            seed=draws.seed,
            adu_lots=options.get(OPTION_ADU_LOTS),
            method=method,
            rivals=options.get(OPTION_RIVALS),
            floor=floor,
        )
        head = res.records[MIDDLE_HEADLINE]
        notes = [*scenario.notes, f"Lot-size fit from: {res.fit_source}.", *floor_notes]
        notes += _variant_notes("missing_middle", options)
        if "proto_duplex_alone" in res.records:
            notes.append(
                "Prototype comparison (records proto_*): a duplex, a fourplex and a six-unit "
                "building on the same lots and draws. Bigger buildings need bigger lots, the "
                "building code above two homes, accessible ground-floor homes from four, and "
                "commercial financing from five, so a duplex can pencil where a six-unit "
                "building does not."
            )
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
            added=_by_tract(res.stages[MIDDLE_HEADLINE]),
            headline=head,
            steps=tuple(res.funnel()),
            records=dict(res.records),
            assumption_ids=head.assumption_ids,
            notes=tuple(notes),
            detail=res,
            ledger=ledger,
            **_site_arrays(res, MIDDLE_STOCK),
        )

    return adapter


def _site_arrays(res: Any, stock_stage: str) -> dict[str, Any]:
    """Year shares and the gross/net arrays of a redevelopment funnel, as tracts x draws."""

    def t(arr: Any) -> np.ndarray | None:
        return None if arr is None else _by_tract(arr)

    return {
        "year_share": res.year_share,
        "net_stock": t(res.stages[stock_stage]),
        "removed": t(res.removed),
        "baseline_on_sites": t(res.baseline_on_sites),
    }


def _by_tract(arr: Any) -> np.ndarray:
    """A draws x tracts funnel array seen as tracts x draws, without copying it.

    The engine's results are tracts x draws; the funnels work draws x tracts.
    A transposed *view* shares the funnel's memory, so the run keeps one copy of
    each array instead of two. Every number is the same; only the memory layout
    differs.
    """
    return np.asarray(arr, dtype=float).T


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

OPTION_WALK = "walk"
"""TOD option: ``"straight"`` (default: the benchmark preset) or
``"network"`` (network walksheds and the peak frequent-bus rule, where the pack has them)."""

OPTION_DECOMPOSE = "decompose"
"""TOD option: also run the low-rise prototype and a 3/4-mile coverage on the same
draws and report why the estimate is what it is (``scenarios.tod.zero_decomposition``)."""

OPTION_EXISTING = "existing_capacity"
"""TOD option: how land a state already zones by right near transit is handled
(``scenarios.tod.EXISTING_METHODS``). ``capacity`` (default; used where the pack has
``mbta_3a_capacity_tract``): additional legal capacity = max(0, proposed - existing)
on the same land. ``acre_share`` (variant ``tod_ma_acre_share``): the older rule that
takes ``tod.state_zoned_overlap_share`` of district acres out of the developable land."""


def tod_by_right(
    state: Any, tracts: pd.DataFrame, options: Mapping[str, Any]
) -> tuple[str, Any, np.ndarray | None]:
    """Which by-right rule a TOD run uses, and its inputs.

    Returns:
        ``(method, existing_for, zoned_m2)``: ``existing_for(suffix)`` gives the
        :class:`~visionhousing.scenarios.tod.ExistingCapacity` at a distance
        suffix (``None`` when the capacity rule is not used); ``zoned_m2`` is the
        acre-share input (``None`` when not used). ``method`` is ``"capacity"``,
        ``"acre_share"`` or ``"none"`` (no by-right table in the pack).
    """
    from visionhousing.scenarios.extra_common import pack_table

    method = str(options.get(OPTION_EXISTING, EXISTING_METHODS[0]))
    if method not in EXISTING_METHODS:
        raise ValueError(f"TOD {OPTION_EXISTING} must be one of {EXISTING_METHODS}, got {method!r}")
    table = pack_table(state, "mbta_3a_capacity_tract") if method == "capacity" else None
    if table is not None:
        cache: dict[str, Any] = {}

        def existing_for(suffix: str) -> Any:
            if suffix not in cache:
                cache[suffix] = existing_capacity(tracts, table, suffix)
            return cache[suffix]

        return "capacity", existing_for, None
    zoned = zoned_land_m2(tracts, getattr(state, "zoned_districts", None))
    return ("acre_share" if zoned is not None else "none"), (lambda _s: None), zoned


MF_COLUMN = "units_5plus"
"""Baseline structure-type column whose county totals cap TOD absorption."""


def _tod_tracts(context: RunContext, ledger: Ledger) -> pd.DataFrame:
    """The tract table with the transit columns.

    Straight-line columns are worked out from blocks if the pack lacks them;
    network-walkshed columns are added when the pack has ``transit_walk_tract``
    (:func:`visionhousing.scenarios.tod.network_columns`).
    """
    straight = _tod_straight_tracts(context, ledger)
    walk = getattr(context.state, "transit_walk", None)
    if walk is None:
        return straight
    key = ("tod_tracts_walk", id(straight))
    if key not in context.cache:
        context.cache[key] = (straight, network_columns(straight, walk))
    return context.cache[key][1]


def _tod_straight_tracts(context: RunContext, ledger: Ledger) -> pd.DataFrame:
    """The tract table with straight-line transit columns, from blocks if the pack lacks them."""
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
        options: ``walk``: ``"straight"`` (default, the benchmark preset) or
            ``"network"`` (used only where the pack has the walkshed table;
            otherwise the run falls back to straight-line and says so);
            ``distance``: ``0.25``, ``0.5`` (network default) or ``0.75``, or
            ``"d07"`` (straight-line default);
            ``decompose``: also run the zero decomposition; ``variant``: a
            named TOD variant.

    Returns:
        Added units by tract and draw.
    """
    state = context.state
    scenario = state.scenarios["tod"]
    options = expand_options("tod", options)
    ledger = reform_ledger(context, draws, "tod", options, family="tod")
    has_walk = getattr(state, "transit_walk", None) is not None
    walk = str(options.get(OPTION_WALK, "straight"))
    if walk == "network" and not has_walk:
        walk = "straight"  # disclosed below: no walkshed table in this state's pack
    default = NETWORK_DISTANCE if walk == "network" else DEFAULT_DISTANCE
    scope = tod_scope("tod", scenario, options.get(OPTION_DISTANCE, default), walk)
    tracts = _tod_tracts(context, ledger)
    order = sorted(set(tracts["county"].astype(str)))
    rows = [context.county_ids.index(c) for c in order]
    mf = context.baseline_types.get(MF_COLUMN)
    by_right, existing_for, zoned = tod_by_right(state, tracts, options)

    def funnel(led: Ledger, sc: Any) -> Any:
        return tod_funnel(
            tracts,
            led,
            sc,
            mf_baseline=mf,
            mf_scale=draws.baseline_scale[rows],
            draws=draws.n,
            seed=draws.seed,
            zoned_m2=zoned,
            existing=existing_for(sc.suffix),
        )

    res = funnel(ledger, scope)
    head = res.records[TOD_HEADLINE]
    records = dict(res.records)
    notes = [
        *(n for n in scenario.notes if "added when this scenario is built" not in n),
        f"Distance: {distance_words(scope.distance, scope.walk)}. Net-new share "
        "(tod.net_new_share) is wide: homes moved within the region are not counted.",
        "Parking near stops is already reduced inside the TOD pro forma (the transit-rich "
        "still-built share); the parking reform counts baseline production only, so no unit "
        "is counted twice.",
    ]
    if mf is None:
        notes.append("No baseline 5+ unit permits: no absorption cap applied.")
    notes += _variant_notes("tod", options)
    county_notes = _walk_county_notes(state, tracts, scope)
    no_stops = _no_major_stops_note(state.stops)
    if no_stops:
        notes.append(no_stops)
        county_notes = {
            c: (f"{text}; {no_stops}", srcs) for c, (text, srcs) in county_notes.items()
        }
    if "already_zoned" in records:
        notes.append(
            "Land the state already zones for multi-family homes by right near transit "
            "(Section 3A districts inside station areas) is taken out of the land that could "
            "become a mid-rise site (record already_zoned; ledger tod.state_zoned_overlap_share)."
        )
        county_notes = _zoned_county_notes(county_notes, res, tracts)
    if by_right == "capacity" and res.existing is not None:
        r = records
        notes.append(
            "Land Massachusetts already zones for multi-family homes by right (Section 3A "
            "parcels in the distance, atlas F_L_M__ split across tracts by area) counts only "
            "its additional legal capacity: max(0, homes the FAR 2.5 five-storey prototype "
            "allows - homes the 3A district already allows) on the same developable land, in "
            "gross homes (records existing_3a_*, proposed_3a_capacity, additional_3a_capacity). "
            "3A's 15 homes per acre is a district-wide minimum gross density; the prototype "
            "is about 105 per acre on one lot: different measures, so the parcels' own modeled "
            "capacity is compared, not the 15-per-acre floor. Eligible land in tracts with no "
            "compliant 3A district in the atlas: "
            f"{1.0 - float(r['existing_3a_resolved_share'].value or 0.0):.0%}, existing 3A "
            "capacity 0 there (base-zoning rule); the pack cannot tie a tract to its town's "
            "3A status. Variant tod_ma_acre_share keeps the older acre-share rule."
        )
        county_notes = _existing_county_notes(county_notes, res, tracts)
    if str(options.get(OPTION_WALK, "straight")) == "network" and not has_walk:
        notes.append(
            "A network walkshed was asked for, but this state's pack has no walkshed table: "
            f"eligible land is the straight-line {distance_words(scope.distance)} from "
            "major stops in every county, at the distance asked for."
        )
    if options.get(OPTION_DECOMPOSE):
        records.update(_tod_decomposition(res, ledger, context, draws, options, scope, funnel))
        notes.append(
            "Why this TOD estimate is what it is (records decomp_*): "
            + decomposition_verdict(records, _major_stop_count(state.stops))
        )
    status = _tod_status(context, res, tracts, ledger)
    if status is not None:
        notes.append(_tod_status_note(status))
    draft = sorted(i for i in head.assumption_ids if i in ledger and ledger[i].status == "draft")
    if draft:
        notes.append("Draft inputs (team judgement, wide ranges): " + ", ".join(draft) + ".")
    return ReformResult(
        reform="tod",
        title=scenario.title,
        level="tract",
        geoids=tuple(str(g) for g in res.geoids),
        county_of=tuple(str(c) for c in res.counties),
        added=_by_tract(res.stages[TOD_HEADLINE]),
        headline=head,
        steps=tuple(res.funnel()),
        records=records,
        assumption_ids=head.assumption_ids,
        notes=tuple(notes),
        detail=res,
        ledger=ledger,
        county_notes=county_notes,
        status=status,
        **_site_arrays(res, TOD_STOCK),
    )


TOD_STATUS_COLUMNS = ("mode", "frequency", "major", "county", "ntd_id")
"""Stop-table columns the TOD status ladder needs (Testland's stop table has none of them)."""


def _tod_status(
    context: RunContext, res: Any, tracts: pd.DataFrame, ledger: Ledger
) -> dict[str, Any] | None:
    """The transit status ladder for this run (``features.transit_status``), JSON-ready.

    ``None`` when the stop table lacks the columns (Testland). Feasibility per
    county is the funnel's eligible and feasible acres at P50 (county sums per draw).
    """
    from visionhousing.data.sources.transit import load_feed_register
    from visionhousing.features import transit_status as ts

    stops = context.state.stops
    if stops is None or not set(TOD_STATUS_COLUMNS) <= set(stops.columns):
        return None
    walk = getattr(context.state, "transit_walk_county", None)
    peak = None
    if walk is not None and {"county", "frequent_bus_stops"} <= set(walk.columns):
        peak = {
            str(c).zfill(5): int(n)
            for c, n in zip(walk["county"], walk["frequent_bus_stops"].fillna(0), strict=True)
        }
    county = tracts["county"].astype(str).to_numpy()
    by = {}
    for stage in ("eligible", "feasible"):
        frame = pd.DataFrame(np.asarray(res.stages[stage], dtype=float).T, index=county)
        by[stage] = frame.groupby(level=0).sum().median(axis=1)
    feasible = {
        str(c): (float(by["eligible"].get(c, 0.0)), float(by["feasible"].get(c, 0.0)))
        for c in context.county_ids
    }
    table = ts.status_table(
        stops,
        context.state.state,
        list(context.county_ids),
        min_share=float(ledger[ts.MIN_SHARE_ID].central),
        register=load_feed_register(),
        peak_major=peak,
        feasible=feasible,
    )
    out = table.to_dict()
    out["ledger_ids"] = [ts.MIN_SHARE_ID]
    out["source_ids"] = ["ntm_stops", "gtfs_agency_feeds", "mdb_catalog", "transitland_atlas"]
    return out


def _tod_status_note(status: Mapping[str, Any]) -> str:
    """One sentence: the state's ladder, its completeness and whether the statewide range shows."""
    st = status["state"]
    ladder = "; ".join(f"{k.replace('_', ' ')} {st[k].replace('_', ' ')}" for k in status["stages"])
    text = f"Transit status (state): {ladder}. Footprint: {st['completeness']}."
    if not status["statewide"]["shown"]:
        text += (
            f" Fewer than {status['statewide']['min_measured_share']:.0%} of the bus stops "
            "have measured service (tod.status_min_measured_bus_share), so the unmeasured part "
            "has no defensible bound: "
            "the statewide range and percent change are not shown; the number is the measured "
            "contribution only. A feed that is only discovered adds no homes."
        )
    return text


def _major_stop_count(stops: pd.DataFrame | None) -> int | None:
    """Major stops in the pack's stop table (``major`` == 1), or ``None`` when not known."""
    if stops is None or "major" not in stops:
        return None
    return int((pd.to_numeric(stops["major"], errors="coerce") == 1).sum())


def _no_major_stops_note(stops: pd.DataFrame | None) -> str:
    """A note naming why a state has no major stop, or "" when it has some (or it is not known).

    The cause is read from the stop table: rail stations, and bus stops whose
    frequency is unknown because no schedule feed covers them.
    """
    if _major_stop_count(stops) != 0 or stops is None:
        return ""
    mode = stops["mode"].astype(str).str.lower() if "mode" in stops else pd.Series(dtype=str)
    freq = (
        stops["frequency"].astype(str).str.lower() if "frequency" in stops else pd.Series(dtype=str)
    )
    rail = int(mode.isin(["rail", "subway", "light_rail", "commuter_rail", "tram"]).sum())
    unknown = int((freq == "unknown").sum())
    cause = (
        f"{unknown:,} of its {len(stops):,} stops have an unknown frequency because no schedule "
        "feed covers them, so none can be counted as frequent"
        if unknown
        else f"none of its {len(stops):,} stops meets the frequent-service rule"
    )
    return (
        f"No major transit stop in this state's stop table ({rail:,} rail stations; {cause}). "
        "Transit-oriented density therefore has no eligible land: a gap in the transit data "
        "or service, not a measured zero of land near stops."
    )


def _zoned_county_notes(
    county_notes: Mapping[str, tuple[str, tuple[str, ...]]], res: Any, tracts: pd.DataFrame
) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Add each county's acres already zoned for multi-family by right (P50) to its note."""
    out = dict(county_notes)
    if res.zoned is None:
        return out
    by = pd.DataFrame(res.zoned.T, index=tracts["county"].astype(str).to_numpy())
    acres = by.groupby(level=0).sum().median(axis=1)
    for c, a in acres.items():
        if a <= 0:
            continue
        text, srcs = out.get(c, ("", ()))
        line = (
            f"{a:,.0f} acres of Section 3A districts inside station areas (P50) are already "
            "zoned for multi-family by right and are taken out of the land that could become "
            "a mid-rise site"
        )
        out[c] = (
            f"{text}; {line}" if text else line,
            tuple(sorted({*srcs, "massgis_mbta3a_atlas"})),
        )
    return out


def _existing_county_notes(
    county_notes: Mapping[str, tuple[str, tuple[str, ...]]], res: Any, tracts: pd.DataFrame
) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Add each county's existing 3A capacity in the distance and its additional capacity (P50)."""
    out = dict(county_notes)
    ex, add = res.existing, res.capacity_by_right
    if ex is None or add is None:
        return out
    county = tracts["county"].astype(str).to_numpy()
    units = pd.Series(ex.units, index=county).groupby(level=0).sum()
    extra = pd.DataFrame(np.asarray(add, dtype=float).T, index=county).groupby(level=0).sum()
    extra = extra.median(axis=1)
    for c, u in units.items():
        if u <= 0:
            continue
        text, srcs = out.get(c, ("", ()))
        line = (
            f"{u:,.0f} homes of existing Section 3A modeled capacity lie in the distance; on "
            f"the feasible part the prototype adds {float(extra.get(c, 0.0)):,.0f} homes of "
            "capacity above them (P50, before take-up and absorption)"
        )
        out[c] = (f"{text}; {line}" if text else line, tuple(sorted({*srcs, *EXISTING_SOURCES})))
    return out


def _walk_county_notes(
    state: Any, tracts: pd.DataFrame, scope: Any
) -> dict[str, tuple[str, tuple[str, ...]]]:
    """Per county: how its eligible land near stops was measured (network, fallback share)."""
    counties = sorted(set(tracts["county"].astype(str)))
    if scope.walk != "network":
        text = (
            "TOD eligible land: straight-line distance from major stops "
            f"({distance_words(scope.distance, scope.walk)})"
        )
        if getattr(state, "transit_walk", None) is None:
            text += "; no network walkshed table in this state's pack"
        return {c: (text, ("ntm_stops", "tiger_2020_tabblock")) for c in counties}
    land = tracts[f"transit_land_m2_{scope.suffix}"].to_numpy(dtype=float)
    fall_col = f"transit_fallback_m2_{scope.suffix}"
    fall = tracts[fall_col].to_numpy(dtype=float) if fall_col in tracts else np.zeros(len(tracts))
    by = pd.DataFrame({"c": tracts["county"].astype(str), "land": land, "fall": fall})
    sums = by.groupby("c")[["land", "fall"]].sum()
    out = {}
    for c in counties:
        tot = float(sums.loc[c, "land"]) if c in sums.index else 0.0
        fb = float(sums.loc[c, "fall"]) if c in sums.index else 0.0
        share = fb / tot if tot > 0 else 0.0
        text = (
            f"TOD eligible land: {distance_words(scope.distance, scope.walk)} on the street "
            f"network; {share:.0%} of it ({fb / 4046.8564224:,.0f} of {tot / 4046.8564224:,.0f} "
            "acres) is straight-line fallback where the mapped network was missing or fragmentary"
        )
        out[c] = (text, NETWORK_SOURCES)
    return out


def _tod_decomposition(
    res: Any,
    ledger: Ledger,
    context: RunContext,
    draws: Draws,
    options: Mapping[str, Any],
    scope: Any,
    funnel: Any,
) -> dict[str, Any]:
    """Run the low-rise prototype and the wider coverage on the same draws, and compare.

    The low-rise ledger is built in the documented order (base, scenario, the
    low-rise variant's swaps, then the user's overrides and any option ledger
    changes), so a user's change to one of the variant's target entries still
    wins. The variant and the wider distance come from ``assumptions/variants.yaml``
    (``decomposition.tod``).
    """
    from visionhousing.engine.variants import tod_decomposition

    settings = tod_decomposition()
    low_opts = {**dict(options), OPTION_VARIANT: settings["lowrise_variant"]}
    low = reform_ledger(context, draws, "tod", low_opts, family="tod")
    wide = replace(scope, distance=settings["wider_distance"])
    results = {
        "benchmark": res,
        "lowrise": funnel(low, scope),
        "wider": funnel(ledger, wide),
        "lowrise_wider": funnel(low, wide),
    }
    return zero_decomposition(results)


# The extra scenarios register themselves on import (they reuse the helpers above).
from visionhousing.engine import extra_adapters as _extra_adapters  # noqa: E402,F401
