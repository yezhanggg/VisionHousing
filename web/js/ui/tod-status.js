/**
 * The transit status ladder on screen: what is known about transit service, and when the
 * statewide transit-oriented density (TOD) range may be shown at all.
 *
 * A run reply carries `status_views` (src/visionhousing/api/status_view.py): for each reform
 * with a transit status, whether its statewide range is shown, the completeness label, the
 * measured contribution, the package note and the ladder (state and every county), all as
 * the server's words. This module only lays them out:
 *
 *   statusView(run, key) / hiddenView(run, key)  the view, or the view only when hidden
 *   HiddenRange   in place of a range: "Statewide range not shown", the measured
 *                 contribution and the completeness label
 *   PartialLabel  next to a range that is shown but partial ("Partial footprint: …")
 *   PackageNotes  under a package total: "counts TOD by its measured contribution only"
 *   Ladder        one row of the ladder (discovered / legal / coverage / headway /
 *                 feasible); "unknown" (not measured) and "measured: none" look different
 *                 (dashed and hatched vs solid), and each carries its word
 *   StatusTable   the state and every county as a small table (the Reform screen)
 *
 * Nothing here works out a number.
 */
import "../../css/tod-status.css";
import { html } from "../lib/html.js";

/** The status view of one reform in a run reply, or null. */
export function statusView(run, key) {
  return run?.status_views?.[key] || null;
}

/** The view only when the statewide range is hidden, else null. */
export function hiddenView(run, key) {
  const v = statusView(run, key);
  return v && v.shown === false ? v : null;
}

/** The view only when the range is shown but the footprint is partial, else null. */
export function partialView(run, key) {
  const v = statusView(run, key);
  return v && v.shown !== false && v.partial ? v : null;
}

const MARK = { yes: "✓", partial: "◐", no: "✕", unknown: "?", measured_zero: "∅", not_reached: "–" };

function Chip({ cell, compact = false }) {
  // Compact (the county table): the mark shows, the word is read out and shown on hover.
  return html`<span class=${`tod-chip is-${cell.kind} ${compact ? "is-compact" : ""}`} data-kind=${cell.kind}
      title=${compact ? cell.word : undefined}>
    <span class="tod-mark" aria-hidden="true">${MARK[cell.kind] || "?"}</span><span class=${compact ? "visually-hidden" : ""}>${cell.word}</span></span>`;
}

/** In place of a range: the hidden word, the measured contribution and the label. */
export function HiddenRange({ view, testid = "tod-hidden" }) {
  if (!view) return null;
  return html`<div class="tod-hidden" role="note" data-testid=${testid}>
    <p class="tod-hidden-word">${view.hidden_word}</p>
    ${view.measured && html`<p class="tod-measured" data-testid="tod-measured">${view.measured.text}</p>`}
    <p class="tod-label" data-testid="tod-label">${view.label}</p>
    ${view.measured && html`<p class="card-sub">${view.measured.help} ${view.rule}</p>`}
  </div>`;
}

/** Next to a range that is shown but partial. */
export function PartialLabel({ view, named = false }) {
  if (!view || view.shown === false || !view.partial) return null;
  return html`<p class="tod-partial" data-testid="tod-partial-label" role="note">
    ${named && html`<strong>${view.title}:</strong> `}${view.label}</p>`;
}

/** Under a package total: the server's note for each reform counted by its measured part only. */
export function PackageNotes({ run }) {
  const notes = Object.values(run?.status_views || {}).filter((v) => v.package_note);
  if (!notes.length) return null;
  return html`${notes.map(
    (v) => html`<p class="tod-package-note" role="note" data-testid="tod-package-note">${v.package_note}</p>`,
  )}`;
}

function Legend({ view }) {
  return html`<details class="tod-legend">
    <summary>What the answers mean</summary>
    <ul>${view.legend.map((l) => html`<li><${Chip} cell=${l} /> ${l.meaning}</li>`)}</ul>
  </details>`;
}

/** One row of the ladder (the state, or one county), with its completeness label. */
export function Ladder({ view, row, title }) {
  if (!view || !row) return null;
  const label = Object.fromEntries(view.stages.map((s) => [s.key, s]));
  return html`<div class="tod-ladder" data-testid="tod-ladder" data-geoid=${row.geoid}>
    <p class="field-label">${title}</p>
    <p class="card-sub tod-completeness">${row.completeness}</p>
    <ol class="tod-steps">
      ${row.cells.map(
        (c) => html`<li class="tod-step" data-stage=${c.stage} data-kind=${c.kind}>
          <span class="tod-stage" title=${label[c.stage]?.question}>${label[c.stage]?.label || c.stage}</span>
          <${Chip} cell=${c} />
        </li>`,
      )}
    </ol>
    <${Legend} view=${view} />
  </div>`;
}

/**
 * The status on the Reform screen: the state's ladder, then every county as a compact table
 * (marks with their words read out and on hover; the legend says what each means).
 * `names`: county id -> name.
 */
export function StatusTable({ view, names = {} }) {
  if (!view) return null;
  const rows = Object.values(view.counties || {});
  return html`<section class="card tod-status" aria-labelledby="tod-status-title" data-testid="tod-status-table">
    <header class="card-head"><div>
      <h2 class="card-title" id="tod-status-title">Transit status</h2>
      <p class="card-sub">${view.rule}</p>
    </div></header>
    <div class="card-body">
      <${PartialLabel} view=${view} />
      <${Ladder} view=${view} row=${view.state} title="Statewide" />
      <details class="tod-counties">
        <summary>Every county (${rows.length})</summary>
        <div class="table-wrap">
          <table class="table tod-table">
            <caption class="visually-hidden">Transit status ladder by county</caption>
            <thead><tr><th scope="col">County</th>${view.stages.map(
              (s) => html`<th scope="col" title=${s.question}>${s.label}</th>`,
            )}</tr></thead>
            <tbody>${rows.map(
              (r) => html`<tr data-geoid=${r.geoid}>
                <th scope="row">${names[r.geoid] || r.geoid}<span class="tod-foot">${r.completeness}</span></th>
                ${r.cells.map((c) => html`<td data-stage=${c.stage}><${Chip} cell=${c} compact=${true} /></td>`)}
              </tr>`,
            )}</tbody>
          </table>
        </div>
      </details>
    </div>
  </section>`;
}
