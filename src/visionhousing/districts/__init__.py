"""Results by state legislative district: weights, per-draw aggregation, the district view.

Plain-language summary
----------------------
A legislator asks: "what does this reform mean where my voters live?" This
package answers with the engine's own draws:

- :mod:`~visionhousing.districts.weights` makes tract-to-district and
  county-to-district weights from 2020 census blocks (housing units in the
  part of a tract inside a district, over the tract's units), and the district
  list with its split shares.
- :mod:`~visionhousing.districts.aggregate` shares draws out to districts draw
  by draw and only then takes P10 / P50 / P90 (a pure function).
- :mod:`~visionhousing.districts.results` does that for a whole run: baseline,
  each reform, the package, and the district brief pointers.
- :mod:`~visionhousing.districts.build` adds the tables and outlines to a pack;
  :mod:`~visionhousing.districts.commands` is the ``visionhousing districts``
  command.

Method and limits: ``docs/modules/districts.md``.
"""

from __future__ import annotations

from visionhousing.districts.aggregate import DistrictWeights, district_draws, percentiles

__all__ = ["DistrictWeights", "district_draws", "percentiles"]
