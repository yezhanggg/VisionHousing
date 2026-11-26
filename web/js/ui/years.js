/**
 * The year slider: homes permitted in each past year, county by county, and the baseline's
 * projected years 2027-2031 on the same scale.
 *
 * Data: GET /states/ST/permit-years (`api.permitYears`). The server adds up the permit table's
 * building sizes, divides by each county's housing units and puts every county-year in one of
 * four fixed classes (breaks in assumptions/decision.yaml); the browser divides nothing. The
 * projected years are the baseline's own yearly county table, so they are a likely range, not
 * a count.
 *
 * On the map: the slider (or Play, one year every 900 ms, stopped by any key or press) pushes
 * the year's classes to the counties as feature state, so the map's west-to-east fill wave
 * plays as the years change. A bar per year shows the state's homes permitted (the projected
 * years with their likely range). The year table is the map's twin.
 */
import { api } from "../lib/api.js";
import { useJson } from "../lib/data.js";
import { html, useEffect, useLayoutEffect, useState } from "../lib/html.js";
import { Icon } from "./icons.js";
import { view3d, yearView } from "./map-state.js";
import { reducedMotion } from "./motion.js";

const PLAY_MS = 900;

/** The permit-years reply while the slider is on (loaded once per state). */
export function usePermitYears(state, on) {
  return useJson(on ? `permit-years:${state}` : "", () => api.permitYears(state));
}

/** Every year on the slider: the past years, then the projected ones. */
export function sliderYears(reply) {
  return [...(reply?.years || []), ...(reply?.projected?.years || [])];
}

/** One year's cell of a county row: {cls, units, rate, imputed, projected}. */
export function yearCell(reply, row, year) {
  const i = (reply?.years || []).indexOf(year);
  if (i >= 0) {
    return { cls: row.class[i], units: row.shown.units[i], rate: row.shown.per_1000[i], imputed: row.imputed[i], projected: false };
  }
  const j = (reply?.projected?.years || []).indexOf(year);
  const k = (reply?.counties || []).indexOf(row);
  const p = j >= 0 && k >= 0 ? reply.projected.counties[k] : null;
  if (!p) return { cls: "nodata", units: "—", rate: "—", imputed: false, projected: true };
  return { cls: p.class[j], units: `${p.shown.range[j]}, middle ${p.shown.p50[j]}`, rate: p.shown.per_1000[j], imputed: false, projected: true };
}

/** Each county's class in `year` (for the map's feature state). */
export function yearClasses(reply, year) {
  const out = {};
  for (const row of reply?.counties || []) out[row.fips] = yearCell(reply, row, year).cls;
  return out;
}

/** The state's line for a year: the pack's sum (past) or the engine's yearly record (projected). */
function stateLine(reply, year) {
  const i = (reply?.years || []).indexOf(year);
  if (i >= 0) return `${reply.state_past[i].shown} permitted`;
  const r = (reply?.projected?.state || []).find((x) => x.year === year);
  return r ? `likely ${r.shown.p10} to ${r.shown.p90}, middle ${r.shown.p50} (baseline, projected)` : "—";
}

/** The legend's "Past permits" switch. */
export function YearToggle() {
  const on = yearView.value.on;
  return html`<button type="button" class="btn btn-quiet btn-xs year-toggle" aria-pressed=${on ? "true" : "false"}
      onClick=${() => {
        yearView.value = { on: !on, year: yearView.peek().year };
        if (!on) view3d.value = false;
      }} title="Shade the counties by the homes permitted in one year">
    <${Icon} name="calendar" size=${14} /><span>Permits by year</span>
  </button>`;
}

/** Bars of the state's homes per year; the projected years carry a likely-range whisker. */
function YearBars({ reply, year, onPick }) {
  const past = reply.state_past || [];
  const proj = reply.projected?.state || [];
  const vals = [...past.map((x) => x.units), ...proj.map((x) => x.p90)];
  const top = Math.max(1e-9, ...vals);
  const n = past.length + (proj.length ? proj.length + 1 : 0);
  const w = 260 / Math.max(1, n);
  const h = (v) => (44 * v) / top;
  return html`<svg class="year-bars" viewBox="0 0 260 52" role="img"
      aria-label=${`Homes permitted in the state each year, ${reply.years[0]} to ${reply.latest}${proj.length ? `, and the baseline's projected years ${proj[0].year} to ${proj[proj.length - 1].year}` : ""}. The year table has the values.`}>
    ${past.map((x, i) => html`<rect x=${i * w + 0.5} y=${48 - h(x.units)} width=${Math.max(1, w - 1)} height=${h(x.units)}
      class=${x.year === year ? "is-on" : ""} onClick=${() => onPick(x.year)}><title>${x.year}: ${x.shown}</title></rect>`)}
    ${proj.map((x, j) => {
      const i = past.length + 1 + j;
      const cx = i * w + w / 2;
      return html`<g class=${`is-proj ${x.year === year ? "is-on" : ""}`} onClick=${() => onPick(x.year)}>
        <rect x=${i * w + 0.5} y=${48 - h(x.p50)} width=${Math.max(1, w - 1)} height=${h(x.p50)}><title>${x.year}: likely ${x.shown.p10} to ${x.shown.p90}</title></rect>
        <line x1=${cx} x2=${cx} y1=${48 - h(x.p90)} y2=${48 - h(x.p10)} />
      </g>`;
    })}
    <line x1="0" x2="260" y1="48.5" y2="48.5" class="year-axis" />
  </svg>`;
}

/** The year table: every county's homes and rate in the year shown (the map's twin). */
function YearTable({ reply, year }) {
  return html`<details class="year-table">
    <summary>Year table (${year})</summary>
    <div class="table-wrap">
      <table class="table">
        <caption class="visually-hidden">Homes permitted in ${year} by county</caption>
        <thead><tr><th scope="col">County</th><th scope="col" class="num">Homes</th><th scope="col" class="num">Per 1,000 homes</th></tr></thead>
        <tbody>
          ${reply.counties.map((row) => {
            const c = yearCell(reply, row, year);
            return html`<tr data-fips=${row.fips}>
              <th scope="row">${row.name}${c.imputed ? html` <span class="year-est" title=${reply.imputed_note}>est.</span>` : ""}</th>
              <td class="num">${c.units}</td><td class="num">${c.rate}</td>
            </tr>`;
          })}
        </tbody>
      </table>
    </div>
    <p class="card-sub">${reply.formula}. ${reply.imputed_note}</p>
  </details>`;
}

/** The map key while the slider is on: the slider, Play, the classes, the bars and the table. */
export function YearKey({ state }) {
  const reply = usePermitYears(state, true);
  const data = reply.data;
  const years = sliderYears(data);
  const v = yearView.value;
  const year = years.includes(v.year) ? v.year : data?.latest;
  const [playing, setPlaying] = useState(false);
  // Start on the latest past year.
  useEffect(() => {
    if (data && !years.includes(yearView.peek().year)) yearView.value = { on: true, year: data.latest };
  }, [data]);
  // A layout effect: the stop keys listen from the moment Play shows as pressed.
  useLayoutEffect(() => {
    if (!playing) return undefined;
    // Any key or press stops it, except Space or Enter on the Play button itself: its own
    // click pauses, and a Space keydown must not stop it first, or the keyup click would
    // start it again.
    const stop = (e) => {
      const own = e.target?.closest?.(".year-play") && (e.key === " " || e.key === "Enter");
      if (!own) setPlaying(false);
    };
    const t = setInterval(() => {
      const at = years.indexOf(yearView.peek().year);
      if (at >= years.length - 1) {
        setPlaying(false);
        return;
      }
      yearView.value = { on: true, year: years[at + 1] };
    }, PLAY_MS);
    window.addEventListener("keydown", stop);
    window.addEventListener("pointerdown", stop);
    return () => {
      clearInterval(t);
      window.removeEventListener("keydown", stop);
      window.removeEventListener("pointerdown", stop);
    };
  }, [playing]);
  if (!data) {
    return html`<div class="year-key"><h2 class="key-title" id="map-key-title">Homes permitted by year</h2>
      <p class="key-hint" role="status">${reply.error || "Loading the permits by year…"}</p></div>`;
  }
  const idx = Math.max(0, years.indexOf(year));
  const projected = !(data.years || []).includes(year);
  const pick = (y) => (yearView.value = { on: true, year: y });
  return html`<div class="year-key">
    <h2 class="key-title" id="map-key-title">${data.title}, ${year}${projected ? " (baseline, projected)" : ""}</h2>
    <div class="year-row">
      <label class="year-slider">
        <span class="visually-hidden">Year shown on the map</span>
        <input type="range" min="0" max=${years.length - 1} step="1" value=${idx}
          aria-valuetext=${projected ? `${year}, baseline, projected` : String(year)}
          onInput=${(e) => pick(years[Number(e.currentTarget.value)])} />
      </label>
      <output class="year-out" aria-hidden="true">${year}</output>
      ${!reducedMotion() &&
      html`<button type="button" class="btn btn-quiet btn-xs year-play" aria-pressed=${playing ? "true" : "false"}
          onPointerDown=${(e) => e.stopPropagation()}
          onClick=${(e) => {
            e.stopPropagation();
            if (!playing && idx >= years.length - 1) pick(years[0]);
            setPlaying(!playing);
          }}>
        <${Icon} name=${playing ? "pause" : "play"} size=${13} /><span>${playing ? "Pause" : "Play"}</span></button>`}
    </div>
    <p class="key-hint year-state">State: ${stateLine(data, year)}</p>
    <${YearBars} reply=${data} year=${year} onPick=${pick} />
    <ol class="key-ramp key-ramp-4" aria-label="Homes permitted per 1,000 existing homes">
      ${data.classes.map((c, i) => html`<li class="key-step" style=${`--i: ${i}`}>
        <span class="key-chip is-static"><span class="key-swatch" style=${`background: var(${YEAR_COLOURS[c.id]})`}></span>
          <span class="key-label"><span class="key-range">${c.label}</span></span></span>
      </li>`)}
    </ol>
    <${YearTable} reply=${data} year=${year} />
  </div>`;
}

/** The year classes' colour tokens (the same ramp as the map's per-1,000 measure). */
export const YEAR_COLOURS = { q1: "--seq-2", q2: "--seq-3", q3: "--seq-5", q4: "--seq-6", nodata: "--no-data" };
