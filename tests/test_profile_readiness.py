"""Profile sections "Water and sewer readiness" and "Hazards and land" on a Testland copy.

The synthetic tables are made here from Testland's tracts, so the numbers the
sections show can be checked by hand. No network.
"""

from __future__ import annotations

import shutil

import pandas as pd
import pytest

from visionhousing.data.registry import load_registry
from visionhousing.engine.context import TESTLAND_DIR
from visionhousing.profile import build_profile
from visionhousing.profile.context import load_profile_context
from visionhousing.profile.registry import build_section

COUNTY = "99001"
TRACT = "99001000100"


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    folder = tmp_path_factory.mktemp("tl") / "TL"
    shutil.copytree(TESTLAND_DIR, folder)
    tracts = pd.read_csv(folder / "tracts.csv", dtype={"geoid": str, "county": str})
    geoids = list(tracts["geoid"])
    n = len(geoids)
    infra = pd.DataFrame(
        {
            "geoid": geoids,
            "sewer_status": ["verified" if i % 3 == 0 else "unknown" for i in range(n)],
            "sewer_share": [0.8 if i % 3 == 0 else None for i in range(n)],
            "sewer_known": [1.0 if i % 3 == 0 else 0.0 for i in range(n)],
            "constraint_share": [0.0] * n,
            "sewer_source": [
                "pa_montco_sewer_service_areas" if i % 3 == 0 else "" for i in range(n)
            ],
            "water_share": [0.9] * n,
            "epa_share": [0.5] * n,
            "act537_class": ["20plus"] * n,
        }
    )
    infra.to_csv(folder / "infra_tract.csv", index=False)
    homes = tracts.groupby("county")["housing_units"].sum()
    county = pd.DataFrame(
        {
            "county": list(homes.index),
            "homes": list(homes.values),
            "tracts_verified": [3] * len(homes),
            "tracts_outside": [0] * len(homes),
            "tracts_constraint": [0] * len(homes),
            "tracts_unknown": [7] * len(homes),
            "homes_verified": [round(h * 0.3) for h in homes.values],
            "homes_outside": [round(h * 0.1) for h in homes.values],
            "homes_constraint": [0] * len(homes),
            "homes_unknown": [h - round(h * 0.3) - round(h * 0.1) for h in homes.values],
            "water_share": [0.9] * len(homes),
            "epa_share": [0.5] * len(homes),
            "sewer_sources": ["pa_montco_sewer_service_areas"] * len(homes),
            "sewer_as_of": ["2022-07"] * len(homes),
            "act537_lt10": [0.1] * len(homes),
            "act537_10to20": [0.2] * len(homes),
            "act537_20plus": [0.6] * len(homes),
            "act537_prereg": [0.1] * len(homes),
            "act537_unmatched": [0.0] * len(homes),
            "cwns_total": [1000.0] * len(homes),
            "cwns_treatment": [400.0] * len(homes),
            "cwns_new_sewers": [100.0] * len(homes),
            "cwns_sewer_repair": [200.0] * len(homes),
            "cwns_cso": [0.0] * len(homes),
            "cwns_stormwater": [300.0] * len(homes),
            "cwns_decentralized": [0.0] * len(homes),
            "cwns_nonpoint": [0.0] * len(homes),
            "epa_plants": [2] * len(homes),
        }
    )
    county.to_csv(folder / "infra_county.csv", index=False)
    pd.DataFrame(
        {
            "county": [COUNTY, COUNTY],
            "kind": ["sewer", "water"],
            "rank": [1, 1],
            "name": ["Harbor Sewer Authority", "Harbor Water Co"],
            "homes": [900, 1200],
            "contact_url": ["", "https://sdwis.epa.gov/x"],
            "source": ["pa_montco_sewer_service_areas", "pa_dep_pws_service_areas"],
        }
    ).to_csv(folder / "infra_contacts.csv", index=False)
    pd.DataFrame(
        {
            "geoid": geoids,
            "sfha_share": [0.1 if i % 2 == 0 else None for i in range(n)],
            "sfha_dev_share": [0.05] * n,
            "developed_share": [0.5] * n,
            "impervious_share": [0.2] * n,
            "steep_share": [0.25] * n,
        }
    ).to_csv(folder / "hazard_tract.csv", index=False)
    pd.DataFrame(
        {
            "county": list(homes.index),
            "tracts": [10] * len(homes),
            "tracts_flood_unmapped": [5] * len(homes),
            "homes": list(homes.values),
            "sfha_homes": [round(h * 0.05) for h in homes.values],
            "land_acres": [10000] * len(homes),
            "developed_acres": [4000] * len(homes),
            "sfha_share": [0.05] * len(homes),
            "sfha_dev_share": [0.04] * len(homes),
            "developed_share": [0.4] * len(homes),
            "impervious_share": [0.15] * len(homes),
            "steep_share": [0.2] * len(homes),
            "nfhl_coverage": [1.0] * len(homes),
            "sfha_polygons": [12] * len(homes),
            "nlcd_year": [2025] * len(homes),
            "read_on": ["2026-11-24"] * len(homes),
        }
    ).to_csv(folder / "hazard_county.csv", index=False)
    return folder


@pytest.fixture(scope="module")
def ctx(pack):
    return load_profile_context("TL", folder=pack)


def _check(section):
    registry = load_registry()
    assert section.status == "ok"
    for f in section.facts:
        assert f.source in registry, f.id
        assert f.measured_at in ("state", "county", "tract")
    for chart in section.charts:
        shares = [i.share for i in chart.items if i.share is not None]
        assert abs(sum(shares) - 1) < 0.002, chart.id
        assert all(s in registry for s in chart.source_ids)


def test_infrastructure_county(ctx):
    sec = build_section("infrastructure", ctx, COUNTY)
    _check(sec)
    facts = {f.id.split(".", 1)[1]: f for f in sec.facts}
    assert facts["sewer_verified"].value == pytest.approx(0.3, abs=0.001)
    assert facts["sewer_unknown"].value == pytest.approx(0.6, abs=0.001)
    assert facts["sewer_constraint"].value == 0
    assert facts["water_inside"].value == pytest.approx(0.9)
    assert facts["act537_old"].value == pytest.approx(0.7)
    assert facts["cwns_total"].value == 1000
    assert facts["contact_water"].value == "Harbor Water Co"
    assert "sdwis" in facts["contact_water"].note
    ids = [c.id for c in sec.charts]
    assert ids == [
        "infrastructure.sewer_evidence",
        "infrastructure.water",
        "infrastructure.act537",
        "infrastructure.cwns_needs",
    ]
    assert sec.headline is not None and sec.headline.fact == "infrastructure.sewer_verified"
    assert "Distance to a sewer line" in sec.limits


def test_infrastructure_tract(ctx):
    sec = build_section("infrastructure", ctx, TRACT)
    _check(sec)
    facts = {f.id.split(".", 1)[1]: f for f in sec.facts}
    assert facts["sewer_verified"].value == pytest.approx(0.8)
    assert facts["sewer_outside"].value == pytest.approx(0.2)
    assert facts["sewer_status"].value == "verified"
    assert facts["act537_class"].value == "Plan 20 or more years old"
    assert facts["sewer_verified"].comparison is not None  # compared with the county


def test_hazards_county_and_tract(ctx):
    sec = build_section("hazards", ctx, COUNTY)
    _check(sec)
    facts = {f.id.split(".", 1)[1]: f for f in sec.facts}
    assert facts["homes_in_floodplain"].value == pytest.approx(0.05)
    assert facts["steep_land"].value == pytest.approx(0.2)
    assert facts["flood_map_coverage"].value == 1.0
    assert [c.id for c in sec.charts] == [
        "hazards.floodplain_homes",
        "hazards.developed_land",
        "hazards.steep_land",
    ]
    tract = build_section("hazards", ctx, TRACT)
    _check(tract)
    t = {f.id.split(".", 1)[1]: f for f in tract.facts}
    assert t["homes_in_floodplain"].value == pytest.approx(0.1)
    blank = build_section("hazards", ctx, "99001000200")
    b = {f.id.split(".", 1)[1]: f for f in blank.facts}
    assert b["homes_in_floodplain"].value is None  # no digital flood map: blank, not zero
    assert b["steep_land"].value == pytest.approx(0.25)


def test_sections_say_no_data_without_their_tables():
    plain = load_profile_context("TL")
    assert build_section("infrastructure", plain, COUNTY).status == "no_data"
    assert build_section("hazards", plain, COUNTY).status == "no_data"


def test_whole_profile_still_builds(pack, ctx):
    profile = build_profile("TL", COUNTY, ctx=ctx)
    keys = [s.key for s in profile.sections]
    assert keys.index("infrastructure") < keys.index("hazards")
    assert profile.section("infrastructure").status == "ok"
