"""Retrospective reform panel: what did real local reforms do to building permits?

Plain-language summary
----------------------
Several U.S. cities let 2 to 4 homes be built on lots once zoned for one
(Minneapolis, Saint Paul, Portland, Raleigh, Spokane, Arlington County). This
module asks whether permits for those building types rose after each rule took
effect, compared with ordinary cities that did not change their rules.

The steps:

1. **Panel.** :func:`fetch_place_years` reads the Census Bureau's Building
   Permits Survey (BPS) place files, one per region and year, and keeps one row
   per permit office and year. :func:`fetch_housing_units` reads housing-unit
   counts (ACS 2020-2024, table B25001) for every place and county, so permits
   become a rate: units authorized per 1,000 existing homes.
2. **Design.** Every choice (outcomes, horizon, base years, comparison pool,
   effective dates, pass marks, the cut-off date for statewide laws) is read from
   ``assumptions/evidence/retro_design.yaml``, which was written before any
   estimate. ``run`` refuses to estimate unless the design matches its
   fingerprint in ``assumptions/preregistration.yaml``; ``check`` also fails
   when the design file changed after the results were written.
3. **Estimates.** :func:`unit_effects` is a group-time difference-in-differences
   (the Callaway-Sant'Anna idea): for a reformed city reforming in year ``g``,
   the change in its permit rate from a base (the mean of the three years before
   ``g``) to year ``t``, minus the same change averaged over comparison cities
   that had not reformed by ``t``. Event time ``e = t - g``. :func:`event_study`
   averages these across cities.
4. **Checks.** A pre-trend test (did the city already drift away from the
   comparison before the reform?), a placebo-date test (does a fake reform three
   years early "work"?), and, for one case, a synthetic control
   (:func:`synth_weights`: a weighted mix of comparison cities that tracks the
   city before the reform).
5. **Uncertainty.** With one reformed city per case, the honest interval comes
   from running the same estimator on every comparison city as if it had
   reformed (:func:`placebo_in_space`): the spread of those fake effects shows
   how much an ordinary city moves by chance. The pooled estimate across cases
   uses a cluster bootstrap that resamples whole cities (:func:`cluster_bootstrap`).
6. **Records.** Each case becomes a comparison record (the format of
   :mod:`visionhousing.evidence.schema`) with an identification label: ``causal``
   only when the pre-trend and placebo checks (and donor fit, for the synthetic
   control) pass the declared marks, else ``matched`` or ``descriptive``.

Results go to ``assumptions/evidence/retro_results.yaml``. They are evidence:
they may inform the range of a stage parameter (for example 2-4 unit take-up),
never a multiplier on a model run. Only yearly aggregates for the offices used
are written; raw files stay in ``data/raw/bps_place/``.

Everything statistical here is plain numpy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.evidence.records import load_panel
from visionhousing.evidence.schema import ComparisonRecord
from visionhousing.ledger.loader import ROOT

__all__ = [
    "DESIGN_PATH",
    "OUTCOMES",
    "REGIONS",
    "RESULTS_PATH",
    "Panel",
    "build_panel",
    "case_estimates",
    "cluster_bootstrap",
    "event_study",
    "excluded_states",
    "fetch_housing_units",
    "fetch_place_years",
    "identification_label",
    "load_design",
    "parse_place_annual",
    "placebo_in_space",
    "pretrend_stat",
    "project_simplex",
    "synth_control",
    "synth_weights",
    "treatment_year",
    "unit_effects",
]

DESIGN_PATH = ROOT / "assumptions" / "evidence" / "retro_design.yaml"
RESULTS_PATH = ROOT / "assumptions" / "evidence" / "retro_results.yaml"
RAW_DIR = ROOT / "data" / "raw"
PREREG_BLOCK = "retro_design"
"""The design's name in ``assumptions/preregistration.yaml``."""

REGIONS: tuple[tuple[str, str], ...] = (
    ("Northeast%20Region", "ne"),
    ("Midwest%20Region", "mw"),
    ("South%20Region", "so"),
    ("West%20Region", "we"),
)
"""BPS place-file folders (with the space escaped) and file prefixes."""

STRUCTURE_COLUMNS = {"1": "units_1", "2": "units_2", "3-4": "units_3_4", "5+": "units_5plus"}

OUTCOMES = ("units_1", "units_2", "units_3_4", "units_2_4", "units_5plus")
"""Outcome names; each is units authorized per 1,000 housing units per year."""

# Identity columns of the place files: the two header cells joined, lower case.
_PLACE_IDS = {
    "survey date": "survey_date",
    "state code": "state_fips",
    "6-digit id": "office_id",
    "county code": "county_fips",
    "census place code": "census_place",
    "fips place code": "place_fips",
    "fips mcd code": "mcd_fips",
    "pop": "pop",
    "csa code": "csa",
    "cbsa code": "cbsa",
    "footnote code": "footnote",
    "central city": "central_city",
    "zip code": "zip",
    "region code": "region",
    "division code": "division",
    "number of months rep": "months_rep",
    "place name": "name",
}


# =========================================================================== data


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.rstrip("\r\n").split(",")]


def place_header_names(row1: list[str], row2: list[str]) -> list[str]:
    """Join the two header rows of a place file into one column name per position.

    Count columns become ``{est|rep}_{structure}_{bldgs|units|value}``.

    Raises:
        ValueError: a header cell this module does not know.
    """
    from visionhousing.data.sources.bps import _structure

    width = max(len(row1), len(row2))
    row1 = row1 + [""] * (width - len(row1))
    row2 = row2 + [""] * (width - len(row2))
    names: list[str] = []
    group: tuple[str, bool] | None = None
    for i in range(width):
        measure = row2[i].lower()
        if measure in ("bldgs", "units", "value"):
            if measure == "bldgs":
                group = _structure(next((c for c in row1[i : i + 3] if c), ""))
            if group is None:
                raise ValueError(f"BPS place header: column {i} has no structure group")
            names.append(f"{'rep' if group[1] else 'est'}_{group[0]}_{measure}")
            continue
        joined = " ".join(f"{row1[i]} {row2[i]}".lower().split())
        if not joined:
            names.append(f"_blank{i}")
            continue
        if joined not in _PLACE_IDS:
            raise ValueError(f"BPS place header: unknown column {joined!r} at position {i}")
        names.append(_PLACE_IDS[joined])
    for s in STRUCTURE_COLUMNS:
        for kind in ("est", "rep"):
            if f"{kind}_{s}_units" not in names:
                raise ValueError(f"BPS place header: missing {kind} units for {s}")
    return names


def parse_place_annual(text: str, year: int) -> pd.DataFrame:
    """Read one BPS place annual file into one row per permit office.

    Returns:
        ``year, state_fips, office_id, county_fips, place_fips, name, pop,
        months_rep, units_1, units_2, units_3_4, units_5plus, reported_units``
        (``reported_units`` is the total of the reported-only columns).

    Raises:
        ValueError: the headers are not found or a row's year differs.
    """
    lines = [ln for ln in text.splitlines() if ln.strip().strip(",")]
    if len(lines) < 2 or not lines[0].lower().startswith("survey"):
        raise ValueError("BPS place file: the two header rows were not found")
    names = place_header_names(_cells(lines[0]), _cells(lines[1]))
    rows = [(_cells(ln) + [""] * len(names))[: len(names)] for ln in lines[2:]]
    frame = pd.DataFrame(rows, columns=names)
    frame = frame[frame["state_fips"].str.fullmatch(r"\d{1,2}")].copy()
    years = {int(str(v)[:4]) for v in frame["survey_date"]}
    if years and years != {year}:
        raise ValueError(f"BPS place file for {year}: survey dates say {sorted(years)}")
    out = pd.DataFrame(
        {
            "year": year,
            "state_fips": frame["state_fips"].str.zfill(2),
            "office_id": frame["office_id"].str.zfill(6),
            "county_fips": frame["county_fips"].str.zfill(3),
            "place_fips": frame["place_fips"].str.strip().str.zfill(5),
            "name": frame["name"].str.strip(),
            "pop": pd.to_numeric(frame["pop"], errors="coerce"),
            "months_rep": pd.to_numeric(frame["months_rep"], errors="coerce"),
        }
    )
    reported = 0
    for s, col in STRUCTURE_COLUMNS.items():
        out[col] = pd.to_numeric(frame[f"est_{s}_units"], errors="coerce").fillna(0)
        reported = reported + pd.to_numeric(frame[f"rep_{s}_units"], errors="coerce").fillna(0)
    out["reported_units"] = reported
    return out.reset_index(drop=True)


def _client(client: Any = None) -> Any:
    if client is not None:
        return client
    from visionhousing.data.http import HttpClient

    return HttpClient()


def fetch_place_years(
    years: Iterable[int],
    cache: Path = RAW_DIR,
    *,
    client: Any = None,
    keep: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Download (or reuse) every region's place file for ``years``; return offices and file log.

    Files are read one at a time and reduced by ``keep`` (default: offices with
    a BPS population of at least 10,000), so memory stays small. The log lists
    each file's address, size and SHA-256.
    """
    from visionhousing.data.registry import get_source

    source = get_source("bps_place_annual")
    client = _client(client)
    folder = Path(cache) / "bps_place"
    frames, log = [], []
    for year in sorted(set(int(y) for y in years)):
        for region_dir, code in REGIONS:
            url = source.url(region_dir=region_dir, region_code=code, year=year)
            dest = folder / f"{code}{year}a.txt"
            if not dest.exists():
                client.download(url, dest)
            raw = dest.read_bytes()
            log.append({"url": url, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            part = parse_place_annual(raw.decode("latin-1"), year)
            part = keep(part) if keep else part[(part["pop"] >= 10000)]
            frames.append(part)
    return pd.concat(frames, ignore_index=True), log


def fetch_housing_units(
    cache: Path = RAW_DIR, *, client: Any = None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Housing units (ACS 2020-2024 B25001) for every place and county.

    The national table is streamed and only place (``160``) and county
    (``050``) rows are written to ``cache/acs/b25001_place_county.dat``.

    Returns:
        ``(table, log)``: columns ``level`` (place or county), ``state_fips``,
        ``code`` (5-digit place or 3-digit county) and ``housing_units``.
    """
    from visionhousing.data.registry import get_source
    from visionhousing.data.sources.acs import _geo_id_keep, parse_summary_table, stream_filter

    client = _client(client)
    url = get_source("acs5_summary_file").url(table="b25001")
    dest = Path(cache) / "acs" / "b25001_place_county.dat"
    if not dest.exists():
        stream_filter(client, url, dest, _geo_id_keep(("1600000US", "0500000US")))
    raw = dest.read_bytes()
    table = parse_summary_table(dest)
    geo = table["GEO_ID"].str.split("US", n=1).str[1]
    out = pd.DataFrame(
        {
            "level": np.where(table["GEO_ID"].str.startswith("160"), "place", "county"),
            "state_fips": geo.str[:2],
            "code": geo.str[2:],
            "housing_units": table["B25001_001E"],
        }
    )
    log = {"url": url, "kept_bytes": len(raw), "sha256_kept": hashlib.sha256(raw).hexdigest()}
    return out, log


# =========================================================================== design


def load_design(path: Path = DESIGN_PATH) -> dict[str, Any]:
    """Read the predeclared design file."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def treatment_year(effective: date) -> int:
    """First calendar year mostly under the rule: the same year if on or before June 30."""
    return effective.year if (effective.month, effective.day) <= (6, 30) else effective.year + 1


def excluded_states(design: Mapping[str, Any], records: Iterable[ComparisonRecord]) -> set[str]:
    """States left out of the comparison pool: statewide ADU or middle-housing law in force.

    Every state with a verified statewide ``adu`` or ``missing_middle`` record
    whose first effective date is on or before the design's
    ``comparison_pool.law_in_force_by`` (2025-12-31), plus the design's
    ``excluded_states_extra``.
    """
    pool = design["comparison_pool"]
    cutoff = pool["law_in_force_by"]
    cutoff = cutoff if isinstance(cutoff, date) else date.fromisoformat(str(cutoff))
    out = set(pool.get("excluded_states_extra", {}))
    for r in records:
        if r.kind != "policy" or not r.verified or r.jurisdiction != "statewide":
            continue
        if not {"adu", "missing_middle"} & set(r.policy_types):
            continue
        if r.implementation_status in ("pending", "lapsed", "struck_down", "not_law"):
            continue
        first = min((e.date for e in r.effective_dates), default=None)
        if first is not None and first <= cutoff:
            out.add(r.state)
    return out


_FIPS_TO_POSTAL = {
    "01": "AL",
    "02": "AK",
    "04": "AZ",
    "05": "AR",
    "06": "CA",
    "08": "CO",
    "09": "CT",
    "10": "DE",
    "11": "DC",
    "12": "FL",
    "13": "GA",
    "15": "HI",
    "16": "ID",
    "17": "IL",
    "18": "IN",
    "19": "IA",
    "20": "KS",
    "21": "KY",
    "22": "LA",
    "23": "ME",
    "24": "MD",
    "25": "MA",
    "26": "MI",
    "27": "MN",
    "28": "MS",
    "29": "MO",
    "30": "MT",
    "31": "NE",
    "32": "NV",
    "33": "NH",
    "34": "NJ",
    "35": "NM",
    "36": "NY",
    "37": "NC",
    "38": "ND",
    "39": "OH",
    "40": "OK",
    "41": "OR",
    "42": "PA",
    "44": "RI",
    "45": "SC",
    "46": "SD",
    "47": "TN",
    "48": "TX",
    "49": "UT",
    "50": "VT",
    "51": "VA",
    "53": "WA",
    "54": "WV",
    "55": "WI",
    "56": "WY",
}


@dataclass
class Panel:
    """Outcome rates for every office kept, as arrays (offices x years).

    ``cohort`` is the treatment year ``g`` for a case and 0 for a pool office.
    ``rates[outcome]`` is units authorized per 1,000 housing units.
    """

    years: np.ndarray
    keys: list[str]
    names: list[str]
    states: list[str]
    housing_units: np.ndarray
    cohort: np.ndarray
    rates: dict[str, np.ndarray]
    case_records: dict[int, str] = field(default_factory=dict)  # row -> record id
    counts: dict[str, np.ndarray] = field(default_factory=dict)  # raw unit counts

    @property
    def pool(self) -> np.ndarray:
        """True for never-treated comparison offices."""
        return self.cohort == 0


def _office_key(state_fips: str, place_fips: str, county_fips: str) -> str:
    return f"{state_fips}{place_fips}" if place_fips != "00000" else f"{state_fips}c{county_fips}"


def build_panel(
    offices: pd.DataFrame,
    housing: pd.DataFrame,
    design: Mapping[str, Any],
    excluded: set[str],
    g_override: Mapping[str, int] | None = None,
) -> tuple[Panel, dict[str, Any]]:
    """Apply the design's pool rule and turn office-years into rates.

    ``g_override`` (record id -> treatment year) replaces the design's ``g`` for
    those cases (the treatment-date audit, :mod:`visionhousing.evidence.retro_audit`,
    which declares its own years and conventions); the timing-rule check then
    applies only to the cases not overridden.

    Returns:
        The panel and a summary of how many offices each pool rule removed.
    """
    pool_rule = design["comparison_pool"]
    first, last = design["data"]["years"]
    years = np.arange(first, last + 1)
    df = offices[(offices["year"] >= first) & (offices["year"] <= last)].copy()
    df["key"] = [
        _office_key(s, p, c)
        for s, p, c in zip(df["state_fips"], df["place_fips"], df["county_fips"], strict=True)
    ]
    # A place can have more than one office row in a year (rare); add them up.
    df["units_2_4"] = df["units_2"] + df["units_3_4"]
    cols = ["units_1", "units_2", "units_3_4", "units_2_4", "units_5plus"]
    agg = df.groupby(["key", "year"], as_index=False).agg(
        {
            **{c: "sum" for c in cols},
            "pop": "max",
            "months_rep": "min",
            "name": "first",
            "state_fips": "first",
        }
    )
    hu = housing.copy()
    hu["key"] = np.where(
        hu["level"] == "place", hu["state_fips"] + hu["code"], hu["state_fips"] + "c" + hu["code"]
    )
    hu_map = dict(zip(hu["key"], hu["housing_units"], strict=True))

    case_keys = {}
    for case in design["cases"]:
        o = case["office"]
        key = _office_key(o["state_fips"], o["place_fips"], o.get("county_fips", "000"))
        case_keys[key] = case
    reformers = {
        f"{r['state_fips']}{r['place_fips']}" for r in pool_rule.get("excluded_local_reformers", [])
    }
    pop_year = agg[agg["year"] == pool_rule["population_year"]].set_index("key")["pop"]
    n_years = agg.groupby("key")["year"].nunique()
    full = agg[agg["months_rep"] >= 12].groupby("key")["year"].nunique()

    why: dict[str, int] = {}
    keep_keys: list[str] = []
    for key in sorted(n_years.index):
        if key in case_keys:
            keep_keys.append(key)
            continue
        state = _FIPS_TO_POSTAL.get(key[:2], "??")
        checks = (
            ("population", pop_year.get(key, 0) >= pool_rule["min_population"]),
            ("all_years", n_years.get(key, 0) == len(years)),
            ("full_reporting", full.get(key, 0) >= pool_rule["min_full_report_years"]),
            ("housing_units", hu_map.get(key, 0) and hu_map.get(key, 0) > 0),
            ("excluded_state", state not in excluded),
            ("local_reformer", key not in reformers),
        )
        failed = next((name for name, ok in checks if not ok), None)
        if failed:
            why[failed] = why.get(failed, 0) + 1
        else:
            keep_keys.append(key)

    missing_cases = [c["name"] for k, c in case_keys.items() if k not in n_years.index]
    if missing_cases:
        raise ValueError(f"cases not found in the BPS place files: {missing_cases}")
    sub = agg[agg["key"].isin(keep_keys)]
    n = len(keep_keys)
    idx = {k: i for i, k in enumerate(keep_keys)}
    counts = {c: np.full((n, len(years)), np.nan) for c in cols}
    for row in sub.itertuples(index=False):
        i, t = idx[row.key], int(row.year) - first
        for c in cols:
            counts[c][i, t] = getattr(row, c)
    hu_arr = np.array([hu_map.get(k, np.nan) for k in keep_keys], dtype=float)
    rates = {c: counts[c] / hu_arr[:, None] * 1000.0 for c in cols}
    names = sub.groupby("key")["name"].last().reindex(keep_keys).tolist()
    cohort = np.zeros(n, dtype=int)
    case_records = {}
    for key, case in case_keys.items():
        if g_override and case["record"] in g_override:
            g = int(g_override[case["record"]])
        else:
            g = treatment_year(case["effective_date"])
            if g != case["g"]:
                raise ValueError(
                    f"{case['name']}: design g {case['g']} breaks the timing rule ({g})"
                )
        cohort[idx[key]] = g
        case_records[idx[key]] = case["record"]
        names[idx[key]] = case["name"]
    panel = Panel(
        years=years,
        keys=keep_keys,
        names=names,
        states=[_FIPS_TO_POSTAL.get(k[:2], "??") for k in keep_keys],
        housing_units=hu_arr,
        cohort=cohort,
        rates=rates,
        case_records=case_records,
        counts=counts,
    )
    summary = {
        "offices_read": len(n_years),
        "pool": int((cohort == 0).sum()),
        "cases": int((cohort > 0).sum()),
        "removed_by_rule": why,
        "excluded_states": sorted(excluded),
    }
    return panel, summary


# =========================================================================== estimators


def _base(y: np.ndarray, years: np.ndarray, g: int, base_years: int) -> np.ndarray:
    cols = (years >= g - base_years) & (years <= g - 1)
    if cols.sum() < base_years:
        return np.full(y.shape[0], np.nan)
    return y[:, cols].mean(axis=1)


def _controls(cohort: np.ndarray, g: int, t: int, not_yet_treated: bool = True) -> np.ndarray:
    """Never treated, or (if ``not_yet_treated``) treated later than both ``t`` and ``g``."""
    if not not_yet_treated:
        return cohort == 0
    return (cohort == 0) | (cohort > max(t, g))


def unit_effects(
    y: np.ndarray,
    years: np.ndarray,
    cohort: np.ndarray,
    units: Sequence[int],
    event_times: Sequence[int],
    *,
    base_years: int = 3,
    weights: np.ndarray | None = None,
    fake_g: Mapping[int, int] | None = None,
    not_yet_treated: bool = True,
) -> np.ndarray:
    """Group-time effects for each unit in ``units`` at each event time.

    ATT(i, t) = (y[i, t] - base_i) - weighted mean over controls of (y[c, t] - base_c),
    where base is the mean of the ``base_years`` years before ``g`` (the unit's
    cohort, or ``fake_g[i]`` for a placebo) and controls are never-treated or
    not-yet-treated units other than ``i``.

    Args:
        y: Outcome array, units x years.
        years: Calendar year of each column.
        cohort: Treatment year per unit; 0 = never treated.
        units: Rows to estimate for.
        event_times: Values of ``e = t - g``.
        base_years: Years in the base.
        weights: Optional weight per unit for the control mean (bootstrap counts).
        fake_g: Optional treatment year to use for a unit instead of its cohort.
        not_yet_treated: Also use later cases as controls before their own ``g``
            (the original design); ``False`` uses never-treated units only.

    Returns:
        Array (len(units), len(event_times)); NaN where ``t`` is outside the data.
    """
    w = np.ones(y.shape[0]) if weights is None else np.asarray(weights, dtype=float)
    out = np.full((len(units), len(event_times)), np.nan)
    col = {int(y): j for j, y in enumerate(years)}
    for a, i in enumerate(units):
        g = int(fake_g[i]) if fake_g and i in fake_g else int(cohort[i])
        if g <= 0:
            raise ValueError(f"unit {i} has no treatment year")
        base = _base(y, years, g, base_years)
        for b, e in enumerate(event_times):
            t = g + int(e)
            if t not in col:
                continue
            ctrl = _controls(cohort, g, t, not_yet_treated)
            ctrl[i] = False
            d = y[:, col[t]] - base
            ok = ctrl & np.isfinite(d) & (w > 0)
            if not ok.any() or not np.isfinite(d[i]):
                continue
            out[a, b] = d[i] - np.average(d[ok], weights=w[ok])
    return out


def placebo_in_space(
    y: np.ndarray,
    years: np.ndarray,
    cohort: np.ndarray,
    g: int,
    event_times: Sequence[int],
    *,
    base_years: int = 3,
    exclude: Iterable[int] = (),
    not_yet_treated: bool = True,
) -> np.ndarray:
    """Run :func:`unit_effects` on every never-treated unit as if it reformed in ``g``.

    Vectorised: for each event time, each pool unit's effect is its change
    minus the mean change of the other controls (the same control set a real
    case reforming in ``g`` would use, minus the placebo unit itself).

    Returns:
        Array (pool units, len(event_times)).
    """
    pool = np.flatnonzero(cohort == 0)
    pool = np.array([p for p in pool if p not in set(exclude)], dtype=int)
    out = np.full((len(pool), len(event_times)), np.nan)
    col = {int(y): j for j, y in enumerate(years)}
    base = _base(y, years, g, base_years)
    for b, e in enumerate(event_times):
        t = g + int(e)
        if t not in col:
            continue
        ctrl = _controls(cohort, g, t, not_yet_treated)
        ctrl[list(exclude)] = False
        d = y[:, col[t]] - base
        ok = ctrl & np.isfinite(d)
        s, n = d[ok].sum(), ok.sum()
        dp = d[pool]
        mine = ok[pool]
        loo = np.where(mine, (s - dp) / np.maximum(n - 1, 1), s / max(n, 1))
        out[:, b] = dp - loo
    return out


def event_study(effects: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    """Average unit effects (units x event times) into one coefficient per event time."""
    w = np.ones(effects.shape[0]) if weights is None else np.asarray(weights, dtype=float)
    out = np.full(effects.shape[1], np.nan)
    for b in range(effects.shape[1]):
        ok = np.isfinite(effects[:, b]) & (w > 0)
        if ok.any():
            out[b] = np.average(effects[ok, b], weights=w[ok])
    return out


def _headline(row: np.ndarray) -> float:
    vals = row[np.isfinite(row)]
    return float(vals.mean()) if vals.size else float("nan")


def pretrend_stat(coefs: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """Sum over pre-period event times of (coefficient / scale) squared; rows are units."""
    z = np.asarray(coefs, dtype=float) / np.where(scale > 0, scale, np.nan)
    return np.nansum(z**2, axis=-1)


def _rank_p(value: float, placebo: np.ndarray) -> float:
    """One-sided rank p-value: share of placebo values at least as large (with +1)."""
    placebo = placebo[np.isfinite(placebo)]
    return float((1 + (placebo >= value).sum()) / (1 + placebo.size))


def cluster_bootstrap(
    stat: Callable[[np.ndarray], np.ndarray],
    treated: np.ndarray,
    pool: np.ndarray,
    n_units: int,
    *,
    draws: int = 999,
    seed: int = 0,
) -> np.ndarray:
    """Resample whole units (clusters) with replacement, treated and pool separately.

    ``stat(weights)`` gets a count per unit (how often it was drawn) and returns
    an array. Returns the stacked draws (draws x len(stat)).
    """
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(draws):
        w = np.zeros(n_units)
        np.add.at(w, rng.choice(treated, size=treated.size, replace=True), 1)
        np.add.at(w, rng.choice(pool, size=pool.size, replace=True), 1)
        out.append(np.atleast_1d(stat(w)))
    return np.vstack(out)


def pooled_estimate(
    y: np.ndarray,
    years: np.ndarray,
    cohort: np.ndarray,
    event_times: Sequence[int],
    post: Sequence[int],
    pre: Sequence[int],
    *,
    base_years: int = 3,
    draws: int = 999,
    seed: int = 0,
    level: float = 0.90,
    not_yet_treated: bool = True,
) -> dict[str, Any]:
    """Event study over every treated unit, with a cluster bootstrap interval and pre-trend test.

    The overall effect is the mean over treated units of each unit's mean
    post-period effect (equal weight per unit). The pre-trend test is a Wald
    statistic on the pre-period coefficients with the bootstrap covariance; its
    p-value comes from the centred bootstrap distribution of the same statistic.
    """
    treated = np.flatnonzero(cohort > 0)
    pool = np.flatnonzero(cohort == 0)
    ev = list(event_times)
    post_idx = [ev.index(e) for e in post]
    pre_idx = [ev.index(e) for e in pre]

    def stat(w: np.ndarray) -> np.ndarray:
        units = np.flatnonzero(w[treated] > 0)
        tw = w[treated][units]
        eff = unit_effects(
            y, years, cohort, treated[units], ev, base_years=base_years, weights=w,
            not_yet_treated=not_yet_treated,
        )  # fmt: skip
        heads = np.array([_headline(r[post_idx]) for r in eff])
        ok = np.isfinite(heads)
        overall = np.average(heads[ok], weights=tw[ok]) if ok.any() else np.nan
        return np.concatenate([[overall], event_study(eff, tw)])

    point = stat(np.ones(y.shape[0]))
    boot = cluster_bootstrap(stat, treated, pool, y.shape[0], draws=draws, seed=seed)
    lo, hi = (1 - level) / 2, 1 - (1 - level) / 2
    q = np.nanquantile(boot, [lo, hi], axis=0)
    pre_point = point[1:][pre_idx]
    pre_boot = boot[:, 1:][:, pre_idx]
    ok = np.all(np.isfinite(pre_boot), axis=1)
    centred = pre_boot[ok] - pre_point
    cov = np.cov(centred, rowvar=False).reshape(len(pre), len(pre))
    inv = np.linalg.pinv(cov)
    w_obs = float(pre_point @ inv @ pre_point)
    w_boot = np.einsum("ij,jk,ik->i", centred, inv, centred)
    return {
        "overall": float(point[0]),
        "ci": [float(q[0, 0]), float(q[1, 0])],
        "event_times": ev,
        "coefs": point[1:].tolist(),
        "coef_ci": [q[0, 1:].tolist(), q[1, 1:].tolist()],
        "pretrend_wald": w_obs,
        "pretrend_p": float((1 + (w_boot >= w_obs).sum()) / (1 + w_boot.size)),
        "treated": int(treated.size),
        "comparisons": int(pool.size),
        "draws": draws,
    }


def case_estimates(
    y: np.ndarray,
    years: np.ndarray,
    cohort: np.ndarray,
    unit: int,
    *,
    horizon: int = 5,
    pre_times: Sequence[int] = (-6, -5, -4),
    base_years: int = 3,
    placebo_shift: int = 3,
    level: float = 0.90,
    draws: int = 999,
    seed: int = 0,
    not_yet_treated: bool = True,
) -> dict[str, Any]:
    """Everything for one reformed unit: effect, interval, pre-trend and placebo-date checks.

    The interval inverts the placebo-in-space distribution: estimate minus the
    upper and lower quantiles of the placebo headline effects. A narrower
    comparison-sampling interval (bootstrap over comparison units only) is also
    returned for reference.
    """
    g = int(cohort[unit])
    last = int(years.max())
    post = [e for e in range(horizon) if g + e <= last]
    ev = list(pre_times) + post
    nyt = {"not_yet_treated": not_yet_treated}
    eff = unit_effects(y, years, cohort, [unit], ev, base_years=base_years, **nyt)[0]
    plac = placebo_in_space(y, years, cohort, g, ev, base_years=base_years, exclude=[unit], **nyt)
    k = len(pre_times)
    head = _headline(eff[k:])
    plac_head = np.array([_headline(r[k:]) for r in plac])
    plac_head = plac_head[np.isfinite(plac_head)]
    lo, hi = (1 - level) / 2, 1 - (1 - level) / 2
    ci = [head - float(np.quantile(plac_head, hi)), head - float(np.quantile(plac_head, lo))]
    p_att = _rank_p(abs(head), np.abs(plac_head))
    scale = np.nanstd(plac[:, :k], axis=0)
    w_case = float(pretrend_stat(eff[:k], scale))
    w_plac = pretrend_stat(plac[:, :k], scale)
    p_pre = _rank_p(w_case, w_plac)
    # Placebo date: the same estimator, reform moved earlier, using only years before g.
    keep = years < g
    fake = g - placebo_shift
    fake_ev = list(range(placebo_shift))
    y_pre, yp = y[:, keep], years[keep]
    cp = cohort.copy()
    cp[unit] = fake
    pd_eff = unit_effects(y_pre, yp, cp, [unit], fake_ev, base_years=base_years, **nyt)[0]
    pd_plac = placebo_in_space(
        y_pre, yp, cp, fake, fake_ev, base_years=base_years, exclude=[unit], **nyt
    )
    pd_head = _headline(pd_eff)
    pd_plac_head = np.array([_headline(r) for r in pd_plac])
    p_date = _rank_p(abs(pd_head), np.abs(pd_plac_head[np.isfinite(pd_plac_head)]))
    # Comparison-sampling bootstrap (treated unit fixed, comparisons resampled).
    pool = np.flatnonzero(cohort == 0)
    rng = np.random.default_rng(seed)
    boot = []
    post_ev = post
    for _ in range(draws):
        w = np.zeros(y.shape[0])
        np.add.at(w, rng.choice(pool, size=pool.size, replace=True), 1)
        w[cohort > 0] = 1
        r = unit_effects(
            y, years, cohort, [unit], post_ev, base_years=base_years, weights=w, **nyt
        )[0]
        boot.append(_headline(r))
    boot = np.array(boot)
    return {
        "g": g,
        "post_years": len(post),
        "att": head,
        "ci": ci,
        "p_value": p_att,
        "ci_comparison_sampling": [
            float(np.nanquantile(boot, lo)),
            float(np.nanquantile(boot, hi)),
        ],
        "event_times": ev,
        "coefs": eff.tolist(),
        "placebo_sd": np.nanstd(plac, axis=0).tolist(),
        "pretrend_stat": w_case,
        "pretrend_p": p_pre,
        "placebo_date": {"fake_g": fake, "effect": pd_head, "p": p_date},
        "comparisons": int(np.isfinite(plac_head).sum()),
    }


# --------------------------------------------------------------------------- synthetic control


def project_simplex(v: np.ndarray) -> np.ndarray:
    """Euclidean projection of ``v`` onto {w >= 0, sum w = 1} (sort-based)."""
    u = np.sort(v)[::-1]
    css = np.cumsum(u)
    rho = np.nonzero(u * np.arange(1, v.size + 1) > (css - 1))[0][-1]
    theta = (css[rho] - 1) / (rho + 1.0)
    return np.maximum(v - theta, 0.0)


def synth_weights(
    y1: np.ndarray, y0: np.ndarray, *, iters: int = 5000, tol: float = 1e-12
) -> np.ndarray:
    """Donor weights: minimise ||y1 - y0 w||^2 with w >= 0 and sum w = 1.

    Accelerated projected gradient (FISTA) on the simplex. ``y1`` is the treated
    unit's pre-period path (T0,), ``y0`` the donors' (T0, J).
    """
    n_donors = y0.shape[1]
    lip = 2.0 * np.linalg.norm(y0, 2) ** 2 or 1.0
    w = np.full(n_donors, 1.0 / n_donors)
    z, t = w.copy(), 1.0
    prev = np.inf
    for _ in range(iters):
        grad = 2.0 * y0.T @ (y0 @ z - y1)
        w_new = project_simplex(z - grad / lip)
        t_new = (1 + np.sqrt(1 + 4 * t * t)) / 2
        z = w_new + ((t - 1) / t_new) * (w_new - w)
        w, t = w_new, t_new
        loss = float(np.sum((y0 @ w - y1) ** 2))
        if abs(prev - loss) < tol:
            break
        prev = loss
    return w


def synth_control(
    y: np.ndarray,
    years: np.ndarray,
    unit: int,
    donors: np.ndarray,
    g: int,
    *,
    horizon: int = 5,
    names: Sequence[str] | None = None,
    placebos: bool = True,
) -> dict[str, Any]:
    """Synthetic control for one unit: weights, pre-period fit, post-period gaps, placebo rank.

    Fit ratio = pre-period RMSPE / mean of the unit's pre-period outcome. The
    placebo rank compares the unit's post/pre RMSPE ratio with the same ratio
    for each donor treated in turn (fitted on the other donors).
    """
    pre = years < g
    post = (years >= g) & (years < g + horizon)

    def fit(i: int, pool: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        w = synth_weights(y[i, pre], y[np.ix_(pool, pre)].T)
        return w, y[i] - w @ y[pool]

    w, gap = fit(unit, donors)
    rmspe_pre = float(np.sqrt(np.mean(gap[pre] ** 2)))
    rmspe_post = float(np.sqrt(np.mean(gap[post] ** 2)))
    mean_pre = float(np.mean(y[unit, pre]))
    ratio = rmspe_post / rmspe_pre if rmspe_pre > 0 else float("inf")
    out: dict[str, Any] = {
        "g": g,
        "pre_rmspe": rmspe_pre,
        "pre_mean": mean_pre,
        "fit_ratio": rmspe_pre / mean_pre if mean_pre > 0 else float("inf"),
        "post_gap_mean": float(np.mean(gap[post])),
        "gaps": {int(y): float(v) for y, v in zip(years, gap, strict=True)},
        "top_donors": [
            {"name": (names[donors[j]] if names else str(donors[j])), "weight": float(w[j])}
            for j in np.argsort(w)[::-1][:5]
            if w[j] > 1e-4
        ],
        "post_pre_ratio": ratio,
    }
    if placebos:
        ratios, post_means = [], []
        for d in donors:
            others = donors[donors != d]
            _, gp = fit(d, others)
            rp = np.sqrt(np.mean(gp[pre] ** 2))
            ratios.append(np.sqrt(np.mean(gp[post] ** 2)) / rp if rp > 0 else np.nan)
            post_means.append(float(np.mean(gp[post])))
        ratios_a = np.array(ratios)
        out["placebo_rank_p"] = _rank_p(ratio, ratios_a)
        lo, hi = np.nanquantile(post_means, [0.05, 0.95])
        out["post_gap_ci"] = [out["post_gap_mean"] - float(hi), out["post_gap_mean"] - float(lo)]
    return out


# =========================================================================== labels and records


def identification_label(
    est: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    *,
    min_post_years: int,
    donor_fit: float | None = None,
) -> tuple[str, list[str]]:
    """Apply the declared rule; return the label and the reasons for it."""
    reasons = []
    if est["post_years"] < min_post_years or est["comparisons"] < thresholds["min_comparisons"]:
        return "descriptive", [
            f"post years {est['post_years']} (need {min_post_years}), "
            f"comparisons {est['comparisons']} (need {thresholds['min_comparisons']})"
        ]
    if est["pretrend_p"] < thresholds["pretrend_p_min"]:
        reasons.append(f"pre-trend p {est['pretrend_p']:.2f} < {thresholds['pretrend_p_min']}")
    if est["placebo_date"]["p"] < thresholds["placebo_date_p_min"]:
        reasons.append(
            f"placebo-date p {est['placebo_date']['p']:.2f} < {thresholds['placebo_date_p_min']}"
        )
    if donor_fit is not None and donor_fit > thresholds["donor_fit_max"]:
        reasons.append(f"donor fit {donor_fit:.2f} > {thresholds['donor_fit_max']}")
    if reasons:
        return "matched", reasons
    return "causal", ["pre-trend, placebo-date and pool-size checks passed"]


def _r(x: float, n: int = 3) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def _clean(obj: Any) -> Any:
    """Round floats and turn numpy types into plain YAML values."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, np.ndarray)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        return _r(float(obj), 4)
    return obj


BPS_PAGE = "https://www.census.gov/construction/bps/index.html"


def _record(
    case: Mapping[str, Any],
    reform: ComparisonRecord,
    outcome: str,
    est: Mapping[str, Any],
    label: str,
    reasons: list[str],
    design: Mapping[str, Any],
    hu: float,
    today: date,
    extra_caveats: list[str],
) -> dict[str, Any]:
    out_label = design["outcomes"]["all"][outcome]
    lo, hi = est["ci"]
    per_year = est["att"] * hu / 1000.0
    summary = (
        f"{case['name']}: {out_label.lower()} per 1,000 homes changed by {est['att']:+.2f} a year "
        f"against the comparison cities over {est['post_years']} years from {est['g']} "
        f"(90% placebo interval {lo:+.2f} to {hi:+.2f}; about {per_year:+.0f} units a year). "
        f"Pre-trend p {est['pretrend_p']:.2f}; placebo-date p {est['placebo_date']['p']:.2f}."
    )
    if lo <= 0 <= hi:
        summary += " The interval includes zero: no change distinguishable from ordinary cities."
    rec = {
        "id": f"{reform.state.lower()}.retro.{reform.id.split('.', 1)[1]}.{outcome}",
        "kind": "study",
        "state": reform.state,
        "jurisdiction": reform.jurisdiction,
        "policy_types": list(reform.policy_types),
        "title": f"Retrospective permit panel: {reform.title} ({out_label.lower()})",
        "citation": (
            "VisionHousing retrospective panel (Census BPS place files and ACS B25001), "
            f"design {design['id']}"
        ),
        "url": BPS_PAGE,
        "geographic_scope": reform.geographic_scope,
        "implementation_status": "not_law",
        "litigation": {"status": "not_applicable"},
        "outcome_measure": f"{out_label} authorized by building permits",
        "denominator": "per 1,000 housing units (ACS 2020-2024 5-year, table B25001)",
        "horizon": f"{est['post_years']} calendar years from {est['g']} (event times 0 to "
        f"{est['post_years'] - 1})",
        "observed_result": {
            "summary": summary,
            "value": _r(est["att"]),
            "unit": "units per 1,000 housing units per year (difference from comparison)",
            "source_label": "Census Building Permits Survey",
            "source_url": BPS_PAGE,
        },
        "identification": label,
        "caveats": [*reasons, *extra_caveats, *design["caveats_always"]],
        "retrieved": today,
        "verified": True,
        "verification_note": (
            f"Effective date from verified record {reform.id}; permit counts from the Census "
            "Bureau's own files; computed by visionhousing evidence-retro."
        ),
        "sources": [
            {"label": "Census Building Permits Survey", "url": BPS_PAGE, "official": True},
            {"label": f"Reform record {reform.id}", "url": reform.url, "official": True},
        ],
        "discovered_via": "addendum",
    }
    ComparisonRecord.model_validate(rec)  # stop here if the schema would refuse it
    return rec


HOW_TO_USE = [
    "These are evidence records, not model inputs. No number here is multiplied into a run.",
    "Use a case only to check or bound a stage parameter: for example whether a 2-4 unit "
    "take-up range implies more or fewer 2-4 unit permits per 1,000 homes than reformed cities "
    "actually saw in their first years.",
    "Read the interval, not the point. Each case has one reformed city, so the interval shows "
    "how much ordinary cities move by chance.",
    "A 'matched' or 'descriptive' label means a check failed: the number is a before/after "
    "contrast with comparison cities, not an effect of the reform.",
    "Never apply a city's change as a blanket multiplier on Pennsylvania: the reforms differ, the "
    "markets differ, and most effects mix the reform with other changes named in the caveats.",
]


def run_estimates(
    panel: Panel,
    design: Mapping[str, Any],
    reforms: Mapping[str, ComparisonRecord],
    *,
    today: date | None = None,
    draws: int | None = None,
    not_yet_treated: bool = True,
) -> dict[str, Any]:
    """Estimate every case, the pooled event study and the synthetic control; build records.

    ``not_yet_treated=False`` uses never-treated comparisons only (the audit's rule).
    """
    today = today or date.today()
    tm, est_cfg, th = design["timing"], design["estimators"], design["thresholds"]
    draws = draws or int(est_cfg["bootstrap_draws"])
    seed = int(est_cfg["seed"])
    pre = list(tm["pretrend_event_times"])
    horizon = int(tm["horizon_years"])
    by_record = {c["record"]: c for c in design["cases"]}
    sc_cfg = est_cfg["synthetic_control"]
    cases_out, records = [], []
    for unit, rec_id in sorted(panel.case_records.items(), key=lambda kv: kv[1]):
        case = by_record[rec_id]
        reform = reforms[rec_id]
        per_outcome = {}
        for outcome in design["outcomes"]["all"]:
            est = case_estimates(
                panel.rates[outcome],
                panel.years,
                panel.cohort,
                unit,
                horizon=horizon,
                pre_times=pre,
                base_years=int(tm["base_years"]),
                placebo_shift=int(tm["placebo_shift_years"]),
                level=float(est_cfg["ci_level"]),
                draws=min(draws, 199),
                seed=seed,
                not_yet_treated=not_yet_treated,
            )
            sc = None
            if rec_id == sc_cfg["case"] and outcome in sc_cfg["outcomes"]:
                sc = synth_control(
                    panel.rates[outcome],
                    panel.years,
                    unit,
                    np.flatnonzero(panel.pool),
                    est["g"],
                    horizon=horizon,
                    names=panel.names,
                )
                est["synthetic_control"] = sc
            label, reasons = identification_label(
                est,
                th,
                min_post_years=int(tm["min_post_years"]),
                donor_fit=sc["fit_ratio"] if sc else None,
            )
            est["identification"] = label
            est["label_reasons"] = reasons
            est["base_rate"] = float(
                np.mean(
                    panel.rates[outcome][
                        unit, (panel.years >= est["g"] - 3) & (panel.years < est["g"])
                    ]
                )
            )
            per_outcome[outcome] = est
            if outcome == design["outcomes"]["primary"]:
                extra = [f"Concurrent reform: {c}" for c in case.get("concurrent", [])]
                extra += list(reform.caveats)
                if sc:
                    extra.append(
                        f"Synthetic control: pre-period fit ratio {sc['fit_ratio']:.2f}, mean "
                        f"post gap {sc['post_gap_mean']:+.2f} (90% placebo interval "
                        f"{sc['post_gap_ci'][0]:+.2f} to {sc['post_gap_ci'][1]:+.2f}), "
                        f"placebo rank p {sc['placebo_rank_p']:.2f}; a near-perfect fit from "
                        "many donors over few years can be over-fitting"
                    )
                records.append(
                    _record(
                        case,
                        reform,
                        outcome,
                        est,
                        label,
                        reasons,
                        design,
                        float(panel.housing_units[unit]),
                        today,
                        extra,
                    )
                )
        cases_out.append(
            {
                "record": rec_id,
                "name": case["name"],
                "effective_date": str(case["effective_date"]),
                "g": int(panel.cohort[unit]),
                "housing_units": float(panel.housing_units[unit]),
                "series": {
                    o: dict(zip(panel.years.tolist(), panel.rates[o][unit].tolist(), strict=True))
                    for o in design["outcomes"]["all"]
                },
                "estimates": per_outcome,
            }
        )
    pooled = {}
    last = int(panel.years.max())
    post = list(range(horizon))
    ev = pre + post
    for outcome in design["outcomes"]["all"]:
        pooled[outcome] = pooled_estimate(
            panel.rates[outcome],
            panel.years,
            panel.cohort,
            ev,
            [e for e in post if e <= last - int(panel.cohort[panel.cohort > 0].min())],
            pre,
            base_years=int(tm["base_years"]),
            draws=draws,
            seed=seed,
            level=float(est_cfg["ci_level"]),
            not_yet_treated=not_yet_treated,
        )
    pool = panel.pool
    comparison_mean = {
        o: dict(
            zip(
                panel.years.tolist(), np.nanmean(panel.rates[o][pool], axis=0).tolist(), strict=True
            )
        )
        for o in design["outcomes"]["all"]
    }
    return {
        "cases": cases_out,
        "pooled": pooled,
        "comparison_mean": comparison_mean,
        "records": records,
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_results(
    results: Mapping[str, Any],
    summary: Mapping[str, Any],
    files: list[dict[str, Any]],
    hu_log: Mapping[str, Any],
    design_path: Path,
    out: Path,
    today: date,
    preregistration: Mapping[str, Any] | None = None,
) -> Path:
    """Write ``retro_results.yaml``: provenance, how-to-use note, records and estimates.

    ``design_sha256`` is the SHA-256 of the design file's bytes (what
    :func:`check_results` compares); ``preregistration`` is the verified
    fingerprint of the design's values (:mod:`visionhousing.validation.preregistration`).
    """
    doc = {
        "version": 1,
        "id": "retro_bps_results",
        "label": "Retrospective reform panel: permits by structure type after local reforms",
        "use": "evidence only; informs stage parameter ranges; never a multiplier",
        "how_to_use": HOW_TO_USE,
        "provenance": {
            "measured_on": str(today),
            "generated_by": "visionhousing evidence-retro run",
            "design_file": "assumptions/evidence/retro_design.yaml",
            "design_sha256": _sha(design_path),
            "preregistration": dict(preregistration or {}),
            "method": "docs/modules/evidence_retro.md",
            "panel": dict(summary),
            "sources": [
                {
                    "id": "bps_place_annual",
                    "files": len(files),
                    "bytes": int(sum(f["bytes"] for f in files)),
                    "sha256_of_file_hashes": hashlib.sha256(
                        "".join(f["sha256"] for f in files).encode()
                    ).hexdigest(),
                    "licence": "Public domain (U.S. government work)",
                },
                {
                    "id": "acs5_summary_file",
                    "table": "b25001",
                    **hu_log,
                    "licence": "Public domain (U.S. government work)",
                },
            ],
            "stored": "Only yearly rates for the case offices, the comparison mean and estimates.",
        },
        "records": results["records"],
        "cases": results["cases"],
        "pooled": results["pooled"],
        "comparison_mean": results["comparison_mean"],
    }
    header = (
        "# Retrospective reform panel results (generated; do not edit).\n"
        "# Written by `visionhousing evidence-retro run` from the predeclared design in\n"
        "# assumptions/evidence/retro_design.yaml. Evidence only: nothing here enters a\n"
        "# model run. Rates are units authorized per 1,000 housing units per year.\n"
        "# Method: docs/modules/evidence_retro.md.\n\n"
    )
    text = yaml.safe_dump(_clean(doc), sort_keys=False, width=100, allow_unicode=False)
    out.write_text(header + text, encoding="utf-8")
    return out


def check_results(
    path: Path = RESULTS_PATH,
    design_path: Path = DESIGN_PATH,
    registry: Mapping[str, Any] | None = None,
) -> list[str]:
    """Check a results file: its records, and that it came from today's registered design.

    Three checks: every record against the comparison-record schema; the
    results' ``design_sha256`` against the SHA-256 of the design file now (a
    design edited after the run means the results are stale); and the design's
    values against the fingerprint recorded in ``assumptions/preregistration.yaml``.
    """
    from visionhousing.validation.preregistration import PreregistrationError, verify

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    problems = []
    recorded = (data.get("provenance") or {}).get("design_sha256")
    now = _sha(design_path)
    if recorded != now:
        problems.append(
            f"design_sha256 {str(recorded)[:12]}... does not match the design file now "
            f"({now[:12]}...): the design changed after this run; run `evidence-retro run` again"
        )
    try:
        verify(PREREG_BLOCK, load_design(design_path), registry)
    except PreregistrationError as err:
        problems.append(str(err))
    for raw in data.get("records", []):
        try:
            ComparisonRecord.model_validate(raw)
        except Exception as err:
            problems.append(f"{raw.get('id')}: {err}")
    return problems


# =========================================================================== command


def add_parser(sub: argparse._SubParsersAction) -> None:
    """Add the ``evidence-retro`` sub-command."""
    p = sub.add_parser(
        "evidence-retro",
        help="Retrospective reform panel: permits after real local reforms (BPS place files)",
    )
    p.add_argument(
        "action",
        nargs="?",
        default="check",
        choices=["run", "check", "audit", "audit-check"],
        help=(
            "run (fetch, estimate, write results), check (validate results), audit "
            "(treatment-date audit: re-score the pre-registered variants, write the "
            "before/after file) or audit-check (validate the audit file)"
        ),
    )
    p.add_argument("--raw", type=Path, default=RAW_DIR, help="folder for raw downloads")
    p.add_argument("--out", type=Path, default=RESULTS_PATH, help="results file")
    p.add_argument("--draws", type=int, default=None, help="bootstrap draws (default: design)")


def run(args: argparse.Namespace) -> int:
    """Run the chosen action; return the exit code."""
    if args.action in ("audit", "audit-check"):
        from visionhousing.evidence import retro_audit

        if args.action == "audit-check":
            problems = retro_audit.check_audit()
            for line in problems:
                print("  " + line)
            print(f"evidence-retro audit: {'ok' if not problems else f'{len(problems)} problems'}")
            return 1 if problems else 0
        doc = retro_audit.run_audit(args.raw, draws=args.draws)
        for row in doc["before_after"]:
            b, a = row["before"], row["after"]
            print(
                f"{row['name']:<18} before g={b['g']} {b['att']:+.3f} "
                f"[{b['ci'][0]:+.3f},{b['ci'][1]:+.3f}] {b['identification']} | after g={a['g']} "
                f"{a['att']:+.3f} [{a['ci'][0]:+.3f},{a['ci'][1]:+.3f}] {a['identification']}"
                f"{'' if row['robust_to_convention'] else ' (not robust to convention)'}"
            )
        print(f"wrote {retro_audit.OUT_PATH}")
        return 0
    if args.action == "check":
        problems = check_results(args.out)
        for line in problems:
            print("  " + line)
        print(f"evidence-retro: {'ok' if not problems else f'{len(problems)} problems'}")
        return 1 if problems else 0
    from visionhousing.validation.preregistration import verify

    design = load_design()
    prereg = verify(PREREG_BLOCK, design)  # refuses to run on a changed design
    panel_records = load_panel()
    reforms = panel_records.by_id()
    for case in design["cases"]:
        rec = reforms.get(case["record"])
        if rec is None or not rec.verified:
            raise SystemExit(f"case {case['record']} needs a verified record")
        if case["effective_date"] not in [e.date for e in rec.effective_dates]:
            raise SystemExit(f"case {case['record']}: effective date not in its record")
    first, last = design["data"]["years"]
    offices, files = fetch_place_years(range(first, last + 1), args.raw)
    housing, hu_log = fetch_housing_units(args.raw)
    excluded = excluded_states(design, panel_records.records)
    panel, summary = build_panel(offices, housing, design, excluded)
    today = date.today()
    results = run_estimates(panel, design, reforms, today=today, draws=args.draws)
    path = write_results(
        results, summary, files, hu_log, DESIGN_PATH, args.out, today, preregistration=prereg
    )
    print(json.dumps(summary))
    for c in results["cases"]:
        e = c["estimates"][design["outcomes"]["primary"]]
        print(
            f"{c['name']:<18} g={c['g']} att={e['att']:+.3f} ci=[{e['ci'][0]:+.3f},"
            f"{e['ci'][1]:+.3f}] pre_p={e['pretrend_p']:.2f} "
            f"date_p={e['placebo_date']['p']:.2f} -> {e['identification']}"
        )
    print(f"wrote {path}")
    return 0
