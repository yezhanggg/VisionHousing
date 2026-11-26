"""Grade the headline numbers of a run: each reform's state figure and each county's.

Plain-language summary
----------------------
:func:`grade_number` grades one number: it gathers the five inputs
(``grades.inputs``), scores them by the published rule (``grades.rule``,
``assumptions/grades.yaml``) and writes the plain sentence, for example
"Grade B: national data plus published studies; past ranges caught 85% of 60
back-test cases across this state's counties; local rules unread; lot fit
measured in Pennsylvania, take-up set by judgement; reform tested elsewhere
(causal design)".

:func:`grade_run` does it for every reform in a run (and the baseline), at the
state level or for every county. All words and numbers come from the run and
the files; nothing is typed in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from visionhousing.explain.wording import STATE_NAMES
from visionhousing.grades import inputs as gi
from visionhousing.grades.rule import (
    Grade,
    backtest_score,
    calibration_score,
    evidence_score,
    grade_from_components,
    law_score,
    load_rule,
    source_score,
)

__all__ = ["grade_number", "grade_run"]


def _pct(x: float) -> str:
    return f"{round(100 * x):d}%"


def _join(words: Sequence[str]) -> str:
    words = list(words)
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _state_name(code: str) -> str:
    return STATE_NAMES.get(code, code)


def _source_phrase(classes: Sequence[tuple[str, str]], rule: Mapping[str, Any]) -> str:
    words = rule["components"]["sources"]["words"]
    present = {c for _, c in classes}
    data = [words[c] for c in ("local", "multi_state", "national") if c in present]
    parts = [f"{_join(data)} data"] if data else []
    if "study" in present:
        parts.append(words["study"])
    if "not_in_pack" in present:
        n = sum(1 for _, c in classes if c == "not_in_pack")
        parts.append(f"{n} {words['not_in_pack']}")
    return " plus ".join(parts) if parts else words["none"]


def _backtest_phrase(row: Mapping[str, Any] | None, county: bool) -> str:
    if not row or row.get("coverage") is None or not row.get("cases"):
        return "no back-test for places like this"
    where = f"in counties like this ({row['group']})" if county else "across this state's counties"
    return f"past ranges caught {_pct(row['coverage'])} of {row['cases']:,} back-test cases {where}"


def _law_phrase(law: Mapping[str, Any], state_law: str | None) -> str:
    unit = "single-family lots" if law["measure"] == "lots" else "residents"
    if law["share"] is None or law["share"] <= 0:
        text = "local rules unread"
    else:
        text = f"local rules read for {_pct(law['share'])} of {unit}"
    return text + (", state law read" if state_law else "")


def _calibration_phrase(rows: Sequence[Mapping[str, Any]], state: str) -> str:
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(r["status"], []).append(r["label"])
    parts = []
    if by.get("local"):
        parts.append(f"{_join(by['local'])} measured in {_state_name(state)}")
    if by.get("transferred"):
        wheres = sorted({_state_name(r["where"]) for r in rows if r["status"] == "transferred"})
        parts.append(f"{_join(by['transferred'])} borrowed from {_join(wheres)}")
    if by.get("sourced"):
        parts.append(f"{_join(by['sourced'])} from published figures")
    if by.get("judgement"):
        parts.append(f"{_join(by['judgement'])} set by judgement")
    return ", ".join(parts) if parts else "no key parameter listed"


def grade_number(
    ctx: Any,
    result: Any,
    key: str,
    county: str | None = None,
    *,
    folder: Path | None = None,
    backtest: Mapping[str, Any] | None = None,
    rule: Mapping[str, Any] | None = None,
) -> Grade:
    """Grade one headline number.

    Args:
        ctx: The state context the run used.
        result: The engine ``Result``.
        key: A reform key in the run, or ``"baseline"``.
        county: A county id for the county figure; ``None`` for the state headline.
        folder: The state's pack folder (for the manifest and the back-test tables).
        backtest: :func:`grades.inputs.backtest_rows` of this run (worked out when ``None``).
        rule: The rule (default: ``assumptions/grades.yaml``).

    Returns:
        The grade with its component scores, inputs and sentence.
    """
    rule = rule or load_rule()
    comp = rule["components"]
    state = str(ctx.state)
    if key == "baseline":
        record = result.baseline if county is None else result.baseline_counties[county]
    else:
        outcome = result.reforms[key]
        record = outcome.added if county is None else outcome.counties[county]
    scores: dict[str, float] = {}
    phrases: dict[str, str] = {}
    details: dict[str, dict[str, Any]] = {}

    classes = gi.source_classes(record.source_ids, gi.pack_sources(folder))
    scores["sources"] = source_score([c for _, c in classes], rule)
    phrases["sources"] = _source_phrase(classes, rule)
    details["sources"] = {"sources": [{"id": s, "class": c} for s, c in classes]}

    bt = backtest if backtest is not None else gi.backtest_rows(ctx, result, folder)
    row = bt["state"] if county is None else bt["counties"].get(str(county))
    scores["backtest"] = backtest_score(
        None if row is None else row["coverage"], None if row is None else row["cases"], rule
    )
    phrases["backtest"] = _backtest_phrase(row, county is not None)
    details["backtest"] = {"grouping": bt.get("grouping"), "row": row}

    measure = comp["law"]["measure"].get(key, comp["law"]["measure"]["default"])
    law = gi.law_inputs(ctx, measure, county)
    state_law = gi.state_law_on_file(state, comp["law"]["law_parameter"].get(key))
    scores["law"] = law_score(law["share"], bool(state_law), rule)
    phrases["law"] = _law_phrase(law, state_law)
    details["law"] = {**law, "state_law": state_law}

    params = comp["calibration"]["parameters"].get(key, {})
    cal = gi.calibration_statuses(state, params, ctx.ledger)
    scores["calibration"] = calibration_score([r["status"] for r in cal], rule)
    phrases["calibration"] = _calibration_phrase(cal, state)
    details["calibration"] = {"parameters": cal}

    ptype = comp["evidence"]["policy_type"].get(key, key)
    ev = gi.best_evidence(ptype, comp["evidence"]["identification_scores"])
    scores["evidence"] = evidence_score(ev["best"], rule)
    phrases["evidence"] = comp["evidence"]["words"][ev["best"]]
    details["evidence"] = ev

    return grade_from_components(key, scores, phrases, details, rule)


def grade_run(
    ctx: Any,
    result: Any,
    level: str = "state",
    *,
    folder: Path | None = None,
    keys: Sequence[str] | None = None,
    counties: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Grade every headline number of a run at one level.

    Args:
        ctx: The state context.
        result: The engine ``Result``.
        level: ``"state"`` (each reform's state headline and the baseline) or
            ``"county"`` (the same for every county).
        folder: The state's pack folder.
        keys: Reform keys to grade (default: every reform in the run), plus the baseline.
        counties: Only these counties (``level="county"``; default every county).

    Returns:
        ``{"rule": {...}, "level", "state", "grades": {key: grade}}`` for the
        state, or ``{..., "counties": {fips: {key: grade}}}`` for counties.
    """
    if level not in ("state", "county"):
        raise ValueError("level must be 'state' or 'county'")
    rule = load_rule()
    wanted = ["baseline", *(keys if keys is not None else list(result.reforms))]
    bt = gi.backtest_rows(ctx, result, folder)
    out: dict[str, Any] = {
        "state": ctx.state,
        "synthetic": bool(getattr(ctx, "synthetic", False)),
        "level": level,
        "rule": {
            "id": rule["id"],
            "file": "assumptions/grades.yaml",
            "thresholds": rule["thresholds"],
            "weights": rule["weights"],
            "method": "docs/modules/grades.md",
        },
        "note": (
            "A grade says how much evidence stands behind a number: source quality, the "
            "back-test, local law read, local or borrowed calibration, and outside evidence for "
            "the reform. It is not a probability and does not change any range."
        ),
    }
    if level == "state":
        out["grades"] = {
            k: grade_number(ctx, result, k, folder=folder, backtest=bt, rule=rule).to_dict()
            for k in wanted
        }
        return out
    ids = [str(c) for c in (counties if counties is not None else result.county_ids)]
    out["counties"] = {
        c: {
            k: grade_number(ctx, result, k, c, folder=folder, backtest=bt, rule=rule).to_dict()
            for k in wanted
        }
        for c in ids
    }
    return out
