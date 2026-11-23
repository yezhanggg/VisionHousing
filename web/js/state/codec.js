/**
 * Scenario codec: the full scenario lives in the address, after the route.
 *
 *   #/reform/adu?v=1&s=PA&r=adu,fees&a=adu.takeup~h,fees.cut~0.5&p=policymaker
 *
 *   v  codec version (always written; links from a newer version fall back to defaults)
 *   s  state, a two-letter postal code from the known list
 *   r  reforms switched on, comma-separated known ids, written in a fixed order. Old ids
 *      listed in `known.aliases` (for example `middle` for `missing_middle`) are read as
 *      their new id, so earlier share links keep working
 *   a  assumption overrides, `ledger.id~choice`, where choice is l / c / h (low, central,
 *      high) or a plain decimal number (a custom value)
 *   p  preset name (for example a slide-deck preset)
 *
 * Every field is checked against a pattern and a length limit. Anything unknown or out of
 * bounds is dropped and reported in `dropped`, never guessed. The module is pure: no
 * browser globals, so it is easy to test.
 */

export const CODEC_VERSION = 1;

export const LIMITS = Object.freeze({
  queryChars: 2000,
  reforms: 8,
  overrides: 40,
  keyChars: 64,
  valueChars: 16,
  presetChars: 24,
});

export const DEFAULT_STATE = "PA";

const KEY = /^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+){0,3}$/;
const CHOICE = /^[lch]$/;
const NUMBER = /^-?\d{1,9}(?:\.\d{1,6})?$/;
const PRESET = /^[a-z0-9][a-z0-9-]*$/;
const STATE = /^[A-Z]{2}$/;

/** A fresh default scenario. */
export function defaultScenario() {
  return { state: DEFAULT_STATE, reforms: [], overrides: {}, preset: "" };
}

/** Split `a=1&b=2` into a Map, keeping the first value of each key. */
function parsePairs(query) {
  const out = new Map();
  for (const part of query.split("&")) {
    if (!part) continue;
    const eq = part.indexOf("=");
    const key = eq < 0 ? part : part.slice(0, eq);
    let value = eq < 0 ? "" : part.slice(eq + 1);
    try {
      value = decodeURIComponent(value.replace(/\+/g, " "));
    } catch {
      value = "";
    }
    if (!out.has(key)) out.set(key, value);
  }
  return out;
}

/**
 * Read a scenario from the query part of the address.
 * @param {string} query text after "?" (without it)
 * @param {{states: string[], reforms: string[], aliases?: Object<string, string>}} known
 *   allowed state codes and reform ids, and old reform ids mapped to their new id
 * @returns {{scenario: object, dropped: string[]}}
 */
export function decodeScenario(query, known) {
  const scenario = defaultScenario();
  const dropped = [];
  if (!query) return { scenario, dropped };
  if (query.length > LIMITS.queryChars) {
    return { scenario, dropped: ["the link is too long"] };
  }
  const pairs = parsePairs(query);
  const version = pairs.get("v");
  if (version !== undefined && version !== String(CODEC_VERSION)) {
    return { scenario, dropped: [`unknown link version ${version.slice(0, 8)}`] };
  }

  const state = pairs.get("s");
  if (state !== undefined) {
    if (STATE.test(state) && known.states.includes(state)) scenario.state = state;
    else dropped.push("state");
  }

  const reforms = pairs.get("r");
  if (reforms) {
    const wanted = new Set();
    const aliases = known.aliases || {};
    for (const raw of reforms.split(",").slice(0, LIMITS.reforms * 2)) {
      const id = Object.hasOwn(aliases, raw) ? aliases[raw] : raw;
      if (known.reforms.includes(id)) wanted.add(id);
      else if (id) dropped.push(`reform ${id.slice(0, 16)}`);
    }
    scenario.reforms = known.reforms.filter((id) => wanted.has(id)).slice(0, LIMITS.reforms);
  }

  const overrides = pairs.get("a");
  if (overrides) {
    for (const item of overrides.split(",").slice(0, LIMITS.overrides * 2)) {
      const [key, value, extra] = item.split("~");
      const ok =
        extra === undefined &&
        key &&
        key.length <= LIMITS.keyChars &&
        KEY.test(key) &&
        value &&
        value.length <= LIMITS.valueChars &&
        (CHOICE.test(value) || (NUMBER.test(value) && Number.isFinite(Number(value))));
      if (ok && Object.keys(scenario.overrides).length < LIMITS.overrides) {
        scenario.overrides[key] = CHOICE.test(value) ? value : String(Number(value));
      } else if (item) {
        dropped.push(`assumption ${item.slice(0, 24)}`);
      }
    }
  }

  const preset = pairs.get("p");
  if (preset) {
    if (preset.length <= LIMITS.presetChars && PRESET.test(preset)) scenario.preset = preset;
    else dropped.push("preset");
  }

  for (const key of pairs.keys()) {
    if (!["v", "s", "r", "a", "p"].includes(key)) dropped.push(`field ${key.slice(0, 8)}`);
  }
  return { scenario, dropped };
}

/**
 * Write a scenario as a query string (without "?"). Only fields that differ from the
 * defaults are written, except the version and the state, which are always written so a
 * shared link says plainly which state it is about.
 */
export function encodeScenario(scenario, known) {
  const parts = [`v=${CODEC_VERSION}`, `s=${scenario.state || DEFAULT_STATE}`];
  const reforms = known.reforms.filter((id) => (scenario.reforms || []).includes(id));
  if (reforms.length) parts.push(`r=${reforms.join(",")}`);
  const keys = Object.keys(scenario.overrides || {}).sort();
  if (keys.length) parts.push(`a=${keys.map((k) => `${k}~${scenario.overrides[k]}`).join(",")}`);
  if (scenario.preset) parts.push(`p=${scenario.preset}`);
  return parts.join("&");
}

/** The screens plus the slides view. Anything else falls back to the State screen. */
export const ROUTES = ["state", "reform", "compare", "place", "method", "slides"];

/** A place in the address: a 5-digit county or an 11-digit tract code, e.g. `#/place/42003`. */
const PLACE = /^(?:\d{5}|\d{11})$/;

/**
 * Split an address hash such as `#/reform/adu?v=1&s=PA` into a route.
 * @returns {{name: string, param: string, query: string, known: boolean}}
 */
export function parseRoute(hash) {
  const raw = (hash || "").replace(/^#/, "");
  const q = raw.indexOf("?");
  const path = q < 0 ? raw : raw.slice(0, q);
  const query = q < 0 ? "" : raw.slice(q + 1);
  const [name = "", param = ""] = path.replace(/^\/+/, "").split("/");
  const known = ROUTES.includes(name);
  let kept = "";
  if (known && name === "reform") kept = param.slice(0, 32);
  if (known && name === "place" && PLACE.test(param)) kept = param;
  return {
    name: known ? name : "state",
    param: kept,
    query,
    known,
  };
}
