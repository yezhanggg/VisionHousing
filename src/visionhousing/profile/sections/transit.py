"""Profile section ``transit``: major transit stops and the homes near them.

Plain-language summary
----------------------
Decision D-07 defines a **major transit stop**: any rail station (subway,
light rail, trolley, commuter or regional rail and similar), or a bus stop with
at least 64 scheduled departures on a weekday (about one every 15 minutes).
A home is **near transit** when its 2020 census block's centre is within 1/2
mile of a rail station or 1/4 mile of a frequent bus stop, in a straight line.

Facts:

- major transit stops inside the place (stop points as the National Transit
  Map lists them, so one station with several platforms counts several times);
- the share of homes near transit by the D-07 rule;
- the share of homes within 1/2 mile of any major stop.

Shares are of 2020 Census block housing units. A county is compared with the
state, a tract with its county.
"""

from __future__ import annotations

import pandas as pd

from visionhousing.profile.context import Area, Measure, ProfileContext
from visionhousing.profile.model import Section
from visionhousing.profile.sections._common import FactMaker, finish, sentence, with_words

KEY = "transit"
TITLE = "Transit"
STOPS = "ntm_stops"
BLOCKS = "tiger_2020_tabblock"


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``transit`` section for a county or tract."""
    place, other = ctx.place(geoid)
    stops = ctx.stops()
    per_tract = ctx.transit_tracts()

    def major_stops(area: Area) -> Measure:
        major = pd.to_numeric(stops["major"], errors="coerce") == 1
        if area.level == "tract":
            inside = stops["tract"].astype(str) == area.geoid
        else:
            inside = stops["county"].astype(str).isin(area.counties)
        return Measure(float((major & inside).sum()))

    def near(column: str):
        def measure(area: Area) -> Measure:
            rows = per_tract[per_tract["tract"].isin(set(area.tracts["geoid"]))]
            homes = pd.to_numeric(rows["block_units"], errors="coerce").sum()
            if homes <= 0:
                return Measure(None)
            return Measure(float(pd.to_numeric(rows[column], errors="coerce").sum() / homes))

        return measure

    fm = FactMaker(ctx, KEY, place, other)
    fm.add("major_stops", "Major transit stops inside the place", "stops", STOPS,
           "latest release", major_stops, compare=False,
           note="stop points as listed; one station can have several")  # fmt: skip
    fm.add("homes_near_transit", "Homes near major transit (rule D-07)", "share", BLOCKS, "2020",
           near("transit_units_d07"),
           note="2020 block homes within 1/2 mile of rail or 1/4 mile of a frequent bus stop; "
           "stop data from the National Transit Map and agency schedules")  # fmt: skip
    fm.add("homes_within_half_mile", "Homes within 1/2 mile of any major stop", "share", BLOCKS,
           "2020", near("transit_units_0_50mi"))  # fmt: skip

    count, share = fm.get("major_stops"), fm.get("homes_near_transit")
    parts = []
    if count and count.value is not None:
        parts.append(f"{place.name} has {count.text} major transit stop"
                     f"{'' if count.value == 1 else 's'}")  # fmt: skip
    if share and share.value is not None:
        parts.append(f"{with_words(share)} of its homes are near one")
    limits = (
        "Bus frequency is counted only where an agency publishes a schedule feed that could be "
        "read without a key; elsewhere bus stops are not counted as major, so access can be "
        "understated. Distances are straight lines, not walking routes. Homes are 2020 Census "
        "counts by block."
    )
    return finish(KEY, TITLE, sentence(parts), fm.facts, limits)
