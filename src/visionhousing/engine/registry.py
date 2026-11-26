"""The reform registry: one common shape for every reform the engine can run.

Plain-language summary
----------------------
Each reform (ADUs, fee cut, parking reform, missing middle and
transit-oriented density) is written in its own module under ``scenarios/``,
with its own inputs. The engine does not need to know those details. Each
reform *registers* one function with the same signature::

    adapter(context, draws, options) -> ReformResult

- ``context`` (:class:`RunContext`): the state's data, the baseline already
  projected, and the county order every array uses.
- ``draws`` (:class:`Draws`): the run's ledger (after overrides), the number of
  Monte Carlo draws, the seed, the shared parameter samples, and the baseline's
  per-draw scale.
- ``options``: reform-specific choices, for example the fee view
  (``{"view": "state"}``) or extra ledger changes a package applies.

It returns a :class:`ReformResult`: added units by tract or county and draw,
the reform's explanation steps (records with formulas), its other records, the
ledger ids behind the headline, and plain notes.

**Shared draws.** The ledger draws each parameter from its own random stream,
fixed by the seed and the parameter id (``ledger.sampling.stream``). So when two
reforms read the same parameter in the same run they see the *same* values,
draw by draw, and a parameter's values do not depend on which other parameters
were drawn. ``Draws.values`` holds those samples once for the engine's own use
(importance ranking); each reform module draws from the same streams.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.baseline import BaselineResult
from visionhousing.engine.context import StateContext
from visionhousing.engine.records import Record
from visionhousing.ledger import Ledger

__all__ = [
    "REGISTRY",
    "Draws",
    "ReformResult",
    "ReformSpec",
    "RunContext",
    "get_reform",
    "register",
]


@dataclass(frozen=True)
class RunContext:
    """The state's data plus the projected baseline, for one run.

    Attributes:
        state: The loaded state context.
        baseline: The projected baseline (records by county and state).
        baseline_types: Five-year baseline units by county (index) and structure
            type (columns), scaled to each county's P50.
        county_ids: County ids, in the order every county array uses.
        cache: Scratch space for values derived once per run context (for
            example the transit-rich tract flags). Not part of any result.
    """

    state: StateContext
    baseline: BaselineResult
    baseline_types: pd.DataFrame
    county_ids: tuple[str, ...]
    cache: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class Draws:
    """The run's ledger and its Monte Carlo draws.

    Attributes:
        ledger: The ledger after the run's overrides.
        n: Number of draws.
        seed: The run's seed.
        values: Parameter id to its ``n`` draws (the shared samples).
        baseline_scale: Baseline multiplier per county and draw, shape
            ``(counties, n)``; 1 means the baseline P50.
        overrides: The user's ledger overrides for this run (already applied
            to ``ledger``; adapters re-apply them after a scenario's own
            overrides, so the user's choice wins).
    """

    ledger: Ledger
    n: int
    seed: int
    values: Mapping[str, np.ndarray] = field(repr=False)
    baseline_scale: np.ndarray = field(repr=False)
    overrides: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReformResult:
    """What one reform adapter returns.

    Attributes:
        reform: Registry key, for example ``"adu"``.
        title: Plain name shown to users.
        level: ``"tract"`` or ``"county"``: the geography of ``added``.
        geoids: Ids along the first axis of ``added``.
        county_of: County id of each row of ``added`` (equal to ``geoids`` at
            county level).
        added: Added units authorized by building permits 2027-2031 (against the
            baseline), shape ``(geographies, draws)``.
        headline: The reform module's own state record for the same quantity.
        steps: Explanation steps in order (the funnel), each a record.
        records: Other records the reform produced, by name.
        assumption_ids: Ledger ids behind the headline number.
        notes: Plain statements a reader needs (limits, draft inputs).
        detail: The reform module's full result, for the explanation layer.
        ledger: The ledger the reform actually used (scenario and user
            overrides applied). Sampling it with the run's seed gives the exact
            input draws behind ``added``.
        county_notes: County id to ``(text, source ids)``: a plain statement
            added to that county's record formula, with the sources it cites
            (for example a value taken from read local law).
        year_share: Each year's share of the five-year total, per draw, shape
            ``(draws, years)``; rows add to 1. The reform's adoption curve
            (start delay and ramp); ``None``: the engine works it out from the
            reform's ramp entries (``engine.outcome``).
        net_stock: Net change in the housing stock by geography and draw (same
            shape as ``added``), or ``None`` when no home is removed (it equals ``added``).
        removed: Existing homes demolished by geography and draw, or ``None`` (none).
        baseline_on_sites: Baseline authorizations on the redeveloped sites by
            geography and draw, or ``None`` (no site is redeveloped).
        status: What is known about the inputs, by stage, JSON-ready (the TOD
            transit status ladder, :mod:`visionhousing.features.transit_status`),
            with ``statewide.shown`` false when the statewide range must not be
            shown; ``None`` when the reform has none.
    """

    reform: str
    title: str
    level: str
    geoids: tuple[str, ...]
    county_of: tuple[str, ...]
    added: np.ndarray = field(repr=False)
    headline: Record
    steps: tuple[Record, ...] = ()
    records: Mapping[str, Record] = field(default_factory=dict)
    assumption_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    detail: Any = field(default=None, repr=False, compare=False)
    ledger: Ledger | None = field(default=None, repr=False, compare=False)
    county_notes: Mapping[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    year_share: np.ndarray | None = field(default=None, repr=False, compare=False)
    net_stock: np.ndarray | None = field(default=None, repr=False, compare=False)
    removed: np.ndarray | None = field(default=None, repr=False, compare=False)
    baseline_on_sites: np.ndarray | None = field(default=None, repr=False, compare=False)
    status: Mapping[str, Any] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Check that the shapes and labels agree."""
        if self.level not in ("tract", "county"):
            raise ValueError("level must be 'tract' or 'county'")
        arr = np.asarray(self.added, dtype=float)
        if arr.ndim != 2 or arr.shape[0] != len(self.geoids):
            raise ValueError("added must have shape (len(geoids), draws)")
        if len(self.county_of) != len(self.geoids):
            raise ValueError("county_of needs one county per geography")
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"reform '{self.reform}' returned a non-finite number of units")

    def county_sum(self, arr: np.ndarray, county_ids: tuple[str, ...]) -> np.ndarray:
        """Sum any ``(geographies, draws)`` array of this result to counties, in the order given."""
        index = {c: i for i, c in enumerate(county_ids)}
        rows = np.array([index[c] for c in self.county_of], dtype=int)
        out = np.zeros((len(county_ids), np.asarray(arr).shape[1]))
        np.add.at(out, rows, np.asarray(arr, dtype=float))
        return out

    def county_draws(self, county_ids: tuple[str, ...]) -> np.ndarray:
        """Sum ``added`` to counties, in the order given.

        Args:
            county_ids: County ids wanted (rows of the output).

        Returns:
            Array of shape ``(counties, draws)``; a county with no rows is 0.

        Raises:
            ValueError: A row belongs to a county not in ``county_ids``.
        """
        index = {c: i for i, c in enumerate(county_ids)}
        unknown = sorted({c for c in self.county_of if c not in index})
        if unknown:
            raise ValueError(f"reform '{self.reform}' has units in unknown counties {unknown}")
        rows = np.array([index[c] for c in self.county_of], dtype=int)
        out = np.zeros((len(county_ids), self.added.shape[1]))
        np.add.at(out, rows, self.added)
        return out


Adapter = Callable[[RunContext, Draws, Mapping[str, Any]], ReformResult]
"""The common reform signature: ``(context, draws, options) -> ReformResult``."""


@dataclass(frozen=True)
class ReformSpec:
    """One registered reform.

    Attributes:
        key: Registry key (``"adu"``, ``"fees"``, ...).
        title: Plain name.
        adapter: The function that runs it.
        family: Reforms in the same family compete for the same lots and cannot
            be combined in one package (for example the benchmark ADU reform and
            an ADU bill).
        uses: Ledger ``used_by`` name of the reform (for listing its inputs).
        excludes: Other families this reform reaches the same lots or projects as
            (an extra scenario and the benchmark it builds on); a package may hold
            only one of them, so no home is counted twice.
    """

    key: str
    title: str
    adapter: Adapter
    family: str
    uses: str
    excludes: tuple[str, ...] = ()


REGISTRY: dict[str, ReformSpec] = {}
"""Registry key to its spec. Filled by ``engine.adapters`` on import."""


def register(
    key: str,
    title: str,
    *,
    family: str | None = None,
    uses: str | None = None,
    excludes: tuple[str, ...] = (),
):
    """Register a reform adapter under ``key`` (a decorator).

    Args:
        key: Registry key.
        title: Plain name shown to users.
        family: Family name (defaults to the key).
        uses: Ledger ``used_by`` name (defaults to the family).
        excludes: Families that may not share a package with this reform.

    Returns:
        The decorator; it returns the adapter unchanged.

    Raises:
        ValueError: The key is already registered.
    """

    def wrap(adapter: Adapter) -> Adapter:
        if key in REGISTRY:
            raise ValueError(f"reform '{key}' is already registered")
        fam = family or key
        REGISTRY[key] = ReformSpec(
            key, title, _screenable(adapter, fam), fam, uses or fam, tuple(excludes)
        )
        return adapter

    return wrap


def _screenable(adapter: Adapter, family: str) -> Adapter:
    """The adapter, plus the practical capacity screen when its options ask for it.

    With the option ``capacity_screen`` (or a variant that sets it) the reform's
    added homes are multiplied by the practical capacity multiplier
    (``engine.capacity.screen_result``); otherwise the adapter's result is
    returned unchanged. Every caller (the run, the package reruns, the
    readouts, the market option) goes through this, so a screened variant is
    screened everywhere.
    """

    @functools.wraps(adapter)
    def run(context: RunContext, draws: Draws, options: Mapping[str, Any]) -> ReformResult:
        res = adapter(context, draws, options)
        from visionhousing.engine.capacity import screen_result, screen_wanted

        if screen_wanted(family, options):
            return screen_result(res, context, draws, family)
        return res

    return run


def get_reform(key: str) -> ReformSpec:
    """Return the registered reform ``key``.

    Raises:
        KeyError: No such reform; the message lists the known keys.
    """
    try:
        return REGISTRY[key]
    except KeyError:
        raise KeyError(f"unknown reform '{key}'; known: {sorted(REGISTRY)}") from None
