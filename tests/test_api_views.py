"""The screen-ready views (``api/views.py``) on Testland.

County map, tract shading, outlines, evidence and the report card.

The map's percent change must come from the engine's own draws, its classes from the
ledger's verdict thresholds, and the tract values must add up to the county record.
"""

from __future__ import annotations

import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app

API = "/api/v1"
QUERY = "r=adu,fees&seed=0&draws=300"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return AsgiClient(create_app(packs_dir=tmp_path_factory.mktemp("packs")))


def test_map_classes_follow_the_verdict_thresholds(client):
    reply = client.get(f"{API}/states/TL/map?{QUERY}").json()
    some = reply["thresholds"]["explain.verdict_some_pct"]
    lot = reply["thresholds"]["explain.verdict_a_lot_pct"]
    assert [c["id"] for c in reply["classes"]] == ["little", "some", "a_lot"]
    assert reply["counties"]
    for c in reply["counties"]:
        mid = c["change"]["p50"]
        want = "a_lot" if mid >= lot else "some" if mid >= some else "little"
        assert c["class"] == want
        assert c["change"]["p10"] <= mid <= c["change"]["p90"]
        assert c["added"]["shown"]["p50"].endswith("units")
    assert reply["ids"]["added"]["assumption_ids"]


def test_map_change_is_added_over_baseline_in_each_draw(client):
    run = client.post(
        f"{API}/run", {"state": "TL", "reforms": ["adu", "fees"], "draws": 300, "explain": False}
    ).json()
    reply = client.get(f"{API}/states/TL/map?{QUERY}").json()
    for c in reply["counties"]:
        assert c["added"]["p50"] == run["package"]["counties"][c["fips"]]["p50"]
        assert c["baseline"]["p50"] == run["baseline_counties"][c["fips"]]["p50"]
        # A draw-by-draw ratio, so not the ratio of the middles, but close to it.
        ratio = 100 * c["added"]["p50"] / c["baseline"]["p50"]
        assert c["change"]["p10"] <= ratio * 1.5 and ratio <= c["change"]["p90"] * 1.5


def test_map_without_reforms_has_no_classes_to_shade(client):
    reply = client.get(f"{API}/states/TL/map").json()
    assert {c["class"] for c in reply["counties"]} == {"none"}
    assert all(c["added"] is None and c["baseline"] for c in reply["counties"])


def test_county_tract_values_add_up_to_the_county(client):
    reply = client.get(f"{API}/states/TL/counties/99001?{QUERY}").json()
    tr = reply["tract_results"]
    assert "fees" not in tr["reforms"]  # a county-level reform has no tract values
    adu = tr["reforms"]["adu"]
    assert {t["geoid"] for t in adu["tracts"]} <= set(reply["tracts"])
    total = sum(t["p50"] for t in adu["tracts"])
    assert total == pytest.approx(reply["reforms"]["adu"]["added"]["p50"], rel=0.15)
    assert len(tr["breaks"]) == 3 and len(tr["labels"]) == 4
    assert tr["breaks"] == sorted(tr["breaks"])


def test_outlines_evidence_and_report_card(client):
    out = client.get(f"{API}/states/TL/outlines").json()
    assert out["features"] and all(len(f["properties"]["geoid"]) == 5 for f in out["features"])
    ev = client.get(f"{API}/evidence?reform=adu").json()
    assert ev["count"] == len(ev["entries"]) > 0
    assert all(e["reform"] == "adu" for e in ev["entries"])
    assert client.get(f"{API}/evidence?reform=a%20b").status == 400
    card = client.get(f"{API}/report-card?state=TL").json()
    assert card["state"] == "TL" and card["inputs"]
