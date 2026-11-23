"""Result records: every number the engine produces, with its working shown.

A record holds one number (or a likely range as P10 / P50 / P90), its unit, the
formula that produced it written out in words and numbers, the inputs that went
into it, the ledger parameters and sources behind it, and its data tier.

The helpers ``mul``, ``div``, ``add``, ``sub`` and ``product`` do the arithmetic
and write the formula from the arithmetic they actually did, for example::

    eligible lots (1,000 lots) [times] fit share (0.536) = 536 lots

where [times] is the multiplication sign, so a formula can never drift
from its number. Assumption ids and source ids are
carried forward from the inputs, and the tier of a result is the lowest tier of
its inputs (a number is only as detailed as its coarsest input).

Records turn into JSON and back without loss (``to_json`` / ``from_json``).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from visionhousing.ledger.schema import Parameter

TIMES = chr(0x00D7)  # multiplication sign
DIVIDE = chr(0x00F7)  # division sign
MINUS = chr(0x2212)  # minus sign

# Units that carry no dimension: multiplying by them keeps the other unit.
DIMENSIONLESS = frozenset({"", "1", "share", "ratio", "share per year", "factor", "multiplier"})


class InputRef(BaseModel):
    """One input to a record, as it was used.

    Attributes:
        name: Plain-language name of the input.
        value: The value used (the central or point value).
        unit: Its unit.
        formula: How the input itself was produced.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    value: float
    unit: str
    formula: str


class Record(BaseModel):
    """One engine result.

    Attributes:
        name: Plain-language name, for example "added ADUs".
        value: The point value, when the result is a single number.
        p10: 10th percentile, when the result is a range.
        p50: Median, when the result is a range.
        p90: 90th percentile, when the result is a range.
        unit: Unit of the result.
        formula: Human-readable formula with names, values and units.
        inputs: The inputs, as used.
        assumption_ids: Ledger parameter ids behind this number.
        source_ids: Source ids behind this number.
        tier: Data tier (0, 1 or 2); ``None`` for a pure assumption.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    value: float | None = None
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None
    unit: str
    formula: str = Field(min_length=1)
    inputs: tuple[InputRef, ...] = ()
    assumption_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    tier: int | None = Field(default=None, ge=0, le=2)

    @model_validator(mode="after")
    def _point_or_range(self) -> Record:
        quantiles = (self.p10, self.p50, self.p90)
        has_range = all(q is not None for q in quantiles)
        if any(q is not None for q in quantiles) and not has_range:
            raise ValueError("a range needs all of p10, p50 and p90")
        if (self.value is None) == (not has_range):
            raise ValueError("give either value, or p10/p50/p90, not both and not neither")
        if has_range and not self.p10 <= self.p50 <= self.p90:  # type: ignore[operator]
            raise ValueError("need p10 <= p50 <= p90")
        return self

    @property
    def is_range(self) -> bool:
        """True when the record holds P10 / P50 / P90 rather than one value."""
        return self.value is None

    def point(self) -> float:
        """Return the point value; raise if the record is a range."""
        if self.value is None:
            raise ValueError(
                f"'{self.name}' is a range; arithmetic helpers need point values. "
                "Do the arithmetic per draw and summarise with from_draws()."
            )
        return self.value

    def to_json(self) -> str:
        """Return the record as JSON text."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, text: str) -> Record:
        """Rebuild a record from ``to_json`` output."""
        return cls.model_validate_json(text)


def format_number(value: float, unit: str = "") -> str:
    """Write a number the way a formula shows it.

    Whole thousands get commas; values of 1 or more show up to two decimals;
    small values show three significant digits. Units starting with ``$`` are
    written as money (``$/unit`` gives ``$25,000/unit``). Dimensionless units are
    not printed.

    Args:
        value: The number.
        unit: Its unit.

    Returns:
        The number as text, with its unit.
    """
    if not math.isfinite(value):
        text = str(value)
    elif value == 0:
        text = "0"
    elif abs(value) >= 1000:
        text = f"{value:,.0f}"
    elif abs(value) >= 1:
        text = f"{value:,.2f}".rstrip("0").rstrip(".")
    else:
        text = f"{value:.3g}"
    if unit.startswith("$"):
        sign = "-" if text.startswith("-") else ""
        return f"{sign}${text.lstrip('-')}{unit[1:]}"
    if unit in DIMENSIONLESS:
        return text
    return f"{text} {unit}"


def _term(record: Record) -> str:
    return f"{record.name} ({format_number(record.point(), record.unit)})"


def _merge(records: Iterable[Record]) -> tuple[tuple[str, ...], tuple[str, ...], int | None]:
    records = list(records)
    assumption_ids = tuple(sorted({a for r in records for a in r.assumption_ids}))
    source_ids = tuple(sorted({s for r in records for s in r.source_ids}))
    tiers = [r.tier for r in records if r.tier is not None]
    return assumption_ids, source_ids, (min(tiers) if tiers else None)


def _inputs(records: Iterable[Record]) -> tuple[InputRef, ...]:
    return tuple(
        InputRef(name=r.name, value=r.point(), unit=r.unit, formula=r.formula) for r in records
    )


def _combine(
    name: str, value: float, unit: str, operator: str, records: Sequence[Record]
) -> Record:
    expression = f" {operator} ".join(_term(r) for r in records)
    assumption_ids, source_ids, tier = _merge(records)
    return Record(
        name=name,
        value=value,
        unit=unit,
        formula=f"{expression} = {format_number(value, unit)}",
        inputs=_inputs(records),
        assumption_ids=assumption_ids,
        source_ids=source_ids,
        tier=tier,
    )


def _product_unit(units: Sequence[str]) -> str:
    real = [u for u in units if u not in DIMENSIONLESS]
    if not real:
        return units[0] if units else ""
    return f" {TIMES} ".join(real)


def quantity(
    name: str,
    value: float,
    unit: str,
    *,
    formula: str | None = None,
    assumption_ids: Iterable[str] = (),
    source_ids: Iterable[str] = (),
    tier: int | None = None,
) -> Record:
    """Make a leaf record: a number read from data or given directly.

    Args:
        name: Plain-language name.
        value: The number.
        unit: Its unit.
        formula: How it was obtained; defaults to "name = value".
        assumption_ids: Ledger ids behind it.
        source_ids: Source ids behind it.
        tier: Data tier, if it comes from data.

    Returns:
        The record.
    """
    return Record(
        name=name,
        value=float(value),
        unit=unit,
        formula=formula or f"{name} = {format_number(float(value), unit)}",
        assumption_ids=tuple(assumption_ids),
        source_ids=tuple(source_ids),
        tier=tier,
    )


def from_parameter(parameter_id: str, param: Parameter, which: str = "central") -> Record:
    """Make a leaf record from one ledger parameter.

    Args:
        parameter_id: The parameter's id.
        param: The parameter.
        which: ``"central"``, ``"low"`` or ``"high"``.

    Returns:
        A record whose formula names the parameter and the end of the range used.
    """
    if which not in ("central", "low", "high"):
        raise ValueError("which must be 'central', 'low' or 'high'")
    value = float(getattr(param, which))
    shown = format_number(value, param.unit)
    return Record(
        name=param.label,
        value=value,
        unit=param.unit,
        formula=f"{param.label} = {shown} (ledger {parameter_id}, {which})",
        assumption_ids=(parameter_id,),
        source_ids=param.source_ids,
        tier=None,
    )


def mul(a: Record, b: Record, *, name: str, unit: str | None = None) -> Record:
    """Multiply two records: "a [times] b = c".

    Args:
        a: First factor.
        b: Second factor.
        name: Name of the result.
        unit: Unit of the result; defaults to the non-dimensionless input unit(s).

    Returns:
        The product, with its formula.
    """
    value = a.point() * b.point()
    return _combine(name, value, unit or _product_unit([a.unit, b.unit]), TIMES, [a, b])


def product(records: Sequence[Record], *, name: str, unit: str | None = None) -> Record:
    """Multiply several records in order: "a [times] b [times] c = d". Used for funnels.

    Args:
        records: The factors (at least one).
        name: Name of the result.
        unit: Unit of the result; defaults as in ``mul``.

    Returns:
        The product, with its formula.
    """
    if not records:
        raise ValueError("product needs at least one record")
    value = math.prod(r.point() for r in records)
    return _combine(name, value, unit or _product_unit([r.unit for r in records]), TIMES, records)


def div(a: Record, b: Record, *, name: str, unit: str | None = None) -> Record:
    """Divide two records: "a ÷ b = c".

    Args:
        a: Numerator.
        b: Denominator (not zero).
        name: Name of the result.
        unit: Unit of the result; defaults to "a-unit per b-unit", or a's unit
            when b is dimensionless.

    Returns:
        The quotient, with its formula.
    """
    if b.point() == 0:
        raise ZeroDivisionError(f"cannot divide by '{b.name}', which is zero")
    if unit is None:
        if b.unit in DIMENSIONLESS:
            unit = a.unit
        else:
            unit = "ratio" if a.unit == b.unit else f"{a.unit} per {b.unit}"
    return _combine(name, a.point() / b.point(), unit, DIVIDE, [a, b])


def add(records: Sequence[Record], *, name: str) -> Record:
    """Add records that share one unit: "a + b = c".

    Args:
        records: The terms (at least one), all in the same unit.
        name: Name of the result.

    Returns:
        The sum, with its formula.
    """
    if not records:
        raise ValueError("add needs at least one record")
    units = {r.unit for r in records}
    if len(units) != 1:
        raise ValueError(f"cannot add different units: {sorted(units)}")
    total = math.fsum(r.point() for r in records)
    return _combine(name, total, records[0].unit, "+", records)


def sub(a: Record, b: Record, *, name: str) -> Record:
    """Subtract one record from another in the same unit: "a [minus] b = c".

    Args:
        a: The record to subtract from.
        b: The record to subtract.
        name: Name of the result.

    Returns:
        The difference, with its formula.
    """
    if a.unit != b.unit:
        raise ValueError(f"cannot subtract '{b.unit}' from '{a.unit}'")
    return _combine(name, a.point() - b.point(), a.unit, MINUS, [a, b])


def from_draws(
    name: str,
    draws: Sequence[float] | np.ndarray,
    unit: str,
    *,
    expression: str,
    inputs: Sequence[Record] = (),
    assumption_ids: Iterable[str] = (),
    source_ids: Iterable[str] = (),
    tier: int | None = None,
) -> Record:
    """Summarise Monte Carlo draws as a P10 / P50 / P90 record.

    Args:
        name: Name of the result.
        draws: The simulated values.
        unit: Their unit.
        expression: The formula applied in each draw, in words.
        inputs: Central-value inputs, for the explanation.
        assumption_ids: Ledger ids that were sampled.
        source_ids: Sources behind them.
        tier: Data tier.

    Returns:
        A range record whose formula names the draws and the expression.
    """
    arr = np.asarray(draws, dtype=float)
    if arr.ndim != 1 or arr.size == 0:
        raise ValueError("draws must be a non-empty one-dimensional sequence")
    p10, p50, p90 = (float(q) for q in np.percentile(arr, [10, 50, 90]))
    merged_ids, merged_sources, merged_tier = _merge(inputs)
    tiers = [t for t in (tier, merged_tier) if t is not None]
    return Record(
        name=name,
        p10=p10,
        p50=p50,
        p90=p90,
        unit=unit,
        formula=(
            f"P10, P50, P90 of {arr.size:,} seeded draws of {expression} = "
            f"{format_number(p10, unit)}, {format_number(p50, unit)}, {format_number(p90, unit)}"
        ),
        inputs=_inputs(inputs),
        assumption_ids=tuple(sorted(set(assumption_ids) | set(merged_ids))),
        source_ids=tuple(sorted(set(source_ids) | set(merged_sources))),
        tier=min(tiers) if tiers else None,
    )
