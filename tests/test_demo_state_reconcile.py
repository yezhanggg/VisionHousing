"""The demo state: every account reconciles in every draw, and the hard cases stay visible.

The demo state (``tests/fixtures/demo_state``, state code ``DM``, made by
``scripts/make_demo_fixture.py``) is a tiny synthetic state with two overlapping transit
station areas, four existing legal units near transit, a sewer service area with unknown
connection capacity, one appeal, a no-change parking minimum and one unobserved permit
stage, under a frozen law snapshot. With a fixed seed, in **every draw**:

- the state equals the sum of its counties, and the counties the sum of their tracts;
- the districts of each chamber add up to the state, for the baseline, each reform and
  the package;
- the five yearly totals add up to the five-year total (state and county);
- the package equals the reforms alone, less the overlap, plus the interactions.

The unknown service area is reported as unknown (never zero) in the API and the export,
and applying the dated test law change shows the original rule beside the revised result.
Every number checked comes from the engine; the test only adds and compares.
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import make_demo_fixture as demo
from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.districts.aggregate import DistrictWeights, district_draws
from visionhousing.districts.results import load_tables
from visionhousing.engine.context import load_context
from visionhousing.engine.core import run
from visionhousing.export_static import export_static, read_saved
from visionhousing.features.transit import TransitRule, tract_transit_features
from visionhousing.law.timeline import eligibility_status, load_timeline
from visionhousing.sensitivity.switch_runs import switch_payload

FIX = Path(__file__).resolve().parent / "fixtures" / "demo_state"
STATE = demo.STATE_CODE
REFORMS = ["adu", "missing_middle", "tod", "parking", "fees"]
SEEDS = [0, 11]
DRAWS = 2000
ATOL = 1e-6
API = "/api/v1"
RIDGE, VALLEY = demo.STATE_FIPS + "001", demo.STATE_FIPS + "003"
UNKNOWN_TRACT = f"{VALLEY}{(demo.UNKNOWN_TRACT[1] + 1) * 100:06d}"
EXISTING_TRACT = f"{RIDGE}{(demo.EXISTING_TRACT[1] + 1) * 100:06d}"


def _pack(root: Path, revised: bool) -> Path:
    """A packs folder holding the demo state as ``DM`` (original or with the law change)."""
    folder = root / STATE
    if revised:
        demo.apply_law_change(FIX, folder)
    else:
        shutil.copytree(FIX, folder)
    return root


@pytest.fixture(scope="module")
def packs(tmp_path_factory):
    return {
        "original": _pack(tmp_path_factory.mktemp("packs-original"), revised=False),
        "revised": _pack(tmp_path_factory.mktemp("packs-revised"), revised=True),
    }


@pytest.fixture(scope="module")
def contexts(packs):
    return {k: load_context(STATE, folder=p / STATE) for k, p in packs.items()}


@pytest.fixture(scope="module")
def results(contexts):
    return {
        (k, seed): run(STATE, REFORMS, seed=seed, draws=DRAWS, context=ctx, use_cache=False)
        for k, ctx in contexts.items()
        for seed in SEEDS
    }


@pytest.fixture(scope="module")
def clients(packs):
    return {k: AsgiClient(create_app(packs_dir=p)) for k, p in packs.items()}


CASES = [(k, s) for k in ("original", "revised") for s in SEEDS]


def close(a, b) -> bool:
    return bool(np.allclose(np.asarray(a, float), np.asarray(b, float), atol=ATOL, rtol=0))


# ------------------------------------------------------------------ the fixture itself


def test_regenerates_byte_identical(tmp_path):
    demo.main(["--out", str(tmp_path)])
    made = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    kept = sorted(str(p.relative_to(FIX)) for p in FIX.rglob("*") if p.is_file())
    assert made == kept
    for rel in made:
        assert (tmp_path / rel).read_bytes() == (FIX / rel).read_bytes(), rel


def test_fixture_is_small_synthetic_and_separate_from_testland():
    assert sum(p.stat().st_size for p in FIX.rglob("*") if p.is_file()) < 120 * 1024
    doc = json.loads((FIX / "data_dictionary.json").read_text(encoding="utf-8"))
    assert doc["synthetic"] is True and doc["state"] == STATE
    assert {e["source"] for e in doc["columns"]} == {"synthetic"}
    assert demo.STATE_FIPS != "99" and demo.SEED != __import__("make_testland").SEED
    tracts = pd.read_csv(FIX / "tracts.csv", dtype={"geoid": str, "county": str})
    assert set(tracts["county"]) == {RIDGE, VALLEY} and len(tracts) == 6
    described = {(e["table"], e["name"]) for e in doc["columns"]}
    for name in ("tracts", "blocks", "infra_tract", "muni_zoning_rules", "permit_stages"):
        with (FIX / f"{name}.csv").open(encoding="utf-8") as f:
            header = next(csv.reader(f))
        assert {(name, c) for c in header} <= described, name


def test_homes_and_residents_add_up():
    blocks = pd.read_csv(FIX / "blocks.csv", dtype={"tract": str})
    tracts = pd.read_csv(FIX / "tracts.csv", dtype={"geoid": str, "county": str})
    counties = pd.read_csv(FIX / "counties.csv", dtype={"geoid": str})
    by_tract = blocks.groupby("tract")["housing_units"].sum()
    assert (by_tract.reindex(tracts["geoid"]).to_numpy() == tracts["housing_units"]).all()
    by_county = tracts.groupby("county")[["housing_units", "population"]].sum()
    c = counties.set_index("geoid")
    assert (by_county["housing_units"] == c["housing_units"]).all()
    munis = pd.read_csv(FIX / "municipal_planning.csv", dtype={"county": str})
    assert munis.groupby("county")["population"].sum().equals(by_county["population"])


# ------------------------------------------------------------------ reconciliation


@pytest.mark.parametrize("case", CASES)
def test_state_counties_and_tracts_reconcile_in_every_draw(results, case):
    r = results[case]
    counties = list(r.county_ids)
    assert close(r.baseline_county_draws.sum(axis=0), r.baseline_draws)
    for key, out in r.reforms.items():
        assert close(out.county_draws.sum(axis=0), out.state_draws), key
        res = out.result
        if res.level == "tract":
            by_county = np.zeros_like(out.county_draws)
            for i, g in enumerate(res.geoids):
                by_county[counties.index(str(g)[:5])] += res.added[i]
            assert close(by_county, out.county_draws), key
    pkg = r.package
    assert close(pkg.county_draws.sum(axis=0), pkg.state_draws)
    county_level = sum(r.reforms[k].county_draws for k in pkg.county_reforms)
    assert close(pkg.tract_county_draws + county_level, pkg.county_draws)
    assert close(pkg.tract_draws.sum(axis=0), pkg.tract_county_draws.sum(axis=0))


def _district_series(r, tables, chamber):
    info = tables.districts[tables.districts["chamber"] == chamber]
    ids = tuple(info["district"].astype(str))
    tw = DistrictWeights.from_table(tables.tract_weights, chamber, "tract", ids)
    cw = DistrictWeights.from_table(tables.county_weights, chamber, "county", ids)
    counties = tuple(r.county_ids)
    series = {"baseline": (district_draws(r.baseline_county_draws, counties, cw), r.baseline_draws)}
    for key, out in r.reforms.items():
        res = out.result
        if res.level == "tract":
            d = district_draws(res.added, res.geoids, tw)
        else:
            d = district_draws(out.county_draws, counties, cw)
        series[key] = (d, out.state_draws)
    pkg = r.package
    d = district_draws(pkg.tract_draws, pkg.tract_geoids, tw) + district_draws(
        pkg.county_draws - pkg.tract_county_draws, counties, cw
    )
    series["package"] = (d, pkg.state_draws)
    return ids, series


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("chamber", ["upper", "lower"])
def test_districts_add_up_to_the_state_in_each_chamber(results, packs, case, chamber):
    r = results[case]
    tables = load_tables(packs[case[0]] / STATE)
    ids, series = _district_series(r, tables, chamber)
    assert len(ids) == (2 if chamber == "upper" else 3)
    for name, (d, state) in series.items():
        assert d.shape == (len(ids), DRAWS)
        assert close(d.sum(axis=0), state), (chamber, name)
        if name != "baseline":
            assert (d >= -ATOL).all() or (state < 0).any(), (chamber, name)


@pytest.mark.parametrize("case", CASES)
def test_five_year_totals_equal_the_annual_totals(results, case):
    r = results[case]
    pairs = {"baseline": (r.baseline_annual, r.baseline_draws, r.baseline_county_draws)}
    for key, out in r.reforms.items():
        pairs[key] = (out.annual, out.state_draws, out.county_draws)
    pairs["package"] = (r.package_annual, r.package.state_draws, r.package.county_draws)
    pairs["total"] = (
        r.total_annual,
        r.baseline_draws + r.package.state_draws,
        r.baseline_county_draws + r.package.county_draws,
    )
    for name, (annual, state, county) in pairs.items():
        assert annual.state_draws.shape == (DRAWS, 5), name
        assert close(annual.state_draws.sum(axis=1), state), name
        assert close(annual.county_draws.sum(axis=2), county), name
        assert close(annual.county_draws.sum(axis=0), annual.state_draws), name


@pytest.mark.parametrize("case", CASES)
def test_package_equals_parts_less_overlap_plus_interactions(results, case):
    r = results[case]
    pkg = r.package
    parts = sum(r.reforms[k].state_draws for k in pkg.reforms)
    assert close(pkg.parts_draws, parts)
    assert close(pkg.state_draws, pkg.parts_draws - pkg.overlap_draws + pkg.interaction_draws)
    assert (pkg.overlap_draws >= -ATOL).all() and (pkg.interaction_draws >= -ATOL).all()
    assert (pkg.overlap_draws <= r.reforms["missing_middle"].state_draws + ATOL).all()
    assert [w.name for w in pkg.waterfall][:1] and len(pkg.waterfall) == 4
    assert set(pkg.interactions) == {
        f"{a} -> {b}" for a in ("fees", "parking") for b in ("adu", "missing_middle")
    }


@pytest.mark.parametrize("case", CASES)
def test_each_named_interaction_reconciles_in_every_draw(results, case):
    """Interaction by interaction, in every draw.

    Each name has its own draws, its record summarises exactly those draws, and the
    named draws add up to the interaction total.
    """
    pkg = results[case].package
    by_name = pkg.interaction_draws_by_name
    assert set(by_name) == set(pkg.interactions)
    total = np.zeros(DRAWS)
    for name, draws in by_name.items():
        assert draws.shape == (DRAWS,), name
        assert (draws >= -ATOL).all(), name
        rec = pkg.interactions[name]
        assert close([rec.p10, rec.p50, rec.p90], np.percentile(draws, [10, 50, 90])), name
        total = total + draws
    assert close(total, pkg.interaction_draws)
    assert close(pkg.state_draws, pkg.parts_draws - pkg.overlap_draws + total)


def test_same_seed_same_draws_and_frozen_law_snapshot(contexts, results):
    again = run(STATE, REFORMS, seed=0, draws=DRAWS, context=contexts["original"], use_cache=False)
    first = results[("original", 0)]
    assert np.array_equal(again.package.state_draws, first.package.state_draws)
    assert again.hashes.data == first.hashes.data
    assert results[("revised", 0)].hashes.data != first.hashes.data  # the law file changed
    policy = first.contract["vintages"]["policy"]
    assert policy["law_snapshot_date"] == demo.SNAPSHOT_READ_ON
    revised = results[("revised", 0)].contract["vintages"]["policy"]
    assert revised["law_snapshot_date"] == demo.LAW_CHANGE["reviewed_on"]
    rules = pd.read_csv(FIX / "muni_zoning_rules.csv", dtype=str)
    assert set(rules["reviewed_on"]) == {demo.SNAPSHOT_READ_ON}


# ------------------------------------------------------------------ the hard cases


def test_overlapping_station_areas_are_counted_once(contexts):
    ctx = contexts["original"]
    rule = TransitRule.from_ledger(ctx.ledger)
    rail = ctx.stops[ctx.stops["mode"] == "rail"]
    assert len(rail) == 2

    def land(stops):
        f = tract_transit_features(ctx.blocks, stops, rule, tracts=ctx.tracts["geoid"])
        return f.set_index("tract")["transit_land_m2_0_50mi"]

    both, a, b = land(rail), land(rail.iloc[[0]]), land(rail.iloc[[1]])
    assert both.sum() < a.sum() + b.sum()  # the overlap is not counted twice
    assert (both >= np.maximum(a, b)).all()
    area = ctx.blocks.groupby("tract")["land_area_m2"].sum()
    assert (both <= area.reindex(both.index).fillna(0) + 1).all()


def test_four_existing_legal_units_cap_the_transit_gain(results):
    for case in CASES:
        out = results[case].reforms["tod"]
        assert out.records["existing_3a_capacity"].p50 == pytest.approx(demo.EXISTING_UNITS)
        assert out.records["additional_3a_capacity"].p10 >= 0
        detail = out.result.detail
        i = list(detail.geoids).index(EXISTING_TRACT)
        assert detail.existing.table_units[i] == pytest.approx(demo.EXISTING_UNITS)
        assert detail.existing.table_units.sum() == pytest.approx(demo.EXISTING_UNITS)
        assert (detail.capacity_by_right >= 0).all()
        assert (out.result.added >= -ATOL).all()


def test_unknown_service_area_is_unknown_not_zero():
    infra = pd.read_csv(FIX / "infra_tract.csv", dtype={"geoid": str}).set_index("geoid")
    row = infra.loc[UNKNOWN_TRACT]
    assert row["sewer_status"] == "unknown" and row["sewer_known"] == 0
    assert pd.isna(row["sewer_share"])  # blank, not zero
    assert (infra.drop(UNKNOWN_TRACT)["sewer_status"] == "verified").all()


def _facts(profile: dict) -> dict:
    sec = next(s for s in profile["sections"] if s["key"] == "infrastructure")
    return {f["id"].split(".", 1)[1]: f for f in sec["facts"]}


def test_unknown_service_area_stays_visible_in_the_api(clients):
    tract = clients["original"].get(f"{API}/profile/{STATE}/{UNKNOWN_TRACT}")
    assert tract.status == 200
    facts = _facts(tract.json())
    assert facts["sewer_unknown"]["value"] == pytest.approx(1.0)
    assert facts["sewer_status"]["text"] == "unknown"
    assert "sewer_verified" not in facts  # no 0% "verified" is shown where no record speaks
    county = _facts(clients["original"].get(f"{API}/profile/{STATE}/{VALLEY}").json())
    assert 0 < county["sewer_unknown"]["value"] < 1


def test_profiles_and_state_list_use_the_demo_state_name(clients):
    """The demo state is named as itself, never as Testland."""
    profile = clients["original"].get(f"{API}/profile/{STATE}/{VALLEY}").json()
    vintages = {f["vintage"] for sec in profile["sections"] for f in sec["facts"]}
    assert vintages == {"synthetic test data (Demo state)"}
    states = clients["original"].get(f"{API}/states").json()["states"]
    entry = next(s for s in states if s["code"] == STATE)
    assert entry["name"] == "Demo state (synthetic)" and entry["synthetic"] is True


def test_no_change_parking_minimum_adds_nothing_in_every_draw(results, clients):
    for case in CASES:
        r = results[case]
        i = list(r.county_ids).index(VALLEY)
        assert np.all(r.reforms["parking"].county_draws[i] == 0), case
    changes = clients["original"].get(f"{API}/states/{STATE}/law-changes?r=parking").json()
    valley = next(c for c in changes["changes"]["counties"] if c["fips"] == VALLEY)
    assert valley["answer"] == "already_allowed"


def test_appeal_keeps_the_revised_rule_in_force_from_its_date():
    records = load_timeline(FIX / "law_timeline.yaml")
    geoid = demo.LAW_CHANGE["geoid"]
    before = eligibility_status(
        records, state=STATE, housing_type="parking", on=date(2026, 8, 31), geoid=geoid
    )
    after = eligibility_status(
        records, state=STATE, housing_type="parking", on=date(2026, 9, 1), geoid=geoid
    )
    assert before.record_id == "dm_ridge_parking_original" and before.in_force
    assert after.record_id == "dm_ridge_parking_revised" and after.in_force
    assert after.court_status == "challenged" and after.status != "stayed"


def test_unobserved_permit_stage_is_blank_and_moves_no_permit(results):
    stages = pd.read_csv(FIX / "permit_stages.csv", keep_default_na=False)
    unobserved = stages[stages["observed"] == 0]
    assert list(unobserved["stage"]) == ["application"]
    assert (unobserved["date_column"] == "").all()
    for key, out in results[("original", 0)].reforms.items():
        timing = out.annual.timing
        assert timing["mechanism"] is False, key
        shifted = timing["shifted_into_window"]
        assert (shifted.p10, shifted.p50, shifted.p90) == (0, 0, 0), key


# ------------------------------------------------------------------ the dated law change


def test_law_change_shows_the_original_rule_and_the_revised_result(results, clients):
    def ridge_city(which):
        body = clients[which].get(f"{API}/states/{STATE}/law-changes?r=parking").json()
        county = next(c for c in body["changes"]["counties"] if c["fips"] == RIDGE)
        muni = next(m for m in county["municipalities"] if m["geoid"] == demo.LAW_CHANGE["geoid"])
        return county, muni

    county_a, muni_a = ridge_city("original")
    county_b, muni_b = ridge_city("revised")
    assert "2.0" in muni_a["rule_text"] and muni_a["answer"] == "changed_condition"
    assert muni_a["dates"]["effective_date"] == demo.RULES["00100"]["effective_date"]
    assert "single-family home: 0" in muni_b["rule_text"] and muni_b["answer"] == "already_allowed"
    assert muni_b["dates"]["effective_date"] == demo.LAW_CHANGE["effective_date"]
    assert muni_b["in_force"] is True
    a, b = results[("original", 0)], results[("revised", 0)]
    i = list(a.county_ids).index(RIDGE)
    pa, pb = a.reforms["parking"].county_draws[i], b.reforms["parking"].county_draws[i]
    assert (pb <= pa + ATOL).all() and pb.sum() < pa.sum()  # the revised result is smaller
    assert np.array_equal(a.baseline_draws, b.baseline_draws)  # the baseline does not move
    assert county_a["units"]["shown"] != county_b["units"]["shown"]


def test_apply_law_change_touches_only_the_changed_row(tmp_path):
    out = demo.apply_law_change(FIX, tmp_path / STATE)
    old = pd.read_csv(FIX / "muni_zoning_rules.csv", dtype=str, keep_default_na=False)
    new = pd.read_csv(out / "muni_zoning_rules.csv", dtype=str, keep_default_na=False)
    changed = (old != new).any(axis=1)
    assert list(new.loc[changed, "geoid"]) == [demo.LAW_CHANGE["geoid"]]
    for name in ("tracts.csv", "infra_tract.csv", "law_timeline.yaml"):
        assert (out / name).read_bytes() == (FIX / name).read_bytes()


# ------------------------------------------------------------------ the static export


EXPORT_DRAWS = 200


@pytest.fixture(scope="module")
def export_run(packs, tmp_path_factory):
    """The static export of the demo state from a packs folder outside ``data/packs``.

    The switch ranges are worked out first (into a temporary cache) from the context read
    from that folder, so the export can save them; the comparisons parts are exported too.
    """
    out = tmp_path_factory.mktemp("site") / "data"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("VISIONHOUSING_COMPARISONS_DIR", str(tmp_path_factory.mktemp("cache")))
        ctx = load_context(STATE, folder=packs["original"] / STATE)
        for key in REFORMS:
            assert switch_payload(STATE, key, draws=EXPORT_DRAWS, context=ctx) is not None, key
        report = export_static(
            STATE, out, packs_dir=packs["original"], draws=EXPORT_DRAWS, law_changes=True
        )
    return out, report


@pytest.fixture(scope="module")
def exported(export_run):
    return export_run[0]


def test_export_finds_the_pack_by_its_folder_not_its_code(export_run):
    """Ablation and switch ranges read the pack from the folder the state was loaded from."""
    out, report = export_run
    notes = " | ".join(report.notes)
    assert "not a known state code" not in notes
    assert "ablation.json not saved" not in notes
    assert (out / STATE / "comparisons" / "ablation.json").is_file()
    for key in REFORMS:
        assert (out / STATE / "switch-ranges" / f"{key}.json").is_file(), key
        assert f"switch ranges for {key} not saved" not in notes


def test_export_names_the_demo_state(exported):
    states = json.loads((exported / "states.json").read_text(encoding="utf-8"))["states"]
    entry = next(s for s in states if s["code"] == STATE)
    assert entry["name"] == "Demo state (synthetic)" and entry["synthetic"] is True


def test_export_keeps_the_unknown_service_area(exported):
    facts = _facts(read_saved(exported / STATE / "profiles" / f"{UNKNOWN_TRACT}.json"))
    assert facts["sewer_unknown"]["value"] == pytest.approx(1.0)
    assert facts["sewer_status"]["text"] == "unknown"
    assert "sewer_verified" not in facts


def test_export_keeps_the_original_rule(exported):
    body = read_saved(exported / STATE / "law_changes" / "parking.json")
    county = next(c for c in body["changes"]["counties"] if c["fips"] == RIDGE)
    muni = next(m for m in county["municipalities"] if m["geoid"] == demo.LAW_CHANGE["geoid"])
    assert "2.0" in muni["rule_text"]
