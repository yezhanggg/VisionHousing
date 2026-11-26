"""Renderers for place profiles: Markdown, a print-ready HTML one-pager, CSV and JSON.

Plain-language summary
----------------------
Every renderer shows the same facts in the same order and only formats what
the profile holds; none computes a number.

- :func:`to_markdown`: a county's ranks among the state's counties, then per
  section the headline, the summary points, the one-sentence summary, a table
  of facts with the comparison and a numbered source note, one small table per
  chart (count, share, margin and the bigger area's share), the limits, and
  the source list at the end.
- :func:`to_html`: the same as one self-contained page (inline CSS, system
  fonts, no scripts, no outside requests) laid out for US Letter paper. Chart
  tables carry a plain bar whose width is the share already in the profile.
- :func:`to_csv`: one row per fact, with every field (value, unit, text,
  comparison, margin, range, source, vintage, year, geography, note).
- :func:`to_json`: the whole profile as JSON.
- :func:`render`: pick one by name (``md``, ``html``, ``csv``, ``json``).
"""

from __future__ import annotations

import csv
import io
import json
from html import escape

from visionhousing.profile.format import fmt
from visionhousing.profile.model import Chart, Fact, Profile, Section

__all__ = ["CSV_COLUMNS", "FORMATS", "render", "to_csv", "to_html", "to_json", "to_markdown"]

FORMATS = ("md", "html", "csv", "json")

CSV_COLUMNS = (
    "state",
    "geoid",
    "place",
    "level",
    "section",
    "fact_id",
    "label",
    "value",
    "unit",
    "text",
    "compare_against",
    "compare_name",
    "compare_value",
    "compare_text",
    "compare_words",
    "moe",
    "low",
    "high",
    "range_text",
    "source",
    "source_name",
    "vintage",
    "year",
    "measured_at",
    "note",
)
"""The CSV header, one column per fact field."""

SYNTHETIC = "Synthetic test state: these numbers are made up for testing and are not real data."
LEVEL_WORDS = {"county": "County", "tract": "Census tract"}


def _footnotes(profile: Profile) -> dict[str, int]:
    return {src: i for i, src in enumerate(profile.sources, start=1)}


def _value(f: Fact) -> str:
    if f.range_text:
        return f"{f.text} (range {f.range_text})"
    return f.text


def _compared(f: Fact) -> str:
    c = f.comparison
    if c is None:
        return ""
    words = f"{c.words}: " if c.words else ""
    return f"{words}{c.text} in {c.name}"


def _where(f: Fact, profile: Profile) -> str:
    return "" if f.measured_at == profile.level else f" (measured by {f.measured_at})"


def _subtitle(profile: Profile) -> str:
    level = LEVEL_WORDS[profile.level]
    if profile.level == "tract":
        return f"{level} {profile.geoid} in {profile.county_name}, {profile.state_name}"
    return f"{level} {profile.geoid}, {profile.state_name}"


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def _share(x: float | None) -> str:
    return "" if x is None else fmt(x, "share")


def _count(x: float | None) -> str:
    return "" if x is None else fmt(x, "persons")


def _compare_shares(chart: Chart) -> dict[str, float | None]:
    if chart.compare is None:
        return {}
    return {i.key or i.label: i.share for i in chart.compare.items}


def _headline(s: Section) -> str:
    if s.headline is None:
        return ""
    words = f" ({s.headline.words})" if s.headline.words else ""
    return f"{s.headline.label}: {s.headline.text}{words}"


def _chart_markdown(chart: Chart, notes: dict[str, int]) -> list[str]:
    refs = "".join(f"[{notes[x]}]" for x in chart.source_ids if x in notes)
    other = _compare_shares(chart)
    head = f"| {_cell(chart.title)} | Count | Share | Margin (90%) |"
    rule = "|---|---|---|---|"
    if chart.compare is not None:
        head += f" {_cell(chart.compare.label)} |"
        rule += "---|"
    out = [f"*Chart: {chart.title}* {refs}", "", head, rule]
    for i in chart.items:
        row = (
            f"| {_cell(i.label)} | {_count(i.value)} | {_share(i.share)} | "
            f"{'' if i.moe is None else '+/- ' + _count(i.moe)} |"
        )
        if chart.compare is not None:
            row += f" {_share(other.get(i.key or i.label))} |"
        out.append(row)
    out += ["", f"<small>{_cell(chart.note)}</small>", ""]
    return out


def to_markdown(profile: Profile) -> str:
    """The profile as Markdown."""
    notes = _footnotes(profile)
    out = [f"# {profile.name}", "", _subtitle(profile) + ".", ""]
    if profile.synthetic:
        out += [f"> **{SYNTHETIC}**", ""]
    out += [
        "Each number names its source [n] (listed at the end), its year and the area it is "
        "compared with. A county is compared with the state; a tract with its county.",
        "",
    ]
    if profile.ranks:
        out += ["## Rank among the state's counties", "",
                "| Measure | Value | Rank | In words |", "|---|---|---|---|"]  # fmt: skip
        for r in profile.ranks:
            out.append(
                f"| {_cell(r.label)} | {_cell(r.text)} | {_cell(r.text_rank)} | {_cell(r.words)} |"
            )
        out.append("")
    for s in profile.sections:
        out += [f"## {s.title}", ""]
        if _headline(s):
            out += [f"### {_headline(s)}", ""]
        if s.summary_points:
            out += [*(f"- {p}" for p in s.summary_points), ""]
        out += [f"**{s.summary}**", ""]
        if s.status == "ok" and s.facts:
            out += ["| Fact | Value | Compared with | Year | Source |", "|---|---|---|---|---|"]
            for f in s.facts:
                out.append(
                    f"| {_cell(f.label + _where(f, profile))} | {_cell(_value(f))} | "
                    f"{_cell(_compared(f))} | {_cell(f.year)} | [{notes[f.source]}] |"
                )
            out.append("")
        for chart in s.charts:
            out += _chart_markdown(chart, notes)
        if s.limits:
            out += [f"*What this cannot tell you:* {s.limits}", ""]
    out += ["## Sources", ""]
    for src, n in notes.items():
        info = profile.sources[src]
        out.append(f"{n}. {info['name']} ({info['publisher']}), {info['vintage']}. `{src}`")
    out.append("")
    if profile.pack_hash:
        out += [f"Data pack `{profile.pack_hash}`.", ""]
    return "\n".join(out)


CSS = """
:root { --ink: #1d232b; --muted: #55606d; --rule: #d5d9de; --soft: #f3f5f7;
  --accent: #0b6e69; --accent-soft: #d7ecea; --warn: #8a4b00; --warn-soft: #fdf1e1; }
* { box-sizing: border-box; }
html { background: #ffffff; }
body { margin: 0 auto; max-width: 52rem; padding: 2rem 1.25rem 3rem; color: var(--ink);
  background: #ffffff; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto,
  "Helvetica Neue", Arial, sans-serif; font-variant-numeric: tabular-nums; }
h1 { font-size: 1.6rem; line-height: 1.2; margin: 0 0 .25rem; }
.sub { color: var(--muted); margin: 0 0 1rem; }
h2 { font-size: 1.1rem; margin: 1.4rem 0 .35rem; padding-top: .35rem;
  border-top: 2px solid var(--accent); }
.summary { font-weight: 600; margin: 0 0 .4rem; }
.status { color: var(--muted); font-style: italic; margin: 0 0 .4rem; }
.banner { background: var(--warn-soft); color: var(--warn); border-left: 4px solid var(--warn);
  padding: .5rem .75rem; font-weight: 600; margin: 0 0 1rem; }
.how { font-size: .85rem; color: var(--muted); margin: 0 0 .5rem; }
table { border-collapse: collapse; width: 100%; margin: .25rem 0 .4rem; font-size: .85rem; }
th, td { border-bottom: 1px solid var(--rule); padding: .25rem .4rem; vertical-align: top;
  text-align: left; }
th { background: var(--soft); font-weight: 600; }
td.r { text-align: right; white-space: nowrap; }
td.c { color: var(--muted); }
sup { font-size: .7em; color: var(--accent); }
.limits { font-size: .8rem; color: var(--muted); margin: .2rem 0 0; }
.coming h2 { border-top-color: var(--rule); color: var(--muted); }
ol.sources { font-size: .8rem; color: var(--muted); padding-left: 1.4rem; }
.headline { font-size: 1.05rem; margin: 0 0 .3rem; }
.headline b { font-size: 1.25rem; }
ul.points { margin: 0 0 .4rem; padding-left: 1.2rem; }
.bar { display: inline-block; height: .6rem; background: var(--accent); vertical-align: middle; }
.bar.o { background: var(--rule); }
caption { text-align: left; font-weight: 600; padding: .2rem 0; }
.stamp { margin-top: 1.5rem; font-size: .75rem; color: var(--muted); word-break: break-all; }
@page { size: letter; margin: 0.5in 0.55in 0.6in; }
@media print {
  body { max-width: none; padding: 0; font-size: 9.5pt; }
  h2 { break-after: avoid; }
  tr, .summary, .banner { break-inside: avoid; }
  thead { display: table-header-group; }
  .banner, th { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
}
""".strip()


def to_html(profile: Profile) -> str:
    """The profile as one self-contained, print-ready HTML page."""
    notes = _footnotes(profile)
    e = escape
    body = [f"<h1>{e(profile.name)}</h1>", f'<p class="sub">{e(_subtitle(profile))}</p>']
    if profile.synthetic:
        body.append(f'<p class="banner">{e(SYNTHETIC)}</p>')
    body.append(
        '<p class="how">Each number names its source (numbered, listed at the end), its year and '
        "the area it is compared with: a county with the state, a tract with its county.</p>"
    )
    if profile.ranks:
        body.append(
            "<h2>Rank among the state's counties</h2><table><thead><tr><th>Measure</th>"
            "<th>Value</th><th>Rank</th><th>In words</th></tr></thead><tbody>"
        )
        for r in profile.ranks:
            body.append(
                f'<tr><td>{e(r.label)}</td><td class="r">{e(r.text)}</td>'
                f'<td class="r">{e(r.text_rank)}</td><td class="c">{e(r.words)}</td></tr>'
            )
        body.append("</tbody></table>")
    for s in profile.sections:
        cls = "section" if s.status == "ok" else "section coming"
        body.append(f'<section class="{cls}" id="{e(s.key)}"><h2>{e(s.title)}</h2>')
        if s.status == "ok":
            if s.headline is not None:
                words = f" ({s.headline.words})" if s.headline.words else ""
                body.append(
                    f'<p class="headline">{e(s.headline.label)}: <b>{e(s.headline.text)}</b>'
                    f"{e(words)}</p>"
                )
            if s.summary_points:
                body.append(
                    '<ul class="points">'
                    + "".join(f"<li>{e(p)}</li>" for p in s.summary_points)
                    + "</ul>"
                )
            body.append(f'<p class="summary">{e(s.summary)}</p>')
        else:
            body.append(f'<p class="status">{e(s.summary)}</p>')
        if s.status == "ok" and s.facts:
            body.append(
                "<table><thead><tr><th>Fact</th><th>Value</th><th>Compared with</th>"
                "<th>Year</th></tr></thead><tbody>"
            )
            for f in s.facts:
                n = notes[f.source]
                title = f"{profile.sources[f.source]['name']}, {f.vintage}"
                body.append(
                    f"<tr><td>{e(f.label + _where(f, profile))}</td>"
                    f'<td class="r">{e(_value(f))} <sup title="{e(title)}">[{n}]</sup></td>'
                    f'<td class="c">{e(_compared(f))}</td><td>{e(f.year)}</td></tr>'
                )
            body.append("</tbody></table>")
        for chart in s.charts:
            body.append(_chart_html(chart, notes))
        if s.limits and s.status == "ok":
            body.append(f'<p class="limits">What this cannot tell you: {e(s.limits)}</p>')
        body.append("</section>")
    body.append('<h2>Sources</h2><ol class="sources">')
    for src in notes:
        info = profile.sources[src]
        body.append(
            f"<li>{e(info['name'])} ({e(info['publisher'])}), {e(info['vintage'])}. "
            f"<code>{e(src)}</code></li>"
        )
    body.append("</ol>")
    if profile.pack_hash:
        body.append(f'<p class="stamp">Data pack {e(profile.pack_hash)}</p>')
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{e(profile.name)} · place profile · VisionHousing</title>\n"
        f"<style>\n{CSS}\n</style>\n</head>\n<body>\n" + "\n".join(body) + "\n</body>\n</html>\n"
    )


def _chart_html(chart: Chart, notes: dict[str, int]) -> str:
    e = escape
    refs = "".join(f"[{notes[x]}]" for x in chart.source_ids if x in notes)
    other = _compare_shares(chart)
    head = "<th></th><th>Count</th><th>Share</th><th>Margin (90%)</th>"
    if chart.compare is not None:
        head += f"<th>{e(chart.compare.label)}</th>"
    rows = []
    for i in chart.items:
        width = 0.0 if i.share is None else max(0.0, min(1.0, i.share)) * 100
        cells = (
            f'<td>{e(i.label)}</td><td class="r">{e(_count(i.value))}</td>'
            f'<td class="r"><span class="bar" style="width:{width:.1f}px"></span> '
            f"{e(_share(i.share))}</td>"
            f'<td class="r">{e("" if i.moe is None else "+/- " + _count(i.moe))}</td>'
        )
        if chart.compare is not None:
            o = other.get(i.key or i.label)
            ow = 0.0 if o is None else max(0.0, min(1.0, o)) * 100
            cells += (
                f'<td class="r"><span class="bar o" style="width:{ow:.1f}px"></span> '
                f"{e(_share(o))}</td>"
            )
        rows.append(f"<tr>{cells}</tr>")
    return (
        f'<table class="chart"><caption>{e(chart.title)} <sup>{e(refs)}</sup></caption>'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        f'<p class="limits">{e(chart.note)}</p>'
    )


def _csv_value(x: object) -> object:
    return "" if x is None else x


def to_csv(profile: Profile) -> str:
    """One row per fact, header :data:`CSV_COLUMNS`."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for s in profile.sections:
        for f in s.facts:
            c = f.comparison
            w.writerow(
                [
                    profile.state,
                    profile.geoid,
                    profile.name,
                    profile.level,
                    s.key,
                    f.id,
                    f.label,
                    _csv_value(f.value),
                    f.unit,
                    f.text,
                    c.against if c else "",
                    c.name if c else "",
                    _csv_value(c.value) if c else "",
                    c.text if c else "",
                    c.words if c else "",
                    _csv_value(f.moe),
                    _csv_value(f.low),
                    _csv_value(f.high),
                    f.range_text,
                    f.source,
                    profile.sources[f.source]["name"],
                    f.vintage,
                    f.year,
                    f.measured_at,
                    f.note,
                ]
            )
    return buf.getvalue()


def to_json(profile: Profile) -> str:
    """The whole profile as JSON."""
    return json.dumps(profile.to_dict(), indent=1, ensure_ascii=False, allow_nan=False) + "\n"


def render(profile: Profile, fmt_name: str) -> str:
    """Render in one of :data:`FORMATS`."""
    renderers = {"md": to_markdown, "html": to_html, "csv": to_csv, "json": to_json}
    if fmt_name not in renderers:
        raise ValueError(f"format must be one of {', '.join(FORMATS)}")
    return renderers[fmt_name](profile)
