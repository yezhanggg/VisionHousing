"""Which unread zoning ordinances to read next: a review-priority score.

Plain-language summary
----------------------
About 2,500 Pennsylvania municipalities have not been read. Reading one takes a
person's time, so we read first where a reading is worth most to the ADU
estimate. Each **review unit** gets

``score = unknown lots x sensitivity x representativeness / effort``

- A review unit is one municipality with its own (or joint) zoning, or one
  county ordinance together with every municipality it zones (one reading
  answers for all of them). Municipalities with no zoning at all have no
  ordinance to read and are not ranked.
- **Unknown lots**: single-family lots in the unit whose rule is not *read*
  (``muni_zoning_rules.sf_lots``). This is the exposure a reading resolves.
  "Read" is the hooks' own test (:mod:`visionhousing.law.inforce`): verified
  *and* in force on the run's as-of date. A verified row whose dates are
  unknown, or whose effective date is still to come, counts for nothing in the
  estimate, so it stays on the list (its note says why) until its dates are
  found.
- **Sensitivity**: how much the statewide ADU result moves per lot in this
  county: the county's added ADU homes per single-family lot in the latest
  exported ADU benchmark run, divided by the state's ratio. It is multiplied by
  a **switch factor**: the engine uses a county's readings only once they
  cover ``min_coverage`` (50%) of its residents, so in a county below that bar
  a unit counts by the share of the missing residents it would add (at least
  ``switch_floor``); in a county over the bar it counts fully.
- **Representativeness**: ``1 + max(0, target - c) / target``, where ``c`` is the
  share of single-family lots read among all municipalities of the same place
  type (the tract typology most of the unit's homes are in). Place types that
  are thinly read rank higher, so the readings do not all come from one kind
  of place.
- **Effort**: relative review time from ``assumptions/law/review_sources.yaml``
  (1 for an own-site PDF with a text layer, 3 for a scan, 2 when nobody has
  looked yet). A unit whose ordinance is only on a commercial code host cannot
  be read here; it is listed with score 0 and the reason.

Parameters live in ``assumptions/law/priorities.yaml``. The ranking is
deterministic: ties are broken by unit id. It changes no estimate; it only
orders the reading list.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from visionhousing.law.coverage import county_coverage
from visionhousing.law.inforce import hook_value, read_flags

__all__ = [
    "PRIORITIES_PATH",
    "SOURCES_PATH",
    "adu_yield_ratio",
    "dominant_type",
    "load_priorities",
    "load_review_sources",
    "review_priorities",
]

ROOT = Path(__file__).resolve().parents[3]
PRIORITIES_PATH = ROOT / "assumptions" / "law" / "priorities.yaml"
SOURCES_PATH = ROOT / "assumptions" / "law" / "review_sources.yaml"

COLUMNS = [
    "rank",
    "unit_id",
    "county",
    "county_name",
    "names",
    "municipalities",
    "zoning_regime",
    "residents",
    "sf_lots",
    "unknown_lots",
    "county_coverage_residents",
    "county_coverage_lots",
    "yield_ratio",
    "switch_factor",
    "sensitivity",
    "sensitivity_source",
    "place_type",
    "type_lot_coverage",
    "representativeness",
    "effort",
    "effort_basis",
    "score",
    "source_url",
    "note",
]


def load_priorities(path: Path = PRIORITIES_PATH) -> dict[str, Any]:
    """Read ``assumptions/law/priorities.yaml``."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_review_sources(path: Path = SOURCES_PATH) -> dict[str, dict[str, Any]]:
    """Geoid (or ``county:<code>``) to what is known about where its ordinance is."""
    if not Path(path).is_file():
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {str(s["unit"]): s for s in data.get("sources", [])}


def adu_yield_ratio(run: dict[str, Any] | None, lots: pd.Series) -> tuple[pd.Series, str]:
    """County added ADU homes per lot divided by the state's, from an exported run.

    The state's rate is the run's own state record (``reform_results.adu.added.p50``, the
    P50 over draws of the state total) divided by the state's single-family lots; summing
    county P50s would not give the state's P50. A county with no lots, or a state with no
    added homes or lots, gets ratio 1.

    Args:
        run: The unpacked ADU run (``reform_results.adu.added.p50`` and
            ``reform_results.adu.counties[c].p50``), or ``None``.
        lots: Single-family lots per county (index: county code).

    Returns:
        The ratio per county (1 where unknown) and a short source label.
    """
    none = pd.Series(1.0, index=lots.index), "none (ratio 1)"
    if not run:
        return none
    try:
        adu = run["reform_results"]["adu"]
        state_added = float(adu["added"]["p50"])
        counties = adu["counties"]
        added = pd.Series({str(c): float(v["p50"]) for c, v in counties.items()}, dtype=float)
    except (KeyError, TypeError, ValueError):
        return none
    lots = pd.to_numeric(lots, errors="coerce").fillna(0.0)
    state_lots = float(lots.sum())
    if not (state_added > 0 and state_lots > 0) or not np.isfinite(state_added):
        return none
    state = state_added / state_lots
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = (added.reindex(lots.index) / lots.where(lots > 0)) / state
    ratio = ratio.replace([np.inf, -np.inf], np.nan).fillna(1.0)
    return ratio, "ADU run, county P50 added homes per lot / state P50 added homes per lot"


def dominant_type(
    typology_tract: pd.DataFrame | None, tract_munis: pd.DataFrame | None
) -> pd.Series:
    """Municipality geoid to the place type holding most of its 2020 homes (empty when unknown)."""
    if typology_tract is None or tract_munis is None:
        return pd.Series(dtype=str)
    tm = tract_munis.copy()
    tm["geoid"] = tm["county"].astype(str).str.zfill(5) + tm["cousub"].astype(str).str.zfill(5)
    tm["tract"] = tm["tract"].astype(str)
    tm["hu"] = pd.to_numeric(tm["housing_units_2020"], errors="coerce").fillna(0.0)
    types = typology_tract.assign(geoid=typology_tract["geoid"].astype(str)).set_index("geoid")
    tm["type"] = tm["tract"].map(types["type_name"])
    tm = tm.dropna(subset=["type"])
    best = (
        tm.groupby(["geoid", "type"])["hu"]
        .sum()
        .reset_index()
        .sort_values(["geoid", "hu", "type"], ascending=[True, False, True])
        .drop_duplicates("geoid")
    )
    return best.set_index("geoid")["type"]


def review_priorities(
    rules: pd.DataFrame,
    munis: pd.DataFrame,
    *,
    counties: pd.DataFrame | None = None,
    typology_tract: pd.DataFrame | None = None,
    tract_munis: pd.DataFrame | None = None,
    adu_run: dict[str, Any] | None = None,
    sources: dict[str, dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
    hooks: dict[str, Any] | None = None,
    as_of: date | str | None = None,
) -> pd.DataFrame:
    """Rank the unread review units (columns :data:`COLUMNS`), best first.

    ``rules`` is ``muni_zoning_rules`` with its ``sf_lots`` weights; ``munis``
    is ``municipal_planning``. A municipality counts as read only when its row
    is verified and in force on ``as_of`` (the run's as-of date when ``None``;
    :func:`visionhousing.law.inforce.read_flags`). Everything else is optional
    and documented in the module summary.
    """
    params = params or load_priorities()
    sources = sources if sources is not None else load_review_sources()
    bar = float(hook_value(hooks, "min_coverage"))
    target = float(params["target_lot_coverage"])
    floor = float(params["switch_floor"])
    efforts = params["effort"]

    m = munis.copy()
    m["geoid"] = m["county"].astype(str).str.zfill(5) + m["cousub"].astype(str).str.zfill(5)
    m["county"] = m["geoid"].str[:5]
    m["residents"] = pd.to_numeric(m["population"], errors="coerce").fillna(0.0)
    r = rules.assign(geoid=rules["geoid"].astype(str)).set_index("geoid")
    m["sf_lots"] = m["geoid"].map(pd.to_numeric(r["sf_lots"], errors="coerce")).fillna(0.0)
    read, _ = read_flags(rules.reset_index(drop=True), as_of, hooks)
    read_by_geoid = pd.Series(read.to_numpy(), index=rules["geoid"].astype(str))
    m["read"] = m["geoid"].map(read_by_geoid).eq(True)
    m["verified"] = m["geoid"].map(r["status"]).eq("verified")
    m["type"] = m["geoid"].map(dominant_type(typology_tract, tract_munis)).astype(object).fillna("")

    cov = county_coverage(rules, munis, hooks, as_of=as_of).set_index("county")
    lots_by_county = m.groupby("county")["sf_lots"].sum()
    ratio, ratio_source = adu_yield_ratio(adu_run, lots_by_county)
    type_cov = m.groupby("type").apply(
        lambda g: (
            g.loc[g["read"], "sf_lots"].sum() / g["sf_lots"].sum()
            if g["sf_lots"].sum() > 0
            else 0.0
        ),
        include_groups=False,
    )
    names = {}
    if counties is not None:
        names = dict(zip(counties["geoid"].astype(str), counties["name"].astype(str), strict=False))

    todo = m[~m["read"] & (m["zoning_regime"] != "none")].copy()
    todo["unit_id"] = [
        f"county:{c}" if reg == "county" else g
        for g, c, reg in zip(todo["geoid"], todo["county"], todo["zoning_regime"], strict=True)
    ]
    rows = []
    for unit, g in todo.groupby("unit_id", sort=True):
        county = str(g["county"].iloc[0])
        residents = float(g["residents"].sum())
        lots = float(g["sf_lots"].sum())
        c = cov.loc[county] if county in cov.index else None
        cov_res = float(c["coverage_residents"]) if c is not None else 0.0
        cov_lots = c["coverage_lots"] if c is not None else None
        if cov_res >= bar:
            switch = 1.0
        else:
            total = float(c["residents"]) if c is not None else residents
            gap = bar * total - (float(c["residents_read"]) if c is not None else 0.0)
            switch = max(floor, min(1.0, residents / gap)) if gap > 0 else 1.0
        yr = float(ratio.get(county, 1.0))
        sensitivity = yr * switch
        kind = g.groupby("type")["sf_lots"].sum().sort_values(ascending=False)
        ptype = str(kind.index[0]) if len(kind) else ""
        tcov = float(type_cov.get(ptype, 0.0)) if ptype else 0.0
        rep = 1.0 + max(0.0, target - tcov) / target if target > 0 else 1.0
        src = (
            sources.get(unit) or next((sources[x] for x in g["geoid"] if x in sources), None) or {}
        )
        basis = str(src.get("effort", "not_searched"))
        effort = efforts.get(basis, efforts["not_searched"])
        score = 0.0 if effort is None else lots * sensitivity * rep / float(effort)
        note = " ".join(str(src.get("note", "")).split())
        if bool(g["verified"].any()):
            pending = "verified but not in force on the as-of date (dates unknown or later)"
            note = f"{pending}; {note}" if note else pending
        rows.append(
            {
                "unit_id": unit,
                "county": county,
                "county_name": names.get(county, ""),
                "names": "; ".join(sorted(g["name"].astype(str)))
                if len(g) <= 6
                else f"{len(g)} municipalities under county zoning",
                "municipalities": len(g),
                "zoning_regime": "county"
                if unit.startswith("county:")
                else str(g["zoning_regime"].iloc[0]),
                "residents": round(residents),
                "sf_lots": round(lots, 1),
                "unknown_lots": round(lots, 1),
                "county_coverage_residents": round(cov_res, 4),
                "county_coverage_lots": None
                if cov_lots is None or pd.isna(cov_lots)
                else round(float(cov_lots), 4),
                "yield_ratio": round(yr, 4),
                "switch_factor": round(switch, 4),
                "sensitivity": round(sensitivity, 4),
                "sensitivity_source": ratio_source,
                "place_type": ptype,
                "type_lot_coverage": round(tcov, 4),
                "representativeness": round(rep, 4),
                "effort": effort,
                "effort_basis": basis,
                "score": round(score, 3),
                "source_url": str(src.get("url", "")),
                "note": note,
            }
        )
    out = pd.DataFrame(rows, columns=[c for c in COLUMNS if c != "rank"])
    out = out.sort_values(["score", "unit_id"], ascending=[False, True]).reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out


def read_adu_run(path: Path) -> dict[str, Any] | None:
    """The unpacked exported ADU run at ``path``, or ``None`` when it is missing or unreadable."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        from visionhousing.export_static import read_saved

        return read_saved(path)
    except (ValueError, OSError):
        return None
