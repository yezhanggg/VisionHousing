/**
 * Three small pieces that show records the server already wrote, word for word:
 *
 *   `BaselineCard`   what the baseline is built from: the historical years, the permit columns
 *                    (the Building Permits Survey's estimated-units columns), the data release
 *                    and its date, the information cutoff, the projected years and the 2026 gap,
 *                    the 2026 nowcast (diagnostic only), Pennsylvania's window sensitivity
 *                    (disclosed, never subtracted) and the state reconciliation ("matched" only
 *                    when the server found the rule's state total equal to the baseline middle
 *                    value). Rows come from `baseline_card.view` (src/visionhousing/api/
 *                    baseline_view.py); each opens to its formula, source ids and assumption ids.
 *                    `compact` folds it (State screen); the Method screen shows it open.
 *   `ScopeNote`      what a reform's headline counts (outcome accounting): for example ADU
 *                    "all work types", with the Census-comparable range beside it.
 *   `HearingButton`  opens the two-page hearing brief of the scenario (GET /export/hearing, or
 *                    the exported file); disabled, with the reason in words, when this build
 *                    cannot give it.
 *
 * Nothing here computes a number: every figure is the server's text.
 */
import "../../css/baseline-card.css";
import { baselineCard, hearingBrief } from "../lib/api.js";
import { numberOf, useJson } from "../lib/data.js";
import { html, useEffect, useState } from "../lib/html.js";
import { Empty, Status } from "./bits.js";
import { Icon } from "./icons.js";

const TAGS = {
  diagnostic: "Diagnostic only",
  sensitivity: "Disclosed sensitivity",
  check: "Check",
};

function Ids({ label, ids }) {
  if (!ids?.length) return null;
  return html`<p class="bc-ids"><span>${label}:</span> ${ids.map((id, i) => html`${i ? ", " : ""}<code>${id}</code>`)}</p>`;
}

function Row({ row }) {
  const more = row.formula || row.sources?.length || row.assumption_ids?.length;
  return html`<div class=${`bc-row is-${row.kind}`} data-row=${row.id}>
    <dt class="bc-label">${row.label}
      ${TAGS[row.kind] && html`<span class=${`bc-tag is-${row.kind}`}>${row.status || TAGS[row.kind]}</span>`}</dt>
    <dd class="bc-text">
      <span data-testid="bc-text">${row.text}</span>
      ${more &&
      html`<details class="bc-more">
        <summary>How it is worked out</summary>
        ${row.formula && html`<p class="bc-formula">${row.formula}</p>`}
        <${Ids} label="Sources" ids=${row.sources} />
        <${Ids} label="Assumptions" ids=${row.assumption_ids} />
      </details>`}
    </dd>
  </div>`;
}

/** The baseline card of a state. */
export function BaselineCard({ state, compact = false, h = 2 }) {
  const reply = useJson(`baseline-card:${state}`, () => baselineCard(state));
  const view = reply.data?.view;
  const recon = view?.rows?.find((r) => r.id === "reconciliation");
  const Heading = `h${h}`;
  const body = html`
    ${(reply.loading || reply.error) && html`<${Status} state=${reply} what="the baseline card" lines=${2} />`}
    ${!reply.loading && !reply.error && !view &&
    html`<${Empty} title="No baseline card for this state">The synthetic test state has no Census permit history, so there is nothing to reconcile.<//>`}
    ${view &&
    html`<dl class="bc-rows">${view.rows.map((r) => html`<${Row} key=${r.id} row=${r} />`)}</dl>
      <p class="bc-source">From <code>${view.source || reply.data.source}</code>; the hearing brief prints the same rows.</p>`}`;
  if (compact) {
    return html`<details class="bc bc-fold" data-testid="baseline-card">
      <summary><span class="bc-sum-title">What the baseline is built from</span>
        ${recon && html`<span class=${`bc-tag is-check is-${recon.status.replace(/\s+/g, "-")}`}>Reconciliation: ${recon.status}</span>`}</summary>
      ${body}
    </details>`;
  }
  return html`<section class="card bc" id="baseline-card" aria-labelledby="baseline-card-title" data-testid="baseline-card">
    <header class="card-head"><div>
      <${Heading} class="card-title" id="baseline-card-title">Baseline card<//>
      <p class="card-sub">What the no-change baseline is built from, the 2026 gap, and the checks on it. None of the diagnostics changes an estimate.</p>
    </div></header>
    <div class="card-body">${body}</div>
  </section>`;
}

/** What one reform's headline counts, with the Census-comparable figure beside it. */
export function ScopeNote({ run, id }) {
  const acc = run?.reform_results?.[id]?.accounting;
  const head = acc?.headline;
  if (!head?.scope) return null;
  const comp = numberOf(run, `reform_results.${id}.accounting.records.comparable`);
  const name = run.reform_results[id].title || id;
  return html`<p class="scope-note" data-testid="scope-note" data-reform=${id} title=${head.text || ""}>
    <span class="scope-key">${name}: headline counts ${head.scope}</span>
    ${head.is_comparable === false && comp?.shown &&
    html`<span class="scope-comp">Census-comparable: <strong>${comp.shown.p10} to ${comp.shown.p90}</strong> (middle ${comp.shown.p50})</span>`}
  </p>`;
}

/** Open the hearing brief of a scenario in a new tab. */
export function HearingButton({ sc, compact = true }) {
  const key = `${sc.state}|${sc.reforms.join(",")}|${JSON.stringify(sc.overrides)}|${sc.preset || "central"}`;
  const [brief, setBrief] = useState({ key: "", url: "", reason: "" });
  useEffect(() => {
    let live = true;
    hearingBrief(sc).then((b) => live && setBrief({ key, ...b }));
    return () => {
      live = false;
    };
  }, [key]);
  const ready = brief.key === key && brief.url;
  const reason = brief.key === key ? brief.reason : "";
  return html`<span class="hearing">
    <a class=${`btn btn-quiet ${compact ? "btn-xs" : ""} ${ready ? "" : "is-disabled"}`} data-testid="hearing-brief"
      href=${ready ? brief.url : undefined} target="_blank" rel="noopener" role=${ready ? undefined : "link"}
      aria-disabled=${ready ? undefined : "true"} aria-describedby=${reason ? "hearing-why" : undefined}>
      <${Icon} name="print" size=${15} /><span>Hearing brief</span></a>
    ${reason && html`<span class="hearing-why" id="hearing-why">${reason}</span>`}
  </span>`;
}
