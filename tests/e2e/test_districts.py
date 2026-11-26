"""End-to-end: the district layer, "Find my district" and the one-page district brief.

On Testland's made-up districts (bands of block points; the server draws their outlines as
rectangles) it checks, in a real browser (Chromium through Playwright, headless):

- the "Districts" button turns the layer on, the House / Senate switch picks the chamber,
  and the map key shows the district classes and the split-share caveat;
- pointing at a district shows its card with the server's range text; clicking it opens the
  district panel in place of the right panel, and every range in the panel is the text the
  API sent for the same scenario (no number is made in the browser);
- the caveat note shows exactly for the districts the server flags;
- "Find my district" works by keyboard: a district number, then a county name (the districts
  holding its homes), Up / Down and Enter, and Esc closes the panel;
- "Print / PDF" prints the panel alone on one US Letter page;
- in 3D each district stands as one column;
- the static site with no district files says the view needs the local app.

Screenshots go to ``nopush/logs/shots/`` (git-ignored), or to ``$VH_SHOTS`` when set.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import test_shell as shell
from playwright.sync_api import expect
from test_shell import DESKTOP, SHOTS, api, open_route, static_file, wait_results

# The shell tests' fixtures: one build and one server per module, the same headless browser.
base_url = shell.base_url
static_url = shell.static_url
browser = shell.browser

QUERY = "v=1&s=TL&r=adu,fees"
OUT = Path(os.environ.get("VH_SHOTS") or SHOTS)


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / f"district-{name}.png"))


def districts_of(base: str, chamber: str, reforms: str = "adu,fees") -> dict:
    return api(base, f"states/TL/districts?chamber={chamber}&s=TL&r={reforms}")


def turn_on(page, chamber: str = "House") -> None:
    page.get_by_role("button", name="Districts").click()
    page.get_by_role("group", name="Chamber").get_by_role("button", name=chamber).click()
    expect(page.locator(".district-key .key-list")).to_be_visible(timeout=30000)
    page.wait_for_timeout(500)


def layer_state(page) -> dict:
    """What the district layer drew, as it writes it on the map once the map rests."""
    return page.locator(".map-canvas").evaluate(
        """(el) => ({ view: el.dataset.districts || '',
            points: JSON.parse(el.dataset.districtPoints || '{}'),
            columns: Number(el.dataset.districtColumns || 0),
            layers: Object.fromEntries((el.dataset.districtLayers || '').split(',').filter(Boolean)
              .map((x) => x.split(':'))) })"""
    )


def centre_of(page, district: str) -> tuple[float, float]:
    """Screen point inside a district (the middle of its outline's box)."""
    page.wait_for_function(
        """(id) => JSON.parse(
            document.querySelector('.map-canvas').dataset.districtPoints || '{}')[id]""",
        arg=district,
        timeout=30000,
    )
    x, y = layer_state(page)["points"][district]
    box = page.locator(".map-canvas").bounding_box()
    return box["x"] + x, box["y"] + y


def open_district(page, district: dict) -> None:
    """Open a district through "Find my district" (its number, then Enter)."""
    box = page.get_by_role("combobox", name=re.compile("Find a"))
    box.fill("")
    box.type(district["id"])
    page.keyboard.press("Enter")
    expect(page.locator("#district-dock-title")).to_have_text(district["name"])


def test_toggle_chamber_click_panel_and_print(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, QUERY)
    wait_results(page)
    turn_on(page, "Senate")
    upper = districts_of(base_url, "upper")
    expect(page.locator(".district-key .key-hint").last).to_contain_text(
        f"{upper['district_count']} Senate districts"
    )
    turn_on_house = page.get_by_role("group", name="Chamber").get_by_role("button", name="House")
    turn_on_house.click()
    lower = districts_of(base_url, "lower")
    expect(page.locator(".district-key .key-hint").last).to_contain_text(
        f"{lower['district_count']} House districts", timeout=30000
    )
    for c in lower["classes"]["classes"]:
        expect(page.locator(".district-key")).to_contain_text(c["label"])
    page.wait_for_timeout(800)
    state = layer_state(page)
    assert state["view"] == "2d" and len(state["points"]) == lower["district_count"]
    assert state["layers"]["dist-fill"] == "visible" and state["layers"]["dist-col-core"] == "none"
    shot(page, "layer-2d")

    flagged = next(d for d in lower["districts"] if d["caveat"])
    x, y = centre_of(page, flagged["id"])
    page.mouse.move(x, y)
    card = page.locator(".district-hover")
    expect(card).to_have_class(re.compile("is-on"))
    rng = flagged["headline"]["homes"]["shown"]
    expect(card).to_contain_text(flagged["name"])
    expect(card).to_contain_text(f"{rng['p10']} to {rng['p90']}")
    expect(card).to_contain_text("Split-share caveat")
    shot(page, "hover")

    page.mouse.click(x, y)
    panel = page.locator(".dock-right .district-brief")
    expect(panel).to_be_visible()
    expect(page.locator(".dock-right:visible")).to_have_count(1)
    expect(page.locator("#district-dock-title")).to_have_text(flagged["name"])
    expect(panel.locator(".district-big")).to_have_text(f"{rng['p10']} to {rng['p90']}")
    base = flagged["baseline"]["shown"]
    expect(panel.locator(".district-table tr.is-base td").first).to_have_text(
        f"{base['p10']} to {base['p90']}"
    )
    for key in ("adu", "fees"):
        s = flagged["reforms"][key]["shown"]
        expect(panel.locator(".district-table")).to_contain_text(f"{s['p10']} to {s['p90']}")
    expect(panel.locator(".district-caveat")).to_contain_text(flagged["caveat_text"])
    for item in lower["limits"]["districts"]["items"]:
        expect(panel).to_contain_text(item)
    expect(panel).to_contain_text(lower["vintage"]["plans"])
    # Two reforms: the drivers load on request, each the statewide tornado's first sentence.
    panel.get_by_role("button", name=re.compile("Show each reform's top driver")).click()
    for key in ("adu", "fees"):
        run = api(
            base_url,
            "run",
            {
                "state": "TL",
                "reforms": [key],
                "overrides": {},
                "seed": 0,
                "explain": True,
                "drivers": True,
            },
        )
        text = run["explanation"]["drivers"][key]["items"][0]["text"]["text"]
        expect(panel.locator(".district-drivers")).to_contain_text(text, timeout=60000)
    shot(page, "panel")

    # A district under the caveat line shows no caveat note.
    calm = next((d for d in lower["districts"] if not d["caveat"]), None)
    if calm:
        open_district(page, calm)
        expect(panel.locator(".district-caveat")).to_have_count(0)
        open_district(page, flagged)

    # Print / PDF: the panel alone, on one page.
    page.evaluate("() => { window.print = () => { window.__printed = true; }; }")
    page.get_by_role("button", name="Print / PDF").click()
    page.wait_for_function("() => window.__printed === true")
    assert page.evaluate("() => document.documentElement.dataset.print") == "district"
    page.emulate_media(media="print")
    expect(page.locator(".district-print-head")).to_be_visible()
    expect(page.locator(".dock-left")).to_be_hidden()
    shot(page, "print-view")
    pdf = page.pdf(prefer_css_page_size=True, print_background=True)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    (OUT / "district-brief.pdf").write_bytes(pdf)
    assert pages == 1, f"the district brief printed on {pages} pages"
    page.emulate_media(media="screen")
    page.evaluate("() => window.dispatchEvent(new Event('afterprint'))")
    assert page.evaluate("() => document.documentElement.dataset.print") is None

    # Esc closes the district; the mode's own right panel comes back.
    page.locator(".district-brief").focus()
    page.keyboard.press("Escape")
    expect(page.locator(".district-brief")).to_have_count(0)
    expect(page.locator(".dock-right:visible")).to_have_count(1)
    assert watch.problems == []
    context.close()


def test_find_my_district_by_keyboard(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, QUERY)
    wait_results(page)
    turn_on(page, "House")
    lower = districts_of(base_url, "lower")
    page.locator("main#main").focus()
    page.keyboard.press("/")
    box = page.get_by_role("combobox", name=re.compile("Find a house district"))
    expect(box).to_be_focused()
    # By number.
    target = lower["districts"][4]
    box.type(target["id"])
    expect(page.get_by_role("listbox")).to_be_visible()
    expect(box).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Enter")
    expect(page.locator("#district-dock-title")).to_have_text(target["name"])
    # By county: the districts holding the county's homes, by the server's county weights.
    touch = target["counties_touched"][0]
    holding = [
        d
        for d in lower["districts"]
        if any(c["fips"] == touch["fips"] for c in d["counties_touched"])
    ]
    page.keyboard.press("/")
    expect(box).to_be_focused()
    box.type(touch["name"])
    opts = page.get_by_role("listbox", name="Matching districts").get_by_role("option")
    expect(opts).to_have_count(len(holding))
    expect(opts.first).to_contain_text(touch["name"])
    page.keyboard.press("ArrowDown")
    second = opts.nth(1 % len(holding))
    expect(second).to_have_attribute("aria-selected", "true")
    name = second.locator(".district-find-name").inner_text()
    shot(page, "find-county")
    page.keyboard.press("Enter")
    expect(page.locator("#district-dock-title")).to_have_text(name)
    # Nothing matches: the list says so.
    page.keyboard.press("/")
    box.type("zzz")
    expect(page.get_by_role("listbox").get_by_role("option")).to_have_text(
        re.compile("No house district matches")
    )
    page.keyboard.press("Escape")
    expect(page.get_by_role("listbox")).to_have_count(0)
    assert watch.problems == []
    context.close()


def test_3d_view_stands_one_column_per_district(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, QUERY)
    wait_results(page)
    turn_on(page, "House")
    page.get_by_role("button", name="3D").click()
    lower = districts_of(base_url, "lower")
    page.wait_for_function(
        "() => document.querySelector('.map-canvas').dataset.districts === '3d'", timeout=30000
    )
    page.wait_for_timeout(1500)
    state = layer_state(page)
    assert state["columns"] == len(lower["districts"])
    assert state["layers"]["dist-col-core"] == "visible" and state["layers"]["dist-fill"] == "none"
    # The county and tract columns step aside while the district columns stand.
    assert state["layers"]["col-county-core"] in ("none", "absent")
    assert state["layers"]["col-tract-core"] in ("none", "absent")
    shot(page, "layer-3d")
    page.get_by_role("button", name="Districts").click()
    page.wait_for_function(
        "() => document.querySelector('.map-canvas').dataset.districts === 'off'", timeout=30000
    )
    assert watch.problems == []
    context.close()


def test_static_site_reads_the_saved_districts(browser, static_url):
    assert static_file(static_url, "TL/index.json")["districts"] is True
    context, page, watch = open_route(browser, static_url, "state", DESKTOP, "v=1&s=TL&r=adu")
    wait_results(page)
    page.get_by_role("button", name="Districts").click()
    expect(page.locator(".district-key .key-list")).to_be_visible(timeout=30000)
    expect(page.locator(".district-key .district-note")).to_have_count(0)
    shot(page, "static-files")
    assert watch.problems == []
    context.close()


def test_static_site_says_the_district_view_needs_the_local_app(browser, static_url):
    index = static_file(static_url, "TL/index.json")
    index.pop("districts", None)
    body = json.dumps(index)
    context, page, watch = open_route(browser, static_url, "state", DESKTOP, "v=1&s=TL&r=adu")
    page.route("**/data/TL/index.json", lambda route: route.fulfill(
        status=200, content_type="application/json", body=body))  # fmt: skip
    page.reload()
    wait_results(page)
    page.get_by_role("button", name="Districts").click()
    expect(page.locator(".district-key .district-note")).to_have_text(
        "The district view needs the local app (make app)."
    )
    shot(page, "static")
    # The note is the page's own answer, not a failed request.
    assert [p for p in watch.problems if "districts" not in p] == []
    context.close()


def test_brief_with_every_reform_prints_on_one_page(browser, base_url):
    """The longest brief (all five reforms, each with its limits and driver) still fits a page."""
    query = "v=1&s=TL&r=adu,missing_middle,tod,parking,fees"
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query)
    wait_results(page)
    turn_on(page, "Senate")
    upper = districts_of(base_url, "upper", "adu,missing_middle,tod,parking,fees")
    open_district(page, upper["districts"][0])
    panel = page.locator(".district-brief")
    panel.get_by_role("button", name=re.compile("Show each reform's top driver")).click()
    expect(panel.locator(".district-driver:not(.is-none)")).to_have_count(5, timeout=120000)
    page.evaluate("() => { window.print = () => { window.__printed = true; }; }")
    page.get_by_role("button", name="Print / PDF").click()
    page.wait_for_function("() => window.__printed === true")
    page.emulate_media(media="print")
    pdf = page.pdf(prefer_css_page_size=True, print_background=True)
    (OUT / "district-brief-five.pdf").write_bytes(pdf)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    assert pages == 1, f"the five-reform brief printed on {pages} pages"
    assert watch.problems == []
    context.close()
