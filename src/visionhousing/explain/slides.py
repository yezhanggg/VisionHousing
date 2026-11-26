"""The slide deck spec: an ordered list of slides as plain data.

Plain-language summary
----------------------
The web route ``#/slides`` and the printed slide PDF draw the same list. Each
slide has:

- ``kind``: ``summary``, ``compare``, ``reform``, ``drivers``, ``package`` or ``limits``;
- ``title`` and ``kicker``: short texts;
- ``big``: the big-number range (P10 to P90, with the middle estimate);
- ``chart``: what to draw (``interval`` bars, ``funnel``, ``tornado``,
  ``waterfall``), or ``None`` on the closing slide, which is text only;
- ``body``: a few sentences;
- ``sources``: the sources behind the slide's numbers, with citations;
- ``stamp``: the three hashes, the seed and the draws.

Order: summary, the reforms compared (when there are two or more), then each
reform (its funnel, then its drivers), the package waterfall (two or more
reforms), and the closing slide with what the estimates cannot tell you.

Every text is a ``Text`` with cites, and every chart item names the record it
draws (``ref``), so the same must-cite test covers the slides.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from visionhousing.explain import wording as w
from visionhousing.explain.numbers import Cite, Text, num
from visionhousing.explain.payload import DISCLAIMER, span

__all__ = ["slides"]


def _big(item: Mapping[str, Any], label: str) -> dict[str, Any]:
    """A big-number range from a range item (``ref``, ``p10``...)."""
    ref = item["ref"]
    lo = Cite(ref, "p10", item["p10"], num(item["p10"]))
    mid = Cite(ref, "p50", item["p50"], num(item["p50"]))
    hi = Cite(ref, "p90", item["p90"], num(item["p90"]))
    pct = item.get("unit") == "% of baseline"
    tail = "%" if pct else ""
    return {
        "ref": ref,
        "label": Text(label).to_dict(),
        "unit": item.get("unit", "units"),
        "p10": item["p10"],
        "p50": item["p50"],
        "p90": item["p90"],
        "text": Text(f"{lo.shown}{tail}\u2013{hi.shown}{tail}", (lo, hi)).to_dict(),
        "middle": Text(f"Middle estimate {mid.shown}{tail}", (mid,)).to_dict(),
    }


def _sources(refs: Iterable[str], explanation: Mapping[str, Any]) -> list[dict[str, Any]]:
    numbers = explanation.get("numbers", {})
    ids: set[str] = set()
    for ref in refs:
        if ref in numbers:
            ids.update(numbers[ref]["source_ids"])
    catalogue = explanation["sources"]
    return [catalogue.get(s, {"id": s, "citation": s, "url": None}) for s in sorted(ids)]


def _chart_refs(chart: Mapping[str, Any] | None) -> list[str]:
    return [] if chart is None else [i["ref"] for i in chart["items"]]


def _slide(kind: str, title: str | dict, kicker: dict, big, chart, body, explanation, stamp):
    refs = [*_chart_refs(chart), *([big["ref"]] if big else [])]
    return {
        "kind": kind,
        "title": title if isinstance(title, dict) else Text(title).to_dict(),
        "kicker": kicker,
        "big": big,
        "chart": chart,
        "body": list(body),
        "sources": _sources(refs, explanation),
        "stamp": stamp,
    }


def slides(result: Any, explanation: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build the deck spec from a result and its explanation.

    Args:
        result: The engine result.
        explanation: Its explanation (``explain.explain`` output so far).

    Returns:
        The slides in order, each numbered from 1.
    """
    st = explanation["stamp"]
    stamp = {k: st[k] for k in ("code", "data", "assumptions", "seed", "draws")}
    state = st["state_name"]
    sp = span(result)
    kicker = Text(f"{state} · {sp[0]}", (sp[1],)).to_dict() if sp else Text(state).to_dict()
    head = explanation["headline"]
    numbers = {n["key"]: n for n in head["numbers"]}
    out: list[dict[str, Any]] = []
    keys = list(result.reforms)

    # 1. Summary
    if "added" in numbers:
        big = _big(numbers["added"], "More homes permitted with the reforms")
    else:
        big = _big(numbers["baseline"], "Homes permitted under today's rules")
    items = [numbers["baseline"]]
    if result.total is not None and keys:
        t = result.total
        items.append(
            {
                "ref": "total",
                "label": Text("With the reforms").to_dict(),
                "unit": t.unit,
                "p10": t.p10,
                "p50": t.p50,
                "p90": t.p90,
                "shown": {q: num(getattr(t, q)) for q in ("p10", "p50", "p90")},
            }
        )
    chart = {"type": "interval", "unit": "units", "items": items}
    out.append(
        _slide(
            "summary",
            f"How many more homes would {state} permit?",
            kicker,
            big,
            chart,
            [head["sentence"], Text(DISCLAIMER).to_dict()],
            explanation,
            stamp,
        )
    )

    # 2. Compare the reforms
    verdicts = explanation["verdicts"]
    if len(keys) > 1:
        parts = explanation["waterfall"]["parts"]
        chart = {"type": "interval", "unit": "units", "items": parts}
        body = [
            Text(
                f"{verdicts[k]['word']}: {verdicts[k]['title']}", _title_cites(result, k)
            ).to_dict()
            for k in keys
        ]
        out.append(
            _slide(
                "compare",
                "Each reform on its own",
                kicker,
                _big(numbers["added"], "All reforms together"),
                chart,
                body,
                explanation,
                stamp,
            )
        )

    # 3. Each reform: funnel, then drivers
    for k in keys:
        f = explanation["funnels"][k]
        v = verdicts[k]
        added = {
            "ref": f"reform_results.{k}.added",
            "unit": "units",
            "p10": result.reforms[k].added.p10,
            "p50": result.reforms[k].added.p50,
            "p90": result.reforms[k].added.p90,
        }
        steps = [s for s in f["steps"] if s["unit"] in ("units", "lots")]
        chart = {"type": "funnel", "items": steps}
        title = Text(f"{v['title']}: {v['word']}", _title_cites(result, k)).to_dict()
        body = [v["why"], *(s["meaning"] for s in steps[:1])]
        out.append(
            _slide(
                "reform",
                title,
                kicker,
                _big(added, w.added_label(k, "More homes permitted, this reform alone")),
                chart,
                body,
                explanation,
                stamp,
            )
        )
        d = explanation["drivers"].get(k)
        if d and d["items"]:
            chart = {
                "type": "tornado",
                "unit": "units",
                "center": d["central_total"],
                "items": [
                    {
                        "ref": i["ref"],
                        "label": _driver_label(i),
                        "total_low": i["total_low"],
                        "total_high": i["total_high"],
                        "shown": {
                            "total_low": num(i["total_low"]),
                            "total_high": num(i["total_high"]),
                        },
                    }
                    for i in d["items"]
                ],
            }
            title = Text(f"What moves the estimate: {v['title']}", _title_cites(result, k))
            out.append(
                _slide(
                    "drivers",
                    title.to_dict(),
                    kicker,
                    _big(added, w.added_label(k, "More homes permitted, this reform alone")),
                    chart,
                    [d["lead"], *(i["text"] for i in d["items"][:3])],
                    explanation,
                    stamp,
                )
            )

    # 4. Package waterfall
    wf = explanation["waterfall"]
    if wf is not None and len(keys) > 1:
        out.append(
            _slide(
                "package",
                "How the reforms add up together",
                kicker,
                _big(numbers["added"], "All reforms together"),
                {"type": "waterfall", "unit": "units", "items": wf["steps"]},
                [wf["text"]],
                explanation,
                stamp,
            )
        )

    # 5. Closing: limits
    lim = explanation["limits"]
    body = [*lim["general"]["items"], Text(DISCLAIMER).to_dict()]
    out.append(
        _slide(
            "limits",
            lim["general"]["title"],
            kicker,
            big,
            None,
            body,
            explanation,
            stamp,
        )
    )
    for n, s in enumerate(out, start=1):
        s["n"] = n
    return out


def _title_cites(result: Any, key: str) -> tuple[Cite, ...]:
    title = result.reforms[key].title
    if any(ch.isdigit() for ch in title):
        return (Cite(f"reform_results.{key}", "title", title, title),)
    return ()


def _driver_label(item: Mapping[str, Any]) -> dict[str, Any]:
    label = item["label"]
    if any(ch.isdigit() for ch in label):
        return Text(label, (Cite(item["ref"], "label", label, label),)).to_dict()
    return Text(label).to_dict()
