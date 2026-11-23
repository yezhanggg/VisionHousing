"""The shared machinery behind the three pro formas.

Each pro forma is written once, as a list of steps ("hard cost = floor area x
cost per square foot x regional cost factor", and so on). The steps call a
small calculator. Two calculators run the same steps:

- ``ArrayCalc`` does the arithmetic with numpy arrays, so one call works out
  every tract and every Monte Carlo draw at once.
- ``RecordCalc`` does the arithmetic on one tract and one draw with the
  record helpers in ``visionhousing.engine.records``. Each step becomes a
  record whose formula is written from the arithmetic it actually did, with
  the ledger ids behind it.

Because both calculators run the same steps, the numbers and the formulas can
never disagree. A test checks that they match.

Inputs come in two kinds:

- **Parameters**, keyed by their ledger id (for example ``adu.permit_fee``).
  They come from ``Ledger.central()`` or from ``ledger.sample()``.
- **Tract context**, keyed ``context.<name>`` (for example
  ``context.rent_monthly``): numbers about the place, from the data pack.

All inputs are combined with numpy's broadcasting rules. To get a grid of
draws by tracts, pass parameter draws as columns (shape ``(draws, 1)``, see
:func:`as_draw_column`) and tract context as rows (shape ``(tracts,)``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from visionhousing.engine import records as rec
from visionhousing.engine.records import Record
from visionhousing.ledger.loader import Ledger

CONTEXT = "context."
"""Prefix of tract-context keys, to tell them apart from ledger ids."""


@dataclass(frozen=True)
class Input:
    """One input a pro forma reads.

    Attributes:
        key: Ledger id, or ``context.<name>`` for tract context.
        name: Plain-language name shown in formulas.
        unit: Unit of the value (matches the ledger entry's unit).
    """

    key: str
    name: str
    unit: str

    @property
    def is_parameter(self) -> bool:
        """True for a ledger parameter, False for tract context."""
        return not self.key.startswith(CONTEXT)


class Calc(Protocol):
    """The operations a pro forma's steps may use."""

    def given(self, item: Input) -> Any:
        """Return an input's value."""

    def const(self, name: str, value: float, unit: str) -> Any:
        """Return a fixed number that is not an assumption (for example 12 months)."""

    def mul(self, a: Any, b: Any, *, name: str, unit: str | None = None) -> Any:
        """Return ``a`` times ``b``."""

    def product(self, items: Sequence[Any], *, name: str, unit: str | None = None) -> Any:
        """Return the product of several values."""

    def div(self, a: Any, b: Any, *, name: str, unit: str | None = None) -> Any:
        """Return ``a`` divided by ``b``."""

    def add(self, items: Sequence[Any], *, name: str) -> Any:
        """Return the sum of several values in one unit."""

    def sub(self, a: Any, b: Any, *, name: str) -> Any:
        """Return ``a`` minus ``b``."""

    def payment_factor(self, rate: Any, years: Any, *, name: str) -> Any:
        """Return the monthly payment per dollar borrowed (see :func:`payment_factor`)."""


Steps = Callable[[Calc], dict[str, Any]]
"""A pro forma body: takes a calculator, returns its named lines."""


def payment_factor(annual_rate: Any, years: Any) -> np.ndarray:
    """Return the monthly payment on a loan of one dollar.

    The standard fixed-payment (annuity) formula with monthly compounding::

        factor = r / (1 - (1 + r) ** -m),   r = annual rate / 12,   m = 12 x years

    A zero rate gives ``1 / m`` (the loan is simply repaid in equal parts).

    Args:
        annual_rate: Yearly interest rate as a decimal (0.06 = 6%).
        years: Loan term in years (more than zero).

    Returns:
        The factor, broadcast over the inputs.
    """
    r = np.asarray(annual_rate, dtype=float) / 12.0
    m = 12.0 * np.asarray(years, dtype=float)
    if np.any(m <= 0):
        raise ValueError("loan term must be more than zero years")
    safe_r = np.where(r == 0, 1.0, r)
    with np.errstate(over="ignore"):
        amortising = safe_r / (1.0 - (1.0 + safe_r) ** -m)
    return np.where(r == 0, 1.0 / m, amortising)


class ArrayCalc:
    """Runs pro forma steps on numpy arrays (every tract and draw at once).

    Args:
        values: Input key to its value (a number or an array).
    """

    def __init__(self, values: Mapping[str, Any]):
        self._values = values

    def given(self, item: Input) -> np.ndarray:
        """Return an input as a float array."""
        return np.asarray(self._values[item.key], dtype=float)

    def const(self, name: str, value: float, unit: str) -> np.ndarray:
        """Return a fixed number."""
        return np.asarray(value, dtype=float)

    def mul(self, a: Any, b: Any, *, name: str, unit: str | None = None) -> np.ndarray:
        """Return ``a * b``."""
        return a * b

    def product(self, items: Sequence[Any], *, name: str, unit: str | None = None) -> np.ndarray:
        """Return the product of the items, in order."""
        out = items[0]
        for item in items[1:]:
            out = out * item
        return out

    def div(self, a: Any, b: Any, *, name: str, unit: str | None = None) -> np.ndarray:
        """Return ``a / b`` (infinite where ``b`` is zero)."""
        with np.errstate(divide="ignore", invalid="ignore"):
            return a / b

    def add(self, items: Sequence[Any], *, name: str) -> np.ndarray:
        """Return the sum of the items."""
        out = items[0]
        for item in items[1:]:
            out = out + item
        return out

    def sub(self, a: Any, b: Any, *, name: str) -> np.ndarray:
        """Return ``a - b``."""
        return a - b

    def payment_factor(self, rate: Any, years: Any, *, name: str) -> np.ndarray:
        """Return the monthly payment per dollar borrowed."""
        return payment_factor(rate, years)


class RecordCalc:
    """Runs pro forma steps on one tract and one draw, building records.

    Args:
        values: Input key to its value (a number or an array).
        index: Which element to explain, for example ``(draw, tract)``.
        ledger: Optional ledger; when given, records carry its source ids.
    """

    def __init__(
        self,
        values: Mapping[str, Any],
        index: tuple[int, ...] | int = (),
        ledger: Ledger | None = None,
    ):
        shape = np.broadcast_shapes(*(np.shape(v) for v in values.values()))
        self._point = {}
        for key, value in values.items():
            picked = np.broadcast_to(np.asarray(value, dtype=float), shape)[index]
            if np.ndim(picked) != 0:
                raise ValueError(f"index {index!r} does not pick a single value from shape {shape}")
            self._point[key] = float(picked)
        self._ledger = ledger

    def given(self, item: Input) -> Record:
        """Return an input as a leaf record that names where it came from."""
        value = self._point[item.key]
        shown = rec.format_number(value, item.unit)
        if not item.is_parameter:
            return rec.quantity(
                item.name,
                value,
                item.unit,
                formula=f"{item.name} = {shown} (tract context)",
            )
        sources: tuple[str, ...] = ()
        if self._ledger is not None and item.key in self._ledger:
            sources = self._ledger[item.key].source_ids
        return rec.quantity(
            item.name,
            value,
            item.unit,
            formula=f"{item.name} = {shown} (ledger {item.key})",
            assumption_ids=(item.key,),
            source_ids=sources,
        )

    def const(self, name: str, value: float, unit: str) -> Record:
        """Return a fixed number as a leaf record."""
        return rec.quantity(name, value, unit)

    def mul(self, a: Record, b: Record, *, name: str, unit: str | None = None) -> Record:
        """Multiply two records."""
        return rec.mul(a, b, name=name, unit=unit)

    def product(self, items: Sequence[Record], *, name: str, unit: str | None = None) -> Record:
        """Multiply several records."""
        return rec.product(items, name=name, unit=unit)

    def div(self, a: Record, b: Record, *, name: str, unit: str | None = None) -> Record:
        """Divide two records."""
        return rec.div(a, b, name=name, unit=unit)

    def add(self, items: Sequence[Record], *, name: str) -> Record:
        """Add records in one unit."""
        return rec.add(items, name=name)

    def sub(self, a: Record, b: Record, *, name: str) -> Record:
        """Subtract one record from another."""
        return rec.sub(a, b, name=name)

    def payment_factor(self, rate: Record, years: Record, *, name: str) -> Record:
        """Return the monthly payment per dollar borrowed, with its formula written out."""
        r, n = rate.point(), years.point()
        value = float(payment_factor(r, n))
        monthly = rec.format_number(r / 12.0)
        months = rec.format_number(12.0 * n)
        if r == 0:
            working = f"1 {rec.DIVIDE} (12 {rec.TIMES} {rec.format_number(n)} years)"
        else:
            working = (
                f"({rate.name} ({rec.format_number(r)}) {rec.DIVIDE} 12 = {monthly}) "
                f"{rec.DIVIDE} (1 {rec.MINUS} (1 + {monthly})^({rec.MINUS}{months} months))"
            )
        records = [rate, years]
        return Record(
            name=name,
            value=value,
            unit="$/month per $",
            formula=f"{working} = {value:.8f}",
            inputs=tuple(
                rec.InputRef(name=x.name, value=x.point(), unit=x.unit, formula=x.formula)
                for x in records
            ),
            assumption_ids=tuple(sorted({a for x in records for a in x.assumption_ids})),
            source_ids=tuple(sorted({s for x in records for s in x.source_ids})),
        )


@dataclass(frozen=True)
class ProFormaResult:
    """What a pro forma returns.

    Attributes:
        kind: Which pro forma ran: ``adu``, ``redevelopment`` or ``midrise``.
        lines: Line name to its amounts, all broadcast to one shape.
        values: The inputs used, by key (ledger id or ``context.<name>``).
        notes: Plain statements of what this pro forma leaves to the scenario.
    """

    kind: str
    lines: dict[str, np.ndarray]
    values: dict[str, Any] = field(repr=False)
    steps: Steps = field(repr=False)
    notes: tuple[str, ...] = ()

    @property
    def shape(self) -> tuple[int, ...]:
        """Shape of every line (for example ``(draws, tracts)``)."""
        return next(iter(self.lines.values())).shape

    def records(
        self, index: tuple[int, ...] | int = (), ledger: Ledger | None = None
    ) -> dict[str, Record]:
        """Explain one element: every line as a record with its formula.

        Args:
            index: Which element, for example ``(draw, tract)``. Leave empty
                when every input is a single number.
            ledger: Optional ledger, so records carry source ids.

        Returns:
            Line name to its record, in the order the lines are worked out.
        """
        return self.steps(RecordCalc(self.values, index, ledger))

    def assumption_ids(self) -> list[str]:
        """Return the ledger ids this pro forma read, sorted."""
        return sorted(k for k in self.values if not k.startswith(CONTEXT))


def collect(
    inputs: Sequence[Input], params: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    """Pick the inputs a pro forma needs from a parameter dict and tract context.

    Args:
        inputs: The inputs the pro forma reads.
        params: Ledger id to value (central values or draws).
        context: Tract-context name (without the prefix) to value.

    Returns:
        Input key to value.

    Raises:
        KeyError: An input is missing; the message names it.
    """
    out: dict[str, Any] = {}
    missing = []
    for item in inputs:
        if item.is_parameter:
            source, key = params, item.key
        else:
            source, key = context, item.key[len(CONTEXT) :]
        if key not in source or source[key] is None:
            missing.append(item.key)
            continue
        out[item.key] = source[key]
    if missing:
        raise KeyError(f"pro forma inputs missing: {', '.join(missing)}")
    return out


def run(
    kind: str,
    steps: Steps,
    values: dict[str, Any],
    notes: Sequence[str] = (),
) -> ProFormaResult:
    """Run the steps on arrays and package the result.

    Args:
        kind: Name of the pro forma.
        steps: Its body.
        values: Its inputs, from :func:`collect`.
        notes: Statements of what it leaves to the scenario.

    Returns:
        The result, every line broadcast to one shape.
    """
    raw = steps(ArrayCalc(values))
    shape = np.broadcast_shapes(
        *(np.shape(v) for v in values.values()), *(np.shape(v) for v in raw.values())
    )
    lines = {name: np.array(np.broadcast_to(v, shape), dtype=float) for name, v in raw.items()}
    return ProFormaResult(kind=kind, lines=lines, values=values, steps=steps, notes=tuple(notes))


def as_draw_column(params: Mapping[str, Any]) -> dict[str, Any]:
    """Turn one-dimensional parameter draws into columns, for a draws-by-tracts grid.

    Args:
        params: Ledger id to a number or a 1-D array of draws.

    Returns:
        The same mapping with every 1-D array reshaped to ``(draws, 1)``.
    """
    return {
        k: (np.asarray(v, dtype=float)[:, None] if np.ndim(v) == 1 else v)
        for k, v in params.items()
    }
