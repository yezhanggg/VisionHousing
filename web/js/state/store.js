/**
 * The app's state, held in signals. The address is the single source of truth:
 * the route and the whole scenario are read from `location.hash`, and every change to the
 * scenario is written back to it. So a copied link always reopens the same view.
 */
import { computed, signal } from "@preact/signals";
import { REFORM_ALIASES, REFORM_IDS, reformById } from "../content/reforms.js";
import { STATE_CODES } from "../content/states.js";
import { decodeScenario, encodeScenario, parseRoute } from "./codec.js";

const KNOWN = { states: STATE_CODES, reforms: REFORM_IDS, aliases: REFORM_ALIASES };

/** The raw address hash. Updated on every `hashchange`. */
export const hash = signal(typeof location === "undefined" ? "" : location.hash);

export const route = computed(() => parseRoute(hash.value));
const decoded = computed(() => decodeScenario(route.value.query, KNOWN));
export const scenario = computed(() => decoded.value.scenario);
/** What the decoder had to drop from the address now in view, in plain words. */
export const dropped = computed(() => decoded.value.dropped);
/**
 * What was dropped from the last link that was opened (typed, pasted or followed). The router
 * rewrites the address in its canonical form at once, which removes the dropped parts, so the
 * list is kept here for the link note (ui/static-note.js) until the next link is opened.
 */
export const linkDropped = signal([]);

/** The last screen that was not the slides view, so Esc in the slides can go back to it. */
export const lastScreen = signal({ name: "state", param: "" });

/** The slide the Slides view opens at (set by a card in the Brief preview, then reset to 0). */
export const slideStart = signal(0);

/** True while the guided tour (ui/tour.js) is open. The shell mounts it; buttons set it. */
export const tourOpen = signal(false);

/** Build an address for a route, keeping the current (or a given) scenario. */
export function hrefFor(name, param = "", sc = scenario.value) {
  const path = param ? `${name}/${param}` : name;
  return `#/${path}?${encodeScenario(sc, KNOWN)}`;
}

/** Go to a screen; adds a history entry so Back works. */
export function navigate(name, param = "") {
  location.hash = hrefFor(name, param);
}

function replaceHash(next) {
  if (next !== location.hash) {
    history.replaceState(null, "", next);
    hash.value = location.hash;
  }
}

/** Change part of the scenario. Replaces the history entry, so toggles do not flood Back. */
export function updateScenario(patch) {
  const next = { ...scenario.value, ...patch };
  replaceHash(hrefFor(route.value.name, route.value.param, next));
}

/**
 * Switch one reform on or off. Reforms of the same family reach the same lots (the ADU
 * reform and the Pennsylvania ADU bills), so switching one on switches the others off.
 */
export function toggleReform(id) {
  const on = scenario.value.reforms.includes(id);
  const family = reformById(id)?.family;
  const reforms = on
    ? scenario.value.reforms.filter((r) => r !== id)
    : [...scenario.value.reforms.filter((r) => reformById(r)?.family !== family), id];
  updateScenario({ reforms });
}

/** Set the reforms switched on (for example "all five benchmarks"). */
export function setReforms(reforms) {
  updateScenario({ reforms });
}

/**
 * Open the guided tour on the screen in view. With no reform on, the ADU reform is switched
 * on first, so the range and drivers steps have something to show.
 */
export function startTour() {
  if (!scenario.value.reforms.length) setReforms(["adu"]);
  tourOpen.value = true;
}

/** Turn every reform off and clear assumption overrides. */
export function backToBaseline() {
  updateScenario({ reforms: [], overrides: {} });
}

/**
 * The intro page. When the build holds one (screens/intro.js), an address with no route
 * ("#/" or nothing) shows it; any route ("#/state", a shared link) opens the tool directly.
 */
let introOn = false;
export function enableIntro() {
  introOn = true;
}
const bare = (h) => /^#?\/?$/.test(h || "");
export const atIntro = computed(() => introOn && bare(hash.value));

/** Start listening to the address and write it in its canonical form. */
export function startRouter() {
  window.addEventListener("hashchange", () => {
    hash.value = location.hash;
    canonical();
  });
  canonical();
}

function canonical() {
  if (introOn && bare(location.hash)) return;
  linkDropped.value = [...dropped.value];
  const r = route.value;
  let param = r.name === "reform" && !r.param ? defaultReform() : r.param;
  if (r.name === "reform" && REFORM_ALIASES[param]) param = REFORM_ALIASES[param];
  if (r.name !== "slides") lastScreen.value = { name: r.name, param };
  replaceHash(hrefFor(r.name, param));
}

/** The reform the Reform tab opens: the first one switched on, else the first benchmark. */
export function defaultReform() {
  return scenario.value.reforms[0] || REFORM_IDS[0];
}

/** Transient messages ("Link copied"). Read by ui/toast.js. */
export const toast = signal(null);
let toastTimer = 0;

/** Show a short status message for a moment. */
export function showToast(text, ms = 1800) {
  clearTimeout(toastTimer);
  toast.value = { text, id: Date.now() };
  toastTimer = setTimeout(() => {
    toast.value = null;
  }, ms);
}

/**
 * Layout preferences: which floating cards are folded. They belong to this browser, not to
 * the scenario, so they live in localStorage and never in the address (a shared link opens
 * the same scenario in the reader's own layout). Unreadable or blocked storage falls back to
 * everything open.
 */
const PREFS_KEY = "visionhousing.layout.v1";
/**
 * leftMin / rightMin: the side panel is folded into its small box. rightW: the right panel's
 * width in pixels (dragged by its edge). chatOpen: the chat box is open.
 */
const PREF_DEFAULTS = { scenarioOpen: true, resultsOpen: true, leftMin: false, rightMin: false, rightW: 420, chatOpen: false };
export const RIGHT_W = { min: 320, max: 760 };

function readPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
    const out = { ...PREF_DEFAULTS };
    for (const k of Object.keys(PREF_DEFAULTS)) {
      if (typeof PREF_DEFAULTS[k] === typeof saved[k] && (typeof saved[k] !== "number" || Number.isFinite(saved[k]))) {
        out[k] = saved[k];
      }
    }
    out.rightW = Math.round(Math.min(RIGHT_W.max, Math.max(RIGHT_W.min, out.rightW)));
    return out;
  } catch {
    return { ...PREF_DEFAULTS };
  }
}

export const prefs = signal(typeof window === "undefined" ? { ...PREF_DEFAULTS } : readPrefs());

/** Change one or more layout preferences and remember them in this browser. */
export function setPref(patch) {
  prefs.value = { ...prefs.value, ...patch };
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs.value));
  } catch {
    // Storage blocked: the layout lasts for this page only.
  }
}

/**
 * A short-lived request to draw the reader's eye to a card (for example "Choose in
 * Scenario" from the map's prompt). The card with that id flashes and takes focus.
 */
export const attention = signal({ id: "", n: 0 });

export function callAttention(id) {
  attention.value = { id, n: attention.value.n + 1 };
}
