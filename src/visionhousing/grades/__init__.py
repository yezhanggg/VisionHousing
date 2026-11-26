"""Evidence grades, number traces and one-assumption switches for the headline numbers.

Plain-language summary
----------------------
Three trust tools that read a finished engine run and change nothing in it:

- **Evidence grades** (:func:`grade_run`, :func:`grade_number`): every headline
  number (each reform's state figure and each county's) gets a letter A to D
  from a published rule (``assumptions/grades.yaml``): source quality, the
  back-test for that kind of place, the share of lots or residents under
  verified local law, local or borrowed calibration, and the strength of
  outside evidence for the reform. Each grade lists its component scores and
  one plain sentence.
- **Number trace** (:func:`trace`): one headline taken apart step by step
  (eligible -> fit -> feasible -> take-up -> authorized), each step with its
  P50 and P10-P90, what was applied, its ledger ids and sources.
- **Switch off one assumption** (:func:`switch_payload`): each reform's top
  three drivers from the tornado at low / central / high, ready for a static page.

Method and the rule: ``docs/modules/grades.md``.
"""

from visionhousing.grades.grade import grade_number, grade_run
from visionhousing.grades.rule import Grade, letter, load_rule
from visionhousing.grades.switch import switch_payload
from visionhousing.grades.trace import TraceError, trace

__all__ = [
    "Grade",
    "TraceError",
    "grade_number",
    "grade_run",
    "letter",
    "load_rule",
    "switch_payload",
    "trace",
]
