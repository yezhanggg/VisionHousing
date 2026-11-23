/** The one status toast ("Link copied"). Announced politely to screen readers. */
import { html } from "../lib/html.js";
import { toast } from "../state/store.js";

export function Toast() {
  const t = toast.value;
  return html`<div class="toast-region" role="status" aria-live="polite">
    ${t && html`<div class="toast" key=${t.id}>${t.text}</div>`}
  </div>`;
}
