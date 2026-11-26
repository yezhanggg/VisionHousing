"""Extra scenario: starter-home lots (smaller minimum lots on single-family land).

Plain-language summary
----------------------
Many single-family zones require large lots (often 10,000 sq ft or more), so a
modest house on a small lot cannot be built. This scenario lowers the minimum
lot for a new subdivision to ``starter.min_lot_sqft`` (3,000-5,000 sq ft; Texas
SB 15 of 2025 caps new lots in large cities at 3,000 sq ft) and asks how many
more homes would be permitted in 2027-2031. Two kinds of project, in a mix:

- **split and keep**: the owner splits the lot, keeps the house, and a new
  small house is built on the new lot (one more home, none removed);
- **split and rebuild**: a builder buys the house, tears it down and builds
  ``starter.rebuild_homes_per_lot`` small homes (a pair or a row of three).

Funnel per tract and draw::

    eligible lots = 1-unit detached homes x adu.lot_proxy_adjustment
                    x missing_middle.sf_only_zoned_share x (1 - share with no public sewer)
    newly splittable = share of lots >= 2 x new minimum - share >= 2 x today's minimum
                       (block land per home, missing_middle.lot_proxy_net_share)
    feasible = keep share x p_keep + (1 - keep share) x p_rebuild
    split = eligible x newly splittable x feasible x min(1, yearly share x sum of ramp)
            (rebuilds also capped by the yearly sale rate)
    new homes = keep splits x 1 + rebuild splits x homes per rebuild
    added units authorized = new homes x (1 - baseline share) x (1 - displacement)
    existing homes replaced = rebuild splits x (1 - baseline share)
    net added homes = added units authorized - existing homes replaced

Only lots that today's minimum does *not* already let the owner split count
(the second term of "newly splittable"), so no split that is legal today is
credited to the reform.

**Does it pencil?** A starter home sells for the tract's median home value x
``starter.small_home_value_ratio`` x (1 + new-construction premium). Its residual
land value is that price less (floor area x hard cost x (1 + soft costs) + fees
+ ``starter.subdivision_cost_per_lot`` (survey, plat, engineering, water and
sewer laterals)) x (1 + margin). Keep: the owner sells half the lot when the
residual covers the land part of the home's value for half a lot
(``V x starter.land_share_of_value x 0.5 x (1 + acquisition premium)``). Rebuild:
``homes per rebuild x residual`` must cover the whole home ``V x (1 + premium)``.
``p`` is the share of the tract's owner home values (ACS brackets) that pass.

**Appraisal and lending flag.** Lenders need comparable sales. Where few of the
tract's homes are valued near the starter home's price (within 20%; share below
``starter.min_comparable_share``), the tract is flagged. Option ``appraisal``:
``"flag"`` (default) only reports the share of added homes in flagged tracts;
``"binding"`` (variant ``starter_home_lots_appraisal_binding``) counts none there.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.ledger.loader import Ledger
from visionhousing.ledger.sampling import sample
from visionhousing.scenarios.extra_common import ramp_years
from visionhousing.scenarios.missing_middle import LOT_COLUMNS, share_at_least
from visionhousing.scenarios.tod import _share_below

__all__ = ["APPRAISAL", "IDS", "StarterResult", "starter_lots_funnel"]

SQ_M_PER_SQ_FT = 0.09290304
NEW_MIN = "starter.min_lot_sqft"
CUR_MIN = "starter.current_min_lot_sqft"
FALLBACK = "starter.newly_splittable_share"
KEEP = "starter.keep_house_share"
PER_REBUILD = "starter.rebuild_homes_per_lot"
SIZE = "starter.home_size_sqft"
SUBDIV = "starter.subdivision_cost_per_lot"
LAND_SHARE = "starter.land_share_of_value"
VALUE_RATIO = "starter.small_home_value_ratio"
HAZARD = "starter.split_hazard_annual"
FIRST = "starter.ramp_first_year_share"
FULL = "starter.ramp_years_to_full"
DELAY = "starter.start_delay_years"
COMPARABLE = "starter.min_comparable_share"
DISPLACE = "starter.displacement_share"
BASELINE = "starter.baseline_split_share"
LOT_ADJ = "adu.lot_proxy_adjustment"
SF_ONLY = "missing_middle.sf_only_zoned_share"
NET_SHARE = "missing_middle.lot_proxy_net_share"
HARD = "missing_middle.hard_cost_per_sqft"
SOFT = "missing_middle.soft_cost_share"
MARGIN = "missing_middle.developer_margin"
PREMIUM = "missing_middle.acquisition_premium"
TURNOVER = "missing_middle.sf_turnover_annual"
NEW_PREMIUM = "supply.new_construction_value_premium"
FEE = "fees.fee_total_per_unit"
YEARS = ("baseline.first_year", "baseline.horizon_years")
IDS = (
    NEW_MIN, CUR_MIN, FALLBACK, KEEP, PER_REBUILD, SIZE, SUBDIV, LAND_SHARE, VALUE_RATIO,
    HAZARD, FIRST, FULL, DELAY, COMPARABLE, DISPLACE, BASELINE, LOT_ADJ, SF_ONLY, NET_SHARE,
    HARD, SOFT, MARGIN, PREMIUM, TURNOVER, NEW_PREMIUM, FEE,
)  # fmt: skip
"""Ledger ids behind the funnel."""

APPRAISAL = ("flag", "binding")
DATA_SOURCES = ("acs5_summary_file", "tiger_2020_tabblock")

STAGES = (
    "eligible", "splittable", "feasible", "split", "new_homes", "authorized", "replaced", "net",
)  # fmt: skip


@dataclass(frozen=True)
class StarterResult:
    """What :func:`starter_lots_funnel` returns (draws x tracts arrays in ``stages``)."""

    geoids: np.ndarray
    counties: np.ndarray
    stages: dict[str, np.ndarray]
    records: dict[str, Record]
    steps: tuple[Record, ...]
    year_share: np.ndarray
    fit_source: str
    flagged: np.ndarray
    appraisal: str


def _col(tracts: pd.DataFrame, name: str) -> np.ndarray:
    if name not in tracts:
        return np.full(len(tracts), np.nan)
    return pd.to_numeric(tracts[name], errors="coerce").to_numpy(dtype=float)


def starter_lots_funnel(
    tracts: pd.DataFrame,
    ledger: Ledger,
    *,
    outside_share: np.ndarray | None = None,
    sewer_sources: Sequence[str] = (),
    appraisal: str = "flag",
    draws: int = 2000,
    seed: int = 0,
) -> StarterResult:
    """Run the starter-lot funnel for every tract and draw.

    Args:
        tracts: Tract table (``units_1_detached``, ``median_home_value``, the value
            brackets; optional block lot-size percentiles ``lot_m2_per_unit_p25/50/75``).
        ledger: The ledger.
        outside_share: Share of each tract's homes outside every mapped public sewer
            service area (``infra_tract``); those lots are left out (on-lot septic
            systems need large lots). ``None``: no table, nothing left out.
        sewer_sources: Source ids behind that table, read from the pack
            (``extra_common.table_sources``); cited only when ``outside_share`` is given.
        appraisal: ``flag`` or ``binding``.
        draws: Monte Carlo draws.
        seed: Seed.

    Returns:
        A :class:`StarterResult`.
    """
    if appraisal not in APPRAISAL:
        raise ValueError(f"appraisal must be one of {APPRAISAL}, got {appraisal!r}")
    for pid in (*IDS, *YEARS):
        ledger[pid]
    v = sample(ledger, n=draws, seed=seed, ids=list(IDS))
    col = {k: a[:, None] for k, a in v.items()}
    first = int(ledger[YEARS[0]].central)
    years = tuple(range(first, first + int(ledger[YEARS[1]].central)))
    n = len(tracts)

    # 1. Eligible lots.
    detached = np.clip(np.nan_to_num(_col(tracts, "units_1_detached")), 0, None)[None, :]
    sewer_ok = 1.0 if outside_share is None else (1.0 - np.clip(outside_share, 0, 1))[None, :]
    eligible = np.broadcast_to(detached * col[LOT_ADJ] * col[SF_ONLY] * sewer_ok, (draws, n))

    # 2. Newly splittable share.
    if all(c in tracts for c in LOT_COLUMNS):
        q = [_col(tracts, c)[None, :] for c in LOT_COLUMNS]

        def at_least(min_sqft: np.ndarray) -> np.ndarray:
            need = 2.0 * min_sqft * SQ_M_PER_SQ_FT / col[NET_SHARE]
            return share_at_least(*q, need)

        f = at_least(col[NEW_MIN]) - at_least(np.maximum(col[CUR_MIN], col[NEW_MIN]))
        f = np.where(np.isnan(f), col[FALLBACK], np.clip(f, 0.0, 1.0))
        fit_source = "lot_proxy"
    else:
        f = np.broadcast_to(col[FALLBACK], (draws, n)).copy()
        fit_source = "parameter"
    splittable = eligible * f

    # 3. Feasible share.
    median = _col(tracts, "median_home_value")[None, :]
    price = np.nan_to_num(median) * col[VALUE_RATIO] * (1.0 + col[NEW_PREMIUM])
    cost = col[SIZE] * col[HARD] * (1.0 + col[SOFT]) + col[FEE] + col[SUBDIV]
    rlv = price - cost * (1.0 + col[MARGIN])  # $ per new home
    keep_t = rlv / (col[LAND_SHARE] * 0.5 * (1.0 + col[PREMIUM]))
    rebuild_t = col[PER_REBUILD] * rlv / (1.0 + col[PREMIUM])
    p_keep = np.where(rlv > 0, _share_below(tracts, np.maximum(keep_t, 0.0), draws), 0.0)
    p_rebuild = np.where(rlv > 0, _share_below(tracts, np.maximum(rebuild_t, 0.0), draws), 0.0)
    comp = np.nan_to_num(
        _share_below(tracts, price * 1.2, draws) - _share_below(tracts, price * 0.8, draws)
    )
    flagged = comp < col[COMPARABLE]
    if appraisal == "binding":
        p_keep = np.where(flagged, 0.0, p_keep)
        p_rebuild = np.where(flagged, 0.0, p_rebuild)
    keep, rebuild = col[KEEP], 1.0 - col[KEEP]
    feasible_keep = splittable * keep * p_keep
    feasible_rebuild = splittable * rebuild * p_rebuild
    feasible = feasible_keep + feasible_rebuild

    # 4. Take-up.
    ramp, year_share = ramp_years(v, FIRST, FULL, DELAY, len(years))
    rsum = ramp.sum(axis=1)[:, None]
    keep_lots = feasible_keep * np.minimum(col[HAZARD] * rsum, 1.0)
    rebuild_lots = feasible_rebuild * np.minimum(np.minimum(col[HAZARD], col[TURNOVER]) * rsum, 1.0)
    split = keep_lots + rebuild_lots
    homes_new_lot = keep_lots
    homes_rebuild = rebuild_lots * col[PER_REBUILD]
    new_homes = homes_new_lot + homes_rebuild
    authorized = new_homes * (1.0 - col[BASELINE]) * (1.0 - col[DISPLACE])
    replaced = rebuild_lots * (1.0 - col[BASELINE])
    net = authorized - replaced
    stages = {
        "eligible": eligible,
        "splittable": splittable,
        "feasible": feasible,
        "split": split,
        "new_homes": new_homes,
        "authorized": authorized,
        "replaced": replaced,
        "net": net,
    }
    records = _records(
        ledger, stages, homes_new_lot, homes_rebuild, flagged, authorized, fit_source,
        outside_share is not None, appraisal, years, sewer_sources,
    )  # fmt: skip
    return StarterResult(
        geoids=tracts["geoid"].astype(str).to_numpy(),
        counties=tracts["county"].astype(str).to_numpy(),
        stages=stages,
        records=records,
        steps=tuple(records[s] for s in STAGES),
        year_share=year_share,
        fit_source=fit_source,
        flagged=flagged,
        appraisal=appraisal,
    )


def _records(
    ledger: Ledger,
    stages: dict[str, np.ndarray],
    homes_new_lot: np.ndarray,
    homes_rebuild: np.ndarray,
    flagged: np.ndarray,
    authorized: np.ndarray,
    fit_source: str,
    sewer: bool,
    appraisal: str,
    years: Sequence[int],
    sewer_sources: Sequence[str] = (),
) -> dict[str, Record]:
    period = f"{years[0]}-{years[-1]}"
    src = tuple(sorted({*DATA_SOURCES, *(sewer_sources if sewer else ())}))
    fit_ids = (NEW_MIN, CUR_MIN, NET_SHARE, FALLBACK) if fit_source == "lot_proxy" else (FALLBACK,)
    specs: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
        "eligible": (
            "single-family lots in single-family-only zones",
            "lots",
            "1-unit detached homes x lot proxy adjustment x single-family-only share"
            + (" x (1 - share of homes outside a mapped public sewer area)" if sewer else ""),
            (LOT_ADJ, SF_ONLY),
        ),
        "splittable": (
            "lots the smaller minimum newly lets the owner split",
            "lots",
            "eligible lots x (share of lots at least 2 x new minimum - share at least "
            "2 x today's minimum)"
            if fit_source == "lot_proxy"
            else "eligible lots x starter.newly_splittable_share (no lot-size proxy)",
            fit_ids,
        ),
        "feasible": (
            "lots where a starter home pencils",
            "lots",
            "splittable lots x (keep share x p_keep + (1 - keep share) x p_rebuild)"
            + (" (none in tracts with too few comparable sales)" if appraisal == "binding" else ""),
            (
                KEEP,
                PER_REBUILD,
                SIZE,
                SUBDIV,
                LAND_SHARE,
                VALUE_RATIO,
                HARD,
                SOFT,
                MARGIN,
                PREMIUM,
                NEW_PREMIUM,
                FEE,
                *((COMPARABLE,) if appraisal == "binding" else ()),
            ),
        ),
        "split": (
            f"lots split {period}",
            "lots",
            "feasible keep lots x min(1, yearly share x sum of ramp) + feasible rebuild lots "
            "x min(1, min(yearly share, yearly sale rate) x sum of ramp)",
            (HAZARD, FIRST, FULL, DELAY, TURNOVER),
        ),
        "new_homes": (
            f"new homes permitted on split lots {period}",
            "units",
            "keep splits x 1 + rebuild splits x homes per rebuild",
            (PER_REBUILD,),
        ),
        "authorized": (
            f"added units authorized {period} (starter-home lots)",
            "units",
            "new homes x (1 - starter.baseline_split_share) x (1 - starter.displacement_share)",
            (BASELINE, DISPLACE),
        ),
        "replaced": (
            f"existing homes replaced {period} (split and rebuild)",
            "units",
            "rebuild splits x (1 - starter.baseline_split_share)",
            (BASELINE,),
        ),
        "net": (
            f"net added homes after replacement {period} (starter-home lots)",
            "units",
            "added units authorized - existing homes replaced",
            (),
        ),
    }
    out: dict[str, Record] = {}
    acc: list[str] = []
    for stage, (name, unit, expr, ids) in specs.items():
        acc += list(ids)
        out[stage] = rec.from_draws(
            name, stages[stage].sum(axis=1), unit,
            expression=f"the sum over tracts of {expr}",
            assumption_ids=sorted(set(acc)), source_ids=src, tier=0,
        )  # fmt: skip
    ids_all = out["authorized"].assumption_ids
    out["homes_new_lot"] = rec.from_draws(
        f"homes built on a new lot beside a kept house {period} (detached starter homes)",
        homes_new_lot.sum(axis=1), "units",
        expression="the sum over tracts of keep splits x 1", assumption_ids=ids_all,
        source_ids=src, tier=0,
    )  # fmt: skip
    out["homes_rebuild"] = rec.from_draws(
        f"homes built where a house is replaced {period} (pairs or rows of small homes)",
        homes_rebuild.sum(axis=1), "units",
        expression="the sum over tracts of rebuild splits x starter.rebuild_homes_per_lot",
        assumption_ids=ids_all, source_ids=src, tier=0,
    )  # fmt: skip
    total = authorized.sum(axis=1)
    in_flag = (authorized * flagged).sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(total > 0, in_flag / np.where(total > 0, total, 1.0), 0.0)
    out["appraisal_flag_share"] = rec.from_draws(
        "share of added homes in tracts flagged for appraisal and lending risk "
        "(few comparable sales near the starter-home price)"
        + (" - binding: counted as zero" if appraisal == "binding" else ""),
        share, "share",
        expression=(
            "added units in tracts where the share of owner homes valued within 20% of the "
            "starter-home price < starter.min_comparable_share / all added units"
        ),
        assumption_ids=sorted({*ids_all, COMPARABLE}), source_ids=src, tier=0,
    )  # fmt: skip
    return out
