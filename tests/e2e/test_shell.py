"""End-to-end: the built front end in a real browser (Chromium through Playwright).

It builds web/ with Vite into a temporary folder (``npm run build``), serves that build with
the real FastAPI app on Testland in a background thread, opens every route on a desktop and a
phone viewport, and checks that:
- there are no console errors, page errors or requests to other hosts (so the Content
  Security Policy and the bundled MapLibre worker work together);
- there is no top bar: the brand and the five mode tabs float over the one map, which stays
  mounted when the mode changes; the scenario's three buttons (Slides, Export, Share link) sit
  in the right panel's header on every mode, and all are reachable by keyboard;
- the floating zones (brand, mode switcher, both panels, chat box, map controls, legend) never
  overlap, at 1440 x 900, at 1280 x 720 and on a phone; a panel folds into a small box and
  back, the right panel is resized by its edge (pointer and keys) and keeps its width, and a
  picked county sits at the middle of the screen;
- the bare address shows the intro page, and its button opens the tool;
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
  on every screen, and its numbers equal the exported files;
- the Results panel's tabs work by keyboard, its Rankings tab lists the server's ranking and a
  row lights the county on the map, its Brief tab shows the live slide cards and a card opens
  the Slides view at that slide;
- the place profile's charts (pie, bars, stack, ranks) draw from the API's chart schema, with
  a table twin (checked with a fixture profile answered by the test);
- the optional basemap falls back to the plain map, with no page error, when its host fails;
- the Reform screen leads with the decision view (five steps, the frequency sentence, the years,
  the binding constraint), the paired readouts and the fee cost row, and "What would narrow the
  range" under the drivers, each equal to the API reply; the map key switches the shading to
  evidence coverage, with the county table as its twin; Method shows the baseline rule contest;
- Tab moves through the zones in reading order (modes, left panel, map, map controls, right
  panel, legend, chat) and back; the map steps through counties by key and names them; 3D, the
  year slider and its Play / Pause and the funnel's stages and Play work by keyboard alone;
- at 390 x 844, 360 x 740, 1280 x 720 and 1366 x 768 no widget overlaps another, nothing scrolls
  sideways, every phone target is at least 44 px and every mode name shows whole; the phone
  sheet follows a drag.
The builds start with the basemap off (``VITE_BASEMAP=off``), so no test needs the internet.

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
from visionhousing.export_static import SharedPool, export_static, unpack

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
        # The basemap (a third-party tile host) starts off, so the tests stay offline.
        env={
            **os.environ,
            "VITE_DATA_MODE": "static" if script.endswith("static") else "",
            "VITE_BASEMAP": "off",
        },
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
    # The build copies whatever web/public/data holds (a real state export can be
    # 25 MB+); clear it so Testland is measured against the size limit on its own.
    shutil.rmtree(dist / "data", ignore_errors=True)
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
    """An exported file as the screens read it (packed files unpacked, as api.js does)."""
    with urllib.request.urlopen(f"{base}data/{path}", timeout=30) as reply:  # noqa: S310
        value = json.loads(reply.read())
    if isinstance(value, dict) and value.get("$shared"):
        st = path.split("/")[0]
        with urllib.request.urlopen(f"{base}data/{st}/pool.json", timeout=30) as reply:  # noqa: S310
            return unpack(value, SharedPool(json.loads(reply.read())))
    return unpack(value)


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
        expect(page.locator("#dock-left-title")).not_to_have_text("What is this place like?")
    else:
        wait_results(page)


def open_route(
    browser: Browser, base: str, route: str, viewport: dict, query: str = SCENARIO, **ctx
):
    context = browser.new_context(viewport=viewport, **ctx)
    page = context.new_page()
    watch = Watch(page, base)
    page.goto(f"{base}{ROUTES.get(route, route)}?{query}")
    if route != "slides":
        expect(page.locator("nav.switcher")).to_be_visible()
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


ZONES_JS = """() => [...document.querySelectorAll('.hud [data-zone]')]
  .filter((el) => el.offsetParent && getComputedStyle(el).visibility !== 'hidden')
  .map((el) => { const r = el.getBoundingClientRect();
    return { zone: el.dataset.zone, l: r.left, t: r.top, r: r.right, b: r.bottom }; })"""


def no_overlaps(page: Page, skip: tuple[str, ...] = ()) -> list[str]:
    """Every floating zone is inside the window and clear of every other zone."""
    page.wait_for_timeout(600)  # let the panels' entrance settle
    # A loaded machine can run the entrance slower than that: also wait for every finite
    # animation or transition still running (endless ones, such as a spinner, are skipped).
    page.evaluate(
        """() => Promise.all(document.getAnimations()
          .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
          .map((a) => a.finished.catch(() => null)))"""
    )
    zones = [z for z in page.evaluate(ZONES_JS) if z["zone"] not in skip]
    size = page.viewport_size
    seen = [z["zone"] for z in zones]
    for z in zones:
        assert z["l"] >= -1 and z["t"] >= -1, z
        assert z["r"] <= size["width"] + 1 and z["b"] <= size["height"] + 1, z
    for i, a in enumerate(zones):
        for b in zones[i + 1 :]:
            apart = (
                a["r"] <= b["l"] + 1
                or b["r"] <= a["l"] + 1
                or a["b"] <= b["t"] + 1
                or b["b"] <= a["t"] + 1
            )
            assert apart, f"{a['zone']} overlaps {b['zone']}: {a} {b}"
    return seen


@pytest.mark.parametrize("route", list(ROUTES))
def test_route_loads_cleanly_on_desktop(browser, base_url, route):
    context, page, watch = open_route(browser, base_url, route, DESKTOP)
    # No top bar: the mode tabs float over the map (the slides view covers everything).
    expect(page.locator("header.topbar")).to_have_count(0)
    if route != "slides":
        tabs = page.get_by_role("navigation", name="Screens").get_by_role("link")
        expect(tabs).to_have_text(TABS)
    where = ".dock-right .scenario-actions" if route != "slides" else ".scenario-actions"
    actions = page.locator(where).first
    for label in BUTTONS if route != "slides" else BUTTONS[1:]:
        expect(actions.get_by_text(label, exact=True)).to_be_visible()
    current = dict(zip(["state", "reform", "compare", "place", "method"], TABS, strict=True))
    if route in current:
        expect(page.locator('.tab[aria-current="page"]')).to_have_text(current[route])
    wait_screen(page, route)
    if route != "slides":
        # Testland has county outlines, so the map draws them and shows no message.
        expect(page.locator(".map-area")).to_have_attribute("data-map-status", "ready")
        zones = no_overlaps(page)
        assert {"brand", "switch", "left", "right", "chat", "controls", "legend"} <= set(zones)
    shoot(page, f"desktop-{route}")
    assert watch.problems == []
    context.close()


@pytest.mark.parametrize("route", [*ROUTES, "#/place/99001"])
def test_route_loads_cleanly_on_phone(browser, base_url, route):
    context, page, watch = open_route(
        browser, base_url, route, PHONE, is_mobile=True, has_touch=True, device_scale_factor=2
    )
    if route != "slides":
        expect(page.locator("nav.switcher")).to_be_visible()
        expect(page.get_by_role("button", name="Show all controls")).to_be_visible()
    wait_screen(page, route)
    page.wait_for_timeout(300)
    no_sideways_scroll(page, PHONE["width"])
    if route != "slides":
        # The panels scroll inside the bottom sheet; the zones over the map stay clear.
        assert {"switch", "chat", "legend"} <= set(no_overlaps(page, skip=("left", "right")))
    shoot(page, f"phone-{route.strip('#/').replace('/', '-')}")
    if route == "state":
        # The legend's table button pulls the sheet up with the table in it.
        page.get_by_role("button", name="County table").click()
        expect(page.get_by_role("button", name="Show the map")).to_be_visible()
        expect(page.locator(".county-table tbody tr")).to_have_count(4)
        # While the sheet is up, the chat box and the legend step aside.
        expect(page.locator(".chat")).to_be_hidden()
        no_sideways_scroll(page, PHONE["width"])
        shoot(page, "phone-state-sheet-open")
        page.get_by_role("button", name="Show the map").click()
        # Slides, Export and Share link sit in the Results panel at the top of the sheet.
        actions = page.locator(".dock-right .scenario-actions")
        for label in BUTTONS:
            expect(actions.get_by_text(label, exact=True)).to_be_visible()
        nav = page.get_by_role("navigation", name="Screens")
        expect(nav.get_by_role("link")).to_have_text(TABS)
    assert watch.problems == []
    context.close()


def test_top_bar_is_keyboard_reachable_in_order(browser, base_url):
    context, page, watch = open_route(browser, base_url, "compare", DESKTOP)
    wait_results(page)
    page.locator("body").focus()
    page.evaluate("document.activeElement.blur()")
    seen = []
    # Skip link, the brand, then the five modes (the map comes after the left panel).
    for _ in range(9):
        page.keyboard.press("Tab")
        seen.append(page.evaluate("document.activeElement.innerText.trim()"))
    order = [s for s in seen if s in ["VisionHousing", *TABS]]
    assert order[: 1 + len(TABS)] == ["VisionHousing", *TABS], seen
    # Then the left panel, then the right panel with its three buttons, in this order.
    for _ in range(80):
        page.keyboard.press("Tab")
        seen.append(page.evaluate("document.activeElement.innerText.trim()"))
    later = [s for s in seen[len(order) :] if s in BUTTONS]
    assert later[: len(BUTTONS)] == BUTTONS, seen
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
    tab_to("el.innerText.trim() === 'County table'", 200)
    page.keyboard.press("Enter")
    # The table opens in the right panel and takes the focus.
    tab_to("el.matches('.county-table .link-btn')")
    page.keyboard.press("Enter")
    expect(page.locator(".county-panel")).to_be_visible()
    expect(page.locator(".tract-map path").first).to_be_visible(timeout=30000)
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Shift+Tab")
    for _ in range(80):
        page.keyboard.press("Shift+Tab")
        here = "document.activeElement"
        if page.evaluate(f"{here}.matches('.tab') && {here}.innerText === 'Compare'"):
            break
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"#/compare\?"))
    wait_results(page)
    expect(page.locator(".bar-row .num-btn").first).to_be_visible(timeout=30000)
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
            [
                f"{b['p10']} to {b['p90']}",
                f"{a['p10']} to {a['p90']}",
                c["per_1000"]["shown"]["p50"],
                c["change"]["shown"]["p50"],
            ]
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
    page.get_by_role("tab", name="Evidence").click()
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
    page.get_by_role("tab", name="Assumptions").click()
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
    used = next(r for r in val["contest_view"]["rules"] if r["used"])
    expect(page.locator("#validation tr.is-picked th")).to_have_text(
        f"{used['name']} (used){val['chosen_rule']}"
    )
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
    expect(items).to_have_count(4)
    labels = ["Brief (PDF)", "Slides (PDF)", "Table (CSV)", "Hearing brief (PDF)"]
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
    term = page.locator(".dock-right .term").first
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
    expect(page.locator("#dock-left-title")).to_have_text("Missing middle")
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
const cyc = c.decodeScenario('v=1&s=NY&r=adu&preset=high', known);
out.cycle = [cyc.scenario.preset, cyc.dropped, c.encodeScenario(cyc.scenario, known)];
const central = c.decodeScenario('v=1&s=NY&preset=central', known).scenario;
out.cycleCentral = c.encodeScenario(central, known);
out.cycleBad = c.decodeScenario('v=1&s=NY&preset=extreme', known);
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
        "preset": "central",
    }
    # The retired view preset is left out of the scenario and reported, never kept silently.
    assert out["goodDropped"] == ["view preset (no longer used; the view is the same without it)"]
    assert out["encoded"] == "v=1&s=NY&r=adu,fees&a=adu.takeup~h,fees.cut~0.5"
    assert out["alias"]["scenario"]["reforms"] == ["missing_middle", "fees"]
    assert out["alias"]["dropped"] == []
    bad = out["bad"]
    assert bad["scenario"] == {
        "state": "PA",
        "reforms": ["adu"],
        "overrides": {},
        "preset": "central",
    }
    assert len(bad["dropped"]) == 7
    assert out["future"] == "PA"
    assert out["long"] == ["the link is too long"]
    assert out["route"] == {"name": "reform", "param": "adu", "query": "v=1", "known": True}
    assert out["unknown"] == "state"
    # The housing-cycle preset round-trips; central is the default and is not written.
    assert out["cycle"] == ["high", [], "v=1&s=NY&r=adu&preset=high"]
    assert out["cycleCentral"] == "v=1&s=NY"
    assert out["cycleBad"]["scenario"]["preset"] == "central"
    assert out["cycleBad"]["dropped"] == ["housing cycle extreme"]


def check_place(page: Page) -> None:
    """A Testland county profile, then one of its tracts, then the county search."""
    title = page.locator("#dock-left-title")
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
    # The screen files share one pool per state (data/TL/pool.json): the run, county and
    # profile files above were put back from it with no console error.
    shared = json.loads(urllib.request.urlopen(f"{static_url}data/TL/runs/adu.json").read())  # noqa: S310
    assert shared.get("$shared") == 1 and static_file(static_url, "TL/runs/adu.json")["reforms"]
    page.goto(f"{static_url}#/state?v=1&s=TL&a=adu.takeup~h")
    expect(page.locator(".static-note")).to_contain_text(
        "Changing assumptions needs the local app (make app)"
    )
    page.goto(f"{static_url}#/state?v=1&s=TL&r=adu,fees")
    expect(page.locator(".dock-right .status-line")).to_contain_text(
        "This mix of reforms needs the local app"
    )
    shoot(page, "static-note")
    assert watch.problems == []
    context.close()


def test_results_tabs_and_live_brief(browser, base_url):
    """Tabs move with the arrow keys; the Brief tab shows the deck; a card opens its slide."""
    context, page, watch = open_route(
        browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu", reduced_motion="reduce"
    )
    wait_results(page)
    slides = run_of(base_url, ["adu"], drivers=True)["explanation"]["slides"]
    tab = page.get_by_role("tab", name="Results")
    expect(tab).to_have_attribute("aria-selected", "true")
    tab.focus()
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("tab", name="Rankings")).to_have_attribute("aria-selected", "true")
    ranking = api(base_url, "states/TL/map?s=TL&r=adu")["rankings"]
    rows = page.locator(".rankings .hbar")
    expect(rows).to_have_count(len(ranking[0]["rows"]))
    expect(rows.locator(".hbar-label")).to_have_text(
        [f"{r['rank']}{r['name']}" for r in ranking[0]["rows"]]
    )
    panel = page.get_by_role("tabpanel", name="Rankings")
    panel.get_by_role("button", name=ranking[1]["label"], exact=True).click()
    expect(rows.first).to_have_attribute("data-fips", ranking[1]["rows"][0]["fips"])
    rows.first.locator(".hbar-btn").hover()
    expect(page.locator(".rankings .hbar.is-hot")).to_have_count(1)
    expect(page.locator(".map-hover")).to_be_visible()
    tab.focus()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("End")
    expect(page.get_by_role("tab", name="Brief")).to_have_attribute("aria-selected", "true")
    cards = page.locator(".brief-card")
    expect(cards).to_have_count(len(slides), timeout=15000)
    big = next(i for i, s in enumerate(slides) if s.get("big"))
    expect(cards.nth(big).locator(".slide-big-value")).to_have_text(
        slides[big]["big"]["text"]["text"]
    )
    shoot(page, "desktop-state-brief")
    cards.nth(1).locator(".brief-open").click()
    expect(page).to_have_url(re.compile(r"#/slides\?"))
    expect(page.locator(".slides-count")).to_have_text(f"Slide 2 of {len(slides)}")
    expect(page.locator(".slide-frame:not(.is-off) .slide-title")).to_have_text(
        slides[1]["title"]["text"]
    )
    assert watch.problems == []
    context.close()


def test_basemap_falls_back_to_the_plain_map(browser, base_url):
    """With the basemap on and its host unreachable, the map keeps the plain look."""
    context = browser.new_context(viewport=DESKTOP)
    context.add_init_script("localStorage.setItem('visionhousing.basemap', 'on')")
    context.route("https://tiles.openfreemap.org/**", lambda route: route.abort())
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}#/state?v=1&s=TL&r=adu")
    area = page.locator(".map-area")
    expect(area).to_have_attribute("data-map-status", "ready", timeout=15000)
    expect(area).to_have_attribute("data-basemap", "failed", timeout=15000)
    expect(page.locator(".basemap-credit")).to_have_count(0)
    button = page.get_by_role("button", name="Basemap unavailable")
    expect(button).to_be_visible()
    button.click()
    expect(area).to_have_attribute("data-basemap", "off")
    expect(page.get_by_role("button", name="Basemap off")).to_be_visible()
    assert errors == []
    context.close()


def test_bare_address_shows_the_intro_and_opens_the_tool(browser, base_url):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    page.goto(base_url)
    expect(page.locator("main.intro")).to_be_visible()
    expect(page.locator("nav.switcher")).to_have_count(0)
    page.get_by_role("link", name="Open the tool").first.click()
    expect(page).to_have_url(re.compile(r"#/state\?"))
    expect(page.locator("nav.switcher")).to_be_visible()
    # A deep link skips the intro.
    page.goto(f"{base_url}#/compare?v=1&s=TL")
    expect(page.locator("main.intro")).to_have_count(0)
    context.close()


def centre_of(page: Page, fips: str) -> dict:
    """Where the map draws a county's box, in window pixels (from the outline reply)."""
    return page.evaluate(
        """async (fips) => {
          const shapes = await (await fetch('/api/v1/states/TL/outlines')).json();
          const f = shapes.features.find((x) => String(x.properties.geoid) === fips);
          let xs = [], ys = [];
          const walk = (c) => typeof c[0] === 'number'
            ? (xs.push(c[0]), ys.push(c[1])) : c.forEach(walk);
          walk(f.geometry.coordinates);
          const mid = (v) => (Math.min(...v) + Math.max(...v)) / 2;
          return { lon: mid(xs), lat: mid(ys) };
        }""",
        fips,
    )


def test_one_key_press_puts_the_hover_card_on_its_county(browser, base_url):
    """One Left / Right press on the map: the 2D hover card sits by the county it names.

    The card's placement classes are part of its rendered class, so the re-render that a
    new county brings cannot drop them and leave the card off its county.
    """
    reply = api(base_url, "states/TL/map?s=TL&r=adu")
    ranked = sorted(reply["counties"], key=lambda c: c["added"]["p50"], reverse=True)
    context, page, watch = open_route(
        browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu", reduced_motion="reduce"
    )
    wait_results(page)
    canvas = page.locator(".map-canvas")
    page.wait_for_function(
        """() => Object.keys(JSON.parse(
            document.querySelector('.map-canvas').dataset.countyPoints || '{}')).length > 0""",
        timeout=30000,
    )
    page.wait_for_timeout(600)  # the opening frame has settled
    card = page.locator(".map-hover").first
    for presses, want in ((1, ranked[0]), (2, ranked[1])):
        page.locator(".map-canvas canvas").focus()
        page.keyboard.press("ArrowRight")
        expect(card.locator(".map-hover-name")).to_have_text(want["name"])
        expect(card).to_have_class(re.compile(r"\bis-at\b"))
        page.wait_for_timeout(200)  # one re-render and a few animation frames
        expect(card).to_have_class(re.compile(r"\bis-at\b"))
        pts = canvas.evaluate("(el) => JSON.parse(el.dataset.countyPoints || '{}')")
        x, y = pts[want["fips"]]
        at = canvas.bounding_box()
        px, py = at["x"] + x, at["y"] + y
        b = card.bounding_box()
        pad = 24  # the card sits 16 px off the point it follows
        assert b["x"] - pad <= px <= b["x"] + b["width"] + pad, (presses, px, b)
        assert b["y"] - pad <= py <= b["y"] + b["height"] + pad, (presses, py, b)
    shoot(page, "desktop-hover-card-on-county")
    assert watch.problems == []
    context.close()


def test_panels_fold_resize_and_the_map_stays(browser, base_url):
    """Fold both panels into boxes and back, resize the right one, and switch modes."""
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    wait_results(page)
    # Fold the left panel: it becomes a box with its title; the box opens it again.
    page.get_by_role("button", name="Minimise Scenario").click()
    box = page.locator(".dock-left .dock-chip")
    expect(box).to_contain_text("Scenario")
    expect(box).to_contain_text("2 reforms on")
    assert no_overlaps(page)
    page.get_by_role("button", name="Minimise Results").click()
    expect(page.locator(".dock-right .dock-chip")).to_be_visible()
    shoot(page, "desktop-panels-folded")
    page.reload()
    # Folded stays folded in this browser.
    expect(page.locator(".dock-left .dock-chip")).to_be_visible()
    page.locator(".dock-left .dock-chip").click()
    page.locator(".dock-right .dock-chip").click()
    expect(page.locator(".dock-left .switch").first).to_be_visible()
    page.wait_for_timeout(700)  # the boxes grow back into panels
    # Resize the right panel by keyboard and by pointer; the width is kept.
    handle = page.get_by_role("separator", name="Resize the right panel")
    right = page.locator(".dock-right")
    before = right.bounding_box()["width"]
    handle.focus()
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(500)
    assert right.bounding_box()["width"] > before + 30
    hb = handle.bounding_box()
    page.mouse.move(hb["x"] + 4, hb["y"] + hb["height"] / 2)
    page.mouse.down()
    page.mouse.move(hb["x"] + 4 + 120, hb["y"] + hb["height"] / 2, steps=6)
    page.mouse.up()
    page.wait_for_timeout(500)
    narrow = right.bounding_box()["width"]
    assert narrow < before
    assert no_overlaps(page)
    shoot(page, "desktop-right-resized")
    page.reload()
    wait_results(page)
    page.wait_for_timeout(500)
    assert abs(page.locator(".dock-right").bounding_box()["width"] - narrow) < 4
    # A picked county sits at the middle of the screen.
    page.get_by_role("tab", name="Rankings").click()
    first = page.locator(".rankings .hbar").first
    fips = first.get_attribute("data-fips")
    first.locator(".hbar-btn").click()
    expect(page.locator(".county-panel")).to_be_visible()
    page.wait_for_timeout(1400)
    at = centre_of(page, fips)
    rest = page.locator(".map-canvas").get_attribute("data-center")
    lon, lat = (float(v) for v in rest.split(","))
    assert abs(lon - at["lon"]) < 0.05 and abs(lat - at["lat"]) < 0.05, (lon, lat, at)
    shoot(page, "desktop-county-centred")
    page.keyboard.press("Escape")
    # Changing mode keeps the same map canvas (it is never drawn again from scratch).
    page.locator(".map-canvas canvas").evaluate("el => { el.dataset.kept = 'yes'; }")
    for name in ["Reform", "Compare", "Place", "Method & data", "State"]:
        page.get_by_role("link", name=name, exact=True).click()
        expect(page.locator(".map-canvas canvas")).to_have_attribute("data-kept", "yes")
    assert watch.problems == []
    context.close()


def test_layout_has_no_overlaps_on_a_small_laptop(browser, base_url):
    for route in ["state", "reform", "compare", "method"]:
        context, page, watch = open_route(browser, base_url, route, {"width": 1280, "height": 720})
        wait_screen(page, route)
        assert {"switch", "left", "right", "chat", "legend"} <= set(no_overlaps(page))
        no_sideways_scroll(page, 1280)
        shoot(page, f"laptop-{route}")
        assert watch.problems == []
        context.close()


PROFILE_CHARTS = [
    {"id": "tenure", "kind": "pie", "title": "Who rents", "unit": "households",
     "items": [{"label": "Owners", "value": 600, "share": 0.6, "moe": None},
               {"label": "Renters", "value": 400, "share": 0.4, "moe": None}],
     "total": 1000, "compare": None, "source_ids": ["acs5_summary_file"], "vintage": "test",
     "note": ""},
    {"id": "types", "kind": "bar", "title": "Homes by type", "unit": "units",
     "items": [{"label": "Detached", "value": 700, "share": 0.7, "moe": None},
               {"label": "Apartments", "value": 300, "share": 0.3, "moe": None}],
     "total": 1000, "compare": None, "source_ids": [], "vintage": "test", "note": ""},
    {"id": "age", "kind": "stack", "title": "Age of homes", "unit": "units",
     "items": [{"label": "Before 1950", "value": 500, "share": 0.5, "moe": None},
               {"label": "Since 1950", "value": 500, "share": 0.5, "moe": None}],
     "total": 1000, "compare": None, "source_ids": [], "vintage": "test", "note": ""},
]  # fmt: skip


def test_place_charts_draw_from_the_api_schema(browser, base_url):
    """A fixture profile with charts, summary points, a headline and ranks draws them all."""
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    watch = Watch(page, base_url)
    real = api(base_url, "profile/TL/99001")
    people = next(s for s in real["sections"] if s["status"] == "ok")
    people.update(
        charts=PROFILE_CHARTS,
        headline={"label": "Fixture", "text": "headline", "words": ""},
        summary_points=["Point one", "Point two"],
    )
    real["ranks"] = [{"label": "Homes added", "text_rank": "2nd of 4", "direction": "highest"}]

    def answer(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(real))

    page.route("**/api/v1/profile/TL/99001", answer)
    page.goto(f"{base_url}#/place/99001?v=1&s=TL")
    sec = page.locator(f"#sec-{people['key']}")
    expect(sec.locator(".place-headline")).to_have_text("Fixture: headline")
    expect(sec.locator(".place-points li")).to_have_text(["Point one", "Point two"])
    expect(sec.locator(".chart")).to_have_count(3)
    expect(sec.locator(".donut-arc")).to_have_count(2)
    expect(sec.locator(".chart-donut .chart-draw")).to_have_attribute(
        "aria-label", "Who rents: Owners 60%, Renters 40%."
    )
    expect(sec.locator(".hbar")).to_have_count(2)
    expect(sec.locator(".stack-part")).to_have_count(2)
    sec.locator(".chart-donut .chart-table-btn").click()
    expect(sec.locator(".chart-donut .chart-table tbody tr")).to_have_count(2)
    expect(page.locator(".place-ranks .rank-word-value")).to_contain_text("2nd of 4")
    shoot(page, "desktop-place-charts")
    assert watch.problems == []
    context.close()


def test_decision_view_equals_the_api(browser, base_url):
    """Reform leads with the decision view; every value is the API's own text."""
    context, page, watch = open_route(browser, base_url, "reform", DESKTOP, query="v=1&s=TL")
    view = page.get_by_test_id("decision-view")
    expect(view).to_be_visible(timeout=120000)
    run = run_of(base_url, ["adu"])
    ro = run["explanation"]["readouts"]["adu"]
    d = api(base_url, "states/TL/map?s=TL&r=adu")["decision"]
    expect(view.locator("[data-step]")).to_have_count(5)
    expect(page.get_by_test_id("frequency")).to_have_text(ro["frequency"]["text"])
    if d["annual"]:
        expect(view.locator(".year-value")).to_have_text(
            [y["shown"]["p50"] for y in d["annual"]["years"]]
        )
    b = d["binding"]["binding"]
    expect(view.locator("[data-step=where]")).to_contain_text(b["name"])
    expect(view.locator("[data-step=change]")).to_contain_text(b["question"])
    # The binding-constraint card: the API's sentence and one bar per stage with its loss.
    card = page.get_by_test_id("binding")
    expect(card).to_contain_text(b["sentence"])
    stages = [x for x in d["binding"]["stages"] if x["kind"] == "loss"]
    expect(card.locator(".hbar-value")).to_have_text([f"{x['lost_text']} lost" for x in stages])
    # How sure: the chances as the API wrote them, and the share of the baseline.
    sure = page.get_by_test_id("readouts")
    shown = [c["text"]["cites"][-1]["shown"] + "%" for c in ro["chances"]]
    expect(sure.locator(".step-labels strong")).to_have_text(shown)
    sb = ro["share_of_baseline"]["shown"]
    expect(sure.locator("[data-readout=share_of_baseline]")).to_contain_text(
        f"{sb['p10']} to {sb['p90']}"
    )
    expect(page.get_by_test_id("cost-framing")).to_have_text(ro["cost"]["framing"]["text"])
    # Drivers: what would narrow the range, with the research-agenda sentence.
    page.get_by_role("tab", name="Drivers").click()
    n = run["explanation"]["narrowing"]["adu"]
    expect(page.get_by_test_id("agenda")).to_have_text(n["agenda"][0]["text"])
    expect(page.get_by_test_id("narrowing").locator(".hbar")).to_have_count(len(n["items"]))
    # (i) About this chart opens the source words.
    about = page.get_by_test_id("narrowing").locator("summary.about-sum")
    about.click()
    expect(page.get_by_test_id("narrowing").locator(".about-body")).to_contain_text(
        n["lead"]["text"]
    )
    shoot(page, "desktop-decision-view")
    assert watch.problems == []
    context.close()


def test_fee_cost_row_equals_the_api(browser, base_url):
    context, page, watch = open_route(browser, base_url, "#/reform/fees", DESKTOP, query="v=1&s=TL")
    row = page.get_by_test_id("cost-row")
    expect(row).to_be_visible(timeout=120000)
    cost = run_of(base_url, ["fees"])["explanation"]["readouts"]["fees"]["cost"]
    for name in ("revenue_given_up", "cost_per_added_home"):
        t = next(x for x in cost["texts"] if x["cites"][0]["ref"].endswith(name))
        p50 = next(c for c in t["cites"] if c["field"] == "p50")
        expect(row.locator(f"[data-readout={name}] .cost-value")).to_have_text(p50["shown"])
    expect(row.locator(".hbar")).to_have_count(len(cost["targeted"]))
    assert watch.problems == []
    context.close()


def test_map_key_switches_to_evidence_coverage(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu")
    wait_results(page)
    reply = api(base_url, "states/TL/map?s=TL&r=adu")
    cov = next(m for m in reply["measures"] if m["id"] == "coverage")
    key = page.locator(".map-legend")
    key.get_by_role("button", name="Coverage").click()
    expect(key.locator(".key-title")).to_have_text(cov["title"])
    expect(key.locator(".key-range")).to_have_text([c["label"] for c in cov["classes"]])
    # The table twin: coverage comes first, with the API's words.
    key.get_by_role("button", name="County table").click()
    for c in reply["counties"]:
        cell = page.locator(f'.county-table tr[data-fips="{c["fips"]}"] .cov-cell .class-chip')
        expect(cell).to_have_text(c["coverage"]["word"])
    per = next(m for m in reply["measures"] if m["id"] == "per_1000")
    key.get_by_role("button", name="Per 1,000 homes").click()
    expect(key.locator(".key-title")).to_have_text(per["title"])
    shoot(page, "desktop-coverage-map")
    assert watch.problems == []
    context.close()


def test_method_shows_the_baseline_contest(browser, base_url):
    context, page, watch = open_route(browser, base_url, "method", DESKTOP)
    wait_screen(page, "method")
    contest = api(base_url, "validation?state=TL")["contest"]
    table = page.get_by_test_id("contest-table")
    expect(table.locator("tbody tr")).to_have_count(len(contest["rules"]))
    expect(table.locator("tr.is-picked")).to_have_attribute("data-rule", contest["winner"])
    expect(page.get_by_test_id("contest-winner")).to_contain_text(contest["winner_name"])
    expect(page.get_by_test_id("stress").locator("li")).to_have_count(len(contest["stress"]))
    for x in contest["stress"]:
        expect(page.get_by_test_id("stress")).to_contain_text(x["sentence"])
    assert watch.problems == []
    context.close()


PITCH_AT_LEAST = "(p) => Number(document.querySelector('.map-canvas').dataset.pitch) >= p"
PITCH_UNDER = "(p) => Number(document.querySelector('.map-canvas').dataset.pitch) < p"


def test_3d_switch_tilts_the_map_and_back(browser, base_url):
    """2D is the default; 3D tilts the camera and shows the column key; 2D lays it flat."""
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu")
    wait_results(page)
    key = page.locator(".map-legend")
    flat = key.get_by_role("button", name="2D", exact=True)
    three = key.get_by_role("button", name="3D", exact=True)
    expect(flat).to_have_attribute("aria-pressed", "true")
    expect(three).to_have_attribute("aria-pressed", "false")
    three.click()
    expect(three).to_have_attribute("aria-pressed", "true")
    expect(flat).to_have_attribute("aria-pressed", "false")
    page.wait_for_function(PITCH_AT_LEAST, arg=40, timeout=15000)
    expect(key.get_by_role("button", name="Show range")).to_have_attribute("aria-pressed", "true")
    expect(key.locator(".col-patch svg")).to_be_visible()
    # The patch names the tallest column of the level in view (Testland is small enough to
    # open at tract zoom): a county with the API's own text, or a tract by its id.
    cap = key.locator(".col-patch-cap")
    if key.locator(".col-legend").get_attribute("data-level") == "county":
        reply = api(base_url, "states/TL/map?s=TL&r=adu")
        top = max(reply["counties"], key=lambda c: c["added"]["p90"])
        expect(cap).to_contain_text(top["name"])
        expect(cap).to_contain_text(top["added"]["shown"]["p90"])
    else:
        tracts = api(base_url, "states/TL/tracts?s=TL&r=adu")
        p90 = tracts["added"]["p90"]
        expect(cap).to_contain_text(f"Tract {tracts['geoids'][p90.index(max(p90))]}")
    shoot(page, "desktop-3d")
    flat.click()
    page.wait_for_function(PITCH_UNDER, arg=1, timeout=15000)
    expect(flat).to_have_attribute("aria-pressed", "true")
    assert watch.problems == []
    context.close()


def test_reduced_motion_skips_the_3d_fly_in_and_play(browser, base_url):
    """With less motion asked for, 3D arrives at once and no Play button is offered."""
    context, page, watch = open_route(
        browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu", reduced_motion="reduce"
    )
    wait_results(page)
    key = page.locator(".map-legend")
    key.get_by_role("button", name="3D", exact=True).click()
    # No fly-in: the camera is at the 3D pitch with no animation in between.
    page.wait_for_function(PITCH_AT_LEAST, arg=49.5, timeout=5000)
    key.get_by_role("button", name="2D", exact=True).click()
    page.wait_for_function(PITCH_UNDER, arg=1, timeout=5000)
    key.get_by_role("button", name="Permits by year").click()
    expect(key.locator(".year-key input[type=range]")).to_be_visible(timeout=30000)
    expect(page.locator(".year-play")).to_have_count(0)
    page.get_by_role("button", name="County table").click()
    page.locator(".county-table .link-btn").first.click()
    expect(page.locator(".map-funnel")).to_be_visible(timeout=30000)
    expect(page.locator(".map-funnel-play")).to_have_count(0)
    assert watch.problems == []
    context.close()


def test_map_funnel_step_changes_the_counts(browser, base_url):
    """Find a county with "/", then step through the reform's stages: the counts follow."""
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu")
    wait_results(page)
    reply_map = api(base_url, "states/TL/map?s=TL&r=adu")
    county = reply_map["counties"][0]
    expect(page.locator(".map-legend .key-ramp")).to_be_visible(timeout=30000)
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("/")
    expect(page.locator(".map-find-input")).to_be_focused()
    page.keyboard.type(county["name"])
    page.keyboard.press("Enter")
    panel = page.locator(".county-panel")
    expect(panel.locator("#county-title")).to_have_text(county["name"])
    funnel = panel.locator(".map-funnel")
    expect(funnel).to_be_visible(timeout=30000)
    steps = api(base_url, "states/TL/tract-funnel?s=TL&r=adu")["funnel"]["adu"]["steps"]
    tabs = funnel.get_by_role("tab")
    expect(tabs).to_have_count(len(steps))
    state = funnel.locator(".map-funnel-counts > div").nth(1).locator("dd")
    here = funnel.locator(".map-funnel-counts > div").first.locator("dd")

    def say(step):
        return f"{step['state']['shown']['p10']} to {step['state']['shown']['p90']}"

    expect(state).to_have_text(say(steps[0]))
    first = here.inner_text()
    last = len(steps) - 1
    tabs.nth(last).click()
    expect(tabs.nth(last)).to_have_attribute("aria-selected", "true")
    expect(state).to_have_text(say(steps[last]))
    expect(here).not_to_have_text(first)
    if steps[last]["unit"] != steps[last - 1]["unit"]:
        expect(funnel.locator(".map-funnel-unit")).to_contain_text("Scale changes: now")
    # Left / Right move between stages.
    tabs.nth(last).focus()
    page.keyboard.press("ArrowLeft")
    expect(tabs.nth(last - 1)).to_have_attribute("aria-selected", "true")
    expect(state).to_have_text(say(steps[last - 1]))
    legend = page.locator(".map-legend .funnel-key .key-title")
    expect(legend).to_contain_text(steps[last - 1]["words"])
    shoot(page, "desktop-map-funnel")
    assert watch.problems == []
    context.close()


def test_year_slider_shades_the_counties_by_year(browser, base_url):
    """Permits by year: the slider, the state line and the year table equal the API reply."""
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu")
    wait_results(page)
    key = page.locator(".map-legend")
    key.get_by_role("button", name="Permits by year").click()
    reply = api(base_url, "states/TL/permit-years")
    slider = key.locator(".year-key input[type=range]")
    expect(slider).to_be_visible(timeout=30000)
    expect(key.locator(".year-out")).to_have_text(str(reply["latest"]))
    expect(key.locator(".year-state")).to_have_text(
        f"State: {reply['state_past'][-1]['shown']} permitted"
    )
    expect(key.get_by_role("button", name="3D", exact=True)).to_be_disabled()
    slider.focus()
    page.keyboard.press("ArrowLeft")
    year = reply["years"][-2]
    expect(key.locator(".year-out")).to_have_text(str(year))
    expect(key.locator(".key-title")).to_contain_text(str(year))
    key.get_by_text(f"Year table ({year})").click()
    for row in reply["counties"]:
        cells = key.locator(f'.year-table tr[data-fips="{row["fips"]}"] td')
        expect(cells).to_have_text([row["shown"]["units"][-2], row["shown"]["per_1000"][-2]])
    # The last stop is the baseline's last projected year, with its likely range.
    slider.focus()
    page.keyboard.press("End")
    end = reply["projected"]["years"][-1]
    expect(key.locator(".year-out")).to_have_text(str(end))
    expect(key.locator(".key-title")).to_contain_text("(baseline, projected)")
    rec = reply["projected"]["state"][-1]["shown"]
    expect(key.locator(".year-state")).to_contain_text(f"likely {rec['p10']} to {rec['p90']}")
    shoot(page, "desktop-year-slider")
    key.get_by_role("button", name="Permits by year").click()
    expect(slider).to_have_count(0)
    assert watch.problems == []
    context.close()


SEEN_TEXTS_JS = """() => new Promise((done) => {
  const seen = new Map();
  const note = () => document.querySelectorAll('[data-count]').forEach((el, i) => {
    const key = el.dataset.count + '#' + i;
    if (!seen.has(key)) seen.set(key, new Set());
    seen.get(key).add(el.textContent);
  });
  const obs = new MutationObserver(note);
  obs.observe(document.body, { subtree: true, childList: true, characterData: true });
  const t0 = performance.now();
  const tick = () => { note(); if (performance.now() - t0 < 1500) requestAnimationFrame(tick);
    else { obs.disconnect(); done([...seen.values()].map((s) => [...s])); } };
  requestAnimationFrame(tick);
  window.__openBrief && window.__openBrief();
})"""


def test_numbers_fade_in_without_in_between_values(browser, base_url):
    """Brief numbers appear as the engine's final text, never counting through other values."""
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    wait_results(page)
    page.evaluate(
        "() => { window.__openBrief = () => document.querySelector('#results-tab-brief, "
        "[role=tab][id$=-tab-brief]').click(); }"
    )
    seen = page.evaluate(SEEN_TEXTS_JS)
    assert seen, "no Brief numbers were drawn"
    for texts in seen:
        assert len([t for t in texts if t.strip()]) <= 1, texts
    assert watch.problems == []
    context.close()


def test_explanation_cards_equal_the_api(browser, base_url):
    """The confidence, plausibility, lever and implementation cards show the API's own lines."""
    context, page, watch = open_route(browser, base_url, "method", DESKTOP, query="v=1&s=TL&r=adu")
    wait_screen(page, "method")
    conf = api(base_url, "confidence/TL")
    card = page.get_by_test_id("confidence")
    expect(card).to_contain_text(conf["baseline_performance"]["sentence"], timeout=30000)
    page.goto(f"{base_url}#/reform/adu?v=1&s=TL&r=adu")
    wait_results(page)
    plaus = api(base_url, "plausibility/TL?r=adu")["reforms"]["adu"]
    if plaus.get("state") == "not_applicable":
        want = plaus["note"]
    else:
        want = plaus["measures"][0]["sentence"]
    expect(page.get_by_test_id("plausibility")).to_contain_text(want, timeout=60000)
    if plaus.get("state") != "not_applicable" and plaus["measures"][0]["observed"]:
        axis = plaus["measures"][0]["axis"]["shown"]  # the server's text, not browser rounding
        ends = page.get_by_test_id("plausibility").locator(".ec-strip-axis")
        expect(ends).to_contain_text(axis["lo"])
        expect(ends).to_contain_text(axis["hi"])
    lever = api(base_url, "lever/TL?r=adu")
    expect(page.get_by_test_id("lever")).to_contain_text(
        f"Binding factor: {lever['binding']['name']}.", timeout=60000
    )
    if (lever.get("grade") or {}).get("sentence"):
        why = page.get_by_test_id("lever").locator(".ec-chip-why")
        expect(why).to_have_text(lever["grade"]["sentence"])  # visible, not only a tooltip
        chip = page.get_by_test_id("lever").locator(".ec-chip")
        assert chip.get_attribute("aria-describedby") == why.get_attribute("id")
    impl = api(base_url, "implementation/TL?r=adu")
    line = impl["none_text"] if not impl["rows"] else "Where each rule stands"
    expect(page.get_by_test_id("implementation").first).to_contain_text(line, timeout=30000)
    shoot(page, "desktop-explanation-cards")
    assert watch.problems == []
    context.close()


CARD_TARGETS_JS = """(card) => [...card.querySelectorAll('.ec-dot, .ec-seg-btn')].map((el) => {
    const r = el.getBoundingClientRect();
    const dot = el.matches('.ec-dot') ? getComputedStyle(el, '::before') : null;
    return { cls: el.className, w: r.width, h: r.height,
      dot: dot ? [parseFloat(dot.width), parseFloat(dot.height)] : null }; })"""


def test_plausibility_targets_are_44px_on_a_phone(browser, base_url):
    """On a phone, the plausibility card's tap targets are 44 px.

    Each observed-rate point and each unit button is a 44 px target; the drawn point stays
    12 px.
    """
    context, page, watch = open_route(
        browser, base_url, "reform", PHONE, query="v=1&s=TL&r=adu", is_mobile=True, has_touch=True
    )
    wait_results(page)
    plaus = api(base_url, "plausibility/TL?r=adu")["reforms"]["adu"]
    card = page.get_by_test_id("plausibility")
    measures = plaus.get("measures") or []
    if plaus.get("state") == "not_applicable" or not measures:
        pytest.skip("Testland has no plausibility strip for ADUs")
    if measures[0]["observed"]:
        expect(card.locator(".ec-dot").first).to_be_attached(timeout=60000)
    if len(measures) > 1:
        expect(card.locator(".ec-seg-btn").first).to_be_attached(timeout=60000)
    targets = card.evaluate(CARD_TARGETS_JS)
    assert targets, "no point or unit button drawn"
    small = [t for t in targets if t["w"] < 43.5 or t["h"] < 43.5]
    assert small == [], small
    for t in targets:
        if t["dot"]:
            assert t["dot"] == [12, 12], t  # the look is unchanged
    shoot(page, "phone-plausibility-targets")
    assert watch.problems == []
    context.close()


HEADINGS_JS = """(root) => [...root.querySelectorAll('h1, h2, h3, h4, h5, h6')]
  .filter((h) => !h.closest('[hidden]') && getComputedStyle(h).display !== 'none')
  .map((h) => ({ level: +h.tagName[1], text: h.textContent.trim(),
    card: h.closest('.ec-card')?.querySelector('.card-title')?.tagName || '',
    title: h.matches('.card-title') }))"""


def test_card_headings_are_unique_and_levelled(browser, base_url):
    """Place with two reforms on: card headings are unique and correctly levelled.

    Each "Is the rule in force?" card has its own heading, and the confidence card's inner
    headings sit one level below its title.
    """
    context, page, watch = open_route(
        browser, base_url, "#/place/99001", DESKTOP, query="v=1&s=TL&r=adu,fees"
    )
    wait_screen(page, "place")
    expect(page.get_by_test_id("implementation")).to_have_count(2, timeout=30000)
    expect(page.get_by_test_id("confidence").locator(".ec-signal-title").first).to_be_visible(
        timeout=30000
    )
    expect(page.get_by_test_id("implementation").locator(".status-line")).to_have_count(
        0, timeout=30000
    )
    heads = page.locator("[data-zone=right]").evaluate(HEADINGS_JS)
    texts = [h["text"] for h in heads]
    assert len(texts) == len(set(texts)), texts
    assert sum(t.startswith("Is the rule in force?") for t in texts) == 2, texts
    inner = [h for h in heads if h["card"] and not h["title"]]
    assert inner, heads
    for h in inner:
        assert h["level"] == int(h["card"][1]) + 1, h
    assert watch.problems == []
    context.close()


ZONE_OF_JS = """() => {
  const el = document.activeElement;
  const zones = [['skip', '.skip-link'], ['brand', '.hud-left-col'], ['switch', '.hud-top'],
    ['left', '[data-zone=left]'], ['map', '.map-canvas'], ['controls', '.hud-controls'],
    ['right', '[data-zone=right]'], ['legend', '.hud-legend'], ['chat', '.hud-chat']];
  const hit = zones.find(([, sel]) => el && el.closest(sel));
  return hit ? hit[0] : '';
}"""
ZONE_ORDER = ["skip", "brand", "switch", "left", "map", "controls", "right", "legend", "chat"]


def tab_until(page: Page, test: str, key: str = "Tab", limit: int = 120) -> None:
    """Press Tab (or Shift+Tab) until ``test`` (JS on ``el``, the focused element) holds."""
    for _ in range(limit):
        page.keyboard.press(key)
        if page.evaluate(f"(() => {{ const el = document.activeElement; return {test}; }})()"):
            return
    raise AssertionError(f"{key} never reached {test}")


def test_keyboard_path_through_the_zones_and_the_map_tools(browser, base_url):
    """Tab follows the screen: modes, left panel, map, its controls, right panel, key, chat.

    Then the new map tools work by keys alone: the map steps through counties (and says which),
    3D and back, the year slider and its Play / Pause (Space pauses, it does not restart), and
    the funnel's stage tabs and Play.
    """
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu")
    wait_results(page)
    page.locator(".skip-link").focus()
    seen = ["skip"]
    for _ in range(120):
        page.keyboard.press("Tab")
        zone = page.evaluate(ZONE_OF_JS)
        if zone != seen[-1]:
            seen.append(zone)
        if zone == "chat":
            break
    assert seen == ZONE_ORDER, seen
    # Shift+Tab walks the same zones backwards.
    back = ["chat"]
    for _ in range(120):
        page.keyboard.press("Shift+Tab")
        zone = page.evaluate(ZONE_OF_JS)
        if zone != back[-1]:
            back.append(zone)
        if zone == "skip":
            break
    assert back == ZONE_ORDER[::-1], back
    # The map: a visible ring inside the canvas, and Right steps to a county and names it.
    tab_until(page, "el.matches('.map-canvas canvas')")
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") == "solid"
    page.keyboard.press("ArrowRight")
    reply = api(base_url, "states/TL/map?s=TL&r=adu")
    top = max(reply["counties"], key=lambda c: c["added"]["p50"])
    status = page.locator(".hud > p[role=status]")
    expect(status).to_contain_text(top["name"])
    shown = top["added"]["shown"]
    expect(status).to_contain_text(f"{shown['p10']} to {shown['p90']}")
    # 3D by keyboard, then back to 2D.
    key = page.locator(".map-legend")
    three = key.get_by_role("button", name="3D", exact=True)
    tab_until(page, "el.matches('.seg') && el.innerText.trim() === '3D'")
    page.keyboard.press("Enter")
    expect(three).to_have_attribute("aria-pressed", "true")
    page.wait_for_function(PITCH_AT_LEAST, arg=40, timeout=15000)
    tab_until(page, "el.matches('.seg') && el.innerText.trim() === '2D'", key="Shift+Tab")
    page.keyboard.press("Space")
    page.wait_for_function(PITCH_UNDER, arg=1, timeout=15000)
    # The year slider: arrow keys move it, Space plays and Space pauses.
    tab_until(page, "el.matches('.year-toggle')")
    page.keyboard.press("Enter")
    slider = key.locator(".year-key input[type=range]")
    expect(slider).to_be_visible(timeout=30000)
    years = api(base_url, "states/TL/permit-years")
    tab_until(page, "el.matches('.year-key input[type=range]')")
    page.keyboard.press("ArrowLeft")
    expect(key.locator(".year-out")).to_have_text(str(years["years"][-2]))
    expect(slider).to_have_attribute("aria-valuetext", str(years["years"][-2]))
    page.keyboard.press("Home")
    play = key.locator(".year-play")
    tab_until(page, "el.matches('.year-play')")
    page.keyboard.press("Space")
    expect(play).to_have_attribute("aria-pressed", "true")
    page.keyboard.press("Space")
    expect(play).to_have_attribute("aria-pressed", "false")
    page.keyboard.press("Enter")  # Enter toggles too
    expect(play).to_have_attribute("aria-pressed", "true")
    page.keyboard.press("Escape")  # any other key stops it
    expect(play).to_have_attribute("aria-pressed", "false")
    tab_until(page, "el.matches('.year-toggle')", key="Shift+Tab")
    page.keyboard.press("Enter")
    expect(slider).to_have_count(0)
    # The funnel on the map: open the county from the map by keyboard, then its stages.
    tab_until(page, "el.matches('.map-canvas canvas')", key="Shift+Tab")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    funnel = page.locator(".county-panel .map-funnel")
    expect(funnel).to_be_visible(timeout=30000)
    tabs = funnel.get_by_role("tab")
    tab_until(page, "el.closest('.map-funnel') && el.getAttribute('role') === 'tab'")
    page.keyboard.press("ArrowRight")
    expect(tabs.nth(1)).to_have_attribute("aria-selected", "true")
    fplay = funnel.locator(".map-funnel-play")
    tab_until(page, "el.matches('.map-funnel-play')", key="Shift+Tab")
    page.keyboard.press("Space")
    expect(fplay).to_have_attribute("aria-pressed", "true")
    page.keyboard.press("Space")
    expect(fplay).to_have_attribute("aria-pressed", "false")
    shoot(page, "desktop-keyboard-map-tools")
    assert watch.problems == []
    context.close()


SMALL_TARGETS_JS = """() => [...document.querySelectorAll(
    '.hud a[href], .hud button, .hud select, .hud input, .hud [role=tab], .hud summary')]
  .filter((el) => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || el.disabled) return false;
    if (el.closest('[hidden], .visually-hidden')) return false;
    if (r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) return false;
    // Inline text buttons (a glossary word, a number in a sentence) keep their line height.
    if (el.matches('.term, .num-btn, .link-btn, .about-sum')) return false;
    if (el.closest('p, li > span')) return false;
    return r.width < 43.5 || r.height < 43.5;
  })
  .map((el) => { const r = el.getBoundingClientRect();
    const words = el.innerText.trim().slice(0, 30);
    return `${el.className} "${words}" ${r.width | 0}x${r.height | 0}`; })"""

SWITCHER_FITS_JS = """() => {
  const nav = document.querySelector('nav.switcher').getBoundingClientRect();
  return [...document.querySelectorAll('nav.switcher .tab')].filter((t) => {
    const r = t.getBoundingClientRect(); return r.left < nav.left - 1 || r.right > nav.right + 1; })
  .map((t) => t.innerText); }"""

SHEET_ZONE_JS = """() => { const s = document.querySelector('.hud-sheet').getBoundingClientRect();
  return { zone: 'sheet', l: s.left, t: s.top, r: s.right, b: s.bottom }; }"""

SIZES = {
    "phone-390": {"width": 390, "height": 844},
    "phone-360": {"width": 360, "height": 740},
    "laptop-1280": {"width": 1280, "height": 720},
    "laptop-1366": {"width": 1366, "height": 768},
}


@pytest.mark.parametrize("size", list(SIZES))
def test_no_overlaps_at_phone_and_laptop_sizes(browser, base_url, size):
    """No widget covers another, nothing scrolls sideways, and phone targets are 44 px."""
    vp = SIZES[size]
    phone = vp["width"] < 768
    extra = {"is_mobile": True, "has_touch": True} if phone else {}
    for route in ["state", "reform", "compare", "method"]:
        context, page, watch = open_route(browser, base_url, route, vp, **extra)
        wait_screen(page, route)
        no_sideways_scroll(page, vp["width"])
        if phone:
            zones = no_overlaps(page, skip=("left", "right"))
            assert {"switch", "chat", "legend"} <= set(zones)
            # The sheet is clear of the widgets above it.
            sheet = page.evaluate(SHEET_ZONE_JS)
            for z in page.evaluate(ZONES_JS):
                if z["zone"] in ("chat", "legend", "switch"):
                    assert z["b"] <= sheet["t"] + 1, (z, sheet)
            assert page.evaluate(SMALL_TARGETS_JS) == [], route
            # Every mode's name shows whole: no tab is cut off at the switcher's edge.
            assert page.evaluate(SWITCHER_FITS_JS) == [], route
        else:
            want = {"switch", "left", "right", "chat", "legend", "controls"}
            assert want <= set(no_overlaps(page))
        shoot(page, f"{size}-{route}")
        assert watch.problems == []
        context.close()


def test_phone_sheet_drags_open_and_closed(browser, base_url):
    """The sheet's handle follows a drag: up opens the sheet, down closes it; a tap toggles."""
    context, page, watch = open_route(
        browser, base_url, "state", PHONE, is_mobile=True, has_touch=True
    )
    wait_results(page)
    handle = page.locator(".sheet-handle")
    sheet = page.locator(".hud-sheet")
    low = sheet.bounding_box()["height"]
    box = handle.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - 150, steps=8)
    assert sheet.bounding_box()["height"] > low + 100  # it follows the finger
    page.mouse.up()
    expect(handle).to_have_attribute("aria-expanded", "true")
    page.wait_for_timeout(600)
    assert sheet.bounding_box()["height"] > low + 150
    box = handle.bounding_box()
    page.mouse.move(x, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(x, box["y"] + 250, steps=8)
    page.mouse.up()
    expect(handle).to_have_attribute("aria-expanded", "false")
    handle.click()
    expect(handle).to_have_attribute("aria-expanded", "true")
    shoot(page, "phone-sheet-dragged")
    assert watch.problems == []
    context.close()
