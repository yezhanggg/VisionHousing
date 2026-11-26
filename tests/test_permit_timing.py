"""Philadelphia permit-stage timing: queries, Kaplan-Meier, the table (recorded CSV, offline)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from visionhousing import cli
from visionhousing.data.sources import phl_permit_timing as src
from visionhousing.data.sources.opendata import read_extract
from visionhousing.features import permit_timing as pt

FIX = Path(__file__).parent / "fixtures" / "local"


@pytest.fixture(scope="module")
def settings() -> src.TimingSettings:
    return src.load_settings()


@pytest.fixture(scope="module")
def frames() -> dict[str, pd.DataFrame]:
    return {
        "building": read_extract(FIX / "phl_timing_building.csv"),
        "zoning": read_extract(FIX / "phl_timing_zoning.csv"),
    }


def test_settings_reuse_pipeline_permit_types(settings):
    assert "Residential Building" in settings.building_types
    assert "New Construction" in settings.building_work_types
    assert settings.unit_groups["new_5_plus"] == (5, None)
    assert settings.quantiles == [0.10, 0.25, 0.50, 0.75, 0.90]


def test_queries_keep_no_names_and_quote_values(settings):
    b = src.building_query(settings)
    z = src.zoning_query(settings)
    for q in (b, z):
        for banned in ("address", "opa_owner", "contractorname", "SELECT *"):
            assert banned not in q
    assert "zoningpermitjobid" in b and "numberofunits" in b
    assert "'Residential Building'" in b and "~*" in b
    assert "building_issued" in z and "'Zoning'" in z
    assert src.query_hash(b) == src.query_hash(b) != src.query_hash(z)
    assert len(src.query_hash(b)) == 64


def test_kaplan_meier_by_hand():
    km = pt.kaplan_meier([2, 3, 3, 5, 8], [True, False, True, True, False])
    # day 2: 1 of 5 -> 0.8 waiting; day 3: 1 of 4 -> 0.6; day 5: 1 of 2 -> 0.3
    assert np.allclose(km.reached, [0.2, 0.4, 0.7])
    assert km.quantile(0.25) == 3 and km.quantile(0.5) == 5
    assert km.quantile(0.9) is None  # the curve never gets to 90%
    assert km.reached_by(1) == 0.0 and km.reached_by(4) == pytest.approx(0.4)
    assert km.reached_by(9) is None  # no case followed that long
    assert km.n == 5 and km.events == 3


def test_kaplan_meier_without_censoring_is_lower_quantile():
    d = np.array([10, 40, 20, 30, 50, 60, 70, 80, 90, 100])
    km = pt.kaplan_meier(d, np.ones_like(d, dtype=bool))
    assert km.quantile(0.5) == 50 and km.quantile(0.1) == 10 and km.quantile(0.9) == 90
    assert pt.kaplan_meier([], []).quantile(0.5) is None
    with pytest.raises(ValueError):
        pt.kaplan_meier([1, 2], [True])


def test_groups_and_issue_counts(frames, settings):
    b = pt.label_groups(frames["building"], settings)
    assert (b["group"] == "adu_like").sum() == 3  # ADU text wins over unit groups
    counts = pt.issue_counts(b, settings)
    assert counts["by_year"]["new_1"][2021] == {"permits": 5, "homes": 5}
    assert counts["by_year"]["new_5_plus"][2021] == {"permits": 1, "homes": 145}
    assert counts["by_year"]["new_2_4"][2019] == {"permits": 2, "homes": 4}
    assert sum(counts["by_month"]["new_1"][2021]) == 5
    assert counts["by_month"]["new_1"][2021][11] == 2  # December
    # 2026 is outside the table's full years
    assert 2026 not in counts["by_year"]["all_new_homes"]


def test_stage_lag_issued(frames, settings):
    b = pt.label_groups(frames["building"], settings)
    lag = pt.stage_lag_issued(b, settings)
    y2021 = lag["new_1"]["by_year"]["2021"]
    # lags: 292, 145, 24, 93, 85 days -> sorted 24, 85, 93, 145, 292
    assert y2021["n"] == 5 and y2021["p50"] == 93 and y2021["p10"] == 24
    assert y2021["linked_share"] == 1.0
    assert lag["new_1"]["by_year"]["2020"]["linked_share"] == 0.0
    assert lag["new_5_plus"]["by_year"]["2021"]["p50"] == 203


def test_stage_lag_cohort_censoring(frames, settings):
    out = pt.stage_lag_cohort(frames["zoning"], settings, date(2026, 11, 24))
    all_years = out["by_zoning_year"]["all"]
    # 14 linked new-structure permits plus 2 open ones (2022): 2 of 16 censored
    assert all_years["n"] == 16 and all_years["with_building_permit"] == 14
    assert all_years["censored_share"] == 0.125
    y2022 = out["by_zoning_year"]["2022"]
    assert y2022["n"] == 2 and y2022["with_building_permit"] == 0
    assert y2022["p50"] is None and y2022["censored_share"] == 1.0
    assert out["by_zoning_year"]["2025"]["reached_by_days"][1095] is None


def test_build_and_write_table(frames, settings, tmp_path):
    doc = pt.build_table(frames, settings)
    assert doc["id"] == "permit_lag_phl_2026_11"
    assert doc["provenance"]["measured_on"] == "2026-11-24"
    assert [s["id"] for s in doc["provenance"]["sources"]] == ["phl_li_permits"]
    assert doc["fields"]["application_date"].startswith("absent")
    path = pt.write_table(doc, tmp_path / "lag.yaml")
    text = path.read_text()
    assert text.startswith("# Calibration table") and "nan" not in text.lower().split()
    back = yaml.safe_load(text)
    assert back["stage_lag_issued"]["new_1"]["by_year"]["2021"]["p50"] == 93


def test_cli_permit_timing_offline(monkeypatch, tmp_path, frames):
    monkeypatch.setattr(src, "fetch_timing_extracts", lambda raw, s, refresh=False: frames)
    out = tmp_path / "permit_lag_phl.yaml"
    assert cli.main(["permit-timing", "--raw", str(tmp_path), "--out", str(out)]) == 0
    assert yaml.safe_load(out.read_text())["state"] == "PA"
