/**
 * Writing a raw value from the API as text, for the few replies that carry no text form of
 * their own (ledger entries, evidence figures, the backtest table). It only rounds for
 * display; it never derives a new number. Run results always use the engine's own text
 * (`shown`), never this.
 */
const SHARE_UNITS = new Set(["share", "share per year", "fraction"]);

/** A number with commas and at most `digits` significant figures (3 by default). */
export function plain(value, digits = 3) {
  if (value === null || value === undefined || value === "") return "—";
  const v = Number(value);
  if (!Number.isFinite(v)) return String(value);
  if (v === 0) return "0";
  const rounded = Number(v.toPrecision(digits));
  return rounded.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

/** A value with its unit: shares as percents, dollars with "$", others "value unit". */
export function withUnit(value, unit = "") {
  if (value === null || value === undefined) return "—";
  if (typeof value !== "number") return String(value);
  if (SHARE_UNITS.has(unit)) return `${plain(value * 100)}%${unit === "share per year" ? " a year" : ""}`;
  if (unit.startsWith("$")) return `$${plain(value)}${unit.slice(1)}`;
  return unit ? `${plain(value)} ${unit}` : plain(value);
}

/** A 0-1 share as a percent ("85%"). */
export function percent(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return `${plain(Number(value) * 100)}%`;
}

/** The first characters of a fingerprint, enough to tell two runs apart. */
export function shortHash(hash, n = 12) {
  return hash ? String(hash).slice(0, n) : "—";
}
