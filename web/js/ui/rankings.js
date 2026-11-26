/**
 * Rankings: the counties in order by one measure, the top ten or the bottom ten, as bars
 * that grow in. The order, the rank words ("3rd of 67") and every value come from the
 * server (the `rankings` part of GET /states/{st}/map, api/views.py); this only shows them.
 *
 * Measures with a reform on: homes added, homes added per 1,000 existing homes, and homes
 * added as a share of the county's baseline. With none on: baseline homes, and per 1,000.
 * Each bar is the middle estimate; the words beside it are the likely range.
 * Pressing a county centres it on the map and opens it; pointing at one outlines it.
 */
import { html, useRef, useState } from "../lib/html.js";
import { Empty, Status } from "./bits.js";
import { HBars } from "./charts.js";
import { hoveredCounty, useHotRows } from "./panel.js";

const SHOW = 10;

export function Rankings({ map, onPick }) {
  const [measure, setMeasure] = useState("");
  const [end, setEnd] = useState("top");
  const box = useRef(null);
  useHotRows(box);
  if (!map.data) return html`<${Status} state=${map} what="the rankings" />`;
  const lists = map.data.rankings || [];
  if (!lists.length) return html`<${Empty}>This map reply has no rankings yet.<//>`;
  const list = lists.find((l) => l.id === measure) || lists[0];
  const rows = end === "top" ? list.rows.slice(0, SHOW) : list.rows.slice(-SHOW).reverse();
  const items = rows.map((r) => ({
    key: r.fips,
    label: r.name,
    rank: r.rank,
    rank_text: r.rank_text,
    value: r.value.p50,
    text: r.value.shown.p10 === r.value.shown.p90 ? r.value.shown.p50 : `${r.value.shown.p10} to ${r.value.shown.p90}`,
  }));
  const few = list.count <= SHOW;
  return html`<div class="rankings">
    <div class="seg-row" role="group" aria-label="Rank counties by">
      ${lists.map(
        (l) => html`<button type="button" class="seg" aria-pressed=${l.id === list.id ? "true" : "false"}
          onClick=${() => setMeasure(l.id)}>${l.label}</button>`,
      )}
    </div>
    ${!few &&
    html`<div class="seg-row" role="group" aria-label="Which end of the list">
      <button type="button" class="seg" aria-pressed=${end === "top" ? "true" : "false"} onClick=${() => setEnd("top")}>Top ${SHOW}</button>
      <button type="button" class="seg" aria-pressed=${end === "bottom" ? "true" : "false"} onClick=${() => setEnd("bottom")}>Bottom ${SHOW}</button>
    </div>`}
    <p class="card-sub">${list.title}. ${few ? `All ${list.count} counties.` : `${end === "top" ? "Highest" : "Lowest"} ${SHOW} of ${list.count}.`}${" "}
      Bars show the middle estimate; the words give the likely range. Press a county to centre it.</p>
    <div ref=${box} onMouseLeave=${() => (hoveredCounty.value = "")}>
      <${HBars} key=${`${list.id}|${end}`} title=${list.title} items=${items} max=${list.rows[0]?.value.p50 > 0 ? list.rows[0].value.p50 : null}
        onPick=${(x) => onPick(x.key)} onHover=${(x) => (hoveredCounty.value = x ? x.key : "")} />
    </div>
  </div>`;
}
