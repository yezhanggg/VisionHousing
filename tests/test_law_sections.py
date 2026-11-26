"""The law facts in the zoning, plans and state_law profile sections, on a tiny hand-made pack."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from visionhousing.data.registry import load_registry
from visionhousing.law import state, tables
from visionhousing.profile.context import load_profile_context
from visionhousing.profile.registry import SECTIONS, build_section

PGH = "42003"
PGH_T, SUB_T = "42003020100", "42003450100"
RURAL, RURAL_T = "42027", "42027010100"


def _write(folder: Path, name: str, frame: pd.DataFrame | list[dict]) -> None:
    pd.DataFrame(frame).to_csv(folder / f"{name}.csv", index=False)


@pytest.fixture
def ctx(tmp_path):
    f = tmp_path / "PA"
    f.mkdir()
    _write(f, "counties", [{"geoid": PGH, "name": "Allegheny County"},
                           {"geoid": RURAL, "name": "Centre County"}])  # fmt: skip
    _write(f, "tracts", [{"geoid": t, "county": t[:5]} for t in (PGH_T, SUB_T, RURAL_T)])
    munis = pd.DataFrame(
        [
            {"county": PGH, "cousub": "61000", "name": "Pittsburgh city", "dced_id": "022612",
             "zoning_regime": "own", "comprehensive_plan": 1, "multi_municipal_plan": 0,
             "population": 300000, "housing_units_2020": 160000},
            {"county": PGH, "cousub": "45000", "name": "Suburb borough", "dced_id": "020001",
             "zoning_regime": "own", "comprehensive_plan": 1, "multi_municipal_plan": 0,
             "population": 100000, "housing_units_2020": 40000},
            {"county": RURAL, "cousub": "00100", "name": "A township", "dced_id": "140001",
             "zoning_regime": "county", "comprehensive_plan": 0, "multi_municipal_plan": 0,
             "population": 3000, "housing_units_2020": 1200},
        ]
    )  # fmt: skip
    munis["cousub"] = munis["cousub"].astype(str)
    _write(f, "municipal_planning", munis)
    _write(
        f,
        "tract_municipalities",
        [
            {"tract": PGH_T, "county": PGH, "cousub": "61000", "housing_units_2020": 900},
            {"tract": SUB_T, "county": PGH, "cousub": "45000", "housing_units_2020": 800},
            {"tract": RURAL_T, "county": RURAL, "cousub": "00100", "housing_units_2020": 300},
        ],
    )
    hand = {
        "documents": [
            {"doc_id": "pgh_ord", "title": "Pittsburgh ADU ordinance", "level": "municipality",
             "publisher": "City", "url": "https://pittsburgh.legistar.com/x",
             "licence": "Public", "access": "hand_read", "source_id": "pgh_legistar"},
        ],
        "rules": [
            {"geoid": "4200361000", "doc_id": "pgh_ord", "section_cite": "§ 912.08",
             "adu_status": "by_right", "adu_detached": True, "evidence_quote": "ADUs by right",
             "evidence_offset": 5, "method": "hand", "confidence": "high",
             "reviewed_on": date(2026, 11, 23), "status": "verified"},
        ],
    }  # fmt: skip
    m = munis.copy()
    m["cousub"] = m["cousub"].str.zfill(5)
    _write(f, "muni_zoning_rules", tables.muni_zoning_rules(m, hand))
    _write(f, "county_land_use", tables.county_land_use(m, {}))
    _write(f, "law_documents", [d.row() for d in tables.municipal_documents(hand)])
    data = state.load_provisions()
    prov = state.provision_rows(data, date(2026, 11, 23))
    notes: list[str] = []

    class Offline:
        def get_bytes(self, url, params=None):
            raise OSError("offline")

    prov += state.bill_rows(
        data, tmp_path, client=Offline(), manifest=None,
        today=lambda: date(2026, 11, 23), notes=notes,
    )  # fmt: skip
    _write(f, "state_law_provisions", [p.row() for p in prov])
    return load_profile_context("PA", folder=f)


def test_state_law_is_a_registered_section():
    assert "state_law" in dict(SECTIONS)


def test_zoning_tract_shows_the_municipal_rule_with_its_citation(ctx):
    s = build_section("zoning", ctx, PGH_T)
    adu = s.fact("zoning.adu_rule")
    assert adu.value == "by_right" and adu.source == "pgh_legistar"
    assert "§ 912.08" in adu.text and "read by hand" in adu.text and "verified" in adu.text
    assert adu.year == "2026-11-23" and adu.measured_at == "municipality"
    assert s.fact("zoning.min_lot").text == "not read yet (unknown)"
    assert "ADUs are allowed by right" in s.summary


def test_unread_municipality_is_unknown_not_no(ctx):
    s = build_section("zoning", ctx, SUB_T)
    adu = s.fact("zoning.adu_rule")
    assert adu.value == "unknown" and "not read yet" in adu.text
    assert s.fact("zoning.parking_min_per_unit_sf").value is None


def test_zoning_county_shows_share_read_and_verified_rules(ctx):
    s = build_section("zoning", ctx, PGH)
    share = s.fact("zoning.share_read")
    assert share.value == pytest.approx(0.75)
    assert share.comparison.against == "state"
    assert s.fact("zoning.adu_rule_4200361000").value == "by_right"
    rural = build_section("zoning", ctx, RURAL)
    assert rural.fact("zoning.under_county_zoning").text == "1 (3,000 residents)"
    assert rural.fact("zoning.adu_rule").value == "unknown"  # a one-municipality county


def test_state_law_reach_and_mpc_exclusion(ctx):
    pgh = build_section("state_law", ctx, PGH_T)
    assert "does not govern zoning here" in pgh.summary
    assert "not in force for Philadelphia or Pittsburgh" in pgh.fact("state_law.code_adu").text
    sb = pgh.fact("state_law.bill_sb1346")
    assert "would reach 0%" in sb.text and sb.value == "pending"
    hb = pgh.fact("state_law.bill_hb2186")
    assert "would reach 100%" in hb.text and "little would change" in hb.text
    assert "depends" in pgh.fact("state_law.bill_hb1459").text
    county = build_section("state_law", ctx, PGH)
    assert "would reach 25% of residents" in county.fact("state_law.bill_sb1346").text
    assert "little would change" not in county.fact("state_law.bill_hb2186").text
    registry = load_registry()
    assert all(f.source in registry for f in county.facts)


def test_plans_show_commission_link_from_the_citation_list(ctx):
    s = build_section("plans", ctx, RURAL)
    c = s.fact("plans.county_commission")
    assert c is not None and c.text.startswith("https://")


def test_law_sections_without_tables_say_no_data(tmp_path):
    f = tmp_path / "PA"
    f.mkdir()
    _write(f, "counties", [{"geoid": RURAL, "name": "Centre County"}])
    _write(f, "tracts", [{"geoid": RURAL_T, "county": RURAL}])
    c = load_profile_context("PA", folder=f)
    assert build_section("state_law", c, RURAL).status == "no_data"
