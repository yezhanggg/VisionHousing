"""The baseline projection: likely permitted units in 2027-2031 with no reform.

Steps, for one rule and one housing-cycle choice:

1. **Project.** Apply the rule to each county and structure type, using
   permits up to the last data year, for each year 2027-2031. Add the five
   years and the four structure types: the county's *point projection*.
2. **Backtest.** Apply the same rule at every past origin that has five
   target years of data (``backtest.backtest_cases``) and record the ratio of
   actual to projected five-year units.
3. **Pools.** Pool the ratios by county size class (terciles by recent
   permits) or by place type (``pools="place_type"``: the county's place type
   if it has at least ``min_group_cases`` past cases, else all counties). The
   engine uses whichever the out-of-time test of
   :mod:`visionhousing.baseline.contest` adopts.
4. **Range.** The county's P10 and P90 are its point projection times the
   pool's 10th and 90th percentile ratios. Its P50 is the point projection
   times the ratio quantile the cycle choice names (weak = 20th percentile,
   typical = median, strong = 80th percentile, from the ledger); at the median
   this corrects the rule's typical past bias in that pool. A county whose own
   back-test is far off gets each end of its range from the wider of its
   pool's ratio and its own (``calibration`` in ``assumptions/backtest.yaml``).
5. **State.** The state's P50 is the sum of the county P50s, so counties add
   up to the state. Percentiles do not add (the sum of county P10s is lower
   than the state's P10, because bad years in different counties partly
   cancel), so the state's range comes from running the same backtest on the
   state's own yearly totals: state P10 = state P50 x (state ratio P10 /
   state ratio at the cycle quantile), and the same for P90.
6. **Small samples.** When the state's back-test rests on few county-origins,
   each end of the state's range is blended toward a prior on the log scale
   and only ever widened (:func:`widen_small_sample`; the rule and the prior
   are ``small_sample`` in ``assumptions/backtest.yaml``). A state with enough
   cases keeps its own range exactly.
7. **Growth-aware widening.** A sparse state listed in the growth coverage
   study (``assumptions/calibration/growth_coverage.yaml``, adopted) then has
   each end blended, with the same weight, toward the raw 10th / 90th
   percentile errors of other states in its growth class, and only ever
   widened (:func:`widen_growth_class`; ``growth_widen`` in
   ``assumptions/backtest.yaml``, ledger switch ``baseline.growth_widen``).

A county with no permits in the rule's window gets 0 / 0 / 0 and its formula
says why. If a size class has no usable ratios, all counties' ratios are used
and the formula says so; if there are none at all, the range collapses to the
point projection and the formula says that too.

8. **Measured reliability.** For a real state, the engine adds to the state's
   and each county's formula how often ranges built this way caught the real
   five-year total in past years, out of time ("In past years, ranges like
   this caught the real five-year total in N of M cases"), from
   ``assumptions/calibration/baseline_reliability.yaml``
   (:mod:`visionhousing.baseline.reliability`). Values are unchanged.

``project_baseline`` is pure: the table, rule and settings go in; a
``BaselineResult`` of records comes out.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.baseline.backtest import (
    backtest_cases,
    backtest_origins,
    class_label,
    lead_years,
    project_totals,
    ratio_quantiles,
    size_classes,
)
from visionhousing.baseline.contest import own_widening, run_contest
from visionhousing.baseline.panel import STRUCTURE_LABELS, STRUCTURE_TYPES, to_panel
from visionhousing.baseline.rules import PlaceInfo, Rule
from visionhousing.baseline.settings import (
    CYCLES,
    INTERVAL,
    BaselineSettings,
    settings_from_ledger,
)
from visionhousing.engine.records import TIMES, InputRef, Record, format_number
from visionhousing.ledger import Ledger

SOURCE_IDS: tuple[str, ...] = ("bps_county_annual",)
"""Registry id of the Census Building Permits Survey county files."""

TIER = 0
"""The baseline uses only national permit data, available for every state."""


@dataclass(frozen=True)
class BaselineResult:
    """The baseline for one state.

    Attributes:
        rule: Name of the rule used.
        cycle: The housing-cycle choice.
        counties: County id to its five-year range record.
        state: The state's five-year range record.
        annual: Point projections by county, structure type and year (tidy).
        multipliers: Ratio quantiles used, by size class, with case counts.
        correction: The bias-correction decision applied (``None`` when the
            baseline was projected directly, without the contest).
    """

    rule: str
    cycle: str
    counties: dict[str, Record]
    state: Record
    annual: pd.DataFrame
    multipliers: pd.DataFrame
    correction: dict[str, Any] | None = None


def project_baseline(
    table: pd.DataFrame,
    rule: Rule,
    settings: BaselineSettings,
    cycle: str = "typical",
    small_sample: Mapping[str, Any] | None = None,
    *,
    info: PlaceInfo | None = None,
    pools: str = "size_class",
    calibration: Mapping[str, Any] | None = None,
    origins: Sequence[int] | None = None,
    correct_bias: bool = True,
    growth: Mapping[str, Any] | None = None,
) -> BaselineResult:
    """Project five-year baseline permits by county and for the state, as ranges.

    Args:
        table: Tidy permit table (county x year x structure type).
        rule: The projection rule (normally the one ``choose_rule`` picked).
        settings: Baseline settings from the ledger.
        cycle: ``weak``, ``typical`` or ``strong`` housing cycle.
        small_sample: The ``small_sample`` block of ``assumptions/backtest.yaml``
            (``None``: no widening).
        info: Place types and homes (the cross-sectional rules and
            ``pools="place_type"``); ``None``: size classes, no homes.
        pools: ``"size_class"`` or ``"place_type"``.
        calibration: ``calibration`` of ``assumptions/backtest.yaml`` plus
            ``name_few_cases_beyond`` (own-error widening and the place-type
            minimum); ``None``: no own-error widening.
        origins: Back-test origins whose errors set the ranges (``None``: all
            usable origins). The stress settings pass one kind of origin.
        correct_bias: Apply the P50 pool-ratio correction (default). ``False``
            divides every ratio quantile by the pool's median ratio: the P50 is
            the rule's own projection (at the typical cycle) and the range keeps
            its shape (the bias-correction decision of
            :func:`visionhousing.baseline.contest.bias_correction_decision`).
        growth: The state's growth-aware widening (:func:`growth_widen_for`), or
            ``None`` (no growth-aware widening).

    Returns:
        The baseline result.

    Raises:
        ValueError: Unknown cycle, or data that reach the first projected year.
    """
    if cycle not in CYCLES:
        raise ValueError(f"cycle must be one of {list(CYCLES)}, got {cycle!r}")
    if pools not in ("size_class", "place_type"):
        raise ValueError(f"pools must be 'size_class' or 'place_type', got {pools!r}")
    panel = to_panel(table)
    lead = lead_years(panel, settings)
    origin = int(panel.years[-1])
    targets = np.arange(settings.first_year, settings.first_year + settings.horizon)
    q_cycle = settings.cycle_quantiles[cycle]
    quantiles = (INTERVAL[0], q_cycle, INTERVAL[1])
    if origins is None:
        origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    origins = list(origins)
    assumption_ids = tuple(sorted({*settings.assumption_ids(cycle), *rule.assumption_ids}))
    span = f"{targets[0]}-{targets[-1]}"
    how = rule.describe(origin)

    # 1. Point projections by county and structure type.
    by_type, annual, has_history = project_totals(
        rule, panel.years, panel.units, origin, targets, info
    )
    point = by_type.sum(axis=1)

    # 2-3. Backtest ratios, pooled by size class or place type.
    cases = backtest_cases(rule, panel.years, panel.units, origins, lead, settings.horizon, info)
    classes = size_classes(panel, settings.recent_window, settings.n_classes)
    cases["size_class"] = classes[cases["place"].to_numpy()] if len(cases) else []
    finite = cases[np.isfinite(cases["ratio"])] if len(cases) else cases
    pooled = ratio_quantiles(cases["ratio"].to_numpy(), quantiles)
    pooled_med = ratio_quantiles(cases["ratio"].to_numpy(), (0.5,))
    calib = dict(calibration or {})
    min_group = int(calib.get("min_group_cases", 30))
    if pools == "place_type":
        type_of = np.array(
            (
                info.groups
                if info is not None
                else tuple(class_label(int(k), settings.n_classes) for k in classes)
            ),
            dtype=object,
        )
        labels = list(dict.fromkeys(type_of.tolist()))
        pool_of = type_of
        pool_word = "place type"
    else:
        labels = [class_label(k, settings.n_classes) for k in range(settings.n_classes)]
        pool_of = np.array([class_label(int(k), settings.n_classes) for k in classes], dtype=object)
        pool_word = "size class"
    class_rows, class_q = [], {}
    class_med: dict[str, float | None] = {}
    for g in labels:
        members = pool_of == g
        ratios = (
            finite.loc[members[finite["place"].to_numpy(dtype=int)], "ratio"].to_numpy()
            if len(finite)
            else np.array([])
        )
        n_g = int(np.isfinite(ratios).sum())
        q = ratio_quantiles(ratios, quantiles)
        med = ratio_quantiles(ratios, (0.5,))
        note = f"{n_g} past county-origins in this {pool_word}"
        if pools == "place_type" and n_g < min_group and pooled is not None:
            q, med = pooled, pooled_med
            note = (
                f"{n_g} past county-origins in this place type, fewer than {min_group}, "
                "so all counties' ratios"
            )
        elif q is None and pooled is not None:
            q, med = pooled, pooled_med
            note = f"no usable ratios in this {pool_word}, so all counties' ratios"
        elif q is None:
            q, note = None, "no usable backtest ratios, so the range is the point projection"
        class_q[g] = (q, note)
        class_med[g] = None if med is None else float(med[0])
        class_rows.append(
            {
                "size_class" if pools == "size_class" else "place_type": g,
                "counties": int(members.sum()),
                "cases": n_g,
                "q_low": np.nan if q is None else q[0],
                "q_cycle": np.nan if q is None else q[1],
                "q_high": np.nan if q is None else q[2],
            }
        )

    # 4. County records.
    counties: dict[str, Record] = {}
    for i, county in enumerate(panel.counties):
        q, note = class_q[pool_of[i]]
        if q is not None and "min_own_cases" in calib and len(finite):
            q, own_note = own_widening(
                finite,
                i,
                np.asarray(q, dtype=float),
                quantiles,
                int(calib["min_own_cases"]),
                float(calib.get("name_few_cases_beyond", math.inf)),
            )
            if own_note:
                note = f"{note}; {own_note}"
        inputs = tuple(
            InputRef(
                name=f"projected {STRUCTURE_LABELS[t]}, {span}",
                value=float(by_type[i, j]),
                unit="units",
                formula=f"{how}, applied to {STRUCTURE_LABELS[t]}, summed over {span}",
            )
            for j, t in enumerate(STRUCTURE_TYPES)
        )
        label = (
            str(pool_of[i])
            if pools == "place_type"
            else class_label(classes[i], settings.n_classes)
        )
        if not has_history[i]:
            p10 = p50 = p90 = 0.0
            formula = (
                f"0 units: no permit records in the rule's window up to {origin}, "
                "so the projection is zero"
            )
        elif point[i] == 0:
            p10 = p50 = p90 = 0.0
            formula = f"0 units: {how} is zero for every structure type, so the projection is zero"
        else:
            m = (1.0, 1.0, 1.0) if q is None else tuple(float(x) for x in q)
            med = class_med.get(pool_of[i])
            uncorrected = not correct_bias and q is not None and med is not None and med > 0
            if uncorrected:
                m = tuple(x / float(med) for x in m)
                note = (
                    f"{note}; bias correction dropped by the out-of-time test, so each ratio "
                    f"is divided by the pool's median ratio {_r(float(med))}"
                )
            p10, p50, p90 = (float(point[i]) * x for x in m)
            ratios_of = (
                f"{label}-county ratios"
                if pools == "size_class"
                else f"ratios in past cases of place type {label}"
            )
            formula = (
                f"point projection ({how}, by structure type, summed over {span}) = "
                f"{format_number(float(point[i]), 'units')}; "
                f"{TIMES} {ratios_of} of actual to projected five-year units "
                f"(P10 {_r(m[0])}, {cycle} cycle q{q_cycle:g} {_r(m[1])}, P90 {_r(m[2])}; "
                f"{note}) = {_trio(p10, p50, p90)}"
            )
        counties[county] = Record(
            name=f"baseline permitted units {span}, county {county}",
            p10=p10,
            p50=p50,
            p90=p90,
            unit="units",
            formula=formula,
            inputs=inputs,
            assumption_ids=assumption_ids,
            source_ids=SOURCE_IDS,
            tier=TIER,
        )

    # 5. State record.
    state = _state_record(
        panel_years=panel.years,
        state_units=panel.state_series(),
        county_records=counties,
        rule=rule,
        origins=origins,
        lead=lead,
        settings=settings,
        quantiles=quantiles,
        cycle=cycle,
        span=span,
        how=how,
        origin=origin,
        targets=targets,
        assumption_ids=assumption_ids,
        county_cases=int(np.isfinite(cases["ratio"]).sum()) if len(cases) else 0,
        small_sample=small_sample,
        growth=growth,
    )

    annual_rows = [
        {
            "county": county,
            "structure_type": t,
            "year": int(year),
            "units": float(annual[i, j, h]),
        }
        for i, county in enumerate(panel.counties)
        for j, t in enumerate(STRUCTURE_TYPES)
        for h, year in enumerate(targets)
    ]
    return BaselineResult(
        rule=rule.name,
        cycle=cycle,
        counties=counties,
        state=state,
        annual=pd.DataFrame(annual_rows),
        multipliers=pd.DataFrame(class_rows),
    )


def _r(x: float) -> str:
    return f"{x:.3f}"


def _trio(p10: float, p50: float, p90: float) -> str:
    return (
        f"P10 {format_number(p10, 'units')}, P50 {format_number(p50, 'units')}, "
        f"P90 {format_number(p90, 'units')}"
    )


def _state_record(
    *,
    panel_years: np.ndarray,
    state_units: np.ndarray,
    county_records: dict[str, Record],
    rule: Rule,
    origins: list[int],
    lead: int,
    settings: BaselineSettings,
    quantiles: tuple[float, float, float],
    cycle: str,
    span: str,
    how: str,
    origin: int,
    targets: np.ndarray,
    assumption_ids: tuple[str, ...],
    county_cases: int = 0,
    small_sample: Mapping[str, Any] | None = None,
    growth: Mapping[str, Any] | None = None,
) -> Record:
    """Build the state record: P50 adds the counties; the range comes from state totals."""
    p50 = math.fsum(r.p50 for r in county_records.values())  # type: ignore[misc]
    cases = backtest_cases(rule, panel_years, state_units, origins, lead, settings.horizon)
    q = ratio_quantiles(cases["ratio"].to_numpy(), quantiles)
    state_point = float(project_totals(rule, panel_years, state_units, origin, targets)[0].sum())
    n_cases = int(np.isfinite(cases["ratio"]).sum()) if len(cases) else 0
    sum_line = f"P50 = sum of county P50s = {format_number(p50, 'units')}"
    extra_inputs: tuple[InputRef, ...] = ()
    if p50 == 0:
        p10 = p90 = 0.0
        formula = f"{sum_line}; every county projects zero, so the range is zero"
    elif q is not None and q[1] > 0:
        low, high = math.log(float(q[0] / q[1])), math.log(float(q[2] / q[1]))
        widened = widen_small_sample(low, high, county_cases, quantiles, small_sample)
        grown = (
            widen_growth_class(widened[0], widened[1], county_cases, growth["entry"], growth)
            if growth
            else (widened[0], widened[1], "")
        )
        if grown[2] and growth:
            assumption_ids = tuple(sorted({*assumption_ids, GROWTH_ID}))
            entry = growth["entry"]
            extra_inputs = (
                InputRef(
                    name=f"other-state cases in growth class {entry.get('growth_class', '?')}",
                    value=float(entry.get("pool_cases", 0)),
                    unit="cases",
                    formula=(
                        f"pool ratio P10 {_r(float(entry['pool_ratio_p10']))}, P90 "
                        f"{_r(float(entry['pool_ratio_p90']))} of real to projected five-year "
                        f"state totals ({growth.get('source', GROWTH_SOURCE)})"
                    ),
                ),
            )
        p10, p90 = p50 * math.exp(grown[0]), p50 * math.exp(grown[1])
        formula = (
            f"{sum_line}; range from the same rule ({how}) backtested on state totals at "
            f"{n_cases} past origins (state point projection "
            f"{format_number(state_point, 'units')}): P10 = P50 {TIMES} "
            f"{_r(float(q[0]))} / {_r(float(q[1]))}, P90 = P50 {TIMES} "
            f"{_r(float(q[2]))} / {_r(float(q[1]))} (ratio P10, {cycle} cycle q"
            f"{quantiles[1]:g}, P90)"
        )
        if widened[2]:
            formula += (
                f"; {widened[2]}: P10 = P50 {TIMES} {_r(math.exp(widened[0]))}, "
                f"P90 = P50 {TIMES} {_r(math.exp(widened[1]))}"
            )
        if grown[2]:
            not_tested = "" if cycle == "typical" else "; not back-tested for this cycle"
            formula += (
                f"; {grown[2]}{not_tested}: P10 = P50 {TIMES} {_r(math.exp(grown[0]))}, "
                f"P90 = P50 {TIMES} {_r(math.exp(grown[1]))} "
                f"({(growth or {}).get('source', GROWTH_SOURCE)})"
            )
        formula += f" = {_trio(p10, p50, p90)}"
    else:
        p10 = math.fsum(r.p10 for r in county_records.values())  # type: ignore[misc]
        p90 = math.fsum(r.p90 for r in county_records.values())  # type: ignore[misc]
        p10, p90 = min(p10, p50), max(p90, p50)
        formula = (
            f"{sum_line}; no usable state backtest ratios, so the range is the sum of "
            f"county P10s and P90s (wider than a true state range) = {_trio(p10, p50, p90)}"
        )
    return Record(
        name=f"baseline permitted units {span}, state",
        p10=p10,
        p50=p50,
        p90=p90,
        unit="units",
        formula=formula,
        inputs=tuple(
            InputRef(
                name=f"county {c} P50",
                value=float(r.p50),  # type: ignore[arg-type]
                unit="units",
                formula=r.formula,
            )
            for c, r in county_records.items()
        )
        + extra_inputs,
        assumption_ids=assumption_ids,
        source_ids=SOURCE_IDS,
        tier=TIER,
    )


def widen_small_sample(
    low: float,
    high: float,
    n: int,
    quantiles: tuple[float, float, float],
    rule: Mapping[str, Any] | None,
) -> tuple[float, float, str]:
    """Widen the state range's two ends when the back-test has few county-origins.

    ``low`` and ``high`` are the log distances of P10 and P90 from the cycle
    quantile (``low`` <= 0 <= ``high`` as a rule). With
    ``w = min(1, n / full_weight_cases)``, each end is blended toward the prior
    (``w x own + (1 - w) x prior``) and the wider of own and blended is kept.

    Args:
        low: ``log(ratio P10 / ratio at the cycle quantile)``.
        high: ``log(ratio P90 / ratio at the cycle quantile)``.
        n: Usable county-origin cases in the state's back-test.
        quantiles: ``(P10 level, cycle quantile, P90 level)``.
        rule: ``small_sample`` of ``assumptions/backtest.yaml``, or ``None``.

    Returns:
        ``(low, high, note)``. ``note`` is empty when nothing changed.
    """
    if not rule:
        return low, high, ""
    full = float(rule["full_weight_cases"])
    w = min(1.0, max(0, n) / full) if full > 0 else 1.0
    if w >= 1.0:
        return low, high, ""
    table = {float(k): float(v) for k, v in rule["prior"]["ratio_quantiles"].items()}
    levels = sorted(table)
    logs = [math.log(table[k]) for k in levels]

    def prior(qv: float) -> float:
        return float(np.interp(qv, levels, logs))

    centre = prior(quantiles[1])
    p_low, p_high = prior(quantiles[0]) - centre, prior(quantiles[2]) - centre
    new_low = min(low, w * low + (1 - w) * p_low)
    new_high = max(high, w * high + (1 - w) * p_high)
    if new_low == low and new_high == high:
        return low, high, ""
    note = (
        f"few cases ({n} county-origins, weight w = {n} / {full:,.0f} = {w:.2f}), so each end is "
        f"blended toward the {rule['prior'].get('state', 'prior')} prior on the log scale "
        "(w x own + (1 - w) x prior) and only widened (assumptions/backtest.yaml, small_sample)"
    )
    return new_low, new_high, note


GROWTH_ID = "baseline.growth_widen"
"""Ledger switch of the growth-aware widening (1 = on, 0 = off)."""

GROWTH_SOURCE = "assumptions/calibration/growth_coverage.yaml"
"""Default results file of the growth coverage study (``growth_widen.source`` may name another)."""


def widen_growth_class(
    low: float,
    high: float,
    n: int,
    entry: Mapping[str, Any] | None,
    rule: Mapping[str, Any] | None,
) -> tuple[float, float, str]:
    """Widen the state range toward its growth class's pooled past errors (sparse states).

    ``low`` and ``high`` are the log distances of P10 and P90 from the P50,
    after the small-sample widening. With ``w = min(1, n / full_weight_cases)``
    (the small-sample weight), ``g_low = log(pool ratio P10)`` and
    ``g_high = log(pool ratio P90)`` (raw pool ratios of real to projected
    totals, not centred on the pool median, which keeps the shift one-sided),
    each end becomes the wider of itself and ``w x end + (1 - w) x g``.

    Args:
        low: Log distance of P10 from P50.
        high: Log distance of P90 from P50.
        n: Usable county-origin cases in the state's back-test.
        entry: The state's ``sparse_states`` entry of the study (``growth_class``,
            ``pool_cases``, ``pool_ratio_p10``, ``pool_ratio_p90``), or ``None``.
        rule: ``growth_widen`` of ``assumptions/backtest.yaml`` plus
            ``full_weight_cases`` (and optionally ``origin``), or ``None``.

    Returns:
        ``(low, high, note)``. ``note`` is empty when nothing changed (no entry,
        no rule, a pool under ``min_pool_cases``, a full record, or ends that
        are already wider). Never narrows.
    """
    if not entry or not rule:
        return low, high, ""
    pool = int(entry.get("pool_cases", 0))
    if pool < int(rule.get("min_pool_cases", 5)):
        return low, high, ""
    full = float(rule["full_weight_cases"])
    w = min(1.0, max(0, n) / full) if full > 0 else 1.0
    if w >= 1.0:
        return low, high, ""
    g_low = math.log(float(entry["pool_ratio_p10"]))
    g_high = math.log(float(entry["pool_ratio_p90"]))
    new_low = min(low, w * low + (1 - w) * g_low)
    new_high = max(high, w * high + (1 - w) * g_high)
    if new_low == low and new_high == high:
        return low, high, ""
    at = f" at {rule['origin']}" if rule.get("origin") is not None else ""
    note = (
        f"growth-aware widening (growth class {entry.get('growth_class', '?')}{at}, "
        f"{pool:,} other-state cases, w = {w:.2f}; each end is the wider of itself and "
        f"w x end + (1 - w) x log(pool ratio), pool ratios P10 {_r(math.exp(g_low))} and "
        f"P90 {_r(math.exp(g_high))})"
    )
    return new_low, new_high, note


def growth_widen_for(
    state: str | None, ledger: Ledger, rules: Mapping[str, Any], origin: int | None = None
) -> dict[str, Any] | None:
    """The growth-aware widening for one state, or ``None`` when it does not apply.

    Applies when ``growth_widen.enabled`` is true, the ledger switch
    ``baseline.growth_widen`` is on, the study's ``decision.outcome`` is
    ``adopt`` and the state is listed under its ``sparse_states``.
    """
    block = rules.get("growth_widen") or {}
    small = rules.get("small_sample") or {}
    if not state or not block.get("enabled") or not small:
        return None
    if GROWTH_ID in ledger and float(ledger[GROWTH_ID].central) < 0.5:
        return None
    from visionhousing.ledger.loader import ROOT, read_yaml

    path = ROOT / str(block.get("source", GROWTH_SOURCE))
    if not path.is_file():
        return None
    study = read_yaml(path) or {}
    if (study.get("decision") or {}).get("outcome") != "adopt":
        return None
    entry = (study.get("sparse_states") or {}).get(state.upper())
    if not entry:
        return None
    return {
        **block,
        "entry": dict(entry),
        "full_weight_cases": float(small["full_weight_cases"]),
        "origin": origin,
    }


def load_small_sample_rule() -> dict[str, Any] | None:
    """The ``small_sample`` block of ``assumptions/backtest.yaml`` (``None`` if absent)."""
    from visionhousing.baseline.places import load_place_rules

    return load_place_rules().get("small_sample")


def baseline_from_ledger(
    table: pd.DataFrame,
    ledger: Ledger,
    cycle: str = "typical",
    *,
    widen: bool = False,
    places: PlaceInfo | None = None,
    state: str | None = None,
) -> tuple[BaselineResult, pd.DataFrame]:
    """Run the rule contest, apply its winner and adopted pools, and project the baseline.

    Args:
        table: Tidy permit table (county x year x structure type).
        ledger: The Assumption Ledger (rule settings, tolerance, cycle quantiles).
        cycle: ``weak``, ``typical`` or ``strong`` housing cycle.
        widen: Apply the small-sample widening of the state range
            (:func:`widen_small_sample`). The engine passes ``True`` for every
            real state; the synthetic Testland is never widened (its prior would
            be a real state's record).
        places: Place types and homes of the counties
            (:func:`visionhousing.baseline.contest.place_info_for_context`);
            ``None`` uses size classes as place types and no homes.
        state: Postal code, for the growth-aware widening of a sparse state
            (:func:`growth_widen_for`; only with ``widen``).

    Returns:
        ``(result, skill)``: the baseline for the winning rule, and the contest's
        skill table with ``eligible`` and ``chosen`` columns.
    """
    from visionhousing.baseline.places import load_place_rules

    rules = load_place_rules()
    contest = run_contest(table, ledger, rules, places)
    settings = settings_from_ledger(ledger)
    rule_small = rules.get("small_sample") if widen else None
    growth = (
        growth_widen_for(state, ledger, rules, int(to_panel(table).years[-1])) if widen else None
    )
    result = project_baseline(
        table,
        contest.rule,
        settings,
        cycle,
        rule_small,
        info=contest.info,
        pools=contest.pools,
        calibration=contest.calibration,
        correct_bias=bool(contest.correction.get("keep", True)),
        growth=growth,
    )
    if "baseline.selection_tolerance" not in result.state.assumption_ids:

        def ids(r: Record) -> tuple[str, ...]:
            return tuple(sorted({*r.assumption_ids, "baseline.selection_tolerance"}))

        result = BaselineResult(
            rule=result.rule,
            cycle=result.cycle,
            counties={
                c: r.model_copy(update={"assumption_ids": ids(r)})
                for c, r in result.counties.items()
            },
            state=result.state.model_copy(update={"assumption_ids": ids(result.state)}),
            annual=result.annual,
            multipliers=result.multipliers,
        )
    result = replace(result, correction=_correction_summary(contest.correction))
    if widen and state:
        from visionhousing.baseline.reliability import attach, reliability_for

        entry = reliability_for(state)
        if reliability_matches(
            entry,
            rule=result.rule,
            bias_correction_kept=bool(contest.correction.get("keep", True)),
            growth_widening=bool(growth),
            cycle=cycle,
        ):
            result = attach(result, table, settings, entry)
    return result, contest.skill


def reliability_matches(
    entry: Mapping[str, Any] | None,
    *,
    rule: str,
    bias_correction_kept: bool,
    growth_widening: bool,
    cycle: str,
) -> bool:
    """Whether a measured-reliability entry describes the range this run builds.

    The count belongs to the range it was measured on: the same winning rule,
    the same bias-correction choice, the same growth widening, and the typical
    cycle (the study back-tests only that one; the weak and strong cycles move
    the median and, through the widening, the ends). Any difference shows no count.
    """
    if not entry:
        return False
    return (
        cycle == "typical"
        and entry.get("rule") == rule
        and bool(entry.get("bias_correction_kept", True)) == bias_correction_kept
        and bool(entry.get("growth_widening", False)) == growth_widening
    )


def _correction_summary(decision: Mapping[str, Any]) -> dict[str, Any]:
    """The decision without NaNs (JSON-ready)."""

    def clean(v: Any) -> Any:
        if isinstance(v, Mapping):
            return {k: clean(x) for k, x in v.items()}
        if isinstance(v, float) and not math.isfinite(v):
            return None
        return v

    return clean(dict(decision))
