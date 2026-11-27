"""Massachusetts ADU rights by date, and what the ADU benchmark adds to them.

Plain-language summary
----------------------
Since February 2, 2025, Massachusetts law protects one accessory dwelling unit
(ADU) by right on a lot in a single-family zoning district (M.G.L. c. 40A
§ 3, ADU paragraph). Its terms:

- **Size** (§ 1A): the protected ADU may be capped at half the principal
  dwelling's gross floor area or 900 sq ft, whichever is smaller. A
  1,000 sq ft house gets a 500 sq ft floor; a 1,600 sq ft house gets 800.
- **Parking** (§ 3): a town may require at most one added space, and none for
  an ADU within 0.5 miles of a commuter rail station, subway station, ferry
  terminal or bus station.
- **Owner occupancy** (§ 3): may not be required.
- **Scope**: single-family residential districts of the cities and towns that
  zone under chapter 40A. Boston zones under its own act, so the right does
  not reach Boston.

The statute's numbers are parameters in ``assumptions/law/statutory_rights.yaml``
(provision ``ma_40a_3_adu``); when they apply comes from the dated timeline
records in ``assumptions/law/states/ma.yaml``. This module holds no legal
number.

Two questions are answered:

1. :func:`ma_adu_right`: for one lot on one date, the state floor (in force or
   not, the size cap, the parking cap, owner occupancy). Unknown inputs stay
   unknown (``None``), never "no right".
2. :func:`benchmark_gain`: on that lot, does the benchmark (an ADU of at least
   ``adu.min_size_sqft``, detached or semi-detached, no added parking) give a
   right the state law does not? ``full`` where the state law does not give a
   by-right ADU of the benchmark's size; ``conditions`` where the only gap is
   the one parking space; ``none`` where the benchmark adds nothing.

For a run, :func:`state_floor_rule` turns the same test into each tract's
share of single-family lots where the benchmark is already legal, per Monte
Carlo draw, from the pack (each tract's 2020 homes within the statute's
distance of a listed station; its Boston share) and labelled ledger ranges
(the shares the pack cannot measure: single-family district coverage, houses
under the size floor, towns that already allow more, towns that require the
space). The named variant ``ma_adu_state_floor`` uses it **in place of** the
benchmark's statewide range (``assumptions/calibration/already_legal_ma.yaml``),
so the two are never added together.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.law.rights import load_rights
from visionhousing.law.timeline import TimelineRecord, eligibility_status, load_timeline

__all__ = [
    "GAIN_KINDS",
    "MA_TIMELINE_PATH",
    "BenchmarkGain",
    "MaAduRight",
    "MaLot",
    "StateFloorRule",
    "application",
    "benchmark_gain",
    "boston_shares",
    "ma_adu_right",
    "protected_size_sqft",
    "state_floor_rule",
    "station_shares",
]

ROOT = Path(__file__).resolve().parents[3]
MA_TIMELINE_PATH = ROOT / "assumptions" / "law" / "states" / "ma.yaml"
"""The Massachusetts state-law file; its ``records`` block is a timeline file."""

GAIN_KINDS = ("none", "conditions", "full")
"""What the benchmark adds on a lot: nothing, only the parking condition, or the right itself."""


@dataclass(frozen=True)
class MaLot:
    """What is known about one single-family lot (``None`` = not known).

    Attributes:
        primary_sqft: Gross floor area of the principal dwelling, sq ft.
        single_family_district: The lot is in a single-family residential district.
        station_miles: Distance to the nearest listed station, miles.
        in_boston: The lot is in Boston.
        town_requires_space: The town's bylaw requires the one added space.
        town_allows_size_sqft: The largest ADU the town's own bylaw allows by
            right whatever the house size, sq ft (``None`` = not read).
    """

    primary_sqft: float | None = None
    single_family_district: bool | None = None
    station_miles: float | None = None
    in_boston: bool | None = None
    town_requires_space: bool | None = None
    town_allows_size_sqft: float | None = None


@dataclass(frozen=True)
class MaAduRight:
    """The state floor for one lot on one date.

    Attributes:
        on: The date.
        version: Timeline record in force ("" when none).
        protected: One ADU protected by right (``None`` = unknown).
        size_cap_sqft: Largest ADU the state protects on this lot (``None`` = unknown).
        max_added_spaces: Most parking spaces a town may require (``None`` = unknown).
        owner_occupancy_required: May the town require owner occupancy.
        why: Plain reasons.
    """

    on: date
    version: str
    protected: bool | None
    size_cap_sqft: float | None
    max_added_spaces: int | None
    owner_occupancy_required: bool | None
    why: str


@dataclass(frozen=True)
class BenchmarkGain:
    """What the benchmark adds on one lot.

    Attributes:
        kind: ``none``, ``conditions`` or ``full`` (``None`` = unknown).
        gaps: The terms the benchmark relaxes (``boston``, ``district``,
            ``size``, ``parking``, ``no_state_right``).
        right: The state floor it was compared with.
    """

    kind: str | None
    gaps: tuple[str, ...]
    right: MaAduRight


def _terms(rights: Mapping[str, Any], provision: str) -> Mapping[str, Any]:
    return rights["provisions"][provision]


def application(rights: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    """The ``ma_adu_application`` block of ``assumptions/law/statutory_rights.yaml``."""
    return (rights or load_rights())["ma_adu_application"]


def _version(
    on: date,
    records: Iterable[TimelineRecord] | None,
    rights: Mapping[str, Any],
    provision: str,
    as_of: date | None,
) -> tuple[str, Mapping[str, Any] | None]:
    spec = _terms(rights, provision)
    recs = list(records) if records is not None else load_timeline(MA_TIMELINE_PATH)
    e = eligibility_status(
        [r for r in recs if r.provision_id == provision],
        state=spec["state"],
        housing_type=spec["housing_type"],
        on=on,
        as_of=as_of,
    )
    if not e.in_force:
        return "", None
    terms = spec["versions"].get(e.record_id)
    if terms is None:
        raise KeyError(f"{e.record_id} has no terms in statutory_rights.yaml ({provision})")
    return e.record_id, terms["adu"]


def protected_size_sqft(primary_sqft: float | None, terms: Mapping[str, Any]) -> float | None:
    """The § 1A size cap: the smaller of a share of the principal dwelling and a fixed cap."""
    if primary_sqft is None:
        return None
    cap = terms["size_cap"]
    return min(float(cap["share_of_principal"]) * float(primary_sqft), float(cap["max_sqft"]))


def ma_adu_right(
    lot: MaLot,
    on: date,
    *,
    records: Iterable[TimelineRecord] | None = None,
    rights: Mapping[str, Any] | None = None,
    as_of: date | None = None,
    provision: str = "ma_40a_3_adu",
) -> MaAduRight:
    """The Massachusetts state ADU floor for one lot on date ``on``."""
    rights = rights or load_rights()
    vid, terms = _version(on, records, rights, provision, as_of)
    if terms is None:
        return MaAduRight(on, "", False, None, None, None, f"no version of {provision} in force")
    why = [vid]
    excluded = {str(m).lower() for m in terms.get("excluded_municipalities") or []}
    districts = set(terms.get("districts") or [])
    scope: list[bool | None] = []
    if "boston" in excluded:
        scope.append(None if lot.in_boston is None else not lot.in_boston)
        if lot.in_boston:
            why.append("Boston: chapter 40A does not apply")
    if "single_family_residential" in districts:
        scope.append(lot.single_family_district)
        if lot.single_family_district is False:
            why.append("not in a single-family residential district")
    if any(s is False for s in scope):
        protected: bool | None = False
    elif any(s is None for s in scope):
        protected = None
    else:
        protected = True
    park = terms["parking"]
    spaces: int | None
    if lot.station_miles is None:
        spaces = None
    elif lot.station_miles <= float(park["none_within_miles"]):
        spaces = 0
        why.append(f"within {park['none_within_miles']} mi of a listed station: no added space")
    else:
        spaces = int(park["max_added_spaces"])
    if protected is False:
        return MaAduRight(on, vid, False, None, None, None, "; ".join(why))
    return MaAduRight(
        on=on,
        version=vid,
        protected=protected,
        size_cap_sqft=protected_size_sqft(lot.primary_sqft, terms),
        max_added_spaces=spaces,
        owner_occupancy_required=bool(terms.get("owner_occupancy_required", False)),
        why="; ".join(why),
    )


def benchmark_gain(
    lot: MaLot,
    on: date,
    *,
    min_size_sqft: float,
    records: Iterable[TimelineRecord] | None = None,
    rights: Mapping[str, Any] | None = None,
    as_of: date | None = None,
) -> BenchmarkGain:
    """What the benchmark ADU (``min_size_sqft``, no added parking) adds on one lot.

    ``full``: the state law gives no by-right ADU of the benchmark's size here
    (Boston, outside a single-family district, the § 1A cap below the size and
    the town allows no more, or no version in force). ``conditions``: the
    state right covers the size, but the town may require, and does require,
    the one parking space. ``none``: the benchmark adds no right. ``None``:
    a fact that matters is unknown.
    """
    right = ma_adu_right(lot, on, records=records, rights=rights, as_of=as_of)
    if not right.version:
        return BenchmarkGain("full", ("no_state_right",), right)
    gaps: list[str] = []
    if right.protected is False:
        gaps.append("boston" if lot.in_boston else "district")
        return BenchmarkGain("full", tuple(gaps), right)
    unknown = right.protected is None
    cap = right.size_cap_sqft
    if cap is None:
        unknown = True
    elif cap < min_size_sqft:
        local = lot.town_allows_size_sqft
        if local is None or local < min_size_sqft:
            gaps.append("size")
    if gaps:
        return BenchmarkGain("full", tuple(gaps), right)
    if right.max_added_spaces is None:
        unknown = True
    elif right.max_added_spaces > 0:
        if lot.town_requires_space is None:
            unknown = True
        elif lot.town_requires_space:
            gaps.append("parking")
    if unknown:
        return BenchmarkGain(None, tuple(gaps), right)
    return BenchmarkGain("conditions" if gaps else "none", tuple(gaps), right)


# ---------------------------------------------------------------- tract shares for a run


def _route_codes(value: Any) -> set[str]:
    """GTFS route-type codes of one stop: ``"2;3"`` -> ``{"2", "3"}``; ``2.0`` -> ``{"2"}``."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return set()
    out = set()
    for part in str(value).split(";"):
        part = part.strip()
        try:
            out.add(str(int(float(part))))
        except ValueError:
            continue
    return out


def station_shares(
    tracts: pd.DataFrame,
    blocks: pd.DataFrame,
    stops: pd.DataFrame,
    *,
    route_types: Iterable[int],
    miles: float,
) -> np.ndarray:
    """Each tract's share of 2020 homes within ``miles`` of a stop of the listed route types.

    Straight-line distance from each block's internal point to the nearest
    stop whose ``route_types`` (GTFS codes, ``;``-separated) include a listed
    type. A tract with no homes in the block table gets 0.
    """
    from visionhousing.features.transit import nearest_stop

    wanted = {str(int(t)) for t in route_types}
    geoids = tracts["geoid"].astype(str).to_numpy()
    if not wanted or stops is None or "route_types" not in stops or blocks is None:
        return np.zeros(len(geoids))
    keep = np.array([bool(wanted & _route_codes(v)) for v in stops["route_types"]], dtype=bool)
    st = stops[keep]
    if st.empty:
        return np.zeros(len(geoids))
    b = blocks[pd.to_numeric(blocks["housing_units"], errors="coerce").fillna(0) > 0]
    _, dist = nearest_stop(
        b["lat"].to_numpy(dtype=float),
        b["lon"].to_numpy(dtype=float),
        st["lat"].to_numpy(dtype=float),
        st["lon"].to_numpy(dtype=float),
    )
    hu = pd.to_numeric(b["housing_units"], errors="coerce").fillna(0).to_numpy(dtype=float)
    frame = pd.DataFrame({"tract": b["tract"].astype(str).to_numpy(), "hu": hu,
                          "near": hu * (dist <= float(miles))})  # fmt: skip
    sums = frame.groupby("tract")[["hu", "near"]].sum()
    total = sums["hu"].reindex(geoids).fillna(0).to_numpy()
    near = sums["near"].reindex(geoids).fillna(0).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(total > 0, near / np.where(total > 0, total, 1.0), 0.0)


def boston_shares(
    tracts: pd.DataFrame, tract_munis: pd.DataFrame | None, *, county: str, cousub: str
) -> np.ndarray | None:
    """Each tract's share of 2020 homes in Boston, or ``None`` without a tract-to-town table."""
    if tract_munis is None or tract_munis.empty:
        return None
    t = tract_munis.assign(
        hu=pd.to_numeric(tract_munis["housing_units_2020"], errors="coerce").fillna(0.0),
        tract=tract_munis["tract"].astype(str),
    )
    inside = (t["county"].astype(str) == county) & (t["cousub"].astype(str).str.zfill(5) == cousub)
    total = t.groupby("tract")["hu"].sum()
    boston = t[inside].groupby("tract")["hu"].sum()
    geoids = tracts["geoid"].astype(str)
    tot = total.reindex(geoids).fillna(0).to_numpy()
    bos = boston.reindex(geoids).fillna(0).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(tot > 0, bos / np.where(tot > 0, tot, 1.0), 0.0)


@dataclass(frozen=True)
class StateFloorRule:
    """Each tract's already-legal share under the Massachusetts state floor, per draw.

    For a draw with ledger values ``d`` (single-family district share), ``u``
    (houses under the size floor), ``w`` (towns already allowing the size),
    ``q`` (towns requiring the space) and ``c`` (exposure of a lot whose only
    gap is a condition), and tract values ``B`` (Boston share) and ``n``
    (homes near a listed station)::

        legal = (1 - B) x d x size_ok x (1 - (1 - n) x q x c)
        size_ok = (1 - u) + u x w     (0 + w when the fixed cap is below the size)

    This is :func:`benchmark_gain` averaged over the lot mix, with the shares
    taken as independent.

    Attributes:
        ids: Ledger ids the rule draws.
        near: Tract share of homes within the statute's distance of a listed station.
        boston: Tract Boston share, or ``None`` (then Suffolk tracts use the
            ledger fallback share).
        suffolk: Tracts in Boston's county.
        size_cap_reaches: The fixed cap (900 sq ft) is at least the benchmark size.
        ledger: Role to ledger id.
        version: Timeline record in force.
        notes: Plain notes for the run.
    """

    ids: tuple[str, ...]
    near: np.ndarray
    boston: np.ndarray | None
    suffolk: np.ndarray
    size_cap_reaches: bool
    ledger: Mapping[str, str]
    version: str
    notes: tuple[str, ...]

    def share(self, col: Mapping[str, np.ndarray]) -> np.ndarray:
        """``(draws, tracts)`` already-legal shares from the drawn ``(draws, 1)`` columns."""
        led = self.ledger
        d = col[led["single_family_district_share"]]
        u = col[led["primary_below_floor_share"]]
        w = col[led["local_size_allows_benchmark_share"]]
        q = col[led["town_requires_space_share"]]
        c = col[led["parking_only_exposure"]]
        if self.boston is not None:
            b = self.boston[None, :]
        else:
            b = self.suffolk[None, :] * col[led["boston_share_without_table"]]
        size_ok = (1.0 - u) + u * w if self.size_cap_reaches else w
        parking = 1.0 - (1.0 - self.near[None, :]) * q * c
        return np.clip((1.0 - b) * d * size_ok * parking, 0.0, 1.0)


def state_floor_rule(
    state: Any,
    ledger: Any,
    *,
    year: int,
    rights: Mapping[str, Any] | None = None,
    records: Iterable[TimelineRecord] | None = None,
    as_of: date | None = None,
) -> StateFloorRule:
    """The :class:`StateFloorRule` for a Massachusetts run context.

    Args:
        state: The ``StateContext`` (tracts, blocks, stops; its pack's
            ``tract_municipalities`` when present).
        ledger: The run's ledger (``adu.min_size_sqft`` and the rule's ids).
        year: First window year; the right is read on ``read_on`` of that year.
        rights: Parsed ``statutory_rights.yaml`` (default: the shipped file).
        records: Timeline records (default: ``assumptions/law/states/ma.yaml``).
        as_of: Run's as-of date for the records.

    Raises:
        ValueError: The state is not listed, or the size-floor parameter's
            threshold disagrees with the benchmark size and the statute's share.
    """
    rights = rights or load_rights()
    app = application(rights)
    code = str(state.state).upper()
    if code not in {str(s).upper() for s in app["states"]}:
        raise ValueError(f"the Massachusetts ADU state floor does not apply to {code}")
    month, day = (int(x) for x in str(app["read_on"]).split("-"))
    on = date(int(year), month, day)
    vid, terms = _version(on, records, rights, str(app["provision"]), as_of)
    min_size = float(ledger["adu.min_size_sqft"].central)
    tracts = state.tracts
    n_tracts = len(tracts)
    led = dict(app["ledger"])
    if terms is None:
        # No state right in force: nothing is already legal under it.
        zero = np.zeros(n_tracts)
        return StateFloorRule(
            ids=(), near=zero, boston=zero, suffolk=zero, size_cap_reaches=False,
            ledger=led, version="", notes=(f"No version of {app['provision']} in force on {on}.",),
        )  # fmt: skip
    cap = terms["size_cap"]
    threshold = min_size / float(cap["share_of_principal"])
    if abs(threshold - float(app["primary_below_floor_at_sqft"])) > 1e-9:
        raise ValueError(
            f"{led['primary_below_floor_share']} is defined at "
            f"{app['primary_below_floor_at_sqft']} sq ft, but the benchmark size "
            f"{min_size:g} / {cap['share_of_principal']} = {threshold:g} sq ft"
        )
    park = terms["parking"]
    types = [
        t for kind in park["station_types"] for t in (app["station_route_types"].get(kind) or [])
    ]
    store = getattr(state, "derived", None)
    key = ("ma_adu_station_share", tuple(sorted(types)), float(park["none_within_miles"]))
    near = store.get(key) if store is not None else None
    if near is None:
        near = station_shares(
            tracts, state.blocks, state.stops, route_types=types,
            miles=float(park["none_within_miles"]),
        )  # fmt: skip
        if store is not None:
            store[key] = near
    from visionhousing.engine.place_scope import read_tract_municipalities

    bos = app["boston"]
    boston = boston_shares(
        tracts, read_tract_municipalities(state), county=str(bos["county"]),
        cousub=str(bos["cousub"]),
    )  # fmt: skip
    suffolk = (tracts["county"].astype(str).to_numpy() == str(bos["county"])).astype(float)
    ids = [
        led[k]
        for k in (
            "single_family_district_share",
            "primary_below_floor_share",
            "local_size_allows_benchmark_share",
            "town_requires_space_share",
            "parking_only_exposure",
        )
    ]
    notes = [
        f"Already-legal ADU share from the Massachusetts state floor ({vid}; c. 40A §§ 1A, 3), "
        "in place of the statewide range: (1 - Boston share) x single-family district share x "
        "size share x (1 - homes beyond the station distance x towns requiring the space x "
        "adu.conditions_exposure_share).",
        f"Size floor: half the principal dwelling or {cap['max_sqft']} sq ft; a "
        f"{min_size:g} sq ft ADU needs a {threshold:g} sq ft house "
        f"({led['primary_below_floor_share']}, judgement).",
        "Near a listed station: 2020 homes within "
        f"{park['none_within_miles']} mi (straight line) of a commuter rail, subway or ferry "
        "stop in the pack; bus stations cannot be told apart from bus stops and are not "
        "counted, so the parking gap is an upper bound.",
    ]
    if boston is None:
        ids.append(led["boston_share_without_table"])
        notes.append(
            "Boston: the pack has no tract-to-town table, so every Suffolk County tract takes "
            f"{led['boston_share_without_table']} (judgement) as its Boston share."
        )
    else:
        notes.append("Boston: each tract's share of 2020 homes in Boston (tract_municipalities).")
    return StateFloorRule(
        ids=tuple(ids),
        near=near,
        boston=boston,
        suffolk=suffolk,
        size_cap_reaches=float(cap["max_sqft"]) >= min_size,
        ledger=led,
        version=vid,
        notes=tuple(notes),
    )
