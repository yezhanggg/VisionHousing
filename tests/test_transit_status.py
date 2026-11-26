"""The transit status ladder (features/transit_status.py) and the Transitland Atlas feed list.

Every case is small enough to check by eye. The rules under test:

- "unknown" (service not measured) is never written as zero; "measured_zero" needs
  every bus stop measured;
- a feed that is only discovered (listed, not read) adds no qualifying stop and so
  never reaches the feasibility stage;
- the statewide range is shown only when the measured share of bus stops reaches
  ``tod.status_min_measured_bus_share``; below it the footprint is labelled partial.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from tests.pack_fakes import FileServer, client
from visionhousing.data.sources import transit as tr
from visionhousing.engine.adapters import _tod_status_note
from visionhousing.features import transit_status as ts
from visionhousing.ledger import load_ledger


def stops(rows: list[tuple[str, str, str, int, str]]) -> pd.DataFrame:
    """Rows of (mode, frequency, county, major, ntd_id)."""
    return pd.DataFrame(rows, columns=["mode", "frequency", "county", "major", "ntd_id"])


REGISTER = {
    "feeds": [
        {"id": "tl-a", "state": "XX", "ntd_id": "1", "read": True,
         "legal": {"status": "usable"}},
        {"id": "tl-b", "state": "XX", "ntd_id": "2", "read": False,
         "legal": {"status": "not_reviewed"}},
        {"id": "tl-c", "state": "XX", "ntd_id": "3", "read": False,
         "legal": {"status": "forbidden"}},
    ]
}  # fmt: skip


def table(frame: pd.DataFrame, counties=("00001", "00002"), **kw) -> ts.StatusTable:
    kw.setdefault("min_share", 0.5)
    return ts.status_table(frame, "XX", list(counties), register=REGISTER, **kw)


def row(t: ts.StatusTable, geoid: str) -> dict:
    return dict(t.rows[t.rows["geoid"] == geoid].iloc[0])


def test_measured_zero_needs_every_bus_stop_measured():
    """Two counted stops, neither frequent: a measured zero. Add one unmeasured stop: unknown."""
    all_counted = stops([("bus", "counted", "00001", 0, "1"), ("bus", "counted", "00001", 0, "1")])
    t = table(all_counted)
    assert row(t, "00001")["headway"] == "measured_zero"
    assert row(t, "00001")["coverage"] == "yes"
    assert t.state["completeness"].startswith("complete")

    one_unknown = stops([*all_counted.itertuples(index=False), ("bus", "unknown", "00001", 0, "9")])
    t = table(one_unknown)
    county = row(t, "00001")
    assert county["headway"] == "unknown"  # not zero: one stop's service is not known
    assert county["coverage"] == "partial"
    assert county["measured_share"] == pytest.approx(2 / 3, abs=1e-4)
    assert "partial footprint: 2 of 3 bus stops (66.6%)" in county["completeness"]


def test_a_county_without_stops_is_not_reached_and_rail_counts_as_measured():
    t = table(stops([("rail", "mode_only", "00002", 1, "")]))
    empty, rail = row(t, "00001"), row(t, "00002")
    assert empty["discovered"] == "not_reached" and empty["coverage"] == "not_reached"
    assert empty["headway"] == "measured_zero"  # no stop in the national inventory
    assert rail["coverage"] == "yes" and rail["headway"] == "yes"


def test_a_discovered_feed_alone_adds_nothing():
    """Agency 2's feed is listed but not read: discovered, legal unknown, service unknown."""
    frame = stops([("bus", "unknown", "00001", 0, "2"), ("bus", "unknown", "00001", 0, "2")])
    t = table(frame, feasible={"00001": (0.0, 0.0), "00002": (0.0, 0.0)})
    county = row(t, "00001")
    assert county["discovered"] == "yes"
    assert county["legal"] == "unknown"
    assert county["coverage"] == "no"
    assert county["headway"] == "unknown"
    assert county["feasible"] == "not_reached"
    assert not t.statewide_shown


def test_legal_reads_each_feed_record():
    a = ts.agency_status(
        stops([("bus", "counted", "00001", 1, "1"), ("bus", "unknown", "00001", 0, "3"),
               ("bus", "stale", "00001", 0, "7"), ("bus", "unknown", "00001", 0, "8")]),
        "XX", REGISTER,
    ).set_index("ntd_id")  # fmt: skip
    assert a.loc["1", "legal"] == "yes"  # register: usable
    assert a.loc["3", "legal"] == "no"  # register: terms forbid reuse
    assert a.loc["7", "legal"] == "yes"  # read through the main catalog (expired copy)
    assert bool(a.loc["7", "discovered"])
    assert a.loc["8", "legal"] == "not_reached" and not bool(a.loc["8", "discovered"])


def test_feasible_stage_follows_the_funnel():
    frame = stops([("bus", "counted", "00001", 1, "1"), ("bus", "counted", "00002", 1, "1")])
    t = table(frame, feasible={"00001": (10.0, 2.0), "00002": (10.0, 0.0)})
    assert row(t, "00001")["feasible"] == "yes"
    assert row(t, "00002")["feasible"] == "no"
    assert t.state["feasible"] == "yes"


def test_partial_footprint_hides_the_statewide_range_below_the_ledger_share():
    """4 bus stops, 1 measured (25%): hidden at 0.5, shown at 0.25; label says partial."""
    frame = stops([("bus", "counted", "00001", 1, "1")] + [("bus", "unknown", "00002", 0, "9")] * 3)
    hidden = table(frame, min_share=0.5)
    assert not hidden.statewide_shown
    out = hidden.to_dict()
    assert out["statewide"]["shown"] is False
    assert out["statewide"]["label"].startswith("partial footprint: 1 of 4 bus stops (25.0%)")
    assert out["state"]["headway"] == "yes"  # a qualifying stop was measured
    json.dumps(out)  # JSON-ready
    note = _tod_status_note(out)
    assert "not shown" in note and "measured contribution" in note
    assert table(frame, min_share=0.25).statewide_shown


def test_ledger_has_the_display_share():
    p = load_ledger()[ts.MIN_SHARE_ID]
    assert 0 < p.central <= 1 and p.low == p.high == p.central


def test_register_lists_wyoming_feeds_with_legal_records():
    reg = tr.load_feed_register()
    wy = tr.register_feeds("WY", reg)
    assert {f["id"] for f in wy if f["read"]} == {"tl-jackson-wy", "tl-cheyenne-wy"}
    for f in wy:
        assert f["legal"]["status"] in ("usable", "forbidden", "not_reviewed")
        if f["read"]:
            assert f["legal"]["status"] == "usable" and f["legal"]["checked_on"]
    assert set(reg["status"]["values"]) == set(ts.VALUES)


def test_atlas_catalog_reads_only_usable_feeds_at_the_pinned_commit(tmp_path):
    register = {
        "atlas": {"commit": "abc123"},
        "feeds": [
            {"id": "tl-a", "state": "WY", "agency": "A", "atlas_file": "feeds/a.dmfr.json",
             "onestop_id": "f-a", "read": True, "legal": {"status": "usable", "terms_url": "t"}},
            {"id": "tl-c", "state": "WY", "agency": "C", "atlas_file": "feeds/c.dmfr.json",
             "onestop_id": "f-c", "read": True, "legal": {"status": "forbidden"}},
        ],
    }  # fmt: skip
    base = "https://raw.githubusercontent.com/transitland/transitland-atlas/abc123/"
    doc = {"feeds": [{"id": "f-a", "urls": {"static_current": "https://op.example/g.zip"}}]}
    server = FileServer({base + "feeds/a.dmfr.json": json.dumps(doc).encode()})
    rows = tr.fetch_atlas_catalog(
        "WY", tmp_path, client=client(server), manifest=None, register=register
    )
    assert list(rows["mdb_source_id"]) == ["tl-a"]
    assert rows["urls.direct_download"].iloc[0] == "https://op.example/g.zip"
    assert server.urls == [base + "feeds/a.dmfr.json"]  # the forbidden feed is never fetched
    again = tr.fetch_atlas_catalog("WY", tmp_path, client=client(FileServer({})), manifest=None,
                                   register=register)  # fmt: skip
    pd.testing.assert_frame_equal(rows, again)  # cached


def _result(status):
    import numpy as np

    from visionhousing.engine import records as rec
    from visionhousing.engine.registry import ReformResult

    draws = np.array([[1.0, 2.0, 3.0, 4.0]])
    head = rec.from_draws("added", draws[0], "units", expression="x")
    return ReformResult(
        reform="tod", title="TOD", level="county", geoids=("00001",), county_of=("00001",),
        added=draws, headline=head, status=status,
    )  # fmt: skip


def test_engine_labels_a_hidden_statewide_range_and_keeps_the_numbers():
    import numpy as np

    from visionhousing.engine import core

    frame = stops([("bus", "counted", "00001", 1, "1")] + [("bus", "unknown", "00001", 0, "9")] * 3)
    status = table(frame, counties=("00001",)).to_dict()
    base = np.full(4, 100.0)
    hidden = core._outcome("tod", {}, _result(status), ("00001",), base)
    shown = core._outcome("tod", {}, _result(None), ("00001",), base)
    assert "measured contribution only (partial footprint" in hidden.added.name
    assert "not shown" in hidden.pct_change.name
    assert (hidden.added.p10, hidden.added.p50, hidden.added.p90) == (
        shown.added.p10, shown.added.p50, shown.added.p90,
    )  # fmt: skip
    assert "measured contribution" not in shown.added.name
