"""Settings of the comparisons: dials, targets, the matrix and the data tiers.

Plain-language summary
----------------------
Two files hold every choice the comparisons make, so no number is typed into
the code:

- ``assumptions/comparisons.yaml``: the stated ordering for splitting the
  package among reforms, the names of the mechanisms, the policy dials and
  their levels, the readout thresholds and targets, the adoption search, the
  two-variable matrix and the ranking-stability rule.
- ``assumptions/data_tiers.yaml``: which inputs are national (tier 0),
  state-level (tier 1) or local (tier 2), and what the engine does when each
  is missing.

This module reads both files, checks them (every dial has a benchmark level,
every level changes only its own reform, every tier is 0-2) and returns plain
frozen objects. A mistake in a file stops the run with a message that names it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from visionhousing.ledger import LedgerError
from visionhousing.ledger.loader import ROOT, read_yaml

__all__ = [
    "COMPARISONS_PATH",
    "DATA_TIERS_PATH",
    "EFFECTS",
    "Comparisons",
    "DataInput",
    "DataTiers",
    "Dial",
    "Level",
    "load_comparisons",
    "load_data_tiers",
    "parse_comparisons",
    "parse_data_tiers",
]

COMPARISONS_PATH = ROOT / "assumptions" / "comparisons.yaml"
DATA_TIERS_PATH = ROOT / "assumptions" / "data_tiers.yaml"

REFORMS = ("adu", "missing_middle", "tod", "parking", "fees")
"""The five benchmark reforms (registry keys)."""

CACHES = ("late_stage", "reform")
"""How a dial level is worked out (see ``engine.comparisons.dials``)."""

EFFECTS = (
    "law_hooks_off",
    "transfer_widening_on",
    "walksheds_off",
    "state_table_off",
    "pay_county_mean",
    "pay_national",
)
"""What masking a data input does (see ``engine.comparisons.ablation``)."""


@dataclass(frozen=True)
class Level:
    """One level of a policy dial.

    Attributes:
        key: Level id inside its dial (for example ``"l75"``).
        x: Its place on the dial's axis.
        label: Plain words.
        options: Reform options it sets.
        overrides: Ledger entries it pins (policy definitions).
    """

    key: str
    x: float
    label: str
    options: Mapping[str, Any] = field(default_factory=dict)
    overrides: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_plain(self) -> bool:
        """True when the level changes nothing (the benchmark preset)."""
        return not self.options and not self.overrides


@dataclass(frozen=True)
class Dial:
    """One policy dial: a reform, its levels (least to most generous) and its benchmark."""

    key: str
    reform: str
    title: str
    unit: str
    cache: str
    benchmark: str
    levels: tuple[Level, ...]

    def level(self, key: str) -> Level:
        """The level with this id."""
        for lv in self.levels:
            if lv.key == key:
                return lv
        raise KeyError(f"dial '{self.key}' has no level '{key}'")

    @property
    def ids(self) -> tuple[str, ...]:
        """Ids a record of this dial cites."""
        return (f"comparisons.dials.{self.key}",)


@dataclass(frozen=True)
class Comparisons:
    """``assumptions/comparisons.yaml``, checked."""

    ordering: tuple[str, ...]
    zero_homes: float
    mechanisms: Mapping[str, tuple[str, str]]
    dials: Mapping[str, Dial]
    diminishing_step_share: float
    targets: tuple[float, ...]
    adoption_parameter: str
    adoption_max_multiplier: float
    adoption_tolerance: float
    matrix_reform: str
    matrix_takeup: tuple[str, tuple[float, ...]]
    matrix_cost: tuple[str, tuple[float, ...]]
    ranking_top: int


@dataclass(frozen=True)
class DataInput:
    """One data input of the tier table."""

    key: str
    tier: int
    title: str
    effect: str
    fallback: str
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataTiers:
    """``assumptions/data_tiers.yaml``, checked."""

    tiers: Mapping[int, str]
    inputs: Mapping[str, DataInput]
    hurt_rule: str
    still_shift: float

    def masked_at(self, tier: int) -> tuple[str, ...]:
        """Inputs masked when running at ``tier`` (every input above it)."""
        if tier not in self.tiers:
            raise ValueError(f"data tier must be one of {sorted(self.tiers)}, got {tier!r}")
        return tuple(k for k, v in self.inputs.items() if v.tier > tier)


def _words(value: Any) -> str:
    return " ".join(str(value or "").split())


def parse_comparisons(data: Any) -> Comparisons:
    """Check parsed ``comparisons.yaml`` data and build the settings.

    Raises:
        LedgerError: A field is missing or does not make sense.
    """
    if not isinstance(data, dict):
        raise LedgerError("comparisons.yaml: expected a mapping")
    problems: list[str] = []
    inter = data.get("interactions") or {}
    ordering = tuple(str(k) for k in inter.get("ordering") or ())
    if sorted(ordering) != sorted(REFORMS):
        problems.append(f"interactions.ordering must list each of {REFORMS} once")
    mechanisms = {}
    for name, raw in (inter.get("mechanisms") or {}).items():
        if not isinstance(raw, dict) or not raw.get("id") or not _words(raw.get("words")):
            problems.append(f"mechanism '{name}' needs an id and words")
            continue
        mechanisms[str(name)] = (str(raw["id"]), _words(raw["words"]))
    dials: dict[str, Dial] = {}
    for key, raw in (data.get("dials") or {}).items():
        if not isinstance(raw, dict):
            problems.append(f"dial '{key}': not a mapping")
            continue
        reform = str(raw.get("reform", ""))
        if reform not in REFORMS:
            problems.append(f"dial '{key}': reform must be one of {REFORMS}")
        cache = str(raw.get("cache", ""))
        if cache not in CACHES:
            problems.append(f"dial '{key}': cache must be one of {CACHES}")
        levels = []
        for lk, lv in (raw.get("levels") or {}).items():
            if not isinstance(lv, dict) or "x" not in lv or not _words(lv.get("label")):
                problems.append(f"dial '{key}' level '{lk}' needs x and a label")
                continue
            overrides = dict(lv.get("overrides") or {})
            bad = [p for p in overrides if not str(p).startswith(f"{reform}.")]
            if bad:
                problems.append(f"dial '{key}' level '{lk}' changes another reform's entries {bad}")
            levels.append(
                Level(
                    str(lk),
                    float(lv["x"]),
                    _words(lv["label"]),
                    dict(lv.get("options") or {}),
                    overrides,
                )
            )
        xs = [lv.x for lv in levels]
        if len(levels) < 2 or xs != sorted(xs) or len(set(xs)) != len(xs):
            problems.append(f"dial '{key}': needs two or more levels with rising x")
        bench = str(raw.get("benchmark", ""))
        if bench not in {lv.key for lv in levels}:
            problems.append(f"dial '{key}': benchmark '{bench}' is not one of its levels")
        elif not next(lv for lv in levels if lv.key == bench).is_plain:
            problems.append(f"dial '{key}': the benchmark level must change nothing")
        dials[str(key)] = Dial(
            str(key),
            reform,
            _words(raw.get("title")),
            _words(raw.get("unit")),
            cache,
            bench,
            tuple(levels),
        )
    readouts = data.get("readouts") or {}
    share = float((readouts.get("diminishing_step_share") or {}).get("value", -1))
    if not 0 < share < 1:
        problems.append("readouts.diminishing_step_share.value must be between 0 and 1")
    targets = tuple(float(t) for t in (readouts.get("targets") or {}).get("homes") or ())
    if not targets or any(t <= 0 for t in targets) or list(targets) != sorted(targets):
        problems.append("readouts.targets.homes must be rising positive numbers")
    adoption = data.get("adoption") or {}
    matrix = data.get("matrix") or {}

    def need(block: Mapping[str, Any], name: str, key: str) -> Any:
        # Every setting lives in the file: a missing one is an error, never a code default.
        if block.get(key) in (None, ""):
            problems.append(f"{name}.{key} is missing")
            return None
        return block[key]

    zero = need(inter, "interactions", "zero_homes")
    a_param = need(adoption, "adoption", "parameter")
    a_max = need(adoption, "adoption", "max_multiplier")
    a_tol = need(adoption, "adoption", "tolerance_homes")
    m_reform = need(matrix, "matrix", "reform")
    if a_max is not None and not float(a_max) > 1:
        problems.append("adoption.max_multiplier must be above 1")
    if a_tol is not None and not float(a_tol) > 0:
        problems.append("adoption.tolerance_homes must be positive")

    def axis(name: str) -> tuple[str, tuple[float, ...]]:
        raw = matrix.get(name) or {}
        mult = tuple(float(m) for m in raw.get("multipliers") or ())
        if not raw.get("parameter") or not mult or any(m < 0 for m in mult) or 1.0 not in mult:
            problems.append(f"matrix.{name} needs a parameter and multipliers including 1")
        return str(raw.get("parameter", "")), mult

    out_takeup, out_cost = axis("takeup"), axis("cost")
    top = int((data.get("ranking") or {}).get("top_per_reform", 0))
    if top < 1:
        problems.append("ranking.top_per_reform must be at least 1")
    if problems:
        raise LedgerError("comparisons.yaml: " + " | ".join(problems))
    return Comparisons(
        ordering=ordering,
        zero_homes=float(zero),
        mechanisms=mechanisms,
        dials=dials,
        diminishing_step_share=share,
        targets=targets,
        adoption_parameter=str(a_param),
        adoption_max_multiplier=float(a_max),
        adoption_tolerance=float(a_tol),
        matrix_reform=str(m_reform),
        matrix_takeup=out_takeup,
        matrix_cost=out_cost,
        ranking_top=top,
    )


def parse_data_tiers(data: Any) -> DataTiers:
    """Check parsed ``data_tiers.yaml`` data and build the tier table.

    Raises:
        LedgerError: A field is missing, a tier is not 0-2 or an effect is unknown.
    """
    if not isinstance(data, dict):
        raise LedgerError("data_tiers.yaml: expected a mapping")
    problems: list[str] = []
    tiers = {int(k): _words(v) for k, v in (data.get("tiers") or {}).items()}
    if sorted(tiers) != [0, 1, 2]:
        problems.append("tiers must be 0, 1 and 2")
    inputs = {}
    for key, raw in (data.get("inputs") or {}).items():
        if not isinstance(raw, dict):
            problems.append(f"input '{key}': not a mapping")
            continue
        tier = int(raw.get("tier", -1))
        if tier not in (1, 2):
            problems.append(f"input '{key}': tier must be 1 or 2 (tier 0 is never masked)")
        effect = str(raw.get("effect", ""))
        if effect not in EFFECTS:
            problems.append(f"input '{key}': effect must be one of {EFFECTS}")
        inputs[str(key)] = DataInput(
            str(key),
            tier,
            _words(raw.get("title")),
            effect,
            _words(raw.get("fallback")),
            tuple(str(s) for s in raw.get("sources") or ()),
        )
    measures = data.get("measures") or {}
    rule = _words(measures.get("hurt_rule"))
    if not rule:
        problems.append("measures.hurt_rule is missing")
    still = measures.get("still_shift")
    if still is None or not 0 <= float(still) < 1:
        problems.append("measures.still_shift must be a share from 0 to 1")
    if problems:
        raise LedgerError("data_tiers.yaml: " + " | ".join(problems))
    return DataTiers(tiers=tiers, inputs=inputs, hurt_rule=rule, still_shift=float(still))


@lru_cache(maxsize=4)
def _comparisons(path: str, mtime: int) -> Comparisons:
    return parse_comparisons(read_yaml(Path(path)))


@lru_cache(maxsize=4)
def _tiers(path: str, mtime: int) -> DataTiers:
    return parse_data_tiers(read_yaml(Path(path)))


def load_comparisons(path: Path | str | None = None) -> Comparisons:
    """Read and check ``assumptions/comparisons.yaml`` (or another file)."""
    p = Path(path) if path is not None else COMPARISONS_PATH
    return _comparisons(str(p), p.stat().st_mtime_ns)


def load_data_tiers(path: Path | str | None = None) -> DataTiers:
    """Read and check ``assumptions/data_tiers.yaml`` (or another file)."""
    p = Path(path) if path is not None else DATA_TIERS_PATH
    return _tiers(str(p), p.stat().st_mtime_ns)
