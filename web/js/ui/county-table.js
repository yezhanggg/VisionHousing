/**
 * The county table: the map's values as a sortable table (its twin for keyboard and screen
 * reader users), opened from the map key in State and Reform. One row per county: the
 * baseline, the homes added, per 1,000 existing homes, the change, the map class and the
 * evidence coverage (with its three checks). Every value is the map reply's own text.
 */
import { html, useEffect, useRef, useState } from "../lib/html.js";
import { CLASS_COLOURS, MEASURE_COLOURS } from "./map.js";
import { hoveredCounty, mapMeasure, useHotRows } from "./panel.js";
import { Num } from "./popover.js";

const SORTS = {
  name: (r) => r.name,
  baseline: (r) => r.baseline?.p50 ?? -Infinity,
  added: (r) => r.added?.p50 ?? -Infinity,
  change: (r) => r.change?.p50 ?? -Infinity,
  per_1000: (r) => r.per_1000?.p50 ?? -Infinity,
  coverage: (r) => r.coverage?.met ?? -Infinity,
};

/** A county's evidence coverage: its class and its three checks (the API's words). */
function covCell(r) {
  return html`<td class="cov-cell"><span class="class-chip" title=${r.coverage ? r.coverage.checks.map((k) => k.text).join(". ") : ""}>
      <span class="key-swatch" style=${`background: var(${MEASURE_COLOURS[r.coverage?.class] || "--no-data"})`}></span>
      ${r.coverage ? r.coverage.word : "—"}</span>
    ${r.coverage && html`<span class="cov-checks">${r.coverage.checks.map((k) => html`<span class=${k.met ? "is-met" : ""}>${k.met ? "✓" : "–"} ${k.label}</span>`)}</span>`}</td>`;
}

export function CountyTable({ map, onOpen, selected }) {
  // With the map shading evidence coverage, its column comes first.
  const first = mapMeasure.value === "coverage";
  const [sort, setSort] = useState({ by: "name", up: true });
  const any = map.reforms.length > 0;
  const cov = map.counties.some((c) => c.coverage);
  const words = Object.fromEntries(map.classes.map((c) => [c.id, c.word]));
  words.none = "No reform on";
  words.nodata = "No data";
  const rows = [...map.counties].sort((a, b) => {
    const [x, y] = [SORTS[sort.by](a), SORTS[sort.by](b)];
    const c = typeof x === "string" ? x.localeCompare(y) : x - y;
    return sort.up ? c : -c;
  });
  const head = (by, label, num = true) => {
    const on = sort.by === by;
    return html`<th scope="col" class=${num ? "num" : ""} aria-sort=${on ? (sort.up ? "ascending" : "descending") : "none"}>
      <button type="button" class="sort-btn" onClick=${() => setSort({ by, up: on ? !sort.up : by === "name" })}>
        ${label}<span aria-hidden="true">${on ? (sort.up ? " ▲" : " ▼") : ""}</span></button>
    </th>`;
  };
  const ids = map.ids || {};
  const body = useRef(null);
  useHotRows(body);
  // Opened from the legend: focus moves to the table, so the next Tab is inside it.
  const box = useRef(null);
  useEffect(() => box.current?.focus({ preventScroll: true }), []);
  return html`<section class="county-table" aria-labelledby="county-table-title" tabindex="-1" ref=${box}>
    <header class="sub-head">
      <h3 class="sub-title" id="county-table-title">Counties</h3>
      <p class="card-sub">The map's values as a table. Press a county name to centre and open it.</p>
    </header>
    <div>
      <div class="table-wrap">
        <table class="table">
          <caption class="visually-hidden">Counties, sortable. ${map.measure}.</caption>
          <thead><tr>
            ${head("name", "County", false)}
            ${cov && first && head("coverage", "Evidence coverage", false)}
            ${head("baseline", "Baseline homes")}
            ${any && head("added", "Homes added")}
            ${any && head("per_1000", "Per 1,000 homes")}
            ${any && head("change", "Change, middle")}
            <th scope="col">Class</th>
            ${cov && !first && head("coverage", "Evidence coverage", false)}
          </tr></thead>
          <tbody ref=${body}>
            ${rows.map(
              (r) => html`<tr class=${r.fips === selected ? "is-picked" : ""}
                data-fips=${r.fips} onMouseEnter=${() => (hoveredCounty.value = r.fips)}
                onMouseLeave=${() => (hoveredCounty.value = "")}>
                <th scope="row"><button type="button" class="link-btn" onClick=${() => onOpen(r.fips)}>${r.name}</button></th>
                ${cov && first && covCell(r)}
                <td class="num"><${Num} of=${`${r.name}: baseline homes`} n=${r.baseline && { ...r.baseline, ...ids.baseline }}
                  text=${r.baseline && `${r.baseline.shown.p10} to ${r.baseline.shown.p90}`} /></td>
                ${any &&
                html`<td class="num"><${Num} of=${`${r.name}: homes added`} n=${r.added && { ...r.added, ...ids.added }}
                    text=${r.added && `${r.added.shown.p10} to ${r.added.shown.p90}`} /></td>
                  <td class="num"><${Num} of=${`${r.name}: homes added per 1,000 existing homes`} n=${r.per_1000 && { ...r.per_1000, ...ids.added }}
                    text=${r.per_1000?.shown.p50} /></td>
                  <td class="num">${r.no_base
                    ? html`<span class="no-base" title=${r.no_base.rule}>${r.no_base.text}</span>`
                    : html`<${Num} of=${`${r.name}: change from baseline`} n=${r.change && { ...r.change, ...ids.change }}
                    text=${r.change?.shown.p50} />`}</td>`}
                <td><span class="class-chip"><span class="key-swatch" style=${`background: var(${CLASS_COLOURS[r.class]})`}></span>${words[r.class]}</span></td>
                ${cov && !first && covCell(r)}
              </tr>`,
            )}
          </tbody>
        </table>
      </div>
    </div>
  </section>`;
}
