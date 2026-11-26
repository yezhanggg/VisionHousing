"""End-to-end: the guided tour, mounted in the shell (web/js/ui/tour.js, ui/shell.js).

In a real browser (Chromium through Playwright, headless) on Testland it checks that:

- the first visit to the State screen shows a small offer ("New here? Take the tour") that
  never opens the tour by itself, sits clear of every floating zone (chat box and legend
  included), and shows once: "Not now" remembers it, and so does finishing the tour;
- the left panel's "Take the tour" button opens the tour by keyboard; Enter and the arrow
  keys step through all seven steps; each card stays inside the window and off its
  spotlight; Esc closes the tour, gives focus back to the button and sets the seen flag;
- the intro page's "Take the tour" opens the tool with the tour running;
- on a phone the tour pulls the sheet up and every step's card fits the screen.

Screenshots go to ``nopush/logs/shots/`` (git-ignored).
"""

from __future__ import annotations

import re

import test_shell as shell
from playwright.sync_api import expect
from test_shell import DESKTOP, PHONE, ZONES_JS, open_route, shoot, wait_results

# The shell tests' fixtures: one build and one server per module, the same headless browser.
base_url = shell.base_url
browser = shell.browser

STEPS = 7
KEY = "vh.tour.seen.v1"

BOXES_JS = """() => {
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    return { l: r.left, t: r.top, r: r.right, b: r.bottom }; };
  return { card: box(document.querySelector('.tour-card')),
    hole: box(document.querySelector('.tour-hole')),
    view: { w: innerWidth, h: innerHeight } }; }"""


def apart(a: dict, b: dict) -> bool:
    return (
        a["r"] <= b["l"] + 1 or b["r"] <= a["l"] + 1 or a["b"] <= b["t"] + 1 or b["b"] <= a["t"] + 1
    )


def check_card(page) -> None:
    """The card is inside the window and does not cover the spotlight around its target."""
    page.wait_for_timeout(450)  # the tour measures again 360 ms after each step
    g = page.evaluate(BOXES_JS)
    card, view = g["card"], g["view"]
    assert card["l"] >= -1 and card["t"] >= -1, g
    assert card["r"] <= view["w"] + 1 and card["b"] <= view["h"] + 1, g
    if g["hole"]:
        assert apart(card, g["hole"]), g


def step_through(page) -> list[str]:
    """Walk all seven steps by keyboard (Enter, then the arrows); return the step ids."""
    ids = []
    card = page.locator(".tour-card")
    for k in range(1, STEPS + 1):
        expect(card.locator(".tour-count")).to_have_text(f"Step {k} of {STEPS}")
        ids.append(page.locator(".tour").get_attribute("data-step"))
        check_card(page)
        if k == STEPS:
            break
        if k == 3:
            # Back and forward again with the arrow keys.
            page.keyboard.press("ArrowLeft")
            expect(card.locator(".tour-count")).to_have_text(f"Step {k - 1} of {STEPS}")
            page.keyboard.press("ArrowRight")
            expect(card.locator(".tour-count")).to_have_text(f"Step {k} of {STEPS}")
        page.keyboard.press("Enter" if k % 2 else "ArrowRight")
    expect(card.get_by_role("button", name="Done")).to_be_focused()
    return ids


def test_tour_opens_from_the_button_and_steps_by_keyboard(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    wait_results(page)
    # The first visit offers the tour; it does not start it.
    offer = page.locator(".tour-offer")
    expect(offer).to_be_visible()
    expect(page.locator(".tour")).to_have_count(0)
    box = offer.bounding_box()
    o = {"l": box["x"], "t": box["y"], "r": box["x"] + box["width"], "b": box["y"] + box["height"]}
    for z in page.evaluate(ZONES_JS):
        if z["zone"] != "switch":
            assert apart(o, z), (z, o)
    shoot(page, "desktop-tour-offer")

    button = page.locator('[data-zone="left"] .dock-actions').get_by_role(
        "button", name="Take the tour"
    )
    button.focus()
    page.keyboard.press("Enter")
    expect(page.locator(".tour-card[role=dialog]")).to_be_visible()
    expect(offer).to_have_count(0)
    ids = step_through(page)
    assert ids == ["pick", "range", "why", "where", "trust", "limits", "share"]
    # Every step found its element on the State screen: none fell back to the middle.
    shoot(page, "desktop-tour-last-step")
    # Tab stays inside the card.
    for _ in range(4):
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.closest('.tour-card') !== null")
    page.keyboard.press("Escape")
    expect(page.locator(".tour")).to_have_count(0)
    expect(button).to_be_focused()
    assert page.evaluate(f"localStorage.getItem('{KEY}')") == "1"
    # Seen: the offer does not come back.
    page.reload()
    wait_results(page)
    page.wait_for_timeout(300)
    expect(page.locator(".tour-offer")).to_have_count(0)
    assert watch.problems == []
    context.close()


def test_every_step_spotlights_its_element(browser, base_url):
    """On the State screen with results, each of the seven steps has a spotlight."""
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    wait_results(page)
    page.locator('[data-zone="left"] .tour-btn').click()
    for k in range(1, STEPS + 1):
        expect(page.locator(".tour-count")).to_have_text(f"Step {k} of {STEPS}")
        expect(page.locator(".tour-hole")).to_have_count(1)
        page.keyboard.press("ArrowRight")
    # Right on the last step is Done: the tour closes and remembers.
    expect(page.locator(".tour")).to_have_count(0)
    assert page.evaluate(f"localStorage.getItem('{KEY}')") == "1"
    assert watch.problems == []
    context.close()


def test_first_visit_offer_shows_once(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    wait_results(page)
    offer = page.locator(".tour-offer")
    expect(offer).to_be_visible()
    expect(offer).to_have_count(1)
    # Other modes do not offer it.
    page.locator("nav.switcher").get_by_role("link", name="Compare", exact=True).click()
    expect(offer).to_have_count(0)
    page.locator("nav.switcher").get_by_role("link", name="State", exact=True).click()
    expect(offer).to_be_visible()
    offer.get_by_role("button", name="Not now").click()
    expect(offer).to_have_count(0)
    expect(page.locator(".tour")).to_have_count(0)
    assert page.evaluate(f"localStorage.getItem('{KEY}')") == "1"
    page.reload()
    wait_results(page)
    page.wait_for_timeout(300)
    expect(offer).to_have_count(0)
    # The panel's button still opens the tour whenever the reader wants it.
    page.locator('[data-zone="left"] .tour-btn').click()
    expect(page.locator(".tour-card")).to_be_visible()
    # Leaving Compare mid-run cancels its requests; a cancelled request is not a fault.
    assert [p for p in watch.problems if not p.startswith("failed:")] == []
    context.close()


def test_offer_starts_the_tour_and_focus_lands_on_the_panel_button(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP)
    wait_results(page)
    page.locator(".tour-offer").get_by_role("button", name="Take the tour").click()
    expect(page.locator(".tour-card")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".tour")).to_have_count(0)
    expect(page.locator('[data-zone="left"] .tour-btn')).to_be_focused()
    assert watch.problems == []
    context.close()


def test_intro_take_the_tour_opens_the_tool_with_the_tour(browser, base_url):
    context = browser.new_context(viewport=DESKTOP, reduced_motion="reduce")
    page = context.new_page()
    page.goto(base_url)
    expect(page.locator("main.intro")).to_be_visible()
    page.get_by_role("button", name="Take the tour").click()
    expect(page).to_have_url(re.compile(r"#/state\?.*r=adu"))
    expect(page.locator(".tour-card")).to_be_visible(timeout=15000)
    expect(page.locator(".tour-count")).to_have_text(f"Step 1 of {STEPS}")
    page.keyboard.press("Escape")
    expect(page.locator(".tour")).to_have_count(0)
    context.close()


def test_tour_fits_a_phone(browser, base_url):
    context, page, watch = open_route(
        browser, base_url, "state", PHONE, is_mobile=True, has_touch=True
    )
    wait_results(page)
    expect(page.locator(".tour-offer")).to_be_visible()
    page.locator(".tour-offer").get_by_role("button", name="Take the tour").click()
    expect(page.locator(".sheet-handle")).to_have_attribute("aria-expanded", "true")
    for k in range(1, STEPS + 1):
        expect(page.locator(".tour-count")).to_have_text(f"Step {k} of {STEPS}")
        check_card(page)
        if k == 4:
            shoot(page, "phone-tour-step-4")
        page.keyboard.press("Enter")
    expect(page.locator(".tour")).to_have_count(0)
    assert watch.problems == []
    context.close()
