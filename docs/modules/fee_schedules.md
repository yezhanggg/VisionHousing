# Documented local fee schedules

Code: `src/visionhousing/features/fee_schedules.py`. Data: `assumptions/fee_schedules.yaml`.
Tests: `tests/test_fee_schedules.py`. Method: [`docs/methods/fees.md`](../methods/fees.md),
section "Documented local fee schedules".

## What it does

It prices five standard prototypes (a 600 sq ft ADU, a single-family home, a
duplex, a fourplex and a 20-unit apartment building) under the fee schedules
that Philadelphia and Pittsburgh (and their water utilities) publish. The
result is a fee per home, as a range, by city and prototype. It feeds only the
named variant `fees_documented_local`; the $50,000 benchmark never reads it.

## The schedule file

`assumptions/fee_schedules.yaml` holds, in this order:

| Block | What it holds |
|---|---|
| `structure_type_prototypes` | Which prototype prices each structure type of the fee reform (1-unit → single-family home, 2-unit → duplex, 3–4 → fourplex, 5+ → 20-unit) |
| `categories` | The core fee categories every city must cover before its schedule is used, and the secondary ones |
| `prototypes` | Floor area, units, fixtures, registers, sprinkler heads, permits, large sheets, the City's table value per sq ft, and the ledger id of the construction cost. A quantity may be `{low, high}` where a reading could go two ways |
| `documents` | Each fee document: title, publisher, address, kind (pdf, html, legistar_text), sha256 for PDFs, what robots.txt said, registry source id |
| `cities` | County, whether the city is the whole county, and the review status of every category |
| `items` | Each fee: city, category, name, status, basis and amounts, which prototypes owe it (with a multiplier), exemptions, payment stage, dollars year, payee, service, document and quote |

Statuses: `documented`, `not_applicable` (reviewed, does not apply to the
prototype as defined), `optional_excluded`, `unknown_not_reviewed`,
`unknown_refused` (the only official copy refuses scripted reading). Unknown
is never zero.

## Main functions

| Function | What it returns |
|---|---|
| `load_schedule(path=None)` | The checked schedule (`Schedule`); raises on a missing field or a broken reference |
| `price_prototype(schedule, city, prototype, params)` | `PrototypePrice`: the counted lines, every shown line, low and high totals per building by draw, the unknown items, and whether the city is complete |
| `prototype_draws(...)` | Documented fee per home in each draw: (low + position × (high − low)) / units |
| `city_status(schedule, city)` | Whether the city is complete, whether it is applied in the variant, its gaps, and why |
| `county_fee_matrix(schedule, counties, types, params, fallback)` | Fees per home before the cut by county, structure type and draw; counties of applied cities get documented fees, the rest the fallback |
| `summary_rows(schedule, ledger)` | Low, central and high fee per home by city and prototype, and the central amount by category (the generated table in the method page) |
| `verify_text(schedule, doc_id, text)`, `check_documents(...)` | Quotes not found in a document's text; `check_documents` fetches every document with quotes into `data/raw/fee_schedules/` and also checks PDF checksums |

Bases: `flat`, `per_count` (× a prototype count, with a minimum),
`area_tiered` (first sq ft, then each 100 sq ft or fraction), `fixtures_tiered`,
`valuation_per_1000` (per $1,000 or fraction of a cost share × construction
cost, with a minimum and maximum), `percent_of_table_value`.

## Rules it enforces

- An application fee credited to the final permit (`credited_to`) adds only
  what exceeds that permit fee, so it is counted once.
- A city is applied only when every core category is documented or not
  applicable and it covers its whole county. Otherwise its county keeps the
  labelled judgement.
- Tap and connection charges are their own categories; `ADU_LEDGER_RULE`
  states how they relate to the ADU pro forma's lines (connection fee yes,
  site work never).

## How to check it

```
uv run python -m visionhousing.features.fee_schedules --price    # prices at the ledger ends
uv run python -m visionhousing.features.fee_schedules --check    # re-fetch documents, check quotes
uv run pytest tests/test_fee_schedules.py
```

The tests price the Philadelphia single-family home by hand, show the filing
fee is counted once, show a not-reviewed core category blocks a city, check
the fourplex electrical permit follows the ledger cost, check the benchmark is
untouched, and run the variant on the synthetic test state (with and without
a documented county) through the fiscal identity.

## Limits

- Only Philadelphia's schedule is complete. Pittsburgh's fee book refuses
  scripted reading; only the water authority's single-family amounts are
  documented there.
- Prototype quantities (fixtures, meter sizes, sheets, review hours) are team
  design assumptions, kept as low and high readings.
- Street-opening permits in Philadelphia are not reviewed, so its total is a
  floor for them.
- No inflation adjustment: dollars of the schedule in force when read.
