"""End-to-end: the front-end shell in a real browser (Chromium through Playwright).

It serves ``web/`` with a small static server in a background thread, opens every route on a
desktop and a phone viewport, and checks that:
- there are no console errors, page errors or requests to other hosts;
- the top bar, the four tabs and the three buttons are there and reachable by keyboard;
- the slides move with the arrow keys and print one slide per page;
- the Export menu, the glossary popover and Share link work by keyboard;
- the scenario codec keeps good input and drops bad input;
- no value is typed by hand: every value slot shows "—" until the engine is connected.

Screenshots go to ``nopush/logs/shots/`` (git-ignored) so the layout can be reviewed.
The browser libraries must be vendored first (``make setup``).
"""

from __future__ import annotations

import functools
import re
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
SHOTS = ROOT / "nopush" / "logs" / "shots"
SCENARIO = "v=1&s=PA&r=adu,fees"
ROUTES = {
    "state": "#/state",
    "reform": "#/reform/adu",
    "compare": "#/compare",
    "method": "#/method",
    "slides": "#/slides",
}
TABS = ["State", "Reform", "Compare", "Method & data"]
BUTTONS = ["Slides", "Export", "Share link"]
DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
# Software WebGL so MapLibre can draw in a headless browser and in CI.
GL_ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]


class QuietHandler(SimpleHTTPRequestHandler):
    """Static file handler with explicit module MIME types and no request log."""

    extensions_map = {  # noqa: RUF012 (class attribute read by the base class)
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".css": "text/css",
        ".json": "application/json",
    }

    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    assert (WEB / "vendor" / "manifest.json").exists(), "browser libraries missing: make setup"
    handler = functools.partial(QuietHandler, directory=str(WEB))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=GL_ARGS)
        yield browser
        browser.close()


class Watch:
    """Collects console errors, page errors and off-site requests for one page."""

    def __init__(self, page: Page, base: str):
        self.problems: list[str] = []
        page.on(
            "console",
            lambda m: m.type == "error" and self.problems.append(f"console: {m.text}"),
        )
        page.on("pageerror", lambda e: self.problems.append(f"page error: {e}"))
        page.on(
            "request",
            lambda r: (
                not r.url.startswith((base, "data:", "blob:"))
                and self.problems.append(f"off-site request: {r.url}")
            ),
        )
        page.on("requestfailed", lambda r: self.problems.append(f"failed: {r.url}"))


def open_route(browser: Browser, base: str, route: str, viewport: dict, **ctx):
    context = browser.new_context(viewport=viewport, **ctx)
    page = context.new_page()
    watch = Watch(page, base)
    page.goto(f"{base}{ROUTES.get(route, route)}?{SCENARIO}")
    expect(page.locator("header.topbar")).to_be_visible()
    expect(page.locator("main#main")).to_be_visible()
    if route == "state":
        expect(page.locator(".map-canvas canvas")).to_have_count(1, timeout=15000)
        expect(page.locator(".map-empty-title")).not_to_have_text("Loading the map…")
    return context, page, watch


def shoot(page: Page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(250)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


@pytest.mark.parametrize("route", list(ROUTES))
def test_route_loads_cleanly_on_desktop(browser, base_url, route):
    context, page, watch = open_route(browser, base_url, route, DESKTOP)
    tabs = page.get_by_role("navigation", name="Screens").get_by_role("link")
    expect(tabs).to_have_text(TABS)
    for label in BUTTONS:
        expect(page.locator(".actions").get_by_text(label, exact=True)).to_be_visible()
    current = dict(zip(ROUTES, TABS, strict=False))
    if route in current:
        expect(page.locator('.tab[aria-current="page"]')).to_have_text(current[route])
    # No typed numbers: every value slot is an em dash until the engine sends results.
    values = page.locator(".value").all_inner_texts()
    assert all(v.strip() == "—" for v in values), values
    if route == "state":
        expect(page.locator(".map-empty-title")).to_have_text("County map for Pennsylvania")
    shoot(page, f"desktop-{route}")
    assert watch.problems == []
    context.close()


@pytest.mark.parametrize("route", list(ROUTES))
def test_route_loads_cleanly_on_phone(browser, base_url, route):
    context, page, watch = open_route(
        browser, base_url, route, PHONE, is_mobile=True, has_touch=True, device_scale_factor=2
    )
    expect(page.locator(".tabs")).to_be_hidden()
    expect(page.get_by_role("button", name="Menu")).to_be_visible()
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= PHONE["width"], f"horizontal scroll: {width}px"
    shoot(page, f"phone-{route}")
    if route == "state":
        page.get_by_role("button", name="Show all controls").click()
        shoot(page, "phone-state-sheet-open")
        page.get_by_role("button", name="Show the map").click()
        page.get_by_role("button", name="Menu").click()
        menu = page.locator("#mobile-menu")
        expect(menu.get_by_role("navigation").get_by_role("link")).to_have_text(TABS)
        expect(menu.get_by_text("Table (CSV)")).to_be_visible()
        shoot(page, "phone-menu")
        page.keyboard.press("Escape")
        expect(menu).to_have_count(0)
    assert watch.problems == []
    context.close()


def test_top_bar_is_keyboard_reachable_in_order(browser, base_url):
    context, page, watch = open_route(browser, base_url, "compare", DESKTOP)
    seen = []
    for _ in range(12):
        page.keyboard.press("Tab")
        seen.append(page.evaluate("document.activeElement.innerText.trim()"))
    order = [s for s in seen if s in ["VisionHousing", *TABS, *BUTTONS]]
    assert order[: 1 + len(TABS) + len(BUTTONS)] == ["VisionHousing", *TABS, *BUTTONS], seen
    # A focused tab shows a visible focus ring.
    page.get_by_role("link", name="Reform", exact=True).focus()
    ring = page.evaluate("getComputedStyle(document.activeElement).boxShadow")
    assert ring and ring != "none"
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"#/reform/adu\?v=1&s=PA&r=adu,fees$"))
    assert watch.problems == []
    context.close()


def test_slides_move_with_arrow_keys(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    page.get_by_role("link", name="Slides", exact=True).click()
    count = page.locator(".slides-count")
    expect(count).to_have_text("Slide 1 of 3")
    page.keyboard.press("ArrowRight")
    expect(count).to_have_text("Slide 2 of 3")
    shoot(page, "desktop-slides-2")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    expect(count).to_have_text("Slide 3 of 3")
    shoot(page, "desktop-slides-3")
    page.keyboard.press("ArrowLeft")
    expect(count).to_have_text("Slide 2 of 3")
    page.keyboard.press("Home")
    expect(count).to_have_text("Slide 1 of 3")
    expect(page.locator(".slide-frame:not(.is-off)")).to_have_count(1)
    page.get_by_role("button", name="Read as a page").click()
    expect(page.locator(".slides-text-item")).to_have_count(3)
    shoot(page, "desktop-slides-text")
    page.keyboard.press("Escape")
    expect(page).to_have_url(re.compile(r"#/state\?"))
    assert watch.problems == []
    context.close()


def test_slides_print_one_slide_per_page(browser, base_url):
    context, page, watch = open_route(browser, base_url, "slides", DESKTOP)
    pdf = page.pdf(width="13.333in", height="7.5in", print_background=True)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    assert pages == 3
    assert watch.problems == []
    context.close()


def test_share_link_copies_the_address(browser, base_url):
    context, page, watch = open_route(
        browser, base_url, "reform", DESKTOP, permissions=["clipboard-read", "clipboard-write"]
    )
    page.get_by_role("button", name="Share link").click()
    expect(page.get_by_role("status")).to_have_text("Link copied")
    assert page.evaluate("navigator.clipboard.readText()") == page.url
    assert watch.problems == []
    context.close()


def test_export_menu_works_by_keyboard(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    button = page.get_by_role("button", name="Export")
    button.focus()
    page.keyboard.press("Enter")
    items = page.get_by_role("menuitem")
    expect(items).to_have_count(3)
    labels = ["Brief (PDF)", "Slides (PDF)", "Table (CSV)"]
    expect(items.locator(".menu-label")).to_have_text(labels)
    expect(items.first).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(items.nth(1)).to_be_focused()
    shoot(page, "desktop-export-menu")
    page.keyboard.press("Escape")
    expect(items).to_have_count(0)
    expect(button).to_be_focused()
    assert watch.problems == []
    context.close()


def test_glossary_and_explain_popovers_work_by_keyboard(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    term = page.locator(".card-results .term").first
    term.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("today's rules")
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(term).to_be_focused()
    explain = page.get_by_role("button", name="Explain: Added units")
    explain.focus()
    page.keyboard.press("Space")
    expect(dialog).to_contain_text("Formula")
    expect(dialog).to_contain_text("Filled in by the engine")
    shoot(page, "desktop-explain")
    page.keyboard.press("Escape")
    assert watch.problems == []
    context.close()


def test_reform_switch_writes_the_scenario_to_the_address(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    switch = page.get_by_role("button", name=re.compile("^Parking reduction"))
    expect(switch).to_have_attribute("aria-pressed", "false")
    switch.click()
    expect(switch).to_have_attribute("aria-pressed", "true")
    expect(page).to_have_url(re.compile(r"r=adu,parking,fees$"))
    page.get_by_role("button", name="Back to baseline").click()
    expect(page).to_have_url(re.compile(r"#/state\?v=1&s=PA$"))
    assert watch.problems == []
    context.close()


CODEC_CHECK = """async () => {
  const c = await import('./js/state/codec.js');
  const known = { states: ['PA', 'NY'], reforms: ['adu', 'middle', 'fees'] };
  const out = {};
  const link = 'v=1&s=NY&r=fees,adu&a=adu.takeup~h,fees.cut~0.5&p=policymaker';
  const good = c.decodeScenario(link, known);
  out.good = good.scenario;
  out.goodDropped = good.dropped;
  out.encoded = c.encodeScenario(good.scenario, known);
  out.bad = c.decodeScenario('s=XX&r=adu,nope&a=Bad~h,ok.key~1e99,x~l~h&p=$$&z=1', known);
  out.future = c.decodeScenario('v=9&s=NY', known).scenario.state;
  out.long = c.decodeScenario('r=' + 'adu,'.repeat(600), known).dropped;
  out.route = c.parseRoute('#/reform/adu?v=1');
  out.unknown = c.parseRoute('#/nowhere').name;
  return out;
}"""


def test_scenario_codec_keeps_good_input_and_drops_bad_input(browser, base_url):
    context, page, watch = open_route(browser, base_url, "method", DESKTOP)
    out = page.evaluate(CODEC_CHECK)
    assert out["good"] == {
        "state": "NY",
        "reforms": ["adu", "fees"],
        "overrides": {"adu.takeup": "h", "fees.cut": "0.5"},
        "preset": "policymaker",
    }
    assert out["goodDropped"] == []
    assert out["encoded"] == "v=1&s=NY&r=adu,fees&a=adu.takeup~h,fees.cut~0.5&p=policymaker"
    bad = out["bad"]
    assert bad["scenario"] == {"state": "PA", "reforms": ["adu"], "overrides": {}, "preset": ""}
    assert len(bad["dropped"]) == 7
    assert out["future"] == "PA"
    assert out["long"] == ["the link is too long"]
    assert out["route"] == {"name": "reform", "param": "adu", "query": "v=1", "known": True}
    assert out["unknown"] == "state"
    assert watch.problems == []
    context.close()
