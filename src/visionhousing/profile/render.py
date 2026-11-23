"""Renderers for place profiles: Markdown, a print-ready HTML one-pager, CSV and JSON.

Plain-language summary
----------------------
Every renderer shows the same facts in the same order and only formats what
the profile holds; none computes a number.

- :func:`to_markdown`: headings per section, the one-sentence summary, a table
  of facts with the comparison and a numbered source note, the limits, and the
  source list at the end.
- :func:`to_html`: the same as one self-contained page (inline CSS, system
  fonts, no scripts, no outside requests) laid out for US Letter paper.
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

from visionhousing.profile.model import Fact, Profile

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
    for s in profile.sections:
        out += [f"## {s.title}", "", f"**{s.summary}**", ""]
        if s.status == "ok" and s.facts:
            out += ["| Fact | Value | Compared with | Year | Source |", "|---|---|---|---|---|"]
            for f in s.facts:
                out.append(
                    f"| {_cell(f.label + _where(f, profile))} | {_cell(_value(f))} | "
                    f"{_cell(_compared(f))} | {_cell(f.year)} | [{notes[f.source]}] |"
                )
            out.append("")
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
    for s in profile.sections:
        cls = "section" if s.status == "ok" else "section coming"
        body.append(f'<section class="{cls}" id="{e(s.key)}"><h2>{e(s.title)}</h2>')
        if s.status == "ok":
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
