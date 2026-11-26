"""Extra scenarios: homes by right on commercial land, and office/retail-to-home conversions.

Plain-language summary
----------------------
Two separate routes to homes on land now used for shops and offices. They use
the same eligible land but answer different questions and are never added
together in the Building Permits Survey (BPS) measure.

**Route 1: housing by right on commercial land** (``commercial_land_housing``).
A state lets apartment buildings be built by right on land zoned for commercial,
office or retail use (as Texas SB 840 of 2025 does in its large cities, with at
least 36 homes per acre, 45 ft and no more than one parking space per home).
New buildings need new-construction permits, so they are in the BPS measure::

    eligible acres  = commercial parcels x acres per parcel        (parcel land use)
    redevelopable   = eligible acres x commercial.redevelopable_share
    feasible        = redevelopable x p
    capacity        = feasible x commercial.units_per_acre
    built           = capacity x min(1, yearly share x sum of ramp)
    absorbed        = built, capped at commercial.absorption_cap_share x county baseline 5+ permits
    authorized      = absorbed x net-new share x (1 - share the baseline would have rezoned)

``p`` is the share of the tract's owner home values ``V`` (ACS brackets, a local
price level) for which a site of one acre, priced at ``V x
commercial.site_value_home_equivalents_per_acre x (1 + acquisition premium)``,
costs no more than the residual land value of ``units_per_acre`` apartments in a
four-storey walk-up (the TOD low-rise prototype: ``tod.lowrise_hard_cost_per_sqft``,
surface parking, at most ``commercial.max_spaces_per_unit`` spaces per home).
No home stands on commercial land, so no home is demolished.

**Route 2: adaptive reuse** (``adaptive_reuse``). An existing office or retail
building is converted into apartments. A conversion is an alteration of an
existing building, which the BPS leaves out, so its homes are **reported
outside the BPS-comparable total**: the reform's added authorizations are zero
and the conversions are a separate record (``conversion_homes``)::

    buildings     = commercial parcels x reuse.suitable_building_share
    homes/building = floor area x efficiency / unit size
    feasible      = share of the tract's renters paying at least the break-even rent
                    / new-unit rent ratio (tod.new_unit_rent_premium, a multiple of
                    today's rent, central 1.25 in the ledger) (ACS rent brackets)
    conversions   = buildings x homes/building x feasible x min(1, yearly share x sum of ramp),
                    capped at reuse.absorption_cap_share x county baseline 5+ permits

Break-even rent: (acquisition + conversion cost per gross sq ft x floor area per
home + fees) x (1 + margin) x cap rate / (12 x (1 - operating share)).

**Eligible land** comes from the pack's parcel land-use table
(``parcels_area``, column ``lu_commercial``), which Pennsylvania has for
Philadelphia (OPA) and Allegheny County only. Every other county is "eligible
land not measured": it gets no homes and says so in a county note. No free
statewide commercial-zoning layer exists; the national land-cover map's
developed classes cannot tell shops from houses, so it is not used as a proxy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.features import brackets as br
from visionhousing.ledger.loader import Ledger
from visionhousing.ledger.sampling import sample
from visionhousing.scenarios.extra_common import ramp_years, table_sources
from visionhousing.scenarios.tod import (
    PROFORMA_IDS,
    RENT_PARAMS,
    SPACES_ID,
    _absorption,
    _new_unit_rent,
    _residual_per_unit,
    _share_below,
)

__all__ = [
    "COMMERCIAL_IDS",
    "REUSE_IDS",
    "LandResult",
    "commercial_land_funnel",
    "eligible_parcels",
    "reuse_funnel",
]

SQ_M_PER_ACRE = 4046.8564224
"""Registry ids behind the parcel land-use counts (Philadelphia and Allegheny County)."""
RENT_SOURCES = ("acs5_summary_file",)
CAP_SOURCES = ("bps_county_annual",)

PARCEL_ACRES = "commercial.parcel_acres"
REDEV = "commercial.redevelopable_share"
DENSITY = "commercial.units_per_acre"
MAX_SPACES = "commercial.max_spaces_per_unit"
SITE_VALUE = "commercial.site_value_home_equivalents_per_acre"
HAZARD = "commercial.redevelopment_hazard_annual"
FIRST = "commercial.ramp_first_year_share"
FULL = "commercial.ramp_years_to_full"
DELAY = "commercial.start_delay_years"
CAP = "commercial.absorption_cap_share"
NET_NEW = "commercial.net_new_share"
REZONED = "commercial.baseline_rezoning_share"
PREMIUM = "tod.acquisition_premium"
LOWRISE_COST = "tod.lowrise_hard_cost_per_sqft"
MF_COST = "multifamily.hard_cost_per_sqft"
SURFACE = "parking.cost_per_space_surface"
STRUCTURED = "parking.cost_per_space_structured"
YEARS = ("baseline.first_year", "baseline.horizon_years")

COMMERCIAL_IDS = (
    PARCEL_ACRES, REDEV, DENSITY, MAX_SPACES, SITE_VALUE, HAZARD, FIRST, FULL, DELAY, CAP,
    NET_NEW, REZONED, PREMIUM, LOWRISE_COST, SURFACE, *RENT_PARAMS, *PROFORMA_IDS,
)  # fmt: skip
"""Ledger ids behind route 1."""

SUITABLE = "reuse.suitable_building_share"
FLOOR = "reuse.floor_area_sqft_per_building"
EFFICIENCY = "reuse.efficiency_share"
UNIT = "reuse.unit_size_sqft"
CONV_COST = "reuse.conversion_cost_per_sqft"
ACQ = "reuse.acquisition_per_sqft"
FEE_SHARE = "reuse.fee_share_of_benchmark"
R_HAZARD = "reuse.conversion_hazard_annual"
R_CAP = "reuse.absorption_cap_share"
FEE = "fees.fee_total_per_unit"
MARGIN = "multifamily.developer_margin"
CAPRATE = "multifamily.cap_rate"
OPEX = "multifamily.operating_cost_share"
NEW_PREMIUM = "tod.new_unit_rent_premium"
REUSE_IDS = (
    SUITABLE, FLOOR, EFFICIENCY, UNIT, CONV_COST, ACQ, FEE_SHARE, R_HAZARD, R_CAP, FEE,
    MARGIN, CAPRATE, OPEX, NEW_PREMIUM, FIRST, FULL, DELAY,
)  # fmt: skip
"""Ledger ids behind route 2."""


def eligible_parcels(tracts: pd.DataFrame, parcels: pd.DataFrame | None) -> np.ndarray:
    """Commercial parcels per tract (``parcels_area``, ``lu_commercial``); NaN where not measured.

    A tract whose county has no parcel table rows is "not measured" (NaN), never 0.
    """
    n = len(tracts)
    out = np.full(n, np.nan)
    if parcels is None or parcels.empty or "lu_commercial" not in parcels:
        return out
    rows = parcels[parcels["level"].astype(str) == "tract"] if "level" in parcels else parcels
    vals = pd.to_numeric(rows["lu_commercial"], errors="coerce")
    by = pd.Series(vals.to_numpy(), index=rows["geoid"].astype(str)).groupby(level=0).sum()
    measured = set(rows["county"].astype(str)) if "county" in rows else set()
    geo = tracts["geoid"].astype(str)
    cty = tracts["county"].astype(str)
    got = geo.map(by).to_numpy(dtype=float)
    in_measured = cty.isin(measured).to_numpy()
    out = np.where(in_measured, np.nan_to_num(got, nan=0.0), np.nan)
    return out


@dataclass(frozen=True)
class LandResult:
    """What either route returns.

    Attributes:
        geoids, counties: Tract ids and their counties.
        stages: Stage name to draws x tracts arrays.
        headline: Stage that is the headline (``authorized`` or ``conversions``).
        records: State records by name (one per stage, plus extras).
        steps: Funnel records in order.
        year_share: ``(draws, years)``.
        measured_counties: Counties with parcel land use; the rest are not measured.
        bps_comparable: True for route 1 (new construction), False for conversions.
    """

    geoids: np.ndarray
    counties: np.ndarray
    stages: dict[str, np.ndarray]
    headline: str
    records: dict[str, Record]
    steps: tuple[Record, ...]
    year_share: np.ndarray
    measured_counties: tuple[str, ...]
    bps_comparable: bool


def _years(ledger: Ledger) -> tuple[int, ...]:
    first = int(ledger[YEARS[0]].central)
    return tuple(range(first, first + int(ledger[YEARS[1]].central)))


def _sources(parcel: Sequence[str], measured: bool, *more: Sequence[str]) -> tuple[str, ...]:
    """Source ids of a record: ACS rents, any extra ids, and the parcel table's own.

    The parcel table's sources are cited only where land is measured, as the pack
    names them (``extra_common.table_sources``).
    """
    out = set(RENT_SOURCES)
    for m in more:
        out |= set(m)
    if measured:
        out |= set(parcel)
    return tuple(sorted(out))


def commercial_land_funnel(
    tracts: pd.DataFrame,
    ledger: Ledger,
    parcels: pd.DataFrame | None,
    *,
    mf_baseline: pd.Series | Mapping[str, float] | None = None,
    mf_scale: np.ndarray | None = None,
    draws: int = 2000,
    seed: int = 0,
) -> LandResult:
    """Route 1: apartments by right on commercial land (new construction, BPS-comparable)."""
    for pid in (*COMMERCIAL_IDS, *YEARS):
        ledger[pid]
    v = sample(ledger, n=draws, seed=seed, ids=list(dict.fromkeys(COMMERCIAL_IDS)))
    col: dict[str, Any] = {k: a[:, None] for k, a in v.items()}
    years = _years(ledger)
    n = len(tracts)
    counties = tracts["county"].astype(str).to_numpy()
    parcels_t = eligible_parcels(tracts, parcels)
    measured = np.isfinite(parcels_t)
    count = np.nan_to_num(parcels_t)[None, :]

    eligible = count * col[PARCEL_ACRES]  # acres
    eligible = np.broadcast_to(eligible, (draws, n))
    redevelopable = eligible * col[REDEV]

    # Walk-up prototype on commercial land: low-rise cost, surface parking, capped spaces.
    pf = dict(col)
    pf[MF_COST] = col[LOWRISE_COST]
    pf[STRUCTURED] = col[SURFACE]
    pf[SPACES_ID] = np.minimum(col[SPACES_ID], col[MAX_SPACES])
    pf["parking.share_still_built_transit_rich"] = np.ones_like(col[MAX_SPACES])
    rent = _new_unit_rent(tracts, pf)
    rlv = _residual_per_unit(tracts, pf, rent, draws)  # $ per apartment
    break_even = rlv * col[DENSITY] / (col[SITE_VALUE] * (1.0 + col[PREMIUM]))
    p = np.where(break_even > 0, _share_below(tracts, break_even, draws), 0.0)
    feasible = redevelopable * p
    capacity = feasible * col[DENSITY]
    ramp, year_share = ramp_years(v, FIRST, FULL, DELAY, len(years))
    over = np.minimum(col[HAZARD] * ramp.sum(axis=1)[:, None], 1.0)
    built = capacity * over
    absorbed, _hit = _absorption(built, counties, col[CAP], mf_baseline, mf_scale)
    authorized = absorbed * col[NET_NEW] * (1.0 - col[REZONED])
    stages = {
        "eligible": eligible,
        "redevelopable": redevelopable,
        "feasible": feasible,
        "capacity": capacity,
        "built": built,
        "absorbed": absorbed,
        "authorized": authorized,
    }
    any_measured = bool(measured.any())
    parcel_src = table_sources(parcels)
    src = _sources(parcel_src, any_measured)
    texts = {
        "eligible": (
            "commercial land (acres) where parcel land use is read",
            "acres",
            "commercial parcels (parcels_area lu_commercial) x commercial.parcel_acres; "
            "counties without a parcel table are not measured (0 here)",
            (PARCEL_ACRES,),
        ),
        "redevelopable": (
            "commercial land that could take an apartment building",
            "acres",
            "eligible acres x commercial.redevelopable_share",
            (PARCEL_ACRES, REDEV),
        ),
        "feasible": (
            "commercial land where a four-storey walk-up pencils",
            "acres",
            "redevelopable acres x p, p = share of owner home values V with V x "
            "site value in home equivalents per acre x (1 + acquisition premium) <= "
            "walk-up residual land value per apartment x homes per acre",
            (
                PARCEL_ACRES,
                REDEV,
                DENSITY,
                SITE_VALUE,
                PREMIUM,
                LOWRISE_COST,
                SURFACE,
                MAX_SPACES,
                *RENT_PARAMS,
                *PROFORMA_IDS,
            ),
        ),
        "capacity": (
            "homes the by-right rule allows on feasible commercial land",
            "units",
            "feasible acres x commercial.units_per_acre",
            (),
        ),
        "built": (
            "apartments permitted 2027-2031 before the absorption cap",
            "units",
            "capacity x min(1, yearly share x sum of ramp)",
            (HAZARD, FIRST, FULL, DELAY),
        ),
        "absorbed": (
            "apartments permitted 2027-2031 within regional absorption",
            "units",
            "built x min(1, commercial.absorption_cap_share x county baseline 5+ permits"
            " / county built)",
            (CAP,),
        ),
        "authorized": (
            "added units authorized 2027-2031 on commercial land (new to the region)",
            "units",
            "absorbed x net-new share x (1 - share the baseline would have rezoned)",
            (NET_NEW, REZONED),
        ),
    }
    records: dict[str, Record] = {}
    acc: list[str] = []
    for stage, (name, unit, expr, ids) in texts.items():
        acc += list(ids)
        if stage == "feasible":
            acc = list(dict.fromkeys(acc))
        records[stage] = rec.from_draws(
            f"{name} (housing on commercial land)",
            stages[stage].sum(axis=1),
            unit,
            expression=f"the sum over tracts of {expr}",
            assumption_ids=sorted(set(acc)),
            source_ids=_sources(
                parcel_src, any_measured, CAP_SOURCES if stage in ("absorbed", "authorized") else ()
            ),
            tier=0,
        )
    records["measured_tracts"] = rec.quantity(
        "tracts whose commercial land is measured from parcel land use",
        float(measured.sum()),
        "tracts",
        formula=(
            f"{int(measured.sum()):,} of {n:,} tracts lie in counties with a parcel land-use "
            "table (parcels_area); the rest are 'eligible land not measured'"
        ),
        source_ids=src if any_measured else (),
        tier=0,
    )
    order = tuple(records[s] for s in texts)
    return LandResult(
        geoids=tracts["geoid"].astype(str).to_numpy(),
        counties=counties,
        stages=stages,
        headline="authorized",
        records=records,
        steps=order,
        year_share=year_share,
        measured_counties=tuple(sorted(set(counties[measured]))),
        bps_comparable=True,
    )


def _rent_share_above(tracts: pd.DataFrame, threshold: np.ndarray) -> np.ndarray:
    """Share of each tract's cash renters paying at least ``threshold`` (draws x tracts)."""
    counts = tracts[list(br.RENT.columns)].to_numpy(dtype=float)
    out = br.share_above_grid(counts, br.RENT, np.maximum(threshold, 1.0))
    return np.nan_to_num(out, nan=0.0)


def reuse_funnel(
    tracts: pd.DataFrame,
    ledger: Ledger,
    parcels: pd.DataFrame | None,
    *,
    mf_baseline: pd.Series | Mapping[str, float] | None = None,
    mf_scale: np.ndarray | None = None,
    draws: int = 2000,
    seed: int = 0,
) -> LandResult:
    """Route 2: office and retail buildings converted to apartments (outside the BPS measure)."""
    for pid in (*REUSE_IDS, *YEARS):
        ledger[pid]
    v = sample(ledger, n=draws, seed=seed, ids=list(dict.fromkeys(REUSE_IDS)))
    col = {k: a[:, None] for k, a in v.items()}
    years = _years(ledger)
    n = len(tracts)
    counties = tracts["county"].astype(str).to_numpy()
    parcels_t = eligible_parcels(tracts, parcels)
    measured = np.isfinite(parcels_t)
    count = np.broadcast_to(np.nan_to_num(parcels_t)[None, :], (draws, n))
    buildings = count * col[SUITABLE]
    homes_per = col[FLOOR] * col[EFFICIENCY] / col[UNIT]
    potential = buildings * homes_per
    gross_per_home = col[UNIT] / col[EFFICIENCY]
    cost = (col[ACQ] + col[CONV_COST]) * gross_per_home + col[FEE] * col[FEE_SHARE]
    break_even = cost * (1.0 + col[MARGIN]) * col[CAPRATE] / (12.0 * (1.0 - col[OPEX]))
    threshold = break_even / col[NEW_PREMIUM]
    p = _rent_share_above(tracts, np.broadcast_to(threshold, (draws, n)))
    feasible = potential * p
    ramp, year_share = ramp_years(v, FIRST, FULL, DELAY, len(years))
    over = np.minimum(col[R_HAZARD] * ramp.sum(axis=1)[:, None], 1.0)
    started = feasible * over
    conversions, _hit = _absorption(started, counties, col[R_CAP], mf_baseline, mf_scale)
    stages = {
        "buildings": buildings,
        "potential": potential,
        "feasible": feasible,
        "started": started,
        "conversions": conversions,
    }
    any_measured = bool(measured.any())
    parcel_src = table_sources(parcels)
    texts = {
        "buildings": (
            "office and retail buildings suitable for conversion",
            "buildings",
            "commercial parcels (parcels_area lu_commercial) x reuse.suitable_building_share",
            (SUITABLE,),
        ),
        "potential": (
            "homes those buildings could hold",
            "units",
            "buildings x floor area x efficiency / unit size",
            (FLOOR, EFFICIENCY, UNIT),
        ),
        "feasible": (
            "homes in buildings where conversion pencils",
            "units",
            "potential x share of the tract's cash renters paying at least the break-even "
            "rent / new-unit rent ratio (tod.new_unit_rent_premium, a multiple of today's "
            "rent); break-even rent = (acquisition + conversion "
            "cost per gross sq ft x gross sq ft per home + fees x fee share) x (1 + margin)"
            " x cap rate / (12 x (1 - operating share))",
            (CONV_COST, ACQ, FEE, FEE_SHARE, MARGIN, CAPRATE, OPEX, NEW_PREMIUM),
        ),
        "started": (
            "conversions permitted 2027-2031 before the absorption cap",
            "units",
            "feasible x min(1, yearly share x sum of ramp)",
            (R_HAZARD, FIRST, FULL, DELAY),
        ),
        "conversions": (
            "homes created by conversion 2027-2031 (outside the BPS measure)",
            "units",
            "started x min(1, reuse.absorption_cap_share x county baseline 5+ permits "
            "/ county started)",
            (R_CAP,),
        ),
    }
    records: dict[str, Record] = {}
    acc: list[str] = []
    for stage, (name, unit, expr, ids) in texts.items():
        acc += list(ids)
        records[stage] = rec.from_draws(
            f"{name} (adaptive reuse)",
            stages[stage].sum(axis=1),
            unit,
            expression=f"the sum over tracts of {expr}",
            assumption_ids=sorted(set(acc)),
            source_ids=_sources(
                parcel_src, any_measured, CAP_SOURCES if stage == "conversions" else ()
            ),
            tier=0,
        )
    records["conversion_homes"] = rec.from_draws(
        "homes created by converting offices and shops 2027-2031, reported OUTSIDE the "
        "Building Permits Survey total (a conversion is an alteration, not new construction)",
        conversions.sum(axis=1),
        "units",
        expression=(
            "the sum over tracts of conversions (route 2); never added to added units authorized"
        ),
        assumption_ids=records["conversions"].assumption_ids,
        source_ids=records["conversions"].source_ids,
        tier=0,
    )
    records["measured_tracts"] = rec.quantity(
        "tracts whose commercial buildings are counted from parcel land use",
        float(measured.sum()),
        "tracts",
        formula=(
            f"{int(measured.sum()):,} of {n:,} tracts lie in counties with a parcel land-use "
            "table (parcels_area); the rest are 'eligible land not measured'"
        ),
        source_ids=_sources(parcel_src, any_measured) if any_measured else (),
        tier=0,
    )
    return LandResult(
        geoids=tracts["geoid"].astype(str).to_numpy(),
        counties=counties,
        stages=stages,
        headline="conversions",
        records=records,
        steps=tuple(records[s] for s in texts),
        year_share=year_share,
        measured_counties=tuple(sorted(set(counties[measured]))),
        bps_comparable=False,
    )
