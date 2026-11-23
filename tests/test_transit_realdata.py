"""Real-data checks for Pennsylvania transit: station count, frequency coverage, homes near stops.

Uses the cached extracts in ``data/raw/transit`` (fetched on first use). Not part of
``make test``; run it with ``make test-realdata``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from visionhousing.data import pack
from visionhousing.data import report_card as rc
from visionhousing.features import transit as tf

pytestmark = pytest.mark.realdata

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


@pytest.fixture(scope="module")
def tables():
    return pack.assemble_tables(pack.load_inputs("PA", RAW))


def test_rail_station_count_is_plausible(tables):
    """Rail stations, platforms joined, land in a band of a few hundred.

    SEPTA Regional Rail (about 150 stations in Pennsylvania), the two subway lines and
    the Norristown line (about 75), the trolleys (several hundred street stops, joined
    in pairs), Pittsburgh's light rail (about 50) and a few Amtrak, PATCO and incline
    stations.
    """
    stops = tables["transit_stops"]
    stations = tf.station_count(stops)
    print(f"rail stops {int((stops['mode'] == 'rail').sum())}, stations {stations}")
    assert 250 <= stations <= 700
    rail = stops[stops["mode"] == "rail"]
    assert rail["feed_id"].eq("30019_1").sum() >= 140  # SEPTA Regional Rail in the state
    assert (rail["county"] == "42003").sum() >= 40  # Pittsburgh light rail and inclines


def test_big_agencies_have_bus_frequencies_counted(tables):
    feeds = tables["transit_feeds"].set_index("feed_id")
    for feed in ("30019_2", "30022_1"):  # SEPTA city and suburban bus; Pittsburgh Regional
        row = feeds.loc[feed]
        assert row["bus_stops_counted"] / row["bus_stops"] > 0.95
        assert row["major_bus_stops"] > 500


def test_share_of_homes_near_a_major_stop(tables):
    tracts, blocks = tables["tracts"], tables["blocks"]
    units = blocks["housing_units"].sum()
    half = tracts["transit_units_0_50mi"].sum() / units
    d07 = tracts["transit_units_d07"].sum() / units
    print(f"PA homes within 1/2 mile of a major stop: {half:.1%}; D-07 distance: {d07:.1%}")
    assert 0.12 <= half <= 0.35
    assert 0.10 <= d07 <= half
    phila = tracts[tracts["county"] == "42101"]
    assert phila["transit_units_d07"].sum() / phila["housing_units_2020"].sum() > 0.8


def test_report_card_renders_for_the_built_pack():
    folder = Path(__file__).resolve().parents[1] / "data" / "packs" / "PA"
    if not (folder / "data_dictionary.json").exists():
        pytest.skip("build the pack first: make data STATE=PA")
    card = rc.report_card(rc.load_pack_files(folder))
    by_id = {r["id"]: r for r in card["inputs"]}
    assert by_id["transit_frequency"]["status"] == "used"
    assert 0.3 < by_id["transit_frequency"]["coverage"] < 1.0
    assert "2026" in by_id["transit_frequency"]["vintage"]
