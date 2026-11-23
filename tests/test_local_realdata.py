"""Real-data checks for the local sources: DCED Munstats, Philadelphia Carto, WPRDC, PHFA.

These call the live services with small queries (totals only) and write to a
temporary cache. Not part of ``make test``; run with ``make test-realdata``.
"""

from __future__ import annotations

import pytest
import yaml

from visionhousing.data.http import HttpClient
from visionhousing.data.local import PIPELINE_RULES
from visionhousing.data.sources import alle_assess, munstats, opendata, phfa
from visionhousing.features import zoning_status

pytestmark = pytest.mark.realdata


def test_munstats_crosswalk_match_rate(tmp_path):
    dced = munstats.fetch_munstats(tmp_path, manifest=None)
    assert len(dced) > 2500
    pop = munstats.fetch_subdivision_population("PA", tmp_path, manifest=None)
    rules = zoning_status.load_rules()
    cw = munstats.crosswalk(dced, munstats.census_subdivisions(pop), rules["name_overrides"])
    assert cw.match_rate >= 0.99, f"unmatched: {list(cw.unmatched_dced['name'])}"
    assert len(cw.unmatched_dced) <= 10
    regimes = cw.table.apply(munstats.zoning_regime, axis=1)
    assert {"own", "county", "none"} <= set(regimes)


def test_philadelphia_carto_answers_totals():
    out = opendata.carto_sql(
        "SELECT category_code, count(*) AS n FROM opa_properties_public GROUP BY 1",
        HttpClient(timeout=120),
    )
    total = out["n"].astype(int).sum()
    assert 500_000 < total < 700_000


def test_wprdc_join_by_tract():
    q = alle_assess.single_family_query(
        ["SINGLE FAMILY"], {"pre_1940": [0, 1939]}, ["CORPORATION"], "PA", by_tract=True
    )
    out = opendata.ckan_sql(q, HttpClient(timeout=120))
    assert 380 <= len(out) <= 420
    # A handful of border parcels sit in a neighbouring county's tract.
    assert (~out["tract"].str.startswith("42003")).sum() <= 10


def test_phfa_award_list_parses():
    rules = yaml.safe_load(PIPELINE_RULES.read_text(encoding="utf-8"))["phfa"]
    client = HttpClient(timeout=120)
    page = client.get_bytes(rules["index_url"]).decode("utf-8", "replace")
    links = phfa.award_links(page, rules["link_pattern"])
    assert 2024 in links
    table = phfa.parse_awards(phfa.pdf_text(client.get_bytes(links[2024])), 2024)
    assert len(table) >= 20
    assert table["county"].str.match(r"^42\d{3}$").all()
    assert table["units"].between(1, 1000).all()
