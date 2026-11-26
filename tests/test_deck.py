"""The slide-deck builder (``scripts/build_deck.py``), run offline on Testland.

The Challenge allows at most 30 slides, as a PDF. The builder must stay under that, take
every number from the engine's replies (each one listed with its source in numbers.json),
and print one 16:9 page per slide.
"""

from __future__ import annotations

import json
import re

import pytest

import build_deck as bd
from visionhousing.api import create_app
from visionhousing.export_static import _Caller, export_static

DRAWS = 100


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """Testland exported as the static site saves it, plus an in-memory API."""
    root = tmp_path_factory.mktemp("deck")
    packs = root / "packs"
    export_static("tl", root / "data", packs_dir=packs, draws=DRAWS)
    return root, _Caller(create_app(packs_dir=packs, web_dir=root / ".no-web", workers=1))


@pytest.fixture(scope="module")
def built(site):
    root, api = site
    out = root / "deck"
    report = bd.build(root / "data", "TL", out, api=api, pdf=False, heavy=True)
    return out, report


def test_at_most_thirty_slides(built):
    out, report = built
    assert 20 <= report["slides"] <= bd.MAX_SLIDES
    page = (out / bd.HTML_NAME).read_text(encoding="utf-8")
    assert page.count('<section class="slide') == report["slides"]


def test_every_number_has_a_source(built):
    out, report = built
    ledger = json.loads((out / bd.NUMBERS_NAME).read_text(encoding="utf-8"))
    assert ledger["state"] == "TL"
    assert len(ledger["slides"]) == report["slides"]
    assert ledger["numbers"], "the deck shows no engine numbers"
    for n in ledger["numbers"]:
        assert n["source"], n
        assert 1 <= n["slide"] <= report["slides"]
    numbered = {n["slide"] for n in ledger["numbers"]}
    for s in ledger["slides"]:
        if s["n"] in numbered:
            assert s["sources"], s


def test_no_broken_values_on_slides(built):
    out, _ = built
    text = re.sub(r"<[^>]+>", " ", (out / bd.HTML_NAME).read_text(encoding="utf-8"))
    for bad in (" None ", " nan ", "NaN", "undefined", "Traceback"):
        assert bad not in text, bad


def test_the_engine_numbers_reach_the_slides(site, built):
    root, _ = site
    out, _ = built
    run = bd.read_file(root / "data" / "TL" / "runs" / "adu.json")
    shown = bd.span(run["reform_results"]["adu"]["added"])
    assert shown in (out / bd.HTML_NAME).read_text(encoding="utf-8")


def test_saved_files_only_still_builds(site, tmp_path):
    root, _ = site
    report = bd.build(root / "data", "TL", tmp_path, api=None, pdf=False)
    assert report["slides"] <= bd.MAX_SLIDES
    page = (tmp_path / bd.HTML_NAME).read_text(encoding="utf-8")
    assert "pending-box" in page  # cards the saved files lack say so instead of a number


def test_formatting():
    assert bd.homes(141659.9) == "142,000"
    assert bd.homes(84.2) == "84"
    assert bd.homes(None) == "-"
    assert bd.span({"p10": 2474.6, "p50": 5963.0, "p90": 12024.5}) == "2,470 to 12,000"
    assert bd.money(36_400_000) == "$36.4 million"
    assert bd.pct(4.93) == "4.9%"
    assert bd.compress_sources(
        [
            "d/PA/runs/adu.json (saved 2026-11-24)",
            "d/PA/runs/tod.json (saved 2026-11-24)",
            "Makefile",
        ]
    ) == ["Makefile", "d/PA/runs/{adu, tod}.json (saved 2026-11-24)"]


def test_pdf_has_one_page_per_slide(built, tmp_path):
    out, report = built
    try:
        pages = bd.print_pdf(out / bd.HTML_NAME, tmp_path / bd.PDF_NAME)
    except Exception as exc:
        pytest.skip(f"headless Chromium not available: {exc}")
    assert pages == report["slides"] <= bd.MAX_SLIDES


def test_usability_slide_uses_the_tool_pictures(site, tmp_path):
    """The Usability slide shows the six tool pictures, else the text cards.

    With all six pictures in out/screens they appear 2 x 3 by a relative path; with one
    missing the slide falls back to the text cards.
    """
    root, _ = site
    shots = tmp_path / bd.SCREENS_NAME
    shots.mkdir()
    for sc in bd.SCREENS:
        (shots / f"{sc.name}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    report = bd.build(root / "data", "TL", tmp_path, api=None, pdf=False)
    assert report["slides"] <= bd.MAX_SLIDES
    page = (tmp_path / bd.HTML_NAME).read_text(encoding="utf-8")
    assert page.count('<figure class="shot">') == len(bd.SCREENS) == 6
    for sc in bd.SCREENS:
        assert f'src="screens/{sc.name}.jpg"' in page
    assert str(tmp_path) not in page  # no machine paths in the deck

    (shots / f"{bd.SCREENS[0].name}.jpg").unlink()
    bd.build(root / "data", "TL", tmp_path, api=None, pdf=False)
    page = (tmp_path / bd.HTML_NAME).read_text(encoding="utf-8")
    assert '<figure class="shot">' not in page
    assert 'class="grid5"' in page


def test_screen_list():
    names = [sc.name for sc in bd.SCREENS]
    assert len(names) == len(set(names)) == 6
    assert bd.SCREEN_SIZE == {"width": 1920, "height": 1080}
    assert bd.SCREEN_MAX_BYTES == 400_000
    modes = {"state", "reform", "place", "method", "compare"}
    for sc in bd.SCREENS:
        assert sc.route.split("?")[0].split("/")[0] in modes
        assert not re.search(r"\d", sc.caption.replace("3D", ""))  # captions carry no numbers
