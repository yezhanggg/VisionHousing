"""The two-page hearing brief: one state and one reform (or the package), from one run.

Plain-language summary
----------------------
A legislative or agency analyst preparing a hearing memo needs one short,
printable page pair that answers the question and shows how far to trust it.
This module writes that brief from the **same run records the screens read**:
the run reply (``POST /run``: the engine's result plus its explanation), the
state's baseline card (``api.baseline_view``) and, when it exists, the
"what this law changes" payload (``api.law_changes``). Nothing in it is worked
out here: every number is the engine's own text (``shown``) or a card row.

Page one
  1. **The question**: how many more homes the state would likely authorize by
     building permit over the five projected years with this reform.
  2. **The answer**: the engine's sentence, the likely ranges (baseline, homes
     added, change), and the **scope** of the headline: which work types it
     counts and the Census-comparable figure beside it (outcome accounting).
  3. **The baseline**: the baseline card in short (years, permit columns,
     release, cutoff, projected years and the 2026 gap, the 2026 nowcast, any
     window sensitivity, the state reconciliation).

Page two
  4. **What the law changes** (only when that payload exists for the reform).
  5. **Main drivers**: the top inputs moved one at a time.
  6. **What is unknown**: where the uncertainty comes from and what is not
     observed (statuses of the outcome accounting, unresolved local rules).
  7. **Limits**: what the model cannot tell a policymaker.
  8. **Reproduce**: the run id, the three hashes, seed, draws, the command that
     writes this brief again and the link that reopens the view.

Formats: ``html`` (one self-contained page, no scripts or outside requests,
print rules for two US Letter pages) and ``md``.

Command::

    visionhousing hearing-brief --state PA --reforms adu [--out FILE] [--format html|md]
    visionhousing hearing-brief --state PA --reforms adu --static web/public/data

The second form reads a saved static export instead of running the engine.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from html import escape
from pathlib import Path
from typing import Any

__all__ = [
    "API_CSS_HREF",
    "CSS",
    "CSS_FILE",
    "FORMATS",
    "STATIC_CSS_HREF",
    "add_parser",
    "command",
    "hearing_doc",
    "hearing_from_result",
    "hearing_reply",
    "law_payload",
    "refresh_static",
    "render",
    "run_key",
    "to_html",
    "to_markdown",
]

FORMATS = ("html", "md")
PACKAGE = ("adu", "missing_middle", "tod", "parking", "fees")
"""The five benchmark reforms (the run key ``package``)."""

MAX_ITEMS = 3
"""Items kept per list (drivers, unknowns, limits per group), so the brief stays two pages."""

DASH = "\u2013"

CSS_FILE = "hearing.css"
"""The style rules as a file: static ``data/hearing.css``; live ``GET /export/hearing.css``."""
API_CSS_HREF = "hearing.css"
"""From ``/api/v1/export/hearing?...`` this resolves to ``/api/v1/export/hearing.css``."""
STATIC_CSS_HREF = "../../hearing.css"
"""From ``data/ST/exports/<key>.hearing.html`` this resolves to ``data/hearing.css``."""


def run_key(reforms: Sequence[str], overrides: Mapping[str, Any] | None = None) -> str:
    """``baseline``, one reform id, ``package``, ``custom`` or a ``+`` joined mix."""
    from visionhousing.api.answers import run_key as key

    return key(list(reforms), dict(overrides or {}), list(PACKAGE))


def _txt(v: Any) -> str:
    if isinstance(v, Mapping):
        return str(v.get("text", ""))
    return "" if v is None else str(v)


def _shown_range(n: Mapping[str, Any] | None) -> tuple[str, str]:
    """``("P10 to P90", "P50")`` from a number's shown text, or blanks."""
    sh = (n or {}).get("shown") or {}
    if sh.get("p10") is not None and sh.get("p90") is not None:
        return f"{sh['p10']} to {sh['p90']}", str(sh.get("p50", ""))
    if sh.get("value") is not None:
        return str(sh["value"]), ""
    return "", ""


def _scenario_title(run: Mapping[str, Any], key: str) -> str:
    results = run.get("reform_results") or {}
    if key == "baseline":
        return "Today's rules (the baseline only)"
    if key == "package":
        return "All five benchmark reforms together"
    titles = [str((results.get(k) or {}).get("title") or k) for k in run.get("reforms") or []]
    return " + ".join(titles)


def _scope_lines(run: Mapping[str, Any]) -> list[str]:
    """What each reform's headline counts, with the Census-comparable figure beside it."""
    e = run.get("explanation") or {}
    numbers = e.get("numbers") or {}
    out: list[str] = []
    for k in run.get("reforms") or []:
        r = (run.get("reform_results") or {}).get(k) or {}
        title = str(r.get("title") or k)
        acc = r.get("accounting")
        if not acc:
            out.append(
                f"{title}: this run was saved before the outcome-accounting records, so the "
                "headline's scope is not stated here; the local app shows it."
            )
            continue
        head = acc.get("headline") or {}
        scope = str(head.get("scope") or "")
        line = f"{title}: the headline counts {scope}." if scope else f"{title}:"
        if head.get("is_comparable") is False:
            rng, mid = _shown_range(
                numbers.get(f"reform_results.{k}.accounting.records.comparable")
            )
            if rng:
                line += (
                    f" Census-comparable (Building Permits Survey definition): {rng}, middle {mid}."
                )
        elif head.get("is_comparable") is True:
            line += " It is the Census-comparable figure."
        out.append(line)
    return out


def _law_block(law: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not law:
        return None
    sw = law.get("statewide") or {}
    den = law.get("denominator") or {}
    rows = []
    for c in law.get("classes") or []:
        rec = (sw.get("classes") or {}).get(c.get("id")) or {}
        share = (rec.get("share") or {}).get("shown")
        count = (rec.get("count") or {}).get("shown")
        if share is None and count is None:
            continue
        rows.append([str(c.get("word", c.get("id"))), str(share or ""), str(count or "")])
    prov = law.get("provision") or {}
    lines = []
    if prov.get("citation"):
        when = prov.get("effective_text") or prov.get("effective_date") or ""
        lines.append(
            f"Provision: {prov['citation']}"
            + (f" ({prov.get('status')})" if prov.get("status") else "")
            + (f", effective {when}" if when else "")
            + "."
        )
    cap = sw.get("capacity")
    if isinstance(cap, Mapping) and cap.get("text"):
        lines.append(f"Legal additional capacity statewide: {cap['text']}.")
    elif sw.get("capacity_note"):
        lines.append(str(sw["capacity_note"]))
    unres = (sw.get("unresolved_result_share") or {}).get("shown")
    if unres:
        lines.append(
            f"Share of the added homes that sits in places with unresolved local rules: {unres}."
        )
    return {
        "denominator": str(den.get("label", "")),
        "rows": rows,
        "lines": lines,
        "unresolved_share": (sw.get("unresolved_share") or {}).get("shown"),
    }


def _drivers(run: Mapping[str, Any], key: str) -> list[str]:
    e = run.get("explanation") or {}
    reforms = list(run.get("reforms") or [])
    if len(reforms) == 1:
        d = (e.get("drivers") or {}).get(reforms[0]) or {}
        items = [_txt(x.get("text")) for x in (d.get("items") or [])[:MAX_ITEMS]]
        if items:
            return items
        nar = (e.get("narrowing") or {}).get(reforms[0]) or {}
        return [_txt(x.get("text")) for x in (nar.get("items") or [])[:MAX_ITEMS]]
    w = e.get("waterfall") or {}
    out = []
    for st in w.get("steps") or []:
        rng, mid = _shown_range(st)
        shown = rng or mid
        out.append(f"{_txt(st.get('label'))}: {shown}" if shown else _txt(st.get("label")))
    return out


def _unknowns(run: Mapping[str, Any], law: Mapping[str, Any] | None) -> list[str]:
    e = run.get("explanation") or {}
    out: list[str] = []
    for k in run.get("reforms") or []:
        nar = (e.get("narrowing") or {}).get(k) or {}
        n = 1 if len(run.get("reforms") or []) > 1 else MAX_ITEMS
        out += [_txt(x.get("text")) for x in (nar.get("items") or [])[:n]]
        acc = ((run.get("reform_results") or {}).get(k) or {}).get("accounting") or {}
        for word in (acc.get("status") or {}).values():
            if word and str(word) not in out:
                out.append(str(word))
    lb = _law_block(law)
    if lb and lb.get("unresolved_share"):
        out.append(
            f"Local rules not yet read (unresolved): {lb['unresolved_share']} of "
            f"{lb['denominator']}; the engine uses its statewide fallback range there."
        )
    return [x for x in out if x]


def _limits(run: Mapping[str, Any]) -> list[str]:
    lim = (run.get("explanation") or {}).get("limits") or {}
    reforms = list(run.get("reforms") or [])
    per = MAX_ITEMS if len(reforms) <= 1 else 1
    out: list[str] = []
    for k in reforms:
        out += [_txt(t) for t in ((lim.get("reforms") or {}).get(k) or {}).get("items", [])[:per]]
    out += [_txt(t) for t in (lim.get("baseline") or {}).get("items", [])[:1]]
    out += [_txt(t) for t in (lim.get("general") or {}).get("items", [])[:2]]
    return [x for x in out if x]


def _query(run: Mapping[str, Any]) -> str:
    from visionhousing.api.answers import scenario_query

    sc = {
        "state": run.get("state"),
        "reforms": list(run.get("reforms") or []),
        "overrides": dict(run.get("overrides") or {}),
    }
    return scenario_query(sc)


def hearing_doc(
    run: Mapping[str, Any],
    card: Mapping[str, Any] | None = None,
    law: Mapping[str, Any] | None = None,
    *,
    run_date: str | None = None,
) -> dict[str, Any]:
    """The hearing brief as data (both formats draw this).

    Args:
        run: A run reply (``POST /run``: the result's ``to_dict()`` plus ``explanation``).
        card: The state's baseline card with its ``view`` (``api.baseline_view.view_for``).
        law: The "what this law changes" payload for the reform, or ``None`` (left out).
        run_date: Date to print, or ``None``.
    """
    from visionhousing.api.answers import run_id

    e = run.get("explanation") or {}
    if not e:
        raise ValueError("the run has no explanation; the hearing brief needs it")
    stamp = e.get("stamp") or {}
    state = str(run.get("state") or stamp.get("state") or "")
    name = str(stamp.get("state_name") or state)
    reforms = list(run.get("reforms") or [])
    key = run_key(reforms, run.get("overrides"))
    title = _scenario_title(run, key)
    span = str(e.get("span") or "the five projected years")
    if key == "baseline":
        question = (
            f"How many homes would {name} likely authorize by building permit over {span} "
            "under today's rules?"
        )
    else:
        question = (
            f"Over {span}, how many more homes would {name} likely authorize by building "
            f"permit with {title[0].lower() + title[1:]}, compared with today's rules?"
        )
    head = e.get("headline") or {}
    # The transit status (api.status_view): a reform whose statewide range is hidden shows
    # its status text and measured contribution, never a range.
    from visionhousing.api.status_view import status_views

    views = run.get("status_views") or status_views(run)
    alone = views.get(reforms[0]) if len(reforms) == 1 else None
    hidden = alone is not None and not alone["shown"]
    rows = []
    for n in head.get("numbers") or []:
        rng, mid = _shown_range(n)
        if hidden and n.get("key") == "added":
            rng, mid = alone["hidden_word"], alone["measured"]["text"]
        elif hidden and n.get("key") == "pct_change":
            rng, mid = alone["not_shown"], alone["not_shown"]
        elif alone is not None and alone["partial"] and n.get("key") == "added":
            rng = f"{rng} ({alone['label']})"
        rows.append([_txt(n.get("label")), rng, mid])
    status_lines = [
        v["package_note"] for v in views.values() if v.get("package_note") and len(reforms) > 1
    ] + [
        f"{v['title']}: {v['label']}."
        for v in views.values()
        if v["shown"] and v["partial"] and len(reforms) > 1
    ]
    view = (card or {}).get("view") if card else None
    baseline_rows = [[r["label"], r["text"]] for r in (view or {}).get("rows", [])]
    hashes = run.get("hashes") or {}
    reforms_arg = ",".join(reforms) or "none"
    cmd = (
        f"visionhousing hearing-brief --state {state} --reforms {reforms_arg} "
        f"--seed {run.get('seed')} --draws {run.get('draws')}"
    )
    if run.get("overrides"):
        cmd += " --overrides " + ",".join(
            f"{k}~{v}" for k, v in sorted(dict(run["overrides"]).items())
        )
    return {
        "title": f"Hearing brief: {title}, {name}",
        "banner": str(stamp.get("disclaimer") or ""),
        "state": state,
        "key": key,
        "question": question,
        "sentence": alone["text"] if hidden else _txt(head.get("sentence")),
        "frequency": "" if hidden else _txt(head.get("frequency")),
        "answer_rows": rows,
        "scope": _scope_lines(run) + status_lines,
        "baseline_rows": baseline_rows,
        "baseline_note": ""
        if view
        else "No baseline card for this state (the synthetic test state, or a card not built).",
        "law": _law_block(law),
        "drivers": [] if hidden else _drivers(run, key),
        "unknowns": _unknowns(run, law),
        "limits": _limits(run),
        "stamp": {
            "run_id": run_id(run, key),
            "code": hashes.get("code", ""),
            "data": hashes.get("data", ""),
            "assumptions": hashes.get("assumptions", ""),
            "seed": run.get("seed"),
            "draws": run.get("draws"),
            "cycle": run.get("cycle"),
            "run_date": run_date,
            "command": cmd,
            "link": f"#/state?{_query(run)}",
            "card_source": (card or {}).get("source", ""),
            "disclaimer": str(stamp.get("disclaimer") or ""),
        },
    }


# ------------------------------------------------------------------ drawing

CSS = """
:root { --ink: #1d232b; --muted: #55606d; --rule: #d5d9de; --soft: #f3f5f7;
  --accent: #0b6e69; --accent-soft: #d7ecea; --warn: #8a4b00; --warn-soft: #fdf1e1; }
* { box-sizing: border-box; }
html { background: #ffffff; }
body { margin: 0 auto; max-width: 50rem; padding: 1.5rem 1.25rem 2rem; color: var(--ink);
  background: #ffffff; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto,
  "Helvetica Neue", Arial, sans-serif; font-variant-numeric: tabular-nums; }
h1 { font-size: 1.35rem; line-height: 1.2; margin: 0 0 .5rem; }
h2 { font-size: 1rem; margin: 1rem 0 .3rem; padding-top: .3rem;
  border-top: 2px solid var(--accent); }
p, li { margin: .2rem 0; }
ul { margin: .2rem 0 .4rem; padding-left: 1.2rem; }
.banner { background: var(--warn-soft); color: var(--warn); border-left: 4px solid var(--warn);
  padding: .35rem .6rem; font-weight: 600; margin: 0 0 .6rem; }
.question { font-weight: 600; }
.lead { background: var(--accent-soft); padding: .5rem .7rem; border-radius: 6px; }
table { border-collapse: collapse; width: 100%; margin: .3rem 0 .5rem; font-size: .9em; }
th, td { border-bottom: 1px solid var(--rule); padding: .25rem .4rem; vertical-align: top;
  text-align: left; }
th { background: var(--soft); font-weight: 600; }
td.r, th.r { text-align: right; white-space: nowrap; }
.small { font-size: .85em; color: var(--muted); }
.stamp { margin-top: 1rem; padding-top: .5rem; border-top: 1px solid var(--rule);
  font-size: .78em; color: var(--muted); word-break: break-all; }
.stamp code { color: var(--ink); }
@page { size: letter; margin: 0.5in 0.55in 0.55in; }
@media print {
  body { max-width: none; padding: 0; font-size: 9.5pt; }
  .page-two { break-before: page; }
  h2 { break-after: avoid; }
  tr, .lead, .banner { break-inside: avoid; }
  .banner, .lead, th { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
}
""".strip()


def _table(head: Sequence[str], rows: Sequence[Sequence[str]], align: str) -> str:
    th = "".join(
        f'<th scope="col"{' class="r"' if a == "r" else ""}>{escape(h)}</th>'
        for h, a in zip(head, align, strict=True)
    )
    body = "".join(
        "<tr>"
        + "".join(
            (
                f'<th scope="row">{escape(c)}</th>'
                if i == 0
                else f"<td{' class="r"' if a == 'r' else ''}>{escape(c)}</td>"
            )
            for i, (c, a) in enumerate(zip(row, align, strict=True))
        )
        + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def _ul(items: Sequence[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(x)}</li>" for x in items) + "</ul>"


def _stamp_lines(s: Mapping[str, Any]) -> list[str]:
    lines = [
        f"Run id: {s['run_id']}",
        f"Code version: {s['code']}",
        f"Data manifest: {s['data']}",
        f"Assumption set: {s['assumptions']}",
        f"Seed: {s['seed']} \u00b7 Draws: {s['draws']} \u00b7 Cycle: {s['cycle']}"
        + (f" \u00b7 Run date: {s['run_date']}" if s.get("run_date") else ""),
        f"Reproduce: {s['command']}",
        f"Reopen the view: {s['link']}",
    ]
    if s.get("card_source"):
        lines.append(f"Baseline card: {s['card_source']}")
    return lines


def to_html(doc: Mapping[str, Any], css_href: str | None = None) -> str:
    """The hearing brief as one print-ready HTML page (two US Letter pages).

    With no ``css_href`` the style rules are inline, so the file is self-contained (the CLI's
    file, opened from disk). A web page under a Content Security Policy that allows styles
    only from its own site (the app and the static site) gets ``css_href`` instead: a link
    to the same rules saved as a file (:data:`CSS_FILE`), never an inline block.
    """
    p: list[str] = [f"<h1>{escape(doc['title'])}</h1>"]
    if doc["banner"]:
        p.append(f'<p class="banner">{escape(doc["banner"])}</p>')
    p.append('<section aria-labelledby="h-q"><h2 id="h-q">The question</h2>')
    p.append(f'<p class="question">{escape(doc["question"])}</p></section>')
    p.append('<section aria-labelledby="h-a"><h2 id="h-a">The answer, as a likely range</h2>')
    p.append(f'<p class="lead">{escape(doc["sentence"])}</p>')
    if doc["answer_rows"]:
        p.append(_table(["Figure", "Likely range", "Middle"], doc["answer_rows"], "lrr"))
    if doc["frequency"]:
        p.append(f"<p>{escape(doc['frequency'])}</p>")
    if doc["scope"]:
        p.append("<p><strong>What the headline counts</strong></p>" + _ul(doc["scope"]))
    p.append("</section>")
    p.append('<section aria-labelledby="h-b"><h2 id="h-b">The baseline</h2>')
    if doc["baseline_rows"]:
        p.append(_table(["Item", "What the baseline uses"], doc["baseline_rows"], "ll"))
    else:
        p.append(f"<p>{escape(doc['baseline_note'])}</p>")
    p.append("</section>")
    p.append('<div class="page-two">')
    law = doc.get("law")
    if law:
        p.append('<section aria-labelledby="h-l"><h2 id="h-l">What the law changes</h2>')
        if law["rows"]:
            p.append(
                _table(["Answer", f"Share of {law['denominator']}", "Count"], law["rows"], "lrr")
            )
        if law["lines"]:
            p.append(_ul(law["lines"]))
        p.append("</section>")
    for hid, head, items in (
        ("h-d", "Main drivers", doc["drivers"]),
        ("h-u", "What is unknown", doc["unknowns"]),
        ("h-m", "What the model cannot tell you", doc["limits"]),
    ):
        if items:
            p.append(
                f'<section aria-labelledby="{hid}"><h2 id="{hid}">{head}</h2>{_ul(items)}</section>'
            )
    lines = _stamp_lines(doc["stamp"])
    p.append(
        '<footer class="stamp" aria-label="Reproduce this brief"><p>'
        + "<br>".join(escape(x) for x in lines)
        + f"</p><p><strong>{escape(doc['stamp']['disclaimer'])}</strong></p></footer>"
    )
    p.append("</div>")
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(doc['title'])}</title>\n"
        + (
            f'<link rel="stylesheet" href="{escape(css_href)}">\n'
            if css_href
            else f"<style>\n{CSS}\n</style>\n"
        )
        + "</head>\n<body>\n"
        "<main>\n" + "\n".join(p) + "\n</main>\n</body>\n</html>\n"
    )


def _md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _md_table(head: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"]
    out += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in rows]
    return out


def to_markdown(doc: Mapping[str, Any]) -> str:
    """The hearing brief as Markdown (same content as the HTML page)."""
    o: list[str] = [f"# {doc['title']}", ""]
    if doc["banner"]:
        o += [f"> **{doc['banner']}**", ""]
    o += ["## The question", "", f"**{doc['question']}**", ""]
    o += ["## The answer, as a likely range", "", doc["sentence"], ""]
    if doc["answer_rows"]:
        o += [*_md_table(["Figure", "Likely range", "Middle"], doc["answer_rows"]), ""]
    if doc["frequency"]:
        o += [doc["frequency"], ""]
    if doc["scope"]:
        o += ["**What the headline counts**", "", *[f"- {x}" for x in doc["scope"]], ""]
    o += ["## The baseline", ""]
    if doc["baseline_rows"]:
        o += [*_md_table(["Item", "What the baseline uses"], doc["baseline_rows"]), ""]
    else:
        o += [doc["baseline_note"], ""]
    law = doc.get("law")
    if law:
        o += ["## What the law changes", ""]
        if law["rows"]:
            o += [
                *_md_table(["Answer", f"Share of {law['denominator']}", "Count"], law["rows"]),
                "",
            ]
        o += [*[f"- {x}" for x in law["lines"]], ""]
    for head, items in (
        ("Main drivers", doc["drivers"]),
        ("What is unknown", doc["unknowns"]),
        ("What the model cannot tell you", doc["limits"]),
    ):
        if items:
            o += [f"## {head}", "", *[f"- {x}" for x in items], ""]
    o += [
        "---",
        "",
        *[f"{x}  " for x in _stamp_lines(doc["stamp"])],
        f"**{doc['stamp']['disclaimer']}**",
    ]
    return "\n".join(o) + "\n"


def render(
    run: Mapping[str, Any],
    fmt: str = "html",
    *,
    card: Mapping[str, Any] | None = None,
    law: Mapping[str, Any] | None = None,
    run_date: str | None = None,
    css_href: str | None = None,
) -> str:
    """The hearing brief of a run reply as ``html`` or ``md`` text (see :func:`to_html`)."""
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {', '.join(FORMATS)}")
    doc = hearing_doc(run, card, law, run_date=run_date)
    return to_html(doc, css_href) if fmt == "html" else to_markdown(doc)


def hearing_from_result(
    result: Any,
    *,
    card: Mapping[str, Any] | None = None,
    law: Mapping[str, Any] | None = None,
    fmt: str = "html",
    run_date: str | None = None,
    css_href: str | None = None,
) -> str:
    """Render from an engine result: the run reply is built exactly as ``POST /run`` builds it."""
    from visionhousing.api import service

    drivers = len(list(result.reform_ids)) == 1
    payload, status = service.explanation(result, drivers=drivers)
    reply = json.loads(
        service.dumps({**result.to_dict(), "explanation": payload, "explanation_status": status})
    )
    return render(reply, fmt, card=card, law=law, run_date=run_date, css_href=css_href)


def hearing_reply(ctx: Any, folder: Path, result: Any) -> bytes:
    """The API's ``GET /export/hearing`` body: the brief of a run, with the state's card.

    The baseline card is compared with this run's baseline record; the law section is
    added for one reform when its payload exists.
    """
    from visionhousing.api.baseline_view import view_for

    card = (
        None
        if result.synthetic
        else view_for(result.state, result.baseline.model_dump(mode="json"))
    )
    reforms = list(result.reform_ids)
    law = (
        law_payload(ctx, folder, reforms[0], result)
        if len(reforms) == 1 and not result.synthetic
        else None
    )
    return hearing_from_result(result, card=card, law=law, css_href=API_CSS_HREF).encode("utf-8")


def law_payload(ctx: Any, folder: Path, key: str, result: Any) -> dict[str, Any] | None:
    """The "what this law changes" payload of one reform, or ``None`` when there is none.

    Optional by design: a state or reform without that view (or any failure reading
    it) leaves the section out of the brief rather than stopping it.
    """
    try:
        from visionhousing.api import law_changes as lc

        tables = lc.read_tables(folder)
        if key not in lc.load_settings().get("reforms", {}):
            return None
        return json.loads(json.dumps(lc.build_payload(ctx, key, tables, result), default=str))
    except Exception:  # optional section: the brief stands without it
        return None


# ------------------------------------------------------------------ command


def add_parser(sub: Any) -> None:
    """Add ``hearing-brief`` to the command line."""
    p = sub.add_parser(
        "hearing-brief",
        help="Write the two-page hearing brief of one state and reform (or the package)",
    )
    p.add_argument("--state", required=True)
    p.add_argument("--reforms", default="adu", help="reform ids, 'package' or 'none'")
    p.add_argument("--format", choices=FORMATS, default="html")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--draws", type=int, default=2000)
    p.add_argument("--date", default=None, help="run date to print (YYYY-MM-DD); default none")
    p.add_argument(
        "--overrides",
        default="",
        help="assumption overrides as in a share link: id~l|c|h or id~number, comma-separated",
    )
    p.add_argument(
        "--static",
        type=Path,
        default=None,
        help="read a saved static export (e.g. web/public/data) instead of running the engine",
    )
    p.add_argument(
        "--refresh-static",
        type=Path,
        default=None,
        help="write the baseline card and every saved run's hearing brief into a static "
        "export folder (states comma-separated in --state); no engine run",
    )


def _overrides_arg(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in (text or "").split(","):
        if not item.strip():
            continue
        key, _, value = item.strip().partition("~")
        words = {"l": "low", "c": "central", "h": "high"}
        if value in words.values():
            out[key] = value
        else:
            out[key] = words[value] if value in words else float(value)
    return out


def _reforms_arg(text: str) -> list[str]:
    t = (text or "").strip()
    if t in ("", "none", "baseline"):
        return []
    if t == "package":
        return list(PACKAGE)
    return [x.strip() for x in t.split(",") if x.strip()]


def from_static(data: Path, state: str, reforms: list[str]) -> tuple[dict, dict | None]:
    """The saved run and baseline card of a scenario in a static export folder."""
    from visionhousing.export_static import read_saved

    code = state.upper()
    key = run_key(reforms)
    path = Path(data) / code / "runs" / f"{key}.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not in this export")
    card_path = Path(data) / code / "baseline-card.json"
    card = read_saved(card_path) if card_path.is_file() else None
    if card is None:
        summary = Path(data) / code / "summary.json"
        card = (read_saved(summary) or {}).get("baseline_card") if summary.is_file() else None
    return read_saved(path), card


def refresh_static(data: Path, state: str) -> list[Path]:
    """Bring a saved static export of one state up to the current file layout, lightly.

    Writes ``ST/baseline-card.json`` (``api.baseline_view.write_static``) and one
    ``ST/exports/<key>.hearing.html`` per saved run, from the saved run files (no engine
    run), and records ``schema_version``, ``baseline_card`` and ``hearing_briefs`` in
    ``ST/index.json``. A full ``visionhousing export-static`` writes the same files.
    """
    from visionhousing.api.baseline_view import write_static
    from visionhousing.export_static import STATIC_SCHEMA_VERSION, read_saved

    code = state.upper()
    folder = Path(data) / code
    idx_path = folder / "index.json"
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    written = write_static(Path(data), [code])
    css = Path(data) / CSS_FILE
    css.write_text(CSS + "\n", encoding="utf-8")
    written.append(css)
    card_path = folder / "baseline-card.json"
    card = read_saved(card_path) if card_path.is_file() else None
    briefs: list[str] = []
    for key in [*(index.get("runs") or []), *(index.get("extra_runs") or [])]:
        run_path = folder / "runs" / f"{key}.json"
        if not run_path.is_file():
            continue
        run = read_saved(run_path)
        if not run.get("explanation"):
            continue
        out = folder / "exports" / f"{key}.hearing.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(run, "html", card=card, css_href=STATIC_CSS_HREF), encoding="utf-8")
        written.append(out)
        briefs.append(key)
    new = {"schema_version": STATIC_SCHEMA_VERSION}
    new.update({k: v for k, v in index.items() if k != "schema_version"})
    new["hearing_briefs"] = briefs
    new["baseline_card"] = card is not None
    idx_path.write_text(json.dumps(new, indent=1), encoding="utf-8")
    written.append(idx_path)
    return written


def command(args: Any) -> int:
    """Write the brief and print where it went."""
    if args.refresh_static is not None:
        for code in args.state.upper().split(","):
            for path in refresh_static(args.refresh_static, code):
                print(path)
        return 0
    reforms = _reforms_arg(args.reforms)
    state = args.state.upper()
    if args.static is not None:
        run, card = from_static(args.static, state, reforms)
        text = render(run, args.format, card=card, run_date=args.date)
    else:
        from visionhousing.api.baseline_view import view_for
        from visionhousing.engine import run as engine_run

        overrides = _overrides_arg(args.overrides)
        result = engine_run(state, reforms, overrides, seed=args.seed, draws=args.draws)
        baseline = result.baseline.model_dump(mode="json")
        card = None if result.synthetic else view_for(state, baseline)
        law = None
        if len(reforms) == 1 and not result.synthetic:
            from visionhousing.engine.context import pack_dir

            rc = getattr(result, "run_context", None)
            if rc is not None:
                law = law_payload(rc.state, pack_dir(state), reforms[0], result)
        text = hearing_from_result(result, card=card, law=law, fmt=args.format, run_date=args.date)
    out = (
        args.out
        or Path("outputs") / "reports" / f"{state}-{run_key(reforms)}-hearing.{args.format}"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(out)
    return 0
