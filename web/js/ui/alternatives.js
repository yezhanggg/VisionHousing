/**
 * "Alternatives" on a benchmark reform's headline: one button that opens a compact table of
 * every named alternative the tool has built for the reform, beside the one headline.
 *
 *   Button   "Alternatives" under the headline numbers (benchmark reforms only). Nothing else on
 *            the screen changes; the table opens in a modal side sheet.
 *   Sheet    the benchmark's middle and likely range with its scope, then one row per
 *            alternative: its added homes (middle, likely range), the paired change from the
 *            benchmark (alternative minus benchmark, draw by draw, then the middle and range)
 *            with the share of draws below / above it, a one-line reason and the status
 *            (built / not built with why / needs the local app). The formula line closes it.
 *
 * Data: GET /api/v1/states/ST/alternatives?r=KEY (src/visionhousing/api/alternatives.py). The
 * first ask after a change can take a few minutes on a large state (the server answers 504 and
 * keeps working; this asks again). Static site: ST/alternatives.json when ST/index.json says
 * `alternatives: true`, else the "needs the local app" note. Every number shown is the server's
 * text; the browser does no arithmetic and only places the words.
 *
 * Accessibility: a native <button> opens a native <dialog> (showModal: focus moves into it,
 * Escape closes it, focus returns to the button); the rows are a real <table> with a caption,
 * column and row headers; the loading line is a polite status. Motion stops with reduced motion.
 */
import "../../css/alternatives.css";
import { API_BASE, DATA_BASE, IS_STATIC, LocalOnlyError } from "../lib/api.js";
import { useJson } from "../lib/data.js";
import { html, useEffect, useId, useRef, useState } from "../lib/html.js";

const BENCHMARKS = ["adu", "missing_middle", "tod", "parking", "fees"];
export const ALTERNATIVES_LOCAL_ONLY = "The alternatives table needs the local app (make app); this site does not hold it yet.";
const RETRY_MS = 15000;
const RETRIES = 12;

/** One reform's table: `{headline, rows, summary, formula, statuses}` as the server wrote it. */
export async function alternativesFor(st, reform) {
  const s = String(st).toUpperCase();
  if (IS_STATIC) {
    const idx = await fetch(`${DATA_BASE}/${s}/index.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (!idx?.alternatives) throw new LocalOnlyError(ALTERNATIVES_LOCAL_ONLY);
    const reply = await fetch(`${DATA_BASE}/${s}/alternatives.json`);
    if (!reply.ok) throw new LocalOnlyError(ALTERNATIVES_LOCAL_ONLY);
    const body = await reply.json();
    const part = body.reforms?.[reform];
    if (!part) throw new LocalOnlyError(ALTERNATIVES_LOCAL_ONLY);
    const shared = { formula: body.formula, statuses: body.statuses, state: body.state, draws: body.draws, seed: body.seed };
    return { ...shared, ...part };
  }
  const url = `${API_BASE}/states/${s}/alternatives?r=${encodeURIComponent(reform)}`;
  for (let attempt = 0; ; attempt += 1) {
    const reply = await fetch(url, { headers: { accept: "application/json" } });
    const body = await reply.json().catch(() => null);
    if (reply.ok) return body;
    if (reply.status !== 504 || attempt >= RETRIES) throw new Error(body?.message || `the request failed (${reply.status})`);
    await new Promise((ok) => setTimeout(ok, RETRY_MS));
  }
}

function Row({ r, statuses }) {
  const status = statuses?.[r.status] || r.status;
  const absent = r.status !== "built";
  return html`<tr class=${`alt-row ${absent ? "is-absent" : ""}`} data-status=${r.status} data-id=${r.id}>
    <th scope="row"><span class="alt-title">${r.title}</span>
      <span class="alt-reason">${absent ? r.absent || r.reason : r.reason}</span>
      ${r.bounds && html`<span class="alt-reason">${r.bounds.text}</span>`}
      <span class=${`alt-status ${absent ? "is-absent" : ""}`}>${status}</span></th>
    ${absent
      ? html`<td class="alt-num" colspan="2"><span class="alt-dash">Not computed</span></td>`
      : html`<td class="alt-num"><strong>${r.added.mid}</strong><span class="alt-range">${r.added.range}</span></td>
        <td class="alt-num"><strong>${r.diff.mid}</strong><span class="alt-range">${r.diff.range}</span>
          <span class="alt-range">below in ${r.below.text} · above in ${r.above.text} of draws</span></td>`}
  </tr>`;
}

function Sheet({ st, reform, title, onClose, dialog }) {
  const data = useJson(`alternatives:${st}:${reform}`, () => alternativesFor(st, reform), st);
  const hid = useId();
  const v = data.data;
  return html`<dialog class="alt-sheet" ref=${dialog} aria-labelledby=${hid} onClose=${onClose}
      onClick=${(e) => e.target === dialog.current && dialog.current.close()}>
    <div class="alt-inner">
      <header class="alt-head">
        <h2 id=${hid} class="alt-heading">Alternatives at a glance: ${title}</h2>
        <button type="button" class="btn btn-quiet btn-xs" onClick=${() => dialog.current?.close()}>Close</button>
      </header>
      ${data.loading && html`<p class="status-line is-loading" role="status">Working out every alternative on the same draws… the first time this can take a few minutes.</p>`}
      ${data.error && html`<p class=${`status-line ${data.localOnly ? "is-local" : "is-error"}`} role="note">
        ${data.localOnly ? data.error : `The alternatives did not load: ${data.error}`}</p>`}
      ${v && html`<p class="alt-bench" data-testid="alt-benchmark">
          <span class="alt-bench-label">Benchmark</span>
          ${v.headline.hidden
            ? html`<strong>Measured contribution only: middle ${v.headline.mid}</strong>`
            : html`<strong>${v.headline.mid}</strong> <span class="alt-range">likely ${v.headline.range}</span>`}
          <span class="alt-scope">${v.headline.what}${v.headline.scope ? `; counts ${v.headline.scope}` : ""}</span>
        </p>
        <div class="alt-scroll" tabindex="0" role="region" aria-label="Alternatives table">
          <table class="alt-table" data-testid="alt-table">
            <caption class="alt-caption">${v.summary.count_text}. ${v.summary.widest_text}.</caption>
            <thead><tr>
              <th scope="col">Alternative and why</th>
              <th scope="col" class="alt-num">Homes added, middle (likely range)</th>
              <th scope="col" class="alt-num">Change from benchmark, paired draw by draw</th>
            </tr></thead>
            <tbody>${v.rows.map((r) => html`<${Row} key=${r.id} r=${r} statuses=${v.statuses} />`)}</tbody>
          </table>
        </div>
        <p class="alt-formula">${v.formula} (${v.rule_id || "alternatives.settings"}).</p>`}
    </div>
  </dialog>`;
}

/** The headline's one "Alternatives" button and its sheet; nothing for a non-benchmark reform. */
export function AlternativesButton({ state, reform, title }) {
  const [open, setOpen] = useState(false);
  const button = useRef(null);
  const dialog = useRef(null);
  useEffect(() => {
    if (open && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [open]);
  if (!BENCHMARKS.includes(reform)) return null;
  const close = () => {
    setOpen(false);
    button.current?.focus();
  };
  return html`<p class="alt-open">
    <button type="button" ref=${button} class="btn btn-quiet btn-xs" data-testid="alternatives-button"
      aria-haspopup="dialog" aria-expanded=${open ? "true" : "false"} onClick=${() => setOpen(true)}>Alternatives</button>
    ${open && html`<${Sheet} st=${state} reform=${reform} title=${title} onClose=${close} dialog=${dialog} />`}
  </p>`;
}
