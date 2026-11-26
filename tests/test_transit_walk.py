"""Transit walksheds: peak frequent-bus rule, network walksheds on a tiny synthetic map, tables.

No network access: schedule feeds and map tiles are built in memory.
"""

from __future__ import annotations

import gzip
import json
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from visionhousing.data.http import FetchError
from visionhousing.data.sources import osm
from visionhousing.data.sources import transit_peaks as tp
from visionhousing.features import planar
from visionhousing.features import transit as tf
from visionhousing.features import transit_walk as tw
from visionhousing.features import walkshed as ws
from visionhousing.ledger import load_ledger

RULE = tw.PeakRule(15.0, (7, 9), (16, 18))
WALK = ws.WalkRule(snap_m=80.0, edge_buffer_m=50.0, min_component_m=500.0)


# --------------------------------------------------------------------------- GTFS peaks


def _csv(rows: list[dict]) -> bytes:
    return pd.DataFrame(rows).to_csv(index=False).encode()


def _feed(*, headway_in: int, headway_out: int, with_direction: bool = True) -> dict[str, bytes]:
    """One route, stops A -> B -> C (C is the last stop), trips 06:00-19:59 each way."""
    stops = [
        {"stop_id": "A", "stop_lat": 40.0, "stop_lon": -75.0, "parent_station": ""},
        {"stop_id": "B", "stop_lat": 40.001, "stop_lon": -75.0, "parent_station": "S"},
        {"stop_id": "C", "stop_lat": 40.002, "stop_lon": -75.0, "parent_station": ""},
        {"stop_id": "S", "stop_lat": 40.001, "stop_lon": -75.0, "parent_station": ""},
    ]
    trips, times = [], []
    k = 0
    for direction, headway, order in ((0, headway_in, "ABC"), (1, headway_out, "CBA")):
        for start in range(6 * 60, 20 * 60, headway):
            k += 1
            trip = {"trip_id": f"t{k}", "route_id": "r", "service_id": "wk", "trip_headsign": order}
            if with_direction:
                trip["direction_id"] = str(direction)
            trips.append(trip)
            for seq, stop in enumerate(order):
                t = start + 2 * seq
                hhmm = f"{t // 60:02d}:{t % 60:02d}:00"
                # stop B has no time (interpolated), the rest are timed
                shown = "" if stop == "B" else hhmm
                call = {"trip_id": f"t{k}", "stop_id": stop, "stop_sequence": seq + 1}
                call.update(arrival_time=shown, departure_time=shown, pickup_type="0")
                times.append(call)
    calendar = [{"service_id": "wk", "monday": 1, "tuesday": 1, "wednesday": 1, "thursday": 1,
                 "friday": 1, "saturday": 0, "sunday": 0,
                 "start_date": "20261101", "end_date": "20261231"}]  # fmt: skip
    return {
        "stops.txt": _csv(stops),
        "trips.txt": _csv(trips),
        "stop_times.txt": _csv(times),
        "calendar.txt": _csv(calendar),
        "calendar_dates.txt": _csv([{"service_id": "wk", "date": "20261126", "exception_type": 2}]),
    }


def _set(feed: dict[str, bytes], name: str, frame: pd.DataFrame) -> None:
    feed[name] = frame.to_csv(index=False).encode()


def _get(feed: dict[str, bytes], name: str) -> pd.DataFrame:
    return pd.read_csv(pd.io.common.BytesIO(feed[name]), dtype=str, keep_default_na=False)


def test_peaks_count_per_platform_and_hour_on_the_pinned_day():
    table, note = tp.read_gtfs_peaks(_feed(headway_in=10, headway_out=30), date(2026, 12, 2))
    assert note["date"] == "2026-12-02" and note["day_rule"] == "pinned"
    assert note["rule"]["version"] == tp.RULE_VERSION
    assert note["rule"]["opposite_heading_deg"] == 120.0
    a = table[table["stop_id"] == "A"].set_index("direction")
    # A is the outbound trips' last stop: only inbound trips board there, so one side
    assert list(a.index) == ["all"] and a.loc["all", "h07"] == 6 and a.loc["all", "h08"] == 6
    c = table[table["stop_id"] == "C"].set_index("direction")
    assert list(c.index) == ["all"] and c.loc["all", "h17"] == 2
    # B is served both ways (north every 10 min, south every 30): two sides
    b = table[table["stop_id"] == "B"].set_index("direction")
    assert b.loc["main", "h07"] == 6 and b.loc["opposite", "h07"] == 2
    assert note["split_poles"] == 1
    # the station S gets one row per platform side, never a sum of its platforms
    s_rows = table[table["stop_id"] == "S"].set_index("direction")
    assert sorted(s_rows.index) == ["B/main", "B/opposite"]
    assert s_rows.loc["B/main", "h07"] == 6


def test_a_pinned_holiday_falls_back_to_the_busiest_midweek_day():
    _, note = tp.read_gtfs_peaks(_feed(headway_in=10, headway_out=10), date(2026, 11, 26))
    assert note["day_rule"] == "busiest_midweek" and note["date"] != "2026-11-26"


def test_irregular_days_are_holidays_and_the_year_end_break():
    for d in (
        date(2026, 11, 26),
        date(2026, 12, 29),
        date(2026, 12, 24),
        date(2027, 1, 1),
        date(2026, 7, 3),
        date(2027, 1, 18),
        date(2026, 6, 19),
        date(2026, 5, 25),
    ):
        assert tp.is_irregular_day(d), d
    for d in (date(2026, 12, 2), date(2026, 11, 24), date(2027, 1, 5), date(2026, 7, 7)):
        assert not tp.is_irregular_day(d), d


def test_a_holiday_is_never_the_counted_day_even_when_service_runs():
    feed = _feed(headway_in=10, headway_out=10)
    cal = _get(feed, "calendar_dates.txt")
    _set(feed, "calendar_dates.txt", cal.iloc[0:0])  # Thanksgiving runs a normal schedule
    _, note = tp.read_gtfs_peaks(feed, date(2026, 11, 26))
    assert note["day_rule"] == "busiest_midweek" and note["date"] != "2026-11-26"
    # the busiest day is the year-end break: extra trips on Dec 29 are skipped
    extra = _get(feed, "trips.txt").assign(service_id="xmas")
    trips = pd.concat([_get(feed, "trips.txt"), extra.assign(trip_id=extra["trip_id"] + "x")])
    _set(feed, "trips.txt", trips)
    xmas = [{"service_id": "xmas", "date": "20261229", "exception_type": "1"}]
    _set(feed, "calendar_dates.txt", pd.DataFrame(xmas))
    _, note = tp.read_gtfs_peaks(feed, None)
    assert note["date"] != "2026-12-29"
    assert not tp.is_irregular_day(date.fromisoformat(note["date"]))


def test_frequencies_are_expanded_into_runs():
    feed = _feed(headway_in=600, headway_out=600)  # one trip each way
    first = _get(feed, "trips.txt")["trip_id"].iloc[0]
    rows = [
        {
            "trip_id": first,
            "start_time": "07:00:00",
            "end_time": "09:00:00",
            "headway_secs": "600.0",
        },
        {"trip_id": first, "start_time": "", "end_time": "10:00:00", "headway_secs": "600"},
        {"trip_id": first, "start_time": "10:00:00", "end_time": "11:00:00", "headway_secs": "x"},
    ]
    _set(feed, "frequencies.txt", pd.DataFrame(rows))
    table, note = tp.read_gtfs_peaks(feed, date(2026, 12, 2))
    a = table[(table["stop_id"] == "A") & (table["direction"] == "all")].iloc[0]
    assert a["h07"] == 6 and a["h08"] == 6 and a["h06"] == 0 and a["h10"] == 0
    assert note["frequencies_skipped"] == 2  # blank start and unreadable headway: skipped


def test_frequency_offsets_start_at_the_first_stop_even_without_boarding():
    feed = _feed(headway_in=600, headway_out=600)
    first = _get(feed, "trips.txt")["trip_id"].iloc[0]
    st = _get(feed, "stop_times.txt")
    st.loc[(st["trip_id"] == first) & (st["stop_id"] == "A"), "pickup_type"] = "1"
    _set(feed, "stop_times.txt", st)
    one = {"trip_id": first, "start_time": "07:58:30", "end_time": "07:59:30", "headway_secs": 60}
    _set(feed, "frequencies.txt", pd.DataFrame([one]))
    table, _ = tp.read_gtfs_peaks(feed, date(2026, 12, 2))
    b = table[(table["stop_id"] == "B") & (table["direction"] == "main")].iloc[0]
    # B is 2 minutes after A: the run starting at A at 07:58:30 leaves B at 08:00:30
    assert b["h08"] == 1 and b["h07"] == 0
    a = table[table["stop_id"] == "A"].iloc[0]
    assert a["h07"] == 0  # no boarding at A on the frequency trip


def test_direction_ids_of_different_routes_are_not_compared():
    """Two routes leave pole A the same way, coded direction 0 and direction 1."""
    feed = _feed(headway_in=20, headway_out=600)
    trips = _get(feed, "trips.txt")
    inbound = trips["direction_id"] == "0"
    second = inbound & (trips.index % 2 == 1)
    trips.loc[second, "route_id"] = "r2"
    trips.loc[second, "direction_id"] = "1"  # same street, other route's numbering
    _set(feed, "trips.txt", trips)
    table, _ = tp.read_gtfs_peaks(feed, date(2026, 12, 2))
    a = table[table["stop_id"] == "A"].set_index("direction")
    assert list(a.index) == ["all"]
    assert a.loc["all", "h07"] + a.loc["all", "h08"] == 6  # every 20 min: both routes added
    counts = tw.peak_counts(table, tw.PeakRule(20.0, (7, 9), (16, 18)))
    assert counts.loc["A", "am"] == 6  # with per-direction_id counting it would be 3


def test_headsign_variants_are_one_direction():
    assert tp.headsign_key("Downtown") == tp.headsign_key("DOWNTOWN via Main St") == "downtown"
    assert tp.headsign_key("Downtown (Express)") == "downtown"
    assert tp.headsign_key("Uptown") != tp.headsign_key("Downtown")
    plain = tp.read_gtfs_peaks(_feed(headway_in=10, headway_out=30), date(2026, 12, 2))[0]
    feed = _feed(headway_in=10, headway_out=30, with_direction=False)
    trips = _get(feed, "trips.txt")
    trips["trip_headsign"] = np.where(
        trips.index % 2 == 0, trips["trip_headsign"], trips["trip_headsign"] + " via Main St"
    )
    _set(feed, "trips.txt", trips)
    table, _ = tp.read_gtfs_peaks(feed, date(2026, 12, 2))
    pd.testing.assert_frame_equal(table, plain)  # headings decide; headsigns never split


def test_a_trip_without_a_heading_takes_its_route_directions_side():
    calls = pd.DataFrame(
        {
            "stop_id": ["P"] * 5 + ["Q"],
            "route_id": ["r", "r", "r", "r", "s", "r"],
            "dkey": ["d0", "d1", "d1", "d1", "d0", "d0"],
            "heading": [0.0, 180.0, 175.0, np.nan, np.nan, np.nan],
        }
    )
    side = tp._sides(calls, 120.0).tolist()
    # r/d1 without a heading joins r/d1's side; s/d0 has no known side: main;
    # Q has no heading at all: one side
    assert side == ["main", "opposite", "opposite", "opposite", "main", "all"]


def test_routes_turning_opposite_ways_at_a_corner_stay_one_side():
    """North up Main St to pole M, then route r1 turns east and r2 turns west."""
    stops = [
        {"stop_id": "S0", "stop_lat": 40.0, "stop_lon": -75.0},
        {"stop_id": "M", "stop_lat": 40.002, "stop_lon": -75.0},
        {"stop_id": "E", "stop_lat": 40.002, "stop_lon": -74.997},
        {"stop_id": "W", "stop_lat": 40.002, "stop_lon": -75.003},
    ]
    trips, times = [], []
    for k in range(16):
        route, end = ("r1", "E") if k % 2 else ("r2", "W")
        trips.append({"trip_id": f"k{k}", "route_id": route, "service_id": "wk",
                      "direction_id": "0", "trip_headsign": end})  # fmt: skip
        t = 7 * 60 + 7 * k
        for seq, stop in enumerate(["S0", "M", end]):
            hhmm = f"{(t + seq) // 60:02d}:{(t + seq) % 60:02d}:00"
            call = {"trip_id": f"k{k}", "stop_id": stop, "stop_sequence": seq + 1}
            call.update(arrival_time=hhmm, departure_time=hhmm, pickup_type="0")
            times.append(call)
    base = _feed(headway_in=10, headway_out=10)
    feed = {
        "stops.txt": _csv(stops),
        "trips.txt": _csv(trips),
        "stop_times.txt": _csv(times),
        "calendar.txt": base["calendar.txt"],
        "calendar_dates.txt": base["calendar_dates.txt"],
    }
    table, note = tp.read_gtfs_peaks(feed, date(2026, 12, 2))
    m = table[table["stop_id"] == "M"].set_index("direction")
    assert list(m.index) == ["all"] and note["split_poles"] == 0
    assert m.loc["all", "h07"] + m.loc["all", "h08"] == 16


def _peak_rows(spec: list[tuple[str, str, int, int]]) -> pd.DataFrame:
    hours = tp.hour_columns()
    rows = []
    for sid, direction, am, pm in spec:
        r = {"stop_id": sid, "lat": 40.0, "lon": -75.0 + 0.01 * len(rows), "direction": direction}
        r.update(dict.fromkeys(hours, 0))
        r.update({"h07": am // 2, "h08": am - am // 2, "h16": pm // 2, "h17": pm - pm // 2})
        rows.append(r)
    return pd.DataFrame(rows)


def test_commuter_only_stop_is_not_frequent_and_platforms_are_not_added():
    peaks = _peak_rows(
        [
            ("w", "main", 8, 0), ("w", "opposite", 0, 8),  # 8 one way am, 8 the other pm
            ("s", "P1/all", 5, 5), ("s", "P2/all", 5, 5),  # two weak platforms
            ("t", "P1/all", 5, 5), ("t", "P2/main", 9, 8),  # one good platform side
        ]
    )  # fmt: skip
    counts = tw.peak_counts(peaks, RULE)
    need = RULE.min_departures(RULE.am)
    passes = (counts["am"] >= need) & (counts["pm"] >= need)
    assert passes.to_dict() == {"s": False, "t": True, "w": False}
    assert (counts.loc["t", "am"], counts.loc["t", "pm"]) == (9, 8)


def test_frequent_rule_needs_both_peaks_on_the_same_platform_side():
    hours = tp.hour_columns()
    rows = []
    for sid, am, pm in (("x", 8, 8), ("y", 8, 7), ("z", 3, 8)):
        r = {"stop_id": sid, "lat": 40.0, "lon": -75.0 + 0.01 * len(rows), "direction": "main"}
        r.update(dict.fromkeys(hours, 0))
        r.update({"h07": am // 2, "h08": am - am // 2, "h16": pm // 2, "h17": pm - pm // 2})
        rows.append(r)
    # z's other side is busy in the morning only: no single side passes both windows
    extra = {**rows[2], "direction": "opposite", "h07": 4, "h08": 4, "h16": 0, "h17": 0}
    peaks = pd.DataFrame([*rows, extra])
    counts = tw.peak_counts(peaks, RULE)
    assert not (counts.loc["z", "am"] >= 8 and counts.loc["z", "pm"] >= 8)
    assert RULE.min_departures(RULE.am) == 8
    stops = pd.DataFrame(
        {
            "stop_id": ["F:x", "F:y", "F:z", "F:far", "G:q", "R:1"],
            "lat": [40.0, 40.0, 40.0, 40.5, 40.0, 40.0],
            "lon": [-75.0, -74.99, -74.98, -75.0, -75.0, -75.0],
            "mode": ["bus", "bus", "bus", "bus", "bus", "rail"],
            "feed_id": ["F", "F", "F", "F", "G", "R"],
            "county": ["c1"] * 6,
            "tract": ["t1"] * 6,
            "major": [1, 1, 1, 0, 1, 1],
            "stop_name": [""] * 6,
        }
    )
    feeds = pd.DataFrame(
        {
            "feed_id": ["F", "G", "R"],
            "schedule_feed": ["11", "12", ""],
            "frequency": ["counted", "unknown: the only keyless schedule ended 2023-01-01", "x"],
            "bus_stops": [4, 1, 0],
        }
    )
    out = tw.frequent_bus_stops(stops, feeds, {"11": peaks, "12": peaks}, RULE)
    assert out.set_index("stop_id")["frequent"].to_dict() == {
        "F:x": 1, "F:y": 0, "F:z": 0, "F:far": 0, "G:q": 0, "R:1": 0,
    }  # fmt: skip
    assert math.isnan(out.loc[3, "am"])  # too far from any schedule stop: not counted
    assert math.isnan(out.loc[4, "am"])  # expired schedule: not counted
    major = tw.major_stops(out)
    assert sorted(major["kind"]) == ["bus", "rail"]
    cov = tw.county_coverage(out, {"11": {"date": "2026-12-02", "day_rule": "pinned"}}, feeds)
    row = cov.iloc[0]
    assert (row["bus_stops"], row["bus_stops_counted"], row["frequent_bus_stops"]) == (5, 3, 1)
    assert row["d07_bus_stops"] == 4 and row["rail_stops"] == 1
    assert row["schedule_dates"] == "11: 2026-12-02 (pinned)"


def test_a_count_under_another_rule_is_redone(tmp_path, monkeypatch):
    folder = tmp_path / "transit"
    folder.mkdir()
    old = {"status": "ok", "rule": {"version": 1}, "date": "2026-12-02"}
    (folder / "gtfs_7_peaks_note.json").write_text(json.dumps(old))
    (folder / "gtfs_7_note.json").write_text(
        json.dumps({"representative_date": "2026-12-02", "url": "https://example.org/g.zip"})
    )
    feed = _feed(headway_in=10, headway_out=30)
    monkeypatch.setattr(tp.transit_src, "_read_members", lambda _c, _u: (feed, "whole"))
    table, note = tp.fetch_peaks("7", tmp_path, client=object(), manifest=None)
    assert note["rule"]["version"] == tp.RULE_VERSION and table is not None
    again, note2 = tp.fetch_peaks("7", tmp_path, client=object(), manifest=None)
    assert note2 == note and len(again) == len(table)  # same rule: reused


def test_ledger_holds_the_rule_numbers():
    ledger = load_ledger()
    rule = tw.PeakRule.from_ledger(ledger)
    assert rule == tw.PeakRule(15.0, (7, 9), (16, 18))
    walk = ws.WalkRule.from_ledger(ledger)
    assert walk.snap_m > 0 and walk.edge_buffer_m > 0 and walk.min_component_m > 0
    rules = tw.load_rules()
    assert rules["network"]["distances_miles"] == [0.25, 0.5, 0.75]
    assert "motorway" in rules["osm"]["exclude_highway"]


# --------------------------------------------------------------------------- geometry


def _grid(step: float = 100.0, half: int = 10, bridge_y: float | None = None):
    """A street grid from -half*step to +half*step, split by a river at x = 0.

    No street crosses the river except, if given, one bridge at ``bridge_y``.
    """
    coords = [(i * step, j * step) for i in range(-half, half + 1) for j in range(-half, half + 1)]
    index = {c: k for k, c in enumerate(coords)}
    xy = np.array(coords, dtype=float)
    ways = []
    for j in range(-half, half + 1):
        y = j * step
        west = [index[(i * step, y)] for i in range(-half, 0)]
        east = [index[(i * step, y)] for i in range(1, half + 1)]
        ways += [np.array(west), np.array(east)]
        if bridge_y is not None and y == bridge_y:
            ways.append(np.array([index[(-step, y)], index[(step, y)]]))
    for i in range(-half, half + 1):
        if i == 0:
            continue
        ways.append(np.array([index[(i * step, j * step)] for j in range(-half, half + 1)]))
    return ws.build_network(xy[:, 0], xy[:, 1], ways)


def test_projection_keeps_short_distances():
    x, y = ws.project(np.array([40.0, 40.01]), np.array([-75.0, -75.01]), 40.0, -75.0)
    metres = math.hypot(x[1] - x[0], y[1] - y[0])
    miles = tf.great_circle_miles(
        np.array([40.0]), np.array([-75.0]), np.array([40.01]), np.array([-75.01])
    )[0]
    assert metres == pytest.approx(miles * ws.METRES_PER_MILE, rel=0.004)


def test_reached_segments_cut_edges_at_the_distance():
    net = ws.build_network(np.array([0.0, 100.0]), np.array([0.0, 0.0]), [np.array([0, 1])])
    segs = ws.reached_segments(net, np.array([0.0, np.inf]), 30.0)
    assert segs.shape == (1, 2, 2) and segs[0, 1, 0] == pytest.approx(30.0)
    both = ws.reached_segments(net, np.array([0.0, 20.0]), 60.0)  # 60 + 40 >= 100: whole edge
    assert both.shape == (1, 2, 2) and both[0, 1, 0] == pytest.approx(100.0)


def test_river_without_bridge_cuts_the_network_walkshed():
    net = _grid()
    d = 400.0
    sheds = ws.walkshed_shapes(net, np.array([-50.0]), np.array([0.0]), [d], WALK)
    network, straight = sheds.network[d], sheds.straight[d]
    assert sheds.joined.all()
    assert sheds.area(network) < sheds.area(straight)
    assert not (network & ~straight).any()  # never outside the straight-line circle
    east = (0, -2000, 2000, 2000)
    # Only the 50 m edge buffer of the west bank's last street crosses the river line.
    assert sheds.area(network, east) < 0.02 * sheds.area(straight)
    assert sheds.area(straight, east) > 0.4 * sheds.area(straight)


def test_a_bridge_far_away_does_not_help_but_a_near_one_does():
    far = _grid(bridge_y=900.0)
    near = _grid(bridge_y=0.0)
    d = 400.0
    east = (50, -2000, 2000, 2000)
    a = ws.walkshed_shapes(far, np.array([-50.0]), np.array([0.0]), [d], WALK)
    b = ws.walkshed_shapes(near, np.array([-50.0]), np.array([0.0]), [d], WALK)
    assert a.area(a.network[d], east) < 1.0
    assert b.area(b.network[d], east) > 10_000


def test_overlapping_stops_are_dissolved():
    net = _grid()
    d = 300.0
    one = ws.walkshed_shapes(net, np.array([-500.0]), np.array([0.0]), [d], WALK)
    two = ws.walkshed_shapes(net, np.array([-500.0, -500.0]), np.array([0.0, 10.0]), [d], WALK)
    assert two.area(two.network[d]) == pytest.approx(one.area(one.network[d]), rel=0.05)
    assert two.area(two.straight[d]) < 1.1 * one.area(one.straight[d])


def test_stop_off_the_network_falls_back_to_a_straight_line():
    net = _grid()
    d = 400.0
    sheds = ws.walkshed_shapes(net, np.array([-500.0, 5000.0]), np.array([0.0, 0.0]), [d], WALK)
    assert sheds.joined.tolist() == [True, False]
    circle = math.pi * d**2
    assert sheds.area(sheds.fallback[d]) == pytest.approx(circle, rel=0.01)
    forced = ws.walkshed_shapes(
        net, np.array([-500.0]), np.array([0.0]), [d], WALK, force_fallback=np.array([True])
    )
    assert not forced.joined.any() and not forced.network[d].any()


def test_small_network_piece_counts_as_incomplete():
    xs = np.array([0.0, 100.0, 200.0])
    net = ws.build_network(xs, np.zeros(3), [np.array([0, 1, 2])])  # 200 m in all
    assert ws.component_lengths(net).tolist() == [200.0, 200.0, 200.0]
    _, ok, _ = ws.seed_stops(net, np.array([10.0]), np.array([0.0]), WALK)
    assert not ok[0]  # 200 m < 500 m minimum piece
    _, ok2, _ = ws.seed_stops(net, np.array([10.0]), np.array([0.0]), ws.WalkRule(80, 50, 100))
    assert ok2[0]


def test_stop_report_measures_each_stop_alone():
    net = _grid()
    res = ws.stop_walksheds(net, np.array([-50.0, -500.0]), np.array([0.0, 0.0]), 400.0, WALK)
    (j1, n1, c1), (j2, n2, c2) = res
    assert j1 and j2 and c1 == pytest.approx(c2)
    assert n1 / c1 < n2 / c2  # the stop next to the river reaches less land


def test_grid_areas_are_within_one_percent_at_the_default_cell():
    """The cell size (assumptions/transit_walk.yaml network.cell_m) keeps area errors small."""
    cell = float(tw.load_rules()["network"]["cell_m"])
    assert cell == ws.CELL_M
    for r in (402.336, 804.672, 1207.008):  # 1/4, 1/2, 3/4 mile
        g = ws.walkshed_grid(np.array([3.3]), np.array([-7.1]), r, cell)
        disc = planar.fill_discs(g, np.array([3.3]), np.array([-7.1]), r)
        assert disc.sum() * cell**2 == pytest.approx(math.pi * r * r, rel=0.002)
    # a 400 m street at an angle, widened by the 50 m edge buffer: 2wL + pi w^2
    seg = np.array([[[0.0, 0.0], [400 * math.cos(0.3), 400 * math.sin(0.3)]]])
    g = planar.grid_for_bounds(-100, -100, 500, 300, cell)
    ribbon = planar.fill_capsules(g, seg, 50.0)
    exact = 2 * 50 * 400 + math.pi * 50**2
    assert ribbon.sum() * cell**2 == pytest.approx(exact, rel=0.01)
    # many short segments in a street grid: the union is still within 1%
    net = _grid(step=60.0, half=8)
    segs = ws.reached_segments(net, np.zeros(net.n_nodes), 1e9)
    g = planar.grid_for_bounds(-600, -600, 600, 600, cell)
    fine = planar.grid_for_bounds(-600, -600, 600, 600, 1.0)
    coarse_area = planar.fill_capsules(g, segs, 20.0).sum() * cell**2
    fine_area = float(planar.fill_capsules(fine, segs, 20.0).sum())
    assert coarse_area == pytest.approx(fine_area, rel=0.01)


def test_tract_areas_count_cells_by_tract():
    g = planar.grid_for_bounds(0, 0, 200, 100, 10.0)
    west = [[np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=float)]]
    east = [[np.array([[100, 0], [200, 0], [200, 100], [100, 100]], dtype=float)]]
    mask = np.zeros((g.rows, g.cols), dtype=bool)
    mask[:, :15] = True  # x from 0 to 150
    areas = tw.tract_areas(g, [west, east], {"m": mask})
    assert areas[:, 0].tolist() == [10_000.0, 5_000.0]
    assert planar.polygon_area(west) == 10_000.0


# --------------------------------------------------------------------------- OSM tiles


def _answer(nodes: dict[int, tuple[float, float]], ways: dict[int, list[int]]) -> dict:
    els = [{"type": "node", "id": k, "lat": la, "lon": lo} for k, (la, lo) in nodes.items()]
    els += [{"type": "way", "id": k, "nodes": v} for k, v in ways.items()]
    return {"elements": els}


def test_parse_overpass_joins_tiles_and_splits_ways_at_missing_nodes():
    a = _answer({1: (40.0, -75.0), 2: (40.0, -75.001), 3: (40.0, -75.002)}, {10: [1, 2, 3]})
    b = _answer(
        {3: (40.0, -75.002), 5: (40.0, -75.004), 6: (40.0, -75.005)},
        {10: [1, 2, 3], 11: [3, 4, 5, 6]},
    )
    o = osm.parse_overpass([a, b])
    assert o.node_id.tolist() == [1, 2, 3, 5, 6]
    assert [o.node_id[w].tolist() for w in o.ways] == [[1, 2, 3], [5, 6]]


def test_tiles_and_query():
    tiles = osm.tiles_for_points([40.05], [-75.05], 1300, 0.1)
    assert tiles == [(400, -751)]
    edge = osm.tiles_for_points([40.001], [-75.05], 1300, 0.1)
    assert edge == [(399, -751), (400, -751)]
    rules = tw.load_rules()["osm"]
    q = osm.overpass_query(osm.tile_bbox((400, -751), 0.1), rules)
    assert "(40.0000,-75.1000,40.1000,-75.0000)" in q and "motorway" in q and "out skel" in q


class _Client:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def get_bytes(self, url, params=None):
        self.calls.append(url)
        got = self.answers.get(url)
        if got is None:
            raise FetchError("down")
        return got


def test_fetch_tiles_caches_and_reports_failures(tmp_path):
    rules = dict(tw.load_rules()["osm"])
    rules["endpoints"] = ["https://one.example/api", "https://two.example/api"]
    body = json.dumps(_answer({1: (40.0, -75.0)}, {})).encode()
    client = _Client({"https://two.example/api": body})
    got, failed = osm.fetch_tiles([(400, -751)], tmp_path, rules, client=client, manifest=None)
    assert failed == [] and len(got) == 1 and client.calls == rules["endpoints"]
    with gzip.open(got[0]) as fh:
        assert json.loads(fh.read())["elements"][0]["id"] == 1
    again, _ = osm.fetch_tiles([(400, -751)], tmp_path, rules, client=_Client({}), manifest=None)
    assert again == got  # reused from the cache
    _, failed = osm.fetch_tiles([(401, -751)], tmp_path, rules, client=_Client({}), manifest=None)
    assert failed == [(401, -751)]


# --------------------------------------------------------------------------- tract table


def test_build_transit_walk_makes_tract_rows_on_a_tiny_map(tmp_path):
    """Two tracts split by a river; a rail stop on the west bank; the east tract is cut off."""
    lat0, lon0 = 40.0, -75.0
    net_pts, ways = {}, {}
    step_lat, step_lon = 100 / 111_000, 100 / 85_000
    k = 0
    grid = {}
    for i in range(-12, 13):
        for j in range(-12, 13):
            k += 1
            grid[(i, j)] = k
            net_pts[k] = (lat0 + j * step_lat, lon0 + i * step_lon)
    w = 0
    for j in range(-12, 13):
        w += 1
        ways[w] = [grid[(i, j)] for i in range(-12, 0)]
        w += 1
        ways[w] = [grid[(i, j)] for i in range(1, 13)]
    for i in range(-12, 13):
        if i:
            w += 1
            ways[w] = [grid[(i, j)] for j in range(-12, 13)]
    tile = tmp_path / "t.json.gz"
    with gzip.open(tile, "wb") as fh:
        fh.write(json.dumps(_answer(net_pts, ways)).encode())
    half = 12 * step_lon

    def box(x0: float, x1: float) -> dict:
        ring = [[x0, lat0 - 0.011], [x1, lat0 - 0.011], [x1, lat0 + 0.011], [x0, lat0 + 0.011]]
        return {"type": "Polygon", "coordinates": [[*ring, ring[0]]]}

    west, east = box(lon0 - half, lon0), box(lon0, lon0 + half)
    stops = pd.DataFrame(
        {
            "stop_id": ["R:1"],
            "stop_name": ["West bank"],
            "lat": [lat0],
            "lon": [lon0 - 0.5 * step_lon],
            "mode": ["rail"],
            "frequent": [0],
            "county": ["99001"],
            "tract": ["99001000100"],
        }
    )
    rules = tw.load_rules()
    tract, county, report = tw.build_transit_walk(
        stops,
        [("99001000100", west), ("99001000200", east)],
        RULE,
        WALK,
        rules,
        lambda tiles: ([tile], []),
        osm.read_tiles,
        stop_report=True,
    )
    assert list(tract.columns) == list(tw.TRACT_COLUMNS)
    t = tract.set_index("geoid")
    assert t.loc["99001000100", "major_rail"] == 1
    for band in ("0_25mi", "0_50mi", "0_75mi"):
        assert (t[f"network_acres_{band}"] <= t[f"straight_acres_{band}"] + 0.1).all()
        assert (t[f"fallback_acres_{band}"] == 0).all()
    # the east tract is near in a straight line but cut off by the river
    assert t.loc["99001000200", "straight_acres_0_25mi"] > 40
    assert (
        t.loc["99001000200", "network_acres_0_25mi"]
        < 0.1 * t.loc["99001000200", "straight_acres_0_25mi"]
    )
    assert county.iloc[0]["fallback_stops"] == 0 and county.iloc[0]["osm_tiles"] >= 1
    assert report is not None and report.iloc[0]["ratio"] < 0.7
    # every column has a dictionary entry
    for col in (*tw.TRACT_COLUMNS, *tw.COUNTY_COLUMNS):
        unit, text = tw.describe("transit_walk_tract", col)
        assert unit and text


def test_cli_needs_a_built_pack(tmp_path, capsys):
    from visionhousing import cli

    assert cli.main(["transit-walk", "--state", "VT", "--out", str(tmp_path)]) == 1
    assert "build the pack first" in capsys.readouterr().err
