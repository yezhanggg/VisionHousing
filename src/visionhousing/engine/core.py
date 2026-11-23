"""The engine's one entry point: ``run(state, reforms, overrides, seed) -> Result``.

Plain-language summary
----------------------
Everything that shows a number (the command line, the interface, the report
writer, the documents and the tests) calls :func:`run`. One call:

1. **Loads the state context** (Testland for ``state="TL"``, else the built
   pack in ``data/packs/<STATE>/``) and applies the user's ledger overrides.
2. **Projects the baseline** (permits 2027-2031 with no reform) by county and
   for the state, with its likely range.
3. **Samples the ledger once.** Every parameter has its own seeded random
   stream, so all reforms in the run share the same draws of any parameter
   they have in common (see ``engine.registry``). The baseline's position in
   its range is drawn too, the same for every county (``engine.montecarlo``).
4. **Runs each reform alone** through its registered adapter.
5. **Builds the package** of all the reforms requested: sum of parts, minus
   lots counted twice, plus interactions (``engine.packages``).
6. **Rolls up** tract -> county -> state in every draw, so counties always add
   up to the state draw by draw, and summarises each as P10 / P50 / P90.
7. **Returns a Result** with records (formula, inputs, assumption ids, sources,
   tier) and the three hashes (code, data, assumptions).

Results are cached in memory by (state, reforms, overrides, seed, draws,
cycle, hashes). The same inputs give the same output, bit for bit.

:func:`compute` is the pure core: a loaded context in, a result out, no files.
"""

from __future__ import annotations

import json
import math
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from visionhousing.baseline import BaselineResult, baseline_from_ledger
from visionhousing.engine import adapters as _adapters  # importing it registers the reforms
from visionhousing.engine import records as rec
from visionhousing.engine.context import TESTLAND, StateContext, load_context
from visionhousing.engine.hashes import Hashes, assumption_hash, code_hash
from visionhousing.engine.montecarlo import baseline_scale
from visionhousing.engine.packages import interaction_options, lot_overlap, overlap_pair
from visionhousing.engine.records import Record
from visionhousing.engine.registry import (
    REGISTRY,
    Draws,
    ReformResult,
    RunContext,
    get_reform,
)
from visionhousing.ledger import DEFAULT_DRAWS, Ledger, apply_overrides, sample
from visionhousing.scenarios.fees import baseline_by_type

__all__ = [
    "PackageOutcome",
    "ReformOutcome",
    "Result",
    "cache_info",
    "clear_cache",
    "compute",
    "normalise_reforms",
    "prepare",
    "reform_total",
    "run",
]

SPAN = "2027-2031"
PCT = "% of baseline"
CACHE_SIZE = 16

ReformsArg = str | Sequence[str] | Mapping[str, Mapping[str, Any] | None] | None
"""Reforms to run: a key, a list of keys, or key -> options (``None`` = none)."""


# ---------------------------------------------------------------- result types


def _frozen(arr: np.ndarray) -> np.ndarray:
    out = np.array(arr, dtype=float)
    out.setflags(write=False)
    return out


@dataclass(frozen=True)
class ReformOutcome:
    """One reform run alone, against the baseline.

    Attributes:
        key: Registry key.
        title: Plain name.
        options: Options it ran with.
        added: State added units 2027-2031 (P10 / P50 / P90).
        pct_change: Added units as a percentage of the baseline, in each draw.
        counties: County id to its added-units record.
        county_draws: Added units by county and draw, shape ``(counties, draws)``.
        state_draws: Added units by draw (the sum of ``county_draws``).
        steps: The reform's explanation steps (the funnel).
        records: The reform module's other records.
        assumption_ids: Ledger ids behind ``added``.
        notes: Plain statements a reader needs.
        result: The adapter's full result.
    """

    key: str
    title: str
    options: Mapping[str, Any]
    added: Record
    pct_change: Record
    counties: Mapping[str, Record]
    county_draws: np.ndarray = field(repr=False)
    state_draws: np.ndarray = field(repr=False)
    steps: tuple[Record, ...] = ()
    records: Mapping[str, Record] = field(default_factory=dict)
    assumption_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    result: ReformResult | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class PackageOutcome:
    """All requested reforms together: the waterfall and the combined effect.

    Attributes:
        reforms: Reform keys in the package.
        sum_of_parts: Sum of the reforms run alone.
        overlap: Units removed because two reforms use the same lots.
        interaction: Units added because one reform's saving feeds another's pro forma.
        interactions: Interaction name (``"fees -> adu"``) to its record.
        combined: ``sum_of_parts - overlap + interaction``.
        pct_change: Combined added units as a percentage of the baseline.
        counties: County id to its combined record.
        county_draws: Combined added units by county and draw.
        state_draws: Combined added units by draw.
        parts_draws: Sum of parts by draw.
        overlap_draws: Overlap by draw.
        interaction_draws: Total interaction by draw.
        waterfall: The four steps in order, for the Compare screen.
        notes: Plain statements on the package rules.
    """

    reforms: tuple[str, ...]
    sum_of_parts: Record
    overlap: Record
    interaction: Record
    interactions: Mapping[str, Record]
    combined: Record
    pct_change: Record
    counties: Mapping[str, Record]
    county_draws: np.ndarray = field(repr=False)
    state_draws: np.ndarray = field(repr=False)
    parts_draws: np.ndarray = field(repr=False)
    overlap_draws: np.ndarray = field(repr=False)
    interaction_draws: np.ndarray = field(repr=False)
    waterfall: tuple[Record, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Result:
    """Everything one run produces.

    Attributes:
        state: State code.
        reform_ids: Reform keys, in the order requested.
        overrides: The user's ledger overrides.
        seed: Seed.
        draws: Number of Monte Carlo draws.
        cycle: Housing-cycle choice for the baseline.
        hashes: Code, data and assumption fingerprints.
        county_ids: County ids, in the order of every county array.
        baseline: The baseline's state record (exactly as the baseline module gives it).
        baseline_counties: County id to its baseline record.
        baseline_draws: State baseline units by draw.
        baseline_county_draws: Baseline units by county and draw.
        reforms: Reform key to its outcome (each run alone).
        package: The package of all requested reforms.
        total: Baseline plus the package, in each draw. With no reform (or a
            package that adds nothing in any draw) this is the baseline record.
        synthetic: True when the data are Testland's.
        notes: Plain statements on the run.
        run_context: The context the run used (for sensitivity; not exported).
    """

    state: str
    reform_ids: tuple[str, ...]
    options: Mapping[str, Mapping[str, Any]]
    overrides: Mapping[str, Any]
    seed: int
    draws: int
    cycle: str
    hashes: Hashes
    county_ids: tuple[str, ...]
    baseline: Record
    baseline_counties: Mapping[str, Record]
    baseline_draws: np.ndarray = field(repr=False)
    baseline_county_draws: np.ndarray = field(repr=False)
    reforms: Mapping[str, ReformOutcome] = field(default_factory=dict)
    package: PackageOutcome | None = None
    total: Record | None = None
    synthetic: bool = False
    notes: tuple[str, ...] = ()
    run_context: RunContext | None = field(default=None, repr=False, compare=False)
    draws_obj: Draws | None = field(default=None, repr=False, compare=False)

    def records(self) -> list[Record]:
        """Return every record in the result, baseline first."""
        out = [self.baseline, *self.baseline_counties.values()]
        for o in self.reforms.values():
            out += [o.added, o.pct_change, *o.counties.values(), *o.steps, *o.records.values()]
        if self.package is not None:
            p = self.package
            out += [*p.waterfall, *p.interactions.values(), p.pct_change, *p.counties.values()]
        if self.total is not None:
            out.append(self.total)
        return out

    def to_dict(self) -> dict[str, Any]:
        """Return the result as plain JSON-ready data (records, no draw arrays)."""

        def r(x: Record) -> dict[str, Any]:
            return x.model_dump(mode="json")

        def outcome(o: ReformOutcome) -> dict[str, Any]:
            return {
                "key": o.key,
                "title": o.title,
                "options": dict(o.options),
                "added": r(o.added),
                "pct_change": r(o.pct_change),
                "counties": {c: r(x) for c, x in o.counties.items()},
                "steps": [r(x) for x in o.steps],
                "records": {k: r(x) for k, x in o.records.items()},
                "assumption_ids": list(o.assumption_ids),
                "notes": list(o.notes),
            }

        package = None
        if self.package is not None:
            p = self.package
            package = {
                "reforms": list(p.reforms),
                "sum_of_parts": r(p.sum_of_parts),
                "overlap": r(p.overlap),
                "interaction": r(p.interaction),
                "interactions": {k: r(x) for k, x in p.interactions.items()},
                "combined": r(p.combined),
                "pct_change": r(p.pct_change),
                "counties": {c: r(x) for c, x in p.counties.items()},
                "waterfall": [r(x) for x in p.waterfall],
                "notes": list(p.notes),
            }
        return {
            "state": self.state,
            "reforms": list(self.reform_ids),
            "options": {k: dict(v) for k, v in self.options.items()},
            "overrides": _jsonable(self.overrides),
            "seed": self.seed,
            "draws": self.draws,
            "cycle": self.cycle,
            "hashes": {
                "code": self.hashes.code,
                "data": self.hashes.data,
                "assumptions": self.hashes.assumptions,
            },
            "synthetic": self.synthetic,
            "county_ids": list(self.county_ids),
            "baseline": r(self.baseline),
            "baseline_counties": {c: r(x) for c, x in self.baseline_counties.items()},
            "reform_results": {k: outcome(o) for k, o in self.reforms.items()},
            "package": package,
            "total": None if self.total is None else r(self.total),
            "notes": list(self.notes),
        }


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


# ---------------------------------------------------------------- inputs


def normalise_reforms(reforms: ReformsArg) -> dict[str, dict[str, Any]]:
    """Turn the ``reforms`` argument into ``{key: options}``, checked.

    Args:
        reforms: ``None`` or ``[]`` (baseline only), one key, a list of keys,
            or key -> options.

    Returns:
        Reform key to its options, in the order given, each key once.

    Raises:
        KeyError: An unknown reform.
        ValueError: Two reforms of the same family (for example ``adu`` and
            ``pa_hb2186``), or options that are not a mapping.
    """
    if reforms is None:
        items: Iterable[tuple[str, Any]] = ()
    elif isinstance(reforms, str):
        items = ((reforms, None),)
    elif isinstance(reforms, Mapping):
        items = reforms.items()
    else:
        items = ((k, None) for k in reforms)
    out: dict[str, dict[str, Any]] = {}
    families: dict[str, str] = {}
    for key, options in items:
        spec = get_reform(str(key))
        if options is not None and not isinstance(options, Mapping):
            raise ValueError(f"options for reform '{key}' must be a mapping")
        if spec.family in families and families[spec.family] != spec.key:
            raise ValueError(
                f"reforms '{families[spec.family]}' and '{spec.key}' reach the same lots; "
                "choose one of them"
            )
        families[spec.family] = spec.key
        out.setdefault(spec.key, dict(options or {}))
    return out


def prepare(
    context: StateContext,
    overrides: Mapping[str, Any] | None = None,
    *,
    seed: int = 0,
    draws: int = DEFAULT_DRAWS,
    cycle: str = "typical",
) -> tuple[RunContext, Draws]:
    """Apply overrides, project the baseline and draw the shared samples (pure).

    Args:
        context: The loaded state context.
        overrides: The user's ledger overrides.
        seed: Seed.
        draws: Number of draws (at least 1).
        cycle: Housing-cycle choice for the baseline.

    Returns:
        ``(run context, draws)``.
    """
    if draws < 1:
        raise ValueError("draws must be at least 1")
    if seed < 0:
        raise ValueError("seed must be a non-negative integer")
    overrides = dict(overrides or {})
    ledger = apply_overrides(context.ledger, overrides)
    baseline, _ = baseline_from_ledger(context.permits, ledger, cycle)
    types = baseline_by_type(baseline)
    county_ids = tuple(
        sorted({*map(str, baseline.counties), *context.tracts["county"].astype(str)})
    )
    run_context = RunContext(
        state=context, baseline=baseline, baseline_types=types, county_ids=county_ids
    )
    shared = Draws(
        ledger=ledger,
        n=draws,
        seed=seed,
        values=sample(ledger, n=draws, seed=seed),
        baseline_scale=baseline_scale(baseline.state, len(county_ids), draws, seed),
        overrides=overrides,
    )
    return run_context, shared


def _baseline_county_draws(baseline: BaselineResult, county_ids: Sequence[str], scale):
    p50 = np.array(
        [
            float(baseline.counties[c].p50) if c in baseline.counties else 0.0  # type: ignore[arg-type]
            for c in county_ids
        ]
    )
    return p50[:, None] * scale


# ---------------------------------------------------------------- records


def _county_records(
    draws: np.ndarray,
    county_ids: Sequence[str],
    *,
    what: str,
    expression: str,
    assumption_ids: Iterable[str],
    source_ids: Iterable[str],
    tier: int | None,
) -> dict[str, Record]:
    return {
        c: rec.from_draws(
            f"{what} {SPAN}, county {c}",
            draws[i],
            "units",
            expression=expression,
            assumption_ids=assumption_ids,
            source_ids=source_ids,
            tier=tier,
        )
        for i, c in enumerate(county_ids)
    }


def _pct(added: np.ndarray, base: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(base > 0, 100.0 * added / np.where(base > 0, base, 1.0), 0.0)


def _pct_record(what: str, added: np.ndarray, base: np.ndarray, ids, sources, tier) -> Record:
    return rec.from_draws(
        f"{what} {SPAN}, % change vs baseline, state",
        _pct(added, base),
        PCT,
        expression=(
            "100 x added units / baseline units in the same draw (the baseline's draw is its "
            "county P50s x the draw's position in the baseline's likely range)"
        ),
        assumption_ids=ids,
        source_ids=sorted({*sources, "bps_county_annual"}),
        tier=tier,
    )


def _outcome(
    key: str,
    options: Mapping[str, Any],
    res: ReformResult,
    county_ids: tuple[str, ...],
    base_state: np.ndarray,
) -> ReformOutcome:
    county = res.county_draws(county_ids)
    state = county.sum(axis=0)
    head = res.headline
    what = f"added units, {res.title}"
    roll = (
        "added units per tract, summed to the county in each draw"
        if res.level == "tract"
        else "added units per county in each draw"
    )
    common = {
        "assumption_ids": head.assumption_ids,
        "source_ids": head.source_ids,
        "tier": head.tier,
    }
    added = rec.from_draws(
        f"{what} {SPAN}, state",
        state,
        "units",
        expression=f"the sum over counties of {roll}; per draw: {_strip(head.formula)}",
        **common,
    )
    return ReformOutcome(
        key=key,
        title=res.title,
        options=dict(options),
        added=added,
        pct_change=_pct_record(what, state, base_state, *common.values()),
        counties=_county_records(county, county_ids, what=what, expression=roll, **common),
        county_draws=_frozen(county),
        state_draws=_frozen(state),
        steps=res.steps,
        records=dict(res.records),
        assumption_ids=head.assumption_ids,
        notes=res.notes,
        result=res,
    )


def _strip(formula: str) -> str:
    """The per-draw expression inside a ``from_draws`` formula."""
    _, sep, tail = formula.partition(" seeded draws of ")
    text = tail if sep else formula
    return text.rsplit(" = ", 1)[0]


def _ids(records: Iterable[Record]) -> tuple[tuple[str, ...], tuple[str, ...], int | None]:
    records = list(records)
    ids = tuple(sorted({a for r in records for a in r.assumption_ids}))
    sources = tuple(sorted({s for r in records for s in r.source_ids}))
    tiers = [r.tier for r in records if r.tier is not None]
    return ids, sources, (min(tiers) if tiers else None)


def _package(
    keys: tuple[str, ...],
    outcomes: Mapping[str, ReformOutcome],
    extras: Mapping[str, tuple[np.ndarray, tuple[str, ...]]],
    overlap: tuple[np.ndarray, tuple[str, ...]] | None,
    county_ids: tuple[str, ...],
    base_state: np.ndarray,
    n: int,
) -> PackageOutcome:
    parts = {k: np.asarray(outcomes[k].county_draws) for k in keys}
    zero = np.zeros((len(county_ids), n))
    parts_c = sum(parts.values(), zero.copy())
    overlap_c = zero.copy() if overlap is None else overlap[0]
    overlap_ids = () if overlap is None else overlap[1]
    inter_c = sum((d for d, _ in extras.values()), zero.copy())
    combined_c = parts_c - overlap_c + inter_c
    ids, sources, tier = _ids([outcomes[k].added for k in keys])
    names = " + ".join(outcomes[k].title for k in keys) or "no reform"
    common = {"assumption_ids": ids, "source_ids": sources, "tier": tier}

    def state_rec(name: str, arr: np.ndarray, expression: str, **kw: Any) -> Record:
        args = {**common, **kw}
        return rec.from_draws(f"{name} {SPAN}, state", arr, "units", expression=expression, **args)

    interactions = {}
    for name, (d, inter_ids) in extras.items():
        interactions[name] = state_rec(
            f"interaction {name}",
            d.sum(axis=0),
            f"the reform run with the other reform's saving in its pro forma ({name}) "
            "minus the reform run alone, same seed, summed over counties",
            assumption_ids=sorted({*ids, *inter_ids}),
        )
    inter_ids = sorted({*ids, *(i for _, ii in extras.values() for i in ii)})
    s_parts = state_rec(
        "sum of individual effects",
        parts_c.sum(axis=0),
        f"the sum of each reform run alone ({names}), in each draw",
    )
    s_overlap = state_rec(
        "overlap removed (lots counted twice)",
        overlap_c.sum(axis=0),
        "missing-middle units lost when the lots the ADU reform builds on are taken out of "
        "the missing-middle pool (a ledger share of them), same seed; zero unless both are on",
        assumption_ids=sorted({*ids, *overlap_ids}),
    )
    s_inter = state_rec(
        "interaction added",
        inter_c.sum(axis=0),
        "the sum of the interactions: savings from one reform entering another's pro forma",
        assumption_ids=inter_ids,
    )
    combined_ids = sorted({*ids, *inter_ids, *overlap_ids})
    s_combined = state_rec(
        f"combined added units, {names}",
        combined_c.sum(axis=0),
        "sum of individual effects - overlap removed + interaction added, in each draw",
        assumption_ids=combined_ids,
    )
    notes = (
        "A lot is used once: when ADU and missing middle are both on, the lots the ADU reform "
        "builds on leave the missing-middle pool (missing_middle.adu_lot_overlap_share).",
        "When the fee cut is on, the ADU and 2-6 unit pro formas' fee lines are cut by the "
        "same share; when parking reform is on, their parking lines are scaled by the share "
        "of spaces still built outside transit-rich tracts.",
        "Fee and parking savings on baseline production are added; their cross term is "
        "not modelled.",
    )
    state = combined_c.sum(axis=0)
    return PackageOutcome(
        reforms=keys,
        sum_of_parts=s_parts,
        overlap=s_overlap,
        interaction=s_inter,
        interactions=interactions,
        combined=s_combined,
        pct_change=_pct_record(
            f"combined added units, {names}", state, base_state, combined_ids, sources, tier
        ),
        counties=_county_records(
            combined_c,
            county_ids,
            what=f"combined added units, {names}",
            expression="sum of parts - overlap + interaction, in each draw",
            assumption_ids=combined_ids,
            source_ids=sources,
            tier=tier,
        ),
        county_draws=_frozen(combined_c),
        state_draws=_frozen(state),
        parts_draws=_frozen(parts_c.sum(axis=0)),
        overlap_draws=_frozen(overlap_c.sum(axis=0)),
        interaction_draws=_frozen(inter_c.sum(axis=0)),
        waterfall=(s_parts, s_overlap, s_inter, s_combined),
        notes=notes,
    )


# ---------------------------------------------------------------- the core


def _interactions(
    keys: tuple[str, ...],
    reforms: Mapping[str, Mapping[str, Any]],
    outcomes: Mapping[str, ReformOutcome],
    run_context: RunContext,
    draws: Draws,
    start: Mapping[str, tuple[dict[str, Any], np.ndarray, ReformResult]],
) -> dict[str, tuple[np.ndarray, tuple[str, ...]]]:
    out: dict[str, tuple[np.ndarray, tuple[str, ...]]] = {}
    for key in keys:
        steps = interaction_options(key, keys, draws.ledger)
        if not steps:
            continue
        spec = get_reform(key)
        if key in start:
            first, before, alone = start[key]
            options = dict(first)
        else:
            options = dict(reforms[key])
            before = np.asarray(outcomes[key].county_draws)
            alone = outcomes[key].result
        for name, change in steps.items():
            if (
                alone is not None
                and alone.ledger is not None
                and _changes_nothing(spec.family, alone.ledger, change)
            ):
                # Scaling a zero line (the benchmark ADU adds no parking) changes no
                # input, so the rerun would give the same draws: the interaction is 0.
                out[name] = (np.zeros_like(before), _interaction_ids(alone, name))
                continue
            options.update(change)
            res = spec.adapter(run_context, draws, options)
            after = res.county_draws(run_context.county_ids)
            out[name] = (after - before, _interaction_ids(res, name))
            before = after
    return out


def _changes_nothing(family: str, ledger: Ledger, change: Mapping[str, Any]) -> bool:
    edits = _adapters.package_changes(family, ledger, change)
    return all(
        all(getattr(ledger[pid], f) == v for f, v in fields.items() if f != "shape")
        for pid, fields in edits.items()
    )


def _interaction_ids(res: ReformResult, name: str) -> tuple[str, ...]:
    ids = set(res.headline.assumption_ids)
    fee_ids, parking_ids = _adapters.PACKAGE_LINES.get(get_reform(res.reform).family, ((), ()))
    if name.startswith("fees"):
        ids |= {"fees.fee_cut_share", *fee_ids}
    if name.startswith("parking"):
        ids |= {"parking.share_still_built_elsewhere", *parking_ids}
    return tuple(sorted(ids))


def _overlap(
    keys: tuple[str, ...],
    reforms: Mapping[str, Mapping[str, Any]],
    outcomes: Mapping[str, ReformOutcome],
    run_context: RunContext,
    draws: Draws,
) -> tuple[
    tuple[np.ndarray, tuple[str, ...]] | None,
    dict[str, tuple[dict[str, Any], np.ndarray, ReformResult]],
]:
    """Rerun missing middle without the lots ADUs use; return the overlap and its start."""
    pair = overlap_pair(keys)
    if pair is None:
        return None, {}
    adu_key, mm_key = pair
    adu = outcomes[adu_key].result
    if adu is None or adu.detail is None:
        raise ValueError("the ADU result has no tract stages to share lots with")
    options = {**dict(reforms[mm_key]), _adapters.OPTION_ADU_LOTS: adu.detail.stages["built"]}
    res = get_reform(mm_key).adapter(run_context, draws, options)
    with_lots = res.county_draws(run_context.county_ids)
    overlap = lot_overlap(outcomes[mm_key].county_draws, with_lots)
    ids = tuple(sorted({*res.headline.assumption_ids, "missing_middle.adu_lot_overlap_share"}))
    return (overlap, ids), {mm_key: (options, with_lots, res)}


def compute(
    context: StateContext,
    reforms: ReformsArg = None,
    overrides: Mapping[str, Any] | None = None,
    seed: int = 0,
    draws: int = DEFAULT_DRAWS,
    cycle: str = "typical",
    *,
    code: str | None = None,
) -> Result:
    """Run the baseline, each reform and the package for a loaded state (pure).

    Args:
        context: The state context (see ``engine.context.load_context``).
        reforms: Reforms to run (see :func:`normalise_reforms`).
        overrides: The user's ledger overrides.
        seed: Seed; the same seed gives the same result.
        draws: Number of Monte Carlo draws.
        cycle: Baseline housing cycle: ``weak``, ``typical`` or ``strong``.
        code: Code fingerprint to stamp; defaults to :func:`code_hash`.

    Returns:
        The result.
    """
    chosen = normalise_reforms(reforms)
    run_context, shared = prepare(context, overrides, seed=seed, draws=draws, cycle=cycle)
    county_ids = run_context.county_ids
    baseline = run_context.baseline
    base_c = _baseline_county_draws(baseline, county_ids, shared.baseline_scale)
    base_state = base_c.sum(axis=0)

    outcomes: dict[str, ReformOutcome] = {}
    for key, options in chosen.items():
        res = get_reform(key).adapter(run_context, shared, options)
        outcomes[key] = _outcome(key, options, res, county_ids, base_state)
    keys = tuple(chosen)
    overlap, start = _overlap(keys, chosen, outcomes, run_context, shared)
    extras = _interactions(keys, chosen, outcomes, run_context, shared, start)
    package = _package(keys, outcomes, extras, overlap, county_ids, base_state, draws)

    if not keys or not np.any(package.state_draws):
        total = baseline.state
    else:
        total = rec.from_draws(
            f"permitted units {SPAN} with {package.combined.name.split(', ', 1)[-1]}",
            base_state + package.state_draws,
            "units",
            expression="baseline units + combined added units, in each draw",
            inputs=(),
            assumption_ids=sorted(
                {*baseline.state.assumption_ids, *package.combined.assumption_ids}
            ),
            source_ids=sorted({*baseline.state.source_ids, *package.combined.source_ids}),
            tier=0,
        )
    hashes = Hashes(
        code=code or code_hash(),
        data=context.data_hash,
        assumptions=assumption_hash(shared.ledger, context.calibration_hash),
    )
    notes = []
    if context.synthetic:
        notes.append("Testland is a synthetic state for tests; its numbers describe no real place.")
    notes.append(
        "Each draw places the baseline at one position in its likely range, the same for "
        "every county; reforms acting on baseline production use the same position."
    )
    return Result(
        state=context.state,
        reform_ids=keys,
        options={k: dict(v) for k, v in chosen.items()},
        overrides=dict(overrides or {}),
        seed=seed,
        draws=draws,
        cycle=cycle,
        hashes=hashes,
        county_ids=county_ids,
        baseline=baseline.state,
        baseline_counties={str(c): r for c, r in baseline.counties.items()},
        baseline_draws=_frozen(base_state),
        baseline_county_draws=_frozen(base_c),
        reforms=outcomes,
        package=package,
        total=total,
        synthetic=context.synthetic,
        notes=tuple(notes),
        run_context=run_context,
        draws_obj=shared,
    )


def reform_total(
    run_context: RunContext,
    key: str,
    *,
    ledger: Ledger,
    options: Mapping[str, Any] | None = None,
    baseline_multiplier: float = 1.0,
) -> float:
    """State added units from one reform with every input pinned (one draw).

    Used by the tornado: ``ledger`` should hold fixed values only, so a single
    draw is the exact answer at those values.

    Args:
        run_context: The run context.
        key: Reform key.
        ledger: The ledger to use as is (no scenario or user overrides are
            re-applied).
        options: Reform options (for example the fee view).
        baseline_multiplier: Baseline scale for every county (1 = baseline P50).

    Returns:
        State added units 2027-2031.
    """
    scale = np.full((len(run_context.county_ids), 1), float(baseline_multiplier))
    draws = Draws(ledger=ledger, n=1, seed=0, values={}, baseline_scale=scale)
    opts = {**dict(options or {}), "ledger_replace": ledger}
    res = get_reform(key).adapter(run_context, draws, opts)
    return math.fsum(res.added[:, 0])


# ---------------------------------------------------------------- the entry point

_CACHE: OrderedDict[tuple, Result] = OrderedDict()
_CONTEXTS: dict[str, tuple[tuple, StateContext]] = {}
_STATS = {"hits": 0, "misses": 0}


def _signature(state: str) -> tuple:
    from visionhousing.engine.context import pack_dir
    from visionhousing.ledger.loader import ROOT

    paths = [p for p in sorted(pack_dir(state).rglob("*")) if p.is_file()]
    paths += sorted((ROOT / "assumptions").rglob("*.yaml"))
    return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)


def _context(state: str) -> StateContext:
    code = state.upper()
    sig = _signature(code)
    held = _CONTEXTS.get(code)
    if held is not None and held[0] == sig:
        return held[1]
    ctx = load_context(code)
    _CONTEXTS[code] = (sig, ctx)
    return ctx


def _key(context: StateContext, chosen, overrides, seed, draws, cycle) -> tuple:
    ledger = apply_overrides(context.ledger, overrides)
    hashes = (code_hash(), context.data_hash, assumption_hash(ledger, context.calibration_hash))
    return (
        context.state,
        json.dumps(chosen, sort_keys=True, default=str),
        json.dumps(dict(overrides or {}), sort_keys=True, default=str),
        int(seed),
        int(draws),
        cycle,
        hashes,
    )


def run(
    state: str = TESTLAND,
    reforms: ReformsArg = None,
    overrides: Mapping[str, Any] | None = None,
    seed: int = 0,
    draws: int = DEFAULT_DRAWS,
    cycle: str = "typical",
    context: StateContext | None = None,
    *,
    use_cache: bool = True,
) -> Result:
    """Run the model: baseline, each reform alone, and the package, with ranges.

    Args:
        state: State code (``"TL"`` = Testland; else ``data/packs/<STATE>/``).
        reforms: Reforms to run: ``None`` (baseline only), a key, a list of
            keys, or key -> options. Keys: ``adu``, ``fees``, ``parking``,
            ``pa_hb2186``, ``pa_sb1346``, ``missing_middle``.
        overrides: Ledger overrides (see ``ledger.apply_overrides``).
        seed: Seed; the same seed gives the same result.
        draws: Monte Carlo draws (default 2,000).
        cycle: Baseline housing cycle: ``weak``, ``typical`` or ``strong``.
        context: A loaded state context to use instead of reading files.
        use_cache: Reuse a cached result for identical inputs and hashes.

    Returns:
        The result.
    """
    ctx = context if context is not None else _context(state)
    chosen = normalise_reforms(reforms)
    key = _key(ctx, chosen, overrides, seed, draws, cycle)
    if use_cache and key in _CACHE:
        _STATS["hits"] += 1
        _CACHE.move_to_end(key)
        return _CACHE[key]
    _STATS["misses"] += 1
    result = compute(ctx, chosen, overrides, seed, draws, cycle)
    if use_cache:
        _CACHE[key] = result
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return result


def clear_cache() -> None:
    """Empty the result cache and the loaded-context cache."""
    _CACHE.clear()
    _CONTEXTS.clear()
    _STATS.update(hits=0, misses=0)


def cache_info() -> dict[str, int]:
    """Return cache hits, misses and size."""
    return {**_STATS, "size": len(_CACHE)}


def known_reforms() -> list[str]:
    """Return the registered reform keys."""
    return sorted(REGISTRY)
