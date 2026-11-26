"""Cost and affordability tables: cost burden, income limits, assisted homes, expirations.

Plain-language summary
----------------------
This module turns the extracts fetched by
:mod:`visionhousing.data.sources.affordability` into four small pack tables,
and gives the engine two plain functions to compare a rent with an income.

Terms used once and then throughout:

- **AMI** (area median income): HUD's median family income for the area
  (``HAMFI`` in CHAS). Income limits are shares of it: 30% ("extremely low
  income"), 50% ("very low income") and 80% ("low income"), set for each
  household size from 1 to 8 people.
- **Cost burden**: housing costs (gross rent for renters; mortgage, taxes,
  insurance and utilities for owners) over household income. Over 30% is
  "cost-burdened"; over 50% is "severely cost-burdened".
- **Assisted homes**: homes whose rent is held down by a federal program:
  project-based rental assistance contracts (``hud_mf_assisted``), public
  housing (``hud_public_housing``) and Low-Income Housing Tax Credit
  restrictions (``hud_lihtc``). Housing Choice Vouchers move with the tenant,
  so they are counted as assisted *households*, never added to homes.

Tables
------
=========================  ==============================================================
table                      one row per
=========================  ==============================================================
``afford_chas``            state and county: households by tenure x income band x cost
                           burden (CHAS Table 8); renter homes by the income their rent
                           is affordable to (15C); renters in older buildings (12); all
                           with margins of error.
``afford_chas_tract``      tract: the same renter cells collapsed to three income groups
                           (:func:`collapse_renters`), no margins, to keep the pack small.
``afford_income_limits``   county: AMI and the 30/50/80% limits for 1-8 people.
``afford_assisted``        state, county and tract: assisted homes by program, units
                           whose contract or restriction ends within 5 and 10 years,
                           voucher households.
``stock_ledger_county``    state and county: the stock ledger (existing homes, recent
                           permits, assisted homes, homes at risk, lower-rent and older
                           rental stock; displacement context).
=========================  ==============================================================

How each number is worked out
-----------------------------
- CHAS cells are published counts (rounded by the Census Bureau). Bands are
  sums of published cells; a band's margin is the square root of the sum of
  squared margins (the Bureau's approximation for a sum).
- Expiring contracts: a property's first two contracts each carry units and an
  end date. Units whose end date falls after the day the list was read and
  within ``N`` years of it are "expiring within N years"; a date already past
  is counted apart ("date passed": usually a renewal in progress).
- Tax-credit restrictions end ``lihtc_extended_use_years`` (30) after the year
  placed in service; low-income units (all units where the low-income count is
  blank) ending within the window are counted. Unknown years (8888, 9999) are
  left out and counted.
- At risk ``at_risk_{N}y`` = contract units ending + tax-credit units ending
  - units counted on both lists. A tax-credit project and a contract property
  are one property when their points are within ``at_risk_dedup``
  ``match_distance_m`` (50 m) and their unit counts agree within
  ``unit_count_max_diff_share``. Where some ending rows cannot be tested, the
  ledger labels the count "upper bound".
- A tax-credit restriction ending in the read year itself still counts: only
  the year is known, so it has not certainly ended on the read date.
- Assisted homes ``assisted_units`` = contract units + public housing units +
  tax-credit low-income units. Programs overlap (a tax-credit property can also
  hold a contract), so this is an upper bound; the ledger says so.
- Points are placed in 2020 tracts with the same point-in-polygon test the
  incentive tables use; the county comes from HUD's own county code.

All thresholds live in ``assumptions/affordability.yaml``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.data.sources.affordability import AffordInputs, parse_expiration

__all__ = [
    "ASSUMPTIONS",
    "BURDEN_BANDS",
    "COLLAPSED",
    "INCOME_BANDS",
    "RENT_BANDS",
    "TABLES",
    "add_affordability",
    "affordability_cmd",
    "affordability_tables",
    "ami_long",
    "assisted_table",
    "chas_table",
    "chas_tract_table",
    "collapse_renters",
    "describe",
    "gap_context",
    "income_limit",
    "income_limits_table",
    "load_settings",
    "max_rent",
    "stock_ledger",
]

ASSUMPTIONS = Path(__file__).resolve().parents[3] / "assumptions" / "affordability.yaml"

INCOME_BANDS: tuple[tuple[str, str], ...] = (
    ("le30", "At or below 30% of AMI"),
    ("30_50", "30-50% of AMI"),
    ("50_80", "50-80% of AMI"),
    ("80_100", "80-100% of AMI"),
    ("gt100", "Above 100% of AMI"),
)
"""CHAS household income bands (share of HUD area median family income), in table order."""

BURDEN_BANDS: tuple[tuple[str, str], ...] = (
    ("lt30", "Pays 30% of income or less"),
    ("30_50", "Pays 30-50% of income"),
    ("gt50", "Pays over 50% of income"),
    ("nc", "Not computed (no or negative income)"),
)
"""CHAS cost-burden bands, in table order (offsets 1, 4, 7, 10 from the band's total)."""

RENT_BANDS: tuple[tuple[str, str, int], ...] = (
    ("rent_le30", "Rent affordable at 30% of AMI or less", 4),
    ("rent_30_50", "Rent affordable at 30-50% of AMI", 25),
    ("rent_50_80", "Rent affordable at 50-80% of AMI", 46),
    ("rent_gt80", "Rent needs over 80% of AMI", 67),
)
"""CHAS Table 15C rent bands (homes with a complete kitchen and plumbing) and their cells."""

OLDER: tuple[tuple[str, int, int], ...] = (
    ("1960_1979", 123, 124),
    ("1940_1959", 140, 141),
    ("pre1940", 157, 158),
)
"""CHAS Table 12 renter year-built bands: (key, all renters cell, renters <= 50% AMI cell)."""

TENURE_BASE = {"owner": 2, "renter": 68}
"""CHAS Table 8 cell of each tenure's total."""


def load_settings(path: Path = ASSUMPTIONS) -> dict[str, Any]:
    """Read ``assumptions/affordability.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- CHAS


def chas_cell_map(tenures: Sequence[str] = ("owner", "renter")) -> dict[str, int]:
    """Pack column name to CHAS Table 8 cell, for the tenure x income x burden grid."""
    out: dict[str, int] = {}
    for tenure in tenures:
        base = TENURE_BASE[tenure]
        for i, (band, _) in enumerate(INCOME_BANDS):
            start = base + 1 + 13 * i
            for j, (burden, _) in enumerate(BURDEN_BANDS):
                out[f"{tenure}_{band}_{burden}"] = start + 1 + 3 * j
    return out


def _sum_cells(raw: pd.DataFrame, table: str, cells: Sequence[int]) -> tuple[pd.Series, pd.Series]:
    est = sum(pd.to_numeric(raw[f"T{table}_est{c}"], errors="coerce") for c in cells)
    moe = np.sqrt(sum(pd.to_numeric(raw[f"T{table}_moe{c}"], errors="coerce") ** 2 for c in cells))
    return est, moe


def chas_table(raw: pd.DataFrame) -> pd.DataFrame:
    """The ``afford_chas`` pack table (state and county rows) from the CHAS extract.

    Columns: the full tenure x income x burden grid (``renter_le30_gt50`` = renter
    households at or below 30% of AMI paying over half their income), the rent
    bands, homes lacking a kitchen or plumbing and the older-renter counts, each
    with its ``_moe``.
    """
    frame = raw[raw["level"].isin(["state", "county"])]
    cols: dict[str, Any] = {"level": frame["level"].to_numpy(), "geoid": frame["geoid"].to_numpy()}
    for name, cell in chas_cell_map().items():
        cols[name], cols[f"{name}_moe"] = _sum_cells(frame, "8", [cell])
    for key, _, cell in RENT_BANDS:
        cols[key], cols[f"{key}_moe"] = _sum_cells(frame, "15C", [cell])
    cols["rent_lacking"], cols["rent_lacking_moe"] = _sum_cells(frame, "15C", [2])
    for key, every, low in OLDER:
        cols[f"renter_built_{key}"], cols[f"renter_built_{key}_moe"] = _sum_cells(
            frame, "12", [every]
        )
        cols[f"renter_built_{key}_le50"], cols[f"renter_built_{key}_le50_moe"] = _sum_cells(
            frame, "12", [low]
        )
    out = pd.DataFrame(
        {k: (v.to_numpy() if isinstance(v, pd.Series) else v) for k, v in cols.items()}
    )
    out["_o"] = (out["level"] != "state").astype(int)
    return out.sort_values(["_o", "geoid"]).drop(columns="_o").reset_index(drop=True)


TRACT_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("le30", "At or below 30% of AMI", ("le30",)),
    ("30_80", "30-80% of AMI", ("30_50", "50_80")),
    ("gt80", "Above 80% of AMI", ("80_100", "gt100")),
)
"""Renter income groups kept on tract rows (and used by the profile's charts at every level)."""


def _collapsed_cells() -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for group, _, bands in TRACT_GROUPS:
        for burden in ("lt30", "30_50", "gt50"):
            out[f"renter_{group}_{burden}"] = tuple(f"renter_{b}_{burden}" for b in bands)
    out["renter_nc"] = tuple(f"renter_{b}_nc" for b, _ in INCOME_BANDS)
    for key, _, _ in RENT_BANDS:
        out[key] = (key,)
    out["older_rental"] = tuple(f"renter_built_{k}" for k, _, _ in OLDER)
    out["older_rental_le50"] = tuple(f"renter_built_{k}_le50" for k, _, _ in OLDER)
    return out


COLLAPSED: dict[str, tuple[str, ...]] = _collapsed_cells()
"""Tract-layout column -> the ``afford_chas`` cells it adds up.

``renter_<group>_<burden>`` for the three :data:`TRACT_GROUPS` and the burdens
``lt30``, ``30_50``, ``gt50``; ``renter_nc`` (no or negative income, any band);
the four rent bands; ``older_rental`` and ``older_rental_le50`` (built before 1980).
"""


def collapse_renters(values: Mapping[str, Any]) -> dict[str, float | None]:
    """One ``afford_chas`` row's renter cells collapsed to the tract layout (:data:`COLLAPSED`).

    A collapsed value is blank when any of its cells is blank.
    """

    def num(name: str) -> float | None:
        v = values.get(name)
        try:
            v = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return None if math.isnan(v) else v

    out: dict[str, float | None] = {}
    for key, cells in COLLAPSED.items():
        got = [num(c) for c in cells]
        out[key] = None if any(g is None for g in got) else float(sum(got))  # type: ignore[arg-type]
    return out


def chas_tract_table(raw: pd.DataFrame, tract_ids: set[str] | None = None) -> pd.DataFrame:
    """The ``afford_chas_tract`` pack table: one compact row per tract, renters only, no margins.

    Columns as :func:`collapse_renters` (the same cells, collapsed the same way
    as for counties, so a tract and its county always compare like with like).
    """
    frame = raw[raw["level"] == "tract"]
    if tract_ids is not None:
        frame = frame[frame["geoid"].isin(tract_ids)]
    wide = chas_table(frame.assign(level="county"))
    rows = [
        {"geoid": g, **collapse_renters(r)}
        for g, r in zip(wide["geoid"], wide.to_dict("records"), strict=True)
    ]
    return pd.DataFrame(rows).sort_values("geoid").reset_index(drop=True)


# --------------------------------------------------------------------------- income limits

LIMIT_COLUMNS = tuple(f"l{p}_{s}" for p in (30, 50, 80) for s in range(1, 9))


def income_limits_table(raw: pd.DataFrame) -> pd.DataFrame:
    """The ``afford_income_limits`` table: one row per county.

    Outside New England the file already has one row per county. In New England
    (rows per town) a county takes the HUD area that covers most of its towns
    (ties: the lower area code), and ``areas_in_county`` says how many areas it
    spans.
    """
    frame = raw.copy()
    frame["county"] = frame["county"].astype(str).str.zfill(5)
    rows = []
    for county, group in frame.groupby("county", sort=True):
        counts = group.groupby("hud_area_code").size()
        best = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[0][0]
        row = group[group["hud_area_code"] == best].iloc[0]
        rec = {
            "county": county,
            "hud_area_code": best,
            "hud_area_name": row["hud_area_name"],
            "areas_in_county": len(counts),
            "median": row["median"],
        }
        rec.update({c: row[c] for c in LIMIT_COLUMNS})
        rows.append(rec)
    return pd.DataFrame(rows)


def _limit_row(limits: pd.DataFrame | Mapping[str, Any], county: str | None) -> Mapping[str, Any]:
    if isinstance(limits, pd.DataFrame):
        hit = limits[limits["county"].astype(str) == str(county)]
        if hit.empty:
            raise KeyError(f"no income limits for county {county}")
        return hit.iloc[0].to_dict()
    return limits


def income_limit(
    limits: pd.DataFrame | Mapping[str, Any],
    pct: int,
    size: float,
    *,
    county: str | None = None,
    settings: Mapping[str, Any] | None = None,
) -> float:
    """The yearly income limit at ``pct``% of AMI for a household of ``size`` people.

    30, 50 and 80 are HUD's published limits. Other levels are derived from the
    50% limit with the multiples in ``assumptions/affordability.yaml``
    (``derived_limit_multiples``: 60% = 1.2x, 100% = 2x, 120% = 2.4x); any other
    level is ``pct / 50`` times the 50% limit. A half size (1.5, 4.5) is the mean
    of the two whole sizes around it. Sizes run from 1 to 8.

    Args:
        limits: The ``afford_income_limits`` table (with ``county``) or one row.
        pct: Share of AMI, in percent.
        size: Household size, 1 to 8 (halves allowed).
        county: The county code when ``limits`` is the whole table.
        settings: The assumptions (default: read from the file).
    """
    row = _limit_row(limits, county)
    if not 1 <= size <= 8:
        raise ValueError(f"household size {size} is outside 1-8")
    low, high = math.floor(size), math.ceil(size)
    if low != high:
        return (
            income_limit(row, pct, low, settings=settings)
            + income_limit(row, pct, high, settings=settings)
        ) / 2
    size = int(size)
    if pct in (30, 50, 80):
        return float(row[f"l{pct}_{size}"])
    rules = (settings or load_settings())["derived_limit_multiples"]
    multiple = float(rules.get(pct, rules.get(str(pct), pct / 50)))
    return multiple * float(row[f"l50_{size}"])


def max_rent(
    limits: pd.DataFrame | Mapping[str, Any],
    pct: int,
    bedrooms: int,
    *,
    county: str | None = None,
    settings: Mapping[str, Any] | None = None,
) -> float:
    """The highest monthly housing cost affordable at ``pct``% of AMI for a home of ``bedrooms``.

    ``rent_share_of_income`` (30%) x the income limit for the household size
    assumed for that many bedrooms (``persons_per_bedroom``: 1 for a studio,
    1.5 per bedroom), / 12. The figure is gross rent: contract rent plus
    utilities paid by the tenant.
    """
    cfg = settings or load_settings()
    sizes = cfg["persons_per_bedroom"]
    size = float(sizes.get(bedrooms, sizes.get(str(bedrooms), 1.5 * bedrooms or 1.0)))
    income = income_limit(limits, pct, min(size, 8.0), county=county, settings=cfg)
    return float(cfg["rent_share_of_income"]) * income / 12


def ami_long(
    limits: pd.DataFrame,
    pcts: Sequence[int] = (30, 50, 60, 80, 100),
    settings: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Engine-ready long table: county x household size x AMI level -> income and rent.

    Columns: ``county``, ``hud_area_code``, ``size`` (1-8), ``pct``, ``income``
    (dollars a year), ``affordable_cost`` (30% of it / 12, dollars a month),
    ``published`` (True for HUD's 30/50/80% limits, False for derived levels).
    """
    cfg = settings or load_settings()
    rows = []
    for _, row in limits.iterrows():
        for size in range(1, 9):
            for pct in pcts:
                income = income_limit(row.to_dict(), pct, size, settings=cfg)
                rows.append(
                    {
                        "county": row["county"],
                        "hud_area_code": row["hud_area_code"],
                        "size": size,
                        "pct": pct,
                        "income": round(income),
                        "affordable_cost": round(cfg["rent_share_of_income"] * income / 12),
                        "published": pct in (30, 50, 80),
                    }
                )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- assisted homes


def _years_after(start: date, years: int) -> date:
    try:
        return start.replace(year=start.year + years)
    except ValueError:  # 29 February
        return start.replace(year=start.year + years, day=28)


def _mf_points(mf: pd.DataFrame, as_of: date, windows: Sequence[int]) -> pd.DataFrame:
    """One row per assisted property: county, point, assisted units and units by end date."""
    total = pd.to_numeric(mf["total_unit_count"], errors="coerce")
    assisted = pd.to_numeric(mf["total_assisted_unit_count"], errors="coerce")
    assisted = np.where(total.notna() & (total > 0), np.fmin(assisted, total), assisted)
    out = pd.DataFrame(
        {
            "county": mf["county_level"].astype(str).str.zfill(5),
            "lat": pd.to_numeric(mf["lat"], errors="coerce"),
            "lon": pd.to_numeric(mf["lon"], errors="coerce"),
            "mf_units": pd.Series(assisted, index=mf.index).fillna(0.0),
            "match_units": total,
        }
    )
    for w in windows:
        out[f"mf_units_exp_{w}y"] = 0.0
    out["mf_units_exp_past"] = 0.0
    ends = {w: _years_after(as_of, w) for w in windows}
    for k in (1, 2):
        units = pd.to_numeric(mf[f"units{k}"], errors="coerce").fillna(0.0)
        dates = mf[f"expiration_date{k}"].map(parse_expiration)
        for i, (u, d) in enumerate(zip(units, dates, strict=True)):
            if d is None or u <= 0:
                continue
            if d <= as_of:
                out.iloc[i, out.columns.get_loc("mf_units_exp_past")] += u
                continue
            for w in windows:
                if d <= ends[w]:
                    out.iloc[i, out.columns.get_loc(f"mf_units_exp_{w}y")] += u
    return out


def _lihtc_points(
    lihtc: pd.DataFrame, as_of: date, windows: Sequence[int], years: int
) -> pd.DataFrame:
    units = pd.to_numeric(lihtc["units"], errors="coerce")
    li = pd.to_numeric(lihtc["li_units"], errors="coerce").fillna(units).fillna(0.0)
    pis = pd.to_numeric(lihtc["year_pis"], errors="coerce")
    known = pis.notna() & (pis >= 1987) & (pis <= as_of.year)
    out = pd.DataFrame(
        {
            "county": lihtc["county"].astype(str).str.zfill(5),
            "lat": pd.to_numeric(lihtc["lat"], errors="coerce"),
            "lon": pd.to_numeric(lihtc["lon"], errors="coerce"),
            "lihtc_li_units": li,
            "lihtc_year_unknown": (~known).astype(float),
            "match_units": units,
        }
    )
    end = pis + years
    for w in windows:
        # A restriction ending this calendar year (end == as_of.year) has not
        # ended yet on the read date (only the year is known), so it counts.
        hit = known & (end >= as_of.year) & (end <= as_of.year + w)
        out[f"lihtc_li_units_end_{w}y"] = np.where(hit, li, 0.0)
    return out


def _distance_m(lat1: np.ndarray, lon1: np.ndarray, lat2: float, lon2: float) -> np.ndarray:
    """Great-circle distance in metres (haversine, Earth radius 6,371 km)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * 6_371_000.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _match_overlap(
    mf: pd.DataFrame, li: pd.DataFrame, windows: Sequence[int], rule: Mapping[str, Any]
) -> None:
    """Find properties on both HUD lists and record the units counted twice (in place).

    A tax-credit project and a contract property are taken to be the same
    property when their points are within ``match_distance_m`` metres and their
    total unit counts differ by at most ``unit_count_max_diff_share`` of the
    larger count (``assumptions/affordability.yaml``, ``at_risk_dedup``). Each
    contract property matches at most one project (the nearest that passes).
    For a matched pair the units counted twice in window ``w`` are
    ``min(contract units ending, tax-credit units ending)``; they are written to
    the contract row as ``at_risk_overlap_{w}y`` so ``at_risk_{w}y`` counts them
    once. Rows ending in a window that cannot be tested (no point or no unit
    count) add 1 to ``at_risk_unchecked_{w}y``; any such row makes the
    de-duplicated count an upper bound.
    """
    dist = float(rule["match_distance_m"])
    share = float(rule["unit_count_max_diff_share"])
    top = max(windows)
    for w in windows:
        mf[f"at_risk_overlap_{w}y"] = 0.0
    for frame, col in ((mf, "mf_units_exp"), (li, "lihtc_li_units_end")):
        testable = frame[["lat", "lon", "match_units"]].notna().all(axis=1) & (
            frame["match_units"] > 0
        )
        frame["_testable"] = testable
        for w in windows:
            frame[f"at_risk_unchecked_{w}y"] = ((frame[f"{col}_{w}y"] > 0) & ~testable).astype(
                float
            )
    cand_mf = mf[mf["_testable"] & (mf[f"mf_units_exp_{top}y"] > 0)]
    cand_li = li[li["_testable"] & (li[f"lihtc_li_units_end_{top}y"] > 0)]
    if len(cand_mf) and len(cand_li):
        lat = cand_mf["lat"].to_numpy(float)
        lon = cand_mf["lon"].to_numpy(float)
        units = cand_mf["match_units"].to_numpy(float)
        free = np.ones(len(cand_mf), dtype=bool)
        for _, row in cand_li.iterrows():
            d = _distance_m(lat, lon, float(row["lat"]), float(row["lon"]))
            big = np.maximum(units, float(row["match_units"]))
            ok = free & (d <= dist) & (np.abs(units - float(row["match_units"])) <= share * big)
            if not ok.any():
                continue
            j = int(np.argmin(np.where(ok, d, np.inf)))
            free[j] = False
            idx = cand_mf.index[j]
            for w in windows:
                both = min(mf.at[idx, f"mf_units_exp_{w}y"], row[f"lihtc_li_units_end_{w}y"])
                mf.at[idx, f"at_risk_overlap_{w}y"] += float(both)
    for w in windows:
        mf[f"at_risk_{w}y"] = mf[f"mf_units_exp_{w}y"] - mf[f"at_risk_overlap_{w}y"]
        li[f"at_risk_{w}y"] = li[f"lihtc_li_units_end_{w}y"]
    for frame in (mf, li):
        del frame["_testable"], frame["match_units"]


def _place(points: pd.DataFrame, shapes: Sequence[tuple[str, Mapping[str, Any]]]) -> pd.Series:
    from visionhousing.features.transit import points_in_polygons

    lat, lon = points["lat"].to_numpy(float), points["lon"].to_numpy(float)
    out = np.full(len(points), "", dtype=object)
    ok = ~(np.isnan(lat) | np.isnan(lon))
    if ok.any() and shapes:
        out[ok] = points_in_polygons(lat[ok], lon[ok], shapes)
    return pd.Series(out, index=points.index)


ASSISTED_SUMS = (
    "mf_properties",
    "mf_units",
    "mf_units_exp_5y",
    "mf_units_exp_10y",
    "mf_units_exp_past",
    "ph_developments",
    "ph_units",
    "lihtc_projects",
    "lihtc_li_units",
    "lihtc_li_units_end_5y",
    "lihtc_li_units_end_10y",
    "lihtc_year_unknown",
    "at_risk_overlap_5y",
    "at_risk_overlap_10y",
    "at_risk_unchecked_5y",
    "at_risk_unchecked_10y",
    "at_risk_5y",
    "at_risk_10y",
    "hcv_households",
)
"""The ``afford_assisted`` count columns (the 5- and 10-year windows are the assumption file's)."""


def assisted_table(
    inputs: AffordInputs,
    state_fips: str,
    shapes: Sequence[tuple[str, Mapping[str, Any]]],
    counties: Sequence[str],
    settings: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """The ``afford_assisted`` table: assisted homes and expirations by state, county and tract.

    Args:
        inputs: The fetched extracts (``read_on`` is the day expirations count from).
        state_fips: Two-digit state code (the state row's ``geoid``).
        shapes: ``(tract code, GeoJSON geometry)`` pairs of the pack's tracts.
        counties: The state's county codes (each gets a row, zeros included).
        settings: The assumptions (default: read from the file).
    """
    cfg = settings or load_settings()
    windows = [int(w) for w in cfg["expiry_windows_years"]]
    as_of = date.fromisoformat(inputs.read_on) if inputs.read_on else date.today()
    parts = []
    mf = li = None
    if inputs.mf_assisted is not None and len(inputs.mf_assisted):
        mf = _mf_points(inputs.mf_assisted, as_of, windows)
        mf["mf_properties"] = 1.0
        parts.append(mf)
    if inputs.public_housing is not None and len(inputs.public_housing):
        ph = inputs.public_housing
        units = pd.to_numeric(ph["total_dwelling_units"], errors="coerce")
        parts.append(
            pd.DataFrame(
                {
                    "county": ph["county_level"].astype(str).str.zfill(5),
                    "lat": pd.to_numeric(ph["lat"], errors="coerce"),
                    "lon": pd.to_numeric(ph["lon"], errors="coerce"),
                    "ph_developments": 1.0,
                    "ph_units": units.fillna(0.0),
                }
            )
        )
    if inputs.lihtc is not None and len(inputs.lihtc):
        li = _lihtc_points(inputs.lihtc, as_of, windows, int(cfg["lihtc_extended_use_years"]))
        li["lihtc_projects"] = 1.0
        parts.append(li)
    empty = pd.DataFrame(
        columns=["lat", "lon", "match_units"]
        + [f"{c}_{w}y" for c in ("mf_units_exp", "lihtc_li_units_end") for w in windows]
    )
    _match_overlap(
        mf if mf is not None else empty.copy(),
        li if li is not None else empty.copy(),
        windows,
        cfg["at_risk_dedup"],
    )
    points = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["county"])
    for col in ASSISTED_SUMS:
        if col not in points.columns:
            points[col] = 0.0
    points[list(ASSISTED_SUMS)] = points[list(ASSISTED_SUMS)].fillna(0.0)
    points["tract"] = _place(points, shapes) if len(points) else pd.Series(dtype=object)
    sums = [c for c in ASSISTED_SUMS if c != "hcv_households"]
    county = points.groupby("county")[sums].sum().reindex(sorted(counties), fill_value=0.0)
    tract = points[points["tract"] != ""].groupby("tract")[sums].sum()
    hcv = inputs.hcv
    if hcv is not None and len(hcv):
        h = pd.DataFrame(
            {
                "tract": hcv["geoid"].astype(str),
                "v": pd.to_numeric(hcv["hcv_public"], errors="coerce"),
            }
        )
        h = h[h["tract"].str[:2] == state_fips]
        by_tract = h.groupby("tract")["v"].sum(min_count=1)
        by_county = h.groupby(h["tract"].str[:5])["v"].sum(min_count=1)
        tract = tract.reindex(tract.index.union(by_tract.dropna().index), fill_value=0.0)
        tract["hcv_households"] = by_tract.reindex(tract.index)
        county["hcv_households"] = by_county.reindex(county.index)
    else:
        tract["hcv_households"] = np.nan
        county["hcv_households"] = np.nan
    state = county.sum(min_count=1).to_frame().T
    state.index = [state_fips]
    frames = []
    for level, part in (("state", state), ("county", county), ("tract", tract.sort_index())):
        part = part.copy()
        part.insert(0, "geoid", part.index.astype(str))
        part.insert(0, "level", level)
        frames.append(part.reset_index(drop=True))
    out = pd.concat(frames, ignore_index=True)
    keep = (out["level"] != "tract") | (out[list(ASSISTED_SUMS)].fillna(0).sum(axis=1) > 0)
    out = out[keep].reset_index(drop=True)
    out["as_of"] = as_of.isoformat()
    return out[["level", "geoid", *ASSISTED_SUMS, "as_of"]]


# --------------------------------------------------------------------------- ledger


def stock_ledger(
    chas: pd.DataFrame,
    assisted: pd.DataFrame,
    counties: pd.DataFrame,
    permits: pd.DataFrame | None,
    settings: Mapping[str, Any] | None = None,
    permit_years: int = 5,
) -> pd.DataFrame:
    """The ``stock_ledger_county`` table: one row per county plus the state.

    Columns (counts of homes or households):

    - ``housing_units``: all homes (ACS 2020-2024, the pack's county table).
    - ``renter_households``, ``owner_households``: CHAS 2018-2022.
    - ``authorized_recent`` and ``authorized_years``: homes authorized by permit
      in the last ``permit_years`` years of the pack's permit table (new supply
      in the pipeline; completions are not observed).
    - ``assisted_units``: contract + public housing + tax-credit low-income
      units (an upper bound: programs overlap).
    - ``at_risk_5y`` / ``at_risk_10y``: contract units ending, plus tax-credit
      restrictions ending, within 5 / 10 years, with a property on both lists
      counted once (:func:`_match_overlap`).
    - ``at_risk_basis``: "matched" when every ending row could be tested for a
      match; "upper bound" when some could not (no point or no unit count), so
      some units may still be counted twice.
    - ``lower_rent_units``: renter homes with rents affordable at 50% of AMI or
      less (CHAS 15C), the stock most exposed to loss through rent increases
      or redevelopment.
    - ``older_rental``: renter households in buildings built before 1980;
      ``older_rental_le50``: of them, households at or below 50% of AMI.
    - ``severe_le50_renters``: renters at or below 50% of AMI paying over half
      their income (displacement *context* only; it predicts nothing).

    Homes removed (demolitions, conversions) are not observed in any public file
    used here, so the ledger has no removal column.
    """
    cfg = settings or load_settings()
    w0, w1 = (int(w) for w in cfg["expiry_windows_years"][:2])
    geo = chas[chas["level"].isin(["state", "county"])].set_index("geoid")
    ass = assisted[assisted["level"].isin(["state", "county"])].set_index("geoid")

    def col(frame: pd.DataFrame, names: Sequence[str]) -> pd.Series:
        return sum(pd.to_numeric(frame[n], errors="coerce").fillna(0.0) for n in names)

    renter = col(geo, [f"renter_{b}_{c}" for b, _ in INCOME_BANDS for c, _ in BURDEN_BANDS])
    owner = col(geo, [f"owner_{b}_{c}" for b, _ in INCOME_BANDS for c, _ in BURDEN_BANDS])
    bands = cfg["pre1980_built_bands"]
    out = pd.DataFrame(
        {
            "level": geo["level"],
            "renter_households": renter,
            "owner_households": owner,
            "lower_rent_units": col(geo, ["rent_le30", "rent_30_50"]),
            "older_rental": col(geo, [f"renter_built_{b}" for b in bands]),
            "older_rental_le50": col(geo, [f"renter_built_{b}_le50" for b in bands]),
            "severe_le50_renters": col(geo, ["renter_le30_gt50", "renter_30_50_gt50"]),
        }
    )
    out["assisted_units"] = col(ass, ["mf_units", "ph_units", "lihtc_li_units"]).reindex(out.index)
    unchecked = pd.Series(0.0, index=ass.index)
    for w in (w0, w1):
        if f"at_risk_{w}y" in ass:
            out[f"at_risk_{w}y"] = col(ass, [f"at_risk_{w}y"]).reindex(out.index)
            unchecked = unchecked + col(ass, [f"at_risk_unchecked_{w}y"])
        else:  # an older pack: no matching was done
            out[f"at_risk_{w}y"] = col(ass, [f"mf_units_exp_{w}y", f"lihtc_li_units_end_{w}y"])
            unchecked = unchecked + 1.0
    out["at_risk_basis"] = np.where(
        unchecked.reindex(out.index).fillna(1.0) > 0, "upper bound", "matched"
    )
    hu = counties.assign(geoid=counties["geoid"].astype(str)).set_index("geoid")["housing_units"]
    out["housing_units"] = pd.to_numeric(hu, errors="coerce").reindex(out.index)
    state_rows = out["level"] == "state"
    out.loc[state_rows, "housing_units"] = pd.to_numeric(hu, errors="coerce").sum()
    out["authorized_recent"] = np.nan
    out["authorized_years"] = ""
    if permits is not None and len(permits):
        p = permits.assign(county=permits["county"].astype(str).str.zfill(5))
        last = int(pd.to_numeric(p["year"]).max())
        first = last - permit_years + 1
        recent = p[pd.to_numeric(p["year"]) >= first]
        units = sum(
            pd.to_numeric(recent[c], errors="coerce").fillna(0)
            for c in ("units_1", "units_2", "units_3_4", "units_5plus")
        )
        by_county = units.groupby(recent["county"]).sum()
        out["authorized_recent"] = by_county.reindex(out.index)
        out.loc[state_rows, "authorized_recent"] = by_county.sum()
        out["authorized_years"] = f"{first}-{last}"
    out["as_of"] = str(assisted["as_of"].iloc[0]) if "as_of" in assisted and len(assisted) else ""
    out.insert(0, "geoid", out.index.astype(str))
    order = [
        "level",
        "geoid",
        "housing_units",
        "renter_households",
        "owner_households",
        "authorized_recent",
        "authorized_years",
        "assisted_units",
        f"at_risk_{w0}y",
        f"at_risk_{w1}y",
        "at_risk_basis",
        "lower_rent_units",
        "older_rental",
        "older_rental_le50",
        "severe_le50_renters",
        "as_of",
    ]
    out = out.reset_index(drop=True)[order]
    out["_o"] = (out["level"] != "state").astype(int)
    return out.sort_values(["_o", "geoid"]).drop(columns="_o").reset_index(drop=True)


# --------------------------------------------------------------------------- context


def gap_context(state: str, settings: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """NLIHC's quoted state figures (``assumptions/affordability.yaml``), or ``None``.

    Context only: shown with credit next to the place's own numbers, never used
    in a calculation.
    """
    cfg = settings or load_settings()
    figures = (cfg.get("gap_context") or {}).get(state.upper())
    if not figures:
        return None
    return {**figures, "national": (cfg["gap_context"].get("national") or {})}


# --------------------------------------------------------------------------- pack

TABLES: dict[str, dict[str, object]] = {
    "afford_chas": {
        "key": ["level", "geoid"],
        "sources": ["hud_chas"],
        "description": (
            "State and county: households by tenure, income (share of HUD area median family "
            "income) and housing cost burden (CHAS Table 8); renter homes by the income their "
            "rent is affordable to (15C); renter households in older buildings (12); with "
            "margins of error. Tracts are in afford_chas_tract."
        ),
    },
    "afford_chas_tract": {
        "key": ["geoid"],
        "sources": ["hud_chas"],
        "description": (
            "Tract: renter households by income group (at or below 30%, 30-80%, above 80% of "
            "HUD area median family income) and cost burden; renter homes by the income their "
            "rent is affordable to; renters in buildings built before 1980. No margins."
        ),
    },
    "afford_income_limits": {
        "key": ["county"],
        "sources": ["hud_income_limits"],
        "description": (
            "County: HUD area median family income and the 30%, 50% and 80% income limits for "
            "households of 1 to 8 people (fiscal year 2026)."
        ),
    },
    "afford_assisted": {
        "key": ["level", "geoid"],
        "sources": ["hud_mf_assisted", "hud_public_housing", "hud_lihtc", "hud_hcv_tract"],
        "description": (
            "State, county and tract: homes with project-based rental assistance, public "
            "housing and tax-credit restrictions; units whose contract or restriction ends "
            "within 5 and 10 years; households using vouchers. Tracts with none are left out."
        ),
    },
    "stock_ledger_county": {
        "key": ["geoid"],
        "sources": [
            "acs5_summary_file",
            "hud_chas",
            "bps_county_annual",
            "hud_mf_assisted",
            "hud_public_housing",
            "hud_lihtc",
        ],
        "description": (
            "State and county stock ledger: existing homes, recent permits, assisted homes, "
            "homes whose assistance ends within 5 and 10 years, lower-rent and older rental "
            "stock, and severe cost burden among lower-income renters (context only)."
        ),
    },
}
"""The affordability pack tables: key columns, source ids, one-line description."""

_FIXED: dict[str, tuple[str, str]] = {
    "level": ("category", "Geography of the row: state, county or tract"),
    "geoid": ("code", "State (2 digits), county (5) or tract (11) code"),
    "county": ("code", "County code (5 digits)"),
    "hud_area_code": ("code", "HUD income-limit area code"),
    "hud_area_name": ("text", "HUD income-limit area name"),
    "areas_in_county": ("count", "HUD areas the county spans (above 1 only in New England)"),
    "median": ("dollars per year", "HUD area median family income (AMI), fiscal year 2026"),
    "rent_lacking": ("units", "Renter homes lacking a complete kitchen or plumbing, any rent"),
    "mf_properties": ("count", "Properties with project-based rental assistance"),
    "mf_units": ("units", "Assisted units in those properties (capped at the property's units)"),
    "mf_units_exp_5y": ("units", "Contract units whose contract ends within 5 years"),
    "mf_units_exp_10y": ("units", "Contract units whose contract ends within 10 years"),
    "mf_units_exp_past": (
        "units",
        "Contract units whose listed end date has passed (renewal pending)",
    ),
    "ph_developments": ("count", "Public housing developments"),
    "ph_units": ("units", "Public housing dwelling units"),
    "lihtc_projects": ("count", "Tax-credit (LIHTC) projects"),
    "lihtc_li_units": ("units", "Tax-credit low-income units (all units where not reported)"),
    "lihtc_li_units_end_5y": (
        "units",
        "Tax-credit low-income units whose 30-year restriction ends within 5 years",
    ),
    "lihtc_li_units_end_10y": (
        "units",
        "Tax-credit low-income units whose 30-year restriction ends within 10 years",
    ),
    "lihtc_year_unknown": ("count", "Tax-credit projects with an unknown year placed in service"),
    "hcv_households": ("households", "Households using a Housing Choice Voucher (tenant-based)"),
    "as_of": ("date", "Day the HUD assisted-housing lists were read; expirations count from it"),
    "housing_units": ("units", "All homes (ACS 2020-2024)"),
    "renter_households": ("households", "Renter households (CHAS 2018-2022)"),
    "owner_households": ("households", "Owner households (CHAS 2018-2022)"),
    "authorized_recent": (
        "units",
        "Homes authorized by permit in the years named in authorized_years",
    ),
    "authorized_years": ("years", "Years counted in authorized_recent"),
    "assisted_units": (
        "units",
        "Contract + public housing + tax-credit low-income units (upper bound; programs overlap)",
    ),
    "at_risk_5y": (
        "units",
        "Contract units and tax-credit restrictions ending within 5 years; a property on "
        "both lists counted once",
    ),
    "at_risk_10y": (
        "units",
        "Contract units and tax-credit restrictions ending within 10 years; a property on "
        "both lists counted once",
    ),
    "at_risk_overlap_5y": (
        "units",
        "Units ending within 5 years on both lists (same property: points within the match "
        "distance and unit counts that agree); subtracted once from at_risk_5y",
    ),
    "at_risk_overlap_10y": (
        "units",
        "Units ending within 10 years on both lists; subtracted once from at_risk_10y",
    ),
    "at_risk_unchecked_5y": (
        "count",
        "Rows ending within 5 years that could not be tested for a match (no point or no "
        "unit count); above 0 makes at_risk_5y an upper bound",
    ),
    "at_risk_unchecked_10y": (
        "count",
        "Rows ending within 10 years that could not be tested for a match; above 0 makes "
        "at_risk_10y an upper bound",
    ),
    "at_risk_basis": (
        "category",
        "matched (every ending row tested for a property on both lists) or upper bound "
        "(some could not be tested)",
    ),
    "lower_rent_units": (
        "units",
        "Renter homes with rents affordable at 50% of AMI or less (CHAS 15C)",
    ),
    "older_rental": ("households", "Renter households in buildings built before 1980 (CHAS 12)"),
    "older_rental_le50": ("households", "Of them, households at or below 50% of AMI"),
    "severe_le50_renters": (
        "households",
        "Renters at or below 50% of AMI paying over half their income (context only)",
    ),
}


def describe(table: str, name: str) -> tuple[str, str]:
    """Unit and plain description of one affordability pack column (for the data dictionary)."""
    if name.endswith("_moe"):
        unit, text = describe(table, name[: -len("_moe")])
        return unit, f"90% margin of error of: {text[0].lower()}{text[1:]}"
    if name in _FIXED:
        return _FIXED[name]
    if name[:4] in ("l30_", "l50_", "l80_"):
        pct, size = name[1:3], name[4:]
        return "dollars per year", f"HUD {pct}% income limit, household of {size}"
    groups = {g: label for g, label, _ in TRACT_GROUPS}
    if name == "renter_nc":
        return (
            "households",
            "Renter households whose cost burden is not computed (no or negative income)",
        )
    if name.startswith("renter_") and name.count("_") >= 2:
        rest = name[len("renter_") :]
        for g, glabel in groups.items():
            burden = rest[len(g) + 1 :]
            if rest.startswith(f"{g}_") and burden in ("lt30", "30_50", "gt50"):
                return (
                    "households",
                    f"Renter households {glabel}: {dict(BURDEN_BANDS)[burden].lower()}",
                )
    for key, label, _ in RENT_BANDS:
        if name == key:
            return "units", f"Renter homes (complete kitchen and plumbing): {label.lower()}"
    if name.startswith("renter_built_"):
        rest = name[len("renter_built_") :]
        low = rest.endswith("_le50")
        span = rest.removesuffix("_le50").replace("pre1940", "1939 or earlier").replace("_", "-")
        who = "at or below 50% of AMI " if low else ""
        return "households", f"Renter households {who}in buildings built {span} (CHAS 12)"
    incomes, burdens = dict(INCOME_BANDS), dict(BURDEN_BANDS)
    for tenure in ("owner", "renter"):
        if name.startswith(f"{tenure}_"):
            rest = name[len(tenure) + 1 :]
            for band, blabel in INCOME_BANDS:
                if rest.startswith(f"{band}_") and rest[len(band) + 1 :] in burdens:
                    burden = burdens[rest[len(band) + 1 :]]
                    return "households", f"{tenure.title()} households {blabel}: {burden.lower()}"
    _ = incomes
    raise ValueError(f"no data-dictionary entry for {table}.{name}")


def affordability_tables(
    inputs: AffordInputs,
    tables: Mapping[str, pd.DataFrame],
    state_fips: str,
    shapes: Sequence[tuple[str, Mapping[str, Any]]],
    settings: Mapping[str, Any] | None = None,
) -> dict[str, pd.DataFrame]:
    """Every affordability pack table that the inputs allow.

    Args:
        inputs: The fetched extracts.
        tables: The pack's tables (``tracts``, ``counties``, ``permits_county_year``).
        state_fips: Two-digit state code.
        shapes: Tract outlines for placing assisted-housing points.
        settings: The assumptions (default: read from the file).
    """
    cfg = settings or load_settings()
    out: dict[str, pd.DataFrame] = {}
    tract_ids = set(tables["tracts"]["geoid"].astype(str))
    counties = sorted(tables["counties"]["geoid"].astype(str))
    if inputs.chas is not None:
        out["afford_chas"] = chas_table(inputs.chas)
        out["afford_chas_tract"] = chas_tract_table(inputs.chas, tract_ids)
    if inputs.income_limits is not None:
        out["afford_income_limits"] = income_limits_table(inputs.income_limits)
    out["afford_assisted"] = assisted_table(inputs, state_fips, shapes, counties, cfg)
    if "afford_chas" in out:
        out["stock_ledger_county"] = stock_ledger(
            out["afford_chas"],
            out["afford_assisted"],
            tables["counties"],
            tables.get("permits_county_year"),
            cfg,
        )
    return out


def add_affordability(
    folder: Path,
    cache: Path,
    *,
    client: Any = None,
    manifest: Path | None = None,
    inputs: AffordInputs | None = None,
) -> dict[str, pd.DataFrame]:
    """Add (or refresh) the affordability tables in an already built pack.

    Fetches (or reuses) the extracts, builds the tables, writes them, and
    rewrites the data dictionary and pack manifest the way a full build does
    (:func:`visionhousing.data.pack._rewrite`). The pack budget is checked
    afterwards by the caller (``visionhousing affordability`` prints the sizes).

    Args:
        folder: The pack folder (``data/packs/{STATE}``), rewritten in place.
        cache: The raw-file cache (``data/raw``).
        client: HTTP client for missing cache files.
        manifest: The download manifest (default: the project's).
        inputs: Extracts already read (tests); ``None`` fetches them from ``cache``.

    Returns:
        The tables written.
    """
    import json

    from visionhousing.data import manifest as mf
    from visionhousing.data import pack
    from visionhousing.data.sources import state_fips as to_state_fips
    from visionhousing.data.sources.affordability import fetch_affordability

    folder, cache = Path(folder), Path(cache)
    state = folder.name.upper()
    if inputs is None:
        inputs = fetch_affordability(
            state, cache, client=client, manifest=manifest or mf.MANIFEST_PATH
        )
    old = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))
    fips = str(old.get("state_fips") or to_state_fips(state))
    tables = pack.load_pack(folder)
    tract_ids = set(tables["tracts"]["geoid"].astype(str))
    shapes = [(g, s) for g, s in pack._tract_shapes(folder, cache, fips) if g in tract_ids]
    written = affordability_tables(inputs, tables, fips, shapes)
    pack._rewrite(folder, cache, written, list(inputs.files))
    return written


def affordability_cmd(state: str, raw: Path, packs: Path) -> int:
    """``visionhousing affordability``: add the tables to a built pack; print sizes and the budget.

    Returns 1 (and leaves the tables written) when the pack is now over its
    budget, so the size problem is seen at once.
    """
    import json

    from visionhousing.data.pack import PACK_BUDGET_BYTES

    folder = Path(packs) / state.upper()
    if not (folder / "pack_manifest.json").is_file():
        print(f"affordability: {folder} is not a built pack; build it first")
        return 1
    written = add_affordability(folder, Path(raw))
    for name, frame in written.items():
        size = (folder / f"{name}.csv.gz").stat().st_size
        print(f"{name}: {len(frame):,} rows, {size:,} bytes")
    files = json.loads((folder / "pack_manifest.json").read_text(encoding="utf-8"))["files"]
    total = sum(int(f["bytes"]) for f in files)
    used = total / PACK_BUDGET_BYTES
    print(f"pack: {total:,} bytes of the {PACK_BUDGET_BYTES:,} byte budget ({used:.1%})")
    return 0 if total < PACK_BUDGET_BYTES else 1
