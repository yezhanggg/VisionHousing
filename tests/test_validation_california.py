"""Tests for the California APR study (visionhousing.validation).

The fixtures in tests/fixtures/apr/ are small synthetic extracts laid out like
the published APR Table A and Table A2 files and the ACS summary-file rows; the
values are invented so each rule can be checked by hand. No network is used.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from visionhousing.cli import build_parser
from visionhousing.validation import apr_data
from visionhousing.validation import california as ca

FIX = Path(__file__).resolve().parent / "fixtures" / "apr"


@pytest.fixture
def settings() -> apr_data.AprSettings:
    raw = copy.deepcopy(apr_data.load_settings().raw)
    raw["chunk_rows"] = 3  # several chunks even on the tiny fixture
    raw["min_projects"] = 1
    return apr_data.AprSettings(raw)


@pytest.fixture
def frames(settings):
    a = apr_data.read_table_a(FIX / "table_a.csv", settings)
    a2 = apr_data.read_table_a2(FIX / "table_a2.csv", settings)
    lots = apr_data.parse_place_lots(
        FIX / "acs_b25024.dat", FIX / "acs_geos.txt", settings["acs_lots_cell"]
    )
    return a, a2, lots


# --------------------------------------------------------------------------- reading


def test_table_a_keeps_only_needed_columns_and_valid_rows(frames):
    a, _, _ = frames
    assert list(a.columns) == [
        "juris",
        "year",
        "apn",
        "unit_cat",
        "app_date",
        "units",
        "streamlining",
        "status",
        "project_type",
    ]
    # 10 rows: one outside the study years and one with a bad date are dropped.
    assert len(a) == 8
    assert a.loc[0, "apn"] == "111111111"
    assert a["apn"].eq("").sum() == 1  # the row without a parcel number stays, blank key


def test_table_a2_dates_and_units(frames):
    _, a2, _ = frames
    assert len(a2) == 11
    row = a2[(a2["apn"] == "333333333")].iloc[0]
    assert row["ent_date"] == pd.Timestamp("2022-05-01")
    assert row["bp_units"] == 2
    assert "NOTES" not in a2.columns and "STREET_ADDRESS" not in a2.columns


def test_apn_key_drops_short_numbers():
    out = apr_data.apn_key(pd.Series(["123-45", "123-456-789", None]), 6)
    assert out.tolist() == ["", "123456789", ""]


def test_place_lots_names_and_levels(frames):
    _, _, lots = frames
    by = lots.set_index("juris")
    assert by.loc["CALIFORNIA", "lots"] == 4000
    assert by.loc["ALPHA", "lots"] == 1000 and bool(by.loc["ALPHA", "incorporated"])
    assert by.loc["BETA", "lots"] == 2000
    assert not bool(by.loc["GAMMA CDP", "incorporated"])


# --------------------------------------------------------------------------- groups


def test_groups_follow_the_stated_order(settings):
    unit = pd.Series(["ADU", "SFD", "5+", "5+", "5+", "2 to 4", "MH", "2 to 4"])
    flag = pd.Series(
        [
            "SB 9 (2021) - Duplex in SF Zone",  # an ADU stays an ADU
            "SB 9 (2021) - Residential Lot Split",
            "SB 35 (2017)",
            "No",
            "No",
            "No",
            "N",
            "SB 9 (2021) - Duplex in SF Zone",
        ]
    )
    ptype = pd.Series(["", "", "", "Ministerial", "Discretionary", "", "", ""])
    got = ca.assign_groups(unit, flag, ptype, settings["groups"]).tolist()
    assert got == [
        "adu",
        "sb9",
        "ministerial_5_plus",
        "ministerial_5_plus",
        "mf_5_plus_other",
        "mf_2_4",
        "single_family",
        "sb9",
    ]


# --------------------------------------------------------------------------- linking


def test_link_takes_first_stage_on_or_after_the_application(frames, settings):
    a, a2, _ = frames
    apps = ca.link_applications(a, a2, settings).set_index("apn")
    adu = apps.loc["111111111"]
    # The 2019 permit on the same lot is an earlier ADU and must not be used.
    assert adu["bp_date"] == pd.Timestamp("2020-04-09")
    assert adu["co_date"] == pd.Timestamp("2021-01-04")
    assert adu["group"] == "adu" and bool(adu["ever_linked"])
    mf = apps.loc["444444444"]
    assert mf["group"] == "ministerial_5_plus"
    assert mf["ent_date"] == pd.Timestamp("2021-09-01")
    assert mf["bp_date"] == pd.Timestamp("2023-03-01")
    pending = apps.loc["222222222"]
    assert pd.isna(pending["bp_date"]) and not bool(pending["ever_linked"])
    # The duplicate report of the same application was dropped once.
    assert int(apps["duplicates_dropped"].iloc[0]) == 1
    assert (apps.index == "222222222").sum() == 1


def test_a2_projects_collapse_rows(frames, settings):
    _, a2, _ = frames
    p = ca.a2_projects(a2, settings).set_index("key")
    mf = p.loc["ALPHA|444444444|5+"]
    assert mf["group"] == "ministerial_5_plus"  # streamlined in any row
    assert mf["ent_date"] == pd.Timestamp("2021-09-01")
    assert mf["bp_date"] == pd.Timestamp("2023-03-01")
    assert mf["units"] == 40


# --------------------------------------------------------------------------- Kaplan-Meier


def test_stage_summary_hand_example(settings):
    raw = copy.deepcopy(settings.raw)
    raw["censor_date"] = "2021-01-01"
    s = apr_data.AprSettings(raw)
    start = pd.Series(pd.to_datetime(["2020-01-01"] * 4))
    # Events at day 100 and day 200; two cases still waiting at day 366.
    end = pd.Series(pd.to_datetime(["2020-04-10", "2020-07-19", None, None]))
    out = ca.stage_summary(start, end, s)
    assert out["projects"] == 4 and out["reached"] == 2
    assert out["censored_share"] == 0.5
    assert out["days"]["p25"] == 100 and out["days"]["p50"] == 200
    assert out["days"]["p75"] is None  # the curve never reaches 75%
    assert out["reached_by_years"][1] == 0.5
    assert out["reached_by_years"][2] is None  # nobody followed two years


def test_stage_summary_drops_end_before_start_and_early_starts(settings):
    start = pd.Series(pd.to_datetime(["2020-06-01", "2015-01-01", "2020-01-01"]))
    end = pd.Series(pd.to_datetime(["2020-01-01", "2016-01-01", "2020-02-01"]))
    out = ca.stage_summary(start, end, settings)
    assert out["end_before_start_dropped"] == 1
    assert out["projects"] == 1  # the 2015 start is before the study's first year


def test_small_cells_are_blank(settings):
    raw = copy.deepcopy(settings.raw)
    raw["min_projects"] = 5
    s = apr_data.AprSettings(raw)
    start = pd.Series(pd.to_datetime(["2020-01-01"]))
    out = ca.stage_summary(start, pd.Series(pd.to_datetime(["2020-02-01"])), s)
    assert out["days"]["p50"] is None and out["reached_by_years"][1] is None


# --------------------------------------------------------------------------- ADU rates


def test_adu_rates_count_rule_and_denominator(frames, settings):
    _, a2, lots = frames
    rates = ca.adu_rates(a2, lots, settings).set_index(["juris", "year"])
    # ALPHA: 2019 one ADU, 2020 one ADU; the 2025 row's permit is dated 2024 -> not counted.
    assert rates.loc[("ALPHA", 2019), "adu_permits"] == 1
    assert rates.loc[("ALPHA", 2025), "adu_permits"] == 0
    assert rates.loc[("ALPHA", 2020), "per_1000_lots"] == pytest.approx(1.0)
    assert rates.loc[("BETA", 2019), "adu_permits"] == 2
    assert rates.loc[("CALIFORNIA", 2019), "adu_permits"] == 3
    assert rates.loc[("CALIFORNIA", 2019), "per_1000_lots"] == pytest.approx(0.75)


def _pre(rows):
    return pd.DataFrame(rows, columns=["juris", "year", "per_1000_lots", "lots"])


def test_selection_sees_only_pre_period(settings):
    with pytest.raises(ValueError, match="pre-period"):
        ca.select_jurisdictions(_pre([["SAN DIEGO", 2021, 1.0, 300000]]), settings)


def test_selection_peer_within_tolerance(settings):
    pre = _pre(
        [
            ["SAN DIEGO", 2018, 2.0, 250000],
            ["LOS ANGELES", 2018, 8.0, 560000],
            ["PEER A", 2018, 2.2, 150000],
            ["PEER B", 2018, 3.5, 150000],
            ["SMALL", 2018, 8.0, 5000],  # too few lots to be a candidate
            ["CALIFORNIA", 2018, 1.0, 9000000],
        ]
    )
    chosen, candidates = ca.select_jurisdictions(pre, settings)
    by = {c["name"]: c for c in chosen}
    assert by["SAN DIEGO"]["peer"] == "PEER A"
    assert by["LOS ANGELES"]["peer"] is None  # no candidate within 25% of 8.0
    assert by["PEER A"]["role"] == "peer" and by["PEER A"]["peer_of"] == "SAN DIEGO"
    assert "SMALL" not in candidates and "CALIFORNIA" not in candidates


# --------------------------------------------------------------------------- documents


def _fake_rates(settings) -> pd.DataFrame:
    rows = []
    for j, base, lots in (
        ("CALIFORNIA", 1.0, 9_000_000),
        ("SAN DIEGO", 2.0, 250_000),
        ("LOS ANGELES", 8.0, 560_000),
        ("SAN JOSE", 2.1, 180_000),
    ):
        for y in settings.years:
            rate = base * (1 if y < 2020 else 2)
            rows.append([j, y, round(rate * lots / 1000), lots, rate])
    return pd.DataFrame(rows, columns=["juris", "year", "adu_permits", "lots", "per_1000_lots"])


def test_comparison_records_have_every_field(frames, settings):
    a, a2, _ = frames
    rates = _fake_rates(settings)
    chosen, _ = ca.select_jurisdictions(rates[rates["year"].isin([2018, 2019])], settings)
    apps = ca.link_applications(a, a2, settings)
    tables = ca.stage_tables(
        apps, ca.a2_projects(a2, settings), [c["name"] for c in chosen], settings
    )
    records = ca.comparison_records(rates, chosen, tables, settings)
    fields = {"what_changed", "where", "date", "outcome", "denominator", "horizon"}
    for r in records:
        assert fields <= set(r)
        assert r["identification"] in {"descriptive", "matched", "causal"}
        assert r["caveats"]
    ids = {r["id"]: r for r in records}
    sd = ids["ca_apr.adu.san_diego_vs_san_jose"]
    assert sd["identification"] == "matched"
    # Focal doubles from 2 to 4, peer from 2.1 to 4.2: difference in change = -0.1.
    assert sd["outcome"]["difference_in_change"] == pytest.approx(-0.1)
    assert ids["ca_apr.adu.los_angeles_before_after"]["identification"] == "descriptive"
    assert ids["ca_apr.adu.statewide_2020"]["outcome"]["change"] == pytest.approx(1.0)


def test_donor_ranges_span_places(settings):
    cell = {
        "days": {"p50": 100, "p75": 200},
        "reached_by_years": {1: 0.5, 2: 0.6, 3: None, 4: None, 5: None},
    }
    other = {
        "days": {"p50": 300, "p75": None},
        "reached_by_years": {1: 0.3, 2: 0.7, 3: None, 4: None, 5: None},
    }
    stages = ("application_to_permit", "permit_to_completion", "application_to_entitlement")
    groups = [g["id"] for g in settings["groups"]]
    tables = {
        "CALIFORNIA": {g: dict.fromkeys(stages, cell) for g in groups},
        "X": {g: dict.fromkeys(stages, other) for g in groups},
    }
    out = ca.donor_ranges(tables, settings)
    r = out["adu"]["application_to_permit"]
    assert r["median_days"] == {"low": 100.0, "high": 300.0, "n": 2}
    assert r["p75_days"] == {"low": 200.0, "high": 200.0, "n": 1}
    assert r["reached_by_years"][1] == {"low": 0.3, "high": 0.5, "n": 2}
    assert r["reached_by_years"][3] is None


def test_pa_adu_rate_from_saved_run(tmp_path, settings):
    run = {
        "state": "PA",
        "seed": 0,
        "draws": 10,
        "hashes": {"code": "c"},
        "reform_results": {
            "adu": {
                "records": {
                    "eligible": {"name": "eligible lots", "p10": 900, "p50": 1000, "p90": 1100},
                    "built": {"name": "ADUs built", "p10": 5, "p50": 10, "p90": 20},
                }
            }
        },
    }
    (tmp_path / "run.json").write_text(json.dumps(run), encoding="utf-8")
    raw = copy.deepcopy(settings.raw)
    raw["plausibility"]["run_files"] = ["missing.json", "run.json"]
    out = ca.pa_adu_rate(apr_data.AprSettings(raw), root=tmp_path)
    assert out["run"] == "run.json"
    # 10 ADUs / 1,000 lots / 5 years x 1,000 = 2 per 1,000 lots per year.
    assert out["per_1000_eligible_lots_per_year"] == {"p10": 1.0, "p50": 2.0, "p90": 4.0}


def test_build_documents_and_write(tmp_path, frames, settings):
    a, a2, lots = frames
    raw = copy.deepcopy(settings.raw)
    raw["selection"]["min_lots"] = 500
    raw["selection"]["focal"] = [dict(raw["selection"]["focal"][0], name="ALPHA")]
    s = apr_data.AprSettings(raw)
    plaus = {
        "state": "PA",
        "per_1000_eligible_lots_per_year": {"p10": 0.2, "p50": 0.5, "p90": 1.0},
    }
    docs = ca.build_documents(a, a2, lots, s, as_of="2026-11-24", plausibility=plaus)
    ev = docs["evidence"]
    assert ev["id"] == "california_apr_2026_11"
    assert ev["selection"]["chosen"][0]["name"] == "ALPHA"
    assert ev["link_quality"]["applications"] == 7
    assert {r["id"] for r in ev["records"]} >= {"ca_apr.adu.statewide_2020"}
    assert docs["donor"]["status"] == "transferred"
    assert docs["plausibility"]["model"]["state"] == "PA"
    path = ca.write_yaml(ev, tmp_path / "ev.yaml", ca.HEADERS["evidence"])
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Evidence: California")
    back = yaml.safe_load(text)
    assert back["stage_tables"]["CALIFORNIA"]["adu"]["applications"] >= 1
    # No parcel numbers or addresses leave the module.
    assert "111111111" not in text and "Example St" not in text


def test_clean_turns_numpy_and_nan_into_plain_values():
    out = ca._clean({"a": np.int64(3), "b": float("nan"), np.int64(2019): np.float64(1.5)})
    assert out == {"a": 3, "b": None, 2019: 1.5}


def test_settings_dates(settings):
    assert settings.censor_date == date(2025, 12, 31)
    assert settings.years[0] == 2018


def test_command_is_registered():
    args = build_parser().parse_args(["validate-california", "--raw", "x"])
    assert args.command == "validate-california" and str(args.raw) == "x"
