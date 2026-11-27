"""Municipal zoning and planning status as state-pack tables.

Plain-language summary
----------------------
Turns the DCED Munstats record (:mod:`visionhousing.data.sources.munstats`)
into two small tables the ``zoning`` and ``plans`` profile sections read:

- ``municipal_planning``: one row per Census county subdivision (a
  municipality, or the part of one inside a county): its DCED id, who
  regulates zoning there (``own``, ``joint``, ``county``, ``none``; see
  :func:`~visionhousing.data.sources.munstats.zoning_regime`), whether it has
  a comprehensive plan and whether that plan is multi-municipal, its other
  yes/no answers, its 2024 population estimate and its 2020 housing units.
- ``tract_municipalities``: one row per piece of a census tract inside one
  municipality, with that piece's 2020 housing units (from 2020 census
  blocks). A tract that crosses a municipal line is split by where its homes
  are.

Rules (the regime labels, the multi-municipal threshold, hand-checked name
pairs) come from ``assumptions/zoning.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from visionhousing.data.sources import munstats

__all__ = [
    "ASSUMPTIONS",
    "MUNICIPAL_COLUMNS",
    "TRACT_COLUMNS",
    "TRACT_PLACE_COLUMNS",
    "load_rules",
    "municipal_planning",
    "read_plan_citations",
    "tract_municipalities",
    "tract_places",
]

ASSUMPTIONS = Path(__file__).resolve().parents[3] / "assumptions" / "zoning.yaml"

MUNICIPAL_COLUMNS = (
    "county",
    "cousub",
    "name",
    "dced_id",
    "zoning_regime",
    "comprehensive_plan",
    "multi_municipal_plan",
    "subdivision_ordinance",
    "planning_commission",
    "zoning_hearing_board",
    "ucc_code",
    "population",
    "housing_units_2020",
)
TRACT_COLUMNS = ("tract", "county", "cousub", "housing_units_2020")
TRACT_PLACE_COLUMNS = ("tract", "county", "place", "housing_units_2020")


def load_rules(path: Path = ASSUMPTIONS) -> dict[str, Any]:
    """Read ``assumptions/zoning.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def municipal_planning(
    crosswalk: munstats.Crosswalk, block_units: pd.DataFrame, multi_min: int
) -> pd.DataFrame:
    """The ``municipal_planning`` table.

    Args:
        crosswalk: DCED rows matched to Census county subdivisions.
        block_units: ``county``, ``cousub``, ``housing_units`` per block piece
            (see :func:`tract_municipalities`), summed here per subdivision.
        multi_min: A plan listing at least this many municipalities is
            multi-municipal.
    """
    t = crosswalk.table.copy()
    t["zoning_regime"] = t.apply(munstats.zoning_regime, axis=1)
    plan = t["comprehensive_plan"]
    members = pd.to_numeric(t["plan_members"], errors="coerce").fillna(0)
    t["multi_municipal_plan"] = ((plan == 1) & (members >= multi_min)).astype("Int64")
    t.loc[plan.isna(), "multi_municipal_plan"] = pd.NA
    units = block_units.groupby(["county", "cousub"])["housing_units_2020"].sum()
    t["housing_units_2020"] = [
        int(units.get((c, s), 0)) for c, s in zip(t["county"], t["cousub"], strict=True)
    ]
    out = t[list(MUNICIPAL_COLUMNS)].copy()
    out["dced_id"] = out["dced_id"].fillna("")
    return out.sort_values(["county", "cousub"]).reset_index(drop=True)


def tract_municipalities(blocks: pd.DataFrame, block_cousub: pd.DataFrame) -> pd.DataFrame:
    """2020 housing units of each tract, split by county subdivision.

    Args:
        blocks: Pack blocks: ``geoid`` (15 digits), ``tract``, ``housing_units``.
        block_cousub: ``block`` and ``cousub`` from the block assignment file.
    """
    b = blocks[["geoid", "tract", "housing_units"]].merge(
        block_cousub.rename(columns={"block": "geoid"}), on="geoid", how="left"
    )
    b["cousub"] = b["cousub"].fillna("")
    b["county"] = b["tract"].astype(str).str[:5]
    b["housing_units"] = pd.to_numeric(b["housing_units"], errors="coerce").fillna(0)
    out = (
        b.groupby(["tract", "county", "cousub"], as_index=False)["housing_units"]
        .sum()
        .rename(columns={"housing_units": "housing_units_2020"})
    )
    out["housing_units_2020"] = out["housing_units_2020"].astype(int)
    return out[list(TRACT_COLUMNS)].sort_values(["tract", "cousub"]).reset_index(drop=True)


def tract_places(blocks: pd.DataFrame, block_place: pd.DataFrame, no_place: str) -> pd.DataFrame:
    """2020 housing units of each tract, split by Census place (same weights as above).

    The place twin of :func:`tract_municipalities`, for cities that are Census
    places but not county subdivisions (Wyoming's). A block outside every
    place, or missing from the file, gets ``no_place``, so a tract's pieces
    always add up to all its 2020 housing units.

    Args:
        blocks: Pack blocks: ``geoid`` (15 digits), ``tract``, ``housing_units``.
        block_place: ``block`` and ``place`` from the block assignment file.
        no_place: Code for "not in any place" (``99999``).
    """
    b = blocks[["geoid", "tract", "housing_units"]].merge(
        block_place.rename(columns={"block": "geoid"}), on="geoid", how="left"
    )
    b["place"] = b["place"].fillna(no_place)
    b["county"] = b["tract"].astype(str).str[:5]
    b["housing_units"] = pd.to_numeric(b["housing_units"], errors="coerce").fillna(0)
    out = (
        b.groupby(["tract", "county", "place"], as_index=False)["housing_units"]
        .sum()
        .rename(columns={"housing_units": "housing_units_2020"})
    )
    out["housing_units_2020"] = out["housing_units_2020"].astype(int)
    return out[list(TRACT_PLACE_COLUMNS)].sort_values(["tract", "place"]).reset_index(drop=True)


PLANS = ASSUMPTIONS.with_name("plans.yaml")


def read_plan_citations(path: Path = PLANS) -> dict[str, dict[str, Any]]:
    """County comprehensive plan citations from ``assumptions/plans.yaml``, by county code.

    Each value has ``county``, ``name``, ``adopted`` (year or ``None``), ``url``,
    ``accessed``, ``verified`` and ``note``.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {str(k): dict(v) for k, v in (data.get("plans") or {}).items()}
