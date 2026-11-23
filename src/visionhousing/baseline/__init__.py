"""The baseline: what housing production looks like with no reform.

Projects building-permit units for the next five years under current rules and
economics, and backtests that projection against past years. Four simple rules
are compared (five- and ten-year means, an exponentially weighted mean, and a
damped trend); the simplest one within tolerance of the best is used. The likely
range comes from the rule's own past errors, by county size class.
"""

from visionhousing.baseline.backtest import (
    backtest_cases,
    backtest_origins,
    choose_rule,
    interval_coverage,
    size_classes,
    validate_baseline,
)
from visionhousing.baseline.panel import STRUCTURE_TYPES, Panel, to_panel
from visionhousing.baseline.project import BaselineResult, baseline_from_ledger, project_baseline
from visionhousing.baseline.rules import Rule, project, rules_from_ledger
from visionhousing.baseline.settings import (
    CYCLES,
    INTERVAL,
    BaselineSettings,
    settings_from_ledger,
)

__all__ = [
    "CYCLES",
    "INTERVAL",
    "STRUCTURE_TYPES",
    "BaselineResult",
    "BaselineSettings",
    "Panel",
    "Rule",
    "backtest_cases",
    "backtest_origins",
    "baseline_from_ledger",
    "choose_rule",
    "interval_coverage",
    "project",
    "project_baseline",
    "rules_from_ledger",
    "settings_from_ledger",
    "size_classes",
    "to_panel",
    "validate_baseline",
]
