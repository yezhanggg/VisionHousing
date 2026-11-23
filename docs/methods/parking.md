# Method: parking reduction (benchmark 4)

## The question

How many more homes would be permitted in 2027–2031 if residential parking
minimums were removed? The Handbook asks teams to state what developers still build.

## What developers still build

Decision D-08: after the minimums go, projects still build about 60% of the spaces
once required in transit-rich tracts (range 45–75%) and 85–100% elsewhere (central
92.5%). Lenders, buyers and tenants still want parking, so most of the saving
appears only where transit makes cars optional. Both shares are ledger entries
(`parking.share_still_built_transit_rich`, `parking.share_still_built_elsewhere`) and
can be changed per run. If both are 1, nothing changes and the effect is exactly zero.

## The chain

1. Spaces required per unit before the reform: 1.5 for apartments (5+ units), 2.0
   for 2-unit and 3–4-unit buildings (draft judgement; a sample of Pennsylvania
   codes will replace them). 1-unit buildings are not reached: the permit data
   cannot separate townhouses from detached homes, so townhouse savings are left out.
2. Spaces avoided = required × (1 − share still built).
3. Cost per space: apartments use the tract class's mix of surface, above-ground
   structured and underground spaces; 2–4-unit buildings are mostly surface
   (`parking.mix_surface_attached`). Costs per space are sourced from published cost
   reviews (surface about $8,000 with land; structured about $52,000 and underground
   about $73,000 excluding land).
4. Saving per unit ΔC = spaces avoided × cost per space.
5. Cost channel: baseline × ε × (ΔC ÷ V) × k, with the same ε and V as the fee
   reform (see [fees.md](fees.md)) and the general pass-through share
   `supply.saving_passthrough_k`.
6. Site yield: baseline × `parking.site_yield_gain` × share of spaces avoided. The
   gain (central 5%, range 0–15%) is the extra units a site could hold if all its
   parking land were freed; it is scaled by the share actually freed, so it is small
   away from transit.

## Transit-rich tracts

A tract is transit-rich when at least half of its homes are in census blocks whose
centre is within ½ mile of a rail station or ¼ mile of a bus stop with 64 or more
weekday departures (decision D-07), or when a major stop lies inside the tract. The
second test matters where blocks are large; in cities, small tracts that contain a
station are mostly within walking distance of it. Distances are straight-line.

Baseline permits are known by county, not tract. Each county's saving for a building
type is therefore the two classes' savings weighted by the share of the county's
existing homes of that type in transit-rich tracts. Everything after the saving is
linear, so this weighting is exact for the cost channel.

## Pro-forma cross-check

As for fees: the saving (times k) lowers each pro forma's break-even, and the rise in
the share of sites that pencil gives a second estimate, reported side by side.

## What this cannot tell you

- Whether lenders in a given market would finance projects with fewer spaces.
- Effects on street parking or on nearby residents.
- Whether freed land is used for homes or for other uses.
- Savings on townhouses counted as 1-unit buildings in the permit data.
