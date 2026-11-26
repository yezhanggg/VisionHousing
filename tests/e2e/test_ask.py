"""End-to-end: the question box (ui/ask.js) on Testland, live and static.

- live: the box opens by keyboard, a typed question gets an answer card whose lines equal
  ``POST /api/v1/answer`` for the same view, the live region reads it out, "Show the record"
  opens the cited records with their ids; a suggested question works; the question is never
  sent anywhere (no request carries its words);
- static: the same box over the exported files gives the same lines as the Python resolver
  over those files, and a custom scenario says it is not in this run instead of showing the
  default run's number;
- phone: the box opens above the sheet and its answer fits the screen width.

Screenshots go to ``nopush/logs/shots/`` (git-ignored), or to ``$VH_SHOTS`` when set.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.error import HTTPError

import test_shell as shell
from playwright.sync_api import expect
from test_shell import DESKTOP, PHONE, SHOTS, api, open_route, static_file

from visionhousing.api.answers import StaticReader, UnavailableError, resolve

base_url = shell.base_url
static_url = shell.static_url
browser = shell.browser

OUT = Path(os.environ.get("VH_SHOTS") or SHOTS)


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / f"u15-{name}.png"))


def open_box(page) -> None:
    toggle = page.locator(".ask .chat-toggle")
    toggle.focus()
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#chat-input")).to_be_visible()


def ask(page, question: str):
    box = page.locator("#chat-input")
    box.fill(question)
    box.press("Enter")
    card = page.get_by_test_id("ask-answer")
    expect(card).to_be_visible(timeout=60000)
    return card


def lines_of(card) -> list[str]:
    return card.get_by_test_id("ask-answer-text").locator("p").all_inner_texts()


def http_reader(base: str) -> StaticReader:
    def load(rel: str):
        try:
            return static_file(base, rel)
        except HTTPError as exc:
            raise UnavailableError(rel) from exc

    return StaticReader(load)


def test_live_question_matches_the_api_and_stays_in_the_page(browser, base_url):
    question = "How many homes would ADUs add?"
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu")
    sent: list[str] = []
    page.on("request", lambda r: sent.append(f"{r.url} {r.post_data or ''}".lower()))
    open_box(page)
    card = ask(page, question)
    want = api(
        base_url,
        "answer",
        {"question": question, "context": {"state": "TL", "reforms": ["adu"], "route": "state"}},
    )
    expect(card).to_have_attribute("data-status", "answered")
    assert lines_of(card) == want["lines"]
    expect(page.get_by_test_id("ask-run-id")).to_have_text(want["run_id"])
    expect(page.get_by_test_id("ask-live")).to_contain_text(want["title"])
    show = card.get_by_role("button", name="Show the record")
    show.focus()
    page.keyboard.press("Enter")
    expect(card.locator(".ask-records")).to_be_visible()
    expect(card.locator(".ask-record-id")).to_have_text([r["id"] for r in want["records"]])
    shot(page, "live-answer-records")
    # The question's own words never leave the page (the test asked the API itself above).
    page_requests = [s for s in sent if "/api/v1/answer" not in s]
    assert not any("would adus add" in s or "how many homes" in s for s in page_requests)
    assert watch.problems == []
    context.close()


def test_live_suggested_and_unsupported_questions(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", DESKTOP, query="v=1&s=TL&r=adu")
    open_box(page)
    chips = page.locator(".ask-offers .ask-chip")
    expect(chips.first).to_be_visible()
    first = chips.first.inner_text()
    chips.first.click()
    card = page.get_by_test_id("ask-answer")
    expect(card).to_be_visible(timeout=60000)
    want = api(
        base_url, "answer", {"question": first, "context": {"state": "TL", "reforms": ["adu"]}}
    )
    assert lines_of(card) == want["lines"]
    card = ask(page, "Tell me a joke")
    expect(card).to_have_attribute("data-status", "unsupported")
    expect(card).to_contain_text("I can't answer that from this run")
    expect(card.locator(".ask-chip").first).to_be_visible()
    shot(page, "live-unsupported")
    assert watch.problems == []
    context.close()


def test_static_answers_equal_the_python_resolver(browser, static_url):
    reader = http_reader(static_url)
    context, page, watch = open_route(browser, static_url, "state", DESKTOP, query="v=1&s=TL")
    open_box(page)
    for question, reforms in [
        ("Why does the baseline look high?", []),
        ("How many homes would all five reforms add?", []),
        ("What data are missing for transit?", []),
    ]:
        card = ask(page, question)
        want = resolve(question, {"state": "TL", "reforms": reforms, "route": "state"}, reader)
        expect(card).to_have_attribute("data-status", want["status"])
        assert lines_of(card) == want["lines"], question
    shot(page, "static-answer")
    page.goto(f"{static_url}#/state?v=1&s=TL&r=adu&a=adu.takeup_feasible_annual~h")
    card = ask(page, "How many homes would ADUs add?")
    expect(card).to_have_attribute("data-status", "unavailable")
    expect(card).to_contain_text("does not fall back to the default run")
    shot(page, "static-custom-unavailable")
    assert watch.problems == []
    context.close()


def test_phone_box_fits_the_screen(browser, base_url):
    context, page, watch = open_route(browser, base_url, "state", PHONE, query="v=1&s=TL&r=adu")
    page.locator(".ask .chat-toggle").click()
    card = ask(page, "Can I share the exact number I'm seeing?")
    expect(card).to_have_attribute("data-intent", "share")
    width = page.evaluate("document.querySelector('.ask').getBoundingClientRect().right")
    assert width <= PHONE["width"] + 1
    shot(page, "phone-share")
    assert watch.problems == []
    context.close()
