/**
 * LeverCard: the counterfactual lever for one reform, in a state or one county.
 *
 *   Headline   the binding factor (the funnel stage that loses the largest share, or the one
 *              lever of fees and parking) and who can act on it
 *   Visual     each named variant's effect as a range around zero: the paired difference,
 *              draw by draw, between the variant run and the benchmark run (same seed and
 *              draws), P10 to P90 with a mark at P50; the county's when a county is given
 *   Then       the evidence grade (A–D) and one next check that would narrow the range
 *   (i)        the binding stage's loss and formula, every actor's loss, the rule, the
 *              variants' own ranges and the grade's sentence
 *
 * Data: GET /api/v1/lever/ST?r=KEY&county=FIPS (src/visionhousing/api/cards.py; it runs the
 * benchmark and the variants named in assumptions/cards.yaml through the cached runner, so the
 * first ask is slow). The static site reads ST/lever/KEY.json when exported; otherwise the card
 * says it needs the local app. Every value and text is the API's; bars only place them.
 */
import { leverCard } from "../lib/api.js";
import { html } from "../lib/html.js";
import { About } from "./charts.js";
import { CardFrame, GradeChip, useCard } from "./cards-common.js";

const GRADE_TONE = { A: "strong", B: "mid", C: "weak", D: "none" };

/** Variants with no appropriation limit (labelled as such on the card). */
const UNCAPPED = ["adu_financing_access", "adu_both"];

/** One effect range around zero, on the card's shared scale [lo, hi]. */
function EffectRow({ e, title, lo, hi }) {
  const span = hi - lo || 1;
  const pct = (v) => Math.max(0, Math.min(100, (100 * (v - lo)) / span));
  const sh = e.shown || {};
  const words = `${title}: ${sh.p10} to ${sh.p90}, middle ${sh.p50}, compared with the benchmark`;
  return html`<li class="ec-eff">
    <span class="ec-eff-title">${title}</span>
    <span class="ec-eff-bar" role="img" aria-label=${words}>
      <span class="ec-eff-track"></span>
      <span class="ec-eff-zero" style=${`--x: ${pct(0).toFixed(2)}`}></span>
      <span class=${`ec-eff-band is-${e.direction}`} style=${`--l: ${pct(Math.min(e.p10, e.p90)).toFixed(2)}; --w: ${Math.max(1, pct(Math.max(e.p10, e.p90)) - pct(Math.min(e.p10, e.p90))).toFixed(2)}`}></span>
      <span class="ec-eff-mid" style=${`--x: ${pct(e.p50).toFixed(2)}`}></span>
    </span>
    <span class="ec-eff-text" aria-hidden="true"><strong>${sh.p50}</strong> <small>(${sh.p10} to ${sh.p90})</small></span>
  </li>`;
}

/**
 * The card. `state` is the two-letter code, `reform` the reform key, `county` an optional
 * 5-digit county code; `h` the heading level.
 */
export function LeverCard({ state, county = "", reform, h = 2 }) {
  const st = useCard(() => leverCard(state, reform, county), [state, reform, county]);
  const d = st.data;
  const b = d?.binding;
  const where = d?.county ? d.county.name || d.county.geoid : "";
  // The original financing variant has no appropriation limit; its title says so, so it is never
  // read as the result of a budget-capped program (the capped ones are adu_finance_budget*).
  const effects = (d?.effects || []).map((x) => ({
    ...x,
    title: UNCAPPED.includes(x.variant) ? `${x.title} (old uncapped variant: no appropriation limit)` : x.title,
    use: where && x.county_effect ? x.county_effect : x.effect,
  }));
  const ends = effects.flatMap((x) => [x.use.p10, x.use.p90]).filter(Number.isFinite);
  const lo = Math.min(0, ...ends);
  const hi = Math.max(0, ...ends);
  const line = d
    ? b
      ? `Binding factor: ${b.name}. Who can act: ${d.who.word.toLowerCase()} (${d.who.who}).`
      : "No binding factor is defined for this reform's steps."
    : "";
  return html`<${CardFrame} id=${`lever-${reform}`} testid="lever" title=${`What would move it${where ? ` in ${where}` : ""}`} h=${h}
      state=${st} what="the lever card" className="ec-lever" line=${line}>
    ${d &&
    html`${effects.length > 0 &&
      html`<p class="ec-sub-title">Effect of a change, against the benchmark${where ? ` (${where})` : ""}</p>
        <ul class="ec-eff-list">${effects.map((x) => html`<${EffectRow} e=${x.use} title=${x.title} lo=${lo} hi=${hi} />`)}</ul>`}
      <p class="ec-row">
        <span class="ec-label">Evidence</span>
        ${d.grade ? html`<${GradeChip} text=${`Grade ${d.grade.grade}`} tone=${GRADE_TONE[d.grade.grade] || ""} label=${d.grade.sentence} />` : html`<span class="ec-quiet">not graded</span>`}
      </p>
      ${d.verify && html`<p class="ec-next"><strong>Check next:</strong> ${d.verify}</p>`}
      <${About} label="How this card is built">
        ${b && html`<p>${b.sentence}</p>`}
        ${b?.formula && html`<p><code>${b.formula}</code></p>`}
        ${d.by_actor?.length > 0 &&
        html`<ul class="ec-actor-list">${d.by_actor.map((g) => html`<li><strong>${g.word}</strong> (${g.who})${g.lost_text ? `: up to ${g.lost_text} lost at ${g.stages.join(", ")}` : `: ${g.stages.join(", ")}`}</li>`)}</ul>`}
        <p>Benchmark: ${d.added?.shown?.p10} to ${d.added?.shown?.p90}, middle ${d.added?.shown?.p50}${d.county?.added ? `; ${where}: ${d.county.added.shown.p10} to ${d.county.added.shown.p90}, middle ${d.county.added.shown.p50}` : ""}.</p>
        ${effects.map((x) => html`<p><strong>${x.title}.</strong> ${x.question} Variant alone: ${x.added.shown.p10} to ${x.added.shown.p90}, middle ${x.added.shown.p50}. <code>${x.use.formula}</code></p>`)}
        <p class="ec-quiet">${d.rule} (${d.rule_id}; ${d.scenario?.draws} draws, seed ${d.scenario?.seed})</p>
      <//>`}
  <//>`;
}
