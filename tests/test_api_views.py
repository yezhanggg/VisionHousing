"""The screen-ready views (``api/views.py``) on Testland.

County map, tract shading, the statewide tract columns (points, values, funnel),
outlines, evidence and the report card.

The map's percent change must come from the engine's own draws, its classes from the
ledger's verdict thresholds, and the tract values must add up to the county record.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.api.views import TRACT_BREAK_IDS, interior_point
from visionhousing.engine.context import load_context
from visionhousing.engine.core import run

API = "/api/v1"
QUERY = "r=adu,fees&seed=0&draws=300"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return AsgiClient(create_app(packs_dir=tmp_path_factory.mktemp("packs")))


def test_map_classes_follow_the_verdict_thresholds(client):
    reply = client.get(f"{API}/states/TL/map?{QUERY}").json()
    some = reply["thresholds"]["explain.verdict_some_pct"]
    lot = reply["thresholds"]["explain.verdict_a_lot_pct"]
    assert [c["id"] for c in reply["classes"]] == ["little", "some", "a_lot"]
    assert reply["counties"]
    for c in reply["counties"]:
        mid = c["change"]["p50"]
        want = "a_lot" if mid >= lot else "some" if mid >= some else "little"
        assert c["class"] == want
        assert c["change"]["p10"] <= mid <= c["change"]["p90"]
        assert c["added"]["shown"]["p50"].endswith("units")
    assert reply["ids"]["added"]["assumption_ids"]


def test_map_change_is_added_over_baseline_in_each_draw(client):
    run = client.post(
        f"{API}/run", {"state": "TL", "reforms": ["adu", "fees"], "draws": 300, "explain": False}
    ).json()
    reply = client.get(f"{API}/states/TL/map?{QUERY}").json()
    for c in reply["counties"]:
        assert c["added"]["p50"] == run["package"]["counties"][c["fips"]]["p50"]
        assert c["baseline"]["p50"] == run["baseline_counties"][c["fips"]]["p50"]
        # A draw-by-draw ratio, so not the ratio of the middles, but close to it.
        ratio = 100 * c["added"]["p50"] / c["baseline"]["p50"]
        assert c["change"]["p10"] <= ratio * 1.5 and ratio <= c["change"]["p90"] * 1.5


def test_map_without_reforms_has_no_classes_to_shade(client):
    reply = client.get(f"{API}/states/TL/map").json()
    assert {c["class"] for c in reply["counties"]} == {"none"}
    assert all(c["added"] is None and c["baseline"] for c in reply["counties"])


def test_county_tract_values_add_up_to_the_county(client):
    reply = client.get(f"{API}/states/TL/counties/99001?{QUERY}").json()
    tr = reply["tract_results"]
    assert "fees" not in tr["reforms"]  # a county-level reform has no tract values
    adu = tr["reforms"]["adu"]
    assert {t["geoid"] for t in adu["tracts"]} <= set(reply["tracts"])
    total = sum(t["p50"] for t in adu["tracts"])
    assert total == pytest.approx(reply["reforms"]["adu"]["added"]["p50"], rel=0.15)
    assert len(tr["breaks"]) == 3 and len(tr["labels"]) == 4
    assert tr["breaks"] == sorted(tr["breaks"])


def test_outlines_evidence_and_report_card(client):
    out = client.get(f"{API}/states/TL/outlines").json()
    assert out["features"] and all(len(f["properties"]["geoid"]) == 5 for f in out["features"])
    ev = client.get(f"{API}/evidence?reform=adu").json()
    assert ev["count"] == len(ev["entries"]) > 0
    assert all(e["reform"] == "adu" for e in ev["entries"])
    assert client.get(f"{API}/evidence?reform=a%20b").status == 400
    card = client.get(f"{API}/report-card?state=TL").json()
    assert card["state"] == "TL" and card["inputs"]


# ------------------------------------------------------------------ statewide tract columns

PACKAGE = "adu,missing_middle,tod,parking,fees"
TRACT_QUERY = "seed=0&draws=300"


def tracts(client, reforms: str = PACKAGE) -> dict:
    reply = client.get(f"{API}/states/TL/tracts?r={reforms}&{TRACT_QUERY}")
    assert reply.status == 200, reply.text
    return reply.json()


def inside(point, geometry) -> bool:
    """Even-odd ray test: is the point inside the polygon (and outside its holes)?"""
    x, y = point

    def crosses(ring) -> bool:
        odd = False
        for (x0, y0), (x1, y1) in itertools.pairwise(ring):
            if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                odd = not odd
        return odd

    polygons = [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        polygons = geometry["coordinates"]
    return any(crosses(p[0]) and not any(crosses(h) for h in p[1:]) for p in polygons)


def test_tract_points_sit_inside_their_tracts(client):
    pts = client.get(f"{API}/states/TL/tract-points").json()
    n = pts["count"]
    assert n == 40 and pts["missing"] == 0
    assert pts["geoids"] == sorted(pts["geoids"])
    for col in ("county_index", "lon", "lat"):
        assert len(pts[col]) == n
    for g, i in zip(pts["geoids"], pts["county_index"], strict=True):
        assert pts["counties"][i] == g[:5]
    ctx = load_context("TL")
    shapes = {f["properties"]["geoid"]: f["geometry"] for f in ctx.boundaries["features"]}
    for g, lon, lat in zip(pts["geoids"], pts["lon"], pts["lat"], strict=True):
        assert inside((lon, lat), shapes[g]), g
        assert round(lon, 5) == lon and round(lat, 5) == lat
    # The values replies use the same ids in the same order.
    assert tracts(client)["geoids"] == pts["geoids"]


def test_interior_point_stays_inside_awkward_shapes():
    u_shape = {  # its centroid falls in the empty middle of the U
        "type": "Polygon",
        "coordinates": [[[0, 0], [3, 0], [3, 3], [2, 3], [2, 1], [1, 1], [1, 3], [0, 3], [0, 0]]],
    }
    ring = [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]
    hole = [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]
    donut = {"type": "Polygon", "coordinates": [ring, hole]}  # centroid in the hole
    islands = {
        "type": "MultiPolygon",
        "coordinates": [[[[10, 10], [10.1, 10], [10.1, 10.1], [10, 10]]], [ring]],
    }
    for shape in (u_shape, donut, islands):
        point = interior_point(shape)
        assert point is not None and inside(point, shape), shape["type"]
    assert interior_point(islands)[0] < 5  # the largest part wins
    assert interior_point(None) is None
    assert interior_point({"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 0]]]}) is None


def test_tract_columns_line_up_and_ranges_are_ordered(client):
    reply = tracts(client)
    n, m = reply["count"], len(reply["counties"])
    added = reply["added"]
    tract_cols = [added["p10"], added["p50"], added["p90"], reply["class"]]
    tract_cols += [r["p50"] for r in reply["by_reform"].values()]
    county_cols = [added["counties"][q] for q in ("p10", "p50", "p90")]
    for r in [*reply["by_reform"].values(), *reply["county_only"].values()]:
        block = r.get("counties", r)
        county_cols += [block["p10"], block["p50"], block["p90"]]
    assert all(len(c) == n for c in tract_cols) and all(len(c) == m for c in county_cols)
    assert len(reply["geoids"]) == n
    for lo, mid, hi in zip(added["p10"], added["p50"], added["p90"], strict=True):
        assert lo <= mid <= hi
    assert set(reply["by_reform"]) == {"adu", "missing_middle", "tod"}
    assert set(reply["county_only"]) == {"parking", "fees"}
    assert set(reply["funnels"]) == {"adu", "missing_middle", "tod"}
    assert reply["funnels"]["adu"]["ask"] == {"reforms": ["adu"]}
    # Classes: the ledger's fixed breaks, the same for every scenario.
    ledger = load_context("TL").ledger
    breaks = [ledger[i].central for i in TRACT_BREAK_IDS]
    assert reply["breaks"] == breaks == tracts(client, "adu")["breaks"]
    assert [c["id"] for c in reply["classes"]] == ["none", "few", "some", "many", "most"]
    for mid, cls in zip(added["p50"], reply["class"], strict=True):
        want = 0 if mid <= 0 else 1 + sum(mid >= b for b in breaks)
        assert cls == want or abs(mid - min(breaks, key=lambda b: abs(b - mid))) <= 0.05
    assert reply["rounding"] and reply["sum_rule"] and reply["baseline"]["tract_split"] is False
    assert reply["scenario_total"]["p50"] > added["total"]["p50"]  # fees and parking add more


def test_tract_values_add_up_the_engine_way(client):
    """Tract P50s are rounded medians; county and state values are sums of draws.

    The exact relation, draw by draw: the package's county draws = its tract draws summed
    by county + each county-level reform's county draws. Percentiles do not add: the sum
    of the tract medians is not the median of the sum, so the reply carries the engine's
    own county and state ranges.
    """
    reply = tracts(client)
    res = run("TL", PACKAGE.split(","), seed=0, draws=300, use_cache=False)
    pkg = res.package
    added = reply["added"]
    total = pkg.tract_combined
    assert (added["total"]["p10"], added["total"]["p50"], added["total"]["p90"]) == (
        total.p10,
        total.p50,
        total.p90,
    )
    rest = sum(res.reforms[k].county_draws for k in pkg.county_reforms)
    assert np.allclose(pkg.tract_county_draws + rest, pkg.county_draws, rtol=0, atol=1e-9)
    at = {g: i for i, g in enumerate(pkg.tract_geoids)}
    medians = np.median(pkg.tract_draws, axis=1)
    for g, shown in zip(reply["geoids"], added["p50"], strict=True):
        assert shown == pytest.approx(medians[at[g]], abs=0.05 if medians[at[g]] < 100 else 0.5)
    for i, shown in enumerate(added["counties"]["p50"]):
        assert shown == pytest.approx(np.median(pkg.tract_county_draws[i]), abs=0.05, rel=0.005)
    sum_of_medians = float(medians.sum())
    median_of_sum = float(np.median(pkg.tract_draws.sum(axis=0)))
    assert median_of_sum == pytest.approx(total.p50, abs=1e-9)
    assert sum_of_medians != pytest.approx(median_of_sum, rel=1e-6)
    # One reform alone: the tract columns are that reform's own draws.
    adu = tracts(client, "adu")
    alone = run("TL", ["adu"], seed=0, draws=300, use_cache=False).reforms["adu"]
    assert adu["added"]["total"]["p50"] == pytest.approx(alone.added.p50, rel=1e-12)
    assert adu["added"]["p50"] == adu["by_reform"]["adu"]["p50"]
    for i, c in enumerate(adu["counties"]):
        want = alone.counties[c].p50
        assert adu["by_reform"]["adu"]["counties"]["p50"][i] == pytest.approx(want, abs=0.05)


def test_county_reforms_and_the_baseline_have_no_tract_values(client):
    fees = tracts(client, "fees")
    assert fees["added"] is None and fees["by_reform"] == {} and fees["funnels"] == {}
    assert set(fees["class"]) == {0}
    run_fees = run("TL", ["fees"], seed=0, draws=300, use_cache=False).reforms["fees"]
    for i, c in enumerate(fees["counties"]):
        assert fees["county_only"]["fees"]["p50"][i] == pytest.approx(
            run_fees.counties[c].p50, abs=0.05
        )
    assert fees["county_only"]["fees"]["funnel"] is None
    base = tracts(client, "")
    assert base["added"] is None and base["county_only"] == {} and base["scenario_total"] is None
    assert base["baseline"]["tract_split"] is False and set(base["class"]) == {0}
    funnel = client.get(f"{API}/states/TL/tract-funnel?r=fees&{TRACT_QUERY}").json()
    assert funnel["funnel"] == {} and funnel["county_only"] == {"fees": "Development fee reduction"}


def test_tract_funnel_steps_follow_the_module_stages(client):
    one = client.get(f"{API}/states/TL/tract-funnel?r=adu&{TRACT_QUERY}").json()
    steps = one["funnel"]["adu"]["steps"]
    assert [s["step"] for s in steps] == ["scope", "fit", "feasible", "permits"]
    assert [s["stage"] for s in steps] == ["eligible", "fit", "feasible", "built"]
    assert [s["unit"] for s in steps] == ["lots", "lots", "lots", "units"]
    n, m = one["count"], len(one["counties"])
    for s in steps:
        t, c = s["tracts"], s["counties"]
        assert len(t["p10"]) == len(t["p50"]) == len(t["p90"]) == n
        assert len(c["p50"]) == m
        assert all(
            lo <= mid <= hi for lo, mid, hi in zip(t["p10"], t["p50"], t["p90"], strict=True)
        )
    # Each step keeps a share of the one before, in every draw, so medians keep the order.
    for i in range(n):
        chain = [s["tracts"]["p50"][i] for s in steps]
        assert chain == sorted(chain, reverse=True)
    run_reply = client.post(
        f"{API}/run", {"state": "TL", "reforms": ["adu"], "draws": 300, "explain": False}
    ).json()
    state_steps = run_reply["reform_results"]["adu"]["steps"]
    for s, i in zip(steps, (0, 1, 2, 3), strict=True):
        assert s["state"]["p50"] == state_steps[i]["p50"]
        assert s["state"]["formula"] == state_steps[i]["formula"]
    tod = client.get(f"{API}/states/TL/tract-funnel?r=tod&{TRACT_QUERY}").json()
    tod_steps = tod["funnel"]["tod"]["steps"]
    assert [s["stage"] for s in tod_steps] == ["eligible", "developable", "feasible", "absorbed"]
    assert [s["unit"] for s in tod_steps] == ["acres", "acres", "acres", "units"]
    # In a package each reform's funnel is the reform run alone: the same numbers.
    pkg = client.get(f"{API}/states/TL/tract-funnel?r={PACKAGE}&{TRACT_QUERY}").json()
    assert set(pkg["funnel"]) == {"adu", "missing_middle", "tod"}
    assert pkg["funnel"]["adu"] == one["funnel"]["adu"]
    assert pkg["county_only"] == {
        "parking": "Parking requirement reduction",
        "fees": "Development fee reduction",
    }


def test_county_panel_carries_the_funnel_steps_of_its_tracts(client):
    reply = client.get(f"{API}/states/TL/counties/99001?r=adu&{TRACT_QUERY}").json()
    adu = reply["tract_results"]["reforms"]["adu"]
    funnel = client.get(f"{API}/states/TL/tract-funnel?r=adu&{TRACT_QUERY}").json()
    whole = funnel["funnel"]["adu"]["steps"]
    at = {g: i for i, g in enumerate(funnel["geoids"])}
    county = funnel["counties"].index("99001")
    assert [s["step"] for s in adu["steps"]] == ["scope", "fit", "feasible", "permits"]
    for own, all_tracts in zip(adu["steps"], whole, strict=True):
        assert len(own["tracts"]["p50"]) == len(adu["tracts"])
        for t, value in zip(adu["tracts"], own["tracts"]["p50"], strict=True):
            assert value == all_tracts["tracts"]["p50"][at[t["geoid"]]]
        assert own["counties"]["p50"] == [all_tracts["counties"]["p50"][county]]


def test_tract_endpoints_take_the_scenario_like_the_map(client):
    by_r = client.get(f"{API}/states/TL/tracts?r=adu&{TRACT_QUERY}")
    by_name = client.get(f"{API}/states/TL/tracts?reforms=adu&{TRACT_QUERY}")
    assert by_r.body == by_name.body
    both = client.get(f"{API}/states/TL/tracts?r=adu&reforms=adu")
    assert both.status == 400 and both.json()["error"] == "invalid_query"
    assert client.get(f"{API}/states/TL/tracts?r=nope").status == 400
    assert client.get(f"{API}/states/ZZ/tracts").status == 404
    assert client.get(f"{API}/states/ZZ/tract-points").status == 404
    custom = client.get(f"{API}/states/TL/tracts?r=adu&a=adu.takeup_feasible_annual~h&draws=300")
    assert custom.json()["added"]["total"]["p50"] > by_r.json()["added"]["total"]["p50"]


def test_map_rankings_order_the_counties_on_the_server(client):
    reply = client.get(f"{API}/states/TL/map?{QUERY}").json()
    ranks = {r["id"]: r for r in reply["rankings"]}
    assert list(ranks) == ["added", "per_1000", "change"]
    by_fips = {c["fips"]: c for c in reply["counties"]}
    for r in ranks.values():
        mids = [row["value"]["p50"] for row in r["rows"]]
        assert mids == sorted(mids, reverse=True)
        assert [row["rank"] for row in r["rows"]] == list(range(1, r["count"] + 1))
        assert r["rows"][0]["rank_text"] == f"1st of {r['count']}"
        for row in r["rows"]:
            assert row["value"] == by_fips[row["fips"]][r["field"]]
    for c in reply["counties"]:
        rate = c["per_1000"]
        assert rate["unit"] == "per 1,000 homes"
        want = 1000 * c["added"]["p50"] / c["housing_units"]
        assert rate["p10"] <= want * 1.5 and want <= rate["p90"] * 1.5
    base = client.get(f"{API}/states/TL/map").json()
    assert [r["id"] for r in base["rankings"]] == ["baseline", "baseline_per_1000"]


def test_permit_years_add_up_the_permit_table(client):
    """The year slider's reply: the pack's permits summed by the server, rates and classes."""
    from visionhousing.api.decision import load_decision

    reply = client.get(f"{API}/states/TL/permit-years").json()
    ctx = load_context("TL")
    spec = load_decision()["permit_years"]
    years = reply["years"]
    assert years[0] == spec["first_year"]
    assert years[-1] == reply["latest"] == int(ctx.permits["year"].max())
    cols = ["units_1", "units_2", "units_3_4", "units_5plus"]
    by = {(str(r.county), int(r.year)): r for r in ctx.permits.itertuples(index=False)}
    ids = ["q1", "q2", "q3", "q4"]
    for row in reply["counties"]:
        assert row["housing_units"] > 0
        for i, y in enumerate(years):
            r = by.get((row["fips"], y))
            if r is None:
                assert row["class"][i] == "nodata"
                continue
            homes = sum(getattr(r, c) for c in cols)
            rate = 1000 * homes / row["housing_units"]
            assert row["units"][i] == round(homes)
            assert row["per_1000"][i] == pytest.approx(rate, abs=1e-3)
            assert row["class"][i] == ids[sum(rate >= b for b in spec["breaks"])]
            assert row["imputed"][i] == bool(r.imputed)
    last = ctx.permits[ctx.permits["year"] == years[-1]][cols].to_numpy().sum()
    assert reply["state_past"][-1]["units"] == pytest.approx(float(last))
    assert [c["id"] for c in reply["classes"]] == ids


def test_permit_years_project_the_baseline_years(client):
    """The projected years are the baseline run's own yearly county table, on the same scale."""
    reply = client.get(f"{API}/states/TL/permit-years").json()
    run_reply = client.post(f"{API}/run", {"state": "TL", "reforms": []}).json()
    base = run_reply["annual"]["baseline"]
    proj = reply["projected"]
    assert proj["years"] == base["years"] == [2027, 2028, 2029, 2030, 2031]
    index = {g: i for i, g in enumerate(base["counties"]["geoids"])}
    for row, p in zip(reply["counties"], proj["counties"], strict=True):
        i = index[row["fips"]]
        assert p["p50"] == pytest.approx(base["counties"]["p50"][i], abs=1e-3)
        for v, rate in zip(p["p50"], p["per_1000"], strict=True):
            assert rate == pytest.approx(1000 * v / row["housing_units"], abs=1e-3)
    assert [r["p50"] for r in proj["state"]] == pytest.approx([r["p50"] for r in base["state"]])


class _NoBaseline:
    """A run whose first county has a baseline of 0 (every draw), all else unchanged."""

    def __init__(self, res):
        self._res = res
        first = res.county_ids[0]
        draws = np.array(res.baseline_county_draws, dtype=float, copy=True)
        draws[0] = 0.0
        self.baseline_county_draws = draws
        zero = res.baseline_counties[first].model_copy(update={"p10": 0.0, "p50": 0.0, "p90": 0.0})
        self.baseline_counties = {**res.baseline_counties, first: zero}

    def __getattr__(self, name):
        return getattr(self._res, name)


def test_a_county_with_no_baseline_is_not_shown_as_zero_percent():
    """A county whose baseline is 0 gets its own class and sentence, never "0%"."""
    from visionhousing.api import service
    from visionhousing.api.views import NO_BASE, county_map
    from visionhousing.engine.context import TESTLAND_DIR

    ctx = load_context("TL")
    res = run("TL", ["adu"], seed=0, draws=300, use_cache=False)
    facts = service._county_facts(TESTLAND_DIR)
    reply = county_map(ctx, facts, _NoBaseline(res), None)
    first, *rest = reply["counties"]
    assert first["added"]["p50"] > 0
    assert first["class"] == NO_BASE
    assert first["change"] is None
    assert first["no_base"]["text"].startswith("No baseline to compare; adds ")
    assert "homes" in first["no_base"]["text"]
    assert any(c["id"] == NO_BASE and c["word"] == "No baseline" for c in reply["classes"])
    assert all(c["class"] != NO_BASE for c in rest)
    # The usual reply has no such class and no legend entry for it.
    usual = county_map(ctx, facts, res, None)
    assert all(c["class"] != NO_BASE for c in usual["counties"])
    assert [c["id"] for c in usual["classes"]] == ["little", "some", "a_lot"]


def _validation(rules, chosen):
    return {"chosen_rule": chosen, "contest": {"rules": rules, "winner": chosen}}


def test_contest_view_uses_the_contest_measure_and_plain_names(client):
    """The Method screen's rule list: the contest's typical miss, plain names, server text."""
    from visionhousing.api.views import contest_view
    from visionhousing.explain.numbers import TABLE_SIG, show

    reply = client.get(f"{API}/validation?state=TL").json()
    view = reply["contest_view"]
    assert view == contest_view(reply)
    rules = {r["rule"]: r for r in reply["contest"]["rules"]}
    assert [r["rule"] for r in view["rules"]] == list(rules)
    for r in view["rules"]:
        assert r["name"] == rules[r["rule"]]["name"]
        assert r["value"] == rules[r["rule"]]["abs_pct"]
        assert r["text"] == show(rules[r["rule"]]["abs_pct"], "share", TABLE_SIG)
        assert r["used"] == (r["rule"] == reply["chosen_rule"])
    assert view["used"] == reply["chosen_rule"]


def test_contest_view_says_why_the_used_rule_is_not_the_lowest():
    from visionhousing.api.views import contest_view

    low = {"rule": "a", "name": "rule a", "abs_pct": 0.40}
    used = {"rule": "b", "name": "rule b", "abs_pct": 0.42}
    view = contest_view(_validation([low, used], "b"))
    assert view["used_name"] == "rule b"
    assert "rule a misses less (40% against 42%)" in view["note"]
    assert "rule fixed before scoring" in view["note"]
    # Lowest on the measure: no note; a tie at the shown rounding is said plainly.
    assert contest_view(_validation([low, used], "a"))["note"] == ""
    tie = contest_view(_validation([{**low, "abs_pct": 0.4001}, {**used, "abs_pct": 0.40004}], "b"))
    assert "same typical miss at this rounding" in tie["note"]
    assert contest_view({"contest": {"rules": []}}) is None
