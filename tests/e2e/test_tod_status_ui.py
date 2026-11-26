"""End-to-end: the screens honour the transit status of the TOD run (real state packs).

- Wyoming (fewer than half of its bus stops measured): the Reform screen shows no statewide
  TOD range, anywhere a range would go (headline, chip, decision view, drivers): it shows
  "Statewide range not shown", the measured contribution and the completeness label, all the
  API's text; the small status table lists the state and every county; a county card shows
  the county's ladder with "unknown" and "measured: none" drawn differently; the lever,
  plausibility and dial cards give way to the rule;
- Wyoming, Compare: the TOD row and bar show no range, and the package total carries the
  API's package note;
- Wyoming, question box: the answer to "how many homes would TOD add" is the status text;
- Pennsylvania (partial but shown): the range is shown with the partial-footprint label.

These need the built Wyoming and Pennsylvania packs (``make data STATE=WY`` / ``STATE=PA``);
without them the tests are skipped. Screenshots go to ``nopush/logs/shots/`` (git-ignored),
or to ``$VH_SHOTS`` when set.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import test_shell as shell
import uvicorn
from playwright.sync_api import expect
from test_shell import DESKTOP, SHOTS, api, free_port, open_route, vite_build

from visionhousing.api import create_app
from visionhousing.engine.context import PACKS_DIR

browser = shell.browser
OUT = Path(os.environ.get("VH_SHOTS") or SHOTS)
RANGE = re.compile(r"\d[\d,.]*\s*(units|homes)?\s+to\s+\d")


def needs(*states: str) -> None:
    missing = [s for s in states if not (PACKS_DIR / s).is_dir()]
    if missing:
        pytest.skip(f"state pack not built: {', '.join(missing)}")


@pytest.fixture(scope="module")
def real_url(tmp_path_factory) -> Iterator[str]:
    """The live build, served by the FastAPI app on the built state packs."""
    needs("WY")
    dist = vite_build("build", tmp_path_factory.mktemp("dist"))
    app = create_app(web_dir=dist)
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", server_header=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "the test server did not start"
    yield f"http://127.0.0.1:{port}/"
    server.should_exit = True
    thread.join(timeout=10)


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / f"u17-{name}.png"), full_page=False)


def view_of(base: str, state: str, reforms: list[str]) -> dict:
    body = {"state": state, "reforms": reforms, "overrides": {}, "seed": 0, "explain": True}
    run = api(base, "run", {**body, "drivers": False})
    return run["status_views"]["tod"]


def wait_view(page) -> None:
    expect(page.locator(".status-line[role=status]")).to_have_count(0, timeout=300000)


def test_wyoming_reform_screen_shows_no_statewide_range(browser, real_url):
    v = view_of(real_url, "WY", ["tod"])
    assert v["shown"] is False
    context, page, watch = open_route(
        browser, real_url, "#/reform/tod", DESKTOP, query="v=1&s=WY&r=tod"
    )
    head = page.locator("#headline")
    expect(head.get_by_test_id("tod-hidden")).to_be_visible(timeout=300000)
    wait_view(page)
    expect(head.get_by_test_id("tod-label")).to_contain_text(v["label"])
    expect(head.get_by_test_id("tod-measured")).to_have_text(v["measured"]["text"])
    expect(head.get_by_test_id("tod-status-text")).to_have_text(v["text"])
    assert not RANGE.search(head.inner_text()), head.inner_text()
    chip = page.locator("#dock-left .dock-chip, #dock-left [class*=chip]").first
    if chip.count():
        assert not RANGE.search(chip.inner_text())
    # The decision view: the measured contribution, no range bar, no "how sure" readouts.
    decision = page.get_by_test_id("decision-view")
    expect(decision.get_by_test_id("tod-hidden-decision")).to_be_visible()
    assert not RANGE.search(decision.locator("[data-step=answer]").inner_text())
    expect(page.get_by_test_id("readouts")).to_have_count(0)
    expect(page.get_by_test_id("tod-hidden-cards-note")).to_contain_text(v["rule"])
    # The status table: the state row and every county, the server's words.
    table = page.get_by_test_id("tod-status-table")
    expect(table).to_be_visible()
    state_row = table.locator("[data-testid=tod-ladder][data-geoid=WY]")
    expect(state_row.locator(".tod-completeness")).to_have_text(v["state"]["completeness"])
    for cell in v["state"]["cells"]:
        step = state_row.locator(f".tod-step[data-stage={cell['stage']}]")
        expect(step).to_have_attribute("data-kind", cell["kind"])
        expect(step.locator(".tod-chip")).to_have_text(re.compile(re.escape(cell["word"])))
    table.locator("details.tod-counties summary").click()
    expect(table.locator("details.tod-counties tbody tr")).to_have_count(len(v["counties"]))
    shot(page, "wy-reform-tod")
    assert watch.problems == []
    context.close()


def test_wyoming_county_card_shows_the_ladder(browser, real_url):
    v = view_of(real_url, "WY", ["tod"])
    unknown = next(
        g for g, r in v["counties"].items() if any(c["kind"] == "unknown" for c in r["cells"])
    )
    context, page, watch = open_route(
        browser, real_url, "#/reform/tod", DESKTOP, query="v=1&s=WY&r=tod"
    )
    expect(page.locator("#headline").get_by_test_id("tod-hidden")).to_be_visible(timeout=300000)
    # Open the county from the county table (the map's text twin).
    page.get_by_role("button", name="County table").click()
    page.locator(f'.county-table tr[data-fips="{unknown}"] .link-btn').click()
    ladder = page.locator(f".county-panel [data-testid=tod-ladder][data-geoid='{unknown}']")
    expect(ladder).to_be_visible(timeout=120000)
    row = v["counties"][unknown]
    expect(ladder.locator(".tod-completeness")).to_have_text(row["completeness"])
    for cell in row["cells"]:
        step = ladder.locator(f".tod-step[data-stage={cell['stage']}]")
        expect(step).to_have_attribute("data-kind", cell["kind"])
        expect(step.locator(".tod-chip")).to_contain_text(cell["word"])
    # "Unknown" and "measured: none" never look the same.
    styles = page.evaluate(
        """() => {
          const make = (k) => {
            const s = document.createElement('span');
            s.className = 'tod-chip is-' + k;
            document.body.append(s);
            const c = getComputedStyle(s);
            const out = [c.borderStyle, c.borderWidth, c.backgroundImage, c.color].join('|');
            s.remove();
            return out;
          };
          return [make('unknown'), make('measured_zero')];
        }"""
    )
    assert styles[0] != styles[1]
    shot(page, "wy-county-ladder")
    assert watch.problems == []
    context.close()


def test_wyoming_compare_hides_the_tod_range(browser, real_url):
    bench = ["adu", "missing_middle", "tod", "parking", "fees"]
    v = view_of(real_url, "WY", bench)
    assert v["package_note"]
    context, page, watch = open_route(browser, real_url, "compare", DESKTOP, query="v=1&s=WY")
    expect(page.get_by_test_id("tod-package-note")).to_have_text(v["package_note"], timeout=300000)
    wait_view(page)
    bar = page.locator(".bar-row[data-key=tod]")
    expect(bar).to_have_attribute("data-hidden", "true")
    expect(bar.locator(".range")).to_have_count(0)
    expect(bar).to_contain_text(v["measured"]["text"])
    row = page.locator("tr[data-key=tod]")
    expect(row).to_contain_text(v["not_shown"])
    expect(row).to_contain_text(v["label"])
    assert not RANGE.search(row.inner_text())
    shot(page, "wy-compare")
    assert watch.problems == []
    context.close()


def test_wyoming_question_box_gives_the_status_text(browser, real_url):
    v = view_of(real_url, "WY", ["tod"])
    context, page, watch = open_route(browser, real_url, "state", DESKTOP, query="v=1&s=WY&r=tod")
    toggle = page.locator(".ask .chat-toggle")
    toggle.focus()
    page.keyboard.press("Enter")
    box = page.locator("#chat-input")
    box.fill("How many homes would transit-oriented density add?")
    box.press("Enter")
    card = page.get_by_test_id("ask-answer")
    expect(card).to_be_visible(timeout=300000)
    lines = card.get_by_test_id("ask-answer-text").locator("p").all_inner_texts()
    assert lines == [v["text"]]
    shot(page, "wy-question-box")
    assert watch.problems == []
    context.close()


def test_pennsylvania_shows_the_range_with_the_partial_label(browser, real_url):
    needs("PA")
    v = view_of(real_url, "PA", ["tod"])
    assert v["shown"] is True and v["partial"] is True
    context, page, watch = open_route(
        browser, real_url, "#/reform/tod", DESKTOP, query="v=1&s=PA&r=tod"
    )
    head = page.locator("#headline")
    expect(head.locator(".lead-number")).to_be_visible(timeout=300000)
    wait_view(page)
    expect(head.get_by_test_id("tod-hidden")).to_have_count(0)
    assert RANGE.search(head.locator(".lead-number").inner_text())
    expect(head.get_by_test_id("tod-partial-label")).to_contain_text(v["label"])
    expect(page.get_by_test_id("tod-status-table")).to_be_visible()
    shot(page, "pa-reform-tod")
    assert watch.problems == []
    context.close()
