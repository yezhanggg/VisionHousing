"""Law layer L1b: Vermont's dated § 4412 floor in missing middle, provision versions, Pittsburgh.

Also the provision-version pack table and the Pittsburgh registry link.

The funnel cases pin every input (the golden fourplex tract of test_scenario_missing_middle.py)
so the floor's effect can be worked out by hand: on lots with municipal water and sewer a
fourplex is already allowed (nothing new); without them only a duplex is, so a fourplex is new
but adds only its two extra homes where the duplex also pencils. "Freeze today's rules" is a
separate record. Everything is offline.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from visionhousing.data import pack
from visionhousing.data.registry import load_registry
from visionhousing.engine.law_floor import middle_floor_for
from visionhousing.features import brackets as br
from visionhousing.law import tables as law_tables
from visionhousing.law.rights import MiddleFloorSchedule, load_rights, middle_floor_schedule
from visionhousing.ledger import apply_overrides, load_ledger, load_scenarios, scenario_ledger
from visionhousing.scenarios.missing_middle import (
    MiddleFloor,
    missing_middle_funnel,
    scope_from_scenario,
)

YEARS = range(2027, 2032)
WATER = "missing_middle.floor_water_share_in_sewer_area"
NO_RECORD = "missing_middle.floor_served_share_no_sewer_record"

GOLDEN = {
    "adu.lot_proxy_adjustment": 0.9,
    "missing_middle.sf_only_zoned_share": 0.5,
    "missing_middle.fit_share": 0.4,
    "missing_middle.typology_weight_duplex": 0,
    "missing_middle.typology_weight_triplex": 0,
    "missing_middle.typology_weight_fourplex": 1,
    "missing_middle.typology_weight_fiveplex": 0,
    "missing_middle.typology_weight_sixplex": 0,
    "missing_middle.new_unit_rent_premium": 1.0,
    "missing_middle.rent_ratio_2br": 1.0,
    "missing_middle.acquisition_premium": 0.0,
    "missing_middle.unit_size_sqft": 1000,
    "missing_middle.hard_cost_per_sqft": 200,
    "missing_middle.soft_cost_share": 0.25,
    "missing_middle.developer_margin": 0.15,
    "missing_middle.cap_rate": 0.06,
    "missing_middle.operating_cost_share": 0.35,
    "parking.required_spaces_per_unit_attached": 1,
    "parking.cost_per_space_surface": 5000,
    "missing_middle.redevelopment_hazard_annual": 0.01,
    "missing_middle.sf_turnover_annual": 0.05,
    "missing_middle.ramp_first_year_share": 0.2,
    "missing_middle.ramp_years_to_full": 3,
    "missing_middle.displacement_share": 0.25,
    "missing_middle.baseline_replacement_probability": 0.1,
    "missing_middle.baseline_units_per_replacement": 1,
    "missing_middle.start_delay_years": 0,
    WATER: 1.0,
    NO_RECORD: 0.0,
}
REDEVELOPED = 1000 * 0.9 * 0.5 * 0.4 * 0.64 * 0.01 * 3.8  # 4.3776 lots (see the golden test)


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


@pytest.fixture(scope="module")
def scenarios(ledger):
    return load_scenarios(ledger)


@pytest.fixture(scope="module")
def vt():
    return middle_floor_schedule("VT", YEARS)


def tract(**changes):
    row = {
        "geoid": "50001000100",
        "county": "50001",
        "population": 2500,
        "units_1_detached": 1000,
        "units_1_attached": 0,
        "median_rent_b2": 3000,
        "median_gross_rent": 2800,
        "median_home_value": 150000,
        **{c: 0 for c in br.VALUE.columns},
        "value_125000_149999": 50,
        "value_150000_174999": 50,
    }
    row.update(changes)
    return pd.DataFrame([row])


def run(ledger, scenarios, floor=None, overrides=None, draws=3, rent=3000):
    scenario = scenarios["missing_middle"]
    led = apply_overrides(scenario_ledger(ledger, scenario), {**GOLDEN, **(overrides or {})})
    scope = scope_from_scenario("missing_middle", scenario)
    tracts = tract(median_rent_b2=rent)
    return missing_middle_funnel(tracts, led, None, scope, draws=draws, seed=3, floor=floor)


# --------------------------------------------------------------------------- the schedule


def test_vermont_schedule_reads_each_year_from_its_dated_version(vt):
    assert vt.versions == ("vt_4412_1d_act179", *["vt_4412_1d_act179_9a"] * 4)
    assert vt.dates[0] == date(2027, 7, 1) and vt.frozen_version == "vt_4412_1d_act179"
    # duplex right on every year-round lot; four units only with water AND sewer
    assert vt.current["served"] == (4,) * 5 and vt.current["unserved"] == (2,) * 5
    # unknown service stays unknown: no right and no denial
    assert vt.current["unknown"] == (None,) * 5 and vt.comparator("unknown") == (None,) * 5
    assert vt.newly_legal(3, "unknown") == (None,) * 5
    # the reform's single-family-only districts require no multiunit buildings, so the 2028
    # exception does not reach them: current law and "freeze today's rules" agree
    assert vt.frozen == vt.current and vt.history == {"served": 4, "unserved": 2, "unknown": None}
    assert (
        vt.newly_legal(5, "served") == (True,) * 5 and vt.newly_legal(4, "served") == (False,) * 5
    )
    assert middle_floor_schedule("PA", YEARS) is None


def test_the_2028_exception_never_counts_rights_the_history_already_holds():
    rights = load_rights()
    app = dict(rights["missing_middle_application"])
    app["district"] = {**app["district"], "required_min_units": 6}  # requires > 4 units
    sched = middle_floor_schedule("VT", YEARS, rights={**rights, "missing_middle_application": app})
    # current law: the four-unit right lapses from January 1, 2028; frozen keeps it
    assert sched.current["served"] == (4, 2, 2, 2, 2) and sched.frozen["served"] == (4,) * 5
    # ...but the permits since mid-2023 already reflect four units, so the comparator stays 4
    assert sched.comparator("served") == (4,) * 5 == sched.comparator("served", frozen=True)


# --------------------------------------------------------------------------- the funnel


def _floor(sched, sewer):
    return MiddleFloor(sched, np.array([sewer], dtype=float), ("vt_acts_2026",))


def test_served_lots_gain_nothing_from_a_fourplex(ledger, scenarios, vt):
    res = run(ledger, scenarios, _floor(vt, 1.0))
    assert res.stages["feasible"][0, 0] == pytest.approx(0.0)
    assert res.stages["authorized"][0, 0] == pytest.approx(0.0)
    assert res.records["floor_served_share"].p50 == pytest.approx(1.0)
    assert res.records["floor_newly_legal_share"].p50 == pytest.approx(0.0)


@pytest.mark.parametrize("rent", [3000, 4000])
def test_unserved_lots_count_only_the_homes_above_the_duplex(ledger, scenarios, vt, rent):
    plain = run(ledger, scenarios, rent=rent)
    res = run(ledger, scenarios, _floor(vt, 0.0), rent=rent)
    p2, p4 = res.feasible_share_by_type[2][0, 0], res.feasible_share_by_type[4][0, 0]
    both = min(p2, p4) / p4  # 0 at $3,000 (the duplex does not pencil), 1 at $4,000
    lots = res.stages["redeveloped"][0, 0]
    if rent == 3000:
        assert lots == pytest.approx(REDEVELOPED) and both == 0
    else:
        assert both == pytest.approx(1.0)
    assert res.stages["feasible"][0, 0] == pytest.approx(plain.stages["feasible"][0, 0])
    assert res.stages["units"][0, 0] == pytest.approx(lots * (4 - 2 * both))
    assert res.records["floor_existing_alternative"].p50 == pytest.approx(lots * 2 * both)
    # where the duplex also pencils the counterfactual is the duplex (no q x r rebuild on top);
    # q x r = 0.1 x 1 comes off only the lots where only the fourplex pencils
    expected = (lots * (4 - 2 * both) - 0.1 * lots * (1 - both)) * 0.75
    assert res.stages["authorized"][0, 0] == pytest.approx(expected)
    assert res.stages["authorized"][0, 0] <= plain.stages["authorized"][0, 0]


@pytest.mark.parametrize("q", [0.0, 1.0])
def test_floor_site_account_matches_the_plan_fixture(ledger, scenarios, vt, q):
    """Fourplex (4 units, 1 demolition) against the already-allowed duplex (2 and 1): 2 and 2.

    On unserved lots at $4,000 both designs pencil, so every redeveloped lot is an
    already-allowed-duplex lot: the counterfactual is the duplex and its demolition,
    whatever the baseline replacement chance q (no q x r rebuild is added on top).
    """
    over = {
        "missing_middle.baseline_replacement_probability": q,
        "missing_middle.displacement_share": 0.0,
        "missing_middle.demolished_per_project": 1.0,
    }
    res = run(ledger, scenarios, _floor(vt, 0.0), overrides=over, rent=4000)
    lots = res.stages["redeveloped"][0, 0]
    assert lots > 0
    assert res.stages["authorized"][0, 0] == pytest.approx(2 * lots)
    assert res.stages["net"][0, 0] == pytest.approx(2 * lots)
    assert res.removed[0, 0] == pytest.approx(lots)
    assert res.baseline_on_sites[0, 0] == pytest.approx(2 * lots)
    assert res.records["counterfactual_removed"].p50 == pytest.approx(lots)
    assert res.records["demolitions_caused"].p50 == pytest.approx(0.0)
    # without the floor (a single-unit counterfactual) the plan's other cases: 3 and 3 at
    # q = 1, 4 and 3 at q = 0
    plain = run(ledger, scenarios, overrides=over, rent=4000)
    lp = plain.stages["redeveloped"][0, 0]
    assert plain.stages["authorized"][0, 0] == pytest.approx((4 - q) * lp)
    assert plain.stages["net"][0, 0] == pytest.approx(3 * lp)


def test_unknown_service_is_a_range_from_the_two_shares(ledger, scenarios, vt):
    half = run(ledger, scenarios, _floor(vt, 0.5), overrides={WATER: 0.8, NO_RECORD: 0.1})
    served = 0.5 * 0.8 + 0.5 * 0.1
    unserved = run(ledger, scenarios, _floor(vt, 0.0))
    assert half.records["floor_served_share"].p50 == pytest.approx(served)
    assert half.stages["units"][0, 0] == pytest.approx(
        (1 - served) * unserved.stages["units"][0, 0]
    )
    assert set(half.records["feasible"].assumption_ids) >= {WATER, NO_RECORD}
    assert "vt_acts_2026" in half.records["feasible"].source_ids


def test_freeze_is_a_separate_record_and_zero_when_the_rules_agree(ledger, scenarios, vt):
    res = run(ledger, scenarios, _floor(vt, 0.0))
    freeze = res.records["floor_freeze_difference"]
    assert freeze.p10 == freeze.p50 == freeze.p90 == 0.0
    assert "not a forecast arm" in freeze.name and "Freeze today's rules" in freeze.name


def test_freeze_difference_when_the_frozen_rules_allow_less():
    sched = MiddleFloorSchedule(
        state="VT", provision="p", years=tuple(YEARS),
        dates=tuple(date(y, 7, 1) for y in YEARS), versions=("a",) * 5,
        freeze_on=date(2026, 11, 25), frozen_version="b",
        current={"served": (4,) * 5, "unserved": (4,) * 5, "unknown": (None,) * 5},
        frozen={"served": (2,) * 5, "unserved": (2,) * 5, "unknown": (None,) * 5},
        history={"served": 1, "unserved": 1, "unknown": None}, history_version="h",
        history_since=date(2023, 7, 1),
        service_params={"water_share_in_sewer_area": WATER,
                        "served_share_no_sewer_record": NO_RECORD},
        why="test",
    )  # fmt: skip
    ledger, scenarios = load_ledger(), load_scenarios(load_ledger())
    res = run(ledger, scenarios, _floor(sched, 0.0))
    # current law already allows the fourplex: nothing added; frozen at two units it would add
    assert res.stages["authorized"][0, 0] == pytest.approx(0.0)
    assert res.records["floor_freeze_difference"].p50 > 0


def test_the_floor_needs_the_mix_method(ledger, scenarios, vt):
    scenario = scenarios["missing_middle"]
    led = apply_overrides(scenario_ledger(ledger, scenario), GOLDEN)
    with pytest.raises(ValueError, match="mix method"):
        missing_middle_funnel(
            tract(), led, None, scope_from_scenario("missing_middle", scenario),
            draws=2, method="prototypes", floor=_floor(vt, 0.0),
        )  # fmt: skip


def test_engine_hook_reads_the_sewer_share_and_names_its_sources(ledger):
    tracts = pd.DataFrame({"geoid": ["50001000100", "50001000200"]})
    infra = pd.DataFrame(
        {"geoid": ["50001000100"], "sewer_share": [0.6],
         "sewer_source": ["vt_accd_wastewater_service_areas"]}
    )  # fmt: skip
    state = SimpleNamespace(
        state="VT", tracts=tracts, derived={("pack_table", "infra_tract"): infra}
    )
    floor, notes = middle_floor_for(state, ledger)
    assert floor is not None and list(floor.sewer_share) == [0.6, 0.0]
    assert set(floor.source_ids) == {"vt_accd_wastewater_service_areas", "vt_acts_2026"}
    assert any("unknown" in n for n in notes)
    none, other = middle_floor_for(state, ledger, method="prototypes")
    assert none is None and "not applied" in other[0]
    pa = SimpleNamespace(state="PA", tracts=tracts, derived={})
    assert middle_floor_for(pa, ledger) == (None, ())


# --------------------------------------------------------------------------- provision versions


def test_provision_version_table_for_each_state():
    pa = law_tables.provision_version_table("PA")
    vt = law_tables.provision_version_table("VT")
    assert {"pgh_ord_31_2026_enacted", "pa_hb2186_pending"} <= set(pa["record_id"])
    assert set(pa["jurisdiction"].str[:2]) <= {"PA", "42"}
    assert {"vt_4412_1d_act179", "vt_4412_1d_act179_9a"} <= set(vt["record_id"])
    assert list(vt.columns) == list(law_tables.VERSION_COLUMNS)
    for code in ("MA", "WY"):
        empty = law_tables.provision_version_table(code)
        assert empty.empty and list(empty.columns) == list(law_tables.VERSION_COLUMNS)


def test_add_provision_versions_writes_the_table_dictionary_and_manifest(tmp_path):
    folder = tmp_path / "VT"
    folder.mkdir()
    (folder / "pack_manifest.json").write_text(json.dumps({"files": [], "inputs": []}))
    table = law_tables.add_provision_versions(folder, tmp_path / "raw")
    assert len(table) >= 2
    back = pack.load_pack(folder)["law_provision_version"]
    assert set(back["record_id"]) == set(table["record_id"])
    names = {
        c["name"]
        for c in json.loads((folder / "data_dictionary.json").read_text())["columns"]
        if c["table"] == "law_provision_version"
    }
    assert names == set(law_tables.VERSION_COLUMNS)
    files = json.loads((folder / "pack_manifest.json").read_text())["files"]
    assert "law_provision_version.csv.gz" in {f["path"] for f in files}


def test_the_full_build_runs_the_law_version_step():
    steps = {name: tables for name, tables, _ in pack.RETROFITS}
    assert steps["law provision versions"] == ("law_provision_version",)
    for spec in law_tables.VERSION_TABLE.values():
        assert set(spec["sources"]) <= set(load_registry())


def test_pittsburgh_registry_link_is_the_working_web_record():
    src = load_registry()["pgh_legistar"]
    assert "GUID=C15553FF-85B6-480E-ACFF-53F40EF99EA9" in src.url_pattern
    assert "ID=7147499" in src.url_pattern and "31504" not in src.url_pattern
    assert "legistar1.com" not in src.url_pattern and "effective October 29, 2026" in src.vintage
    assert src.checked_on == date(2026, 11, 25)
