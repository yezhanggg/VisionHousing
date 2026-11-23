"""Homeowner ADU pro forma: what rent does an owner need to build an ADU?

Plain-language summary
----------------------
An owner who adds an accessory dwelling unit (ADU) pays to build it, usually
borrows most of the money, and earns rent. This pro forma works out the
monthly cost of the ADU and the rent that would cover it (the *break-even
rent*). The scenario then compares that break-even with the tract's
distribution of rents to get the share of lots where an ADU pays.

The lines
---------
1. **Building cost** = floor area x construction cost per sq ft x regional cost
   factor. The benchmark ADU is 600 sq ft; the size is a ledger parameter.
2. **Site work and utility runs** (trenching, sewer, water, power): one amount.
3. **Hard cost** = building cost + site work.
4. **Soft cost** = soft-cost share x hard cost (design, permit work, contingency).
5. **Local permit fees** and **utility connection fees** are two separate lines,
   so the fee reform can cut both and SB 1346 can set the connection fee to 0.
6. **Parking cost** = added spaces x cost per surface space. The benchmark needs
   no added parking, so this is 0 unless a scenario says otherwise.
7. **Total cost** = hard + soft + fees + parking.
8. **Financing.** The owner borrows ``loan share`` of the total at the 30-year
   mortgage rate plus a home-equity spread, over the loan term. The rest is the
   owner's own cash, which could have earned ``cash return`` elsewhere; it is
   spread over the same term the same way. Each gives a fixed monthly amount
   (the annuity formula, see ``payment_factor``). **Monthly cost** = loan
   payment + cash cost.
9. **Net rent** = rent x (1 - vacancy and running-cost share).
10. **Break-even rent** = required coverage x monthly cost / (1 - running-cost
    share). With coverage 1 this is exactly "monthly cost / (1 - share)".
11. **Monthly surplus** = net rent - required coverage x monthly cost. Positive
    means the ADU pays.
12. **Break-even home value** = total cost / value-uplift share: the host home
    value above which the value an ADU adds covers its cost. This is for a
    value-based test, where a tract's home-value distribution is used instead
    of rents.

What this pro forma leaves out
------------------------------
- **Family use.** Some owners build for relatives regardless of rent. That
  non-financial share (``adu.nonfinancial_share``) is added in the scenario,
  not here.
- **Rent level.** The rent is an input: the tract's rent for a unit of this
  size, already scaled by bedroom ratio and new-unit premium in the scenario.
- Taxes on rental income, depreciation and appreciation are not modelled.

All functions are pure: no files, no network, no global state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from visionhousing.proforma._calc import Calc, Input, ProFormaResult, collect, run

SIZE = Input("adu.unit_size_sqft", "ADU floor area", "sq ft")
COST_PSF = Input("adu.hard_cost_per_sqft", "construction cost per sq ft", "$/sq ft")
COST_FACTOR = Input("context.cost_factor", "regional cost factor", "factor")
SITE = Input("adu.site_utility_cost", "site work and utility runs", "$/unit")
SOFT = Input("adu.soft_cost_share", "soft-cost share", "share")
PERMIT_FEE = Input("adu.permit_fee", "local permit fees", "$/unit")
CONNECTION_FEE = Input("adu.utility_connection_fee", "utility connection fees", "$/unit")
SPACES = Input("adu.added_parking_spaces", "added parking spaces", "spaces/unit")
SPACE_COST = Input("parking.cost_per_space_surface", "cost per surface space", "$/space")
LOAN_SHARE = Input("adu.loan_share", "loan share", "share")
MORTGAGE = Input("econ.mortgage_rate_30y", "30-year mortgage rate", "share per year")
SPREAD = Input("adu.loan_rate_spread", "home-equity spread", "share per year")
TERM = Input("adu.loan_term_years", "loan term", "years")
CASH_RETURN = Input("adu.cash_return", "return on owner cash", "share per year")
COVERAGE = Input("adu.required_coverage", "required coverage", "ratio")
OPEX = Input("adu.operating_cost_share", "vacancy and running-cost share", "share")
UPLIFT = Input("adu.value_uplift_share", "value-uplift share", "share")
RENT = Input("context.rent_monthly", "market rent for the ADU", "$/month")

INPUTS = (
    SIZE,
    COST_PSF,
    COST_FACTOR,
    SITE,
    SOFT,
    PERMIT_FEE,
    CONNECTION_FEE,
    SPACES,
    SPACE_COST,
    LOAN_SHARE,
    MORTGAGE,
    SPREAD,
    TERM,
    CASH_RETURN,
    COVERAGE,
    OPEX,
    UPLIFT,
    RENT,
)
"""Every input the ADU pro forma reads, in the order the steps use them."""

NOTES = (
    "The non-financial (family-use) share is applied in the ADU scenario, not here.",
    "Rent is an input: the tract's rent for a unit of this size, from the scenario.",
    "Parking is 0 under the benchmark (no added spaces); a scenario may add spaces.",
)


def _steps(c: Calc) -> dict[str, Any]:
    building = c.product(
        [c.given(SIZE), c.given(COST_PSF), c.given(COST_FACTOR)],
        name="building cost",
        unit="$/unit",
    )
    site = c.given(SITE)
    hard = c.add([building, site], name="hard cost")
    soft = c.mul(c.given(SOFT), hard, name="soft cost", unit="$/unit")
    permit = c.given(PERMIT_FEE)
    connection = c.given(CONNECTION_FEE)
    fees = c.add([permit, connection], name="fees")
    parking = c.mul(c.given(SPACES), c.given(SPACE_COST), name="parking cost", unit="$/unit")
    total = c.add([hard, soft, fees, parking], name="total cost")

    loan = c.mul(c.given(LOAN_SHARE), total, name="loan amount", unit="$/unit")
    cash = c.sub(total, loan, name="owner cash")
    rate = c.add([c.given(MORTGAGE), c.given(SPREAD)], name="loan rate")
    term = c.given(TERM)
    loan_factor = c.payment_factor(rate, term, name="loan payment per $")
    cash_factor = c.payment_factor(c.given(CASH_RETURN), term, name="cash cost per $")
    loan_payment = c.mul(loan, loan_factor, name="loan payment", unit="$/month")
    cash_cost = c.mul(cash, cash_factor, name="cash cost", unit="$/month")
    monthly = c.add([loan_payment, cash_cost], name="monthly cost")
    required = c.mul(c.given(COVERAGE), monthly, name="required net rent", unit="$/month")

    kept = c.sub(c.const("all of the rent", 1.0, "share"), c.given(OPEX), name="share kept")
    rent = c.given(RENT)
    net_rent = c.mul(rent, kept, name="net rent", unit="$/month")
    breakeven = c.div(required, kept, name="break-even rent", unit="$/month")
    surplus = c.sub(net_rent, required, name="monthly surplus")
    breakeven_value = c.div(total, c.given(UPLIFT), name="break-even home value", unit="$")
    return {
        "building_cost": building,
        "site_work": site,
        "hard_cost": hard,
        "soft_cost": soft,
        "permit_fees": permit,
        "connection_fees": connection,
        "fees": fees,
        "parking_cost": parking,
        "total_cost": total,
        "loan_amount": loan,
        "owner_cash": cash,
        "loan_rate": rate,
        "loan_payment_factor": loan_factor,
        "cash_cost_factor": cash_factor,
        "loan_payment": loan_payment,
        "cash_cost": cash_cost,
        "monthly_cost": monthly,
        "required_net_rent": required,
        "share_kept": kept,
        "rent": rent,
        "net_rent": net_rent,
        "breakeven_rent": breakeven,
        "monthly_surplus": surplus,
        "breakeven_home_value": breakeven_value,
    }


def adu_proforma(params: Mapping[str, Any], context: Mapping[str, Any]) -> ProFormaResult:
    """Work out the homeowner ADU pro forma.

    Args:
        params: Ledger id to value: central values (``Ledger.central()``) or
            draws (``sample()``, reshaped with ``as_draw_column`` for a grid).
        context: Tract context: ``rent_monthly`` (market rent for the ADU, $/month)
            and ``cost_factor`` (regional construction cost factor, 1 = average).

    Returns:
        The lines (amounts per ADU, or per month for the monthly lines) and a
        ``records()`` method that explains any one element.

    Raises:
        KeyError: A parameter or context value is missing; the message names it.
    """
    return run("adu", _steps, collect(INPUTS, params, context), NOTES)
