"""Sensitivity: which assumptions move the answer most.

- ``tornado``: swing one input at a time from low to high, all others at
  central, and rank by the change in the state total.
- ``importance``: Spearman rank correlation between each input's Monte Carlo
  draws and the answer, to check the tornado's order when everything varies
  at once.
- ``drivers``: both, for one reform of an engine result, with the top five in
  plain labels.
- ``variance`` / ``uncertainty``: each input's first-order share of the
  answer's variance, from the stored draws ("what would narrow the range").
- ``switch_runs``: likely ranges (state and county) with one of a reform's top
  tornado inputs fixed at its low, central or high value, every other input
  drawn as in the run (paired draws).

See ``docs/modules/sensitivity.md``.
"""

from visionhousing.sensitivity.drivers import (
    TOP,
    Driver,
    Drivers,
    drivers,
    input_draws,
    uncertain_ids,
    uncertainty,
)
from visionhousing.sensitivity.importance import importance, rankdata, spearman
from visionhousing.sensitivity.switch_runs import cached_switch_runs, switch_runs
from visionhousing.sensitivity.tornado import Swing, pin_central, swing_frame, tornado
from visionhousing.sensitivity.variance import (
    Narrowing,
    Share,
    first_order_share,
    first_order_shares,
    narrowing,
    range_shrink,
)

__all__ = [
    "TOP",
    "Driver",
    "Drivers",
    "Narrowing",
    "Share",
    "Swing",
    "cached_switch_runs",
    "drivers",
    "first_order_share",
    "first_order_shares",
    "importance",
    "input_draws",
    "narrowing",
    "pin_central",
    "range_shrink",
    "rankdata",
    "spearman",
    "swing_frame",
    "switch_runs",
    "tornado",
    "uncertain_ids",
    "uncertainty",
]
