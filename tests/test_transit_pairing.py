"""Transit: map-service retries and pairing renumbered stops with their schedule feed.

Found while building Massachusetts: the National Transit Map service sometimes
refuses a valid page under load, and three regional agencies renumbered their
stops, so their schedule feeds matched no map feed by stop id. Pairing by place
must not read an airport operator from the big agency's feed at shared stops
(the reverse check). No network.
"""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from tests.pack_fakes import Response, client
from visionhousing.data.http import FetchError
from visionhousing.data.sources import transit as tr


class Flaky:
    """Answers with an error object ``fails`` times, then with ``body``."""

    def __init__(self, fails: int, body: dict):
        self.fails, self.body, self.calls = fails, body, 0

    def open(self, req, data=None, timeout=None):
        self.calls += 1
        if self.calls <= self.fails:
            return Response(200, json.dumps({"error": {"code": 400, "message": ""}}).encode())
        return Response(200, json.dumps(self.body).encode())


def test_an_error_object_is_asked_again_then_answered():
    server = Flaky(2, {"count": 7})
    c = client(server)
    data = tr.query_ntm(c, "https://example.org/query", {"f": "json"})
    assert json.loads(data) == {"count": 7}
    assert server.calls == 3 and len(c.waits) == 2


def test_an_error_object_every_time_raises():
    with pytest.raises(FetchError, match="after 3 tries"):
        tr.query_ntm(client(Flaky(9, {})), "https://example.org/query", {}, tries=3)


def ntm(feed: str, ntd: str, rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"feed_id": feed, "ntd_id": ntd, "stop_id": s, "lat": la, "lon": lo} for s, la, lo in rows]
    )


def schedule(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    cols = {"weekday_departures": 10, "route_groups": "bus"}
    return pd.DataFrame([{"stop_id": s, "lat": la, "lon": lo, **cols} for s, la, lo in rows])


def test_renumbered_stops_pair_by_place_when_the_agency_names_agree():
    stops = ntm("44_1", "44", [("a", 42.0, -71.0), ("b", 42.01, -71.0), ("c", 42.02, -71.0)])
    feed = schedule([("9001", 42.0, -71.0), ("9002", 42.0101, -71.0), ("9003", 42.03, -71.0)])
    note = {"provider": "Lowell Regional Transit Authority (LRTA)", "agency_names": "['LRTA']"}
    agencies = pd.DataFrame({"ntd_id": ["44"], "agency": ["Lowell Regional Transit Authority"]})
    assert tr._pair_feeds(stops, [(note, feed)], agencies) == {"44_1": (0, "place")}
    assert tr._pair_feeds(stops, [(note, feed)]) == {}  # without names, no place pairing


def test_a_town_shuttle_is_not_read_from_a_big_agencys_feed_at_shared_stops():
    stops = ntm("81_1", "81", [("x", 42.0, -71.0), ("y", 42.01, -71.0)])
    feed = schedule([("1", 42.0, -71.0), ("2", 42.01, -71.0)])
    note = {"provider": "Massachusetts Bay Transportation Authority (MBTA)", "agency_names": ""}
    agencies = pd.DataFrame({"ntd_id": ["81"], "agency": ["Town of Lexington"]})
    assert tr._pair_feeds(stops, [(note, feed)], agencies) == {}


def test_same_ids_still_pair_first():
    stops = ntm("9_1", "9", [("A", 40.0, -75.0), ("B", 40.001, -75.0)])
    feed = schedule([("A", 40.0, -75.0), ("B", 40.001, -75.0)])
    assert tr._pair_feeds(stops, [({"provider": "Tiny"}, feed)]) == {"9_1": (0, "id")}


def test_name_words_drop_generic_words():
    assert tr.name_words("Brockton Area Transit Authority (BAT)") == {"brockton"}
    # state names and words like port, valley, cape, greater are not distinctive
    assert tr.name_words("Massachusetts Bay Transportation Authority") == set()
    assert tr.name_words("Massachusetts Port Authority (Massport)") == {"massport"}
    assert tr.name_words("Pioneer Valley Transit Authority") == {"pioneer"}
    assert tr.name_words("Cape Ann Transportation Authority") == set()
    assert tr.name_words("Greater Attleboro Taunton Regional Transit") == {"attleboro", "taunton"}
    assert tr.name_words("Vermont Pennsylvania County Area") == set()
    # state-name words that are also local place names stay distinctive
    assert tr.name_words("Washington County Transit Authority") == {"washington"}


def _mbta_like() -> tuple[dict, pd.DataFrame]:
    """A big agency: 40 stops on a line, four of them at an airport."""
    rows = [(f"{100 + i}", 42.30 + 0.002 * i, -71.02, f"Main St @ {i} Ave") for i in range(36)]
    rows += [
        ("airA", 42.3660, -71.0200, "Airport Terminal A"),
        ("airB", 42.3670, -71.0180, "Airport Terminal B"),
        ("airC", 42.3680, -71.0160, "Airport Terminal C"),
        ("airE", 42.3690, -71.0140, "Airport Terminal E"),
    ]
    feed = pd.DataFrame(
        [{"stop_id": s, "lat": la, "lon": lo, "stop_name": n, "weekday_departures": 90,
          "route_groups": "bus"} for s, la, lo, n in rows]
    )  # fmt: skip
    note = {
        "provider": "Massachusetts Bay Transportation Authority (MBTA)",
        "agency_names": "['MBTA', 'Massport']",  # partner service listed in the feed
    }
    return note, feed


def test_an_airport_operator_is_not_paired_with_the_big_agency_by_place():
    note, feed = _mbta_like()
    stops = pd.DataFrame(
        [
            {"feed_id": "77_1", "ntd_id": "77", "stop_id": f"MP{i}", "lat": la, "lon": lo,
             "stop_name": n}
            for i, (la, lo, n) in enumerate(
                [(42.36601, -71.02001, "Logan Terminal A Arrivals Curb"),
                 (42.36701, -71.01801, "Logan Terminal B Arrivals Curb"),
                 (42.36801, -71.01601, "Logan Terminal C Arrivals Curb"),
                 (42.36901, -71.01401, "Logan Terminal E Arrivals Curb")]
            )
        ]
    )  # fmt: skip
    # the real names share only the state name: no distinctive word, no place pairing
    agencies = pd.DataFrame({"ntd_id": ["77"], "agency": ["Massachusetts Port Authority"]})
    assert tr._pair_feeds(stops, [(note, feed)], agencies) == {}
    # even with a shared word ("Massport" in the feed's agency list) and every airport
    # stop within 50 m, only 4 of the feed's 40 stops sit at the operator's stops and the
    # stop names differ: the reverse check refuses the pairing
    agencies = pd.DataFrame({"ntd_id": ["77"], "agency": ["Massport Logan Shuttle"]})
    assert tr._pair_feeds(stops, [(note, feed)], agencies) == {}
    # the same stops with the feed's own names would pair (renumbered, same names)
    same = stops.assign(stop_name=feed["stop_name"].iloc[36:].to_numpy())
    assert tr._pair_feeds(same, [(note, feed)], agencies) == {"77_1": (0, "place")}


def test_paired_by_is_recorded_in_the_feed_table():
    note, feed = _mbta_like()
    note = {**note, "schedule_feed": "5", "status": "ok"}
    ntm = pd.DataFrame(
        {
            "feed_id": ["1_1", "1_1"], "ntd_id": ["1", "1"], "stop_id": ["100", "101"],
            "lat": [42.30, 42.302], "lon": [-71.02, -71.02],
        }
    )  # fmt: skip
    pairs = tr._pair_feeds(ntm, [(note, feed)])
    assert pairs == {"1_1": (0, "id")}
    stops = pd.DataFrame(
        {"feed_id": ["1_1", "1_1"], "ntd_id": ["1", "1"], "mode": ["bus", "bus"],
         "frequency": ["counted", "counted"], "major": [1, 0]}
    )  # fmt: skip
    inputs = tr.TransitInputs(
        ntm=ntm, agencies=pd.DataFrame({"ntd_id": ["1"], "agency": ["X"]}), feeds=[(note, feed)]
    )
    table = tr._feed_table(stops, inputs, pairs)
    assert table["paired_by"].tolist() == ["id"]
    assert "paired_by" in tr.FEED_COLUMNS


def test_frequency_rows_skip_bad_rows_and_read_float_headways():
    freq = pd.DataFrame(
        {
            "trip_id": ["a", "a", "a", "a", "b"],
            "start_time": ["07:00:00", "", "08:00:00", "09:00:00", "07:00:00"],
            "end_time": ["08:00:00", "09:00:00", "07:00:00", "10:00:00", "08:00:00"],
            "headway_secs": ["600.0", "600", "600", "0", "300"],
        }
    )
    rows, skipped = tr.frequency_rows(freq, {"a"})
    assert rows == [("a", 25200.0, 28800.0, 600.0)] and skipped == 3
    assert tr.clock_seconds("25:10:00") == 90600.0 and math.isnan(tr.clock_seconds("7h"))
