"""Jobs where people work: LEHD LODES job counts and Census County Business Patterns.

Plain-language summary
----------------------
Two public Census Bureau products describe a place's jobs. Neither needs a key.

**LODES** (LEHD Origin-Destination Employment Statistics, version 8) counts
jobs by the census block where people work and, separately, by the pair of
blocks where they live and work. The jobs come from state unemployment
insurance records plus federal civilian employment, so self-employed people,
the military and some others are left out. Blocks use 2020 Census codes, so
the first 11 digits of a block code are its tract and the first 5 its county.
The Bureau adds small random "noise" to block counts to protect privacy; sums
over a tract or county are much less affected.

- The **workplace file** (WAC, "workplace area characteristics", all jobs,
  file ``{st}_wac_S000_JT00_{year}.csv.gz``) is summed to tracts here: total
  jobs, jobs in three monthly-earnings bands and jobs in 20 industry sectors.
- The **origin-destination files** (OD ``main``: both ends in the state;
  ``aux``: work in the state, live in another state) are summed to pairs of
  counties: how many jobs in county A are held by people living in county B.

**County Business Patterns** (CBP) counts establishments (single business
locations), mid-March employment and yearly payroll by county and industry
sector (two-digit NAICS, the North American Industry Classification System).
Since 2017 the Bureau protects small cells with noise and flags, instead of
blanking them; the flag is kept.

Only the state's rows are kept on disk (``data/raw/jobs/``); the national CBP
zip and the LODES files are read as a stream and not kept.
"""

from __future__ import annotations

import csv
import gzip
import io
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import HttpClient
from visionhousing.data.registry import get_source
from visionhousing.data.sources import STATE_FIPS
from visionhousing.data.sources import state_fips as to_state_fips

__all__ = [
    "CBP_COLUMNS",
    "CBP_YEAR",
    "EARNINGS_BANDS",
    "LODES_YEAR",
    "OD_COLUMNS",
    "SECTORS",
    "WAC_COLUMNS",
    "fetch_cbp",
    "fetch_od_county",
    "fetch_wac_tracts",
    "read_cbp",
    "read_od_county",
    "read_wac_tracts",
    "sum_od_to_counties",
    "sum_wac_to_tracts",
]

LODES_YEAR = 2024
"""Latest LODES 8 year published for Pennsylvania (files dated 2026-10-14, checked 2026-11-22)."""
CBP_YEAR = 2023
"""Latest County Business Patterns year (2024 not yet published on 2026-11-22)."""

SOURCE_WAC = "lodes_wac"
SOURCE_OD = "lodes_od"
SOURCE_CBP = "cbp_county"

SECTORS: dict[str, tuple[str, str]] = {
    "CNS01": ("11", "Agriculture, forestry, fishing and hunting"),
    "CNS02": ("21", "Mining, quarrying, and oil and gas extraction"),
    "CNS03": ("22", "Utilities"),
    "CNS04": ("23", "Construction"),
    "CNS05": ("31", "Manufacturing"),
    "CNS06": ("42", "Wholesale trade"),
    "CNS07": ("44", "Retail trade"),
    "CNS08": ("48", "Transportation and warehousing"),
    "CNS09": ("51", "Information"),
    "CNS10": ("52", "Finance and insurance"),
    "CNS11": ("53", "Real estate and rental and leasing"),
    "CNS12": ("54", "Professional, scientific, and technical services"),
    "CNS13": ("55", "Management of companies and enterprises"),
    "CNS14": ("56", "Administrative and support and waste management services"),
    "CNS15": ("61", "Educational services"),
    "CNS16": ("62", "Health care and social assistance"),
    "CNS17": ("71", "Arts, entertainment, and recreation"),
    "CNS18": ("72", "Accommodation and food services"),
    "CNS19": ("81", "Other services (except public administration)"),
    "CNS20": ("92", "Public administration"),
}
"""LODES sector columns: the two-digit NAICS code (31 stands for 31-33, 44 for 44-45,
48 for 48-49) and the sector name."""

EARNINGS_BANDS = {
    "CE01": ("jobs_earn_low", "$1,250 a month or less"),
    "CE02": ("jobs_earn_mid", "$1,251 to $3,333 a month"),
    "CE03": ("jobs_earn_high", "more than $3,333 a month"),
}
"""LODES monthly-earnings bands and the column names used here."""

WAC_COLUMNS = (
    "tract",
    "jobs",
    *(name for name, _ in EARNINGS_BANDS.values()),
    *(f"jobs_naics_{code}" for code, _ in SECTORS.values()),
)
"""Columns of the tract job table (one row per tract with at least one job)."""

OD_COLUMNS = ("work_county", "home_county", "jobs")
"""Columns of the county-pair commuting table. ``home_county`` is ``"out_of_state"``
for workers who live in another state (the ``aux`` file)."""

OUT_OF_STATE = "out_of_state"

CBP_COLUMNS = (
    "county",
    "naics",
    "establishments",
    "employment",
    "employment_flag",
    "annual_payroll_k",
)
"""Columns of the CBP table: ``naics`` is ``"00"`` for all sectors or a two-digit code;
``annual_payroll_k`` is in thousands of dollars."""

_POSTAL = {v: k.lower() for k, v in STATE_FIPS.items()}


def _postal(fips: str) -> str:
    return _POSTAL[fips]


# --------------------------------------------------------------------------- LODES workplace


def sum_wac_to_tracts(lines: io.TextIOBase | list[str]) -> pd.DataFrame:
    """Sum a LODES workplace (WAC) file's block rows to 2020 tracts.

    Args:
        lines: The CSV text, header first (an open text file or a list of lines).

    Returns:
        Columns :data:`WAC_COLUMNS`, one row per tract, sorted by tract.

    Raises:
        ValueError: If a needed column is missing.
    """
    reader = csv.reader(lines)
    header = next(reader)
    wanted = ["C000", *EARNINGS_BANDS, *SECTORS]
    missing = [c for c in ["w_geocode", *wanted] if c not in header]
    if missing:
        raise ValueError(f"LODES workplace file lacks {missing}")
    at = [header.index(c) for c in wanted]
    geo = header.index("w_geocode")
    totals: dict[str, list[int]] = defaultdict(lambda: [0] * len(wanted))
    for row in reader:
        if not row:
            continue
        acc = totals[row[geo][:11]]
        for k, i in enumerate(at):
            acc[k] += int(row[i])
    frame = pd.DataFrame(
        [[tract, *vals] for tract, vals in sorted(totals.items())], columns=list(WAC_COLUMNS)
    )
    return frame


def read_wac_tracts(path: str | Path) -> pd.DataFrame:
    """Read the cached tract job table written by :func:`fetch_wac_tracts`."""
    frame = pd.read_csv(path, dtype={"tract": str})
    return frame[list(WAC_COLUMNS)]


def fetch_wac_tracts(
    state: str,
    cache: Path,
    *,
    year: int = LODES_YEAR,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Download the state's LODES workplace file, sum it to tracts, keep only the sums."""
    fips = to_state_fips(state)
    dest = Path(cache) / "jobs" / f"lodes_wac_{year}_tracts_{fips}.csv"
    if not dest.exists():
        url = get_source(SOURCE_WAC).url(st=_postal(fips), year=year)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f"wac_{fips}_{year}.csv.gz")
        try:
            (client or HttpClient()).download(url, tmp)
            with gzip.open(tmp, "rt", encoding="utf-8", newline="") as fh:
                table = sum_wac_to_tracts(fh)
        finally:
            tmp.unlink(missing_ok=True)
        if table.empty or not table["tract"].str.startswith(fips).all():
            raise ValueError(f"LODES workplace file has no rows (or other states' rows) for {fips}")
        _write(table, dest)
        _record(SOURCE_WAC, url, dest, f"LODES 8, {year}", manifest)
    return read_wac_tracts(dest)


# --------------------------------------------------------------------------- LODES flows


def sum_od_to_counties(
    lines: io.TextIOBase | list[str], counts: dict[tuple[str, str], int], *, aux: bool
) -> None:
    """Add a LODES origin-destination file's all-jobs counts (S000) to county pairs.

    Args:
        lines: The CSV text, header first.
        counts: ``(work county, home county) -> jobs``, updated in place.
        aux: True for the ``aux`` file (home in another state): the home county
            is recorded as ``"out_of_state"``.
    """
    reader = csv.reader(lines)
    header = next(reader)
    missing = [c for c in ("w_geocode", "h_geocode", "S000") if c not in header]
    if missing:
        raise ValueError(f"LODES origin-destination file lacks {missing}")
    w, h, s = header.index("w_geocode"), header.index("h_geocode"), header.index("S000")
    for row in reader:
        if not row:
            continue
        home = OUT_OF_STATE if aux else row[h][:5]
        key = (row[w][:5], home)
        counts[key] = counts.get(key, 0) + int(row[s])


def read_od_county(path: str | Path) -> pd.DataFrame:
    """Read the cached county-pair table written by :func:`fetch_od_county`."""
    return pd.read_csv(path, dtype={"work_county": str, "home_county": str})[list(OD_COLUMNS)]


def fetch_od_county(
    state: str,
    cache: Path,
    *,
    year: int = LODES_YEAR,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Download the state's LODES ``main`` and ``aux`` flow files and keep county-pair sums.

    The ``main`` file is about 31 MB compressed. It is read line by line, so memory
    stays small; the downloaded files are deleted afterwards.
    """
    fips = to_state_fips(state)
    dest = Path(cache) / "jobs" / f"lodes_od_{year}_counties_{fips}.csv"
    if not dest.exists():
        source = get_source(SOURCE_OD)
        client = client or HttpClient()
        dest.parent.mkdir(parents=True, exist_ok=True)
        counts: dict[tuple[str, str], int] = {}
        urls = []
        for part in ("main", "aux"):
            url = source.url(st=_postal(fips), part=part, year=year)
            urls.append(url)
            tmp = dest.with_name(f"od_{part}_{fips}_{year}.csv.gz")
            try:
                client.download(url, tmp)
                with gzip.open(tmp, "rt", encoding="utf-8", newline="") as fh:
                    sum_od_to_counties(fh, counts, aux=part == "aux")
            finally:
                tmp.unlink(missing_ok=True)
        rows = [(w, h, n) for (w, h), n in sorted(counts.items())]
        table = pd.DataFrame(rows, columns=list(OD_COLUMNS))
        if table.empty:
            raise ValueError(f"LODES flow files have no rows for {fips}")
        _write(table, dest)
        _record(SOURCE_OD, urls[0], dest, f"LODES 8, {year}", manifest)
    return read_od_county(dest)


# --------------------------------------------------------------------------- CBP


def read_cbp(source: str | Path | io.TextIOBase, state_fips: str | None = None) -> pd.DataFrame:
    """Read a CBP county file (or the cached state extract) into :data:`CBP_COLUMNS`.

    Keeps the all-sector rows (``naics`` ``------``, written ``"00"``) and the
    two-digit sector rows (``NN----``). With ``state_fips``, keeps that state only.
    """
    frame = pd.read_csv(source, dtype=str, keep_default_na=False)
    if "county" in frame.columns:  # the cached extract
        out = frame
    else:
        need = ["fipstate", "fipscty", "naics", "emp_nf", "emp", "ap", "est"]
        missing = [c for c in need if c not in frame.columns]
        if missing:
            raise ValueError(f"CBP file lacks {missing}")
        naics = frame["naics"].str.strip()
        two = naics.str.fullmatch(r"\d\d----") | (naics == "------")
        frame = frame[two]
        out = pd.DataFrame(
            {
                "county": frame["fipstate"].str.zfill(2) + frame["fipscty"].str.zfill(3),
                "naics": frame["naics"].str[:2].replace({"--": "00"}),
                "establishments": frame["est"],
                "employment": frame["emp"],
                "employment_flag": frame["emp_nf"],
                "annual_payroll_k": frame["ap"],
            }
        )
        out = out[~out["county"].str.endswith("999")]  # statewide, no county
    if state_fips is not None:
        out = out[out["county"].str.startswith(state_fips)]
    out = out.copy()
    for c in ("establishments", "employment", "annual_payroll_k"):
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("Int64")
    out["county"] = out["county"].astype(str)
    out["naics"] = out["naics"].astype(str).str.zfill(2)
    out["employment_flag"] = out["employment_flag"].fillna("").astype(str)
    return out[list(CBP_COLUMNS)].sort_values(["county", "naics"]).reset_index(drop=True)


def fetch_cbp(
    state: str,
    cache: Path,
    *,
    year: int = CBP_YEAR,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Download the national CBP county zip (13 MB), keep the state's sector rows, delete the zip."""
    fips = to_state_fips(state)
    dest = Path(cache) / "jobs" / f"cbp_{year}_county_{fips}.csv"
    if not dest.exists():
        url = get_source(SOURCE_CBP).url(year=year, yy=f"{year % 100:02d}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f"cbp{year % 100:02d}co.zip")
        try:
            (client or HttpClient()).download(url, tmp)
            with zipfile.ZipFile(tmp) as zf:
                member = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
                with zf.open(member) as raw:
                    text = io.TextIOWrapper(raw, encoding="latin-1", newline="")
                    header = text.readline()
                    keep = [header]
                    prefix = (f'"{fips}",', f"{fips},")
                    keep += [line for line in text if line.startswith(prefix)]
            table = read_cbp(io.StringIO("".join(keep)), fips)
        finally:
            tmp.unlink(missing_ok=True)
        if table.empty:
            raise ValueError(f"CBP file has no rows for state {fips}")
        _write(table, dest)
        _record(SOURCE_CBP, url, dest, f"{year}", manifest)
    return read_cbp(dest, fips)


# --------------------------------------------------------------------------- helpers


def _write(table: pd.DataFrame, dest: Path) -> None:
    part = dest.with_name(dest.name + ".part")
    table.to_csv(part, index=False, lineterminator="\n")
    part.replace(dest)


def _record(source_id: str, url: str, dest: Path, vintage: str, manifest: Path | None) -> None:
    if manifest is not None:
        mf.record(mf.entry_for_file(source_id, url, dest, vintage, root=mf.ROOT), manifest)
