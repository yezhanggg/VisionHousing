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
7. **Splits every five-year total into years** 2027-2031 in every draw
   (baseline, each reform, the package and the total), and keeps a reform's
   added authorizations apart from its net change in the housing stock
   (``engine.outcome``).
8. **Returns a Result** with records (formula, inputs, assumption ids, sources,
   tier), the three hashes (code, data, assumptions) and the outcome contract
   (as-of date, data and policy vintages, effective-date rule, economic
   conditions held constant, and the audit of each reform's final stage).

Every headline is additional units authorized by building permits, 2027-2031,
in the same unit as the baseline.

**Tract level.** The ADU, missing-middle and TOD reforms work tract by tract;
the fee cut and the parking reform work by county. The package keeps its
tract-level part draw by draw (``PackageOutcome.tract_draws``: the tract-level
reforms run alone, minus the missing-middle units lost to ADU lots, plus the
pro-forma interactions, all per tract), so for every county and draw::

    package county = sum of its tracts' tract_draws + each county-level reform's county draws

The baseline is projected by county only; it has no tract split.
:meth:`ReformOutcome.tract_steps` summarises the funnel steps that a tract-level
reform module computes per tract (P10 / P50 / P90 per tract), and
:meth:`ReformOutcome.county_steps` the same steps by county (the county's tracts
summed in each draw); a county-level reform has none.

Results are cached in memory by (state, reforms, overrides, seed, draws,
cycle, hashes). The same inputs give the same output, bit for bit.

:func:`compute` is the pure core: a loaded context in, a result out, no files.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from visionhousing.baseline import BaselineResult, baseline_from_ledger
from visionhousing.baseline.contest import place_info_for_context
from visionhousing.engine import adapters as _adapters  # importing it registers the reforms
from visionhousing.engine import outcome as oc
from visionhousing.engine import records as rec
from visionhousing.engine.capacity import build_capacity, screen_wanted
from visionhousing.engine.context import TESTLAND, StateContext, load_context
from visionhousing.engine.hashes import Hashes, assumption_hash, code_hash
from visionhousing.engine.market import MARKET_MODES, build_market
from visionhousing.engine.montecarlo import baseline_scale
from visionhousing.engine.packages import interaction_options, lot_overlap, overlap_pair
from visionhousing.engine.readouts import build_readouts
from visionhousing.engine.records import Record
from visionhousing.engine.registry import (
    REGISTRY,
    Draws,
    ReformResult,
    RunContext,
    get_reform,
)
from visionhousing.ledger import DEFAULT_DRAWS, Ledger, apply_overrides, sample
from visionhousing.scenarios.adu_levers import outcome_timing
from visionhousing.scenarios.fees import baseline_by_type

__all__ = [
    "PackageOutcome",
    "ReformOutcome",
    "Result",
    "StepSummary",
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
"""Most results the engine cache keeps."""

CACHE_BYTES_ENV = "VH_ENGINE_CACHE_BYTES"
"""Environment variable overriding :data:`CACHE_BYTES_DEFAULT` (bytes; 0 = keep the newest only)."""

CACHE_BYTES_DEFAULT = 3 * 2**30
"""Most memory (estimated, bytes) the engine cache's results may hold together: about 3 GB.

This is a software setting, not a model parameter: it changes how often a run
is repeated, never a number.
"""

ReformsArg = str | Sequence[str] | Mapping[str, Mapping[str, Any] | None] | None
"""Reforms to run: a key, a list of keys, or key -> options (``None`` = none)."""


# ---------------------------------------------------------------- result types


def _frozen(arr: np.ndarray) -> np.ndarray:
    out = np.array(arr, dtype=float)
    out.setflags(write=False)
    return out


@dataclass(frozen=True)
class StepSummary:
    """One funnel step of a tract-level reform, summarised by tract or by county.

    Attributes:
        stage: The reform module's stage key (for example ``"eligible"``).
        record: The module's own state record of the step (name, unit, formula, ids).
        level: ``"tract"`` (each tract's own draws) or ``"county"`` (the county's
            tracts summed in each draw, then summarised).
        geoids: Tract or county ids, in the order of the arrays.
        p10: 10th percentile per tract or county, over the run's draws.
        p50: Median per tract or county.
        p90: 90th percentile per tract or county.
        formula: How each value was made, in words (no numbers).
    """

    stage: str
    record: Record
    level: str
    geoids: tuple[str, ...]
    p10: np.ndarray = field(repr=False, compare=False)
    p50: np.ndarray = field(repr=False, compare=False)
    p90: np.ndarray = field(repr=False, compare=False)
    formula: str = ""


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
        readouts: Readouts from the paired draws (``engine.readouts``): the
            chance of adding at least N homes, the widths of the baseline and of
            the added homes, and what the reform costs, by name.
        readout_counties: Readout name to county id to its record (added homes
            as a share of the county baseline; for the fee cut, revenue given up
            and cost per added home; for parking, the builders' saving).
        readout_texts: Plain texts that go with the readouts (the cost framing,
            or "not defined" when a cost per added home cannot be worked out).
        gross_net: ``authorized`` (the headline: added units authorized by
            building permits), ``net_stock`` (net added homes after
            demolition) and, for a reform that redevelops sites, ``removed``
            (homes demolished) and ``baseline_on_sites`` (the baseline's own
            authorizations on those sites); state records.
        net_stock_counties: County id to its net-added-homes record (only for
            a reform that removes homes; otherwise net equals the headline).
        annual: The headline split into 2027-2031 by the reform's adoption
            curve, per draw (``engine.outcome.AnnualSeries``).
        accounting: The five record types (``engine.outcome.Accounting``):
            baseline in the permit survey's definition, additional comparable
            authorizations by work type, locally authorized units outside that
            definition, authorized net change if completed, and the timing
            ledger.
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
    readouts: Mapping[str, Record] = field(default_factory=dict)
    readout_counties: Mapping[str, Mapping[str, Record]] = field(default_factory=dict)
    readout_texts: Mapping[str, str] = field(default_factory=dict)
    gross_net: Mapping[str, Record] = field(default_factory=dict)
    net_stock_counties: Mapping[str, Record] = field(default_factory=dict)
    annual: oc.AnnualSeries | None = field(default=None, compare=False)
    accounting: oc.Accounting | None = field(default=None, compare=False)

    def tract_annual(self, county: str | None = None) -> oc.AnnualTable | None:
        """The headline by tract and year (P10 / P50 / P90), or ``None`` for a county-level reform.

        Each tract's draws times the draw's year shares, so a tract's years add
        up to its five-year draws in every draw.

        Args:
            county: Only this county's tracts (default: every tract).
        """
        res = self.result
        if res is None or res.level != "tract" or self.annual is None:
            return None
        cols = None
        if county is not None:
            cols = np.flatnonzero(np.asarray(res.county_of) == str(county))
        return oc.tract_table(
            res.added,
            self.annual.shares,
            res.geoids,
            self.annual.years,
            expression=_strip(res.headline.formula).removeprefix("the sum over tracts of "),
            assumption_ids=sorted({*res.headline.assumption_ids, *self.annual.assumption_ids}),
            source_ids=res.headline.source_ids,
            rows=cols,
        )

    @property
    def level(self) -> str:
        """``"tract"`` or ``"county"``: the geography the reform module works at."""
        return self.result.level if self.result is not None else "county"

    def _stage_arrays(self, stages: Iterable[str] | None) -> list[tuple[str, Record, Any]]:
        """The module's own (stage, state record, draws x tracts array), in funnel order."""
        detail = self.result.detail if self.result is not None else None
        arrays = getattr(detail, "stages", None)
        if self.level != "tract" or not isinstance(arrays, Mapping):
            return []
        if len(arrays) != len(self.steps):
            raise ValueError(f"reform '{self.key}': stages and funnel records do not line up")
        wanted = None if stages is None else set(stages)
        return [
            (stage, record, draws)
            for (stage, draws), record in zip(arrays.items(), self.steps, strict=True)
            if wanted is None or stage in wanted
        ]

    def tract_steps(
        self, stages: Iterable[str] | None = None, *, county: str | None = None
    ) -> tuple[StepSummary, ...]:
        """Summarise the funnel steps the reform module computes for every tract.

        Each step is P10 / P50 / P90 per tract of the module's own stage array
        (draws x tracts), with the same percentile rule as ``records.from_draws``.
        Nothing is split or spread: a step the module computes only for the
        county or the state is not here, and a county-level reform has none.

        Args:
            stages: Stage keys wanted (default: every stage), in the module's order.
            county: Only this county's tracts (default: every tract).

        Returns:
            The steps, in the module's funnel order; empty for a county-level reform.
        """
        if not (rows := self._stage_arrays(stages)):
            return ()
        detail = self.result.detail  # type: ignore[union-attr]
        cols = self._columns(county)
        geoids = tuple(str(g) for g in np.asarray(detail.geoids)[cols])
        out = []
        for stage, record, draws in rows:
            arr = np.asarray(draws, dtype=float)[:, cols]
            p10, p50, p90 = np.percentile(arr, [10, 50, 90], axis=0)
            expression = _tract_expression(record)
            formula = f"P10, P50, P90 of {arr.shape[0]:,} seeded draws, per tract, of {expression}"
            out.append(StepSummary(stage, record, "tract", geoids, p10, p50, p90, formula))
        return tuple(out)

    def county_steps(
        self, stages: Iterable[str] | None = None, *, county: str | None = None
    ) -> tuple[StepSummary, ...]:
        """Summarise the same funnel steps by county: the county's tracts summed in each draw.

        So a county's range is the engine's own (a sum of draws, never a sum of
        tract percentiles), as ``AduResult.county_records`` does for one stage.

        Args:
            stages: Stage keys wanted (default: every stage), in the module's order.
            county: Only this county (default: every county with tracts).

        Returns:
            The steps, in the module's funnel order, with county ids sorted;
            empty for a county-level reform.
        """
        if not (rows := self._stage_arrays(stages)):
            return ()
        cols = self._columns(county)
        if cols.size == 0:
            return ()
        counties = np.asarray([str(c) for c in self.result.detail.counties])[cols]  # type: ignore[union-attr]
        order = cols[np.argsort(counties, kind="stable")]
        ids, starts = np.unique(np.sort(counties, kind="stable"), return_index=True)
        out = []
        for stage, record, draws in rows:
            arr = np.asarray(draws, dtype=float)
            sums = np.add.reduceat(arr[:, order], starts, axis=1)  # draws x counties
            p10, p50, p90 = np.percentile(sums, [10, 50, 90], axis=0)
            formula = (
                f"P10, P50, P90 of {arr.shape[0]:,} seeded draws, per county, of the sum over "
                f"the county's tracts of {_tract_expression(record)}"
            )
            out.append(
                StepSummary(stage, record, "county", tuple(map(str, ids)), p10, p50, p90, formula)
            )
        return tuple(out)

    def _columns(self, county: str | None) -> np.ndarray:
        """Column numbers of the stage arrays: every tract, or one county's tracts."""
        counties = np.asarray([str(c) for c in self.result.detail.counties])  # type: ignore[union-attr]
        if county is None:
            return np.arange(counties.size)
        return np.flatnonzero(counties == str(county))


def _tract_expression(record: Record) -> str:
    """The per-tract expression inside a state funnel record's formula."""
    return _strip(record.formula).removeprefix("the sum over tracts of ")


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
        tract_reforms: Keys of the package's tract-level reforms.
        county_reforms: Keys of the package's county-level reforms (no tract values).
        tract_geoids: Tract ids, in the order of ``tract_draws`` rows.
        tract_counties: County id of each tract.
        tract_draws: The package's tract-level part by tract and draw, shape
            ``(tracts, draws)``: each tract-level reform run alone, minus the
            missing-middle units lost to ADU lots, plus the interactions, all
            per tract. ``None`` when no tract-level reform is on.
        tract_county_draws: ``tract_draws`` summed to counties in each draw,
            shape ``(counties, draws)`` in the run's county order (``None`` with
            no tract-level reform). Adding each county-level reform's county
            draws gives ``county_draws``.
        tract_combined: State record of ``tract_draws`` summed over tracts in
            each draw (``None`` with no tract-level reform).
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
    tract_reforms: tuple[str, ...] = ()
    county_reforms: tuple[str, ...] = ()
    tract_geoids: tuple[str, ...] = field(default=(), repr=False, compare=False)
    tract_counties: tuple[str, ...] = field(default=(), repr=False, compare=False)
    tract_draws: np.ndarray | None = field(default=None, repr=False, compare=False)
    tract_county_draws: np.ndarray | None = field(default=None, repr=False, compare=False)
    tract_combined: Record | None = None


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
        baseline_annual: The baseline split into years (each county's own
            yearly point projections, scaled to its draw).
        package_annual: The package split into years (each part by its own curve).
        total_annual: Baseline plus package, by year.
        contract: The outcome contract (``engine.outcome.contract``): as-of
            date, measure, vintages, effective-date rule, conditions held
            constant, audit of each final stage, the baseline's bias-correction
            decision.
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
    baseline_annual: oc.AnnualSeries | None = field(default=None, compare=False)
    package_annual: oc.AnnualSeries | None = field(default=None, compare=False)
    total_annual: oc.AnnualSeries | None = field(default=None, compare=False)
    contract: Mapping[str, Any] = field(default_factory=dict, compare=False)
    market: Any = field(default=None, compare=False)
    """The package under the ``market`` option (``engine.market.MarketOutcome``), or ``None``."""
    capacity: Any = field(default=None, compare=False)
    """Every reform screened for practical capacity (``engine.capacity.CapacityOutcome``),
    only with the run option ``capacity_screen``; ``None`` otherwise."""

    @property
    def as_of(self) -> str | None:
        """The as-of date the run is frozen at (``assumptions/outcome.yaml``)."""
        return self.contract.get("as_of")

    def records(self) -> list[Record]:
        """Return every record in the result, baseline first."""
        out = [self.baseline, *self.baseline_counties.values()]
        for o in self.reforms.values():
            out += [o.added, o.pct_change, *o.counties.values(), *o.steps, *o.records.values()]
            out += [*o.readouts.values()]
            out += [r for by in o.readout_counties.values() for r in by.values()]
            out += [r for k, r in o.gross_net.items() if k != "authorized"]
            out += [*o.net_stock_counties.values()]
            if o.annual is not None:
                out += [*o.annual.state]
                out += [r for r in o.annual.timing.values() if isinstance(r, Record)]
            if o.accounting is not None:
                out += [*o.accounting.records.values()]
        if self.package is not None:
            p = self.package
            out += [*p.waterfall, *p.interactions.values(), p.pct_change, *p.counties.values()]
        if self.market is not None:
            out += self.market.records()
        if self.capacity is not None:
            out += self.capacity.records()
        if self.total is not None:
            out.append(self.total)
        for series in (self.baseline_annual, self.package_annual, self.total_annual):
            if series is not None:
                out += [*series.state]
                out += [r for r in series.timing.values() if isinstance(r, Record)]
        return out

    def to_dict(self) -> dict[str, Any]:
        """Return the result as plain JSON-ready data (records, no draw arrays)."""

        def r(x: Record) -> dict[str, Any]:
            return x.model_dump(mode="json")

        def outcome(o: ReformOutcome) -> dict[str, Any]:
            out = _outcome_dict(o)
            status = None if o.result is None else o.result.status
            if status is not None:
                out["status"] = _jsonable(dict(status))
            return out

        def _outcome_dict(o: ReformOutcome) -> dict[str, Any]:
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
                "readouts": {k: r(x) for k, x in o.readouts.items()},
                "readout_counties": {
                    name: {c: r(x) for c, x in by.items()}
                    for name, by in o.readout_counties.items()
                },
                "readout_texts": dict(o.readout_texts),
                "gross_net": {k: r(x) for k, x in o.gross_net.items()},
                "net_stock_counties": {c: r(x) for c, x in o.net_stock_counties.items()},
                "annual": None if o.annual is None else o.annual.to_dict(),
                "accounting": None if o.accounting is None else o.accounting.to_dict(),
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
        out = {
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
            "as_of": self.as_of,
            "contract": _jsonable(dict(self.contract)),
            "annual": {
                name: None if series is None else series.to_dict()
                for name, series in (
                    ("baseline", self.baseline_annual),
                    ("package", self.package_annual),
                    ("total", self.total_annual),
                )
            },
        }
        if self.market is not None:  # only with the package option, so today's output is unchanged
            out["market"] = self.market.to_dict()
        if self.capacity is not None:  # only with capacity_screen, so today's output is unchanged
            out["capacity"] = self.capacity.to_dict()
        return out


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
            ``pa_hb2186``), an extra scenario with the benchmark it builds on
            (``ReformSpec.excludes``), or options that are not a mapping.
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
    for key in out:
        spec = get_reform(key)
        for other in out:
            o = get_reform(other)
            if other != key and (o.family in spec.excludes or spec.family in o.excludes):
                raise ValueError(
                    f"reforms '{key}' and '{other}' reach the same lots or projects; "
                    "choose one of them"
                )
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
    baseline, _ = baseline_from_ledger(
        context.permits,
        ledger,
        cycle,
        widen=not context.synthetic,
        places=place_info_for_context(context),
        state=context.state,
    )
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
    what = f"added units authorized, {res.title}"
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
    statewide = (res.status or {}).get("statewide") or {}
    hidden = statewide.get("shown") is False
    label = str(statewide.get("label", ""))
    added = rec.from_draws(
        f"{what} {SPAN}, state"
        + (f", measured contribution only ({label}); statewide range not shown" if hidden else ""),
        state,
        "units",
        expression=f"the sum over counties of {roll}; per draw: {_strip(head.formula)}",
        **common,
    )
    pct = _pct_record(what, state, base_state, *common.values())
    if hidden:
        pct = pct.model_copy(
            update={"name": f"{pct.name}: not shown, the measured footprint is partial ({label})"}
        )
    counties = _county_records(county, county_ids, what=what, expression=roll, **common)
    for c, (text, sources) in (res.county_notes or {}).items():
        if c in counties:
            i = county_ids.index(c)
            counties[c] = rec.from_draws(
                f"{what} {SPAN}, county {c}",
                county[i],
                "units",
                expression=f"{roll}; {text}",
                assumption_ids=head.assumption_ids,
                source_ids=sorted({*head.source_ids, *sources}),
                tier=head.tier,
            )
    return ReformOutcome(
        key=key,
        title=res.title,
        options=dict(options),
        added=added,
        pct_change=pct,
        counties=counties,
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


TractRows = tuple[np.ndarray, tuple[str, ...], tuple[str, ...]]
"""A tract-level array with its row labels: ``(array (tracts, draws), geoids, county ids)``."""

Extra = tuple[np.ndarray, tuple[str, ...], TractRows | None]
"""One interaction: ``(county draws, assumption ids, tract rows or None)``."""


def _rows(res: ReformResult, arr: np.ndarray | None = None) -> TractRows:
    data = np.asarray(res.added if arr is None else arr, dtype=float)
    return data, tuple(map(str, res.geoids)), tuple(map(str, res.county_of))


def _tract_part(
    keys: tuple[str, ...],
    outcomes: Mapping[str, ReformOutcome],
    extras: Mapping[str, Extra],
    overlap_rows: TractRows | None,
    county_ids: tuple[str, ...],
    ids: Iterable[str],
    sources: Iterable[str],
    tier: int | None,
) -> dict[str, Any]:
    """The package's tract-level part: per tract and draw, and its state record.

    Tract-level reforms run alone are added, the tract rows of the ADU-lot
    overlap are taken away and tract-level interactions are added, row by row,
    matched by tract id. Nothing from a county-level reform is spread to tracts.
    """
    tract_keys = tuple(k for k in keys if outcomes[k].level == "tract")
    county_keys = tuple(k for k in keys if k not in tract_keys)
    if not tract_keys:
        return {"tract_reforms": (), "county_reforms": county_keys}
    plus = [_rows(outcomes[k].result) for k in tract_keys]  # type: ignore[arg-type]
    plus += [rows for _d, _i, rows in extras.values() if rows is not None]
    minus = [] if overlap_rows is None else [overlap_rows]
    geoids: dict[str, str] = {}
    for _arr, gs, cs in [*plus, *minus]:
        for g, c in zip(gs, cs, strict=True):
            geoids.setdefault(g, c)
    order = tuple(geoids)
    index = {g: i for i, g in enumerate(order)}
    if len(plus) == 1 and not minus and plus[0][1] == order:
        total = plus[0][0].view()  # one reform alone: its own draws, not a copy
    else:
        total = np.zeros((len(order), plus[0][0].shape[1]))
        for sign, group in ((1.0, plus), (-1.0, minus)):
            for arr, gs, _cs in group:
                total[np.array([index[g] for g in gs], dtype=int)] += sign * arr  # ids are unique
    total.setflags(write=False)
    at = {c: i for i, c in enumerate(county_ids)}
    county_of_row = np.array([at[geoids[g]] for g in order], dtype=int)
    by_row = np.argsort(county_of_row, kind="stable")
    found, starts = np.unique(county_of_row[by_row], return_index=True)
    by_county = np.zeros((len(county_ids), total.shape[1]))
    by_county[found] = np.add.reduceat(total[by_row], starts, axis=0)
    names = " + ".join(outcomes[k].title for k in tract_keys)
    adjust = []
    if minus:
        adjust.append("minus the missing-middle units lost to lots ADUs use")
    if any(rows is not None for _d, _i, rows in extras.values()):
        adjust.append("plus the pro-forma interactions")
    expression = (
        f"the sum over tracts of each tract's added units authorized under {names} (each run alone"
        + (", " + ", ".join(adjust) if adjust else "")
        + ", tract by tract)"
    )
    record = rec.from_draws(
        f"combined added units of the tract-level reforms, {names} {SPAN}, state",
        total.sum(axis=0),
        "units",
        expression=expression,
        assumption_ids=ids,
        source_ids=sources,
        tier=tier,
    )
    return {
        "tract_reforms": tract_keys,
        "county_reforms": county_keys,
        "tract_geoids": order,
        "tract_counties": tuple(geoids[g] for g in order),
        "tract_draws": total,
        "tract_county_draws": _frozen(by_county),
        "tract_combined": record,
    }


def _package(
    keys: tuple[str, ...],
    outcomes: Mapping[str, ReformOutcome],
    extras: Mapping[str, Extra],
    overlap: tuple[np.ndarray, tuple[str, ...]] | None,
    county_ids: tuple[str, ...],
    base_state: np.ndarray,
    n: int,
    overlap_rows: TractRows | None = None,
) -> PackageOutcome:
    parts = {k: np.asarray(outcomes[k].county_draws) for k in keys}
    zero = np.zeros((len(county_ids), n))
    parts_c = sum(parts.values(), zero.copy())
    overlap_c = zero.copy() if overlap is None else overlap[0]
    overlap_ids = () if overlap is None else overlap[1]
    inter_c = sum((d for d, _i, _t in extras.values()), zero.copy())
    combined_c = parts_c - overlap_c + inter_c
    ids, sources, tier = _ids([outcomes[k].added for k in keys])
    names = " + ".join(outcomes[k].title for k in keys) or "no reform"
    common = {"assumption_ids": ids, "source_ids": sources, "tier": tier}

    def state_rec(name: str, arr: np.ndarray, expression: str, **kw: Any) -> Record:
        args = {**common, **kw}
        return rec.from_draws(f"{name} {SPAN}, state", arr, "units", expression=expression, **args)

    interactions = {}
    for name, (d, inter_ids, _t) in extras.items():
        interactions[name] = state_rec(
            f"interaction {name}",
            d.sum(axis=0),
            f"the reform run with the other reform's saving in its pro forma ({name}) "
            "minus the reform run alone, same seed, summed over counties",
            assumption_ids=sorted({*ids, *inter_ids}),
        )
    inter_ids = sorted({*ids, *(i for _d, ii, _t in extras.values() for i in ii)})
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
    tract_keys = [k for k in keys if outcomes[k].level == "tract"]
    t_ids, t_sources, t_tier = _ids([outcomes[k].added for k in tract_keys])
    t_ids = sorted(
        {
            *t_ids,
            *(overlap_ids if overlap_rows is not None else ()),
            *(i for _d, ii, rows in extras.values() if rows is not None for i in ii),
        }
    )
    tract = _tract_part(keys, outcomes, extras, overlap_rows, county_ids, t_ids, t_sources, t_tier)
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
        **tract,
    )


# ---------------------------------------------------------------- the core


def _interactions(
    keys: tuple[str, ...],
    reforms: Mapping[str, Mapping[str, Any]],
    outcomes: Mapping[str, ReformOutcome],
    run_context: RunContext,
    draws: Draws,
    start: Mapping[str, tuple[dict[str, Any], np.ndarray, ReformResult]],
) -> dict[str, Extra]:
    """Rerun each reform with the other reforms' savings in its pro forma.

    Returns interaction name to ``(county draws, assumption ids, tract rows)``;
    the tract rows (the same difference, tract by tract) are kept for a
    tract-level reform and are ``None`` for a county-level one or a zero interaction.
    """
    out: dict[str, Extra] = {}
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
        before_t = alone.added if alone is not None and alone.level == "tract" else None
        for name, change in steps.items():
            if (
                alone is not None
                and alone.ledger is not None
                and _changes_nothing(spec.family, alone.ledger, change)
            ):
                # Scaling a zero line (the benchmark ADU adds no parking) changes no
                # input, so the rerun would give the same draws: the interaction is 0.
                out[name] = (np.zeros_like(before), _interaction_ids(alone, name), None)
                continue
            options.update(change)
            res = spec.adapter(run_context, draws, options)
            after = res.county_draws(run_context.county_ids)
            rows = None
            if res.level == "tract" and before_t is not None:
                rows = _rows(res, np.asarray(res.added) - np.asarray(before_t))
                before_t = res.added
            out[name] = (after - before, _interaction_ids(res, name), rows)
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
    TractRows | None,
]:
    """Rerun missing middle without the lots ADUs use.

    Returns the overlap by county (with its ids), the rerun to start the
    interactions from, and the same overlap tract by tract (``lot_overlap`` per tract).
    """
    pair = overlap_pair(keys)
    if pair is None:
        return None, {}, None
    adu_key, mm_key = pair
    adu = outcomes[adu_key].result
    if adu is None or adu.detail is None:
        raise ValueError("the ADU result has no tract stages to share lots with")
    options = {**dict(reforms[mm_key]), _adapters.OPTION_ADU_LOTS: adu.detail.stages["built"]}
    res = get_reform(mm_key).adapter(run_context, draws, options)
    with_lots = res.county_draws(run_context.county_ids)
    overlap = lot_overlap(outcomes[mm_key].county_draws, with_lots)
    ids = tuple(sorted({*res.headline.assumption_ids, "missing_middle.adu_lot_overlap_share"}))
    rows = None
    alone = outcomes[mm_key].result
    if alone is not None and alone.level == "tract" and res.level == "tract":
        if tuple(map(str, alone.geoids)) != tuple(map(str, res.geoids)):
            raise ValueError("missing middle's rerun has other tracts than the run alone")
        rows = _rows(res, lot_overlap(alone.added, res.added))
    return (overlap, ids), {mm_key: (options, with_lots, res)}, rows


def compute(
    context: StateContext,
    reforms: ReformsArg = None,
    overrides: Mapping[str, Any] | None = None,
    seed: int = 0,
    draws: int = DEFAULT_DRAWS,
    cycle: str = "typical",
    *,
    code: str | None = None,
    market: str | None = None,
    capacity_screen: bool = False,
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
        market: Package option: ``None`` (today's package rules), ``"choice"``
            or ``"choice+capacity"`` (``engine.market``). It adds
            ``Result.market`` beside the package; the package itself is unchanged.
        capacity_screen: Run option: also report every reform and the package
            screened for practical capacity (sewer, flood zone, steep slope;
            ``engine.capacity``) in ``Result.capacity``. Every other number is
            unchanged ("not screened" is today's).

    Returns:
        The result.
    """
    if market is not None and market not in MARKET_MODES:
        raise ValueError(f"market must be one of {MARKET_MODES}, got {market!r}")
    chosen = normalise_reforms(reforms)
    if capacity_screen:
        already = [k for k, o in chosen.items() if screen_wanted(get_reform(k).family, o)]
        if already:
            raise ValueError(
                f"reforms {already} are already screened (capacity_screen option or variant); "
                "ask for the run option capacity_screen or the reform option, not both"
            )
    run_context, shared = prepare(context, overrides, seed=seed, draws=draws, cycle=cycle)
    county_ids = run_context.county_ids
    baseline = run_context.baseline
    base_c = _baseline_county_draws(baseline, county_ids, shared.baseline_scale)
    base_state = base_c.sum(axis=0)

    outcomes: dict[str, ReformOutcome] = {}
    for key, options in chosen.items():
        spec = get_reform(key)
        res = spec.adapter(run_context, shared, options)
        outcome = _outcome(key, options, res, county_ids, base_state)
        extra = build_readouts(
            key,
            spec.family,
            outcome,
            base_state,
            base_c,
            county_ids,
            shared.ledger,
            baseline.state,
            seed=seed,
            rerun=lambda opts, spec=spec: spec.adapter(run_context, shared, opts),
            groups=context.county_types,
        )
        outcomes[key] = replace(outcome, **extra)
    keys = tuple(chosen)
    overlap, start, overlap_rows = _overlap(keys, chosen, outcomes, run_context, shared)
    extras = _interactions(keys, chosen, outcomes, run_context, shared, start)
    package = _package(keys, outcomes, extras, overlap, county_ids, base_state, draws, overlap_rows)
    years = _years(shared.ledger)
    outcomes = {
        k: _with_outcome_contract(o, county_ids, shared, years, baseline.state)
        for k, o in outcomes.items()
    }
    base_annual = oc.annual_series(
        "baseline",
        "baseline units authorized by building permits",
        base_c,
        oc.baseline_year_shares(baseline.annual, county_ids, years),
        county_ids,
        years,
        expression="county baseline P50 x the draw's position in the baseline's likely range",
        assumption_ids=baseline.state.assumption_ids,
        source_ids=baseline.state.source_ids,
        tier=baseline.state.tier,
        per_county_shares=True,
    )
    package_annual = _package_annual(package, outcomes, extras, overlap, county_ids, years)
    market_outcome = None
    if market is not None and keys:
        market_outcome = build_market(
            market,
            keys=keys,
            chosen=chosen,
            outcomes=outcomes,
            extras=extras,
            overlap=overlap,
            run_context=run_context,
            shared=shared,
            package=package,
            baseline_by_year=np.asarray(base_annual.county_draws),
            years=years,
        )
    capacity_outcome = None
    if capacity_screen and keys:
        capacity_outcome = build_capacity(keys, outcomes, package, run_context, shared, county_ids)
    total_annual = None
    if package_annual is not None:
        total_annual = oc.annual_series(
            "total",
            "units authorized by building permits with the package",
            base_c + package.county_draws,
            np.zeros((draws, len(years))),
            county_ids,
            years,
            expression="baseline units + combined added units, in each draw",
            assumption_ids=sorted({*base_annual.assumption_ids, *package_annual.assumption_ids}),
            source_ids=sorted({*baseline.state.source_ids, *package.combined.source_ids}),
            tier=0,
            by_year=base_annual.county_draws + package_annual.county_draws,
            how="the baseline's yearly split plus the package's",
        )

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
    contract = oc.contract(
        context.state,
        {k: get_reform(k).family for k in keys},
        baseline_correction=baseline.correction,
        synthetic=context.synthetic,
        folder=context.derived.get(("pack_folder",)),
    )
    outcomes = {k: _slim(o) for k, o in outcomes.items()}
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
        baseline_annual=base_annual,
        package_annual=package_annual,
        total_annual=total_annual,
        contract=contract,
        market=market_outcome,
        capacity=capacity_outcome,
    )


_UNREAD_DETAIL: dict[str, Any] = {
    "feasible_share": None,
    "feasible_share_by_type": {},
    "breakeven_rent": None,
    "residual_per_unit": None,
    "eligible_homes": None,
    "removed": None,
    "baseline_on_sites": None,
}
"""Funnel detail fields that nothing reads once a run is finished, and their empty value.

``removed`` and ``baseline_on_sites`` stay on the reform's result (tracts x
draws, the copy the outcome records read); only the funnel's duplicate goes.
"""


def _slim(o: ReformOutcome) -> ReformOutcome:
    """Drop the funnel detail arrays nothing reads after the run.

    A finished result is kept in the engine cache and read by the API, grade
    cards, comparisons and district views. None of them reads the feasible
    shares, break-even rents or the funnel's own copies of the demolition
    arrays: those were inputs to the stages, which are kept. Dropping them
    roughly halves what a large state's result holds in memory. No number
    changes.
    """
    res = o.result
    detail = getattr(res, "detail", None) if res is not None else None
    if detail is None or not dataclasses.is_dataclass(detail):
        return o
    names = {f.name for f in dataclasses.fields(detail)}
    changes = {k: v for k, v in _UNREAD_DETAIL.items() if k in names}
    if not changes:
        return o
    return replace(o, result=replace(res, detail=replace(detail, **changes)))


def _years(ledger: Ledger) -> tuple[int, ...]:
    first = int(ledger["baseline.first_year"].central)
    horizon = int(ledger["baseline.horizon_years"].central)
    return tuple(range(first, first + horizon))


def _with_outcome_contract(
    o: ReformOutcome,
    county_ids: tuple[str, ...],
    shared: Draws,
    years: tuple[int, ...],
    baseline: Record | None = None,
) -> ReformOutcome:
    """Add the gross/net records, the yearly split and the five record types to one outcome."""
    res = o.result
    if res is None:
        return o
    spec = get_reform(o.key)
    ledger = res.ledger if res.ledger is not None else shared.ledger
    shares, timing_ids = oc.reform_year_shares(
        spec.family, ledger, shared.n, shared.seed, given=res.year_share, horizon=len(years)
    )
    gross_net, net_counties = oc.gross_net_records(res, county_ids, o.added, res.title)
    annual = oc.annual_series(
        o.key,
        f"added units authorized by building permits, {res.title}",
        np.asarray(o.county_draws),
        shares,
        county_ids,
        years,
        expression=_strip(o.added.formula),
        assumption_ids=sorted({*o.added.assumption_ids, *timing_ids}),
        source_ids=o.added.source_ids,
        tier=o.added.tier,
    )
    lever_timing = outcome_timing(
        res, f"added units authorized by building permits, {res.title}", years
    )
    if lever_timing is not None:  # ADU two-lever case: report permits moved in time apart
        annual = replace(annual, timing={**annual.timing, **lever_timing})
    books = oc.accounting(
        spec.family, res.title, o.added, np.asarray(o.state_draws), res, baseline, gross_net, annual
    )
    return replace(
        o, gross_net=gross_net, net_stock_counties=net_counties, annual=annual, accounting=books
    )


def _package_annual(
    package: PackageOutcome,
    outcomes: Mapping[str, ReformOutcome],
    extras: Mapping[str, Extra],
    overlap: tuple[np.ndarray, tuple[str, ...]] | None,
    county_ids: tuple[str, ...],
    years: tuple[int, ...],
) -> oc.AnnualSeries | None:
    """The package by year, each part split by its own curve.

    Each reform by its own curve, the overlap by missing middle's, each
    interaction by the curve of the reform it reruns.
    """
    keys = package.reforms
    if not keys or any(outcomes[k].annual is None for k in keys):
        return None
    by_year = sum(np.asarray(outcomes[k].annual.county_draws) for k in keys)  # type: ignore[union-attr]
    if overlap is not None:
        pair = overlap_pair(keys)
        if pair is not None:
            share = outcomes[pair[1]].annual.shares  # type: ignore[union-attr]
            by_year = by_year - overlap[0][:, :, None] * share[None, :, :]
    for name, (d, _ids, _rows) in extras.items():
        key = name.rsplit(" -> ", 1)[-1]
        share = outcomes[key].annual.shares  # type: ignore[union-attr]
        by_year = by_year + np.asarray(d)[:, :, None] * share[None, :, :]
    ids = sorted({i for k in keys for i in outcomes[k].annual.assumption_ids})  # type: ignore[union-attr]
    ids = sorted({*ids, *package.combined.assumption_ids})
    return oc.annual_series(
        "package",
        f"combined added units authorized, {' + '.join(outcomes[k].title for k in keys)}",
        package.county_draws,
        np.zeros((package.county_draws.shape[1], len(years))),
        county_ids,
        years,
        expression="sum of parts - overlap + interaction, in each draw",
        assumption_ids=ids,
        source_ids=package.combined.source_ids,
        tier=package.combined.tier,
        by_year=by_year,
        how=(
            "each reform's own year shares (the overlap by missing middle's, each interaction "
            "by the curve of the reform it reruns)"
        ),
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
_BYTES: dict[tuple, int] = {}
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


def _key(
    context: StateContext,
    chosen,
    overrides,
    seed,
    draws,
    cycle,
    market=None,
    capacity_screen: bool = False,
) -> tuple:
    ledger = apply_overrides(context.ledger, overrides)
    hashes = (code_hash(), context.data_hash, assumption_hash(ledger, context.calibration_hash))
    key = (
        context.state,
        json.dumps(chosen, sort_keys=True, default=str),
        json.dumps(dict(overrides or {}), sort_keys=True, default=str),
        int(seed),
        int(draws),
        cycle,
        hashes,
    )
    if market is not None:
        key = (*key, ("market", market))
    return (*key, ("capacity_screen", True)) if capacity_screen else key


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
    market: str | None = None,
    capacity_screen: bool = False,
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
        market: Package option ``None`` / ``"choice"`` / ``"choice+capacity"``
            (project choice and a shared market, ``engine.market``).
        capacity_screen: Also report every reform screened for practical
            capacity (``Result.capacity``, ``engine.capacity``); the benchmark
            numbers are unchanged.

    Returns:
        The result.
    """
    ctx = context if context is not None else _context(state)
    chosen = normalise_reforms(reforms)
    key = _key(ctx, chosen, overrides, seed, draws, cycle, market, capacity_screen)
    if use_cache and key in _CACHE:
        _STATS["hits"] += 1
        _CACHE.move_to_end(key)
        return _CACHE[key]
    _STATS["misses"] += 1
    result = compute(
        ctx, chosen, overrides, seed, draws, cycle, market=market, capacity_screen=capacity_screen
    )
    if use_cache:
        _CACHE[key] = result
        _BYTES[key] = result_nbytes(result)
        _trim_cache()
    return result


def cache_bytes_limit() -> int:
    """The cache's memory cap in bytes: :data:`CACHE_BYTES_ENV` if set, else the default."""
    raw = os.environ.get(CACHE_BYTES_ENV, "").strip()
    if not raw:
        return CACHE_BYTES_DEFAULT
    try:
        return max(0, int(float(raw)))
    except ValueError as exc:
        raise ValueError(f"{CACHE_BYTES_ENV} must be a number of bytes, got {raw!r}") from exc


def _trim_cache() -> None:
    """Drop the oldest results until at most CACHE_SIZE remain and their bytes fit the cap.

    The newest result is always kept, even when it alone is over the cap.
    """
    limit = cache_bytes_limit()
    while len(_CACHE) > 1 and (len(_CACHE) > CACHE_SIZE or sum(_BYTES.values()) > limit):
        old, _ = _CACHE.popitem(last=False)
        _BYTES.pop(old, None)


def result_nbytes(value: Any) -> int:
    """Estimate the memory a result holds: the bytes of the arrays it reaches.

    Walks dataclass fields, mappings and sequences and adds the bytes of every
    numpy array, counting each underlying buffer once (a view of another array
    adds nothing). The loaded state data and the ledger are shared by every
    result, so they are not counted. It is an estimate: small objects (records,
    text) are left out, because the draws are what make a result large.
    """
    seen: set[int] = set()
    total = 0
    stack: list[Any] = [value]
    while stack:
        v = stack.pop()
        if v is None or isinstance(v, str | bytes | int | float | bool | Record):
            continue
        if isinstance(v, StateContext | Ledger | type):
            continue
        if id(v) in seen:
            continue
        seen.add(id(v))
        if isinstance(v, np.ndarray):
            base = v
            while isinstance(base.base, np.ndarray):
                base = base.base
            if base is not v:
                if id(base) in seen:
                    continue
                seen.add(id(base))
            total += int(base.nbytes)
        elif type(v).__name__ in ("DataFrame", "Series") and hasattr(v, "memory_usage"):
            used = v.memory_usage(index=True, deep=False)
            total += int(used.sum()) if hasattr(used, "sum") else int(used)
        elif isinstance(v, Mapping):
            stack.extend(v.values())
        elif isinstance(v, list | tuple | set | frozenset):
            stack.extend(v)
        elif dataclasses.is_dataclass(v):
            stack.extend(getattr(v, f.name, None) for f in dataclasses.fields(v))
    return total


def clear_cache() -> None:
    """Empty the result cache and the loaded-context cache."""
    _CACHE.clear()
    _BYTES.clear()
    _CONTEXTS.clear()
    _STATS.update(hits=0, misses=0)


def cache_info() -> dict[str, int]:
    """Return cache hits, misses, size (results) and bytes (estimated) and the byte cap."""
    return {
        **_STATS,
        "size": len(_CACHE),
        "bytes": sum(_BYTES.values()),
        "bytes_limit": cache_bytes_limit(),
    }


def known_reforms() -> list[str]:
    """Return the registered reform keys."""
    return sorted(REGISTRY)
