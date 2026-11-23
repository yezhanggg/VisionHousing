"""Stable ids for every record in an engine result.

Plain-language summary
----------------------
An "explain this number" popover, a cite in a sentence and a row in a CSV all
need to name one number in a run. The id of a record is simply its path in the
result's JSON (``Result.to_dict()``), with dots between the parts:

========================================  ==========================================
id                                        record
========================================  ==========================================
``baseline``                              state baseline, 2027-2031
``baseline_counties.42003``               one county's baseline
``reform_results.adu.added``              units the ADU reform adds, state
``reform_results.adu.pct_change``         the same as a percent of the baseline
``reform_results.adu.steps.2``            the third step of the ADU funnel
``reform_results.adu.records.per_100k``   another record of the reform
``reform_results.adu.counties.42003``     units added in one county
``package.combined``                      all reforms together (also ``sum_of_parts``,
                                          ``overlap``, ``interaction``, ``pct_change``)
``package.interactions.fees -> adu``      one interaction
``package.counties.42003``                the package in one county
``total``                                 baseline plus the package
========================================  ==========================================

Because the id is the path, anyone holding the JSON can find the number
without this code (:func:`resolve`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from visionhousing.engine.records import Record

__all__ = ["record_index", "resolve"]


def record_index(result: Any) -> dict[str, Record]:
    """Every record in a result, keyed by its id, in a fixed order.

    Args:
        result: An engine ``Result``.

    Returns:
        Id to record: baseline, counties, each reform (headline, percent,
        steps, other records, counties), the package, and the total.
    """
    out: dict[str, Record] = {"baseline": result.baseline}
    for c, r in result.baseline_counties.items():
        out[f"baseline_counties.{c}"] = r
    for key, o in result.reforms.items():
        base = f"reform_results.{key}"
        out[f"{base}.added"] = o.added
        out[f"{base}.pct_change"] = o.pct_change
        for i, s in enumerate(o.steps):
            out[f"{base}.steps.{i}"] = s
        for name, r in o.records.items():
            out[f"{base}.records.{name}"] = r
        for c, r in o.counties.items():
            out[f"{base}.counties.{c}"] = r
    p = result.package
    if p is not None:
        for name in ("sum_of_parts", "overlap", "interaction", "combined", "pct_change"):
            out[f"package.{name}"] = getattr(p, name)
        for name, r in p.interactions.items():
            out[f"package.interactions.{name}"] = r
        for c, r in p.counties.items():
            out[f"package.counties.{c}"] = r
    if result.total is not None:
        out["total"] = result.total
    return out


def resolve(data: Mapping[str, Any], ref: str) -> Any:
    """Follow a dotted id into a result's ``to_dict()`` output.

    Args:
        data: ``Result.to_dict()`` (or any nested mapping and list).
        ref: Dotted path; list items are numbered from 0.

    Returns:
        The value at that path.

    Raises:
        KeyError: The path does not exist.
    """
    node: Any = data
    for part in ref.split("."):
        if isinstance(node, Mapping):
            if part not in node:
                raise KeyError(f"'{ref}': no '{part}'")
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError) as err:
                raise KeyError(f"'{ref}': no item '{part}'") from err
        else:
            raise KeyError(f"'{ref}': cannot look inside a {type(node).__name__}")
    return node
