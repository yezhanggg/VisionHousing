"""Zoning-district weights (law/rule_weights.py) on hand-made maps.

Five deliberately difficult intersections (plan §2, "Geometry and law tests"):
a tract split between jurisdictions and a block split between districts, a
station block at a river, an overlay that changes the base parking rule, a
district the rule table does not cover, and a rule that starts mid-window.
Every map here is invented for the test (a fictional place near 40° N).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from visionhousing.law import rule_weights as rw
from visionhousing.law import zone_rules as zr

WINDOW = (date(2027, 1, 1), date(2032, 1, 1))
BENCHES = {
    "adu": {
        "detached_allowed": True,
        "min_size_sf": 600,
        "owner_occupancy": False,
        "extra_parking": 0,
    },
    "missing_middle": {"units_min": 2, "units_max": 6},
    "parking": {"min_per_unit": 0},
    "tod": {},
}
FINE = rw.SplitRule(cells_per_side=120, min_cell_m=0.5, min_piece_share=0.02)
U = 0.001  # one map unit in degrees (about 85 m east-west, 111 m north-south)
J1, J2 = "4299900001", "4299900002"


def sq(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    """A rectangle in map units, as a lon/lat ring near (-77, 40)."""
    return [
        [-77 + x0 * U, 40 + y0 * U],
        [-77 + x1 * U, 40 + y0 * U],
        [-77 + x1 * U, 40 + y1 * U],
        [-77 + x0 * U, 40 + y1 * U],
    ]


def row(
    rule_id: str,
    jurisdiction: str = J1,
    district: str = "*",
    overlay: str = "none",
    **fields: object,
) -> dict[str, object]:
    """A complete zone_rule row: every field unknown (or inherit) unless given."""
    fill = zr.INHERIT if overlay != "none" else zr.UNKNOWN
    out: dict[str, object] = {
        "rule_id": rule_id,
        "jurisdiction": jurisdiction,
        "district": district,
        "overlay": overlay,
        "prototype": "sf_lot",
        "valid_from": "2020-01-01",
        "valid_from_unknown": False,
        "valid_to": "",
        "review_state": "verified_code",
        "doc_id": "testland_doc",
        "section_cite": "Testland § 1",
        "reviewed_on": "2026-11-26",
    }
    out.update({k: fill for k in zr.RULE_FIELDS})
    if overlay == "none":
        out["exclusivity_clause"] = "unread"
    out.update(fields)
    return out


def weights_of(
    blocks: dict[str, float],
    tract: str = "42999000100",
    cousub: dict[str, str] | None = None,
    sf: float | None = None,
) -> pd.DataFrame:
    """Block weights from housing units (tract one-unit detached = their sum unless given)."""
    b = pd.DataFrame(
        {
            "geoid": list(blocks),
            "tract": tract,
            "housing_units": list(blocks.values()),
            "land_area_m2": 1000.0,
        }
    )
    t = pd.DataFrame({"geoid": [tract], "units_1_detached": [sf or sum(blocks.values())]})
    cs = cousub or dict.fromkeys(blocks, J1[5:])
    bc = pd.DataFrame({"block": list(cs), "cousub": list(cs.values())})
    juris = {f"{g[:5]}{c}" for g, c in cs.items()}
    return rw.block_weights(b, t, bc, juris)


def run(
    pieces: pd.DataFrame, weights: pd.DataFrame, rules: list[zr.ZoneRule], jurisdiction: str = J1
) -> pd.DataFrame:
    return rw.zone_weights(
        pieces,
        weights,
        rules,
        jurisdiction=jurisdiction,
        map_name=None,
        map_version="test",
        period="window",
        window=WINDOW,
        benches=BENCHES,
    )


def lots(table: pd.DataFrame, bench: str, cls: str) -> float:
    z = table[(table["benchmark"] == bench) & (table["opportunity_class"] == cls)]
    return float(z["sf_lots"].sum())


# --------------------------------------------------------------------------- 1 split tract


def test_split_tract_block_split_by_area_not_centre():
    """An L-shaped block: 60% of its area in R1, but its centre point lies in R2."""
    corners = [(0, 0), (3, 0), (3, 1), (1, 1), (1, 3), (0, 3)]
    block = [[-77 + x * U, 40 + y * U] for x, y in corners]
    districts = [("R1", [sq(0, -1, 1, 4)]), ("R2", [sq(1, -1, 4, 4)])]
    pieces, info = rw.split_blocks({"429990001001000": [block]}, districts, None, FINE)
    share = pieces.set_index("district")["share"]
    assert share["R1"] == pytest.approx(0.6, abs=0.02)
    assert share["R2"] == pytest.approx(0.4, abs=0.02)
    assert pieces["share"].sum() == pytest.approx(1.0)
    # the block's area-weighted centre is at x = 1.1 units, inside R2: a centroid rule
    # would give R2 everything
    assert info["method"].iloc[0] == "grid"


def test_split_tract_between_jurisdictions():
    """One tract, two municipalities: each keeps only its own blocks' lots."""
    w = weights_of(
        {"429990001001000": 60, "429990001001001": 40},
        cousub={"429990001001000": J1[5:], "429990001001001": J2[5:]},
    )
    pieces = pd.DataFrame(
        {
            "block": ["429990001001000", "429990001001000", "429990001001001"],
            "district": ["R1", "R2", "R1"],
            "overlays": "",
            "share": [0.6, 0.4, 1.0],
        }
    )
    rules = zr.load_rows(
        rows=[
            row("r1", district="R1", adu_permission="prohibited"),
            row("r2", district="R2", adu_permission="special_exception"),
            row("j2", jurisdiction=J2, adu_permission="prohibited"),
        ]
    )
    t1 = run(pieces, w, rules)
    assert lots(t1, "adu", "newly_eligible") == pytest.approx(36.0)
    assert lots(t1, "adu", "discretionary") == pytest.approx(24.0)
    tracts = rw.tract_rows(pieces, w, t1)
    adu = tracts[tracts["benchmark"] == "adu"]
    assert adu["sf_lots"].sum() == pytest.approx(60.0)  # J2's 40 lots are not in J1's tract row
    t2 = run(pieces, w, rules, jurisdiction=J2)
    assert lots(t2, "adu", "newly_eligible") == pytest.approx(40.0)
    assert rw.check_weights(pd.concat([t1, t2])) == []


# --------------------------------------------------------------------------- 2 river station


def test_river_station_block_unmatched_water_and_unresolved_bucket():
    """A station block half in the river, and a block wholly in it.

    Homes of the first go to its zoned half; the second goes to the unresolved
    bucket; unmatched area is reported.
    """
    districts = [("CMX", [sq(0, 0, 2, 4)])]  # the river runs over x = 2..4, unzoned
    blocks = {
        "429990001001000": [sq(1, 0, 3, 2)],  # half land, half river
        "429990001001001": [sq(2.5, 2, 3.5, 3)],
    }  # in the river (moored homes)
    pieces, info = rw.split_blocks(blocks, districts, None, FINE)
    unmatched = info.set_index("block")["unmatched"]
    assert unmatched["429990001001000"] == pytest.approx(0.5, abs=0.02)
    assert pieces[pieces["block"] == "429990001001000"]["share"].sum() == pytest.approx(1.0)
    river = pieces[pieces["block"] == "429990001001001"]
    assert list(river["district"]) == [rw.UNMATCHED]
    w = weights_of({"429990001001000": 30, "429990001001001": 10})
    rules = zr.load_rows(rows=[row("cmx", district="CMX", parking_min_sf="0")])
    table = run(pieces, w, rules)
    assert lots(table, "parking", "known_by_right") == pytest.approx(30.0)
    assert lots(table, "parking", "unresolved") == pytest.approx(10.0)
    assert rw.check_weights(table, pieces=pieces) == []
    j = rw.jurisdiction_row(J1, "test", "window", w, info, pieces)
    assert j["unresolved_geometry_lots"] == pytest.approx(10.0)
    assert j["unmatched_area_share"] == pytest.approx((0.5 * 30 + 1.0 * 10) / 40, abs=0.01)


# --------------------------------------------------------------------------- 3 overlay parking


def test_overlay_changes_base_parking_only_inside_it():
    """The base rule asks 2 spaces; inside the overlay, 0. Other fields are inherited."""
    districts = [("R2", [sq(0, 0, 4, 4)])]
    overlays = [("/PK", [sq(0, 0, 2, 4)])]
    pieces, _ = rw.split_blocks({"429990001001000": [sq(0, 0, 4, 2)]}, districts, overlays, FINE)
    assert set(pieces["overlays"]) == {"", "/PK"}
    w = weights_of({"429990001001000": 50})
    rules = zr.load_rows(
        rows=[
            row("base", district="R2", parking_min_sf="2", adu_permission="prohibited"),
            row("pk", overlay="/PK", parking_min_sf="0", parking_overlay_change="minimum removed"),
        ]
    )
    table = run(pieces, w, rules)
    assert lots(table, "parking", "known_by_right") == pytest.approx(25.0, abs=1.0)
    assert lots(table, "parking", "conditions_relaxed") == pytest.approx(25.0, abs=1.0)
    # the overlay does not touch the ADU rule (inherit)
    assert lots(table, "adu", "newly_eligible") == pytest.approx(50.0)
    inside = table[(table["overlays"] == "/PK") & (table["benchmark"] == "parking")]
    assert inside["rule_ids"].iloc[0] == "base+pk"


def test_overlays_no_rule_names_are_dropped_from_the_zone_key():
    districts = [("R2", [sq(0, 0, 4, 4)])]
    overlays = [("/XX", [sq(0, 0, 2, 4)])]
    pieces, _ = rw.split_blocks({"429990001001000": [sq(0, 0, 4, 2)]}, districts, overlays, FINE)
    w = weights_of({"429990001001000": 50})
    table = run(pieces, w, zr.load_rows(rows=[row("base", district="R2")]))
    assert set(table["overlays"]) == {""}


# --------------------------------------------------------------------------- 4 missing district


def test_missing_district_stays_unresolved_and_in_the_denominator():
    districts = [("R1", [sq(0, 0, 2, 2)]), ("NEW-9", [sq(2, 0, 4, 2)])]
    pieces, _ = rw.split_blocks({"429990001001000": [sq(0, 0, 4, 2)]}, districts, None, FINE)
    w = weights_of({"429990001001000": 40})
    rules = zr.load_rows(
        rows=[
            row(
                "r1",
                district="R1",
                adu_permission="by_right",
                adu_forms="internal,detached",
                adu_max_sf="1000",
                adu_owner_occupancy="no",
                parking_min_adu="0",
            )
        ]
    )
    table = run(pieces, w, rules)
    assert lots(table, "adu", "known_by_right") == pytest.approx(20.0, abs=1.0)
    miss = table[(table["district"] == "NEW-9") & (table["benchmark"] == "adu")]
    assert miss["opportunity_class"].iloc[0] == "unresolved"
    assert "No reviewed rule" in miss["why"].iloc[0]
    assert miss["rule_ids"].iloc[0] == "none"
    assert table[table["benchmark"] == "adu"]["weight"].sum() == pytest.approx(1.0)


# --------------------------------------------------------------------------- 5 mid-window phase


def test_rule_starting_mid_window_splits_the_weight_by_days():
    pieces = pd.DataFrame(
        {"block": ["429990001001000"], "district": ["R1"], "overlays": "", "share": [1.0]}
    )
    w = weights_of({"429990001001000": 100})
    rules = zr.load_rows(
        rows=[
            row("old", district="R1", valid_to="2029-07-01", adu_permission="prohibited"),
            row(
                "new",
                district="R1",
                valid_from="2029-07-01",
                adu_permission="by_right",
                adu_forms="internal,detached",
                adu_max_sf="800",
                adu_owner_occupancy="no",
                parking_min_adu="0",
            ),
        ]
    )
    table = run(pieces, w, rules)
    adu = table[table["benchmark"] == "adu"].set_index("rule_ids")
    days = (WINDOW[1] - WINDOW[0]).days
    before = (date(2029, 7, 1) - WINDOW[0]).days / days
    assert adu.loc["old", "window_share"] == pytest.approx(before)
    assert adu.loc["new", "window_share"] == pytest.approx(1 - before)
    assert adu.loc["old", "opportunity_class"] == "newly_eligible"
    assert adu.loc["new", "opportunity_class"] == "known_by_right"
    assert adu["sf_lots"].sum() == pytest.approx(100.0)
    assert rw.check_weights(table) == []


# --------------------------------------------------------------------------- the rest


def test_slivers_are_folded_and_reported():
    districts = [("R1", [sq(0, 0, 4, 4)]), ("C1", [sq(3.95, 0, 5, 4)])]
    s = rw.split_block(
        [rw.Projection(-77, 40)(r) for r in [sq(0, 0, 4, 1)]],
        rw.ShapeIndex.build(districts, rw.Projection(-77, 40)),
        None,
        FINE,
    )
    assert list(s.pieces) == [("R1", ())]
    assert 0 < s.sliver < 0.02
    assert sum(s.pieces.values()) == pytest.approx(1.0)


def test_no_map_gives_one_whole_jurisdiction_zone():
    pieces, info = rw.split_blocks({"429990001001000": [sq(0, 0, 1, 1)]}, None, None, FINE)
    assert list(pieces["district"]) == [rw.NO_MAP]
    assert list(info["method"]) == ["whole_jurisdiction"]


def test_block_weights_split_tract_lots_by_housing_units():
    w = weights_of({"429990001001000": 30, "429990001001001": 10}, sf=20)
    assert w.set_index("block")["sf_lots"].to_dict() == {
        "429990001001000": pytest.approx(15.0),
        "429990001001001": pytest.approx(5.0),
    }


def test_district_change_counts_lots_that_moved():
    old = pd.DataFrame(
        {"block": ["b1", "b1", "b2"], "district": ["R1", "R2", "R1"], "share": [0.5, 0.5, 1.0]}
    )
    new = pd.DataFrame({"block": ["b1", "b2"], "district": ["R1", "R1"], "share": [1.0, 1.0]})
    w = pd.DataFrame({"block": ["b1", "b2"], "sf_lots": [10.0, 7.0]})
    assert rw.district_change(old, new, w) == pytest.approx(5.0)


def test_check_weights_flags_a_bad_sum():
    t = pd.DataFrame(
        {
            "level": ["zone"],
            "jurisdiction": [J1],
            "map_version": ["m"],
            "period": ["window"],
            "benchmark": ["adu"],
            "district": ["R1"],
            "overlays": [""],
            "opportunity_class": ["unresolved"],
            "sf_lots": [5.0],
            "weight": [0.9],
            "window_share": [1.0],
        }
    )
    assert any("sum to" in p for p in rw.check_weights(t))


def test_u13_answers_count_only_verified_rows_unless_asked():
    t = pd.DataFrame(
        {
            "level": "zone",
            "period": "window",
            "benchmark": "adu",
            "jurisdiction": [J1, J1],
            "review_state": ["verified_code", "guidance"],
            "opportunity_class": ["newly_eligible", "conditions_relaxed"],
            "sf_lots": [3.0, 7.0],
        }
    )
    strict = rw.u13_answers(t, "adu").set_index("answer")["sf_lots"]
    assert strict.to_dict() == {"newly_allowed": 3.0, "unresolved": 7.0}
    loose = rw.u13_answers(t, "adu", include_guidance=True).set_index("answer")["sf_lots"]
    assert loose.to_dict() == {"changed_condition": 7.0, "newly_allowed": 3.0}
    munis = pd.DataFrame(
        {
            "geoid": [J1, J2, "4299900003"],
            "answer": ["unresolved", "already_allowed", "outside"],
            "weight": [10.0, 30.0, 5.0],
        }
    )
    moved = rw.move_u13(munis, rw.u13_answers(t, "adu")).set_index("answer")
    assert moved.loc["unresolved", "before"] == pytest.approx(0.25)
    assert moved.loc["unresolved", "after"] == pytest.approx(7 / 40)
    assert moved.loc["newly_allowed", "after"] == pytest.approx(3 / 40)
    assert moved["after"].sum() == pytest.approx(1.0)
