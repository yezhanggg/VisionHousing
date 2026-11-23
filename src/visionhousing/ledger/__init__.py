"""The Assumption Ledger: loads, checks and samples every model parameter.

All parameters live in ``assumptions/parameters.yaml``, never in code. This package
reads that file, validates each entry, draws seeded random samples for the likely
ranges, and gives the whole set a stable hash so a run can be identified.
Scenarios (``assumptions/scenarios.yaml``) are read and checked here too.
"""

from visionhousing.ledger.loader import (
    DEFAULT_PATH,
    Ledger,
    LedgerError,
    apply_overrides,
    load_ledger,
    parse_ledger,
)
from visionhousing.ledger.sampling import DEFAULT_DRAWS, draw, percentiles, sample, stream
from visionhousing.ledger.scenarios import Scenario, load_scenarios, scenario_ledger
from visionhousing.ledger.schema import Parameter, Source

__all__ = [
    "DEFAULT_DRAWS",
    "DEFAULT_PATH",
    "Ledger",
    "LedgerError",
    "Parameter",
    "Scenario",
    "Source",
    "apply_overrides",
    "draw",
    "load_ledger",
    "load_scenarios",
    "parse_ledger",
    "percentiles",
    "sample",
    "scenario_ledger",
    "stream",
]
