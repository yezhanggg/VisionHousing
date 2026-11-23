"""Report writer: golden brief text, must-cite on the whole brief, determinism, HTML, CSV, CLI."""

import csv
import io
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.test_explain import check_cites, consistent, make_resolver
from visionhousing import cli
from visionhousing.engine import run
from visionhousing.explain import cited_texts, explain, numbers_in, uncited
from visionhousing.report import FORMATS, brief_blocks, render, to_markdown, write_report
from visionhousing.report.tables import BOM, TABLE_NAMES

REFORMS = ["adu", "fees", "parking"]
DATE = "2026-11-22"


@pytest.fixture(scope="module")
def result():
    return run("TL", REFORMS, seed=0)


@pytest.fixture(scope="module")
def explained(result):
    return explain(result)


@pytest.fixture(scope="module")
def blocks(result, explained):
    return brief_blocks(result, explained, run_date=DATE)


def _stamp_free(markdown: str) -> str:
    head, _, rest = markdown.partition("<!-- stamp -->")
    _, _, tail = rest.partition("<!-- /stamp -->")
    return head + tail


def _allowed(blocks) -> set[str]:
    allowed: set[str] = set()
    for t in cited_texts(blocks):
        for c in t["cites"]:
            allowed.update(numbers_in(c["shown"]))
    return allowed


# ---------------------------------------------------------------- golden text


def test_golden_brief_opening_on_testland(blocks):
    md = to_markdown(blocks)
    lines = md.splitlines()
    assert lines[0] == "# Housing reforms in Testland: homes permitted, 2027\u20132031"
    assert lines[2].startswith("> **Decision support, not a forecast. Not an official state")
    assert "| Homes added by the reforms | 186 to 612 | 366 |" in lines
    assert "| Change from the baseline | 5.7% to 15% | 9.3% |" in lines
    assert (
        "| Accessory dwelling units | Adds some | 34 to 174 | 0.8% to 5.8% | Middle estimate: "
        "2.1% more homes than the baseline. “Adds some” means 1% to under 5%. |"
    ) in lines
    for heading in (
        "## In one sentence",
        "## Each reform on its own",
        "## All reforms together",
        "### How it adds homes, step by step",
        "### What moves this estimate most",
        "### What this cannot tell you",
        "## Where the added homes would be",
        "## Assumptions used",
        "## Sources",
    ):
        assert heading in lines, heading


def test_stamp_has_the_three_hashes_seed_draws_and_date(result):
    md = render(result, "md", run_date=DATE)["brief"]
    stamp = md.split("<!-- stamp -->")[1]
    for h in (result.hashes.code, result.hashes.data, result.hashes.assumptions):
        assert h in stamp
    assert "Seed: 0" in stamp and "Draws: 2,000" in stamp and f"Run date: {DATE}" in stamp
    assert "Decision support, not a forecast. Not an official state document." in stamp
    assert "Run date" not in render(result, "md", run_date=None)["brief"]


# ---------------------------------------------------------------- must cite, whole brief


def test_every_text_in_the_brief_is_cited(result, explained, blocks):
    texts = list(cited_texts(blocks))
    assert check_cites(texts, make_resolver(result, explained)) > 200


def test_no_number_in_the_markdown_is_typed_by_hand(blocks):
    body = _stamp_free(to_markdown(blocks))
    stray = [n for n in numbers_in(body) if n not in _allowed(blocks)]
    assert stray == []


def _chart_numbers(blocks, get) -> set[str]:
    """Numbers drawn in the charts, each checked against the record it names."""
    out: set[str] = set()
    for b in blocks:
        if b["type"] != "chart":
            continue
        for item in b["chart"]["items"]:
            for q, shown in item["shown"].items():
                assert get(item["ref"], q) == item[q]
                (token,) = numbers_in(shown)
                assert consistent(token, item[q]), (item["ref"], q, shown)
                out.add(token)
    return out


def test_no_number_in_the_html_text_is_typed_by_hand(result, explained, blocks):
    page = render(result, "html", run_date=DATE)["brief"]
    texts = _HTMLText()
    texts.feed(page)
    footer_free = "\n".join(texts.chunks)
    allowed = _allowed(blocks) | _chart_numbers(blocks, make_resolver(result, explained))
    stray = [n for n in numbers_in(footer_free) if n not in allowed]
    assert stray == []


# ---------------------------------------------------------------- determinism


@pytest.mark.parametrize("fmt", FORMATS)
def test_same_run_gives_identical_bytes(result, fmt):
    again = run("TL", REFORMS, seed=0, use_cache=False)
    assert render(result, fmt, run_date=DATE) == render(again, fmt, run_date=DATE)


def test_only_the_date_differs(result):
    a = render(result, "md", run_date="2026-11-22")["brief"].splitlines()
    b = render(result, "md", run_date="2026-12-01")["brief"].splitlines()
    assert [x for x, y in zip(a, b, strict=True) if x != y] == [
        "Seed: 0 · Draws: 2,000 · Run date: 2026-11-22  "
    ]


# ---------------------------------------------------------------- HTML


VOID = {"meta", "br", "link", "img", "input", "hr", "wbr", "col", "source", "area", "base"}


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> with open {self.stack[-3:]}")
            return
        self.stack.pop()


class _HTMLText(HTMLParser):
    """Visible text, leaving out the style sheet and the footer stamp."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "footer"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("style", "footer"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.chunks.append(data)


def test_html_is_well_formed_and_self_contained(result):
    page = render(result, "html", run_date=DATE)["brief"]
    assert page.startswith('<!doctype html>\n<html lang="en">')
    p = _Checker()
    p.feed(page)
    p.close()
    assert p.errors == [] and p.stack == []
    names = [t for t, _ in p.tags]
    assert names.count("title") == 1 and names.count("h1") == 1 and names.count("style") == 1
    assert "script" not in names and "link" not in names and "img" not in names
    assert "@import" not in page and "url(" not in page and "@font-face" not in page
    assert names.count("svg") == 2
    for tag, attrs in p.tags:
        if tag == "svg":
            assert attrs.get("role") == "img" and attrs.get("aria-label")
    assert "@page" in page and "@media print" in page


# ---------------------------------------------------------------- CSV


def test_csv_tables_are_rfc4180_with_full_precision(result):
    tables = render(result, "csv", run_date=DATE)
    assert tuple(tables) == TABLE_NAMES
    for name, text in tables.items():
        assert text.startswith(BOM), name
        assert "\r\n" in text and "\n" not in text.replace("\r\n", ""), name
        rows = list(csv.DictReader(io.StringIO(text[1:], newline="")))
        assert rows, name
    summary = list(csv.DictReader(io.StringIO(tables["summary"][1:], newline="")))
    base = next(r for r in summary if r["id"] == "baseline")
    assert float(base["p50"]) == result.baseline.p50
    adu = next(r for r in summary if r["id"] == "reform_results.adu.added")
    assert adu["verdict"] == "Adds some"
    counties = list(csv.DictReader(io.StringIO(tables["counties"][1:], newline="")))
    assert {r["county_id"] for r in counties} == set(result.county_ids)
    numbers = list(csv.DictReader(io.StringIO(tables["numbers"][1:], newline="")))
    assert len(numbers) == len(explain(result, with_drivers=False)["numbers"])


# ---------------------------------------------------------------- JSON


def test_json_is_the_explanation(result):
    data = json.loads(render(result, "json", run_date=DATE)["brief"])
    assert data["run_date"] == DATE
    assert data["headline"]["sentence"]["text"].startswith("Over 2027\u20132031, Testland")
    assert data["slides"][0]["n"] == 1


# ---------------------------------------------------------------- the command line


@pytest.mark.parametrize("fmt", ["md", "html", "json"])
def test_cli_writes_the_file(tmp_path: Path, fmt, capsys):
    out = tmp_path / f"brief.{fmt}"
    code = cli.main(["report", "--state", "TL", "--format", fmt, "--out", str(out), "--date", DATE])
    assert code == 0 and out.exists() and out.stat().st_size > 1000
    assert str(out) in capsys.readouterr().out


def test_cli_writes_every_csv_table(tmp_path: Path):
    code = cli.main(["report", "--state", "TL", "--format", "csv", "--out", str(tmp_path)])
    assert code == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == sorted(f"TL-adu-fees-parking-{t}.csv" for t in TABLE_NAMES)


def test_cli_baseline_only_and_bad_input(tmp_path: Path, capsys):
    out = tmp_path / "base.md"
    args = ["report", "--state", "TL", "--reforms", "none", "--out", str(out), "--date", "none"]
    assert cli.main(args) == 0
    text = out.read_text(encoding="utf-8")
    assert "homes under today's rules." in text and "Run date" not in text
    bad = ["report", "--state", "TL", "--reforms", "nonsense", "--out", str(out)]
    assert cli.main(bad) == 1
    assert "nonsense" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["report", "--state", "TL", "--date", "22/11/2026"])


def test_write_report_default_name(tmp_path: Path, result):
    paths = write_report(result, "md", tmp_path / "x" / "b.md", run_date=None)
    assert paths == [tmp_path / "x" / "b.md"]
    assert uncited("plain", []) == []
