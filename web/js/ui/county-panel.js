/**
 * The county panel: one county under the current scenario, opened from the map or the
 * county table.
 *
 *   - the county's baseline, the homes the switched-on reforms add there and that change as
 *     a share of its baseline (from the map reply, GET /states/{st}/map);
 *   - each switched-on reform alone in this county (from the run's explanation);
 *   - a small map of its census tracts shaded by the homes one reform adds in each tract,
 *     with the same values in a table (GET /states/{st}/counties/{fips}, `tract_results`).
 *
 * The tract map is plain SVG drawn from the county reply's outlines: no map library, so it
 * also prints. Class breaks come with the reply and stay the same while the reform shown
 * changes.
 */
import { reformById } from "../content/reforms.js";
import { api, scenarioQuery } from "../lib/api.js";
import { numberOf, useJson } from "../lib/data.js";
import { html, useState } from "../lib/html.js";
import { Status } from "./bits.js";
import { Icon } from "./icons.js";
import { Num } from "./popover.js";

const TRACT_SWATCHES = ["--seq-2", "--seq-3", "--seq-4", "--seq-5", "--seq-6"];

/** SVG path text for the tract outlines, projected to fit a `w` wide box. */
function project(features, w = 320) {
  const pts = [];
  const walk = (c) => (typeof c[0] === "number" ? pts.push(c) : c.forEach(walk));
  features.forEach((f) => f.geometry && walk(f.geometry.coordinates));
  if (!pts.length) return { paths: [], h: 0 };
  const lat0 = pts.reduce((a, p) => a + p[1], 0) / pts.length;
  const k = Math.cos((lat0 * Math.PI) / 180);
  const xs = pts.map((p) => p[0] * k);
  const ys = pts.map((p) => -p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const scale = w / Math.max(x1 - x0, 1e-9);
  const h = Math.max(40, (y1 - y0) * scale);
  const ring = (r) => `M${r.map((p) => `${((p[0] * k - x0) * scale).toFixed(1)},${((-p[1] - y0) * scale).toFixed(1)}`).join("L")}Z`;
  const poly = (g) =>
    g.type === "Polygon" ? g.coordinates.map(ring).join("") : g.coordinates.flatMap((pp) => pp.map(ring)).join("");
  return {
    paths: features.filter((f) => f.geometry).map((f) => ({ geoid: String(f.properties?.geoid), d: poly(f.geometry) })),
    h,
  };
}

function tractClass(p50, breaks) {
  if (!(p50 > 0)) return -1;
  let i = 0;
  while (i < breaks.length && p50 > breaks[i]) i += 1;
  return i;
}

function TractMap({ county, reformKey }) {
  const tr = county.tract_results;
  const res = tr?.reforms?.[reformKey];
  const shapes = (county.boundaries?.features || []).filter((f) => String(f.properties?.geoid).length === 11);
  const { paths, h } = project(shapes);
  const byId = Object.fromEntries((res?.tracts || []).map((t) => [t.geoid, t]));
  const fill = (geoid) => {
    const t = byId[geoid];
    if (!t) return "var(--no-data)";
    const c = tractClass(t.p50, tr.breaks);
    return c < 0 ? "var(--not-modelled)" : `var(${TRACT_SWATCHES[Math.min(c + 1, TRACT_SWATCHES.length - 1)]})`;
  };
  return html`<figure class="tract-figure">
    ${paths.length > 0 &&
    html`<svg class="tract-map" viewBox=${`-2 -2 324 ${h + 4}`} role="img"
        aria-label=${`Census tracts of ${county.name}, shaded by homes added by ${res?.title || "the reform"}. The tract table below has the same values.`}>
      ${paths.map((p) => html`<path d=${p.d} fill=${fill(p.geoid)}><title>${p.geoid}: ${byId[p.geoid]?.shown?.p50 || "no value"}</title></path>`)}
    </svg>`}
    <figcaption>
      <ol class="key-ramp tract-key" aria-label="Homes added per tract, middle estimate">
        <li class="key-step"><span class="key-swatch" style="background: var(--not-modelled)"></span><span class="key-label">None</span></li>
        ${(tr?.labels || []).map(
          (label, i) => html`<li class="key-step">
            <span class="key-swatch" style=${`background: var(${TRACT_SWATCHES[i + 1]})`}></span>
            <span class="key-label">${label}</span>
          </li>`,
        )}
      </ol>
    </figcaption>
  </figure>`;
}

function TractTable({ res, formula }) {
  const rows = [...(res?.tracts || [])].sort((a, b) => b.p50 - a.p50);
  return html`<details class="tract-details">
    <summary>Tract table (${rows.length} tracts)</summary>
    <div class="table-wrap">
      <table class="table">
        <caption class="visually-hidden">Homes added per census tract by ${res?.title}</caption>
        <thead><tr><th scope="col">Tract</th><th scope="col" class="num">Likely range</th><th scope="col" class="num">Middle</th></tr></thead>
        <tbody>
          ${rows.map(
            (t) => html`<tr>
              <th scope="row">${t.geoid}</th>
              <td class="num"><${Num} of=${`Tract ${t.geoid}, homes added`} text=${`${t.shown.p10} to ${t.shown.p90}`}
                n=${{ ...t, name: `Homes added in tract ${t.geoid} by ${res.title}, run alone`, formula, assumption_ids: res.assumption_ids, source_ids: res.source_ids }} /></td>
              <td class="num">${t.shown.p50}</td>
            </tr>`,
          )}
        </tbody>
      </table>
    </div>
  </details>`;
}

export function CountyPanel({ sc, fips, row, ids, run, onClose }) {
  const query = scenarioQuery(sc);
  const county = useJson(`county:${sc.state}:${fips}:${query}`, () => api.county(sc.state, fips, query));
  const data = county.data;
  const withTracts = sc.reforms.filter((k) => data?.tract_results?.reforms?.[k]);
  const [pick, setPick] = useState("");
  const shown = withTracts.includes(pick) ? pick : withTracts[0];
  const name = row?.name || data?.name || fips;
  const res = shown ? data.tract_results.reforms[shown] : null;
  return html`<section class="card county-panel" aria-labelledby="county-title">
    <header class="card-head">
      <div>
        <h2 class="card-title" id="county-title">${name}</h2>
        <p class="card-sub">County ${fips} · the next five years</p>
      </div>
      <button type="button" class="btn btn-quiet btn-xs" onClick=${onClose}>
        <${Icon} name="close" size=${16} /><span>Close panel</span></button>
    </header>
    <div class="card-body">
      <dl class="county-numbers">
        <div><dt>Baseline homes</dt>
          <dd><${Num} of=${`${name}: baseline homes`} text=${row?.baseline && `${row.baseline.shown.p10} to ${row.baseline.shown.p90}`}
            n=${row?.baseline && { ...row.baseline, ...ids?.baseline }} /></dd></div>
        ${sc.reforms.length > 0 &&
        html`<div><dt>Added by the reforms</dt>
            <dd><${Num} of=${`${name}: homes added`} text=${row?.added && `${row.added.shown.p10} to ${row.added.shown.p90}`}
              n=${row?.added && { ...row.added, ...ids?.added }} /></dd></div>
          <div><dt>Change from baseline</dt>
            <dd><${Num} of=${`${name}: change from baseline`} text=${row?.change && `${row.change.shown.p10} to ${row.change.shown.p90}`}
              n=${row?.change && { ...row.change, ...ids?.change }} /></dd></div>`}
      </dl>
      ${sc.reforms.length > 1 &&
      html`<div>
        <p class="field-label">Each reform alone in this county</p>
        <ul class="verdict-list">
          ${sc.reforms.map((k) => {
            const n = numberOf(run, `reform_results.${k}.counties.${fips}`);
            const r = reformById(k);
            return html`<li class="verdict"><span>${r?.name || k}</span>
              <${Num} of=${`${name}: ${r?.name || k}`} n=${n} text=${n && `${n.shown.p10} to ${n.shown.p90}`} /></li>`;
          })}
        </ul>
      </div>`}
      <${Status} state=${county} what="the tract detail" />
      ${data &&
      (withTracts.length
        ? html`<div class="tract-block">
            <p class="field-label">Homes added in each census tract</p>
            ${withTracts.length > 1 &&
            html`<div class="pills" role="group" aria-label="Reform shown on the tract map">
              ${withTracts.map(
                (k) => html`<button type="button" class="pill" aria-pressed=${k === shown ? "true" : "false"}
                  onClick=${() => setPick(k)}>${reformById(k)?.short || k}</button>`,
              )}
            </div>`}
            <${TractMap} county=${data} reformKey=${shown} />
            <p class="card-sub">${res.title}, run alone. Middle estimate per tract.</p>
            <${TractTable} res=${res} formula=${res.formula} />
          </div>`
        : html`<p class="card-sub">
            ${sc.reforms.length
              ? "The reforms switched on work county by county, so there are no tract values to shade."
              : "Switch a reform on to see the homes it adds in each census tract."}
          </p>`)}
    </div>
  </section>`;
}
