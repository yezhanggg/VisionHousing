"""Consistency: the interface returns the engine's numbers unchanged.

For the same inputs, ``POST /run`` equals ``engine.run(...).to_dict()``, and the
compare, county, export and summary endpoints carry the very same records.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

from tests.asgi_client import AsgiClient
from visionhousing import engine
from visionhousing.api import create_app

API = "/api/v1"
INPUTS = {
    "state": "TL",
    "reforms": ["adu", "fees", "parking"],
    "overrides": {"fees.passthrough_k": "h"},
    "seed": 7,
    "draws": 400,
    "preset": "low",
}
QUERY = "s=TL&r=adu,fees,parking&a=fees.passthrough_k~h&seed=7&draws=400&preset=low"


def _engine(**kw):
    return engine.run(
        kw.get("state", "TL"),
        kw.get("reforms"),
        kw.get("overrides"),
        seed=kw.get("seed", 0),
        draws=kw.get("draws", 2000),
        cycle=kw.get("cycle", "typical"),
    )


def _plain(data):
    """The JSON text form, as a browser would read it."""
    return json.loads(json.dumps(data, allow_nan=False))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return AsgiClient(create_app(packs_dir=tmp_path_factory.mktemp("packs")))


@pytest.fixture(scope="module")
def expected():
    # "h" is the link code for "high"; preset "low" selects the weak housing cycle.
    return _engine(
        reforms=INPUTS["reforms"],
        overrides={"fees.passthrough_k": "high"},
        seed=7,
        draws=400,
        cycle="weak",
    )


def test_run_equals_engine(client, expected):
    r = client.post(f"{API}/run", INPUTS)
    assert r.status == 200
    got = r.json()
    got.pop("explanation")
    got.pop("explanation_status")
    assert got == _plain(expected.to_dict())


def test_run_equals_engine_baseline_only(client):
    r = client.post(f"{API}/run", {"state": "TL", "draws": 250, "seed": 2})
    got = r.json()
    for k in ("explanation", "explanation_status"):
        got.pop(k)
    assert got == _plain(_engine(draws=250, seed=2).to_dict())


def test_export_json_equals_engine(client, expected):
    url = f"{API}/export/json?{QUERY}"
    got = client.get(url).json()
    got.pop("explanation")
    got.pop("explanation_status")
    assert got == _plain(expected.to_dict())


def test_export_csv_rows_equal_engine_records(client, expected):
    url = f"{API}/export/csv?{QUERY}"
    rows = list(csv.DictReader(io.StringIO(client.get(url).text)))
    base = next(r for r in rows if r["scope"] == "state" and r["reform"] == "baseline")
    assert float(base["p50"]) == expected.baseline.p50
    assert float(base["p10"]) == expected.baseline.p10
    adu = next(r for r in rows if r["scope"] == "state" and r["reform"] == "adu")
    assert adu["measure"] == expected.reforms["adu"].added.name
    assert float(adu["p90"]) == expected.reforms["adu"].added.p90
    assert {r["assumption_hash"] for r in rows} == {expected.hashes.assumptions}
    assert {r["data_hash"] for r in rows} == {expected.hashes.data}
    counties = [r for r in rows if r["scope"] == "county" and r["reform"] == "fees"]
    assert len(counties) == len(expected.reforms["fees"].counties)
    for r in counties:
        assert float(r["p50"]) == expected.reforms["fees"].counties[r["geography"]].p50


def test_compare_equals_engine(client, expected):
    got = client.post(f"{API}/compare", INPUTS).json()["columns"][0]
    assert got["baseline"] == _plain(expected.baseline.model_dump(mode="json"))
    for k, o in expected.reforms.items():
        assert got["reform_results"][k]["added"] == _plain(o.added.model_dump(mode="json"))
        assert got["reform_results"][k]["pct_change"] == _plain(
            o.pct_change.model_dump(mode="json")
        )
    assert got["package"]["combined"] == _plain(expected.package.combined.model_dump(mode="json"))
    assert got["total"] == _plain(expected.total.model_dump(mode="json"))


def test_compare_scenarios_each_equal_engine(client):
    body = {
        "state": "TL",
        "seed": 5,
        "draws": 200,
        "scenarios": [{"label": "a", "reforms": ["adu"]}, {"label": "b", "reforms": ["fees"]}],
    }
    cols = client.post(f"{API}/compare", body).json()["columns"]
    for col, key in zip(cols, ["adu", "fees"], strict=True):
        res = _engine(reforms=[key], seed=5, draws=200)
        assert col["reform_results"][key]["added"] == _plain(
            res.reforms[key].added.model_dump(mode="json")
        )


def test_county_equals_engine(client, expected):
    fips = expected.county_ids[0]
    url = f"{API}/states/TL/counties/{fips}?{QUERY}"
    got = client.get(url).json()
    assert got["baseline"] == _plain(expected.baseline_counties[fips].model_dump(mode="json"))
    for k, o in expected.reforms.items():
        assert got["reforms"][k]["added"] == _plain(o.counties[fips].model_dump(mode="json"))
    assert got["package"] == _plain(expected.package.counties[fips].model_dump(mode="json"))


def test_summary_baseline_equals_engine(client):
    got = client.get(f"{API}/states/TL/summary").json()
    res = _engine()
    assert got["baseline"] == _plain(res.baseline.model_dump(mode="json"))
    for c in got["counties"]:
        assert c["baseline"] == _plain(res.baseline_counties[c["fips"]].model_dump(mode="json"))


def test_validation_baseline_equals_engine(client):
    got = client.get(f"{API}/validation?state=TL").json()
    assert got["baseline"] == _plain(_engine().baseline.model_dump(mode="json"))
