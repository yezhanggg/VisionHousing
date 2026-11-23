# Glossary

Plain definitions of the terms used in the tool and the documents.

| Term | Meaning |
|---|---|
| **ADU** | Accessory dwelling unit. A small second home on the same lot as a house, either detached (a separate building) or semi-detached (attached to the house). The benchmark reform allows one ADU of at least 600 sq ft per single-family lot. |
| **Missing middle** | Buildings with 2 to 6 homes, such as duplexes, triplexes and small apartment buildings. Common before the mid-1900s, now often not allowed on single-family lots. |
| **FAR** | Floor area ratio. Total floor area of a building divided by the area of its lot. A FAR of 2 on a 5,000 sq ft lot allows 10,000 sq ft of floor space. |
| **Pro forma** | A simple budget for a building project: what it costs to build and what it earns. If earnings cover costs and a normal return, the project "pencils out". |
| **Break-even** | The rent or home value at which a project just pays for itself. |
| **Baseline** | What happens under today's rules and economics, with no reform. Each reform is measured against it. |
| **P10, P50, P90** | Points in the range of results. P50 is the middle. There is a 10% chance the true value is below P10 and a 10% chance it is above P90. P10 to P90 is the "likely range". |
| **Likely range** | The span from P10 to P90. The tool reports ranges, not single numbers. |
| **Tier** | How rich the data behind a number is. Tier 0 uses national public data only. Tier 1 adds statewide layers such as building footprints. Tier 2 adds local parcel or zoning data. |
| **Take-up** | The share of lots where a reform's new option is feasible that actually get built on each year. The most uncertain input. |
| **Feasible share** | The share of a tract's homes or lots where a project pays off, from the pro forma. A tract can be 38% feasible. It is a share, not a yes or no. |
| **Permits** | Building permits. Official approval to build. The tool's outcome is housing units authorized by building permits, as counted by the Census Bureau's Building Permits Survey. |
| **Census tract** | A small statistical area, usually with 1,200 to 8,000 people. The model computes by tract. |
| **Major transit stop** | A rail or other fixed-guideway station, or a bus stop with at least 64 weekday departures. |
| **Assumption ledger** | The single file (`assumptions/parameters.yaml`) that holds every number the model needs but cannot read from data, with its range and source. |
| **Monte Carlo** | Running the model many times, each time drawing inputs from their ranges, to get a range of results. |
| **Tornado chart** | A chart that ranks inputs by how much the result moves when each one goes from low to high. |
| **Hindcast** | Running the model on a reform that already happened and comparing its range with what was actually permitted. |
| **Data pack** | The set of cleaned tables for one state that the engine reads, built by `make data`. |
