"""The state_law profile section for states without the Pennsylvania table (MA, VT, WY, TL)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from visionhousing.data.registry import load_registry
from visionhousing.engine.context import TESTLAND
from visionhousing.law import states
from visionhousing.law.inforce import run_as_of
from visionhousing.profile.context import load_profile_context
from visionhousing.profile.registry import build_section

AS_OF = run_as_of()


def _write(folder: Path, name: str, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(folder / f"{name}.csv", index=False)


def _pack(tmp_path: Path, state: str, county: str, tract: str):
    f = tmp_path / state
    f.mkdir()
    _write(f, "counties", [{"geoid": county, "name": "A county"}])
    _write(f, "tracts", [{"geoid": tract, "county": county}])
    return f


@pytest.fixture
def ma(tmp_path):
    f = _pack(tmp_path, "MA", "25001", "25001013700")
    ring = [[-70.6, 41.7], [-70.5, 41.7], [-70.5, 41.8], [-70.6, 41.7]]
    _write(
        f,
        "mbta_3a_districts",
        [
            {"district_id": 1, "municipality": "Bourne", "county": "25001",
             "tract": "25001013700", "zone_name": "Overlay Area 1", "status": "Compliant",
             "capacity_district": 599, "units_per_acre": 17.2, "atlas_read_on": "2026-11-24",
             "geometry": json.dumps({"type": "Polygon", "coordinates": [ring]})},
        ],
    )  # fmt: skip
    return load_profile_context("MA", folder=f)


def test_massachusetts_lists_its_provisions_with_in_force_dates(ma):
    s = build_section("state_law", ma, "25001")
    assert s.status == "ok"
    adu = s.fact("state_law.law_ma_40a_3_adu")
    assert adu.value == "legally_effective"
    assert "effective 2025-02-02" in adu.text and f"in force on {AS_OF}" in adu.text
    assert adu.source == "ma_gl_40a" and adu.measured_at == "state"
    tod = s.fact("state_law.law_ma_40a_3a")
    assert "court: upheld" in tod.text and tod.value == "legally_effective"
    # a provision with no dated record is "not known", never "no"
    defn = s.fact("state_law.law_ma_40a_1a_adu_definition")
    assert "not known (no dated record)" in defn.text
    legal = s.fact("state_law.already_legal_adu")
    assert legal.low < legal.value < legal.high
    county = s.fact("state_law.mbta_3a_districts")
    assert county.value == 1 and county.text == "1 district in 1 town"
    registry = load_registry()
    assert all(f.source in registry for f in s.facts)


def test_massachusetts_tract_inside_a_3a_district_gets_the_note(ma):
    s = build_section("state_law", ma, "25001013700")
    d = s.fact("state_law.mbta_3a_districts")
    assert d.value == 1 and "Overlay Area 1 (Bourne; compliant, room for 599 homes" in d.text
    assert "Section 3A multi-family district" in s.summary


def test_massachusetts_tract_reached_by_a_district_outline_gets_the_note(ma):
    """With the link table, a tract the outline reaches is named, not only the centre tract."""
    _write(ma.folder, "tracts", [{"geoid": "25001013700", "county": "25001"},
                                 {"geoid": "25001013800", "county": "25001"}])  # fmt: skip
    _write(ma.folder, "mbta_3a_district_tracts", [
        {"district_id": 1, "tract": "25001013700", "overlap_acres": 30.0,
         "district_share": 0.6, "centre": 1},
        {"district_id": 1, "tract": "25001013800", "overlap_acres": 20.0,
         "district_share": 0.4, "centre": 0},
    ])  # fmt: skip
    ctx = load_profile_context("MA", folder=ma.folder)
    s = build_section("state_law", ctx, "25001013800")
    d = s.fact("state_law.mbta_3a_districts")
    assert d.label == "Section 3A districts in this tract" and d.value == 1
    assert "Overlay Area 1 (Bourne; compliant" in d.text and "40% of it in this tract" in d.text
    assert "a Section 3A multi-family district reaches this tract" in s.summary
    # the county count is unchanged (districts by their town's county)
    assert build_section("state_law", ctx, "25001").fact("state_law.mbta_3a_districts").value == 1


def test_massachusetts_provisions_show_their_latest_check(ma):
    s = build_section("state_law", ma, "25001")
    for pid in ("ma_40a_1a_adu_definition", "ma_40a_3_adu", "ma_40a_3a"):
        assert s.fact(f"state_law.law_{pid}").year == "2026-11-25", pid


def test_vermont_reads_its_acts_from_the_timeline_and_the_calibration(tmp_path):
    c = load_profile_context("VT", folder=_pack(tmp_path, "VT", "50007", "50007000100"))
    s = build_section("state_law", c, "50007")
    adu = s.fact("state_law.law_vt_24vsa_4412_1_e")
    # the Act 179 record supersedes the owner-occupied version
    assert "Act 179" in adu.label and adu.value == "legally_effective"
    assert f"in force on {AS_OF}" in adu.text and "effective 2026-07-01" in adu.text
    act250 = s.fact("state_law.law_vt_10vsa_6081_dd")
    assert "until 2028-01-01" in act250.text
    assert adu.source == act250.source == "vt_acts_2026"
    assert s.facts[0].id == "state_law.law_vt_24vsa_4412_1_e"  # reform order: ADU first
    # missing middle reads the dated § 4412(1)(D) floor itself (law.rights), not a judged share
    ids = {f.id for f in s.facts}
    assert "state_law.already_legal_missing_middle" not in ids
    assert "state_law.law_vt_24vsa_4412_1_d" in ids
    assert s.fact("state_law.already_legal_adu").range_text == "55% to 85%"
    assert "in force" in s.summary


def test_wyoming_says_no_state_reform_law_found(tmp_path):
    c = load_profile_context("WY", folder=_pack(tmp_path, "WY", "56021", "56021000100"))
    s = build_section("state_law", c, "56021")
    none = s.fact("state_law.no_reform_law")
    assert none.text == "No state reform law found (checked 2026-11-24)"
    assert s.summary == "No state reform law found (checked 2026-11-24)."
    assert len([f for f in s.facts if f.id.startswith("state_law.law_wy_")]) == 5
    ids = {f.id for f in s.facts}
    assert "state_law.already_legal_adu" not in ids and "state_law.mbta_3a_districts" not in ids


def test_testland_has_no_state_law():
    ctx = load_profile_context(TESTLAND)
    county = str(ctx.counties()["geoid"].iloc[0])
    assert build_section("state_law", ctx, county).status == "no_data"


def test_state_records_join_the_state_file_and_the_timeline():
    ma = {r.record_id for r in states.state_records("MA", "25")}
    assert {"ma_adu_by_right_effective", "ma_3a_effective"} <= ma
    vt = {r.record_id for r in states.state_records("VT", "50")}
    assert "vt_4412_adu_act179" in vt and not any(r.startswith("pa_") for r in vt)
    assert states.state_records("WY", "56") == []
    assert states.load_state_file("TL") is None
    assert [c["state"] for c in states.calibration_files("VT")] == ["VT"]


def test_source_for_prefers_own_id_then_the_closest_address():
    reg = load_registry()
    assert states.source_for(reg, source_id="wy_statutes") == "wy_statutes"
    url = "https://malegislature.gov/Laws/SessionLaws/Acts/2024/Chapter150"
    assert states.source_for(reg, urls=[url]) == "ma_acts_2024_c150"
    assert states.source_for(reg, urls=["https://example.org/x"], fallbacks=["nope"]) == ""
