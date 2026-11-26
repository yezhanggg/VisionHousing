"""Cross-state comparison records: what other states changed, and what is known of the results.

Plain-language summary
----------------------
Pennsylvania is the tool's primary state, but its reforms have been tried
elsewhere. This package keeps one **comparison record** per real law, court
decision, agency program or published study from other states and cities:
what changed, where, on which dates, whether it is in force, whether a court
has stepped in, what outcome was measured (if any), over which denominator and
horizon, and how strong the evidence is (descriptive, matched or causal).

The records live in ``assumptions/evidence/states/*.yaml``, one file per state
plus ``studies.yaml`` (published studies) and ``discovery.yaml`` (candidate
reforms found in trackers or searches that nobody has reviewed yet). Every
record says whether its citation and dates were read on an official page
(``verified``) and, if not, what is still missing.

- :mod:`visionhousing.evidence.schema`: the record format and its checks;
- :mod:`visionhousing.evidence.records`: loads and checks every file;
- :mod:`visionhousing.evidence.matrix`: the completeness matrix, state by policy
  type, for all 50 states and DC;
- :mod:`visionhousing.evidence.phl_fields`: a live check of the date fields in
  Philadelphia's public permit layer;
- :mod:`visionhousing.evidence.retro`: the retrospective reform panel (permits
  in reformed cities against comparison cities, from a design declared first).

The records are context. No number in them enters a model run.
"""
