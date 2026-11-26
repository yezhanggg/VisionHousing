"""Interface endpoints on Testland, called in memory through ``tests/asgi_client.py``.

Each endpoint is called once with good input and checked for shape; the numbers
themselves are checked against the engine in ``test_api_consistency.py``.
"""

from __future__ import annotations

import json

import pytest

from tests.asgi_client import AsgiClient
from visionhousing import cli
from visionhousing.api import create_app
from visionhousing.engine import REGISTRY
from visionhousing.ledger import load_ledger

API = "/api/v1"
SMALL = {"state": "TL", "reforms": ["adu", "fees"], "draws": 300, "seed": 3}


@pytest.fixture(scope="module")
def packs(tmp_path_factory):
    """A packs folder with one fake built state (PA) and one folder that is not a pack."""
    root = tmp_path_factory.mktemp("packs")
    (root / "PA").mkdir()
    (root / "PA" / "tracts.csv").write_text("geoid,county\n")
    (root / "PA" / "pack_manifest.json").write_text(json.dumps({"pack_hash": "abc123"}))
    (root / "OH").mkdir()  # no tracts table: not a built pack
    return root


@pytest.fixture(scope="module")
def client(packs):
    return AsgiClient(create_app(packs_dir=packs))


def test_states_lists_testland_and_built_packs(client):
    r = client.get(f"{API}/states")
    assert r.status == 200
    states = r.json()["states"]
    assert [s["code"] for s in states] == ["TL", "PA"]
    assert states[0]["synthetic"] is True
    assert states[1] == {
        "code": "PA",
        "name": "Pennsylvania",
        "synthetic": False,
        "pack_hash": "abc123",
    }


def test_state_summary(client):
    r = client.get(f"{API}/states/TL/summary")
    assert r.status == 200
    s = r.json()
    assert s["state"] == "TL" and s["synthetic"] is True
    assert s["county_count"] == len(s["counties"]) > 0
    assert s["baseline"]["unit"] == "units" and s["baseline"]["p10"] <= s["baseline"]["p90"]
    first = s["counties"][0]
    assert first["name"].endswith("County") and first["baseline"]["formula"]
    assert {r["key"] for r in s["reforms"]} == set(REGISTRY)
    assert set(s["hashes"]) == {"code", "data", "assumptions"}


def test_state_summary_lowercase_code(client):
    assert client.get(f"{API}/states/tl/summary").json()["state"] == "TL"


def test_run_returns_result_and_explanation_slot(client):
    r = client.post(f"{API}/run", SMALL)
    assert r.status == 200
    body = r.json()
    assert body["state"] == "TL" and body["reforms"] == ["adu", "fees"]
    assert body["draws"] == 300 and body["seed"] == 3 and body["cycle"] == "typical"
    assert set(body["reform_results"]) == {"adu", "fees"}
    assert body["package"]["combined"]["p50"] is not None
    assert body["explanation_status"] == "ok"
    assert body["explanation"]["headline"]


def test_run_with_options_mapping_and_preset(client):
    body = {**SMALL, "reforms": {"fees": {"view": "benchmark"}}, "preset": "high"}
    r = client.post(f"{API}/run", body)
    assert r.status == 200
    assert r.json()["cycle"] == "strong"
    assert r.json()["options"] == {"fees": {"view": "benchmark"}}


def test_run_cache_hits_on_repeat(client):
    runner = client.app.fastapi.state.runner
    body = {**SMALL, "seed": 11}
    first = client.post(f"{API}/run", body)
    hits, misses = runner.stats["hits"], runner.stats["misses"]
    second = client.post(f"{API}/run", body)
    assert second.body == first.body
    assert runner.stats["misses"] == misses  # nothing was run again
    assert runner.stats["hits"] > hits


def test_reforms_list_and_detail(client):
    listed = client.get(f"{API}/reforms").json()["reforms"]
    assert {r["key"] for r in listed} == set(REGISTRY)
    r = client.get(f"{API}/reforms/adu")
    assert r.status == 200
    d = r.json()
    assert d["runnable"] is True and d["title"] == REGISTRY["adu"].title
    assert "600" in d["definition"]["handbook_text"]
    ids = {p["id"] for p in d["parameters"]}
    assert ids == set(load_ledger().used_by("adu"))
    assert all(p["label"] and p["unit"] for p in d["parameters"])


def test_reform_without_engine_module_is_described_not_runnable(client, monkeypatch):
    # A scenario in scenarios.yaml whose engine module is not registered.
    monkeypatch.delitem(REGISTRY, "tod", raising=False)
    d = client.get(f"{API}/reforms/tod").json()
    assert d["runnable"] is False and d["definition"]["title"]


def test_unknown_reform_detail_is_404(client):
    r = client.get(f"{API}/reforms/nothing_here")
    assert r.status == 404 and r.json()["error"] == "reform_not_found"


def test_compare_one_run(client):
    r = client.post(f"{API}/compare", SMALL)
    assert r.status == 200
    cols = r.json()["columns"]
    assert len(cols) == 1
    assert set(cols[0]["reform_results"]) == {"adu", "fees"}
    assert [w["name"] for w in cols[0]["package"]["waterfall"]]


def test_compare_scenarios(client):
    body = {
        "state": "TL",
        "draws": 200,
        "scenarios": [
            {"label": "ADU only", "reforms": ["adu"]},
            {"label": "Fees, weak cycle", "reforms": ["fees"], "preset": "low"},
        ],
    }
    r = client.post(f"{API}/compare", body)
    assert r.status == 200
    cols = r.json()["columns"]
    assert [c["label"] for c in cols] == ["ADU only", "Fees, weak cycle"]
    assert cols[1]["cycle"] == "weak" and cols[1]["reforms"] == ["fees"]


def test_county_detail(client):
    summary = client.get(f"{API}/states/TL/summary").json()
    fips = summary["counties"][0]["fips"]
    r = client.get(f"{API}/states/TL/counties/{fips}?r=adu,fees&draws=300&seed=3")
    assert r.status == 200
    d = r.json()
    assert d["fips"] == fips and d["name"] == summary["counties"][0]["name"]
    assert set(d["reforms"]) == {"adu", "fees"}
    assert d["package"]["p50"] is not None
    assert d["tracts"] and all(t.startswith(fips) for t in d["tracts"])
    geoids = {f["properties"]["geoid"] for f in d["boundaries"]["features"]}
    assert fips in geoids and set(d["tracts"]) <= geoids
    # plain notes for the county panel: the back-test record; no local law read in Testland
    assert d["notes"]["backtest"].startswith("In past years, this tool's baseline for this county")
    assert d["notes"]["local_law"] is None


def test_county_not_in_state_is_404(client):
    r = client.get(f"{API}/states/TL/counties/42001")
    assert r.status == 404 and r.json()["error"] == "county_not_found"


def test_ledger_full_and_search(client):
    full = client.get(f"{API}/ledger").json()
    assert full["count"] == full["total"] == len(load_ledger())
    hit = client.get(f"{API}/ledger?q=Parking").json()
    assert 0 < hit["count"] < full["total"]
    assert hit["hash"] == full["hash"]
    for p in hit["parameters"]:
        text = json.dumps(p).lower()
        assert "parking" in text
    fees = client.get(f"{API}/ledger?used_by=fees&kind=benchmark").json()["parameters"]
    assert fees and all("fees" in p["used_by"] and p["kind"] == "benchmark" for p in fees)


def test_ledger_state_view(client):
    r = client.get(f"{API}/ledger?state=TL")
    assert r.status == 200 and r.json()["total"] > 0


def test_provenance(client):
    r = client.get(f"{API}/provenance")
    assert r.status == 200
    rows = r.json()["sources"]
    assert rows and {
        "source",
        "version",
        "access_date",
        "licence",
        "cleaning",
        "limits",
        "id",
    } <= set(rows[0])


def test_validation(client):
    r = client.get(f"{API}/validation?state=TL")
    assert r.status == 200
    v = r.json()
    assert sum(row["chosen"] for row in v["rows"]) == 1
    chosen = next(row["rule"] for row in v["rows"] if row["chosen"])
    assert chosen == v["chosen_rule"]
    assert all(0 <= row["coverage_80"] <= 1 for row in v["rows"])


def test_export_csv(client):
    r = client.get(f"{API}/export/csv?s=TL&r=adu,fees&draws=300&seed=3")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.splitlines()
    assert lines[0].startswith("scope,geography,reform,measure,p10,p50,p90")
    assert any(",baseline," in line for line in lines[1:])
    assert any(",package," in line for line in lines[1:])


def test_export_json(client):
    r = client.get(f"{API}/export/json?v=1&s=TL&r=adu&draws=300&p=policymaker")
    assert r.status == 200
    assert r.json()["reforms"] == ["adu"]
    assert r.headers["content-disposition"].endswith('.json"')


def test_export_html_brief(client):
    r = client.get(f"{API}/export/html?s=TL&r=adu&draws=300")
    assert r.status == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"].endswith('.html"')
    assert "<html" in r.text.lower()


def test_run_without_explanation(client):
    r = client.post(f"{API}/run", {**SMALL, "explain": False})
    assert r.status == 200
    assert r.json()["explanation"] is None and r.json()["explanation_status"] == "skipped"


def test_web_build_served_at_root(packs, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<!doctype html><script type="module" src="/assets/a.js">'
                                     "</script>", encoding="utf-8")  # fmt: skip
    (dist / "assets" / "a.js").write_text("export {};\n", encoding="utf-8")
    web = AsgiClient(create_app(packs_dir=packs, web_dir=dist))
    r = web.get("/")
    assert r.status == 200 and r.headers["content-type"].startswith("text/html")
    assert "/assets/a.js" in r.text
    js = web.get("/assets/a.js")
    assert js.status == 200 and js.headers["content-type"].startswith("text/javascript")
    assert web.get("/place").text == r.text  # an extension-less address gets the page
    assert web.get("/assets/missing.js").status == 404
    assert web.get(f"{API}/nothing").json()["error"] == "not_found"


def test_unbuilt_web_says_how_to_build(packs, tmp_path):
    web = AsgiClient(create_app(packs_dir=packs, web_dir=tmp_path / "nothing"))
    r = web.get("/")
    assert r.status == 503 and r.headers["content-type"].startswith("text/html")
    assert "make app" in r.text and "npm run build" in r.text
    assert web.get(f"{API}/states").status == 200


def test_unknown_api_address_is_json_404(client):
    r = client.get(f"{API}/not/a/thing")
    assert r.status == 404 and r.json()["error"] == "not_found"


def test_state_without_pack_is_404(client):
    r = client.get(f"{API}/states/OH/summary")
    assert r.status == 404 and r.json()["error"] == "state_not_built"


def test_run_timeout_gives_clear_error():
    app = create_app(run_timeout=0.0001)
    r = AsgiClient(app).post(f"{API}/run", {**SMALL, "seed": 987})
    app.fastapi.state.runner.pool.shutdown(wait=True)
    assert r.status == 504
    body = r.json()
    assert body["error"] == "run_timeout" and "background" in body["message"]


def test_cli_serve_starts_uvicorn(monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    assert cli.main(["serve", "--port", "8123"]) == 0
    assert seen["port"] == 8123 and seen["host"] == "127.0.0.1"
    assert seen["server_header"] is False
    assert hasattr(seen["app"], "fastapi")


def test_run_drivers_only_when_asked(client):
    plain = client.post(f"{API}/run", SMALL).json()["explanation"]
    assert plain["drivers"] == {}
    full = client.post(f"{API}/run", {**SMALL, "drivers": True}).json()["explanation"]
    assert set(full["drivers"]) == {"adu", "fees"}
