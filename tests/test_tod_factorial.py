"""TOD factorial comparison: service rule x access x distance, paired with the benchmark.

Checks (docs/methods/tod.md, "Factorial comparison of the transit definition"):
the longest-gap rule on departure times (window edges, repeats, bunching); the
hourly lower bound fails bunched departures that the average rule passes; a cell
that changes no factor is the benchmark exactly; a service rule that picks the same
stops as the benchmark gives the benchmark exactly; every paired difference is taken
draw by draw; unavailable cells get their own outcome; the part is opt-in.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.engine.comparisons import PARTS, Bench, comparisons
from visionhousing.engine.comparisons.tod_factorial import (
    OUTCOMES,
    load_factorial_settings,
    tod_factorial,
)
from visionhousing.engine.context import load_context
from visionhousing.features import transit_walk as tw
from visionhousing.ledger import LedgerError
from visionhousing.scenarios import tod as T  # noqa: N812

RULE = tw.PeakRule(15.0, (7, 9), (16, 18))
SEED, DRAWS = 5, 200

# Testland's rents are too low for the mid-rise to pencil at central costs (as in
# tests/test_scenario_tod.py): rich rents and cheap construction make the cells differ.
RICH = {
    "multifamily.hard_cost_per_sqft": 120,
    "tod.new_unit_rent_premium": 2.5,
    "tod.acquisition_premium": 0.05,
}


# ---------------------------------------------------------------- the longest-gap rule


def test_max_gap_counts_the_window_edges():
    every15 = [7 * 60 + 15 * k for k in range(8)]  # 7:00 ... 8:45
    assert T.max_scheduled_gap(every15, (420, 540)) == 15
    late = [7 * 60 + 15 * k for k in range(1, 8)]  # 7:15 ... 8:45: start gap 15, end gap 15
    assert T.max_scheduled_gap(late, (420, 540)) == 15
    # A departure at the window end belongs to the next window.
    assert T.max_scheduled_gap([420, 540], (420, 540)) == 120
    assert T.max_scheduled_gap([], (420, 540)) == 120


def test_bunched_departures_fail_the_max_gap_rule_but_pass_the_average():
    bunched = [420 + 2 * k for k in range(8)]  # eight buses 7:00-7:14
    assert len(bunched) >= RULE.min_departures(RULE.am)  # average headway 15 minutes
    assert T.max_scheduled_gap(bunched, (420, 540)) == 540 - 434
    assert T.max_scheduled_gap(bunched, (420, 540)) > RULE.max_headway_min


def test_repeated_listings_of_one_bus_count_once():
    four = [420, 450, 480, 510]
    listed_twice = sorted(four + four)  # eight listings, four buses
    assert T.max_scheduled_gap(listed_twice, (420, 540)) == 30
    near = sorted(four + [t + 0.5 for t in four])
    assert T.max_scheduled_gap(near, (420, 540), dedupe_min=1.0) == 30
    assert T.max_scheduled_gap(near, (420, 540), dedupe_min=0.0) == 29.5


def test_hourly_bound_rules_out_bunching_and_never_confirms():
    lb = T.hourly_gap_lower_bound(np.array([[8, 0], [4, 4], [3, 4], [3, 3], [2, 6]]))
    assert lb[0] == 60  # an empty hour: some gap is at least an hour
    assert lb[1] == pytest.approx(120 / 9)  # not ruled out
    assert lb[2] == pytest.approx(15)  # at the headway: not ruled out
    assert lb[3] == pytest.approx(120 / 7)  # six in two hours: ruled out
    assert lb[4] == pytest.approx(20)  # two in one hour: ruled out
    # Bunched in the first hour: the average rule passes, the bound fails it.
    assert lb[0] > 15 and RULE.min_departures(RULE.am) <= 8


def _peaks_row(stop: str, lat: float, lon: float, am: list[int], pm: list[int], side="all"):
    row = {"stop_id": stop, "lat": lat, "lon": lon, "direction": side}
    row.update({f"h{h:02d}": 0 for h in range(30)})
    for h, c in zip((7, 8), am, strict=True):
        row[f"h{h:02d}"] = c
    for h, c in zip((16, 17), pm, strict=True):
        row[f"h{h:02d}"] = c
    return row


def test_gap_screen_judges_both_peaks_on_one_side():
    peaks = pd.DataFrame(
        [
            _peaks_row("A", 0, 0, [4, 4], [0, 0], "main"),  # morning only this way
            _peaks_row("A", 0, 0, [0, 0], [4, 4], "opposite"),  # evening only that way
            _peaks_row("B", 0, 0, [4, 4], [4, 4], "main"),
            _peaks_row("B", 0, 0, [8, 0], [8, 0], "opposite"),
        ]
    )
    s = T.gap_screen(peaks, RULE)
    assert s.loc["A", "not_ruled_out"] == 0  # no single side serves both peaks
    assert s.loc["B", "not_ruled_out"] == 1  # the even side
    assert s.loc["B", "gap_lb"] == pytest.approx(120 / 9)


# ---------------------------------------------------------------- the part on Testland


def _service_context(bunched: tuple[str, ...] = ()):
    """Testland with a schedule feed, built in memory.

    Its daily-rule bus stops run every 15 minutes in both peaks; the other bus stops
    once an hour; ``bunched`` stops run eight buses in the first hour of each peak and
    none in the second.
    """
    ctx = load_context("TL")
    stops = ctx.stops.copy()
    stops["feed_id"] = "TLF"
    local = stops["stop_id"].astype(str)
    stops["stop_id"] = "TLF:" + local
    rows = []
    for sid, lat, lon, mode, deps in zip(
        local, stops["lat"], stops["lon"], stops["mode"], stops["weekday_departures"], strict=True
    ):
        if mode != "bus":
            continue
        if sid in bunched:
            am, pm = [8, 0], [8, 0]
        elif deps >= 64:
            am, pm = [4, 4], [4, 4]
        else:
            am, pm = [1, 1], [1, 1]
        rows.append(_peaks_row(sid, lat, lon, am, pm))
    feeds = pd.DataFrame(
        [{"feed_id": "TLF", "schedule_feed": "S1", "frequency": "counted", "bus_stops": 22}]
    )
    out = replace(ctx, stops=stops)
    out.derived[("pack_table", "transit_feeds")] = feeds
    return out, {"S1": pd.DataFrame(rows)}


@pytest.fixture(scope="module")
def same_stops():
    ctx, peaks = _service_context()
    bench = Bench.build(ctx, overrides=RICH, seed=SEED, draws=DRAWS)
    return bench, tod_factorial(bench, peaks=peaks, peak_notes={"S1": {"date": "2026-11-03"}})


def _cells(out):
    return {c["key"]: c for c in out["cells"]}


def test_no_factor_changed_is_the_benchmark_exactly(same_stops):
    bench, out = same_stops
    cells = _cells(out)
    head = bench.alone("tod").headline
    b = cells[out["benchmark"]["key"]]
    assert out["checks"]["benchmark_reproduced"] is True
    assert b["changed"] == [] and b["benchmark"] is True
    assert (b["p10"], b["p50"], b["p90"]) == (head.p10, head.p50, head.p90)
    assert b["difference"]["p10"] == b["difference"]["p50"] == b["difference"]["p90"] == 0
    assert b["difference"]["below_benchmark"]["share"] == 0
    assert head.p50 > 0  # the fixture is not trivially zero


def test_a_service_rule_picking_the_same_stops_gives_the_benchmark(same_stops):
    _, out = same_stops
    cells = _cells(out)
    assert out["stops"]["peak_average"]["major_bus"] == out["stops"]["daily"]["major_bus"]
    for service in ("peak_average", "peak_max_gap"):
        c = cells[f"{service}|straight|d07"]
        assert c["changed"] == ["service"]
        assert c["difference"]["p10"] == c["difference"]["p90"] == 0
    assert cells["peak_max_gap|straight|d07"]["reading"] == "upper"


def test_paired_differences_are_taken_draw_by_draw(same_stops):
    bench, out = same_stops
    cells = _cells(out)
    base = np.asarray(bench.alone("tod").detail.stages["authorized"]).sum(axis=1)
    half = np.asarray(bench.alone("tod", {"distance": 0.5}).detail.stages["authorized"]).sum(axis=1)
    c = cells["daily|straight|0.5"]
    assert c["p50"] == pytest.approx(float(np.percentile(half, 50)))
    d = half - base
    assert c["difference"]["p50"] == pytest.approx(float(np.percentile(d, 50)))
    assert c["difference"]["p90"] == pytest.approx(float(np.percentile(d, 90)))
    share = c["difference"]["above_benchmark"]
    assert share["share"] == pytest.approx(float((d > 1e-9).mean()))
    assert share["se"] == pytest.approx(np.sqrt(share["share"] * (1 - share["share"]) / DRAWS))


def test_unavailable_cells_get_their_own_outcomes(same_stops):
    _, out = same_stops
    cells = _cells(out)
    assert set(out["outcomes"]) == set(OUTCOMES)
    assert cells["daily|network|d07"]["outcome"] == "not_defined"
    assert cells["daily|network|0.5"]["outcome"] == "not_measured"  # no walk table here
    assert all(c["outcome"] in OUTCOMES for c in out["cells"])
    assert len(out["cells"]) == 3 * 2 * 4
    assert {o["factor"] for o in out["one_at_a_time"]} == {"service", "access", "distance"}


def test_bunched_stops_are_ruled_out_only_by_the_max_gap_rule():
    bunched = ("TL-B01", "TL-B03")
    ctx, peaks = _service_context(bunched)
    bench = Bench.build(ctx, overrides=RICH, seed=SEED, draws=DRAWS)
    out = tod_factorial(bench, peaks=peaks)
    s = out["stops"]
    assert s["peak_average"]["major_bus"] == s["daily"]["major_bus"]
    assert s["peak_max_gap"]["ruled_out_bus"] >= len(bunched)
    assert s["peak_max_gap"]["not_ruled_out_bus"] == s["daily"]["major_bus"] - len(bunched)
    cells = _cells(out)
    avg = cells["peak_average|straight|0.75"]["eligible_acres_p50"]
    gap = cells["peak_max_gap|straight|0.75"]["eligible_acres_p50"]
    assert gap <= avg


def test_network_cells_and_the_access_anchor_with_a_walk_table():
    from tests.test_benchmark_variants import _walk_table

    ctx, peaks = _service_context()
    ctx = replace(ctx, transit_walk=_walk_table(ctx.tracts))
    ctx.derived[("pack_table", "transit_feeds")] = _service_context()[0].derived[
        ("pack_table", "transit_feeds")
    ]
    bench = Bench.build(ctx, overrides=RICH, seed=SEED, draws=DRAWS)
    out = tod_factorial(bench, peaks=peaks)
    cells = _cells(out)
    assert cells["peak_average|network|0.5"]["outcome"] in OUTCOMES[:4]
    variant = bench.alone("tod", {"variant": "tod_network_half_mile"}).headline
    assert cells["peak_average|network|0.5"]["p50"] == pytest.approx(variant.p50)
    assert cells["daily|network|0.5"]["outcome"] == "not_measured"
    assert cells["peak_max_gap|network|0.5"]["outcome"] == "not_measurable"
    anchor = out["access_anchor"]
    assert anchor["measured"] is True
    # The fixture's network acres are 1/1.3 of its straight acres: the network walk reaches less.
    assert anchor["difference"]["p50"] <= 0
    assert "access" in out["moves_most"]["effects"]


def test_no_peak_schedules_reads_as_schedules_missing():
    bench = Bench.build("TL", overrides=RICH, seed=SEED, draws=DRAWS)
    cells = _cells(tod_factorial(bench, peaks={}))
    assert cells["peak_average|straight|d07"]["outcome"] == "schedules_missing"
    assert cells["daily|straight|d07"]["outcome"] in ("estimate", "small_estimate")


def test_settings_are_checked(tmp_path):
    cfg = load_factorial_settings()
    assert cfg.benchmark == ("daily", "straight", "d07")
    bad = tmp_path / "c.yaml"
    bad.write_text("tod_factorial:\n  services: [hourly]\n", encoding="utf-8")
    with pytest.raises(LedgerError):
        load_factorial_settings(bad)


def test_the_part_is_opt_in(tmp_path):
    assert "tod_factorial" not in PARTS
    out = comparisons("TL", ["tod_factorial"], seed=SEED, draws=50)
    assert "tod_factorial" in out and "interactions" not in out
    client = AsgiClient(create_app(packs_dir=tmp_path))
    r = client.get("/api/v1/states/TL/comparisons?part=tod_factorial&draws=50")
    assert r.status == 200
    body = r.json()["tod_factorial"]
    assert body["benchmark"]["key"] == "daily|straight|d07"
    r = client.get("/api/v1/states/TL/comparisons?part=all&draws=50")
    assert "tod_factorial" not in r.json()
