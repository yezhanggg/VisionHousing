"""Request shapes and limits for the interface endpoints.

Plain-language summary
----------------------
Every request that asks for a run (``POST /run``, ``POST /compare``, the county
panel and the exports) describes one *scenario*: a state, the reforms switched
on, any assumption overrides, a preset, a seed and a number of draws. This
module checks that description before the engine sees it:

- the state is a two-letter code;
- at most 8 reforms and 40 overrides, each id with a fixed pattern;
- an override is a number, a range end (``low`` / ``central`` / ``high``, or the
  link codes ``l`` / ``c`` / ``h``), or a small mapping of ``low`` / ``central`` /
  ``high`` / ``shape``;
- the preset is ``low``, ``central`` or ``high``. It chooses the baseline's
  housing cycle (``weak``, ``typical`` or ``strong``), the engine's own
  scenario-level switch. Single assumptions are pinned with overrides;
- the seed is a non-negative whole number and the draws are capped at 4,000.

Anything else is refused with a 400 and a plain message. The checks are about
shape and size only. Whether an id exists in the ledger or the reform registry
is the engine's question, and its answer is passed back unchanged.

:func:`scenario_from_query` reads the same scenario from a web address, in the
front end's link format (``s``, ``r``, ``a=id~l,id~0.5``, plus ``seed``,
``draws`` and ``preset``), so a shared link and an export ask for the same run.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from visionhousing.ledger import DEFAULT_DRAWS

__all__ = [
    "LIMITS",
    "PRESET_CYCLE",
    "CompareRequest",
    "RunRequest",
    "ScenarioError",
    "ScenarioItem",
    "scenario_from_query",
]

LIMITS = {
    "body_bytes": 64 * 1024,
    "reforms": 8,
    "overrides": 40,
    "draws": 4000,
    "scenarios": 6,
    "option_keys": 8,
    "query_chars": 4096,
    "search_chars": 100,
}
"""Request limits. They match the front end's link limits (``web/js/state/codec.js``)."""

PRESET_CYCLE = {"low": "weak", "central": "typical", "high": "strong"}
"""Preset to the baseline housing cycle it selects."""

CHOICES = {
    "l": "low",
    "c": "central",
    "h": "high",
    "low": "low",
    "central": "central",
    "high": "high",
}
"""Accepted range-end words, including the link codes, to the ledger's own words."""

STATE = re.compile(r"^[A-Z]{2}$")
REFORM = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PARAM = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+){1,3}$")
NUMBER = re.compile(r"^-?\d{1,9}(?:\.\d{1,6})?$")

ReformKey = Annotated[str, StringConstraints(pattern=REFORM.pattern, max_length=32)]
ParamId = Annotated[str, StringConstraints(pattern=PARAM.pattern, max_length=64)]
OptionValue = Annotated[str, StringConstraints(max_length=64)] | int | float | bool
RANGE_FIELDS = frozenset({"low", "central", "high", "shape"})


class ScenarioError(ValueError):
    """A scenario in a web address is malformed."""


def _finite(value: float, what: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{what} must be a finite number")
    return value


def _override(pid: str, value: Any) -> float | str | dict[str, Any]:
    """Check one override and return it in the ledger's own form."""
    if isinstance(value, bool):
        raise ValueError(f"override '{pid}': expected a number or low / central / high")
    if isinstance(value, int | float):
        return _finite(float(value), f"override '{pid}'")
    if isinstance(value, str):
        if value in CHOICES:
            return CHOICES[value]
        raise ValueError(f"override '{pid}': expected a number or low / central / high")
    if isinstance(value, Mapping):
        unknown = sorted(set(map(str, value)) - RANGE_FIELDS)
        if unknown or not value:
            raise ValueError(f"override '{pid}': a mapping may only set {sorted(RANGE_FIELDS)}")
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key == "shape":
                if item not in ("pert", "triangular", "fixed"):
                    raise ValueError(f"override '{pid}': shape must be pert, triangular or fixed")
                out[key] = item
            elif isinstance(item, int | float) and not isinstance(item, bool):
                out[key] = _finite(float(item), f"override '{pid}.{key}'")
            else:
                raise ValueError(f"override '{pid}.{key}': expected a number")
        return out
    raise ValueError(f"override '{pid}': unsupported value")


class _Scenario(BaseModel):
    """The run fields every scenario shares (reforms, overrides, preset)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reforms: (
        list[ReformKey]
        | dict[
            ReformKey, dict[Annotated[str, StringConstraints(max_length=32)], OptionValue] | None
        ]
    ) = Field(default_factory=list)
    overrides: dict[ParamId, Any] = Field(default_factory=dict)
    preset: Literal["low", "central", "high"] = "central"

    @field_validator("reforms")
    @classmethod
    def _few_reforms(cls, reforms: Any) -> Any:
        if len(reforms) > LIMITS["reforms"]:
            raise ValueError(f"at most {LIMITS['reforms']} reforms")
        if isinstance(reforms, list) and len(set(reforms)) != len(reforms):
            raise ValueError("each reform may appear once")
        if isinstance(reforms, dict):
            for key, options in reforms.items():
                if options and len(options) > LIMITS["option_keys"]:
                    raise ValueError(f"reform '{key}': at most {LIMITS['option_keys']} options")
        return reforms

    @field_validator("overrides")
    @classmethod
    def _few_overrides(cls, overrides: dict[str, Any]) -> dict[str, Any]:
        if len(overrides) > LIMITS["overrides"]:
            raise ValueError(f"at most {LIMITS['overrides']} overrides")
        return {pid: _override(pid, value) for pid, value in overrides.items()}

    @property
    def cycle(self) -> str:
        """The baseline housing cycle the preset selects."""
        return PRESET_CYCLE[self.preset]

    def reform_arg(self) -> list[str] | dict[str, dict[str, Any]]:
        """Return the reforms in the form ``engine.run`` takes."""
        if isinstance(self.reforms, dict):
            return {k: dict(v or {}) for k, v in self.reforms.items()}
        return list(self.reforms)


class RunRequest(_Scenario):
    """The body of ``POST /run``: one scenario on one state.

    Attributes:
        state: Two-letter state code (``TL`` = Testland).
        reforms: Reform keys, or key -> options.
        overrides: Ledger id -> number, range end, or a small range mapping.
        preset: ``low`` / ``central`` / ``high``: the baseline housing cycle.
        seed: Random seed (same seed, same numbers).
        draws: Monte Carlo draws, 1 to 4,000.
        explain: Attach the explanation payload (default true; false is quicker).
        drivers: Add each reform's top drivers (the sensitivity tornado) to the
            explanation. Slow on a large state, so off by default.
    """

    state: Annotated[str, StringConstraints(min_length=2, max_length=2)]
    seed: int = Field(default=0, ge=0, le=2**32 - 1)
    draws: int = Field(default=DEFAULT_DRAWS, ge=1, le=LIMITS["draws"])
    explain: bool = True
    drivers: bool = False

    @field_validator("state")
    @classmethod
    def _state_code(cls, state: str) -> str:
        code = state.upper()
        if not STATE.match(code):
            raise ValueError("state must be a two-letter code")
        return code


class ScenarioItem(_Scenario):
    """One column of a comparison: a label and its scenario."""

    label: Annotated[str, StringConstraints(min_length=1, max_length=60)]


class CompareRequest(RunRequest):
    """The body of ``POST /compare``.

    Without ``scenarios`` it compares each reform run alone with the package of
    all of them, from one run. With ``scenarios`` (at most 6) it runs each one on
    the same state, seed and draws; their own reforms, overrides and preset are
    used, and the top-level ones are ignored.
    """

    scenarios: list[ScenarioItem] | None = Field(default=None, max_length=LIMITS["scenarios"])


def _split_overrides(text: str) -> dict[str, str | float]:
    out: dict[str, str | float] = {}
    for item in text.split(","):
        if not item:
            continue
        parts = item.split("~")
        if len(parts) != 2 or not PARAM.match(parts[0]) or len(parts[0]) > 64:
            raise ScenarioError(
                f"bad assumption override {item[:24]!r}; use id~l, id~c, id~h or id~0.5"
            )
        pid, value = parts
        if value in CHOICES:
            out[pid] = value
        elif NUMBER.match(value):
            out[pid] = float(value)
        else:
            raise ScenarioError(f"bad value for {pid!r}; use l, c, h or a plain decimal number")
    return out


QUERY_FIELDS = frozenset({"v", "s", "r", "a", "p", "seed", "draws", "preset"})


def scenario_from_query(params: Mapping[str, str], state: str | None = None) -> RunRequest:
    """Read a scenario from web-address fields (the front end's link format).

    Args:
        params: Query fields: ``s`` state, ``r`` reforms (comma list), ``a``
            overrides (``id~l,id~0.5``), ``seed``, ``draws``, ``preset``. ``v``
            must be ``1`` when given; ``p`` (a view preset) is checked and ignored.
        state: The state from the path, if the address has one (wins over ``s``).

    Returns:
        The checked scenario.

    Raises:
        ScenarioError: An unknown field or a malformed value.
        pydantic.ValidationError: A value outside the limits.
    """
    unknown = sorted(set(params) - QUERY_FIELDS)
    if unknown:
        raise ScenarioError(f"unknown query fields {unknown[:5]}")
    if params.get("v", "1") != "1":
        raise ScenarioError("unknown link version")
    if "p" in params and not re.match(r"^[a-z0-9][a-z0-9-]{0,23}$", params["p"]):
        raise ScenarioError("bad view preset")
    reforms = [r for r in params.get("r", "").split(",") if r]
    body: dict[str, Any] = {
        "state": state or params.get("s", ""),
        "reforms": reforms,
        "overrides": _split_overrides(params.get("a", "")),
    }
    for key in ("seed", "draws"):
        if key in params:
            if not re.match(r"^\d{1,10}$", params[key]):
                raise ScenarioError(f"{key} must be a whole number")
            body[key] = int(params[key])
    if "preset" in params:
        body["preset"] = params["preset"]
    return RunRequest.model_validate(body)
