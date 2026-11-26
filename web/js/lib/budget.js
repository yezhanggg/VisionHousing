/**
 * The ADU budget-and-target grid in the browser: which tested options meet a target.
 *
 * `gridLookup(grid, {target, budget, award, accept})` is the browser twin of
 * `grid_lookup` in src/visionhousing/scenarios/adu_budget.py (tests/test_budget_target.py
 * runs both over the same grids and checks they agree, field by field). It only picks cells
 * and compares numbers with the inputs; it writes no number. Nothing is interpolated: a
 * target between two tested ones is a bracket of the two, one outside them is "outside the
 * tested range". The text of every number shown comes from the server's `view`
 * (src/visionhousing/api/budget_view.py), found by `cellKey`.
 *
 * Flags: input_missing, outside_tested_range, between_tested_values, budget_exceeded,
 * no_tested_option_meets_both.
 */

/** A number as the server's key writes it (whole numbers without a decimal point). */
const plain = (v) => String(Number(v));

/** `arm|appropriation|award`, as `cell_key` in api/budget_view.py. */
export function cellKey(c) {
  return `${c.arm}|${plain(c.appropriation)}|${plain(c.award)}`;
}

/** A target as the grid's own key writes it (Python `f"{t:g}"` for the tested targets). */
export function targetKey(t) {
  return plain(t);
}

function brief(c, key) {
  const m = c.target_met[key];
  return {
    arm: c.arm,
    appropriation: c.appropriation,
    award: c.award,
    label: c.label,
    share_meeting_target: m.share,
    mc_se: m.mc_se,
    net_public_cost_p50: c.net_public_cost.p50,
    added_p10: c.added.p10,
    added_p50: c.added.p50,
    added_p90: c.added.p90,
    pareto: Boolean(c.pareto?.[key]),
  };
}

const num = (v) => (v === null || v === undefined || v === "" ? null : Number(v));

/**
 * Answer a budget-and-target question from the grid (no interpolation).
 * Inputs are numbers or null; returns the same fields as the Python function except `text`.
 */
export function gridLookup(grid, { target = null, budget = null, award = null, accept = null } = {}) {
  const s = grid.settings;
  const flags = [];
  const brackets = {};
  const out = { flags, brackets };
  const t = num(target);
  const b = num(budget);
  const a = num(award);
  if (t === null) {
    flags.push("input_missing");
    return out;
  }
  const tested = s.targets;
  const key = targetKey(t);
  if (!tested.includes(t)) {
    if (t < tested[0] || t > tested[tested.length - 1]) flags.push("outside_tested_range");
    else {
      flags.push("between_tested_values");
      brackets.target = [Math.max(...tested.filter((x) => x < t)), Math.min(...tested.filter((x) => x > t))];
    }
    return out;
  }
  for (const [name, value, vals] of [
    ["budget", b, s.appropriations],
    ["award", a, s.awards],
  ]) {
    if (value === null) continue;
    if (value < vals[0] || value > vals[vals.length - 1]) {
      if (name === "award" || value < vals[0]) flags.push("outside_tested_range");
    } else if (!vals.includes(value)) {
      flags.push("between_tested_values");
      brackets[name] = [Math.max(...vals.filter((g) => g < value)), Math.min(...vals.filter((g) => g > value))];
    }
  }
  const cells = grid.cells.filter(
    (c) => a === null || c.arm === "law_only" || !s.awards.includes(a) || c.award === a,
  );
  const within = cells.filter((c) => b === null || c.appropriation <= b);
  if (b !== null && within.length < cells.length) flags.push("budget_exceeded");
  const acc = accept === null || accept === undefined || accept === "" ? 0 : Number(accept);
  const meets = within.filter((c) => c.target_met[key].share >= acc);
  if (!meets.length) flags.push("no_tested_option_meets_both");
  // A stable sort by median net cost, as Python's sorted().
  const byCost = meets
    .map((c, i) => [c, i])
    .sort((x, y) => x[0].net_public_cost.p50 - y[0].net_public_cost.p50 || x[1] - y[1])
    .map(([c]) => c);
  Object.assign(out, {
    target: t,
    accept: acc,
    options: byCost.map((c) => brief(c, key)),
    pareto: within.filter((c) => c.pareto?.[key]).map((c) => brief(c, key)),
    over_budget: cells.filter((c) => !within.includes(c)).map((c) => brief(c, key)),
  });
  return out;
}
