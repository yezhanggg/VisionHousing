/**
 * Runs the browser's budget-grid lookup (web/js/lib/budget.js) in Node, for
 * tests/test_budget_target.py: the same grid and inputs must give the same answer as the
 * server's grid_lookup (src/visionhousing/scenarios/adu_budget.py), field by field.
 *
 * Input (stdin, JSON): {"grid": {...}, "cases": [{target, budget, award, accept}]}.
 * Output (stdout, JSON): [{lookup, keys: {options, pareto, over_budget}}] in order.
 */
import { cellKey, gridLookup } from "../../web/js/lib/budget.js";

const chunks = [];
for await (const c of process.stdin) chunks.push(c);
const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const out = input.cases.map((c) => {
  const lookup = gridLookup(input.grid, c);
  const keys = {};
  for (const k of ["options", "pareto", "over_budget"]) if (lookup[k]) keys[k] = lookup[k].map(cellKey);
  return { lookup, keys };
});
process.stdout.write(JSON.stringify(out));
