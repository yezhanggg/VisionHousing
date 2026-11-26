/**
 * "Ask about this run": the question box in the bottom-left corner of the map.
 *
 * A reader types a plain question, or picks a suggested one for the state, and gets an answer
 * card built from this run's own records. There is no language model behind it: the resolver
 * (lib/answers.js) matches the question to one fixed intent by words from
 * assumptions/answers.yaml (copied to content/answers.json) and fills a template with text
 * copied from the records the screens already read (lib/api.js: the live back end, or the
 * static site's files). Every number in an answer is a record's shown value, with its period,
 * units, likely range, comparison, sources and the run id.
 *
 * Privacy: the question is answered inside this page. It is never sent to the back end or
 * anywhere else, and it is not kept (not in the address, not in browser storage). Only the
 * records are fetched, the same ones the screens fetch.
 *
 * Accessibility: the box opens with its toggle button (closed by default; whether it is open is
 * this browser's preference); the input has a label; Enter asks; suggested questions are
 * buttons; the answer is read out through a polite live region; "Show the record" is a
 * disclosure button.
 */
import table from "../content/answers.json";
import { api, budgetGrid, implementationRows, runBody } from "../lib/api.js";
import { resolve, UnavailableError } from "../lib/answers.js";
import { html, useRef, useState } from "../lib/html.js";
import { prefs, route, scenario, setPref } from "../state/store.js";
import { Icon } from "./icons.js";
import "../../css/ask.css";

/** Anything the build cannot read becomes "not in this run" for the resolver. */
function held(promise) {
  return Promise.resolve(promise).catch((err) => {
    throw new UnavailableError(err?.message || "");
  });
}

/** The resolver's reader over lib/api.js (live back end or static files, as this build is). */
export const apiReader = {
  run: (sc, drivers) => held(api.run(runBody(sc, drivers ? { drivers: true } : {}))),
  validation: (st) => held(api.validation(st)),
  permitYears: (st) => held(api.permitYears(st)),
  implementation: (st, reform, geoid) => held(implementationRows(st, reform, geoid)),
  dials: (st) => held(api.comparisons(st, "dials")),
  reportCard: (st) => held(api.reportCard(st)),
  budgetGrid: (st) => held(budgetGrid(st)),
  download: (sc) => Promise.resolve(api.exportUrl("csv", sc)).catch(() => ""),
};

const STATUS_WORD = {
  answered: "From this run",
  unknown: "Not known from this run",
  out_of_range: "Out of range",
  unavailable: "Not in this run",
  unsupported: "Can't answer",
};

/** A record's shown value as text (it is already text in the record; only joined here). */
function shownText(shown) {
  if (typeof shown === "string") return shown;
  if (Array.isArray(shown)) return shown.map((x) => (x && typeof x === "object" ? `${x.year}: ${x.shown}` : String(x))).join("; ");
  if (shown && typeof shown === "object") return Object.entries(shown).map(([k, v]) => `${k.toUpperCase()} ${v}`).join(" · ");
  return "";
}

function Record({ rec }) {
  const extra = [
    rec.unit && `unit: ${rec.unit}`,
    rec.start && `start: ${rec.start}`,
    rec.verified_on && `checked: ${rec.verified_on}`,
    rec.value_state && `value state: ${rec.value_state}`,
  ].filter(Boolean);
  return html`<li class="ask-record">
    <code class="ask-record-id">${rec.id}</code>
    ${rec.name && html`<span class="ask-record-name">${rec.name}</span>`}
    ${shownText(rec.shown) && html`<span class="ask-record-shown">${shownText(rec.shown)}</span>`}
    ${extra.length > 0 && html`<span class="ask-record-meta">${extra.join(" · ")}</span>`}
    ${rec.formula && html`<span class="ask-record-formula">${rec.formula}</span>`}
    ${(rec.source_ids || []).length > 0 && html`<span class="ask-record-meta">sources: ${rec.source_ids.join(", ")}</span>`}
    ${(rec.urls || []).map((u) => html`<a class="ask-link" href=${u} target="_blank" rel="noopener noreferrer">${u}</a>`)}
  </li>`;
}

function AnswerCard({ answer, onAsk }) {
  const [open, setOpen] = useState(false);
  const id = "ask-records";
  return html`<article class=${`ask-answer is-${answer.status}`} data-testid="ask-answer" data-intent=${answer.intent}
      data-status=${answer.status} aria-labelledby="ask-answer-title">
    <header class="ask-answer-head">
      <h3 id="ask-answer-title" class="ask-answer-title">${answer.title}</h3>
      <span class="ask-status">${STATUS_WORD[answer.status] || answer.status}</span>
    </header>
    <div class="ask-answer-text" data-testid="ask-answer-text">
      ${answer.lines.map((line) => html`<p>${line}</p>`)}
    </div>
    ${answer.run_id && html`<p class="ask-meta">Run <code data-testid="ask-run-id">${answer.run_id}</code>${answer.period ? ` · ${answer.period}` : ""}</p>`}
    ${answer.status !== "unsupported" && answer.status !== "unavailable" && html`<p class="ask-meta">
      <a class="ask-link" href=${answer.link}>Open this exact view</a>
      ${answer.download && html` · <a class="ask-link" href=${answer.download} download>Download the table</a>`}
    </p>`}
    ${answer.records.length > 0 && html`<button type="button" class="btn btn-quiet btn-xs ask-show" aria-expanded=${open ? "true" : "false"}
        aria-controls=${id} onClick=${() => setOpen(!open)}>${open ? "Hide the record" : "Show the record"}</button>`}
    ${answer.records.length > 0 && html`<div id=${id} class="ask-records" hidden=${!open}>
      <ul>${answer.records.map((rec) => html`<${Record} rec=${rec} />`)}</ul>
      ${answer.sources.length > 0 && html`<p class="ask-sources-title">Sources</p>
      <ul class="ask-sources">${answer.sources.map((s) => html`<li>
        ${s.url ? html`<a class="ask-link" href=${s.url} target="_blank" rel="noopener noreferrer">${s.citation}</a>` : s.citation}
      </li>`)}</ul>`}
    </div>`}
    ${answer.offers.length > 0 && html`<div class="ask-offers" role="group" aria-label="Questions this box can answer">
      ${answer.offers.map((o) => html`<button type="button" class="btn btn-quiet btn-xs ask-chip" onClick=${() => onAsk(o)}>${o}</button>`)}
    </div>`}
  </article>`;
}

/** The view on screen, as the resolver reads it. */
function contextOf(county) {
  const sc = scenario.value;
  const r = route.value;
  const onReform = r.name === "reform" && Object.prototype.hasOwnProperty.call(table.reforms, r.param);
  return {
    state: sc.state,
    reforms: onReform ? [r.param] : [...(sc.reforms || [])],
    overrides: { ...(sc.overrides || {}) },
    route: r.name || "state",
    param: r.param || "",
    county: r.name === "place" ? String(r.param || "").slice(0, 5) : county || "",
  };
}

export function AskBox({ county = "" }) {
  const open = prefs.value.chatOpen;
  const st = scenario.value.state;
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const asked = useRef(0);
  const suggestions = table.suggestions[st] || table.suggestions.default;

  const ask = async (text) => {
    const q = String(text || "").trim();
    if (!q) return;
    setQuestion(q);
    const n = ++asked.current;
    setBusy(true);
    setSaid("Working on the answer from this run's records.");
    try {
      const reply = await resolve(q, contextOf(county), apiReader, table);
      if (n !== asked.current) return;
      setAnswer(reply);
      setSaid(`${reply.title}. ${reply.text}`);
    } catch {
      if (n !== asked.current) return;
      setAnswer(null);
      setSaid("The answer could not be built. Try again.");
    } finally {
      if (n === asked.current) setBusy(false);
    }
  };

  return html`<section class=${`chat ask ${open ? "is-open" : ""}`} data-zone="chat" aria-label="Ask about this run">
    <button type="button" class="chat-toggle" aria-expanded=${open ? "true" : "false"} aria-controls="chat-body"
        onClick=${() => setPref({ chatOpen: !open })}>
      <${Icon} name="chat" size=${16} /><span class="chat-title">Ask<span class="chat-more"> about this run</span></span>
      <span class="ask-badge" title="Answers come from fixed rules and this run's records; no AI model">No AI</span>
      <${Icon} name=${open ? "chevronDown" : "chevronUp"} size=${14} />
    </button>
    <div class="chat-body ask-body" id="chat-body" hidden=${!open}>
      <form class="ask-form" role="search" aria-label="Ask a question about this run" onSubmit=${(e) => {
        e.preventDefault();
        ask(question);
      }}>
        <label class="visually-hidden" for="chat-input">Your question about this run</label>
        <input id="chat-input" class="input chat-input" type="text" maxlength="500" autocomplete="off"
          placeholder="Ask about this run" value=${question} onInput=${(e) => setQuestion(e.currentTarget.value)}
          aria-describedby="ask-privacy" />
        <button type="submit" class="btn btn-primary ask-go" disabled=${busy || !question.trim()}>Ask</button>
      </form>
      <p class="ask-privacy" id="ask-privacy">Answered in this page from this run's records by fixed rules (no AI).
        Your question is not sent anywhere or kept.</p>
      ${!answer && html`<div class="ask-offers" role="group" aria-label="Suggested questions">
        ${suggestions.map((s) => html`<button type="button" class="btn btn-quiet btn-xs ask-chip" onClick=${() => ask(s)}>${s}</button>`)}
      </div>`}
      ${busy && html`<p class="ask-busy">Reading this run's records…</p>`}
      ${answer && !busy && html`<${AnswerCard} key=${asked.current} answer=${answer} onAsk=${ask} />`}
      <p class="visually-hidden" role="status" aria-live="polite" data-testid="ask-live">${said}</p>
    </div>
  </section>`;
}
