"""Sentences for the paired readouts, the costs, the frequency wording and the research agenda.

Plain-language summary
----------------------
The engine works out, for every reform, readouts from the paired draws
(``engine.readouts``) and the sensitivity module works out each input's share
of the uncertainty (``sensitivity.variance``). This module writes them up as
sentences, by rule, with every number cited to the record it came from:

- **frequency wording**: a P10-P90 range said as "In eight of ten simulated
  futures, between X and Y homes" (eight in ten draws fall between the 10th
  and the 90th percentile, so the words carry no typed figure);
- **paired sentence**: "The future level of building is uncertain; the
  reform's effect on it is much less so", with the two widths. The qualifier
  ("much less", "less", "about as") is chosen by the width ratio and the
  ledger threshold ``explain.paired_much_less_ratio``;
- **draw shares**: "Adds at least N homes in P% of the modeled draws (Monte
  Carlo standard error S percentage points)" for each ledger threshold;
- **what it costs**: the fee cut's revenue given up and cost per added home
  (with the state-fee and targeted variants), the parking reform's private
  saving, and the "no direct public cost" text for the others;
- **what would narrow the range**: each input's share of the variance, the
  leftover "interactions and noise", and a research agenda sentence per top
  input: "Knowing X would shrink the range by about Y%".

ADU reforms carry the permit-statistics caveat wherever homes added are
named: "homes added, of which some may not appear in permit statistics".
"""

from __future__ import annotations

from typing import Any

from visionhousing.engine.readouts import share_percent
from visionhousing.explain import wording as w
from visionhousing.explain.numbers import SENTENCE_SIG, Cite, Text, num, show
from visionhousing.explain.payload import _cite, _plain, _range_item, _run_ledger, _text_cite

__all__ = [
    "frequency_text",
    "headline_frequency",
    "narrowing_block",
    "reform_readouts",
]

MUCH_LESS_ID = "explain.paired_much_less_ratio"


def _pct(ref: str, field: str, value: float) -> Cite:
    """A share (0-1) written as a percent."""
    return Cite(ref=ref, field=field, value=float(value), shown=num(100.0 * float(value)))


def _money(ref: str, field: str, value: float, unit: str = "$") -> Cite:
    return Cite(ref=ref, field=field, value=float(value), shown=show(float(value), unit))


def frequency_text(
    ref: str, record: Any, subject: str, tail: str = "", *, more: bool = False
) -> dict:
    """Frequency wording of one range record.

    Args:
        ref: The record's id.
        record: The record (P10 / P90 in homes).
        subject: What does it, for example "this reform adds" or "Pennsylvania permits".
        tail: Words after "homes" (for example the ADU caveat or "under today's rules").
        more: Write "more homes".

    Returns:
        A Text: "In eight of ten simulated futures, {subject} between X and Y homes{tail}."
    """
    lo, hi = _cite(ref, "p10", record.p10), _cite(ref, "p90", record.p90)
    homes = "more homes" if more else "homes"
    end = f", {tail}" if tail and tail.startswith("of which") else (f" {tail}" if tail else "")
    if hi.shown == "0":
        return Text(f"{w.FREQUENCY_LEAD}, {subject} few or no {homes}{end}.", ()).to_dict()
    return Text(
        f"{w.FREQUENCY_LEAD}, {subject} between {lo.shown} and {hi.shown} {homes}{end}.",
        (lo, hi),
    ).to_dict()


def headline_frequency(result: Any) -> dict:
    """The headline's frequency sentence (the package, or the baseline alone)."""
    state = w.state_name(result.state)
    keys = list(result.package.reforms) if result.package is not None else []
    if not keys:
        return frequency_text(
            "baseline", result.baseline, f"{state} permits", "under today's rules"
        )
    caveat = ""
    if any(k in w.ADU_FAMILY for k in keys):
        caveat = (
            w.ADU_MEASUREMENT
            if len(keys) == 1
            else "of which some ADUs may not appear in permit statistics"
        )
    return frequency_text(
        "package.combined", result.package.combined, f"{state} permits", caveat, more=True
    )


def _paired(result: Any, key: str, o: Any) -> dict:
    ref = f"reform_results.{key}.readouts"
    r = o.readouts
    bw = _cite(f"{ref}.baseline_width", "value", r["baseline_width"].value)
    aw = _cite(f"{ref}.added_width", "value", r["added_width"].value)
    ledger = _run_ledger(result)
    limit = float(ledger[MUCH_LESS_ID].central) if ledger and MUCH_LESS_ID in ledger else 0.25
    ratio = r["width_ratio"].value if "width_ratio" in r else None
    if ratio is not None and ratio < limit:
        lead = (
            "The future level of building is uncertain; the reform's effect on it is much less so."
        )
    elif ratio is not None and ratio < 1:
        lead = "The future level of building is uncertain; the reform's effect on it is less so."
    else:
        lead = "The reform's effect is about as uncertain as the future level of building."
    pct = o.pct_change
    s_lo = _cite(f"reform_results.{key}.pct_change", "p10", pct.p10)
    s_hi = _cite(f"reform_results.{key}.pct_change", "p90", pct.p90)
    text = (
        f"{lead} Across the simulated futures the baseline's range is {bw.shown} homes "
        f"wide; the homes this reform adds vary over only {aw.shown}, because each future is "
        f"worked out with and without the reform from the same draws. As a share of the "
        f"baseline in the same future, it adds {s_lo.shown}% to {s_hi.shown}%."
    )
    return Text(text, (bw, aw, s_lo, s_hi)).to_dict()


def _chances(result: Any, key: str, o: Any) -> list[dict]:
    from visionhousing.engine.readouts import THRESHOLD_IDS

    out = []
    for i, pid in enumerate(THRESHOLD_IDS, start=1):
        name = f"chance_at_least_{i}"
        if name not in o.readouts:
            continue
        r = o.readouts[name]
        n_min = _cite(f"ledger:/{pid}", "central", _run_ledger(result)[pid].central)
        n = int(getattr(result, "draws", 0) or 0)
        ref = f"reform_results.{key}.readouts.{name}"
        if n > 0:
            # Printed no finer than the draws support (engine.readouts.share_percent).
            share = Cite(
                ref=ref, field="value", value=float(r.value),
                shown=share_percent(float(r.value), n).rstrip("%"),
            )  # fmt: skip
        else:
            share = _pct(ref, "value", r.value)
        se_rec = o.readouts.get(f"{name}_se")
        cites: tuple[Cite, ...] = (n_min, share)
        tail = ""
        if se_rec is not None:
            se = Cite(
                ref=f"{ref}_se", field="value", value=float(se_rec.value),
                shown=num(100.0 * float(se_rec.value), 2),
            )  # fmt: skip
            cites = (n_min, se, share)  # the share stays the last cite (the web reads it)
            tail = f" (Monte Carlo standard error {se.shown} percentage points)"
        out.append(
            {
                "ref": ref,
                "parameter": pid,
                "threshold": n_min.value,
                "chance": r.value,
                "text": Text(
                    f"Adds at least {n_min.shown} homes in {share.shown}% of the modeled "
                    f"draws{tail}.",
                    cites,
                ).to_dict(),
            }
        )
    return out


def _range_words(ref: str, r: Any, unit: str) -> tuple[str, tuple[Cite, ...]]:
    mid = _money(ref, "p50", r.p50, unit)
    lo = _money(ref, "p10", r.p10, unit)
    hi = _money(ref, "p90", r.p90, unit)
    return f"about {mid.shown} (likely {lo.shown} to {hi.shown})", (mid, lo, hi)


def _group_label(ref: str, rec: Any, name: str) -> tuple[str, tuple[Cite, ...]]:
    if any(ch.isdigit() for ch in name):
        return name, (_text_cite(ref, "name", rec.name, name),)
    return name, ()


def _fee_costs(key: str, o: Any) -> dict:
    base = f"reform_results.{key}.readouts"
    r = o.readouts
    texts: list[dict] = []
    if "revenue_given_up" in r:
        words, cites = _range_words(f"{base}.revenue_given_up", r["revenue_given_up"], "$")
        cut = _money(f"{base}.fee_cut_per_unit", "p50", r["fee_cut_per_unit"].p50, "$/unit")
        texts.append(
            Text(
                f"Fee revenue given up over the five years: {words}. The cut of {cut.shown} "
                "applies to every permitted home, not only the added ones.",
                (*cites, cut),
            ).to_dict()
        )
    if "cost_per_added_home" in r:
        words, cites = _range_words(f"{base}.cost_per_added_home", r["cost_per_added_home"], "$")
        texts.append(Text(f"Public cost per added home: {words}.", cites).to_dict())
    elif "cost_per_added_home" in o.readout_texts:
        texts.append(_plain(o.readout_texts["cost_per_added_home"]))
    if "share_to_added_homes" in r:
        s = _pct(f"{base}.share_to_added_homes", "p50", r["share_to_added_homes"].p50)
        texts.append(
            Text(
                f"Middle estimate: only {s.shown}% of the revenue given up goes to the added "
                "homes; the rest goes to homes that would be built anyway.",
                (s,),
            ).to_dict()
        )
    variant = None
    if "cost_per_added_home_state_fees" in r:
        words, cites = _range_words(
            f"{base}.cost_per_added_home_state_fees", r["cost_per_added_home_state_fees"], "$"
        )
        cut = _money(
            f"{base}.fee_cut_per_unit_state_fees",
            "p50",
            r["fee_cut_per_unit_state_fees"].p50,
            "$/unit",
        )
        variant = Text(
            f"With the state fee level instead of the benchmark (a draft judgement, not yet "
            f"sourced from fee schedules), the cut is about {cut.shown} and the public cost "
            f"per added home is {words}.",
            (cut, *cites),
        ).to_dict()
    elif "cost_per_added_home_state_fees" in o.readout_texts:
        variant = _plain(o.readout_texts["cost_per_added_home_state_fees"])
    targeted = []
    prefix = "cost_per_added_home_targeted_"
    for name, rec in r.items():
        if not name.startswith(prefix):
            continue
        ref = f"{base}.{name}"
        group = rec.name.split("limited to '", 1)[-1].rsplit("' counties", 1)[0]
        label, gcites = _group_label(ref, rec, group)
        words, cites = _range_words(ref, rec, "$")
        targeted.append(
            {
                "ref": ref,
                "group": group,
                "revenue_ref": f"{base}.revenue_given_up_targeted_{name[len(prefix) :]}",
                **{q: getattr(rec, q) for q in ("p10", "p50", "p90")},
                "text": Text(
                    f"Cut limited to '{label}' counties: {words} per added home.",
                    (*gcites, *cites),
                ).to_dict(),
            }
        )
    return {"texts": texts, "state_fees": variant, "targeted": targeted}


def _parking_costs(key: str, o: Any) -> dict:
    base = f"reform_results.{key}.readouts"
    r = o.readouts
    texts = []
    if "private_saving_per_home" in r:
        words, cites = _range_words(
            f"{base}.private_saving_per_home", r["private_saving_per_home"], "$"
        )
        av = _cite(f"{base}.spaces_avoided_per_home", "p50", r["spaces_avoided_per_home"].p50)
        cps = _money(f"{base}.cost_per_space_avoided", "p50", r["cost_per_space_avoided"].p50)
        texts.append(
            Text(
                f"Builders save {words} per home the reform reaches: middle estimates of "
                f"{av.shown} spaces avoided per home at {cps.shown} a space.",
                (*cites, av, cps),
            ).to_dict()
        )
    if "private_saving_total" in r:
        words, cites = _range_words(f"{base}.private_saving_total", r["private_saving_total"], "$")
        texts.append(
            Text(f"Private saving to builders over the five years: {words}.", cites).to_dict()
        )
    return {"texts": texts, "state_fees": None, "targeted": []}


def reform_readouts(result: Any, key: str) -> dict[str, Any]:
    """Every readout sentence of one reform.

    Args:
        result: An engine result.
        key: Reform key.

    Returns:
        ``{"key", "added_label", "frequency", "paired", "chances",
        "share_of_baseline", "cost": {"framing", "texts", "state_fees", "targeted"}}``.
    """
    o = result.reforms[key]
    caveat = w.ADU_MEASUREMENT if key in w.ADU_FAMILY else ""
    out: dict[str, Any] = {
        "key": key,
        "title": o.title,
        "added_label": _plain(w.added_label(key)),
        "frequency": frequency_text(
            f"reform_results.{key}.added", o.added, "this reform adds", caveat
        ),
        "paired": _paired(result, key, o) if "baseline_width" in o.readouts else None,
        "chances": _chances(result, key, o),
        "share_of_baseline": _range_item(
            f"reform_results.{key}.pct_change",
            o.pct_change,
            _plain("Added homes as a share of the baseline, same simulated future"),
            SENTENCE_SIG,
        ),
    }
    framing = o.readout_texts.get("cost")
    if key == "fees" or (o.result is not None and getattr(o.result.detail, "reform", "") == "fees"):
        cost = _fee_costs(key, o)
    elif "private_saving_per_home" in o.readouts:
        cost = _parking_costs(key, o)
    else:
        cost = {"texts": [], "state_fees": None, "targeted": []}
    out["cost"] = {"framing": _plain(framing) if framing else None, **cost}
    return out


def _lower(label: str) -> str:
    """Lower-case the first letter unless the first word is an acronym (``ADU``)."""
    first = label.split(" ", 1)[0]
    if len(first) > 1 and not first[1:2].islower():
        return label
    return label[:1].lower() + label[1:]


def narrowing_block(result: Any, key: str) -> dict[str, Any]:
    """What would narrow the range of one reform: shares, leftover and the research agenda.

    Cites point at ``uncertainty.<key>.<i>`` (the i-th input, largest share first;
    fields ``share`` and ``shrink``) and ``uncertainty.<key>.leftover``.
    """
    from visionhousing.sensitivity import uncertainty

    n = uncertainty(result, key)
    items = []
    for i, s in enumerate(n.items):
        ref = f"uncertainty.{key}.{i}"
        share = _pct(ref, "share", s.share.value)
        shrink = _pct(ref, "shrink", s.shrink.value)
        label_cites = (
            (_text_cite(ref, "label", s.label),) if any(c.isdigit() for c in s.label) else ()
        )
        items.append(
            {
                "ref": ref,
                "rank": s.rank,
                "parameter": s.parameter,
                "label": s.label,
                "share": s.share.value,
                "shrink": s.shrink.value,
                "share_record": s.share.model_dump(mode="json"),
                "shrink_record": s.shrink.model_dump(mode="json"),
                "text": Text(
                    f"{s.label}: {share.shown}% of the uncertainty.", (*label_cites, share)
                ).to_dict(),
                "agenda": Text(
                    f"Knowing {_lower(s.label)} would shrink the range by about {shrink.shown}%.",
                    (*label_cites, shrink),
                ).to_dict(),
            }
        )
    lref = f"uncertainty.{key}.leftover"
    left = _pct(lref, "share", n.leftover.value)
    return {
        "key": key,
        "title": result.reforms[key].title,
        "bins": n.bins,
        "draws": n.draws,
        "lead": _plain(
            "How much of the uncertainty in the homes this reform adds comes from each input: "
            "the part of the spread that would go away if that input were known exactly. "
            "Worked out from the same simulated draws, with no new run."
        ),
        "items": items,
        "leftover": {
            "ref": lref,
            "share": n.leftover.value,
            "record": n.leftover.model_dump(mode="json"),
            "text": Text(
                f"Interactions and noise: {left.shown}% (inputs acting together, and the "
                "estimate's own error).",
                (left,),
            ).to_dict(),
        },
        "agenda": [it["agenda"] for it in items[: n.agenda] if it["shrink"] > 0],
    }
