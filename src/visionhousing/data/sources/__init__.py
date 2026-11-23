"""Readers for each published data source, one module per source.

Each module downloads its source through :mod:`visionhousing.data.http`, reads the
published file by its header names, and returns a tidy pandas table. This package
also holds the table of state codes that every source needs.
"""

from __future__ import annotations

__all__ = ["STATE_FIPS", "state_fips"]

STATE_FIPS: dict[str, str] = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
    "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
    "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
    "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56", "PR": "72",
}  # fmt: skip
"""Two-letter postal code to two-digit state code (FIPS), the 50 states, DC and Puerto Rico."""


def state_fips(state: str) -> str:
    """Return the two-digit state code for ``"PA"``, ``"pa"``, ``"42"`` or ``42``.

    Raises:
        ValueError: For a code that is not a state, DC or Puerto Rico.
    """
    text = str(state).strip().upper()
    if text in STATE_FIPS:
        return STATE_FIPS[text]
    if text.isdigit() and text.zfill(2) in STATE_FIPS.values():
        return text.zfill(2)
    raise ValueError(f"unknown state {state!r}; use a postal code such as 'PA' or a FIPS code")
