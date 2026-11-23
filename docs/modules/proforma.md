# Pro formas: does a project pencil out?

Code: `src/visionhousing/proforma/`. Tests: `tests/test_proforma.py` (golden cases),
`tests/test_proforma_invariants.py` (invariants, vectorised vs. scalar, records).

## What it does

A pro forma is a simple budget for one project: what it costs to build and what it
earns. The model has three:

| Function | Project | Main result | Used by |
|---|---|---|---|
| `adu_proforma` | A homeowner adds one ADU (benchmark 600 sq ft; the size is a parameter) | Break-even rent; break-even home value | ADU reform, HB 2186, SB 1346 |
| `redevelopment_proforma` | A 2–6 unit building replaces a single-family home, surface parking | Residual land value | Missing middle |
| `midrise_proforma` | A mid-rise apartment building, structured parking | Residual land value | Transit-oriented density, parking |

The scenario code compares these results with each tract's distribution of rents or
home values (see `brackets.md`) to get the **share** of lots where a project pays.

## Inputs

Each function takes:

- **`params`**: ledger id → value. Use `ledger.central()` for central values, or
  `sample(ledger, n, seed)` for Monte Carlo draws.
- **`context`**: tract numbers from the data pack: `rent_monthly`, `cost_factor`
  (regional construction cost factor, 1 = average), and for the residual pro formas
  optionally `sale_value_per_unit` and `acquisition_price`.

Everything is combined with numpy's broadcasting rules. To work out every draw for
every tract in one call, pass draws as columns and tracts as rows:

```python
from visionhousing.proforma import adu_proforma, as_draw_column

result = adu_proforma(as_draw_column(draws), {"rent_monthly": rents, "cost_factor": factors})
result.lines["breakeven_rent"]  # shape (draws, tracts)
```

## Homeowner ADU

| Line | Formula | Ledger ids |
|---|---|---|
| Building cost | floor area × cost per sq ft × regional cost factor | `adu.unit_size_sqft`, `adu.hard_cost_per_sqft` |
| Site work | one amount per ADU | `adu.site_utility_cost` |
| Hard cost | building + site work | |
| Soft cost | soft-cost share × hard cost | `adu.soft_cost_share` |
| **Local permit fees** | one amount per ADU | `adu.permit_fee` |
| **Utility connection fees** | one amount per ADU (SB 1346 sets it to 0) | `adu.utility_connection_fee` |
| **Parking cost** | added spaces × cost per surface space (0 under the benchmark) | `adu.added_parking_spaces`, `parking.cost_per_space_surface` |
| Total cost | hard + soft + fees + parking | |
| Loan payment | loan share × total × payment factor at (mortgage rate + spread) | `adu.loan_share`, `econ.mortgage_rate_30y`, `adu.loan_rate_spread`, `adu.loan_term_years` |
| Cash cost | the owner's own cash × payment factor at the cash return | `adu.cash_return` |
| Monthly cost | loan payment + cash cost | |
| Net rent | rent × (1 − vacancy and running-cost share) | `adu.operating_cost_share` |
| **Break-even rent** | required coverage × monthly cost ÷ (1 − running-cost share) | `adu.required_coverage` |
| Monthly surplus | net rent − required coverage × monthly cost | |
| **Break-even home value** | total cost ÷ value-uplift share | `adu.value_uplift_share` |

The **payment factor** is the monthly payment on a loan of one dollar:
`r ÷ (1 − (1 + r)^−m)` with `r` = yearly rate ÷ 12 and `m` = 12 × years. At a zero
rate it is `1 ÷ m`. The owner's own cash is charged the same way at the return it
could have earned elsewhere, so cash is never treated as free.

With coverage 1 the break-even is exactly "monthly cost ÷ (1 − running-cost share)".
The break-even home value is for a value-based test, where a tract's home-value
distribution is used instead of its rents.

**Applied in the scenario, not here:** the non-financial (family-use) share
(`adu.nonfinancial_share`), the bedroom ratio and new-unit rent premium that turn a
tract's median rent into the ADU's rent.

## Residual land value (redevelopment and mid-rise)

| Line | Formula |
|---|---|
| Floor area | units × floor area per unit |
| Hard cost | floor area × cost per sq ft × regional cost factor |
| Soft cost | soft-cost share × hard cost |
| **Fees** | units × development fees per unit (`fees.fee_total_per_unit`, the $50,000 benchmark) |
| **Parking** | units × spaces per unit × cost per space (surface for redevelopment, structured for mid-rise) |
| Development cost | hard + soft + fees + parking |
| Developer return | margin × development cost |
| Stabilised value | income basis: units × rent × 12 × (1 − operating share) ÷ cap rate; sale basis: units × sale value per unit |
| **Residual land value** | stabilised value − (development cost + developer return) |
| Land margin | residual land value − acquisition price (when given) |

Ledger ids: `missing_middle.*` for redevelopment (units, unit size, cost per sq ft,
soft share, margin, cap rate, operating share) with
`parking.required_spaces_per_unit_attached` and `parking.cost_per_space_surface`;
`multifamily.*` for mid-rise with `parking.required_spaces_per_unit_multifamily` and
`parking.cost_per_space_structured`.

Redevelopment pencils where the residual land value is above what the lot costs
today, which for missing middle is the price of the single-family home on it. In the
scenario that price is the tract's distribution of home values, not one number.

## How the reforms reach these lines

Fees and parking are always their own lines, so a reform changes exactly one line:

- **Fee reform:** a scenario override halves `fees.fee_total_per_unit` (or the ADU fee
  lines).
- **Parking reform:** a scenario override sets spaces per unit to the spaces still
  built (decision D-08).
- **SB 1346:** `adu.utility_connection_fee` = 0.

## Formulas you can read

`result.records(index)` explains one element (one draw, one tract) as result records
(`records.md`). The same steps run twice: once on arrays for the numbers, once with
the record helpers for the formulas, so the two can never disagree. A test checks it.
Example lines for the ADU at central values:

```
building cost: ADU floor area (600 sq ft) × construction cost per sq ft ($250/sq ft) × regional cost factor (1) = $150,000/unit
fees: local permit fees ($2,500/unit) + utility connection fees ($3,000/unit) = $5,500/unit
```

Each record carries the ledger ids behind it; pass `ledger=` to add source ids.

## How to check it

- Three golden cases per pro forma, worked by hand in each test's docstring and
  checked to the cent.
- Invariants: lower fees never raise the break-even or raise residual land value;
  fewer parking spaces never raise cost; higher rent (or sale value) never lowers
  residual land value; the vectorised result equals a scalar loop; records equal the
  arrays.

## Limits

- One building type per pro forma; real projects vary in size, finish and timing.
- No taxes on rental income, depreciation, appreciation, or construction-period
  interest as separate lines; they sit inside the soft-cost share and the margin.
- Demolition is not a separate line in redevelopment.
- Most cost parameters are the team's judgement with wide ranges (`status: draft`);
  the ranges, not the central values, are what the results report.
