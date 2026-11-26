# Method: the ADU two-lever case (predictable approval and financing access)

This note explains two follow-up questions the tool asks about the ADU reform, on the same draws as the benchmark:

1. **Timing.** If the slowest ADU reviews got faster, how many permits that would have come after 2031 move into 2027–2031?
2. **Financing.** If a state program paid part of the owner's cash share, and lenders counted part of the ADU's rent, how many more ADUs become feasible?

The code is `src/visionhousing/scenarios/adu_levers.py`. The ADU funnel itself is in [`adu.md`](adu.md).

## Two answers, kept apart

| Answer | Comes from | Meaning |
|---|---|---|
| **Authorizations moved into 2027–2031** | timing lever | Permits issued earlier. No new application. |
| **Newly feasible authorizations** | financing lever | More owners can build. New applications. |

The timing lever never adds a home. It only changes when a permit is issued. A timing-only run has zero newly feasible authorizations by construction.

## Three named variants

Run with the reform option `{"variant": name}` (`assumptions/variants.yaml`). The benchmark preset is unchanged: each lever has a setting that is fixed at "no change" in the ledger, and a variant gives that setting the range of a second entry.

| Variant | Lever setting → range it takes |
|---|---|
| `adu_timing_reliable` | `adu.timing_tail_pull_share` → `adu.timing_reliable_tail_pull_share` |
| `adu_financing_access` | `adu.financing_program_amount` → `adu.financing_access_program_amount`; `adu.financing_rent_income_share` → `adu.financing_access_rent_income_share` |
| `adu_both` | all three |

Every ledger id keeps its own random stream, so draw *i* of a variant shares all its other inputs with draw *i* of the benchmark. The paired change is taken draw by draw:

```
paired change = variant added units − benchmark added units = newly feasible + moved into the window
```

The lever card (`GET /lever/{state}?r=adu`) lists these variants beside the tight and easy financing cases (`assumptions/cards.yaml`).

## The timing lever: a lag model

The chain is: adopted law → local implementation date → applications → permit issued.

- **Implementation date.** Applications start after the ADU start delay (`adu.start_delay_years`; 0 in the benchmark, which is in force on January 1, 2027).
- **Applications.** The benchmark's take-up is counted in permits issued, so it already carries today's approval times. The lag model files applications through each year in proportion to the ramp `ramp(t)` (twelve mid-month filing dates a year), then scales them so that, under today's lag, permits issued in 2027–2031 equal the benchmark's added units in that draw. Applications filed in 2027–2031 that are still waiting at the end of 2031 are the projects "beyond 2031".
- **Projects beyond 2031 stay in the simulation** for `adu.timing_simulation_years_beyond` (10) more years. Those still waiting at the end are reported as pending (right-censored), never dropped or counted as refused.
- **Lag.** Days from application to permit are lognormal: `F(x) = Φ(ln(x / m) / s)`, with median `m` (`adu.permit_lag_median_days`) and log spread `s` (`adu.permit_lag_log_sd`).
- **The lever** pulls the slow tail toward the median. Every lag above the median moves a share `π` of the way to it: `lag' = m + (1 − π)(lag − m)`. P75 and P90 fall; the median stays; no lag gets longer. The new curve is `F'(x) = F(m + (x − m)/(1 − π))` for `x ≥ m`.
- **Moved into the window:**

  ```
  W  = permits issued 2027–2031 under F,  W' = the same under F'
  moved into the window = added units (before timing) × (W'/W − 1)
  ```

- **What timing holds fixed.** Applicants and the chance of being issued a permit are the same in both runs. So the applications filed in 2027–2031 (the "five-year plus beyond" total) are the same; only the share issued inside the window changes. Tests check this in every draw.

### Why the lag is labelled illustrative/transferred

No Pennsylvania permit table publishes an application date. Philadelphia's table has issue dates only (`assumptions/calibration/permit_lag_phl.yaml`). The ranges come from two calibration files, read on every run (`lag_anchors()`), and a test checks the ledger ranges bracket them:

| Source | What it measures | Used for |
|---|---|---|
| Philadelphia, zoning permit issued → building permit issued, one-unit new homes | P50 141 days, P90 491 days (Kaplan-Meier; pending zoning permits right-censored) | Low end of the median (a floor: it leaves out zoning review); central spread `ln(491/141)/1.2816` |
| California APR, ADU application → permit (`apr_timing_donor_ca.yaml`) | median 196–670 days, P75 591–973 days across California and three cities | High end of the median; low and high spread `ln(P75/median)/0.6745` |

Right-censoring is handled as in those files: the lag curve describes applications that are issued, and an application still waiting at the end of the simulation is kept as pending.

## The financing lever

From the homeowner pro forma ([`proforma.md`](../modules/proforma.md)), the owner's cash is `C0 = total cost × (1 − loan share)`.

1. **Program money.** A state ADU program offers up to the program amount at the program rate (`adu.financing_access_program_amount`, `adu.financing_program_rate`). California's ADU grant paid up to $40,000 of predevelopment costs; that is the high end.
2. **Counted rent.** A lender that counts a share `r` of the expected ADU rent as income (`adu.financing_access_rent_income_share`) offers up to `r × rent / loan payment per $` more. Fannie Mae's Selling Guide lets lenders count rent from an existing ADU, capped at 30% of qualifying income; the access case assumes the same treatment for a planned ADU, so `r` is a judgement range.
3. **Cheapest dollar first.** Each dollar of `C0` comes from the cheaper of the two offers first (the lower monthly payment per dollar, `min(p, l)`; program money on a tie); the other covers what is left. So `P + R ≤ C0`, and when both offers together exceed the need, the dearer one is the one cut back. **New owner cash** `C1 = C0 − P − R`.
4. **Monthly cost.** An owner uses each new dollar only where it costs no more per month than own cash. The monthly cost falls by `P (c − min(p, c)) + R (c − min(l, c))`, with `c`, `p`, `l` the payment per dollar of own cash, program money and the loan. The break-even rent falls by `coverage × that / (1 − running-cost share)`, so more lots pass the rent test.
5. **Owners who lacked the cash.** Take-up rises by `A(C1) / A(C0)`, with `A(C) = 1 / (1 + (C / C50)^k)` the share of would-be builders whose savings or equity cover `C` (`adu.owner_cash_capacity_median`, `adu.owner_cash_capacity_shape`). Today's take-up already reflects today's cash need, so the factor is exactly 1 when nothing changes.

```
newly feasible = added units with the financing lever − benchmark added units (both under today's lag)
```

More access never gives fewer homes: a larger program or more counted rent lowers `C1`, never raises the monthly cost, and `A` falls with cash (tested). The tight and easy financing cases stay the variants `adu_financing_tight` and `adu_financing_easy` (loan share and rate spread), described in [`adu.md`](adu.md).

## Both levers together

Financing first (more applications), then timing on that larger pool. `moved into the window = (benchmark + newly feasible) × (W'/W − 1)`.

## Years 2027–2031

The change in each year is

```
change(y) = newly feasible × benchmark year share(y) + pool × (I'(y) − I(y)) × kept
```

with `I`, `I'` the lag model's permits issued in year `y` per unit of benchmark in-window permits, and `kept = moved into the window ÷ (pool × (W'/W − 1))`: a tract cannot build more than its feasible lots, so the moved total can fall short of the lag model's, and the yearly split is scaled to what is kept. The five yearly changes add up to the paired change in every draw (tested). Because the ramp never falls, a faster lag never lowers any year. The outcome contract's yearly series reports *shifted into the window* and *newly feasible* apart for these runs (`docs/methods/outcome.md`).

## Records each lever run carries

`lever_benchmark_net`, `lever_net`, `lever_paired_change`, `lever_moved_into_window`, `lever_newly_feasible`, `lever_change_2027` … `lever_change_2031`; with timing: `lever_window_applications`, `lever_after_window_benchmark`, `lever_after_window`, `lever_pending_at_end`, `lever_lag_p90_days_today`, `lever_lag_p90_days_reliable`; with financing: `lever_owner_cash_before`, `lever_owner_cash_after`, `lever_cash_access_factor`. County splits of moved and newly feasible come with the lever card. Every record is a P10 / P50 / P90 range with its formula and ledger ids.

## The binding factor on the lever card

Each lever run also works out both levers' potential on the same draws (each at its variant range) and names the one with the larger P50 effect: **timing** (moved into the window) or **financing** (newly feasible). When neither reaches `adu.lever_binding_min_share` (2%) of the benchmark's P50, it names **eligibility**: who gains a legal right to build an ADU and has room for one. The card text reads, for example: "This case estimates which authorizations move into the five-year window when review is faster (P50 …) and which become newly feasible with financing access (P50 …). The largest remaining constraint in this group is household financing under the selected assumptions." This potential feeds only the binding text: every applied number of the run uses the run's own lever settings, so a setting left at 0 (for example the counted rent in a program-only run) stays at 0.

## A program with a budget, and take-up on a finite stock of sites

The financing variant above has no budget: every owner who wants help gets it. Its paired change is a scenario difference under that assumption, not the result of any appropriation, and must never be shown as the result of a budget-capped program. Four more variants answer the questions a committee asks next. They use a different take-up method, so they get their own ids; the benchmark and the three variants above are unchanged.

| Variant | What it adds |
|---|---|
| `adu_hazard_reference` | The benchmark worked out with the hazard on surviving sites (no lever). Its gap to the benchmark is the disclosed method sensitivity. |
| `adu_timing_conserved` | Predictable review on the hazard method; projects are counted over an extended horizon. |
| `adu_finance_budget` | A budget-capped grant program (illustrative $20 million, $25,000 awards). |
| `adu_finance_budget_loan` | The same program paid as loans, with repayment and loss. |

The code is `src/visionhousing/scenarios/adu_budget.py`.

### Take-up on a finite stock (the hazard method)

The benchmark multiplies feasible lots by a yearly take-up and the ramp, and caps the total at the feasible lots. That treats each year as if no site had been used before. The hazard method keeps count:

```
applications(t) = remaining sites(t) x hazard(t)
remaining sites(t + 1) = remaining sites(t) - applications(t)
hazard(t) = min(kappa x take-up x ramp(t), 1)
```

A site used in one year cannot file again. Applications then reach a permit after the application-to-permit lag (the same lag curve as the timing lever), so a project filed late in 2031 can be issued in 2032. The simulation runs 10 more years (`adu.timing_simulation_years_beyond`); projects still waiting at the end are pending, never dropped.

**Why `kappa`.** The take-up evidence is counted in permits, so it already carries today's lag. `kappa` is set once per draw so that, with no depletion and today's lag, permits in 2027–2031 equal the benchmark's take-up x the ramp's sum. It is set with no start delay and today's lag. So a delayed start or a longer lag lowers permits in the window; it is not normalised away (tested).

**The benchmark keeps its method.** Using sites up can only lower the window, so the hazard method moves the benchmark's headline a little. The benchmark headline is therefore unchanged. Each hazard variant reports `hazard_reference_net` (law only, hazard method) and `hazard_method_difference` (that minus the benchmark, in the same draw) beside it. Every hazard variant's `lever_paired_change` is against the hazard reference, so the lever effect is never mixed with the method change; `lever_paired_change_vs_benchmark` adds the two.

### Predictable review, with projects conserved

Faster review changes when a permit is issued, never whether an application is filed. In the hazard method the applications do not depend on the lag at all, so the projects filed over the extended horizon are identical with and without faster review (record `timing_lifetime_difference`, 0 in every draw; tested). The window is reconciled by filing year:

```
moved into 2027-2031  = sum over filing years of applications x max(in-window share with the faster lag - today's, 0)
moved out of the window = sum over filing years of applications x max(today's in-window share - with the faster lag, 0)
change in the window = moved in - moved out
```

Pulling the slow tail toward the median never lengthens a lag, so "moved out" is 0. Records: `timing_moved_into_window`, `timing_moved_out_of_window`, `timing_after_window_{reference,variant}`, `timing_pending_{reference,variant}`. When a run also has a budget-capped program (a user setting both), the timing records compare the program arm with itself at the two lags, and `timing_lifetime_difference` is worked out the same way (applications with the faster lag minus without, same program), never set by hand.

### The budget-capped program

**Settings (illustrative, `assumptions/parameters.yaml`).** Appropriation `adu.budget_appropriation_illustrative`, award `adu.budget_award_amount_illustrative`, loan share `adu.budget_loan_share` (0 = grants). These are policy choices to test, not estimates.

**Behaviour (judgement ranges).** Eligible share `adu.budget_eligible_share`, award take-up `adu.budget_award_takeup`, administration share `adu.budget_admin_share`, loan loss `adu.budget_loan_loss_share`, public discount rate `adu.budget_public_discount_rate`.

**Who can be paid.** Two pools of sites in the same tracts and draws:

- newly legal sites (the benchmark's feasible lots), with the ramp after enactment;
- already legal sites (lots where an ADU is already allowed), at the full take-up rate. Their projects are in today's baseline; they matter because a program pays them too.

In each pool, *group 1* sites are feasible without help; *group 2* sites pass the rent test only with the award. An award replaces up to the award of the owner's cash (never more than the owner's own cash), so the break-even rent falls (more lots pass) and the hazard rises by `r = A(C1) / A(C0)` (owners who could not raise the cash; the same curve as the financing lever).

**Each program year (2027–2031).**

```
funded share = min(1, money left / dollars asked for this year)      (first come, first served; pro rata in the year it runs out)
q = eligible share x award take-up x funded share
applications = group 1 x (hazard x (1 - q) + min(hazard x r, 1) x q) + group 2 x min(hazard x r, 1) x q
awards = q x min(hazard x r, 1) x (group 1 + group 2)
awards to owners who would have filed anyway = q x hazard x group 1
spending = awards x min(award, owner cash)
```

Money for awards is `appropriation x (1 - administration share)`. Once it is spent, the funded share is 0: **a fully used budget funds no further award** (tested). With $2 million and $20,000 awards the ceiling is 100 awards before administration (90 with a 10% share) — a ceiling on funded awards, not 100 induced ADUs: many of those awards go to owners who would have built anyway (tested).

**Dollars.** Administration is charged on the money used (`award spending / (1 - administration share) x administration share`). Money never asked for is returned (`budget_unused_funds`), not a cost.

```
net public cost = administration + grants + loans x (loss share + (1 - loss share) x interest subsidy)
interest subsidy = max(0, 1 - payment factor at the program rate / payment factor at the public discount rate)
loan repayments = loans x (1 - loss share)
```

**Additional units.** The program's effect is the variant minus the law-only hazard reference, on the same draws and the same candidate cells:

```
additional authorizations = (1 - displacement) x (permits in 2027-2031 with the program - without)
additional comparable = the same per tract x the tract's detached share (B2's 'comparable' scope), the same review lag in both arms
moved from later years = (1 - displacement) x sum over the years after 2031 and over the site groups both arms have of max(applications without the program - with it, 0)
newly created = additional - moved from later years
```

"Moved from later years" counts owners the award only speeds up: their sites are missing from the later years of the extended horizon. The count is **paired**: sites are split into groups (newly legal or already legal pool x feasible without help or only with the award), and only a group that exists in both arms on the same draws can move a project. Each year's term is zero or more by construction (the program raises the yearly hazard in the window and leaves it unchanged after, so fewer sites remain), so the record is never negative. A site that would only have been used after the horizon is not counted as moved, so this is a lower bound.

Both arms use the same review lag. When a run also turns on faster review, the program's gain (headline and comparable) and its cost per comparable unit compare the program with no program at the faster lag, so units the faster review moves into the window are never credited to the program.

**Cost per unit and targets.** `budget_cost_per_additional_comparable_unit` is the net public cost divided by the additional comparable units **in each draw**, over the draws where the program adds at least `adu.budget_min_gain_units`; the share of the other draws is `budget_zero_gain_share`. It is never a ratio of two percentiles. For the illustrative target (`adu.budget_target_additional_units`), each draw also gives the awards, award take-up and appropriation needed (linear in awards, so a lower bound where a larger program would use up more sites), and `budget_target_met_share` with its Monte Carlo standard error.

### The budget-and-target grid

`visionhousing adu-budget-grid --state PA` (and `GET /api/v1/states/{st}/adu-budget-grid`) runs a small grid on the same draws (`assumptions/adu_budget_grid.yaml`): appropriations x award sizes x three arms:

| Arm | Law | Program |
|---|---|---|
| `law_only` | changed | none |
| `finance_only` | today's | on already legal lots |
| `both` | changed | on newly and already legal lots |

Every cell is against no change in the same draws. Each gives the added authorizations (headline scope and comparable), net public cost, applicants served, awards to owners building anyway, unused money, and for each target the share of draws meeting it with its Monte Carlo standard error (`sqrt(s (1 - s) / n)`). A cell is on the Pareto set for a target when no other tested cell has a lower median net cost and an equal or higher share (or the same cost and a higher share); cells that meet the target in no draw are left off. `both` also reports the program's own addition on top of the law.

Each cell also splits what happens beyond the window into two records, both against no change on the same draws:

```
moved in from later years = (1 - displacement) x sum over 2032 to the end of the extended span and over the site groups no change also has of max(applications with no change - in this arm, 0)
new projects after 2031   = (1 - displacement) x sum over years of applications from site groups no change does not have x (1 - share issued by the end of 2031)
```

"Moved in from later years" (`moved_from_later`) is only the projects no change would have filed after 2031 on the same sites and that this arm files earlier: owners the money speeds up. It is zero or more by construction. The law change itself moves nothing: against no change, newly legal sites file nothing at all, so their projects are new, not moved. Those that land after 2031 are "new projects after 2031" (`new_after_window`): created by the law (or by an award on a site that builds only with it), in the lifetime count but not in the window's added units. An earlier definition summed every feasible-without-help application after 2031 without pairing site groups; against no change that went negative for any cell with the law change because the law adds post-2031 applications. Cells without the law change (`finance_only`) give the same numbers under both definitions (tested).

A query (`target`, `budget`, `award`, `accept`) never interpolates. A tested value gives its cells. A value between two tested ones is marked `between_tested_values` with the bracket; one outside them is `outside_tested_range`. Cells above the budget are listed apart (`budget_exceeded`); when no tested cell within the budget meets the accepted share the answer says `no_tested_option_meets_both`; with no target it says `input_missing`. The grid also says, per arm and award, whether the share meeting each target rises with the appropriation (`monotone`), because project choice can make the response jump.

## What this cannot tell you

- The real application-to-permit lag in any Pennsylvania place. It is illustrative/transferred until a municipality publishes application dates.
- Refusals and withdrawals: the issue chance is held fixed. A rule that also changes how many applications are refused is not modelled.
- Who has the cash. The cash-capacity curve is a judgement range, not measured household savings.
- Whether any lender would count the rent of an ADU not yet built.
- The fiscal cost of the uncapped financing variant (`adu_financing_access`): it has no appropriation. The capped variants and the grid give a net public cost under illustrative settings and judgement ranges for eligibility, award take-up and administration.
- Who applies first. Awards go first come, first served within each year, the same for owners who would build anyway and for owners the award brings in; a program that targets the second group would do better, and one that is mostly claimed by the first would do worse.
- Effects after the simulation's end, and projects that would only come after it.
