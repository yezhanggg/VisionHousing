"""Parcel attributes by tract and county, for the two counties that publish them openly.

Plain-language summary
----------------------
Builds the ``parcels_area`` pack table: one row per census tract and one per
county, for Philadelphia (42101) and Allegheny (42003). Every other county
has no row, and its profile says "no free parcel attributes for this county".

Columns (``sf`` means single-family homes as defined in
``assumptions/parcels.yaml``):

- ``parcels`` and ``lu_<class>``: all parcels and parcels per shared land-use
  class (single-family, small multifamily, apartments, condominium, mixed use,
  commercial, industrial, vacant, government, agricultural, other);
- ``sf_parcels``, ``sf_median_value``, ``sf_median_lot_sqft``: single-family
  parcels, their median assessed value (Philadelphia: market value;
  Allegheny: fair market value, which is the county's 2012 base-year value)
  and median lot area in square feet;
- ``sf_built_<group>``: single-family parcels by year built;
- ``sales_short``, ``sales_long``, ``median_sale_short``,
  ``median_sale_long``: single-family sales in the two windows (12 and 36
  months by default) and their median price;
- ``unplaced`` (county rows): parcels that could not be put in a tract.

Medians resting on fewer parcels than ``min_parcels_for_median`` are left
blank. Philadelphia parcels are placed in 2020 tracts by testing which tract
outline holds each parcel's point (the City's own tract field uses older
tracts); Allegheny parcels carry their 2020 tract in WPRDC's centroid file.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.features.transit import points_in_polygons

__all__ = [
    "ASSUMPTIONS",
    "LAND_USE",
    "allegheny_class",
    "allegheny_parcels",
    "load_rules",
    "parcel_columns",
    "philadelphia_class",
    "philadelphia_parcels",
    "place_in_tracts",
]

ASSUMPTIONS = Path(__file__).resolve().parents[3] / "assumptions" / "parcels.yaml"
PHILADELPHIA = "42101"
ALLEGHENY = "42003"


def load_rules(path: Path = ASSUMPTIONS) -> dict[str, Any]:
    """Read ``assumptions/parcels.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


LAND_USE: tuple[str, ...] = tuple(load_rules()["land_use_classes"])
"""Shared land-use classes, in display order."""


def parcel_columns(rules: Mapping[str, Any]) -> list[str]:
    """Every column of ``parcels_area``, in order."""
    groups = list(rules["year_built_groups"])
    return [
        "level",
        "geoid",
        "county",
        "as_of",
        "value_basis",
        "parcels",
        *[f"lu_{c}" for c in rules["land_use_classes"]],
        "sf_parcels",
        "sf_median_value",
        "sf_median_lot_sqft",
        *[f"sf_built_{g}" for g in groups],
        "sf_built_unknown",
        "sales_short",
        "sales_long",
        "median_sale_short",
        "median_sale_long",
        "unplaced",
    ]


# --------------------------------------------------------------------------- land use


def philadelphia_class(
    category: pd.Series, condo: pd.Series, rules: Mapping[str, Any]
) -> pd.Series:
    """Shared land-use class of each Philadelphia parcel from its OPA category."""
    phl = rules["philadelphia"]
    cls = category.astype(str).map({str(k): v for k, v in phl["categories"].items()})
    cls = cls.fillna(phl["default"])
    is_condo = (category.astype(str) == "1") & (pd.to_numeric(condo, errors="coerce") == 1)
    return cls.where(~is_condo, "condo")


def allegheny_class(cls: pd.Series, use: pd.Series, rules: Mapping[str, Any]) -> pd.Series:
    """Shared land-use class of each Allegheny row from ``USEDESC`` then ``CLASSDESC``."""
    a = rules["allegheny"]
    by_use = use.fillna("").map(a["use_codes"])
    apartments = use.fillna("").str.upper().str.startswith(a["apartment_prefix"])
    by_use = by_use.where(~apartments, "apartments")
    by_class = cls.fillna("").str.upper().map(a["classes"]).fillna(a["default"])
    return by_use.fillna(by_class)


def single_family_uses(rules: Mapping[str, Any]) -> list[str]:
    """Allegheny ``USEDESC`` values in the single-family class."""
    return [u for u, c in rules["allegheny"]["use_codes"].items() if c == "single_family"]


# --------------------------------------------------------------------------- Philadelphia


def place_in_tracts(
    lat: Iterable[float], lon: Iterable[float], shapes: Iterable[tuple[str, Mapping[str, Any]]]
) -> np.ndarray:
    """The 2020 tract holding each point (``""`` when none does)."""
    return points_in_polygons(np.asarray(lat, float), np.asarray(lon, float), list(shapes))


def _median(values: pd.Series, minimum: int) -> float | None:
    v = pd.to_numeric(values, errors="coerce").dropna()
    if len(v) < minimum or len(v) == 0:
        return None
    return float(v.median())


def _months_back(day: date, months: int) -> date:
    from visionhousing.data.sources.alle_assess import months_back

    return months_back(day, months)


def philadelphia_parcels(
    parcels: pd.DataFrame, tracts: np.ndarray, read_on: str, rules: Mapping[str, Any]
) -> pd.DataFrame:
    """``parcels_area`` rows for Philadelphia from the slim OPA extract.

    Args:
        parcels: The extract of :func:`visionhousing.data.sources.phl_opa.fetch_opa_parcels`.
        tracts: The 2020 tract of each parcel (``""`` when unplaced).
        read_on: The date the extract was read (``YYYY-MM-DD``); sales windows
            count back from it.
        rules: ``assumptions/parcels.yaml``.
    """
    p = parcels.copy()
    p["tract"] = tracts
    p["cls"] = philadelphia_class(p["category"], p["condo"], rules)
    for c in ("market_value", "year_built", "lot_sqft", "sale_price"):
        p[c] = pd.to_numeric(p[c], errors="coerce")
    p["sale_date"] = pd.to_datetime(p["sale_date"], errors="coerce")
    day = date.fromisoformat(read_on)
    short, long_ = sorted(rules["sales_windows_months"])
    cut_short = pd.Timestamp(_months_back(day, short))
    cut_long = pd.Timestamp(_months_back(day, long_))
    floor = float(rules["min_sale_price"])
    minimum = int(rules["min_parcels_for_median"])
    groups = rules["year_built_groups"]

    def summary(rows: pd.DataFrame) -> dict[str, Any]:
        sf = rows[rows["cls"] == "single_family"]
        out: dict[str, Any] = {"parcels": len(rows)}
        counts = rows["cls"].value_counts()
        for c in rules["land_use_classes"]:
            out[f"lu_{c}"] = int(counts.get(c, 0))
        out["sf_parcels"] = len(sf)
        out["sf_median_value"] = _median(sf["market_value"].where(sf["market_value"] > 0), minimum)
        out["sf_median_lot_sqft"] = _median(sf["lot_sqft"].where(sf["lot_sqft"] > 0), minimum)
        yb = sf["year_built"]
        known = yb.notna() & (yb > 0)
        for g, (lo, hi) in groups.items():
            out[f"sf_built_{g}"] = int((known & (yb >= max(lo, 1)) & (yb <= hi)).sum())
        out["sf_built_unknown"] = int((~known).sum())
        sold = sf[(sf["sale_price"] >= floor) & sf["sale_date"].notna()]
        recent_long = sold[sold["sale_date"] >= cut_long]
        recent_short = sold[sold["sale_date"] >= cut_short]
        out["sales_short"] = len(recent_short)
        out["sales_long"] = len(recent_long)
        out["median_sale_short"] = _median(recent_short["sale_price"], minimum)
        out["median_sale_long"] = _median(recent_long["sale_price"], minimum)
        return out

    rows = []
    for tract, part in p[p["tract"] != ""].groupby("tract"):
        rows.append({"level": "tract", "geoid": tract, **summary(part)})
    county = summary(p)
    county["unplaced"] = int((p["tract"] == "").sum())
    rows.append({"level": "county", "geoid": PHILADELPHIA, **county})
    return _finish(rows, PHILADELPHIA, read_on, "market value (Philadelphia OPA)", rules)


def _finish(
    rows: list[dict[str, Any]], county: str, read_on: str, basis: str, rules
) -> pd.DataFrame:
    out = pd.DataFrame(rows)
    out["county"] = county
    out["as_of"] = read_on
    out["value_basis"] = basis
    for c in parcel_columns(rules):
        if c not in out.columns:
            out[c] = None
    return out[parcel_columns(rules)]


# --------------------------------------------------------------------------- Allegheny


def allegheny_parcels(
    use_counts: pd.DataFrame,
    single_family: pd.DataFrame,
    sales: pd.DataFrame,
    rules: Mapping[str, Any],
) -> pd.DataFrame:
    """``parcels_area`` rows for Allegheny from the three server-side extracts.

    Each extract has one row per tract plus one county row whose ``tract`` is
    empty. Medians were worked out on WPRDC's server; they are blanked here
    when they rest on fewer parcels or sales than the minimum.
    """
    minimum = int(rules["min_parcels_for_median"])
    uc = use_counts.copy()
    uc["cls"] = allegheny_class(uc["class"], uc["use"], rules)
    uc["parcels"] = pd.to_numeric(uc["parcels"], errors="coerce").fillna(0)
    uc["tract"] = uc["tract"].fillna("")
    # A few border parcels' centre points fall in a neighbouring county's tract:
    # they count only in the county total ("unplaced").
    uc = uc[(uc["tract"] == "") | uc["tract"].str.startswith(ALLEGHENY)]
    lu = uc.pivot_table(index="tract", columns="cls", values="parcels", aggfunc="sum", fill_value=0)
    sf = single_family.copy()
    sf["tract"] = sf["tract"].fillna("")
    sf = sf.set_index("tract")
    sa = sales.copy()
    sa["tract"] = sa["tract"].fillna("")
    sa = sa.set_index("tract")
    read_on = str(sf["read_on"].iloc[0]) if "read_on" in sf.columns and len(sf) else ""

    def num(frame: pd.DataFrame, key: str, col: str) -> float | None:
        if key not in frame.index or col not in frame.columns:
            return None
        v = pd.to_numeric(pd.Series([frame.at[key, col]]), errors="coerce").iloc[0]
        return None if pd.isna(v) else float(v)

    rows = []
    for key in lu.index:
        row: dict[str, Any] = {
            "level": "county" if key == "" else "tract",
            "geoid": ALLEGHENY if key == "" else key,
            "parcels": int(lu.loc[key].sum()),
        }
        for c in rules["land_use_classes"]:
            row[f"lu_{c}"] = int(lu.loc[key].get(c, 0))
        n_sf = num(sf, key, "sf_parcels") or 0.0
        row["sf_parcels"] = int(n_sf)
        for c in ("sf_median_value", "sf_median_lot_sqft"):
            row[c] = num(sf, key, c) if n_sf >= minimum else None
        for g in [*rules["year_built_groups"], "unknown"]:
            row[f"sf_built_{g}"] = int(num(sf, key, f"sf_built_{g}") or 0)
        for w in ("short", "long"):
            n = num(sa, key, f"sales_{w}") or 0.0
            row[f"sales_{w}"] = int(n)
            row[f"median_sale_{w}"] = num(sa, key, f"median_sale_{w}") if n >= minimum else None
        rows.append(row)
    county = next(r for r in rows if r["level"] == "county")
    placed = sum(r["parcels"] for r in rows if r["level"] == "tract")
    county["unplaced"] = max(county["parcels"] - placed, 0)
    basis = "fair market value (Allegheny County assessment, 2012 base year)"
    return _finish(rows, ALLEGHENY, read_on, basis, rules)
