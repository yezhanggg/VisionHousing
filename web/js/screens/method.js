/**
 * Screen 4, Method & data: the data report card, the provenance table, the full Assumption
 * Ledger (searchable), validation, the glossary and downloads.
 * The in-page contents list uses buttons that scroll, not "#" links, because the address
 * hash holds the route and the scenario.
 */
import { glossaryList } from "../content/glossary.js";
import { stateName } from "../content/states.js";
import { html, useState } from "../lib/html.js";
import { scenario } from "../state/store.js";
import { Card, Empty } from "../ui/bits.js";
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

export function MethodScreen() {
  const [query, setQuery] = useState("");
  const terms = glossaryList().filter(
    (t) => !query || `${t.term} ${t.text}`.toLowerCase().includes(query.toLowerCase()),
  );
  return html`<main id="main" class="screen screen-page" tabindex="-1">
    <div class="page page-with-toc">
      <header class="page-head">
        <p class="kicker">Method & data · ${stateName(scenario.value.state)}</p>
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
        <${Card} id="report-card" title="Data report card"
            sub=${html`What is measured, used and assumed, by <${Term} id="tier">data tier<//>.`}>
          <${Empty} title="No data pack yet">The report card appears once the state data pack is built.<//>
        <//>
        <${Card} id="provenance" title="Sources"
            sub="Source, version, access date, licence, cleaning and limits.">
          <${Empty} title="No sources loaded yet">The provenance table appears once the state data pack is built.<//>
        <//>
        <${Card} id="ledger" title="Assumption Ledger"
            sub="Every assumption, with its low, central and high value, source and reason.">
          <label class="field field-search">
            <span class="visually-hidden">Search the ledger</span>
            <${Icon} name="search" />
            <input class="input" type="search" placeholder="Search the ledger" disabled
              aria-describedby="ledger-empty" />
          </label>
          <div id="ledger-empty"><${Empty}>The ledger loads here once the engine is connected.<//></div>
        <//>
        <${Card} id="validation" title="Validation"
            sub="Baseline backtests, hindcasts of past reforms, and tests with less data.">
          <${Empty} title="No validation runs yet">Backtests and hindcasts appear once the engine is connected.<//>
        <//>
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
        <${Card} id="downloads" title="Downloads"
            sub="Each file is stamped with the code version, data manifest and assumption set.">
          <${Empty}>Downloads appear once the engine is connected.<//>
        <//>
      </div>
    </div>
  </main>`;
}
