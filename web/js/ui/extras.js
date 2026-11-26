/**
 * The extra scenarios in the interface: questions the five benchmark reforms leave open
 * (docs/methods/extra_scenarios.md).
 *
 * - `useCatalog(state)` reads GET /reforms (each reform's `kind` and `family`) and GET /scenarios
 *   (each scenario's description and `excludes`, the reform families it may not share a package
 *   with). It returns the extras the engine lists, with the engine's own title and description,
 *   and `blockers(id, on)`: the switched-on reforms that reach the same lots or projects as `id`.
 *   The back end refuses such a package, so the screens grey the switch out and say why instead
 *   of sending it.
 * - On the static site an extra is offered only when its run was exported (the state's index);
 *   otherwise its switch says it needs the local app.
 * - `ExtraSwitches` is the folded "Extra scenarios" group under the benchmark switches (State);
 *   `OutsideCount` shows the homes a scenario reports outside the permit measure (adaptive
 *   reuse: conversions), as the engine's record and text; `RecordsCard` lists a scenario's
 *   records. Nothing here computes a number.
 */
import { EXTRA_IDS, reformById } from "../content/reforms.js";
import { api, canShow, IS_STATIC, SCENARIO_ONLY } from "../lib/api.js";
import { numberOf, useJson } from "../lib/data.js";
import { html } from "../lib/html.js";
import { toggleReform } from "../state/store.js";
import { Card, Switch } from "./bits.js";
import { Explain, Num } from "./popover.js";

/** The short reason under a switch the static site cannot run. */
export const EXTRA_LOCAL_ONLY = "Needs the local app (make app).";

/**
 * Records a scenario reports outside the measure (added units authorized by building permits):
 * record name -> the reform that carries it. A conversion is an alteration of an existing
 * building, which the Building Permits Survey leaves out.
 */
const OUTSIDE = { adaptive_reuse: "conversion_homes" };

function loadCatalog() {
  return Promise.all([api.reforms().catch(() => null), api.scenarios().catch(() => null)]).then(([r, s]) => ({
    reforms: r?.reforms || [],
    scenarios: s?.scenarios || [],
  }));
}

/** Which extras the static site holds for a state: id -> true / false. */
function loadSaved(state) {
  return Promise.all(EXTRA_IDS.map((id) => canShow({ state, reforms: [id], overrides: {} }))).then((ok) =>
    Object.fromEntries(EXTRA_IDS.map((id, i) => [id, ok[i]])),
  );
}

/**
 * The extras for a state and the overlap rule. Until the catalog loads, the extras are the
 * interface's own list (so a shared link's switches still show) and nothing is greyed out.
 */
export function useCatalog(state) {
  const cat = useJson("catalog:reforms", loadCatalog);
  const saved = useJson(IS_STATIC ? `extras-saved:${state}` : "", () => loadSaved(state), state);
  const reforms = cat.data?.reforms || [];
  const scenarios = cat.data?.scenarios || [];
  const byKey = Object.fromEntries(reforms.map((r) => [r.key, r]));
  const about = Object.fromEntries(scenarios.map((s) => [s.id, s]));
  // The engine lists the extras by kind; one it does not list (an older back end) is left out.
  const listed = reforms.length ? EXTRA_IDS.filter((id) => byKey[id]?.kind === "extra") : EXTRA_IDS;
  const extras = listed.map((id) => {
    const mine = reformById(id);
    const available = !IS_STATIC || Boolean(saved.data?.[id]);
    return {
      id,
      name: byKey[id]?.title || mine.name,
      short: mine.short,
      line: mine.line,
      description: about[id]?.description || "",
      available,
      note: available ? "" : EXTRA_LOCAL_ONLY,
    };
  });
  const family = (id) => byKey[id]?.family || reformById(id)?.family || id;
  const excludes = (id) => about[id]?.excludes || [];
  const clash = (a, b) => {
    const fa = family(a);
    const fb = family(b);
    if (fa === fb) return false; // same family: switching one on replaces the other
    return excludes(a).includes(fb) || excludes(b).includes(fa) || excludes(a).includes(b) || excludes(b).includes(a);
  };
  /** The switched-on reforms that may not share a package with `id`. */
  const blockers = (id, on) => on.filter((r) => r !== id && clash(id, r));
  return { extras, blockers, description: (id) => about[id]?.description || "", ready: Boolean(cat.data) };
}

/** The short reason a reform cannot join the switched-on ones, or "". */
export function overlapReason(blocked) {
  if (!blocked.length) return "";
  const names = blocked.map((r) => reformById(r)?.name || r).join(" and ");
  return `Not with ${names}: they reach the same lots or projects. Switch ${blocked.length > 1 ? "those" : "it"} off to use this one.`;
}

/**
 * The homes a scenario reports outside the permit count (adaptive reuse: conversions), as the
 * engine's record: its range and its own name. Nothing for any other reform.
 */
export function OutsideCount({ run, id }) {
  const key = OUTSIDE[id];
  const n = key ? numberOf(run, `reform_results.${id}.records.${key}`) : null;
  if (!n?.shown) return null;
  const text = n.shown.p10 !== undefined ? `${n.shown.p10} to ${n.shown.p90}` : n.shown.value;
  return html`<div class="outside-count" data-testid="outside-count">
    <p class="lead-label">Outside the permit count</p>
    <p class="outside-value"><${Num} of=${n.name} n=${n} text=${text} />
      ${n.shown.p50 !== undefined && html`<span class="headline-sub">middle ${n.shown.p50}</span>`}
      <${Explain} of=${n.name} n=${n} /></p>
    <p class="card-sub outside-name">${n.name}</p>
  </div>`;
}

/** A scenario's records (each with its range and formula), in the engine's order. */
export function RecordsCard({ run, id }) {
  const prefix = `reform_results.${id}.records.`;
  const rows = Object.entries(run?.explanation?.numbers || {}).filter(([k]) => k.startsWith(prefix));
  if (!rows.length) return null;
  return html`<${Card} id="records" title="Everything this scenario reports"
      sub="The engine's records for this scenario, each with its likely range. Press a number for its formula and sources.">
    <dl class="records-list" data-testid="extra-records">
      ${rows.map(([k, n]) => {
        const sh = n.shown || {};
        const text = sh.p10 !== undefined ? `${sh.p10} to ${sh.p90}` : sh.value;
        return html`<div class="records-row" key=${k}>
          <dt>${n.name}</dt>
          <dd><${Num} of=${n.name} n=${n} text=${text} />${sh.p50 !== undefined && html`<span class="headline-sub">middle ${sh.p50}</span>`}</dd>
        </div>`;
      })}
    </dl>
  <//>`;
}

/**
 * The folded "Extra scenarios" group under the benchmark switches (State). It opens by itself
 * when an extra is on. Each switch is greyed out, with its reason, when it overlaps a reform
 * that is on, or (static site) when its run was not exported.
 */
export function ExtraSwitches({ sc, catalog }) {
  const { extras, blockers } = catalog;
  if (!extras.length) return null;
  const anyOn = extras.some((x) => sc.reforms.includes(x.id));
  return html`<details class="extra-group" data-testid="extra-scenarios" open=${anyOn}>
    <summary><span>Extra scenarios</span><span class="limit-count">${extras.length}</span></summary>
    <p class="card-sub extra-lead">Questions the five benchmarks leave open. Each is its own reform and never part of
      "All five reforms". One that reaches the same lots as a reform that is on is greyed out.</p>
    <div class="switches" role="group" aria-label="Extra scenarios">
      ${extras.map((x, i) => {
        const on = sc.reforms.includes(x.id);
        const reason = on ? "" : x.note || overlapReason(blockers(x.id, sc.reforms));
        return html`<div class="stagger" style=${`--i: ${i}`}>
          <${Switch} on=${on} label=${x.name} caption=${x.line} id=${x.id} reason=${reason}
            onToggle=${() => toggleReform(x.id)} />
        </div>`;
      })}
    </div>
  </details>`;
}

/** The note for an extra the static site does not hold (the Reform screen's run says the same). */
export { SCENARIO_ONLY };
