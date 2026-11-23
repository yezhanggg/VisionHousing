"""End-to-end: the built front end in a real browser (Chromium through Playwright).

It builds web/ with Vite into a temporary folder (``npm run build``), serves that build with
the real FastAPI app on Testland in a background thread, opens every route on a desktop and a
phone viewport, and checks that:
- there are no console errors, page errors or requests to other hosts (so the Content
  Security Policy and the bundled MapLibre worker work together);
- the top bar, the five tabs and the three buttons are there and reachable by keyboard;
- the slides move with the arrow keys and print one slide per page;
- the Export menu, the glossary popover and Share link work by keyboard;
- the scenario codec keeps good input, maps old reform ids and drops bad input (run in Node);
- every screen shows the engine's numbers: each value on State, Reform, Compare, Method and
  the slides equals the text the API sends for the same scenario (consistency);
- the county table equals the map reply, and the county panel shades its tracts;
- a custom assumption is marked "Yours" and changes the run;
- a keyboard-only path works from the State screen to an explained number on Compare;
- no screen scrolls sideways on a phone;
- the Place screen loads a Testland county and tract profile from the API;
- the static build (``npm run build:static``) reads exported Testland files with no server,
  on every screen, and its numbers equal the exported files.

Screenshots go to ``nopush/logs/shots/`` (git-ignored) so the layout can be reviewed.
Node and the web packages must be installed first (``make setup``).
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Browser, Page, expect, sync_playwright

from visionhousing.api import create_app
from visionhousing.export_static import export_static

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
SHOTS = ROOT / "nopush" / "logs" / "shots"
SCENARIO = "v=1&s=TL&r=adu,fees"
PACKAGE = ["adu", "missing_middle", "tod", "parking", "fees"]
ROUTES = {
    "state": "#/state",
    "reform": "#/reform/adu",
    "compare": "#/compare",
    "method": "#/method",
    "slides": "#/slides",
}
TABS = ["State", "Reform", "Compare", "Place", "Method & data"]
BUTTONS = ["Slides", "Export", "Share link"]
DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
# Software WebGL so MapLibre can draw in a headless browser and in CI.
GL_ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]


def npm() -> str:
    found = shutil.which("npm")
    assert found, "npm not found: install Node 22.12+ and run make setup"
    assert (WEB / "node_modules" / "vite").is_dir(), "web packages missing: run make setup"
    return found


def vite_build(script: str, out: Path) -> Path:
    """Build web/ with an npm script into ``out`` (never touches web/dist)."""
    subprocess.run(  # noqa: S603 (fixed command, no shell)
        [npm(), "run", script, "--", "--outDir", str(out), "--emptyOutDir", "--logLevel", "warn"],
        cwd=WEB,
        check=True,
        timeout=300,
        env={**os.environ, "VITE_DATA_MODE": "static" if script.endswith("static") else ""},
    )
    assert (out / "index.html").is_file()
    return out


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def base_url(tmp_path_factory) -> Iterator[str]:
    """The live build, served by the FastAPI app (engine on Testland) on a free port."""
    dist = vite_build("build", tmp_path_factory.mktemp("dist"))
    app = create_app(web_dir=dist, packs_dir=tmp_path_factory.mktemp("packs"))
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", server_header=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "the test server did not start"
    yield f"http://127.0.0.1:{port}/"
    server.should_exit = True
    thread.join(timeout=10)


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
def static_url(tmp_path_factory) -> Iterator[str]:
    """The static build plus an exported Testland, served as plain files (no engine)."""
    dist = vite_build("build:static", tmp_path_factory.mktemp("dist-static"))
    export_static("TL", dist / "data", packs_dir=tmp_path_factory.mktemp("packs"), draws=100)
    handler = functools.partial(QuietHandler, directory=str(dist))
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


def api(base: str, path: str, body: dict | None = None) -> dict:
    """Ask the same server the page uses, so both see the same (cached) run."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(  # noqa: S310 (a local test server)
        f"{base}api/v1/{path}",
        data=data,
        headers={"content-type": "application/json", "accept": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=120) as reply:  # noqa: S310
        return json.loads(reply.read())


def run_of(base: str, reforms: list[str], **extra) -> dict:
    """POST /run the way the screens ask for it (seed 0, default draws, with explanation)."""
    body = {"state": "TL", "reforms": reforms, "overrides": {}, "seed": 0, "explain": True}
    return api(base, "run", {**body, "drivers": False, **extra})


def static_file(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}data/{path}", timeout=30) as reply:  # noqa: S310
        return json.loads(reply.read())


def wait_results(page: Page) -> None:
    """Wait until the screen shows engine numbers (no loading line left)."""
    expect(page.locator(".num-btn").first).to_be_visible(timeout=30000)
    expect(page.locator(".status-line[role=status]")).to_have_count(0, timeout=30000)


def wait_screen(page: Page, route: str) -> None:
    """Wait until a route has drawn what it loads from the engine."""
    if route == "slides":
        expect(page.locator(".slide-frame").first).to_be_attached(timeout=30000)
    elif route == "method":
        expect(page.locator("#report-card tbody tr").first).to_be_visible(timeout=30000)
        expect(page.locator(".status-line[role=status]")).to_have_count(0, timeout=30000)
    elif "place" in route:
        expect(page.locator("h1.page-title")).not_to_have_text("What is this place like?")
    else:
        wait_results(page)


def open_route(
    browser: Browser, base: str, route: str, viewport: dict, query: str = SCENARIO, **ctx
):
    context = browser.new_context(viewport=viewport, **ctx)
    page = context.new_page()
    watch = Watch(page, base)
    page.goto(f"{base}{ROUTES.get(route, route)}?{query}")
    expect(page.locator("header.topbar")).to_be_visible()
    expect(page.locator("main#main")).to_be_visible()
    if route == "state":
        expect(page.locator(".map-canvas canvas")).to_have_count(1, timeout=15000)
        expect(page.locator(".map-area")).not_to_have_attribute("data-map-status", "loading")
    return context, page, watch


def shoot(page: Page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(250)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def no_sideways_scroll(page: Page, width: int) -> None:
    scroll = page.evaluate("document.documentElement.scrollWidth")
    assert scroll <= width, f"horizontal scroll: {scroll}px"


@pytest.mark.parametrize("route", list(ROUTES))
def test_route_loads_cleanly_on_desktop(browser, base_url, route):
    context, page, watch = open_route(browser, base_url, route, DESKTOP)
    tabs = page.get_by_role("navigation", name="Screens").get_by_role("link")
    expect(tabs).to_have_text(TABS)
    for label in BUTTONS:
        expect(page.locator(".actions").get_by_text(label, exact=True)).to_be_visible()
    current = dict(zip(["state", "reform", "compare", "place", "method"], TABS, strict=True))
    if route in current:
        expect(page.locator('.tab[aria-current="page"]')).to_have_text(current[route])
    wait_screen(page, route)
    if route == "state":
        # Testland has county outlines, so the map draws them and shows no message.
        expect(page.locator(".map-area")).to_have_attribute("data-map-status", "ready")
    shoot(page, f"desktop-{route}")
    assert watch.problems == []
    context.close()


@pytest.mark.parametrize("route", [*ROUTES, "#/place/99001"])
def test_route_loads_cleanly_on_phone(browser, base_url, route):
    context, page, watch = open_route(
        browser, base_url, route, PHONE, is_mobile=True, has_touch=True, device_scale_factor=2
    )
    expect(page.locator(".tabs")).to_be_hidden()
    expect(page.get_by_role("button", name="Menu")).to_be_visible()
    wait_screen(page, route)
    page.wait_for_timeout(300)
    no_sideways_scroll(page, PHONE["width"])
    shoot(page, f"phone-{route.strip('#/').replace('/', '-')}")
    if route == "state":
        page.get_by_role("button", name="Show all controls").click()
        page.get_by_role("button", name="County table").click()
        expect(page.locator(".county-table tbody tr")).to_have_count(4)
        no_sideways_scroll(page, PHONE["width"])
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
    expect(page).to_have_url(re.compile(r"#/reform/adu\?v=1&s=TL&r=adu,fees$"))
    wait_results(page)
    assert watch.problems == []
    context.close()


def test_keyboard_only_path(browser, base_url):
    """Switch a reform on, open a county, move to Compare and explain a number: keys only."""
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL")
    wait_results(page)

    def tab_to(test: str, limit: int = 60) -> None:
        for _ in range(limit):
            page.keyboard.press("Tab")
            if page.evaluate(f"(() => {{ const el = document.activeElement; return {test}; }})()"):
                return
        raise AssertionError(f"Tab never reached {test}")

    tab_to("el.matches('.switch') && el.innerText.startsWith('Accessory dwelling units')")
    page.keyboard.press("Space")
    expect(page).to_have_url(re.compile(r"r=adu$"))
    expect(page.locator(".headline-row")).to_have_count(3, timeout=30000)
    tab_to("el.innerText.trim() === 'County table'")
    page.keyboard.press("Enter")
    tab_to("el.matches('.county-table .link-btn')")
    page.keyboard.press("Enter")
    expect(page.locator(".county-panel")).to_be_visible()
    expect(page.locator(".tract-map path").first).to_be_visible(timeout=30000)
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Shift+Tab")
    for _ in range(30):
        page.keyboard.press("Shift+Tab")
        here = "document.activeElement"
        if page.evaluate(f"{here}.matches('.tab') && {here}.innerText === 'Compare'"):
            break
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"#/compare\?"))
    wait_results(page)
    tab_to("el.matches('.num-btn')", 200)
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("Formula")
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    shoot(page, "desktop-keyboard-path")
    assert watch.problems == []
    context.close()


def test_state_numbers_equal_the_api(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    wait_results(page)
    run = run_of(base_url, ["adu", "fees"])
    e = run["explanation"]
    expect(page.get_by_test_id("headline-sentence")).to_have_text(e["headline"]["sentence"]["text"])
    for item in e["headline"]["numbers"]:
        sh = item["shown"]
        main = sh["p50"] if item["key"] == "baseline" else f"{sh['p10']} to {sh['p90']}"
        row = page.locator(f".headline-row[data-key={item['key']}]")
        expect(row.locator(".num-btn")).to_have_text(main)
    verdicts = page.locator(".verdict-word")
    expect(verdicts).to_have_text([v["word"] for v in e["verdicts"].values()])
    # The county table is the map's twin: the same rows and the same text.
    page.get_by_role("button", name="County table").click()
    query = "r=adu,fees&s=TL"
    reply = api(base_url, f"states/TL/map?{query}")
    rows = page.locator(".county-table tbody tr")
    expect(rows).to_have_count(len(reply["counties"]))
    for c in reply["counties"]:
        row = page.locator(f'.county-table tr[data-fips="{c["fips"]}"]')
        b, a = c["baseline"]["shown"], c["added"]["shown"]
        cells = row.locator(".num-btn")
        expect(cells).to_have_text(
            [f"{b['p10']} to {b['p90']}", f"{a['p10']} to {a['p90']}", c["change"]["shown"]["p50"]]
        )
    # Sorting by the change puts the largest first (a number column sorts high to low first).
    page.get_by_role("button", name=re.compile("^Change, middle")).click()
    top = max(reply["counties"], key=lambda c: c["change"]["p50"])
    expect(rows.first).to_have_attribute("data-fips", top["fips"])
    shoot(page, "desktop-state-table")
    assert watch.problems == []
    context.close()


def test_county_panel_shades_tracts(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    wait_results(page)
    page.get_by_role("button", name="County table").click()
    page.locator(".county-table .link-btn").first.click()
    panel = page.locator(".county-panel")
    expect(panel).to_be_visible()
    fips = panel.locator(".card-sub").first.inner_text().split()[1]
    county = api(base_url, f"states/TL/counties/{fips}?s=TL&r=adu,fees")
    tracts = county["tract_results"]["reforms"]["adu"]["tracts"]
    expect(panel.locator(".tract-map path")).to_have_count(len(county["tracts"]), timeout=30000)
    expect(panel.locator(".tract-key .key-label")).to_have_text(
        ["None", *county["tract_results"]["labels"]]
    )
    panel.get_by_text("Tract table").click()
    expect(panel.locator(".tract-details tbody tr")).to_have_count(len(tracts))
    best = max(tracts, key=lambda t: t["p50"])
    expect(panel.locator(".tract-details tbody tr").first.locator("th")).to_have_text(best["geoid"])
    shoot(page, "desktop-county-panel")
    panel.get_by_role("button", name="Close panel").click()
    expect(panel).to_have_count(0)
    assert watch.problems == []
    context.close()


def test_reform_screen_equals_the_api(browser, base_url):
    context, page, watch = open_route(browser, base_url, "reform", DESKTOP)
    wait_results(page)
    e = run_of(base_url, ["adu"], drivers=True)["explanation"]
    steps = e["funnels"]["adu"]["steps"]
    expect(page.locator(".funnel-step .funnel-value .num-btn")).to_have_text(
        [f"{s['shown']['p10']} to {s['shown']['p90']}" for s in steps]
    )
    head = {x["key"]: x["shown"] for x in e["headline"]["numbers"]}
    nums = page.locator(".reform-numbers .num-btn")
    expect(nums).to_have_text(
        [f"{head[k]['p10']} to {head[k]['p90']}" for k in ("added", "pct_change")]
    )
    drivers = e["drivers"]["adu"]["items"]
    expect(page.locator(".tornado-row .funnel-help")).to_have_text(
        [d["text"]["text"] for d in drivers], timeout=60000
    )
    limits = [t["text"] for t in e["limits"]["reforms"]["adu"]["items"]]
    for text in limits:
        expect(page.locator("#limits")).to_contain_text(text)
    detail = api(base_url, "reforms/adu")
    expect(page.locator("#assumptions tbody tr")).to_have_count(len(detail["parameters"]))
    evidence = api(base_url, "evidence?reform=adu")["entries"]
    cards = [
        x for x in evidence if "evidence_card" in x["used_for"] or x["entry_type"] == "outcome"
    ]
    expect(page.locator(".ev-card")).to_have_count(len(cards))
    # The explain panel of a funnel step shows the engine's formula.
    page.locator(".funnel-step .explain-btn").first.click()
    num = e["numbers"][steps[0]["ref"]]
    expect(page.get_by_role("dialog")).to_contain_text(num["formula"][:60])
    page.keyboard.press("Escape")
    shoot(page, "desktop-reform")
    assert watch.problems == []
    context.close()


def test_custom_assumption_is_marked_and_changes_the_run(browser, base_url):
    context, page, watch = open_route(browser, base_url, "reform", DESKTOP, query="v=1&s=TL")
    wait_results(page)
    pid = "adu.takeup_feasible_annual"
    row = page.locator("#assumptions tbody tr").filter(has=page.locator(f"code:text-is('{pid}')"))
    row.get_by_role("button", name="High", exact=True).click()
    expect(page).to_have_url(re.compile(rf"a={re.escape(pid)}~h"))
    expect(row.locator(".tag-yours")).to_have_text("Yours")
    body = {"state": "TL", "reforms": ["adu"], "overrides": {pid: "h"}, "seed": 0}
    e = api(base_url, "run", {**body, "explain": True, "drivers": False})["explanation"]
    head = {x["key"]: x["shown"] for x in e["headline"]["numbers"]}
    expect(page.locator(".reform-numbers .num-btn").first).to_have_text(
        f"{head['added']['p10']} to {head['added']['p90']}", timeout=30000
    )
    row.locator("input").fill("0.02")
    row.get_by_role("button", name="Set custom").click()
    expect(page).to_have_url(re.compile(rf"a={re.escape(pid)}~0.02"))
    body["overrides"] = {pid: 0.02}
    e = api(base_url, "run", {**body, "explain": True, "drivers": False})["explanation"]
    head = {x["key"]: x["shown"] for x in e["headline"]["numbers"]}
    expect(page.locator(".reform-numbers .num-btn").first).to_have_text(
        f"{head['added']['p10']} to {head['added']['p90']}", timeout=30000
    )
    expect(row.locator(".tag-yours")).to_have_text("Yours")
    page.get_by_role("button", name=re.compile(r"^Reset all")).click()
    expect(page).to_have_url(re.compile(r"#/reform/adu\?v=1&s=TL$"))
    assert watch.problems == []
    context.close()


def test_compare_screen_equals_the_api(browser, base_url):
    context, page, watch = open_route(browser, base_url, "compare", DESKTOP)
    wait_results(page)
    run = run_of(base_url, PACKAGE)
    e = run["explanation"]
    bills = {k: run_of(base_url, [k])["explanation"] for k in ("pa_hb2186", "pa_sb1346")}
    expected = [e["numbers"][f"reform_results.{k}.added"]["shown"]["p50"] for k in PACKAGE]
    expected += [bills[k]["numbers"][f"reform_results.{k}.added"]["shown"]["p50"] for k in bills]
    expect(page.locator(".bar-row .num-btn")).to_have_text(expected, timeout=30000)
    rows = page.locator("#table tbody tr")
    expect(rows).to_have_count(len(expected))
    for k in PACKAGE:
        sh = e["numbers"][f"reform_results.{k}.added"]["shown"]
        expect(page.locator(f'#table tr[data-key="{k}"] .num-btn').nth(0)).to_have_text(sh["p10"])
        expect(page.locator(f'#table tr[data-key="{k}"] .num-btn').nth(2)).to_have_text(sh["p90"])
        expect(page.locator(f'#table tr[data-key="{k}"] td').last).to_have_text(
            e["verdicts"][k]["word"]
        )
    steps = e["waterfall"]["steps"]
    expect(page.locator(".waterfall-step .num-btn")).to_have_text(
        [f"{s['shown']['p10']} to {s['shown']['p90']}" for s in steps]
    )
    assert [s["kind"] for s in steps] == ["start", "minus", "plus", "total"]
    with page.expect_download() as down:
        page.get_by_role("button", name="Download CSV").click()
    path = down.value.path()
    header = Path(path).read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("scope,geography,reform,measure,p10,p50,p90")
    shoot(page, "desktop-compare")
    assert watch.problems == []
    context.close()


def test_method_screen_equals_the_api(browser, base_url):
    context, page, watch = open_route(browser, base_url, "method", DESKTOP)
    expect(page.locator("#report-card tbody tr").first).to_be_visible(timeout=30000)
    card = api(base_url, "report-card?state=TL")
    expect(page.locator("#report-card tbody tr")).to_have_count(len(card["inputs"]))
    prov = api(base_url, "provenance?state=TL")
    expect(page.locator("#provenance tbody tr")).to_have_count(len(prov["sources"]))
    val = api(base_url, "validation?state=TL")
    expect(page.locator("#validation tbody tr")).to_have_count(len(val["rows"]))
    expect(page.locator("#validation tr.is-picked th")).to_have_text(f"{val['chosen_rule']} (used)")
    search = page.get_by_role("searchbox", name="Search the ledger")
    search.fill("takeup")
    led = api(base_url, "ledger?q=takeup&state=TL")
    expect(page.locator("#ledger [role=status]")).to_have_text(
        re.compile(rf"^{led['count']} of {led['total']} assumptions")
    )
    expect(page.locator("#ledger tbody tr")).to_have_count(min(led["count"], 200))
    shoot(page, "desktop-method")
    assert watch.problems == []
    context.close()


def test_slides_move_with_arrow_keys(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    wait_results(page)
    run = run_of(base_url, ["adu", "fees"])
    slides = run["explanation"]["slides"]
    total = len(slides)
    assert total >= 3
    page.get_by_role("link", name="Slides", exact=True).click()
    count = page.locator(".slides-count")
    expect(count).to_have_text(f"Slide 1 of {total}", timeout=30000)
    expect(page.locator(".slide-frame:not(.is-off) .slide-title")).to_have_text(
        slides[0]["title"]["text"]
    )
    expect(page.locator(".slide-frame:not(.is-off) .slide-big-value")).to_have_text(
        slides[0]["big"]["text"]["text"]
    )
    stamp = run["explanation"]["stamp"]
    foot = page.locator(".slide-frame:not(.is-off) .slide-foot")
    for key in ("code", "data", "assumptions"):
        expect(foot).to_contain_text(stamp[key][:12])
    page.keyboard.press("ArrowRight")
    expect(count).to_have_text(f"Slide 2 of {total}")
    expect(page.locator(".slide-frame:not(.is-off) .slide-title")).to_have_text(
        slides[1]["title"]["text"]
    )
    shoot(page, "desktop-slides-2")
    page.keyboard.press("End")
    expect(count).to_have_text(f"Slide {total} of {total}")
    page.keyboard.press("ArrowLeft")
    expect(count).to_have_text(f"Slide {total - 1} of {total}")
    page.keyboard.press("Home")
    expect(count).to_have_text(f"Slide 1 of {total}")
    expect(page.locator(".slide-frame:not(.is-off)")).to_have_count(1)
    page.get_by_role("button", name="Read as page").click()
    expect(page.locator(".slides-text-item")).to_have_count(total)
    shoot(page, "desktop-slides-text")
    page.keyboard.press("Escape")
    expect(page).to_have_url(re.compile(r"#/state\?"))
    assert watch.problems == []
    context.close()


def test_slides_print_one_slide_per_page(browser, base_url):
    context, page, watch = open_route(browser, base_url, "slides", DESKTOP)
    total = len(run_of(base_url, ["adu", "fees"])["explanation"]["slides"])
    expect(page.locator(".slide-frame")).to_have_count(total, timeout=30000)
    pdf = page.pdf(width="13.333in", height="7.5in", print_background=True)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    assert pages == total
    assert watch.problems == []
    context.close()


def test_share_link_copies_the_address(browser, base_url):
    context, page, watch = open_route(
        browser, base_url, "reform", DESKTOP, permissions=["clipboard-read", "clipboard-write"]
    )
    page.get_by_role("button", name="Share link").click()
    expect(page.get_by_role("status").filter(has_text="Link copied")).to_have_text("Link copied")
    assert page.evaluate("navigator.clipboard.readText()") == page.url
    wait_results(page)
    assert watch.problems == []
    context.close()


def test_export_menu_works_by_keyboard(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    wait_results(page)
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
    page.keyboard.press("ArrowDown")
    with page.expect_download() as down:
        page.keyboard.press("Enter")
    text = Path(down.value.path()).read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith("scope,geography,reform")
    run = run_of(base_url, ["adu", "fees"])
    assert run["hashes"]["data"] in text
    expect(button).to_be_focused()
    page.keyboard.press("Enter")
    with page.expect_download() as down:
        page.get_by_role("menuitem").first.click()
    assert "<html" in Path(down.value.path()).read_text(encoding="utf-8").lower()
    assert watch.problems == []
    context.close()


def test_glossary_and_explain_popovers_work_by_keyboard(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    wait_results(page)
    term = page.locator(".card-results .term").first
    term.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("today's rules")
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(term).to_be_focused()
    e = run_of(base_url, ["adu", "fees"])["explanation"]
    added = next(x for x in e["headline"]["numbers"] if x["key"] == "added")
    explain = page.get_by_role("button", name=f"Explain: {added['label']['text']}")
    explain.focus()
    page.keyboard.press("Space")
    expect(dialog).to_contain_text("Formula")
    expect(dialog).to_contain_text(e["numbers"][added["ref"]]["formula"][:60])
    expect(dialog).to_contain_text("Sources")
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
    # A Pennsylvania bill reaches the same lots as the ADU reform, so it replaces it.
    page.get_by_role("button", name=re.compile("^PA House Bill 2186")).click()
    expect(page).to_have_url(re.compile(r"r=parking,fees,pa_hb2186$"))
    page.get_by_role("button", name="Back to baseline").click()
    expect(page).to_have_url(re.compile(r"#/state\?v=1&s=TL$"))
    page.get_by_role("button", name="All five reforms").click()
    expect(page).to_have_url(re.compile(r"r=adu,missing_middle,tod,parking,fees$"))
    wait_results(page)
    assert watch.problems == []
    context.close()


def test_old_share_links_still_open(browser, base_url):
    """`middle` was the old id of Missing middle; links that use it keep working."""
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    watch = Watch(page, base_url)
    page.goto(f"{base_url}#/reform/middle?v=1&s=TL&r=middle")
    expect(page).to_have_url(re.compile(r"#/reform/missing_middle\?v=1&s=TL&r=missing_middle$"))
    expect(page.locator("h1.page-title")).to_have_text("Missing middle")
    wait_results(page)
    assert watch.problems == []
    context.close()


CODEC_CHECK = """
const c = await import(process.argv[1]);
const known = { states: ['PA', 'NY'], reforms: ['adu', 'missing_middle', 'fees'],
  aliases: { middle: 'missing_middle' } };
const out = {};
const link = 'v=1&s=NY&r=fees,adu&a=adu.takeup~h,fees.cut~0.5&p=policymaker';
const good = c.decodeScenario(link, known);
out.good = good.scenario;
out.goodDropped = good.dropped;
out.encoded = c.encodeScenario(good.scenario, known);
out.alias = c.decodeScenario('r=middle,fees', known);
out.bad = c.decodeScenario('s=XX&r=adu,nope&a=Bad~h,ok.key~1e99,x~l~h&p=$$&z=1', known);
out.future = c.decodeScenario('v=9&s=NY', known).scenario.state;
out.long = c.decodeScenario('r=' + 'adu,'.repeat(600), known).dropped;
out.route = c.parseRoute('#/reform/adu?v=1');
out.unknown = c.parseRoute('#/nowhere').name;
console.log(JSON.stringify(out));
"""


def test_scenario_codec_keeps_good_input_and_drops_bad_input():
    """The codec is pure JavaScript, so Node runs it straight from the source file."""
    node = shutil.which("node")
    assert node, "node not found: install Node 22.12+"
    codec = (WEB / "js" / "state" / "codec.js").as_uri()
    done = subprocess.run(  # noqa: S603 (fixed command, no shell)
        [node, "--input-type=module", "-e", CODEC_CHECK, codec],
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    out = json.loads(done.stdout)
    assert out["good"] == {
        "state": "NY",
        "reforms": ["adu", "fees"],
        "overrides": {"adu.takeup": "h", "fees.cut": "0.5"},
        "preset": "policymaker",
    }
    assert out["goodDropped"] == []
    assert out["encoded"] == "v=1&s=NY&r=adu,fees&a=adu.takeup~h,fees.cut~0.5&p=policymaker"
    assert out["alias"]["scenario"]["reforms"] == ["missing_middle", "fees"]
    assert out["alias"]["dropped"] == []
    bad = out["bad"]
    assert bad["scenario"] == {"state": "PA", "reforms": ["adu"], "overrides": {}, "preset": ""}
    assert len(bad["dropped"]) == 7
    assert out["future"] == "PA"
    assert out["long"] == ["the link is too long"]
    assert out["route"] == {"name": "reform", "param": "adu", "query": "v=1", "known": True}
    assert out["unknown"] == "state"


def check_place(page: Page) -> None:
    """A Testland county profile, then one of its tracts, then the county search."""
    title = page.locator("h1.page-title")
    expect(title).not_to_have_text("What is this place like?", timeout=15000)
    expect(page.locator(".place-card").first).to_be_visible()
    expect(page.locator(".place-sources li").first).to_be_visible()
    expect(page.locator("link[data-screen=place]")).to_have_attribute("href", re.compile(r"\.css$"))
    county = title.inner_text()
    tracts = page.locator(".place-tracts select")
    expect(tracts.locator("option").nth(1)).to_be_attached()
    tract = tracts.locator("option").nth(1).get_attribute("value")
    tracts.select_option(tract)
    expect(page).to_have_url(re.compile(rf"#/place/{tract}\?"))
    expect(title).not_to_have_text(county)
    search = page.get_by_role("searchbox")
    search.fill("990")
    expect(page.locator(".place-match").first).to_be_visible()
    search.press("Enter")
    expect(page).to_have_url(re.compile(r"#/place/990\d\d\?"))


def test_place_route_loads_a_testland_profile(browser, base_url):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    watch = Watch(page, base_url)
    page.goto(f"{base_url}#/place/99001?v=1&s=TL")
    check_place(page)
    shoot(page, "desktop-place")
    assert watch.problems == []
    context.close()


def test_app_serves_the_build_with_its_security_headers(browser, base_url):
    context = browser.new_context()
    page = context.new_page()
    reply = page.goto(base_url)
    headers = reply.headers
    assert "script-src 'self'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"
    assert '<script type="importmap">' not in page.content()
    context.close()


STATIC_VIEWS = {
    "baseline": "v=1&s=TL",
    "adu": "v=1&s=TL&r=adu",
    "pa_hb2186": "v=1&s=TL&r=pa_hb2186",
    "package": "v=1&s=TL&r=adu,missing_middle,tod,parking,fees",
}


@pytest.mark.parametrize("view", list(STATIC_VIEWS))
def test_static_state_screen_equals_the_exported_files(browser, static_url, view):
    context, page, watch = open_route(
        browser, static_url, "state", DESKTOP, query=STATIC_VIEWS[view]
    )
    expect(page.locator(".static-note")).to_have_count(0)
    wait_results(page)
    run = static_file(static_url, f"TL/runs/{view}.json")
    e = run["explanation"]
    expect(page.get_by_test_id("headline-sentence")).to_have_text(e["headline"]["sentence"]["text"])
    expect(page.locator(".headline-row")).to_have_count(len(e["headline"]["numbers"]))
    page.get_by_role("button", name="County table").click()
    expect(page.locator(".county-table tbody tr")).to_have_count(4)
    page.locator(".county-table .link-btn").first.click()
    expect(page.locator(".county-panel")).to_be_visible()
    expect(page.locator(".county-panel .status-line")).to_have_count(0, timeout=15000)
    shoot(page, f"static-state-{view}")
    assert watch.problems == []
    context.close()


@pytest.mark.parametrize("route", ["reform", "compare", "method", "slides"])
def test_static_screens_work_with_no_server(browser, static_url, route):
    context, page, watch = open_route(browser, static_url, route, DESKTOP, query="v=1&s=TL")
    if route == "slides":
        total = len(static_file(static_url, "TL/runs/baseline.json")["explanation"]["slides"])
        expect(page.locator(".slide-frame")).to_have_count(total, timeout=15000)
    elif route == "method":
        expect(page.locator("#report-card tbody tr").first).to_be_visible(timeout=15000)
        expect(page.locator("#validation tbody tr").first).to_be_visible()
        page.get_by_role("searchbox", name="Search the ledger").fill("takeup")
        expect(page.locator("#ledger [role=status]")).to_contain_text("assumptions")
    else:
        wait_results(page)
    if route == "reform":
        e = static_file(static_url, "TL/runs/adu.json")["explanation"]
        steps = e["funnels"]["adu"]["steps"]
        expect(page.locator(".funnel-step .funnel-value .num-btn")).to_have_text(
            [f"{s['shown']['p10']} to {s['shown']['p90']}" for s in steps]
        )
        expect(page.locator(".tornado-row")).to_have_count(len(e["drivers"]["adu"]["items"]))
        expect(page.locator(".static-inline")).to_contain_text(
            "Changing assumptions needs the local app"
        )
        expect(page.locator("#assumptions .seg")).to_have_count(0)
    if route == "compare":
        e = static_file(static_url, "TL/runs/package.json")["explanation"]
        expected = [e["numbers"][f"reform_results.{k}.added"]["shown"]["p50"] for k in PACKAGE]
        expect(page.locator(".bar-row .num-btn")).to_have_count(len(PACKAGE) + 2)
        expect(page.locator(".bar-row .num-btn").nth(0)).to_have_text(expected[0])
        with page.expect_download() as down:
            page.get_by_role("button", name="Download CSV").click()
        assert Path(down.value.path()).read_text(encoding="utf-8").startswith("scope,")
    shoot(page, f"static-{route}")
    assert watch.problems == []
    context.close()


def test_static_site_says_what_needs_the_local_app(browser, static_url):
    context, page, watch = open_route(browser, static_url, "state", DESKTOP, query="v=1&s=TL")
    page.goto(f"{static_url}#/place/99001?v=1&s=TL")
    check_place(page)
    shoot(page, "static-place")
    page.goto(f"{static_url}#/state?v=1&s=TL&a=adu.takeup~h")
    expect(page.locator(".static-note")).to_contain_text(
        "Changing assumptions needs the local app (make app)"
    )
    page.goto(f"{static_url}#/state?v=1&s=TL&r=adu,fees")
    expect(page.locator(".card-results .status-line")).to_contain_text(
        "This mix of reforms needs the local app"
    )
    shoot(page, "static-note")
    assert watch.problems == []
    context.close()
