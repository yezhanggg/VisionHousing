"""Transit: modes, the representative weekday, departure counts, stop matching and tract features.

Every case is small enough to check by hand. No network: feeds are built in memory
and served by a fake server.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.pack_fakes import FileServer, Response, client, zip_of
from visionhousing.data.http import FetchError
from visionhousing.data.sources import transit as tr
from visionhousing.features import transit as tf
from visionhousing.ledger import load_ledger
from visionhousing.scenarios.parking import transit_rich_tracts

TESTLAND = Path(__file__).resolve().parent / "fixtures" / "testland"
MILE_LAT = 180 / (np.pi * tf.EARTH_RADIUS_MILES)  # degrees of latitude in one mile
RULE = tf.TransitRule(64, 0.5, 0.25, 0.5)


def csv(text: str) -> bytes:
    return "\n".join(line.strip() for line in text.strip().splitlines()).encode() + b"\n"


# --------------------------------------------------------------------------- modes


@pytest.mark.parametrize(
    ("route_type", "group"),
    [(0, "rail"), (1, "rail"), (2, "rail"), (7, "rail"), (3, "bus"), (11, "bus"), (4, "other"),
     (6, "other"), ("109", "rail"), ("700", "bus"), ("x", "other")],
)  # fmt: skip
def test_route_types_are_grouped(route_type, group):
    assert tr.route_group(route_type) == group


# --------------------------------------------------------------------------- weekday


def test_representative_weekday_applies_exceptions():
    # Weekday service W runs Mon-Fri for one week (2026-12-07 .. 12-11). Tue 12-08 is
    # removed (holiday); Thu 12-10 gets an extra service X with 2 trips.
    calendar = pd.DataFrame(
        [{"service_id": "W", "monday": "1", "tuesday": "1", "wednesday": "1",
          "thursday": "1", "friday": "1", "saturday": "0", "sunday": "0",
          "start_date": "20261207", "end_date": "20261211"}]
    )  # fmt: skip
    dates = pd.DataFrame(
        [
            {"service_id": "W", "date": "20261208", "exception_type": "2"},
            {"service_id": "X", "date": "20261210", "exception_type": "1"},
        ]
    )
    trips = pd.DataFrame({"service_id": ["W"] * 3 + ["X"] * 2, "trip_id": list("abcde")})
    day, services, n = tr.representative_weekday(calendar, dates, trips)
    assert day == date(2026, 12, 10)
    assert services == {"W", "X"}
    assert n == 5


def test_ties_go_to_the_earliest_midweek_day():
    calendar = pd.DataFrame(
        [{"service_id": "W", "monday": "1", "tuesday": "1", "wednesday": "1",
          "thursday": "1", "friday": "1", "saturday": "0", "sunday": "0",
          "start_date": "20261207", "end_date": "20261218"}]
    )  # fmt: skip
    trips = pd.DataFrame({"service_id": ["W"], "trip_id": ["a"]})
    day, _, n = tr.representative_weekday(calendar, pd.DataFrame(), trips)
    assert day == date(2026, 12, 8)  # the first Tuesday
    assert n == 1


# --------------------------------------------------------------------------- departures


def feed_members(frequent: bool = True) -> dict[str, bytes]:
    """A tiny feed: route R1 (bus) calls A -> B -> C; station P has platforms P1, P2."""
    stop_times = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type"]
    for k in range(3):  # three plain trips A, B, C; at B pickup is not allowed on trip t2
        t = f"t{k}"
        stop_times += [
            f"{t},08:0{k}:00,08:0{k}:00,A,1,0",
            f"{t},08:1{k}:00,08:1{k}:00,B,2,{1 if k == 2 else 0}",
            f"{t},08:2{k}:00,08:2{k}:00,C,3,0",
        ]
    stop_times += ["f1,07:00:00,07:00:00,P1,1,0", "f1,07:05:00,07:05:00,P2,2,0"]
    members = {
        "agency.txt": csv("agency_id,agency_name\n1,Tiny Transit"),
        "stops.txt": csv(
            """stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station
            A,A,40.0,-75.0,0,
            B,B,40.001,-75.0,0,
            C,C,40.002,-75.0,0,
            P,Station P,40.01,-75.0,1,
            P1,P north,40.0101,-75.0,0,P
            P2,P south,40.0099,-75.0,0,P"""
        ),
        "routes.txt": csv("route_id,route_type\nR1,3\nL1,0"),
        "trips.txt": csv(
            "route_id,service_id,trip_id\nR1,W,t0\nR1,W,t1\nR1,W,t2\nL1,W,f1\nR1,S,t9"
        ),
        "stop_times.txt": "\n".join(stop_times).encode() + b"\n",
        "calendar.txt": csv(
            """service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date
            W,1,1,1,1,1,0,0,20261207,20261211
            S,0,0,0,0,0,1,1,20261207,20261211"""
        ),
    }
    if frequent:  # f1 runs every 10 minutes from 07:00 to 08:00: 6 runs
        members["frequencies.txt"] = csv(
            "trip_id,start_time,end_time,headway_secs\nf1,07:00:00,08:00:00,600"
        )
    return members


def test_departures_skip_last_stops_and_no_pickup_and_count_frequency_runs():
    table, note = tr.read_gtfs(feed_members())
    deps = dict(zip(table["stop_id"], table["weekday_departures"], strict=True))
    assert deps["A"] == 3  # three trips leave A
    assert deps["B"] == 2  # t2 lets no one board at B
    assert deps["C"] == 0  # the last stop of every trip
    assert deps["P1"] == 6  # one frequency trip, six runs
    assert deps["P2"] == 0  # its last stop
    assert deps["P"] == 6  # a station sums its platforms
    groups = dict(zip(table["stop_id"], table["route_groups"], strict=True))
    assert groups["A"] == "bus"
    assert groups["P"] == "rail"
    assert note["representative_date"] == "2026-12-08"
    assert note["trips_on_date"] == 4
    assert note["agency_names"] == ["Tiny Transit"]
    assert (note["valid_from"], note["valid_to"]) == ("2026-12-07", "2026-12-11")


def test_without_frequencies_a_template_trip_counts_once():
    table, _ = tr.read_gtfs(feed_members(frequent=False))
    assert table.set_index("stop_id").loc["P1", "weekday_departures"] == 1


# --------------------------------------------------------------------------- fetching


class FeatureServer:
    """Answers the map-service queries: a count, then pages by resultOffset."""

    def __init__(self, rows: list[dict], page: int = 2):
        self.rows, self.page, self.calls = rows, page, 0

    def open(self, req, data=None, timeout=None):
        self.calls += 1
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))
        if q.get("returnCountOnly") == "true":
            return Response(200, json.dumps({"count": len(self.rows)}).encode())
        start = int(q["resultOffset"])
        part = self.rows[start : start + self.page]
        more = start + self.page < len(self.rows)
        body = {"features": [{"attributes": r} for r in part], "exceededTransferLimit": more}
        return Response(200, json.dumps(body).encode())


def ntm_row(feed: str, sid: str, lat: float, lon: float, types: str, **kw) -> dict:
    return {"ntd_id": feed.split("_")[0], "feed_id": feed, "stop_id": sid, "stop_name": sid,
            "stop_lat": lat, "stop_lon": lon, "location_type": kw.get("loc", "0"),
            "parent_station": kw.get("parent", ""), "stop_type": types}  # fmt: skip


def test_stop_pages_are_joined_and_cached(tmp_path, monkeypatch):
    rows = [ntm_row("1_1", f"s{i}", 40 + i / 100, -75, "3") for i in range(5)]
    server = FeatureServer(rows, page=2)
    frame = tr.fetch_ntm_stops(
        "PA", (-76, 39, -74, 41), tmp_path, client=client(server), manifest=None
    )
    assert list(frame["stop_id"]) == [f"s{i}" for i in range(5)]
    calls = server.calls
    again = tr.fetch_ntm_stops(
        "PA", (-76, 39, -74, 41), tmp_path, client=client(server), manifest=None
    )
    assert server.calls == calls  # read from the cache
    pd.testing.assert_frame_equal(frame, again)


def test_a_page_with_an_error_raises():
    with pytest.raises(FetchError):
        tr.read_ntm_page(b'{"error": {"code": 400, "message": "bad"}}')


def test_feed_is_read_by_range_and_cached_and_failures_are_recorded(tmp_path):
    url = "https://agency.example/gtfs.zip"
    members = feed_members()
    members["shapes.txt"] = np.random.default_rng(1).bytes(2_000_000)  # does not compress
    server = FileServer({url: zip_of(members, stored=False)})
    feed = {"mdb_source_id": "7", "provider": "Tiny", "status": "active",
            "urls.direct_download": "http://agency.example/gtfs.zip",
            "urls.latest": ""}  # fmt: skip
    table, note = tr.fetch_gtfs_feed(feed, tmp_path, client=client(server), manifest=None)
    assert note["status"] == "ok" and note["read"] == "range" and note["url"] == url
    assert server.sent < len(server.files[url]) / 2  # shapes.txt was never downloaded
    sent = server.sent
    again, note2 = tr.fetch_gtfs_feed(feed, tmp_path, client=client(server), manifest=None)
    assert server.sent == sent and note2 == note
    pd.testing.assert_frame_equal(table, again)

    dead = {**feed, "mdb_source_id": "8", "urls.direct_download": "https://gone.example/x.zip"}
    table, note = tr.fetch_gtfs_feed(dead, tmp_path, client=client(FileServer({})), manifest=None)
    assert table is None and note["status"] == "unreachable" and note["errors"]


def test_select_feeds_drops_keyed_and_superseded_feeds():
    cat = pd.DataFrame(
        {
            "mdb_source_id": ["1", "2", "3", "4"],
            "provider": ["A", "A", "B", "C"],
            "status": ["deprecated", "", "deprecated", ""],
            "urls.authentication_type": ["0", "", "", "2"],
        }
    )
    assert list(tr.select_feeds(cat)["mdb_source_id"]) == ["2", "3"]


# --------------------------------------------------------------------------- the stop table


def square(geoid: str, west: float, south: float, size: float):
    ring = [[west, south], [west + size, south], [west + size, south + size],
            [west, south + size], [west, south]]  # fmt: skip
    return type("S", (), {"geoid": geoid, "geometry": {"type": "Polygon", "coordinates": [ring]}})


def stop_inputs(valid_to: str = "2026-12-11") -> tr.TransitInputs:
    ntm = pd.DataFrame(
        [
            ntm_row("9_1", "A", 40.0, -75.0, "3"),
            ntm_row("9_1", "B", 40.001, -75.0, "3"),
            ntm_row("9_1", "Z", 40.003, -75.0, "3"),  # not in the feed: unknown
            ntm_row("9_1", "P", 40.01, -75.0, "0", loc="1"),
            ntm_row("9_1", "P1", 40.0101, -75.0, "0", parent="P"),  # its station is listed
            ntm_row("9_1", "E", 40.0, -75.001, "3", loc="2"),  # an entrance
            ntm_row("5_1", "Q", 40.02, -75.0, "3"),  # agency with no feed
            ntm_row("9_1", "OUT", 45.0, -75.0, "2"),  # outside the state
        ]
    ).rename(columns={"stop_lat": "lat", "stop_lon": "lon"})
    table, note = tr.read_gtfs(feed_members())
    note.update(schedule_feed="7", provider="Tiny", fetched_on="2026-12-01", valid_to=valid_to)
    agencies = pd.DataFrame({"ntd_id": ["9", "5"], "agency": ["Tiny Transit", "Other"]})
    return tr.TransitInputs(ntm=ntm[list(tr.NTM_COLUMNS)], agencies=agencies, feeds=[(note, table)])


def test_stop_table_modes_departures_and_major_flags():
    shapes = [square("42001000100", -75.01, 39.99, 0.04)]
    rule = tf.TransitRule(3, 0.5, 0.25, 0.5)  # a bus stop needs 3 departures here
    stops, feeds = tr.build_stop_table(stop_inputs(), shapes, rule)
    s = stops.set_index("stop_id")
    assert list(stops.columns) == list(tr.STOP_COLUMNS)
    assert set(s.index) == {"9_1:A", "9_1:B", "9_1:Z", "9_1:P", "5_1:Q"}
    assert s.loc["9_1:A", ["mode", "weekday_departures", "major", "frequency"]].tolist() == [
        "bus", 3, 1, "counted"]  # fmt: skip
    assert s.loc["9_1:B", "major"] == 0  # 2 departures
    assert s.loc["9_1:Z", "frequency"] == "unknown"
    assert s.loc["9_1:P", ["mode", "major", "frequency"]].tolist() == ["rail", 1, "mode_only"]
    assert s.loc["5_1:Q", "frequency"] == "unknown"
    assert s.loc["9_1:A", "county"] == "42001" and s.loc["9_1:A", "tract"] == "42001000100"
    assert s.loc["9_1:B", "stop_name"] == ""  # names only for major stops
    f = feeds.set_index("feed_id")
    assert f.loc["9_1", "bus_stops_counted"] == 2 and f.loc["9_1", "bus_stops"] == 3
    assert f.loc["9_1", "schedule_date"] == "2026-12-08"
    assert f.loc["5_1", "frequency"].startswith("unknown")


def test_an_expired_schedule_does_not_make_a_stop_major():
    shapes = [square("42001000100", -75.01, 39.99, 0.04)]
    stops, feeds = tr.build_stop_table(
        stop_inputs(valid_to="2023-01-01"), shapes, tf.TransitRule(3, 0.5, 0.25, 0.5)
    )
    s = stops.set_index("stop_id")
    assert s.loc["9_1:A", "frequency"] == "stale" and s.loc["9_1:A", "major"] == 0
    assert "ended 2023-01-01" in feeds.set_index("feed_id").loc["9_1", "frequency"]


# --------------------------------------------------------------------------- geometry


def test_nearest_stop_matches_brute_force():
    rng = np.random.default_rng(7)
    lat, lon = 40 + rng.random(500), -80 + rng.random(500) * 5
    s_lat, s_lon = 40 + rng.random(37), -80 + rng.random(37) * 5
    idx, miles = tf.nearest_stop(lat, lon, s_lat, s_lon, cells=1000)
    full = tf.great_circle_miles(lat[:, None], lon[:, None], s_lat[None, :], s_lon[None, :])
    assert np.array_equal(idx, full.argmin(axis=1))
    assert np.allclose(miles, full.min(axis=1))


def test_great_circle_one_mile_north():
    assert tf.great_circle_miles(40.0, -75.0, 40.0 + MILE_LAT, -75.0) == pytest.approx(1, 1e-9)


def test_points_in_polygons_respects_holes():
    ring = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]
    hole = [[1, 1], [1, 3], [3, 3], [3, 1], [1, 1]]
    shapes = [("donut", {"type": "Polygon", "coordinates": [ring, hole]})]
    got = tf.points_in_polygons(np.array([0.5, 2.0, 5.0]), np.array([0.5, 2.0, 5.0]), shapes)
    assert list(got) == ["donut", "", ""]


def test_station_count_joins_platforms():
    stops = pd.DataFrame(
        {"mode": ["rail", "rail", "rail", "bus"],
         "lat": [40.0, 40.0005, 40.1, 40.0], "lon": [-75.0, -75.0, -75.0, -75.0]}
    )  # fmt: skip
    assert tf.station_count(stops) == 2


# --------------------------------------------------------------------------- tract features


def test_hand_checkable_tract_features():
    """One rail station at (40, -75); blocks due north at 0.2, 0.4, 0.6 and 1.0 mile.

    Block units 10, 20, 30, 40 and land 1, 2, 3, 4 (tract T1). A frequent bus stop
    sits 0.15 mile north of the 1.0-mile block (tract T2 holds the bus stop only).
    Within 1/4 mile of any major stop: blocks at 0.2 (rail) and 1.0 (bus, 0.15 away).
    Within 1/2: add 0.4. Within 3/4: add 0.6 (0.55 from the bus stop).
    D-07 (rail 1/2, bus 1/4): blocks at 0.2, 0.4 and 1.0 = 70 of 100 units.
    """
    blocks = pd.DataFrame(
        {
            "tract": ["T1"] * 4,
            "housing_units": [10, 20, 30, 40],
            "land_area_m2": [1, 2, 3, 4],
            "lat": [40 + d * MILE_LAT for d in (0.2, 0.4, 0.6, 1.0)],
            "lon": [-75.0] * 4,
        }
    )
    stops = pd.DataFrame(
        {
            "stop_id": ["R", "B", "b"],
            "lat": [40.0, 40 + 1.15 * MILE_LAT, 40 + 0.4 * MILE_LAT],
            "lon": [-75.0, -75.0, -75.0],
            "mode": ["rail", "bus", "bus"],
            "weekday_departures": [None, 64, 63],  # the 63-departure stop is not major
        }
    )
    out = tf.tract_transit_features(
        blocks, stops, RULE, stop_tracts=["T1", "T2", "T1"], tracts=["T1", "T2", "T3"]
    ).set_index("tract")
    t1 = out.loc["T1"]
    assert t1["transit_units_0_25mi"] == 50 and t1["transit_land_m2_0_25mi"] == 5
    assert t1["transit_units_0_50mi"] == 70 and t1["transit_units_0_75mi"] == 100
    assert t1["transit_units_share_0_50mi"] == pytest.approx(0.7)
    assert t1["transit_land_share_0_75mi"] == pytest.approx(1.0)
    assert t1["transit_units_d07"] == 70 and t1["transit_units_share_d07"] == pytest.approx(0.7)
    assert t1["transit_major_stops"] == 1 and t1["transit_rich"] == 1
    assert out.loc["T2", "transit_rich"] == 1  # holds a major stop, no homes
    assert out.loc["T3"].drop("transit_rich").sum() == 0 and out.loc["T3", "transit_rich"] == 0
    assert list(out.reset_index().columns) == ["tract", *tf.FEATURE_COLUMNS]


def test_wider_distance_never_covers_fewer_homes():
    blocks = pd.read_csv(TESTLAND / "blocks.csv", dtype={"geoid": str, "tract": str})
    stops = pd.read_csv(TESTLAND / "transit_stops.csv", dtype={"stop_id": str})
    out = tf.tract_transit_features(blocks, stops, RULE)
    q, h, t = (out[f"transit_units_{tf.radius_label(r)}"] for r in tf.DISTANCE_CHOICES_MILES)
    assert (q <= h).all() and (h <= t).all() and t.sum() > q.sum() > 0


def test_testland_flags_match_the_parking_rule():
    """The pack's transit-rich flag and the parking reform's own test agree on Testland."""
    blocks = pd.read_csv(TESTLAND / "blocks.csv", dtype={"geoid": str, "tract": str})
    stops = pd.read_csv(TESTLAND / "transit_stops.csv", dtype={"stop_id": str})
    gj = json.loads((TESTLAND / "boundaries.geojson").read_text())
    tracts = [
        (f["properties"]["geoid"], f["geometry"])
        for f in gj["features"]
        if f["properties"]["level"] == "tract"
    ]
    holder = tf.points_in_polygons(stops["lat"], stops["lon"], tracts)
    ledger = load_ledger()
    ours = tf.tract_transit_features(
        blocks, stops, tf.TransitRule.from_ledger(ledger), stop_tracts=holder
    ).set_index("tract")["transit_rich"]
    theirs = transit_rich_tracts(blocks, stops, ledger, boundaries=gj)
    assert ours.astype(bool).to_dict() == theirs.reindex(ours.index, fill_value=False).to_dict()
    assert 0 < ours.sum() < len(ours)
