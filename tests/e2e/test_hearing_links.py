"""End-to-end: baseline card, headline scope, share links, profile coverage and the hearing brief.

On Testland, live (the back end) and static (the exported files):

- baseline card: the State screen's folded card and the Method screen's card show the rows the
  API sent (``baseline_card.view``), word for word. Testland has no card, so the test answers
  the summary with a real state's card from ``api.baseline_view``; without it the screen says
  plainly that the state has none;
- headline scope: with ADU on, the note says what the headline counts and shows the
  Census-comparable range of the run's own accounting record;
- share links: a link with the retired ``p`` preset reopens the same scenario, says what was
  left out, and its canonical address round-trips; a link from a newer version says so instead
  of quietly opening the default view; on the static site a custom scenario gets its own
  "not on this site" status (no number) and one press switches to the default assumptions;
- empty profile sections: each is listed with the coverage text the server generated
  (``web/js/content/coverage.json``) instead of an empty block;
- hearing brief: the button opens a page that holds the run id of the same run and its
  headline numbers, live and static; printed, it is two pages.

Screenshots go to ``nopush/logs/shots/`` (git-ignored), or to ``$VH_SHOTS`` when set.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

import test_shell as shell
from playwright.sync_api import expect
from test_shell import DESKTOP, SHOTS, api, open_route, run_of, static_file, wait_results

from visionhousing.api.answers import run_id
from visionhousing.api.baseline_view import view_for
from visionhousing.profile.coverage import coverage_table

base_url = shell.base_url
static_url = shell.static_url
browser = shell.browser

OUT = Path(os.environ.get("VH_SHOTS") or SHOTS)
CARD = json.loads(json.dumps(view_for("PA", {"p50": 151882.0})))
"""A real state's card, answered for Testland (which has none) so the screen can be checked."""


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / f"u16-{name}.png"))


def answer_with_card(page, card=CARD) -> None:
    def handle(route):
        reply = route.fetch()
        body = reply.json()
        route.fulfill(response=reply, json={**body, "baseline_card": card})

    page.route(re.compile(r".*/api/v1/states/TL/summary$"), handle)


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=120) as reply:  # noqa: S310 (local test server)
        return reply.read().decode("utf-8")


def test_baseline_card_rows_equal_the_api(browser, base_url):
    assert api(base_url, "states/TL/summary")["baseline_card"] is None
    want = [r["text"] for r in CARD["view"]["rows"]]
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu")
    answer_with_card(page)
    page.reload()
    wait_results(page)
    card = page.get_by_test_id("baseline-card")
    expect(card).to_be_visible(timeout=30000)
    head = card.locator(":scope > summary")
    expect(head).to_contain_text("Reconciliation: matched")
    head.click()
    expect(card.get_by_test_id("bc-text")).to_have_text(want)
    expect(card.locator("[data-row=baseline_pa_window_2022_2025] .bc-tag")).to_have_text(
        "disclosed sensitivity; record only"
    )
    card.locator("[data-row=reconciliation]").scroll_into_view_if_needed()
    shot(page, "state-baseline-card")

    page.goto(f"{base_url}#/method?v=1&s=TL")
    method = page.locator("#baseline-card")
    expect(method.get_by_test_id("bc-text")).to_have_text(want, timeout=30000)
    method.locator("[data-row=baseline_bridge_nowcast_2026] summary").click()
    expect(method.locator("[data-row=baseline_bridge_nowcast_2026] .bc-formula")).to_have_text(
        CARD["view"]["rows"][5]["formula"]
    )
    method.scroll_into_view_if_needed()
    shot(page, "method-baseline-card")
    assert watch.problems == []
    context.close()


def test_state_without_a_card_says_so(browser, base_url):
    context, page, watch = open_route(browser, base_url, "method", DESKTOP, query="v=1&s=TL")
    expect(page.locator("#baseline-card")).to_contain_text(
        "No baseline card for this state", timeout=30000
    )
    assert watch.problems == []
    context.close()


def test_scope_note_shows_the_census_comparable_record(browser, base_url):
    run = run_of(base_url, ["adu"])
    head = run["reform_results"]["adu"]["accounting"]["headline"]
    comp = run["explanation"]["numbers"]["reform_results.adu.accounting.records.comparable"]
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu")
    wait_results(page)
    note = page.get_by_test_id("scope-note")
    expect(note).to_contain_text(f"headline counts {head['scope']}")
    expect(note).to_contain_text(
        f"Census-comparable: {comp['shown']['p10']} to {comp['shown']['p90']} "
        f"(middle {comp['shown']['p50']})"
    )
    assert watch.problems == []
    context.close()


def test_share_link_round_trips_and_says_what_it_dropped(browser, base_url):
    old = "v=1&s=TL&r=adu&a=adu.takeup_feasible_annual~h&p=policymaker"
    context, page, watch = open_route(
        browser, base_url, "state", DESKTOP, query=old,
        permissions=["clipboard-read", "clipboard-write"],
    )  # fmt: skip
    wait_results(page)
    note = page.get_by_test_id("link-dropped")
    expect(note).to_contain_text("view preset (no longer used")
    expect(page).to_have_url(re.compile(r"\?v=1&s=TL&r=adu&a=adu\.takeup_feasible_annual~h$"))
    shot(page, "link-dropped")
    page.get_by_role("group", name="Slides, export and share").get_by_role(
        "button", name="Share link"
    ).click()
    shared = page.evaluate("navigator.clipboard.readText()")
    assert shared == page.url and "p=" not in shared

    again = context.new_page()
    again.goto(shared)
    wait_results(again)
    expect(again).to_have_url(shared)
    expect(again.get_by_test_id("link-dropped")).to_have_count(0)
    expect(again.locator('.switch[aria-pressed="true"]')).to_have_count(1)

    again.goto(f"{base_url}#/state?v=9&s=TL&r=adu")
    expect(again.get_by_test_id("link-dropped")).to_contain_text("unknown link version 9")
    assert watch.problems == []
    context.close()


def test_static_custom_scenario_has_its_own_status(browser, static_url):
    query = "v=1&s=TL&r=adu&a=adu.takeup_feasible_annual~h"
    context, page, watch = open_route(browser, static_url, "state", DESKTOP, query=query)
    custom = page.get_by_test_id("link-custom")
    expect(custom).to_contain_text("Custom scenario, not on this site")
    expect(custom).to_contain_text("adu.takeup_feasible_annual")
    expect(page.locator(".dock-right .status-line.is-local").first).to_be_visible(timeout=15000)
    expect(page.locator(".lead-number")).to_have_count(0)
    brief = page.get_by_test_id("hearing-brief")
    expect(brief).to_have_attribute("aria-disabled", "true")
    expect(page.locator("#hearing-why")).to_contain_text("needs the local app")
    shot(page, "static-custom")

    custom.get_by_role("button", name="Use the default assumptions instead").click()
    expect(custom).to_have_count(0)
    expect(page).to_have_url(re.compile(r"\?v=1&s=TL&r=adu$"))
    wait_results(page)
    expect(brief).to_have_attribute("href", re.compile(r"data/TL/exports/adu\.hearing\.html$"))
    idx = static_file(static_url, "TL/index.json")
    assert idx["schema_version"] == 2 and "adu" in idx["hearing_briefs"]
    text = fetch_text(f"{static_url}data/TL/exports/adu.hearing.html")
    doc = context.new_page()
    doc_watch = shell.Watch(doc, static_url)
    doc.goto(f"{static_url}data/TL/exports/adu.hearing.html")
    assert doc.evaluate("getComputedStyle(document.querySelector('h2')).borderTopStyle") == "solid"
    assert doc_watch.problems == []
    run = static_file(static_url, "TL/runs/adu.json")
    assert run_id(run, "adu") in text
    for n in run["explanation"]["headline"]["numbers"]:
        assert f"{n['shown']['p10']} to {n['shown']['p90']}" in text
    assert watch.problems == []
    context.close()


def test_live_hearing_brief_matches_the_api_and_prints_on_two_pages(browser, base_url):
    for reforms in (["adu"], ["adu", "missing_middle", "tod", "parking", "fees"]):
        context, page, watch = open_route(
            browser, base_url, "state", DESKTOP, query=f"v=1&s=TL&r={','.join(reforms)}"
        )
        wait_results(page)
        brief = page.get_by_test_id("hearing-brief")
        expect(brief).to_have_attribute("href", re.compile(r"/api/v1/export/hearing\?s=TL&r="))
        url = brief.get_attribute("href")
        url = url if url.startswith("http") else f"{base_url.rstrip('/')}{url}"
        run = run_of(base_url, reforms, drivers=len(reforms) == 1)
        key = "adu" if len(reforms) == 1 else "package"
        text = fetch_text(url)
        assert run_id(run, key) in text
        for n in run["explanation"]["headline"]["numbers"]:
            assert f"{n['shown']['p10']} to {n['shown']['p90']}" in text
            assert n["shown"]["p50"] in text
        doc = context.new_page()
        doc_watch = shell.Watch(doc, base_url)
        doc.goto(url)
        expect(doc.locator("h1")).to_contain_text("Hearing brief")
        # Its style rules load under the app's policy (a linked file, no inline style).
        assert (
            doc.evaluate("getComputedStyle(document.querySelector('h2')).borderTopStyle") == "solid"
        )
        assert doc_watch.problems == []
        doc.screenshot(path=str(OUT / f"u16-hearing-{key}.png"), full_page=True)
        pdf = doc.pdf(format="Letter", print_background=True)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"u16-hearing-{key}.pdf").write_bytes(pdf)
        pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
        assert pages == 2, f"{key}: the hearing brief printed on {pages} pages"
        assert watch.problems == []
        context.close()


def test_empty_profile_sections_show_coverage_text(browser, base_url):
    profile = api(base_url, "profile/TL/99001")
    empty = [
        s["key"]
        for s in profile["sections"]
        if s["status"] != "ok" or not (s["facts"] or s["charts"])
    ]
    assert empty, "Testland should have sections with no data"
    table = coverage_table()["sections"]
    context, page, watch = open_route(browser, base_url, "#/place/99001", DESKTOP, query="v=1&s=TL")
    block = page.get_by_test_id("not-measured")
    expect(block).to_be_visible(timeout=30000)
    items = block.locator(".coverage-item")
    expect(items).to_have_count(len(empty))
    for i, key in enumerate(empty):
        item = items.nth(i)
        expect(item).to_have_attribute("data-section", key)
        expect(item.get_by_test_id("coverage-text")).to_have_text(table[key]["text"])
    block.scroll_into_view_if_needed()
    shot(page, "place-coverage")
    assert watch.problems == []
    context.close()
