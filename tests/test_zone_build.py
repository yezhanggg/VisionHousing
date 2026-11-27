"""The zoning-district pack step (law/zone_build.py) on a hand-made sample, offline."""

from __future__ import annotations

import pandas as pd
import pytest

from tests.test_rule_area_weights import BENCHES, row, sq
from visionhousing.data import pack
from visionhousing.law import zone_build
from visionhousing.law import zone_rules as zr

MAPPED, RURAL = "4299900001", "4299900002"


def settings() -> dict:
    return {
        "window": {"start": "2027-01-01", "end": "2032-01-01"},
        "sample": [
            {"geoid": MAPPED, "maps": ["now", "old"], "overlays": "ov"},
            {"geoid": RURAL, "maps": [], "overlays": None},
        ],
        "layers": {
            "now": {"district_field": "code", "file": "x"},
            "old": {"district_field": "code", "file": "y"},
            "ov": {"overlay_field": "sym", "keep_types": ["Overlay District"], "file": "z"},
        },
        "split": {"cells_per_side": 60, "min_cell_m": 0.5, "min_piece_share": 0.02},
        "benchmarks": BENCHES,
    }


def feature(code: str, ring, **extra) -> dict:
    return {"attributes": {"code": code, **extra}, "rings": [ring]}


def test_build_tables_end_to_end():
    blocks = pd.DataFrame(
        {
            "geoid": ["429990001001000", "429990001001001", "429990002001000"],
            "tract": ["42999000100", "42999000100", "42999000200"],
            "housing_units": [40, 60, 10],
            "land_area_m2": [1000.0, 1500.0, 9000.0],
        }
    )
    tracts = pd.DataFrame({"geoid": ["42999000100", "42999000200"], "units_1_detached": [50, 8]})
    cousub = pd.DataFrame(
        {"block": list(blocks["geoid"]), "cousub": [MAPPED[5:], MAPPED[5:], RURAL[5:]]}
    )
    layers = {
        "now": {
            "map_version": "2026-11-26",
            "features": [feature("R1", sq(0, 0, 2, 2)), feature("R2", sq(2, 0, 4, 2))],
        },
        "old": {"map_version": "2021-12", "features": [feature("R1", sq(0, 0, 4, 2))]},
        "ov": {
            "features": [
                {
                    "attributes": {"sym": "/PK", "type": "Overlay District"},
                    "rings": [sq(3, 0, 4, 2)],
                },
                {
                    "attributes": {"sym": "/SIGN", "type": "Supplemental Control"},
                    "rings": [sq(0, 0, 4, 2)],
                },
            ]
        },
    }
    shapes = {"blocks": {"429990001001000": [sq(0, 0, 2, 2)], "429990001001001": [sq(2, 0, 4, 2)]}}
    rules = zr.load_rows(
        rows=[
            row("r1", jurisdiction=MAPPED, district="R1", parking_min_sf="1"),
            row("r2", jurisdiction=MAPPED, district="R2", parking_min_sf="2"),
            row("pk", jurisdiction=MAPPED, overlay="/PK", parking_min_sf="0"),
            row(
                "rural",
                jurisdiction=RURAL,
                adu_permission="not_listed",
                exclusivity_clause="verified",
            ),
        ]
    )
    out = zone_build.build_tables(
        {"blocks": blocks, "tracts": tracts},
        cache=None,
        settings=settings(),
        rules=rules,
        layers=layers,
        block_shapes=shapes,
        block_cousub=cousub,
    )
    w = out["rule_area_weights"]
    assert set(w["level"]) == {"zone", "tract", "jurisdiction"}
    zone = w[(w["level"] == "zone") & (w["period"] == "window")]
    park = zone[(zone["jurisdiction"] == MAPPED) & (zone["benchmark"] == "parking")]
    by = park.groupby("opportunity_class")["sf_lots"].sum()
    assert by["known_by_right"] == pytest.approx(15.0, abs=0.5)  # half of R2's 30 lots
    assert by["conditions_relaxed"] == pytest.approx(35.0, abs=0.5)
    rural = zone[(zone["jurisdiction"] == RURAL) & (zone["benchmark"] == "adu")]
    assert list(rural["opportunity_class"]) == ["newly_eligible"]
    assert rural["sf_lots"].sum() == pytest.approx(8.0)
    hist = w[(w["level"] == "jurisdiction") & (w["period"] == "map_history")]
    assert hist["district_changed_lots"].iloc[0] == pytest.approx(30.0, abs=0.5)
    assert "/SIGN" not in set(zone["overlays"])
    assert zone_build.report(w, state_sf_lots=580.0)["checks"] == []
    assert list(out["zone_rule"].columns) == list(zr.ZONE_RULE_COLUMNS)
    for table in zone_build.TABLES:
        assert table in pack.TABLES
        frame = out[table]
        for col in frame.columns:
            assert pack._describe(table, col)


def test_pack_step_skips_states_without_a_sample(tmp_path):
    assert zone_build.add_zone_tables(tmp_path / "VT", tmp_path) == {}


def test_step_is_registered():
    names = [name for name, _tables, _step in pack.RETROFITS]
    assert "zoning districts" in names


@pytest.mark.realdata
def test_built_pa_pack_tables_are_sound():
    """On a built Pennsylvania pack (make data; skipped without one)."""
    from pathlib import Path

    folder = Path(__file__).resolve().parents[1] / "data" / "packs" / "PA"
    if not (folder / "rule_area_weights.csv.gz").is_file():
        pytest.skip("no built PA pack with the zoning-district tables")
    t = pack.load_pack(folder)
    w = t["rule_area_weights"]
    for c in ("district", "overlays", "rule_ids", "review_state", "map_version", "jurisdiction"):
        w[c] = w[c].fillna("").astype(str)
    rep = zone_build.report(w, state_sf_lots=float(t["tracts"]["units_1_detached"].sum()))
    assert rep["checks"] == []
    j = w[(w["level"] == "jurisdiction") & (w["period"] == "window")].set_index("jurisdiction")
    # Pittsburgh's lots match the SB 1346 place weights (law/places.py): 70,572
    assert j.loc["4200361000", "sf_lots"] == pytest.approx(70572, abs=1)
