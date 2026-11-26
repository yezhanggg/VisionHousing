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


# Testland's small synthetic optional tables (scripts/make_testland.py). Tests that use
# Testland as a stand-in for a pack without them drop them first.
OPTIONAL = ("infra_tract", "hazard_tract", "parcels_area", "afford_income_limits")


def _plain_testland() -> dict:
    files = rc.load_pack_files(TESTLAND)
    files["tables"] = {k: v for k, v in files["tables"].items() if k not in OPTIONAL}
    return files


def test_report_card_renders_for_testland_from_the_fixture_alone():
    files = rc.load_pack_files(TESTLAND)
    card = rc.report_card(files)
    assert card["state"] == "TL" and card["synthetic"]
    by_id = {r["id"]: r for r in card["inputs"]}
    # A state-only input is still shown where its table is present (Testland's parcels).
    shown = {i.id for i in rc.INPUTS if i.states and i.table in files["tables"]}
    assert "parcels" in shown
    assert [r["id"] for r in card["inputs"]] == [
        i.id for i in rc.INPUTS if not i.states or i.id in shown
    ]
    assert {r["id"] for r in card["not_applicable"]} == {
        i.id for i in rc.INPUTS if i.states and i.id not in shown
    }
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


def _card_for(folder, state, extra=None):
    files = _plain_testland() if folder == TESTLAND else rc.load_pack_files(folder)
    files["dictionary"] = {**files["dictionary"], "state": state}
    files["tables"] = {**files["tables"], **(extra or {})}
    return rc.report_card(files)


def test_state_only_inputs_are_listed_only_for_their_states():
    for state in ("PA", "VT", "MA"):
        card = _card_for(TESTLAND, state)
        listed = {r["id"] for r in card["inputs"]}
        other = {r["id"] for r in card["not_applicable"]}
        for item in rc.INPUTS:
            if item.states and state not in item.states:
                assert item.id in other and item.id not in listed, (state, item.id)
            else:
                assert item.id in listed and item.id not in other, (state, item.id)
        assert all(r["value_state"] == "not_applicable" for r in card["not_applicable"])
        md = rc.to_markdown(card)
        missing_rows = [r["label"] for r in card["inputs"] if r["status"] == "missing"]
        assert not any("Massachusetts" in label for label in missing_rows) or state == "MA"
        if state != "MA":
            assert "Massachusetts ADU survey" in md and "(MA only)" in md
    pa = {r["id"] for r in _card_for(TESTLAND, "PA")["inputs"]}
    assert {"municipal_rules", "parcels", "state_bills"} <= pa
    assert not {"ma_3a_districts", "ma_adu_survey"} & pa


def test_state_only_input_keeps_missing_and_feed_failure_apart(tmp_path):
    # MA without the survey (never promised): missing, not a feed failure
    card = _card_for(TESTLAND, "MA")
    by_id = {r["id"]: r for r in card["inputs"]}
    assert by_id["ma_adu_survey"]["value_state"] == "missing"
    assert by_id["ma_adu_survey"]["status"] == "missing"
    # promised by the dictionary but empty: a feed failure, never 0%
    files = _plain_testland()
    files["dictionary"] = {
        **files["dictionary"],
        "state": "MA",
        "tables": [*files["dictionary"].get("tables", []), {"table": "mbta_3a_districts"}],
    }
    files["tables"]["mbta_3a_districts"] = pd.DataFrame(columns=["capacity_district", "acres"])
    card = rc.report_card(files)
    row = {r["id"]: r for r in card["inputs"]}["ma_3a_districts"]
    assert row["value_state"] == "feed_failure"
    line = next(
        x
        for x in rc.to_markdown(card).splitlines()
        if x.startswith("| Massachusetts Section 3A districts")
    )
    assert "0%" not in line and "source failed to load" in line


def test_state_only_table_present_elsewhere_is_still_shown():
    frame = pd.DataFrame({"capacity_district": [120.0], "acres": [10.0]})
    card = _card_for(TESTLAND, "PA", {"mbta_3a_districts": frame})
    row = {r["id"]: r for r in card["inputs"]}["ma_3a_districts"]
    assert row["status"] == "available" and row["value_state"] == "observed"


def test_a_skipped_add_on_is_a_feed_failure_in_a_real_pack():
    # a real state's full build adds the hazard tables; absent, they failed to load
    files = _plain_testland()
    files["dictionary"] = {**files["dictionary"], "state": "MA", "synthetic": False}
    files["manifest"] = {"provenance": []}
    card = rc.report_card(files)
    by_id = {r["id"]: r for r in card["inputs"]}
    for ident in ("flood_zones", "land_cover", "slope"):
        assert by_id[ident]["value_state"] == "feed_failure", ident
    assert by_id["ma_adu_survey"]["value_state"] == "missing"  # not an add-on: no data
    md = rc.to_markdown(card)
    assert "3 input(s) failed to load" in md
    # Testland (synthetic, no manifest) never claims the add-ons: without its synthetic
    # hazard table land cover is missing, not a feed failure; with it, observed
    tl = {r["id"]: r for r in rc.report_card(_plain_testland())["inputs"]}
    assert tl["land_cover"]["value_state"] == "missing"
    own = {r["id"]: r for r in rc.report_card(rc.load_pack_files(TESTLAND))["inputs"]}
    assert own["land_cover"]["value_state"] == "observed"


def test_a_hand_saved_table_is_flagged_with_its_edition():
    status = pd.DataFrame(
        {
            "compliance_status": ["Compliant", "Noncompliant"],
            "status_route": ["hand_saved", "hand_saved"],
            "status_as_of": ["2026-11-24", "2026-11-24"],
            "categories_route": ["hand_saved", "hand_saved"],
            "categories_as_of": ["", ""],
            "tracker_route": ["publisher", "publisher"],
            "tracker_as_of": ["2026-09-11", "2026-09-11"],
        }
    )
    survey = pd.DataFrame(
        {"approvals": [3], "survey_route": ["publisher"], "survey_as_of": ["2026-08-12"]}
    )
    card = _card_for(TESTLAND, "MA", {"mbta_3a_status": status, "ma_adu_survey": survey})
    by_id = {r["id"]: r for r in card["inputs"]}
    row = by_id["ma_3a_status"]
    assert row["hand_saved"] is True
    hand = [x for x in row["limits"] if x.startswith("hand-saved")]
    assert len(hand) == 2  # status sheet and categories, not the tracker
    assert any("status file" in x and "as of 2026-11-24" in x for x in hand)
    assert any("categories file" in x and "edition unknown" in x for x in hand)
    assert by_id["ma_adu_survey"]["hand_saved"] is False
    line = next(
        x for x in rc.to_markdown(card).splitlines() if x.startswith("| Massachusetts Section 3A c")
    )
    assert "(hand-saved)" in line
    assert all(r["hand_saved"] is False for r in card["inputs"] if r["id"] != "ma_3a_status")


def test_optional_hand_saved_files_not_supplied_are_said_so():
    # a clean build with every hand-saved MA file absent: the status list comes from
    # the atlas alone, categories and tracker are blank, and the survey table is absent
    status = pd.DataFrame(
        {
            "compliance_status": ["Compliant", "Compliant"],
            "status_source": ["atlas", "atlas"],
            "status_route": ["atlas", "atlas"],
            "status_as_of": ["2026-11-24", "2026-11-24"],
            "categories_route": [None, None],
            "tracker_route": [None, None],
        }
    )
    files = _plain_testland()
    files["dictionary"] = {
        **files["dictionary"],
        "state": "MA",
        "columns": [
            *files["dictionary"].get("columns", []),
            {
                "table": "mbta_3a_status",
                "name": "compliance_status",
                "source": "massgis_mbta3a_atlas, ma_3a_compliance_status, ma_3a_categories, "
                "ma_3a_development_tracker",
                "tier": "2",
            },
        ],
    }
    files["tables"] = {**files["tables"], "mbta_3a_status": status}
    card = rc.report_card(files)
    by_id = {r["id"]: r for r in card["inputs"]}
    row = by_id["ma_3a_status"]
    assert row["status"] == "available" and row["not_supplied"] is True
    absent = [x for x in row["limits"] if x.startswith("not supplied")]
    assert len(absent) == 3
    assert all("data/raw/local/ma/" in x for x in absent)
    # an unread file's source is never listed or dated as if it were read
    assert row["sources"] == ["massgis_mbta3a_atlas"]
    survey = by_id["ma_adu_survey"]
    assert survey["value_state"] == "missing" and survey["not_supplied"] is True
    assert any("ma_adu_survey.csv" in x for x in survey["limits"] if x.startswith("not supplied"))
    # a table read from the publisher or a hand-saved copy is not flagged
    read = status.assign(status_route="hand_saved", categories_route="publisher")
    read["tracker_route"] = "publisher"
    files["tables"]["mbta_3a_status"] = read
    again = {r["id"]: r for r in rc.report_card(files)["inputs"]}["ma_3a_status"]
    assert again["not_supplied"] is False
