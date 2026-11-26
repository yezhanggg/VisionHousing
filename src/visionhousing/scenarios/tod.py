"""Reform 3, transit-oriented density: mid-rise apartments near major transit stops, as a funnel.

Plain-language summary
----------------------
The reform allows more floor area and height near major transit stops
(a major stop is a rail station, or a bus stop with at least 64
weekday departures), and lets projects there build less parking. This module
estimates how many homes that adds in each census tract over 2027-2031, as a
likely range. All arrays are draws x tracts.

The funnel, one step after another:

1. **Eligible land** ``L``: block land within the chosen distance of a major
   stop (tract columns ``transit_land_m2_*``; ``0_25mi``, ``0_50mi``,
   ``0_75mi`` for any stop, or ``d07`` for the major-stop rule: 1/2 mile from rail,
   1/4 mile from frequent bus). The homes already there (``transit_units_*``)
   are kept for the site price.
2. **Developable land** = ``L x d``. ``d`` is the share of that land that
   could realistically become a mid-rise site (ledger, by area type:
   ``tod.developable_share_urban`` / ``_suburban`` / ``_rural``). Where the
   state already zones land for multi-family homes by right near transit
   (Massachusetts, Section 3A), the same share ``d`` of that land is its
   developable part ``D3`` (see step 4 and :class:`ExistingCapacity`).
3. **Feasible land** = developable land ``x p``. The shared mid-rise pro forma
   (:func:`visionhousing.proforma.midrise_proforma`) gives residual land value
   per apartment ``RLV``, with parking at the transit-rich still-built share of the old
   requirement (spaces per unit = ``parking.required_spaces_per_unit_multifamily
   x parking.share_still_built_transit_rich``), structured parking cost. At the
   upzoned intensity a site holds ``n`` apartments per square metre::

       FAR used = min(tod.upzoned_far, tod.upzoned_storeys x tod.lot_coverage)
       n        = FAR used x tod.net_floor_share / floor area per apartment

   so the site is worth ``RLV x n`` per square metre to a builder. Today the
   site costs its existing homes: ``V x h`` per square metre, with ``h`` the
   homes per square metre on the eligible land (never below
   ``tod.min_existing_homes_per_acre``) and ``V`` a home value. It pencils when
   ``V x h x (1 + tod.acquisition_premium) <= RLV x n``. ``p`` is the share of
   the tract's owner home values (ACS brackets) at or below that break-even
   value. The open top bracket is never counted as cheap enough (as for
   missing middle).
4. **Added capacity** = feasible land ``x (FAR used - q x tod.current_far_allowed)
   x net floor share / apartment size``, never below 0. Every apartment on a
   redeveloped site needs a permit, but a site would have been redeveloped at
   today's floor area anyway with chance ``q``
   (``tod.baseline_replacement_probability``); those permits are in the
   baseline, so they are subtracted. With ``q = 1`` only floor area above what
   today's rules allow counts.

   **Land a state already zones by right (Massachusetts Section 3A).** On the
   feasible part of that land, ``F3 = D3 x p``, the capacity counted is the
   additional legal capacity in common gross units on the same land::

       additional legal capacity = max(0, proposed - existing)
       proposed = F3 x n          (the FAR 2.5, five-storey prototype)
       existing = F3 x e          (e = 3A modeled capacity per m2 of 3A parcel land,
                                   atlas F_L_M__, split across tracts by area)

   so a tract's 3A land adds only what the prototype allows above what its
   district already allows; land with an identical envelope adds nothing.
   Section 3A's floor of 15 homes per acre is a minimum gross density for a
   whole district; the prototype's ~105 per acre is a building on one lot.
   They are different measures, which is why the tool compares the parcels'
   own modeled capacity, not the 15-per-acre floor. The older acre-share
   method (``tod.state_zoned_overlap_share`` x district acres taken out of
   ``L x d``) remains as variant ``tod_ma_acre_share``.
5. **Built** = capacity ``x min(1, h_y x sum of ramp(t))``, with ``h_y`` the
   yearly share of feasible sites permitted (``tod.redevelopment_hazard_annual``)
   and the ramp of ``tod.ramp_first_year_share`` and ``tod.ramp_years_to_full``
   (0 before ``tod.start_delay_years``).
6. **Absorbed**: new apartments must be rented, so the county's total is capped
   at ``tod.absorption_cap_share x`` the county's baseline 5+ unit permits over
   the five years (passed in by the engine, by draw). When a county is over the
   cap, every tract in it is scaled down by the same factor.
7. **Added units authorized** (the headline, :data:`HEADLINE_STAGE`) = absorbed
   ``x tod.net_new_share``. Apartments that would have been permitted elsewhere
   in the region anyway are moved, not added, and are not counted. This share
   is wide and is a headline caveat.
8. **Net added homes** (net change in the housing stock, :data:`NET_STOCK_STAGE`)
   = added units authorized ``- (1 - q) x`` homes removed. Homes removed = land
   redeveloped ``x`` the homes per square metre on the eligible land today
   (2020 blocks); land redeveloped = feasible land ``x min(1, h_y x sum of
   ramp) x`` the absorption factor (absorbed / built). A baseline
   redevelopment would have removed the same homes, so only ``1 - q`` of them
   are the reform's. Added units authorized are never below net added homes.

Years: the total is split into years by the ramp (``ramp(t) / sum of ramp``,
:attr:`TodResult.year_share`), the same in every tract of a draw.

Channels: A (new capacity near stops, steps 1-5) and B (the reduced parking
cost inside the pro forma, step 3).

What it does not do
-------------------
- No parcel data: "developable" is a share, and the site price uses the tract's
  home values, not assessed land values.
- Only rented apartments (income basis); condominiums are not priced.
- Fees enter through ``fees.fee_total_per_unit``; a fee cut in a package does
  not re-enter this pro forma (stated in the package notes).

Overlap hooks (for the engine)
------------------------------
- With missing middle: lots inside transit areas count under the denser reform,
  TOD. :func:`visionhousing.engine.packages.tod_middle_overlap` uses
  :attr:`TodResult.in_area_units_share` to take those lots out of missing middle.
- With the parking reform: the parking reduction near stops is already inside
  this pro forma, and the parking reform acts only on baseline production, so a
  unit is never counted twice (see ``docs/methods/tod.md``).

:func:`tod_funnel` is pure: tables and ledger in, result out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.features import brackets as br
from visionhousing.features.transit import DISTANCE_CHOICES_MILES, radius_label
from visionhousing.ledger.loader import Ledger
from visionhousing.ledger.sampling import sample
from visionhousing.ledger.scenarios import Scenario
from visionhousing.proforma import MIDRISE, midrise_proforma
from visionhousing.scenarios.adu import ramp_sum, year_shares

__all__ = [
    "ACCESSES",
    "DECOMPOSITION_CASES",
    "DISTANCES",
    "EXISTING_METHODS",
    "EXISTING_SOURCES",
    "HEADLINE_STAGE",
    "M2_PER_ACRE",
    "NET_STOCK_STAGE",
    "NO_MAJOR_STOPS",
    "SERVICES",
    "STAGES",
    "WALKS",
    "ExistingCapacity",
    "TodResult",
    "TodScope",
    "additional_legal_capacity",
    "decomposition_verdict",
    "distance_suffix",
    "existing_capacity",
    "gap_screen",
    "hourly_gap_lower_bound",
    "max_scheduled_gap",
    "network_columns",
    "network_suffix",
    "recoded_transit_columns",
    "scope_from_scenario",
    "service_flags",
    "stops_for_service",
    "tod_funnel",
    "walk_straight_columns",
    "zero_decomposition",
    "zoned_land_m2",
]

DISTANCES: tuple[Any, ...] = (*DISTANCE_CHOICES_MILES, "d07")
"""Distance choices: 1/4, 1/2 or 3/4 mile from any major stop, or the major-stop rule."""

DEFAULT_DISTANCE = "d07"
"""The major-stop rule: 1/2 mile from rail, 1/4 mile from frequent bus (straight line)."""

WALKS = ("straight", "network")
"""How land near a stop is measured: a straight-line circle (major stops),
or a network walkshed on mapped streets and paths around the stops that meet the
peak frequent-bus rule (:mod:`visionhousing.features.transit_walk`)."""

NETWORK_DISTANCE = 0.5
"""Default walk distance on the network: 1/2 mile (1/4 and 3/4 are variants)."""

WALK_COLUMNS = ("tract_acres", "network_acres", "fallback_acres")
"""Columns the network option reads from the pack table ``transit_walk_tract``."""

M2_PER_ACRE = 4046.8564224
"""Square metres in one acre (exact by definition)."""

SQ_M_PER_SQ_FT = 0.09290304
"""Square metres in one square foot (exact by definition)."""

STAGES = (
    "eligible",
    "developable",
    "feasible",
    "capacity",
    "built",
    "absorbed",
    "authorized",
    "net",
)
"""Funnel stages, in order. Land stages are in acres, the rest in units."""

HEADLINE_STAGE = "authorized"
"""The stage that is the headline: added units authorized by building permits."""

NET_STOCK_STAGE = "net"
"""The stage that is the net change in the housing stock (after demolition)."""

STAGE_NAMES = {
    "eligible": "land within {distance} of a major transit stop",
    "developable": "land that could become a mid-rise site",
    "feasible": "land where a mid-rise building pencils",
    "capacity": "added homes the upzoning allows on feasible land, beyond the baseline's",
    "built": "mid-rise homes permitted {years}, before the absorption cap",
    "absorbed": "mid-rise homes permitted {years}, within regional absorption",
    "authorized": "added units authorized by building permits {years} (new to the region)",
    "net": "net added homes after demolition {years} (new to the region)",
}

STAGE_UNITS = {
    "eligible": "acres",
    "developable": "acres",
    "feasible": "acres",
    "capacity": "units",
    "built": "units",
    "absorbed": "units",
    "authorized": "units",
    "net": "units",
}

AREA_PARAMS = {
    "urban": "tod.developable_share_urban",
    "suburban": "tod.developable_share_suburban",
    "rural": "tod.developable_share_rural",
}
"""Area type to the ledger id of its developable share. Unknown types use suburban."""

INTENSITY_PARAMS = (
    "tod.upzoned_far",
    "tod.upzoned_storeys",
    "tod.lot_coverage",
    "tod.net_floor_share",
)
CURRENT_FAR = "tod.current_far_allowed"
RENT_PARAMS = ("tod.new_unit_rent_premium", "tod.rent_ratio_2br")
PRICE_PARAMS = ("tod.acquisition_premium", "tod.min_existing_homes_per_acre")
PARKING_SHARE = "parking.share_still_built_transit_rich"
TAKEUP_PARAMS = (
    "tod.redevelopment_hazard_annual",
    "tod.ramp_first_year_share",
    "tod.ramp_years_to_full",
)
CAP_PARAM = "tod.absorption_cap_share"
DELAY_PARAM = "tod.start_delay_years"
REPLACE_PARAM = "tod.baseline_replacement_probability"
OPTIONAL_PARAMS = (DELAY_PARAM, REPLACE_PARAM)
"""Read when the ledger has them (delay 0 and replacement chance 1 otherwise)."""
NET_NEW_PARAM = "tod.net_new_share"
ZONED_PARAM = "tod.state_zoned_overlap_share"
"""Share of a by-right district's acres inside the walk distance (read when the pack has
districts)."""
ZONED_SOURCES = ("massgis_mbta3a_atlas",)
"""Registry ids behind the districts already zoned for multi-family by right."""
EXISTING_SOURCES = ("massgis_mbta3a_parcels", "massgis_mbta3a_atlas", "cb_2020_tract")
"""Registry ids behind the existing 3A modeled capacity by tract (``mbta_3a_capacity_tract``)."""
EXISTING_METHODS = ("capacity", "acre_share")
"""How land a state already zones by right is handled: ``capacity`` (the benchmark
where the pack has ``mbta_3a_capacity_tract``: additional legal capacity =
max(0, proposed - existing) in gross units on the same land) or ``acre_share``
(variant ``tod_ma_acre_share``: a share of district acres is taken out of the
developable land)."""
EXISTING_COLUMNS = (
    "geoid",
    "capacity_units",
    "capacity_parcel_units",
    "station_area_units",
    "parcel_acres",
)
"""Columns of ``mbta_3a_capacity_tract`` the TOD funnel reads."""
YEAR_PARAMS = ("baseline.first_year", "baseline.horizon_years")

PROFORMA_IDS = tuple(
    i.key
    for i in MIDRISE.inputs("income", with_acquisition=False)
    if i.is_parameter and i.key != MIDRISE.units.key
)
"""Ledger ids the mid-rise pro forma reads. Units per project is set to 1, so the
residual is per apartment (every line is proportional to units)."""

SPACES_ID = MIDRISE.spaces.key
UNIT_SIZE_ID = MIDRISE.unit_size.key
COST_FACTOR_COLUMN = "cost_factor"

DATA_SOURCES = {
    "eligible": ("tiger_2020_tabblock", "ntm_stops", "gtfs_agency_feeds"),
    "developable": ("acs5_summary_file",),
    "feasible": ("acs5_summary_file",),
    "absorbed": ("bps_county_annual",),
}
"""Registry ids of the data behind each stage (2020 blocks and stops; ACS area
type, rents and home values; Building Permits Survey 5+ unit baseline)."""

NETWORK_SOURCES = ("osm_overpass_walk", "gtfs_agency_feeds")
"""Registry ids behind the network walksheds (map and schedules)."""

_CHUNK_ROWS = 200_000


# ---------------------------------------------------------------- scope


def network_suffix(distance: Any) -> str:
    """Tract-column suffix of a network walk distance: ``0.5`` -> ``"net_0_50mi"``.

    Raises:
        ValueError: Not 1/4, 1/2 or 3/4 mile (``d07`` is a straight-line rule only).
    """
    suffix = distance_suffix(distance)
    if suffix == "d07":
        raise ValueError(
            "the major-stop rule is straight-line only; use 0.25, 0.5 or 0.75 on the network"
        )
    return f"net_{suffix}"


def network_columns(tracts: pd.DataFrame, walk: pd.DataFrame | None) -> pd.DataFrame:
    """Add network-walkshed transit columns to the tract table.

    For each distance (1/4, 1/2, 3/4 mile) and tract, from the pack table
    ``transit_walk_tract`` (``network_acres_*``, ``fallback_acres_*``,
    ``tract_acres``):

    - land share = tract land area / tract outline area (the outline includes
      inland water), at most 1;
    - **eligible land** ``transit_land_m2_net_<d>`` = network acres x
      4,046.86 m2 per acre x land share;
    - **homes on it** ``transit_units_net_<d>`` = eligible land x homes per m2
      of the straight-line land at the same distance (2020 blocks), or the
      tract's own homes per m2 of land where that is blank;
    - ``transit_units_share_net_<d>`` = those homes / the tract's homes (at most 1);
    - ``transit_fallback_m2_net_<d>`` = fallback acres x the same conversion:
      the part of the eligible land that is a straight-line circle because the
      street network was missing or fragmentary there.

    Tracts not in the walk table get zero. ``walk=None`` returns the table unchanged.
    """
    if walk is None:
        return tracts
    out = tracts.copy()
    w = walk.copy()
    w["geoid"] = w["geoid"].astype(str)
    w = w.set_index("geoid")
    geoids = out["geoid"].astype(str)
    land = out["land_area_m2"].to_numpy(dtype=float) if "land_area_m2" in out else None
    homes_all = out["housing_units"].to_numpy(dtype=float) if "housing_units" in out else None
    outline = geoids.map(w["tract_acres"]).to_numpy(dtype=float) * M2_PER_ACRE
    with np.errstate(divide="ignore", invalid="ignore"):
        land_share = (
            np.clip(np.where(outline > 0, land / outline, 1.0), 0.0, 1.0)
            if land is not None
            else np.ones(len(out))
        )
        own_density = (
            np.where(land > 0, homes_all / land, 0.0)
            if land is not None and homes_all is not None
            else np.zeros(len(out))
        )
    land_share = np.nan_to_num(land_share, nan=1.0)
    for miles in DISTANCE_CHOICES_MILES:
        lab = radius_label(miles)
        acres = np.nan_to_num(geoids.map(w.get(f"network_acres_{lab}")).to_numpy(dtype=float))
        fall = np.nan_to_num(geoids.map(w.get(f"fallback_acres_{lab}")).to_numpy(dtype=float))
        net_m2 = np.clip(acres, 0, None) * M2_PER_ACRE * land_share
        s_land = np.nan_to_num(
            out.get(f"transit_land_m2_{lab}", pd.Series(0.0, index=out.index)).to_numpy(dtype=float)
        )
        s_units = np.nan_to_num(
            out.get(f"transit_units_{lab}", pd.Series(0.0, index=out.index)).to_numpy(dtype=float)
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            density = np.where(s_land > 0, s_units / np.where(s_land > 0, s_land, 1.0), own_density)
        density = np.nan_to_num(density)
        homes = net_m2 * density
        with np.errstate(divide="ignore", invalid="ignore"):
            share = (
                np.where(homes_all > 0, homes / np.where(homes_all > 0, homes_all, 1.0), 0.0)
                if homes_all is not None
                else np.zeros(len(out))
            )
        out[f"transit_land_m2_net_{lab}"] = net_m2
        out[f"transit_units_net_{lab}"] = homes
        out[f"transit_units_share_net_{lab}"] = np.clip(np.nan_to_num(share), 0.0, 1.0)
        out[f"transit_fallback_m2_net_{lab}"] = np.clip(fall, 0, None) * M2_PER_ACRE * land_share
    return out


def distance_suffix(distance: Any) -> str:
    """Tract-column suffix for a distance choice: ``0.5`` -> ``"0_50mi"``, ``"d07"`` -> ``"d07"``.

    Raises:
        ValueError: Not one of :data:`DISTANCES`.
    """
    if isinstance(distance, str) and distance.lower() == "d07":
        return "d07"
    try:
        miles = float(distance)
    except (TypeError, ValueError):
        raise ValueError(f"TOD distance must be one of {DISTANCES}, got {distance!r}") from None
    if not any(abs(miles - m) < 1e-9 for m in DISTANCE_CHOICES_MILES):
        raise ValueError(f"TOD distance must be one of {DISTANCES}, got {distance!r}")
    return radius_label(miles)


def distance_words(distance: Any, walk: str = "straight") -> str:
    """Plain words for a distance choice."""
    suffix = distance_suffix(distance)
    if suffix == "d07":
        return "1/2 mile (rail) or 1/4 mile (frequent bus)"
    words = {"0_25mi": "1/4 mile", "0_50mi": "1/2 mile", "0_75mi": "3/4 mile"}
    if walk == "network":
        return f"a {words[suffix]} walk (peak frequent-bus rule)"
    return words[suffix]


@dataclass(frozen=True)
class TodScope:
    """What a TOD scenario reaches.

    Attributes:
        name: Scenario id.
        distance: ``0.25``, ``0.5``, ``0.75`` (miles from any major stop) or
            ``"d07"`` (the major-stop rule).
    """

    name: str
    distance: Any = DEFAULT_DISTANCE
    walk: str = "straight"

    def __post_init__(self) -> None:
        """Check the distance and the walk measure."""
        if self.walk not in WALKS:
            raise ValueError(f"TOD walk must be one of {WALKS}, got {self.walk!r}")
        if self.walk == "network":
            network_suffix(self.distance)
        else:
            distance_suffix(self.distance)

    @property
    def suffix(self) -> str:
        """Tract-column suffix of the distance (``net_...`` on the network)."""
        if self.walk == "network":
            return network_suffix(self.distance)
        return distance_suffix(self.distance)


def scope_from_scenario(
    scenario_id: str, scenario: Scenario, distance: Any = DEFAULT_DISTANCE, walk: str = "straight"
) -> TodScope:
    """Turn a scenario from ``scenarios.yaml`` into a TOD scope.

    Raises:
        ValueError: The scenario does not turn on the TOD reform, or leaves out
            places (not supported yet), or the distance is not a choice.
    """
    if "tod" not in scenario.reforms:
        raise ValueError(f"scenario '{scenario_id}' does not turn on the TOD reform")
    if scenario.excluded_places:
        raise ValueError(f"scenario '{scenario_id}': excluded places are not supported yet")
    return TodScope(name=scenario_id, distance=distance, walk=walk)


# ---------------------------------------------------------------- the result


@dataclass(frozen=True)
class TodResult:
    """What :func:`tod_funnel` returns.

    Attributes:
        scope: The scenario's scope.
        geoids: Tract codes.
        counties: County code per tract.
        stages: Stage name to draws x tracts arrays (see :data:`STAGES`).
        eligible_homes: Existing homes within the distance, per tract (2020 blocks).
        in_area_units_share: Share of each tract's homes within the distance
            (the missing-middle overlap hook).
        feasible_share: ``p`` per draw and tract.
        residual_per_unit: Median over draws of ``RLV`` per apartment, per tract ($).
        capped_share: Share of draws in which each county hit the absorption cap.
        years: Calendar years counted.
        net_by_year: State added units authorized per draw and year.
        records: State-total records, one per stage, plus ``eligible_homes``,
            ``per_100k``, ``removed`` and ``baseline_on_sites``.
        seed: Seed used.
        capped: Whether an absorption cap was applied.
        year_share: Each year's share of the five-year total per draw, ``(draws, years)``.
        removed: Existing homes demolished on redeveloped land, per draw and tract.
        baseline_on_sites: Baseline authorizations on the redeveloped land
            (q x the apartments today's floor area allows there), per draw and tract.
        zoned: Land already zoned for multi-family by right taken out of the
            developable land, acres per draw and tract (``None`` without districts).
        existing: Land already zoned by right and its modeled capacity, per
            tract (:class:`ExistingCapacity`; ``None`` when not used).
        capacity_by_right: Of ``capacity``, the additional legal capacity on
            land already zoned by right, draws x tracts (``None`` when not used).
    """

    scope: TodScope
    geoids: np.ndarray
    counties: np.ndarray
    stages: dict[str, np.ndarray]
    eligible_homes: np.ndarray
    in_area_units_share: np.ndarray
    feasible_share: np.ndarray
    residual_per_unit: np.ndarray
    capped_share: dict[str, float]
    years: tuple[int, ...]
    net_by_year: np.ndarray
    records: dict[str, Record]
    seed: int
    capped: bool
    year_share: np.ndarray | None = None
    removed: np.ndarray | None = None
    baseline_on_sites: np.ndarray | None = None
    zoned: np.ndarray | None = None
    existing: ExistingCapacity | None = None
    capacity_by_right: np.ndarray | None = None

    def funnel(self) -> list[Record]:
        """Return the state funnel, one record per stage in :data:`STAGES` order."""
        return [self.records[s] for s in STAGES]

    def tract_table(self) -> pd.DataFrame:
        """Return P10 / P50 / P90 of every stage for every tract."""
        out = pd.DataFrame({"geoid": self.geoids, "county": self.counties})
        for stage in STAGES:
            p10, p50, p90 = np.percentile(self.stages[stage], [10, 50, 90], axis=0)
            out[f"{stage}_p10"], out[f"{stage}_p50"], out[f"{stage}_p90"] = p10, p50, p90
        return out


_EXPRESSIONS = {
    "eligible": "eligible land = block land within {distance} of a major stop",
    "developable": "developable land = eligible land x developable share for the area type",
    "feasible": (
        "feasible land = developable land x p, where p = share of owner home values V with "
        "V x homes per m2 x (1 + acquisition premium) <= mid-rise residual land value per "
        "apartment x apartments per m2 at the upzoned FAR (parking at the transit-rich "
        "still-built share)"
    ),
    "capacity": (
        "added capacity = feasible land x max(min(upzoned FAR, storeys x coverage) - baseline "
        "replacement chance q x today's FAR, 0) x net floor share / apartment floor area"
    ),
    "built": (
        "built = added capacity x min(1, yearly share permitted x sum over {years} of ramp(t))"
    ),
    "absorbed": (
        "absorbed = built x min(1, absorption cap share x county baseline 5+ unit permits "
        "{years} / county built)"
    ),
    "authorized": (
        "added units authorized = absorbed x net-new share (units moved within the region "
        "are not counted)"
    ),
    "net": (
        "net added homes = added units authorized - (1 - q) x homes removed, homes removed = "
        "land redeveloped x today's homes per m2 on the eligible land"
    ),
}


def _period(years: Sequence[int]) -> str:
    return f"{years[0]}-{years[-1]}" if years else ""


def _column(tracts: pd.DataFrame, name: str) -> np.ndarray:
    if name not in tracts:
        raise KeyError(f"tract table needs column '{name}'")
    return tracts[name].to_numpy(dtype=float)


def _stage_ids(capped: bool, zoned: bool = False) -> dict[str, list[str]]:
    eligible: list[str] = []
    developable = [*AREA_PARAMS.values(), *([ZONED_PARAM] if zoned else [])]
    feasible = [
        *developable,
        *INTENSITY_PARAMS,
        *RENT_PARAMS,
        *PRICE_PARAMS,
        PARKING_SHARE,
        *PROFORMA_IDS,
    ]
    capacity = [*feasible, CURRENT_FAR, REPLACE_PARAM]
    built = [*capacity, *TAKEUP_PARAMS, DELAY_PARAM, *YEAR_PARAMS]
    absorbed = [*built, *([CAP_PARAM] if capped else [])]
    authorized = [*absorbed, NET_NEW_PARAM]
    out = {
        "eligible": eligible,
        "developable": developable,
        "feasible": feasible,
        "capacity": capacity,
        "built": built,
        "absorbed": absorbed,
        "authorized": authorized,
        "net": authorized,
    }
    return {k: sorted(set(v)) for k, v in out.items()}


def zoned_land_m2(tracts: pd.DataFrame, districts: pd.DataFrame | None) -> np.ndarray | None:
    """Acres already zoned for multi-family by right inside station areas, in m2 per tract.

    Districts with ``capacity_station_area > 0`` count (the atlas puts part of
    their capacity inside a station area); their ``acres`` are summed by centre
    ``tract``. ``None`` when there is no district table.
    """
    if districts is None or not {"tract", "acres", "capacity_station_area"} <= set(districts):
        return None
    d = districts.assign(
        acres=pd.to_numeric(districts["acres"], errors="coerce").fillna(0.0),
        station=pd.to_numeric(districts["capacity_station_area"], errors="coerce").fillna(0.0),
        tract=districts["tract"].astype(str),
    )
    by = d[(d["station"] > 0) & (d["acres"] > 0)].groupby("tract")["acres"].sum()
    geoids = tracts["geoid"].astype(str)
    return geoids.map(by).fillna(0.0).to_numpy(dtype=float) * M2_PER_ACRE


def additional_legal_capacity(proposed: Any, existing: Any) -> np.ndarray:
    """Additional legal capacity on the same land: ``max(0, proposed - existing)``.

    Both in the same gross units (homes) on the same land and extent, cell by
    cell. A cell whose envelope is unchanged gives 0; a district allowing 30
    homes that the reform raises to 50 gives 20. The maximum is taken per cell
    before any sum, so a cell where today's zoning already allows more never
    offsets another cell.
    """
    return np.maximum(np.asarray(proposed, dtype=float) - np.asarray(existing, dtype=float), 0.0)


@dataclass(frozen=True)
class ExistingCapacity:
    """Land a state already zones by right near transit, and what it allows, per tract.

    Built by :func:`existing_capacity` from the pack table
    ``mbta_3a_capacity_tract`` (Massachusetts, Section 3A). All arrays are per
    tract, in the order of the tract table.

    Attributes:
        land_m2: 3A parcel land inside the run's distance (m2), never more than
            the tract's eligible land.
        units: Existing 3A modeled capacity on that land (gross homes, atlas
            ``F_L_M__``), scaled with the land when it is capped.
        measured: The tract has a row in the 3A capacity table. A tract without
            one holds no compliant 3A district in the atlas; its existing 3A
            capacity is taken as 0, so the benchmark's base-zoning rule
            (``q x today's FAR``) applies there, and its land is reported as not
            resolved (the pack has no tract-to-town table, so the town's
            ``mbta_3a_status`` cannot be attached to a tract).
        table_units: The table's capacity in the tract (all of it, any distance).
        table_station_units: Of it, on parcels in a 3A half-mile station area.
    """

    land_m2: np.ndarray
    units: np.ndarray
    measured: np.ndarray
    table_units: np.ndarray
    table_station_units: np.ndarray

    @property
    def per_m2(self) -> np.ndarray:
        """Existing capacity per m2 of 3A land (homes per m2), 0 where there is none."""
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(
                self.land_m2 > 0, self.units / np.where(self.land_m2 > 0, self.land_m2, 1.0), 0.0
            )


def existing_capacity(
    tracts: pd.DataFrame, table: pd.DataFrame | None, suffix: str
) -> ExistingCapacity | None:
    """Existing 3A capacity and land inside the run's distance, per tract.

    From ``mbta_3a_capacity_tract`` (one row per tract; capacity and acres
    already split across tracts by area, never by centre). The table splits
    each tract's capacity into a station-area part (parcels the compliance
    model places in a half-mile station area, ``station_area_units``) and the
    rest; the pack's walk tables hold acres, not shapes, so neither can be cut
    by the run's own distance. Per tract, with ``L_s`` the eligible land at the
    run's distance, ``L_h`` the straight-line half-mile land and ``A`` the
    tract's land area::

        land share in station areas = station_area_units / capacity_parcel_units
        inside share  = min(1, L_s / L_h)                (station-area part)
        outside share = max(0, L_s - L_h) / (A - L_h)     (the rest, by area)
        land  = parcel acres x (station share x inside + (1 - station share) x outside)
        units = station units x inside + other units x outside

    capped at ``L_s`` (land and units scaled together, so the density holds).
    The station-area land share uses the capacity share (3A density taken
    as even within a tract): an approximation, labelled as such.

    Returns:
        The per-tract arrays, or ``None`` without a table.
    """
    if table is None or not {"geoid", "capacity_units", "parcel_acres"} <= set(table):
        return None
    t = table.assign(geoid=table["geoid"].astype(str)).drop_duplicates("geoid").set_index("geoid")
    geo = tracts["geoid"].astype(str)

    def col(name: str) -> np.ndarray:
        if name not in t:
            return np.zeros(len(geo))
        vals = pd.to_numeric(t[name], errors="coerce")
        return np.clip(np.nan_to_num(geo.map(vals).to_numpy(dtype=float)), 0.0, None)

    measured = geo.isin(t.index).to_numpy()
    total = col("capacity_units")
    parcel = col("capacity_parcel_units") if "capacity_parcel_units" in t else total
    station = np.minimum(col("station_area_units"), total)
    acres = col("parcel_acres")
    with np.errstate(divide="ignore", invalid="ignore"):
        st_share = np.where(
            parcel > 0, np.clip(station / np.where(parcel > 0, parcel, 1.0), 0, 1), 0.0
        )
    land_s = np.clip(np.nan_to_num(_column(tracts, f"transit_land_m2_{suffix}")), 0, None)
    half = f"transit_land_m2_{distance_suffix(0.5)}"
    land_h = np.clip(np.nan_to_num(_column(tracts, half)), 0, None) if half in tracts else land_s
    area = (
        np.clip(np.nan_to_num(_column(tracts, "land_area_m2")), 0, None)
        if "land_area_m2" in tracts
        else np.maximum(land_s, land_h)
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        inside = np.where(
            land_h > 0, np.clip(land_s / np.where(land_h > 0, land_h, 1.0), 0, 1), 0.0
        )
        room = area - land_h
        outside = np.where(
            room > 0, np.clip((land_s - land_h) / np.where(room > 0, room, 1.0), 0, 1), 0.0
        )
    land = acres * M2_PER_ACRE * (st_share * inside + (1.0 - st_share) * outside)
    units = station * inside + (total - station) * outside
    with np.errstate(divide="ignore", invalid="ignore"):
        cap = np.where(land > land_s, land_s / np.where(land > 0, land, 1.0), 1.0)
    return ExistingCapacity(
        land_m2=land * cap,
        units=units * cap,
        measured=measured,
        table_units=total,
        table_station_units=station,
    )


def _needed_ids(ledger: Ledger) -> list[str]:
    ids = [
        *AREA_PARAMS.values(),
        *INTENSITY_PARAMS,
        CURRENT_FAR,
        *RENT_PARAMS,
        *PRICE_PARAMS,
        PARKING_SHARE,
        *PROFORMA_IDS,
        *TAKEUP_PARAMS,
        CAP_PARAM,
        NET_NEW_PARAM,
    ]
    for pid in [*ids, *YEAR_PARAMS]:
        ledger[pid]  # raises a clear LedgerError if missing
    ids += [pid for pid in OPTIONAL_PARAMS if pid in ledger]
    return list(dict.fromkeys(ids))


# ---------------------------------------------------------------- the steps


def _developable_share(tracts: pd.DataFrame, col: Mapping[str, np.ndarray]) -> np.ndarray:
    """Developable share per draw and tract, by area type."""
    areas = (
        tracts["area_type"].astype(str).str.lower().to_numpy() if "area_type" in tracts else None
    )
    n = len(tracts)
    out = np.broadcast_to(col[AREA_PARAMS["suburban"]], (col[CAP_PARAM].shape[0], n)).copy()
    if areas is None:
        return out
    for area, pid in AREA_PARAMS.items():
        mask = areas == area
        out[:, mask] = col[pid]
    return out


def _new_unit_rent(tracts: pd.DataFrame, col: Mapping[str, np.ndarray]) -> np.ndarray:
    """Monthly rent of a new mid-rise apartment, draws x tracts (blank rent gives 0)."""
    b2 = _column(tracts, "median_rent_b2")[None, :]
    gross = _column(tracts, "median_gross_rent")[None, :]
    base = np.where(np.isfinite(b2) & (b2 > 0), b2, gross * col["tod.rent_ratio_2br"])
    return np.nan_to_num(base * col["tod.new_unit_rent_premium"], nan=0.0)


def _residual_per_unit(
    tracts: pd.DataFrame, col: Mapping[str, np.ndarray], rent: np.ndarray, draws: int
) -> np.ndarray:
    """Mid-rise residual land value per apartment, draws x tracts ($)."""
    n = len(tracts)
    cost_factor = (
        _column(tracts, COST_FACTOR_COLUMN)[None, :] if COST_FACTOR_COLUMN in tracts else 1.0
    )
    out = np.empty((draws, n))
    step = max(1, _CHUNK_ROWS // max(n, 1))
    for start in range(0, draws, step):
        stop = min(start + step, draws)
        params = {pid: col[pid][start:stop] for pid in PROFORMA_IDS}
        params[MIDRISE.units.key] = 1.0
        params[SPACES_ID] = col[SPACES_ID][start:stop] * col[PARKING_SHARE][start:stop]
        context = {"rent_monthly": rent[start:stop], "cost_factor": cost_factor}
        rlv = midrise_proforma(params, context).lines["residual_land_value"]
        out[start:stop] = np.broadcast_to(rlv, (stop - start, n))
    return out


def _share_below(tracts: pd.DataFrame, threshold: np.ndarray, rows: int) -> np.ndarray:
    """Share of each tract's owner home values at or below ``threshold`` (draws x tracts)."""
    n = len(tracts)
    counts = tracts[list(br.VALUE.columns)].to_numpy(dtype=float)
    top_low = float(br.VALUE.lows[-1])
    median_value = _column(tracts, "median_home_value")
    t = np.minimum(threshold, top_low)
    out = np.empty((rows, n))
    step = max(1, _CHUNK_ROWS // max(n, 1))
    for start in range(0, rows, step):
        stop = min(start + step, rows)
        part = t[start:stop]
        p = np.where(part > 0, br.share_below_grid(counts, br.VALUE, part), 0.0)
        fallback = np.where(
            np.isfinite(median_value)[None, :] & (median_value[None, :] <= part) & (part > 0),
            1.0,
            0.0,
        )
        out[start:stop] = np.where(np.isnan(p), fallback, p)
    return out


def _absorption(
    built: np.ndarray,
    counties: np.ndarray,
    cap_share: np.ndarray,
    mf_baseline: pd.Series | Mapping[str, float] | None,
    mf_scale: np.ndarray | None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Scale built units down where a county is over its absorption cap."""
    if mf_baseline is None:
        return built.copy(), {}
    base = pd.Series(mf_baseline, dtype=float)
    base.index = base.index.astype(str)
    order = sorted(set(counties))
    out = built.copy()
    hit: dict[str, float] = {}
    for i, county in enumerate(order):
        mask = counties == county
        total = built[:, mask].sum(axis=1)  # (draws,)
        scale = 1.0 if mf_scale is None else np.asarray(mf_scale, dtype=float)[i]
        b5 = float(base.get(county, 0.0))
        cap = cap_share[:, 0] * max(b5, 0.0) * scale
        with np.errstate(divide="ignore", invalid="ignore"):
            factor = np.where(total > cap, cap / total, 1.0)
        factor = np.nan_to_num(factor, nan=0.0)
        out[:, mask] = built[:, mask] * factor[:, None]
        hit[county] = float(np.mean(total > cap + 1e-12))
    return out, hit


# ---------------------------------------------------------------- the funnel


def tod_funnel(
    tracts: pd.DataFrame,
    ledger: Ledger,
    scope: TodScope,
    *,
    mf_baseline: pd.Series | Mapping[str, float] | None = None,
    mf_scale: np.ndarray | None = None,
    draws: int = 2000,
    seed: int = 0,
    zoned_m2: np.ndarray | None = None,
    extra_cost_per_unit: np.ndarray | None = None,
    land_factor: np.ndarray | None = None,
    existing: ExistingCapacity | None = None,
) -> TodResult:
    """Run the TOD funnel for every tract and every Monte Carlo draw.

    Args:
        tracts: Tract table in the state-pack schema: ``geoid``, ``county``,
            ``area_type``, ``population``, ``median_rent_b2``,
            ``median_gross_rent``, ``median_home_value``, the ``value_*``
            brackets, and the transit columns for the scope's distance
            (``transit_land_m2_<s>``, ``transit_units_<s>``,
            ``transit_units_share_<s>``). Optional: ``cost_factor``.
        ledger: The ledger, with the scenario's overrides already applied.
        scope: What the scenario reaches (:func:`scope_from_scenario`).
        mf_baseline: Baseline 5+ unit permits over the five years, by county
            code (the absorption cap's scale). ``None`` applies no cap.
        mf_scale: Optional baseline multiplier per county and draw, shape
            ``(counties, draws)``, counties in sorted order of the tract table's
            county codes (1 = baseline P50).
        draws: Number of Monte Carlo draws.
        seed: Seed; the same seed gives the same result.
        zoned_m2: Land already zoned for multi-family by right inside station
            areas, m2 per tract (:func:`zoned_land_m2`), or ``None``. Taken out
            of the developable land before the pencil test.
        extra_cost_per_unit: Optional cost per apartment a builder must also pay
            ($, per tract or draws x tracts), for example a water and sewer
            capacity charge (``scenarios.tod_infrastructure``). It comes off the
            residual land value dollar for dollar. ``None`` (the benchmark) adds nothing.
        land_factor: Optional share of each tract's developable land that can be
            used at all (per tract, 0-1), for example 0 where a documented sewer
            moratorium applies. ``None`` (the benchmark) keeps all of it.
        existing: Land already zoned by right and its modeled capacity
            (:func:`existing_capacity`), or ``None``. On that land the funnel
            counts the additional legal capacity, max(0, proposed - existing),
            in place of the base-zoning rule. Not used together with ``zoned_m2``.

    Returns:
        A :class:`TodResult`.

    Raises:
        KeyError: A tract column is missing.
        LedgerError: A ledger parameter is missing.
    """
    if existing is not None and zoned_m2 is not None:
        raise ValueError("pass existing capacity or zoned acres, not both")
    ids = _needed_ids(ledger)
    zoned = zoned_m2 is not None and ZONED_PARAM in ledger
    if zoned:
        ids.append(ZONED_PARAM)
    v = sample(ledger, n=draws, seed=seed, ids=ids)
    col = {k: a[:, None] for k, a in v.items()}
    first_year = int(ledger[YEAR_PARAMS[0]].central)
    horizon = int(ledger[YEAR_PARAMS[1]].central)
    years = tuple(range(first_year, first_year + horizon))
    counties = tracts["county"].astype(str).to_numpy()
    s = scope.suffix

    # 1. Eligible land and the homes on it.
    land_m2 = np.clip(np.nan_to_num(_column(tracts, f"transit_land_m2_{s}")), 0, None)
    homes = np.clip(np.nan_to_num(_column(tracts, f"transit_units_{s}")), 0, None)
    in_share = np.clip(np.nan_to_num(_column(tracts, f"transit_units_share_{s}")), 0, 1)
    eligible_m2 = np.broadcast_to(land_m2[None, :], (draws, len(tracts)))

    # 2. Developable land, less land the state already zones for multi-family by right.
    dev_share = _developable_share(tracts, col)
    developable_m2 = eligible_m2 * dev_share
    zoned_in = None
    dev3 = None  # developable land already zoned by right (3A), draws x tracts
    if existing is not None:
        z3 = np.minimum(np.clip(np.asarray(existing.land_m2, dtype=float), 0, None), land_m2)
        dev3 = z3[None, :] * dev_share
        e_per_m2 = existing.per_m2[None, :]
    if zoned:
        z = np.clip(np.nan_to_num(np.asarray(zoned_m2, dtype=float)), 0, None)[None, :]
        zoned_in = np.minimum(z * col[ZONED_PARAM], land_m2[None, :])
        developable_m2 = np.maximum(developable_m2 - zoned_in, 0.0)
    if land_factor is not None:
        developable_m2 = developable_m2 * np.clip(np.asarray(land_factor, dtype=float), 0, 1)
        if dev3 is not None:
            dev3 = dev3 * np.clip(np.asarray(land_factor, dtype=float), 0, 1)

    # 3. Feasible land.
    far_used = np.minimum(
        col["tod.upzoned_far"], col["tod.upzoned_storeys"] * col["tod.lot_coverage"]
    )
    unit_m2 = col[UNIT_SIZE_ID] * SQ_M_PER_SQ_FT
    units_per_m2 = far_used * col["tod.net_floor_share"] / unit_m2  # (draws, 1)
    rent = _new_unit_rent(tracts, col)
    rlv_unit = _residual_per_unit(tracts, col, rent, draws)
    if extra_cost_per_unit is not None:
        rlv_unit = rlv_unit - np.asarray(extra_cost_per_unit, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        density = np.where(land_m2 > 0, homes / land_m2, 0.0)[None, :]
    density = np.maximum(density, col["tod.min_existing_homes_per_acre"] / M2_PER_ACRE)
    break_even = rlv_unit * units_per_m2 / (density * (1.0 + col["tod.acquisition_premium"]))
    p = np.where(units_per_m2 > 0, _share_below(tracts, break_even, draws), 0.0)
    feasible_m2 = developable_m2 * p

    # 4. Added capacity beyond what the baseline would authorize on the same sites.
    q = col.get(REPLACE_PARAM, np.ones((draws, 1)))
    gain_far = np.maximum(far_used - q * col[CURRENT_FAR], 0.0)
    if dev3 is None:
        feasible3 = np.zeros_like(feasible_m2)
        gain3 = np.zeros_like(feasible_m2)
    else:
        # Land already zoned by right: additional legal capacity on the same land,
        # max(0, proposed - existing) per m2, in gross homes.
        feasible3 = dev3 * p
        gain3 = additional_legal_capacity(units_per_m2, e_per_m2)
        gain3 = np.broadcast_to(gain3, feasible_m2.shape)
    feasible0 = feasible_m2 - feasible3
    capacity = feasible0 * gain_far * col["tod.net_floor_share"] / unit_m2
    if dev3 is not None:
        capacity = capacity + feasible3 * gain3

    # 5. Take-up with the ramp.
    ramp = ramp_sum(
        v["tod.ramp_first_year_share"],
        v["tod.ramp_years_to_full"],
        horizon,
        v.get(DELAY_PARAM, 0.0),
    )
    over_period = np.minimum(col["tod.redevelopment_hazard_annual"] * ramp.sum(axis=1)[:, None], 1)
    built = capacity * over_period

    # 6. Regional absorption cap.
    absorbed, hit = _absorption(built, counties, col[CAP_PARAM], mf_baseline, mf_scale)

    # 7. Added authorizations, new to the region.
    authorized = absorbed * col[NET_NEW_PARAM]

    # 8. Net change in the housing stock: the reform's share of the homes removed comes off.
    with np.errstate(divide="ignore", invalid="ignore"):
        kept = np.where(built > 0, absorbed / np.where(built > 0, built, 1.0), 0.0)
        homes_per_m2 = np.where(land_m2 > 0, homes / np.where(land_m2 > 0, land_m2, 1.0), 0.0)
    land0 = np.where(gain_far > 0, feasible0 * over_period * kept, 0.0)
    land3 = np.where(gain3 > 0, feasible3 * over_period * kept, 0.0)
    land_redeveloped = land0 + land3 if dev3 is not None else land0
    removed = land_redeveloped * homes_per_m2[None, :]
    baseline_on_sites = land0 * q * col[CURRENT_FAR] * col["tod.net_floor_share"] / unit_m2
    if dev3 is not None:
        baseline_on_sites = baseline_on_sites + land3 * q * np.minimum(e_per_m2, units_per_m2)
    net = authorized - (1.0 - q) * removed
    year_share = year_shares(ramp)
    net_by_year = authorized.sum(axis=1)[:, None] * year_share

    stages = {
        "eligible": eligible_m2 / M2_PER_ACRE,
        "developable": developable_m2 / M2_PER_ACRE,
        "feasible": feasible_m2 / M2_PER_ACRE,
        "capacity": capacity,
        "built": built,
        "absorbed": absorbed,
        "authorized": authorized,
        "net": net,
    }
    population = float(np.nansum(_column(tracts, "population")))
    records = _state_records(
        stages,
        ledger,
        scope,
        mf_baseline is not None,
        population,
        years,
        homes,
        zoned=zoned,
        existing=dev3 is not None,
    )
    if dev3 is not None and existing is not None:
        records.update(
            _existing_records(
                existing, dev3, units_per_m2, e_per_m2, land_m2, records["capacity"], scope
            )
        )
    if zoned_in is not None:
        records["already_zoned"] = rec.from_draws(
            f"land already zoned for multi-family by right (MA Section 3A) within "
            f"{distance_words(scope.distance, scope.walk)} ({scope.name})",
            zoned_in.sum(axis=1) / M2_PER_ACRE,
            "acres",
            expression=(
                "the sum over tracts of min(acres of Section 3A districts inside station areas "
                "(capacity_station_area > 0), summed by centre tract, x overlap share, eligible "
                "land); taken out of the developable land before the pencil test"
            ),
            inputs=[rec.from_parameter(ZONED_PARAM, ledger[ZONED_PARAM])],
            source_ids=ZONED_SOURCES,
            tier=0,
        )
    base = records["absorbed"]
    period = _period(years)
    records["removed"] = rec.from_draws(
        f"existing homes demolished on redeveloped land {period} ({scope.name})",
        removed.sum(axis=1),
        "units",
        expression=(
            "the sum over tracts of land redeveloped (feasible land x min(1, yearly share "
            "permitted x sum of ramp) x absorbed / built) x 2020 homes per m2 on the eligible land"
        ),
        assumption_ids=base.assumption_ids,
        source_ids=base.source_ids,
        tier=0,
    )
    records["baseline_on_sites"] = rec.from_draws(
        f"baseline authorizations on the redeveloped land {period} ({scope.name})",
        baseline_on_sites.sum(axis=1),
        "units",
        expression=(
            "the sum over tracts of land redeveloped x baseline replacement chance q x today's "
            "FAR x net floor share / apartment floor area"
        ),
        assumption_ids=base.assumption_ids,
        source_ids=base.source_ids,
        tier=0,
    )
    fb_col = f"transit_fallback_m2_{s}"
    if scope.walk == "network" and fb_col in tracts:
        fallback = np.clip(np.nan_to_num(tracts[fb_col].to_numpy(dtype=float)), 0, None)
        total = float(land_m2.sum())
        records["walk_fallback_share"] = rec.quantity(
            f"share of the eligible land measured by straight-line fallback ({scope.name})",
            float(fallback.sum()) / total if total > 0 else 0.0,
            "share",
            formula=(
                "straight-line fallback land "
                f"{rec.format_number(float(fallback.sum()) / M2_PER_ACRE, 'acres')} "
                f"/ eligible land {rec.format_number(total / M2_PER_ACRE, 'acres')} (stops with no "
                "usable street network keep their straight-line circle)"
            ),
            source_ids=NETWORK_SOURCES,
            tier=0,
        )
    return TodResult(
        scope=scope,
        geoids=tracts["geoid"].astype(str).to_numpy(),
        counties=counties,
        stages=stages,
        eligible_homes=homes,
        in_area_units_share=in_share,
        feasible_share=p,
        residual_per_unit=np.median(rlv_unit, axis=0),
        capped_share=hit,
        years=years,
        net_by_year=net_by_year,
        records=records,
        seed=seed,
        capped=mf_baseline is not None,
        year_share=year_share,
        removed=removed,
        baseline_on_sites=baseline_on_sites,
        zoned=None if zoned_in is None else zoned_in / M2_PER_ACRE,
        existing=existing if dev3 is not None else None,
        capacity_by_right=None if dev3 is None else feasible3 * gain3,
    )


def _state_records(
    stages: Mapping[str, np.ndarray],
    ledger: Ledger,
    scope: TodScope,
    capped: bool,
    population: float,
    years: Sequence[int],
    homes: np.ndarray,
    *,
    zoned: bool = False,
    existing: bool = False,
) -> dict[str, Record]:
    horizon = len(years)
    words = distance_words(scope.distance, scope.walk)
    stage_ids = _stage_ids(capped, zoned)
    out: dict[str, Record] = {}
    data_sources: list[str] = []
    fmt = {"years": _period(years), "distance": words}
    for stage in STAGES:
        data_sources += DATA_SOURCES.get(stage, ())
        if stage == "eligible" and scope.walk == "network":
            data_sources += list(NETWORK_SOURCES)
        if stage == "developable" and zoned:
            data_sources += list(ZONED_SOURCES)
        if stage == "capacity" and existing:
            data_sources += list(EXISTING_SOURCES)
        inputs = [rec.from_parameter(pid, ledger[pid]) for pid in stage_ids[stage] if pid in ledger]
        text = _EXPRESSIONS[stage].format(**fmt)
        if stage == "eligible" and scope.walk == "network":
            text = (
                "eligible land = network walkshed acres around major stops (rail, and bus stops "
                "every 15 minutes or better in both weekday peaks) x tract land share, with "
                "straight-line fallback where the street network is incomplete"
            )
        if stage == "developable" and zoned:
            text = (
                "developable land = max(eligible land x developable share for the area type - "
                "land already zoned for multi-family by right (MA Section 3A), 0)"
            )
        if stage == "capacity" and existing:
            text = (
                "added capacity = feasible land not zoned by right x max(min(upzoned FAR, "
                "storeys x coverage) - baseline replacement chance q x today's FAR, 0) x net "
                "floor share / apartment floor area + feasible land already zoned by right "
                "(MA Section 3A parcels in the distance x developable share x p) x max(0, "
                "proposed homes per m2 - existing 3A modeled capacity per m2), in gross homes "
                "on the same land"
            )
        out[stage] = rec.from_draws(
            f"{STAGE_NAMES[stage].format(**fmt)} ({scope.name}, {words})",
            stages[stage].sum(axis=1),
            STAGE_UNITS[stage],
            expression=f"the sum over tracts of {text}",
            inputs=inputs,
            source_ids=sorted(set(data_sources)),
            tier=0,
        )
    out["eligible_homes"] = rec.from_draws(
        f"existing homes within {words} of a major transit stop ({scope.name})",
        np.full(stages["eligible"].shape[0], float(homes.sum())),
        "units",
        expression="the sum over tracts of 2020 block housing units within the distance",
        source_ids=DATA_SOURCES["eligible"],
        tier=0,
    )
    if population > 0:
        net = out["authorized"]
        out["per_100k"] = rec.from_draws(
            f"added TOD units authorized per 100,000 residents per year ({scope.name})",
            stages["authorized"].sum(axis=1) / horizon / population * 100_000,
            "permits per 100k people per year",
            expression=(
                f"added units authorized / {horizon} years / residents ({population:,.0f}) "
                "x 100,000"
            ),
            assumption_ids=net.assumption_ids,
            source_ids=net.source_ids,
            tier=0,
        )
    return out


def _existing_records(
    existing: ExistingCapacity,
    dev3: np.ndarray,
    units_per_m2: np.ndarray,
    e_per_m2: np.ndarray,
    land_m2: np.ndarray,
    capacity: Record,
    scope: TodScope,
) -> dict[str, Record]:
    """State records of the comparison with capacity already zoned by right (3A)."""
    words = distance_words(scope.distance, scope.walk)
    draws = dev3.shape[0]
    src = list(EXISTING_SOURCES)
    ids = list(capacity.assumption_ids)
    z = np.minimum(existing.land_m2, land_m2)
    proposed = dev3 * units_per_m2
    held = dev3 * e_per_m2
    added = additional_legal_capacity(proposed, held)
    eligible = float(land_m2.sum())
    resolved = float(land_m2[existing.measured].sum())
    share = resolved / eligible if eligible > 0 else 0.0
    unresolved_acres = (eligible - resolved) / M2_PER_ACRE
    out = {
        "existing_3a_land": rec.from_draws(
            f"Section 3A parcel land within {words} ({scope.name})",
            np.full(draws, float(z.sum()) / M2_PER_ACRE),
            "acres",
            expression=(
                "the sum over tracts of min(3A parcel acres (mbta_3a_capacity_tract.parcel_acres, "
                "split across tracts by area) x (station-area share x min(1, eligible land / "
                "half-mile land) + other share x max(0, eligible land - half-mile land) / (tract "
                "land - half-mile land)), eligible land); the station-area share of the land is "
                "taken as its share of the capacity (approximation)"
            ),
            source_ids=src,
            tier=0,
        ),
        "existing_3a_capacity": rec.from_draws(
            f"existing Section 3A modeled capacity within {words} ({scope.name})",
            np.full(draws, float(existing.units.sum())),
            "units",
            expression=(
                "the sum over tracts of station_area_units x min(1, eligible land / half-mile "
                "land) + (capacity_units - station_area_units) x max(0, eligible land - half-mile "
                "land) / (tract land - half-mile land), scaled with the land where it is capped "
                "at the eligible land (gross homes, atlas F_L_M__)"
            ),
            source_ids=src,
            tier=0,
        ),
        "proposed_3a_capacity": rec.from_draws(
            f"homes the TOD prototype allows on developable 3A land ({scope.name})",
            proposed.sum(axis=1),
            "units",
            expression=(
                "the sum over tracts of 3A land in the distance x developable share x "
                "min(upzoned FAR, storeys x coverage) x net floor share / apartment floor area"
            ),
            assumption_ids=ids,
            source_ids=src,
            tier=0,
        ),
        "existing_3a_on_developable": rec.from_draws(
            f"existing 3A modeled capacity on the same developable land ({scope.name})",
            held.sum(axis=1),
            "units",
            expression=(
                "the sum over tracts of 3A land in the distance x developable share x existing "
                "3A modeled capacity per m2 of that land"
            ),
            assumption_ids=ids,
            source_ids=src,
            tier=0,
        ),
        "additional_3a_capacity": rec.from_draws(
            f"additional legal capacity on developable 3A land, before feasibility ({scope.name})",
            added.sum(axis=1),
            "units",
            expression=(
                "the sum over tracts of max(0, proposed homes - existing 3A modeled capacity) "
                "on the same developable 3A land, in gross homes (diagnostic; the funnel then "
                "applies feasibility, take-up, absorption and the net-new share)"
            ),
            assumption_ids=ids,
            source_ids=src,
            tier=0,
        ),
        "existing_3a_resolved_share": rec.quantity(
            f"share of eligible land in tracts the 3A capacity table speaks to ({scope.name})",
            share,
            "share",
            formula=(
                f"eligible land in tracts with a row in mbta_3a_capacity_tract "
                f"{rec.format_number(resolved / M2_PER_ACRE, 'acres')} / all eligible land "
                f"{rec.format_number(eligible / M2_PER_ACRE, 'acres')}; the other "
                f"{rec.format_number(unresolved_acres, 'acres')} hold no compliant 3A district "
                "in the atlas: existing 3A capacity 0 (base-zoning rule), town status not "
                "resolved by tract"
            ),
            source_ids=src,
            tier=0,
        ),
    }
    return out


# ---------------------------------------------------------------- why a state's TOD is zero

DECOMPOSITION_CASES = ("benchmark", "lowrise", "wider", "lowrise_wider")
"""The four runs of the zero decomposition: the benchmark prototype and the
low-rise prototype (variant ``tod_lowrise``), each at the run's coverage and
at a wider coverage. The variant and the wider distance (3/4 mile from any major
stop) are set in ``assumptions/variants.yaml`` (``decomposition.tod``)."""


def zero_decomposition(results: Mapping[str, TodResult]) -> dict[str, Record]:
    """Why a state's TOD estimate is (near) zero: transit coverage, prototype, or both.

    Takes the four runs of :data:`DECOMPOSITION_CASES` (same seed and draws)
    and returns, for each, the eligible land (coverage), the land where the
    building pencils (prototype economics) and the added units authorized.
    Reading them side by side:

    - eligible land near zero in every case: transit coverage binds;
    - eligible land, but nothing pencils with the mid-rise while the low-rise
      pencils: the prototype binds;
    - eligible land, and neither prototype pencils: rents and prices bind
      under either prototype;
    - a wider coverage adds homes only with the low-rise: both bind.

    Args:
        results: Case name to its :class:`TodResult`.

    Returns:
        Records named ``decomp_<case>_eligible``, ``decomp_<case>_feasible``
        and ``decomp_<case>_authorized``.
    """
    out: dict[str, Record] = {}
    for case in DECOMPOSITION_CASES:
        res = results.get(case)
        if res is None:
            continue
        for stage in ("eligible", "feasible", "authorized"):
            base = res.records[stage]
            out[f"decomp_{case}_{stage}"] = rec.from_draws(
                f"{case.replace('_', ' + ')} case: {base.name}",
                res.stages[stage].sum(axis=1),
                base.unit,
                expression=(
                    f"the {stage} step of the TOD funnel run for this case (same seed and draws); "
                    f"see the funnel record '{stage}' for its formula"
                ),
                assumption_ids=base.assumption_ids,
                source_ids=base.source_ids,
                tier=0,
            )
    return out


NO_MAJOR_STOPS = (
    "No major transit stops in this state (no rail station; no bus stop with a counted "
    "schedule), so transit-oriented density has no eligible land."
)
"""Verdict when the stop table holds no major stop and the eligible land is exactly zero."""


def decomposition_verdict(records: Mapping[str, Record], major_stops: int | None = None) -> str:
    """A plain sentence from the decomposition records, by rule (P50 values).

    ``major_stops`` is the number of major stops in the state's stop table
    (``None``: not known). With none and exactly zero eligible land at both
    distances the sentence says there are no major stops, not "almost no land".
    """

    def p50(key: str) -> float:
        r = records.get(key)
        return float(r.p50) if r is not None and r.p50 is not None else 0.0

    eligible = p50("decomp_benchmark_eligible")
    wide = p50("decomp_wider_eligible")
    bench = p50("decomp_benchmark_authorized")
    low = p50("decomp_lowrise_authorized")
    low_wide = p50("decomp_lowrise_wider_authorized")
    words = (
        f"Eligible land {eligible:,.0f} acres ({wide:,.0f} at 3/4 mile); added units authorized: "
        f"mid-rise {bench:,.0f}, low-rise {low:,.0f}, low-rise at 3/4 mile {low_wide:,.0f}. "
    )
    if eligible == 0 and wide == 0 and major_stops == 0:
        return words + NO_MAJOR_STOPS
    if eligible < 1 and wide < 1:
        return words + "Transit coverage binds: there is almost no land near a major stop."
    if bench < 1 and low >= 1:
        verdict = "The prototype binds: a 3-4 storey walk-up pencils where the mid-rise does not."
        if low_wide > low * 1.5:
            verdict += " Wider coverage adds more, so coverage binds too."
        return words + verdict
    if bench < 1 and low < 1:
        if low_wide >= 1:
            return words + "Both bind: only the low-rise at a wider distance adds homes."
        return words + "Neither prototype pencils at today's rents and prices near the stops."
    return words + "The benchmark adds homes; neither coverage nor the prototype rules it out."


# ------------------------------------------------------------ factorial comparison: service rules

SERVICES = ("daily", "peak_average", "peak_max_gap")
"""Service rules of the factorial comparison (``engine.comparisons.tod_factorial``).

- ``daily`` (the benchmark): rail, or a bus stop with at least
  ``parking.transit_bus_min_departures`` scheduled departures on the representative weekday;
- ``peak_average``: rail, or a bus stop whose busiest platform side has on average a bus
  every ``tod.frequent_bus_max_headway_min`` minutes or better in both weekday peak
  windows (the network variants' rule, ``features.transit_walk``);
- ``peak_max_gap``: rail, or a bus stop whose longest scheduled gap in each peak window,
  window edges included, is at most that headway on one platform side. Without
  departure times this rule can rule a stop out but never confirm it (see
  :func:`hourly_gap_lower_bound`)."""

ACCESSES = ("straight", "network")
"""Access measures of the factorial comparison: straight line (the benchmark) or a walk on
the street network (pack table ``transit_walk_tract``)."""

GAP_RULED_OUT = "ruled_out"
GAP_NOT_RULED_OUT = "not_ruled_out"
GAP_NO_SCHEDULE = "no_schedule"


def max_scheduled_gap(
    times_min: Sequence[float] | np.ndarray,
    window: tuple[float, float],
    *,
    dedupe_min: float = 0.0,
) -> float:
    """The longest scheduled wait in a window, in minutes, window edges included.

    Convention (``comparisons.tod_factorial.max_gap.edge_rule`` = ``window_edges``): the
    gaps are window start to the first departure, between consecutive departures, and the
    last departure to the window end. A departure at the window end belongs to the next
    window (the window is ``[start, end)``). Departures at the same stop side less than
    ``dedupe_min`` minutes after the one before (and exact repeats) are one departure: a
    trip listed twice (shared or interlined trips) is one bus. A window with no departure
    has one gap, the whole window.

    Args:
        times_min: Departure times at one platform side, minutes of the service day.
        window: ``(start, end)`` minutes of the service day.
        dedupe_min: Repeats closer than this many minutes count once.

    Returns:
        The longest gap, minutes.
    """
    a, b = float(window[0]), float(window[1])
    t = np.sort(np.asarray(times_min, dtype=float))
    t = t[(t >= a) & (t < b)]
    if len(t) > 1:
        keep = np.concatenate([[True], np.diff(t) > max(float(dedupe_min), 0.0)])
        t = t[keep]
    points = np.concatenate([[a], t, [b]])
    return float(np.diff(points).max())


def hourly_gap_lower_bound(counts: np.ndarray, *, minutes_per_hour: float = 60.0) -> np.ndarray:
    """The smallest the longest gap in a window can be, given departures per hour only.

    ``counts`` is ``(rows, hours)``: departures in each whole hour of one window. In an
    hour with ``c`` departures the hour is cut into ``c + 1`` pieces, and each piece lies
    inside one gap (window edges count as gap ends), so some gap is at least
    ``60 / (c + 1)`` minutes; over the window, ``window minutes / (n + 1)``. The bound is
    the larger of the two. If it is above the headway the stop side **fails** for sure;
    if not, the departures may still be bunched, so the side is only **not ruled out**:
    hourly counts can never confirm a maximum-gap rule. Duplicate listings of one bus
    raise ``c`` and lower the bound, so they can hide a failure but never invent one.

    Returns:
        Minutes, one value per row.
    """
    c = np.asarray(counts, dtype=float)
    if c.ndim == 1:
        c = c[None, :]
    per_hour = (minutes_per_hour / (c + 1.0)).max(axis=1)
    whole = c.shape[1] * minutes_per_hour / (c.sum(axis=1) + 1.0)
    return np.maximum(per_hour, whole)


def gap_screen(peaks: pd.DataFrame, rule: Any) -> pd.DataFrame:
    """Per stop: the best platform side's lower bound on the longest peak gap.

    ``peaks`` is a schedule feed's hourly table (``data.sources.transit_peaks``: one row
    per platform side, ``h00`` ... ``h29``); ``rule`` is a
    :class:`visionhousing.features.transit_walk.PeakRule`. For each side the bound is the
    larger of the morning and afternoon bounds (:func:`hourly_gap_lower_bound`), so both
    windows are judged on the same side; the stop keeps its best (smallest) side.

    Returns:
        Indexed by ``stop_id``: ``lat``, ``lon``, ``gap_lb`` (minutes) and
        ``not_ruled_out`` (1 when ``gap_lb`` is at most the rule's headway).
    """
    bounds = []
    for h0, h1 in (rule.am, rule.pm):
        cols = [f"h{h:02d}" for h in range(int(h0), int(h1))]
        bounds.append(hourly_gap_lower_bound(peaks[cols].to_numpy(dtype=float)))
    out = peaks[["stop_id", "lat", "lon"]].copy()
    out["gap_lb"] = np.maximum(bounds[0], bounds[1]) if bounds else np.inf
    out = out.sort_values(["stop_id", "gap_lb"]).drop_duplicates("stop_id").set_index("stop_id")
    out["not_ruled_out"] = (out["gap_lb"] <= float(rule.max_headway_min) + 1e-9).astype("int64")
    return out[["lat", "lon", "gap_lb", "not_ruled_out"]]


def _paired_schedules(feeds: pd.DataFrame) -> dict[str, str]:
    """Map feed id to the schedule feed it was paired with (current, with bus stops).

    The same pairing as the peak-rule build (``features.transit_walk.frequent_bus_stops``).
    """
    f = feeds.fillna({"feed_id": "", "schedule_feed": "", "frequency": ""})
    paired = f[
        (f["feed_id"].astype(str) != "")
        & (f["schedule_feed"].astype(str) != "")
        & ~f["frequency"].astype(str).str.startswith("unknown")
        & (pd.to_numeric(f["bus_stops"], errors="coerce") > 0)
    ]
    return {
        str(a): str(b).split(".")[0]
        for a, b in zip(paired["feed_id"], paired["schedule_feed"], strict=True)
    }


def service_flags(
    stops: pd.DataFrame,
    feeds: pd.DataFrame | None,
    peaks: Mapping[str, pd.DataFrame],
    rule: Any,
) -> pd.DataFrame:
    """Each stop's standing under the three service rules, on one stop table.

    Args:
        stops: The pack's ``transit_stops`` (``mode``, ``weekday_departures``, ``feed_id``...).
        feeds: The pack's ``transit_feeds`` (``None``: no pairing known, no peak counts).
        peaks: Schedule feed id to its hourly peaks table (cached; never fetched here).
        rule: The peak rule (:class:`visionhousing.features.transit_walk.PeakRule`).

    Returns:
        A frame aligned with ``stops``: ``am``, ``pm`` (best side's peak departures, blank
        where not counted), ``peak_counted`` (1 where a bus stop has peak counts),
        ``peak_average`` (1 for a bus stop meeting the average-headway rule),
        ``gap_lb`` (lower bound on the best side's longest gap, minutes) and ``gap``
        (``ruled_out``, ``not_ruled_out`` or ``no_schedule``).
    """
    from visionhousing.features import transit_walk as tw

    out = pd.DataFrame(index=stops.index)
    bus = stops["mode"].astype(str).str.lower() == "bus"
    if feeds is None or "feed_id" not in stops.columns or not peaks:
        out["am"] = np.nan
        out["pm"] = np.nan
        out["gap_lb"] = np.nan
    else:
        flagged = tw.frequent_bus_stops(stops, feeds, peaks, rule)
        out["am"] = flagged["am"].to_numpy(dtype=float)
        out["pm"] = flagged["pm"].to_numpy(dtype=float)
        lb = np.full(len(stops), np.nan)
        pair = _paired_schedules(feeds)
        pos = pd.Series(np.arange(len(stops)), index=stops.index)
        for feed_id, group in stops[bus].groupby("feed_id", sort=True):
            mdb = pair.get(str(feed_id))
            if mdb is None or peaks.get(mdb) is None:
                continue
            screen = gap_screen(peaks[mdb], rule).rename(columns={"gap_lb": "am"})
            screen["pm"] = screen["am"]
            matched = tw._match(group, screen[["lat", "lon", "am", "pm"]])
            lb[pos[group.index].to_numpy()] = matched["am"].to_numpy(dtype=float)
        out["gap_lb"] = lb
    need_am, need_pm = (rule.min_departures(w) for w in (rule.am, rule.pm))
    counted = bus & out["am"].notna()
    out["peak_counted"] = counted.astype("int64")
    out["peak_average"] = (counted & (out["am"] >= need_am) & (out["pm"] >= need_pm)).astype(
        "int64"
    )
    has_gap = bus & out["gap_lb"].notna()
    out["gap"] = np.where(
        ~has_gap,
        GAP_NO_SCHEDULE,
        np.where(
            out["gap_lb"] <= float(rule.max_headway_min) + 1e-9, GAP_NOT_RULED_OUT, GAP_RULED_OUT
        ),
    )
    return out


def stops_for_service(
    stops: pd.DataFrame, flags: pd.DataFrame | None, service: str, min_departures: float
) -> pd.DataFrame:
    """The stop table recoded so the major-stop rule picks the service rule's major stops.

    Rail stays major by mode under every rule. A bus stop gets
    ``weekday_departures = min_departures`` when it qualifies under ``service`` and 0
    when it does not, so :func:`visionhousing.features.transit.tract_transit_features`
    (the benchmark's own block method) draws the same distances around a different set
    of stops: the same stop universe, only the rule changes. ``daily`` returns the table
    unchanged. For ``peak_max_gap`` the stops kept are those **not ruled out**, so the
    land is an upper reading (see :func:`hourly_gap_lower_bound`).

    Raises:
        ValueError: Unknown service rule.
    """
    if service not in SERVICES:
        raise ValueError(f"service must be one of {SERVICES}, got {service!r}")
    if service == "daily":
        return stops
    if flags is None:
        qualifies = np.zeros(len(stops), dtype=bool)
    elif service == "peak_average":
        qualifies = flags["peak_average"].to_numpy() == 1
    else:
        qualifies = flags["gap"].to_numpy() == GAP_NOT_RULED_OUT
    bus = (stops["mode"].astype(str).str.lower() == "bus").to_numpy()
    out = stops.copy()
    out["weekday_departures"] = np.where(bus & qualifies, float(min_departures), 0.0)
    return out


def recoded_transit_columns(
    tracts: pd.DataFrame, blocks: pd.DataFrame, stops: pd.DataFrame, rule: Any
) -> pd.DataFrame:
    """The tract table with its straight-line ``transit_*`` columns redrawn for ``stops``.

    Same block method as the pack build
    (:func:`visionhousing.features.transit.tract_transit_features`): 2020 block
    internal points, 1/4, 1/2, 3/4 mile from any major stop, and the major-stop rule
    (1/2 mile from rail, 1/4 mile from a frequent bus stop).
    """
    from visionhousing.features.transit import tract_transit_features

    feats = tract_transit_features(blocks, stops, rule, tracts=tracts["geoid"].astype(str))
    feats = feats.drop(columns=["transit_major_stops", "transit_rich"]).set_index("tract")
    geoids = tracts["geoid"].astype(str)
    out = tracts.copy()
    for name in feats.columns:
        out[name] = geoids.map(feats[name]).fillna(0.0).to_numpy()
    return out.reset_index(drop=True)


def walk_straight_columns(tracts: pd.DataFrame, walk: pd.DataFrame) -> pd.DataFrame:
    """Network-style columns built from the walk table's **straight-line** acres.

    The same conversion as :func:`network_columns` (acres x land share of the tract
    outline; homes at the straight-line density), applied to ``straight_acres_*`` with no
    fallback, written to the ``transit_*_net_<d>`` columns. Network and straight-line land
    then come from one geometry (the walkshed grid) around one stop set, so their
    difference is the access measure alone.
    """
    w = walk.copy()
    for miles in DISTANCE_CHOICES_MILES:
        lab = radius_label(miles)
        w[f"network_acres_{lab}"] = w[f"straight_acres_{lab}"]
        w[f"fallback_acres_{lab}"] = 0.0
    return network_columns(tracts, w)
