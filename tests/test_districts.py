"""District weights, per-draw aggregation and the districts endpoint, on made-up Testland districts.

Checks the two promises of the district view: weights lie in [0, 1] and add up
to 1 per tract (and per county), and the districts add up to the state in every
draw, for the baseline, every reform and the package.
"""

from __future__ import annotations

import gzip
import json

import numpy as np
import pandas as pd
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.data.sources import boundaries as bnd
from visionhousing.data.sources import sld
from visionhousing.districts import DistrictWeights, district_draws, percentiles
from visionhousing.districts.results import district_results, load_tables
from visionhousing.districts.weights import round_shares, weight_table
from visionhousing.engine import run
from visionhousing.engine.context import TESTLAND_DIR

API = "/api/v1"
REFORMS = ["adu", "missing_middle", "fees", "parking"]


@pytest.fixture(scope="module")
def tables():
    return load_tables(TESTLAND_DIR, synthetic=True)


@pytest.fixture(scope="module")
def result():
    return run("TL", REFORMS, draws=300, seed=5)


# ---------------------------------------------------------------- weights


def test_round_shares_adds_up_to_one_exactly():
    shares = np.array([1 / 3, 1 / 3, 1 / 3])
    assert round_shares(shares).tolist() == [3334, 3333, 3333]
    rng = np.random.default_rng(0)
    for _ in range(50):
        raw = rng.random(rng.integers(1, 8))
        assert round_shares(raw / raw.sum()).sum() == 10_000


@pytest.mark.parametrize(
    ("name", "key"), [("tract_weights", "tract"), ("county_weights", "county")]
)
def test_weights_in_unit_interval_and_sum_to_one(tables, name, key):
    table = getattr(tables, name)
    assert set(table["chamber"]) == {"upper", "lower"}
    assert table["weight"].between(0, 1).all() and (table["weight"] > 0).all()
    sums = table.groupby(["chamber", key])["weight"].sum()
    assert np.allclose(sums, 1.0, atol=1e-9)
    tenk = (table["weight"] * 10_000).round().astype(int).groupby([table["chamber"], table[key]])
    assert (tenk.sum() == 10_000).all()


def test_every_testland_tract_has_weights_and_some_are_split(tables):
    tracts = pd.read_csv(TESTLAND_DIR / "tracts.csv", dtype={"geoid": str})
    for chamber in ("upper", "lower"):
        t = tables.tract_weights[tables.tract_weights["chamber"] == chamber]
        assert set(t["tract"]) == set(tracts["geoid"])
    lower = tables.tract_weights[tables.tract_weights["chamber"] == "lower"]
    assert lower["tract"].duplicated().any()
    assert tables.districts["split_share"].between(0, 1).all()
    assert len(tables.districts) == 3 + 9


def test_weight_table_by_housing_units_and_land_fallback():
    blocks = pd.DataFrame(
        {
            "geoid": ["990010001001001", "990010001001002", "990010001001003", "990010002001001"],
            "housing_units": [30, 10, 0, 0],
            "land_area_m2": [1, 1, 1, 300],
        }
    )
    assign = pd.DataFrame({"geoid": blocks["geoid"], "district": ["A", "B", "B", "ZZZ"]})
    with pytest.raises(ValueError, match="no block"):
        weight_table(blocks, assign, "lower", keep=["99001000100", "99001000200"])
    assign.loc[3, "district"] = "B"
    blocks.loc[3, "geoid"] = "990010002001001"
    out = weight_table(blocks, assign, "lower")
    first = out[out["tract"] == "99001000100"].set_index("district")
    assert first.loc["A", "weight"] == 0.75 and first.loc["B", "weight"] == 0.25
    assert first["units"].tolist() == [30, 10]
    second = out[out["tract"] == "99001000200"]
    assert second["weight"].tolist() == [1.0] and second["units"].tolist() == [0]


def test_homes_in_no_district_stop_the_build():
    blocks = pd.DataFrame({"geoid": ["990010001001001"], "housing_units": [5], "land_area_m2": [1]})
    assign = pd.DataFrame({"geoid": blocks["geoid"], "district": ["ZZZ"]})
    with pytest.raises(ValueError, match="no district"):
        weight_table(blocks, assign, "upper")


# ---------------------------------------------------------------- aggregation


def test_district_draws_sum_to_the_state_in_every_draw():
    table = pd.DataFrame(
        {
            "chamber": "lower",
            "district": ["1", "2", "2", "3"],
            "tract": ["a", "a", "b", "c"],
            "weight": [0.3333, 0.6667, 1.0, 1.0],
        }
    )
    w = DistrictWeights.from_table(table, "lower", "tract")
    values = np.random.default_rng(1).gamma(2.0, 10.0, size=(3, 500))
    out = district_draws(values, ["a", "b", "c"], w)
    assert out.shape == (3, 500)
    assert np.allclose(out.sum(axis=0), values.sum(axis=0))
    with pytest.raises(ValueError, match="no weights"):
        district_draws(np.ones((1, 4)), ["zzz"], w)
    # Percentiles come from the summed draws, not from summed percentiles.
    p = percentiles(out)
    assert p.shape == (3, 3) and (p[:, 0] <= p[:, 2]).all()


def test_run_results_add_up_to_the_state_per_draw(result, tables):
    for chamber in ("upper", "lower"):
        ids = tuple(tables.districts.loc[tables.districts["chamber"] == chamber, "district"])
        tw = DistrictWeights.from_table(tables.tract_weights, chamber, "tract", ids)
        cw = DistrictWeights.from_table(tables.county_weights, chamber, "county", ids)
        counties = result.county_ids
        base = district_draws(result.baseline_county_draws, counties, cw)
        assert np.allclose(base.sum(axis=0), result.baseline_draws)
        for key, out in result.reforms.items():
            res = out.result
            if res.level == "tract":
                got = district_draws(res.added, res.geoids, tw)
            else:
                got = district_draws(out.county_draws, counties, cw)
            assert np.allclose(got.sum(axis=0), out.state_draws), key


def test_district_results_payload(result, tables):
    body = district_results(result, tables, "lower", 0.25)
    assert body["district_count"] == 9 and len(body["districts"]) == 9
    first = body["districts"][0]
    assert set(first["reforms"]) == set(REFORMS)
    assert first["package"] is not None
    assert first["caveat"] == (first["split_share"] > 0.25)
    state = {
        "baseline": result.baseline_draws,
        "adu": result.reforms["adu"].state_draws,
        "fees": result.reforms["fees"].state_draws,
        "package": result.package.state_draws,
    }
    for name, draws in state.items():
        want = np.percentile(draws, [10, 50, 90])
        got = body["state_check"][name]
        assert [got["p10"], got["p50"], got["p90"]] == pytest.approx(want, abs=0.06), name
    brief = body["brief"]
    assert {r["key"] for r in brief["reforms"]} == set(REFORMS)
    levels = {r["key"]: r["level"] for r in brief["reforms"]}
    assert levels["adu"] == "tract" and levels["fees"] == "county"
    assert brief["reforms"][0]["driver"]["path"].startswith("explanation.drivers.")
    with pytest.raises(ValueError, match="chamber"):
        district_results(result, tables, "middle", 0.25)


# ---------------------------------------------------------------- endpoint


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("packs")
    (root / "PA").mkdir()
    (root / "PA" / "tracts.csv").write_text("geoid,county\n")
    return AsgiClient(create_app(packs_dir=root))


def test_endpoint_on_testland(client):
    r = client.get(f"{API}/states/TL/districts?chamber=upper&r=adu,fees&draws=200&seed=2")
    assert r.status == 200
    body = r.json()
    assert body["state"] == "TL" and body["synthetic"] is True
    assert body["chamber"] == "upper" and body["district_count"] == 3
    assert body["threshold"] == 0.25
    row = body["districts"][0]
    assert set(row["reforms"]) == {"adu", "fees"}
    assert row["baseline"]["p10"] <= row["baseline"]["p50"] <= row["baseline"]["p90"]
    one = client.get(f"{API}/states/TL/districts?district=2&r=adu&draws=200").json()
    assert [d["id"] for d in one["districts"]] == ["2"]


def test_outlines_endpoint(client):
    r = client.get(f"{API}/states/TL/district-outlines?chamber=upper")
    assert r.status == 200
    feats = r.json()["features"]
    assert [f["properties"]["geoid"] for f in feats] == ["1", "2", "3"]
    lower = client.get(f"{API}/states/TL/district-outlines?chamber=lower").json()["features"]
    assert len(lower) == 9 and {f["properties"]["level"] for f in lower} == {"sldl"}
    assert client.get(f"{API}/states/TL/district-outlines?chamber=x").status == 400


def test_synthetic_outlines_hold_their_own_blocks():
    """Each made-up district's rectangle holds every block point assigned to it."""
    from visionhousing.districts.weights import synthetic_assignment, synthetic_outlines

    blocks = pd.read_csv(TESTLAND_DIR / "blocks.csv", dtype={"geoid": str, "tract": str})
    assignments, _ = synthetic_assignment(blocks)
    shapes = synthetic_outlines(blocks, (-60.2, 30.2, -58.8, 30.7))
    for chamber in ("upper", "lower"):
        boxes = {}
        for f in shapes[chamber]["features"]:
            ring = f["geometry"]["coordinates"][0]
            xs, ys = [p[0] for p in ring], [p[1] for p in ring]
            boxes[f["properties"]["geoid"]] = (min(xs), min(ys), max(xs), max(ys))
        joined = blocks.merge(assignments[chamber], on="geoid")
        for row in joined.itertuples():
            x0, y0, x1, y1 = boxes[row.district]
            assert x0 - 1e-4 <= row.lon <= x1 + 1e-4 and y0 - 1e-4 <= row.lat <= y1 + 1e-4


def test_reply_is_dressed_for_the_screens(client):
    """Text forms, headline, class, caveat text, counties and limits; no number changes."""
    body = client.get(f"{API}/states/TL/districts?chamber=lower&r=adu,fees&draws=200").json()
    assert [c["id"] for c in body["classes"]["classes"]] == ["q1", "q2", "q3", "q4"]
    assert body["limits"]["districts"]["items"] and set(body["limits"]["reforms"]) == {
        "adu",
        "fees",
    }
    breaks = [0.5, 2, 5]
    for d in body["districts"]:
        assert d["baseline"]["shown"]["p50"].endswith("units")
        assert set(d["reforms"]["adu"]["shown"]) == {"p10", "p50", "p90"}
        head = d["headline"]
        assert head["key"] == "package" and head["homes"]["p50"] == d["package"]["p50"]
        rate = d["per_1000"]["package"]
        assert rate["p50"] == pytest.approx(d["package"]["p50"] * 1000 / d["units_2020"], abs=0.06)
        # The server sends the whole sentence, its formula and its ids; the screen adds nothing.
        assert rate["likely"].endswith("homes added per 1,000 of its 2020 homes")
        assert rate["likely"].count("per 1,000") == 1 and " to " in rate["likely"]
        assert rate["formula"].startswith(
            "P10/P50/P90 of homes added \u00d7 1000 \u00f7 2020 housing units"
        )
        assert rate["rule_id"] == "decision.measure.per_1000" and "source_ids" in rate
        assert d["per_1000"]["baseline"]["likely"].endswith(
            "homes permitted per 1,000 of its 2020 homes"
        )
        k = sum(1 for b in breaks if rate["p50"] >= b)
        assert d["class"] == ["q1", "q2", "q3", "q4"][k]
        assert bool(d["caveat_text"]) == d["caveat"]
        assert d["counties_touched"] and all(0 < c["weight"] <= 1 for c in d["counties_touched"])
        assert all(c["name"] for c in d["counties_touched"])
    one = client.get(f"{API}/states/TL/districts?chamber=upper&r=adu&draws=200").json()
    assert one["districts"][0]["headline"]["key"] == "adu"
    none = client.get(f"{API}/states/TL/districts?draws=200").json()
    assert none["districts"][0]["headline"] is None and none["districts"][0]["class"] == "none"


def test_endpoint_errors(client):
    assert client.get(f"{API}/states/TL/districts?chamber=middle").status == 400
    assert client.get(f"{API}/states/TL/districts?district=%3Cx%3E").status == 400
    assert client.get(f"{API}/states/TL/districts?district=99&draws=200").status == 404
    assert client.get(f"{API}/states/TL/districts?bogus=1").status == 400
    r = client.get(f"{API}/states/PA/districts")
    assert r.status == 404 and r.json()["error"] in {"state_not_built", "districts_not_built"}


def test_pack_without_district_tables_says_how_to_build_them(tmp_path):
    with pytest.raises(FileNotFoundError, match="visionhousing districts"):
        load_tables(tmp_path)


# ---------------------------------------------------------------- sources and pack


def test_block_equivalency_reader():
    text = b"GEOID,SLDLST\n420010301001000,091\n420010301001001,ZZZ\n"
    out = sld.read_block_equivalency(text, "42")
    assert out["district"].tolist() == ["091", "ZZZ"]
    with pytest.raises(ValueError, match="outside"):
        sld.read_block_equivalency(text, "50")
    with pytest.raises(ValueError, match="GEOID"):
        sld.read_block_equivalency(b"A,B\n1,2\n")


def _square(x: float, y: float) -> dict:
    ring = [[x, y], [x + 0.1, y], [x + 0.1, y + 0.1], [x, y + 0.1], [x, y]]
    return {"type": "MultiPolygon", "coordinates": [[ring]]}


def test_add_districts_writes_tables_outlines_and_manifest(tmp_path):
    from visionhousing.districts.build import add_districts

    folder = tmp_path / "TL"
    folder.mkdir()
    blocks = pd.read_csv(TESTLAND_DIR / "blocks.csv", dtype={"geoid": str, "tract": str})
    tracts = pd.read_csv(TESTLAND_DIR / "tracts.csv", dtype={"geoid": str, "county": str})
    with gzip.open(folder / "tracts.csv.gz", "wt") as fh:
        tracts[["geoid", "county"]].to_csv(fh, index=False)
    (folder / "pack_manifest.json").write_text(
        json.dumps({"state": "TL", "files": [], "inputs": []})
    )
    from visionhousing.districts.weights import synthetic_assignment

    assignments, names = synthetic_assignment(blocks)
    shapes = {
        ch: [bnd.Shape(d, n, 1, 0, _square(i, 0)) for i, (d, n) in enumerate(sorted(m.items()))]
        for ch, m in names.items()
    }
    inputs = sld.DistrictInputs("TL", "99", assignments, shapes, [])
    built = add_districts(folder, tmp_path, inputs=inputs, blocks=blocks, manifest=None)
    assert set(built.tables) == {"districts", "district_tract_weights", "district_county_weights"}
    outlines = json.loads((folder / "boundaries" / "districts_lower.geojson").read_text())
    assert len(outlines["features"]) == 9
    manifest = json.loads((folder / "pack_manifest.json").read_text())
    paths = {f["path"] for f in manifest["files"]}
    assert {
        "districts.csv.gz",
        "boundaries/districts_upper.geojson",
        "district_vintage.json",
    } <= paths
    loaded = load_tables(folder)
    assert loaded.tract_weights.groupby(["chamber", "tract"])["weight"].sum().round(4).eq(1).all()
    assert loaded.districts["district"].str.len().gt(0).all()
    assert built.pack_bytes > 0
