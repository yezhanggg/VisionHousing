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
        added: Net added permitted units 2027-2031, shape ``(geographies, draws)``.
        headline: The reform module's own state record for the same quantity.
        steps: Explanation steps in order (the funnel), each a record.
        records: Other records the reform produced, by name.
        assumption_ids: Ledger ids behind the headline number.
        notes: Plain statements a reader needs (limits, draft inputs).
        detail: The reform module's full result, for the explanation layer.
        ledger: The ledger the reform actually used (scenario and user
            overrides applied). Sampling it with the run's seed gives the exact
            input draws behind ``added``.
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
    """

    key: str
    title: str
    adapter: Adapter
    family: str
    uses: str


REGISTRY: dict[str, ReformSpec] = {}
"""Registry key to its spec. Filled by ``engine.adapters`` on import."""


def register(key: str, title: str, *, family: str | None = None, uses: str | None = None):
    """Register a reform adapter under ``key`` (a decorator).

    Args:
        key: Registry key.
        title: Plain name shown to users.
        family: Family name (defaults to the key).
        uses: Ledger ``used_by`` name (defaults to the family).

    Returns:
        The decorator; it returns the adapter unchanged.

    Raises:
        ValueError: The key is already registered.
    """

    def wrap(adapter: Adapter) -> Adapter:
        if key in REGISTRY:
            raise ValueError(f"reform '{key}' is already registered")
        fam = family or key
        REGISTRY[key] = ReformSpec(key, title, adapter, fam, uses or fam)
        return adapter

    return wrap


def get_reform(key: str) -> ReformSpec:
    """Return the registered reform ``key``.

    Raises:
        KeyError: No such reform; the message lists the known keys.
    """
    try:
        return REGISTRY[key]
    except KeyError:
        raise KeyError(f"unknown reform '{key}'; known: {sorted(REGISTRY)}") from None
