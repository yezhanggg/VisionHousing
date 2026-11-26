"""State intake: the per-input manifest, value states, readiness check and reproduce links.

Plain-language summary
----------------------
Before a state's results are published, every input should say where it came
from, what it measures, when it was read and how it was changed. This package
gathers that from records the repository already keeps (the source registry,
each state pack's manifest and data dictionary, the Assumption Ledger, the law
tables, the calibration files and the comparison records), so nothing is typed
twice:

- :mod:`visionhousing.intake.value_state`: the six words for why a value is or
  is not there (true zero, missing, suppressed, imputed, not applicable, feed
  failure);
- :mod:`visionhousing.intake.manifest`: the per-input intake manifest and the
  two evidence fractions (verified exposure, transferred take-up);
- :mod:`visionhousing.intake.readiness`: the five-step intake and release check;
- :mod:`visionhousing.intake.reproduce`: the "reproduce this number" metadata.

Submodules are imported on use, so the report card can use the value states
without loading the rest. Method: ``docs/modules/intake.md``.
"""
