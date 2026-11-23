"""Turn the tidy permit table into arrays the baseline rules can work on.

The tidy table has one row per county and year, with the units authorized by
building permits in four structure types: ``units_1`` (single-unit buildings),
``units_2``, ``units_3_4`` and ``units_5plus``. Other columns (for example
``imputed``) are ignored.

``to_panel`` checks the table and returns a ``Panel``: a county list, a year
list, and a three-dimensional array of units (county, year, structure type). A
county-year that is not in the table is ``NaN`` (not reported), never zero, so a
missing report is not mistaken for a year with no building.

Everything here is a pure function: the table goes in, a new object comes out.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

STRUCTURE_TYPES: tuple[str, ...] = ("units_1", "units_2", "units_3_4", "units_5plus")
"""The four structure-type columns, in the order the arrays use."""

STRUCTURE_LABELS: dict[str, str] = {
    "units_1": "1-unit buildings",
    "units_2": "2-unit buildings",
    "units_3_4": "3-4-unit buildings",
    "units_5plus": "5+-unit buildings",
}
"""Plain-language names of the structure types."""


@dataclass(frozen=True)
class Panel:
    """Permit units arranged as county x year x structure type.

    Attributes:
        counties: County ids (text), sorted.
        years: Calendar years, ascending and consecutive.
        units: Array of shape (counties, years, 4); ``NaN`` where not reported.
    """

    counties: tuple[str, ...]
    years: np.ndarray
    units: np.ndarray

    def totals(self) -> np.ndarray:
        """Return units summed over structure types, shape (counties, years).

        A county-year is ``NaN`` only if every structure type is missing.
        """
        present = ~np.isnan(self.units).all(axis=2)
        summed = np.nansum(self.units, axis=2)
        return np.where(present, summed, np.nan)

    def state_series(self) -> np.ndarray:
        """Return state units by year and structure type, shape (1, years, 4).

        Counties that did not report a year are left out of that year's sum;
        a year no county reported is ``NaN``.
        """
        present = ~np.isnan(self.units).all(axis=0)
        summed = np.nansum(self.units, axis=0)
        return np.where(present, summed, np.nan)[np.newaxis, :, :]


def to_panel(table: pd.DataFrame) -> Panel:
    """Check the tidy permit table and arrange it as a ``Panel``.

    Args:
        table: Columns ``county``, ``year`` and the four structure types.

    Returns:
        The panel. Years run from the table's first to its last year without gaps.

    Raises:
        ValueError: A column is missing, a county-year appears twice, or a unit
            count is negative.
    """
    missing = [c for c in ("county", "year", *STRUCTURE_TYPES) if c not in table.columns]
    if missing:
        raise ValueError(f"permit table is missing columns {missing}")
    if table.empty:
        raise ValueError("permit table has no rows")
    data = table[["county", "year", *STRUCTURE_TYPES]].copy()
    data["county"] = data["county"].astype(str)
    data["year"] = data["year"].astype(int)
    if data.duplicated(["county", "year"]).any():
        raise ValueError("permit table lists a county-year more than once")
    values = data[list(STRUCTURE_TYPES)].to_numpy(dtype=float)
    if (values[~np.isnan(values)] < 0).any():
        raise ValueError("permit table has a negative unit count")
    counties = tuple(sorted(data["county"].unique()))
    years = np.arange(data["year"].min(), data["year"].max() + 1)
    units = np.full((len(counties), len(years), len(STRUCTURE_TYPES)), np.nan)
    c_index = {c: i for i, c in enumerate(counties)}
    rows = data["county"].map(c_index).to_numpy()
    cols = data["year"].to_numpy() - years[0]
    units[rows, cols, :] = values
    return Panel(counties=counties, years=years, units=units)
