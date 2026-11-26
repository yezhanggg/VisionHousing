/**
 * The one way the screens ask for data.
 *
 * Two modes, chosen when the site is built (`VITE_DATA_MODE`):
 *
 *   live    (default) ask the FastAPI back end, at `VITE_API_BASE` (default `/api/v1`, the same
 *           site). Every scenario works, including custom assumptions.
 *   static  no server at all: read the precomputed JSON files that
 *           `visionhousing export-static` wrote into web/public/data/ (served at /data/...).
 *           Only what was exported can be shown: the default-assumption runs of each reform
 *           alone (and each state bill alone), the full package and the baseline, with their
 *           county maps and exports. Anything else raises a
 *           `LocalOnlyError` whose message says to use the local app.
 *
 * Each function returns the same JSON in both modes, because the exporter saves the back
 * end's own replies. The file layout is written down in src/visionhousing/export_static.py.
 */

const ENV = import.meta.env || {};

/** "live" or "static". */
export const DATA_MODE = ENV.VITE_DATA_MODE === "static" ? "static" : "live";
export const IS_STATIC = DATA_MODE === "static";
/** Where the live back end answers. */
export const API_BASE = (ENV.VITE_API_BASE || "/api/v1").replace(/\/+$/, "");
/** Where the static files are. */
export const DATA_BASE = `${(ENV.BASE_URL || "/").replace(/\/+$/, "")}/data`;

/** The plain note shown when a feature needs the live back end. */
export const LOCAL_ONLY = "Changing assumptions needs the local app (make app).";
/** The note for a mix of reforms the static site did not save. */
export const COMBO_ONLY =
  "This mix of reforms needs the local app (make app). This site holds each reform alone and all five together.";

/** The note for one reform the static site did not save (an extra scenario not exported). */
export const SCENARIO_ONLY =
  "This scenario needs the local app (make app). This site holds the benchmark reforms, the state bills and all five together.";

/** Why the static site cannot show a scenario: custom assumptions, an unsaved reform, or an unsaved mix. */
function whyNot(sc) {
  if (Object.keys(sc.overrides || {}).length) return LOCAL_ONLY;
  if (sc.preset && sc.preset !== "central") return LOCAL_ONLY;
  return (sc.reforms || []).length === 1 ? SCENARIO_ONLY : COMBO_ONLY;
}

/** The note for a state exported before the tract columns existed. */
const NO_TRACT_COLUMNS = "The tract columns for this state are not on this site; the local app (make app) shows them.";

/** Raised in static mode for anything that was not exported. */
export class LocalOnlyError extends Error {
  constructor(message = LOCAL_ONLY) {
    super(message);
    this.name = "LocalOnlyError";
  }
}

/** The back end's time limit passed (504); the run continues there. */
class SlowRunError extends Error {}

async function getJson(url, init = {}) {
  const reply = await fetch(url, { ...init, headers: { accept: "application/json", ...init.headers } });
  const body = await reply.json().catch(() => null);
  const message = body?.message || `the request failed (${reply.status})`;
  if (reply.status === 504) throw new SlowRunError(message);
  if (!reply.ok) throw new Error(message);
  return body;
}

const RETRY_MS = 15000;
const RETRIES = 6;

/**
 * Ask for something that runs the engine. A long run on a large state can pass the back
 * end's time limit (504); the run keeps going there and its result is cached, so ask again
 * a few times before giving up.
 */
async function retrying(ask) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await ask();
    } catch (err) {
      if (!(err instanceof SlowRunError) || attempt >= RETRIES) throw err;
      await new Promise((ok) => setTimeout(ok, RETRY_MS));
    }
  }
}

function post(path, body) {
  return retrying(() =>
    getJson(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

const slowGet = (url) => retrying(() => getJson(url));

/** Read one exported file; a missing file means "not in the static site" (`missing` says so). */
async function file(path, missing = LOCAL_ONLY) {
  const reply = await fetch(`${DATA_BASE}/${path}`, { headers: { accept: "application/json" } });
  if (reply.status === 404) throw new LocalOnlyError(missing);
  if (!reply.ok) throw new Error(`the data file failed to load (${reply.status})`);
  const value = await reply.json();
  // A screen file marked "$shared" refers to its state's shared pool (ST/pool.json).
  if (value && value.$shared) return unpack(value, await sharedPool(path.split("/")[0]));
  return unpack(value);
}

const pools = {};
/** A state's shared pool (`ST/pool.json`), read once. */
function sharedPool(st) {
  pools[st] ||= (async () => {
    const reply = await fetch(`${DATA_BASE}/${st}/pool.json`, { headers: { accept: "application/json" } });
    if (!reply.ok) throw new Error(`the data file failed to load (${reply.status})`);
    return makePool(await reply.json());
  })();
  return pools[st];
}

/** The shared pool as `{get(n)}`: entry n is what `{"$s": n}` stands for (exported for tests). */
export function makePool(raw) {
  const packed = raw && raw.$packed === 1;
  const entries = packed ? raw.$data : raw;
  const done = new Map();
  const pool = {
    get(n) {
      if (!done.has(n)) done.set(n, resolver(packed ? raw.$pool : [], pool)(entries[n]));
      return done.get(n);
    },
  };
  return pool;
}

/**
 * A function that puts packed data back together: `{"$p": n}` is block n of the file's own
 * pool, `{"$s": n}` block n of the state's shared pool, and `{"$c": keys, "$v": columns}` a
 * list of objects saved column by column. Every use gets a fresh copy, so a screen that edits
 * one place never changes another.
 */
function resolver(local, shared) {
  const done = new Map();
  const walk = (node) => {
    if (Array.isArray(node)) return node.map(walk);
    if (node && typeof node === "object") {
      const keys = Object.keys(node);
      if (keys.length === 1 && keys[0] === "$p") {
        const n = node.$p;
        if (!done.has(n)) done.set(n, walk(local[n]));
        return structuredClone(done.get(n));
      }
      if (keys.length === 1 && keys[0] === "$s") {
        if (!shared) throw new Error("this data file needs its state's shared pool");
        return structuredClone(shared.get(node.$s));
      }
      if (keys.length === 2 && "$c" in node && "$v" in node) {
        // The key list and the column list may themselves be pooled blocks.
        const names = walk(node.$c);
        const columns = walk(node.$v);
        const rows = columns.length ? columns[0].length : 0;
        const out = [];
        for (let r = 0; r < rows; r += 1) {
          const row = {};
          names.forEach((k, i) => { row[k] = columns[i][r]; });
          out.push(row);
        }
        return out;
      }
      const out = {};
      for (const k of keys) out[k] = walk(node[k]);
      return out;
    }
    return node;
  };
  return walk;
}

/**
 * Undo the exporter's packing (see "Packed files" in src/visionhousing/export_static.py):
 * a block that repeats is saved once in `$pool` and replaced by `{"$p": n}`; a block that
 * other files of the state repeat too is in the shared pool (`shared`, from `makePool`);
 * a list of like objects may be saved as columns. A file that is not packed comes back unchanged.
 */
export function unpack(value, shared = null) {
  if (!value || value.$packed !== 1 || !Array.isArray(value.$pool)) return value;
  return resolver(value.$pool, shared)(value.$data);
}

const code = (st) => String(st || "").toUpperCase();

/**
 * A scenario as query fields in the link format the back end reads (`s`, `r`, `a`):
 * `{state, reforms, overrides}` -> "s=PA&r=adu,fees&a=adu.x~h".
 */
export function scenarioQuery({ state, reforms = [], overrides = {}, preset = "central" } = {}) {
  const qs = new URLSearchParams();
  if (state) qs.set("s", code(state));
  qs.set("r", reforms.join(","));
  const keys = Object.keys(overrides || {}).sort();
  if (keys.length) qs.set("a", keys.map((k) => `${k}~${overrides[k]}`).join(","));
  if (preset && preset !== "central") qs.set("preset", preset);
  return qs.toString().replace(/%2C/g, ",").replace(/%7E/gi, "~");
}

/** The body of POST /run and /compare for a scenario (default seed and draws). */
export function runBody({ state, reforms = [], overrides = {}, preset = "central" } = {}, extra = {}) {
  // The link keeps a custom value as text; the run body wants a number (l / c / h stay words).
  const pinned = Object.fromEntries(
    Object.entries(overrides || {}).map(([k, v]) => [k, /^[lch]$/.test(v) ? v : Number(v)]),
  );
  // The housing-cycle preset is sent only when it is not the default, so default runs keep
  // their cache keys and the static site's saved runs.
  const cycle = preset && preset !== "central" ? { preset } : {};
  return { state: code(state), reforms: [...reforms], overrides: pinned, seed: 0, ...cycle, ...extra };
}

/**
 * The name of an exported run: "baseline", one reform id, or "package" for all five
 * benchmark reforms. Returns "" for a scenario the static site does not hold.
 */
export function runKey({ reforms = [], overrides = {}, preset = "central" } = {}, all = []) {
  if (Object.keys(overrides || {}).length) return "";
  if (preset && preset !== "central") return "";
  if (!Array.isArray(reforms)) return "";
  if (reforms.length === 0) return "baseline";
  if (reforms.length === 1) return reforms[0];
  const same = all.length && reforms.length === all.length && all.every((r) => reforms.includes(r));
  return same ? "package" : "";
}

/** Every run key a state's export holds (the benchmark runs and any state bills). */
function savedRuns(idx) {
  return [...(idx.runs || []), ...(idx.extra_runs || [])];
}

let indexes = {};
/** The exporter's index for a state: which runs, counties and places it holds. */
function stateIndex(st) {
  indexes[st] ||= file(`${st}/index.json`, `${st} is not in the static site`);
  return indexes[st];
}

/** The note when the static site holds no comparisons for a state. */
export const COMPARISONS_LOCAL_ONLY = "These comparisons need the local app (make app).";

/**
 * Static site: the comparisons (policy dials, interactions, matrix, ablation, ranking) are read
 * only when the state's index says `comparisons: true`; an older export, or a state the site
 * does not hold, gives the local-app note instead of a failed request.
 */
async function comparisonsSaved(st) {
  const idx = await stateIndex(code(st)).catch(() => ({}));
  if (idx?.comparisons !== true) throw new LocalOnlyError(COMPARISONS_LOCAL_ONLY);
}

const tractFiles = {};
/** One character per tract picks a value from a short list in a compact county file. */
const LOWER = "abcdefghijklm" + "nopqrstuvwxyz";
const TRACT_DIGITS = "0123456789" + LOWER + LOWER.toUpperCase();
/** One compact profile file, read once (a template, one county's tract values, or the county values). */
function tractFile(path, missing = "No tract profiles for this county on this site") {
  tractFiles[path] ||= file(path, missing);
  return tractFiles[path];
}

/**
 * One tract profile from the compact files (see src/visionhousing/compact_profiles.py):
 * walk the state's template and fill each slot from the county file. Values are copied
 * as the server wrote them; nothing is formatted or worked out here.
 */
async function compactTract(st, geoid) {
  const [template, data] = await Promise.all([
    tractFile(`${st}/profiles/tract-template.json`),
    tractFile(`${st}/profiles/tracts-${geoid.slice(0, 5)}.json`),
  ]);
  return assembleTract(template, data, geoid);
}

/**
 * One county profile from the compact county files (the same format, with every county of
 * the state in one values file): copied as the server wrote it, as for a tract.
 */
async function compactCounty(st, geoid) {
  const missing = `No profile for ${geoid} on this site`;
  const [template, data] = await Promise.all([
    tractFile(`${st}/profiles/county-template.json`, missing),
    tractFile(`${st}/profiles/county-values.json`, missing),
  ]);
  return assembleTract(template, data, geoid);
}

/** Rebuild one tract's profile from a compact template and county file (exported for tests). */
export function assembleTract(template, data, geoid) {
  if (data.raw && Object.hasOwn(data.raw, geoid)) return structuredClone(data.raw[geoid]);
  const t = data.geoids.indexOf(geoid);
  if (t < 0) throw new LocalOnlyError(`No profile for ${geoid} on this site`);
  const col = (k) => {
    const entry = data.cols[k];
    if (Array.isArray(entry)) return entry[t];
    if (entry && typeof entry === "object") {
      if ("=" in entry) return col(entry["="]);
      const i = entry.i[t];
      return entry.v[typeof i === "string" ? TRACT_DIGITS.indexOf(i) : i];
    }
    return entry;
  };
  const has = (p) => p < 0 || Boolean(col(p));
  const build = (node) => {
    switch (node[0]) {
      case "k": return node[1];
      case "c": return col(node[1]);
      case "e": return node[2][col(node[1])];
      case "p": {
        const words = node[3][col(node[1])];
        const numbers = words.length > 1 ? String(col(node[2])).split("|") : [];
        let text = words[0];
        for (let i = 1; i < words.length; i += 1) text += numbers[i - 1] + words[i];
        return text;
      }
      case "x": return build(node[2][col(node[1])]);
      case "o": {
        const order = String(col(node[1]));
        return order ? order.split(",").map((i) => build(node[2][Number(i)])) : [];
      }
      case "d": {
        // A third slot holds this tract's key order when it differs from the template's.
        const order = node.length > 2 ? String(col(node[2]) ?? "") : "";
        const entries = order ? order.split(",").map((i) => node[1][Number(i)]) : node[1];
        const out = {};
        for (const [key, child, p] of entries) if (has(p)) out[key] = build(child);
        return out;
      }
      default: return node[1].filter(([, p]) => has(p)).map(([child]) => build(child));
    }
  };
  return build(template);
}

/**
 * The static file layout this page understands (`schema_version` in each `ST/index.json`,
 * written by src/visionhousing/export_static.py). An index without the field is version 1:
 * before the baseline card file and the hearing briefs.
 */
export const STATIC_SCHEMA = 2;

/** The note when the static files are newer than this page. */
export function schemaNewer(v) {
  return `This site's data use file layout ${v}; this page reads up to ${STATIC_SCHEMA}. Reload the page to get the matching version. Until then, controls that need the newer files are off.`;
}

/**
 * What this build can honour for a state: `{schema, newer, reason}`. On the live app every
 * control works; on the static site the index's `schema_version` is checked, so a control the
 * page does not understand is switched off with its reason, never quietly shown differently.
 */
export async function staticSupport(st) {
  if (!IS_STATIC) return { schema: STATIC_SCHEMA, newer: false, reason: "" };
  try {
    const idx = await stateIndex(code(st));
    const schema = Number.isInteger(idx.schema_version) ? idx.schema_version : 1;
    const newer = schema > STATIC_SCHEMA;
    return { schema, newer, reason: newer ? schemaNewer(schema) : "", index: idx };
  } catch {
    return { schema: 0, newer: false, reason: `${code(st)} is not in the static site`, index: null };
  }
}

/** The note for a state whose export predates the baseline card file. */
export const NO_BASELINE_CARD = "The baseline card is not in this export yet; the local app (make app) shows it.";

/**
 * The state's baseline card (years, permit columns, release, cutoff, gap, nowcast, window
 * sensitivity, reconciliation) with its display rows under `view`, as the server wrote them;
 * `null` when the state has none (the synthetic test state). Live: from GET /states/ST/summary.
 * Static: `ST/baseline-card.json` (schema 2), else the saved summary's field, else LocalOnlyError.
 */
export async function baselineCard(st) {
  const s = code(st);
  if (!IS_STATIC) return (await live.summary(s))?.baseline_card ?? null;
  const support = await staticSupport(s);
  if (support.newer) throw new LocalOnlyError(support.reason);
  if (support.index?.baseline_card) return file(`${s}/baseline-card.json`, NO_BASELINE_CARD);
  const summary = await file(`${s}/summary.json`).catch(() => null);
  if (summary?.baseline_card) return summary.baseline_card;
  throw new LocalOnlyError(NO_BASELINE_CARD);
}

/**
 * Where the two-page hearing brief of a scenario is, or `{url: "", reason}` when this build
 * cannot give it: live GET /export/hearing; static `ST/exports/<key>.hearing.html` when the
 * index lists the run under `hearing_briefs`.
 */
export async function hearingBrief(sc) {
  if (!IS_STATIC) return { url: `${API_BASE}/export/hearing?${scenarioQuery(sc)}`, reason: "" };
  const st = code(sc.state);
  const support = await staticSupport(st);
  if (support.newer || !support.index) return { url: "", reason: support.reason };
  const key = runKey(sc, support.index.package || []);
  if (!key) return { url: "", reason: whyNot(sc) };
  if (!(support.index.hearing_briefs || []).includes(key)) {
    return {
      url: "",
      reason: savedRuns(support.index).includes(key)
        ? "This export has no hearing brief for this scenario; the local app (make app) writes it."
        : whyNot(sc),
    };
  }
  return { url: `${DATA_BASE}/${st}/exports/${key}.hearing.html`, reason: "" };
}

/** True when the scenario (state, reforms, overrides) can be shown in this build. */
export async function canShow(scenario) {
  if (!IS_STATIC) return true;
  try {
    const idx = await stateIndex(code(scenario.state));
    const key = runKey(scenario, idx.package || []);
    return Boolean(key && savedRuns(idx).includes(key));
  } catch {
    return false;
  }
}

/**
 * The scenario behind `api.stateTracts(st, reforms, overrides)`: `reforms` is a list of reform
 * ids (or one id); `overrides` are assumption overrides as in the link format.
 */
function tractScenario(st, reforms = [], overrides = {}) {
  const list = Array.isArray(reforms) ? reforms : [reforms].filter(Boolean);
  return { state: code(st), reforms: list, overrides: overrides || {} };
}

/** Search text matching the back end's place search (name or code contains q). */
function matchPlaces(places, q, limit) {
  const needle = String(q || "").trim().toLowerCase();
  const hits = needle
    ? places.filter((p) => p.name.toLowerCase().includes(needle) || p.geoid.includes(needle))
    : places;
  return hits.slice(0, limit);
}

const live = {
  states: () => getJson(`${API_BASE}/states`),
  summary: (st) => slowGet(`${API_BASE}/states/${code(st)}/summary`),
  county: (st, fips, query = "") => slowGet(`${API_BASE}/states/${code(st)}/counties/${fips}${query ? `?${query}` : ""}`),
  reforms: () => getJson(`${API_BASE}/reforms`),
  /** Every scenario (benchmarks, bills, extra scenarios) with its description and exclusions. */
  scenarios: () => getJson(`${API_BASE}/scenarios`),
  reform: (key) => getJson(`${API_BASE}/reforms/${encodeURIComponent(key)}`),
  run: (body) => post("/run", body),
  compare: (body) => post("/compare", body),
  ledger: ({ q = "", state = "" } = {}) => {
    const qs = new URLSearchParams();
    if (q) qs.set("q", q);
    if (state) qs.set("state", code(state));
    return getJson(`${API_BASE}/ledger${qs.size ? `?${qs}` : ""}`);
  },
  provenance: (st) => getJson(`${API_BASE}/provenance${st ? `?state=${code(st)}` : ""}`),
  validation: (st = "TL") => getJson(`${API_BASE}/validation?state=${code(st)}`),
  map: (sc) => slowGet(`${API_BASE}/states/${code(sc.state)}/map?${scenarioQuery(sc)}`),
  /** Every tract's id, county and interior point (GET /states/ST/tract-points); load once. */
  tractPoints: (st) => getJson(`${API_BASE}/states/${code(st)}/tract-points`),
  /**
   * The homes every tract gains under a scenario, column by column and keyed by `geoids`
   * (GET /states/ST/tracts): P10 / P50 / P90, each reform's P50, class, county and state
   * totals; reforms that work by county are in `county_only`.
   */
  stateTracts: (st, reforms = [], overrides = {}) =>
    slowGet(`${API_BASE}/states/${code(st)}/tracts?${scenarioQuery(tractScenario(st, reforms, overrides))}`),
  /**
   * One tract-level reform's map funnel (lots in scope, fit, feasible, expected permits), per
   * tract, county and state (GET /states/ST/tract-funnel). The reform runs alone, so a
   * package's `funnels` entry asks for exactly this.
   */
  tractFunnel: (st, reform, overrides = {}) =>
    slowGet(`${API_BASE}/states/${code(st)}/tract-funnel?${scenarioQuery(tractScenario(st, [reform], overrides))}`),
  outlines: (st) => getJson(`${API_BASE}/states/${code(st)}/outlines`),
  /**
   * Homes permitted by county and year (GET /states/ST/permit-years): the past years from the
   * pack's permit table with the rate per 1,000 homes and its class worked out by the server,
   * and the baseline's projected years 2027-2031 on the same scale.
   */
  permitYears: (st) => slowGet(`${API_BASE}/states/${code(st)}/permit-years`),
  /**
   * State legislative district outlines (GET /states/ST/district-outlines), when the state
   * has them; an empty collection or an error means "no district layer".
   */
  districtOutlines: (st, chamber = "lower") =>
    getJson(`${API_BASE}/states/${code(st)}/district-outlines?chamber=${encodeURIComponent(chamber)}`),
  /**
   * Comparisons (GET /states/ST/comparisons?part=): `part` is "interactions", "dials",
   * "matrix", "ablation" or "ranking". State and county results only; slow the first time.
   */
  comparisons: (st, part = "interactions") =>
    slowGet(`${API_BASE}/states/${code(st)}/comparisons?part=${encodeURIComponent(part)}`),
  evidence: (reform = "") => getJson(`${API_BASE}/evidence${reform ? `?reform=${encodeURIComponent(reform)}` : ""}`),
  reportCard: (st) => getJson(`${API_BASE}/report-card?state=${code(st)}`),
  places: (st, { q = "", level = "county", limit = 25 } = {}) => {
    const qs = new URLSearchParams({ level, limit: String(limit) });
    if (q) qs.set("q", q);
    return getJson(`${API_BASE}/places/${code(st)}?${qs}`);
  },
  profile: (st, geoid) => getJson(`${API_BASE}/profile/${code(st)}/${geoid}`),
  /** Where to download the scenario as `csv` or `html` (the brief); "" if not available. */
  exportUrl: async (fmt, sc) => `${API_BASE}/export/${fmt}?${scenarioQuery(sc)}`,
  tractsAvailable: async () => true,
};

const fixed = {
  states: () => file("states.json"),
  /**
   * One comparisons part, saved by the exporter as ST/comparisons/<part>.json (default seed and
   * draws). Read only when ST/index.json says `comparisons: true`; otherwise the local-app note.
   */
  comparisons: async (st, part = "interactions") => {
    await comparisonsSaved(st);
    return file(`${code(st)}/comparisons/${encodeURIComponent(part)}.json`, COMPARISONS_LOCAL_ONLY);
  },
  summary: (st) => file(`${code(st)}/summary.json`),
  county: async (st, fips, query = "") => {
    // The county panel holds the baseline, every reform and the package at default
    // assumptions, whatever is switched on; custom assumptions need the back end.
    if (/(^|&)(a|preset|p|seed|draws)=/.test(query)) throw new LocalOnlyError();
    return file(`${code(st)}/counties/${fips}.json`, `No data for county ${fips} on this site`);
  },
  reforms: () => file("reforms.json"),
  /**
   * The exporter does not save GET /scenarios, so it is not asked for (a missing file would be a
   * failed request): the screens use their own short lines, and a mix of reforms is not on the
   * static site anyway, so no overlap rule is needed here.
   */
  scenarios: async () => {
    throw new LocalOnlyError(SCENARIO_ONLY);
  },
  reform: (key) => file(`reforms/${encodeURIComponent(key)}.json`),
  run: async (body) => {
    const st = code(body.state);
    const idx = await stateIndex(st);
    if ((body.seed ?? 0) !== idx.seed || (body.draws ?? idx.draws) !== idx.draws) throw new LocalOnlyError();
    const key = runKey(body, idx.package || []);
    if (!key || !savedRuns(idx).includes(key)) throw new LocalOnlyError(whyNot(body));
    return file(`${st}/runs/${key}.json`);
  },
  compare: async (body) => {
    const st = code(body.state);
    if (body.scenarios || Object.keys(body.overrides || {}).length) throw new LocalOnlyError();
    const idx = await stateIndex(st);
    const key = runKey(body, idx.package || []);
    if (!key || !idx.compare.includes(key)) throw new LocalOnlyError();
    return file(`${st}/compare/${key}.json`);
  },
  map: async (sc) => {
    const st = code(sc.state);
    const idx = await stateIndex(st);
    const key = runKey(sc, idx.package || []);
    if (!key || !(idx.maps || []).includes(key)) throw new LocalOnlyError(whyNot(sc));
    return file(`${st}/maps/${key}.json`);
  },
  tractPoints: async (st) => {
    const s = code(st);
    if (!(await stateIndex(s)).tract_points) throw new LocalOnlyError(NO_TRACT_COLUMNS);
    return file(`${s}/tract-points.json`);
  },
  stateTracts: async (st, reforms = [], overrides = {}) => {
    const sc = tractScenario(st, reforms, overrides);
    const idx = await stateIndex(sc.state);
    if (!idx.tracts) throw new LocalOnlyError(NO_TRACT_COLUMNS);
    const key = runKey(sc, idx.package || []);
    if (!key || !idx.tracts.includes(key)) throw new LocalOnlyError(whyNot(sc));
    return file(`${sc.state}/tracts/${key}.json`);
  },
  tractFunnel: async (st, reform, overrides = {}) => {
    const sc = tractScenario(st, [reform], overrides);
    const idx = await stateIndex(sc.state);
    if (!idx.tract_funnels) throw new LocalOnlyError(NO_TRACT_COLUMNS);
    if (Object.keys(sc.overrides).length) throw new LocalOnlyError();
    if (!idx.tract_funnels.includes(reform)) throw new LocalOnlyError(`${reform} has no tract funnel on this site.`);
    return file(`${sc.state}/tract-funnel/${reform}.json`);
  },
  outlines: (st) => file(`${code(st)}/outlines.json`, `${code(st)} is not in the static site`),
  permitYears: async (st) => {
    const s = code(st);
    if (!(await stateIndex(s)).permit_years) throw new LocalOnlyError("The past permits by year are not in this export yet.");
    return file(`${s}/permit-years.json`);
  },
  districtOutlines: async () => {
    throw new LocalOnlyError("District outlines need the local app (make app).");
  },
  evidence: async (reform = "") => {
    const all = await file("evidence.json");
    if (!reform) return all;
    const entries = all.entries.filter((e) => e.reform === reform);
    return { count: entries.length, entries };
  },
  reportCard: (st) => file(`${code(st)}/report-card.json`, `No report card for ${code(st)} on this site`),
  ledger: async ({ q = "", state = "" } = {}) => {
    const all = await file(state ? `${code(state)}/ledger.json` : "ledger.json");
    if (!q) return all;
    const needle = q.toLowerCase();
    const parameters = all.parameters.filter((e) => JSON.stringify(e).toLowerCase().includes(needle));
    return { ...all, count: parameters.length, parameters };
  },
  provenance: (st) => file(st ? `${code(st)}/provenance.json` : "provenance.json"),
  validation: (st = "TL") => file(`${code(st)}/validation.json`),
  places: async (st, { q = "", level = "county", limit = 25 } = {}) => {
    const s = code(st);
    let list;
    if (level === "tract") {
      // Tract lists are stored per county; the Place screen asks with q = the county code.
      const county = String(q).slice(0, 5);
      if (!/^\d{5}$/.test(county)) throw new LocalOnlyError("Tract search needs a county code.");
      list = await file(`${s}/places/tracts-${county}.json`, "No tract list for this county on this site");
    } else {
      list = await file(`${s}/places/counties.json`, `${s} is not in the static site`);
    }
    return { ...list, places: matchPlaces(list.places, q, limit) };
  },
  profile: async (st, geoid) => {
    const s = code(st);
    // A tract profile is saved only when the export kept them (index.tract_profiles); asking
    // for one that is not there would be a failed request, so say "local app" straight away.
    if (String(geoid).length === 11) {
      const idx = await stateIndex(s);
      if (!idx.tract_profiles) {
        throw new LocalOnlyError("Tract profiles for this state are not on this site; the local app (make app) shows them.");
      }
      if (idx.tract_profile_format === "compact") return compactTract(s, String(geoid));
    } else if (String(geoid).length === 5) {
      const idx = await stateIndex(s);
      if (idx.county_profile_format === "compact") return compactCounty(s, String(geoid));
    }
    return file(`${s}/profiles/${geoid}.json`, `No profile for ${geoid} on this site`);
  },
  exportUrl: async (fmt, sc) => {
    const st = code(sc.state);
    try {
      const idx = await stateIndex(st);
      const key = runKey(sc, idx.package || []);
      return key && (idx.exports || []).includes(key) ? `${DATA_BASE}/${st}/exports/${key}.${fmt}` : "";
    } catch {
      return "";
    }
  },
  tractsAvailable: async (st) => {
    try {
      return Boolean((await stateIndex(code(st))).tract_profiles);
    } catch {
      return false;
    }
  },
};

/** The client for this build. Every screen imports this, never `fetch` directly. */
export const api = IS_STATIC ? fixed : live;

/** Forget cached indexes (tests). */
export function resetApiCache() {
  indexes = {};
}

/**
 * Read the baseline rule contest out of a `/validation` reply (`validation.contest`).
 * Every number in it was computed by the engine (src/visionhousing/baseline/contest.py);
 * these helpers only pick fields, so a screen never sums or divides.
 *
 * Returns null when the reply has no contest (an older export).
 */
export function baselineContest(validation) {
  const c = validation?.contest;
  if (!c) return null;
  return {
    choosingRule: c.choosing_rule,
    coverageBand: c.coverage_band,
    rules: c.rules || [],
    columns: c.columns || {},
    winner: c.winner,
    winnerName: c.winner_name,
    winnerReason: c.winner_reason,
    reference: c.reference,
    placeType: c.place_type,
    findings: c.findings || [],
  };
}

/**
 * The baseline rules on the contest's own measure (the typical miss), with plain names and the
 * server's text (`contest_view` of a validation reply, written in api/views.py). An older
 * reply without it gives the same list from the contest's rules, with no text of its own
 * (the caller rounds the raw share for display, as the contest table does).
 */
export function contestView(validation) {
  const v = validation?.contest_view;
  if (v?.rules?.length) {
    return {
      measure: v.measure,
      measureHelp: v.measure_help,
      used: v.used,
      usedName: v.used_name,
      usedText: v.used_text,
      rules: v.rules,
      note: v.note || "",
    };
  }
  const c = validation?.contest;
  const used = validation?.chosen_rule || c?.winner;
  const rules = (c?.rules || [])
    .filter((r) => Number.isFinite(r.abs_pct))
    .map((r) => ({ rule: r.rule, name: r.name || r.rule, value: r.abs_pct, text: "", used: r.rule === used }));
  if (!rules.length) return null;
  const mine = rules.find((r) => r.used);
  return {
    measure: "Typical miss (either way)",
    measureHelp: c.columns?.median_abs_log_ratio || "",
    used,
    usedName: mine?.name || used,
    usedText: "",
    rules,
    note: "",
  };
}

/**
 * The bias-correction and per-place-type range test (out of time), and the error by group
 * before and after: `methods` names each correction, `overall` scores them on all counties,
 * `byGroup` has one row per group with `reference`, `winner` and each method's scores.
 */
export function baselineCalibration(validation) {
  const c = validation?.contest;
  if (!c) return null;
  return { ...(c.calibration || {}), byGroup: c.by_group || [] };
}

/** Surge years in the baseline window and the named counties' error with and without them. */
export function baselineSurges(validation) {
  return validation?.contest?.surge || null;
}

/**
 * The stress settings: the baseline's state range using only "slump" or only "boom"
 * back-test origins' errors. One entry per kind: `{kind, origins, state, sentence}`, where
 * `state` is the engine's record (p10, p50, p90, formula).
 */
export function baselineStress(validation) {
  return validation?.contest?.stress || [];
}

/*
 * Readouts of a run reply (POST /run, live or saved). Pure lookups: the engine wrote every
 * number with its formula and ids; nothing here sums, divides or rounds.
 */

/** The paired and cost readouts of one reform: record name -> engine record ({} if none). */
export function readouts(run, key) {
  return run?.reform_results?.[key]?.readouts || {};
}

/** One readout by county: county id -> record (for example "share_of_baseline", "revenue_given_up"). */
export function readoutCounties(run, key, name) {
  return run?.reform_results?.[key]?.readout_counties?.[name] || {};
}

/** Plain texts that go with the readouts ("cost" framing, "not defined" cost). */
export function readoutTexts(run, key) {
  return run?.reform_results?.[key]?.readout_texts || {};
}

/**
 * The written readouts of one reform (explanation): frequency wording, the paired sentence,
 * chances of adding at least N homes, share of the baseline and what it costs; null if absent.
 */
export function readoutSentences(run, key) {
  return run?.explanation?.readouts?.[key] || null;
}

/** What would narrow one reform's range: variance shares, leftover and research agenda; null if absent. */
export function narrowing(run, key) {
  return run?.explanation?.narrowing?.[key] || null;
}

/** The headline's frequency sentence ("In eight of ten simulated futures ..."); null if absent. */
export function frequencyText(run) {
  return run?.explanation?.headline?.frequency || null;
}

/** One reform's readouts in a county panel reply (GET /states/ST/counties/FIPS): name -> record. */
export function countyReadouts(county, key) {
  return county?.reforms?.[key]?.readouts || {};
}

/*
 * Outcome contract (engine.outcome): every headline is additional units authorized by building
 * permits, 2027-2031. Pure lookups of engine numbers; nothing here sums or divides.
 */

/** The run's as-of date (ISO text), or null. */
export function asOf(run) {
  return run?.as_of || run?.contract?.as_of || null;
}

/** The outcome contract: {as_of, measure, window, vintages, effective_dates, hold_constant, audit, baseline_correction}. */
export function outcomeContract(run) {
  return run?.contract || null;
}

/**
 * One series split into 2027-2031: "baseline", "package", "total" or a reform key.
 * Returns {key, label, years, state: [record per year], counties: {geoids, years, p10, p50, p90, formula}, timing}.
 */
export function annualSeries(run, key) {
  if (key === "baseline" || key === "package" || key === "total") return run?.annual?.[key] || null;
  return run?.reform_results?.[key]?.annual || null;
}

/** Gross and net of one reform: {authorized, net_stock, removed?, baseline_on_sites?} (engine records). */
export function grossNet(run, key) {
  return run?.reform_results?.[key]?.gross_net || {};
}

/** Net added homes by county for a reform that removes homes: county id -> record ({} otherwise). */
export function netStockCounties(run, key) {
  return run?.reform_results?.[key]?.net_stock_counties || {};
}

/** One county's years (county panel reply): key "baseline" | "package" | a reform key. */
export function countyAnnual(county, key) {
  if (key === "baseline" || key === "package") return county?.annual?.[key] || null;
  return county?.reforms?.[key]?.annual || null;
}

/** A tract-level reform's years for every tract of the county (county panel reply), or null. */
export function countyTractsAnnual(county, key) {
  return county?.reforms?.[key]?.tracts_annual || null;
}

/** The baseline's bias-correction decision ({keep, reason, rule, corrected, uncorrected}) from /validation. */
export function baselineBiasCorrection(validation) {
  return validation?.contest?.bias_correction || null;
}

/** The contest table on all years and on fully reported (observed-only) years. */
export function backtestVariants(validation) {
  return validation?.contest?.variants || null;
}

/** Each contest rule back-tested on the state's own totals. */
export function statewideBacktest(validation) {
  return validation?.contest?.statewide || null;
}

/** Rolling origins and the independent five-year periods they hold ({origins, independent_periods, note}). */
export function originOverlap(validation) {
  return validation?.contest?.origins || null;
}

/* ---------------------------------------------------------------- comparisons (engine E3) */

/** Package vs the reforms alone: {state: {solo_sum, package, net_interaction}, counties, check}. */
export function interactionSummary(cmp) {
  const i = cmp?.interactions;
  return i ? { state: i.state, counties: i.counties, check: i.check } : null;
}

/** Each reform's alone, last-in and interaction-share records, by reform key. */
export function lastInEffects(cmp) {
  return cmp?.interactions?.last_in || null;
}

/** The ordered allocation ([{reform, position, record, counties}]) and its stated order. */
export function orderedAllocation(cmp) {
  const i = cmp?.interactions;
  return i ? { ordering: i.ordering, rows: i.allocation } : null;
}

/** Waterfall steps ({id, kind: start|step|end, label, record}) and the note on medians. */
export function interactionWaterfall(cmp) {
  return cmp?.interactions?.waterfall || null;
}

/** Named mechanisms with their rule-written sentences. */
export function interactionMechanisms(cmp) {
  return cmp?.interactions?.mechanisms || [];
}

/** One policy dial's curve (levels, steps, each_step, diminishing, targets), or every dial. */
export function dialCurve(cmp, key) {
  const d = cmp?.dials?.dials;
  if (!d) return null;
  return key ? d[key] || null : d;
}

/** Target-seeker rows of one dial: [{homes, p50: {status, level, x}, p10: {...}}]. */
export function dialTargets(cmp, key) {
  return cmp?.dials?.dials?.[key]?.targets?.rows || [];
}

/** ADU adoption threshold: rows of {homes, p50, p10} with rate and sentence. */
export function adoptionThreshold(cmp) {
  return cmp?.dials?.adoption || null;
}

/** Take-up x construction-cost matrix: {rows, columns, cells[row][col]: {p10, p50, p90}}. */
export function costMatrix(cmp) {
  return cmp?.matrix || null;
}

/** Data-ablation tiers ("2", "1", "0") with the four measures per reform, and the property check. */
export function ablationTiers(cmp) {
  const a = cmp?.ablation;
  return a ? { tiers: a.tiers, property: a.property, formulas: a.formulas } : null;
}

/** "Which missing dataset hurts most": ranked rows and a sentence. */
export function missingDataRanking(cmp) {
  return cmp?.ablation?.single_input || null;
}

/** Ranking stability: benchmark order, cases at each input's low and high, overlapping pairs. */
export function rankingStability(cmp) {
  return cmp?.ranking || null;
}

/* --------------------------------------------- explanation cards (src/visionhousing/api/cards.py) */

/** The note for a card the static site does not hold. */
export const CARD_LOCAL_ONLY = "This card needs the local app (make app).";

/**
 * The two confidence signals for a state, side by side and never merged
 * (GET /confidence/ST; static: ST/confidence.json): `baseline_performance` (grade, sentence,
 * hold-out coverage and cases) and `reform_evidence.by_reform_type` (grade, records, verified,
 * with observed results, in state), plus `type_labels` and `benchmark_types`.
 */
export async function confidenceSignals(st) {
  if (IS_STATIC) {
    await cardSaved(st, "confidence");
    return file(`${code(st)}/confidence.json`, CARD_LOCAL_ONLY);
  }
  return getJson(`${API_BASE}/confidence/${code(st)}`);
}

/**
 * Static site: a card's file is asked for only when the state's index says it was exported
 * (`confidence`, `plausibility`, `lever`, `implementation` keys), so an older export shows the
 * card's local-app note instead of a failed request.
 */
async function cardSaved(st, key) {
  const idx = await stateIndex(code(st));
  if (!idx[key]) throw new LocalOnlyError(CARD_LOCAL_ONLY);
}

/**
 * The plausibility card of one reform (GET /plausibility/ST?r=KEY; static: ST/plausibility.json,
 * all benchmark reforms): our P10–P90 rate per measure beside the observed rates, each with its
 * caveat, and the rule-written sentence. Returns the whole reply; read `reforms[KEY]`.
 */
export async function plausibilityCard(st, reform) {
  if (IS_STATIC) await cardSaved(st, "plausibility");
  return IS_STATIC
    ? file(`${code(st)}/plausibility.json`, CARD_LOCAL_ONLY)
    : slowGet(`${API_BASE}/plausibility/${code(st)}?r=${encodeURIComponent(reform)}`);
}

/**
 * The counterfactual lever card of one reform, for the state or one county
 * (GET /lever/ST?r=KEY&county=FIPS; static: ST/lever/KEY.json, state only): binding factor,
 * who can act, each named variant's paired effect range, the evidence grade and the next check.
 * It runs the named variants, so it is slow the first time.
 */
export async function leverCard(st, reform, county = "") {
  if (IS_STATIC) {
    if (county) throw new LocalOnlyError(CARD_LOCAL_ONLY);
    await cardSaved(st, "lever");
    return file(`${code(st)}/lever/${encodeURIComponent(reform)}.json`, CARD_LOCAL_ONLY);
  }
  const qs = new URLSearchParams({ r: reform });
  if (county) qs.set("county", county);
  return slowGet(`${API_BASE}/lever/${code(st)}?${qs}`);
}

/** True when an implementation row applies to a place (static mode picks rows; no number). */
function rowCovers(row, geoid) {
  const j = String(row.jurisdiction || "");
  if (!geoid || /^[A-Z]{2}$/.test(j)) return true;
  return geoid.startsWith(j) || j.startsWith(geoid);
}

/**
 * Implementation-status rows for a reform in a place (GET /implementation/ST?r=KEY&geoid=G;
 * static: ST/implementation.json, every reform and place, picked here by reform and place).
 * Each row: legal / local / permits steps with their record (dates, sources), `line`, `pending`.
 */
export async function implementationRows(st, reform, geoid = "") {
  if (IS_STATIC) {
    await cardSaved(st, "implementation");
    const all = await file(`${code(st)}/implementation.json`, CARD_LOCAL_ONLY);
    const rows = (all.rows || []).filter((r) => r.reform === reform && rowCovers(r, geoid));
    return { ...all, geoid: geoid || null, reforms: [reform], rows };
  }
  const qs = new URLSearchParams({ r: reform });
  if (geoid) qs.set("geoid", geoid);
  return getJson(`${API_BASE}/implementation/${code(st)}?${qs}`);
}

/* --------------------------------------------- district view (src/visionhousing/api/districts.py) */

/** The note for the district view on a static site whose export holds no district files. */
export const DISTRICTS_LOCAL_ONLY = "The district view needs the local app (make app).";

/**
 * Static site: the district files are there only when the state's index says so
 * (`districts: true`). Layout (written by the exporter): ST/districts/outlines-<chamber>.json
 * and ST/districts/<chamber>/<run key>.json, the back end's own replies.
 */
async function districtsSaved(st) {
  const idx = await stateIndex(code(st));
  if (!idx.districts) throw new LocalOnlyError(DISTRICTS_LOCAL_ONLY);
  return idx;
}

/**
 * One chamber's district outlines (GeoJSON, `level, geoid, name`): GET
 * /states/ST/district-outlines?chamber=; static: ST/districts/outlines-<chamber>.json.
 */
export async function districtShapes(st, chamber = "lower") {
  if (IS_STATIC) {
    await districtsSaved(st);
    return file(`${code(st)}/districts/outlines-${encodeURIComponent(chamber)}.json`, DISTRICTS_LOCAL_ONLY);
  }
  return getJson(`${API_BASE}/states/${code(st)}/district-outlines?chamber=${encodeURIComponent(chamber)}`);
}

/**
 * Every district of one chamber under a scenario (GET /states/ST/districts?chamber=&r=):
 * baseline, each reform and the package as P10 / P50 / P90 with the engine's text (`shown`),
 * the headline, the map class, the split caveat, the counties touched, the limits and the
 * brief pointers. Static: ST/districts/<chamber>/<run key>.json (default assumptions only).
 */
export async function districtsReply(sc, chamber = "lower") {
  if (IS_STATIC) {
    const idx = await districtsSaved(sc.state);
    if (Object.keys(sc.overrides || {}).length) throw new LocalOnlyError();
    const key = runKey(sc, idx.package || []);
    if (!key) throw new LocalOnlyError(COMBO_ONLY);
    return file(`${code(sc.state)}/districts/${encodeURIComponent(chamber)}/${key}.json`, COMBO_ONLY);
  }
  const qs = scenarioQuery(sc);
  return slowGet(`${API_BASE}/states/${code(sc.state)}/districts?chamber=${encodeURIComponent(chamber)}${qs ? `&${qs}` : ""}`);
}

/** The note when the static site holds no ADU budget-and-target grid for a state. */
export const BUDGET_LOCAL_ONLY = "The budget-and-target grid needs the local app (make app); this site does not hold it yet.";

/**
 * The ADU budget-and-target grid of a state with its text: `{grid, view}`
 * (GET /states/ST/adu-budget-grid, src/visionhousing/api/budget_grid.py and budget_view.py;
 * static: ST/adu-budget-grid.json when the index says `adu_budget_grid: true`, else the
 * local-app note). The first ask after a restart can take a minute (the grid is worked out
 * once on the same draws and kept on disk). Which options meet a target is picked by
 * `gridLookup` in lib/budget.js; every number shown is the view's text.
 */
export async function budgetGrid(st) {
  if (IS_STATIC) {
    const idx = await stateIndex(code(st));
    if (!idx.adu_budget_grid) throw new LocalOnlyError(BUDGET_LOCAL_ONLY);
    return file(`${code(st)}/adu-budget-grid.json`, BUDGET_LOCAL_ONLY);
  }
  const body = await slowGet(`${API_BASE}/states/${code(st)}/adu-budget-grid`);
  return { grid: body.grid, view: body.view };
}
