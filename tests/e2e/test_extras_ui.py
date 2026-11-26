"""End-to-end: the extra scenarios in the interface (Testland).

- State: a folded "Extra scenarios" group sits under the benchmark switches and lists every
  reform GET /reforms marks ``kind: "extra"``, with the engine's title; "All five reforms"
  still switches on the five benchmarks only;
- one extra (fee deferral with affordability terms) runs alone and the screen shows the
  engine's headline range for it; the benchmark it overlaps (the fee cut) is greyed out with
  its reason, and pressing it changes nothing (the back end refuses that package);
- Reform: adaptive reuse shows its conversion homes as "outside the permit count", with the
  engine's record name and range, and lists the scenario's records;
- static site: an extra whose run was not exported says it needs the local app.

Every number checked is the API's own text. Screenshots go to ``nopush/logs/shots/``
(git-ignored), or to ``$VH_SHOTS`` when set.
"""

from __future__ import annotations

import os
from pathlib import Path

import test_shell as shell
from playwright.sync_api import expect
from test_shell import DESKTOP, SHOTS, api, open_route, run_of, static_file

base_url = shell.base_url
static_url = shell.static_url
browser = shell.browser

OUT = Path(os.environ.get("VH_SHOTS") or SHOTS)
BENCHMARKS = ["adu", "missing_middle", "tod", "parking", "fees"]
FEE_EXTRA = "fee_deferral_with_affordability"
LOCAL = "Needs the local app (make app)."


def shot(page, name: str, focus=None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if focus is not None:
        focus.scroll_into_view_if_needed()
    page.wait_for_timeout(600)  # the fold's glide
    page.screenshot(path=str(OUT / f"u12-{name}.png"))


def extras_of(base: str) -> list[dict]:
    return [r for r in api(base, "reforms")["reforms"] if r["kind"] == "extra"]


def reforms_in_link(page) -> list[str]:
    query = page.evaluate("location.hash").split("?", 1)[1]
    pairs = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
    return [r for r in pairs.get("r", "").split(",") if r]


def test_extras_are_listed_under_the_benchmarks(browser, base_url):
    extras = extras_of(base_url)
    assert len(extras) == 5
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL")
    group = page.get_by_test_id("extra-scenarios")
    expect(group).to_be_visible()
    expect(group).not_to_have_attribute("open", "")  # folded while no extra is on
    # The group comes after the last benchmark switch in the panel.
    after = page.evaluate(
        """() => {
          const g = document.querySelector('[data-testid="extra-scenarios"]');
          const fees = document.querySelector('.switch[data-reform="fees"]');
          return Boolean(fees.compareDocumentPosition(g) & Node.DOCUMENT_POSITION_FOLLOWING);
        }"""
    )
    assert after, "the extra scenarios are not under the benchmark switches"
    group.locator("summary").click()
    labels = group.locator(".switch .switch-label")
    expect(labels).to_have_text([r["title"] for r in extras])
    shot(page, "extras-listed", group)
    # "All five reforms" stays the five benchmarks.
    page.get_by_role("button", name="All five reforms").click()
    expect(page.locator(".switch.is-on")).to_have_count(5)
    assert sorted(reforms_in_link(page)) == sorted(BENCHMARKS)
    assert watch.problems == []
    context.close()


def test_an_extra_runs_alone_and_greys_out_its_benchmark(browser, base_url):
    reply = run_of(base_url, [FEE_EXTRA])
    added = next(x for x in reply["explanation"]["headline"]["numbers"] if x["key"] == "added")
    title = next(r["title"] for r in extras_of(base_url) if r["key"] == FEE_EXTRA)
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL")
    group = page.get_by_test_id("extra-scenarios")
    group.locator("summary").click()
    group.locator(f'.switch[data-reform="{FEE_EXTRA}"]').click()
    expect(page.locator(".lead-number").first).to_have_text(
        f"{added['shown']['p10']} to {added['shown']['p90']}", timeout=60000
    )
    expect(page.get_by_test_id("headline-sentence")).to_have_text(
        reply["explanation"]["headline"]["sentence"]["text"]
    )
    fees = page.locator('.switch[data-reform="fees"]')
    expect(fees).to_have_attribute("aria-disabled", "true")
    expect(fees.locator(".switch-why")).to_contain_text(f"Not with {title}")
    expect(fees).to_be_disabled()
    fees.click(force=True)  # pressing it anyway changes nothing
    expect(fees).to_have_attribute("aria-pressed", "false")
    assert reforms_in_link(page) == [FEE_EXTRA]
    shot(page, "extra-runs-benchmark-greyed", fees)
    # The Reform screen of the fee cut says the same, on its "Add to scenario" button.
    page.goto(f"{base_url}#/reform/fees?v=1&s=TL&r={FEE_EXTRA}")
    add = page.get_by_test_id("add-to-scenario")
    expect(add).to_have_attribute("aria-disabled", "true")
    expect(page.locator("#add-why")).to_contain_text(f"Not with {title}")
    assert watch.problems == []
    context.close()


def test_adaptive_reuse_shows_its_homes_outside_the_permit_count(browser, base_url):
    reply = run_of(base_url, ["adaptive_reuse"])
    numbers = reply["explanation"]["numbers"]
    rec = numbers["reform_results.adaptive_reuse.records.conversion_homes"]
    records = [k for k in numbers if k.startswith("reform_results.adaptive_reuse.records.")]
    context, page, watch = open_route(
        browser, base_url, "#/reform/adaptive_reuse", DESKTOP, query="v=1&s=TL"
    )
    box = page.get_by_test_id("outside-count")
    expect(box).to_contain_text("Outside the permit count", ignore_case=True, timeout=60000)
    expect(box.locator(".outside-name")).to_have_text(rec["name"])
    expect(box.locator(".num-btn")).to_have_text(f"{rec['shown']['p10']} to {rec['shown']['p90']}")
    expect(page.get_by_test_id("extra-records").locator(".records-row")).to_have_count(len(records))
    expect(page.get_by_test_id("extra-scenarios")).to_have_attribute("open", "")
    shot(page, "adaptive-reuse-outside", box)
    assert watch.problems == []
    context.close()


def test_static_site_says_an_unexported_extra_needs_the_local_app(browser, static_url):
    index = static_file(static_url, "TL/index.json")
    saved = set(index.get("runs", [])) | set(index.get("extra_runs", []))
    context, page, watch = open_route(browser, static_url, "state", DESKTOP, query="v=1&s=TL")
    group = page.get_by_test_id("extra-scenarios")
    group.locator("summary").click()
    switches = group.locator(".switch")
    expect(switches).to_have_count(5)
    for i in range(5):
        sw = switches.nth(i)
        key = sw.get_attribute("data-reform")
        if key in saved:
            expect(sw).not_to_have_attribute("aria-disabled", "true")
        else:
            expect(sw.locator(".switch-why")).to_have_text(LOCAL)
    shot(page, "static-extras-local-app", group)
    assert watch.problems == []
    context.close()
