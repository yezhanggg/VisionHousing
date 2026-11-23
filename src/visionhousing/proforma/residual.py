"""Residual land value: what a builder could pay for a lot and still make a profit.

Plain-language summary
----------------------
A builder deciding whether to buy a lot and build works backwards. Start from
what the finished building is worth, take off every cost of building it and the
profit the builder needs, and what is left is the most the builder could pay
for the land. That is the **residual land value**. If it is above what the lot
costs today (for missing middle, the price of the single-family home on it),
redevelopment pencils out.

Two pro formas use the same steps at different intensities:

- :func:`redevelopment_proforma`: a 2-6 unit building on a former single-family
  lot (benchmark 2, missing middle), with surface parking.
- :func:`midrise_proforma`: a mid-rise apartment building (benchmark 3,
  transit-oriented density), with structured parking.

The lines
---------
1. **Floor area** = units x floor area per unit.
2. **Hard cost** = floor area x construction cost per sq ft x regional cost factor.
3. **Soft cost** = soft-cost share x hard cost.
4. **Fees** = units x development fees per unit. The benchmark is $50,000 per
   unit across all categories; the fee reform halves it through a scenario
   override.
5. **Parking spaces** = units x spaces per unit; **parking cost** = spaces x
   cost per space (surface for missing middle, structured for mid-rise). The
   parking reform changes spaces per unit through a scenario override.
6. **Development cost** = hard + soft + fees + parking.
7. **Developer return** = required margin x development cost.
8. **Stabilised value**, by one of two bases:

   * ``"income"`` (default): gross rent = units x rent x 12; net operating
     income (NOI) = gross rent x (1 - operating-cost share); value = NOI /
     capitalisation rate (cap rate).
   * ``"sale"``: value = units x sale value per unit (for condominiums or
     townhouses sold one by one).

9. **Residual land value** = stabilised value - (development cost + developer
   return).
10. **Land margin** (only when an acquisition price is given) = residual land
    value - acquisition price. In the scenario the acquisition price is the
    tract's distribution of single-family home values, so the share of lots
    where redevelopment pencils is a share, not a yes or no.

What this leaves out
--------------------
Demolition, holding costs during construction, and the time between buying
and selling are not separate lines; they are part of the soft-cost share and
the margin. The number of units is a parameter; whether a lot can physically
hold them is the scenario's "fit" step.

All functions are pure: no files, no network, no global state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from visionhousing.proforma._calc import Calc, Input, ProFormaResult, collect, run

Basis = Literal["income", "sale"]

FEE = Input("fees.fee_total_per_unit", "development fees per unit", "$/unit")
COST_FACTOR = Input("context.cost_factor", "regional cost factor", "factor")
RENT = Input("context.rent_monthly", "market rent per unit", "$/month")
SALE_VALUE = Input("context.sale_value_per_unit", "sale value per unit", "$/unit")
ACQUISITION = Input("context.acquisition_price", "acquisition price of the lot", "$")


@dataclass(frozen=True)
class Intensity:
    """The ledger ids and names that set one building type.

    Attributes:
        kind: Name of the pro forma.
        units: Units per project.
        unit_size: Floor area per unit.
        cost_psf: Construction cost per sq ft.
        soft: Soft-cost share.
        margin: Developer margin on cost.
        cap_rate: Capitalisation rate.
        opex: Operating-cost share of rent.
        spaces: Parking spaces per unit.
        space_cost: Cost per parking space.
        parking_name: Name of the parking-cost line.
    """

    kind: str
    units: Input
    unit_size: Input
    cost_psf: Input
    soft: Input
    margin: Input
    cap_rate: Input
    opex: Input
    spaces: Input
    space_cost: Input
    parking_name: str

    def inputs(self, basis: Basis, with_acquisition: bool) -> tuple[Input, ...]:
        """Return the inputs this building type reads for a value basis."""
        common = (
            self.units,
            self.unit_size,
            self.cost_psf,
            COST_FACTOR,
            self.soft,
            FEE,
            self.spaces,
            self.space_cost,
            self.margin,
        )
        valuation = (RENT, self.opex, self.cap_rate) if basis == "income" else (SALE_VALUE,)
        return common + valuation + ((ACQUISITION,) if with_acquisition else ())


REDEVELOPMENT = Intensity(
    kind="redevelopment",
    units=Input("missing_middle.units_per_project", "units", "units/lot"),
    unit_size=Input("missing_middle.unit_size_sqft", "floor area per unit", "sq ft"),
    cost_psf=Input("missing_middle.hard_cost_per_sqft", "construction cost per sq ft", "$/sq ft"),
    soft=Input("missing_middle.soft_cost_share", "soft-cost share", "share"),
    margin=Input("missing_middle.developer_margin", "developer margin", "share"),
    cap_rate=Input("missing_middle.cap_rate", "cap rate", "share per year"),
    opex=Input("missing_middle.operating_cost_share", "operating-cost share", "share"),
    spaces=Input(
        "parking.required_spaces_per_unit_attached", "parking spaces per unit", "spaces/unit"
    ),
    space_cost=Input("parking.cost_per_space_surface", "cost per surface space", "$/space"),
    parking_name="surface parking cost",
)
"""A 2-6 unit building on a former single-family lot, surface parking."""

MIDRISE = Intensity(
    kind="midrise",
    units=Input("multifamily.units_per_project", "units", "units/lot"),
    unit_size=Input("multifamily.unit_size_sqft", "floor area per unit", "sq ft"),
    cost_psf=Input("multifamily.hard_cost_per_sqft", "construction cost per sq ft", "$/sq ft"),
    soft=Input("multifamily.soft_cost_share", "soft-cost share", "share"),
    margin=Input("multifamily.developer_margin", "developer margin", "share"),
    cap_rate=Input("multifamily.cap_rate", "cap rate", "share per year"),
    opex=Input("multifamily.operating_cost_share", "operating-cost share", "share"),
    spaces=Input(
        "parking.required_spaces_per_unit_multifamily", "parking spaces per unit", "spaces/unit"
    ),
    space_cost=Input("parking.cost_per_space_structured", "cost per structured space", "$/space"),
    parking_name="structured parking cost",
)
"""A mid-rise apartment building, structured parking."""


def _steps_for(spec: Intensity, basis: Basis, with_acquisition: bool):
    def steps(c: Calc) -> dict[str, Any]:
        units = c.given(spec.units)
        floor_area = c.mul(units, c.given(spec.unit_size), name="floor area", unit="sq ft")
        hard = c.product(
            [floor_area, c.given(spec.cost_psf), c.given(COST_FACTOR)],
            name="hard cost",
            unit="$",
        )
        soft = c.mul(c.given(spec.soft), hard, name="soft cost", unit="$")
        fees = c.mul(units, c.given(FEE), name="fees", unit="$")
        spaces = c.mul(units, c.given(spec.spaces), name="parking spaces", unit="spaces")
        parking = c.mul(spaces, c.given(spec.space_cost), name=spec.parking_name, unit="$")
        cost = c.add([hard, soft, fees, parking], name="development cost")
        developer_return = c.mul(c.given(spec.margin), cost, name="developer return", unit="$")
        lines: dict[str, Any] = {
            "units": units,
            "floor_area": floor_area,
            "hard_cost": hard,
            "soft_cost": soft,
            "fees": fees,
            "parking_spaces": spaces,
            "parking_cost": parking,
            "development_cost": cost,
            "developer_return": developer_return,
        }
        if basis == "income":
            gross = c.product(
                [units, c.given(RENT), c.const("months per year", 12.0, "months/year")],
                name="gross rent",
                unit="$/year",
            )
            kept = c.sub(
                c.const("all of the rent", 1.0, "share"), c.given(spec.opex), name="share kept"
            )
            noi = c.mul(gross, kept, name="net operating income", unit="$/year")
            value = c.div(noi, c.given(spec.cap_rate), name="stabilised value", unit="$")
            lines.update(gross_rent=gross, share_kept=kept, noi=noi)
        else:
            value = c.mul(units, c.given(SALE_VALUE), name="stabilised value", unit="$")
        all_in = c.add([cost, developer_return], name="cost with developer return")
        residual = c.sub(value, all_in, name="residual land value")
        lines.update(stabilised_value=value, cost_with_return=all_in, residual_land_value=residual)
        if with_acquisition:
            lines["land_margin"] = c.sub(residual, c.given(ACQUISITION), name="land margin")
        return lines

    return steps


def residual_proforma(
    spec: Intensity,
    params: Mapping[str, Any],
    context: Mapping[str, Any],
    basis: Basis = "income",
) -> ProFormaResult:
    """Work out residual land value for one building type.

    Args:
        spec: Which building type (``REDEVELOPMENT`` or ``MIDRISE``).
        params: Ledger id to value (central values or draws).
        context: Tract context: ``cost_factor``; ``rent_monthly`` for the income
            basis or ``sale_value_per_unit`` for the sale basis; optionally
            ``acquisition_price`` (what the lot costs today, $).
        basis: ``"income"`` (rent capitalised) or ``"sale"`` (units sold).

    Returns:
        The lines (project totals in $) and a ``records()`` method.

    Raises:
        KeyError: An input is missing; the message names it.
        ValueError: The basis is not ``income`` or ``sale``.
    """
    if basis not in ("income", "sale"):
        raise ValueError("basis must be 'income' or 'sale'")
    with_acquisition = context.get("acquisition_price") is not None
    values = collect(spec.inputs(basis, with_acquisition), params, context)
    notes = (
        f"Value basis: {basis}.",
        "Fees and parking are separate lines; the fee and parking reforms change them "
        "through scenario overrides.",
        "Compare residual land value with the tract's distribution of home values in "
        "the scenario, not with one price.",
    )
    return run(spec.kind, _steps_for(spec, basis, with_acquisition), values, notes)


def redevelopment_proforma(
    params: Mapping[str, Any], context: Mapping[str, Any], basis: Basis = "income"
) -> ProFormaResult:
    """Residual land value of a 2-6 unit building on a former single-family lot.

    Args:
        params: Ledger id to value (central values or draws).
        context: Tract context (see :func:`residual_proforma`).
        basis: ``"income"`` or ``"sale"``.

    Returns:
        The pro forma result.
    """
    return residual_proforma(REDEVELOPMENT, params, context, basis)


def midrise_proforma(
    params: Mapping[str, Any], context: Mapping[str, Any], basis: Basis = "income"
) -> ProFormaResult:
    """Residual land value of a mid-rise apartment building with structured parking.

    Args:
        params: Ledger id to value (central values or draws).
        context: Tract context (see :func:`residual_proforma`).
        basis: ``"income"`` or ``"sale"``.

    Returns:
        The pro forma result.
    """
    return residual_proforma(MIDRISE, params, context, basis)
