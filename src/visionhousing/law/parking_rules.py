"""Verified parking minimums per municipality, read-only, for the parking reform.

Plain-language summary
----------------------
The municipal rules table (``muni_zoning_rules``, built from
``assumptions/law/municipal_rules.yaml``) stores two parking numbers per
municipality: ``parking_min_per_unit_sf`` (off-street spaces required per
single-family home) and ``parking_min_per_unit_mf`` (per apartment). Most
municipalities have not been read, so both are blank.

:func:`verified_parking_minimums` keeps the rows the parking reform may use:

- the row is **read**: ``status: verified`` and in force on the run's as-of
  date, with the same test the hooks and coverage tables use
  (:func:`visionhousing.law.inforce.read_flags`). A rule whose effective date
  is after the as-of date is not law yet, so it is left out. For example
  Pittsburgh's Ordinance 31 (no minimum, only maximums) counts from its
  effective date, October 29, 2026, and not before;
- the row gives at least one of the two numbers.

For each kept row it returns the minimum that applies to an **apartment
building** (5+ homes) and to a **2-4 unit building**, each taken from the
first field in a given order that has a number (the order is a setting in
``assumptions/cost_reforms.yaml``, ``verified_minimums``). A blank stays blank:
the parking reform then uses its labelled ledger default for that building
type. Blank never means zero.

The module reads and filters; it changes no law table and holds no number.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

import pandas as pd

from visionhousing.law.inforce import read_flags

__all__ = ["PARKING_FIELDS", "verified_parking_minimums"]

PARKING_FIELDS = ("parking_min_per_unit_sf", "parking_min_per_unit_mf")
"""The two parking columns of the municipal rules table."""

_COLUMNS = [
    "geoid",
    "county",
    "name",
    "min_apartment",
    "min_small",
    "doc_id",
    "section_cite",
    "effective_date",
    "reviewed_on",
]


def _first(row: dict[str, Any], fields: Sequence[str]) -> float | None:
    for f in fields:
        x = pd.to_numeric(row.get(f), errors="coerce")
        if not pd.isna(x):
            return float(x)
    return None


def verified_parking_minimums(
    rules: pd.DataFrame | None,
    *,
    apartment_fields: Sequence[str] = ("parking_min_per_unit_mf",),
    small_fields: Sequence[str] = ("parking_min_per_unit_mf", "parking_min_per_unit_sf"),
    as_of: date | str | None = None,
    hooks: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Read, in-force parking minimums per municipality.

    Args:
        rules: The ``muni_zoning_rules`` table (``geoid`` = 5-digit county +
            5-digit county subdivision, ``status``, the date columns and the
            two parking columns). ``None`` gives an empty table.
        apartment_fields: Columns tried in order for the minimum per apartment
            (5+ unit building).
        small_fields: Columns tried in order for the minimum per home in a
            2-4 unit building.
        as_of: The run's as-of date (default: ``as_of`` in
            ``assumptions/outcome.yaml``).
        hooks: ``assumptions/law/hooks.yaml`` (for the in-force rule; read
            when not given).

    Returns:
        One row per kept municipality: ``geoid``, ``county``, ``name``,
        ``min_apartment`` and ``min_small`` (spaces per home; blank where the
        reading gives no number for that building type), ``doc_id``,
        ``section_cite``, ``effective_date``, ``reviewed_on``. Sorted by geoid.

    Raises:
        ValueError: A kept minimum is negative.
    """
    if rules is None or rules.empty:
        return pd.DataFrame(columns=_COLUMNS)
    r = rules.copy()
    r["geoid"] = r["geoid"].astype(str)
    read, _ = read_flags(r, as_of, hooks)
    rows = []
    for row in r[read].to_dict("records"):
        apt, small = _first(row, apartment_fields), _first(row, small_fields)
        if apt is None and small is None:
            continue
        if (apt is not None and apt < 0) or (small is not None and small < 0):
            raise ValueError(f"negative parking minimum for {row['geoid']}")
        rows.append(
            {
                "geoid": row["geoid"],
                "county": row["geoid"][:5],
                "name": row.get("name", ""),
                "min_apartment": apt,
                "min_small": small,
                "doc_id": row.get("doc_id", ""),
                "section_cite": row.get("section_cite", ""),
                "effective_date": row.get("effective_date", ""),
                "reviewed_on": row.get("reviewed_on", ""),
            }
        )
    out = pd.DataFrame(rows, columns=_COLUMNS)
    for c in ("min_apartment", "min_small"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.sort_values("geoid").reset_index(drop=True)
