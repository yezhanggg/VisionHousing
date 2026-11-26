"""End-to-end: interface fixes found while recording the demo (headless Chromium, Testland).

- the Explain popover opens inside the window and is not clipped, from a number in the right
  panel and in the left panel (the panels are glass cards that would otherwise offset or clip
  it), and it keeps its keyboard order: Tab goes into it, Esc closes it and returns focus;
- the decision view's "By year" chart stays inside its own step at 1280, 1920 and phone width;
- switching the state drops the old state's numbers at once and shows the loading state;
- "Try it at its high value" keeps naming the input it moved, flags the headline
  ("Assumption changed: ..."), and "Back to the benchmark" undoes exactly that change;
- the Reform screen's funnel card stacks its words over its value in a narrow panel;
- the data report card's rows are compact (one block per input);
- the Method screen's left panel lists the rules by their plain names on the rule contest's
  own measure, with the server's text;
- the map key gives "No baseline" its own full-width row.

Screenshots go to ``nopush/logs/shots/`` (git-ignored), or to ``$VH_SHOTS`` when set.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import test_shell as shell
from playwright.sync_api import expect
from test_shell import DESKTOP, PHONE, SHOTS, api, open_route, run_of, wait_results

base_url = shell.base_url
browser = shell.browser

OUT = Path(os.environ.get("VH_SHOTS") or SHOTS)


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(250)
    page.screenshot(path=str(OUT / f"demo-fix-{name}.png"))


def inside_window(page, box: dict) -> None:
    vp = page.viewport_size
    assert box["x"] >= 0 and box["y"] >= 0, box
    assert box["x"] + box["width"] <= vp["width"] + 1, (box, vp)
    assert box["y"] + box["height"] <= vp["height"] + 1, (box, vp)


def not_clipped(page, selector: str) -> None:
    """The element is drawn on top at its middle (no panel clips or covers it)."""
    hit = page.evaluate(
        """(sel) => {
          const el = document.querySelector(sel);
          const r = el.getBoundingClientRect();
          const y = r.top + Math.min(40, r.height / 2);
          const at = document.elementFromPoint(r.left + r.width / 2, y);
          return Boolean(at && el.contains(at));
        }""",
        selector,
    )
    assert hit, f"{selector} is clipped or covered"


def test_explain_popover_stays_in_the_window_from_both_panels(browser, base_url):
    for width in (1920, 1280):
        context, page, watch = open_route(
            browser, base_url, "state", {"width": width, "height": 900}, query="v=1&s=TL&r=adu"
        )
        wait_results(page)
        # Right panel: a number in the key figures.
        num = page.locator("[data-zone=right] .headline-row .num-btn").first
        num.scroll_into_view_if_needed()
        num.focus()
        page.keyboard.press("Enter")
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        inside_window(page, dialog.bounding_box())
        not_clipped(page, ".pop-panel")
        # Keyboard order: Tab goes into the panel, Esc closes it and gives focus back.
        page.keyboard.press("Tab")
        assert page.evaluate("Boolean(document.activeElement.closest('.pop-panel'))")
        page.keyboard.press("Escape")
        expect(dialog).to_have_count(0)
        expect(num).to_be_focused()
        if width == 1920:
            shot(page, "popover-right")
        assert watch.problems == []
        context.close()
    # Left panel: the Reform screen's headline number.
    context, page, watch = open_route(
        browser, base_url, "reform", {"width": 1920, "height": 1080}, query="v=1&s=TL&r=adu"
    )
    wait_results(page)
    num = page.locator("[data-zone=left] .num-btn").first
    num.click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    inside_window(page, dialog.bounding_box())
    not_clipped(page, ".pop-panel")
    shot(page, "popover-left")
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    assert watch.problems == []
    context.close()


def test_year_chart_stays_inside_its_step(browser, base_url):
    d = api(base_url, "states/TL/map?s=TL&r=adu")["decision"]
    for size in ({"width": 1280, "height": 800}, {"width": 1920, "height": 1080}, PHONE):
        context, page, watch = open_route(browser, base_url, "reform", size, query="v=1&s=TL&r=adu")
        view = page.get_by_test_id("decision-view")
        expect(view).to_be_visible(timeout=120000)
        if not d["annual"]:
            context.close()
            continue
        chart = view.locator(".year-cols")
        chart.scroll_into_view_if_needed()
        expect(view.locator(".year-value")).to_have_text(
            [y["shown"]["p50"] for y in d["annual"]["years"]]
        )
        answer = view.locator("[data-step=answer]").bounding_box()
        where = view.locator("[data-step=where]").bounding_box()
        box = chart.bounding_box()
        assert box["height"] < 160, box
        assert box["y"] + box["height"] <= where["y"], (box, where)
        assert box["x"] + box["width"] <= answer["x"] + answer["width"] + 1, (box, answer)
        assert page.evaluate("document.documentElement.scrollWidth") <= size["width"]
        # Every column (and its value and year) sits inside the chart's box.
        for bar in view.locator(".year-bar").all():
            b = bar.bounding_box()
            assert b["y"] >= box["y"] - 1 and b["y"] + b["height"] <= box["y"] + box["height"] + 1
        shot(page, f"year-chart-{size['width']}")
        assert watch.problems == []
        context.close()


def test_switching_state_drops_the_old_numbers_at_once(browser, base_url):
    context, page, _watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu")
    wait_results(page)
    old = page.get_by_test_id("headline-sentence").inner_text()

    # Hold every request for the next state: the screen must not wait for it to clear.
    def hold(route):
        body = route.request.post_data or ""
        if "VT" in route.request.url or '"VT"' in body:
            return  # left pending on purpose
        route.continue_()

    page.route(re.compile(r".*/api/v1/.*"), hold)
    page.evaluate("location.hash = location.hash.replace('s=TL', 's=VT')")
    right = page.locator("[data-zone=right]")
    expect(right.locator(".status-line.is-loading")).to_be_visible(timeout=2000)
    expect(page.get_by_test_id("headline-sentence")).to_have_count(0)
    assert old not in right.inner_text()
    shot(page, "state-switch-loading")
    context.close()


def test_try_high_value_keeps_its_input_and_flags_the_headline(browser, base_url):
    context, page, watch = open_route(browser, base_url, "reform", DESKTOP, query="v=1&s=TL&r=adu")
    view = page.get_by_test_id("decision-view")
    expect(view).to_be_visible(timeout=120000)
    run = run_of(base_url, ["adu"])
    lever = next(
        x
        for x in run["explanation"]["narrowing"]["adu"]["items"]
        if re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", x["parameter"])
        and not x["parameter"].startswith("engine.")
    )
    change = view.locator("[data-step=change]")
    expect(change).to_contain_text(lever["parameter"])
    expect(page.get_by_test_id("assumption-flag")).to_have_count(0)
    change.locator(".decision-try").click()
    expect(page).to_have_url(re.compile(rf"a=[^&]*{re.escape(lever['parameter'])}~h"))
    # The varied run arrives; the step still names the moved input, the button goes back.
    btn = change.locator(".decision-try")
    expect(btn).to_have_text("Back to the benchmark", timeout=120000)
    expect(btn).to_have_attribute("aria-pressed", "true")
    expect(change).to_contain_text(lever["parameter"])
    flags = page.get_by_test_id("assumption-flag")
    expect(flags.first).to_contain_text(f"Assumption changed: {lever['label']} at its high value")
    expect(page.locator("[data-zone=left] [data-testid=assumption-flag]")).to_have_count(1)
    shot(page, "try-high")
    btn.click()
    expect(page).not_to_have_url(re.compile(r"~h"))
    expect(page.get_by_test_id("assumption-flag")).to_have_count(0, timeout=120000)
    expect(change.locator(".decision-try")).to_have_text("Try it at its high value (same futures)")
    assert watch.problems == []
    context.close()


def test_funnel_card_stacks_in_the_narrow_panel(browser, base_url):
    context, page, watch = open_route(
        browser, base_url, "reform", {"width": 1280, "height": 800}, query="v=1&s=TL&r=adu"
    )
    wait_results(page)
    steps = page.locator("[data-zone=left] #funnel .funnel-step")
    expect(steps.first).to_be_visible(timeout=60000)
    steps.first.scroll_into_view_if_needed()
    card = page.locator("[data-zone=left] #funnel").bounding_box()
    for step in steps.all():
        text = step.locator(".funnel-text").bounding_box()
        value = step.locator(".funnel-value").bounding_box()
        # The words take most of the card; the value sits under them, full width.
        assert text["width"] >= 0.5 * card["width"], (text, card)
        assert value["y"] >= text["y"] + text["height"] - 1, (text, value)
    shot(page, "funnel-card")
    assert watch.problems == []
    context.close()


def test_report_card_rows_are_compact(browser, base_url):
    context, page, watch = open_route(browser, base_url, "method", DESKTOP)
    rows = page.locator("#report-card tbody tr")
    expect(rows.first).to_be_visible(timeout=30000)
    card = api(base_url, "report-card?state=TL")
    expect(rows).to_have_count(len(card["inputs"]))
    rows.first.scroll_into_view_if_needed()
    table = page.get_by_test_id("report-table").bounding_box()
    for row, item in zip(rows.all(), card["inputs"], strict=True):
        name = row.locator("th[scope=row]")
        expect(name).to_have_text(item["label"])
        # The name runs the full row width; the facts sit on one short line under it.
        assert name.bounding_box()["width"] >= 0.8 * table["width"] - 30
        tier = row.locator("td[data-label=Tier]").bounding_box()
        cover = row.locator("td[data-label=Coverage]").bounding_box()
        assert abs(tier["y"] - cover["y"]) < 24, (tier, cover)
    shot(page, "report-card")
    assert watch.problems == []
    context.close()


def test_method_left_panel_uses_the_contest_names_and_measure(browser, base_url):
    context, page, watch = open_route(browser, base_url, "method", DESKTOP)
    val = api(base_url, "validation?state=TL")
    view = val["contest_view"]
    left = page.locator("[data-zone=left]")
    expect(left.get_by_test_id("method-lead").locator(".lead-number")).to_have_text(
        view["used_text"], timeout=30000
    )
    labels = [f"{r['name']}{' (used)' if r['used'] else ''}" for r in view["rules"]]
    expect(left.locator(".hbar-label")).to_have_text(labels)
    expect(left.locator(".hbar-value")).to_have_text([r["text"] for r in view["rules"]])
    for r in view["rules"]:
        assert r["rule"] not in left.locator(".hbar-label").all_inner_texts()
    if view["note"]:
        expect(left.get_by_test_id("method-rule-note")).to_have_text(view["note"])
    # The contest table shows the same text for each rule's miss.
    table = page.get_by_test_id("contest-table")
    for r in view["rules"]:
        expect(table.locator(f"tr[data-rule={r['rule']}] td").first).to_have_text(r["text"])
    assert watch.problems == []
    context.close()


def test_no_baseline_class_has_its_own_row_in_the_map_key(browser, base_url):
    """A county with no baseline (none in Testland, so one is made in the reply here)."""
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu")

    def with_no_base(route):
        reply = route.fetch()
        data = json.loads(reply.text())
        if data.get("classes") and data.get("counties"):
            data["classes"].append(
                {
                    "id": "no_base",
                    "word": "No baseline",
                    "label": "adds homes; no baseline to compare",
                }
            )
            data["counties"][0]["class"] = "no_base"
        route.fulfill(response=reply, body=json.dumps(data))

    page.route(re.compile(r".*/api/v1/states/TL/map\?.*"), with_no_base)
    page.reload()
    wait_results(page)
    key = page.locator(".map-legend")
    nb = key.locator(".key-step[data-class=no_base]")
    expect(nb).to_be_visible(timeout=30000)
    ramp = key.locator(".key-ramp").bounding_box()
    box = nb.bounding_box()
    assert box["width"] >= 0.9 * ramp["width"], (box, ramp)
    # Its words sit on one or two lines, not a narrow column.
    assert nb.locator(".key-label").bounding_box()["height"] <= 40
    first = key.locator(".key-step").first.bounding_box()
    assert box["y"] > first["y"] + 4  # a row of its own, under the ramp
    shot(page, "map-key-no-baseline")
    assert watch.problems == []
    context.close()
