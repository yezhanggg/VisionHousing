"""Baseline rule contest, bias correction by place type, surge years and stress settings.

Plain-language summary
----------------------
The baseline carries recent permits forward with one simple rule. This module
decides which rule, and how its middle value and range are corrected, by
testing the options on the past (the rolling-origin back-test of
:mod:`visionhousing.baseline.backtest`).

1. **Rule contest.** Seven rules (five-year mean, three-year mean, median of
   five years, damped trend, shrink to place type, top-down share, and the
   simple average of the six) are back-tested on the same origins and the same
   county cases. Each is scored on its median signed error, median absolute
   error, P10-P90 coverage and mean range width. The choosing rule is written in
   ``assumptions/backtest.yaml`` (``contest``) and applied as written
   (:func:`choose_winner`).
2. **Bias correction and ranges by place type.** The baseline's P50 is the
   winner's projection times the median past ratio of actual to projected units,
   and its P10 / P90 use the 10th / 90th percentile ratios. Those ratios can be
   pooled by county size class or by place type. :func:`out_of_time_test` checks
   both honestly: for each past origin it uses only cases whose five target
   years had all been observed by then, so nothing from later years leaks in.
   The adoption rule in ``assumptions/backtest.yaml`` (``calibration``) picks
   the pools the engine uses (:func:`choose_pools`).
3. **Own-error widening.** A county whose own back-test is far off (for
   Pennsylvania, Philadelphia) gets each end of its range from the wider of its
   pool's ratios and its own.
4. **Surge years.** A county-year far above the county's other years in the
   same five-year window is named, and the back-test error is shown with and
   without the cases whose target years hold one (:func:`surge_check`).
5. **Stress settings.** Back-test origins are split into "slump" and "boom" by
   whether the state built less or more in the five target years than in the
   five years before. The baseline is re-run using only one kind's errors, as
   an alternative range (:func:`stress_baselines`).

Everything but :func:`place_info_for_context` is pure: tables in, tables out.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.baseline.backtest import (
    backtest_cases,
    backtest_origins,
    class_label,
    lead_years,
    origin_size_classes,
    ratio_quantiles,
    size_classes,
    validate_baseline,
)
from visionhousing.baseline.panel import Panel, to_panel
from visionhousing.baseline.rules import PlaceInfo, Rule, rules_from_ledger
from visionhousing.baseline.settings import INTERVAL, BaselineSettings, settings_from_ledger
from visionhousing.ledger import Ledger

__all__ = [
    "Contest",
    "bias_correction_decision",
    "choose_pools",
    "choose_winner",
    "contest_report",
    "contest_rules",
    "default_place_info",
    "origin_kinds",
    "out_of_time_test",
    "place_info",
    "place_info_for_context",
    "pool_quantiles",
    "run_contest",
    "surge_check",
]

METHOD_LABELS: dict[str, str] = {
    "none": "no correction (the rule's own projection)",
    "size_class": "corrected and ranged by county size class",
    "place_type": "corrected and ranged by place type (all counties pooled when a type is small)",
    "place_type_own": "place type, with own-error widening for far-off counties",
}
"""Names of the correction methods compared in the out-of-time test (labels)."""


# --------------------------------------------------------------------------- rules


def contest_rules(ledger: Ledger, rules: Mapping[str, Any]) -> tuple[Rule, ...]:
    """Build the contest's rules, in the order ``contest.rules`` lists them.

    Settings come from the ledger (five-year window, ten-year window, damping)
    and from ``contest`` in ``assumptions/backtest.yaml`` (three-year window,
    median window, shrink half weight, share window, complexity ranks).

    Raises:
        ValueError: ``contest.rules`` names a rule this module does not know.
    """
    c = rules["contest"]
    rank = {str(k): int(v) for k, v in c["complexity"].items()}
    recent = round(ledger["baseline.recent_window_years"].central)
    long = round(ledger["baseline.long_window_years"].central)
    damping = ledger["baseline.trend_damping"].central
    known: dict[str, Rule] = {
        "last_5_mean": Rule(
            f"last_{recent}_mean",
            "mean",
            rank.get("last_5_mean", 1),
            window=recent,
            assumption_ids=("baseline.recent_window_years",),
        ),
        "last_3_mean": Rule(
            "last_3_mean",
            "mean",
            rank.get("last_3_mean", 1),
            window=int(c["short_window_years"]),
            assumption_ids=("baseline.recent_window_years",),
        ),
        "median_5": Rule(
            "median_5",
            "median",
            rank.get("median_5", 1),
            window=int(c["median_window_years"]),
            assumption_ids=("baseline.recent_window_years",),
        ),
        "damped_trend": Rule(
            "damped_trend",
            "damped_trend",
            rank.get("damped_trend", 3),
            window=long,
            damping=damping,
            assumption_ids=("baseline.long_window_years", "baseline.trend_damping"),
        ),
        "shrink_to_group": Rule(
            "shrink_to_group",
            "shrink",
            rank.get("shrink_to_group", 3),
            window=recent,
            half_weight=float(c["half_weight_units"]),
            assumption_ids=("baseline.recent_window_years",),
        ),
        "top_down_share": Rule(
            "top_down_share",
            "top_down",
            rank.get("top_down_share", 2),
            window=recent,
            share_window=int(c["share_window_years"]),
            assumption_ids=("baseline.recent_window_years", "baseline.long_window_years"),
        ),
    }
    out: list[Rule] = []
    for name in c["rules"]:
        name = str(name)
        if name == "average":
            parts = tuple(r for r in out if r.method != "average")
            ids = tuple(sorted({a for r in parts for a in r.assumption_ids}))
            out.append(
                Rule(
                    "average",
                    "average",
                    rank.get("average", 4),
                    components=parts,
                    assumption_ids=ids,
                )
            )
        elif name in known:
            out.append(known[name])
        else:
            raise ValueError(f"unknown contest rule {name!r} in backtest.yaml")
    return tuple(out)


def all_rules(ledger: Ledger, rules: Mapping[str, Any]) -> tuple[Rule, ...]:
    """Every rule a baseline may be run with: the contest's and the earlier four."""
    out = {r.name: r for r in rules_from_ledger(ledger)}
    out.update({r.name: r for r in contest_rules(ledger, rules)})
    return tuple(out.values())


# --------------------------------------------------------------------------- choosing


def choose_winner(skill: pd.DataFrame, band: Sequence[float], tolerance: float) -> tuple[str, str]:
    """Apply the choosing rule of ``assumptions/backtest.yaml`` (``contest``).

    1. Eligible: ``coverage_80`` inside ``band`` (edges count).
    2. Best: the lowest ``median_abs_log_ratio`` among eligible rules.
    3. Winner: the simplest eligible rule within ``best x (1 + tolerance)``;
       ties go to the lower error, then to the earlier row.
    4. No eligible rule: the rule with coverage closest to the P10-P90 aim
       (80%); ties go to the lower error, then to the earlier row.

    Args:
        skill: Output of ``validate_baseline`` (must have ``complexity``).
        band: ``(low, high)`` coverage band.
        tolerance: ``baseline.selection_tolerance``.

    Returns:
        ``(winner, reason)``: the rule name and the step that chose it
        (``"in band"`` or ``"closest coverage"``).

    Raises:
        ValueError: No rule has a finite error.
    """
    metric = "median_abs_log_ratio"
    scored = skill[np.isfinite(skill[metric])].assign(
        _row=range(int(np.isfinite(skill[metric]).sum()))
    )
    if scored.empty:
        raise ValueError("no rule has a finite score; the backtest had no usable cases")
    low, high = float(band[0]), float(band[1])
    eligible = scored[(scored["coverage_80"] >= low) & (scored["coverage_80"] <= high)]
    if not eligible.empty:
        best = eligible[metric].min()
        ok = eligible[eligible[metric] <= best * (1 + tolerance)]
        ok = ok.sort_values(["complexity", metric, "_row"])
        return str(ok.iloc[0]["rule"]), "in band"
    aim = INTERVAL[1] - INTERVAL[0]
    gap = (scored["coverage_80"] - aim).abs().fillna(np.inf)
    pick = scored.assign(_gap=gap).sort_values(["_gap", metric, "_row"])
    return str(pick.iloc[0]["rule"]), "closest coverage"


def choosing_rule_text(band: Sequence[float], tolerance: float) -> str:
    """The choosing rule in words (from the ledger values; for the Method screen)."""
    return (
        f"Rules whose P10-P90 ranges caught the actual total in {_pct(band[0])} to "
        f"{_pct(band[1])} of back-test cases are eligible. Among them the lowest median "
        f"absolute error is the best; the simplest eligible rule within {_pct(tolerance)} of "
        "that error wins. If no rule is eligible, the rule whose coverage is closest to 80% "
        "wins. This rule was fixed in assumptions/backtest.yaml before any score was computed."
    )


# --------------------------------------------------------------------------- place info


def place_info(
    counties: Sequence[str],
    classes: np.ndarray,
    n_classes: int,
    order: Sequence[str],
    *,
    typology: pd.DataFrame | None = None,
    tracts: pd.DataFrame | None = None,
) -> PlaceInfo:
    """Give each county a place type and its homes.

    The place type is the first grouping in ``order`` (``place_type`` in
    ``assumptions/backtest.yaml``) that covers every county: ``typology``
    (pack table ``typology_county``: ``geoid``, ``type_name``) or
    ``size_class`` (always available). Homes are the tract ``housing_units``
    summed by county (``None`` when the tracts lack them).
    """
    ids = [str(c) for c in counties]
    groups: tuple[str, ...] | None = None
    source = "size_class"
    for key in order:
        if (
            key == "typology"
            and typology is not None
            and {"geoid", "type_name"} <= set(typology.columns)
        ):
            names = dict(
                zip(typology["geoid"].astype(str), typology["type_name"].astype(str), strict=False)
            )
            if all(c in names for c in ids):
                groups, source = tuple(names[c] for c in ids), "typology"
                break
        if key == "size_class":
            groups = tuple(class_label(int(k), n_classes) for k in classes)
            source = "size_class"
            break
    if groups is None:
        groups = tuple(class_label(int(k), n_classes) for k in classes)
    homes = None
    if tracts is not None and {"county", "housing_units"} <= set(tracts.columns):
        h = pd.to_numeric(tracts["housing_units"], errors="coerce")
        by = h.groupby(tracts["county"].astype(str)).sum(min_count=1)
        homes = np.array([float(by.get(c, np.nan)) for c in ids])
    return PlaceInfo(groups=groups, source=source, homes=homes)


def default_place_info(panel: Panel, settings: BaselineSettings) -> PlaceInfo:
    """Place info from the permit table alone: size classes as place types, no homes."""
    classes = size_classes(panel, settings.recent_window, settings.n_classes)
    return place_info(panel.counties, classes, settings.n_classes, ["size_class"])


_INFO: dict[tuple[str, ...], PlaceInfo] = {}


def place_info_for_context(ctx: Any) -> PlaceInfo:
    """Place info for a loaded state (reads ``typology_county`` from its pack once)."""
    from visionhousing.baseline.places import (
        _typology_on_the_fly,
        load_place_rules,
        read_pack_table,
    )
    from visionhousing.engine.context import pack_dir

    key = (ctx.state, ctx.data_hash, ctx.ledger.hash())
    if key in _INFO:
        return _INFO[key]
    settings = settings_from_ledger(ctx.ledger)
    panel = to_panel(ctx.permits)
    classes = size_classes(panel, settings.recent_window, settings.n_classes)
    held = getattr(ctx, "derived", {}).get(("pack_folder",))  # the folder it was read from
    folder = None if ctx.synthetic else (Path(held) if held is not None else pack_dir(ctx.state))
    folder = folder if folder is not None and folder.is_dir() else None
    typology = read_pack_table(folder, "typology_county")
    if typology is None:
        counties = read_pack_table(folder, "counties")
        if counties is not None:
            typology = _typology_on_the_fly(ctx, counties)
    order = load_place_rules().get("place_type", ["size_class"])
    info = place_info(
        panel.counties, classes, settings.n_classes, order, typology=typology, tracts=ctx.tracts
    )
    _INFO[key] = info
    return info


# --------------------------------------------------------------------------- pooling


def pool_quantiles(
    ratios: pd.DataFrame,
    county: int,
    keys: np.ndarray,
    quantiles: Sequence[float],
    min_group: int | None,
    *,
    row_keys: np.ndarray | None = None,
    label: Any = None,
) -> tuple[np.ndarray | None, str]:
    """Ratio quantiles for one county from its pool of past cases.

    Args:
        ratios: Past cases (``place``, ``ratio``), finite ratios only.
        county: The county's row index.
        keys: Pool label of each county (size class or place type).
        quantiles: Quantile levels.
        min_group: Fewest cases a pool needs, else all cases are pooled;
            ``None``: any case will do (the size-class method).
        row_keys: Pool label of each row of ``ratios`` (the label at the
            case's own origin), with ``label`` the county's label now; ``None``
            reads both from ``keys``.
        label: See ``row_keys``.

    Returns:
        ``(quantiles or None, note)``; the note says which pool was used.
    """
    if ratios.empty:
        return None, "no usable back-test ratios"
    if row_keys is None:
        mine = keys[ratios["place"].to_numpy(dtype=int)] == keys[county]
        name = keys[county]
    else:
        mine, name = np.asarray(row_keys) == label, label
    own = ratios.loc[mine, "ratio"].to_numpy()
    need = 1 if min_group is None else int(min_group)
    if own.size >= need:
        return np.quantile(own, list(quantiles)), f"{own.size} past cases of {name}"
    allr = ratios["ratio"].to_numpy()
    return (
        np.quantile(allr, list(quantiles)),
        f"{name} has {own.size} past cases (fewer than {need}), so all {allr.size} cases pooled",
    )


def own_widening(
    ratios: pd.DataFrame,
    county: int,
    q: np.ndarray,
    quantiles: Sequence[float],
    min_own: int,
    beyond: float,
) -> tuple[np.ndarray, str]:
    """Widen a far-off county's range ends from its own past ratios (see module docstring).

    Returns:
        ``(quantiles, note)``; the note is empty when nothing changed.
    """
    own = ratios.loc[ratios["place"].to_numpy(dtype=int) == county, "ratio"].to_numpy()
    own = own[own > 0]
    if own.size < min_own:
        return q, ""
    typical = math.expm1(-float(np.median(np.log(own))))
    if abs(typical) < beyond:
        return q, ""
    mine = np.quantile(own, list(quantiles))
    out = q.copy()
    out[0], out[-1] = min(q[0], mine[0]), max(q[-1], mine[-1])
    if out[0] == q[0] and out[-1] == q[-1]:
        return q, ""
    way = "high" if typical > 0 else "low"
    return out, (
        f"its own {own.size} back-test cases ran {_pct(typical)} {way}, so each end of its "
        "range is the wider of its pool's ratio and its own"
    )


# --------------------------------------------------------------------------- out-of-time test


def out_of_time_test(
    cases: pd.DataFrame,
    keys: Mapping[str, np.ndarray],
    lead: int,
    horizon: int,
    min_group: int,
    min_own: int,
    beyond: float,
) -> pd.DataFrame:
    """Score the correction methods on each origin using earlier cases only.

    For an origin ``o`` the pools hold only cases whose origin ``o'`` satisfies
    ``o' + lead + horizon - 1 <= o``: their five target years had all been
    observed when ``o`` was the last data year. Origins with fewer than
    ``min_group`` such cases are skipped.

    Args:
        cases: ``backtest_cases`` output for counties (one rule).
        keys: ``size_class`` and ``place_type`` label of each county; either
            may instead map each origin to the labels as they stood then (the
            size classes, worked out from years up to the origin only).
        lead: Years from origin to the first target year.
        horizon: Target years.
        min_group: ``calibration.min_group_cases``.
        min_own: ``calibration.min_own_cases``.
        beyond: ``name_few_cases_beyond``.

    Returns:
        The scored cases (actual and projection above zero) of the origins
        tested, with ``p50_<m>``, ``low_<m>``, ``high_<m>`` for each method
        ``none``, ``size_class``, ``place_type``, ``place_type_own`` (``none``
        has no range).
    """
    finite = cases[np.isfinite(cases["ratio"])]
    q3 = (INTERVAL[0], 0.5, INTERVAL[1])
    out = []
    for o in sorted(finite["origin"].unique()):
        earlier = finite[finite["origin"] <= o - lead - horizon + 1]
        if len(earlier) < min_group:
            continue
        now = finite[(finite["origin"] == o) & (finite["actual"] > 0)].copy()
        if now.empty:
            continue
        cols: dict[str, list[float]] = {}
        # Labels now, and each earlier case's label at its own origin (a county
        # judged by the size class it had when its case was made).
        now_k, row_k = {}, {}
        e_place = earlier["place"].to_numpy(dtype=int)
        e_origin = earlier["origin"].to_numpy(dtype=int)
        for name in ("size_class", "place_type"):
            k = keys[name]
            if isinstance(k, Mapping):
                now_k[name] = k[int(o)]
                row_k[name] = np.array(
                    [k[int(oo)][pp] for pp, oo in zip(e_place, e_origin, strict=True)],
                    dtype=object,
                )
            else:
                now_k[name], row_k[name] = k, None
        for _, case in now.iterrows():
            c = int(case["place"])
            proj = float(case["projected"])
            qs, qp = (
                pool_quantiles(
                    earlier, c, now_k[name], q3, need,
                    row_keys=row_k[name],
                    label=None if row_k[name] is None else now_k[name][c],
                )[0]
                for name, need in (("size_class", None), ("place_type", min_group))
            )  # fmt: skip
            qo = own_widening(earlier, c, qp, q3, min_own, beyond)[0] if qp is not None else None
            for m, q in (
                ("none", None),
                ("size_class", qs),
                ("place_type", qp),
                ("place_type_own", qo),
            ):
                if m == "none":
                    trio = (math.nan, proj, math.nan)
                elif q is None:
                    trio = (math.nan, math.nan, math.nan)
                else:
                    trio = (proj * q[0], proj * q[1], proj * q[2])
                cols.setdefault(f"low_{m}", []).append(trio[0])
                cols.setdefault(f"p50_{m}", []).append(trio[1])
                cols.setdefault(f"high_{m}", []).append(trio[2])
        for k, v in cols.items():
            now[k] = v
        out.append(now)
    if not out:
        return pd.DataFrame(columns=[*cases.columns])
    return pd.concat(out, ignore_index=True)


def method_stats(tested: pd.DataFrame, method: str) -> dict[str, Any]:
    """Error and coverage of one method on a set of out-of-time cases."""
    if f"p50_{method}" not in tested.columns:
        tested = tested.assign(**{f"{p}_{method}": math.nan for p in ("low", "p50", "high")})
    p50 = tested[f"p50_{method}"].to_numpy(dtype=float)
    actual = tested["actual"].to_numpy(dtype=float)
    ok = np.isfinite(p50) & (p50 > 0) & (actual > 0)
    err = np.log(p50[ok] / actual[ok])
    low = tested[f"low_{method}"].to_numpy(dtype=float)
    high = tested[f"high_{method}"].to_numpy(dtype=float)
    has = np.isfinite(low) & np.isfinite(high)
    inside = (actual >= low) & (actual <= high)
    med = float(np.median(err)) if err.size else math.nan
    return {
        "method": method,
        "cases": int(err.size),
        "median_signed": med,
        "typical_pct": math.expm1(med) if err.size else math.nan,
        "median_abs": float(np.median(np.abs(err))) if err.size else math.nan,
        "interval_cases": int(has.sum()),
        "covered_cases": int(inside[has].sum()),
        "coverage": float(inside[has].mean()) if has.any() else math.nan,
    }


def choose_pools(
    overall: Mapping[str, Mapping[str, Any]], band: Sequence[float]
) -> tuple[str, str]:
    """Apply the adoption rule of ``calibration`` in ``assumptions/backtest.yaml``.

    ``place_type`` is adopted if its out-of-time median absolute error is below
    ``size_class``'s and its coverage is inside ``band``; when neither
    coverage is inside the band, "inside" is replaced by "closer to 80%".

    Returns:
        ``(pools, reason)``.
    """
    s, p = overall["size_class"], overall["place_type"]
    if not (math.isfinite(p["median_abs"]) and math.isfinite(s["median_abs"])):
        return "size_class", "the out-of-time test had no cases"
    lower = p["median_abs"] < s["median_abs"]

    def inside(x: float) -> bool:
        return math.isfinite(x) and band[0] <= x <= band[1]

    aim = INTERVAL[1] - INTERVAL[0]
    if inside(p["coverage"]):
        cover_ok = True
    elif inside(s["coverage"]):
        cover_ok = False
    else:
        cover_ok = abs(p["coverage"] - aim) < abs(s["coverage"] - aim)
    if lower and cover_ok:
        return "place_type", "lower out-of-time error with coverage in the band"
    if not lower:
        return "size_class", "place-type pools did not lower the out-of-time error"
    return "size_class", "place-type pools lowered the error but their coverage missed the band"


def bias_correction_decision(
    tested: pd.DataFrame, pools: str, band: Sequence[float]
) -> dict[str, Any]:
    """Keep or drop the P50 pool-ratio correction, by the rule in ``assumptions/backtest.yaml``.

    Out of time (pools from earlier origins only, later origins scored), the
    corrected method (P50 = projection x pool median ratio, range from the pool's
    P10 / P90 ratios) is compared with the uncorrected projection (P50 = the
    rule's own projection, the same range shape divided by the pool median).
    The correction is kept only if its median absolute error is lower AND its
    P10-P90 coverage is inside ``band``; otherwise it is dropped.

    Args:
        tested: :func:`out_of_time_test` output.
        pools: The adopted pools (``"size_class"`` or ``"place_type"``).
        band: ``bias_correction.coverage_band``.

    Returns:
        ``{keep, reason, rule, pools, method, band, corrected, uncorrected}``;
        ``corrected`` and ``uncorrected`` are :func:`method_stats` dicts with
        the out-of-time coverage of each.
    """
    method = "place_type_own" if pools == "place_type" else "size_class"
    rule = (
        "Keep the P50 pool-ratio correction only if, out of time, its median absolute "
        "error is lower than the uncorrected projection's and its P10-P90 coverage is "
        f"inside {_pct(band[0])}-{_pct(band[1])}; otherwise drop it. Fixed in "
        "assumptions/backtest.yaml (bias_correction) before any score was computed."
    )
    base = {"rule": rule, "pools": pools, "method": method, "band": [float(b) for b in band]}
    if tested.empty or f"p50_{method}" not in tested.columns:
        empty = method_stats(tested, "none")
        return {
            **base,
            "keep": True,
            "reason": "the out-of-time test had no cases, so the correction is kept",
            "corrected": empty,
            "uncorrected": empty,
        }
    proj = tested["p50_none"].to_numpy(dtype=float)
    mid = tested[f"p50_{method}"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(mid > 0, proj / np.where(mid > 0, mid, 1.0), np.nan)
    frame = tested[["actual"]].copy()
    frame["p50_corrected"] = mid
    frame["low_corrected"] = tested[f"low_{method}"].to_numpy(dtype=float)
    frame["high_corrected"] = tested[f"high_{method}"].to_numpy(dtype=float)
    frame["p50_uncorrected"] = proj
    frame["low_uncorrected"] = frame["low_corrected"] * scale
    frame["high_uncorrected"] = frame["high_corrected"] * scale
    corrected = method_stats(frame, "corrected")
    uncorrected = method_stats(frame, "uncorrected")
    lower = (
        math.isfinite(corrected["median_abs"])
        and math.isfinite(uncorrected["median_abs"])
        and corrected["median_abs"] < uncorrected["median_abs"]
    )
    cov = corrected["coverage"]
    inside = math.isfinite(cov) and band[0] <= cov <= band[1]
    if lower and inside:
        keep, reason = True, "lower out-of-time error with coverage in the band"
    elif not lower:
        keep, reason = False, "the correction did not lower the out-of-time error"
    else:
        keep, reason = False, "the correction lowered the error but its coverage missed the band"
    return {
        **base,
        "keep": keep,
        "reason": reason,
        "corrected": corrected,
        "uncorrected": uncorrected,
    }


# --------------------------------------------------------------------------- surge years


def surge_years(
    series: np.ndarray, years: np.ndarray, factor: float
) -> list[tuple[int, float, float]]:
    """Years in one window whose units exceed ``factor`` x the median of the others.

    Args:
        series: Units in the window (``NaN`` = not reported).
        years: The window's years.
        factor: ``surge_factor``.

    Returns:
        ``(year, units, median of the other years)`` for each surge year.
    """
    out = []
    for i, y in enumerate(years):
        v = series[i]
        others = np.delete(series, i)
        others = others[np.isfinite(others)]
        if not np.isfinite(v) or others.size == 0:
            continue
        med = float(np.median(others))
        if med > 0 and v > factor * med:
            out.append((int(y), float(v), med))
    return out


def surge_check(
    panel: Panel,
    cases: pd.DataFrame,
    factor: float,
    window: int,
    lead: int,
    horizon: int,
    names: Mapping[str, str],
) -> dict[str, Any]:
    """Name surge years in the baseline window and test their effect on back-test error.

    Args:
        panel: The permit panel.
        cases: ``backtest_cases`` of the winner, for counties.
        factor: ``surge_factor``.
        window: Years of the baseline window (``baseline.recent_window_years``).
        lead: Years from origin to the first target year.
        horizon: Target years.
        names: County id to name, for the named counties to report.

    Returns:
        ``window_surges`` (county, name, year, units, median of the others, times
        the median) in the last ``window`` data years; ``named`` (per named
        county: error with and without surge cases); and ``sentences``.
    """
    totals = panel.totals()
    last = int(panel.years[-1])
    cols = (panel.years > last - window) & (panel.years <= last)
    win_years = panel.years[cols]
    surges = []
    for i, c in enumerate(panel.counties):
        for y, v, med in surge_years(totals[i, cols], win_years, factor):
            surges.append(
                {
                    "county": c,
                    "name": names.get(c, c),
                    "year": y,
                    "units": v,
                    "median_others": med,
                    "times": v / med,
                }
            )
    flags = []
    for _, case in cases.iterrows():
        o = int(case["origin"])
        tcols = np.arange(o + lead, o + lead + horizon) - int(panel.years[0])
        flags.append(
            bool(
                surge_years(
                    totals[int(case["place"]), tcols],
                    np.arange(o + lead, o + lead + horizon),
                    factor,
                )
            )
        )
    marked = cases.assign(surge=flags)
    marked = marked[(marked["actual"] > 0) & (marked["projected"] > 0)]
    named = []
    index = {c: i for i, c in enumerate(panel.counties)}
    for c, name in names.items():
        if c not in index:
            continue
        mine = marked[marked["place"] == index[c]]
        row: dict[str, Any] = {"county": c, "name": name}
        for key, part in (
            ("with_surge", mine[mine["surge"]]),
            ("without_surge", mine[~mine["surge"]]),
            ("all", mine),
        ):
            e = np.log(part["projected"].to_numpy() / part["actual"].to_numpy())
            row[key] = {
                "cases": int(e.size),
                "typical_pct": math.expm1(float(np.median(e))) if e.size else None,
            }
        named.append(row)
    sentences = []
    for s in surges:
        if s["county"] not in names:
            continue
        sentences.append(
            f"{s['name']}: {s['year']} permits ({s['units']:,.0f} units) were "
            f"{s['times']:.1f} times the median of its other years in "
            f"{int(win_years[0])}-{int(win_years[-1])} ({s['median_others']:,.0f}), so a mean "
            "of that window is pulled up by one year; a median is not."
        )
    for r in named:
        w, wo = r["with_surge"], r["without_surge"]
        if w["cases"] and wo["cases"] and w["typical_pct"] is not None:
            sentences.append(
                f"In the back-test, the rule ran {_signed(w['typical_pct'])} for {r['name']} in "
                f"the {w['cases']} cases whose target years hold a surge year, and "
                f"{_signed(wo['typical_pct'])} in the other {wo['cases']}."
            )
    return {"factor": factor, "window_surges": surges, "named": named, "sentences": sentences}


# --------------------------------------------------------------------------- stress


def origin_kinds(panel: Panel, origins: Sequence[int], lead: int, horizon: int) -> dict[int, str]:
    """Label each origin "slump" or "boom" from the state's own totals.

    "slump": the state's units in the origin's five target years were below its
    units in the ``horizon`` years up to the origin; "boom" otherwise. Origins
    whose windows lack data are left out.
    """
    state = panel.state_series()[0].sum(axis=1) if panel.units.size else np.array([])
    state = np.where(np.isnan(panel.state_series()[0]).all(axis=1), np.nan, state)
    first = int(panel.years[0])
    out = {}
    for o in origins:
        before = state[o - horizon + 1 - first : o - first + 1]
        after_idx = np.arange(o + lead, o + lead + horizon) - first
        if before.size < horizon or after_idx.max() >= state.size or o - horizon + 1 < first:
            continue
        after = state[after_idx]
        if np.isnan(before).any() or np.isnan(after).any():
            continue
        out[int(o)] = "slump" if after.sum() < before.sum() else "boom"
    return out


# --------------------------------------------------------------------------- one call


@dataclass(frozen=True)
class Contest:
    """The contest and calibration results for one permit table and ledger.

    Attributes:
        skill: One row per contest rule (``validate_baseline`` columns plus
            ``eligible`` and ``chosen``).
        winner: The winning rule's name.
        winner_reason: ``"in band"`` or ``"closest coverage"``.
        rules: The contest's rules.
        info: Place types and homes used.
        band: Coverage band.
        tolerance: ``baseline.selection_tolerance``.
        tested: Out-of-time scored cases of the winner (:func:`out_of_time_test`).
        overall: Out-of-time stats per method, all counties.
        pools: ``"place_type"`` or ``"size_class"``: what the engine uses.
        pools_reason: Why (the adoption rule's outcome).
        cases: The winner's back-test cases (counties).
        calibration: ``calibration`` block of ``assumptions/backtest.yaml``
            plus ``name_few_cases_beyond``.
        correction: The bias-correction decision (:func:`bias_correction_decision`).
    """

    skill: pd.DataFrame = field(repr=False)
    winner: str
    winner_reason: str
    rules: tuple[Rule, ...] = field(repr=False)
    info: PlaceInfo = field(repr=False)
    band: tuple[float, float]
    tolerance: float
    tested: pd.DataFrame = field(repr=False)
    overall: dict[str, dict[str, Any]] = field(repr=False)
    pools: str
    pools_reason: str
    cases: pd.DataFrame = field(repr=False)
    calibration: dict[str, Any] = field(repr=False)
    correction: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def rule(self) -> Rule:
        """The winning rule."""
        return next(r for r in self.rules if r.name == self.winner)


def _digest(table: pd.DataFrame, info: PlaceInfo | None) -> str:
    h = hashlib.sha256(pd.util.hash_pandas_object(table, index=False).to_numpy().tobytes())
    if info is not None:
        h.update(repr((info.groups, info.source)).encode())
        if info.homes is not None:
            h.update(np.asarray(info.homes, dtype=float).tobytes())
    return h.hexdigest()


_CONTESTS: dict[tuple[str, ...], Contest] = {}


def run_contest(
    table: pd.DataFrame,
    ledger: Ledger,
    rules: Mapping[str, Any],
    info: PlaceInfo | None = None,
) -> Contest:
    """Run the contest, choose the winner, and test the corrections out of time.

    Args:
        table: Tidy permit table.
        ledger: The Assumption Ledger.
        rules: ``assumptions/backtest.yaml``.
        info: Place types and homes; ``None`` uses size classes and no homes.

    Returns:
        The contest (cached by data, ledger and place info).
    """
    key = (
        _digest(table, info),
        ledger.hash(),
        repr(rules.get("contest")),
        repr(rules.get("calibration")),
        repr(rules.get("bias_correction")),
    )
    if key in _CONTESTS:
        return _CONTESTS[key]
    settings = settings_from_ledger(ledger)
    panel = to_panel(table)
    info = info or default_place_info(panel, settings)
    lead = lead_years(panel, settings)
    origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    candidates = contest_rules(ledger, rules)
    skill = validate_baseline(table, candidates, origins, settings, info)
    band = (
        float(rules["contest"]["coverage_band"][0]),
        float(rules["contest"]["coverage_band"][1]),
    )
    winner, reason = choose_winner(skill, band, settings.tolerance)
    skill = skill.assign(
        eligible=(skill["coverage_80"] >= band[0]) & (skill["coverage_80"] <= band[1]),
        chosen=skill["rule"] == winner,
    )
    rule = next(r for r in candidates if r.name == winner)
    cases = backtest_cases(rule, panel.years, panel.units, origins, lead, settings.horizon, info)
    at_origin = origin_size_classes(panel, origins, settings.recent_window, settings.n_classes)
    calib = dict(rules.get("calibration", {}))
    calib["name_few_cases_beyond"] = float(rules.get("name_few_cases_beyond", math.inf))
    # Size classes as they stood at each origin. A typology place type is today's
    # grouping (current data); size classes standing in for place types follow
    # the origin too.
    keys: dict[str, Any] = {
        "size_class": {o: k.astype(object) for o, k in at_origin.items()},
        "place_type": (
            {
                o: np.array([class_label(int(x), settings.n_classes) for x in k], dtype=object)
                for o, k in at_origin.items()
            }
            if info.source == "size_class"
            else np.array(info.groups, dtype=object)
        ),
    }
    tested = out_of_time_test(
        cases,
        keys,
        lead,
        settings.horizon,
        int(calib.get("min_group_cases", 30)),
        int(calib.get("min_own_cases", 10)),
        calib["name_few_cases_beyond"],
    )
    overall = {m: method_stats(tested, m) for m in METHOD_LABELS}
    pools, pools_reason = choose_pools(overall, band)
    bc = rules.get("bias_correction") or {}
    bc_band = tuple(float(x) for x in bc.get("coverage_band", band))
    correction = bias_correction_decision(tested, pools, bc_band)
    out = Contest(
        skill=skill,
        winner=winner,
        winner_reason=reason,
        rules=candidates,
        info=info,
        band=band,
        tolerance=settings.tolerance,
        tested=tested,
        overall=overall,
        pools=pools,
        pools_reason=pools_reason,
        cases=cases,
        calibration=calib,
        correction=correction,
    )
    _CONTESTS[key] = out
    return out


# --------------------------------------------------------------------------- words


def _pct(x: float) -> str:
    return f"{round(abs(float(x)) * 100):.0f}%"


def _signed(x: float) -> str:
    if not math.isfinite(x):
        return "no cases"
    return f"{_pct(x)} {'high' if x > 0 else 'low'}"


# --------------------------------------------------------------------------- report


def _clean(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if hasattr(v, "item"):
        return _clean(v.item())
    return v


def _clean_deep(v: Any) -> Any:
    if isinstance(v, Mapping):
        return {str(k): _clean_deep(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_clean_deep(x) for x in v]
    return _clean(v)


def group_comparison(
    contest: Contest,
    panel: Panel,
    settings: BaselineSettings,
    groups: pd.DataFrame,
    dims: Sequence[Mapping[str, Any]],
    rules: Mapping[str, Any],
    reference: Rule | None,
) -> list[dict[str, Any]]:
    """Error and coverage by group: the reference rule, the winner, and each correction.

    ``reference`` (the five-year mean) and ``winner`` are scored as in the
    place breakdown (every origin; ranges from other counties' errors in the
    same size class). The correction methods are scored out of time
    (:func:`out_of_time_test`). One row per group, all counties first.
    """
    from visionhousing.baseline.places import DIMENSION_TITLES, group_stats, scored_cases

    lead = lead_years(panel, settings)
    origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    at_origin = origin_size_classes(panel, origins, settings.recent_window, settings.n_classes)
    ids = np.array([str(c) for c in panel.counties])
    scored = {}
    for tag, rule in (("reference", reference), ("winner", contest.rule)):
        if rule is None:
            continue
        cases = backtest_cases(
            rule, panel.years, panel.units, origins, lead, settings.horizon, contest.info
        )
        scored[tag] = scored_cases(cases, at_origin, lead, settings.horizon)
    min_counties, min_cases = int(rules["min_counties"]), int(rules["min_cases"])
    rows: list[dict[str, Any]] = []

    def add(dim: str, title: str, group: str, phrase: str, members: set[str]) -> None:
        row: dict[str, Any] = {
            "dimension": dim,
            "title": title,
            "group": group,
            "phrase": phrase,
            "counties": len(members),
        }
        for tag, sc in scored.items():
            mask = np.isin(ids[sc["place"].to_numpy(dtype=int)], list(members))
            st = group_stats(sc[mask])
            row[tag] = {k: _clean(v) for k, v in st.items()}
        t = contest.tested
        tmask = (
            np.isin(ids[t["place"].to_numpy(dtype=int)], list(members))
            if len(t)
            else np.array([], dtype=bool)
        )
        for m in METHOD_LABELS:
            st = method_stats(t[tmask] if len(t) else t, m)
            row[m] = {k: _clean(v) for k, v in st.items() if k != "method"}
        cases = (row.get("winner") or {}).get("cases") or 0
        row["small"] = bool(len(members) < min_counties or cases < min_cases)
        rows.append(row)

    add("all", DIMENSION_TITLES["all"], "all counties", "all counties", set(ids))
    for d in dims:
        if not d["available"]:
            continue
        col = groups[d["key"]]
        for g in d["groups"]:
            add(d["key"], d["title"], g, d["phrases"][g], set(col.index[col == g]))
    return rows


def contest_findings(
    contest: Contest,
    reference: str,
    comparison: Sequence[Mapping[str, Any]],
    rules: Mapping[str, Any],
) -> list[str]:
    """Sentences written by rule from the contest and the group comparison."""
    sk = contest.skill.set_index("rule")
    w = sk.loc[contest.winner]
    cases = int(w["cases"])
    w_abs = _pct(math.expm1(w["median_abs_log_ratio"]))
    w_signed = _signed(math.expm1(w["median_signed_log_error"]))
    out = [
        f"{len(sk)} rules were back-tested on the same {cases:,} county cases. "
        f"The {_rule_name(contest.winner)} won: it was typically {w_abs} off, its ranges "
        f"caught the real total in {_pct(w['coverage_80'])} of cases, and it ran {w_signed} "
        "on average."
        + (
            ""
            if contest.winner_reason == "in band"
            else " No rule's coverage was inside the band, so the closest to 80% won."
        )
    ]
    if reference in sk.index and reference != contest.winner:
        r = sk.loc[reference]
        r_abs = _pct(math.expm1(r["median_abs_log_ratio"]))
        r_signed = _signed(math.expm1(r["median_signed_log_error"]))
        out.append(f"The {_rule_name(reference)} was typically {r_abs} off and ran {r_signed}.")
    best = sk["median_abs_log_ratio"].idxmin()
    if best != contest.winner:
        b = sk.loc[best]
        why = (
            f"its coverage ({_pct(b['coverage_80'])}) was outside the band"
            if not bool(b["eligible"])
            else "a simpler rule was within the tolerance"
        )
        out.append(f"The {_rule_name(best)} had the lowest error but did not win: {why}.")
    o = contest.overall
    n, s, p = o["none"], o["size_class"], o["place_type"]
    if n["cases"]:
        out.append(
            f"Out of time (each origin corrected only with errors known by then; {n['cases']:,} "
            f"cases), the winner's own projection was typically "
            f"{_pct(math.expm1(n['median_abs']))} off. Correcting it by size class gave "
            f"{_pct(math.expm1(s['median_abs']))} with ranges that caught "
            f"{_pct(s['coverage'])}; by place type, "
            f"{_pct(math.expm1(p['median_abs']))} and {_pct(p['coverage'])}. The engine uses "
            f"{'place-type' if contest.pools == 'place_type' else 'size-class'} pools: "
            f"{contest.pools_reason}."
        )
        if math.isfinite(s["coverage"]) and s["coverage"] < contest.band[0]:
            out.append(
                "Ranges built from past errors caught the real total less often when tested "
                "on later years than when tested on other counties: a new housing cycle moves "
                "every county at once."
            )
    out.append(
        "The winner was chosen on the whole back-test (every origin at once), so the choice "
        "itself is in-sample; its ranges and the corrections are scored only with errors "
        "from earlier origins. Size classes are worked out at each origin from the years up "
        "to it"
        + (
            "; place types are today's county grouping (current data) applied to every origin."
            if contest.info.source == "typology"
            else "."
        )
    )
    big = float(rules["map_breaks"][1])
    for row in comparison:
        if row["dimension"] == "all":
            continue
        ref, win = row.get("reference") or {}, row.get("winner") or {}
        a, b = ref.get("typical_pct"), win.get("typical_pct")
        if a is None or b is None or abs(a) < big:
            continue
        before = (
            ""
            if reference == contest.winner
            else f"the {_rule_name(reference)} ran {_signed(a)} and "
        )
        out.append(
            f"In {row['phrase']} {before}the {_rule_name(contest.winner)} runs {_signed(b)}; "
            f"its ranges caught {win.get('covered_cases')} of {win.get('interval_cases')} cases."
            + (" Few cases: read this as a few places, not a pattern." if row["small"] else "")
        )
    return out


RULE_NAMES: dict[str, str] = {
    "last_5_mean": "five-year mean",
    "last_3_mean": "three-year mean",
    "median_5": "median of five years",
    "damped_trend": "damped trend",
    "shrink_to_group": "shrink to place type",
    "top_down_share": "top-down share",
    "average": "average of the six rules",
    "last_10_mean": "ten-year mean",
    "ewm": "weighted mean",
}
"""Plain names of the rules (labels)."""


def _rule_name(name: str) -> str:
    text = RULE_NAMES.get(name, name)
    return text


def stress_baselines(
    table: pd.DataFrame,
    contest: Contest,
    ledger: Ledger,
    rules: Mapping[str, Any],
    *,
    widen: bool,
    state: str | None = None,
) -> list[dict[str, Any]]:
    """The baseline re-run with only slump or only boom origins' errors (alternative ranges)."""
    from visionhousing.baseline.project import growth_widen_for, project_baseline
    from visionhousing.engine.records import format_number

    settings = settings_from_ledger(ledger)
    panel = to_panel(table)
    lead = lead_years(panel, settings)
    origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    kinds = origin_kinds(panel, origins, lead, settings.horizon)
    growth = growth_widen_for(state, ledger, rules, int(panel.years[-1])) if widen else None
    out = []
    for kind in rules.get("stress", []):
        subset = [o for o, k in kinds.items() if k == kind]
        if not subset:
            out.append(
                {
                    "kind": kind,
                    "origins": [],
                    "state": None,
                    "sentence": f"No back-test origin was a {kind}.",
                }
            )
            continue
        res = project_baseline(
            table,
            contest.rule,
            settings,
            "typical",
            rules.get("small_sample") if widen else None,
            info=contest.info,
            pools=contest.pools,
            calibration=contest.calibration,
            origins=subset,
            correct_bias=bool(contest.correction.get("keep", True)),
            growth=growth,
        )
        st = res.state
        out.append(
            {
                "kind": kind,
                "origins": subset,
                "state": st.model_dump(mode="json"),
                "sentence": (
                    f"If the next five years look like the {len(subset)} past {kind} periods "
                    f"(origins {', '.join(str(o) for o in subset)}), the baseline's likely range "
                    f"is {format_number(st.p10, 'units')} to {format_number(st.p90, 'units')} "
                    f"(middle {format_number(st.p50, 'units')})."
                ),
            }
        )
    return out


def observed_only_table(table: pd.DataFrame) -> pd.DataFrame | None:
    """The permit table without county-years that hold any imputed figure (``None``: no flag).

    The Building Permits Survey fills in figures for permit offices that did not
    report; ``imputed`` is true when any part of a county-year was filled in.
    Dropping those rows leaves only fully reported county-years, so a back-test
    case survives only if all five of its target years were fully reported.
    """
    if "imputed" not in table.columns:
        return None
    flag = table["imputed"].astype(str).str.lower().isin(("true", "1", "yes"))
    return table[~flag].reset_index(drop=True)


def _skill_rows(skill: pd.DataFrame) -> list[dict[str, Any]]:
    keep = [
        "rule",
        "cases",
        "median_abs_log_ratio",
        "median_signed_log_error",
        "coverage_80",
        "coverage_cases",
        "range_width",
    ]
    rows = []
    for r in skill[[c for c in keep if c in skill.columns]].to_dict(orient="records"):
        row = {k: _clean(v) for k, v in r.items()}
        row["name"] = _rule_name(str(row["rule"]))
        rows.append(row)
    return rows


def imputation_variants(
    table: pd.DataFrame,
    contest: Contest,
    settings: BaselineSettings,
) -> dict[str, Any]:
    """The contest table scored on all years and on fully reported years only.

    Both variants use the same rules and the same origins; only the county-years
    allowed into the history and the targets differ.
    """
    panel = to_panel(table)
    lead = lead_years(panel, settings)
    origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    out: dict[str, Any] = {
        "definition": (
            "all years: every county-year in the Building Permits Survey files, including "
            "figures the Census Bureau imputed for offices that did not report; observed "
            "only: county-years with any imputed figure removed from both the history a rule "
            "reads and the target years it is scored on"
        ),
        "all_years": {"rows": _skill_rows(contest.skill)},
    }
    if observed_only_table(table) is None:
        out["observed_only"] = {"rows": [], "note": "This permit table has no imputation flag."}
        return out
    flag = table["imputed"].astype(str).str.lower().isin(("true", "1", "yes"))
    out["imputed_share"] = _clean(float(flag.mean())) if len(table) else None
    if bool(flag.all()):
        out["observed_only"] = {"rows": [], "note": "Every county-year holds an imputed figure."}
        return out
    observed = table.copy()
    cols = [c for c in ("units_1", "units_2", "units_3_4", "units_5plus") if c in observed]
    observed[cols] = observed[cols].astype(float)
    observed.loc[flag.to_numpy(), cols] = np.nan  # kept as "not reported" so the years stay
    try:
        skill = validate_baseline(observed, contest.rules, origins, settings, contest.info)
    except ValueError as exc:  # a table too thin to back-test
        out["observed_only"] = {"rows": [], "note": f"Not scored: {exc}."}
        return out
    out["observed_only"] = {"rows": _skill_rows(skill)}
    return out


def statewide_backtest(
    table: pd.DataFrame, contest: Contest, settings: BaselineSettings
) -> dict[str, Any]:
    """Each contest rule back-tested on the state's own yearly totals, beside the county table.

    One case per origin. The P10-P90 range of each case comes from the other
    origins' ratios only (leave one origin out).
    """
    panel = to_panel(table)
    lead = lead_years(panel, settings)
    origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    state = panel.state_series()
    rows = []
    for rule in contest.rules:
        try:
            cases = backtest_cases(rule, panel.years, state, origins, lead, settings.horizon)
        except (ValueError, IndexError) as exc:  # a rule that needs several places
            rows.append(
                {
                    "rule": rule.name,
                    "name": _rule_name(rule.name),
                    "cases": 0,
                    "note": f"not scored on one state total: {exc}",
                }
            )
            continue
        ok = cases[(cases["actual"] > 0) & (cases["projected"] > 0)]
        logs = np.log(ok["ratio"].to_numpy(dtype=float))
        inside = []
        for i in range(len(ok)):
            others = np.delete(ok["ratio"].to_numpy(dtype=float), i)
            q = ratio_quantiles(others, INTERVAL)
            if q is None:
                continue
            proj, act = float(ok["projected"].iloc[i]), float(ok["actual"].iloc[i])
            inside.append(proj * q[0] <= act <= proj * q[1])
        rows.append(
            {
                "rule": rule.name,
                "name": _rule_name(rule.name),
                "cases": len(ok),
                "median_abs_log_ratio": _clean(float(np.median(np.abs(logs))))
                if logs.size
                else None,
                "median_signed_log_error": _clean(float(-np.median(logs))) if logs.size else None,
                "coverage_80": _clean(float(np.mean(inside))) if inside else None,
                "coverage_cases": len(inside),
            }
        )
    return {
        "definition": (
            "the same rules applied to the state's yearly totals (counties that did not report "
            "a year left out of that year); one case per origin; each case's P10-P90 range "
            "from the other origins' ratios"
        ),
        "rows": rows,
    }


def origin_overlap(panel: Panel, settings: BaselineSettings) -> dict[str, Any]:
    """How many independent five-year periods the rolling origins really hold.

    Consecutive origins share four of their five target years, so the cases are
    not independent. The number of origins whose target windows do not overlap
    is ``floor((last origin - first origin) / horizon) + 1``.
    """
    lead = lead_years(panel, settings)
    origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    if not origins:
        return {"origins": 0, "independent_periods": 0, "note": "No usable back-test origin."}
    first, last = origins[0], origins[-1]
    independent = (last - first) // settings.horizon + 1
    return {
        "origins": len(origins),
        "first_origin": first,
        "last_origin": last,
        "target_years": f"{first + lead}-{last + lead + settings.horizon - 1}",
        "independent_periods": independent,
        "note": (
            f"The {len(origins)} rolling origins overlap: each shares up to "
            f"{settings.horizon - 1} of its {settings.horizon} target years with the next. "
            f"They hold about {independent} non-overlapping {settings.horizon}-year periods, so "
            "case counts overstate the independent evidence; read coverage and error as "
            f"resting on about {independent} periods, not {len(origins)}."
        ),
    }


def contest_report(
    contest: Contest,
    table: pd.DataFrame,
    ledger: Ledger,
    rules: Mapping[str, Any],
    *,
    state: str,
    tracts: pd.DataFrame | None = None,
    typology: pd.DataFrame | None = None,
    incentives: pd.DataFrame | None = None,
    names: Mapping[str, str] | None = None,
    widen: bool = False,
) -> dict[str, Any]:
    """The JSON-ready contest payload for the Method screen (``/validation``)."""
    from visionhousing.baseline.places import county_groups

    settings = settings_from_ledger(ledger)
    panel = to_panel(table)
    classes = size_classes(panel, settings.recent_window, settings.n_classes)
    groups, dims = county_groups(
        panel.counties,
        classes,
        settings.n_classes,
        rules,
        state=state,
        tracts=tracts,
        typology=typology,
        incentives=incentives,
    )
    reference_name = contest.rules[0].name
    reference = contest.rules[0]
    comparison = group_comparison(contest, panel, settings, groups, dims, rules, reference)
    named = {str(k): str(v) for k, v in rules.get("named_counties", {}).get(state, {}).items()}
    lead = lead_years(panel, settings)
    surge = surge_check(
        panel,
        contest.cases,
        float(rules.get("surge_factor", math.inf)),
        settings.recent_window,
        lead,
        settings.horizon,
        named,
    )
    stress = stress_baselines(table, contest, ledger, rules, widen=widen, state=state)
    table_rows = [
        {k: _clean(v) for k, v in r.items()}
        for r in contest.skill.drop(columns=["mape"], errors="ignore").to_dict(orient="records")
    ]
    for r in table_rows:
        r["name"] = _rule_name(str(r["rule"]))
        r["typical_pct"] = (
            _clean(math.expm1(r["median_signed_log_error"]))
            if r.get("median_signed_log_error") is not None
            else None
        )
        r["abs_pct"] = (
            _clean(math.expm1(r["median_abs_log_ratio"]))
            if r.get("median_abs_log_ratio") is not None
            else None
        )
        r["describe"] = next(x for x in contest.rules if x.name == r["rule"]).describe(
            int(panel.years[-1])
        )
    return {
        "choosing_rule": choosing_rule_text(contest.band, contest.tolerance),
        "coverage_band": list(contest.band),
        "tolerance": contest.tolerance,
        "columns": {
            "median_signed_log_error": "median of log(projected / actual); above zero ran high",
            "median_abs_log_ratio": "median of |log(projected / actual)|",
            "coverage_80": "share of cases inside the P10-P90 range built from other counties",
            "range_width": "mean of (P90 - P10) / projection",
            "eligible": "coverage inside the band",
            "chosen": "the winner",
        },
        "rules": table_rows,
        "winner": contest.winner,
        "winner_name": _rule_name(contest.winner),
        "winner_reason": contest.winner_reason,
        "reference": reference_name,
        "place_type": {"source": contest.info.source, "groups": sorted(set(contest.info.groups))},
        "calibration": {
            "methods": {m: METHOD_LABELS[m] for m in METHOD_LABELS},
            "overall": {
                m: {k: _clean(v) for k, v in st.items()} for m, st in contest.overall.items()
            },
            "pools": contest.pools,
            "pools_reason": contest.pools_reason,
            "min_group_cases": contest.calibration.get("min_group_cases"),
            "min_own_cases": contest.calibration.get("min_own_cases"),
            "origins_tested": sorted(int(o) for o in contest.tested["origin"].unique())
            if len(contest.tested)
            else [],
        },
        "by_group": comparison,
        "bias_correction": {k: _clean_deep(v) for k, v in contest.correction.items()},
        "variants": _clean_deep(imputation_variants(table, contest, settings)),
        "statewide": _clean_deep(statewide_backtest(table, contest, settings)),
        "origins": origin_overlap(panel, settings),
        "surge": surge,
        "stress": stress,
        "findings": [
            *contest_findings(contest, reference_name, comparison, rules),
            *surge["sentences"],
        ],
    }
