"""Build every explanation of one engine run, as plain JSON-ready data.

Plain-language summary
----------------------
:func:`explain` takes a finished engine result and returns one dictionary
that the interface, the report writer and the slides all read:

- ``headline``: the one-sentence summary and three numbers (baseline
  five-year homes, homes added, percent change), each as a likely range;
- ``verdicts``: one word per reform (*Adds a lot / Adds some / Little effect /
  Overlaps*) from thresholds in the Assumption Ledger;
- ``funnels``: each reform's steps, each with its range, formula and a plain
  sentence on what the step means;
- ``drivers``: the top inputs from the sensitivity tornado, with sentences;
- ``readouts``: per reform, the frequency wording ("In eight of ten simulated
  futures ..."), the paired sentence, the chance of adding at least N homes,
  the share of the baseline and what the reform costs (``explain.readouts``);
- ``narrowing``: per reform, each input's share of the uncertainty and the
  research agenda ("Knowing X would shrink the range by about Y%");
- ``waterfall``: how the reforms add up as a package;
- ``limits``: what each estimate cannot tell you (``assumptions/limits.yaml``);
- ``numbers``: the "explain this number" payload for every record id
  (formula, inputs, sources, assumptions with labels, range, tier);
- ``assumptions`` and ``sources``: the lookups those payloads point to;
- ``slides``: the slide deck spec (see ``explain.slides``);
- ``stamp``: the three hashes, seed and draws.

No number is typed here. Every number in every sentence is copied from the
result (or from the sensitivity run on it, or the run's ledger) and carries a
cite saying where it came from; ``explain.numbers`` and the tests check this.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from visionhousing.engine.records import Record
from visionhousing.explain import wording as w
from visionhousing.explain.ids import record_index
from visionhousing.explain.limits import limits_for
from visionhousing.explain.numbers import SENTENCE_SIG, TABLE_SIG, Cite, Text, count, num, show
from visionhousing.ledger import Ledger

__all__ = [
    "DISCLAIMER",
    "VERDICT_WORDS",
    "explain",
    "explain_number",
    "funnel",
    "headline",
    "limits_block",
    "source_catalogue",
    "span",
    "top_drivers",
    "verdicts",
    "waterfall",
]

DISCLAIMER = "Decision support, not a forecast. Not an official state document."
"""Printed on every brief and every slide."""

VERDICT_WORDS = ("Adds a lot", "Adds some", "Little effect", "Overlaps")
"""The four verdict words, strongest first; "Overlaps" applies only in a package."""

A_LOT_ID = "explain.verdict_a_lot_pct"
SOME_ID = "explain.verdict_some_pct"
OVERLAP_ID = "explain.verdict_overlap_share"

COUNT_UNITS = frozenset({"units", "lots"})
NO_TIER = "Assumption only (no data tier)"
_SPAN = re.compile(r"(\d{4})-(\d{4})")


# ---------------------------------------------------------------- small helpers


def _cite(ref: str, field: str, value: float, sig: int = SENTENCE_SIG) -> Cite:
    return Cite(ref=ref, field=field, value=float(value), shown=num(value, sig))


def _text_cite(ref: str, field: str, value: str, shown: str | None = None) -> Cite:
    return Cite(ref=ref, field=field, value=value, shown=value if shown is None else shown)


def _label(ref: str, field: str, text: str, source: str | None = None) -> dict[str, Any]:
    """A label taken from engine text, as a Text whose numbers cite that text.

    Args:
        ref: Where the engine text is.
        field: Its field.
        text: The label as shown (may be a shortened or recapitalised form).
        source: The full engine text, when ``text`` is not exactly it.
    """
    full = text if source is None else source
    cites = (_text_cite(ref, field, full, text),) if any(ch.isdigit() for ch in text) else ()
    return Text(text, cites).to_dict()


def _quoted(ref: str, text: str) -> dict[str, Any]:
    """Engine text quoted whole (a note), citing itself so its numbers are traced."""
    cites = (_text_cite(ref, "", text),) if any(ch.isdigit() for ch in text) else ()
    return Text(text, cites).to_dict()


def _plain(text: str) -> dict[str, Any]:
    """Fixed words with no numbers (checked by the tests)."""
    return Text(text).to_dict()


def _finite(value: float) -> float | None:
    return None if value is None or not math.isfinite(value) else float(value)


def _values(r: Record) -> dict[str, float | None]:
    if r.is_range:
        return {"p10": r.p10, "p50": r.p50, "p90": r.p90, "value": None}
    return {"p10": None, "p50": None, "p90": None, "value": r.value}


def _shown(r: Record, sig: int = TABLE_SIG) -> dict[str, str]:
    if r.is_range:
        return {q: show(getattr(r, q), r.unit, sig) for q in ("p10", "p50", "p90")}
    return {"value": show(r.point(), r.unit, sig)}


def _range_item(ref: str, r: Record, label: dict[str, Any], sig: int = SENTENCE_SIG):
    return {
        "ref": ref,
        "label": label,
        "unit": r.unit,
        **_values(r),
        "shown": _shown(r, sig),
    }


def span(result: Any) -> tuple[str, Cite] | None:
    """The years covered (``2027-2031``) and a cite to the baseline's name that holds them."""
    m = _SPAN.search(result.baseline.name)
    if not m:
        return None
    shown = f"{m.group(1)}\u2013{m.group(2)}"
    return shown, _text_cite("baseline", "name", result.baseline.name, shown)


def _run_ledger(result: Any) -> Ledger | None:
    d = getattr(result, "draws_obj", None)
    return None if d is None else d.ledger


def _ledger_for(result: Any, ref: str) -> Ledger | None:
    parts = ref.split(".")
    if parts[0] == "reform_results" and len(parts) > 1 and parts[1] in result.reforms:
        res = result.reforms[parts[1]].result
        if res is not None and res.ledger is not None:
            return res.ledger
    return _run_ledger(result)


# ---------------------------------------------------------------- sources


def source_catalogue(result: Any) -> dict[str, dict[str, Any]]:
    """Every source id the run can name, with a citation and web address.

    Collected from the data source registry, the run's ledger and the state's
    scenarios. Unknown ids are left out (``explain_number`` then shows the id).

    Args:
        result: An engine result.

    Returns:
        Source id to ``{"id", "citation", "url"}``, sorted by id.
    """
    out: dict[str, dict[str, Any]] = {}
    try:
        from visionhousing.data.registry import load_registry

        for sid, s in load_registry().items():
            url = s.url_pattern if "{" not in s.url_pattern else s.terms_url
            out[sid] = {"id": sid, "citation": f"{s.publisher}, {s.name} ({s.vintage})", "url": url}
    except (OSError, ValueError):  # pragma: no cover - the registry ships with the code
        pass
    ledgers = [_run_ledger(result)]
    ledgers += [o.result.ledger for o in result.reforms.values() if o.result is not None]
    for led in ledgers:
        if led is None:
            continue
        for _, p in led.items():
            for s in p.source:
                out.setdefault(s.id, {"id": s.id, "citation": s.citation, "url": s.url})
    for entry in _calibration_sources():
        out.setdefault(entry["id"], entry)
    ctx = getattr(result, "run_context", None)
    if ctx is not None:
        for sc in ctx.state.scenarios.values():
            for s in sc.source:
                out.setdefault(s.id, {"id": s.id, "citation": s.citation, "url": s.url})
    return {k: out[k] for k in sorted(out)}


def _calibration_sources() -> list[dict[str, Any]]:
    """Sources named in the calibration files (the fit table and its inputs)."""
    import yaml

    from visionhousing.ledger.loader import ROOT

    out: list[dict[str, Any]] = []
    for path in sorted((ROOT / "assumptions" / "calibration").glob("*.yaml")):
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            continue
        prov = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
        if data.get("id") and data.get("label"):
            summary = str(prov.get("summary", "")).strip()
            citation = f"VisionHousing calibration: {data['label']}" + (
                f". {summary}" if summary else ""
            )
            out.append({"id": str(data["id"]), "citation": citation, "url": None})
        for s in prov.get("sources", ()) or ():
            if isinstance(s, dict) and s.get("id") and s.get("citation"):
                out.append(
                    {"id": str(s["id"]), "citation": str(s["citation"]), "url": s.get("url")}
                )
    return out


def _assumption(pid: str, ledger: Ledger | None, owner: str = "") -> dict[str, Any]:
    """One ledger entry as data; ``ref`` names the ledger it was read from.

    ``ref`` is ``ledger:<reform>/<id>`` for a reform's own ledger (scenario
    overrides applied) and ``ledger:/<id>`` for the run's ledger.
    """
    ref = f"ledger:{owner}/{pid}"
    if ledger is None or pid not in ledger:
        return {"id": pid, "ref": ref, "label": pid}
    p = ledger[pid]
    return {
        "id": pid,
        "ref": ref,
        "label": p.label,
        "unit": p.unit,
        "kind": p.kind,
        "status": p.status,
        "shape": p.shape,
        "low": p.low,
        "central": p.central,
        "high": p.high,
        "shown": {f: show(getattr(p, f), p.unit, TABLE_SIG) for f in ("low", "central", "high")},
        "source_ids": list(p.source_ids),
    }


# ---------------------------------------------------------------- explain this number


def explain_number(result: Any, ref: str, record: Record | None = None) -> dict[str, Any]:
    """The "explain this number" payload for one record id.

    Args:
        result: An engine result.
        ref: The record's id (see ``explain.ids``).
        record: The record, if already looked up.

    Returns:
        Name, unit, range (or value) with its rounded form, formula, inputs,
        assumption ids with labels, source ids, and tier with its label.
    """
    r = record if record is not None else record_index(result)[ref]
    ledger = _ledger_for(result, ref)
    return {
        "id": ref,
        "name": r.name,
        "unit": r.unit,
        "kind": "range" if r.is_range else "value",
        **_values(r),
        "shown": _shown(r),
        "formula": r.formula,
        "inputs": [
            {
                "name": i.name,
                "value": i.value,
                "unit": i.unit,
                "shown": show(i.value, i.unit, TABLE_SIG),
                "formula": i.formula,
            }
            for i in r.inputs
        ],
        "assumptions": [
            {"id": a, "label": _assumption(a, ledger)["label"]} for a in r.assumption_ids
        ],
        "source_ids": list(r.source_ids),
        "tier": r.tier,
        "tier_label": w.TIER_LABELS.get(r.tier, NO_TIER) if r.tier is not None else NO_TIER,
    }


# ---------------------------------------------------------------- headline


def _reform_list(result: Any, keys: Sequence[str]) -> tuple[str, tuple[Cite, ...]]:
    parts, cites = [], []
    for k in keys:
        title = result.reforms[k].title
        phrase = w.reform_phrase(k, title)
        parts.append(phrase)
        if any(ch.isdigit() for ch in phrase):
            cites.append(_text_cite(f"reform_results.{k}", "title", title, phrase))
    return w.join_words(parts), tuple(cites)


def headline(result: Any) -> dict[str, Any]:
    """The one sentence and the three numbers.

    Template (two significant figures)::

        Over {span}, {state} would likely permit between {p10} and {p90} more
        homes ({pct_p10}-{pct_p90}%) with {reforms}, on top of a baseline of
        {b_p10}-{b_p90} homes expected under today's rules.

    Args:
        result: An engine result.

    Returns:
        ``{"sentence": Text, "numbers": [baseline, added, percent]}``.
    """
    state = w.state_name(result.state)
    sp = span(result)
    years, span_cites = (sp[0], (sp[1],)) if sp else ("the five years", ())
    b = result.baseline
    b_lo, b_hi = _cite("baseline", "p10", b.p10), _cite("baseline", "p90", b.p90)
    keys = list(result.package.reforms) if result.package is not None else []
    from visionhousing.baseline.reliability import range_tag

    words = "Baseline: homes permitted under today's rules"
    tag = range_tag(b.formula)
    # A real state's baseline range says how often ranges like it caught past totals.
    b_label = (
        _label("baseline", "formula", f"{words}, {tag}", source=b.formula) if tag else _plain(words)
    )
    numbers = [{"key": "baseline", **_range_item("baseline", b, b_label)}]
    if not keys:
        text = (
            f"Over {years}, {state} would likely permit between {b_lo.shown} and "
            f"{b_hi.shown} homes under today's rules."
        )
        return {
            "sentence": Text(text, (*span_cites, b_lo, b_hi)).to_dict(),
            "numbers": numbers,
        }
    p = result.package
    lo, hi = (
        _cite("package.combined", "p10", p.combined.p10),
        _cite("package.combined", "p90", p.combined.p90),
    )
    pct_lo, pct_hi = (
        _cite("package.pct_change", "p10", p.pct_change.p10),
        _cite("package.pct_change", "p90", p.pct_change.p90),
    )
    reforms, reform_cites = _reform_list(result, keys)
    if hi.shown == "0":
        text = (
            f"Over {years}, {state} would likely permit few or no more homes with "
            f"{reforms}, on top of a baseline of {b_lo.shown}\u2013{b_hi.shown} homes "
            "expected under today's rules."
        )
        cites = (*span_cites, *reform_cites, b_lo, b_hi)
    else:
        text = (
            f"Over {years}, {state} would likely permit between {lo.shown} and {hi.shown} "
            f"more homes ({pct_lo.shown}\u2013{pct_hi.shown}%) with {reforms}, on top of a "
            f"baseline of {b_lo.shown}\u2013{b_hi.shown} homes expected under today's rules."
        )
        cites = (*span_cites, lo, hi, pct_lo, pct_hi, *reform_cites, b_lo, b_hi)
    numbers.append(
        {
            "key": "added",
            **_range_item(
                "package.combined",
                p.combined,
                _plain(w.added_label(keys, "Homes added by the reforms")),
            ),
        }
    )
    numbers.append(
        {
            "key": "pct_change",
            **_range_item("package.pct_change", p.pct_change, _plain("Change from the baseline")),
        }
    )
    return {"sentence": Text(text, cites).to_dict(), "numbers": numbers}


# ---------------------------------------------------------------- verdicts


def _threshold(ledger: Ledger | None, pid: str) -> float:
    if ledger is None or pid not in ledger:
        raise KeyError(f"the ledger has no '{pid}'; add it to assumptions/parameters.yaml")
    return float(ledger[pid].central)


def verdict_word(pct_p50: float, a_lot: float, some: float, overlap_share: float | None = None,
                 overlap_limit: float | None = None) -> str:  # fmt: skip
    """Pick the verdict word from the middle estimate (pure).

    Args:
        pct_p50: The reform's P50 percent change against the baseline, run alone.
        a_lot: Smallest P50 percent for "Adds a lot".
        some: Smallest P50 percent for "Adds some".
        overlap_share: Share of the reform's own P50 removed as overlap in the
            package (``None`` when it shares no lots).
        overlap_limit: Smallest share for "Overlaps".

    Returns:
        One of :data:`VERDICT_WORDS`.
    """
    if overlap_share is not None and overlap_limit is not None and overlap_share >= overlap_limit:
        return "Overlaps"
    if pct_p50 >= a_lot:
        return "Adds a lot"
    if pct_p50 >= some:
        return "Adds some"
    return "Little effect"


def _overlap_target(result: Any) -> tuple[str, str] | None:
    """``(reform losing lots, reform keeping them)`` when the package has an overlap."""
    if result.package is None:
        return None
    from visionhousing.engine.packages import overlap_pair

    pair = overlap_pair(result.package.reforms)
    if pair is None:
        return None
    keeper, loser = pair
    return loser, keeper


def verdicts(result: Any) -> dict[str, dict[str, Any]]:
    """The verdict word for each reform, with the rule that picked it.

    Args:
        result: An engine result.

    Returns:
        Reform key to ``{"key", "title", "word", "why": Text, "thresholds"}``.
    """
    ledger = _run_ledger(result)
    a_lot, some = _threshold(ledger, A_LOT_ID), _threshold(ledger, SOME_ID)
    limit = _threshold(ledger, OVERLAP_ID)
    target = _overlap_target(result)
    out: dict[str, dict[str, Any]] = {}
    for key, o in result.reforms.items():
        ref = f"reform_results.{key}"
        mid = _cite(f"{ref}.pct_change", "p50", o.pct_change.p50)
        share = None
        overlap_text = None
        if target is not None and target[0] == key and o.added.p50 > 0:
            share = result.package.overlap.p50 / o.added.p50
            ov = _cite("package.overlap", "p50", result.package.overlap.p50)
            own = _cite(f"{ref}.added", "p50", o.added.p50)
            keeper = result.reforms[target[1]].title
            overlap_text = (ov, own, keeper)
        word = verdict_word(o.pct_change.p50, a_lot, some, share, limit)
        if word == "Overlaps" and overlap_text is not None:
            ov, own, keeper = overlap_text
            keeper_phrase = w.reform_phrase(target[1], keeper)
            kc = (
                (_text_cite(f"reform_results.{target[1]}", "title", keeper, keeper_phrase),)
                if any(ch.isdigit() for ch in keeper_phrase)
                else ()
            )
            why = Text(
                f"In this package, {ov.shown} of the {own.shown} homes it adds alone (middle "
                f"estimates) are on lots already used with {keeper_phrase}.",
                (ov, own, *kc),
            )
        else:
            lot = _cite(f"ledger:/{A_LOT_ID}", "central", a_lot)
            sm = _cite(f"ledger:/{SOME_ID}", "central", some)
            rule = {
                "Adds a lot": f"“Adds a lot” means at least {lot.shown}%.",
                "Adds some": f"“Adds some” means {sm.shown}% to under {lot.shown}%.",
                "Little effect": f"“Little effect” means under {sm.shown}%.",
            }[word]
            used = {"Adds a lot": (lot,), "Adds some": (sm, lot), "Little effect": (sm,)}[word]
            why = Text(
                f"Middle estimate: {mid.shown}% more homes than the baseline. {rule}",
                (mid, *used),
            )
        out[key] = {
            "key": key,
            "title": o.title,
            "word": word,
            "pct_p50": o.pct_change.p50,
            "overlap_share": share,
            "why": why.to_dict(),
            "thresholds": {A_LOT_ID: a_lot, SOME_ID: some, OVERLAP_ID: limit},
        }
    return out


# ---------------------------------------------------------------- funnels


def funnel(result: Any, key: str) -> dict[str, Any]:
    """One reform's steps, for the funnel table and chart.

    Each step has its label, plain meaning, range (or central value), rounded
    form, formula and tier. When the reform's steps are central values only
    (the fee and parking reforms work out one central chain and sample it
    separately), the reform's likely range is added as a last row.

    Args:
        result: An engine result.
        key: Reform key.

    Returns:
        ``{"key", "title", "scope", "steps", "central_only"}``.
    """
    o = result.reforms[key]
    base = f"reform_results.{key}"
    steps = []
    for i, s in enumerate(o.steps):
        ref = f"{base}.steps.{i}"
        label = w.step_label(s.name)
        label = label[:1].upper() + label[1:]
        steps.append(
            {
                "ref": ref,
                "label": _label(ref, "name", label, s.name),
                "meaning": _plain(w.step_meaning(s.name)),
                "kind": "range" if s.is_range else "central",
                "unit": s.unit,
                **_values(s),
                "shown": _shown(s),
                "formula": s.formula,
                "tier": s.tier,
            }
        )
    central_only = bool(o.steps) and not any(s.is_range for s in o.steps)
    if central_only or not o.steps:
        steps.append(
            {
                "ref": f"{base}.added",
                "label": _plain("Homes added, likely range"),
                "meaning": _plain(
                    "The same chain worked out in every simulated draw, with every "
                    "uncertain input varied together."
                ),
                "kind": "range",
                "unit": o.added.unit,
                **_values(o.added),
                "shown": _shown(o.added),
                "formula": o.added.formula,
                "tier": o.added.tier,
            }
        )
    scope = w.step_scope(o.steps[0].name) if o.steps else ""
    return {
        "key": key,
        "title": o.title,
        "scope": scope,
        "central_only": central_only,
        "steps": steps,
    }


# ---------------------------------------------------------------- drivers


def top_drivers(result: Any, key: str) -> dict[str, Any]:
    """The top inputs behind one reform, from the sensitivity tornado, with sentences.

    Args:
        result: An engine result (from ``engine.run``, with its run context).
        key: Reform key.

    Returns:
        ``{"key", "lead": Text, "central_total", "items": [...]}``; each item
        has the raw tornado values and a sentence.
    """
    from visionhousing.sensitivity import drivers

    d = drivers(result, key)
    rows = {str(r.parameter): r for r in d.tornado.itertuples(index=False)}
    items = []
    for i, x in enumerate(d.top):
        ref = f"drivers.{key}.{i}"
        row = rows[x.parameter]
        lo = _cite(ref, "total_low", x.total_low)
        hi = _cite(ref, "total_high", x.total_high)
        label = x.label
        cites: list[Cite] = [lo, hi]
        if any(ch.isdigit() for ch in label):
            cites.append(_text_cite(ref, "label", label))
        unit = str(row.unit)
        if unit.startswith("multiplier"):
            text = (
                f"{label}: about {lo.shown} homes with permits at the low end of their "
                f"range, about {hi.shown} at the high end."
            )
        else:
            low_v = Cite(ref, "low", float(row.low), show(float(row.low), unit, TABLE_SIG))
            high_v = Cite(ref, "high", float(row.high), show(float(row.high), unit, TABLE_SIG))
            cites += [low_v, high_v]
            text = (
                f"{label}: about {lo.shown} homes at its low value ({low_v.shown}), "
                f"about {hi.shown} at its high value ({high_v.shown})."
            )
        items.append(
            {
                "ref": ref,
                "rank": x.rank,
                "parameter": x.parameter,
                "label": label,
                "unit": unit,
                "low": float(row.low),
                "central": float(row.central),
                "high": float(row.high),
                "total_low": x.total_low,
                "total_central": float(row.total_central),
                "total_high": x.total_high,
                "swing": x.swing,
                "rho": _finite(x.rho),
                "text": Text(text, tuple(cites)).to_dict(),
            }
        )
    central = float(d.tornado["total_central"].iloc[0]) if len(d.tornado) else 0.0
    return {
        "key": key,
        "title": result.reforms[key].title,
        "lead": _plain(
            "Each input below is moved alone from its low to its high value while every "
            "other input stays at its central value. The figures are the five-year homes "
            "this reform adds at each end."
        ),
        "central_total": central,
        "items": items,
    }


# ---------------------------------------------------------------- waterfall


WATERFALL_STEPS = (
    ("sum_of_parts", "start", "Each reform alone, added up"),
    ("overlap", "minus", "Minus lots counted twice"),
    ("interaction", "plus", "Plus savings that feed another reform"),
    ("combined", "total", "All reforms together"),
)


def waterfall(result: Any) -> dict[str, Any] | None:
    """The package waterfall: sum of parts, overlap removed, interaction added, combined.

    Args:
        result: An engine result.

    Returns:
        ``None`` without reforms; else ``{"steps", "parts", "interactions", "text"}``.
    """
    p = result.package
    if p is None or not p.reforms:
        return None
    steps = []
    for name, kind, label in WATERFALL_STEPS:
        r = getattr(p, name)
        steps.append({"kind": kind, **_range_item(f"package.{name}", r, _plain(label))})
    parts = [
        _range_item(
            f"reform_results.{k}.added",
            result.reforms[k].added,
            _label(f"reform_results.{k}", "title", result.reforms[k].title),
        )
        for k in p.reforms
    ]
    inter = [
        _range_item(
            f"package.interactions.{n}",
            r,
            _label(f"package.interactions.{n}", "name", n.replace("->", "→")),
        )
        for n, r in p.interactions.items()
    ]
    c = {
        n: Cite(f"package.{n}", "p50", getattr(p, n).p50, count(getattr(p, n).p50, TABLE_SIG))
        for n, _, _ in WATERFALL_STEPS
    }
    text = Text(
        f"Middle estimates: each reform alone adds up to {c['sum_of_parts'].shown} homes; "
        f"lots counted twice remove {c['overlap'].shown}; savings that feed another "
        f"reform add {c['interaction'].shown}; together, {c['combined'].shown}.",
        tuple(c.values()),
    )
    return {"steps": steps, "parts": parts, "interactions": inter, "text": text.to_dict()}


# ---------------------------------------------------------------- limits


def weakest_places(result: Any) -> str | None:
    """One sentence naming where the baseline's back-test has been weakest, or ``None``.

    Written by rule from the chosen rule's back-test by kind of place
    (``baseline.places``), with no figures, so it can sit in every "what the
    model cannot tell you" list. ``None`` when the run kept no context.
    """
    rc = getattr(result, "run_context", None)
    if rc is None:
        return None
    from visionhousing.baseline.places import place_backtest_for_context
    from visionhousing.ledger.loader import ROOT

    where = ROOT / str(rc.state.source)
    folder = where if rc.state.source and where != ROOT and where.is_dir() else None
    return place_backtest_for_context(rc.state, rc.baseline.rule, folder).limits


def limits_block(result: Any) -> dict[str, Any]:
    """What the estimates cannot tell you: general, baseline, package and each reform.

    The baseline and every reform also get the sentence of :func:`weakest_places`:
    a reform's change rests on the baseline, so where the baseline has been least
    accurate, the change is least certain too.
    """
    weak = weakest_places(result)

    def one(key: str) -> dict[str, Any]:
        lim = limits_for(key)
        items = [_plain(t) for t in lim.items]
        if weak and key not in ("general", "package"):
            items.append(_plain(weak))
        return {"key": key, "title": lim.title, "items": items}

    out = {"general": one("general"), "baseline": one("baseline")}
    if result.package is not None and len(result.package.reforms) > 1:
        out["package"] = one("package")
    out["reforms"] = {k: one(k) for k in result.reforms}
    return out


# ---------------------------------------------------------------- everything


def _assumptions_used(result: Any) -> dict[str, dict[str, Any]]:
    run_ledger = _run_ledger(result)
    out: dict[str, dict[str, Any]] = {}
    for key, o in result.reforms.items():
        led = o.result.ledger if o.result is not None and o.result.ledger else run_ledger
        owner = key if led is not run_ledger else ""
        for pid in o.added.assumption_ids:
            if pid not in out:
                out[pid] = {**_assumption(pid, led, owner), "used_by": []}
            out[pid]["used_by"].append(key)
    for pid in (*result.baseline.assumption_ids, A_LOT_ID, SOME_ID, OVERLAP_ID):
        if pid not in out:
            out[pid] = {**_assumption(pid, run_ledger), "used_by": []}
    for pid in result.baseline.assumption_ids:
        if "baseline" not in out[pid]["used_by"]:
            out[pid]["used_by"].append("baseline")
    for pid in (A_LOT_ID, SOME_ID, OVERLAP_ID):
        out[pid]["used_by"].append("verdicts")
    return {k: out[k] for k in sorted(out)}


def _sources_used(index: Mapping[str, Record], assumptions: Mapping[str, Any]) -> list[str]:
    ids: set[str] = set()
    for r in index.values():
        ids.update(r.source_ids)
    for a in assumptions.values():
        ids.update(a.get("source_ids", ()))
    return sorted(ids)


def stamp(result: Any) -> dict[str, Any]:
    """The run's fingerprint: three hashes, seed, draws, state, and the disclaimer."""
    return {
        "state": result.state,
        "state_name": w.state_name(result.state),
        "reforms": list(result.reform_ids),
        "code": result.hashes.code,
        "data": result.hashes.data,
        "assumptions": result.hashes.assumptions,
        "seed": result.seed,
        "draws": result.draws,
        "cycle": result.cycle,
        "synthetic": result.synthetic,
        "disclaimer": DISCLAIMER,
    }


def explain(result: Any, *, with_drivers: bool = True, numbers: bool = True) -> dict[str, Any]:
    """Every explanation of a run, as one JSON-ready dictionary.

    Args:
        result: An engine result (from ``engine.run``).
        with_drivers: Run the sensitivity tornado for each reform (needs the
            run context, which ``engine.run`` keeps).
        numbers: Include the "explain this number" payload for every record.

    Returns:
        The explanation (see the module summary for its keys). Same result in,
        same dictionary out.
    """
    from visionhousing.explain.readouts import (
        headline_frequency,
        narrowing_block,
        reform_readouts,
    )
    from visionhousing.explain.slides import slides

    index = record_index(result)
    assumptions = _assumptions_used(result)
    catalogue = source_catalogue(result)
    used = _sources_used(index, assumptions)
    out: dict[str, Any] = {
        "stamp": stamp(result),
        "span": span(result)[0] if span(result) else None,
        "headline": {**headline(result), "frequency": headline_frequency(result)},
        "verdicts": verdicts(result),
        "readouts": {k: reform_readouts(result, k) for k in result.reforms},
        "narrowing": {k: narrowing_block(result, k) for k in result.reforms},
        "funnels": {k: funnel(result, k) for k in result.reforms},
        "drivers": {k: top_drivers(result, k) for k in result.reforms} if with_drivers else {},
        "waterfall": waterfall(result),
        "limits": limits_block(result),
        "assumptions": assumptions,
        "sources": {s: catalogue.get(s, {"id": s, "citation": s, "url": None}) for s in used},
        "notes": [
            *(_quoted(f"notes.{i}", t) for i, t in enumerate(result.notes)),
            *(
                _quoted(f"package.notes.{i}", t)
                for i, t in enumerate(result.package.notes if result.package else ())
            ),
        ],
        "reform_notes": {
            k: [_quoted(f"reform_results.{k}.notes.{i}", t) for i, t in enumerate(o.notes)]
            for k, o in result.reforms.items()
        },
    }
    if numbers:
        out["numbers"] = {ref: explain_number(result, ref, r) for ref, r in index.items()}
    out["slides"] = slides(result, out)
    return out


def cited_texts(node: Any) -> Iterable[dict[str, Any]]:
    """Every Text (``{"text", "cites"}``) anywhere inside an explanation, in order."""
    if isinstance(node, Mapping):
        if set(node) == {"text", "cites"}:
            yield node  # type: ignore[misc]
            return
        for v in node.values():
            yield from cited_texts(v)
    elif isinstance(node, list | tuple):
        for v in node:
            yield from cited_texts(v)
