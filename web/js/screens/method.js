/**
 * Mode 5, Method & data. The map shades each county by the baseline's back-test error.
 *   left panel   how the numbers are made, in brief, and the sections (each button scrolls
 *                the right panel to its section)
 *   right panel  the back-test first (validation, the confidence signals, a folded
 *                "Sparse-data test and ADU sensitivity" section with the data ablation card
 *                and the ADU take-up x cost grid from ui/comparisons.js, drawn and asked for
 *                only when opened, the baseline rule contest with its stress
 *                ranges and surge note, error by kind of place), then the sources,
 *                the data report card, the Assumption Ledger, the glossary and downloads
 * In more words: the data report card, the provenance table, the full Assumption
 * Ledger (searchable), validation (the baseline backtest), the glossary and downloads.
 * The in-page contents list uses buttons that scroll, not "#" links, because the address
 * hash holds the route and the scenario.
 *
 * Data: GET /report-card, /provenance, /ledger and /validation for the state, the county
 * outlines (for the back-test error map), and the export links of the current scenario;
 * GET /states/ST/comparisons?part=ablation and ?part=matrix once the folded section opens.
 * The validation reply's `by_place` part carries the back-test broken down by kind of place,
 * each county's median signed error with its map class, and the sentences written from them;
 * every figure on that part is the engine's own text (`error_text`, `caught_text` ...). Values the API sends without a text form (ledger
 * entries, backtest errors) are only rounded for display (lib/format.js).
 */
import { glossaryList } from "../content/glossary.js";
import { stateName } from "../content/states.js";
import { api, API_BASE, baselineContest, contestView, baselineStress, baselineSurges, DATA_BASE, IS_STATIC } from "../lib/api.js";
import { useJson } from "../lib/data.js";
import { downloadCsv, openBrief } from "../lib/exports.js";
import { percent, plain, shortHash, withUnit } from "../lib/format.js";
import { html, useEffect, useState } from "../lib/html.js";
import { scenario } from "../state/store.js";
import { ConfidencePanel } from "../ui/confidence.js";
import { ScenarioActions } from "../ui/actions.js";
import { Card, Empty, Status } from "../ui/bits.js";
import { About, HBars, RangeMini } from "../ui/charts.js";
import { Dock } from "../ui/dock.js";
import { Icon } from "../ui/icons.js";
import { reducedMotion } from "../ui/motion.js";
import { ERROR_COLOURS } from "../ui/map.js";
import { Term } from "../ui/popover.js";
import { AblationCard, ComparisonFold, TakeupCostMatrix } from "../ui/comparisons.js";
import { BaselineCard } from "../ui/baseline-card.js";

const SECTIONS = [
  { id: "validation", label: "Validation (back-test)" },
  { id: "baseline-card", label: "Baseline card" },
  { id: "stress-tests", label: "Sparse-data test" },
  { id: "contest", label: "Baseline rule contest" },
  { id: "place-errors", label: "Error by place" },
  { id: "provenance", label: "Sources" },
  { id: "report-card", label: "Data report card" },
  { id: "ledger", label: "Assumption Ledger" },
  { id: "glossary", label: "Glossary" },
  { id: "downloads", label: "Downloads" },
];

function jump(id) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el.tagName === "DETAILS") {
    // A folded section opens (and asks for its cards) when the reader jumps to it.
    el.open = true;
    el.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
    el.querySelector("summary")?.focus({ preventScroll: true });
    return;
  }
  el.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
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
      <table class="table report-table" data-testid="report-table">
        <caption class="visually-hidden">Model inputs and their data status</caption>
        <thead><tr><th scope="col">Input</th><th scope="col">Status</th><th scope="col">Tier</th>
          <th scope="col">Edition</th><th scope="col" class="num">Coverage</th><th scope="col">Limits</th></tr></thead>
        <tbody>
          ${c.inputs.map((i) => {
            const limits = `${(i.limits || []).join(" ")}${i.assumed_by?.length ? ` Assumed by ${i.assumed_by.join(", ")}.` : ""}`.trim();
            return html`<tr>
              <th scope="row">${i.label}</th>
              <td class="rc-status"><span class=${`tag tag-${i.status}`}>${i.status}</span></td>
              <td data-label="Tier">${i.tier ?? "—"}</td>
              <td data-label="Edition">${i.vintage || "—"}</td>
              <td data-label="Coverage">${i.coverage === null || i.coverage === undefined ? "—" : percent(i.coverage)}</td>
              <td class="rc-limits">${limits}</td>
            </tr>`;
          })}
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
    html`<p class="card-sub">The baseline uses the rule <strong>${contestView(v)?.usedName || v.chosen_rule}</strong> (<code>${v.chosen_rule}</code>), chosen by the lowest ${v.columns[v.metric]}.
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
                  ? html`<th scope="row">${contestView(v)?.rules.find((x) => x.rule === r.rule)?.name || f(r[k])}${r.chosen ? " (used)" : ""}<code class="param-id">${r.rule}</code></th>`
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

/**
 * The baseline rule contest: every rule's error, coverage and range width on the same cases,
 * the pre-stated choosing rule and the winner; then the stress ranges (slump and boom
 * origins only) and the surge-year note. The validation reply carries no text form for these
 * scores, so they are only rounded for display (lib/format.js).
 */
/** A rule's typical miss: the server's text when it sent one, else the raw share rounded. */
function missText(validation, r) {
  return contestView(validation)?.rules.find((x) => x.rule === r.rule)?.text || percent(r.abs_pct);
}

function Contest({ state }) {
  const val = useJson(`validation:${state}`, () => api.validation(state));
  const c = baselineContest(val.data);
  const stress = baselineStress(val.data);
  const surge = baselineSurges(val.data);
  if (val.data && !c) return null;
  const vals = stress.flatMap((x) => [x.state?.p10, x.state?.p90]).filter(Number.isFinite);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  return html`<${Card} id="contest" title="Baseline rule contest"
      sub="Each simple rule projected past years it did not see; the rule fixed in advance picks the winner.">
    <${Status} state=${val} what="the rule contest" />
    ${c &&
    html`<p class="decision-sentence" data-testid="contest-winner">Winner: <strong>${c.winnerName}</strong> (${c.winnerReason}).</p>
    <div class="table-wrap">
      <table class="table contest-table" data-testid="contest-table">
        <caption class="visually-hidden">Baseline rules by error, coverage and range width</caption>
        <thead><tr>
          <th scope="col">Rule</th>
          <th scope="col" class="num" title=${c.columns.median_abs_log_ratio}>Miss</th>
          <th scope="col" class="num" title=${c.columns.median_signed_log_error}>Error</th>
          <th scope="col" class="num" title=${c.columns.coverage_80}>Caught</th>
          <th scope="col" class="num" title=${c.columns.range_width}>Width</th>
        </tr></thead>
        <tbody>
          ${c.rules.map(
            (r) => html`<tr class=${r.chosen ? "is-picked" : ""} data-rule=${r.rule}>
              <th scope="row" title=${r.describe}>${r.name}${r.chosen ? html` <span class="tag">winner</span>` : ""}${r.eligible ? "" : html` <span class="tag" title=${c.columns.eligible}>out of band</span>`}</th>
              <td class="num">${missText(val.data, r)}</td>
              <td class="num">${percent(r.typical_pct)}</td>
              <td class="num">${percent(r.coverage_80)}</td>
              <td class="num">${plain(r.range_width)}</td>
            </tr>`,
          )}
        </tbody>
      </table>
    </div>
    <${About} label="The choosing rule (fixed before scoring)">
      <p>${c.choosingRule}</p>
      <p>Miss: ${c.columns.median_abs_log_ratio}. Error: ${c.columns.median_signed_log_error}. Caught: ${c.columns.coverage_80}. Width: ${c.columns.range_width}.</p>
      <ul class="findings">${c.findings.map((f) => html`<li>${f}</li>`)}</ul>
    <//>`}
    ${stress.length > 0 &&
    html`<h3 class="card-h3">Stress ranges</h3>
    <ul class="stress-list" data-testid="stress">
      ${stress.map(
        (x) => html`<li class="stress-row">
          <span class="stress-kind">${x.kind === "slump" ? "Slump years only" : x.kind === "boom" ? "Boom years only" : x.kind}</span>
          <${RangeMini} n=${x.state} lo=${lo} hi=${hi} label=${x.kind} />
          <span class="decision-hint">${x.sentence}</span>
        </li>`,
      )}
    </ul>`}
    ${surge &&
    html`<h3 class="card-h3">Surge years</h3>
    <div data-testid="surge">
      ${surge.sentences?.length
        ? html`<ul class="findings">${surge.sentences.map((t) => html`<li>${t}</li>`)}</ul>`
        : html`<p class="decision-hint">No surge year (more than ${plain(surge.factor)} times the usual) falls in the baseline window.</p>`}
      ${(surge.named || []).map(
        (n) => html`<p class="decision-hint">${n.name}: typically ${percent(Math.abs(n.all?.typical_pct ?? NaN))} ${(n.all?.typical_pct ?? 0) > 0 ? "high" : "low"}
          over ${plain(n.all?.cases)} cases; ${n.with_surge?.cases ? `${plain(n.with_surge.cases)} cases include a surge year` : "none includes a surge year"}.</p>`,
      )}
    </div>`}
  <//>`;
}

function PlaceTable({ bp }) {
  return html`<div class="table-wrap">
    <table class="table table-wrapcells">
      <caption class="visually-hidden">Baseline back-test error and range coverage by kind of place</caption>
      <thead><tr>
        <th scope="col">Grouping</th><th scope="col">Group</th>
        <th scope="col" class="num">Counties</th><th scope="col" class="num">Cases</th>
        <th scope="col" class="num" title=${bp.measure.typical_pct}>Typical error (median)</th>
        <th scope="col" class="num" title=${bp.measure.median_abs}>Typical miss (either way)</th>
        <th scope="col" class="num" title=${bp.measure.coverage}>Range caught the real total</th>
      </tr></thead>
      <tbody>
        ${bp.groups.map(
          (g) => html`<tr class=${g.small ? "is-small-group" : ""}>
            <td>${g.title}</td>
            <th scope="row">${g.group}${g.small ? html` <span class="tag" title="Few counties or cases: a few places, not a pattern">few cases</span>` : ""}</th>
            <td class="num">${plain(g.counties)}</td>
            <td class="num">${plain(g.cases, 6)}</td>
            <td class="num">${g.error_text}</td>
            <td class="num">${g.abs_text}</td>
            <td class="num">${g.caught_text}</td>
          </tr>`,
        )}
      </tbody>
    </table>
  </div>`;
}

function ErrorMap({ bp }) {
  const [showTable, setShowTable] = useState(false);
  return html`<div class="error-map">
    <h3 class="card-h3">County error map</h3>
    <p class="card-sub">The map behind this panel shows each county's median error over its back-test cases
      (rule <strong>${bp.rule}</strong>). Red: the baseline projected more than was permitted. Blue: less.</p>
    <ol class="key-ramp key-ramp-5" aria-label="Map key">
      ${bp.map_classes.map(
        (c) => html`<li class="key-step"><span class="key-swatch" style=${`background: var(${ERROR_COLOURS[c.id]})`}></span>
          <span class="key-label">${c.label}</span></li>`,
      )}
    </ol>
    <ul class="key-grey"><li><span class="key-swatch is-nodata"></span>No back-test case</li></ul>
    <button type="button" class="btn btn-quiet btn-xs" aria-expanded=${showTable ? "true" : "false"}
      aria-controls="error-map-table" onClick=${() => setShowTable(!showTable)}>
      ${showTable ? "Hide the county error table" : "Show the county error table"}</button>
    <div id="error-map-table" class="table-wrap table-tall" hidden=${!showTable}>
      <table class="table">
        <caption class="visually-hidden">Baseline back-test error by county (the map's values)</caption>
        <thead><tr><th scope="col">County</th><th scope="col" class="num">Cases</th>
          <th scope="col" class="num">Typical error (median)</th><th scope="col" class="num">Typical miss</th>
          <th scope="col" class="num">Range caught the real total</th><th scope="col">Map class</th></tr></thead>
        <tbody>
          ${bp.counties.map(
            (c) => html`<tr>
              <th scope="row">${c.name}</th>
              <td class="num">${plain(c.cases, 6)}</td>
              <td class="num">${c.error_text}</td>
              <td class="num">${c.abs_text}</td>
              <td class="num">${c.caught_text}</td>
              <td><span class="key-swatch" style=${`background: var(${ERROR_COLOURS[c.cls]})`}></span>
                ${bp.map_classes.find((k) => k.id === c.cls)?.label || "no back-test case"}</td>
            </tr>`,
          )}
        </tbody>
      </table>
    </div>
  </div>`;
}

function PlaceErrors({ state }) {
  const val = useJson(`validation:${state}`, () => api.validation(state));
  const bp = val.data?.by_place;
  const missing = (bp?.dimensions || []).filter((d) => !d.available);
  return html`<${Card} id="place-errors" title="Error by kind of place"
      sub="Where the baseline has been right or wrong: the same back-test, split by kind of county.">
    <${Status} state=${val} what="the back-test by place" />
    ${val.data && !bp && html`<${Empty}>No back-test by place for this state.<//>`}
    ${bp &&
    html`${val.data.synthetic && html`<p class="card-sub">Testland is synthetic: these errors describe no real place.</p>`}
    <ul class="findings">${bp.findings.map((f) => html`<li>${f}</li>`)}</ul>
    <${About} label="How to read this table"><p>Typical error: the median of log(projected / actual), shown as a percent; "high" means the
      baseline projected more than was permitted. Typical miss: the same, either way. A range is built only from
      other counties' past errors, so "caught" is an out-of-sample check of the likely range.
      Groups marked "few cases" are shown, not hidden: read them as a few places, not a pattern.</p><//>
    <${PlaceTable} bp=${bp} />
    ${missing.length > 0 &&
    html`<p class="card-sub">Not available for this state: ${missing.map((d) => `${d.title} (${d.reason})`).join("; ")}.</p>`}
    <${ErrorMap} bp=${bp} />`}
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

export function MethodMode({ validation }) {
  const state = scenario.value.state;
  const [query, setQuery] = useState("");
  const terms = glossaryList().filter(
    (t) => !query || `${t.term} ${t.text}`.toLowerCase().includes(query.toLowerCase()),
  );
  const v = validation?.data;
  const view = contestView(v);
  const bars = (view?.rules || []).map((r) => ({
    key: r.rule,
    label: `${r.name}${r.used ? " (used)" : ""}`,
    value: r.value,
    text: r.text || percent(r.value),
  }));
  const usedRow = view?.rules.find((r) => r.used);
  return html`<${Dock} side="left" mode="method" title="How the numbers are made" icon="book"
      sub=${`Method & data · ${stateName(state)}`} chip=${view ? `Rule: ${view.usedName}` : v ? `Rule: ${v.chosen_rule}` : ""}>
      <p class="sentence">Every number in this tool comes from one engine run, with its formula, sources and
        assumptions. This is decision support, not a forecast.</p>
      ${usedRow &&
      html`<div class="lead" data-testid="method-lead"><p class="lead-label">Baseline rule (${view.usedName}): ${view.measure.toLowerCase()} in the back-test</p>
        <p class="lead-number">${usedRow.text || percent(usedRow.value)}</p></div>`}
      ${bars.length > 1 &&
      html`<${HBars} title=${`Each projection rule's ${view.measure.toLowerCase()} in the back-test (lower is better)`} items=${bars}
        note=${`${view.measure}: ${view.measureHelp}. The same measure as "Miss" in the rule contest.`} />`}
      ${view?.note && html`<p class="decision-hint" data-testid="method-rule-note">${view.note}</p>`}
      <nav class="toc" aria-label="On this page">
        ${SECTIONS.map((sec, i) => html`<button type="button" class="toc-link stagger" style=${`--i: ${i}`} onClick=${() => jump(sec.id)}>${sec.label}</button>`)}
      </nav>
    <//>
    <${Dock} side="right" mode="method" title="Back-test and sources" icon="chart" sub="Validation, sources, ledger, glossary, downloads"
      chip=${v ? `${v.rows.length} rules tested` : ""}
      actions=${html`<${ScenarioActions} compact=${true} label="Slides, export and share" />`}>
      <div class="stack">
        <${Validation} state=${state} />
        <${BaselineCard} state=${state} />
        <${ConfidencePanel} state=${state} reform=${scenario.value.reforms.length === 1 ? scenario.value.reforms[0] : ""} />
        <${ComparisonFold} id="stress-tests" title="Sparse-data test and ADU sensitivity"
            sub="The method with less data (tiers 2, 1, 0), and ADU take-up with construction cost. Computed when opened; the first time can take about a minute.">
          <${AblationCard} state=${state} h=${3} />
          <${TakeupCostMatrix} state=${state} h=${3} />
        <//>
        <${Contest} state=${state} />
        <${PlaceErrors} state=${state} />
        <${Provenance} state=${state} />
        <${ReportCard} state=${state} />
        <${Ledger} state=${state} />
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
    <//>`;
}
