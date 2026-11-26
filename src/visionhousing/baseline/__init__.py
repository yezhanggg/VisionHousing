"""The baseline: what housing production looks like with no reform.

Projects building-permit units for the next five years under current rules and
economics, and backtests that projection against past years. Seven simple rules
compete (``contest``): five- and three-year means, the median of five years, a
damped trend, shrink to place type, top-down share and their average. A choosing
rule fixed in ``assumptions/backtest.yaml`` picks the winner. The likely range
comes from the winner's own past errors, pooled by county size class or place
type, whichever does better when tested on later years only.
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
