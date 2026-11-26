"""Packages: turning on several reforms at once without double counting.

Plain-language summary
----------------------
A package is not simply the sum of its reforms. The engine builds it in four
steps, the same four the Compare screen shows as a waterfall:

1. **Sum of individual effects.** Each reform run alone, added draw by draw.
2. **Overlap removed.** A lot can be used once. ADUs and missing-middle
   buildings compete for the same single-family lots. When both are on, the
   lots that the ADU reform (run alone) builds on are handed to the
   missing-middle funnel, which takes a share of them out of its own pool
   (``missing_middle.adu_lot_overlap_share``, a ledger parameter). Missing
   middle is rerun with the same seed; the overlap is the missing-middle units
   lost, draw by draw (:func:`lot_overlap`). ADUs keep their lots: an ADU is
   cheaper and faster than a rebuild, so it usually comes first.
3. **Interaction added.** Savings from one reform feed another reform's pro
   forma. When the fee cut is on, the ADU pro forma's own fee lines (local
   permit fees and utility connection fees) and the 2-6 unit pro forma's
   fee line (``fees.fee_total_per_unit``) are cut by the same share
   (``fees.fee_cut_share``). When the parking reform is on, the ADU's required
   parking spaces and the 2-6 unit building's spaces per unit are multiplied
   by the share of spaces still built outside transit-rich tracts
   (``parking.share_still_built_elsewhere``; most such lots are outside them).
   The reform is then run again with those changes (missing middle with the
   ADU lots already removed), with the same seed, and the interaction is the
   difference, draw by draw. The benchmark ADU needs no added parking, so the
   parking -> ADU interaction is zero under the benchmark.
4. **Combined** = sum - overlap + interaction.

So the package is never more than the sum of the parts plus the stated
interactions, which the tests check draw by draw.

What the package leaves out: the fee cut and parking reform act on baseline
production through the same cost formula and are simply added (each is a small
share of the value of a home, so the cross term is negligible and is not
modelled).

Transit-oriented density (a separate rule at the end of this file, switched by
the flag ``TOD_WINS_OVER_MIDDLE``): lots inside transit areas count under the
denser reform, TOD, so missing-middle units there are removed
(:func:`tod_package`). TOD and the parking reform never count the same unit:
the TOD pro forma already has reduced parking, and the parking reform acts only
on baseline production.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.engine import records as rec
from visionhousing.engine.adapters import OPTION_FEE_CUT, OPTION_PARKING_KEPT
from visionhousing.engine.records import Record
from visionhousing.engine.registry import get_reform
from visionhousing.ledger import Ledger

__all__ = [
    "FEE_CUT_ID",
    "LOT_FAMILIES",
    "PARKING_KEPT_ID",
    "TOD_WINS_OVER_MIDDLE",
    "TodPackage",
    "interaction_options",
    "lot_overlap",
    "overlap_pair",
    "tod_middle_overlap",
    "tod_package",
]

FEE_CUT_ID = "fees.fee_cut_share"
"""Share of every fee category the fee reform cuts."""

PARKING_KEPT_ID = "parking.share_still_built_elsewhere"
"""Share of formerly required spaces still built outside transit-rich tracts."""

LOT_FAMILIES = ("adu", "missing_middle")
"""Reform families that compete for the same single-family lots."""


def interaction_options(
    key: str, active: Sequence[str], ledger: Ledger
) -> dict[str, dict[str, Any]]:
    """Return the package changes one reform needs, by interaction name.

    The ADU and missing-middle families have interactions: the fee cut and the
    parking reform feed their pro formas.

    Args:
        key: The reform being adjusted.
        active: Every reform key in the package.
        ledger: The run's ledger (after the user's overrides).

    Returns:
        Interaction name (for example ``"fees -> adu"``) to the adapter options
        that switch it on, in the order they are applied. Empty when none apply.
    """
    if get_reform(key).family not in LOT_FAMILIES:
        return {}
    families = {get_reform(k).family for k in active if k != key}
    out: dict[str, dict[str, Any]] = {}
    if "fees" in families:
        out[f"fees -> {key}"] = {OPTION_FEE_CUT: float(ledger[FEE_CUT_ID].central)}
    if "parking" in families:
        out[f"parking -> {key}"] = {OPTION_PARKING_KEPT: float(ledger[PARKING_KEPT_ID].central)}
    return out


def overlap_pair(keys: Sequence[str]) -> tuple[str, str] | None:
    """Return ``(ADU key, missing-middle key)`` when both compete in the package.

    Args:
        keys: Reform keys in the package.

    Returns:
        The pair, or ``None`` when the package does not hold both.
    """
    by_family = {get_reform(k).family: k for k in keys}
    if all(f in by_family for f in LOT_FAMILIES):
        return by_family["adu"], by_family["missing_middle"]
    return None


def lot_overlap(alone: np.ndarray, with_adu_lots: np.ndarray) -> np.ndarray:
    """Missing-middle units lost because ADUs used some of the same lots.

    Args:
        alone: Missing-middle added units run alone, ``(counties, draws)``.
        with_adu_lots: The same run with the ADU lots taken out of its pool.

    Returns:
        Units to remove, ``(counties, draws)``.

    Raises:
        ValueError: Taking lots out raised units somewhere (it never should).
    """
    out = np.asarray(alone, dtype=float) - np.asarray(with_adu_lots, dtype=float)
    if np.any(out < -1e-9):
        raise ValueError("removing lots used by ADUs cannot add missing-middle units")
    return np.clip(out, 0.0, None)


# ================================================================= TOD over missing middle
#
# A separate rule, switched by a flag. It is not called by ``engine.core``; the
# package waterfall above is unchanged. Call :func:`tod_package` on a result
# (from ``engine.run``) whose package holds both TOD and missing middle.
#
# Rule: a single-family lot inside a transit area counts under the denser
# reform, TOD. Missing middle's net units in each tract are split by the share
# of the tract's homes within the TOD distance (``in_area_units_share``, 2020
# blocks); the in-area part is removed from the package. When ADUs are in the
# package too, missing middle has already lost lots to ADUs; the removal applies
# to what is left (state share, per draw), so a lot is never removed twice.
#
# Parking: the TOD pro forma already builds parking at the transit-rich
# still-built share, and the parking reform acts only on baseline production, so TOD and
# parking never count the same unit; no parking -> TOD interaction is added.

TOD_FAMILY = "tod"
"""Registry family of the transit-oriented density reform."""

TOD_WINS_OVER_MIDDLE = True
"""Flag: when on, missing-middle lots inside transit areas count under TOD only."""


def tod_middle_overlap(middle: Any, tod: Any) -> np.ndarray:
    """Missing-middle units, by draw, on lots inside the TOD transit area.

    Args:
        middle: The missing-middle ``ReformResult`` (run alone; tract level, its
            ``detail.stages["authorized"]``, its headline, is draws x tracts).
        tod: The TOD ``ReformResult`` (its ``detail.in_area_units_share`` is the
            share of each tract's homes within the TOD distance).

    Returns:
        Units by draw, shape ``(draws,)``, never negative and never above
        missing middle's own total in that draw.
    """
    net = np.asarray(middle.detail.stages["authorized"], dtype=float)  # draws x tracts
    share = pd.Series(
        np.asarray(tod.detail.in_area_units_share, dtype=float),
        index=[str(g) for g in tod.detail.geoids],
    )
    geoids = [str(g) for g in middle.detail.geoids]
    s = share.reindex(geoids).fillna(0.0).clip(0.0, 1.0).to_numpy()
    return np.clip((net * s[None, :]).sum(axis=1), 0.0, None)


@dataclass(frozen=True)
class TodPackage:
    """The package after the TOD-over-missing-middle rule.

    Attributes:
        applied: Whether the rule changed anything (flag on and both reforms in).
        overlap_draws: Units removed, by draw.
        state_draws: Combined added units after the rule, by draw.
        overlap: Record of the units removed.
        combined: Record of the combined added units after the rule.
    """

    applied: bool
    overlap_draws: np.ndarray
    state_draws: np.ndarray
    overlap: Record
    combined: Record


def tod_package(result: Any, *, tod_wins: bool | None = None) -> TodPackage:
    """Apply the TOD-over-missing-middle rule to an engine result's package.

    Args:
        result: An ``engine.core.Result`` with a package.
        tod_wins: The flag; ``None`` uses :data:`TOD_WINS_OVER_MIDDLE`.

    Returns:
        The adjusted package. With the flag off, or without both reforms, the
        removal is 0 and the combined draws equal the package's own.
    """
    flag = TOD_WINS_OVER_MIDDLE if tod_wins is None else bool(tod_wins)
    pkg = result.package
    by_family = {get_reform(k).family: k for k in result.reform_ids}
    base = np.asarray(pkg.state_draws, dtype=float)
    removed = np.zeros_like(base)
    ids = set(pkg.combined.assumption_ids)
    applied = False
    if flag and TOD_FAMILY in by_family and "missing_middle" in by_family:
        middle = result.reforms[by_family["missing_middle"]]
        tod = result.reforms[by_family[TOD_FAMILY]]
        in_area = tod_middle_overlap(middle.result, tod.result)
        alone = np.asarray(middle.state_draws, dtype=float)
        left = np.clip(alone - np.asarray(pkg.overlap_draws, dtype=float), 0.0, None)
        with np.errstate(divide="ignore", invalid="ignore"):
            frac = np.where(alone > 0, in_area / alone, 0.0)
        removed = np.clip(frac, 0.0, 1.0) * left
        ids |= set(middle.assumption_ids) | set(tod.assumption_ids)
        applied = True
    combined = base - removed
    span = "2027-2031"
    common = {"assumption_ids": sorted(ids), "source_ids": pkg.combined.source_ids, "tier": 0}
    overlap = rec.from_draws(
        f"missing-middle units inside transit areas, counted under TOD {span}, state",
        removed,
        "units",
        expression=(
            "missing-middle net units x share of the tract's homes within the TOD distance, "
            "summed over tracts, applied to what missing middle keeps after ADU lots"
        ),
        **common,
    )
    total = rec.from_draws(
        f"combined added units with TOD over missing middle {span}, state",
        combined,
        "units",
        expression="package combined added units - missing-middle units inside transit areas",
        **common,
    )
    return TodPackage(applied, removed, combined, overlap, total)
