"""Law, zoning and housing policy at three levels: state, county and municipality.

Plain-language summary
----------------------
Housing reforms are written at the state level, but what they change depends on
the rules a county or a municipality already has. This package keeps the rules
that bear on the five benchmark reforms (ADUs, missing middle, transit-oriented
density, parking, fees) in four small tables of the state pack:

- ``law_documents``: each legal text read (the state planning code, bill
  pages, municipal ordinances), with its address, licence note and checksum;
- ``state_law_provisions``: the state law sections and pending bills that bear
  on each reform, each with its citation, status and a summary in our words;
- ``county_land_use``: per county, how many municipalities and residents fall
  under the county's own zoning ordinance, and the county comprehensive plan;
- ``muni_zoning_rules``: per municipality, the ADU rule, the smallest minimum
  lot, the parking minimums, the housing types permitted and whether there is a
  transportation impact fee ordinance. A municipality whose ordinance nobody
  has read says ``unknown``, never "no".

Modules:

- :mod:`~visionhousing.law.schema`: the row types and their allowed values;
- :mod:`~visionhousing.law.text`: reads a PDF's text and splits it into
  sections, keeping character offsets so every quote can be found again;
- :mod:`~visionhousing.law.extract`: a rule-based reader that ranks sections by
  keywords and pulls values with patterns. It only drafts rows for a person to
  check; it never marks a row verified. There is no AI step.
- :mod:`~visionhousing.law.state`: fetches the state planning code and the bill
  pages and builds the state provisions;
- :mod:`~visionhousing.law.tables`: builds the four tables and adds them to a pack;
- :mod:`~visionhousing.law.inforce`: when a verified municipal rule counts as
  read on the run's as-of date (one test for the hooks and the coverage tables);
- :mod:`~visionhousing.law.hooks`: how verified municipal rules make the
  reform assumptions vary by county (``assumptions/law/hooks.yaml``);
- :mod:`~visionhousing.law.coverage`: lot and land weights, coverage three
  ways (residents, lots, land) and the ADU legal-status categories;
- :mod:`~visionhousing.law.priorities`: which unread ordinance to read next;
- :mod:`~visionhousing.law.timeline`: the policy implementation timeline
  (pending, enacted, legally effective, stayed, locally implemented,
  practically usable), with supersession and "took effect" vs "verified on"
  dates, and the eligibility call the engine can make;
- :mod:`~visionhousing.law.states`: reads the law files of states without the
  Pennsylvania table (``assumptions/law/states/<st>.yaml``, their timeline
  records and the already-legal calibration) for the ``state_law`` profile section.
"""
