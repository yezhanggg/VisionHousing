"""Build the submission slide deck from the tool's own numbers.

The Challenge asks for a slide deck of at most 30 slides, as a PDF. This script writes it so
that it can be rebuilt whenever the numbers change:

1. It reads the tool's saved results (the static-site files in ``web/public/data``, which are
   the API's own replies saved by ``make export-static``) and, when an API address is given,
   asks the running tool (``make app``) for the cards the static files do not hold yet
   (evidence grades, the plausibility card, districts, the law timeline).
2. It writes one HTML page with every slide (``deck/VisionHousing_deck.html``) and a list of
   every number on the slides with the file or address it came from (``deck/numbers.json``).
3. It prints the page to PDF with headless Chromium (``deck/VisionHousing_deck.pdf``), one
   16:9 slide per page, and checks the page count is at most 30.

No number on a slide is typed here. Each one is read from an engine reply and only formatted
(rounded, given a thousands separator). A slide whose numbers come from a saved run made
before the final export carries a footer that says so. Words that are not numbers (titles,
plain explanations) live in this file so the team can edit them.

Usage::

    uv run python scripts/build_deck.py                          # PA, saved files only
    uv run python scripts/build_deck.py --api http://127.0.0.1:8000
    uv run python scripts/build_deck.py --state TL --data /tmp/site/data --out /tmp/deck
    uv run python scripts/build_deck.py --screens --api http://127.0.0.1:8000

``--screens`` first photographs the running tool (``make app``) at 1920 x 1080 with headless
Chromium: six views, saved as JPEG pictures in ``deck/screens/``. The Usability slide shows
them in a 2 x 3 grid. The pictures show the tool's own screens, so every number in them is the
tool's own text; they are as current as the run the tool was serving when they were taken.
Without the pictures (a fresh checkout, the Testland test) that slide shows short text cards.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import html
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "web" / "public" / "data"
DEFAULT_OUT = ROOT / "deck"
TOKENS_CSS = ROOT / "web" / "css" / "tokens.css"
API = "/api/v1"
MAX_SLIDES = 30
BENCHMARKS = ("adu", "missing_middle", "tod", "parking", "fees")
PDF_NAME = "VisionHousing_deck.pdf"
HTML_NAME = "VisionHousing_deck.html"
NUMBERS_NAME = "numbers.json"
SCREENS_NAME = "screens"
SCREEN_SIZE = {"width": 1920, "height": 1080}
SCREEN_MAX_BYTES = 400_000
SCREEN_QUALITY = 85
GPU_ARGS = ["--use-angle=metal", "--enable-gpu"]


@dataclass(frozen=True)
class Screen:
    """One view of the running tool to photograph for the deck.

    ``route`` is the address after ``#/`` with ``{st}`` and ``{county}`` filled in. ``step``
    names an extra action after the page settles: ``"3d"`` presses the legend's 3D switch,
    ``"people"`` scrolls the place profile to its first charts. ``tries`` is how many times
    the view is opened before giving up: the tool stops waiting for a long run after a set
    time but keeps working on it, so asking again picks the finished run up.
    """

    name: str
    route: str
    caption: str
    step: str = ""
    tries: int = 3


SCREENS: tuple[Screen, ...] = (
    Screen("state-adu-2d", "state?v=1&s={st}&r=adu", "State: one reform on, likely range"),
    Screen("state-adu-3d", "state?v=1&s={st}&r=adu", "State in 3D: middle and high values", "3d"),
    Screen("reform-decision", "reform/adu?v=1&s={st}&r=adu", "Reform: the five-step decision view"),
    Screen("place-profile", "place/{county}?v=1&s={st}&r=adu", "Place: county profile", "people"),
    Screen("method-backtest", "method?v=1&s={st}", "Method: back-test error by county"),
    Screen("compare", "compare?v=1&s={st}", "Compare: each reform alone and together", tries=10),
)

# A caller answers (method, path, body) with (status, reply bytes), like the exporter's.
Caller = Callable[[str, str, Any], tuple[int, bytes]]


# --------------------------------------------------------------------------- formatting


def esc(text: Any) -> str:
    """HTML-escape any value as text."""
    return html.escape(str(text), quote=True)


def homes(x: float | None) -> str:
    """A count of homes for a slide: whole numbers, three significant figures above 1,000."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    v = float(x)
    if abs(v) >= 1000:
        digits = math.floor(math.log10(abs(v))) + 1
        v = round(v, -(digits - 3))
    return f"{round(v):,}"


def pct(x: float | None, places: int = 1) -> str:
    """A percent value (already in percent units) with ``places`` decimals."""
    if x is None:
        return "-"
    return f"{x:.{places}f}%"


def share(x: float | None) -> str:
    """A share (0 to 1) as a whole percent."""
    if x is None:
        return "-"
    return f"{100 * x:.0f}%"


def money(x: float | None) -> str:
    """Dollars, three significant figures above $1,000."""
    if x is None:
        return "-"
    for size, word in ((1e9, "billion"), (1e6, "million")):
        if abs(x) >= size:
            return f"${x / size:.3g} {word}"
    return "$" + homes(x)


def span(r: dict[str, Any] | None, fmt: Callable[[float], str] = homes) -> str:
    """``P10 to P90`` of a record."""
    if not r or r.get("p10") is None:
        return "-"
    return f"{fmt(r['p10'])} to {fmt(r['p90'])}"


# --------------------------------------------------------------------------- data access


def unpack(value: Any) -> Any:
    """Undo the static exporter's string pooling (``$packed`` files)."""
    if isinstance(value, dict) and value.get("$packed") == 1:
        sys.path.insert(0, str(ROOT / "src"))
        from visionhousing.export_static import unpack as _unpack

        return _unpack(value)
    return value


def read_file(path: Path) -> Any:
    """One saved static-site file, unpacked (also a file that uses its state's shared pool)."""
    sys.path.insert(0, str(ROOT / "src"))
    from visionhousing.export_static import read_saved

    return read_saved(path)


def http_caller(base: str, timeout: float = 180.0, retries: int = 6) -> Caller:
    """A caller for a running tool at ``base`` (waits and retries while it says it is busy)."""

    def call(method: str, path: str, body: Any = None) -> tuple[int, bytes]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        for attempt in range(retries):
            req = urllib.request.Request(  # noqa: S310 - a local address given by the user
                base.rstrip("/") + path,
                data=data,
                method=method,
                headers={"content-type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                    return resp.status, resp.read()
            except urllib.error.HTTPError as err:
                # 503: busy; 504: a long run continues on the server, ask again to pick it up.
                if err.code in (503, 504) and attempt < retries - 1:
                    time.sleep(15)
                    continue
                return err.code, err.read()
            except (urllib.error.URLError, TimeoutError, OSError):
                return 0, b""
        return 0, b""

    return call


@dataclass
class Got:
    """One reply and where it came from (shown in the slide footer and in numbers.json)."""

    value: Any
    source: str
    pending: bool = False


@dataclass
class Data:
    """Reads saved files first, then (if given) asks the running tool."""

    data_dir: Path
    state: str
    api: Caller | None = None
    heavy: bool = False
    today: str = field(default_factory=lambda: dt.date.today().isoformat())

    def _label(self, path: Path) -> str:
        try:
            rel = path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            rel = f"{self.data_dir.name}/{path.relative_to(self.data_dir).as_posix()}"
        saved = dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
        return f"{rel} (saved {saved})"

    def saved(self, rel: str) -> Got | None:
        """A saved static-site file under the data folder, or None."""
        path = self.data_dir / rel
        if not path.is_file():
            return None
        value = read_file(path)
        is_run = "/runs/" in f"/{rel}" or "/compare/" in f"/{rel}"
        pending = is_run and isinstance(value, dict) and not value.get("as_of")
        return Got(value, self._label(path), pending)

    def live(self, method: str, path: str, body: Any = None) -> Got | None:
        """A reply from the running tool, or None when there is no tool or it refused."""
        if self.api is None:
            return None
        status, data = self.api(method, API + path, body)
        if status != 200 or not data:
            return None
        try:
            value = json.loads(data)
        except ValueError:
            return None
        return Got(value, f"{method} {API}{path.split('?')[0]} (live, {self.today})")

    def either(self, rels: Iterable[str], path: str) -> Got | None:
        """The first saved file that exists, else the live reply."""
        for rel in rels:
            got = self.saved(rel)
            if got is not None:
                return got
        return self.live("GET", path)

    def comparisons(self) -> Got | None:
        """Dials and data ablation: a saved file, else (only if allowed) the live reply.

        The live reply is a heavy job on a large state, so it is asked for only when
        ``heavy`` is set (``--comparisons``).
        """
        for rel in (f"{self.state}/comparisons.json", f"{self.state}/comparisons/all.json"):
            got = self.saved(rel)
            if got is not None:
                return got
        # The exporter saves one file per part (ST/comparisons/<part>.json, each {part: ...}).
        parts = [self.saved(f"{self.state}/comparisons/{p}.json") for p in ("dials", "ablation")]
        if all(parts):
            merged: dict[str, Any] = {}
            for got in parts:
                merged.update(got.value)
            return Got(merged, " + ".join(g.source for g in parts))
        if not self.heavy:
            return None
        return self.live("GET", f"/states/{self.state}/comparisons?part=dials,ablation")

    def run(self, key: str, state: str | None = None) -> Got | None:
        """One default run (``baseline``, a reform key or ``package``), saved or live."""
        st = state or self.state
        got = self.saved(f"{st}/runs/{key}.json")
        if got is not None:
            return got
        reforms = [] if key == "baseline" else list(BENCHMARKS) if key == "package" else [key]
        body = {"state": st, "reforms": reforms, "drivers": len(reforms) == 1}
        return self.live("POST", "/run", body)


# --------------------------------------------------------------------------- deck model


@dataclass
class Number:
    """One number on a slide and its source."""

    slide: int
    shown: str
    what: str
    source: str
    ref: str = ""


@dataclass
class Slide:
    """One slide: words, an optional chart (inline SVG) and the files its numbers came from."""

    kicker: str
    title: str
    body: str
    sources: list[str] = field(default_factory=list)
    pending: bool = False
    kind: str = "content"


class Deck:
    """Collects slides and the number ledger."""

    def __init__(self) -> None:
        self.slides: list[Slide] = []
        self.numbers: list[Number] = []
        self._src: list[str] = []
        self._pending = False

    def use(self, got: Got | None) -> Any:
        """Note a reply as a source of the slide being built; return its value."""
        if got is None:
            return None
        if got.source not in self._src:
            self._src.append(got.source)
        self._pending = self._pending or got.pending
        return got.value

    def n(self, shown: str, what: str, got: Got | None, ref: str = "") -> str:
        """Record a number shown on the slide being built; return it HTML-escaped."""
        self.use(got)
        self.numbers.append(
            Number(len(self.slides) + 1, shown, what, got.source if got else "", ref)
        )
        return esc(shown)

    def add(self, kicker: str, title: str, body: str, kind: str = "content") -> None:
        """Close the slide being built."""
        self.slides.append(Slide(kicker, title, body, self._src, self._pending, kind))
        self._src, self._pending = [], False


# --------------------------------------------------------------------------- charts (SVG)


def range_chart(rows: list[tuple[str, dict[str, Any] | None, str]], width: int = 500) -> str:
    """Horizontal range bars (P10 to P90, a tick at P50) on one shared scale.

    Args:
        rows: (label, record with p10/p50/p90, text shown at the right).
        width: Width of the bar area in pixels.
    """
    good = [r for _, r, _ in rows if r and r.get("p90") is not None]
    hi = max([r["p90"] for r in good] + [1.0])
    lo = min([r["p10"] for r in good] + [0.0])
    row_h, label_w, text_w = 50, 320, 330
    total_w = label_w + width + text_w
    out = [f'<svg class="chart" viewBox="0 0 {total_w} {row_h * len(rows) + 10}" role="img">']
    for i, (label, r, text) in enumerate(rows):
        y = i * row_h + 8
        out.append(f'<text x="0" y="{y + 26}" class="c-label">{esc(label)}</text>')
        out.append(
            f'<rect x="{label_w}" y="{y + 12}" width="{width}" height="18" rx="9" class="c-track"/>'
        )
        if r and r.get("p90") is not None:

            def at(v: float) -> float:
                return label_w + width * (v - lo) / ((hi - lo) or 1)

            a, b, m = at(r["p10"]), at(r["p90"]), at(r["p50"])
            out.append(
                f'<rect x="{a:.1f}" y="{y + 12}" width="{max(b - a, 4):.1f}" height="18" rx="9" '
                'class="c-band"/>'
            )
            out.append(f'<rect x="{m - 2:.1f}" y="{y + 6}" width="4" height="30" class="c-mid"/>')
        out.append(
            f'<text x="{label_w + width + 16}" y="{y + 27}" class="c-value">{esc(text)}</text>'
        )
    out.append("</svg>")
    return "".join(out)


def tornado(items: list[dict[str, Any]], center: float | None) -> str:
    """The top drivers as a small tornado: each input's low-to-high swing in added homes."""
    if not items:
        return ""
    vals = [v for x in items for v in (x["total_low"], x["total_high"])]
    vals.append(center or 0.0)
    lo, hi = min(0.0, *vals), max(vals)

    def at(v: float) -> float:
        return 100 * (v - lo) / ((hi - lo) or 1)

    rows = []
    for x in items:
        a, b = sorted((at(x["total_low"]), at(x["total_high"])))
        mid = (
            "" if center is None else f'<span class="t-mid" style="left:{at(center):.1f}%"></span>'
        )
        rows.append(
            f'<div class="t-row"><div class="t-label">{esc(x["label"])}</div>'
            f'<div class="t-track"><span class="t-band" style="left:{a:.1f}%;width:'
            f'{max(b - a, 0.6):.1f}%"></span>{mid}</div>'
            f'<div class="t-ends"><span>{esc(homes(x["total_low"]))} at its low value</span>'
            f"<span>{esc(homes(x['total_high']))} at its high value</span></div></div>"
        )
    return '<div class="tornado">' + "".join(rows) + "</div>"


def bars(rows: list[tuple[str, float, str]], target: float | None = None) -> str:
    """Simple horizontal bars for shares (0 to 1), with an optional target line."""
    row_h, label_w, width, text_w = 44, 260, 380, 360
    out = [f'<svg class="chart" viewBox="0 0 {label_w + width + text_w} {row_h * len(rows) + 30}">']
    for i, (label, v, text) in enumerate(rows):
        y = i * row_h + 4
        out.append(f'<text x="0" y="{y + 24}" class="c-label">{esc(label)}</text>')
        out.append(f'<rect x="{label_w}" y="{y + 8}" width="{width}" height="22" class="c-track"/>')
        out.append(
            f'<rect x="{label_w}" y="{y + 8}" width="{width * max(0.0, min(v, 1.0)):.1f}" '
            'height="22" class="c-band"/>'
        )
        out.append(
            f'<text x="{label_w + width + 14}" y="{y + 25}" class="c-value-sm">{esc(text)}</text>'
        )
    if target is not None:
        x = label_w + width * target
        out.append(
            f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{row_h * len(rows) + 8}" '
            'class="c-target"/>'
        )
    out.append("</svg>")
    return "".join(out)


def county_map(outlines: dict[str, Any], classes: dict[str, str]) -> str:
    """County outlines shaded by the map's class (little / some / a lot), projected flat."""
    feats = outlines.get("features") or []
    pts = [
        p
        for f in feats
        for poly in _polys(f.get("geometry") or {})
        for ring in poly[:1]
        for p in ring
    ]
    if not pts:
        return ""
    lat0 = sum(p[1] for p in pts) / len(pts)
    k = math.cos(math.radians(lat0))
    xs = [p[0] * k for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = 640.0, 640.0 * (y1 - y0) / ((x1 - x0) or 1)
    if h > 420:
        w, h = w * 420 / h, 420.0
    s = w / ((x1 - x0) or 1)
    out = [f'<svg class="chart map" viewBox="0 0 {w:.0f} {h:.0f}" role="img">']
    for f in feats:
        cls = classes.get(f["properties"].get("geoid"), "none")
        d = ""
        for poly in _polys(f["geometry"]):
            for ring in poly[:1]:
                d += (
                    "M"
                    + "L".join(f"{(p[0] * k - x0) * s:.1f},{(y1 - p[1]) * s:.1f}" for p in ring)
                    + "Z"
                )
        out.append(f'<path d="{d}" class="m-{esc(cls)}"/>')
    out.append("</svg>")
    return "".join(out)


def quintile_classes(
    values: dict[str, float | None],
) -> tuple[dict[str, str], list[tuple[float, float]]]:
    """Put counties in five equal-count groups by value (for shading only).

    Returns:
        County id to ``q1``..``q5`` (``none`` without a value), and each group's lowest and
        highest value, for the legend.
    """
    ranked = sorted((v, k) for k, v in values.items() if v is not None)
    out = {k: "none" for k, v in values.items() if v is None}
    groups: list[list[float]] = [[] for _ in range(5)]
    for i, (v, k) in enumerate(ranked):
        g = min(4, i * 5 // max(len(ranked), 1))
        out[k] = f"q{g + 1}"
        groups[g].append(v)
    return out, [(min(g), max(g)) for g in groups if g]


def _polys(geom: dict[str, Any]) -> list[Any]:
    if geom.get("type") == "Polygon":
        return [geom["coordinates"]]
    if geom.get("type") == "MultiPolygon":
        return list(geom["coordinates"])
    return []


# --------------------------------------------------------------------------- slide builders


def _state_name(data: Data) -> str:
    got = data.saved(f"{data.state}/summary.json")
    if got:
        return got.value.get("name", data.state)
    live = data.live("GET", f"/states/{data.state}/summary")
    return live.value.get("name", data.state) if live else data.state


def _pending_box(text: str) -> str:
    return f'<p class="pending-box">{esc(text)}</p>'


def _limits(run: dict[str, Any] | None, key: str, k: int = 3) -> list[str]:
    lim = ((run or {}).get("explanation") or {}).get("limits") or {}
    entry = lim.get(key) or (lim.get("reforms") or {}).get(key) or {}
    return [x["text"] for x in entry.get("items", [])[:k]]


def _ul(items: Iterable[str], cls: str = "bullets") -> str:
    return f'<ul class="{cls}">' + "".join(f"<li>{esc(t)}</li>" for t in items) + "</ul>"


def _reform_title(run: dict[str, Any] | None, key: str) -> str:
    rr = ((run or {}).get("reform_results") or {}).get(key) or {}
    return rr.get("title") or key


def build_slides(data: Data, deck: Deck, screens: Path | None = None) -> None:
    """Add every slide, in order. ``screens`` is the folder of tool pictures, if any."""
    st = data.state
    name = _state_name(data)
    base_got = data.run("baseline")
    base_run = base_got.value if base_got else None
    runs = {k: data.run(k) for k in (*BENCHMARKS, "package")}
    expl = (base_run or {}).get("explanation") or {}
    span_years = expl.get("span") or ""

    # 1. Title
    deck.add(
        "VisionHousing",
        "If the state changed the rules, how many more homes would be permitted?",
        '<p class="lead">An open-source tool that gives a likely range for the extra homes a '
        "state housing reform would bring over five years, shows what drives it, and says "
        "what it cannot tell you.</p>"
        '<p class="sub">Every number traceable. No AI inside the tool. Apache-2.0.</p>',
        kind="title",
    )

    # 2. Problem and user
    deck.add(
        "The problem",
        "State analysts must answer a hard question quickly",
        '<div class="two"><div>'
        "<h3>The question</h3>"
        + _ul(
            [
                "A bill would allow backyard homes, small apartment buildings or fewer parking "
                "minimums. How many more homes would that bring, and where?",
                "Answers today are single numbers with no reasons, or studies of one city.",
            ]
        )
        + "</div><div><h3>The user</h3>"
        + _ul(
            [
                "A state policy analyst or legislative staffer, not a modeller.",
                "Needs a defensible range, the main reasons, and the limits, on one screen.",
                "Needs to show the work to colleagues who will question it.",
            ]
        )
        + "</div></div>",
    )

    # 3. Two questions
    deck.add(
        "What the tool does",
        "It answers two questions",
        '<div class="two"><div class="card"><h3>1. What if the state changed the rules?</h3>'
        "<p>Five benchmark reforms, alone or together. A likely range of the extra homes "
        "permitted over five years, against a no-change baseline, by state, county and "
        "district.</p></div>"
        '<div class="card"><h3>2. What is this place like today?</h3>'
        "<p>For any county or census tract: people, homes and prices, jobs, what is being "
        "built, zoning, water and sewer, flood risk. One printable page.</p></div></div>",
    )

    # 4. Outcome
    rc = data.either([f"{st}/report-card.json"], f"/report-card?state={st}")
    rcv = deck.use(rc) or {}
    permits = next((i for i in rcv.get("inputs", []) if i.get("id") == "permits"), {})
    deck.use(base_got)
    vint = permits.get("vintage", "")
    deck.add(
        "The outcome",
        "We count homes authorized by building permits over five years",
        '<div class="two"><div>'
        f'<p class="big">{deck.n(span_years, "five-year window", base_got, "explanation.span")}'
        '</p><p class="stat-label">the five-year window</p>'
        + _ul(
            [
                "Outcome: housing units authorized by building permits, summed over five years.",
                "Source: the Census Bureau's Building Permits Survey, every county, every year.",
            ]
        )
        + "</div><div><h3>Why permits</h3>"
        + _ul(
            [
                "Published for every county in every state, the same way, with years of history.",
                "Out within months, so the baseline can be checked against what happened.",
                "A permit is the step a reform changes first. Not every permit becomes a home; "
                "we say so.",
            ]
        )
        + (
            f'<p class="note">Permit files used: {deck.n(vint, "permit vintage", rc)}</p>'
            if vint
            else ""
        )
        + "</div></div>",
    )

    # 5. Data
    states = data.live("GET", "/states") or data.saved("states.json")
    sv = deck.use(states) or {}
    st_names = [s.get("name", s.get("code")) for s in sv.get("states", [])]
    prov = data.either([f"{st}/provenance.json"], f"/provenance?state={st}")
    pv = deck.use(prov) or {}
    tiers: dict[int, int] = {}
    for s in pv.get("sources", []):
        tiers[s.get("tier", 0)] = tiers.get(s.get("tier", 0), 0) + 1
    counts = rcv.get("counts") or {}
    tier_rows = [
        ("Tier 0: national (permits, Census survey, national pay)", tiers.get(0)),
        ("Tier 1: state (state pay, transit feeds, state law)", tiers.get(1)),
        ("Tier 2: local (assessment data, local permits, ordinances)", tiers.get(2)),
    ]
    tier_html = "".join(
        f"<li>{esc(t)}: <b>{deck.n(str(c), f'{st} sources at this tier', prov)}</b> "
        f"source{'s' if c != 1 else ''}</li>"
        for t, c in tier_rows
        if c
    )
    deck.add(
        "Data",
        "Public data only, fetched by one script",
        '<div class="two"><div>'
        "<h3>States the same code runs</h3>"
        + _ul(st_names or ["-"])
        + '<p class="note">Testland is a small synthetic state for the tests; it needs no '
        "download.</p></div><div>"
        f'<h3>{esc(name)}: sources by data tier</h3><ul class="bullets">{tier_html}</ul>'
        + (
            '<p class="note">Report card: '
            f"{deck.n(str(counts.get('used', '-')), 'inputs used', rc)} "
            f"inputs used, {deck.n(str(counts.get('assumed', '-')), 'inputs assumed', rc)} set by "
            "assumption, "
            f"{deck.n(str(counts.get('missing', '-')), 'inputs missing', rc)} missing.</p>"
            if counts
            else ""
        )
        + '<p class="note"><code>make data STATE=PA</code> downloads and checks every file '
        "(SHA-256); none is stored in the repository.</p></div></div>",
    )

    # 6. Method in four steps
    draws = (base_run or {}).get("draws")
    deck.use(base_got)
    deck.add(
        "Method",
        "Four steps, each one simple enough to check",
        '<ol class="steps">'
        "<li><b>Baseline, back-tested.</b> Carry forward recent permits by county. The rule "
        "won a contest on past years, judged by a test fixed in advance.</li>"
        "<li><b>A funnel per reform.</b> Lots allowed, lots that fit, projects that pay, owners "
        "who build. Each step is a number you can open.</li>"
        "<li><b>Paired Monte Carlo.</b> "
        + (deck.n(f"{draws:,}", "draws per run", base_got, "draws") if draws else "Many")
        + " simulated futures. Baseline and reform share each future, so the reform's effect "
        "is measured like for like.</li>"
        "<li><b>Ranges.</b> We report P10 to P90: eight in ten simulated futures fall inside. "
        "Never one number.</li></ol>",
    )

    # 7. Baseline and the contest
    val = data.either([f"{st}/validation.json"], f"/validation?state={st}")
    vv = deck.use(val) or {}
    b = (base_run or {}).get("baseline")
    deck.use(base_got)
    rows = "".join(
        _contest_row(r, deck, val)
        for r in sorted(
            vv.get("rows", []),
            key=lambda r: (not r.get("chosen"), r.get("median_abs_log_ratio") or 9.0),
        )[:5]
    )
    card = data.saved(f"{st}/baseline-card.json")
    card_rows = {r["id"]: r for r in ((deck.use(card) or {}).get("view") or {}).get("rows", [])}
    card_html = "".join(
        f'<p class="note"><b>{esc(card_rows[i]["label"])}:</b> '
        f"{deck.n(card_rows[i]['text'], f'baseline card: {i}', card)}</p>"
        for i in ("reconciliation", "baseline_bridge_nowcast_2026")
        if i in card_rows
    )
    deck.add(
        "The baseline",
        f"With no reform, {name} would likely permit",
        '<div class="two"><div>'
        f'<p class="big">{deck.n(span(b), "baseline range", base_got, "baseline")}</p>'
        f'<p class="stat-label">homes, {esc(span_years)} (middle '
        f"{deck.n(homes((b or {}).get('p50')), 'baseline middle', base_got, 'baseline.p50')})</p>"
        f"<p>{deck.n(str(len(vv.get('rows', []))), 'rules in the contest', val)} simple rules were "
        "tried on every past five-year window. The winner has the smallest typical miss; its "
        "ranges are built from its own past misses. The five best are shown.</p>"
        + card_html
        + "</div><div>"
        "<table><tr><th>Rule</th><th>Past cases</th><th>Typical miss</th>"
        f"<th>Range caught the real total</th></tr>{rows}</table>"
        '<p class="note">Typical miss = median |log(actual / projected)|. A P10-P90 range aims '
        "to catch about 8 in 10.</p></div></div>",
    )

    # 8. Honest back-test by place
    bp = vv.get("by_place") or {}
    pick = [
        g
        for g in bp.get("groups", [])
        if g.get("dimension") in ("named", "size_class") and g.get("group") != "rest of the state"
    ]
    chart = bars(
        [
            (
                g["group"] if g["dimension"] == "named" else g["group"].capitalize() + " counties",
                g["coverage"],
                f"{g.get('caught_text', '')} · {g.get('error_text', '')}",
            )
            for g in pick
            if g.get("coverage") is not None
        ],
        target=0.8,
    )
    for g in pick:
        deck.n(g.get("caught_text", ""), f"back-test coverage, {g['group']}", val)
    deck.add(
        "Honest back-test",
        "Where the baseline has missed, we say so by name",
        f'<div class="chart-wide">{chart}</div>'
        + (f'<p class="callout">{esc(bp["limits"])}</p>' if bp.get("limits") else ""),
    )

    # 9. Overview of the five reforms
    over_rows = []
    for k in (*BENCHMARKS, "package"):
        g = runs[k]
        rv = deck.use(g)
        if not rv:
            continue
        if k == "package":
            rec, pc, title = (
                rv["package"]["combined"],
                rv["package"].get("pct_change"),
                "All five together",
            )
        else:
            rr = rv["reform_results"][k]
            rec, pc, title = rr["added"], rr.get("pct_change"), rr["title"]
        text = f"{span(rec)}  ({span(pc, pct)})"
        deck.n(text, f"added homes, {k}", g, "added")
        over_rows.append((title, rec, text))
    deck.add(
        "Results at a glance",
        f"Extra homes permitted in {name}, {span_years}",
        f'<div class="chart-wide">{range_chart(over_rows)}</div>'
        '<p class="note">Bar = likely range (P10 to P90); tick = middle (P50). In brackets: the '
        "same as a share of the baseline. The future level is uncertain; the reform's effect "
        "much less so.</p>",
    )

    # 10-14. One slide per reform
    for k in BENCHMARKS:
        g = runs[k]
        rv = deck.use(g)
        if not rv:
            deck.add("Reform", k, _pending_box("No saved run for this reform yet."))
            continue
        rr = rv["reform_results"][k]
        e = rv.get("explanation") or {}
        verdict = ((e.get("verdicts") or {}).get(k) or {}).get("word", "")
        drv = (e.get("drivers") or {}).get(k) or {}
        items = drv.get("items", [])[:3]
        top = items[0]["text"]["text"] if items else ""
        for x in items:
            deck.n(
                f"{homes(x['total_low'])} / {homes(x['total_high'])}", f"driver {x['parameter']}", g
            )
        lim = _limits(rv, k, 2)
        head = (rr.get("accounting") or {}).get("headline") or {}
        comp = ((rr.get("accounting") or {}).get("records") or {}).get("comparable")
        scope_html = ""
        if head.get("scope") and head.get("is_comparable") is False and comp:
            scope_html = (
                f'<p class="note">The headline counts {esc(head["scope"])}. Census-comparable '
                f"(survey definition): {deck.n(span(comp), f'comparable {k}', g)} (middle "
                f"{deck.n(homes(comp.get('p50')), f'comparable middle {k}', g)}).</p>"
            )
        deck.add(
            f"Reform {BENCHMARKS.index(k) + 1} of 5",
            rr["title"] + (f": {verdict}" if verdict else ""),
            '<div class="two"><div>'
            f'<p class="big">{deck.n(span(rr["added"]), f"added homes {k}", g, "added")}</p>'
            f'<p class="stat-label">more homes, {esc(span_years)} (middle '
            f"{deck.n(homes(rr['added'].get('p50')), f'middle {k}', g)}); "
            f"{deck.n(span(rr.get('pct_change'), pct), f'share of baseline {k}', g)}"
            " of the baseline</p>"
            + scope_html
            + f"<h3>Top driver</h3><p>{deck.n(top, f'top driver {k}', g)}</p>"
            "<h3>What it cannot tell you</h3>"
            + _ul(lim)
            + "</div><div><h3>Sensitivity: added homes as each input moves low to high</h3>"
            + tornado(items, drv.get("central_total"))
            + '<p class="note">Each input moves alone from its low to its high value '
            "(Assumption Ledger); the others stay central. Tick = all inputs central.</p>"
            "</div></div>",
        )

    # 15. Pennsylvania bills (only where saved)
    bills = [(k, data.run(k)) for k in ("pa_hb2186", "pa_sb1346")]
    bills = [(k, g) for k, g in bills if g]
    if bills:
        adu = runs["adu"]
        rows_b = []
        if adu:
            rec = deck.use(adu)["reform_results"]["adu"]["added"]
            rows_b.append(("ADU benchmark", rec, deck.n(span(rec), "ADU added", adu)))
        for k, g in bills:
            rr = deck.use(g)["reform_results"][k]
            rows_b.append((rr["title"].split(":")[0], rr["added"], deck.n(span(rr["added"]), k, g)))
        bg = data.saved(f"{st}/adu-budget-grid.json")
        bview = (deck.use(bg) or {}).get("view") or {}
        budget_html = ""
        # One tested cell: the largest appropriation at the largest award (both arms alike).
        cells = {(c["arm"], c["appropriation"], c["award"]): c for c in bview.get("cells", [])}
        pick = max(((a, w) for arm, a, w in cells if arm == "finance_only"), default=(None, None))
        prog, both = cells.get(("finance_only", *pick)), cells.get(("both", *pick))
        if prog and both and prog.get("added") and both.get("added"):
            budget_html = (
                '<p class="note"><b>Budget-capped ADU financing</b> ('
                f"{deck.n(prog['appropriation_text'], 'budget appropriation', bg)}, "
                f"{deck.n(prog['award_text'], 'budget award', bg)} per award): the program "
                f"alone adds {deck.n(prog['added']['likely'], 'capped program added', bg)}; "
                "with the ADU law change, "
                f"{deck.n(both['added']['likely'], 'capped program with law added', bg)}. "
                f"{esc(bview.get('uncapped_note', ''))}</p>"
            )
        deck.add(
            "Real bills",
            "The same engine scores bills now before the legislature",
            f'<div class="chart-wide">{_chart_of(rows_b)}</div>'
            + _ul([_reform_title(g.value, k) for k, g in bills])
            + '<p class="note">A pending bill never changes the baseline; it is scored only in '
            "its own run.</p>" + budget_html,
        )

    # 16. Package and interactions
    pk = runs["package"]
    pv_ = deck.use(pk)
    if pv_:
        steps = ((pv_.get("explanation") or {}).get("waterfall") or {}).get("steps", [])
        rows_p = []
        for s in steps:
            rec = {"p10": s.get("p10"), "p50": s.get("p50"), "p90": s.get("p90")}
            rows_p.append((s["label"]["text"], rec, deck.n(span(rec), s["label"]["text"], pk)))
        notes = _limits(pv_, "package", 2)
        deck.add(
            "Reforms together",
            "Reforms do not simply add up",
            f'<div class="chart-wide">{_chart_of(rows_p)}</div>' + _ul(notes),
        )

    # 17. Cost per added home (fees) and what-ifs
    fg = runs["fees"]
    fv = deck.use(fg) or {}
    ro = ((fv.get("reform_results") or {}).get("fees") or {}).get("readouts") or {}
    if not ro.get("cost_per_added_home"):
        # The saved fee run predates the readouts: ask the running tool, if there is one.
        live = data.live("POST", "/run", {"state": st, "reforms": ["fees"], "explain": False})
        lro = (((live.value if live else {}).get("reform_results") or {}).get("fees") or {}).get(
            "readouts"
        ) or {}
        if lro.get("cost_per_added_home"):
            fg, ro = live, lro
    cost = ro.get("cost_per_added_home")
    rev = ro.get("revenue_given_up")
    to_added = ro.get("share_to_added_homes")
    comps = data.comparisons()
    cv = deck.use(comps) if comps else None
    if cost:
        body = (
            '<div class="two"><div>'
            '<p class="big-sm">'
            f"{deck.n(span(cost, money), 'cost per added home', fg, 'readouts.cost_per_added')}"
            "</p>"
            '<p class="stat-label">fee revenue given up per extra home</p>'
            + (
                f"<p>Revenue given up: {deck.n(span(rev, money), 'revenue given up', fg)}. "
                if rev
                else "<p>"
            )
            + (
                f"Only {deck.n(span(to_added, share), 'share to added homes', fg)} of it reaches "
                "homes that would not have been built anyway.</p>"
                if to_added
                else "</p>"
            )
            + "</div><div>"
        )
    else:
        body = (
            '<div class="two"><div>'
            + _pending_box(
                "The cost per added home is worked out per draw by the engine; the saved fee run "
                "predates it. It appears here after the final export (make export-static)."
            )
            + "<p>A fee cut applies to every permitted home, not only the extra ones. So most "
            "of the revenue given up goes to homes that would be built anyway.</p></div><div>"
        )
    if cv:
        body += "<h3>What-ifs</h3>" + _what_ifs(cv, deck, comps)
    else:
        body += (
            "<h3>What-ifs</h3>"
            + _ul(
                [
                    "Policy dials: try the fee cut at other levels and see where it flattens.",
                    "Two-input matrix: take-up against construction cost.",
                    "Target seeker: what would it take to reach a goal?",
                ]
            )
            + _pending_box("Dial results appear here once they are exported.")
        )
    deck.add("Cost and what-ifs", "A fee cut has a price per extra home", body + "</div></div>")

    # 18. Where
    mp = data.saved(f"{st}/maps/package.json")
    ol = data.saved(f"{st}/outlines.json")
    mv, ov = deck.use(mp), deck.use(ol)
    dist = data.live("GET", f"/states/{st}/districts")
    dv = deck.use(dist) if dist else None
    if mv and ov:
        classes, edges = quintile_classes(
            {c["fips"]: (c.get("change") or {}).get("p50") for c in mv.get("counties", [])}
        )
        top = sorted(mv["counties"], key=lambda c: -(c.get("added") or {}).get("p50", 0))[:5]
        legend = "".join(
            f'<li><span class="sw m-q{i + 1}"></span>{esc(pct(lo))} to {esc(pct(hi))}</li>'
            for i, (lo, hi) in enumerate(edges)
        )
        top_rows = "".join(
            f"<li>{esc(c['name'])}: {deck.n(span(c['added']), 'county added', mp)}</li>"
            for c in top
        )
        dist_line = (
            f'<p class="note">{deck.n(str(dv.get("district_count")), "districts", dist)} '
            "state House districts each get a one-page brief, built from county weights.</p>"
            if dv
            else ""
        )
        deck.add(
            "Where",
            "All five reforms together, county by county",
            f'<div class="two map-two"><div>{county_map(ov, classes)}'
            f'<ul class="legend">{legend}</ul></div><div>'
            f'<h3>Most added homes (likely range)</h3><ul class="bullets">{top_rows}</ul>'
            '<p class="note">Shading: added homes as a share of the county\'s own baseline '
            "(middle estimate), in five equal-count groups of counties.</p>"
            f"{dist_line}</div></div>",
        )

    # 19. Local law layer and timeline
    imp = data.either([f"{st}/implementation.json"], f"/implementation/{st}")
    iv = deck.use(imp) or {}
    evs = data.either(["evidence-states.json"], "/evidence/states")
    ev = deck.use(evs) or {}
    lines = [f"{r.get('citation', '')}: {r.get('line', '')}" for r in iv.get("rows", [])[:5]]
    for t in lines:
        deck.n(t, "law timeline row", imp)
    summ = ev.get("summary") or {}
    deck.add(
        "Local law and timing",
        "Which rules are in force, and since when",
        '<div class="two"><div><h3>The law timeline</h3>'
        + (_ul(lines) if lines else _pending_box("Timeline not in this build."))
        + "</div><div><h3>Reform records across states</h3>"
        + (
            f'<p class="big-sm">{deck.n(str(summ.get("records")), "policy records", evs)}</p>'
            '<p class="stat-label">records ('
            f"{deck.n(str(summ.get('verified')), 'verified records', evs)} verified) in "
            f"{deck.n(str(summ.get('states_with_records')), 'states with records', evs)} states</p>"
            if summ
            else _pending_box("Cross-state records not in this build.")
        )
        + "<p>Local rules read from ordinances switch the funnel to the real legal status. "
        'Where no rule is read, the status is unknown, not "no rule".</p></div></div>',
    )

    # 19b. Alternatives at a glance
    _alternatives_slide(data, deck, st)

    # 20. Sparse data
    _sparse_slide(data, deck, runs, name)

    # 21. Validation
    _validation_slide(data, deck, st)

    # 22. Transparency
    adu = runs["adu"]
    av = deck.use(adu) or {}
    formula = (
        ((av.get("reform_results") or {}).get("adu") or {}).get("added", {}).get("formula", "")
    )
    rep = data.live("GET", f"/reproduce?id={st}:adu:reform_results.adu.added")
    rv_ = deck.use(rep) if rep else None
    led = data.either([f"{st}/ledger.json"], f"/ledger?state={st}")
    lv = deck.use(led) or {}
    kinds: dict[str, int] = {}
    for p in lv.get("parameters", []):
        kinds[p.get("kind", "?")] = kinds.get(p.get("kind", "?"), 0) + 1
    kind_line = ", ".join(
        f"{deck.n(str(v), f'ledger {k}', led)} {esc(k)}" for k, v in sorted(kinds.items())
    )
    cmd = (rv_ or {}).get("command") or ""
    short_formula = formula[:330] + ("..." if len(formula) > 330 else "")
    stamp = ((av.get("explanation") or {}).get("stamp")) or {}
    receipt = cmd or (
        f"seed {stamp.get('seed')}, {stamp.get('draws')} draws; "
        f"code {str(stamp.get('code', ''))[:8]}, data {str(stamp.get('data', ''))[:8]}, "
        f"assumptions {str(stamp.get('assumptions', ''))[:8]}"
    )
    deck.add(
        "Transparency",
        "Every number shows its working",
        '<div class="two"><div><h3>One number, opened</h3>'
        f'<p class="formula">{deck.n(short_formula, "ADU formula", adu)}</p>'
        "<h3>Reproduce receipt</h3>"
        f'<p class="formula">{deck.n(receipt, "reproduce command", rep or adu)}</p>'
        "</div><div><h3>Assumptions ledger</h3>"
        f"<p>{deck.n(str(lv.get('count', '-')), 'ledger parameters', led)} parameters, each with a "
        f"low, central and high value, a source or a stated judgement: {kind_line}.</p>"
        "<h3>No AI inside</h3><p>The tool calls no AI model. Every sentence with a number is "
        "written by a rule from the engine's records. An AI coding assistant wrote code under "
        "the team's review; this is disclosed.</p></div></div>",
    )

    # 23. Usability
    deck.add("Usability", "Five screens, one map, plain words", _usability_body(screens))

    # 24. Replicability
    targets = _make_targets()
    sbom = ROOT / "sbom" / "visionhousing.cdx.json"
    comp = None
    sbom_got = None
    if sbom.is_file():
        comp = len(json.loads(sbom.read_text(encoding="utf-8")).get("components", []))
        sbom_got = Got(None, "sbom/visionhousing.cdx.json")
    wf = sorted(p.name for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    deck._src.append("Makefile")
    deck.add(
        "Replicability",
        "One command from a clean machine",
        '<div class="two"><div><table class="cmds">'
        + "".join(
            f"<tr><td><code>make {esc(t)}</code></td><td>{esc(d)}</td></tr>" for t, d in targets
        )
        + "</table></div><div>"
        + _ul(
            [
                "Continuous integration on every push: " + ", ".join(wf) + ".",
                "Dependabot and CodeQL; secret scanning with push protection.",
                (
                    f"Software bill of materials: "
                    f"{html.unescape(deck.n(str(comp), 'SBOM components', sbom_got))} "
                    "components (CycloneDX)."
                    if comp
                    else "Software bill of materials (CycloneDX)."
                ),
                "Permissive licences only, checked by the licence audit. Apache-2.0.",
                "Assumptions live in assumptions/, never in code. A new state is a new data pack.",
            ]
        )
        + "</div></div>",
    )

    # 25. Limits
    deck.use(base_got)
    deck.add(
        "Limits",
        "What these estimates cannot tell you",
        _ul(_limits(base_run, "general", 6) or ["-"], "bullets big-bullets"),
    )

    # 26. Next
    deck.add(
        "What's next",
        "Where the tool goes from here",
        _ul(
            [
                "Read more local ordinances, so fewer counties rely on the statewide share.",
                "Add states: a new state is a new data pack and the same code.",
                "Track real permits after reforms pass, and score our ranges against them.",
                "Approval timing: model how faster permits move homes into the window.",
            ],
            "bullets big-bullets",
        ),
    )

    # 27. Credits
    deck.add(
        "Thank you",
        "VisionHousing",
        '<p class="lead">Built for the Housing Data and Technology Innovation Challenge.</p>'
        '<p class="sub">Team lead: Ye Zhang. Public data from the U.S. Census Bureau, HUD, '
        "state agencies and local governments; see DATA_PROVENANCE.md. Code under Apache-2.0.</p>",
        kind="title",
    )


USABILITY_LINES = [
    "Slides, a one-page brief, CSV and a share link from every screen.",
    "Keyboard paths, screen-reader labels and a phone layout are tested in a real "
    "browser on every check.",
    "A no-server copy runs from saved files, so anyone can open it.",
]


def screen_files(screens: Path | None) -> list[tuple[Screen, Path]]:
    """The tool pictures present in ``screens``, in slide order (all six, or none)."""
    if screens is None:
        return []
    found = []
    for sc in SCREENS:
        hit = next(
            (
                screens / f"{sc.name}{ext}"
                for ext in (".jpg", ".png")
                if (screens / f"{sc.name}{ext}").is_file()
            ),
            None,
        )
        if hit is None:
            return []
        found.append((sc, hit))
    return found


def _usability_body(screens: Path | None) -> str:
    """The Usability slide: a 2 x 3 grid of tool pictures, or text cards without them."""
    shots = screen_files(screens)
    if shots:
        cells = "".join(
            f'<figure class="shot"><img src="{esc(SCREENS_NAME + "/" + f.name)}" '
            f'alt="{esc(sc.caption)}"><figcaption>{esc(sc.caption)}</figcaption></figure>'
            for sc, f in shots
        )
        return (
            f'<div class="shots">{cells}</div>'
            f'<p class="note shots-note">{esc(" ".join(USABILITY_LINES))} '
            "Pictures of the running tool; every number in them is the tool's own text.</p>"
        )
    return (
        '<div class="grid5">'
        '<div class="card"><h3>State</h3><p>The baseline and the likely range, with how well '
        "the rule did in the past.</p></div>"
        '<div class="card"><h3>Reform</h3><p>A five-step decision view: answer, where, why, '
        "what would narrow the range.</p></div>"
        '<div class="card"><h3>Compare</h3><p>Reforms side by side and together.</p></div>'
        '<div class="card"><h3>Place</h3><p>A county or tract profile, printable on one '
        "page.</p></div>"
        '<div class="card"><h3>Method &amp; data</h3><p>Back-test, data report card, '
        "assumptions and sources.</p></div></div>" + _ul(USABILITY_LINES)
    )


def _contest_row(r: dict[str, Any], deck: Deck, val: Got | None) -> str:
    """One row of the baseline rule contest."""
    chosen = bool(r.get("chosen"))
    miss = r.get("median_abs_log_ratio")
    return (
        f"<tr{' class="chosen"' if chosen else ''}><td><code>{esc(r['rule'])}</code>"
        f"{' (chosen)' if chosen else ''}</td>"
        f"<td>{deck.n(str(r.get('cases')), 'back-test cases', val)}</td>"
        f"<td>{deck.n('-' if miss is None else f'{miss:.2f}', 'typical miss', val)}</td>"
        f"<td>{deck.n(share(r.get('coverage_80')), 'range caught', val)}</td></tr>"
    )


def _chart_of(rows: list[tuple[str, dict[str, Any] | None, str]]) -> str:
    """A range chart from rows whose value text is already escaped."""
    return range_chart([(a, r, html.unescape(t)) for a, r, t in rows])


def _what_ifs(cv: dict[str, Any], deck: Deck, got: Got) -> str:
    """The fee-cut dial (or the first dial) from the comparisons reply."""
    dials = (cv.get("dials") or {}).get("dials") or {}
    d = dials.get("fees_cut") or next(iter(dials.values()), None)
    if not d:
        return _pending_box("Dial results appear here once they are exported.")
    rows = [
        f"<li>{esc(lv.get('label', ''))}: {deck.n(span(lv.get('record')), 'dial level', got)}</li>"
        for lv in (d.get("levels") or [])[:5]
    ]
    sentence = (d.get("each_step") or {}).get("sentence", "")
    return (
        f"<p>{esc(d.get('title', ''))}: added homes at each level.</p>"
        f'<ul class="bullets">{"".join(rows)}</ul>'
        + (f'<p class="note">{deck.n(sentence, "dial step sentence", got)}</p>' if sentence else "")
    )


def _alternatives_slide(data: Data, deck: Deck, st: str) -> None:
    """One summary slide: each reform's headline, how many alternatives, the widest swing.

    Every line is the server's own text (``api.alternatives``: saved ``ST/alternatives.json``
    or the live ``GET /states/ST/alternatives``); nothing is worked out here.
    """
    got = data.either([f"{st}/alternatives.json"], f"/states/{st}/alternatives")
    av = deck.use(got) or {}
    rows = []
    for key in BENCHMARKS:
        part = (av.get("reforms") or {}).get(key)
        if not part:
            continue
        s = part["summary"]
        rows.append(
            f"<tr><td>{esc(part['title'])}</td>"
            f"<td>{deck.n(s['headline'], f'{key} headline', got, f'reforms.{key}.summary')}</td>"
            f"<td>{deck.n(s['count_text'], f'{key} alternatives', got)}</td>"
            f"<td>{deck.n(s['widest_text'], f'{key} widest swing', got)}</td></tr>"
        )
    body = (
        "<table><tr><th>Reform</th><th>Headline, middle (likely range)</th>"
        "<th>Alternatives</th><th>Widest swing (paired, middle)</th></tr>"
        + "".join(rows)
        + "</table>"
        '<p class="note">One headline per reform. Every named alternative (named variants, the '
        "capacity screen, project choice, the Census-comparable scope) sits one click away in the "
        "tool, run on the same draws; the swing is the alternative minus the benchmark, draw by "
        "draw.</p>"
        if rows
        else _pending_box("The alternatives table is not in this build yet.")
    )
    deck.add("Alternatives", "One headline per reform, every alternative one click away", body)


def _sparse_slide(data: Data, deck: Deck, runs: dict[str, Got | None], name: str) -> None:
    """Vermont next to the main state (and Wyoming, if the running tool answers)."""
    other = "VT" if data.state != "VT" else "PA"
    rows = []
    tod_notes: list[str] = []
    for st in (data.state, other, "WY"):
        b = data.saved(f"{st}/runs/baseline.json")
        p = data.saved(f"{st}/runs/package.json")
        if st == "WY" and p is None and data.api is not None:
            p = data.live(
                "POST", "/run", {"state": "WY", "reforms": list(BENCHMARKS), "explain": False}
            )
            b = p
        if not p:
            continue
        pv = deck.use(p)
        bv = deck.use(b) if b else pv
        tod_view = (pv.get("status_views") or {}).get("tod") or {}
        if tod_view.get("shown") is False and tod_view.get("package_note"):
            tod_notes.append(deck.n(tod_view["package_note"], f"{st} TOD status", p))
        label = st
        sm = data.saved(f"{st}/summary.json")
        if sm:
            label = sm.value.get("name", st)
        elif st == "WY":
            label = "Wyoming"
        rows.append(
            (
                label,
                deck.n(span(bv["baseline"]), f"{st} baseline", b or p),
                deck.n(span(pv["package"]["combined"]), f"{st} package", p),
                deck.n(span(pv["package"].get("pct_change"), pct), f"{st} package share", p),
            )
        )
    table = "".join(
        f"<tr><td>{esc(a)}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>" for a, b, c, d in rows
    )
    deck.add(
        "Thin data",
        "The same code runs where data is thin",
        "<table><tr><th>State</th><th>Baseline homes</th><th>All five reforms add</th>"
        f"<th>Share of baseline</th></tr>{table}</table>"
        + _ul(
            [
                "Missing local inputs fall back to state, then national values, and the range "
                "widens. Every number shows its data tier.",
                "Vermont and Wyoming have no parcel or local zoning data. Where too few bus "
                "stops have measured service, the statewide transit range is not shown, and "
                "the tool says why.",
            ]
        )
        + "".join(f'<p class="note">Wyoming: {t}</p>' for t in tod_notes)
        + _ablation(data, deck),
    )


def _ablation(data: Data, deck: Deck) -> str:
    """The data-ablation sentences (national data only), or a pending note."""
    got = data.comparisons()
    cv = deck.use(got) if got else None
    tier0 = (((cv or {}).get("ablation") or {}).get("tiers") or {}).get("0") or {}
    lines = [
        r["sentence"]
        for k, r in (tier0.get("reforms") or {}).items()
        if k in ("adu", "fees") and r.get("sentence")
    ]
    if not lines:
        return _pending_box(
            "Data ablation (the main state re-run with its local data removed) appears here "
            "once its results are exported."
        )
    return "<h3>Ablation: the main state with national data only</h3>" + "".join(
        f'<p class="note">{deck.n(t, "ablation sentence", got)}</p>' for t in lines
    )


def _validation_slide(data: Data, deck: Deck, st: str) -> None:
    """California check, outside studies, plausibility and evidence grades."""
    evg = data.either(["evidence.json"], "/evidence")
    ev = deck.use(evg) or {}
    by_reform: dict[str, int] = {}
    for e in ev.get("entries", []):
        by_reform[e.get("reform", "?")] = by_reform.get(e.get("reform", "?"), 0) + 1
    pl = data.either([f"{st}/plausibility.json"], f"/plausibility/{st}")
    plv = deck.use(pl) if pl else None
    conf = data.either([f"{st}/confidence.json"], f"/confidence/{st}")
    cfv = deck.use(conf) if conf else None
    grades = []
    for k in ("baseline", *BENCHMARKS):
        g = data.either(
            [f"{st}/grades/{k}.json"], f"/grades/{st}?r={k}" if k != "baseline" else f"/grades/{st}"
        )
        gv = deck.use(g) if g else None
        one = ((gv or {}).get("grades") or {}).get(k)
        if one:
            grades.append((k, deck.n(one["grade"], f"grade {k}", g)))
    left = (
        f"<p>{deck.n(str(ev.get('count', '-')), 'outside studies', evg)} before-and-after "
        "records from other places (California, Seattle, Portland, Minneapolis, Buffalo and "
        "more) set and check the take-up ranges.</p>"
    )
    if plv:
        adu = ((plv.get("reforms") or {}).get("adu") or {}).get("measures") or []
        if adu:
            m = adu[0]
            mod, band = m.get("model") or {}, m.get("band") or {}
            shown_m, shown_b = mod.get("shown") or {}, band.get("shown") or {}
            left += (
                "<h3>Plausibility: ADUs</h3><p>Model: "
                f"{deck.n(f'{shown_m.get("p10")} to {shown_m.get("p90")}', 'ADU rate', pl)} "
                f"{esc(m.get('label', ''))}; other places: "
                f"{deck.n(f'{shown_b.get("low")} to {shown_b.get("high")}', 'observed', pl)}"
                f" ({esc(band.get('position', ''))} the observed band).</p>"
            )
    if cfv:
        s = (cfv.get("baseline_performance") or {}).get("sentence")
        if s:
            left += f"<h3>Held-out state check</h3><p>{deck.n(s, 'held-out check', conf)}</p>"
    names = {
        "baseline": "Baseline",
        "adu": "ADUs",
        "missing_middle": "Missing middle",
        "tod": "Transit-oriented density",
        "parking": "Parking",
        "fees": "Fees",
    }
    right = (
        "<h3>Evidence grades</h3><table>"
        + "".join(f'<tr><td>{esc(names[k])}</td><td class="grade">{g}</td></tr>' for k, g in grades)
        + '</table><p class="note">A grade says how much evidence stands behind a number '
        "(sources, back-test, law read, calibration, outside studies). It is not a "
        "probability.</p>"
        if grades
        else _pending_box("Evidence grades appear here once they are exported.")
    )
    deck.add(
        "Validation",
        "Checked against the past and against other places",
        f'<div class="two"><div>{left}</div><div>{right}</div></div>',
    )


def _make_targets() -> list[tuple[str, str]]:
    """The documented make targets and their one-line help, read from the Makefile."""
    want = ("all", "setup", "data", "test", "verify", "app", "export-static")
    out = {}
    for line in (ROOT / "Makefile").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([a-z-]+):.*?## (.*)$", line)
        if m and m.group(1) in want:
            out[m.group(1)] = m.group(2)
    return [(t, out[t]) for t in want if t in out]


# --------------------------------------------------------------------------- HTML and PDF

CSS_FILE = DEFAULT_OUT / "deck.css"


def compress_sources(sources: list[str]) -> list[str]:
    """Group saved files in one folder with one date: ``a/runs/{x, y}.json (saved D)``."""
    groups: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []
    out: list[str] = []
    for src in sources:
        m = re.match(r"^(.*/)([^/]+)\.json (\(saved [0-9-]+\))$", src)
        if not m:
            out.append(src)
            continue
        key = (m.group(1), m.group(3))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(m.group(2))
    for folder, saved in order:
        names = groups[(folder, saved)]
        inner = names[0] if len(names) == 1 else "{" + ", ".join(names) + "}"
        out.append(f"{folder}{inner}.json {saved}")
    return out


def tokens_css() -> str:
    """The app's colour and type tokens (the first ``:root`` block of web/css/tokens.css)."""
    text = TOKENS_CSS.read_text(encoding="utf-8")
    m = re.search(r":root\s*\{.*?\n\}", text, re.S)
    return m.group(0) if m else ""


def render_html(deck: Deck, state: str) -> str:
    """The whole deck as one HTML page, one ``.slide`` per page."""
    total = len(deck.slides)
    parts = []
    for i, s in enumerate(deck.slides, 1):
        src = ""
        if s.sources:
            src = "Numbers from " + "; ".join(compress_sources(s.sources))
        pend = (
            '<span class="pend">Numbers from a saved run made before the final export</span>'
            if s.pending
            else ""
        )
        parts.append(
            f'<section class="slide slide--{s.kind}"><p class="kicker">{esc(s.kicker)}</p>'
            f"<h2>{esc(s.title)}</h2>{s.body}"
            f'<div class="foot"><span class="src">{esc(src)} {pend}</span>'
            f"<span>{i} / {total}</span></div></section>"
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>VisionHousing deck ({esc(state)})</title>"
        f"<style>{tokens_css()}{CSS_FILE.read_text(encoding='utf-8')}</style></head><body>"
        + "".join(parts)
        + "</body></html>"
    )


def print_pdf(html_path: Path, pdf_path: Path) -> int:
    """Print the HTML page to PDF with headless Chromium; return the page count."""
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(html_path.resolve().as_uri())
        page.emulate_media(media="print")
        page.pdf(
            path=str(pdf_path),
            width="13.333in",
            height="7.5in",
            print_background=True,
            prefer_css_page_size=True,
        )
        browser.close()
    return len(PdfReader(str(pdf_path)).pages)


def capture_screens(
    base: str, folder: Path, state: str = "PA", county: str = "42101", timeout_s: int = 1200
) -> list[Path]:
    """Photograph the running tool for the deck; return the files written.

    Opens each view in ``SCREENS`` in headless Chromium at 1920 x 1080 (reduced motion, so
    every animation is already finished), waits until nothing on the page is loading, then
    saves a JPEG at quality 85. A picture over 400 KB is saved again at a lower quality
    (5 steps at a time, not below 60). Compare runs every reform, so the wait is long.
    """
    from playwright.sync_api import sync_playwright

    folder.mkdir(parents=True, exist_ok=True)
    root = base.rstrip("/") + "/#/"
    written: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=GPU_ARGS)
        for sc in SCREENS:
            # A fresh context per view: the tool remembers choices such as 3D in
            # localStorage, and one view must not carry into the next.
            ctx = browser.new_context(viewport=SCREEN_SIZE, reduced_motion="reduce")
            page = ctx.new_page()
            url = root + sc.route.format(st=state, county=county)
            for _attempt in range(sc.tries):
                page.goto(url)
                _settle(page, timeout_s)
                if "did not load" not in page.inner_text("body"):
                    break
                print(f"screen: {sc.name} did not load; trying again")
            else:
                raise SystemExit(f"screen {sc.name}: the tool did not load {url}")
            if sc.step == "3d":
                page.get_by_role("button", name="3D", exact=True).first.click()
                _settle(page, timeout_s)
            elif sc.step == "people":
                page.evaluate(
                    """() => { const h = [...document.querySelectorAll('h3')]
                      .find(e => e.offsetParent && e.textContent.trim() === 'People');
                      const body = h && h.closest('.dock-body');
                      if (body) body.scrollTop += h.getBoundingClientRect().top
                        - body.getBoundingClientRect().top - 8; }"""
                )
                page.wait_for_timeout(800)
            path = folder / f"{sc.name}.jpg"
            quality = SCREEN_QUALITY
            while True:
                page.screenshot(path=str(path), type="jpeg", quality=quality)
                if path.stat().st_size < SCREEN_MAX_BYTES or quality <= 60:
                    break
                quality -= 5
            (folder / f"{sc.name}.png").unlink(missing_ok=True)  # an older PNG copy
            written.append(path)
            print(f"screen: {path.name} ({path.stat().st_size // 1000} KB, quality {quality})")
            ctx.close()
        browser.close()
    return written


def _settle(page: Any, timeout_s: int) -> None:
    """Wait until no part of the page is loading and the map has had time to draw."""
    page.wait_for_function(
        "() => !document.querySelector('[aria-busy=\"true\"]') && "
        "!/Running all five|Loading the/.test(document.body.innerText)",
        timeout=timeout_s * 1000,
        polling=500,
    )
    # Basemap tiles may keep streaming; the pause below is enough when they do.
    with contextlib.suppress(Exception):
        page.wait_for_load_state("networkidle", timeout=15_000)
    page.wait_for_timeout(2500)


def build(
    data_dir: Path = DEFAULT_DATA,
    state: str = "PA",
    out: Path = DEFAULT_OUT,
    api: Caller | None = None,
    pdf: bool = True,
    heavy: bool = False,
) -> dict[str, Any]:
    """Build the deck; return a short report (slides, pages, files).

    Raises:
        SystemExit: More than 30 slides, or the PDF has more than 30 pages.
    """
    data = Data(Path(data_dir), state.upper(), api, heavy)
    deck = Deck()
    build_slides(data, deck, out / SCREENS_NAME)
    if len(deck.slides) > MAX_SLIDES:
        raise SystemExit(f"deck has {len(deck.slides)} slides; the limit is {MAX_SLIDES}")
    out.mkdir(parents=True, exist_ok=True)
    html_path = out / HTML_NAME
    html_path.write_text(render_html(deck, data.state), encoding="utf-8")
    numbers = [n.__dict__ for n in deck.numbers]
    (out / NUMBERS_NAME).write_text(
        json.dumps(
            {
                "state": data.state,
                "built": data.today,
                "slides": [
                    {"n": i, "title": s.title, "sources": s.sources, "pending_export": s.pending}
                    for i, s in enumerate(deck.slides, 1)
                ],
                "numbers": numbers,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    report: dict[str, Any] = {"slides": len(deck.slides), "html": html_path, "pages": None}
    if pdf:
        pdf_path = out / PDF_NAME
        pages = print_pdf(html_path, pdf_path)
        if pages > MAX_SLIDES:
            raise SystemExit(f"PDF has {pages} pages; the limit is {MAX_SLIDES}")
        report.update(pages=pages, pdf=pdf_path)
    return report


def main(argv: list[str] | None = None) -> int:
    """Command line: build the deck and print where it went."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", default="PA")
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA, help="static-site data folder")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--api", default="", help="address of a running tool (make app), optional")
    ap.add_argument(
        "--comparisons", action="store_true", help="ask the tool for dials and ablation (heavy)"
    )
    ap.add_argument("--no-pdf", action="store_true", help="write the HTML and numbers only")
    ap.add_argument(
        "--screens", action="store_true", help="photograph the running tool first (make app)"
    )
    ap.add_argument(
        "--app", default="", help="address of the tool to photograph (default: --api or :8000)"
    )
    ap.add_argument("--county", default="42101", help="county shown on the Place picture")
    args = ap.parse_args(argv)
    if args.screens:
        capture_screens(
            args.app or args.api or "http://127.0.0.1:8000",
            args.out / SCREENS_NAME,
            args.state.upper(),
            args.county,
        )
    api = http_caller(args.api) if args.api else None
    rep = build(args.data, args.state, args.out, api, pdf=not args.no_pdf, heavy=args.comparisons)
    print(f"deck: {rep['slides']} slides -> {rep['html']}")
    if rep.get("pdf"):
        print(f"deck: {rep['pages']} PDF pages -> {rep['pdf']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
