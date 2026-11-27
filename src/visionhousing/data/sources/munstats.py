"""Pennsylvania municipal planning and zoning status (PA DCED Municipal Statistics).

Plain-language summary
----------------------
Pennsylvania has about 2,560 municipalities (cities, boroughs, townships and
one town). Each decides for itself whether to adopt a zoning ordinance, a
comprehensive plan, a subdivision ordinance and so on. Where a municipality
has no zoning of its own, a county zoning ordinance may apply, or none at all.
The Department of Community and Economic Development (DCED) keeps this record
in its Municipal Statistics ("Munstats") service, in the report "County /
Municipal Planning, Zoning & Building Code". It is public information with no
stated licence; we cite it as "PA DCED Municipal Statistics".

How the report is read
----------------------
The report sits behind an ASP.NET form. :func:`fetch_munstats` opens the form,
posts it back with every county chosen (``county = NULL``), finds the
report's export address in the answer (``ExportUrlBase``) and asks for it as
CSV. That gives one row per municipality and one per county government
(about 2,634 rows), with DCED's own municipality ids.

DCED ids are not Census codes, so :func:`crosswalk` matches each DCED
municipality to a Census county subdivision (the Census name for a
municipality) by county and by name, after writing both names the same way
("ABBOTTSTOWN BORO" and "Abbottstown borough" both become
``ABBOTTSTOWN BOROUGH``). Unmatched names are returned in a list, never
dropped silently, and a test checks the match rate.

Census inputs, both small public files from the Census Bureau:

- the 2024 subcounty population estimates (``sub-est2024_{fips}.csv``): the
  name, county and code of every county subdivision, with its population;
- the 2020 block assignment file for county subdivisions (``BlockAssign``),
  which says which county subdivision every 2020 block lies in. It lets a
  tract be split by municipality using its blocks' 2020 housing units.

What a row says
---------------
:func:`zoning_regime` sorts each municipality into one of:

- ``own``: it has its own zoning ordinance;
- ``joint``: it has zoning through a joint (multi-municipal) ordinance;
- ``county``: it has no ordinance of its own, but the county's applies;
- ``none``: no zoning ordinance applies.
"""

from __future__ import annotations

import html
import http.cookiejar
import io
import re
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import USER_AGENT, HttpClient, ssl_context
from visionhousing.data.registry import get_source
from visionhousing.data.sources import state_fips as to_state_fips

__all__ = [
    "REGIMES",
    "Crosswalk",
    "census_subdivisions",
    "crosswalk",
    "dced_county_fips",
    "export_url",
    "fetch_block_places",
    "fetch_block_subdivisions",
    "fetch_munstats",
    "fetch_subdivision_population",
    "hidden_fields",
    "normalise_name",
    "parse_block_assignment",
    "parse_block_places",
    "parse_munstats",
    "parse_subdivision_population",
    "zoning_regime",
]

SOURCE = "pa_dced_munstats"
POP_SOURCE = "census_subcounty_pop"
BAF_SOURCE = "census_baf_2020_mcd"
BAF_PLACE_SOURCE = "census_baf_2020_place"
NO_PLACE = "99999"
"""Place code written for a block outside every incorporated place and CDP
(the block assignment file leaves it blank)."""
FORM_URL = "https://apps.dced.pa.gov/munstats-public/ReportInformation2.aspx?report=CountyMuniBuilding_Excel"
HOST = "https://apps.dced.pa.gov"
POP_YEAR = 2024
REGIMES: tuple[str, ...] = ("own", "joint", "county", "none")
"""Who regulates zoning in a municipality (see the module summary)."""

YES_NO = (
    "COMPREHENSIVE_PLAN",
    "ZONING_ORDINANCE",
    "COUNTY_ZONING",
    "SUBDIVISION_ORDINANCE",
    "COUNTY_SUBDIVISION",
    "PLANNING_COMMISSION",
    "ZONING_HEARING_BOARD",
    "UCC_CODE",
)
NEEDED = ("COUNTY_NAME", "MUNICIPALITY_ID", "MUNICIPALITY_NAME", *YES_NO)

_TYPE_WORDS = {
    "BORO": "BOROUGH",
    "BOROUGH": "BOROUGH",
    "TWP": "TOWNSHIP",
    "TOWNSHIP": "TOWNSHIP",
    "CITY": "CITY",
    "TOWN": "TOWN",
}
_SPELLING = {"SAINT": "ST", "MOUNT": "MT", "FORT": "FT", "MC": "MC"}


# --------------------------------------------------------------------------- the form


def hidden_fields(page: str) -> dict[str, str]:
    """Every hidden input of an ASP.NET page (``__VIEWSTATE`` and friends), by name."""
    fields: dict[str, str] = {}
    for tag in re.findall(r"<input[^>]*type=\"hidden\"[^>]*>", page):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        fields[html.unescape(name.group(1))] = html.unescape(value.group(1)) if value else ""
    return fields


def export_url(page: str, fmt: str = "CSV") -> str:
    """The report's export address from a rendered report page.

    Raises:
        ValueError: The page has no ``ExportUrlBase`` (the report did not render).
    """
    found = re.search(r'"ExportUrlBase":"([^"]+)"', page)
    if not found:
        raise ValueError("the Munstats page has no report export address (ExportUrlBase)")
    base = found.group(1).encode("ascii").decode("unicode_escape")
    return HOST + base + fmt


def _session() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=ssl_context()),
    )
    opener.addheaders = [("User-Agent", USER_AGENT)]
    return opener


def _download_report(timeout: float = 120.0) -> bytes:
    """Open the form, post it for all counties, and download the CSV export."""
    opener = _session()
    with opener.open(FORM_URL, timeout=timeout) as resp:
        page = resp.read().decode("utf-8", "replace")
    fields = hidden_fields(page)
    fields.update(
        {
            "ctl00$ContentPlaceHolder1$ddCountyId": "NULL",
            "ctl00$ContentPlaceHolder1$ddMunicipalityTypeCode": "NULL",
            "ctl00$ContentPlaceHolder1$btnDisplay": "Display Report",
        }
    )
    body = urllib.parse.urlencode(fields).encode()
    with opener.open(FORM_URL, body, timeout=timeout) as resp:
        report = resp.read().decode("utf-8", "replace")
    with opener.open(export_url(report), timeout=timeout) as resp:
        return resp.read()


# --------------------------------------------------------------------------- parsing


def parse_munstats(raw: bytes | str) -> pd.DataFrame:
    """Read the report CSV; keep municipality rows and the columns used.

    Returns:
        One row per DCED municipality with ``dced_id``, ``county_name``,
        ``name`` and the yes/no columns as 1/0 (missing stays empty), plus
        ``plan_members`` and ``zoning_members``: how many municipalities the
        comprehensive plan and the zoning ordinance cover (0 when not listed).

    Raises:
        ValueError: A needed column is missing (the report changed).
    """
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw.lstrip("﻿")
    frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, na_values=[""])
    missing = [c for c in NEEDED if c not in frame.columns]
    if missing:
        raise ValueError(f"Munstats report lacks columns {missing}")
    frame = frame[~frame["MUNICIPALITY_NAME"].str.upper().str.endswith(" COUNTY")].copy()
    out = pd.DataFrame(
        {
            "dced_id": frame["MUNICIPALITY_ID"].str.strip(),
            "county_name": frame["COUNTY_NAME"].str.strip().str.upper(),
            "name": frame["MUNICIPALITY_NAME"].str.strip(),
        }
    )
    for col in YES_NO:
        v = frame[col].str.strip().str.upper()
        out[col.lower()] = v.map({"YES": 1, "NO": 0}).astype("Int64")

    def members(col: str) -> pd.Series:
        return frame[col].fillna("").map(lambda s: len([p for p in s.split(",") if p.strip()]))

    out["plan_members"] = members("COMPREHENSIVE_MUNICIPALITIES").to_numpy()
    out["zoning_members"] = members("ZONING_MUNICIPALITIES").to_numpy()
    return out.reset_index(drop=True)


def zoning_regime(row: pd.Series | dict) -> str:
    """``own``, ``joint``, ``county`` or ``none`` for one parsed Munstats row.

    A row with no DCED record (an unmatched Census subdivision) gives ``""``.
    """

    def flag(name: str) -> int | None:
        v = row.get(name)
        return None if v is None or pd.isna(v) else int(v)

    own = flag("zoning_ordinance")
    if own is None:
        return ""
    if own == 1:
        members = flag("zoning_members") or 0
        return "joint" if members >= 2 else "own"
    if flag("county_zoning") == 1:
        return "county"
    return "none"


def dced_county_fips(dced_id: str, state_fips: str = "42") -> str:
    """County code from a DCED id: the first two digits number counties alphabetically.

    Pennsylvania county codes are the odd numbers in alphabetical order
    (Adams 001, Allegheny 003 ...), so DCED county ``n`` is code ``2n - 1``.
    """
    n = int(str(dced_id)[:2])
    return f"{state_fips}{2 * n - 1:03d}"


def normalise_name(name: str) -> str:
    """One spelling for a municipality name: upper case, type word spelled out.

    ``"Mc Kees Rocks Boro"`` and ``"McKees Rocks borough"`` both give
    ``"MCKEES ROCKS BOROUGH"``.
    """
    text = re.sub(r"[.'`]", "", str(name).upper())
    text = re.sub(r"[-/,()]", " ", text)
    words = text.split()
    if words and words[-1] in _TYPE_WORDS:
        words[-1] = _TYPE_WORDS[words[-1]]
    words = [_SPELLING.get(w, w) for w in words]
    joined = " ".join(words)
    joined = re.sub(r"\bMC (?=[A-Z])", "MC", joined)
    return joined.replace(" ", "")


def _base_name(name: str) -> str:
    """A normalised name without its last word when that word is a type word."""
    words = re.sub(r"[.'`]", "", str(name).upper()).split()
    if len(words) > 1 and (words[-1] in _TYPE_WORDS or words[-1] == "MUNICIPALITY"):
        words = words[:-1]
    return normalise_name(" ".join(words) + " X")[:-1]


def parse_subdivision_population(raw: bytes | str, state_fips: str = "42") -> pd.DataFrame:
    """County subdivisions of one state from the Census subcounty estimates file.

    Returns:
        ``county`` (5 digits), ``cousub`` (5 digits), ``name`` and
        ``population`` (the latest yearly estimate), one row per county part.
    """
    text = raw.decode("latin-1") if isinstance(raw, bytes) else raw
    frame = pd.read_csv(io.StringIO(text), dtype=str)
    frame = frame[frame["STATE"] == state_fips]
    rows = frame[frame["SUMLEV"] == "061"]
    # A consolidated city-county (Philadelphia) has no 061 row: its one county
    # subdivision is the city itself, listed as a place (157) with the same code.
    covered = set(rows["COUNTY"])
    city = frame[
        (frame["SUMLEV"] == "157")
        & (frame["PRIMGEO_FLAG"] == "1")
        & ~frame["COUNTY"].isin(covered)
        & (frame["COUNTY"] != "000")
    ]
    city = city.assign(COUSUB=city["PLACE"])
    rows = pd.concat([rows, city], ignore_index=True)
    pop_col = sorted(c for c in frame.columns if c.startswith("POPESTIMATE"))[-1]
    return (
        pd.DataFrame(
            {
                "county": state_fips + rows["COUNTY"],
                "cousub": rows["COUSUB"],
                "name": rows["NAME"],
                "population": pd.to_numeric(rows[pop_col], errors="coerce").astype("Int64"),
            }
        )
        .sort_values(["county", "cousub"])
        .reset_index(drop=True)
    )


def parse_block_assignment(raw: bytes | str) -> pd.DataFrame:
    """The 2020 block assignment file for county subdivisions: block -> county subdivision.

    Returns:
        ``block`` (15 digits) and ``cousub`` (5 digits).
    """
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    frame = pd.read_csv(io.StringIO(text), sep="|", dtype=str)
    return pd.DataFrame({"block": frame["BLOCKID"], "cousub": frame["COUSUBFP"]})


def parse_block_places(raw: bytes | str) -> pd.DataFrame:
    """The 2020 block assignment file for places (incorporated places and CDPs).

    Returns:
        ``block`` (15 digits) and ``place`` (5 digits; :data:`NO_PLACE` when
        the block is in no place).
    """
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    frame = pd.read_csv(io.StringIO(text), sep="|", dtype=str)
    place = frame["PLACEFP"].fillna("").str.strip()
    return pd.DataFrame({"block": frame["BLOCKID"], "place": place.where(place != "", NO_PLACE)})


# --------------------------------------------------------------------------- crosswalk


@dataclass(frozen=True)
class Crosswalk:
    """DCED municipalities matched to Census county subdivisions.

    Attributes:
        table: One row per Census county subdivision part, with the DCED
            columns of its match (``dced_id`` empty when unmatched).
        unmatched_dced: DCED rows with no Census match (``county_name``, ``name``).
        unmatched_census: Census subdivisions with no DCED match.
    """

    table: pd.DataFrame
    unmatched_dced: pd.DataFrame
    unmatched_census: pd.DataFrame

    @property
    def match_rate(self) -> float:
        """Share of DCED municipalities matched to a Census subdivision."""
        matched = self.table["dced_id"].dropna().nunique()
        total = matched + len(self.unmatched_dced)
        return matched / total if total else 0.0


def census_subdivisions(population: pd.DataFrame) -> pd.DataFrame:
    """County subdivisions that are real municipalities (not water or unorganised areas)."""
    keep = ~population["name"].str.contains(r"(?i)county subdivisions not defined|^water ")
    return population[keep].copy()


def crosswalk(dced: pd.DataFrame, census: pd.DataFrame, overrides: dict | None = None) -> Crosswalk:
    """Match DCED municipalities to Census county subdivisions by county and name.

    Steps: (1) same county and same normalised name; (2) for what is left,
    the same normalised name anywhere in the state when it is unique there
    (DCED lists a municipality that crosses a county line once); (3) manual
    pairs from ``overrides`` (``{dced_id: cousub}``). A Census subdivision
    whose code was matched in one county takes the same DCED row in every
    county it crosses into.
    """
    dced = dced.copy()
    dced["county"] = dced["dced_id"].map(dced_county_fips)
    dced["key"] = dced["name"].map(normalise_name)
    census = census.copy()
    census["key"] = census["name"].map(normalise_name)

    census["base"] = census["name"].map(_base_name)
    by_county = {(r.county, r.key): r.cousub for r in census.itertuples()}
    state_keys = census.groupby("key")["cousub"].agg(lambda s: sorted(set(s)))
    base_keys = census.groupby(["county", "base"])["cousub"].agg(lambda s: sorted(set(s)))
    pairs: dict[str, str] = {}
    for r in dced.itertuples():
        hit = by_county.get((r.county, r.key))
        if hit is None:
            options = state_keys.get(r.key, [])
            hit = options[0] if len(options) == 1 else None
        if hit is None:
            # Home-rule places Census calls "municipality" (DCED: "BORO") and
            # names without a type word ("OIL CITY"): same county, name
            # without its type word, when that is unique.
            for base in dict.fromkeys((_base_name(r.name), normalise_name(r.name))):
                options = base_keys.get((r.county, base), [])
                if len(options) == 1:
                    hit = options[0]
                    break
        if hit is None and overrides and r.dced_id in overrides:
            hit = str(overrides[r.dced_id])
        if hit is not None:
            pairs[r.dced_id] = hit
    by_code = {code: d for d, code in pairs.items()}
    census["dced_id"] = census["cousub"].map(by_code)
    cols = [c for c in dced.columns if c not in ("county", "key", "name", "county_name")]
    table = census.drop(columns=["key", "base"]).merge(
        dced[cols].rename(columns={"dced_id": "dced_id"}), on="dced_id", how="left"
    )
    unmatched_dced = dced.loc[~dced["dced_id"].isin(pairs), ["dced_id", "county_name", "name"]]
    unmatched_census = table.loc[table["dced_id"].isna(), ["county", "cousub", "name"]]
    return Crosswalk(
        table.reset_index(drop=True),
        unmatched_dced.reset_index(drop=True),
        unmatched_census.reset_index(drop=True),
    )


# --------------------------------------------------------------------------- fetching


def fetch_munstats(
    cache: Path,
    *,
    manifest: Path | None = mf.MANIFEST_PATH,
    refresh: bool = False,
) -> pd.DataFrame:
    """Download (or reuse) the Munstats planning and zoning report; return it parsed."""
    dest = Path(cache) / "local" / "munstats" / "CountyMuniBuilding.csv"
    if refresh or not dest.exists():
        raw = _download_report()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        if manifest is not None:
            source = get_source(SOURCE)
            mf.record(
                mf.entry_for_file(SOURCE, source.url_pattern, dest, "current", root=mf.ROOT),
                manifest,
            )
    return parse_munstats(dest.read_bytes())


def fetch_subdivision_population(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Download (or reuse) the Census subcounty population estimates for one state."""
    fips = to_state_fips(state)
    source = get_source(POP_SOURCE)
    url = source.url(fips=fips)
    dest = Path(cache) / "local" / "census" / f"sub-est{POP_YEAR}_{fips}.csv"
    if not dest.exists():
        (client or HttpClient()).download(url, dest)
        if manifest is not None:
            mf.record(
                mf.entry_for_file(POP_SOURCE, url, dest, str(POP_YEAR), root=mf.ROOT), manifest
            )
    return parse_subdivision_population(dest.read_bytes(), fips)


def fetch_block_subdivisions(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Download (or reuse) the 2020 block-to-county-subdivision file for one state.

    The zip (about 6 MB for Pennsylvania) is read in memory and only the two
    needed columns are kept, gzipped, in the cache.

    Raises:
        LookupError: The state's zip has no county subdivision member (a state
            whose subdivisions are statistical census county divisions, such
            as Wyoming).
    """
    from visionhousing.data.sources import STATE_FIPS

    fips = to_state_fips(state)
    postal = next(k for k, v in STATE_FIPS.items() if v == fips)
    dest = Path(cache) / "local" / "census" / f"block_cousub_2020_{fips}.csv.gz"
    if dest.exists():
        return pd.read_csv(dest, dtype=str)
    source = get_source(BAF_SOURCE)
    url = source.url(fips=fips, postal=postal)
    raw = (client or HttpClient()).get_bytes(url)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        member = next((n for n in zf.namelist() if n.endswith("_MCD.txt")), None)
        if member is None:
            raise LookupError(
                f"{url} has no county subdivision member: the state's county subdivisions "
                "are census county divisions, which have no block assignment file"
            )
        table = parse_block_assignment(zf.read(member))
    dest.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(dest, index=False, compression={"method": "gzip", "mtime": 0})
    if manifest is not None:
        mf.record(mf.entry_for_file(BAF_SOURCE, url, dest, "2020", root=mf.ROOT), manifest)
    return table


def fetch_block_places(
    state: str,
    cache: Path,
    *,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame:
    """Download (or reuse) the 2020 block-to-place file for one state.

    Same zip as :func:`fetch_block_subdivisions` (the Census Bureau's block
    assignment files); only the incorporated place and CDP member is read and
    two columns are kept, gzipped, in the cache. Used where a city is a Census
    place but not a county subdivision (Wyoming).
    """
    from visionhousing.data.sources import STATE_FIPS

    fips = to_state_fips(state)
    postal = next(k for k, v in STATE_FIPS.items() if v == fips)
    dest = Path(cache) / "local" / "census" / f"block_place_2020_{fips}.csv.gz"
    if dest.exists():
        return pd.read_csv(dest, dtype=str)
    source = get_source(BAF_PLACE_SOURCE)
    url = source.url(fips=fips, postal=postal)
    raw = (client or HttpClient()).get_bytes(url)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        member = next(n for n in zf.namelist() if n.endswith("_INCPLACE_CDP.txt"))
        table = parse_block_places(zf.read(member))
    dest.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(dest, index=False, compression={"method": "gzip", "mtime": 0})
    if manifest is not None:
        mf.record(mf.entry_for_file(BAF_PLACE_SOURCE, url, dest, "2020", root=mf.ROOT), manifest)
    return table
