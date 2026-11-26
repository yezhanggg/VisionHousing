/**
 * CycleChoice: the baseline housing-cycle preset (low / central / high) as three buttons.
 *
 * The preset picks the baseline's housing cycle on the back end (weak, typical or strong;
 * src/visionhousing/api/models.py PRESET_CYCLE). It is part of the scenario, so it is written
 * into the share link (`preset=low` or `preset=high`; central is the default and not written)
 * and a copied link reopens the same cycle (js/state/codec.js). The static site holds only the
 * typical cycle: another choice shows the "custom scenario, not on this site" note instead of a
 * number.
 */
import { IS_STATIC, LOCAL_ONLY } from "../lib/api.js";
import { html } from "../lib/html.js";
import { scenario, updateScenario } from "../state/store.js";

const CHOICES = [
  ["low", "Low", "weak housing cycle"],
  ["central", "Central", "typical housing cycle (default)"],
  ["high", "High", "strong housing cycle"],
];

export function CycleChoice() {
  const now = scenario.value.preset || "central";
  // The static site holds the typical cycle only: no buttons, just the note when a link asks
  // for another cycle.
  if (IS_STATIC) {
    return now === "central"
      ? null
      : html`<p class="static-inline" role="note" data-testid="cycle-choice">Housing cycle "${now}" in this link: ${LOCAL_ONLY}</p>`;
  }
  return html`<div class="cycle-choice" data-testid="cycle-choice">
    <p class="field-label" id="cycle-label">Baseline housing cycle</p>
    <div class="choice" role="group" aria-labelledby="cycle-label">
      ${CHOICES.map(
        ([v, word, what]) => html`<button type="button" class="seg" data-preset=${v} aria-pressed=${now === v ? "true" : "false"}
          title=${what} onClick=${() => updateScenario({ preset: v })}>${word}<span class="visually-hidden">: ${what}</span></button>`,
      )}
    </div>
  </div>`;
}
