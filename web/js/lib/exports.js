/**
 * Downloads of the current scenario, made by the engine (never by the browser):
 *   csv   every state and county record of the run, each row stamped with the three
 *         fingerprints (GET /export/csv, or the exported file on the static site)
 *   html  the print-ready brief from the report writer (GET /export/html), saved as a page;
 *         open it and use the browser's Print to save a PDF
 *   hearing  the two-page hearing brief (GET /export/hearing, src/visionhousing/report/
 *         hearing.py), opened in a new tab to print; when this build cannot give it, the
 *         reason is said instead
 */
import { showToast } from "../state/store.js";
import { api, COMBO_ONLY, hearingBrief, LOCAL_ONLY } from "./api.js";

function why(sc) {
  return Object.keys(sc.overrides || {}).length ? LOCAL_ONLY : COMBO_ONLY;
}

function follow(url) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** Download the engine's CSV of a scenario. */
export async function downloadCsv(sc) {
  const url = await api.exportUrl("csv", sc);
  if (!url) return showToast(why(sc), 3600);
  follow(url);
  showToast("Downloading the table");
}

/** Save the engine's brief of a scenario (a print-ready page). */
export async function openBrief(sc) {
  const url = await api.exportUrl("html", sc);
  if (!url) return showToast(why(sc), 3600);
  follow(url);
  showToast("Brief saved: open it and print to PDF", 3200);
}

/** Open the two-page hearing brief of a scenario in a new tab, or say why it cannot open. */
export async function openHearing(sc) {
  const { url, reason } = await hearingBrief(sc);
  if (!url) return showToast(reason || why(sc), 4200);
  window.open(url, "_blank", "noopener");
  showToast("Hearing brief opened: print it to PDF", 3200);
}
