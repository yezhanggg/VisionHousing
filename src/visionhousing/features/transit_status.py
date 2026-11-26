"""The transit status ladder: what is known about transit service, county by county.

Plain-language summary
----------------------
The transit-oriented density (TOD) reform adds homes only near **major transit
stops**. Whether a stop is major depends on its scheduled service, and that is
only known where an agency's schedule feed (GTFS) could be found, read and
counted. This module keeps those questions apart, so that "we could not measure
it" is never shown as "there is none".

For every county, and for the state, it answers five questions in order:

1. **discovered**: is a schedule feed listed in a catalogue (the Mobility
   Database, or the Transitland Atlas list in ``assumptions/transit_feeds.yaml``)
   for the agencies whose bus stops are there?
2. **legal**: may the feed be read and its counts used (its terms)?
3. **coverage**: how many of the bus stops in the National Transit Map had
   their weekday departures counted from a current feed?
4. **headway**: does any stop qualify? The benchmark rule (a rail station, or a
   bus stop with at least 64 scheduled weekday departures) and, as a variant,
   the peak rule (every 15 minutes or better in both weekday peaks).
5. **feasible**: does the mid-rise prototype pencil on any land near those
   stops (the TOD funnel's feasible land, P50)?

Each answer is one word: ``yes``, ``partial``, ``no``, ``unknown``,
``not_reached`` (an earlier question has no "yes", so this one does not arise)
or ``measured_zero`` (every stop's service was measured and none qualifies).
A qualifying-stop answer is ``measured_zero`` only when coverage is complete;
otherwise "none found" is ``unknown``.

A feed that is only discovered adds no homes: homes come from the funnel alone,
on land near stops that were measured and qualify. The **completeness label**
says how much of the stop inventory was measured. When the measured share of
bus stops is below ``tod.status_min_measured_bus_share``, the unknown part has
no defensible bound: the statewide range and percent change are suppressed and
the run reports the measured contribution with that label.

Rules (words) live in ``assumptions/transit_feeds.yaml``; the one number in the
ledger. Method: ``docs/modules/transit.md`` ("Feed discovery and the status
ladder") and ``docs/methods/tod.md``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

__all__ = [
    "MIN_SHARE_ID",
    "STAGES",
    "VALUES",
    "StatusTable",
    "agency_status",
    "status_table",
]

MIN_SHARE_ID = "tod.status_min_measured_bus_share"
"""Ledger id of the measured share of bus stops needed to show a statewide range."""
STAGES = ("discovered", "legal", "coverage", "headway", "headway_peak", "feasible")
"""The questions, in order (``headway_peak`` is the peak-rule variant of ``headway``)."""
VALUES = ("yes", "partial", "no", "unknown", "not_reached", "measured_zero")
"""The answers a stage can take."""
_RAIL = frozenset({"rail", "subway", "light_rail", "commuter_rail", "tram"})
_LEGAL = {"usable": "yes", "forbidden": "no"}


@dataclass(frozen=True)
class StatusTable:
    """The ladder for every county and the state.

    Attributes:
        rows: One row per county plus one ``level == "state"`` row (geoid = the
            state code): counts (``bus_stops``, ``bus_stops_measured``,
            ``rail_stops``, ``major_stops``, ``agencies``, ``agencies_measured``,
            ``agencies_discovered``), ``measured_share``, one column per stage
            and ``completeness`` (a label in words).
        min_share: The measured share needed to show the statewide range.
    """

    rows: pd.DataFrame
    min_share: float

    @property
    def state(self) -> dict[str, Any]:
        """The state row as a plain dict."""
        return dict(self.rows[self.rows["level"] == "state"].iloc[0])

    @property
    def statewide_shown(self) -> bool:
        """True when the statewide range and percent change may be shown."""
        return float(self.state["measured_share"]) >= self.min_share

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready: the state row, the counties and the display decision."""
        state = _plain(self.state)
        return {
            "stages": list(STAGES),
            "state": state,
            "counties": {
                str(r["geoid"]): _plain(r)
                for r in self.rows[self.rows["level"] == "county"].to_dict("records")
            },
            "statewide": {
                "shown": self.statewide_shown,
                "min_measured_share": self.min_share,
                "label": state["completeness"],
            },
        }


def _plain(row: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if hasattr(v, "item"):
            v = v.item()
        out[str(k)] = v
    return out


def _register_by_ntd(register: Mapping[str, Any] | None, state: str) -> dict[str, dict]:
    if not register:
        return {}
    st = state.upper()
    out: dict[str, dict] = {}
    for feed in register.get("feeds", []):
        if str(feed.get("state", "")).upper() == st and feed.get("ntd_id"):
            out[str(feed["ntd_id"])] = dict(feed)
    return out


def agency_status(
    stops: pd.DataFrame, state: str, register: Mapping[str, Any] | None = None
) -> pd.DataFrame:
    """One row per agency (National Transit Database id) with bus stops: discovered and legal.

    - discovered: the agency's stops were read from a schedule feed (``counted``,
      or ``stale``: only an expired feed), or the register lists a feed for it.
    - legal: a register feed carries its own legal record (``usable`` → yes,
      ``forbidden`` → no, anything else → unknown); a feed read through the
      Mobility Database carries the terms recorded for it in the source
      register (yes); an agency with no feed found → ``not_reached``.
    """
    bus = _bus(stops)
    reg = _register_by_ntd(register, state)
    rows = []
    for ntd, g in bus.groupby(bus["ntd_id"].astype(str), sort=True):
        freq = g["frequency"].astype(str)
        read = bool(freq.isin(["counted", "stale"]).any())
        feed = reg.get(ntd)
        discovered = read or feed is not None
        if feed is not None:
            legal = _LEGAL.get(str((feed.get("legal") or {}).get("status", "")), "unknown")
        elif read:
            legal = "yes"
        else:
            legal = "not_reached"
        rows.append(
            {
                "ntd_id": ntd,
                "bus_stops": len(g),
                "bus_stops_measured": int((freq == "counted").sum()),
                "discovered": discovered,
                "legal": legal,
                "register_feed": "" if feed is None else str(feed.get("id", "")),
            }
        )
    cols = ["ntd_id", "bus_stops", "bus_stops_measured", "discovered", "legal", "register_feed"]
    return pd.DataFrame(rows, columns=cols)


def _bus(stops: pd.DataFrame) -> pd.DataFrame:
    mode = stops["mode"].astype(str).str.lower()
    out = stops[mode == "bus"].copy()
    for col in ("ntd_id", "frequency", "county"):
        if col not in out:
            out[col] = ""
    out["ntd_id"] = out["ntd_id"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
    return out


def _share_word(part: int, whole: int) -> str:
    if whole == 0:
        return "not_reached"
    if part >= whole:
        return "yes"
    return "partial" if part > 0 else "no"


def _ladder(
    stops: pd.DataFrame,
    agencies: pd.DataFrame,
    peak_major: int | None,
    feasible: tuple[float, float] | None,
) -> dict[str, Any]:
    """The ladder for one area's stops (a county, or the whole state)."""
    mode = stops["mode"].astype(str).str.lower()
    freq = stops["frequency"].astype(str) if "frequency" in stops else pd.Series("", stops.index)
    major_col = pd.to_numeric(stops.get("major", 0), errors="coerce").fillna(0)
    rail = int(mode.isin(_RAIL).sum())
    bus = mode == "bus"
    n_bus, n_measured = int(bus.sum()), int((bus & (freq == "counted")).sum())
    major = int((major_col == 1).sum())
    ag = agencies[agencies["bus_stops"] > 0]
    n_ag = len(ag)
    n_disc = int(ag["discovered"].sum()) if n_ag else 0
    disc = ag[ag["discovered"]] if n_ag else ag
    legal_yes = int((disc["legal"] == "yes").sum()) if len(disc) else 0
    if n_bus == 0:
        discovered = legal = "not_reached"
        coverage = "yes" if rail > 0 else "not_reached"  # rail counts as measured (by mode)
    else:
        discovered = _share_word(n_disc, n_ag)
        if n_disc == 0:
            legal = "not_reached"
        elif legal_yes == len(disc):
            legal = "yes"
        elif legal_yes > 0:
            legal = "partial"
        elif (disc["legal"] == "unknown").any():
            legal = "unknown"
        else:
            legal = "no"
        coverage = _share_word(n_measured, n_bus)
    complete = n_measured >= n_bus
    headway = "yes" if major > 0 else "measured_zero" if complete else "unknown"
    if peak_major is None:
        headway_peak = "yes" if rail > 0 else "unknown"  # no peak counts in this pack
    elif peak_major + rail > 0:
        headway_peak = "yes"
    else:
        headway_peak = "measured_zero" if complete else "unknown"
    if feasible is None:
        feas = "unknown" if headway == "yes" else "not_reached"
    else:
        eligible, land = feasible
        feas = "not_reached" if headway != "yes" or eligible <= 0 else "yes" if land > 0 else "no"
    share = 1.0 if n_bus == 0 else n_measured / n_bus
    n_ag_measured = int((ag["bus_stops_measured"] > 0).sum()) if n_ag else 0
    if complete:
        label = "complete: every stop's service measured"
        if n_bus == 0 and rail == 0:
            label = "complete: no transit stop in the National Transit Map"
    else:
        pct = math.floor(share * 1000) / 10  # never rounds a partial footprint up to 100%
        label = (
            f"partial footprint: {n_measured:,} of {n_bus:,} bus stops ({pct:.1f}%) from "
            f"{n_ag_measured} of {n_ag} agencies have measured service"
        )
    return {
        "stops": len(stops),
        "rail_stops": rail,
        "bus_stops": n_bus,
        "bus_stops_measured": n_measured,
        "major_stops": major,
        "agencies": n_ag,
        "agencies_discovered": n_disc,
        "agencies_measured": n_ag_measured,
        "measured_share": round(share, 4),
        "discovered": discovered,
        "legal": legal,
        "coverage": coverage,
        "headway": headway,
        "headway_peak": headway_peak,
        "feasible": feas,
        "completeness": label,
    }


def status_table(
    stops: pd.DataFrame,
    state: str,
    counties: list[str] | tuple[str, ...],
    *,
    min_share: float,
    register: Mapping[str, Any] | None = None,
    peak_major: Mapping[str, int] | None = None,
    feasible: Mapping[str, tuple[float, float]] | None = None,
) -> StatusTable:
    """The status ladder for every county and the state (pure).

    Args:
        stops: The pack's ``transit_stops`` (``mode``, ``frequency``, ``major``,
            ``county``, ``ntd_id``).
        state: Two-letter state code (to pick the register's feeds).
        counties: Every county of the state, in order (a county without stops
            gets a row too).
        min_share: ``tod.status_min_measured_bus_share``.
        register: ``assumptions/transit_feeds.yaml`` as read
            (:func:`visionhousing.data.sources.transit.load_feed_register`).
        peak_major: County id to bus stops passing the peak rule (pack table
            ``transit_walk_county``, column ``frequent_bus_stops``); ``None``
            when the pack has no such table.
        feasible: County id to ``(eligible acres, feasible acres)`` at P50 from
            the TOD funnel; ``None`` leaves the stage ``unknown``/``not_reached``.

    Returns:
        A :class:`StatusTable`.
    """
    stops = stops.copy()
    stops["county"] = stops["county"].fillna("").astype(str).str.zfill(5)
    agencies_all = agency_status(stops, state, register)
    bus = _bus(stops)
    rows = []
    for c in counties:
        c = str(c)
        here = stops[stops["county"] == c]
        counts = (
            bus[bus["county"] == c]
            .groupby("ntd_id")
            .agg(
                bus_stops=("ntd_id", "size"),
                bus_stops_measured=("frequency", lambda f: int((f.astype(str) == "counted").sum())),
            )
        )
        ag = agencies_all.drop(columns=["bus_stops", "bus_stops_measured"]).merge(
            counts.reset_index(), on="ntd_id", how="inner"
        )
        pm = None if peak_major is None else int(peak_major.get(c, 0))
        fe = None if feasible is None else feasible.get(c, (0.0, 0.0))
        rows.append({"level": "county", "geoid": c, **_ladder(here, ag, pm, fe)})
    pm_all = None if peak_major is None else int(sum(peak_major.values()))
    fe_all = None
    if feasible is not None:
        fe_all = (
            float(sum(v[0] for v in feasible.values())),
            float(sum(v[1] for v in feasible.values())),
        )
    rows.append(
        {"level": "state", "geoid": state.upper(), **_ladder(stops, agencies_all, pm_all, fe_all)}
    )
    return StatusTable(pd.DataFrame(rows), float(min_share))
