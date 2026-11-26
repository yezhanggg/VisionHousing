/**
 * Loading engine output for the screens, and small readers of the run reply.
 *
 * `useJson(key, load)` loads once per key and returns {data, error, loading, localOnly}.
 * The readers below only look things up in what the API sent (the run's explanation, its
 * records); they never compute a number.
 */
import { signal } from "@preact/signals";
import { api, LocalOnlyError, runBody } from "./api.js";
import { useEffect, useState } from "./html.js";

const cache = new Map();
const CACHE_MAX = 40;

/** Remember a reply by key so going back to a screen is instant. */
function remember(key, promise) {
  cache.set(key, promise);
  if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value);
  promise.catch(() => cache.delete(key));
  return promise;
}

/**
 * Load (or reuse) the reply for `key`. An empty key loads nothing.
 *
 * While a new key loads, the last reply stays (`stale: true`) so a changed number can roll from
 * its old text to the new one, but only while `keep` is the same: a reply loaded under another
 * `keep` (another state) is dropped at once, so the old state's range and sentence never sit
 * under the new state's heading. The screens then show their loading skeleton. `forKey` is the
 * key the data belongs to.
 */
export function useJson(key, load, keep = "") {
  const [state, setState] = useState({ data: null, error: "", loading: Boolean(key), localOnly: false, keep, forKey: "" });
  // Drop a reply of another `keep` in the same render the key changes (no frame with old numbers).
  const shown = state.keep === keep ? state : { data: null, error: "", loading: Boolean(key), localOnly: false, keep, forKey: "" };
  useEffect(() => {
    if (!key) {
      setState({ data: null, error: "", loading: false, localOnly: false, keep, forKey: "" });
      return undefined;
    }
    let live = true;
    setState((s) =>
      s.keep === keep
        ? { data: s.data, error: "", loading: true, localOnly: false, stale: Boolean(s.data), keep, forKey: s.forKey }
        : { data: null, error: "", loading: true, localOnly: false, keep, forKey: "" },
    );
    const promise = cache.get(key) || remember(key, load());
    promise
      .then((data) => live && setState({ data, error: "", loading: false, localOnly: false, keep, forKey: key }))
      .catch(
        (err) =>
          live &&
          setState({
            data: null,
            error: String(err?.message || err),
            loading: false,
            localOnly: err instanceof LocalOnlyError,
            keep,
            forKey: key,
          }),
      );
    return () => {
      live = false;
    };
  }, [key, keep]);
  return shown;
}

/** The run of a scenario: `POST /run` with its explanation (and, if asked, the top drivers). */
export function useRun(sc, { drivers = false, explain = true, skip = false } = {}) {
  const body = runBody(sc, { explain, drivers });
  const key = skip ? "" : `run:${JSON.stringify(body)}`;
  return useJson(key, () => api.run(body), sc.state);
}

/** The county map of a scenario. */
export function useMap(sc) {
  return useJson(`map:${JSON.stringify(runBody(sc))}`, () => api.map(sc), sc.state);
}

/** The "explain this number" payload of a record id in a run, or null. */
export function numberOf(run, ref) {
  return run?.explanation?.numbers?.[ref] || null;
}

/**
 * Lookups the Explain popover reads: source id -> {citation, url}, assumption id -> entry.
 * Every screen that loads a run with its explanation adds to them.
 */
export const lookups = signal({ sources: {}, assumptions: {} });

/** Add a run's sources and assumptions to the lookups. */
export function learn(run) {
  const e = run?.explanation;
  if (!e) return;
  const now = lookups.value;
  const sources = { ...now.sources, ...(e.sources || {}) };
  const assumptions = { ...now.assumptions, ...(e.assumptions || {}) };
  if (
    Object.keys(sources).length !== Object.keys(now.sources).length ||
    Object.keys(assumptions).length !== Object.keys(now.assumptions).length
  ) {
    lookups.value = { sources, assumptions };
  }
}

/** Plain words for a failed load. */
export function loadMessage(state, what) {
  if (state.localOnly) return state.error;
  return `${what} did not load: ${state.error}`;
}
