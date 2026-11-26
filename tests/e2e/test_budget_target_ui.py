"""End-to-end: the budget-and-target panel, the draw-share wording and the cycle preset link.

On Testland, live (the back end) and static (the exported files). The live server reads a
small grid setting (targets Testland can reach) instead of the Pennsylvania one, so the cells
are not all zero; the panel reads whatever grid the server holds.

- inputs -> cells and flags: for a target, a budget and an accepted share typed into the panel
  (keyboard), the rows marked "meets", "below" and "over budget" are exactly the API's options
  and over-budget cells for the same question (GET /states/TL/adu-budget-grid?target=&budget=
  &accept=), in the API's order, and every number in a row is the server's text;
- Pareto: the chart's table twin lists the API's Pareto set for that question, cheapest first;
- bracket: a target between two tested ones is shown as the two tested cells, never a third,
  and the API also has no option for it;
- the ADU Decision tab's "Budget and target" link opens the panel on the Compare screen; the
  "How sure" chart says "share of modeled draws" with the standard error the server wrote;
- share links: `preset=high` survives the canonical address, is sent in the run request, and
  the cycle buttons change it (central is not written); the static site shows a non-central
  cycle as a custom scenario; the live hearing brief link of a two-reform mix opens a page;
- static: without the exported grid the panel says it needs the local app; a custom scenario's
  Assumptions line no longer says the site shows the default assumptions.

Screenshots go to ``nopush/logs/shots/`` (git-ignored), or to ``$VH_SHOTS`` when set.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

import pytest
import test_shell as shell
from playwright.sync_api import expect
from test_shell import DESKTOP, PHONE, SHOTS, api, no_sideways_scroll, open_route, wait_results

from visionhousing.api.budget_view import cell_key

base_url = shell.base_url
static_url = shell.static_url
browser = shell.browser

OUT = Path(os.environ.get("VH_SHOTS") or SHOTS)
GRID_YAML = """
version: 1
id: adu_budget_grid_e2e
label: "Testland settings for the end-to-end test"
variant: adu_finance_budget
arms: [law_only, finance_only, both]
appropriations: [2000000, 20000000]
awards: [10000, 20000]
loan_share: 0
targets: [10, 50, 100, 150]
"""


@pytest.fixture(scope="module", autouse=True)
def small_grid(tmp_path_factory):
    """The live server's grid setting for this module (Testland-sized targets), no disk cache."""
    from visionhousing.scenarios import adu_budget

    path = tmp_path_factory.mktemp("grid") / "adu_budget_grid.yaml"
    path.write_text(GRID_YAML, encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(adu_budget, "GRID_PATH", path)
        mp.setenv("VISIONHOUSING_COMPARISONS_DIR", "none")
        yield


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / f"u14-{name}.png"), full_page=False)


def grid_reply(base: str, **q) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in q.items() if v is not None)
    return api(base, f"states/TL/adu-budget-grid{'?' + qs if qs else ''}")


def open_panel(browser, base: str, viewport=DESKTOP):
    context, page, watch = open_route(browser, base, "compare", viewport, query="v=1&s=TL")
    wait_results(page)
    fold = page.get_by_test_id("budget-fold")
    fold.locator("summary").click()
    card = page.get_by_test_id("budget-target")
    expect(card.get_by_test_id("bt-target")).to_be_visible(timeout=120000)
    return context, page, watch, card


def type_into(page, locator, text: str) -> None:
    """Keyboard only: focus, select all, type."""
    locator.focus()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.press("Backspace")
    if text:
        page.keyboard.type(text)


def set_inputs(page, card, target: str, budget: str, accept: str) -> None:
    type_into(page, card.get_by_test_id("bt-target"), target)
    type_into(page, card.get_by_test_id("bt-budget"), budget)
    card.get_by_test_id("bt-accept").select_option(accept)


def test_inputs_give_the_api_cells_flags_and_pareto(browser, base_url):
    context, page, watch, card = open_panel(browser, base_url)
    set_inputs(page, card, "50", "2000000", "0.5")
    reply = grid_reply(base_url, target=50, budget=2000000, accept=0.5)
    q, view = reply["query"], reply["view"]
    texts = {c["key"]: c for c in view["cells"]}
    block = card.get_by_test_id("bt-block")
    expect(block).to_have_count(1)
    rows = block.locator("tr.bt-row")
    meets = [cell_key(o) for o in q["options"]]
    over = [cell_key(o) for o in q["over_budget"]]
    expect(block.locator("tr.bt-row[data-status=meets]")).to_have_count(len(meets))
    keys = rows.evaluate_all("rs => rs.map(r => [r.dataset.key, r.dataset.status])")
    assert [k for k, s in keys if s == "meets"] == meets
    assert [k for k, s in keys if s == "over"] == over
    # Every number in a row is the server's text for that cell.
    for k, _s in keys:
        c = texts[k]
        row = block.locator(f'tr.bt-row[data-key="{k}"]')
        expect(row.locator("[data-col=share] strong")).to_have_text(
            c["targets"]["50"]["share_text"]
        )
        expect(row.locator("[data-col=share]")).to_contain_text(c["targets"]["50"]["se_text"])
        expect(row.locator("[data-col=added]")).to_contain_text(c["added"]["likely"])
        expect(row.locator("[data-col=cost]")).to_contain_text(c["net_cost"]["shown"]["p50"])
        expect(row.locator("[data-col=moved]")).to_have_text(c["moved"]["shown"]["p50"])
    # Flags: the API's, each with its own mark.
    flags = card.get_by_test_id("bt-flags").locator(".bt-flag")
    shown = flags.evaluate_all("fs => fs.map(f => f.dataset.flag)")
    assert sorted(shown) == sorted(q["flags"])
    for f in q["flags"]:
        expect(card.locator(f'.bt-flag[data-flag="{f}"]')).to_contain_text(view["flags"][f])
    # Pareto: the chart's table twin is the API's Pareto set, cheapest first.
    pareto = sorted(q["pareto"], key=lambda o: o["net_public_cost_p50"])
    twin = block.locator(".bt-chart")
    twin.get_by_role("button", name="Table").press("Enter")
    expect(twin.locator(".chart-table tbody tr")).to_have_count(len(pareto))
    for i, o in enumerate(pareto):
        c = texts[cell_key(o)]
        expect(twin.locator(".chart-table tbody tr").nth(i)).to_contain_text(
            c["targets"]["50"]["share_text"]
        )
    expect(card.get_by_test_id("bt-result")).to_have_attribute("aria-live", "polite")
    shot(page, "panel")
    assert watch.problems == []
    context.close()


def test_between_tested_targets_is_a_bracket(browser, base_url):
    context, page, watch, card = open_panel(browser, base_url)
    set_inputs(page, card, "30", "", "0")
    q = grid_reply(base_url, target=30)["query"]
    assert q["flags"] == ["between_tested_values"] and "options" not in q
    expect(card.get_by_test_id("bt-bracket-target")).to_contain_text("10 and 50")
    blocks = card.get_by_test_id("bt-block")
    expect(blocks).to_have_count(2)
    assert blocks.evaluate_all("bs => bs.map(b => b.dataset.target)") == ["10", "50"]
    expect(card.locator('[data-target="30"]')).to_have_count(0)
    shot(page, "bracket")
    # Outside the tested range and a missing input each get their own flag.
    type_into(page, card.get_by_test_id("bt-target"), "99999")
    expect(card.locator('.bt-flag[data-flag="outside_tested_range"]')).to_be_visible()
    type_into(page, card.get_by_test_id("bt-target"), "")
    expect(card.locator('.bt-flag[data-flag="input_missing"]')).to_be_visible()
    assert watch.problems == []
    context.close()


def test_phone_width_has_no_sideways_scroll(browser, base_url):
    context, page, _watch, _card = open_panel(browser, base_url, viewport=PHONE)
    no_sideways_scroll(page, PHONE["width"])
    shot(page, "phone")
    context.close()


def test_decision_tab_link_and_draw_share_wording(browser, base_url):
    context, page, watch = open_route(browser, base_url, "reform", DESKTOP, query="v=1&s=TL&r=adu")
    wait_results(page)
    sure = page.get_by_test_id("readouts")
    expect(sure.locator(".chart-title")).to_contain_text("Share of modeled draws")
    run = api(base_url, "run", {"state": "TL", "reforms": ["adu"], "overrides": {}, "seed": 0})
    chances = run["explanation"]["readouts"]["adu"]["chances"]
    se = [next(c["shown"] for c in x["text"]["cites"] if c["ref"].endswith("_se")) for x in chances]
    sure.get_by_role("button", name="Table").first.click()
    head = sure.locator(".chart-table thead").first
    expect(head).to_contain_text("Share of modeled draws")
    expect(head).to_contain_text("Monte Carlo standard error")
    for s in se:
        expect(sure.locator(".chart-table tbody").first).to_contain_text(f"{s} percentage points")
    shot(page, "how-sure")
    link = page.get_by_test_id("budget-link").get_by_role("link")
    link.click()
    expect(page).to_have_url(re.compile(r"#/compare\?"))
    expect(page.get_by_test_id("budget-fold")).to_have_attribute("open", "")
    expect(page.get_by_test_id("budget-target")).to_be_visible(timeout=120000)
    assert watch.problems == []
    context.close()


def test_cycle_preset_round_trips_in_the_link(browser, base_url):
    bodies: list[dict] = []
    context, page, watch = open_route(
        browser, base_url, "reform", DESKTOP, query="v=1&s=TL&r=adu&preset=high"
    )
    page.on(
        "request",
        lambda r: r.url.endswith("/api/v1/run") and bodies.append(json.loads(r.post_data or "{}")),
    )
    expect(page).to_have_url(re.compile(r"preset=high"))
    page.get_by_role("tab", name="Assumptions").click()
    choice = page.get_by_test_id("cycle-choice")
    expect(choice.locator("[data-preset=high]")).to_have_attribute("aria-pressed", "true")
    choice.locator("[data-preset=low]").press("Enter")
    expect(page).to_have_url(re.compile(r"preset=low$"))
    wait_results(page)
    assert any(b.get("preset") == "low" for b in bodies)
    choice.locator("[data-preset=central]").click()
    expect(page).not_to_have_url(re.compile(r"preset="))
    # The hearing brief of a two-reform mix: the live server writes it.
    with urllib.request.urlopen(  # noqa: S310 (a local test server)
        f"{base_url}api/v1/export/hearing?s=TL&r=adu,fees&preset=high", timeout=120
    ) as reply:
        assert reply.status == 200 and "text/html" in reply.headers["content-type"]
    assert watch.problems == []
    context.close()


def test_static_needs_the_local_app_and_custom_cycle(browser, static_url):
    context, page, _watch = open_route(browser, static_url, "compare", DESKTOP, query="v=1&s=TL")
    wait_results(page)
    page.get_by_test_id("budget-fold").locator("summary").click()
    expect(page.get_by_test_id("budget-target")).to_contain_text("needs the local app")
    shot(page, "static-local-app")
    context.close()
    query = "v=1&s=TL&r=adu&preset=high"
    context, page, _watch = open_route(browser, static_url, "reform", DESKTOP, query=query)
    custom = page.get_by_test_id("link-custom")
    expect(custom).to_contain_text("housing cycle to high")
    page.get_by_role("tab", name="Assumptions").click()
    note = page.get_by_test_id("assumptions-static-custom")
    expect(note).to_be_visible()
    expect(note).not_to_contain_text("shows the default assumptions")
    custom.get_by_role("button", name="Use the default assumptions instead").click()
    expect(page).to_have_url(re.compile(r"\?v=1&s=TL&r=adu$"))
    context.close()


def test_demo_path_labels_the_uncapped_variant(browser, base_url):
    context, page, watch, card = open_panel(browser, base_url)
    expect(card.get_by_test_id("bt-result")).not_to_be_empty()
    demo_fold = page.get_by_test_id("bt-demo-fold")
    demo_fold.locator("summary").click()
    demo = page.get_by_test_id("bt-demo")
    run = api(base_url, "run", {"state": "TL", "reforms": ["adu"], "overrides": {}, "seed": 0})
    added = run["explanation"]["numbers"]["reform_results.adu.added"]["shown"]
    expect(demo.locator("[data-step=benchmark]")).to_contain_text(
        f"{added['p10']} to {added['p90']}", timeout=60000
    )
    lever = api(base_url, "lever/TL?r=adu")
    eff = {e["variant"]: e["effect"]["shown"]["p50"] for e in lever["effects"]}
    expect(demo.get_by_test_id("bt-uncapped")).to_contain_text(
        "Old uncapped variant", timeout=180000
    )
    expect(demo.locator("[data-step=access]")).to_contain_text(eff["adu_financing_access"])
    expect(demo.locator("[data-step=timing]")).to_contain_text(eff["adu_timing_reliable"])
    expect(demo.locator("[data-step=cap]")).to_contain_text(eff["adu_finance_budget"])
    expect(demo.get_by_test_id("bt-ranking")).to_contain_text("The ranking")
    shot(page, "demo")
    assert watch.problems == []
    context.close()
