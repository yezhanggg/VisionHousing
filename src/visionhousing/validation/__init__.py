"""Validation studies that check the model's assumptions against other places.

Plain-language summary
----------------------
The engine's timing and take-up assumptions for Pennsylvania cannot all be
measured in Pennsylvania. This package holds studies that read another state's
public records and turn them into small, labelled tables:

- :mod:`visionhousing.validation.apr_data` downloads California's Housing
  Element Annual Progress Report tables and the Census counts used as
  denominators, streaming each file and keeping only the columns needed.
- :mod:`visionhousing.validation.california` follows each application through
  entitlement, building permit and completion, estimates how long each step
  takes and how many projects drop out, and writes comparison records, ranges a
  Pennsylvania scenario may borrow (always marked "transferred"), and a
  plausibility table for the ADU result.
- :mod:`visionhousing.validation.donor_pool` back-tests the baseline's state
  range on held-out states with errors borrowed from other places (Pennsylvania,
  all states, or a matched pool of counties in other states), applies a
  pre-stated decision rule, and builds the two separate confidence signals
  (baseline performance, reform evidence) the interface can show.
- :mod:`visionhousing.validation.preregistration` checks a study's fixed rules
  against the fingerprints recorded in ``assumptions/preregistration.yaml``
  before anything is scored, and refuses to run on a mismatch.

Only aggregates leave these modules; no project row is written to the repository.
"""
