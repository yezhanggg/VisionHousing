/**
 * ImplementationRows: where each rule that touches this reform and place stands in its life.
 *
 * One row per provision (a state law, a pending bill, a local ordinance), read like
 * "Legally effective · local guidance issued · permit results not yet published". The three
 * steps are pills: the legal status, the local step and the permit step. A pending bill shows
 * as pending and says it does not change the baseline. Each row folds open to show every
 * status with its dates (took effect, verified on) and sources, and any earlier version the
 * rule replaced.
 *
 * Data: GET /api/v1/implementation/ST?r=KEY&geoid=G (src/visionhousing/api/cards.py, from the
 * verified records of assumptions/law/timeline.yaml); the static site reads
 * ST/implementation.json when exported and picks the rows for the reform and place. Every word
 * and date is the API's.
 */
import { reformById } from "../content/reforms.js";
import { implementationRows } from "../lib/api.js";
import { html } from "../lib/html.js";
import { CardFrame, SourceLink, useCard } from "./cards-common.js";

const LEVEL = { state: "State", county: "County", municipality: "Municipality", area: "Mapped area" };

function Pill({ step, kind }) {
  return html`<span class=${`ec-pill is-${step.state} ec-pill-${kind}`}>${step.text}</span>`;
}

function Dates({ r }) {
  const parts = [];
  if (r.start) parts.push(`from ${r.start}`);
  if (r.end) parts.push(`until ${r.end}`);
  if (r.verified_on) parts.push(`verified ${r.verified_on}`);
  return parts.length ? html`<span class="ec-dates">${parts.join(" · ")}</span>` : null;
}

function StepDetail({ label, step }) {
  const r = step.record;
  return html`<li class="ec-step">
    <span class="ec-step-label">${label}:</span> ${step.text}
    ${r &&
    html`<div class="ec-step-rec">
      <span class="ec-step-cite">${r.citation}</span> <${Dates} r=${r} />
      ${r.sources.length > 0 && html`<span class="ec-srcs">${r.sources.map((s) => html`<${SourceLink} href=${s} />`)}</span>`}
      ${r.note && html`<p class="ec-quiet">${r.note}</p>`}
    </div>`}
    ${!r && html`<span class="ec-quiet"> (no record)</span>`}
  </li>`;
}

function Row({ row, i }) {
  return html`<li class=${`ec-impl-row ${row.pending ? "is-pending" : ""}`} data-provision=${row.provision_id}>
    <div class="ec-impl-head">
      <span class="ec-impl-cite">${row.citation}</span>
      <span class="ec-level">${LEVEL[row.level] || row.level}</span>
    </div>
    <p class="ec-impl-line">
      <${Pill} step=${row.legal} kind="legal" />
      ${!row.pending && row.local.state !== "not_yet" && html`<span class="ec-sep" aria-hidden="true">·</span><${Pill} step=${row.local} kind="local" />`}
      ${!row.pending && row.permits.state !== "not_yet" && html`<span class="ec-sep" aria-hidden="true">·</span><${Pill} step=${row.permits} kind="permits" />`}
    </p>
    <details class="ec-impl-more">
      <summary><span class="about-i" aria-hidden="true">i</span>Sources and dates<span class="visually-hidden"> for ${row.citation}</span></summary>
      <ul class="ec-steps">
        <${StepDetail} label="Law" step=${row.legal} />
        <${StepDetail} label="Local step" step=${row.local} />
        <${StepDetail} label="Permits" step=${row.permits} />
      </ul>
      ${row.scope && html`<p class="ec-quiet">Scope: ${row.scope}</p>`}
      ${row.history.length > 0 &&
      html`<p class="ec-sub-title">Earlier versions</p>
        <ul class="ec-steps">${row.history.map((r) => html`<li class="ec-step"><span class="ec-step-cite">${r.citation}</span> <${Dates} r=${r} /></li>`)}</ul>`}
    </details>
  </li>`;
}

/**
 * The rows. `state` is the two-letter code, `geoid` a county (5 digits) or municipality (10)
 * code, or empty for the whole state; `reform` the reform key; `h` the heading level. The
 * title names the reform ("Is the rule in force? Accessory dwelling units").
 */
export function ImplementationRows({ state, geoid = "", reform, h = 2 }) {
  const st = useCard(() => implementationRows(state, reform, geoid), [state, reform, geoid]);
  const d = st.data;
  const rows = d?.rows || [];
  const pending = rows.some((r) => r.pending);
  const line = d ? (rows.length ? `Where each rule stands, as of ${d.on}.` : d.none_text) : "";
  // The reform's name is in the title, so two of these cards on one screen (Place shows one
  // per reform) never share a heading.
  const name = reformById(reform)?.name || reform;
  return html`<${CardFrame} id=${`implementation-${reform}`} testid="implementation" title=${`Is the rule in force? ${name}`} h=${h}
      state=${st} what="the implementation status" className="ec-impl" line=${line}>
    ${rows.length > 0 &&
    html`<ul class="ec-impl-list">${rows.map((row, i) => html`<${Row} row=${row} i=${i} />`)}</ul>
      ${pending && html`<p class="ec-quiet ec-pending-note">${d.pending_note}</p>`}
      <p class="ec-quiet">Source: <code>${d.source}</code></p>`}
  <//>`;
}
