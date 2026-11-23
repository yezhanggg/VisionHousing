/**
 * The app's state, held in signals. The address is the single source of truth:
 * the route and the whole scenario are read from `location.hash`, and every change to the
 * scenario is written back to it. So a copied link always reopens the same view.
 */
import { computed, signal } from "@preact/signals";
import { REFORM_IDS } from "../content/reforms.js";
import { STATE_CODES } from "../content/states.js";
import { decodeScenario, encodeScenario, parseRoute } from "./codec.js";

const KNOWN = { states: STATE_CODES, reforms: REFORM_IDS };

/** The raw address hash. Updated on every `hashchange`. */
export const hash = signal(typeof location === "undefined" ? "" : location.hash);

export const route = computed(() => parseRoute(hash.value));
const decoded = computed(() => decodeScenario(route.value.query, KNOWN));
export const scenario = computed(() => decoded.value.scenario);
/** What the decoder had to drop from the link, in plain words. */
export const dropped = computed(() => decoded.value.dropped);

/** The last screen that was not the slides view, so Esc in the slides can go back to it. */
export const lastScreen = signal({ name: "state", param: "" });

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

/** Switch one reform on or off. */
export function toggleReform(id) {
  const on = scenario.value.reforms.includes(id);
  const reforms = on
    ? scenario.value.reforms.filter((r) => r !== id)
    : [...scenario.value.reforms, id];
  updateScenario({ reforms });
}

/** Turn every reform off and clear assumption overrides. */
export function backToBaseline() {
  updateScenario({ reforms: [], overrides: {} });
}

/** Start listening to the address and write it in its canonical form. */
export function startRouter() {
  window.addEventListener("hashchange", () => {
    hash.value = location.hash;
    canonical();
  });
  canonical();
}

function canonical() {
  const r = route.value;
  const param = r.name === "reform" && !r.param ? defaultReform() : r.param;
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
