/**
 * Screen "Place": a plain profile of one county (or one census tract) for market research.
 *
 *   #/place?v=1&s=PA            search a county by name
 *   #/place/42003?v=1&s=PA      that county's profile
 *   #/place/42003020100?v=1&s=PA one tract's profile (picked from its county)
 *
 * Each section is a card: the one-sentence summary on top, the facts below. Every fact shows
 * its source on hover (title) or tap (a short note under it), and a numbered footnote that
 * matches the source list at the end. Nothing here computes a number: the text comes from
 * the profile endpoint, through lib/api.js (the back end, or the exported files on the static
 * site). "Copy share link" copies the address, which holds the
 * state and the place; "Print / save PDF" prints the page with the print rules in app.css.
 */
import printCss from "../../css/place-print.css?url";
import { stateName } from "../content/states.js";
import { api, IS_STATIC } from "../lib/api.js";
import { html, useEffect, useState } from "../lib/html.js";
import { navigate, route, scenario } from "../state/store.js";
import { Card, Empty } from "../ui/bits.js";
import { Icon } from "../ui/icons.js";
import { shareLink } from "../ui/topbar.js";

const MAX_MATCHES = 8;

/**
 * Load a list or a profile; returns {data, error, loading}. `key` names the request (it
 * changes when the request changes); `load` makes it. An empty key loads nothing.
 */
function useJson(key, load) {
  const [state, setState] = useState({ data: null, error: "", loading: Boolean(key) });
  useEffect(() => {
    if (!key) {
      setState({ data: null, error: "", loading: false });
      return undefined;
    }
    let live = true;
    setState({ data: null, error: "", loading: true });
    load()
      .then((data) => live && setState({ data, error: "", loading: false }))
      .catch((err) => live && setState({ data: null, error: String(err.message || err), loading: false }));
    return () => {
      live = false;
    };
  }, [key]);
  return state;
}

function PlaceSearch({ places, current }) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const matches = q
    ? places.filter((p) => p.name.toLowerCase().includes(q) || p.geoid.includes(q)).slice(0, MAX_MATCHES)
    : [];
  const pick = (geoid) => {
    setQuery("");
    navigate("place", geoid);
  };
  return html`<div class="place-search">
    <label class="field">
      <span class="field-label">Find a county</span>
      <span class="field-search">
        <${Icon} name="search" />
        <input class="input" type="search" value=${query} placeholder="Type a county name"
          autocomplete="off" aria-describedby="place-search-hint"
          onInput=${(e) => setQuery(e.currentTarget.value)}
          onKeyDown=${(e) => {
            if (e.key === "Enter" && matches.length) pick(matches[0].geoid);
          }} />
      </span>
    </label>
    <p class="place-hint" id="place-search-hint">
      ${places.length ? `${places.length} counties. Press Enter to open the first match.` : ""}
    </p>
    ${matches.length > 0 &&
    html`<ul class="place-matches" aria-label="Matching counties">
      ${matches.map(
        (p) => html`<li><button type="button" class="place-match"
            aria-current=${p.geoid === current ? "true" : undefined} onClick=${() => pick(p.geoid)}>
          <span>${p.name}</span><span class="place-code">${p.geoid}</span>
        </button></li>`,
      )}
    </ul>`}
    ${q && matches.length === 0 && html`<p class="place-hint">No county matches “${query}”.</p>`}
  </div>`;
}

function TractPicker({ state, county, current }) {
  const { data } = useJson(`tracts/${state}/${county}`, async () =>
    (await api.tractsAvailable(state)) ? api.places(state, { level: "tract", q: county, limit: 500 }) : null,
  );
  const tracts = (data?.places || []).filter((p) => p.geoid.startsWith(county));
  if (!tracts.length) return null;
  return html`<label class="field place-tracts">
    <span class="field-label">Look at one census tract</span>
    <select class="select" value=${current.length === 11 ? current : ""}
      onChange=${(e) => navigate("place", e.currentTarget.value || county)}>
      <option value="">The whole county</option>
      ${tracts.map((t) => html`<option value=${t.geoid}>${t.name.split(",")[0]} (${t.geoid})</option>`)}
    </select>
  </label>`;
}

function Fact({ fact, notes, sources }) {
  const [open, setOpen] = useState(false);
  const src = sources[fact.source] || {};
  const where = fact.measured_at;
  const detail = `${src.name || fact.source} (${src.publisher || ""}), ${fact.vintage}. Year: ${fact.year}. Measured by ${where}.`;
  const margin = fact.range_text ? ` Survey range (90% margin of error): ${fact.range_text}.` : "";
  const c = fact.comparison;
  return html`<div class="fact">
    <dt class="fact-label">${fact.label}</dt>
    <dd class="fact-body">
      <span class="fact-value">${fact.text}</span>
      <button type="button" class="fact-src" title=${detail} aria-expanded=${open ? "true" : "false"}
        aria-label=${`Source ${notes[fact.source]}: ${detail}`} onClick=${() => setOpen(!open)}>
        [${notes[fact.source]}]</button>
      ${c && c.words && html`<span class="fact-compare">${c.words}: ${c.text} in ${c.name}</span>`}
      ${fact.range_text && html`<span class="fact-range">range ${fact.range_text}</span>`}
      ${open && html`<span class="fact-note">${detail}${margin}${fact.note ? ` How: ${fact.note}.` : ""}</span>`}
    </dd>
  </div>`;
}

function SectionCard({ section, notes, sources }) {
  return html`<${Card} id=${`sec-${section.key}`} title=${section.title} className="place-card">
    <p class="place-summary">${section.summary}</p>
    <dl class="facts">
      ${section.facts.map((f) => html`<${Fact} fact=${f} notes=${notes} sources=${sources} />`)}
    </dl>
    ${section.limits && html`<p class="place-limits"><strong>What this cannot tell you:</strong> ${section.limits}</p>`}
  <//>`;
}

function Profile({ profile }) {
  const notes = {};
  Object.keys(profile.sources).forEach((id, i) => {
    notes[id] = i + 1;
  });
  const ready = profile.sections.filter((s) => s.status === "ok");
  const later = profile.sections.filter((s) => s.status !== "ok");
  return html`<div class="place-profile">
    ${profile.synthetic &&
    html`<p class="place-banner">Synthetic test state: these numbers are made up for testing and are not real data.</p>`}
    <div class="place-grid">
      ${ready.map((s) => html`<${SectionCard} section=${s} notes=${notes} sources=${profile.sources} />`)}
    </div>
    ${later.length > 0 &&
    html`<${Card} id="sec-later" title="Not available for this place yet" className="place-card place-later">
      <ul class="place-later-list">
        ${later.map((s) => html`<li><strong>${s.title}</strong>: ${s.summary}</li>`)}
      </ul>
    <//>`}
    <${Card} id="sec-sources" title="Sources" className="place-card">
      <ol class="place-sources">
        ${Object.entries(profile.sources).map(
          ([id, s]) => html`<li>${s.name} (${s.publisher}), ${s.vintage}. <code>${id}</code></li>`,
        )}
      </ol>
      ${profile.pack_hash && html`<p class="place-hint">Data pack ${profile.pack_hash}</p>`}
    <//>
  </div>`;
}

/** Add the Place screen's print page (css/place-print.css) while the screen is open. */
function usePrintPage() {
  useEffect(() => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = printCss;
    link.dataset.screen = "place";
    document.head.appendChild(link);
    return () => link.remove();
  }, []);
}

export function PlaceScreen() {
  usePrintPage();
  const state = scenario.value.state;
  const geoid = route.value.param;
  const county = geoid.slice(0, 5);
  const list = useJson(`counties/${state}`, () => api.places(state, { level: "county", limit: 500 }));
  const prof = useJson(geoid ? `profile/${state}/${geoid}` : "", () => api.profile(state, geoid));
  const profile = prof.data;
  return html`<main id="main" class="screen screen-page" tabindex="-1">
    <div class="page">
      <header class="page-head page-head-split">
        <div>
          <p class="kicker">Place · ${stateName(state)}</p>
          <h1 class="page-title">${profile ? profile.name : "What is this place like?"}</h1>
          <p class="page-lead">
            ${profile
              ? `${profile.level === "tract" ? "Census tract" : "County"} ${profile.geoid}. A county is compared with the state, a tract with its county. Tap a [number] to see where a fact comes from.`
              : "People, homes, permits and transit for one county, each fact with its source and year."}
          </p>
        </div>
        ${profile &&
        html`<div class="place-actions">
          <button type="button" class="btn btn-quiet" onClick=${shareLink}>
            <${Icon} name="link" /><span>Copy share link</span></button>
          <button type="button" class="btn btn-primary" onClick=${() => window.print()}>
            <${Icon} name="print" /><span>Print / save PDF</span></button>
        </div>`}
      </header>
      <div class="place-tools">
        ${list.error
          ? html`<${Empty} title="Place search is not available">${list.error}.${IS_STATIC
                ? " This site holds only the exported states."
                : html` Start the tool with <code>make app</code> and build the state's data pack.`}<//>`
          : html`<${PlaceSearch} places=${list.data?.places || []} current=${geoid} />`}
        ${geoid && !list.error && html`<${TractPicker} state=${state} county=${county} current=${geoid} />`}
      </div>
      ${!geoid && !list.error && html`<${Empty} title="Pick a county">Type its name above to see its profile.<//>`}
      ${prof.loading && html`<p class="place-hint" role="status">Loading the profile…</p>`}
      ${prof.error && html`<${Empty} title="No profile for this place">${prof.error}<//>`}
      ${profile && html`<${Profile} profile=${profile} />`}
    </div>
  </main>`;
}
