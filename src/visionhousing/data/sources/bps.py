"""Building Permits Survey: county and state annual files, read by header name.

Plain-language summary
----------------------
The Census Bureau's Building Permits Survey (BPS) counts the housing units that
local permit offices authorized each year. It publishes one comma-separated text
file per year for all counties (``co{YYYY}a.txt``) and one for all states
(``st{YYYY}a.txt``). This module downloads those files, reads them, and returns
one tidy table with a row per place, year and structure type.

How the files are laid out
--------------------------
- **Two header rows.** The first row names the group ("1-unit", "2-units",
  "3-4 units", "5+ units", and the same four again with "rep"). The second row
  names the measure inside each group: "Bldgs" (buildings), "Units", "Value".
  The two rows are joined and every column is found by its name, never by its
  position. A header the code does not recognise stops the run with a message.
- **Two layouts.** 1990-1998 files pad every cell with spaces, use Windows line
  ends and short group names ("34unit", "5-unit"). From 1999 the cells are
  unpadded and the names read "3-4 units", "5+ units". Both are handled by the
  same name rules. The state files have one identity column fewer (no county).
- **Estimated and reported figures.** The first four groups are the Bureau's
  estimates, which include *imputed* figures for permit offices that did not
  report. The four "rep" groups hold only what offices reported. This module
  keeps both. ``units_imputed`` is the difference, and ``imputed`` is true when
  any part of a row (buildings or units) was imputed.
- **Value** is the construction cost on the permit: dollars in the county files,
  thousands of dollars in the state files. Both are returned in dollars.
- **Rows that are not states** (the U.S. total, regions "R1"...,
  divisions "D1"...) are dropped from the state files.

Output
------
:func:`fetch_bps` returns the tidy table described in :data:`TIDY_COLUMNS`.
:func:`permits_county_year` turns it into the state-pack table (one row per
county and year, the Testland column names).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources import state_fips as to_state_fips

__all__ = [
    "FIRST_YEAR",
    "LATEST_YEAR",
    "STRUCTURES",
    "TIDY_COLUMNS",
    "county_state_check",
    "fetch_bps",
    "header_names",
    "parse_annual",
    "parse_county_annual",
    "parse_state_annual",
    "permits_county_year",
    "survey_year",
    "tidy",
]

FIRST_YEAR = 1990
"""First annual county file used."""
LATEST_YEAR = 2025
"""Latest published annual file (checked 2026-11-22)."""

STRUCTURES = ("1", "2", "3-4", "5+")
"""Structure types: units in buildings with 1, 2, 3 or 4, and 5 or more units."""

MEASURES = {"bldgs": "buildings", "units": "units", "value": "value"}

# Identity columns: the two header cells joined, lower case, single spaces.
_ID_NAMES = {
    "survey date": "survey_date",
    "fips state": "state_fips",
    "fips county": "county_fips",
    "region code": "region",
    "division code": "division",
    "county name": "name",
    "state name": "name",
}

TIDY_COLUMNS = (
    "level",  # "county" or "state"
    "geoid",  # 5-digit county code or 2-digit state code
    "state_fips",
    "name",
    "year",
    "structure",  # one of STRUCTURES
    "buildings",
    "units",
    "value_dollars",
    "buildings_reported",
    "units_reported",
    "value_reported_dollars",
    "units_imputed",
    "imputed",
)
"""Columns of the tidy table, in order."""


# --------------------------------------------------------------------------- header


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.rstrip("\r\n").split(",")]


def _structure(label: str) -> tuple[str, bool]:
    """Turn a group label such as "3-4 units rep" or "34unit" into ("3-4", reported?)."""
    text = label.lower().replace(" ", "")
    reported = text.endswith("rep")
    match = re.match(r"^(\d)(?:-?(\d))?(?:\+)?-?units?(?:rep)?$", text)
    if not match:
        raise ValueError(f"BPS header: unknown structure group {label!r}")
    first, second = match.groups()
    key = first + (second or "")
    names = {"1": "1", "2": "2", "34": "3-4", "5": "5+"}
    if key not in names:
        raise ValueError(f"BPS header: unknown structure group {label!r}")
    return names[key], reported


def header_names(row1: list[str], row2: list[str]) -> list[str]:
    """Join the two header rows into one column name per position.

    Identity columns get plain names (``state_fips``, ``county_fips``, ``name``...).
    Count columns get ``{est|rep}_{structure}_{buildings|units|value}``, for
    example ``est_3-4_units`` or ``rep_5+_value``.

    Raises:
        ValueError: When a header cell is not one this module knows, or a
            structure group is missing or repeated.
    """
    width = max(len(row1), len(row2))
    row1 = row1 + [""] * (width - len(row1))
    row2 = row2 + [""] * (width - len(row2))
    names: list[str] = []
    group: tuple[str, bool] | None = None
    for i in range(width):
        measure = row2[i].lower()
        if measure in MEASURES:
            if measure == "bldgs":
                label = next((c for c in row1[i : i + 3] if c), "")
                group = _structure(label)
            if group is None:
                raise ValueError(f"BPS header: column {i} ({row2[i]!r}) has no structure group")
            kind = "rep" if group[1] else "est"
            names.append(f"{kind}_{group[0]}_{MEASURES[measure]}")
            continue
        joined = " ".join(f"{row1[i]} {row2[i]}".lower().split())
        if not joined:
            names.append(f"_blank{i}")
            continue
        if joined not in _ID_NAMES:
            raise ValueError(f"BPS header: unknown column {joined!r} at position {i}")
        names.append(_ID_NAMES[joined])
    wanted = {f"{k}_{s}_{m}" for k in ("est", "rep") for s in STRUCTURES for m in MEASURES.values()}
    counts = [n for n in names if n in wanted]
    if set(counts) != wanted or len(counts) != len(wanted):
        missing = sorted(wanted - set(counts))
        raise ValueError(f"BPS header: count columns missing or repeated: {missing}")
    return names


def survey_year(code: str) -> int:
    """Read the year from a survey-date cell: ``"9099"`` -> 1990, ``"199999"`` -> 1999.

    Annual files mark the month as ``99``. Before 1999 the date is ``YY99``; from
    1999 it is ``YYYY99`` or just ``YYYY``.
    """
    code = code.strip()
    if len(code) == 6 and code.endswith("99"):
        return int(code[:4])
    if len(code) == 4 and code.endswith("99") and int(code[:2]) >= 80:
        return 1900 + int(code[:2])
    if len(code) == 4:
        return int(code)
    raise ValueError(f"BPS: survey date {code!r} is not an annual code")


# --------------------------------------------------------------------------- parse


def parse_annual(text: str, level: str, year: int) -> pd.DataFrame:
    """Read one annual county or state file into a wide table (one row per place).

    Args:
        text: The whole file as text.
        level: ``"county"`` or ``"state"``.
        year: The year in the file name; every row's survey date must agree.

    Returns:
        Columns ``level, geoid, state_fips, name, year`` and the 24 count columns
        from :func:`header_names`. Values are in dollars at both levels.
    """
    if level not in ("county", "state"):
        raise ValueError("level must be 'county' or 'state'")
    lines = [ln for ln in text.splitlines() if ln.strip().strip(",")]
    headers = [
        i for i, ln in enumerate(lines[:5]) if ln.lstrip().lower().startswith(("survey", "date"))
    ]
    if len(headers) < 2:
        raise ValueError("BPS: the two header rows were not found")
    names = header_names(_cells(lines[headers[0]]), _cells(lines[headers[1]]))
    rows = []
    for ln in lines[headers[1] + 1 :]:
        cells = _cells(ln)
        cells = (cells + [""] * len(names))[: len(names)]
        rows.append(cells)
    frame = pd.DataFrame(rows, columns=names)
    frame = frame[[c for c in names if not c.startswith("_blank")]]
    need = {"survey_date", "state_fips", "name"} | ({"county_fips"} if level == "county" else set())
    if not need <= set(frame.columns):
        raise ValueError(f"BPS {level} file: missing columns {sorted(need - set(frame.columns))}")
    frame = frame[frame["state_fips"].str.fullmatch(r"\d{1,2}")].copy()
    years = {survey_year(c) for c in frame["survey_date"]}
    if years != {year}:
        raise ValueError(f"BPS {level} file for {year}: survey dates say {sorted(years)}")
    frame["state_fips"] = frame["state_fips"].str.zfill(2)
    if level == "county":
        frame["county_fips"] = frame["county_fips"].str.zfill(3)
        frame["geoid"] = frame["state_fips"] + frame["county_fips"]
    else:
        frame["geoid"] = frame["state_fips"]
    counts = [c for c in frame.columns if c.startswith(("est_", "rep_"))]
    for col in counts:
        # A blank cell (seen in a few territory rows) stays missing; text is an error.
        frame[col] = pd.to_numeric(frame[col].replace("", None), errors="raise").astype("Int64")
    if level == "state":
        for col in counts:
            if col.endswith("_value"):
                frame[col] = frame[col] * 1000  # state files report thousands of dollars
    frame["level"] = level
    frame["year"] = year
    frame["name"] = frame["name"].str.strip()
    return frame[["level", "geoid", "state_fips", "name", "year", *counts]].reset_index(drop=True)


def parse_county_annual(text: str, year: int) -> pd.DataFrame:
    """Read one county annual file (``co{YYYY}a.txt``); see :func:`parse_annual`."""
    return parse_annual(text, "county", year)


def parse_state_annual(text: str, year: int) -> pd.DataFrame:
    """Read one state annual file (``st{YYYY}a.txt``); see :func:`parse_annual`."""
    return parse_annual(text, "state", year)


def tidy(wide: pd.DataFrame) -> pd.DataFrame:
    """Reshape a wide table from :func:`parse_annual` to one row per structure type."""
    parts = []
    for s in STRUCTURES:
        part = wide[["level", "geoid", "state_fips", "name", "year"]].copy()
        part["structure"] = s
        part["buildings"] = wide[f"est_{s}_buildings"]
        part["units"] = wide[f"est_{s}_units"]
        part["value_dollars"] = wide[f"est_{s}_value"]
        part["buildings_reported"] = wide[f"rep_{s}_buildings"]
        part["units_reported"] = wide[f"rep_{s}_units"]
        part["value_reported_dollars"] = wide[f"rep_{s}_value"]
        parts.append(part)
    out = pd.concat(parts, ignore_index=True)
    out["units_imputed"] = out["units"] - out["units_reported"]
    differs = (out["units_imputed"] != 0) | (out["buildings"] != out["buildings_reported"])
    out["imputed"] = differs.fillna(False).astype(bool)
    order = {s: i for i, s in enumerate(STRUCTURES)}
    out = out.sort_values(
        ["level", "geoid", "year", "structure"],
        key=lambda c: c.map(order) if c.name == "structure" else c,
    )
    return out[list(TIDY_COLUMNS)].reset_index(drop=True)


# --------------------------------------------------------------------------- fetch


def _get(client: HttpClient, source_id: str, year: int, dest: Path, manifest: Path | None) -> str:
    source = get_source(source_id)
    url = source.url(year=year)
    if not dest.exists():
        client.download(url, dest)
        if manifest is not None:
            mf.record(mf.entry_for_file(source_id, url, dest, str(year), root=mf.ROOT), manifest)
    return dest.read_text(encoding="latin-1")


def fetch_bps(
    state: str,
    years: Iterable[int],
    cache: Path,
    *,
    client: HttpClient | None = None,
    include_state_totals: bool = True,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Download (or reuse) the annual files and return the tidy permits table.

    Args:
        state: Postal code or FIPS code, for example ``"PA"``.
        years: Calendar years, for example ``range(1990, 2026)``.
        cache: Folder for raw files (``data/raw``). Files go in ``cache/bps/``
            and are kept: each is under 1 MB.
        client: The HTTP client; a default one is made when omitted.
        include_state_totals: Also read the state file and add its row for the
            state (``level == "state"``).
        manifest: Manifest file to record downloads in; ``None`` records nothing.

    Returns:
        The table in :data:`TIDY_COLUMNS`, county rows then state rows.
    """
    fips = to_state_fips(state)
    client = client or HttpClient()
    folder = Path(cache) / "bps"
    frames = []
    for year in sorted(set(int(y) for y in years)):
        if not FIRST_YEAR <= year <= LATEST_YEAR:
            raise ValueError(
                f"BPS annual files are used for {FIRST_YEAR}-{LATEST_YEAR}, not {year}"
            )
        text = _get(client, "bps_county_annual", year, folder / f"co{year}a.txt", manifest)
        wide = parse_county_annual(text, year)
        frames.append(wide[wide["state_fips"] == fips])
        if include_state_totals:
            text = _get(client, "bps_state_annual", year, folder / f"st{year}a.txt", manifest)
            wide = parse_state_annual(text, year)
            frames.append(wide[wide["state_fips"] == fips])
    if not frames:
        return pd.DataFrame(columns=list(TIDY_COLUMNS))
    return tidy(pd.concat(frames, ignore_index=True))


# --------------------------------------------------------------------------- outputs


def county_state_check(table: pd.DataFrame) -> pd.DataFrame:
    """Compare the sum of a state's county rows with its state row, by year and type.

    Returns:
        Columns ``year, structure, county_sum, state_total, difference``.
    """
    counties = table[table["level"] == "county"].groupby(["year", "structure"])["units"].sum()
    states = table[table["level"] == "state"].groupby(["year", "structure"])["units"].sum()
    out = pd.DataFrame({"county_sum": counties, "state_total": states}).reset_index()
    out["difference"] = out["county_sum"] - out["state_total"]
    return out


def permits_county_year(table: pd.DataFrame) -> pd.DataFrame:
    """Return the state-pack table: one row per county and year, Testland column names.

    Columns: ``county, year, units_1, units_2, units_3_4, units_5plus, imputed``.
    ``imputed`` is 1 when any structure type in that county-year was partly
    estimated by the Census Bureau.
    """
    counties = table[table["level"] == "county"]
    wide = counties.pivot_table(
        index=["geoid", "year"], columns="structure", values="units", aggfunc="sum"
    )
    names = {"1": "units_1", "2": "units_2", "3-4": "units_3_4", "5+": "units_5plus"}
    wide = wide.rename(columns=names)[list(names.values())]
    flag = counties.groupby(["geoid", "year"])["imputed"].any().astype(np.int64)
    wide["imputed"] = flag
    wide = wide.reset_index().rename(columns={"geoid": "county"})
    wide.columns.name = None
    return wide.astype({c: "Int64" for c in names.values()} | {"imputed": "int64", "year": "int64"})
