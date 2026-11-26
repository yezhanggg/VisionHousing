/**
 * "What this law changes": a map mode of the Reform screen, its key, its table twin, the
 * county card and the "Read next" list (src/visionhousing/api/law_changes.py).
 *
 * Every county gets one of four answers for the reform shown: already allowed, allowed with a
 * changed condition, newly allowed, or unresolved (no verified rule in force answers the
 * question). A place a bill does not reach is "outside this bill". The map shades the answers;
 * the key lists them with the statewide share of each (the server's text, with its
 * denominator) and switches the values shown between legal additional capacity and additional
 * units authorized (both from the server). Clicking a county opens its card: the official
 * section, the effective date, the denominator, the observed or fallback share, the model
 * stage each answer changes, and the municipal rows read there. The table twin lists every
 * county with the same values. "Read next" lists the top unread ordinances with the bounding
 * runs: statewide units if read as permissive or restrictive, whether the preferred option
 * would switch, and the review effort.
 *
 * Data: GET /states/ST/law-changes?r=KEY and /law-changes/read-next?r=KEY (live); static:
 * data/ST/law_changes/KEY.json ({changes, read_next}) when the export holds it, else the
 * "needs the local app" note. No number is worked out here: every value shown is a `shown`
 * text from the server, with its formula and ids a press away.
 */
import { signal } from "@preact/signals";
import "../../css/law-changes.css";
import { API_BASE, DATA_BASE, IS_STATIC, LocalOnlyError } from "../lib/api.js";
import { useJson } from "../lib/data.js";
import { html, useEffect, useRef, useState } from "../lib/html.js";
import { Status } from "./bits.js";
import { Icon } from "./icons.js";
import { hoveredCounty, useHotRows } from "./panel.js";

/** The mode: on or off, and which values the key, table and card show ("units" or "capacity"). */
export const lawView = signal({ on: false, view: "units" });

/** Reforms with this view (the keys of `reforms` in assumptions/law_changes.yaml). */
export const LAW_REFORMS = ["adu", "missing_middle", "tod", "parking", "fees", "pa_hb2186", "pa_sb1346"];

/** The note on a static site whose export does not hold this view. */
export const LAW_LOCAL_ONLY = "What this law changes needs the local app (make app).";

/** Answer id to the map class id (one palette with the map's other classes, map.js). */
export const lawClassId = (answer) => `law_${answer}`;

const ORDER = ["already_allowed", "changed_condition", "newly_allowed", "unresolved"];

async function getJson(url) {
  const reply = await fetch(url, { headers: { accept: "application/json" } });
  const body = await reply.json().catch(() => null);
  if (!reply.ok) throw new Error(body?.message || `the request failed (${reply.status})`);
  return body;
}

/** The exported file of one reform ({changes, read_next}); a missing file means local only. */
const saved = {};
function staticFile(st, reform) {
  const path = `${DATA_BASE}/${encodeURIComponent(st)}/law_changes/${encodeURIComponent(reform)}.json`;
  saved[path] ||= fetch(path, { headers: { accept: "application/json" } }).then(async (reply) => {
    if (reply.status === 404) throw new LocalOnlyError(LAW_LOCAL_ONLY);
    if (!reply.ok) throw new Error(`the data file failed to load (${reply.status})`);
    return reply.json();
  });
  return saved[path];
}

/** The payload of one reform (GET /states/ST/law-changes?r=KEY; static: the exported file). */
export function lawChanges(st, reform) {
  if (IS_STATIC) return staticFile(st, reform).then((f) => f.changes);
  return getJson(`${API_BASE}/states/${encodeURIComponent(st)}/law-changes?r=${encodeURIComponent(reform)}`).then((b) => b.changes);
}

/** The read-next bounds (slow the first time on the server; static: only when exported). */
export function lawReadNext(st, reform) {
  if (IS_STATIC) {
    return staticFile(st, reform).then((f) => {
      if (!f.read_next) throw new LocalOnlyError("The read-next runs need the local app (make app).");
      return f.read_next;
    });
  }
  return getJson(`${API_BASE}/states/${encodeURIComponent(st)}/law-changes/read-next?r=${encodeURIComponent(reform)}`).then(
    (b) => b.read_next,
  );
}

/** The payload while the mode is on (one request per state and reform, shared by every part). */
export function useLawChanges(st, reform, on = true) {
  const law = useJson(on && reform ? `law-changes:${st}:${reform}` : "", () => lawChanges(st, reform));
  // While another reform's reply is still in hand, show nothing rather than its numbers.
  const mine = law.data && law.data.reform === reform && law.data.state === st;
  return mine ? law : { ...law, data: null };
}

/** Each county's map class under the mode. */
export function lawClasses(data) {
  const out = {};
  for (const c of data?.counties || []) out[c.fips] = lawClassId(c.answer);
  return out;
}

/** A shown number with its formula and ids behind a small "how" disclosure. */
function Shown({ n, text = null, label = "" }) {
  if (!n) return html`<span class="law-none">—</span>`;
  const value = text ?? (typeof n.shown === "string" ? n.shown : n.text ?? `${n.shown.p10} to ${n.shown.p90}`);
  const ids = [...(n.source_ids || []), ...(n.assumption_ids || [])];
  return html`<span class="law-num" title=${`${label ? `${label}: ` : ""}${n.formula || ""}${ids.length ? ` [${ids.join(", ")}]` : ""}`}>${value}</span>`;
}

function Swatch({ answer }) {
  return html`<span class="key-swatch law-swatch" data-answer=${answer} aria-hidden="true"></span>`;
}

function wordOf(data, answer) {
  if (answer === "outside") return data.outside_class?.word || "Outside this bill";
  return data.classes.find((c) => c.id === answer)?.word || answer;
}

/** The county's value for the current view (units authorized or legal capacity). */
function viewValue(c, view) {
  if (view === "capacity") return c.capacity ? html`<${Shown} n=${c.capacity} text=${c.capacity.text} label="Legal additional capacity" />` : html`<span class="law-none">not a capacity change</span>`;
  return c.units ? html`<${Shown} n=${c.units} text=${`${c.units.shown.p10} to ${c.units.shown.p90}`} label="Additional units authorized" />` : html`<span class="law-none">—</span>`;
}

const VIEWS = [
  ["units", "Units authorized"],
  ["capacity", "Legal capacity"],
];

/** The two-way switch: legal additional capacity or additional units authorized. */
export function ViewSwitch() {
  const v = lawView.value.view;
  return html`<div class="law-views" role="group" aria-label="Values shown">
    ${VIEWS.map(([id, label]) => html`<button type="button" class="seg seg-xs" aria-pressed=${v === id ? "true" : "false"}
      data-view=${id} onClick=${() => (lawView.value = { ...lawView.peek(), view: id })}>${label}</button>`)}
  </div>`;
}

/** The button on the Reform screen that turns the mode on and off. */
export function LawToggle() {
  const on = lawView.value.on;
  return html`<button type="button" class=${`btn btn-xs law-toggle ${on ? "btn-primary" : "btn-quiet"}`}
      aria-pressed=${on ? "true" : "false"} data-testid="law-mode"
      onClick=${() => (lawView.value = { ...lawView.peek(), on: !on })}>
    <${Icon} name="layers" size=${14} /><span>What this law changes</span>
  </button>`;
}

/** The map key in this mode: the four answers with their statewide shares, and the value switch. */
export function LawKey({ state, reform }) {
  const law = useLawChanges(state, reform);
  const d = law.data;
  const sw = d?.statewide;
  const view = lawView.value.view;
  return html`<div class="law-key" data-testid="law-key">
    <h2 class="key-title" id="map-key-title">What this law changes${d ? `: ${d.title}` : ""}</h2>
    <${Status} state=${law} what="what this law changes" lines=${2} />
    ${d &&
    html`<ol class="law-key-list" aria-label="Answers">
        ${ORDER.map(
          (id) => html`<li class="law-key-row" data-answer=${id}>
            <${Swatch} answer=${id} />
            <span class="law-key-word">${wordOf(d, id)}</span>
            <span class="law-key-share"><${Shown} n=${sw.classes[id].share} label=${`Share of ${d.denominator.label}`} /></span>
          </li>`,
        )}
        ${sw.outside?.value > 0 &&
        html`<li class="law-key-row" data-answer="outside"><${Swatch} answer="outside" />
          <span class="law-key-word">${wordOf(d, "outside")}</span>
          <span class="law-key-share"><${Shown} n=${sw.outside} /></span></li>`}
      </ol>
      <p class="key-hint">Shares of ${sw.denominator.shown} ${d.denominator.label === "single-family lots" ? "" : `(${d.denominator.label})`} in the reform's reach, statewide.
        ${sw.unresolved_result_share && html` Share of the added homes in unresolved places: <${Shown} n=${sw.unresolved_result_share} />.`}</p>
      <${ViewSwitch} />
      <p class="law-key-total" data-testid="law-total">
        ${view === "capacity"
          ? sw.capacity
            ? html`Legal additional capacity: <strong><${Shown} n=${sw.capacity} text=${sw.capacity.text} /></strong>`
            : html`${sw.capacity_note}`
          : sw.units
            ? html`Additional units authorized: <strong><${Shown} n=${sw.units} text=${`${sw.units.shown.p10} to ${sw.units.shown.p90}`} /></strong>`
            : "Additional units authorized: not in the reply."}
      </p>`}
  </div>`;
}

/** One county's card: the proposal, the denominator and shares, the stage, the municipal rows. */
export function LawCard({ state, reform, fips, onClose }) {
  const law = useLawChanges(state, reform);
  const d = law.data;
  const c = d?.counties.find((x) => x.fips === fips);
  const card = useRef(null);
  useEffect(() => card.current?.focus({ preventScroll: true }), [fips]);
  const p = d?.provision;
  const view = lawView.value.view;
  return html`<section class="card law-card" aria-labelledby="law-card-title" tabindex="-1" ref=${card} data-testid="law-card" data-fips=${fips}>
    <header class="card-head">
      <div>
        <p class="kicker">What this law changes</p>
        <h2 class="card-title" id="law-card-title">${c?.name || fips}</h2>
      </div>
      <button type="button" class="btn btn-quiet btn-xs" onClick=${onClose} title="Close (Esc)">
        <${Icon} name="close" size=${15} /><span class="visually-hidden">Close</span></button>
    </header>
    <div class="card-body">
      <${Status} state=${law} what="what this law changes" />
      ${c &&
      html`<p class="law-answer" data-answer=${c.answer}><${Swatch} answer=${c.answer} /><strong>${wordOf(d, c.answer)}</strong>
          <span class="card-sub">${c.answer === "outside" ? d.outside_class.meaning : d.classes.find((k) => k.id === c.answer)?.meaning}</span></p>
        <dl class="law-facts">
          <div><dt>Proposal</dt><dd>${p.citation}${p.sections?.length > 0 && html`<ul class="law-sections">${p.sections.map((s) => html`<li>${s}</li>`)}</ul>`}
            ${p.url && html` <a href=${p.url} rel="noopener noreferrer" target="_blank">Official text</a>`}</dd></div>
          <div><dt>Effective date</dt><dd>${p.effective_text}</dd></div>
          ${p.current_law?.length > 0 &&
          html`<div><dt>State law today</dt><dd><ul class="law-sections">${p.current_law.map(
            (x) => html`<li>${x.citation}${x.effective_date ? ` (effective ${x.effective_date})` : ""}: ${x.summary}</li>`,
          )}</ul></dd></div>`}
          <div><dt>Denominator</dt><dd><${Shown} n=${c.denominator} /> ${d.denominator.label}${c.outside?.value > 0 ? html`; outside the reform: <${Shown} n=${c.outside} />` : ""}</dd></div>
          <div><dt>${view === "capacity" ? "Legal additional capacity" : "Additional units authorized"}</dt><dd>${viewValue(c, view)}</dd></div>
          <div><dt>Observed or fallback</dt><dd>${c.observed.text}</dd></div>
          <div><dt>Engine</dt><dd>${c.engine.text}</dd></div>
        </dl>
        <table class="table law-shares">
          <caption class="visually-hidden">Share of ${d.denominator.label} in each answer, and the model stage it changes</caption>
          <thead><tr><th scope="col">Answer</th><th scope="col" class="num">Share</th><th scope="col">Model stage it changes</th></tr></thead>
          <tbody>${ORDER.map(
            (id) => html`<tr data-answer=${id}><th scope="row"><${Swatch} answer=${id} /> ${wordOf(d, id)}</th>
              <td class="num"><${Shown} n=${c.classes[id].share} /></td><td class="law-stage">${c.stage[id]}</td></tr>`,
          )}</tbody>
        </table>
        ${c.municipalities.length > 0 &&
        html`<details class="law-munis" open=${c.municipalities.length <= 4}>
          <summary>Municipal rules read here (${c.municipalities.length})</summary>
          <ul class="law-muni-list">${c.municipalities.map(
            (m) => html`<li class="law-muni" data-answer=${m.answer}>
              <p class="law-muni-head"><${Swatch} answer=${m.answer} /><strong>${m.name}</strong> · ${wordOf(d, m.answer)}</p>
              <p class="law-muni-rule">${m.rule_text}</p>
              <p class="card-sub">${m.why}</p>
              <p class="card-sub">${m.section ? `Section: ${m.section}. ` : ""}${m.document ? `Document: ${m.document}. ` : ""}
                Effective ${m.dates.effective_date || "not stated"}${m.dates.adopted_on ? `; adopted ${m.dates.adopted_on}` : ""}${m.dates.in_force_by ? `; in force by ${m.dates.in_force_by}` : ""}; read ${m.dates.reviewed_on || "not recorded"} (${m.status}${m.in_force ? ", in force on " + m.dates.as_of : ", not counted on " + m.dates.as_of}).
                Weight: <${Shown} n=${m.denominator} />.</p>
            </li>`,
          )}</ul>
        </details>`}`}
      ${d && !c && html`<p class="card-sub">This county is not in the reply.</p>`}
    </div>
  </section>`;
}

/** The table twin of the map in this mode: every county with its answer, shares and value. */
export function LawTable({ state, reform, onOpen, selected }) {
  const law = useLawChanges(state, reform);
  const d = law.data;
  const view = lawView.value.view;
  const body = useRef(null);
  useHotRows(body);
  const box = useRef(null);
  useEffect(() => box.current?.focus({ preventScroll: true }), []);
  const [by, setBy] = useState("name");
  const rows = [...(d?.counties || [])].sort((a, b) =>
    by === "name" ? a.name.localeCompare(b.name) : (b.classes.unresolved.share?.value ?? -1) - (a.classes.unresolved.share?.value ?? -1),
  );
  return html`<section class="county-table law-table" aria-labelledby="law-table-title" tabindex="-1" ref=${box} data-testid="law-table">
    <header class="sub-head">
      <h3 class="sub-title" id="law-table-title">What this law changes, by county</h3>
      <p class="card-sub">The map's answers as a table. Press a county name to open its card.</p>
      <div class="law-table-tools">
        <${ViewSwitch} />
        <label class="law-sort">Sort by <select class="select select-xs" value=${by} onChange=${(e) => setBy(e.currentTarget.value)}>
          <option value="name">County</option><option value="unresolved">Most unresolved</option></select></label>
      </div>
    </header>
    <${Status} state=${law} what="what this law changes" />
    ${d &&
    html`<div class="table-wrap">
      <table class="table">
        <caption class="visually-hidden">Counties: answer, share of ${d.denominator.label} in each answer, and ${view === "capacity" ? "legal additional capacity" : "additional units authorized"}</caption>
        <thead><tr>
          <th scope="col">County</th><th scope="col">Answer</th>
          ${ORDER.map((id) => html`<th scope="col" class="num">${wordOf(d, id)}</th>`)}
          <th scope="col" class="num">${d.denominator.label}</th>
          <th scope="col" class="num">${view === "capacity" ? "Legal capacity" : "Units authorized"}</th>
        </tr></thead>
        <tbody ref=${body}>${rows.map(
          (c) => html`<tr class=${c.fips === selected ? "is-picked" : ""} data-fips=${c.fips}
              onMouseEnter=${() => (hoveredCounty.value = c.fips)} onMouseLeave=${() => (hoveredCounty.value = "")}>
            <th scope="row"><button type="button" class="link-btn" onClick=${() => onOpen(c.fips)}>${c.name}</button></th>
            <td><span class="class-chip"><${Swatch} answer=${c.answer} />${wordOf(d, c.answer)}</span></td>
            ${ORDER.map((id) => html`<td class="num"><${Shown} n=${c.classes[id].share} /></td>`)}
            <td class="num"><${Shown} n=${c.denominator} /></td>
            <td class="num">${viewValue(c, view)}</td>
          </tr>`,
        )}</tbody>
      </table>
    </div>`}
  </section>`;
}

/** The summary under the toggle and the "Read next" list. */
export function ReadNext({ state, reform }) {
  const [asked, setAsked] = useState(false);
  const rn = useJson(asked ? `law-read-next:${state}:${reform}` : "", () => lawReadNext(state, reform));
  useEffect(() => setAsked(false), [state, reform]);
  const r = rn.data;
  return html`<section class="card law-read-next" aria-labelledby="law-read-next-title" data-testid="read-next">
    <header class="card-head"><div>
      <h2 class="card-title" id="law-read-next-title">Read next</h2>
      <p class="card-sub">The unread ordinances that matter most, each run twice: read as permissive, then as restrictive.</p>
    </div></header>
    <div class="card-body">
      ${!asked &&
      html`<button type="button" class="btn btn-xs btn-quiet" data-testid="read-next-run" onClick=${() => setAsked(true)}>
        Show the read-next list${IS_STATIC ? "" : " (runs the engine; the first time can take half a minute)"}</button>`}
      <${Status} state=${rn} what="the read-next list">Running the bounding runs… The first time can take half a minute.<//>
      ${r?.note && html`<p class="card-sub">${r.note}</p>`}
      ${r?.current &&
      html`<p class="card-sub">Today: ${r.options.map((o, i) => html`${i ? "; " : ""}${r.labels?.[o] || o} adds <${Shown} n=${r.current[o]} text=${r.current[o].shown.p50} /> (middle)`)}. Decision unit: ${r.decision_unit}.</p>`}
      ${r?.units?.length > 0 &&
      html`<ol class="law-rn-list">${r.units.map(
        (u) => html`<li class="law-rn" data-switch=${u.switch ? "yes" : "no"}>
          <p class="law-rn-head"><strong>${u.names}</strong> <span class="card-sub">${u.county_name}</span></p>
          <dl class="law-rn-facts">
            <div><dt>Unread lots</dt><dd><${Shown} n=${u.unknown_lots} /></dd></div>
            <div><dt>Review effort</dt><dd>${u.effort.text}</dd></div>
            ${u.bounds &&
            html`<div><dt>If permissive</dt><dd><${Shown} n=${u.bounds.permissive.change} /> statewide</dd></div>
              <div><dt>If restrictive</dt><dd><${Shown} n=${u.bounds.restrictive.change} /> statewide</dd></div>`}
          </dl>
          ${u.switch_text && html`<p class=${`law-rn-switch ${u.switch ? "is-switch" : ""}`}>${u.switch_text}</p>`}
          ${u.source_url && html`<p class="card-sub"><a href=${u.source_url} rel="noopener noreferrer" target="_blank">Where the ordinance is</a></p>`}
        </li>`,
      )}</ol>`}
    </div>
  </section>`;
}
