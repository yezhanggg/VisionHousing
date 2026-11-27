"""Reviewed zoning rules by district (law/zone_rules.py), the shipped rows, and a worked trace.

The trace follows one citation (Pittsburgh's Ordinance 31, § 912.08.C.2, at its
offset in the pinned text) through a zone rule, a lot weight and the state
output (the sample's share of Pennsylvania's single-family lots in each class).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
import yaml

from tests.test_rule_area_weights import BENCHES, WINDOW, row
from visionhousing.law import rule_weights as rw
from visionhousing.law import timeline, zone_build
from visionhousing.law import zone_rules as zr

PGH, PHL, FRK = "4200361000", "4210160000", "4201327600"
ON = date(2027, 1, 1)


@pytest.fixture(scope="module")
def shipped() -> list[zr.ZoneRule]:
    return zr.load_rows()


# --------------------------------------------------------------------------- loader


@pytest.mark.parametrize(
    "change, message",
    [
        ({"adu_permission": ""}, "is blank"),
        ({"height_max_ft": "inherit"}, "'inherit' in a base row"),
        ({"review_state": "unread", "adu_permission": "by_right"}, "unread row"),
        ({"adu_permission": "allowed"}, "adu_permission"),
        ({"exclusivity_clause": "maybe"}, "exclusivity_clause"),
        ({"valid_from": "", "valid_from_unknown": False}, "valid_from is empty"),
        ({"valid_to": "2019-01-01"}, "not after"),
    ],
)
def test_loader_refuses_bad_rows(change, message):
    with pytest.raises(ValueError, match=message):
        zr.load_rows(rows=[row("x", **change)])


def test_loader_refuses_missing_fields_and_duplicates():
    r = row("x")
    del r["parking_min_sf"]
    with pytest.raises(ValueError, match="missing parking_min_sf"):
        zr.load_rows(rows=[r])
    with pytest.raises(ValueError, match="duplicate"):
        zr.load_rows(rows=[row("x"), row("x")])


def test_overlay_list_gives_one_row_per_overlay():
    rules = zr.load_rows(rows=[row("ov", overlay="/A,/B", adu_permission="unknown")])
    assert [(r.rule_id, r.overlay) for r in rules] == [("ov:/A", "/A"), ("ov:/B", "/B")]


def test_start_unknown_counts_from_review_date():
    r = zr.load_rows(
        rows=[row("x", valid_from="", valid_from_unknown=True, reviewed_on="2026-11-26")]
    )[0]
    assert not zr.counts_on(r, date(2026, 11, 25))
    assert zr.counts_on(r, date(2026, 11, 26))


# --------------------------------------------------------------------------- classes


@pytest.mark.parametrize(
    "fields, cls",
    [
        (
            {
                "adu_permission": "by_right",
                "adu_forms": "internal,detached",
                "adu_max_sf": "1000",
                "adu_owner_occupancy": "no",
                "parking_min_adu": "0",
            },
            "known_by_right",
        ),
        (
            {
                "adu_permission": "by_right",
                "adu_forms": "internal,existing_accessory",
                "adu_max_sf": "800",
                "adu_owner_occupancy": "yes",
                "parking_min_adu": "0",
            },
            "conditions_relaxed",
        ),
        (
            {
                "adu_permission": "by_right",
                "adu_forms": "internal,detached",
                "adu_max_sf": "500",
                "adu_owner_occupancy": "no",
                "parking_min_adu": "0",
            },
            "conditions_relaxed",
        ),
        ({"adu_permission": "by_right"}, "conditions_relaxed"),  # conditions not read
        ({"adu_permission": "special_exception"}, "discretionary"),
        ({"adu_permission": "prohibited"}, "newly_eligible"),
        ({"adu_permission": "not_listed", "exclusivity_clause": "verified"}, "newly_eligible"),
        ({"adu_permission": "not_listed", "exclusivity_clause": "hearing_route"}, "discretionary"),
        ({"adu_permission": "not_listed", "exclusivity_clause": "unread"}, "unresolved"),
        ({"adu_permission": "unknown"}, "unresolved"),
    ],
)
def test_adu_classes(fields, cls):
    full = {k: zr.UNKNOWN for k in zr.RULE_FIELDS} | fields
    assert zr.classify(full, "adu", BENCHES)[0] == cls


@pytest.mark.parametrize(
    "units, path, cls",
    [
        ("6", "unknown", "known_by_right"),
        ("4", "unknown", "conditions_relaxed"),
        ("1", "unknown", "newly_eligible"),
        ("1", "special_exception", "discretionary"),
        ("unknown", "unknown", "unresolved"),
    ],
)
def test_missing_middle_classes(units, path, cls):
    full = {k: zr.UNKNOWN for k in zr.RULE_FIELDS} | {
        "units_by_right": units,
        "multi_unit_review_path": path,
    }
    assert zr.classify(full, "missing_middle", BENCHES)[0] == cls


def test_parking_and_tod_classes():
    f = {k: zr.UNKNOWN for k in zr.RULE_FIELDS}
    assert zr.classify(f | {"parking_min_sf": "0"}, "parking", BENCHES)[0] == "known_by_right"
    assert zr.classify(f | {"parking_min_sf": "2"}, "parking", BENCHES)[0] == "conditions_relaxed"
    assert zr.classify(f, "parking", BENCHES)[0] == "unresolved"
    assert zr.classify(f | {"height_max_ft": "90"}, "tod", BENCHES)[0] == "unresolved"
    with pytest.raises(ValueError, match="fees"):
        zr.classify(f, "fees", BENCHES)


def test_exact_district_beats_star_and_overlays_apply_in_order():
    rules = zr.load_rows(
        rows=[
            row("all", adu_permission="prohibited"),
            row("r1", district="R1", adu_permission="special_exception"),
            row("b", overlay="/B", adu_permission="unknown"),
            row("a", overlay="/A", adu_permission="prohibited", precedence=1),
        ]
    )
    assert zr.resolve(rules, row("q")["jurisdiction"], "R1", [], ON).rule_ids == ("r1",)
    assert zr.resolve(rules, row("q")["jurisdiction"], "R7", [], ON).rule_ids == ("all",)
    both = zr.resolve(rules, row("q")["jurisdiction"], "R1", ["/A", "/B"], ON)
    assert both.rule_ids == ("r1", "b", "a")  # /B (precedence 0) first, /A (1) last wins
    assert both.fields["adu_permission"] == "prohibited"
    none = zr.resolve(rules, "4299999999", "R1", [], ON)
    assert none.missing and none.fields["adu_permission"] == zr.UNKNOWN


def test_window_intervals_sum_to_one():
    rules = zr.load_rows(rows=[row("a", valid_to="2028-01-01"), row("b", valid_from="2028-01-01")])
    iv = zr.window_intervals(rules, row("q")["jurisdiction"], *WINDOW)
    assert [a.isoformat() for a, _b, _s in iv] == ["2027-01-01", "2028-01-01"]
    assert sum(s for *_x, s in iv) == pytest.approx(1.0)


# --------------------------------------------------------------------------- shipped rows


def test_shipped_rows_load_and_name_known_documents(shipped):
    docs = yaml.safe_load((zr.RULES_PATH.parent / "municipal_rules.yaml").read_text("utf-8"))
    by_id = {d["doc_id"]: d for d in docs["documents"]}
    for r in shipped:
        assert r.meta["doc_id"] in by_id, r.rule_id
        sha = r.meta.get("text_sha256")
        if sha:
            assert sha == by_id[r.meta["doc_id"]]["sha256"], r.rule_id
    assert {r.jurisdiction for r in shipped} == {PGH, PHL, FRK}


def test_shipped_rows_agree_with_the_law_timeline(shipped):
    records = {rec.record_id: rec for rec in timeline.load_timeline()}
    linked = [r for r in shipped if r.meta.get("timeline_record")]
    assert linked
    for r in linked:
        rec = records[r.meta["timeline_record"]]
        if not rec.start_unknown:
            assert r.valid_from == rec.start, r.rule_id
        assert r.valid_to == rec.end, r.rule_id


def test_shipped_rows_never_read_from_a_code_host(shipped):
    hosts = ("ecode360", "amlegal", "municode")
    for r in shipped:
        text = " ".join(r.meta.values()).lower()
        assert not any(h in text for h in hosts), r.rule_id


def test_shipped_classes_on_the_window_start(shipped):
    def cls(j, d, ovs, bench="adu"):
        res = zr.resolve(shipped, j, d, ovs, ON)
        return zr.classify(res.fields, bench, BENCHES)[0], res

    c, res = cls(PHL, "RSA-5", [])
    assert c == "conditions_relaxed" and res.review_state == "guidance"
    assert cls(PHL, "RSA-5", ["/NE"])[0] == "unresolved"
    assert cls(PHL, "CMX-1", ["/NDO"])[0] == "unresolved"
    assert cls(PHL, "RM-1", [])[0] == "unresolved"
    assert cls(PGH, "R1D-L", [])[0] == "known_by_right"
    assert cls(PGH, "R1D-L", ["PRO"], "parking")[0] == "known_by_right"
    assert cls(FRK, rw.NO_MAP, [])[0] == "newly_eligible"
    assert cls(FRK, rw.NO_MAP, [], "parking")[0] == "conditions_relaxed"
    assert cls(FRK, rw.NO_MAP, [], "missing_middle")[0] == "unresolved"
    # before Ordinance 31 nothing counts (its start is unknown): unresolved, not "no"
    before = zr.resolve(shipped, PGH, "R1D-L", [], date(2026, 6, 1))
    assert before.missing


def test_zone_rule_table_is_described(shipped):
    t = zr.zone_rule_table(shipped)
    assert len(t) == len(shipped)
    for c in t.columns:
        assert zone_build.describe("zone_rule", c)
    for c in rw.WEIGHT_COLUMNS:
        assert zone_build.describe("rule_area_weights", c)
    assert (t[list(zr.RULE_FIELDS)] != "").all().all()


# --------------------------------------------------------------------------- worked trace


def test_trace_one_citation_to_the_state_output(shipped):
    """Ordinance 31, § 912.08.C.2 (offset 237803) -> rule pgh_ord31 -> 100 lots -> state share."""
    rule = next(r for r in shipped if r.rule_id == "pgh_ord31")
    assert "§ 912.08" in rule.meta["section_cite"]
    assert rule.meta["evidence_offset"] == "237803"
    assert rule.valid_from == date(2026, 10, 29)
    pieces = pd.DataFrame(
        {"block": ["420030001001000"], "district": ["R1D-L"], "overlays": "", "share": [1.0]}
    )
    weights = pd.DataFrame(
        {
            "block": ["420030001001000"],
            "tract": ["42003000100"],
            "county": ["42003"],
            "jurisdiction": [PGH],
            "sf_lots": [100.0],
            "housing_units": [120.0],
            "land_m2": [5000.0],
        }
    )

    def state_known(rules):
        t = rw.zone_weights(
            pieces,
            weights,
            rules,
            jurisdiction=PGH,
            map_name="pgh_current",
            map_version="test",
            period="window",
            window=WINDOW,
            benches=BENCHES,
        )
        return t, rw.state_exposure(t, "adu", state_sf_lots=1000.0)

    table, state = state_known(shipped)
    adu = table[table["benchmark"] == "adu"]
    assert list(adu["rule_ids"]) == ["pgh_ord31"]
    assert list(adu["opportunity_class"]) == ["known_by_right"]
    assert state["known_by_right"] == pytest.approx(0.1)
    assert state["outside_sample"] == pytest.approx(0.9)
    _t, without = state_known([r for r in shipped if r.rule_id != "pgh_ord31"])
    assert without["known_by_right"] == 0.0
    assert without["unresolved"] == pytest.approx(0.1)  # unknown, never "no"
