"""Wyoming as a sparse state: few schedule feeds, no rail, no state reform law.

Checks the report-card and intake fixes the Wyoming build needed, on small
hand-made tables (no download, no pack):

- text columns (a stop's mode, a tract's area type) count as filled;
- a zero for transit access where a bus stop has no counted departures is
  ``missing``, not a true zero, and the card says "no major transit stops";
- the Wyoming law file loads, every provision is a dated hand reading of a
  registered source, and no already-legal table is written for Wyoming.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from visionhousing.data import report_card as rc
from visionhousing.data.registry import get_source
from visionhousing.intake.manifest import _law_rows
from visionhousing.law.timeline import load_timeline
from visionhousing.scenarios.adu import load_legal_shares

ROOT = Path(__file__).resolve().parents[1]
WY_LAW = ROOT / "assumptions" / "law" / "states" / "wy.yaml"


def _input(item_id: str) -> rc.Input:
    return next(i for i in rc.INPUTS if i.id == item_id)


def _no_feed_tables() -> dict[str, pd.DataFrame]:
    stops = pd.DataFrame(
        {
            "lat": [41.1, 41.2, 42.0],
            "lon": [-104.8, -104.7, -106.3],
            "mode": ["bus", "bus", "unknown"],
            "frequency": ["unknown", "unknown", "not_major"],
            "weekday_departures": [None, None, None],
            "major": [0, 0, 0],
            "tract": ["56021000100", "56021000100", "56025000200"],
        }
    )
    feeds = pd.DataFrame(
        {"feed_id": ["a_1", "b_1"], "schedule_feed": [None, None], "bus_stops": [2, 0]}
    )
    tracts = pd.DataFrame(
        {
            "geoid": ["56021000100", "56025000200", "56001000300"],
            "transit_units_d07": [0, 0, 0],
            "transit_rich": [0, 0, 0],
        }
    )
    return {"transit_stops": stops, "transit_feeds": feeds, "tracts": tracts}


def test_text_columns_count_as_filled():
    tables = _no_feed_tables()
    states = rc._row_states(_input("transit_stops"), tables["transit_stops"], tables)
    assert states["observed"] == 3 and states["missing"] == 0


def test_access_zero_next_to_an_unscheduled_bus_stop_is_missing_not_zero():
    tables = _no_feed_tables()
    states = rc._row_states(_input("transit_access"), tables["tracts"], tables)
    # Tract 100 holds two bus stops with no schedule: its zero is not measured.
    assert states["missing"] == 1
    # The other two tracts hold no bus stop of unknown frequency: a true zero.
    assert states["true_zero"] == 2


def test_card_says_no_major_transit_stops_and_why():
    tables = _no_feed_tables()
    access = rc._transit_notes("transit_access", "WY", tables)
    assert access and access[0].startswith("No major transit stops in WY")
    assert "no rail station" in access[0] and "not as a measured absence" in access[0]
    freq = rc._transit_notes("transit_frequency", "WY", tables)
    assert "2 of 2 transit agencies" in freq[0] and "missing, not zero" in freq[0]


def test_no_note_when_a_major_stop_exists():
    tables = _no_feed_tables()
    tables["transit_stops"].loc[0, "major"] = 1
    assert rc._transit_notes("transit_access", "WY", tables) == []


def test_wyoming_law_file_is_dated_hand_reading_of_a_registered_source():
    data = yaml.safe_load(WY_LAW.read_text(encoding="utf-8"))
    assert data["state"] == "WY" and data["provisions"]
    assert load_timeline(WY_LAW) == []  # nothing enacted to switch on
    for p in data["provisions"]:
        assert p["checked_on"] and p["source_id"] == "wy_statutes"
        assert p["url"].startswith("https://wyoleg.gov/")
    assert get_source("wy_statutes").url(title=15).endswith("title15.pdf")


def test_wyoming_law_rows_count_as_verified_readings(tmp_path):
    rows = _law_rows("WY", tmp_path, ROOT, "56")
    mine = [r for r in rows if str(r["id"]).startswith("wy_")]
    assert len(mine) == 5
    assert all(r["reviewer"] and r["reviewer"].startswith("verified reading on") for r in mine)


def test_no_already_legal_table_for_wyoming():
    # No state ADU or missing-middle law, so the national default range stands.
    assert "WY" not in load_legal_shares()


def test_an_empty_walkshed_table_counts_as_no_walksheds():
    from visionhousing.engine.context import context_from_tables, load_context
    from visionhousing.engine.core import compute

    ctx = load_context("TL")
    empty = pd.DataFrame(columns=["geoid", "tract_acres", "network_acres", "fallback_acres"])
    local = context_from_tables(
        "TL", tracts=ctx.tracts, permits=ctx.permits, blocks=ctx.blocks, stops=ctx.stops,
        boundaries=ctx.boundaries, ledger=ctx.ledger, scenarios=ctx.scenarios, fit=ctx.fit,
        legal=ctx.legal, data_hash=ctx.data_hash, calibration_hash=ctx.calibration_hash,
        transit_walk=empty,
    )  # fmt: skip
    assert local.transit_walk is None
    res = compute(local, {"tod": {"walk": "network"}}, seed=1, draws=50)
    assert any("no walkshed table" in n for n in res.reforms["tod"].notes)
