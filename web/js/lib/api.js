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

/** Why the static site cannot show a scenario: custom assumptions, or an unsaved mix. */
function whyNot(sc) {
  return Object.keys(sc.overrides || {}).length ? LOCAL_ONLY : COMBO_ONLY;
}

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
  return reply.json();
}

const code = (st) => String(st || "").toUpperCase();

/**
 * A scenario as query fields in the link format the back end reads (`s`, `r`, `a`):
 * `{state, reforms, overrides}` -> "s=PA&r=adu,fees&a=adu.x~h".
 */
export function scenarioQuery({ state, reforms = [], overrides = {} } = {}) {
  const qs = new URLSearchParams();
  if (state) qs.set("s", code(state));
  qs.set("r", reforms.join(","));
  const keys = Object.keys(overrides || {}).sort();
  if (keys.length) qs.set("a", keys.map((k) => `${k}~${overrides[k]}`).join(","));
  return qs.toString().replace(/%2C/g, ",").replace(/%7E/gi, "~");
}

/** The body of POST /run and /compare for a scenario (default seed and draws). */
export function runBody({ state, reforms = [], overrides = {} } = {}, extra = {}) {
  // The link keeps a custom value as text; the run body wants a number (l / c / h stay words).
  const pinned = Object.fromEntries(
    Object.entries(overrides || {}).map(([k, v]) => [k, /^[lch]$/.test(v) ? v : Number(v)]),
  );
  return { state: code(state), reforms: [...reforms], overrides: pinned, seed: 0, ...extra };
}

/**
 * The name of an exported run: "baseline", one reform id, or "package" for all five
 * benchmark reforms. Returns "" for a scenario the static site does not hold.
 */
export function runKey({ reforms = [], overrides = {} } = {}, all = []) {
  if (Object.keys(overrides || {}).length) return "";
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

/** True when the scenario (state, reforms, overrides, preset) can be shown in this build. */
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
  outlines: (st) => getJson(`${API_BASE}/states/${code(st)}/outlines`),
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
  summary: (st) => file(`${code(st)}/summary.json`),
  county: async (st, fips, query = "") => {
    // The county panel holds the baseline, every reform and the package at default
    // assumptions, whatever is switched on; custom assumptions need the back end.
    if (/(^|&)(a|preset|p|seed|draws)=/.test(query)) throw new LocalOnlyError();
    return file(`${code(st)}/counties/${fips}.json`, `No data for county ${fips} on this site`);
  },
  reforms: () => file("reforms.json"),
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
  outlines: (st) => file(`${code(st)}/outlines.json`, `${code(st)} is not in the static site`),
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
  profile: (st, geoid) => file(`${code(st)}/profiles/${geoid}.json`, `No profile for ${geoid} on this site`),
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
