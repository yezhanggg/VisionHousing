/**
 * ConfidencePanel: how sure to be, as two signals side by side that are never merged.
 *
 *   Baseline performance   how often the baseline's past ranges caught what was really
 *                          permitted (the hold-out back-test of the donor-pool study)
 *   Reform evidence        how much outside evidence exists for each reform type: its grade,
 *                          how many records, how many verified, how many with an observed
 *                          result, how many in this state
 *
 * A state can have a well-tested baseline and little evidence for a reform, or the reverse,
 * so the two are never added into one score. Data: GET /api/v1/confidence/ST (built by
 * src/visionhousing/api/cards.py from validation.donor_pool.confidence_signals); the static
 * site reads ST/confidence.json, and says "needs the local app" when it is not there.
 * Every value and word is the API's; this file only picks a tone for each grade word.
 */
import { stateName } from "../content/states.js";
import { confidenceSignals } from "../lib/api.js";
import { html, useId } from "../lib/html.js";
import { About } from "./charts.js";
import { CardFrame, GradeChip, useCard } from "./cards-common.js";

/** Tone (and ladder step, 0–3) of each grade word the API sends; an unknown word gets none. */
const BASELINE_TONE = { "back-tested": ["strong", 3], borrowed: ["mid", 2], limited: ["weak", 1] };
const EVIDENCE_TONE = {
  "tested elsewhere": ["strong", 3],
  "observed elsewhere": ["mid", 2],
  "law on record": ["weak", 1],
  "no records": ["none", 0],
};

/** Three pips, filled up to the grade's step: the panel's one visual per row. */
function Pips({ step, label }) {
  return html`<span class="ec-pips" role="img" aria-label=${label}>
    ${[1, 2, 3].map((i) => html`<span class=${`ec-pip ${i <= step ? "is-on" : ""}`} aria-hidden="true"></span>`)}
  </span>`;
}

function count(n, one, many = `${one}s`) {
  return `${n ?? "—"} ${n === 1 ? one : many}`;
}

/**
 * The panel. `state` is the two-letter code; `reform` (optional) highlights that reform's row;
 * `h` is the heading level (2 by default); the two signals' headings are one level lower.
 */
export function ConfidencePanel({ state, reform = "", h = 2 }) {
  const st = useCard(() => confidenceSignals(state), [state]);
  const uid = useId(); // heading ids unique per card instance
  // The two signals' headings sit one level below the card's own title.
  const Sub = `h${Math.min(6, Math.max(2, h) + 1)}`;
  const d = st.data;
  const bp = d?.baseline_performance || {};
  const byType = d?.reform_evidence?.by_reform_type || {};
  const labels = d?.type_labels || {};
  const bench = (d?.benchmark_types || []).filter((t) => byType[t]);
  const others = Object.keys(byType).filter((t) => !bench.includes(t));
  const [bTone, bStep] = BASELINE_TONE[bp.grade] || ["", 0];
  const row = (t) => {
    const e = byType[t];
    const [tone, step] = EVIDENCE_TONE[e.grade] || ["", 0];
    const counts = `${count(e.verified, "verified record")} of ${e.records} · ${e.with_observed_result} with an observed result · ${e.in_state} in this state`;
    return html`<li class=${`ec-ev-row ${t === reform ? "is-hot" : ""}`} data-type=${t}>
      <span class="ec-ev-name">${labels[t] || t}</span>
      <${Pips} step=${step} label=${`${labels[t] || t}: ${e.grade}. ${counts}.`} />
      <${GradeChip} text=${e.grade} tone=${tone} />
      <span class="ec-ev-counts">${counts}</span>
    </li>`;
  };
  return html`<${CardFrame} id="confidence" title="How sure: two separate signals" h=${h} state=${st}
      what="the confidence signals" className="ec-confidence"
      line=${d ? `${stateName(state)}: how the baseline has done, and how much evidence each reform has. One never raises or lowers the other.` : ""}>
    ${d &&
    html`<div class="ec-two">
        <section class="ec-signal" aria-labelledby=${`${uid}-base`}>
          <${Sub} class="ec-signal-title" id=${`${uid}-base`}>Baseline performance<//>
          <p class="ec-signal-grade">
            <${Pips} step=${bStep} label=${`Baseline performance: ${bp.grade || "not graded"}`} />
            <${GradeChip} text=${bp.grade} tone=${bTone} />
          </p>
          <p class="ec-signal-text">${bp.sentence}</p>
        </section>
        <section class="ec-signal" aria-labelledby=${`${uid}-ev`}>
          <${Sub} class="ec-signal-title" id=${`${uid}-ev`}>Reform evidence<//>
          <ul class="ec-ev-list">${bench.map(row)}</ul>
        </section>
      </div>
      <${About} label="About these signals">
        <p>${d.note}</p>
        <dl class="ec-dl">
          <dt>Baseline error record</dt><dd>${bp.error_source || "—"}${bp.method ? ` (method ${bp.method})` : ""}</dd>
          <dt>County cases</dt><dd>${bp.county_cases ?? "—"}</dd>
          <dt>Hold-out coverage</dt><dd>${bp.coverage_text ? `${bp.coverage_text} of ${bp.held_out_cases} start years` : "not measured"}</dd>
          <dt>Sources</dt><dd><code>${bp.source}</code>, <code>${d.reform_evidence?.source}</code></dd>
        </dl>
        ${others.length > 0 &&
        html`<p class="ec-sub-title">Other reform types</p><ul class="ec-ev-list is-small">${others.map(row)}</ul>`}
      <//>`}
  <//>`;
}
