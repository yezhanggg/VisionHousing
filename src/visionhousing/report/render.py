"""Draw the brief's blocks as Markdown or as a print-ready HTML page.

Plain-language summary
----------------------
:func:`to_markdown` and :func:`to_html` take the same list of blocks
(``report.brief``) and draw it. The HTML page is self-contained: inline CSS,
system fonts, no scripts, no web fonts, no outside requests. Charts are drawn
as inline SVG with an ``aria-label`` that lists every value, and every chart
sits next to a table holding the same numbers. Print rules set US Letter
pages with margins, keep table rows and charts whole, and repeat table heads.

Both functions are deterministic: the same blocks give the same bytes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

__all__ = ["to_html", "to_markdown"]


# ---------------------------------------------------------------- Markdown


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _md_table(block: Mapping[str, Any]) -> list[str]:
    cols = block["columns"]
    sep = ["---:" if a == "r" else "---" for a in block["align"]]
    lines = [
        "| " + " | ".join(_md_cell(c) for c in cols) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in block["rows"]:
        lines.append("| " + " | ".join(_md_cell(c["text"]) for c in row) + " |")
    return lines


def _stamp_lines(b: Mapping[str, Any]) -> list[str]:
    lines = [
        f"Code version: {b['code']}",
        f"Data manifest: {b['data']}",
        f"Assumption set: {b['assumptions']}",
        f"Seed: {b['seed']} \u00b7 Draws: {b['draws']:,}"
        + (f" \u00b7 Run date: {b['run_date']}" if b.get("run_date") else ""),
        f"Command: visionhousing report --state {b['state']}"
        + (f" --reforms {','.join(b['reforms'])}" if b["reforms"] else " --reforms none"),
        b["disclaimer"],
    ]
    return lines


def to_markdown(blocks: Sequence[Mapping[str, Any]]) -> str:
    """The brief as Markdown (charts are left out; their tables are kept).

    Args:
        blocks: The brief's blocks.

    Returns:
        Markdown text ending in one newline.
    """
    out: list[str] = []
    for b in blocks:
        kind = b["type"]
        if kind == "title":
            out += [f"# {b['text']['text']}", ""]
        elif kind == "banner":
            out += [f"> **{b['text']['text']}**", ""]
        elif kind == "heading":
            out += ["#" * b["level"] + " " + b["text"]["text"], ""]
        elif kind == "paragraph":
            out += [b["text"]["text"], ""]
        elif kind == "list":
            for i, item in enumerate(b["items"], start=1):
                bullet = f"{i}." if b["ordered"] else "-"
                out.append(f"{bullet} {item['text']}")
            out.append("")
        elif kind == "table":
            out += [*_md_table(b), ""]
        elif kind == "chart":
            continue
        elif kind == "stamp":
            out += ["---", "", "<!-- stamp -->"]
            out += [f"{line}  " for line in _stamp_lines(b)[:-1]]
            out += [f"**{_stamp_lines(b)[-1]}**", "<!-- /stamp -->", ""]
        else:
            raise ValueError(f"unknown block type '{kind}'")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- HTML

CSS = """
:root { --ink: #1d232b; --muted: #55606d; --rule: #d5d9de; --soft: #f3f5f7;
  --accent: #0b6e69; --accent-soft: #d7ecea; --warn: #8a4b00; --warn-soft: #fdf1e1; }
* { box-sizing: border-box; }
html { background: #ffffff; }
body { margin: 0 auto; max-width: 52rem; padding: 2rem 1.25rem 3rem; color: var(--ink);
  background: #ffffff; font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto,
  "Helvetica Neue", Arial, sans-serif; font-variant-numeric: tabular-nums; }
h1 { font-size: 1.7rem; line-height: 1.2; margin: 0 0 .75rem; }
h2 { font-size: 1.25rem; margin: 2rem 0 .5rem; padding-top: .5rem;
  border-top: 2px solid var(--accent); }
h3 { font-size: 1.05rem; margin: 1.25rem 0 .4rem; }
p, li { max-width: 46rem; }
.banner { background: var(--warn-soft); color: var(--warn); border-left: 4px solid var(--warn);
  padding: .55rem .8rem; font-weight: 600; margin: 0 0 1rem; }
.lead { font-size: 1.15rem; line-height: 1.45; background: var(--accent-soft);
  padding: .8rem 1rem; border-radius: 6px; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; font-size: .9rem; }
th, td { border-bottom: 1px solid var(--rule); padding: .35rem .5rem; vertical-align: top;
  text-align: left; }
th { background: var(--soft); font-weight: 600; }
td.r, th.r { text-align: right; white-space: nowrap; }
figure { margin: .75rem 0 1rem; }
figcaption { font-size: .85rem; color: var(--muted); margin-bottom: .25rem; }
svg { width: 100%; height: auto; display: block; }
svg text { font: 12px system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  fill: var(--ink); }
.stamp { margin-top: 2.5rem; padding-top: .75rem; border-top: 1px solid var(--rule);
  font-size: .8rem; color: var(--muted); word-break: break-all; }
.stamp strong { color: var(--ink); word-break: normal; }
@page { size: letter; margin: 0.6in 0.6in 0.7in; }
@media print {
  body { max-width: none; padding: 0; font-size: 10.5pt; }
  h2 { break-after: avoid; }
  h3 { break-after: avoid; }
  tr, figure, .lead, .banner { break-inside: avoid; }
  thead { display: table-header-group; }
  .banner, .lead, th { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
}
""".strip()


def _f(x: float) -> str:
    """A coordinate with one decimal, for stable SVG output."""
    return f"{x:.1f}"


def _label(item: Mapping[str, Any]) -> str:
    lab = item.get("label")
    return lab["text"] if isinstance(lab, Mapping) else str(lab)


def _bars_svg(chart: Mapping[str, Any]) -> str:
    """Interval bars (P10 to P90 with a P50 tick), one row per item."""
    items = chart["items"]
    hi = max([float(i["p90"]) for i in items] + [0.0]) or 1.0
    left, width, row = 230.0, 380.0, 34.0
    h = row * len(items) + 24
    parts = [
        f'<svg viewBox="0 0 680 {_f(h)}" role="img" aria-label="'
        + escape(
            "; ".join(
                f"{_label(i)}: {i['shown']['p10']} to {i['shown']['p90']}, "
                f"middle {i['shown']['p50']}"
                for i in items
            )
        )
        + '">'
    ]
    for n, i in enumerate(items):
        y = 12 + n * row
        x0 = left + width * float(i["p10"]) / hi
        x1 = left + width * float(i["p90"]) / hi
        xm = left + width * float(i["p50"]) / hi
        parts.append(f'<text x="0" y="{_f(y + 15)}">{escape(_label(i))}</text>')
        parts.append(
            f'<rect x="{_f(x0)}" y="{_f(y + 5)}" width="{_f(max(x1 - x0, 1.0))}" height="14" '
            'rx="3" fill="#9fd0cc"/>'
        )
        parts.append(
            f'<rect x="{_f(xm - 1.5)}" y="{_f(y + 1)}" width="3" height="22" fill="#0b6e69"/>'
        )
        parts.append(f'<text x="{_f(x1 + 6)}" y="{_f(y + 16)}">{escape(i["shown"]["p50"])}</text>')
    parts.append(
        f'<line x1="{_f(left)}" y1="6" x2="{_f(left)}" y2="{_f(h - 6)}" stroke="#55606d"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _waterfall_svg(chart: Mapping[str, Any]) -> str:
    """Waterfall of middle estimates: start, minus, plus, total."""
    items = chart["items"]
    running, spans = 0.0, []
    for i in items:
        v = float(i["p50"])
        if i["kind"] == "start":
            spans.append((0.0, v))
            running = v
        elif i["kind"] == "minus":
            spans.append((running - v, running))
            running -= v
        elif i["kind"] == "plus":
            spans.append((running, running + v))
            running += v
        else:
            spans.append((0.0, v))
    top = max([b for _, b in spans] + [0.0]) or 1.0
    left, width, row = 280.0, 330.0, 34.0
    h = row * len(items) + 24
    label = "; ".join(f"{_label(i)}: {i['shown']['p50']}" for i in items)
    parts = [f'<svg viewBox="0 0 680 {_f(h)}" role="img" aria-label="{escape(label)}">']
    colours = {"start": "#0b6e69", "minus": "#b4561f", "plus": "#3f8f4f", "total": "#1d232b"}
    for n, (i, (a, b)) in enumerate(zip(items, spans, strict=True)):
        y = 12 + n * row
        x0, x1 = left + width * a / top, left + width * b / top
        parts.append(f'<text x="0" y="{_f(y + 15)}">{escape(_label(i))}</text>')
        parts.append(
            f'<rect x="{_f(x0)}" y="{_f(y + 4)}" width="{_f(max(x1 - x0, 1.0))}" height="16" '
            f'fill="{colours[i["kind"]]}"/>'
        )
        parts.append(
            f'<text x="{_f(max(x1, x0) + 6)}" y="{_f(y + 16)}">{escape(i["shown"]["p50"])}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _chart(block: Mapping[str, Any]) -> str:
    chart = block["chart"]
    kind = chart["type"]
    if kind == "interval":
        svg = _bars_svg(chart)
    elif kind == "waterfall":
        svg = _waterfall_svg(chart)
    else:
        raise ValueError(f"the brief draws interval and waterfall charts, not '{kind}'")
    cap = escape(block["title"]["text"]) if block.get("title") else ""
    return f"<figure><figcaption>{cap}</figcaption>{svg}</figure>"


def _html_table(block: Mapping[str, Any]) -> str:
    align = block["align"]
    head = "".join(
        f'<th scope="col"{' class="r"' if a == "r" else ""}>{escape(c)}</th>'
        for c, a in zip(block["columns"], align, strict=True)
    )
    rows = []
    for row in block["rows"]:
        cells = "".join(
            f"<td{' class="r"' if a == 'r' else ''}>{escape(c['text'])}</td>"
            for c, a in zip(row, align, strict=True)
        )
        rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def to_html(blocks: Sequence[Mapping[str, Any]]) -> str:
    """The brief as one self-contained, print-ready HTML page.

    Args:
        blocks: The brief's blocks.

    Returns:
        HTML text ending in one newline.
    """
    title = next((b["text"]["text"] for b in blocks if b["type"] == "title"), "Brief")
    body: list[str] = []
    first_paragraph_after_summary = False
    for b in blocks:
        kind = b["type"]
        if kind == "title":
            body.append(f"<h1>{escape(b['text']['text'])}</h1>")
        elif kind == "banner":
            body.append(f'<p class="banner">{escape(b["text"]["text"])}</p>')
        elif kind == "heading":
            level = int(b["level"])
            body.append(f"<h{level}>{escape(b['text']['text'])}</h{level}>")
            first_paragraph_after_summary = b["text"]["text"] == "In one sentence"
        elif kind == "paragraph":
            cls = ' class="lead"' if first_paragraph_after_summary else ""
            body.append(f"<p{cls}>{escape(b['text']['text'])}</p>")
            first_paragraph_after_summary = False
        elif kind == "list":
            tag = "ol" if b["ordered"] else "ul"
            items = "".join(f"<li>{escape(i['text'])}</li>" for i in b["items"])
            body.append(f"<{tag}>{items}</{tag}>")
        elif kind == "table":
            body.append(_html_table(b))
        elif kind == "chart":
            body.append(_chart(b))
        elif kind == "stamp":
            lines = _stamp_lines(b)
            inner = "<br>".join(escape(x) for x in lines[:-1])
            body.append(
                f'<footer class="stamp"><p>{inner}</p><p><strong>{escape(lines[-1])}'
                "</strong></p></footer>"
            )
        else:
            raise ValueError(f"unknown block type '{kind}'")
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n<style>\n{CSS}\n</style>\n</head>\n<body>\n"
        "<main>\n" + "\n".join(body) + "\n</main>\n</body>\n</html>\n"
    )
