"""A run's results by legislative district: baseline, each reform, the package, and a brief.

Plain-language summary
----------------------
:func:`district_results` takes a finished engine run and the state's district
tables and returns, for one chamber, every district's:

- **baseline**: the county baseline draws shared out by county weights;
- **each reform**: a tract-level reform (ADU, missing middle, transit-oriented
  density) by tract weights from its per-tract draws; a county-level reform
  (fee cut, parking) by county weights from its per-county draws;
- **package** (two or more reforms): its tract-level part by tract weights plus
  its county-level part by county weights, draw by draw;
- the same three ranges **per 1,000 of the district's 2020 homes** (the 2020
  count is fixed, so this is the homes range scaled, draw by draw);
- the **split shares** and a caveat flag when the split share is above the
  ledger threshold ``districts.split_caveat_share``.

Each is P10 / P50 / P90 over the run's draws (see
:mod:`visionhousing.districts.aggregate`). It only reads the run; it never
changes it or runs the engine again.

The **brief** part names what a one-page district brief shows and where its
words come from: the baseline and each reform's range (in the district rows),
a pointer to each reform's top driver sentence (statewide; the sensitivity
tornado in the run's explanation) and a pointer to its "what this cannot tell
you" lines (``assumptions/limits.yaml``), plus the district-specific limits.

District tables come from the state pack (``districts.csv.gz``,
``district_tract_weights.csv.gz``, ``district_county_weights.csv.gz``). For
Testland they are made on the fly from made-up districts
(:func:`visionhousing.districts.weights.synthetic_assignment`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.districts.aggregate import DistrictWeights, district_draws, percentiles
from visionhousing.districts.weights import synthetic_assignment, weights_for

__all__ = [
    "BOUNDARY_FILES",
    "CHAMBERS",
    "SPLIT_PARAMETER",
    "DistrictTables",
    "district_results",
    "load_tables",
]

CHAMBERS = ("upper", "lower")
SPLIT_PARAMETER = "districts.split_caveat_share"
"""Ledger id of the split share above which a district's numbers carry a caveat."""
BOUNDARY_FILES = {"upper": "boundaries/districts_upper.geojson",
                  "lower": "boundaries/districts_lower.geojson"}  # fmt: skip
"""Where each chamber's outlines sit in a pack."""
VINTAGE_FILE = "district_vintage.json"


@dataclass(frozen=True)
class DistrictTables:
    """A state's three district tables, plus where its plans and outlines come from."""

    districts: pd.DataFrame
    tract_weights: pd.DataFrame
    county_weights: pd.DataFrame
    vintage: Mapping[str, Any]
    synthetic: bool = False


def _read(path: Path) -> pd.DataFrame:
    dtypes = {c: str for c in ("chamber", "district", "name", "tract", "county")}
    if path.suffix == ".gz":
        return pd.read_csv(path, dtype=dtypes, compression="gzip", keep_default_na=False)
    return pd.read_csv(path, dtype=dtypes, keep_default_na=False)


def load_tables(folder: Path, synthetic: bool = False) -> DistrictTables:
    """Read a pack's district tables, or make Testland's made-up ones.

    Args:
        folder: The state's data folder (a pack, or the Testland fixture folder).
        synthetic: True for Testland: districts are drawn from its block points.

    Raises:
        FileNotFoundError: The pack has no district tables yet.
    """
    folder = Path(folder)
    if synthetic:
        blocks = _read(folder / "blocks.csv")
        blocks["geoid"] = blocks["geoid"].astype(str)
        tracts = _read(folder / "tracts.csv")
        assignments, names = synthetic_assignment(blocks)
        t = weights_for(blocks, assignments, names, tracts["geoid"].astype(str))
        vintage = {"plans": "synthetic (Testland)", "outlines": "synthetic rectangles (Testland)",
                   "synthetic": True}  # fmt: skip
        return DistrictTables(
            t["districts"], t["district_tract_weights"], t["district_county_weights"], vintage, True
        )
    needed = ("districts", "district_tract_weights", "district_county_weights")
    paths = [folder / f"{n}.csv.gz" for n in needed]
    missing = [p.name for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            f"no district tables in {folder.name} ({', '.join(missing)}); "
            f"run `visionhousing districts --state {folder.name}`"
        )
    vintage_path = folder / VINTAGE_FILE
    vintage = json.loads(vintage_path.read_text(encoding="utf-8")) if vintage_path.is_file() else {}
    return DistrictTables(*(_read(p) for p in paths), vintage=vintage)


def _range(arr: np.ndarray) -> dict[str, float]:
    p10, p50, p90 = (round(float(x), 1) for x in arr)
    return {"p10": p10, "p50": p50, "p90": p90}


def _ranges(draws: np.ndarray) -> list[dict[str, float]]:
    return [_range(row) for row in percentiles(draws)]


def _brief(result: Any, tables: DistrictTables, chamber: str) -> dict[str, Any]:
    reforms = []
    for key, out in result.reforms.items():
        level = out.result.level if out.result is not None else "county"
        reforms.append(
            {
                "key": key,
                "title": out.title,
                "level": level,
                "allocation": (
                    "tract results shared out by 2020 housing units (tract weights)"
                    if level == "tract"
                    else "county results shared out by 2020 housing units (county weights)"
                ),
                "range": f"districts[].reforms.{key}",
                "driver": {
                    "scope": "state",
                    "ref": f"drivers.{key}.0",
                    "path": f"explanation.drivers.{key}.items[0].text",
                    "request": "POST /api/v1/run with drivers=true",
                },
                "limits": {
                    "key": key,
                    "path": f"explanation.limits.reforms.{key}",
                    "file": "assumptions/limits.yaml",
                },
            }
        )
    return {
        "chamber": chamber,
        "baseline": {
            "range": "districts[].baseline",
            "allocation": "county weights",
            "limits": {"key": "baseline", "path": "explanation.limits.baseline"},
        },
        "reforms": reforms,
        "package": "districts[].package" if _has_package(result) else None,
        "limits": {
            "general": {"key": "general", "path": "explanation.limits.general"},
            "districts": {"key": "districts", "file": "assumptions/limits.yaml"},
        },
        "caveat": {"field": "districts[].caveat", "parameter": SPLIT_PARAMETER},
    }


def _has_package(result: Any) -> bool:
    return result.package is not None and len(result.package.reforms) > 1


def district_results(
    result: Any,
    tables: DistrictTables,
    chamber: str,
    threshold: float,
    *,
    district: str | None = None,
) -> dict[str, Any]:
    """Return one chamber's district results for a run, draw by draw then percentiles.

    Args:
        result: An engine ``Result`` (read only).
        tables: The state's district tables (:func:`load_tables`).
        chamber: ``"upper"`` or ``"lower"``.
        threshold: Split share above which a district is flagged (ledger
            ``districts.split_caveat_share``).
        district: Only this district code in ``districts`` (all are still
            computed, so ``state_check`` stays whole).

    Returns:
        ``{"chamber", "threshold", "vintage", "reforms", "districts", "state_check",
        "brief"}``. ``state_check`` holds the sum over districts, per draw, as
        P10/P50/P90 (it equals the engine's state ranges).

    Raises:
        ValueError: Unknown chamber, or results that some weights do not cover.
        KeyError: Unknown district code.
    """
    if chamber not in CHAMBERS:
        raise ValueError("chamber must be 'upper' or 'lower'")
    info = tables.districts[tables.districts["chamber"] == chamber].reset_index(drop=True)
    ids = tuple(info["district"].astype(str))
    tract_w = DistrictWeights.from_table(tables.tract_weights, chamber, "tract", ids)
    county_w = DistrictWeights.from_table(tables.county_weights, chamber, "county", ids)
    counties = tuple(str(c) for c in result.county_ids)

    series: dict[str, np.ndarray] = {
        "baseline": district_draws(result.baseline_county_draws, counties, county_w)
    }
    for key, out in result.reforms.items():
        res = out.result
        if res is not None and res.level == "tract":
            series[key] = district_draws(res.added, res.geoids, tract_w)
        else:
            series[key] = district_draws(out.county_draws, counties, county_w)
    if _has_package(result):
        pkg = result.package
        county_part = np.asarray(pkg.county_draws, dtype=float)
        total = np.zeros((len(ids), county_part.shape[1]))
        if pkg.tract_draws is not None:
            total += district_draws(pkg.tract_draws, pkg.tract_geoids, tract_w)
            county_part = county_part - np.asarray(pkg.tract_county_draws, dtype=float)
        series["package"] = total + district_draws(county_part, counties, county_w)

    ranges = {name: _ranges(d) for name, d in series.items()}
    units = info["units"].astype(float).to_numpy()
    # Homes per 1,000 of the district's 2020 homes: the 2020 count is one fixed number per
    # district, so the percentile of the per-draw rate is the percentile of homes scaled.
    rates = {
        name: [
            _range(row * 1000.0 / u) if u > 0 else None
            for row, u in zip(percentiles(d), units, strict=True)
        ]
        for name, d in series.items()
    }
    reform_keys = list(result.reforms)
    rows = []
    for i, d in enumerate(ids):
        if district is not None and d != district:
            continue
        split = float(info.at[i, "split_share"])
        rows.append(
            {
                "id": d,
                "name": str(info.at[i, "name"]),
                "units_2020": int(info.at[i, "units"]),
                "tracts": int(info.at[i, "tracts"]),
                "counties": int(info.at[i, "counties"]),
                "split_share": split,
                "county_split_share": float(info.at[i, "county_split_share"]),
                "caveat": bool(split > threshold),
                "baseline": ranges["baseline"][i],
                "reforms": {k: ranges[k][i] for k in reform_keys},
                "package": ranges["package"][i] if "package" in ranges else None,
                "per_1000": {
                    "baseline": rates["baseline"][i],
                    "reforms": {k: rates[k][i] for k in reform_keys},
                    "package": rates["package"][i] if "package" in rates else None,
                },
            }
        )
    if district is not None and not rows:
        raise KeyError(district)
    check = {
        name: _range(percentiles(d.sum(axis=0, keepdims=True))[0]) for name, d in series.items()
    }
    return {
        "chamber": chamber,
        "threshold": threshold,
        "vintage": dict(tables.vintage),
        "synthetic": tables.synthetic,
        "reforms": [{"key": k, "title": result.reforms[k].title} for k in reform_keys],
        "district_count": len(ids),
        "districts": rows,
        "state_check": check,
        "brief": _brief(result, tables, chamber),
    }
