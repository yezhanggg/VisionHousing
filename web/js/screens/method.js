/**
 * Screen 4, Method & data: the data report card, the provenance table, the full Assumption
 * Ledger (searchable), validation (the baseline backtest), the glossary and downloads.
 * The in-page contents list uses buttons that scroll, not "#" links, because the address
 * hash holds the route and the scenario.
 *
 * Data: GET /report-card, /provenance, /ledger and /validation for the state, and the
 * export links of the current scenario. Values the API sends without a text form (ledger
 * entries, backtest errors) are only rounded for display (lib/format.js).
 */
import { glossaryList } from "../content/glossary.js";
import { stateName } from "../content/states.js";
import { api, API_BASE, DATA_BASE, IS_STATIC } from "../lib/api.js";
import { useJson } from "../lib/data.js";
import { downloadCsv, openBrief } from "../lib/exports.js";
import { percent, plain, shortHash, withUnit } from "../lib/format.js";
import { html, useEffect, useState } from "../lib/html.js";
import { scenario } from "../state/store.js";
import { Card, Empty, Status } from "../ui/bits.js";
import { Icon } from "../ui/icons.js";
import { Term } from "../ui/popover.js";

const SECTIONS = [
  { id: "report-card", label: "Data report card" },
  { id: "provenance", label: "Sources" },
  { id: "ledger", label: "Assumption Ledger" },
  { id: "validation", label: "Validation" },
  { id: "glossary", label: "Glossary" },
  { id: "downloads", label: "Downloads" },
];

function jump(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ block: "start" });
  el.querySelector("h2")?.setAttribute("tabindex", "-1");
  el.querySelector("h2")?.focus({ preventScroll: true });
}

function ReportCard({ state }) {
  const card = useJson(`report-card:${state}`, () => api.reportCard(state));
  const c = card.data;
  return html`<${Card} id="report-card" title="Data report card"
      sub=${html`What is measured, used and assumed, by <${Term} id="tier">data tier<//>.`}>
    <${Status} state=${card} what="the report card" />
    ${c &&
    html`<p class="card-sub">${Object.entries(c.counts).map(([k, v]) => `${v} ${k}`).join(" · ")}${c.pack_hash ? ` · pack ${shortHash(c.pack_hash)}` : ""}</p>
    <div class="table-wrap">
      <table class="table table-wrapcells">
        <caption class="visually-hidden">Model inputs and their data status</caption>
        <thead><tr><th scope="col">Input</th><th scope="col">Status</th><th scope="col">Tier</th>
          <th scope="col">Edition</th><th scope="col" class="num">Coverage</th><th scope="col">Limits</th></tr></thead>
        <tbody>
          ${c.inputs.map(
            (i) => html`<tr>
              <th scope="row">${i.label}</th>
              <td><span class=${`tag tag-${i.status}`}>${i.status}</span></td>
              <td>${i.tier}</td>
              <td>${i.vintage}</td>
              <td class="num">${i.coverage === null || i.coverage === undefined ? "—" : percent(i.coverage)}</td>
              <td>${(i.limits || []).join(" ")}${i.assumed_by?.length ? ` Assumed by ${i.assumed_by.join(", ")}.` : ""}</td>
            </tr>`,
          )}
        </tbody>
      </table>
    </div>`}
  <//>`;
}

function Provenance({ state }) {
  const prov = useJson(`provenance:${state}`, () => api.provenance(state));
  const rows = prov.data?.sources || [];
  return html`<${Card} id="provenance" title="Sources" sub="Source, version, access date, licence, cleaning and limits.">
    <${Status} state=${prov} what="the sources" />
    ${prov.data?.pack && html`<p class="card-sub">Data pack ${shortHash(prov.data.pack.pack_hash)} · code ${prov.data.pack.code_version || "—"}</p>`}
    ${rows.length > 0 &&
    html`<div class="table-wrap table-tall">
      <table class="table table-wrapcells">
        <caption class="visually-hidden">Where every dataset comes from</caption>
        <thead><tr><th scope="col">Source</th><th scope="col">Version</th><th scope="col">Read on</th>
          <th scope="col">Licence</th><th scope="col">Cleaning</th><th scope="col">Limits</th></tr></thead>
        <tbody>
          ${rows.map(
            (r) => html`<tr>
              <th scope="row">${r.source}<code class="param-id">${r.id}</code></th>
              <td>${r.version}</td><td>${r.access_date}</td><td>${r.licence}</td>
              <td>${r.cleaning}</td><td>${r.limits}</td>
            </tr>`,
          )}
        </tbody>
      </table>
    </div>`}
  <//>`;
}

function Ledger({ state }) {
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(query.trim()), 250);
    return () => clearTimeout(t);
  }, [query]);
  const led = useJson(`ledger:${state}:${q}`, () => api.ledger({ q, state }));
  const rows = led.data?.parameters || [];
  return html`<${Card} id="ledger" title="Assumption Ledger"
      sub="Every assumption, with its low, central and high value, source and reason.">
    <label class="field field-search">
      <span class="visually-hidden">Search the ledger</span>
      <${Icon} name="search" />
      <input class="input" type="search" placeholder="Search the ledger" value=${query}
        onInput=${(e) => setQuery(e.currentTarget.value.slice(0, 100))} />
    </label>
    ${led.data && html`<p class="card-sub" role="status">${led.data.count} of ${led.data.total} assumptions · ledger ${shortHash(led.data.hash)}</p>`}
    <${Status} state=${led} what="the ledger" />
    ${led.data && rows.length === 0 && html`<${Empty}>No assumption matches "${q}".<//>`}
    ${rows.length > 0 &&
    html`<div class="table-wrap table-tall">
      <table class="table table-wrapcells">
        <caption class="visually-hidden">Assumption Ledger entries</caption>
        <thead><tr><th scope="col">Assumption</th><th scope="col" class="num">Low</th><th scope="col" class="num">Central</th>
          <th scope="col" class="num">High</th><th scope="col">Status</th><th scope="col">Source and reason</th></tr></thead>
        <tbody>
          ${rows.slice(0, 200).map(
            (p) => html`<tr>
              <th scope="row">${p.label}<code class="param-id">${p.id}</code></th>
              <td class="num">${withUnit(p.low, p.unit)}</td>
              <td class="num">${withUnit(p.central, p.unit)}</td>
              <td class="num">${withUnit(p.high, p.unit)}</td>
              <td><span class="tag">${p.status}</span></td>
              <td>${(p.source || []).map((s) => s.citation).join("; ")}${p.rationale ? ` ${p.rationale}` : ""}</td>
            </tr>`,
          )}
        </tbody>
      </table>
    </div>`}
    ${rows.length > 200 && html`<p class="card-sub">Showing the first 200. Search to narrow the list.</p>`}
  <//>`;
}

const VALIDATION_COLS = [
  ["rule", (v) => v],
  ["complexity", (v) => plain(v)],
  ["cases", (v) => plain(v)],
  ["mape", (v) => percent(v)],
  ["median_abs_log_ratio", (v) => plain(v)],
  ["coverage_80", (v) => percent(v)],
  ["coverage_cases", (v) => plain(v)],
];

function Validation({ state }) {
  const val = useJson(`validation:${state}`, () => api.validation(state));
  const v = val.data;
  return html`<${Card} id="validation" title="Validation"
      sub="The baseline backtest: each simple projection rule is scored on past years it did not see.">
    <${Status} state=${val} what="the validation" />
    ${v &&
    html`<p class="card-sub">The baseline uses the rule <strong>${v.chosen_rule}</strong>, chosen by the lowest ${v.columns[v.metric]}.
      ${v.synthetic ? " Testland is synthetic: these scores describe no real place." : ""}</p>
    <div class="table-wrap">
      <table class="table">
        <caption class="visually-hidden">Baseline backtest by projection rule</caption>
        <thead><tr>${VALIDATION_COLS.map(([k]) => html`<th scope="col" class=${k === "rule" ? "" : "num"} title=${v.columns[k]}>${v.columns[k]}</th>`)}</tr></thead>
        <tbody>
          ${v.rows.map(
            (r) => html`<tr class=${r.chosen ? "is-picked" : ""}>
              ${VALIDATION_COLS.map(([k, f], i) =>
                i === 0
                  ? html`<th scope="row">${f(r[k])}${r.chosen ? " (used)" : ""}</th>`
                  : html`<td class="num">${f(r[k])}</td>`,
              )}
            </tr>`,
          )}
        </tbody>
      </table>
    </div>
    <p class="card-sub">Hindcasts of past reforms are not in the engine yet; the evidence cards on each Reform page show what happened where similar rules were tried.</p>`}
  <//>`;
}

function Downloads({ state }) {
  const sc = scenario.value;
  const base = IS_STATIC ? DATA_BASE : API_BASE;
  const files = IS_STATIC
    ? [
        ["Assumption Ledger (JSON)", `${base}/${state}/ledger.json`],
        ["Sources (JSON)", `${base}/${state}/provenance.json`],
        ["Data report card (JSON)", `${base}/${state}/report-card.json`],
        ["Backtest (JSON)", `${base}/${state}/validation.json`],
      ]
    : [
        ["Assumption Ledger (JSON)", `${base}/ledger?state=${state}`],
        ["Sources (JSON)", `${base}/provenance?state=${state}`],
        ["Data report card (JSON)", `${base}/report-card?state=${state}`],
        ["Backtest (JSON)", `${base}/validation?state=${state}`],
      ];
  return html`<${Card} id="downloads" title="Downloads"
      sub="Each run file is stamped with the code version, data and assumption fingerprints.">
    <div class="download-row">
      <button type="button" class="btn btn-primary" onClick=${() => downloadCsv(sc)}>
        <${Icon} name="download" /><span>Results table (CSV)</span></button>
      <button type="button" class="btn btn-quiet" onClick=${() => openBrief(sc)}>
        <${Icon} name="page" /><span>Brief (HTML)</span></button>
    </div>
    <p class="card-sub">Both are made from the current scenario: ${sc.reforms.length ? sc.reforms.join(", ") : "the baseline"}.</p>
    <ul class="download-list">
      ${files.map(([label, href]) => html`<li><a href=${href} download="">${label}</a></li>`)}
    </ul>
  <//>`;
}

export function MethodScreen() {
  const state = scenario.value.state;
  const [query, setQuery] = useState("");
  const terms = glossaryList().filter(
    (t) => !query || `${t.term} ${t.text}`.toLowerCase().includes(query.toLowerCase()),
  );
  return html`<main id="main" class="screen screen-page" tabindex="-1">
    <div class="page page-with-toc">
      <header class="page-head">
        <p class="kicker">Method & data · ${stateName(state)}</p>
        <h1 class="page-title">How the numbers are made</h1>
        <p class="page-lead">
          Every number in this tool comes from one engine run, with its formula, sources and
          assumptions. This is decision support, not a forecast.
        </p>
      </header>
      <nav class="toc" aria-label="On this page">
        ${SECTIONS.map((s) => html`<button type="button" class="toc-link" onClick=${() => jump(s.id)}>${s.label}</button>`)}
      </nav>
      <div class="stack">
        <${ReportCard} state=${state} />
        <${Provenance} state=${state} />
        <${Ledger} state=${state} />
        <${Validation} state=${state} />
        <${Card} id="glossary" title="Glossary" sub="Plain words for every technical term.">
          <label class="field field-search">
            <span class="visually-hidden">Filter the glossary</span>
            <${Icon} name="search" />
            <input class="input" type="search" placeholder="Filter the glossary" value=${query}
              onInput=${(e) => setQuery(e.currentTarget.value.slice(0, 60))} />
          </label>
          ${terms.length === 0
            ? html`<${Empty}>No term matches "${query}".<//>`
            : html`<dl class="glossary">
                ${terms.map((t) => html`<div class="glossary-row"><dt>${t.term}</dt><dd>${t.text}</dd></div>`)}
              </dl>`}
        <//>
        <${Downloads} state=${state} />
      </div>
    </div>
  </main>`;
}
