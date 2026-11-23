"""The engine: runs reforms against the baseline and adds up the results.

Holds the single entry point ``run(state, reforms, overrides, seed)``, the Monte
Carlo sampling that produces likely ranges, the reform registry and its thin
adapters, the rules for combining reforms into packages without double
counting, the roll-up from tracts to counties to the state, the result cache
and the three run hashes. See ``docs/modules/engine.md``.

Names are loaded on first use, so ``visionhousing.engine.records`` can be
imported by the reform modules without loading the whole engine.
"""

from __future__ import annotations

from typing import Any

__all__ = ["REGISTRY", "Result", "cache_info", "clear_cache", "compute", "load_context", "run"]


def __getattr__(name: str) -> Any:
    if name in ("Result", "run", "compute", "cache_info", "clear_cache"):
        from visionhousing.engine import core as _run

        return getattr(_run, name)
    if name == "load_context":
        from visionhousing.engine.context import load_context

        return load_context
    if name == "REGISTRY":
        from visionhousing.engine import core as _run  # registers the reforms

        return _run.REGISTRY
    raise AttributeError(f"module 'visionhousing.engine' has no attribute {name!r}")
