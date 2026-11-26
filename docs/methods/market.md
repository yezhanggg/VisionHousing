# Method: project choice and a shared market

The headline package adds the reforms up with fixed rules (see
[comparisons.md](comparisons.md) and the package rules in `engine/packages.py`).
Two questions are left open by those rules:

1. **Which project does a lot get?** A single-family lot that both the ADU rule
   and the missing-middle rule reach can take an ADU, a 2-6 unit building, a
   conventional new house, or nothing. Today's package lets ADUs keep their lots
   and removes a ledger share of them from the missing-middle pool.
2. **Can the area build it all?** Builders, lenders and buyers in an area can
   take on only so many new homes a year. Today's package has no such limit.

The package option `market` answers both, as named alternatives beside the
headline. The benchmark package never changes: without the option every number
is exactly today's. The code is `src/visionhousing/engine/market.py`; the
rules are in `assumptions/market.yaml` (each with an id); the two uncertain
numbers are ledger entries in `assumptions/parameters.yaml`.

| Option | What it does |
|---|---|
| *(none)* | Today's package rules (the headline). |
| `choice` | Project choice on the same lots, in the same draws. |
| `choice+capacity` | Project choice, then a shared market capacity limit by county group. The no-constraint case (choice alone) is reported beside it. |

## Same lots, same draws

Every case is built from the same run: the same seed, the same draw of every
uncertain input and the same position of the baseline in its likely range. The
ADU and missing-middle reforms are rerun once each with the package's
interactions (fee cut, parking) already in their pro formas. Every difference
between cases is worked out **draw by draw**. The other reforms (TOD, parking,
fees) keep their draws exactly.

## 1. Project choice (`choice`, rule `market.choice`)

### The options on a lot

| Use | Worth to the owner or builder | From |
|---|---|---|
| ADU | The ADU pro forma's value test: value-uplift share x the home's value - the ADU's total cost (0 when negative) | `adu.value_uplift_share`, the ADU pro forma |
| 2-6 unit building | The land bid of the best prototype that fits the lot (duplex, fourplex or six-unit building) | [missing_middle.md](missing_middle.md), "Prototypes" |
| Conventional new house | The same redevelopment pro forma with one larger home (`market.conventional_home_size_sqft`), minus demolition | `scenarios.prototypes.conventional_bid` |
| Retain / defer | Keeping the house as it is; or the chosen project does not start inside 2027-2031 (take-up) | the funnels' take-up steps |

A builder buys a lot only at its price: the home's value x (1 + acquisition
premium). A 2-6 unit building wins a lot when its bid covers that price, is
above the conventional house's bid, and, where an ADU also pays, also covers the
ADU's value to the owner (the owner would otherwise keep the house and add the
ADU). In home-value terms the building wins where

    home value <= min(bid / (1 + premium), (bid + ADU cost) / (1 + premium + uplift share))

and the bid is above the conventional house's bid. This is read from the
tract's home-value brackets, the same way the missing-middle funnel reads them.

### The shared lots, counted once

Per tract and draw, on the lots both rules reach (missing middle's eligible
lots x the share the ADU rule exposes), with

- `pA` = share where an ADU pays (lots with room x the ADU's feasible share),
- `f_alone` = share where a 2-6 unit building pencils,
- `f_conv` = share where it pencils and outbids a conventional house,
- `f_win` = share where it also beats the ADU's value to the owner,

each lot goes to exactly one use:

| Use | Share of the shared lots |
|---|---|
| ADU | `pA x (1 - f_win)` |
| 2-6 unit building | `(1 - pA) x f_conv + pA x f_win` |
| Conventional new house | `(1 - pA) x (f_alone - f_conv)` |
| Retain | `(1 - pA) x (1 - f_alone)` |

The four shares add up to 1, so a lot is counted once (the tests check it draw
by draw). Whether an ADU pays (a rent test) and whether a building pencils (a
home-value test) are taken as independent inside a tract. The conventional
house competes with the 2-6 unit building only.

Lots that only one rule reaches keep that rule's own funnel. Missing-middle
lots outside the ADU rule's reach still compare the building with a
conventional house.

### What changes in the package

- **ADU**: loses its homes on the lots a 2-6 unit building wins: ADUs on lot
  types missing middle reaches x the single-family-only zoned share x `f_win`.
- **Missing middle**: is rerun with the rivals (`scenarios.missing_middle`,
  option `rivals`): feasible lots = eligible x `((1 - a) f_conv + a f_win)`,
  where `a` is the share of its eligible lots where an ADU pays.
- **Today's overlap rule** (`missing_middle.adu_lot_overlap_share`) is not
  used: choice replaces it.

The change from today's package is shown as a bridge, each step paired draw by
draw: today's overlap rule undone; the fee and parking lift on the lots ADUs
use (today's package works the fee and parking interactions out on the
missing-middle run without those lots, so undoing the overlap leaves that lift
out; this step adds it back, shown only when the package has fee or parking
changes); the 2-6 unit building priced as the best
prototype per lot (instead of the benchmark's ledger mix, on the same lots with
the same fee and parking changes); lots won by a
conventional house; lots where the ADU is worth more; ADU lots where the
building is worth more. Today + the steps = choice, in every draw.

### Substitution records

Lots and homes that switch use: ADU lots that take a 2-6 unit building
instead; 2-6 unit lots that keep the house and add an ADU; 2-6 unit lots that
go to a conventional new house. The lot ledger reports the shared lots by
chosen use, and the lots whose chosen project does not start inside the
window (deferred).

## 2. Shared market capacity (`choice+capacity`, rule `market.capacity`)

- **County groups.** Counties that share builders, lenders and buyers: the
  pack's "places like this one" groups (`typology_county`). A state without
  them is one group.
- **Past peak.** The highest yearly total of all permits (every structure
  type) in the group over the last `peak_window_years` years of the county
  permit series (Census Building Permits Survey). The group record names the
  year and the window.
- **Cap.** `market.capacity_peak_multiplier` (ledger range) x past peak,
  drawn once per draw for every group.
- **Room.** Cap - the baseline's permits in the group that year, never below 0.
- **Local permits take the room.** The cap is a count of permits in the area,
  so each reform takes room with its local permits: its homes after
  displacement / its kept share (1 - its displacement share), the "local
  gain" below. What fits is scaled back by the same kept share, so the
  capacity case, the shifted and pushed homes and the bridge are in statewide
  net homes, like the other cases. (Using homes after displacement would
  understate the room the package uses by the displacement share.)
- **Sharing.** Each year, if the package wants more than the room, every
  reform and county keeps the same share of what it wants (proportional).
- **Soft cap.** What does not fit waits for next year's room. Homes still
  waiting after 2031 are pushed past the window.

The units the package uses never exceed the room, in any group, year or draw
(tested). Homes on time + moved later + pushed past = homes the package wants,
in every draw. Because the room is what the baseline leaves, the capacity case
reacts strongly to the baseline's position in its likely range: in a draw with
a high baseline, a group may have no room at all. The run reports the share of
draws where that happens, and each group's chance that the cap binds.

**No constraint.** The choice case is the no-constraint case. It is always
reported beside the capacity case, with its own sentence.

## Outputs, kept apart

For each case (today, choice, capacity):

| Output | Meaning |
|---|---|
| Local gain | Homes authorized where they are built, before displacement (each reform's homes / (1 - its displacement share)) |
| Statewide net authorizations | The headline measure: homes added after displacement |
| Net stock change | Statewide net - homes demolished because of the reforms |
| Stock losses | Homes demolished because of the reforms (redevelopment). Today's package scales each reform's own losses by its share kept in the package |
| Fee effects | The fee cut's own homes; today's case also shows its lift to ADU and 2-6 unit projects (the interactions) |
| Substitution | Lots and homes that switch use (choice) |
| Shifted years | Homes moved to a later year inside the window; homes pushed past 2031 (capacity) |

Each comes with its formula, ledger ids and sources, as a state record; the
statewide net also by county. Sentences are written by rule from the records
(templates in `assumptions/market.yaml`, numbers filled in by the engine).

## How to ask for it

- Python: `engine.run(state, reforms, market="choice")` or `market="choice+capacity"`;
  the result's `market` holds the cases (`Result.market.to_dict()`).
- API: `POST /run` and `POST /compare` take `"market": "choice"` or
  `"choice+capacity"` in the body (also per scenario in a comparison); the reply
  then carries a `market` block. Without it the reply is unchanged.
- API, comparisons path: `GET /states/{st}/comparisons?part=market` gives the
  five benchmarks under `choice+capacity` (asked for by name; `part=all` leaves
  it out), with the text form of every range added.

## What this cannot tell you

- It is a stress test of how the package adds up, not a forecast of builder
  behaviour. Both the conventional house's size and the capacity multiple are
  draft judgements with wide ranges.
- The ADU's value uses the pro forma's value test on each lot's home value;
  whether the ADU pays uses rents. The two are taken as independent.
- Builders moving work to another state, and the effect of the reforms on
  each other's prices (a shared demand curve), are not modelled.
- Capacity groups are typology groups, not measured labour or lending markets.
  The past peak may include a one-off rush (the group record names its year).
