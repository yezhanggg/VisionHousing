"""End-to-end: the static export of the demo state, in a headless browser (no server, no engine).

The demo state (``tests/fixtures/demo_state``, code ``DM``, synthetic) is exported twice with
the static exporter: once with the frozen law snapshot and once with the dated test law change
applied (``scripts/make_demo_fixture.py``, ``apply_law_change``). Each export is served as
plain files beside the static build. The checks:

- the Place screen of the tract whose sewer service area has unknown connection capacity
  shows the exported "no official sewer record" share (100%), never a 0% "inside a sewer
  service area" (the export itself holds the status "unknown");
- "What this law changes" for the parking reform shows Ridge city's original rule
  (a parking minimum of 2) in the first export and the revised rule (no minimum, effective
  2026-09-01) with the revised county result in the second, each exactly as exported.

Screenshots go to ``nopush/logs/shots/`` (git-ignored), or to ``$VH_SHOTS`` when set.
"""

from __future__ import annotations

import functools
import os
import shutil
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import test_shell as shell
from playwright.sync_api import expect
from test_shell import DESKTOP, SHOTS, QuietHandler, open_route, static_file, vite_build

import make_demo_fixture as demo
from visionhousing.export_static import export_static

browser = shell.browser

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "demo_state"
STATE = demo.STATE_CODE
OUT = Path(os.environ.get("VH_SHOTS") or SHOTS)
UNKNOWN_TRACT = f"{demo.STATE_FIPS}{demo.UNKNOWN_TRACT[0]}{(demo.UNKNOWN_TRACT[1] + 1) * 100:06d}"
RIDGE = demo.STATE_FIPS + "001"


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / f"a1-{name}.png"))


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    """Two static sites: the original demo state and the one with the law change."""
    built = vite_build("build:static", tmp_path_factory.mktemp("dist-demo"))
    shutil.rmtree(built / "data", ignore_errors=True)
    urls, servers = {}, []
    for which in ("original", "revised"):
        dist = tmp_path_factory.mktemp(f"site-{which}")
        shutil.copytree(built, dist, dirs_exist_ok=True)
        packs = tmp_path_factory.mktemp(f"packs-{which}")
        if which == "revised":
            demo.apply_law_change(FIX, packs / STATE)
        else:
            shutil.copytree(FIX, packs / STATE)
        export_static(STATE, dist / "data", packs_dir=packs, comparisons=False, law_changes=True)
        handler = functools.partial(QuietHandler, directory=str(dist))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        urls[which] = f"http://127.0.0.1:{server.server_address[1]}/"
    yield urls
    for server in servers:
        server.shutdown()
        server.server_close()


def _facts(profile: dict) -> dict:
    sec = next(s for s in profile["sections"] if s["key"] == "infrastructure")
    return {f["id"].split(".", 1)[1]: f for f in sec["facts"]}


def test_unknown_service_area_shows_as_unknown(browser, sites):
    base = sites["original"]
    profile = static_file(base, f"{STATE}/profiles/{UNKNOWN_TRACT}.json")
    facts = _facts(profile)
    assert "sewer_verified" not in facts
    assert facts["sewer_status"]["text"] == "unknown"
    context, page, watch = open_route(
        browser, base, f"#/place/{UNKNOWN_TRACT}", DESKTOP, query=f"v=1&s={STATE}"
    )
    expect(page.locator("#dock-left-title")).not_to_have_text(
        "What is this place like?", timeout=30000
    )
    body = page.locator("main#main")
    expect(body).to_contain_text(facts["sewer_unknown"]["label"], timeout=30000)
    expect(body).to_contain_text(facts["sewer_unknown"]["text"])
    expect(body).not_to_contain_text("Homes inside a mapped public sewer service area")
    shot(page, "unknown-service")
    assert watch.problems == []
    context.close()


def _ridge_card(browser, base: str, name: str):
    """Open "What this law changes" for parking and Ridge County's card; return its text."""
    data = static_file(base, f"{STATE}/law_changes/parking.json")["changes"]
    county = next(c for c in data["counties"] if c["fips"] == RIDGE)
    muni = next(m for m in county["municipalities"] if m["geoid"] == demo.LAW_CHANGE["geoid"])
    context, page, watch = open_route(
        browser, base, "#/reform/parking", DESKTOP, query=f"v=1&s={STATE}&r=parking"
    )
    toggle = page.get_by_test_id("law-mode")
    expect(toggle).to_be_visible(timeout=30000)
    toggle.click()
    expect(page.get_by_test_id("law-key")).to_be_visible(timeout=60000)
    page.get_by_role("button", name="County table").click()
    table = page.get_by_test_id("law-table")
    expect(table).to_be_visible(timeout=30000)
    table.get_by_role("button", name=county["name"]).click()
    card = page.get_by_test_id("law-card")
    expect(card).to_be_visible(timeout=30000)
    rule = card.locator(".law-muni", has_text=muni["name"]).locator(".law-muni-rule")
    expect(rule).to_have_text(muni["rule_text"])
    expect(card).to_contain_text(f"Effective {muni['dates']['effective_date']}")
    shot(page, name)
    text = card.inner_text()
    assert watch.problems == []
    context.close()
    return muni, county, text


def test_rule_change_shows_original_rule_and_revised_result(browser, sites):
    muni_a, county_a, card_a = _ridge_card(browser, sites["original"], "rule-original")
    muni_b, county_b, card_b = _ridge_card(browser, sites["revised"], "rule-revised")
    assert "single-family home: 2" in muni_a["rule_text"]
    assert "single-family home: 0" in muni_b["rule_text"]
    assert muni_b["dates"]["effective_date"] == demo.LAW_CHANGE["effective_date"]
    assert muni_a["rule_text"] not in card_b and muni_b["rule_text"] not in card_a
    # The revised county result is the one the second export holds, and it differs.
    assert county_a["units"]["shown"] != county_b["units"]["shown"]
    low_b = county_b["units"]["shown"]["p10"]
    assert low_b in card_b
