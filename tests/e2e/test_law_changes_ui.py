"""End-to-end: "What this law changes" on the Reform screen (Testland).

- the map mode switch: "What this law changes" turns the map key into the four answers, with
  the statewide share of each, exactly the API's text (GET /states/TL/law-changes?r=adu);
- the value switch: legal additional capacity and additional units authorized, both the
  server's text;
- clicking a county on the map opens its card (answer, proposal, effective date,
  denominator, observed or fallback share, model stage), the API's values;
- the table twin lists every county with the same answer and shares, and a county name in it
  opens the same card; keyboard only;
- "Read next" asks for the bounding runs and shows the reply (Testland has no law table:
  its note);
- the static site without the exported file says it needs the local app.

Screenshots go to ``nopush/logs/shots/`` (git-ignored), or to ``$VH_SHOTS`` when set.
"""

from __future__ import annotations

import os
from pathlib import Path

import test_shell as shell
from playwright.sync_api import expect
from test_shell import DESKTOP, SHOTS, api, open_route, wait_results

base_url = shell.base_url
static_url = shell.static_url
browser = shell.browser

OUT = Path(os.environ.get("VH_SHOTS") or SHOTS)
ORDER = ["already_allowed", "changed_condition", "newly_allowed", "unresolved"]


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / f"u13-{name}.png"))


def changes(base: str) -> dict:
    return api(base, "states/TL/law-changes?r=adu")["changes"]


def turn_on(page) -> None:
    toggle = page.get_by_test_id("law-mode")
    expect(toggle).to_have_attribute("aria-pressed", "false")
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_test_id("law-key")).to_be_visible(timeout=60000)


def test_mode_switch_key_and_values(browser, base_url):
    context, page, watch = open_route(browser, base_url, "reform", DESKTOP, query="v=1&s=TL&r=adu")
    wait_results(page)
    turn_on(page)
    d = changes(base_url)
    key = page.get_by_test_id("law-key")
    for cid in ORDER:
        row = key.locator(f".law-key-row[data-answer={cid}]")
        word = next(c["word"] for c in d["classes"] if c["id"] == cid)
        expect(row).to_contain_text(word)
        expect(row.locator(".law-key-share")).to_have_text(
            d["statewide"]["classes"][cid]["share"]["shown"]
        )
    units = d["statewide"]["units"]["shown"]
    total = page.get_by_test_id("law-total")
    expect(total).to_contain_text(f"{units['p10']} to {units['p90']}")
    key.get_by_role("button", name="Legal capacity").click()
    expect(total).to_contain_text(d["statewide"]["capacity"]["text"])
    shot(page, "key-capacity")
    key.get_by_role("button", name="Units authorized").click()
    expect(total).to_contain_text(units["p10"])
    # Off again: the usual key comes back.
    page.get_by_test_id("law-mode").click()
    expect(page.get_by_test_id("law-key")).to_have_count(0)
    assert watch.problems == []
    context.close()


def test_map_click_opens_the_card(browser, base_url):
    context, page, watch = open_route(browser, base_url, "reform", DESKTOP, query="v=1&s=TL&r=adu")
    wait_results(page)
    turn_on(page)
    d = changes(base_url)
    page.wait_for_timeout(1200)  # the camera settles on the state
    # Find a point over a county in the free map area (the hover card lights up), then click.
    left = page.locator("[data-zone=left]").bounding_box()
    right = page.locator("[data-zone=right]").bounding_box()
    x0, x1 = left["x"] + left["width"] + 20, right["x"] - 20
    hover = page.locator(".map-hover")
    spot = None
    for fy in (0.5, 0.4, 0.6, 0.3, 0.7):
        for fx in (0.5, 0.4, 0.6, 0.3, 0.7):
            x, y = x0 + fx * (x1 - x0), DESKTOP["height"] * fy
            page.mouse.move(x, y)
            page.wait_for_timeout(150)
            if "is-on" in (hover.get_attribute("class") or ""):
                spot = (x, y)
                break
        if spot:
            break
    assert spot, "no county under the pointer in the free map area"
    page.mouse.click(*spot)
    card = page.get_by_test_id("law-card")
    expect(card).to_be_visible(timeout=30000)
    fips = card.get_attribute("data-fips")
    c = next(c for c in d["counties"] if c["fips"] == fips)
    expect(card.locator("#law-card-title")).to_have_text(c["name"])
    word = next(k["word"] for k in d["classes"] if k["id"] == c["answer"])
    expect(card.locator(".law-answer")).to_contain_text(word)
    expect(card).to_contain_text(d["provision"]["effective_text"])
    expect(card).to_contain_text(c["denominator"]["shown"])
    expect(card).to_contain_text(c["observed"]["text"])
    for cid in ORDER:
        r = card.locator(f".law-shares tr[data-answer={cid}]")
        expect(r.locator("td.num")).to_have_text(c["classes"][cid]["share"]["shown"])
        expect(r.locator(".law-stage")).to_have_text(c["stage"][cid])
    shot(page, "card")
    page.keyboard.press("Escape")
    expect(card).to_have_count(0)
    assert watch.problems == []
    context.close()


def test_table_twin_by_keyboard(browser, base_url):
    context, page, watch = open_route(browser, base_url, "reform", DESKTOP, query="v=1&s=TL&r=adu")
    wait_results(page)
    turn_on(page)
    d = changes(base_url)
    page.get_by_role("button", name="County table").click()
    table = page.get_by_test_id("law-table")
    expect(table).to_be_visible(timeout=30000)
    rows = table.locator("tbody tr")
    expect(rows).to_have_count(len(d["counties"]))
    for c in d["counties"]:
        row = table.locator(f"tbody tr[data-fips='{c['fips']}']")
        word = next(k["word"] for k in d["classes"] if k["id"] == c["answer"])
        expect(row.locator(".class-chip")).to_have_text(word)
        cells = row.locator("td.num")
        for i, cid in enumerate(ORDER):
            expect(cells.nth(i)).to_have_text(c["classes"][cid]["share"]["shown"])
    shot(page, "table")
    first = d["counties"][0]
    link = table.get_by_role("button", name=first["name"])
    link.focus()
    page.keyboard.press("Enter")
    card = page.get_by_test_id("law-card")
    expect(card).to_be_visible()
    expect(card.locator("#law-card-title")).to_have_text(first["name"])
    assert watch.problems == []
    context.close()


def test_read_next_list(browser, base_url):
    context, page, watch = open_route(browser, base_url, "reform", DESKTOP, query="v=1&s=TL&r=adu")
    wait_results(page)
    turn_on(page)
    box = page.get_by_test_id("read-next")
    box.get_by_test_id("read-next-run").click()
    rn = api(base_url, "states/TL/law-changes/read-next?r=adu")["read_next"]
    expect(box).to_contain_text(rn["note"], timeout=60000)
    shot(page, "read-next")
    assert watch.problems == []
    context.close()


def test_static_needs_local_app(browser, static_url):
    context, page, _watch = open_route(
        browser, static_url, "reform", DESKTOP, query="v=1&s=TL&r=adu"
    )
    expect(page.get_by_test_id("law-mode")).to_be_visible(timeout=30000)
    page.get_by_test_id("law-mode").click()
    expect(page.get_by_test_id("law-key")).to_contain_text("needs the local app", timeout=30000)
    context.close()
