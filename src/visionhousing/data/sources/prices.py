"""House prices and mortgage rates: FHFA house price indexes and the Freddie Mac rate survey.

Plain-language summary
----------------------
**House price index (HPI).** The Federal Housing Finance Agency (FHFA) measures
how home prices change by tracking homes that sold more than once (a "repeat
sales" index) using mortgages bought or guaranteed by Fannie Mae and Freddie
Mac. It publishes annual indexes for every county (an Excel ``.xlsx`` file) and
for census tracts (a CSV file). An index level only means something compared
with the same area's other years: a tract at 280 is not dearer than one at 200.
So the pack keeps **changes**, which can be compared across areas:

- ``hpi_change_5yr_pct`` = index in the latest year / index five years earlier
  - 1, as a percentage. Same for 10 years.
- ``hpi_annual_change_pct``: the published change in the latest year.

Where FHFA has too few sales the cell is blank, and it stays blank here.
The tract file uses 2020 census tracts (checked: every Pennsylvania tract code
in it is a 2020 tract code).

**Reading the ``.xlsx`` without a spreadsheet library.** An ``.xlsx`` file is a
zip of XML files. The text of every text cell is stored once in
``xl/sharedStrings.xml``; the sheet (``xl/worksheets/sheet1.xml``) holds cells
with a reference such as ``C7`` and either a number or the position of a shared
text. This module reads both with Python's own ``zipfile`` and
``xml.etree.ElementTree``, finds the header row by its "FIPS code" cell, and
reads columns by header name.

**Mortgage rates.** Freddie Mac's Primary Mortgage Market Survey (PMMS) is a
weekly national average of offered rates on 30-year and 15-year fixed-rate
mortgages. The pack keeps the average for each calendar year and the number of
weeks behind it (the current year is partial). The model's rate is a ledger
parameter; this table is the evidence for its range.

Downloads
---------
- The national tract CSV (90 MB) is streamed and only the state's rows are
  written to ``data/raw/prices/``. The national file is never stored.
- The county ``.xlsx`` (5 MB) is downloaded, the state's rows are saved as a
  small CSV, and the ``.xlsx`` is deleted.
- The PMMS CSV (0.1 MB) is kept as published.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.data.sources.acs import stream_filter

__all__ = [
    "HPI_COLUMNS",
    "fetch_hpi_county",
    "fetch_hpi_tract",
    "fetch_pmms",
    "hpi_changes",
    "pmms_annual",
    "read_hpi_county",
    "read_hpi_tract",
    "read_pmms",
    "xlsx_rows",
]

HPI_COLUMNS = ("geoid", "year", "annual_change_pct", "hpi", "hpi_1990_base", "hpi_2000_base")
"""Long-form HPI columns returned by both readers."""

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_MAX_XML_BYTES = 400_000_000  # refuse absurd members (a zip bomb) before parsing

_COUNTY_HEADERS = {
    "fips code": "geoid",
    "year": "year",
    "annual change (%)": "annual_change_pct",
    "hpi": "hpi",
    "hpi with 1990 base": "hpi_1990_base",
    "hpi with 2000 base": "hpi_2000_base",
}
_TRACT_HEADERS = {
    "tract": "geoid",
    "year": "year",
    "annual_change": "annual_change_pct",
    "hpi": "hpi",
    "hpi1990": "hpi_1990_base",
    "hpi2000": "hpi_2000_base",
}


# --------------------------------------------------------------------------- xlsx


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref)
    if not letters:
        raise ValueError(f"bad cell reference {ref!r}")
    n = 0
    for ch in letters.group(0):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _member(zf: zipfile.ZipFile, name: str) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > _MAX_XML_BYTES:
        raise ValueError(f"{name} is {info.file_size:,} bytes; refusing to parse")
    return zf.read(name)


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(_member(zf, "xl/sharedStrings.xml"))  # noqa: S314 - see xlsx_rows
    return ["".join(t.text or "" for t in si.iter(f"{_NS}t")) for si in root.iter(f"{_NS}si")]


def _first_sheet(zf: zipfile.ZipFile) -> str:
    book = ET.fromstring(_member(zf, "xl/workbook.xml"))  # noqa: S314 - see xlsx_rows
    sheet = book.find(f"{_NS}sheets/{_NS}sheet")
    if sheet is None:
        raise ValueError("workbook lists no sheets")
    rel_id = sheet.get(f"{_REL_NS}id")
    rels = ET.fromstring(_member(zf, "xl/_rels/workbook.xml.rels"))  # noqa: S314
    for rel in rels:
        if rel.get("Id") == rel_id:
            target = rel.get("Target", "").lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise ValueError("first sheet's file not found in the workbook relations")


def xlsx_rows(source: bytes | str | Path) -> Iterator[list[str | float | None]]:
    """Yield the first sheet's rows as lists of cell values (text, number or ``None``).

    Only the standard library is used. The XML comes from a federal agency's
    published file; Python's XML parser does not fetch external resources, and
    members over 400 MB are refused before parsing.
    """
    data = Path(source).read_bytes() if isinstance(source, str | Path) else bytes(source)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        strings = _shared_strings(zf)
        sheet = _member(zf, _first_sheet(zf))
    for _, row in ET.iterparse(io.BytesIO(sheet), events=("end",)):  # noqa: S314
        if row.tag != f"{_NS}row":
            continue
        cells: dict[int, str | float | None] = {}
        for c in row.iter(f"{_NS}c"):
            kind = c.get("t")
            v = c.find(f"{_NS}v")
            if kind == "inlineStr":
                t = c.find(f"{_NS}is")
                value: str | float | None = (
                    "".join(x.text or "" for x in t.iter(f"{_NS}t")) if t is not None else None
                )
            elif v is None or v.text is None:
                value = None
            elif kind == "s":
                value = strings[int(v.text)]
            elif kind in ("str", "e", "b"):
                value = v.text
            else:
                value = float(v.text)
            cells[_col_index(c.get("r", "A"))] = value
        row.clear()
        if cells:
            yield [cells.get(i) for i in range(max(cells) + 1)]


def read_hpi_county(source: bytes | str | Path, state_fips: str | None = None) -> pd.DataFrame:
    """Read FHFA's annual county HPI ``.xlsx`` (or a saved state CSV) into long form.

    Args:
        source: The ``.xlsx`` bytes or path, or a ``.csv`` written by
            :func:`fetch_hpi_county`.
        state_fips: When given, keep only that state's counties.

    Returns:
        Columns :data:`HPI_COLUMNS`, sorted by ``geoid`` and ``year``; a "." or
        blank in the file is a missing value.
    """
    if isinstance(source, str | Path) and str(source).endswith(".csv"):
        frame = pd.read_csv(source, dtype={"geoid": str})
        return _finish(frame, state_fips)
    header: dict[int, str] | None = None
    rows = []
    for cells in xlsx_rows(source):
        if header is None:
            names = [str(c).strip().lower() if c is not None else "" for c in cells]
            if "fips code" in names:
                header = {
                    i: _COUNTY_HEADERS[n] for i, n in enumerate(names) if n in _COUNTY_HEADERS
                }
                missing = set(_COUNTY_HEADERS.values()) - set(header.values())
                if missing:
                    raise ValueError(f"county HPI header lacks {sorted(missing)}")
            continue
        row = {name: cells[i] if i < len(cells) else None for i, name in header.items()}
        if row["geoid"] is None:
            continue
        rows.append(row)
    if header is None:
        raise ValueError("county HPI sheet has no 'FIPS code' header row")
    return _finish(pd.DataFrame(rows, columns=list(HPI_COLUMNS)), state_fips)


def _finish(frame: pd.DataFrame, state_fips: str | None, width: int = 5) -> pd.DataFrame:
    frame = frame.copy()
    ids = frame["geoid"].map(lambda v: f"{int(v):0{width}d}" if isinstance(v, float) else str(v))
    frame["geoid"] = ids.str.strip().str.zfill(width)
    if state_fips is not None:
        frame = frame[frame["geoid"].str.startswith(state_fips)]
    for name in HPI_COLUMNS[2:]:
        frame[name] = pd.to_numeric(frame[name].replace({".": None, "": None}), errors="coerce")
    frame["year"] = pd.to_numeric(frame["year"], errors="raise").astype("int64")
    if frame.duplicated(["geoid", "year"]).any():
        raise ValueError("HPI table repeats an area and year")
    out = frame[list(HPI_COLUMNS)].sort_values(["geoid", "year"]).reset_index(drop=True)
    return out


def read_hpi_tract(source: str | Path, state_fips: str | None = None) -> pd.DataFrame:
    """Read FHFA's annual tract HPI CSV (national or a state extract) into long form."""
    frame = pd.read_csv(source, dtype={"tract": str}, na_values=[".", ""])
    missing = [h for h in _TRACT_HEADERS if h not in frame.columns]
    if missing:
        raise ValueError(f"tract HPI file lacks {missing}")
    frame = frame.rename(columns=_TRACT_HEADERS)
    return _finish(frame, state_fips, width=11)


def hpi_changes(hpi: pd.DataFrame, year: int | None = None) -> pd.DataFrame:
    """Return per area: the latest year, its annual change, and the 5- and 10-year changes.

    Args:
        hpi: Long table from :func:`read_hpi_county` or :func:`read_hpi_tract`.
        year: The year to measure to; the latest year in the table when omitted.

    Returns:
        Columns ``geoid, hpi_year, hpi_annual_change_pct, hpi_change_5yr_pct,
        hpi_change_10yr_pct``. A change is blank when either end is missing.
    """
    year = int(hpi["year"].max()) if year is None else int(year)
    wide = hpi.pivot(index="geoid", columns="year", values="hpi")
    change = hpi[hpi["year"] == year].set_index("geoid")["annual_change_pct"]

    def pct(span: int) -> pd.Series:
        if year not in wide or year - span not in wide:
            return pd.Series(np.nan, index=wide.index)
        return ((wide[year] / wide[year - span] - 1.0) * 100).round(2)

    out = pd.DataFrame(
        {
            "hpi_year": year,
            "hpi_annual_change_pct": change.reindex(wide.index).round(2),
            "hpi_change_5yr_pct": pct(5),
            "hpi_change_10yr_pct": pct(10),
        },
        index=wide.index,
    )
    out.index.name = "geoid"
    return out.reset_index()


# --------------------------------------------------------------------------- PMMS


def read_pmms(source: str | Path) -> pd.DataFrame:
    """Read the PMMS weekly history: ``date``, ``rate_30yr_pct``, ``rate_15yr_pct``."""
    frame = pd.read_csv(source, dtype=str, skipinitialspace=True)
    frame.columns = [c.strip().lower() for c in frame.columns]
    for need in ("date", "pmms30", "pmms15"):
        if need not in frame.columns:
            raise ValueError(f"PMMS file lacks the {need} column")
    frame = frame[frame["date"].notna() & (frame["date"].str.strip() != "")]
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(frame["date"].str.strip(), format="%m/%d/%Y"),
            "rate_30yr_pct": pd.to_numeric(frame["pmms30"].str.strip(), errors="coerce"),
            "rate_15yr_pct": pd.to_numeric(frame["pmms15"].str.strip(), errors="coerce"),
        }
    )
    return out.sort_values("date").reset_index(drop=True)


def pmms_annual(weekly: pd.DataFrame) -> pd.DataFrame:
    """Average the weekly rates by calendar year.

    Returns:
        ``year, rate_30yr_pct, rate_15yr_pct, weeks_30yr, weeks_15yr`` (the
        number of weeks with a published rate; the 15-year series starts in 1991).
    """
    years = weekly["date"].dt.year.rename("year")
    grouped = weekly.groupby(years)
    out = pd.DataFrame(
        {
            "rate_30yr_pct": grouped["rate_30yr_pct"].mean().round(3),
            "rate_15yr_pct": grouped["rate_15yr_pct"].mean().round(3),
            "weeks_30yr": grouped["rate_30yr_pct"].count().astype("int64"),
            "weeks_15yr": grouped["rate_15yr_pct"].count().astype("int64"),
        }
    )
    return out.reset_index()


# --------------------------------------------------------------------------- fetch


def _record(source_id: str, url: str, path: Path, vintage: str, manifest: Path | None) -> None:
    if manifest is not None:
        mf.record(mf.entry_for_file(source_id, url, path, vintage, root=mf.ROOT), manifest)


def fetch_hpi_tract(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Stream the national tract HPI file, keep the state's rows, and read them."""
    fips = to_state_fips(state)
    dest = Path(cache) / "prices" / f"hpi_at_tract_{fips}.csv"
    if not dest.exists():
        url = get_source("fhfa_hpi_tract").url()

        def make_keep(header: bytes):
            if not header.startswith(b"tract,"):
                raise ValueError("tract HPI file does not start with a 'tract' column")
            prefix = fips.encode("ascii")
            return lambda line: line.startswith(prefix)

        stream_filter(client or HttpClient(), url, dest, make_keep)
        _record("fhfa_hpi_tract", url, dest, "annual, latest release", manifest)
    return read_hpi_tract(dest, fips)


def fetch_hpi_county(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Download the county HPI ``.xlsx``, save the state's rows as CSV, delete the ``.xlsx``."""
    fips = to_state_fips(state)
    dest = Path(cache) / "prices" / f"hpi_at_county_{fips}.csv"
    if not dest.exists():
        url = get_source("fhfa_hpi_county").url()
        xlsx = dest.with_name("hpi_at_county.xlsx")
        try:
            (client or HttpClient()).download(url, xlsx)
            table = read_hpi_county(xlsx, fips)
        finally:
            xlsx.unlink(missing_ok=True)
        if table.empty:
            raise ValueError(f"county HPI file has no rows for state {fips}")
        part = dest.with_name(dest.name + ".part")
        table.to_csv(part, index=False, lineterminator="\n")
        part.replace(dest)
        _record("fhfa_hpi_county", url, dest, "annual, latest release", manifest)
    return read_hpi_county(dest, fips)


def fetch_pmms(
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Download (or reuse) the PMMS weekly history and return it read."""
    dest = Path(cache) / "prices" / "PMMS_history.csv"
    if not dest.exists():
        url = get_source("freddie_pmms").url()
        (client or HttpClient()).download(url, dest)
        _record("freddie_pmms", url, dest, "weekly, to the latest week", manifest)
    return read_pmms(dest)
