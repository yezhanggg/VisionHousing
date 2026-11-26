"""The grade rule: five component scores in, one letter and one plain sentence out.

Plain-language summary
----------------------
Every weight, threshold and class score lives in ``assumptions/grades.yaml``;
this module only does the arithmetic the file describes. It is pure: the same
inputs always give the same grade.

Each component turns one kind of evidence into a score between 0 and 1:

- **sources**: the mean of the class scores of the number's sources (local
  data scores more than national data, which scores more than a study);
- **backtest**: ``min(1, coverage / target) x min(1, cases / full_cases)``;
- **law**: ``min(1, share read / full_share)``, at least the state-law credit
  when a state-level reading is on file;
- **calibration**: the mean of the status scores of the reform's key parameters;
- **evidence**: the score of the best identification on file.

The weighted mean of the components that apply gives the score; the first
threshold it reaches gives the letter. Each component is non-decreasing in its
input and the weights are fixed per reform, so a better input never lowers a
grade (``tests/test_grades.py`` checks this).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from visionhousing.ledger.loader import ROOT

__all__ = [
    "COMPONENTS",
    "GRADES_PATH",
    "Component",
    "Grade",
    "backtest_score",
    "calibration_score",
    "evidence_score",
    "grade_from_components",
    "law_score",
    "letter",
    "load_rule",
    "source_score",
]

GRADES_PATH = ROOT / "assumptions" / "grades.yaml"
COMPONENTS = ("sources", "backtest", "law", "calibration", "evidence")
"""The five components, in the order a grade lists them."""


@lru_cache(maxsize=4)
def _load(path: str, stamp: int) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    weights = data["weights"]
    if set(weights) != set(COMPONENTS):
        raise ValueError(f"grades.yaml weights must name exactly {COMPONENTS}")
    mins = [float(t["min_score"]) for t in data["thresholds"]]
    if mins != sorted(mins, reverse=True) or mins[-1] != 0.0:
        raise ValueError("grades.yaml thresholds must fall and end at 0")
    return data


def load_rule(path: Path | str | None = None) -> dict[str, Any]:
    """Read ``assumptions/grades.yaml`` (cached until the file changes)."""
    p = Path(path) if path is not None else GRADES_PATH
    return _load(str(p), p.stat().st_mtime_ns)


@dataclass(frozen=True)
class Component:
    """One scored component of a grade.

    Attributes:
        id: Component id (``sources``, ``backtest``, ``law``, ``calibration``, ``evidence``).
        title: Plain title from the rule file.
        score: Score between 0 and 1.
        weight: Weight after scaling over the components that apply.
        phrase: The few words the grade sentence uses for it.
        detail: The inputs that produced the score (shares, counts, ids).
    """

    id: str
    title: str
    score: float
    weight: float
    phrase: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form."""
        return {
            "id": self.id,
            "title": self.title,
            "score": round(self.score, 4),
            "weight": round(self.weight, 4),
            "phrase": self.phrase,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class Grade:
    """A graded number.

    Attributes:
        grade: ``A``, ``B``, ``C`` or ``D``.
        score: Weighted score between 0 and 1.
        components: The components that apply, in rule order.
        sentence: "Grade B: ..." in plain words, built from the component phrases.
        rule_id: Id of the rule file used.
    """

    grade: str
    score: float
    components: tuple[Component, ...]
    sentence: str
    rule_id: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form."""
        return {
            "grade": self.grade,
            "score": round(self.score, 4),
            "sentence": self.sentence,
            "components": [c.to_dict() for c in self.components],
            "rule_id": self.rule_id,
        }


def _clip(x: float) -> float:
    return 0.0 if not math.isfinite(x) else min(1.0, max(0.0, x))


# ---------------------------------------------------------------- component scores


def source_score(classes: Sequence[str], rule: Mapping[str, Any] | None = None) -> float:
    """Mean class score of the number's sources (0 when it names none)."""
    scores = (rule or load_rule())["components"]["sources"]["class_scores"]
    if not classes:
        return 0.0
    return _clip(sum(float(scores[c]) for c in classes) / len(classes))


def backtest_score(
    coverage: float | None, cases: int | None, rule: Mapping[str, Any] | None = None
) -> float:
    """``min(1, coverage / target) x min(1, cases / full_cases)``; 0 when there is no back-test."""
    cfg = (rule or load_rule())["components"]["backtest"]
    if coverage is None or not cases:
        return 0.0
    hit = min(1.0, float(coverage) / float(cfg["target_coverage"]))
    depth = min(1.0, float(cases) / float(cfg["full_cases"]))
    return _clip(hit * depth)


def law_score(
    share: float | None, state_law: bool = False, rule: Mapping[str, Any] | None = None
) -> float:
    """``min(1, share / full_share)``, at least the state-law credit when a state reading exists."""
    cfg = (rule or load_rule())["components"]["law"]
    base = 0.0 if share is None else min(1.0, float(share) / float(cfg["full_share"]))
    if state_law:
        base = max(base, float(cfg["state_law_credit"]))
    return _clip(base)


def calibration_score(statuses: Sequence[str], rule: Mapping[str, Any] | None = None) -> float:
    """Mean status score of the reform's key parameters (0 when none is listed)."""
    scores = (rule or load_rule())["components"]["calibration"]["status_scores"]
    if not statuses:
        return 0.0
    return _clip(sum(float(scores[s]) for s in statuses) / len(statuses))


def evidence_score(best: str, rule: Mapping[str, Any] | None = None) -> float:
    """Score of the best identification on file (``causal`` ... ``none``)."""
    return _clip(
        float((rule or load_rule())["components"]["evidence"]["identification_scores"][best])
    )


# ---------------------------------------------------------------- the letter


def applies(key: str, rule: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    """Components that apply to ``key`` (a reform key or ``baseline``), in rule order."""
    cfg = (rule or load_rule())["applies"]
    wanted = set(cfg.get(key, cfg["default"]))
    return tuple(c for c in COMPONENTS if c in wanted)


def letter(score: float, rule: Mapping[str, Any] | None = None) -> str:
    """The first grade whose minimum ``score`` reaches."""
    for t in (rule or load_rule())["thresholds"]:
        if score >= float(t["min_score"]) - 1e-12:
            return str(t["grade"])
    return str((rule or load_rule())["thresholds"][-1]["grade"])


def grade_from_components(
    key: str,
    scores: Mapping[str, float],
    phrases: Mapping[str, str] | None = None,
    details: Mapping[str, Mapping[str, Any]] | None = None,
    rule: Mapping[str, Any] | None = None,
) -> Grade:
    """Weight the component scores that apply to ``key`` and cut the letter.

    Args:
        key: Reform key, or ``baseline``.
        scores: Component id to score (components that do not apply are ignored).
        phrases: Component id to its words for the sentence.
        details: Component id to the inputs behind its score.
        rule: The rule (default: ``assumptions/grades.yaml``).

    Returns:
        The grade, with its components and sentence.
    """
    rule = rule or load_rule()
    used = applies(key, rule)
    total_w = sum(float(rule["weights"][c]) for c in used)
    comps = []
    score = 0.0
    for c in used:
        w = float(rule["weights"][c]) / total_w
        s = _clip(float(scores.get(c, 0.0)))
        score += w * s
        comps.append(
            Component(
                id=c,
                title=str(rule["components"][c]["title"]),
                score=s,
                weight=w,
                phrase=(phrases or {}).get(c, ""),
                detail=(details or {}).get(c, {}),
            )
        )
    g = letter(score, rule)
    words = [c.phrase for c in comps if c.phrase]
    sentence = f"Grade {g}: " + "; ".join(words) + "." if words else f"Grade {g}."
    return Grade(g, _clip(score), tuple(comps), sentence, str(rule["id"]))
