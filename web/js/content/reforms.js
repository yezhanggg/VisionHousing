/**
 * The benchmark reforms the interface knows by id.
 * Words only. Every number about a reform (its assumptions and its results) comes from the engine
 * and the Assumption Ledger, never from this file.
 */
export const REFORMS = [
  {
    id: "adu",
    name: "Accessory dwelling units",
    short: "ADUs",
    line: "One added home on lots that already have a single-family house.",
  },
  {
    id: "middle",
    name: "Missing middle",
    short: "Missing middle",
    line: "Small multi-unit buildings on lots limited to one house today.",
  },
  {
    id: "tod",
    name: "Transit-oriented density",
    short: "Transit density",
    line: "More height or floor area near major transit stops.",
  },
  {
    id: "parking",
    name: "Parking reduction",
    short: "Parking",
    line: "Lower or remove the parking that new homes must provide.",
  },
  {
    id: "fees",
    name: "Development fee reduction",
    short: "Fees",
    line: "Cut the fees charged on each new home.",
  },
];

export const REFORM_IDS = REFORMS.map((r) => r.id);

/** Find a reform by id, or `undefined`. */
export function reformById(id) {
  return REFORMS.find((r) => r.id === id);
}
