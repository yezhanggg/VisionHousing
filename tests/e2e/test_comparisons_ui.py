"""End-to-end: the policy dial and the comparison cards, mounted on their screens (Testland).

- Reform: the folded "How much reform?" section (Decision tab) holds the policy dial; an arrow
  key moves the dial one level and the shown middle estimate becomes the server's text for it;
- Compare: the folded "More comparisons" section draws the interaction waterfall and the
  ranking check when opened (and asks for nothing before);
- Method: the folded "Sparse-data test and ADU sensitivity" section draws the ablation tiers
  and the 5 x 5 take-up x cost grid;
- static site: with no `comparisons: true` in the state's index, every card says it needs
  the local app instead of asking for a file.

Every number checked is the API's own text for the same state and part.
Screenshots go to ``nopush/logs/shots/`` (git-ignored), or to ``$VH_SHOTS`` when set.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import test_shell as shell
from playwright.sync_api import expect
from test_shell import DESKTOP, SHOTS, api, open_route, static_file

base_url = shell.base_url
static_url = shell.static_url
browser = shell.browser

OUT = Path(os.environ.get("VH_SHOTS") or SHOTS)
SLOW = 240000  # a comparisons part runs the engine many times the first time it is asked
NOTE = "These comparisons need the local app (make app)."


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(250)
    page.screenshot(path=str(OUT / f"u9-{name}.png"))


def comparisons_requests(page) -> list[str]:
    seen: list[str] = []
    page.on("request", lambda r: "comparisons" in r.url and seen.append(r.url))
    return seen


def test_dial_arrow_key_changes_the_shown_middle(browser, base_url):
    dials = api(base_url, "states/TL/comparisons?part=dials")["dials"]["dials"]
    dial = next(d for d in dials.values() if d["reform"] == "adu")
    levels = dial["levels"]
    bench = next(i for i, lv in enumerate(levels) if lv.get("benchmark"))
    context, page, watch = open_route(browser, base_url, "reform", DESKTOP, query="v=1&s=TL&r=adu")
    asked = comparisons_requests(page)
    expect(page.get_by_test_id("decision-view")).to_be_visible(timeout=120000)
    assert asked == [], "the dial part was asked for before its section opened"
    page.get_by_test_id("how-much").locator("summary").click()
    card = page.get_by_test_id("policy-dial")
    num = card.locator(".dial-num strong")
    expect(num).to_have_text(levels[bench]["record"]["shown"]["p50"], timeout=SLOW)
    step, key = (1, "ArrowRight") if bench + 1 < len(levels) else (-1, "ArrowLeft")
    card.locator(".dial-range").focus()
    page.keyboard.press(key)
    expect(num).to_have_text(levels[bench + step]["record"]["shown"]["p50"])
    expect(card.locator(".dial-level")).to_have_text(levels[bench + step]["label"])
    shot(page, "reform-dial")
    assert watch.problems == []
    context.close()


def test_compare_shows_the_waterfall_and_the_ranking(browser, base_url):
    inter = api(base_url, "states/TL/comparisons?part=interactions")["interactions"]
    rank = api(base_url, "states/TL/comparisons?part=ranking")["ranking"]
    context, page, watch = open_route(browser, base_url, "compare", DESKTOP, query="v=1&s=TL")
    asked = comparisons_requests(page)
    fold = page.get_by_test_id("cmp-more")
    expect(fold).to_be_visible(timeout=120000)
    assert asked == [], "the comparisons were asked for before the section opened"
    fold.locator("summary").click()
    wf = page.get_by_test_id("cmp-interactions")
    expect(wf.locator(".cmp-wf-row")).to_have_count(len(inter["waterfall"]["steps"]), timeout=SLOW)
    expect(wf.locator(".ec-line")).to_have_text(inter["headline"])
    expect(wf.locator(".cmp-wf-num")).to_have_text(
        [s.get("signed") or "" for s in inter["waterfall"]["steps"]]
    )
    rk = page.get_by_test_id("cmp-ranking")
    order = rank["benchmark"]["order"]
    expect(rk.locator(".cmp-rank-row")).to_have_count(len(order), timeout=SLOW)
    expect(rk.locator(".cmp-rank-num")).to_have_text(
        [rank["benchmark"]["ranges"][k]["shown"]["p50"] for k in order]
    )
    fold.scroll_into_view_if_needed()
    shot(page, "compare-comparisons")
    assert watch.problems == []
    context.close()


def test_method_shows_the_ablation_and_the_matrix(browser, base_url):
    matrix = api(base_url, "states/TL/comparisons?part=matrix")["matrix"]
    abl = api(base_url, "states/TL/comparisons?part=ablation")["ablation"]
    context, page, watch = open_route(browser, base_url, "method", DESKTOP, query="v=1&s=TL")
    fold = page.get_by_test_id("stress-tests")
    expect(fold).to_be_visible(timeout=120000)
    fold.locator("summary").click()
    ab = page.get_by_test_id("cmp-ablation")
    expect(ab.locator(".cmp-tier")).to_have_count(len(abl["tiers"]), timeout=SLOW)
    expect(ab.locator(".ec-line")).to_have_text(abl["headline"])
    mx = page.get_by_test_id("cmp-matrix")
    cells = [c for row in matrix["cells"] for c in row]
    expect(mx.locator(".cmp-cell")).to_have_count(len(cells), timeout=SLOW)
    assert len(cells) == 25
    first = mx.locator(".cmp-cell").first
    # Keep the pointer off the grid, so only the focused cell can be named in the live line.
    page.mouse.move(0, 0)
    first.focus()
    expect(first).to_be_focused()
    expect(mx.locator(".cmp-live")).to_contain_text(cells[0]["likely"])
    fold.scroll_into_view_if_needed()
    shot(page, "method-comparisons")
    assert watch.problems == []
    context.close()


def without_flags(page, static_url: str, *flags: str) -> None:
    """Serve TL/index.json with ``flags`` removed, as an export made before they existed."""
    index = static_file(static_url, "TL/index.json")
    for flag in flags:
        index.pop(flag, None)
    body = json.dumps(index)
    page.route("**/data/TL/index.json", lambda route: route.fulfill(
        status=200, content_type="application/json", body=body))  # fmt: skip


def test_static_site_reads_the_saved_comparisons(browser, static_url):
    index = static_file(static_url, "TL/index.json")
    assert index["comparisons"] is True
    dials = static_file(static_url, "TL/comparisons/dials.json")["dials"]["dials"]
    levels = next(d for d in dials.values() if d["reform"] == "adu")["levels"]
    bench = next(i for i, lv in enumerate(levels) if lv.get("benchmark"))
    inter = static_file(static_url, "TL/comparisons/interactions.json")["interactions"]
    abl = static_file(static_url, "TL/comparisons/ablation.json")["ablation"]
    context, page, watch = open_route(
        browser, static_url, "reform", DESKTOP, query="v=1&s=TL&r=adu"
    )
    asked = comparisons_requests(page)
    page.get_by_test_id("how-much").locator("summary").click()
    card = page.get_by_test_id("policy-dial")
    expect(card.locator(".dial-num strong")).to_have_text(levels[bench]["record"]["shown"]["p50"])
    page.goto(f"{static_url}#/compare?v=1&s=TL")
    page.get_by_test_id("cmp-more").locator("summary").click()
    wf = page.get_by_test_id("cmp-interactions")
    expect(wf.locator(".ec-line")).to_have_text(inter["headline"])
    expect(page.get_by_test_id("cmp-ranking").locator(".cmp-rank-row")).not_to_have_count(0)
    shot(page, "static-compare-files")
    page.goto(f"{static_url}#/method?v=1&s=TL")
    page.get_by_test_id("stress-tests").locator("summary").click()
    expect(page.get_by_test_id("cmp-ablation").locator(".ec-line")).to_have_text(abl["headline"])
    expect(page.get_by_test_id("cmp-matrix").locator(".cmp-cell")).to_have_count(25)
    assert asked and all("/data/TL/comparisons/" in u for u in asked), asked
    assert watch.problems == []
    context.close()


def test_static_site_says_the_comparisons_need_the_local_app(browser, static_url):
    context, page, watch = open_route(
        browser, static_url, "reform", DESKTOP, query="v=1&s=TL&r=adu"
    )
    asked = comparisons_requests(page)
    without_flags(page, static_url, "comparisons")
    # Let the first load finish (map worker included): a reload mid-load cancels requests.
    area = page.locator(".map-area")
    expect(area).not_to_have_attribute("data-map-status", "loading", timeout=60000)
    page.wait_for_load_state("networkidle")
    page.reload()
    page.get_by_test_id("how-much").locator("summary").click()
    expect(page.get_by_test_id("policy-dial")).to_contain_text(NOTE)
    shot(page, "static-reform-note")
    page.goto(f"{static_url}#/compare?v=1&s=TL")
    page.get_by_test_id("cmp-more").locator("summary").click()
    expect(page.get_by_test_id("cmp-interactions")).to_contain_text(NOTE)
    expect(page.get_by_test_id("cmp-ranking")).to_contain_text(NOTE)
    shot(page, "static-compare-note")
    page.goto(f"{static_url}#/method?v=1&s=TL")
    page.get_by_test_id("stress-tests").locator("summary").click()
    expect(page.get_by_test_id("cmp-ablation")).to_contain_text(NOTE)
    expect(page.get_by_test_id("cmp-matrix")).to_contain_text(NOTE)
    assert asked == [], f"the static site asked for comparison files: {asked}"
    assert watch.problems == []
    context.close()


def plain(v: float) -> str:
    """The browser's `plain` (web/js/lib/format.js): three significant figures, commas."""
    if v == 0:
        return "0"
    r = float(f"{v:.3g}")
    return f"{r:,.6f}".rstrip("0").rstrip(".")


def dial_text(row: list) -> str:
    return f"likely {plain(row[1])} to {plain(row[3])}, middle {plain(row[2])}"


def u10_shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / f"u10-{name}.png"))


def test_dial_level_shows_on_the_map_and_back(browser, base_url):
    """A moved dial shows its level's county values on the map, and the scenario comes back.

    2D and 3D, with a chip; "Back to the scenario" and leaving the Reform screen restore.
    """
    dials = api(base_url, "states/TL/comparisons?part=dials")["dials"]["dials"]
    dial = next(d for d in dials.values() if d["reform"] == "adu")
    levels = dial["levels"]
    bench = next(i for i, lv in enumerate(levels) if lv.get("benchmark"))
    step, key = (1, "ArrowRight") if bench + 1 < len(levels) else (-1, "ArrowLeft")
    level = levels[bench + step]
    table = level["counties"]
    assert table["columns"] == ["county", "p10", "p50", "p90"]
    by_fips = {r[0]: r for r in table["rows"]}
    reply = api(base_url, "states/TL/map?s=TL&r=adu")
    # The map's Left / Right keys step through the counties by rank (highest middle first).
    first = max(reply["counties"], key=lambda c: c["added"]["p50"])
    context, page, watch = open_route(
        browser, base_url, "reform", DESKTOP, query="v=1&s=TL&r=adu", reduced_motion="reduce"
    )
    expect(page.get_by_test_id("decision-view")).to_be_visible(timeout=120000)
    page.get_by_test_id("how-much").locator("summary").click()
    card = page.get_by_test_id("policy-dial")
    num = card.locator(".dial-num strong")
    expect(num).to_have_text(levels[bench]["record"]["shown"]["p50"], timeout=SLOW)
    chip = page.get_by_test_id("dial-map-chip")
    expect(chip).to_have_count(0)  # the dial drawing alone does not take the map
    card.locator(".dial-range").focus()
    page.keyboard.press(key)
    expect(num).to_have_text(level["record"]["shown"]["p50"])
    expect(chip).to_be_visible()
    expect(chip).to_contain_text(f"Showing: Accessory dwelling units at {level['label']} (dial)")
    expect(page.locator(".map-legend .dial-key")).to_be_visible()
    # The hover card names the dial level's value for the county pointed at.
    page.locator(".map-canvas canvas").focus()
    page.keyboard.press("ArrowRight")  # one press: the first county, with the card on it
    hover = page.get_by_test_id("dial-hover-value")
    expect(hover).to_have_text(f"Homes added: {dial_text(by_fips[first['fips']])}")
    u10_shot(page, "dial-2d")
    # 3D: the county column's card shows the same level.
    legend = page.locator(".map-legend")
    legend.get_by_role("button", name="3D", exact=True).click()
    page.wait_for_function(shell.PITCH_AT_LEAST, arg=40, timeout=15000)
    page.locator(".map-canvas canvas").focus()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowLeft")
    col = page.locator(".col-card.is-on .map-hover-value")
    expect(col).to_contain_text(f"at {level['label']} (dial)")
    u10_shot(page, "dial-3d")
    legend.get_by_role("button", name="2D", exact=True).click()
    # Back to the scenario: the chip goes and the card shows the scenario's text again.
    chip.get_by_role("button", name="Back to the scenario").click()
    expect(chip).to_have_count(0)
    expect(page.locator(".map-legend .dial-key")).to_have_count(0)
    page.locator(".map-canvas canvas").focus()
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowRight")
    expect(hover).to_have_count(0)
    shown = page.locator(".map-hover.is-on .map-hover-value").first
    expect(shown).to_contain_text(first["added"]["shown"]["p10"])
    u10_shot(page, "back-to-scenario")
    # Moving the dial again shows it again; leaving the Reform screen puts the scenario back.
    card.locator(".dial-range").focus()
    page.keyboard.press(key)
    page.keyboard.press("ArrowLeft" if key == "ArrowRight" else "ArrowRight")
    expect(chip).to_be_visible()
    page.locator("nav.switcher").get_by_role("link", name="State", exact=True).click()
    expect(chip).to_have_count(0)
    assert watch.problems == []
    context.close()
