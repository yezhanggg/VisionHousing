"""Reported crime by police agency, from the FBI Crime Data Explorer (CDE) API.

Plain-language summary
----------------------
The FBI collects "offenses known to law enforcement" from police agencies
through its Uniform Crime Reporting (UCR) program and publishes them in the
Crime Data Explorer. Its API answers in JSON and needs a free key from
api.data.gov.

**The key.** The key is read only from the environment variable
``API_DATA_GOV_KEY`` (or ``FBI_API_KEY``). It is never written to disk, never
part of a cache file name, and removed ("redacted") from every message. With
no key the fetch is skipped and the pack's safety table stays empty; nothing
else in the build depends on it, and no test needs it.

**What is fetched.**

1. The state's agency list (``agency/byStateAbbr/{state}``): each agency's
   code ("ORI"), name, type and the county or counties it lists.
2. For every city, county and State Police agency, its monthly counts of
   violent offenses (``V``: murder, rape, robbery, aggravated assault) and
   property offenses (``P``: burglary, larceny-theft, motor-vehicle theft,
   arson) for one calendar year, with the population the agency serves.

Answers are cached one file per agency and offense under
``data/raw/safety/{year}/``, so an interrupted run picks up where it stopped.
api.data.gov allows about 1,000 requests an hour per key, so the first full
Pennsylvania run (about 1,200 agencies, two requests each) waits between
requests and takes a few hours; later runs read the cache.

University, transit, park and other special agencies serve no resident
population, so they are left out; their offenses are therefore not counted.

Pennsylvania's move to incident-based reporting (NIBRS) around 2021 changed
how offenses are counted, so years before and after the change should not be
compared.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from visionhousing.data import manifest as mf
from visionhousing.data.http import FetchError, HttpClient, redact
from visionhousing.data.registry import get_source
from visionhousing.data.sources import STATE_FIPS
from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.data.sources.incentives import STATE_NAMES

__all__ = [
    "AGENCY_COLUMNS",
    "KEY_ENVS",
    "OFFENSES",
    "SAFETY_YEAR",
    "api_key",
    "fetch_safety",
    "read_agencies",
    "read_agency_offenses",
]

SOURCE = "fbi_cde_agency"
SAFETY_YEAR = 2024
"""Calendar year of the offense counts."""
KEY_ENVS = ("API_DATA_GOV_KEY", "FBI_API_KEY")
"""Environment variables the key is read from, in order."""
OFFENSES = {"violent": "V", "property": "P"}
"""Offense groups and their API codes."""
AGENCY_TYPES = ("City", "County", "State Police")
"""Agency types kept: those that serve a resident population."""
PAUSE_SECONDS = 3.7
"""Wait between uncached requests, to stay under about 1,000 requests an hour."""

AGENCY_COLUMNS = (
    "ori",
    "agency_name",
    "agency_type",
    "county_names",
    "year",
    "violent",
    "violent_months",
    "property",
    "property_months",
    "population",
)
"""Columns of the cached agency table."""

_POSTAL = {v: k for k, v in STATE_FIPS.items()}


def api_key(env: Mapping[str, str] | None = None) -> str | None:
    """The api.data.gov key from the environment, or ``None`` when none is set."""
    source = os.environ if env is None else env
    for name in KEY_ENVS:
        value = (source.get(name) or "").strip()
        if value:
            return value
    return None


def read_agencies(data: bytes | str) -> pd.DataFrame:
    """Read the agency-list answer into one row per agency.

    The API groups agencies by county name (``{"ADAMS": [ {...}, ... ], ...}``);
    a plain list of agencies is accepted too.

    Returns:
        Columns ``ori, agency_name, agency_type, county_names, is_nibrs, lat, lon``.
    """
    doc = json.loads(data)
    groups = doc.values() if isinstance(doc, dict) else [doc]
    rows = {}
    for group in groups:
        for a in group or []:
            ori = str(a.get("ori") or "").strip()
            if not ori or ori in rows:
                continue
            rows[ori] = {
                "ori": ori,
                "agency_name": str(a.get("agency_name") or "").strip(),
                "agency_type": str(a.get("agency_type_name") or "").strip(),
                "county_names": str(a.get("counties") or "").strip(),
                "is_nibrs": bool(a.get("is_nibrs")),
                "lat": a.get("latitude"),
                "lon": a.get("longitude"),
            }
    columns = ["ori", "agency_name", "agency_type", "county_names", "is_nibrs", "lat", "lon"]
    return pd.DataFrame(list(rows.values()), columns=columns).sort_values("ori", ignore_index=True)


def _own_series(
    series: Mapping[str, Any], others: tuple[str, ...], suffix: str = "", agency: str = ""
) -> Any:
    """The one series that belongs to the agency (not the nation's or the state's)."""
    picked = [
        (name, values)
        for name, values in series.items()
        if name.endswith(suffix) and not any(name.startswith(o) for o in others)
    ]
    if len(picked) > 1 and agency:
        picked = [(n, v) for n, v in picked if n.startswith(agency)]
    if len(picked) != 1:
        return None
    return picked[0][1]


def read_agency_offenses(
    data: bytes | str, state_name: str = "", agency_name: str = ""
) -> tuple[int | None, int, float | None]:
    """Read one summarized-offense answer: the year's total, months reported, population.

    The answer holds monthly series keyed ``"MM-YYYY"`` for the agency, its state
    and the nation, under ``offenses.actuals`` (series named ``"<name> Offenses"``
    and ``"<name> Clearances"``) and ``populations.population``.

    Returns:
        ``(total offenses or None, months with a reported value, population or None)``.
    """
    doc = json.loads(data)
    others = ("United States", state_name) if state_name else ("United States",)
    actuals = ((doc.get("offenses") or {}).get("actuals")) or {}
    own = _own_series(actuals, others, " Offenses", agency_name)
    total, months = None, 0
    if isinstance(own, Mapping):
        values = [v for v in own.values() if v is not None]
        months = len(values)
        total = int(sum(float(v) for v in values)) if values else None
    pops = ((doc.get("populations") or {}).get("population")) or {}
    own_pop = _own_series(pops, others, "", agency_name)
    population = None
    if isinstance(own_pop, Mapping):
        values = [float(v) for v in own_pop.values() if v is not None]
        population = max(values) if values else None
    return total, months, population


def _get(client: HttpClient, path: str, params: dict[str, str], key: str, dest: Path) -> bytes:
    """One API answer, from the cache or the network (the key is never cached or shown)."""
    if dest.exists():
        return dest.read_bytes()
    url = get_source(SOURCE).url(path=path)
    try:
        data = client.get_bytes(url, {**params, "API_KEY": key})
    except FetchError as err:
        raise FetchError(redact(str(err), (key,)), status=err.status) from None
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(data)
    part.replace(dest)
    client.sleep(PAUSE_SECONDS)
    return data


def fetch_safety(
    state: str,
    cache: Path,
    *,
    year: int = SAFETY_YEAR,
    key: str | None = None,
    client: HttpClient | None = None,
    manifest: Path | None = mf.MANIFEST_PATH,
) -> pd.DataFrame | None:
    """Fetch every kept agency's violent and property offenses for ``year``.

    Returns:
        The agency table (:data:`AGENCY_COLUMNS`), or ``None`` when no key is set
        and nothing is cached.
    """
    fips = to_state_fips(state)
    abbr = _POSTAL[fips]
    folder = Path(cache) / "safety"
    dest = folder / f"fbi_agencies_{year}_{fips}.csv"
    if dest.exists():
        return pd.read_csv(dest, dtype={"ori": str, "county_names": str})
    key = key or api_key()
    if not key:
        return None
    client = client or HttpClient()
    raw = folder / str(year)
    agencies = read_agencies(
        _get(client, f"agency/byStateAbbr/{abbr}", {}, key, raw / "agencies.json")
    )
    agencies = agencies[agencies["agency_type"].isin(AGENCY_TYPES)]
    state_name = STATE_NAMES.get(fips, "")
    rows = []
    span = {"from": f"01-{year}", "to": f"12-{year}"}
    for a in agencies.itertuples(index=False):
        rec: dict[str, Any] = {
            "ori": a.ori,
            "agency_name": a.agency_name,
            "agency_type": a.agency_type,
            "county_names": a.county_names,
            "year": year,
        }
        population = None
        for name, code in OFFENSES.items():
            data = _get(
                client,
                f"summarized/agency/{a.ori}/{code}",
                span,
                key,
                raw / f"{a.ori}_{code}.json",
            )
            total, months, pop = read_agency_offenses(data, state_name, a.agency_name)
            rec[name], rec[f"{name}_months"] = total, months
            population = population if population is not None else pop
        rec["population"] = population
        rows.append(rec)
    table = pd.DataFrame(rows, columns=list(AGENCY_COLUMNS))
    folder.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    table.to_csv(part, index=False, lineterminator="\n")
    part.replace(dest)
    if manifest is not None:
        url = get_source(SOURCE).url(path=f"agency/byStateAbbr/{abbr}")
        mf.record(mf.entry_for_file(SOURCE, url, dest, str(year), root=mf.ROOT), manifest)
    return table
