"""The brief as data: an ordered list of blocks built from one explanation.

Plain-language summary
----------------------
The brief is written once, as plain data, and then drawn as Markdown or as a
print-ready HTML page (``report.render``). Both drawings therefore say exactly
the same thing. A block is one of:

- ``title``, ``banner``, ``heading``, ``paragraph``: one ``Text``;
- ``list``: several ``Text`` items (``ordered`` or not);
- ``table``: column names, column alignment and rows of ``Text`` cells;
- ``chart``: a chart spec from the slides layer (drawn in HTML only; the
  table next to it holds the same numbers);
- ``stamp``: the run's three hashes, seed, draws, run date and disclaimer.

Every ``Text`` carries cites to the engine numbers it shows, so the must-cite
test can check the whole brief, not just the summary sentence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from visionhousing.explain.numbers import (
    COUNT_UNITS,
    SENTENCE_SIG,
    TABLE_SIG,
    Cite,
    Text,
    count,
    num,
)
from visionhousing.explain.payload import DISCLAIMER, explain_number

__all__ = ["COUNTY_ROWS", "brief_blocks"]

COUNTY_ROWS = 10
"""Counties listed in the brief (all counties are in the CSV export)."""

DASH = "—"


def _t(text: str, cites: Sequence[Cite] = ()) -> dict[str, Any]:
    return Text(text, tuple(cites)).to_dict()


def _quote(ref: str, field: str, text: str) -> dict[str, Any]:
    if any(ch.isdigit() for ch in text):
        return _t(text, (Cite(ref, field, text, text),))
    return _t(text)


def _fmt(value: float, unit: str, sig: int) -> str:
    """A table number: whole homes for counts, two figures for percents."""
    if unit in COUNT_UNITS:
        return count(value, sig)
    if unit == "% of baseline":
        return num(value, SENTENCE_SIG)
    return num(value, sig)


def _range_cells(item: Mapping[str, Any], sig: int = TABLE_SIG) -> list[dict[str, Any]]:
    """Cells "low - high" and "middle" for a range item, or the value."""
    ref, unit = item["ref"], item.get("unit", "units")
    pct = "%" if unit == "% of baseline" else ""
    if item.get("p10") is None:
        v = Cite(ref, "value", item["value"], item["shown"]["value"])
        return [_t(DASH), _t(f"{v.shown} (central value)", (v,))]
    lo = Cite(ref, "p10", item["p10"], _fmt(item["p10"], unit, sig))
    mid = Cite(ref, "p50", item["p50"], _fmt(item["p50"], unit, sig))
    hi = Cite(ref, "p90", item["p90"], _fmt(item["p90"], unit, sig))
    return [
        _t(f"{lo.shown}{pct} to {hi.shown}{pct}", (lo, hi)),
        _t(f"{mid.shown}{pct}", (mid,)),
    ]


def _block(kind: str, **kw: Any) -> dict[str, Any]:
    return {"type": kind, **kw}


def _table(columns: Sequence[str], align: str, rows: list[list[dict[str, Any]]], **kw: Any):
    if len(align) != len(columns):
        raise ValueError("one alignment letter per column")
    return _block("table", columns=list(columns), align=list(align), rows=rows, **kw)


def _county_names(result: Any) -> dict[str, str]:
    ctx = getattr(result, "run_context", None)
    b = None if ctx is None else ctx.state.boundaries
    if not b:
        return {}
    out = {}
    for f in b.get("features", ()):
        p = f.get("properties") or {}
        if p.get("level") == "county" and p.get("geoid") and p.get("name"):
            out[str(p["geoid"])] = str(p["name"])
    return out


def _summary(e: Mapping[str, Any]) -> list[dict[str, Any]]:
    head = e["headline"]
    rows = [[n["label"], *_range_cells({**n, "shown": n["shown"]})] for n in head["numbers"]]
    return [
        _block("heading", level=2, text=_t("In one sentence")),
        _block("paragraph", text=head["sentence"]),
        _table(["Measure", "Likely range (P10 to P90)", "Middle estimate (P50)"], "lrr", rows),
        _block(
            "paragraph",
            text=_t(
                "P10 to P90 is the likely range: in nine of every ten simulated runs the "
                "answer is above the low end, and in nine of ten it is below the high end. "
                "P50 is the middle estimate."
            ),
        ),
    ]


def _reforms_table(result: Any, e: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, o in result.reforms.items():
        v = e["verdicts"][k]
        added = {"ref": f"reform_results.{k}.added", "unit": "units", **_rng(o.added)}
        pct = {
            "ref": f"reform_results.{k}.pct_change",
            "unit": "% of baseline",
            **_rng(o.pct_change),
        }
        rows.append(
            [
                _quote(f"reform_results.{k}", "title", o.title),
                _t(v["word"]),
                _range_cells(added)[0],
                _range_cells(pct)[0],
                v["why"],
            ]
        )
    blocks = [
        _block("heading", level=2, text=_t("Each reform on its own")),
        _table(
            ["Reform", "Verdict", "Homes added", "Change from baseline", "Why this verdict"],
            "llrrl",
            rows,
        ),
    ]
    compare = next((s for s in e["slides"] if s["kind"] == "compare"), None)
    if compare is not None:
        blocks.insert(
            1, _block("chart", chart=compare["chart"], title=_t("Homes added, likely range"))
        )
    return blocks


def _rng(r: Any) -> dict[str, float]:
    return {"p10": r.p10, "p50": r.p50, "p90": r.p90}


def _package(e: Mapping[str, Any]) -> list[dict[str, Any]]:
    wf = e["waterfall"]
    if wf is None or len(wf["parts"]) < 2:
        return []
    rows = [[s["label"], *_range_cells(s)] for s in wf["steps"]]
    blocks = [
        _block("heading", level=2, text=_t("All reforms together")),
        _block("paragraph", text=wf["text"]),
        _block(
            "chart",
            chart={"type": "waterfall", "unit": "units", "items": wf["steps"]},
            title=_t("How the reforms add up (middle estimates)"),
        ),
        _table(["Step", "Likely range (P10 to P90)", "Middle estimate (P50)"], "lrr", rows),
    ]
    lim = e["limits"].get("package")
    if lim:
        blocks.append(_block("list", ordered=False, items=lim["items"]))
    return blocks


def _reform_section(result: Any, e: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    o = result.reforms[key]
    f = e["funnels"][key]
    blocks = [
        _block("heading", level=2, text=_quote(f"reform_results.{key}", "title", o.title)),
        _block("paragraph", text=_t(f"Verdict: {e['verdicts'][key]['word']}.")),
        _block("paragraph", text=e["verdicts"][key]["why"]),
        _block("heading", level=3, text=_t("How it adds homes, step by step")),
    ]
    rows = []
    for s in f["steps"]:
        cells = _range_cells(s)
        rows.append([s["label"], s["meaning"], *cells])
    blocks.append(
        _table(["Step", "What it means", "Likely range (P10 to P90)", "Middle (P50)"], "llrr", rows)
    )
    if f["central_only"]:
        blocks.append(
            _block(
                "paragraph",
                text=_t(
                    "The steps above use central values to show how the chain works. "
                    "The last row is the likely range, from every simulated run with all "
                    "uncertain inputs varied together."
                ),
            )
        )
    d = e["drivers"].get(key)
    if d and d["items"]:
        blocks += [
            _block("heading", level=3, text=_t("What moves this estimate most")),
            _block("paragraph", text=d["lead"]),
            _block("list", ordered=True, items=[i["text"] for i in d["items"]]),
        ]
    lim = e["limits"]["reforms"].get(key)
    if lim and lim["items"]:
        blocks += [
            _block("heading", level=3, text=_t("What this cannot tell you")),
            _block("list", ordered=False, items=lim["items"]),
        ]
    return blocks


def _technical_notes(result: Any, e: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = [n for k in result.reforms for n in e["reform_notes"].get(k, ())]
    items = [n for n in items if not n["text"].startswith("Draft inputs")]
    items += list(e["notes"])
    if not items:
        return []
    return [
        _block("heading", level=2, text=_t("Technical notes from the model")),
        _block("list", ordered=False, items=items),
    ]


def _counties(result: Any, e: Mapping[str, Any]) -> list[dict[str, Any]]:
    p = result.package
    if p is None or not p.reforms or not p.counties:
        return []
    names = _county_names(result)
    order = sorted(p.counties, key=lambda c: (-p.counties[c].p50, c))[:COUNTY_ROWS]
    rows = []
    for c in order:
        base = result.baseline_counties.get(c)
        name = names.get(c, c)
        cell = (
            _quote(f"county:{c}", "name", name)
            if c in names
            else _quote(f"package.counties.{c}", "name", p.counties[c].name)
        )
        base_cell = (
            _range_cells({"ref": f"baseline_counties.{c}", **_rng(base)})[1]
            if base is not None and base.is_range
            else _t(DASH)
        )
        rows.append(
            [
                cell,
                base_cell,
                *_range_cells({"ref": f"package.counties.{c}", **_rng(p.counties[c])}),
            ]
        )
    return [
        _block("heading", level=2, text=_t("Where the added homes would be")),
        _block(
            "paragraph",
            text=_t(
                "The counties with the most added homes (middle estimate), all reforms "
                "together. Every county is in the CSV export."
            ),
        ),
        _table(
            ["County", "Baseline (P50)", "Added, likely range (P10 to P90)", "Added (P50)"],
            "lrrr",
            rows,
        ),
    ]


def _limits(e: Mapping[str, Any]) -> list[dict[str, Any]]:
    lim = e["limits"]
    return [
        _block("heading", level=2, text=_t(lim["general"]["title"])),
        _block("list", ordered=False, items=lim["general"]["items"]),
        _block("heading", level=3, text=_t(lim["baseline"]["title"])),
        _block("list", ordered=False, items=lim["baseline"]["items"]),
    ]


def _how_ranges(result: Any) -> list[dict[str, Any]]:
    draws = Cite("draws", "", result.draws, f"{result.draws:,}")
    seed = Cite("seed", "", result.seed, str(result.seed))
    return [
        _block("heading", level=2, text=_t("How the ranges are made")),
        _block(
            "paragraph",
            text=_t(
                f"Every uncertain assumption has a low, central and high value in the "
                f"Assumption Ledger. The model runs {draws.shown} times (seed {seed.shown}), "
                "drawing each assumption from its range, and reports the spread of answers. "
                "Run it again with the same seed and every number comes out the same.",
                (draws, seed),
            ),
        ),
    ]


def _assumptions(e: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for pid, a in e["assumptions"].items():
        ref = a["ref"]
        if "shown" not in a:
            rows.append([_t(pid), _t(DASH), _t(DASH), _t(DASH), _t(DASH), _t(DASH)])
            continue
        vals = [Cite(ref, f, a[f], a["shown"][f]) for f in ("low", "central", "high")]
        rows.append(
            [
                _quote(ref, "label", a["label"]),
                *(_t(v.shown, (v,)) for v in vals),
                _t(a["kind"]),
                _t("checked against a source" if a["status"] == "sourced" else "team judgement"),
            ]
        )
    return [
        _block("heading", level=2, text=_t("Assumptions used")),
        _block(
            "paragraph",
            text=_t(
                "Every number the model needs but cannot read from data. “Team "
                "judgement” entries have deliberately wide ranges and a note on what "
                "evidence would change them; the full ledger is in the tool."
            ),
        ),
        _table(["Assumption", "Low", "Central", "High", "Kind", "Status"], "lrrrll", rows),
    ]


def _sources(e: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = []
    for sid, s in e["sources"].items():
        text = s["citation"] + (f" {s['url']}" if s.get("url") else "")
        cites = [Cite(f"source:{sid}", "citation", s["citation"], s["citation"])]
        if s.get("url"):
            cites.append(Cite(f"source:{sid}", "url", s["url"], s["url"]))
        items.append(_t(text, cites) if any(ch.isdigit() for ch in text) else _t(text))
    return [
        _block("heading", level=2, text=_t("Sources")),
        _block("list", ordered=False, items=items),
    ]


def _workings(result: Any, e: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = ["baseline"]
    refs += [f"reform_results.{k}.added" for k in result.reforms]
    if result.package is not None and result.package.reforms:
        refs.append("package.combined")
    numbers = e.get("numbers") or {}
    items = []
    for ref in refs:
        n = numbers.get(ref) or explain_number(result, ref)
        items.append(
            _t(
                f"{n['name']}: {n['formula']}",
                (
                    Cite(ref, "name", n["name"], n["name"]),
                    Cite(ref, "formula", n["formula"], n["formula"]),
                ),
            )
        )
    return [
        _block("heading", level=2, text=_t("How the headline numbers are worked out")),
        _block("list", ordered=False, items=items),
    ]


def brief_blocks(result: Any, e: Mapping[str, Any], *, run_date: str | None) -> list[dict]:
    """The brief, block by block.

    Args:
        result: The engine result.
        e: Its explanation (``explain.explain``).
        run_date: Date to print in the stamp (``YYYY-MM-DD``), or ``None`` to leave it out.

    Returns:
        The blocks in reading order.
    """
    st = e["stamp"]
    span = e["span"]
    kicker = e["slides"][0]["kicker"]
    title = f"Housing reforms in {st['state_name']}: homes permitted"
    cites = [Cite(**c) for c in kicker["cites"]]
    title_text = _t(f"{title}, {span}", cites) if span else _t(title)
    banner = [DISCLAIMER]
    if st["synthetic"]:
        banner.append(
            "Testland is a made-up state used for testing; its numbers describe no real place."
        )
    blocks: list[dict[str, Any]] = [
        _block("title", text=title_text),
        _block("banner", text=_t(" ".join(banner))),
    ]
    blocks += _summary(e)
    if result.reforms:
        blocks += _reforms_table(result, e)
        blocks += _package(e)
        for k in result.reforms:
            blocks += _reform_section(result, e, k)
        blocks += _counties(result, e)
    blocks += _limits(e)
    blocks += _how_ranges(result)
    blocks += _assumptions(e)
    blocks += _sources(e)
    blocks += _workings(result, e)
    blocks += _technical_notes(result, e)
    blocks.append(
        _block(
            "stamp",
            code=st["code"],
            data=st["data"],
            assumptions=st["assumptions"],
            seed=st["seed"],
            draws=st["draws"],
            state=st["state"],
            reforms=st["reforms"],
            run_date=run_date,
            disclaimer=DISCLAIMER,
        )
    )
    return blocks
