/**
 * The reforms the interface knows, by the engine's own keys (GET /api/v1/reforms).
 * Words only. Every number about a reform (its assumptions and its results) comes from the
 * engine and the Assumption Ledger, never from this file.
 *
 *   group "benchmark"  the five Challenge reforms; together they are "the package"
 *   group "bill"       Pennsylvania bills. Each reaches the same lots as the ADU reform
 *                      (same `family`), so only one of them can be on at a time.
 *   group "extra"      the extra scenarios (`kind: "extra"` in GET /api/v1/reforms): questions
 *                      the benchmarks leave open. Never part of a default (the switches, "All
 *                      five reforms", Compare and the package stay on the benchmarks). The
 *                      screens list them only when the engine does, with its own title and
 *                      description (GET /api/v1/scenarios); which reforms each may not share a
 *                      package with also comes from there (ui/extras.js).
 */
export const REFORMS = [
  {
    id: "adu",
    family: "adu",
    group: "benchmark",
    name: "Accessory dwelling units",
    short: "ADUs",
    line: "One added home on lots that already have a single-family house.",
  },
  {
    id: "missing_middle",
    family: "missing_middle",
    group: "benchmark",
    name: "Missing middle",
    short: "Missing middle",
    line: "Small multi-unit buildings on lots limited to one house today.",
  },
  {
    id: "tod",
    family: "tod",
    group: "benchmark",
    name: "Transit-oriented density",
    short: "Transit density",
    line: "More height or floor area near major transit stops.",
  },
  {
    id: "parking",
    family: "parking",
    group: "benchmark",
    name: "Parking reduction",
    short: "Parking",
    line: "Lower or remove the parking that new homes must provide.",
  },
  {
    id: "fees",
    family: "fees",
    group: "benchmark",
    name: "Development fee reduction",
    short: "Fees",
    line: "Cut the fees charged on each new home.",
  },
  {
    id: "pa_hb2186",
    family: "adu",
    group: "bill",
    name: "PA House Bill 2186",
    short: "HB 2186",
    line: "One ADU by right on any residential lot. Replaces the ADU switch.",
  },
  {
    id: "pa_sb1346",
    family: "adu",
    group: "bill",
    name: "PA Senate Bill 1346",
    short: "SB 1346",
    line: "One ADU per single-family house, outside Philadelphia and Pittsburgh. Replaces the ADU switch.",
  },
  {
    id: "transit_zoning_funded_infrastructure",
    family: "transit_zoning_funded_infrastructure",
    group: "extra",
    name: "Transit zoning with funded infrastructure",
    short: "Funded transit zoning",
    line: "The transit upzoning, plus public money for the water, sewer and power it needs.",
  },
  {
    id: "commercial_land_housing",
    family: "commercial_land_housing",
    group: "extra",
    name: "Housing by right on commercial land",
    short: "Commercial land",
    line: "Apartment buildings allowed by right on land used for shops and offices.",
  },
  {
    id: "adaptive_reuse",
    family: "adaptive_reuse",
    group: "extra",
    name: "Adaptive reuse (office and retail conversions)",
    short: "Adaptive reuse",
    line: "Existing offices and shops converted into homes; counted outside the permit measure.",
  },
  {
    id: "starter_home_lots",
    family: "starter_home_lots",
    group: "extra",
    name: "Starter-home lots",
    short: "Starter lots",
    line: "Smaller minimum lots for new subdivisions on single-family land.",
  },
  {
    id: "fee_deferral_with_affordability",
    family: "fee_deferral_with_affordability",
    group: "extra",
    name: "Fee deferral or waiver with affordability terms",
    short: "Fee deferral",
    line: "Fees paid at occupancy (or waived) for rental projects that keep homes affordable.",
  },
];

export const REFORM_IDS = REFORMS.map((r) => r.id);
/** The five benchmark reforms: switched on together they are "the package". */
export const BENCHMARK_IDS = REFORMS.filter((r) => r.group === "benchmark").map((r) => r.id);
/** States whose bills are offered (Testland stands in for Pennsylvania in tests). */
export const BILL_STATES = ["PA", "TL"];

/** Old link ids, kept so earlier share links still open the same scenario. */
export const REFORM_ALIASES = { middle: "missing_middle" };

/** Find a reform by id (or an old alias), or `undefined`. */
export function reformById(id) {
  const key = REFORM_ALIASES[id] || id;
  return REFORMS.find((r) => r.id === key);
}

/** The extra scenarios this interface knows (ui/extras.js keeps those the engine lists). */
export const EXTRA_IDS = REFORMS.filter((r) => r.group === "extra").map((r) => r.id);

/** The reforms offered for a state: the benchmarks, plus its bills (the extras are listed apart). */
export function reformsFor(state) {
  return REFORMS.filter((r) => r.group === "benchmark" || (r.group === "bill" && BILL_STATES.includes(state)));
}
