"""The baseline projection: likely permitted units in 2027-2031 with no reform.

Steps, for one rule and one housing-cycle choice:

1. **Project.** Apply the rule to each county and structure type, using
   permits up to the last data year, for each year 2027-2031. Add the five
   years and the four structure types: the county's *point projection*.
2. **Backtest.** Apply the same rule at every past origin that has five
   target years of data (``backtest.backtest_cases``) and record the ratio of
   actual to projected five-year units.
3. **Size class.** Sort counties into terciles by recent permits; pool the
   ratios of each class.
4. **Range.** The county's P10 and P90 are its point projection times the
   class's 10th and 90th percentile ratios. Its P50 is the point projection
   times the ratio quantile the cycle choice names (weak = 20th percentile,
   typical = median, strong = 80th percentile, from the ledger).
5. **State.** The state's P50 is the sum of the county P50s, so counties add
   up to the state. Percentiles do not add (the sum of county P10s is lower
   than the state's P10, because bad years in different counties partly
   cancel), so the state's range comes from running the same backtest on the
   state's own yearly totals: state P10 = state P50 x (state ratio P10 /
   state ratio at the cycle quantile), and the same for P90.

A county with no permits in the rule's window gets 0 / 0 / 0 and its formula
says why. If a size class has no usable ratios, all counties' ratios are used
and the formula says so; if there are none at all, the range collapses to the
point projection and the formula says that too.

``project_baseline`` is pure: the table, rule and settings go in; a
``BaselineResult`` of records comes out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from visionhousing.baseline.backtest import (
    backtest_cases,
    backtest_origins,
    choose_rule,
    class_label,
    lead_years,
    project_totals,
    ratio_quantiles,
    size_classes,
    validate_baseline,
)
from visionhousing.baseline.panel import STRUCTURE_LABELS, STRUCTURE_TYPES, to_panel
from visionhousing.baseline.rules import Rule, rules_from_ledger
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
    """

    rule: str
    cycle: str
    counties: dict[str, Record]
    state: Record
    annual: pd.DataFrame
    multipliers: pd.DataFrame


def project_baseline(
    table: pd.DataFrame,
    rule: Rule,
    settings: BaselineSettings,
    cycle: str = "typical",
) -> BaselineResult:
    """Project five-year baseline permits by county and for the state, as ranges.

    Args:
        table: Tidy permit table (county x year x structure type).
        rule: The projection rule (normally the one ``choose_rule`` picked).
        settings: Baseline settings from the ledger.
        cycle: ``weak``, ``typical`` or ``strong`` housing cycle.

    Returns:
        The baseline result.

    Raises:
        ValueError: Unknown cycle, or data that reach the first projected year.
    """
    if cycle not in CYCLES:
        raise ValueError(f"cycle must be one of {list(CYCLES)}, got {cycle!r}")
    panel = to_panel(table)
    lead = lead_years(panel, settings)
    origin = int(panel.years[-1])
    targets = np.arange(settings.first_year, settings.first_year + settings.horizon)
    q_cycle = settings.cycle_quantiles[cycle]
    quantiles = (INTERVAL[0], q_cycle, INTERVAL[1])
    origins = backtest_origins(panel.years, lead, settings.horizon, settings.min_history)
    assumption_ids = tuple(sorted({*settings.assumption_ids(cycle), *rule.assumption_ids}))
    span = f"{targets[0]}-{targets[-1]}"
    how = rule.describe(origin)

    # 1. Point projections by county and structure type.
    by_type, annual, has_history = project_totals(rule, panel.years, panel.units, origin, targets)
    point = by_type.sum(axis=1)

    # 2-3. Backtest ratios, pooled by size class.
    cases = backtest_cases(rule, panel.years, panel.units, origins, lead, settings.horizon)
    classes = size_classes(panel, settings.recent_window, settings.n_classes)
    cases["size_class"] = classes[cases["place"].to_numpy()] if len(cases) else []
    pooled = ratio_quantiles(cases["ratio"].to_numpy(), quantiles)
    class_rows, class_q = [], {}
    for k in range(settings.n_classes):
        ratios = cases.loc[cases["size_class"] == k, "ratio"].to_numpy()
        q = ratio_quantiles(ratios, quantiles)
        note = f"{np.isfinite(ratios).sum()} past county-origins in this class"
        if q is None and pooled is not None:
            q, note = pooled, "no usable ratios in this class, so all counties' ratios"
        elif q is None:
            q, note = None, "no usable backtest ratios, so the range is the point projection"
        class_q[k] = (q, note)
        class_rows.append(
            {
                "size_class": class_label(k, settings.n_classes),
                "counties": int((classes == k).sum()),
                "cases": int(np.isfinite(ratios).sum()),
                "q_low": np.nan if q is None else q[0],
                "q_cycle": np.nan if q is None else q[1],
                "q_high": np.nan if q is None else q[2],
            }
        )

    # 4. County records.
    counties: dict[str, Record] = {}
    for i, county in enumerate(panel.counties):
        q, note = class_q[classes[i]]
        inputs = tuple(
            InputRef(
                name=f"projected {STRUCTURE_LABELS[t]}, {span}",
                value=float(by_type[i, j]),
                unit="units",
                formula=f"{how}, applied to {STRUCTURE_LABELS[t]}, summed over {span}",
            )
            for j, t in enumerate(STRUCTURE_TYPES)
        )
        label = class_label(classes[i], settings.n_classes)
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
            p10, p50, p90 = (float(point[i]) * x for x in m)
            formula = (
                f"point projection ({how}, by structure type, summed over {span}) = "
                f"{format_number(float(point[i]), 'units')}; "
                f"{TIMES} {label}-county ratios of actual to projected five-year units "
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
) -> Record:
    """Build the state record: P50 adds the counties; the range comes from state totals."""
    p50 = math.fsum(r.p50 for r in county_records.values())  # type: ignore[misc]
    cases = backtest_cases(rule, panel_years, state_units, origins, lead, settings.horizon)
    q = ratio_quantiles(cases["ratio"].to_numpy(), quantiles)
    state_point = float(project_totals(rule, panel_years, state_units, origin, targets)[0].sum())
    n_cases = int(np.isfinite(cases["ratio"]).sum()) if len(cases) else 0
    sum_line = f"P50 = sum of county P50s = {format_number(p50, 'units')}"
    if p50 == 0:
        p10 = p90 = 0.0
        formula = f"{sum_line}; every county projects zero, so the range is zero"
    elif q is not None and q[1] > 0:
        p10, p90 = p50 * float(q[0] / q[1]), p50 * float(q[2] / q[1])
        formula = (
            f"{sum_line}; range from the same rule ({how}) backtested on state totals at "
            f"{n_cases} past origins (state point projection "
            f"{format_number(state_point, 'units')}): P10 = P50 {TIMES} "
            f"{_r(float(q[0]))} / {_r(float(q[1]))}, P90 = P50 {TIMES} "
            f"{_r(float(q[2]))} / {_r(float(q[1]))} (ratio P10, {cycle} cycle q"
            f"{quantiles[1]:g}, P90) = {_trio(p10, p50, p90)}"
        )
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
        ),
        assumption_ids=assumption_ids,
        source_ids=SOURCE_IDS,
        tier=TIER,
    )


def baseline_from_ledger(
    table: pd.DataFrame, ledger: Ledger, cycle: str = "typical"
) -> tuple[BaselineResult, pd.DataFrame]:
    """Backtest the candidate rules, choose one, and project the baseline.

    Args:
        table: Tidy permit table (county x year x structure type).
        ledger: The Assumption Ledger (rule settings, tolerance, cycle quantiles).
        cycle: ``weak``, ``typical`` or ``strong`` housing cycle.

    Returns:
        ``(result, skill)``: the baseline for the chosen rule, and the skill
        table with a ``chosen`` column.
    """
    settings = settings_from_ledger(ledger)
    rules = rules_from_ledger(ledger)
    skill = validate_baseline(table, rules, None, settings)
    name = choose_rule(skill, settings.tolerance)
    rule = next(r for r in rules if r.name == name)
    result = project_baseline(table, rule, settings, cycle)
    if "baseline.selection_tolerance" not in result.state.assumption_ids:
        ids = tuple(sorted({*result.state.assumption_ids, "baseline.selection_tolerance"}))
        result = BaselineResult(
            rule=result.rule,
            cycle=result.cycle,
            counties={
                c: r.model_copy(update={"assumption_ids": ids}) for c, r in result.counties.items()
            },
            state=result.state.model_copy(update={"assumption_ids": ids}),
            annual=result.annual,
            multipliers=result.multipliers,
        )
    return result, skill.assign(chosen=skill["rule"] == name)
