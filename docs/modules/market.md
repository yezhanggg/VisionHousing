# Module: Project choice and a shared market (`src/visionhousing/engine/market.py`)

**What it does.** Builds the package under the package option `market`:
`"choice"` (each single-family lot that more than one reform reaches is used
once, for the use worth most) and `"choice+capacity"` (the same, then a soft cap
on all homes a county group can take on each year). It reports today's package,
project choice and the capacity case side by side, with local gain, statewide
net authorizations, net stock change, stock losses, fee effects, substitution
and shifted years kept apart. The method, in plain words, is in
[the market method](../methods/market.md).

**Why.** A reform package is not a sum of separate projects: the same lot can
take only one project, and the same builders, lenders and buyers serve every
reform in an area.

## Terms

- **Shared lots.** Single-family lots both the ADU and the missing-middle rules
  reach (missing middle's eligible lots x the ADU rule's exposed share).
- **Residual value.** What a use is worth on the lot: a builder's land bid
  (2-6 unit building, conventional house) or the ADU's value to the owner
  (value-uplift share x home value - ADU cost).
- **Room.** A county group's yearly permit cap minus the baseline's permits.
- **Soft cap.** Homes over the room wait for the next year's room instead of
  disappearing; those still waiting after 2031 are "pushed past the window".

## Files and functions

| Piece | What it does |
|---|---|
| `assumptions/market.yaml` | The modes (title, question), the choice rule (`middle_method: prototypes`, conventional rebuild on), the capacity rule (grouping, peak window, allocation, carry-over) and the sentence templates |
| `market.conventional_home_size_sqft`, `market.capacity_peak_multiplier` | The two uncertain numbers, ledger entries in `assumptions/parameters.yaml` (`used_by: packages`) |
| `load_settings` / `parse_settings` | Read and check the rules file (`LedgerError` on a missing or unknown setting) |
| `build_market(mode, ...)` | Called by `engine.core.compute` when `market` is set: reruns ADU (option `lot_detail`) and missing middle (option `rivals`) with the package's interactions, builds the cases and records |
| `capacity_allocate(wanted, group_of, room)` | The proportional soft cap: per group, draw and year, every part and county keeps the same share; the units used never exceed the room |
| `MarketOutcome` | The result: `cases`, `measures`, `parts`, `substitution`, `lots`, `timing`, `bridge`, `counties`, `groups`, `texts`, `notes`, and per-draw arrays (`state_draws`, `county_draws`, `draws`) |

Hooks in the reform modules (no stage changes unless asked):

- `scenarios.adu.adu_funnel(..., lot_detail=True)` keeps `AduLotDetail`
  (exposed share, share where an ADU pays by lot type, each type's share of
  ADUs, value-uplift share, ADU total cost).
- `scenarios.missing_middle.missing_middle_funnel(..., method="prototypes",
  rivals=MiddleRivals(...))` compares the best prototype with a conventional
  rebuild and the ADU's value on each lot, and keeps `MiddleChoice` (lot
  shares and units with and without each rival).
- `scenarios.prototypes.prototype_choice(..., rival=(u, C), floor=bid)` and
  `conventional_bid(...)`: the price test with a rival's value added and a
  conventional bid as floor. With neither, the result is the benchmark
  comparison bit for bit.

## Where it shows

- `engine.run(..., market=...)` -> `Result.market`; `Result.to_dict()` adds a
  `market` block only when the option is set, so today's output is unchanged.
- API: `POST /run` and `POST /compare` take `market` in the body (see
  [api.md](api.md)); `compare_rows` adds the `market` block to that column.

## Tests

`tests/test_market.py` (Testland): without the option the package is
unchanged; the today case equals the package draw by draw; each shared lot is
counted once (the four uses add up to the shared lots, none negative);
substitution equals the change in the ADU part; the bridge adds up from today
to choice; reforms outside the choice keep their draws; the same seed repeats
exactly; the cap is never exceeded and is shared proportionally; with no
constraint the capacity case equals choice; the outputs are kept apart and the
sentences are written from the records; the API carries the option.
