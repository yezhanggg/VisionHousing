"""Read and check the scenario file, ``assumptions/scenarios.yaml``.

A scenario is a named set of reforms to compare with the baseline: the five
benchmark reforms in the Challenge Handbook's own words, and two Pennsylvania
bills. A scenario says which reforms it turns on, which kinds of lots it reaches,
which places it leaves out, and which ledger values it changes.

The checks make sure every override names a real ledger parameter and is itself
valid, so a scenario can never point at a number that does not exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from visionhousing.ledger.loader import (
    ROOT,
    Ledger,
    LedgerError,
    Override,
    apply_overrides,
    describe_errors,
    read_yaml,
)
from visionhousing.ledger.schema import Source

DEFAULT_PATH = ROOT / "assumptions" / "scenarios.yaml"

Reform = Literal[
    "adu",
    "missing_middle",
    "tod",
    "parking",
    "fees",
    "transit_zoning_funded_infrastructure",
    "commercial_land_housing",
    "adaptive_reuse",
    "starter_home_lots",
    "fee_deferral_with_affordability",
]
BuildingType = Literal["sf_detached", "sf_attached", "two_to_four", "five_plus"]


class ExcludedPlace(BaseModel):
    """A place a scenario does not reach, and why."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Scenario(BaseModel):
    """One scenario to compare with the baseline.

    Attributes:
        title: Short name shown to users.
        kind: ``baseline``, a Handbook ``benchmark``, a ``state`` scenario (a bill),
            or an ``extra`` scenario (a named policy beyond the benchmarks; the
            benchmark presets stay unchanged).
        priority: Build priority: ``must``, ``should`` or ``could``.
        handbook_text: The Handbook's exact wording (benchmark and baseline only).
        description: Plain-language summary.
        reforms: Reforms this scenario turns on.
        building_types: Kinds of existing lots the reform reaches (empty if none).
        excluded_places: Places the scenario leaves out.
        overrides: Ledger values this scenario changes.
        source: Where the scenario's rules come from.
        notes: Modelling choices worth stating.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)
    kind: Literal["baseline", "benchmark", "state", "extra"]
    priority: Literal["must", "should", "could"]
    handbook_text: str | None = None
    description: str = Field(min_length=1)
    reforms: tuple[Reform, ...] = ()
    building_types: tuple[BuildingType, ...] = ()
    excluded_places: tuple[ExcludedPlace, ...] = ()
    overrides: dict[str, float | str | dict[str, Any]] = Field(default_factory=dict)
    source: tuple[Source, ...] = Field(min_length=1)
    notes: tuple[str, ...] = ()


def parse_scenarios(data: Any, ledger: Ledger, name: str = "scenarios.yaml") -> dict[str, Scenario]:
    """Check parsed scenario data against the ledger.

    Args:
        data: The parsed YAML: a mapping with ``scenarios``.
        ledger: The ledger the overrides refer to.
        name: File name used in error messages.

    Returns:
        Scenario id to its checked entry.

    Raises:
        LedgerError: A scenario is malformed or an override is invalid.
    """
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), dict):
        raise LedgerError(f"{name}: expected a top-level 'scenarios' mapping")
    problems: list[str] = []
    out: dict[str, Scenario] = {}
    for sid, entry in data["scenarios"].items():
        try:
            scenario = Scenario.model_validate(entry)
        except ValidationError as exc:
            problems.append(f"scenario '{sid}': {describe_errors(exc)}")
            continue
        if scenario.kind == "baseline" and scenario.reforms:
            problems.append(f"scenario '{sid}': the baseline turns on no reforms")
        if scenario.kind in ("baseline", "benchmark") and not scenario.handbook_text:
            problems.append(f"scenario '{sid}': needs the Handbook's wording")
        try:
            apply_overrides(ledger, scenario.overrides)
        except LedgerError as exc:
            problems.append(f"scenario '{sid}': {exc}")
        out[sid] = scenario
    if problems:
        raise LedgerError(f"{name}: " + " | ".join(problems))
    return out


def load_scenarios(ledger: Ledger, path: Path | str | None = None) -> dict[str, Scenario]:
    """Load and check the scenario file.

    Args:
        ledger: The ledger the overrides refer to.
        path: File to read. Defaults to ``assumptions/scenarios.yaml``.

    Returns:
        Scenario id to its checked entry.
    """
    path = Path(path) if path is not None else DEFAULT_PATH
    return parse_scenarios(read_yaml(path), ledger, name=path.name)


def scenario_ledger(ledger: Ledger, scenario: Scenario) -> Ledger:
    """Return the ledger with a scenario's overrides applied.

    Args:
        ledger: The base ledger.
        scenario: The scenario.

    Returns:
        A new ledger.
    """
    overrides: dict[str, Override] = dict(scenario.overrides)
    return apply_overrides(ledger, overrides)
