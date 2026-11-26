"""The comparison bench: one prepared run, reused for every comparison.

Plain-language summary
----------------------
Every comparison (interactions, dials, the matrix, ranking stability) asks the
same kind of question: "what if one thing were different, with everything else
held exactly the same?". To answer it fairly the engine must use the *same*
simulated futures each time: the same seed, the same baseline position in each
draw, the same draws of every shared input. These are **common random
numbers**: two runs then differ only because of the thing that was changed.

The bench prepares a run once (``engine.core.prepare``: state data, ledger,
baseline projection, the shared samples) and keeps it. It then:

- runs each reform alone through its registered adapter, exactly as
  ``engine.run`` does, and caches the result (``alone``);
- builds the package of any set of reforms (``package``) by the engine's own
  package rules (``engine.packages``): sum of the reforms alone, minus the
  missing-middle units lost to ADU lots, plus each interaction (the fee cut and
  the parking reform entering the ADU and 2-6 unit pro formas). Every rerun the
  rules need is cached too, so the 11 packages of the interaction accounting
  share their work.

The package of all five reforms equals ``engine.run``'s package draw by draw
(``tests/test_comparisons.py`` checks it), so the comparisons and the headline
always agree.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from visionhousing.engine import core
from visionhousing.engine.adapters import OPTION_ADU_LOTS
from visionhousing.engine.context import StateContext
from visionhousing.engine.packages import interaction_options, lot_overlap, overlap_pair
from visionhousing.engine.registry import Draws, ReformResult, RunContext, get_reform
from visionhousing.ledger import DEFAULT_DRAWS

__all__ = ["BENCHMARKS", "Bench", "PackageParts"]

BENCHMARKS = ("adu", "missing_middle", "tod", "parking", "fees")
"""The five benchmark reforms, in the Handbook's order."""


@dataclass(frozen=True)
class PackageParts:
    """One package, by county and draw.

    Attributes:
        keys: Reforms in the package.
        combined: Combined added units, ``(counties, draws)``.
        parts: Sum of the reforms run alone.
        overlap: Missing-middle units lost to ADU lots (zeros unless both are on).
        interactions: Interaction name (``"fees -> adu"``) to its units.
        ids: Ledger ids behind the package.
    """

    keys: tuple[str, ...]
    combined: np.ndarray = field(repr=False)
    parts: np.ndarray = field(repr=False)
    overlap: np.ndarray = field(repr=False)
    interactions: Mapping[str, np.ndarray] = field(repr=False)
    ids: tuple[str, ...] = ()


@dataclass
class Bench:
    """A prepared run and its caches (see the module summary).

    Attributes:
        context: The state context.
        run_context: The run context (baseline projected; ``cache`` holds
            transit features worked out once).
        draws: The run's draws (ledger after overrides, shared samples, seed).
        base_county: Baseline units by county and draw.
        base_state: Baseline units by draw.
        cycle: The baseline's housing-cycle choice.
    """

    context: StateContext
    run_context: RunContext
    draws: Draws
    base_county: np.ndarray = field(repr=False)
    base_state: np.ndarray = field(repr=False)
    cycle: str = "typical"
    _alone: dict[str, ReformResult] = field(default_factory=dict, repr=False)
    _reruns: dict[tuple, ReformResult] = field(default_factory=dict, repr=False)
    _packages: dict[tuple[str, ...], PackageParts] = field(default_factory=dict, repr=False)
    stats: dict[str, int] = field(default_factory=lambda: {"adapter_runs": 0, "cache_hits": 0})

    @classmethod
    def build(
        cls,
        state: str | StateContext,
        *,
        overrides: Mapping[str, Any] | None = None,
        seed: int = 0,
        draws: int = DEFAULT_DRAWS,
        cycle: str = "typical",
    ) -> Bench:
        """Prepare a bench for a state code or a loaded context."""
        ctx = state if isinstance(state, StateContext) else core._context(str(state))
        run_context, shared = core.prepare(ctx, overrides, seed=seed, draws=draws, cycle=cycle)
        base_c = core._baseline_county_draws(
            run_context.baseline, run_context.county_ids, shared.baseline_scale
        )
        return cls(ctx, run_context, shared, base_c, base_c.sum(axis=0), cycle)

    # ------------------------------------------------------------ basics

    @property
    def county_ids(self) -> tuple[str, ...]:
        """County order of every county array."""
        return self.run_context.county_ids

    @property
    def n(self) -> int:
        """Number of draws."""
        return self.draws.n

    def _adapter(self, key: str, options: Mapping[str, Any]) -> ReformResult:
        self.stats["adapter_runs"] += 1
        return get_reform(key).adapter(self.run_context, self.draws, options)

    def alone(
        self, key: str, options: Mapping[str, Any] | None = None, *, remember: bool | None = None
    ) -> ReformResult:
        """One reform run alone; ``options`` as for ``engine.run``.

        The benchmark run (no options) is kept for the bench's life; a run with
        options is kept only when ``remember`` is true, so a sweep of policy
        levels on a large state does not hold every level's tract arrays at once.
        """
        opts = dict(options or {})
        memo = f"{key}|{json.dumps(opts, sort_keys=True, default=str)}"
        if memo in self._alone:
            self.stats["cache_hits"] += 1
            return self._alone[memo]
        res = self._adapter(key, opts)
        if remember or (remember is None and not opts):
            self._alone[memo] = res
        return res

    def county(self, res: ReformResult) -> np.ndarray:
        """A reform result's added units by county and draw (the run's county order)."""
        return res.county_draws(self.county_ids)

    def alone_county(self, key: str, options: Mapping[str, Any] | None = None) -> np.ndarray:
        """``alone`` summed to counties."""
        return self.county(self.alone(key, options))

    # ------------------------------------------------------------ packages

    def _rerun(self, key: str, lots_from: str | None, changes: tuple[str, ...]) -> ReformResult:
        """A package rerun of ``key``, cached.

        Without the lots ``lots_from`` (an ADU reform) builds on, and/or with the
        named reforms' savings in its pro forma.
        """
        memo = (key, lots_from, changes)
        if memo in self._reruns:
            self.stats["cache_hits"] += 1
            return self._reruns[memo]
        options: dict[str, Any] = {}
        if lots_from is not None:
            adu = self.alone(lots_from)
            if adu.detail is None:
                raise ValueError("the ADU result has no tract stages to share lots with")
            options[OPTION_ADU_LOTS] = adu.detail.stages["built"]
        steps = interaction_options(key, (*changes, key), self.draws.ledger)
        for name in changes:
            options.update(steps[f"{name} -> {key}"])
        res = self._adapter(key, options)
        self._reruns[memo] = replace(res, detail=None)  # keep draws and ledger, drop stages
        return res

    def package(self, keys: Iterable[str]) -> PackageParts:
        """The package of ``keys`` by the engine's package rules (cached).

        The same steps as ``engine.core.compute``: overlap first (missing middle
        rerun without the ADU lots), then each interaction in order, the
        reform rerun with the other reform's saving in its pro forma, the
        difference taken draw by draw.
        """
        keys = tuple(dict.fromkeys(keys))
        memo = tuple(sorted(keys))
        if memo in self._packages:
            return self._packages[memo]
        zero = np.zeros((len(self.county_ids), self.n))
        parts = zero.copy()
        ids: set[str] = set()
        for k in keys:
            res = self.alone(k)
            parts = parts + self.county(res)
            ids |= set(res.headline.assumption_ids)
        overlap = zero.copy()
        pair = overlap_pair(keys)
        start: dict[str, tuple[np.ndarray, ReformResult]] = {}
        lots_from: str | None = None
        if pair is not None:
            lots_from, mm_key = pair
            with_lots = self._rerun(mm_key, lots_from, ())
            w = self.county(with_lots)
            overlap = lot_overlap(self.alone_county(mm_key), w)
            start[mm_key] = (w, with_lots)
            ids |= {*with_lots.headline.assumption_ids, "missing_middle.adu_lot_overlap_share"}
        extras: dict[str, np.ndarray] = {}
        for key in keys:
            steps = interaction_options(key, keys, self.draws.ledger)
            if not steps:
                continue
            spec = get_reform(key)
            lots = lots_from if key in start else None
            before, alone = start[key] if lots else (self.alone_county(key), self.alone(key))
            applied: list[str] = []
            for name, change in steps.items():
                if alone.ledger is not None and core._changes_nothing(
                    spec.family, alone.ledger, change
                ):
                    extras[name] = np.zeros_like(before)
                    ids |= set(core._interaction_ids(alone, name))
                    continue
                applied.append(name.split(" -> ", 1)[0])
                res = self._rerun(key, lots, tuple(applied))
                after = self.county(res)
                extras[name] = after - before
                ids |= set(core._interaction_ids(res, name))
                before = after
        inter = sum(extras.values(), zero.copy())
        out = PackageParts(
            keys=keys,
            combined=parts - overlap + inter,
            parts=parts,
            overlap=overlap,
            interactions=extras,
            ids=tuple(sorted(ids)),
        )
        self._packages[memo] = out
        return out
