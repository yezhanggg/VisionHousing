/**
 * Mode "Place": a plain profile of one county (or one census tract) for market research.
 *   left panel   search a county, pick a tract, and the place in brief: a compact grid of
 *                key figures, one tile per section (its first fact, the profile's own text)
 *   right panel  the profile: how the county ranks among the state's counties, then each
 *                section led by its headline and up to three summary points, its charts
 *                (ui/charts.js, from the API's `charts` list), and its facts folded below
 * Clicking a county on the map opens its profile; the place is centred on the map.
 *
 *   #/place?v=1&s=PA            search a county by name
 *   #/place/42003?v=1&s=PA      that county's profile
 *   #/place/42003020100?v=1&s=PA one tract's profile (picked from its county)
 *
 * A section with no data for the place is not drawn as an empty block: it is listed under
 * "Not measured for this place" with the source it is built from and what that source covers.
 *
 * Each section is a card: the one-sentence summary on top, the facts below. Every fact shows
 * its source on hover (title) or tap (a short note under it), and a numbered footnote that
 * matches the source list at the end. Nothing here computes a number: the text comes from
 * the profile endpoint, through lib/api.js (the back end, or the exported files on the static
 * site). "Share link" (with Slides and Export, ui/actions.js) copies the address, which holds
 * the state and the place; "Print / save PDF" prints the page with the print rules in app.css.
 */
import printCss from "../../css/place-print.css?url";
import { stateName } from "../content/states.js";
import { api, IS_STATIC } from "../lib/api.js";
import { html, useEffect, useState } from "../lib/html.js";
import { navigate, route, scenario } from "../state/store.js";
import { BENCHMARK_IDS } from "../content/reforms.js";
import { ConfidencePanel } from "../ui/confidence.js";
import { ImplementationRows } from "../ui/implementation.js";
import { Empty } from "../ui/bits.js";
import { ProfileChart, RankRows } from "../ui/charts.js";
import { Dock } from "../ui/dock.js";
import { Icon } from "../ui/icons.js";
import { reducedMotion } from "../ui/motion.js";
import { ScenarioActions } from "../ui/actions.js";
import "../../css/baseline-card.css";

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

/** A section's summary: up to three points (`summary_points`) or its one sentence. */
function Summary({ summary, points }) {
  if (Array.isArray(points) && points.length) {
    return html`<ul class="place-points">${points.slice(0, 3).map((t) => html`<li>${typeof t === "string" ? t : t.text}</li>`)}</ul>`;
  }
  if (Array.isArray(summary)) {
    return html`<ul class="place-points">${summary.slice(0, 3).map((t) => html`<li>${typeof t === "string" ? t : t.text}</li>`)}</ul>`;
  }
  return summary ? html`<p class="place-summary">${summary}</p>` : null;
}

/** A section's one-line headline, when the API sends one. */
function headlineOf(section) {
  const h = section.headline;
  if (!h) return "";
  if (typeof h === "string") return h;
  const words = h.words ? ` (${h.words})` : "";
  return h.label ? `${h.label}: ${h.text}${words}` : `${h.text || ""}${words}`;
}

function SectionCard({ section, notes, sources }) {
  const head = headlineOf(section);
  const charts = section.charts || [];
  return html`<section class="card place-card" id=${`sec-${section.key}`} aria-labelledby=${`sec-${section.key}-title`}>
    <header class="card-head"><div>
      <h3 class="card-title" id=${`sec-${section.key}-title`}>${section.title}</h3>
      ${head && html`<p class="place-headline">${head}</p>`}
    </div></header>
    <div class="card-body">
      <${Summary} summary=${section.summary} points=${section.summary_points} />
      ${charts.length > 0 && html`<div class="place-charts">${charts.map((c) => html`<${ProfileChart} key=${c.id} chart=${c} sources=${sources} />`)}</div>`}
      <details class="place-facts" open=${charts.length === 0}>
        <summary>All facts (${section.facts.length})</summary>
        <dl class="facts">
          ${section.facts.map((f) => html`<${Fact} fact=${f} notes=${notes} sources=${sources} />`)}
        </dl>
        ${section.limits && html`<p class="place-limits"><strong>What this cannot tell you:</strong> ${section.limits}</p>`}
      </details>
    </div>
  </section>`;
}

/** How the place ranks among the state's counties (the API's `ranks`), if it sent them. */
function Ranks({ ranks }) {
  const list = Array.isArray(ranks) ? ranks : ranks?.items || [];
  if (!list.length) return null;
  const items = list.map((r) => ({
    label: r.label || r.measure || r.title || "",
    rank_text: r.rank_text || r.text_rank || "",
    rank: r.rank,
    of: r.of || r.count,
    direction: r.direction,
    value_text: r.text,
    words: r.words,
    note: r.note,
  }));
  return html`<section class="card place-card place-ranks" id="sec-ranks" aria-labelledby="sec-ranks-title">
    <header class="card-head"><div><h3 class="card-title" id="sec-ranks-title">Among the state's counties</h3></div></header>
    <div class="card-body"><${RankRows} title="Ranks among counties" items=${items} /></div>
  </section>`;
}

/** A section with nothing to show: not built, no data, or built with no facts and no charts. */
function isEmpty(s) {
  return s.status !== "ok" || (!(s.facts || []).length && !(s.charts || []).length);
}

let coverageTable = null; // web/js/content/coverage.json, loaded once when first needed
function useCoverage(on) {
  const [table, setTable] = useState(null);
  useEffect(() => {
    if (!on) return;
    coverageTable ||= import("../content/coverage.json").then((m) => m.default || m);
    coverageTable.then(setTable).catch(() => setTable({ sections: {} }));
  }, [on]);
  return table;
}

/**
 * The sections with nothing to show, each with what it would be built from and what those
 * sources cover (generated from the source registry by src/visionhousing/profile/coverage.py),
 * so "not measured here" is never read as zero.
 */
function NotMeasured({ sections }) {
  const table = useCoverage(sections.length > 0);
  if (!sections.length) return null;
  return html`<section class="card place-card place-later" id="sec-later" aria-labelledby="sec-later-title"
      data-testid="not-measured">
    <header class="card-head"><div>
      <h3 class="card-title" id="sec-later-title">Not measured for this place (${sections.length})</h3>
      <p class="card-sub">These sections have no data here. Each says which source it is built from and what that source covers.</p>
    </div></header>
    <div class="card-body">
      <ul class="coverage-list">
        ${sections.map((s) => {
          const c = table?.sections?.[s.key];
          const why = s.limits || (Array.isArray(s.summary) ? s.summary.join(" ") : s.summary);
          return html`<li class="coverage-item" data-section=${s.key}>
            <h4>${s.title}</h4>
            ${why && html`<p>${why}</p>`}
            ${c ? html`<p data-testid="coverage-text">${c.text}</p>
                ${c.sources.length > 0 && html`<ul class="coverage-sources" aria-label=${`Sources for ${s.title}`}>
                  ${c.sources.map((x) => html`<li><code>${x.id}</code>: ${x.covers}</li>`)}
                </ul>`}`
              : table && html`<p>No source is registered for this section yet.</p>`}
          </li>`;
        })}
      </ul>
    </div>
  </section>`;
}

function Profile({ profile }) {
  const notes = {};
  Object.keys(profile.sources).forEach((id, i) => {
    notes[id] = i + 1;
  });
  const ready = profile.sections.filter((s) => !isEmpty(s));
  const later = profile.sections.filter(isEmpty);
  return html`<div class="place-profile">
    ${profile.synthetic &&
    html`<p class="place-banner">Synthetic test state: these numbers are made up for testing and are not real data.</p>`}
    <${Ranks} ranks=${profile.ranks} />
    <div class="place-grid">
      ${ready.map((s) => html`<${SectionCard} section=${s} notes=${notes} sources=${profile.sources} />`)}
    </div>
    <${NotMeasured} sections=${later} />
    <section class="card place-card" id="sec-sources" aria-labelledby="sec-sources-title">
      <header class="card-head"><div><h3 class="card-title" id="sec-sources-title">Sources</h3></div></header>
      <div class="card-body">
        <ol class="place-sources">
          ${Object.entries(profile.sources).map(
            ([id, src]) => html`<li>${src.name} (${src.publisher}), ${src.vintage}. <code>${id}</code></li>`,
          )}
        </ol>
        ${profile.pack_hash && html`<p class="place-hint">Data pack ${profile.pack_hash}</p>`}
      </div>
    </section>
  </div>`;
}

/**
 * The place in brief, for the left panel: a compact grid of key figures, one tile per ready
 * section (its first fact: the API's text and label). Pressing a tile scrolls the profile to
 * that section.
 */
function Brief({ profile }) {
  const ready = profile.sections.filter((s) => !isEmpty(s) && s.facts?.length);
  return html`<ul class="key-grid" aria-label="Key figures">
    ${ready.map((s, i) => {
      const f = s.facts[0];
      return html`<li class="key-tile stagger" style=${`--i: ${i}`}>
        <button type="button" class="key-tile-btn" title=${headlineOf(s) || s.title} onClick=${() => {
          const el = document.getElementById(`sec-${s.key}`);
          el?.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
          el?.querySelector("h3")?.setAttribute("tabindex", "-1");
          el?.querySelector("h3")?.focus({ preventScroll: true });
        }}>
          <span class="key-tile-kicker">${s.title}</span>
          <strong class="key-tile-value">${f.text}</strong>
          <span class="key-tile-label">${f.label}</span>
        </button>
      </li>`;
    })}
  </ul>`;
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

export function PlaceMode() {
  usePrintPage();
  const state = scenario.value.state;
  const geoid = route.value.param;
  const county = geoid.slice(0, 5);
  const list = useJson(`counties/${state}`, () => api.places(state, { level: "county", limit: 500 }));
  const prof = useJson(geoid ? `profile/${state}/${geoid}` : "", () => api.profile(state, geoid));
  const profile = prof.data;
  const people = profile?.sections?.find((s) => s.key === "people")?.facts?.[0];
  const actions = html`${profile &&
    html`<button type="button" class="btn btn-quiet btn-xs" onClick=${() => window.print()}>
      <${Icon} name="print" size=${15} /><span>Print / save PDF</span></button>`}
    <${ScenarioActions} compact=${true} label="Slides, export and share" />`;
  return html`<${Dock} side="left" mode="place" title=${profile ? profile.name : "What is this place like?"} icon="pin"
      sub=${profile
        ? `${profile.level === "tract" ? "Census tract" : "County"} ${profile.geoid} · ${stateName(state)}`
        : `People, homes, permits and transit · ${stateName(state)}`}
      chip=${profile ? (people ? `${people.text} ${people.label.toLowerCase()}` : profile.geoid) : "Search"} busy=${prof.loading}>
      <div class="place-tools">
        ${list.error
          ? html`<${Empty} title="Place search is not available">${list.error}.${IS_STATIC
                ? " This site holds only the exported states."
                : html` Start the tool with <code>make app</code> and build the state's data pack.`}<//>`
          : html`<${PlaceSearch} places=${list.data?.places || []} current=${geoid} />`}
        ${geoid && !list.error && html`<${TractPicker} state=${state} county=${county} current=${geoid} />`}
      </div>
      ${!geoid && !list.error && html`<${Empty} title="Pick a county">Type its name above, or click a county on the map.<//>`}
      ${profile && html`<p class="card-sub">A county is compared with the state, a tract with its county. Tap a [number] to see where a fact comes from.</p>
        <${Brief} profile=${profile} />`}
    <//>
    <${Dock} side="right" mode=${`place-${geoid}`} title="Profile" icon="chart"
      sub=${profile ? "Ranks, charts and facts, each with its source" : "Pick a place to see its profile"}
      chip=${profile ? profile.name : ""} busy=${prof.loading} actions=${actions}>
      ${!geoid && html`<${Empty} title="No place picked yet">Search on the left or click a county on the map.<//>`}
      ${prof.loading && html`<p class="place-hint" role="status">Loading the profile…</p>`}
      ${prof.error && html`<${Empty} title="No profile for this place">${prof.error}<//>`}
      ${profile && html`<${Profile} profile=${profile} />`}
      ${profile && (() => {
        const on = scenario.value.reforms.filter((k) => BENCHMARK_IDS.includes(k));
        return html`${(on.length ? on : BENCHMARK_IDS).map((k) => html`<${ImplementationRows} key=${k} state=${state} geoid=${geoid.slice(0, 5)} reform=${k} h=${3} />`)}
          <${ConfidencePanel} state=${state} h=${3} />`;
      })()}
    <//>`;
}
