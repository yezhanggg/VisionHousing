"""The state data report card, and the transit tables inside a built pack (Testland inputs)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from tests.test_pack import TESTLAND, make_inputs
from visionhousing.data import pack
from visionhousing.data import report_card as rc
from visionhousing.data.sources import transit as tr
from visionhousing.features import transit as tf
from visionhousing.ledger import load_ledger


def landed_transit() -> tr.TransitInputs:
    """Testland's 30 stops dressed as map stops, with a matching schedule feed."""
    stops = pd.read_csv(TESTLAND / "transit_stops.csv", dtype={"stop_id": str})
    ntm = pd.DataFrame(
        {
            "ntd_id": "99",
            "feed_id": "99_1",
            "stop_id": stops["stop_id"],
            "stop_name": stops["stop_id"],
            "lat": stops["lat"],
            "lon": stops["lon"],
            "location_type": "0",
            "parent_station": "",
            "stop_type": stops["mode"].map({"rail": "2", "bus": "3"}),
        }
    )
    table = pd.DataFrame(
        {
            "stop_id": stops["stop_id"],
            "lat": stops["lat"],
            "lon": stops["lon"],
            "location_type": "0",
            "parent_station": "",
            "route_groups": stops["mode"],
            "weekday_departures": stops["weekday_departures"].astype("int64"),
        }
    )
    note = {"schedule_feed": "1", "provider": "Testland Transit", "status": "ok",
            "representative_date": "2026-12-08", "valid_from": "2026-12-01",
            "valid_to": "2027-03-01", "fetched_on": "2026-11-22"}  # fmt: skip
    agencies = pd.DataFrame({"ntd_id": ["99"], "agency": ["Testland Transit"]})
    return tr.TransitInputs(ntm=ntm, agencies=agencies, feeds=[(note, table)])


@pytest.fixture
def built(tmp_path):
    inputs = make_inputs(tmp_path)
    inputs.transit = landed_transit()
    return pack.write_pack(inputs, tmp_path / "packs", manifest=None)


def test_pack_carries_stops_feeds_and_tract_features(built):
    tables = pack.load_pack(built.folder)
    stops = tables["transit_stops"]
    assert list(stops.columns) == list(tr.STOP_COLUMNS)
    assert len(stops) == 30  # every Testland stop is inside a Testland tract
    rule = tf.TransitRule.from_ledger(load_ledger())
    expected_major = (stops["mode"] == "rail") | (
        stops["weekday_departures"] >= rule.min_bus_departures
    )
    assert (stops["major"] == 1).tolist() == expected_major.tolist()
    assert set(tf.FEATURE_COLUMNS) <= set(tables["tracts"].columns)
    assert tables["tracts"]["transit_rich"].isin([0, 1]).all()
    assert tables["tracts"]["transit_rich"].sum() > 0
    feeds = tables["transit_feeds"]
    assert feeds.loc[0, "bus_stops_counted"] == feeds.loc[0, "bus_stops"] == 22
    d = json.loads((built.folder / "data_dictionary.json").read_text())
    tiers = {(c["table"], c["name"]): c["tier"] for c in d["columns"]}
    assert tiers[("transit_stops", "weekday_departures")] == "1"
    assert tiers[("transit_stops", "lat")] == "0"


def test_pack_with_transit_is_deterministic(tmp_path):
    hashes = []
    for k in range(2):
        (tmp_path / str(k)).mkdir()
        inputs = make_inputs(tmp_path / str(k))
        inputs.transit = landed_transit()
        hashes.append(pack.write_pack(inputs, tmp_path / f"p{k}", manifest=None).pack_hash)
    assert hashes[0] == hashes[1]


def test_report_card_renders_for_testland_from_the_fixture_alone():
    card = rc.report_card(rc.load_pack_files(TESTLAND))
    assert card["state"] == "TL" and card["synthetic"]
    by_id = {r["id"]: r for r in card["inputs"]}
    assert [r["id"] for r in card["inputs"]] == [i.id for i in rc.INPUTS]
    assert by_id["permits"]["status"] == "used" and by_id["permits"]["vintage"] == "synthetic"
    assert by_id["transit_access"]["status"] == "used"  # computed from blocks and stops
    assert by_id["zoning_single_family"]["status"] == "assumed"
    assert by_id["zoning_single_family"]["tier"] == 2
    md = rc.to_markdown(card)
    assert "Synthetic test state" in md
    for item in rc.INPUTS:
        assert item.label in md
    json.dumps(card)  # JSON-ready


def test_report_card_renders_for_a_built_pack(built):
    card = rc.report_card(rc.load_pack_files(built.folder))
    by_id = {r["id"]: r for r in card["inputs"]}
    assert card["pack_hash"] == built.pack_hash
    assert by_id["transit_frequency"]["coverage"] == 1.0
    assert by_id["transit_frequency"]["tier"] == 1
    assert by_id["house_price_trend"]["status"] == "available"
    assert by_id["mortgage_rates"]["status"] == "available"
    assert "departures counted for 22 of 22 bus stops" in " ".join(
        by_id["transit_frequency"]["limits"]
    )


def test_missing_table_falls_back_to_the_ledger(built):
    (built.folder / "transit_stops.csv.gz").unlink()
    card = rc.report_card(rc.load_pack_files(built.folder))
    by_id = {r["id"]: r for r in card["inputs"]}
    assert by_id["transit_frequency"]["status"] == "assumed"
    assert by_id["transit_stops"]["status"] == "missing"


def test_every_named_ledger_id_exists():
    ledger = load_ledger()
    for item in rc.INPUTS:
        for ident in item.assumed_by:
            assert ledger[ident] is not None, ident


def test_command_line_writes_json_and_markdown(tmp_path):
    assert rc.main(["TL", "--out", str(tmp_path)]) == 0
    card = json.loads((tmp_path / "TL_report_card.json").read_text())
    assert card["state"] == "TL"
    assert (tmp_path / "TL_report_card.md").read_text().startswith("# State data report card")
