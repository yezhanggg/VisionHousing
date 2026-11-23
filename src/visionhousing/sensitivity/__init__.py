"""Sensitivity: which assumptions move the answer most.

- ``tornado``: swing one input at a time from low to high, all others at
  central, and rank by the change in the state total.
- ``importance``: Spearman rank correlation between each input's Monte Carlo
  draws and the answer, to check the tornado's order when everything varies
  at once.
- ``drivers``: both, for one reform of an engine result, with the top five in
  plain labels.

See ``docs/modules/sensitivity.md``.
"""

from visionhousing.sensitivity.drivers import TOP, Driver, Drivers, drivers, uncertain_ids
from visionhousing.sensitivity.importance import importance, rankdata, spearman
from visionhousing.sensitivity.tornado import Swing, pin_central, swing_frame, tornado

__all__ = [
    "TOP",
    "Driver",
    "Drivers",
    "Swing",
    "drivers",
    "importance",
    "pin_central",
    "rankdata",
    "spearman",
    "swing_frame",
    "tornado",
    "uncertain_ids",
]
