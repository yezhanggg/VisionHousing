/**
 * PlausibilityCard: is our rate believable next to what has really been seen elsewhere?
 *
 * One strip per measure (homes per 1,000 eligible lots per year, or per 100,000 residents per
 * year): our likely range (P10 to P90, a mark at P50) as a bar, the observed band as a shaded
 * span, and each observed rate as a point. Pointing at or focusing a point shows its place,
 * period, how it was identified and its caveat. Above the strip sits the rule-written sentence
 * ("Our likely range ... sits below all of the observed rates ... Why: ..."). When no observed
 * rate exists in a unit, the card says "No observed rate in this unit yet".
 *
 * Data: GET /api/v1/plausibility/ST?r=KEY (src/visionhousing/validation/plausibility.py, via
 * api/intake.py); the static site reads ST/plausibility.json when the export has it. Every
 * number is the API's: the strip only places values between the API's axis ends (`m.axis`, a
 * log scale when the API says so), and every label is the API's own text (`shown`); the browser
 * rounds nothing.
 */
import { plausibilityCard } from "../lib/api.js";
import { html, useId, useState } from "../lib/html.js";
import { About } from "./charts.js";
import { CardFrame, useCard } from "./cards-common.js";

/** A placement function from value to percent of the strip's width, from the API's axis ends. */
function scaleFor(axis) {
  const { lo, hi, log } = axis || {};
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { at: () => 0, log: false, shown: {} };
  const shown = axis.shown || {};
  if (log) {
    const a = Math.log(lo);
    const b = Math.log(hi);
    return { at: (v) => (b > a ? (100 * (Math.log(Math.max(lo, v)) - a)) / (b - a) : 50), log, shown };
  }
  return { at: (v) => (hi > lo ? (100 * (v - lo)) / (hi - lo) : 50), log: false, shown };
}

function Strip({ m, unitLabel }) {
  const [hot, setHot] = useState(-1);
  const tipId = `${useId()}-tip`; // unique per strip, even with two cards on one screen
  const model = m.model || {};
  const band = m.band || {};
  const obs = (m.observed || []).filter((o) => Number.isFinite(o.value));
  const hasModel = Number.isFinite(model.p10) && Number.isFinite(model.p90);
  const hasBand = Number.isFinite(band.low) && Number.isFinite(band.high);
  const sc = scaleFor(m.axis);
  const ms = model.shown || {};
  const bs = band.shown || {};
  const pct = (v) => Math.max(0, Math.min(100, sc.at(v)));
  const on = hot >= 0 ? obs[hot] : null;
  // Nothing to place: no observed rate and our range is zero throughout (the sentence says so).
  const nothing = !obs.length && !hasBand && (!hasModel || (model.p10 === 0 && model.p90 === 0));
  if (nothing) return html`<p class="ec-tip ec-quiet">No observed rate in this unit yet.</p>`;
  const summary = [
    hasModel ? `Our likely range ${ms.p10} to ${ms.p90} ${unitLabel}, middle ${ms.p50}` : "Our range is not available",
    obs.length
      ? `${obs.length} observed rates${hasBand ? ` from ${bs.low} to ${bs.high}` : ""}`
      : "no observed rate in this unit yet",
  ].join("; ");
  return html`<div class="ec-strip-wrap">
    <div class="ec-strip" role="group" aria-label=${`${m.label}: ${summary}.`}>
      <div class="ec-strip-draw" role="img" aria-label=${summary}>
        <span class="ec-strip-track"></span>
        ${hasBand && html`<span class="ec-strip-band" style=${`--l: ${pct(band.low).toFixed(2)}; --w: ${Math.max(0.8, pct(band.high) - pct(band.low)).toFixed(2)}`}></span>`}
        ${hasModel &&
        html`<span class="ec-strip-model" style=${`--l: ${pct(model.p10).toFixed(2)}; --w: ${Math.max(1, pct(model.p90) - pct(model.p10)).toFixed(2)}`}></span>
          ${Number.isFinite(model.p50) && html`<span class="ec-strip-mid" style=${`--x: ${pct(model.p50).toFixed(2)}`}></span>`}`}
      </div>
      ${obs.map(
        (o, i) => html`<button type="button" class=${`ec-dot ${hot === i ? "is-hot" : ""}`}
            style=${`--x: ${pct(o.value).toFixed(2)}; --row: ${i % 3}`}
            aria-label=${`${o.place}, ${o.period}: ${o.shown} ${unitLabel}. ${o.identification}.`}
            aria-describedby=${hot === i ? tipId : undefined}
            onMouseEnter=${() => setHot(i)} onFocus=${() => setHot(i)}
            onMouseLeave=${() => setHot(-1)} onBlur=${() => setHot(-1)}></button>`,
      )}
    </div>
    <div class="ec-strip-axis" aria-hidden="true">
      <span>${sc.shown.lo ?? ""}</span>
      <span class="ec-strip-key"><i class="ec-key-model"></i>Our likely range <i class="ec-key-band"></i>Observed band <i class="ec-key-dot"></i>Observed rate</span>
      <span>${sc.shown.hi ?? ""}${sc.log ? " (log scale)" : ""}</span>
    </div>
    <div class="ec-tip" id=${tipId} role="status" aria-live="polite">
      ${on
        ? html`<p><strong>${on.place}</strong> · ${on.period} · ${on.shown} ${unitLabel} · <em>${on.identification}</em></p>
            ${on.what_changed && html`<p>${on.what_changed}</p>`}
            <p class="ec-caveat">${on.caveat}</p>`
        : obs.length
          ? html`<p class="ec-quiet">Point at or tab to an observed rate to see its place and caveat.</p>`
          : html`<p class="ec-quiet">No observed rate in this unit yet.</p>`}
    </div>
  </div>`;
}

/**
 * The card. `state` is the two-letter code, `reform` the reform key; `h` the heading level.
 */
export function PlausibilityCard({ state, reform, h = 2 }) {
  const st = useCard(() => plausibilityCard(state, reform), [state, reform]);
  const [pick, setPick] = useState(0);
  const r = st.data?.reforms?.[reform];
  const measures = r?.measures || [];
  const m = measures[Math.min(pick, Math.max(0, measures.length - 1))];
  const na = r && r.state === "not_applicable";
  const line = na ? r.note : m?.sentence || (st.data && !r ? "No plausibility check for this reform." : "");
  return html`<${CardFrame} id=${`plausibility-${reform}`} testid="plausibility" title="Is this rate plausible?" h=${h}
      state=${st} what="the plausibility check" className="ec-plausibility" line=${line}>
    ${m &&
    !na &&
    html`${measures.length > 1 &&
      html`<div class="ec-seg" role="group" aria-label="Measure">
        ${measures.map((x, i) => html`<button type="button" class="ec-seg-btn" aria-pressed=${i === pick ? "true" : "false"} onClick=${() => setPick(i)}>${x.label}</button>`)}
      </div>`}
      <${Strip} m=${m} unitLabel=${m.label} />
      <${About} label="How this check works">
        ${m.model?.formula && html`<p><strong>Our rate:</strong> ${m.model.formula}</p>`}
        ${measures.filter((x) => x !== m).map((x) => html`<p><strong>${x.label}:</strong> ${x.sentence}</p>`)}
        ${(m.observed || []).length > 0 &&
        html`<table class="table ec-table">
          <caption class="visually-hidden">Observed rates, ${m.label}</caption>
          <thead><tr><th scope="col">Place</th><th scope="col">Period</th><th scope="col" class="num">Rate</th><th scope="col">Design</th></tr></thead>
          <tbody>${m.observed.map((o) => html`<tr><th scope="row">${o.place}</th><td>${o.period}</td><td class="num">${o.shown}</td><td>${o.identification}</td></tr>`)}</tbody>
        </table>`}
        <p class="ec-quiet">Settings ${st.data.settings_id}; ${st.data.note || ""}</p>
      <//>`}
  <//>`;
}
