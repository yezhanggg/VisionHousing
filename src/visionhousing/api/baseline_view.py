"""The baseline card as rows a screen or a brief can show without working anything out.

Plain-language summary
----------------------
The baseline card (``visionhousing.baseline.card``) says what the baseline is
built from: the past years it reads, the permit columns, the data release and
its date, the information cutoff, the five projected years and the gap year
2026, a 2026 nowcast (diagnostic only), Pennsylvania's window sensitivity
(``baseline_pa_window_2022_2025``, disclosed, never subtracted) and the state
reconciliation (the shipped rule applied to the state's own yearly totals).

:func:`card_view` turns that card into an ordered list of **rows**. Each row has
a label, the text to show (every number already written out here, on the
server), and where it comes from: the formula, the source ids and the
assumption ids of the card entry behind it. The reconciliation row compares the
card's state component with the baseline's own middle value (the run's
``baseline`` record) and says "matched" only when they are equal to the unit.

The screens (State and Method) and the hearing brief (``report.hearing``) all
read these rows, so they always say the same thing.

Static site: :func:`write_static` writes ``<ST>/baseline-card.json`` next to an
exported ``<ST>/summary.json`` (light: the card file and the saved summary only,
no engine run). ``python -m visionhousing.api.baseline_view --data web/public/data``
refreshes every exported state.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = ["VIEW_VERSION", "card_view", "main", "view_for", "write_static"]

VIEW_VERSION = 1
"""Version of the row layout (bumped when a row changes meaning)."""

DASH = "\u2013"
MINUS = "\u2212"


def _n(x: Any) -> str:
    """A count of homes with commas (whole units)."""
    v = float(x)
    s = f"{abs(v):,.0f}"
    return f"{MINUS}{s}" if v < 0 and s != "0" else s


def _span(pair: Any) -> str:
    if not pair:
        return ""
    items = list(pair)
    a, b = items[0], items[-1]
    return str(a) if a == b else f"{a}{DASH}{b}"


def _row(
    rid: str,
    label: str,
    text: str,
    *,
    kind: str = "fact",
    formula: str = "",
    sources: Any = (),
    assumption_ids: Any = (),
    status: str = "",
) -> dict[str, Any]:
    return {
        "id": rid,
        "label": label,
        "text": text,
        "kind": kind,
        "status": status,
        "formula": formula,
        "sources": list(sources or []),
        "assumption_ids": list(assumption_ids or []),
    }


def _baseline_p50(baseline: Mapping[str, Any] | None) -> float | None:
    if not baseline:
        return None
    v = baseline.get("p50")
    return None if v is None else float(v)


def card_view(
    card: Mapping[str, Any] | None, baseline: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """The card's rows, in display order, with every number written out.

    Args:
        card: ``visionhousing.baseline.card.card_for(state)`` (``None`` gives ``None``).
        baseline: The run's state ``baseline`` record (a dict with ``p50``), for the
            reconciliation row; ``None`` leaves the comparison out.

    Returns:
        ``{"version", "state", "rows", "reconciliation", "source"}`` or ``None``.
    """
    if not card:
        return None
    rows: list[dict[str, Any]] = []
    release = dict(card.get("data_release") or {})
    years = dict(card.get("historical_years") or {})
    srcs = list(card.get("sources") or [release.get("source", "bps_state_annual")])
    ids = list(card.get("assumption_ids") or [])

    parts = []
    if years.get("level_years"):
        parts.append(f"level from {_span(years['level_years'])} permits")
    if years.get("share_years"):
        parts.append(f"county shares from {_span(years['share_years'])}")
    if years.get("backtest_years"):
        parts.append(f"rules tested on {_span(years['backtest_years'])}")
    rule = str(card.get("rule_text") or "")
    rows.append(
        _row(
            "historical_years",
            "Historical years used",
            "; ".join(parts) + (f". Rule: {rule}." if rule else "."),
            sources=srcs,
            assumption_ids=ids,
        )
    )
    fields = list(card.get("unit_fields") or [])
    rows.append(
        _row(
            "unit_fields",
            "Permit columns counted",
            f"{card.get('unit_fields_label', '')} ({', '.join(fields)}). "
            "The reported-only columns are not used.",
            sources=srcs,
        )
    )
    if release:
        rows.append(
            _row(
                "data_release",
                "Data release",
                f"{release.get('vintage', '')}, posted {release.get('release_date', '')} "
                f"({release.get('release_date_basis', '')}).",
                sources=[release.get("source", "")],
            )
        )
    rows.append(
        _row(
            "information_cutoff",
            "Information cutoff",
            f"{card.get('information_cutoff', '')}: nothing released after this date is used "
            f"({card.get('information_cutoff_basis', '')}).",
        )
    )
    bridge = dict(card.get("bridge") or {})
    gap = _span(card.get("forecast_gap"))
    rows.append(
        _row(
            "forecast_window",
            "Projected years and gap",
            f"{_span(card.get('forecast_window'))}; gap year {gap}. "
            f"{card.get('forecast_gap_text', '')} {bridge.get('method_text', '')}".strip(),
            kind="fact",
        )
    )
    nc = dict(bridge.get("nowcast") or {})
    st = nc.get("state") or None
    if nc and st:
        rows.append(
            _row(
                nc.get("id", "baseline_bridge_nowcast_2026"),
                f"{nc.get('year', '')} nowcast (diagnostic only)",
                f"{_n(st['low'])} to {_n(st['high'])} homes, middle {_n(st['mid'])}, from "
                f"{_n(st['ytd'])} permits in January{DASH}"
                f"{_month(nc.get('through_month'))} (file {nc.get('file', '')}, posted "
                f"{nc.get('release_date', '')}); the baseline carries "
                f"{_n(st['carried_annual'])} a year through the gap. No estimate uses the nowcast.",
                kind="diagnostic",
                status="diagnostic only",
                formula=str(st.get("formula", "")),
                sources=st.get("sources", ()),
                assumption_ids=st.get("assumption_ids", ()),
            )
        )
    elif nc:
        rows.append(
            _row(
                nc.get("id", "baseline_bridge_nowcast_2026"),
                f"{nc.get('year', '')} nowcast (diagnostic only)",
                f"No nowcast for this state ({nc.get('status', 'not available')}).",
                kind="diagnostic",
                status="not available",
            )
        )
    for did, d in sorted((card.get("diagnostics") or {}).items()):
        pct = float(d.get("difference_pct", 0.0)) * 100
        pct_s = f"{MINUS if pct < 0 else '+'}{abs(pct):.1f}%"
        rows.append(
            _row(
                did,
                "Window sensitivity (disclosed, never subtracted)",
                f"Five times the {_span(d.get('years'))} mean: {_n(d['five_year'])} homes "
                "against the shipped "
                f"{_n(d['shipped_state_component'])} ({_n(d['difference'])}, {pct_s}). "
                f"{d.get('status', '')}",
                kind="sensitivity",
                status="disclosed sensitivity; record only",
                formula=str(d.get("formula", "")),
                sources=d.get("sources", ()),
                assumption_ids=d.get("assumption_ids", ()),
            )
        )
    recon = _reconciliation(card, baseline)
    rows.append(recon["row"])
    return {
        "version": VIEW_VERSION,
        "state": card.get("state"),
        "rows": rows,
        "reconciliation": {k: v for k, v in recon.items() if k != "row"},
        "source": card.get("source", ""),
    }


def _month(m: Any) -> str:
    import calendar

    try:
        return calendar.month_name[int(m)]
    except (TypeError, ValueError, IndexError):
        return str(m or "")


def _reconciliation(card: Mapping[str, Any], baseline: Mapping[str, Any] | None) -> dict[str, Any]:
    role = str(card.get("role", ""))
    comp = card.get("state_component")
    p50 = _baseline_p50(baseline)
    if role != "reconciled" or comp is None:
        note = str(card.get("note") or "The state series is shown as an outside control only.")
        return {
            "role": role or "control",
            "state_component": None,
            "baseline_p50": p50,
            "matched": None,
            "row": _row(
                "reconciliation",
                "State reconciliation",
                f"Outside control, not reconciled. {note}",
                kind="check",
                status="control",
                sources=card.get("sources", ()),
            ),
        }
    matched = None if p50 is None else round(p50) == round(float(comp))
    if matched is None:
        verdict = "the run's baseline was not loaded, so it is not compared here"
        status = "not compared"
    elif matched:
        verdict = f"matches the baseline middle value {_n(p50)}"
        status = "matched"
    else:
        verdict = f"does NOT match the baseline middle value {_n(p50)}"
        status = "not matched"
    return {
        "role": role,
        "state_component": comp,
        "baseline_p50": p50,
        "matched": matched,
        "row": _row(
            "reconciliation",
            "State reconciliation",
            f"Shipped rule on the state's own yearly totals: {_n(comp)} homes; {verdict}.",
            kind="check",
            status=status,
            formula=str(card.get("formula", "")),
            sources=card.get("sources", ()),
            assumption_ids=card.get("assumption_ids", ()),
        ),
    }


def view_for(state: str, baseline: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """The card of ``state`` (``baseline.card.card_for``) with its rows added under ``view``."""
    from visionhousing.baseline.card import card_for

    card = card_for(state)
    if not card:
        return None
    return {**card, "view": card_view(card, baseline)}


def write_static(data: Path, states: list[str] | None = None) -> list[Path]:
    """Write ``<ST>/baseline-card.json`` for each exported state that has a card.

    The baseline record is read from the state's saved ``summary.json`` (packing undone),
    so the reconciliation row compares against the exported middle value.
    """
    from visionhousing.export_static import read_saved

    data = Path(data)
    written: list[Path] = []
    codes = states or sorted(p.name for p in data.iterdir() if (p / "summary.json").is_file())
    for code in codes:
        summary_path = data / code / "summary.json"
        if not summary_path.is_file():
            continue
        summary = read_saved(summary_path)
        out = view_for(code, (summary or {}).get("baseline"))
        if out is None:
            continue
        path = data / code / "baseline-card.json"
        path.write_text(
            json.dumps(out, indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
        )
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    """``python -m visionhousing.api.baseline_view --data web/public/data [--state PA]``."""
    p = argparse.ArgumentParser(description="Write each exported state's baseline-card.json.")
    p.add_argument("--data", type=Path, default=Path("web/public/data"))
    p.add_argument("--state", action="append", default=None)
    args = p.parse_args(argv)
    for path in write_static(args.data, [s.upper() for s in args.state] if args.state else None):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
