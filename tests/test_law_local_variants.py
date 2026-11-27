"""L2: Vermont and Wyoming local readings enter only through named variants.

- ``vt_missing_middle_town_bylaws``: a verified, in-force town bylaw that allows
  more homes by right than the state floor becomes the level already allowed on
  its part of a tract's lots; everything else keeps the statewide floor.
- ``vt_parking_town_bylaws`` / ``wy_parking_local``: verified local parking
  minimums into the parking mix, the default elsewhere.

Every case checks the three rules: the variant equals the benchmark with no
readings (draw for draw), drafts never feed a number, and a reading counts only
from its effective date (else its review date) relative to the run's as-of date.
Everything is offline; the tract-to-town table is built in the test.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml

from tests.test_law_floor_middle import run as mm_run
from visionhousing.engine.context import load_context
from visionhousing.engine.core import run
from visionhousing.engine.law_floor import (
    load_local_settings,
    local_parking_minimums,
    middle_floor_for,
    town_levels_for,
    town_tract_shares,
)
from visionhousing.engine.variants import load_variants
from visionhousing.law.local_readings import READINGS_PATH, LocalReadings, load_local_readings
from visionhousing.law.rights import middle_floor_schedule
from visionhousing.ledger import load_ledger, load_scenarios
from visionhousing.scenarios.missing_middle import LocalLevel, MiddleFloor

YEARS = range(2027, 2032)
AS_OF = "2026-11-24"  # the run's as-of date (assumptions/outcome.yaml)
BARRE, BURLINGTON = "5002303175", "5000710675"


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


@pytest.fixture(scope="module")
def scenarios(ledger):
    return load_scenarios(ledger)


@pytest.fixture(scope="module")
def vt():
    return middle_floor_schedule("VT", YEARS)


def _readings(edit=None) -> LocalReadings:
    """The shipped readings, optionally edited as plain data first."""
    data = yaml.safe_load(READINGS_PATH.read_text(encoding="utf-8"))
    if edit is not None:
        edit(data)
    return LocalReadings(documents=tuple(data["documents"]), places=tuple(data["places"]))


def _vt_state(tm=None):
    tracts = pd.DataFrame({"geoid": ["50023000100", "50007000100", "50001000100"]})
    if tm is None:
        tm = pd.DataFrame(
            {
                "tract": ["50023000100", "50023000100", "50007000100"],
                "county": ["50023", "50023", "50007"],
                "cousub": ["03175", "99999", "10675"],  # Barre 3/4, other town 1/4; Burlington
                "housing_units_2020": [300, 100, 500],
            }
        )
    return SimpleNamespace(
        state="VT", tracts=tracts, derived={("parking_law_table", "tract_municipalities"): tm}
    )


# --------------------------------------------------------------------------- variants and rows


def test_variants_and_alternative_rows_are_listed():
    v = load_variants()
    assert v["vt_missing_middle_town_bylaws"].options == {"local_rights": "town_bylaws"}
    assert v["vt_parking_town_bylaws"].options["readings_state"] == "VT"
    assert v["wy_parking_local"].options["readings_state"] == "WY"
    with open("assumptions/alternatives.yaml", encoding="utf-8") as fh:
        alt = yaml.safe_load(fh)
    rows = {r.get("variant") or r.get("id"): r for f in alt["reforms"].values() for r in f["rows"]}
    assert rows["vt_missing_middle_town_bylaws"]["states"] == ["VT"]
    assert rows["vt_parking_town_bylaws"]["states"] == ["VT"]
    assert rows["wy_parking_local"]["states"] == ["WY"]
    wy_adu = rows["wy_adu_local"]
    assert wy_adu["kind"] == "planned" and wy_adu["states"] == ["WY"]
    assert "Laramie ADU permission not confirmed by a use-table row" in " ".join(
        wy_adu["absent"].split()
    )
    # the reason holds in the readings: the ADU fact is flagged, unlisted uses are prohibited
    laramie = next(p for p in load_local_readings().places if p.place_id == "wy_laramie")
    adu = next(f for f in laramie.facts if f.topic == "adu")
    assert "not confirmed in a use table" in adu.flag
    assert "prohibited" in next(f for f in laramie.facts if f.topic == "unlisted_uses").value


# --------------------------------------------------------------------------- missing middle


def test_town_tract_shares_split_by_2020_housing_units():
    st = _vt_state()
    assert list(town_tract_shares(st.tracts, st.derived[next(iter(st.derived))], BARRE)) == [
        0.75, 0.0, 0.0,
    ]  # fmt: skip
    assert town_tract_shares(st.tracts, None, BARRE) is None  # no table: cannot be placed
    assert town_tract_shares(st.tracts, st.derived[next(iter(st.derived))], "5645050") is None


def test_levels_count_only_from_the_effective_or_review_date(vt):
    st = _vt_state()
    on_as_of, src, notes = town_levels_for(st, vt, as_of=AS_OF)
    ids = {lv.place_id for lv in on_as_of}
    # Barre and Montpelier print effective dates before the as-of date; Burlington has none
    # and counts from its review date (2026-11-26), after it
    assert "vt_barre_city" in ids and "vt_burlington" not in ids
    assert any("Burlington (city) (counts from 2026-11-26" in n for n in notes)
    later, _, _ = town_levels_for(st, vt, as_of="2026-11-26")
    assert "vt_burlington" in {lv.place_id for lv in later}
    barre = next(lv for lv in on_as_of if lv.place_id == "vt_barre_city")
    assert barre.current == (4,) * 5 and barre.frozen == 4
    assert set(src) == {"vt_wy_local_bylaws", "census_baf_2020_mcd"}
    # not read places and places with a draft small-multiunit fact never count
    assert {"vt_williston", "vt_colchester", "vt_essex"}.isdisjoint(ids)


def test_drafts_are_ignored(vt):
    def demote(data):
        for p in data["places"]:
            if p["place_id"] == "vt_barre_city":
                for f in p["facts"]:
                    if f["topic"] == "small_multiunit":
                        f["review"] = "draft"
                        f["check"] = "test: column reading not confirmed"

    got, _, notes = town_levels_for(_vt_state(), vt, as_of=AS_OF, readings=_readings(demote))
    assert "vt_barre_city" not in {lv.place_id for lv in got}
    assert any("Barre (city) (small_multiunit not verified)" in n for n in notes)


def test_unknown_district_rights_keep_the_statewide_floor(vt):
    def blank(data):
        for p in data["places"]:
            if p["place_id"] == "vt_barre_city":
                p["districts"]["R-4"]["local_max_units"] = None

    got, _, notes = town_levels_for(_vt_state(), vt, as_of=AS_OF, readings=_readings(blank))
    assert "vt_barre_city" not in {lv.place_id for lv in got}
    assert any("units allowed not known in R-4" in n for n in notes)


def test_no_crosswalk_no_levels_and_the_engine_hook_names_sources(ledger, vt):
    st = _vt_state()
    st.derived[("parking_law_table", "tract_municipalities")] = None
    got, src, notes = town_levels_for(st, vt, as_of=AS_OF)
    assert got == () and src == ()
    assert any("no tract_municipalities table" in n for n in notes)
    floor, fnotes = middle_floor_for(_vt_state(), ledger, local="town_bylaws", as_of=AS_OF)
    assert floor is not None and floor.local and "vt_wy_local_bylaws" in floor.source_ids
    assert any("Town bylaws (variant" in n for n in fnotes)
    plain, _ = middle_floor_for(_vt_state(), ledger)
    assert plain is not None and plain.local == () and "vt_wy_local_bylaws" not in plain.source_ids
    with pytest.raises(ValueError, match="local_rights"):
        middle_floor_for(_vt_state(), ledger, local="everything")


def _floor(sched, sewer, local=()):
    return MiddleFloor(sched, np.array([sewer]), ("vt_acts_2026",), local)


def _level(share, units=4):
    return LocalLevel("vt_test", "Testtown", np.array([share]), (units,) * 5, units)


def test_variant_equals_the_benchmark_with_no_readings(ledger, scenarios, vt):
    plain = mm_run(ledger, scenarios, _floor(vt, 0.0), rent=4000)
    for local in ((), (_level(0.0),), (_level(1.0, units=2),)):  # none, no lots, not above floor
        got = mm_run(ledger, scenarios, _floor(vt, 0.0, local), rent=4000)
        for stage in ("feasible", "units", "authorized", "net"):
            np.testing.assert_array_equal(got.stages[stage], plain.stages[stage])


def test_a_town_allowing_four_takes_out_the_fourplex_on_its_lots(ledger, scenarios, vt):
    plain = mm_run(ledger, scenarios, _floor(vt, 0.0), rent=4000)
    assert plain.stages["authorized"][0, 0] > 0  # unserved: the fourplex is new above the duplex
    full = mm_run(ledger, scenarios, _floor(vt, 0.0, (_level(1.0),)), rent=4000)
    assert full.stages["authorized"][0, 0] == pytest.approx(0.0)
    half = mm_run(ledger, scenarios, _floor(vt, 0.0, (_level(0.5),)), rent=4000)
    assert half.stages["authorized"][0, 0] == pytest.approx(0.5 * plain.stages["authorized"][0, 0])
    assert half.records["floor_town_bylaw_share"].p50 == pytest.approx(0.5)
    assert "Testtown" in half.records["floor_town_bylaw_share"].name
    # "freeze today's rules" stays a separate record and is zero when the rules agree
    assert half.records["floor_freeze_difference"].p50 == 0.0


# --------------------------------------------------------------------------- parking


def test_parking_minimums_gate_on_dates_and_ignore_drafts_and_flags():
    t, notes = local_parking_minimums("VT", as_of=AS_OF)
    by = t.set_index("geoid")
    assert set(by.index) == {BARRE, "5000714875", "5002346000"}  # Barre, Colchester, Montpelier
    assert by.loc[BARRE, "min_small"] == 1.0 and by.loc[BARRE, "min_apartment"] == 1.0
    text = " ".join(notes)
    assert "Burlington (city) (counts from 2026-11-26" in text  # review-date rule
    assert "Essex" in text and "flagged" in text  # flagged fact keeps the default
    later, notes2 = local_parking_minimums("VT", as_of="2026-11-26")
    assert BURLINGTON in set(later["geoid"])
    assert "Rutland (city) (parking fact is draft)" in " ".join(notes2)  # drafts never feed
    wy, wnotes = local_parking_minimums("WY", as_of=AS_OF)
    assert wy.empty and "Laramie (city) (counts from 2026-11-26" in " ".join(wnotes)
    wy2, wnotes2 = local_parking_minimums("WY", as_of="2026-11-26")
    row = wy2.set_index("geoid").loc["5645050"]
    assert row["min_small"] == 1.0 and pd.isna(row["min_apartment"])  # apartments: default
    assert "superseded" in " ".join(wnotes2)  # Teton County's former text never feeds
    assert load_local_settings()["parking"]["skip_flagged"] is True


@pytest.fixture(scope="module")
def tl():
    return load_context("TL")


def _tl_with_readings(ctx, monkeypatch, level=3.0):
    """Testland with one verified 'TL' town reading in force, on every tract of one county."""
    c = replace(ctx, local_law=None)
    tracts = c.tracts.assign(geoid=c.tracts["geoid"].astype(str))
    county = str(tracts["county"].iloc[0])
    tm = pd.DataFrame(
        {"tract": tracts["geoid"], "county": tracts["county"].astype(str), "cousub": "00001",
         "housing_units_2020": 1}
    )  # fmt: skip
    c.derived[("parking_law_table", "tract_municipalities")] = tm
    doc = {
        "doc_id": "tl_bylaw", "state": "TL", "title": "Testtown bylaw", "publisher": "Testtown",
        "url": "https://testtown.example/bylaw.pdf", "retrieved_on": "2026-11-01",
        "sha256": "0" * 64, "kind": "bylaw", "version_state": "final",
        "effective_date": "2026-01-01",
    }  # fmt: skip
    fact = {
        "topic": "parking", "value": f"{level} per unit", "review": "verified",
        "evidence": [{"doc_id": "tl_bylaw", "locator": "Sec. 1", "quote": "spaces per unit"}],
        "numbers": {"residential_min_per_unit": level},
    }  # fmt: skip
    place = {
        "place_id": "tl_testtown", "state": "TL", "name": "Testtown", "geoid": f"{county}00001",
        "status": "verified", "reviewed_on": "2026-11-01", "effective_date": "2026-01-01",
        "facts": [fact],
    }  # fmt: skip
    readings = LocalReadings(documents=(doc,), places=(place,))
    import visionhousing.law.local_readings as lr

    monkeypatch.setattr(lr, "load_local_readings", lambda *a, **k: readings)
    return c


def _parking(c, **opts):
    return run("TL", {"parking": opts}, draws=300, context=c, use_cache=False).reforms["parking"]


def test_parking_variant_equals_benchmark_in_another_state(tl):
    c = replace(tl, local_law=None)
    a = _parking(c)
    b = _parking(c, variant="vt_parking_town_bylaws")  # a Vermont variant on Testland
    np.testing.assert_allclose(b.result.added, a.result.added, rtol=1e-12, atol=1e-9)
    assert b.result.records["parking_verified_share"].p50 == 0.0


def test_parking_local_readings_enter_the_mix(tl, monkeypatch):
    c = _tl_with_readings(tl, monkeypatch, level=3.0)
    a = _parking(c)
    b = _parking(c, minimums="local_readings", readings_state="TL")
    assert b.result.records["parking_verified_share"].p50 > 0
    assert b.added.p50 > a.added.p50  # 3.0 spaces is above the default minimum
    assert "vt_wy_local_bylaws" in b.result.records["parking_verified_share"].source_ids
    assert any("TL local readings" in n for n in b.result.notes)
    # the same reading before its effective date: nothing counts, equal to the benchmark
    monkeypatch.setattr("visionhousing.law.inforce.run_as_of", lambda *a, **k: pd.Timestamp(
        "2025-12-31").date())  # fmt: skip
    d = _parking(c, minimums="local_readings", readings_state="TL")
    np.testing.assert_allclose(d.result.added, a.result.added, rtol=1e-12, atol=1e-9)
