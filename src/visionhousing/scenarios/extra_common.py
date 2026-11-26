"""Shared helpers for the four extra (non-benchmark) scenarios.

Plain-language summary
----------------------
The extra scenarios (transit zoning with funded infrastructure, housing on
commercial land, adaptive reuse, starter-home lots and fee deferral with
affordability terms) read a few optional state-pack tables that the benchmark
reforms do not: water and sewer readiness (``infra_tract``), parcel land use
(``parcels_area``) and HUD income limits (``afford_income_limits``); the
practical capacity screen also reads hazards (``hazard_tract``). A state pack
may lack any of them (Testland ships all four, synthetic; the tests hide them to
check the no-table path), so each scenario says what it did without the table
instead of guessing.

This module holds:

- :func:`pack_table`: read one optional pack table once per state context (kept
  on ``StateContext.derived``; tests put a table there to stand in for a pack);
- :func:`ramp_years`: the yearly ramp and year shares of a scenario's take-up;
- :func:`in_service_weight`: the share of a draw's permits that fall on or after
  a service date (used by the funded-infrastructure scenario);
- :func:`per_draw_ratio`: a public cost per home, defined only in draws that add homes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.ledger.loader import ROOT
from visionhousing.scenarios.adu import ramp_sum, year_shares

__all__ = [
    "OPTIONAL_TABLES",
    "STATE_TABLES",
    "in_service_weight",
    "pack_table",
    "per_draw_ratio",
    "ramp_years",
    "table_sources",
]

OPTIONAL_TABLES: dict[str, dict[str, Any]] = {
    "infra_tract": {"dtype": {"geoid": str}},
    "hazard_tract": {"dtype": {"geoid": str}},
    "parcels_area": {"dtype": {"geoid": str, "county": str}},
    "afford_income_limits": {"dtype": {"county": str}},
}
"""Optional pack tables the extra scenarios (and the practical capacity screen,
``engine.capacity``) read, with their text columns."""

STATE_TABLES: dict[str, dict[str, Any]] = {
    "mbta_3a_capacity_tract": {"dtype": {"geoid": str, "county": str}},
    "ma_sewer_estimated_tract": {"dtype": {"geoid": str}},
}
"""Optional pack tables only some states have (Massachusetts: existing Section 3A
capacity, read by the TOD reform; MassDEP estimated sewer service, read by the
capacity screen). Read the same way as :data:`OPTIONAL_TABLES`; Testland has none."""


def _folder(state: Any) -> Path | None:
    """The folder a state context was read from, or ``None`` (an in-memory context)."""
    held = getattr(state, "derived", {}).get(("pack_folder",))
    if held is not None and Path(held).is_dir():
        return Path(held)
    src = str(getattr(state, "source", "") or "")
    for cand in (ROOT / src, Path(src)):
        if src and cand.is_dir():
            return cand
    try:
        from visionhousing.engine.context import pack_dir

        where = pack_dir(str(state.state))
    except Exception:
        return None
    return where if where.is_dir() else None


def pack_table(state: Any, name: str) -> pd.DataFrame | None:
    """One optional pack table, read once per state context; ``None`` when absent.

    Args:
        state: The ``StateContext``.
        name: Table name, one of :data:`OPTIONAL_TABLES`.

    Returns:
        The table, or ``None`` when the pack has no such file. A table placed in
        ``state.derived[("pack_table", name)]`` (tests) is returned as is.
    """
    spec = OPTIONAL_TABLES.get(name) or STATE_TABLES.get(name)
    if spec is None:
        raise KeyError(f"unknown optional table {name!r}")
    key = ("pack_table", name)
    store = state.derived
    if key in store:
        return store[key]
    table = None
    folder = _folder(state)
    if folder is not None:
        for cand in (folder / f"{name}.csv.gz", folder / f"{name}.csv"):
            if cand.is_file():
                table = pd.read_csv(cand, dtype=spec["dtype"])
                table.attrs["source_ids"] = _dictionary_sources(folder, name)
                break
    store[key] = table
    return table


def _dictionary_sources(folder: Path, name: str) -> tuple[str, ...]:
    """Source ids the pack's own data dictionary gives for a table's columns."""
    import json

    path = folder / "data_dictionary.json"
    if not path.is_file():
        return ()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    out: set[str] = set()
    for row in doc.get("columns") or []:
        if isinstance(row, Mapping) and row.get("table") == name:
            out |= {s.strip() for s in str(row.get("source") or "").split(",") if s.strip()}
    return tuple(sorted(out))


def table_sources(table: pd.DataFrame | None) -> tuple[str, ...]:
    """Source ids behind an optional pack table, from the pack, never from code.

    The readiness table names the official record behind each tract
    (``sewer_source``); those ids are used when any is filled in. Otherwise the
    ids are the ones the pack's data dictionary gives for the table's columns
    (``"synthetic"`` on Testland). A table built in memory (tests) has none.

    Args:
        table: A table from :func:`pack_table`, or ``None``.

    Returns:
        Sorted source ids (empty when the table is absent or names none).
    """
    if table is None:
        return ()
    if "sewer_source" in table.columns:
        named = {str(x).strip() for x in table["sewer_source"].dropna() if str(x).strip()}
        if named:
            return tuple(sorted(named))
    return tuple(table.attrs.get("source_ids", ()))


def ramp_years(
    v: Mapping[str, np.ndarray], first: str, full: str, delay: str | None, horizon: int
) -> tuple[np.ndarray, np.ndarray]:
    """The take-up ramp ``(draws, years)`` and each year's share of the five-year total."""
    d = v[delay] if delay and delay in v else 0.0
    ramp = ramp_sum(v[first], v[full], horizon, d)
    return ramp, year_shares(ramp)


def in_service_weight(
    year_share: np.ndarray, years: Sequence[int], service: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Share of each draw's permits issued on or after the service date.

    A year counts in full when service starts by January 1, in part when it
    starts during the year (``clip(year + 1 - service, 0, 1)``), and not at all
    before. ``service`` is a calendar date as a decimal year (2029.0 = January 1, 2029).

    Args:
        year_share: ``(draws, years)`` shares that add to 1 per draw.
        years: Calendar years of the window.
        service: In-service date per draw, ``(draws,)``.

    Returns:
        ``(weight (draws,), counted year shares (draws, years))``; the second
        holds each year's counted share (not renormalised).
    """
    yrs = np.asarray(years, dtype=float)[None, :]
    on = np.clip(yrs + 1.0 - np.asarray(service, dtype=float)[:, None], 0.0, 1.0)
    counted = np.asarray(year_share, dtype=float) * on
    return counted.sum(axis=1), counted


def per_draw_ratio(
    cost: np.ndarray, homes: np.ndarray, minimum_share: float
) -> tuple[np.ndarray | None, float]:
    """``cost / homes`` in the draws that add homes, when enough draws do.

    Returns:
        ``(ratios over the draws with homes > 0, or None when fewer than
        ``minimum_share`` of draws add any, share of draws with homes > 0)``.
    """
    homes = np.asarray(homes, dtype=float)
    ok = homes > 1e-9
    share = float(ok.mean()) if homes.size else 0.0
    if share < minimum_share or not ok.any():
        return None, share
    return np.asarray(cost, dtype=float)[ok] / homes[ok], share
