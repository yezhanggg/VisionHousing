"""American Community Survey 5-year estimates for one state's tracts and counties.

Plain-language summary
----------------------
The American Community Survey (ACS) is the Census Bureau's yearly household
survey. Its 5-year estimates (here 2020-2024) pool five years of answers so that
small areas such as census tracts have numbers. Every estimate comes with a
*margin of error* (MOE): the published 90% range is estimate +/- MOE.

Two ways to get the numbers
---------------------------
- **Default: the summary files, no key needed.** The Bureau publishes one
  national pipe-delimited (``|``) file per table, for example
  ``acsdt5y2024-b25024.dat``. Column ``GEO_ID`` names the place
  (``1400000US42001030101`` is a Pennsylvania tract; ``0500000US42001`` a
  county; ``0400000US42`` the state). Then come ``B25024_E001``,
  ``B25024_M001``, ... (estimate and margin of each cell). These files are big
  (up to 200 MB), so they are **streamed**: read a piece at a time, and only the
  rows of the chosen state's state, county and tract rows are written to disk.
  The national file never sits on disk or in memory.
- **Optional: the Census Data API**, used only when asked for and when
  ``CENSUS_API_KEY`` is set in the environment. At most 50 variables per call,
  so the variables are sent in batches.

Both ways give the same columns, named the API way: ``B25024_002E`` (estimate)
and ``B25024_002M`` (margin of error).

Cleaning
--------
- **Negative values are codes, not data** (for example ``-666666666`` means "no
  estimate: too few sample cases" and ``-222222222`` means "margin not
  computed"). Every negative value becomes missing (NaN).
- **Labels are checked.** The table shells (the Bureau's list of what every cell
  means) are read for this vintage. The value and rent brackets are checked with
  :func:`visionhousing.features.brackets.check_labels`; every other cell used is
  checked against :data:`EXPECTED_LABELS`. A mismatch stops the run, because the
  Bureau sometimes renumbers cells between vintages.

Turning these raw columns into the state-pack features is done by
:mod:`visionhousing.features.acs`.

Profile tables
--------------
:func:`fetch_acs_profile` fetches the extra tables behind the place profile's
charts (:data:`PROFILE_TABLES`: race and ethnicity, commute mode, household
size, education, income brackets, vacancy status, rent as a share of income).
By default (``method="auto"``) it uses the API when ``CENSUS_API_KEY`` is set,
because that moves a few hundred KB instead of streaming several national
files, and the summary files otherwise. Both give the same numbers. The
result is saved once as ``acs5_profile_{fips}.csv.gz``, so the pack is built
from the same file whichever path fetched it. The cells are collapsed into a
few bands each by :mod:`visionhousing.features.acs_bands`.
"""

from __future__ import annotations

import gzip
import json
import os
from collections.abc import Callable, Iterable, Mapping
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import (
    _TRANSIENT,
    CHUNK,
    KEY_ENV,
    FetchError,
    HttpClient,
    IncompleteDownloadError,
    check_free_space,
)
from visionhousing.data.registry import get_source
from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.features import brackets as br

__all__ = [
    "API_BATCH",
    "EXPECTED_LABELS",
    "LEVELS",
    "PROFILE_LABELS",
    "PROFILE_TABLES",
    "TABLES",
    "VINTAGE",
    "api_variables",
    "check_profile_labels",
    "check_shell_labels",
    "choose_method",
    "fetch_acs",
    "fetch_acs_profile",
    "geo_prefixes",
    "parse_api_rows",
    "parse_geos",
    "parse_summary_table",
    "parse_table_shells",
    "profile_variables",
    "stream_filter",
]

VINTAGE = 2024
"""The ACS 5-year vintage used (2020-2024). The registry URLs are pinned to it."""

TABLES = (
    "B01001",  # sex by age (share aged 65+)
    "B01003",  # total population
    "B19013",  # median household income
    "B25001",  # housing units
    "B25002",  # occupancy status (vacancy)
    "B25003",  # tenure (owner / renter)
    "B25024",  # units in structure
    "B25031",  # median gross rent by bedrooms
    "B25034",  # year structure built
    "B25063",  # gross rent brackets
    "B25064",  # median gross rent
    "B25075",  # value brackets
    "B25077",  # median value
)
"""The tables fetched, in alphabetical order."""

LEVELS = {"040": "state", "050": "county", "140": "tract"}
"""Summary levels kept: the code in ``GEO_ID`` and its plain name."""

API_BATCH = 50
"""Most variables the Census Data API accepts in one call (GEO_ID and NAME included)."""

_AGE_65 = {
    20: "65 and 66 years",
    21: "67 to 69 years",
    22: "70 to 74 years",
    23: "75 to 79 years",
    24: "80 to 84 years",
    25: "85 years and over",
}

EXPECTED_LABELS: dict[str, str] = {
    "B01001_001E": "Total",
    "B01001_002E": "Male",
    "B01001_026E": "Female",
    **{f"B01001_{k:03d}E": v for k, v in _AGE_65.items()},
    **{f"B01001_{k + 24:03d}E": v for k, v in _AGE_65.items()},
    "B01003_001E": "Total",
    "B19013_001E": (
        "Median household income in the past 12 months (in 2024 inflation-adjusted dollars)"
    ),
    "B25001_001E": "Total",
    "B25002_001E": "Total",
    "B25002_002E": "Occupied",
    "B25002_003E": "Vacant",
    "B25003_001E": "Total",
    "B25003_002E": "Owner occupied",
    "B25003_003E": "Renter occupied",
    "B25024_001E": "Total",
    "B25024_002E": "1, detached",
    "B25024_003E": "1, attached",
    "B25024_004E": "2",
    "B25024_005E": "3 or 4",
    "B25024_006E": "5 to 9",
    "B25024_007E": "10 to 19",
    "B25024_008E": "20 to 49",
    "B25024_009E": "50 or more",
    "B25024_010E": "Mobile home",
    "B25024_011E": "Boat, RV, van, etc.",
    "B25031_001E": "Total",
    "B25031_002E": "No bedroom",
    "B25031_003E": "1 bedroom",
    "B25031_004E": "2 bedrooms",
    "B25031_005E": "3 bedrooms",
    "B25031_006E": "4 bedrooms",
    "B25031_007E": "5 or more bedrooms",
    "B25034_001E": "Total",
    "B25034_002E": "Built 2020 or later",
    "B25034_003E": "Built 2010 to 2019",
    "B25034_004E": "Built 2000 to 2009",
    "B25034_005E": "Built 1990 to 1999",
    "B25034_006E": "Built 1980 to 1989",
    "B25034_007E": "Built 1970 to 1979",
    "B25034_008E": "Built 1960 to 1969",
    "B25034_009E": "Built 1950 to 1959",
    "B25034_010E": "Built 1940 to 1949",
    "B25034_011E": "Built 1939 or earlier",
    "B25064_001E": "Median gross rent",
    "B25077_001E": "Median value (dollars)",
}
"""Expected label (last part, trailing colon dropped) of every non-bracket cell used."""

_CELLS = {
    "B01001": 49, "B01003": 1, "B19013": 1, "B25001": 1, "B25002": 3, "B25003": 3,
    "B25024": 11, "B25031": 7, "B25034": 11, "B25063": 27, "B25064": 1, "B25075": 27,
    "B25077": 1,
}  # fmt: skip


def api_variables() -> list[str]:
    """Every estimate and margin variable fetched, API style (``B25024_002E``, ``..._002M``)."""
    out = []
    for table in TABLES:
        for i in range(1, _CELLS[table] + 1):
            out += [f"{table}_{i:03d}E", f"{table}_{i:03d}M"]
    return out


def geo_prefixes(fips: str) -> tuple[str, ...]:
    """``GEO_ID`` beginnings of a state's own row, its counties and its tracts."""
    return tuple(f"{code}0000US{fips}" for code in LEVELS)


# --------------------------------------------------------------------------- streaming


def _geo_id_keep(prefixes: tuple[str, ...]) -> Callable[[bytes], Callable[[bytes], bool]]:
    """Build a row filter: keep rows whose ``GEO_ID`` column starts with a prefix."""
    wanted = tuple(p.encode("ascii") for p in prefixes)

    def make(header: bytes) -> Callable[[bytes], bool]:
        names = header.rstrip(b"\r\n").split(b"|")
        if b"GEO_ID" not in names:
            raise FetchError("ACS file has no GEO_ID column")
        idx = names.index(b"GEO_ID")
        if idx == 0:
            return lambda line: line.startswith(wanted)

        def keep(line: bytes) -> bool:
            parts = line.split(b"|", idx + 1)
            return len(parts) > idx and parts[idx].startswith(wanted)

        return keep

    return make


def _first_field_keep(values: Iterable[str]) -> Callable[[bytes], Callable[[bytes], bool]]:
    wanted = {v.encode("ascii") for v in values}

    def make(header: bytes) -> Callable[[bytes], bool]:
        return lambda line: line.split(b"|", 1)[0] in wanted

    return make


def _stream_once(client: HttpClient, url: str, part: Path, make_keep) -> tuple[int, int]:
    resp = client.open(url)
    seen = kept = 0
    keep = None
    rest = b""
    try:
        with part.open("wb") as out:
            for block in iter(lambda: resp.read(CHUNK), b""):
                seen += len(block)
                lines = (rest + block).split(b"\n")
                rest = lines.pop()
                for line in lines:
                    if keep is None:
                        keep = make_keep(line)
                        out.write(line + b"\n")
                    elif line.strip() and keep(line):
                        out.write(line + b"\n")
                        kept += 1
            if rest.strip():
                if keep is None:
                    keep = make_keep(rest)
                    out.write(rest + b"\n")
                elif keep(rest):
                    out.write(rest + b"\n")
                    kept += 1
        length = resp.headers.get("Content-Length") if resp.headers is not None else None
        if length and length.isdigit() and seen != int(length):
            raise IncompleteDownloadError(f"{url}: got {seen} bytes, server said {length}")
    finally:
        resp.close()
    return kept, seen


def stream_filter(
    client: HttpClient,
    url: str,
    dest: Path,
    make_keep: Callable[[bytes], Callable[[bytes], bool]],
) -> tuple[int, int]:
    """Stream ``url`` and write only its header and the rows a filter keeps to ``dest``.

    ``make_keep`` gets the header line and returns the row test. The kept rows go
    to ``dest.part``, which is renamed to ``dest`` only when the whole file was
    read and its length checked. A dropped connection restarts the file from the
    beginning, with the client's waits between attempts.

    Returns:
        ``(rows kept, bytes streamed)``.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    check_free_space(dest.parent, 0, min_gb=client.min_free_gb, free_bytes=client.free_bytes)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(client.retries + 1):
        try:
            result = _stream_once(client, url, part, make_keep)
            part.replace(dest)
            return result
        except (IncompleteDownloadError, *_TRANSIENT) as err:
            part.unlink(missing_ok=True)
            if attempt >= client.retries:
                raise FetchError(f"gave up streaming {url}: {err}") from None
            wait = min(client.backoff * (2**attempt), client.max_wait)
            client.waits.append(wait)
            client.sleep(wait)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
    raise AssertionError("unreachable")  # pragma: no cover


# --------------------------------------------------------------------------- parsers


def _clean_numbers(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    columns = list(columns)
    values = frame[columns].apply(pd.to_numeric, errors="coerce").astype("float64")
    values = values.where(values >= 0, np.nan)  # negative = special code
    keep = frame[[c for c in frame.columns if c not in set(columns)]]
    return pd.concat([keep, values], axis=1)[list(frame.columns)]


def parse_summary_table(source: str | Path) -> pd.DataFrame:
    """Read a (filtered) summary-file table into API-style columns.

    Args:
        source: A path, or the file's text.

    Returns:
        ``GEO_ID`` plus ``{TABLE}_{cell}E`` and ``{TABLE}_{cell}M`` as floats, with
        every negative special code turned into NaN.
    """
    text = Path(source).read_text(encoding="latin-1") if isinstance(source, Path) else source
    frame = pd.read_csv(StringIO(text), sep="|", dtype=str, keep_default_na=False)
    renamed = {}
    for col in frame.columns:
        if col == "GEO_ID":
            continue
        table, _, cell = col.partition("_")
        if len(cell) != 4 or cell[0] not in "EM" or not cell[1:].isdigit():
            raise ValueError(f"ACS summary file: unexpected column {col!r}")
        renamed[col] = f"{table}_{cell[1:]}{cell[0]}"
    frame = frame.rename(columns=renamed)
    return _clean_numbers(frame, renamed.values())


def parse_geos(source: str | Path) -> pd.DataFrame:
    """Read the (filtered) geography list into ``GEO_ID, level, geoid, name``.

    ``geoid`` is the part after ``US``: 2 digits for a state, 5 for a county, 11
    for a tract. Rows of summary levels other than :data:`LEVELS` are dropped.
    """
    text = Path(source).read_text(encoding="latin-1") if isinstance(source, Path) else source
    frame = pd.read_csv(StringIO(text), sep="|", dtype=str, keep_default_na=False)
    frame = frame[frame["SUMLEVEL"].isin(list(LEVELS)) & (frame["COMPONENT"] == "00")].copy()
    frame["level"] = frame["SUMLEVEL"].map(LEVELS)
    frame["geoid"] = frame["GEO_ID"].str.split("US", n=1).str[1]
    frame = frame.rename(columns={"NAME": "name"})
    return frame[["GEO_ID", "level", "geoid", "name"]].reset_index(drop=True)


def parse_table_shells(source: str | Path, tables: Iterable[str] = TABLES) -> dict[str, str]:
    """Read the table shells into ``{"B25024_002E": "1, detached", ...}`` for ``tables``."""
    text = Path(source).read_text(encoding="latin-1") if isinstance(source, Path) else source
    frame = pd.read_csv(StringIO(text), sep="|", dtype=str, keep_default_na=False)
    frame = frame[frame["Table ID"].isin(list(tables)) & (frame["Unique ID"] != "")]
    return {f"{uid}E": label for uid, label in zip(frame["Unique ID"], frame["Label"], strict=True)}


def check_shell_labels(labels: Mapping[str, str], vintage: int = VINTAGE) -> list[str]:
    """Check every cell used against its expected label; return the problems (empty = fine).

    The value (B25075) and rent (B25063) brackets go through
    :func:`visionhousing.features.brackets.check_labels`; the other cells are
    compared with :data:`EXPECTED_LABELS` (last ``!!`` part, trailing colon dropped).
    """
    problems = []
    for table in (br.VALUE, br.RENT):
        subset = {k: v for k, v in labels.items() if k.startswith(f"{table.table}_")}
        problems += br.check_labels(subset, table, vintage)
    for var, want in EXPECTED_LABELS.items():
        got = labels.get(var)
        if got is None:
            problems.append(f"{var}: missing, expected {want!r}")
            continue
        last = got.split("!!")[-1].strip().rstrip(":").strip()
        if last != want:
            problems.append(f"{var}: label {got!r}, expected {want!r}")
    return problems


def parse_api_rows(rows: list[list[str]]) -> pd.DataFrame:
    """Turn one Census Data API answer (a header row then data rows) into a table.

    Estimate and margin columns become floats, negative special codes become NaN,
    and the API's geography columns (``state``, ``county``, ``tract``) are dropped.
    """
    if not rows:
        raise ValueError("empty API answer")
    frame = pd.DataFrame(rows[1:], columns=rows[0])
    values = [c for c in frame.columns if c[:1] == "B" and c[-1:] in ("E", "M")]
    frame = frame[[c for c in ("GEO_ID", "NAME") if c in frame.columns] + values]
    return _clean_numbers(frame.copy(), values)


# --------------------------------------------------------------------------- fetch


def _record(source_id: str, url: str, path: Path, manifest: Path | None) -> None:
    if manifest is not None:
        mf.record(
            mf.entry_for_file(source_id, url, path, f"{VINTAGE} 5-year", root=mf.ROOT), manifest
        )


def _fetch_labels(client: HttpClient, folder: Path, manifest: Path | None) -> dict[str, str]:
    url = get_source("acs5_table_shells").url()
    dest = folder / f"ACS{VINTAGE}5YR_Table_Shells_subset.txt"
    if not dest.exists():
        stream_filter(client, url, dest, _first_field_keep(TABLES))
        _record("acs5_table_shells", url, dest, manifest)
    return parse_table_shells(dest)


def _fetch_summary(
    client: HttpClient,
    fips: str,
    folder: Path,
    manifest: Path | None,
    tables: tuple[str, ...] = TABLES,
) -> pd.DataFrame:
    keep = _geo_id_keep(geo_prefixes(fips))
    url = get_source("acs5_geos").url()
    dest = folder / f"Geos{VINTAGE}5YR_{fips}.txt"
    if not dest.exists():
        stream_filter(client, url, dest, keep)
        _record("acs5_geos", url, dest, manifest)
    parts = [parse_geos(dest).set_index("GEO_ID")]
    source = get_source("acs5_summary_file")
    for table in tables:
        url = source.url(table=table.lower())
        dest = folder / f"acsdt5y{VINTAGE}-{table.lower()}_{fips}.dat"
        if not dest.exists():
            stream_filter(client, url, dest, keep)
            _record("acs5_summary_file", url, dest, manifest)
        parts.append(parse_summary_table(dest).set_index("GEO_ID"))
    out = pd.concat(parts, axis=1, join="outer").loc[parts[0].index]
    out.index.name = "GEO_ID"
    return out.reset_index()


def _api_get(client: HttpClient, url: str, params: dict[str, str]) -> list[list[str]]:
    if client.cache_dir is not None:
        path = client.cached(url, params, suffix=".json").path
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(client.get_bytes(url, params).decode("utf-8"))


def _fetch_api(
    client: HttpClient, fips: str, key: str, variables: list[str] | None = None
) -> pd.DataFrame:
    url = get_source("acs5_api").url()
    variables = api_variables() if variables is None else variables
    size = API_BATCH - 2  # GEO_ID and NAME are sent with every batch
    batches = [variables[i : i + size] for i in range(0, len(variables), size)]
    levels = (
        ("state", {"for": f"state:{fips}"}),
        ("county", {"for": "county:*", "in": f"state:{fips}"}),
        ("tract", {"for": "tract:*", "in": f"state:{fips}"}),
    )
    frames = []
    for level, where in levels:
        parts = []
        for batch in batches:
            params = {"get": ",".join(["GEO_ID", "NAME", *batch]), **where, "key": key}
            part = parse_api_rows(_api_get(client, url, params)).set_index("GEO_ID")
            parts.append(part if not parts else part.drop(columns="NAME"))
        merged = pd.concat(parts, axis=1).rename(columns={"NAME": "name"})
        merged.index.name = "GEO_ID"
        ids = merged.index.to_series()
        head = pd.DataFrame(
            {"level": level, "geoid": ids.str.split("US", n=1).str[1]}, index=merged.index
        )
        frames.append(pd.concat([head, merged], axis=1).reset_index())
    return pd.concat(frames, ignore_index=True)


def fetch_acs(
    state: str,
    vintage: int,
    cache: Path,
    *,
    client: HttpClient | None = None,
    method: str = "summary",
    env: Mapping[str, str] | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Fetch every table in :data:`TABLES` for a state's own row, counties and tracts.

    Args:
        state: Postal code or FIPS code, for example ``"PA"``.
        vintage: ACS 5-year end year. Only :data:`VINTAGE` (2024) is supported.
        cache: Folder for raw files (``data/raw``). The state's filtered files go
            in ``cache/acs/{vintage}/``; each is a few hundred KB.
        client: The HTTP client; a default one is made when omitted.
        method: ``"summary"`` (default, no key) or ``"api"`` (needs
            ``CENSUS_API_KEY`` in the environment).
        env: Environment to read the key from; ``os.environ`` when omitted.
        manifest: Manifest file to record downloads in; ``None`` records nothing.

    Returns:
        One row per geography with ``GEO_ID, level, geoid, name`` and every
        ``..._E`` / ``..._M`` column of :func:`api_variables`.

    Raises:
        ValueError: For another vintage, an unknown method, a missing key on the
            API path, or labels that do not match this vintage.
    """
    if int(vintage) != VINTAGE:
        raise ValueError(
            f"ACS vintage {vintage} is not supported; the sources are pinned to {VINTAGE}"
        )
    if method not in ("summary", "api"):
        raise ValueError("method must be 'summary' or 'api'")
    fips = to_state_fips(state)
    folder = Path(cache) / "acs" / str(VINTAGE)
    client = client or HttpClient()
    problems = check_shell_labels(_fetch_labels(client, folder, manifest), VINTAGE)
    if problems:
        raise ValueError("ACS labels do not match this vintage:\n" + "\n".join(problems))
    if method == "api":
        key = (os.environ if env is None else env).get(KEY_ENV, "").strip()
        if not key:
            raise ValueError(f"method='api' needs {KEY_ENV} in the environment")
        out = _fetch_api(client, fips, key)
    else:
        out = _fetch_summary(client, fips, folder, manifest)
    columns = ["GEO_ID", "level", "geoid", "name", *api_variables()]
    missing = [c for c in columns if c not in out.columns]
    if missing:
        raise ValueError(f"ACS fetch: columns missing from the answer: {missing[:5]}...")
    return out[columns].sort_values(["level", "geoid"]).reset_index(drop=True)


# --------------------------------------------------------------------------- profile tables

PROFILE_TABLES = (
    "B03002",  # Hispanic or Latino origin by race
    "B08301",  # means of transportation to work
    "B11016",  # household type by household size
    "B15003",  # educational attainment, people aged 25 or older
    "B19001",  # household income brackets
    "B25004",  # vacancy status
    "B25070",  # gross rent as a share of household income
)
"""Extra tables for the place profile's charts (see :mod:`visionhousing.features.acs_bands`).

The charts also use B01001, B19013, B25003, B25024, B25034 and B25064, which
:func:`fetch_acs` already fetches; their filtered files are reused."""

_RACE = (
    "White alone", "Black or African American alone",
    "American Indian and Alaska Native alone", "Asian alone",
    "Native Hawaiian and Other Pacific Islander alone", "Some other race alone",
    "Two or more races", "Two races including Some other race",
    "Two races excluding Some other race, and three or more races",
)  # fmt: skip
_GRADES = ("Nursery school", "Kindergarten", "1st grade", "2nd grade", "3rd grade",
           "4th grade", "5th grade", "6th grade", "7th grade", "8th grade", "9th grade",
           "10th grade", "11th grade")  # fmt: skip
_SIZES = ("2-person household", "3-person household", "4-person household",
          "5-person household", "6-person household")  # fmt: skip

PROFILE_LABELS: dict[str, tuple[str, ...]] = {
    "B03002": ("Total", "Not Hispanic or Latino", *_RACE, "Hispanic or Latino", *_RACE),
    "B08301": (
        "Total", "Car, truck, or van", "Drove alone", "Carpooled", "In 2-person carpool",
        "In 3-person carpool", "In 4-person carpool", "In 5- or 6-person carpool",
        "In 7-or-more-person carpool", "Public transportation", "Bus",
        "Subway or elevated rail", "Long-distance train or commuter rail", None,
        "Ferryboat", "Taxi or ride-hailing services", "Motorcycle", "Bicycle", "Walked",
        "Other means", "Worked from home",
    ),
    "B11016": (
        "Total", "Family households", *_SIZES, "7-or-more person household",
        "Nonfamily households", "1-person household", *_SIZES, "7-or-more person household",
    ),
    "B15003": (
        "Total", "No schooling completed", *_GRADES, "12th grade, no diploma",
        "Regular high school diploma", "GED or alternative credential",
        "Some college, less than 1 year", "Some college, 1 or more years, no degree",
        "Associate's degree", "Bachelor's degree", "Master's degree",
        "Professional school degree", "Doctorate degree",
    ),
    "B19001": (
        "Total", "Less than $10,000", "$10,000 to $14,999", "$15,000 to $19,999",
        "$20,000 to $24,999", "$25,000 to $29,999", "$30,000 to $34,999",
        "$35,000 to $39,999", "$40,000 to $44,999", "$45,000 to $49,999",
        "$50,000 to $59,999", "$60,000 to $74,999", "$75,000 to $99,999",
        "$100,000 to $124,999", "$125,000 to $149,999", "$150,000 to $199,999",
        "$200,000 or more",
    ),
    "B25004": (
        "Total", "For rent", "Rented, not occupied", "For sale only", "Sold, not occupied",
        "For seasonal, recreational, or occasional use", "For migrant workers", "Other vacant",
    ),
    "B25070": (
        "Total", "Less than 10.0 percent", "10.0 to 14.9 percent", "15.0 to 19.9 percent",
        "20.0 to 24.9 percent", "25.0 to 29.9 percent", "30.0 to 34.9 percent",
        "35.0 to 39.9 percent", "40.0 to 49.9 percent", "50.0 percent or more", "Not computed",
    ),
}  # fmt: skip
"""Expected label of every cell of :data:`PROFILE_TABLES`, cell 1 first (``None``: not
checked, for a label with a non-ASCII character)."""

PROFILE_EXTRACT = "acs5_profile_{fips}.csv.gz"
"""The state's profile cells, saved once in ``cache/acs/{vintage}/`` whichever path fetched
them, so the pack is built from the same file either way."""


def profile_variables() -> list[str]:
    """Every estimate and margin variable of :data:`PROFILE_TABLES`, API style."""
    out = []
    for table in PROFILE_TABLES:
        for i in range(1, len(PROFILE_LABELS[table]) + 1):
            out += [f"{table}_{i:03d}E", f"{table}_{i:03d}M"]
    return out


def check_profile_labels(labels: Mapping[str, str]) -> list[str]:
    """Check every profile cell against :data:`PROFILE_LABELS`; return the problems."""
    problems = []
    for table, wanted in PROFILE_LABELS.items():
        for i, want in enumerate(wanted, start=1):
            var = f"{table}_{i:03d}E"
            got = labels.get(var)
            if got is None:
                problems.append(f"{var}: missing, expected {want!r}")
                continue
            last = got.split("!!")[-1].strip().rstrip(":").strip()
            if want is not None and last != want:
                problems.append(f"{var}: label {got!r}, expected {want!r}")
    return problems


def choose_method(method: str = "auto", env: Mapping[str, str] | None = None) -> str:
    """``"api"`` when asked for, or for ``"auto"`` when a key is set; else ``"summary"``.

    The summary files need no key, so a run with no key always works.
    """
    if method not in ("auto", "summary", "api"):
        raise ValueError("method must be 'auto', 'summary' or 'api'")
    if method != "auto":
        return method
    key = (os.environ if env is None else env).get(KEY_ENV, "").strip()
    return "api" if key else "summary"


def fetch_acs_profile(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    method: str = "auto",
    env: Mapping[str, str] | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Fetch :data:`PROFILE_TABLES` for a state's own row, its counties and its tracts.

    The first run fetches the cells and saves them to one small file
    (:data:`PROFILE_EXTRACT`); later runs read that file and use no network.

    Args:
        state: Postal code or FIPS code.
        cache: Folder for raw files (``data/raw``).
        client: The HTTP client; a default one is made when omitted.
        method: ``"auto"`` (default: the API when ``CENSUS_API_KEY`` is set, else
            the summary files), ``"summary"`` (no key) or ``"api"``.
        env: Environment to read the key from; ``os.environ`` when omitted.
        manifest: Manifest file to record downloads in; ``None`` records nothing.

    Returns:
        ``GEO_ID, level, geoid`` and every column of :func:`profile_variables`,
        sorted by level and code. Both paths give the same table.

    Raises:
        ValueError: An unknown method, a missing key on the API path, or labels
            that do not match this vintage.
    """
    fips = to_state_fips(state)
    folder = Path(cache) / "acs" / str(VINTAGE)
    dest = folder / PROFILE_EXTRACT.format(fips=fips)
    columns = ["GEO_ID", "level", "geoid", *profile_variables()]
    if dest.exists():
        out = pd.read_csv(dest, dtype={"GEO_ID": str, "level": str, "geoid": str})
        return out[columns]
    how = choose_method(method, env)
    client = client or HttpClient()
    shells = folder / f"ACS{VINTAGE}5YR_Table_Shells_profile_subset.txt"
    if not shells.exists():
        url = get_source("acs5_table_shells").url()
        stream_filter(client, url, shells, _first_field_keep(PROFILE_TABLES))
        _record("acs5_table_shells", url, shells, manifest)
    problems = check_profile_labels(parse_table_shells(shells, PROFILE_TABLES))
    if problems:
        raise ValueError("ACS labels do not match this vintage:\n" + "\n".join(problems))
    if how == "api":
        key = (os.environ if env is None else env).get(KEY_ENV, "").strip()
        if not key:
            raise ValueError(f"method='api' needs {KEY_ENV} in the environment")
        out = _fetch_api(client, fips, key, profile_variables())
        url = get_source("acs5_api").url()
    else:
        out = _fetch_summary(client, fips, folder, manifest, PROFILE_TABLES)
        url = get_source("acs5_profile_tables").url(table="{table}")
    missing = [c for c in columns if c not in out.columns]
    if missing:
        raise ValueError(f"ACS profile fetch: columns missing from the answer: {missing[:5]}...")
    out = out[columns].sort_values(["level", "geoid"]).reset_index(drop=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with part.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(out.to_csv(index=False, lineterminator="\n").encode("utf-8"))
    part.replace(dest)
    _record("acs5_profile_tables", url, dest, manifest)
    return pd.read_csv(dest, dtype={"GEO_ID": str, "level": str, "geoid": str})[columns]
