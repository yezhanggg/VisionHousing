/**
 * The shell: one map that fills the screen and stays put, with everything else floating over
 * it in fixed zones, so nothing ever covers anything else.
 *
 *   ┌ brand ┬──────── mode switcher ────────┬ right panel ┐
 *   │ left  │                               │  (results,  │
 *   │ panel │          the map              │  charts,    │
 *   │       │                               │  rankings)  │
 *   ├ chat ─┴──────── map controls ─────────┴─ legend ────┘
 *
 * The zones are one CSS grid (`.hud`, css/shell.css): three columns (left, middle, right)
 * and three rows (top, middle, bottom). The left column holds the brand, the left panel and
 * the question box (ui/ask.js); the right column the right panel and the legend; the middle column the mode
 * switcher at the top and the map controls at the bottom. A panel that is folded into its
 * box, or a right panel made narrower, makes its column narrower, and the map's free area
 * grows. The shell measures the zones and tells the map (`mapInsets`), so the state is fitted
 * inside the free area and a picked county sits in the true middle of the screen.
 *
 * Modes (the routes #/state, #/reform/<id>, #/compare, #/place/<geoid>, #/method) only change
 * what the two panels hold and what the map shades; the map is never drawn again from
 * scratch. Each mode's screen module renders its two panels (`<Dock side="left">` and
 * `<Dock side="right">`) with the map data the shell gives it:
 *
 *   State    left: the scenario            right: results, rankings, drivers, limits, brief
 *   Reform   left: reform picker, funnel   right: drivers, limits, assumptions, evidence
 *   Compare  left: the reforms side by side   right: the package and the table
 *   Place    left: place search and summary   right: the profile with its charts
 *   Method   left: the sections            right: the back-test, sources, ledger
 *
 * The map key can switch what the counties are shaded by (`mapMeasure`): the homes added as a
 * share of each county's baseline (the default), per 1,000 existing homes, the homes added,
 * or evidence coverage (api/decision.py puts each county in its class; the county table in
 * State and Reform is the twin). One palette holds every class (`MAP_COLOURS`).
 *
 * Clicking a county centres it (and opens it in the right panel; on Place it opens its
 * profile). Esc closes it.
 *
 * With the district layer on (ui/districts-layer.js), the districts answer the pointer
 * instead of the counties, "Find my district" takes the county search's place, the map key
 * shows the district classes, and a picked district takes the right panel
 * (ui/district-panel.js) while the mode's own right panel waits, hidden. On a phone the two panels become one bottom sheet; the chat box and
 * the legend sit in a row above the sheet and hide while it is pulled up.
 *
 * The guided tour (ui/tour.js) is mounted here, once, while `tourOpen` is true; on a phone it
 * pulls the sheet up first. On the State screen a first-visit note under the mode switcher
 * offers it (`TourOffer`) until this browser has seen the tour; it never starts by itself.
 */
import { BENCHMARK_IDS, reformById } from "../content/reforms.js";
import { stateName } from "../content/states.js";
import { api } from "../lib/api.js";
import { useJson, useMap } from "../lib/data.js";
import { html, useEffect, useLayoutEffect, useRef, useState } from "../lib/html.js";
import { navigate, route, scenario, startTour, tourOpen } from "../state/store.js";
import { RangeBar, scaleOf, useMedia } from "./bits.js";
import { AskBox } from "./ask.js";
import { Icon } from "./icons.js";
import { ColumnPatch, columnScale, DialHoverLines, DialKey, levelValues, MapColumns, TRACT_LEGEND_ZOOM } from "./columns.js";
import { DistrictFind, DistrictKey, DistrictLayer, DistrictToggle } from "./districts-layer.js";
import { DistrictPanel } from "./district-panel.js";
import { BasemapToggle, camera, CLASS_COLOURS, classesFor, ERROR_COLOURS, MAP_COLOURS, MapLegend, mapInsets, MapView, MEASURE_COLOURS, stateFrame } from "./map.js";
import { FunnelKey, MapFunnelLayer } from "./map-funnel.js";
import { bearing, dialView, districtView, mapFunnel, mapZoom, showRange, view3d, yearView } from "./map-state.js";
import { focusClass, hoveredCounty, hoverPoint, mapMeasure, useHoverFollow } from "./panel.js";
import { usePermitYears, yearCell, yearClasses, YearKey, YearToggle } from "./years.js";
import { StaticNote } from "./static-note.js";
import { markTourSeen, Tour, tourSeen } from "./tour.js";
import { LAW_REFORMS, LawKey, lawClasses, lawView, useLawChanges } from "./law-changes.js";
import { Brand, ModeSwitcher } from "./topbar.js";

/** The scenario the map shades in each mode. */
function mapScenario(name, param, sc) {
  const reform = name === "reform" ? reformById(param) : null;
  if (reform) return { state: sc.state, reforms: [reform.id], overrides: sc.overrides, preset: sc.preset };
  if (name === "compare") return { state: sc.state, reforms: [...BENCHMARK_IDS], overrides: sc.overrides, preset: sc.preset };
  return sc;
}

/**
 * Measure the zones and write the map's free area (`mapInsets`): from the left edge to the
 * left column's right side, and so on. Runs when any zone changes size.
 */
function useInsets(hud) {
  useEffect(() => {
    const box = hud.current;
    if (!box) return undefined;
    let raf = 0;
    const measure = () => {
      raf = 0;
      const W = window.innerWidth;
      const H = window.innerHeight;
      const rect = (sel) => {
        const r = box.querySelector(sel)?.getBoundingClientRect();
        return r && r.width && r.height ? r : null;
      };
      const phone = W < 768;
      if (phone) {
        const top = rect('[data-zone="switch"]');
        const low = rect(".hud-sheet");
        mapInsets.value = { top: top ? Math.round(top.bottom) : 56, bottom: low ? Math.round(H - low.top) : Math.round(H * 0.4), left: 0, right: 0 };
        return;
      }
      const left = [rect('[data-zone="left"]'), rect('[data-zone="brand"]')].filter(Boolean);
      const right = rect('[data-zone="right"]');
      const sw = rect('[data-zone="switch"]');
      const ctrl = rect('[data-zone="controls"]');
      const next = {
        left: left.length ? Math.round(Math.max(...left.map((r) => r.right))) : 0,
        right: right ? Math.round(W - right.left) : 0,
        top: sw ? Math.round(sw.bottom) : 0,
        bottom: ctrl ? Math.round(H - ctrl.top) : 0,
      };
      const was = mapInsets.peek();
      if (!was || Object.keys(next).some((k) => Math.abs(next[k] - was[k]) > 2)) mapInsets.value = next;
    };
    const ask = () => {
      if (!raf) raf = requestAnimationFrame(measure);
    };
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(ask);
    const watch = () => {
      ro?.disconnect();
      box.querySelectorAll("[data-zone], .hud-sheet").forEach((el) => ro?.observe(el));
    };
    watch();
    // Zones come and go (a panel folds into its box): watch the new ones.
    const mo = typeof MutationObserver === "undefined" ? null : new MutationObserver(() => {
      watch();
      ask();
    });
    mo?.observe(box, { childList: true, subtree: true });
    window.addEventListener("resize", ask);
    ask();
    return () => {
      cancelAnimationFrame(raf);
      ro?.disconnect();
      mo?.disconnect();
      window.removeEventListener("resize", ask);
    };
  }, []);
}

/**
 * The hover card: the county pointed at, on the map or in a list, with the engine's likely
 * range for it and a small range bar on the scale of every county. It follows the pointer
 * (or sits on the county's middle when a list row is pointed at) and flips to stay on screen.
 */
function HoverCard({ map, measure = "change", years = null }) {
  const hot = view3d.value ? "" : hoveredCounty.value;
  const card = useRef(null);
  const place = useHoverFollow(card);
  const row = hot && map?.counties?.find((c) => c.fips === hot);
  const any = map?.reforms?.length > 0;
  const field = any ? "added" : "baseline";
  const n = row?.[field];
  const word = row && map.classes.find((c) => c.id === row.class)?.word;
  const [lo, hi] = row ? scaleOf(map.counties.map((c) => c[field])) : [0, 0];
  // A policy dial level on the map (ui/columns.js): the card shows that level's values.
  const dv = dialView.value;
  return html`<div class=${`map-hover ${place.value} ${row ? "is-on" : ""}`} aria-hidden="true" ref=${card}>
    ${row && dv &&
    html`<div class="map-hover-in" key=${`dial-${hot}`}>
      <strong class="map-hover-name">${row.name}</strong>
      <${DialHoverLines} dv=${dv} fips=${hot} />
      <span class="map-hover-hint">Click to centre the county</span>
    </div>`}
    ${row && !dv &&
    html`<div class="map-hover-in" key=${hot}>
      <strong class="map-hover-name">${row.name}</strong>
      ${measure === "coverage" && row.coverage
        ? html`<span class="map-hover-class"><span class="key-swatch" style=${`background: var(${MEASURE_COLOURS[row.coverage.class]})`}></span>Evidence coverage: ${row.coverage.word}</span>
          <span class="map-hover-value">${row.coverage.checks.map((k) => `${k.met ? "✓" : "–"} ${k.label}`).join(" · ")}</span>`
        : any && word && html`<span class="map-hover-class"><span class="key-swatch" style=${`background: var(${CLASS_COLOURS[row.class]})`}></span>${word}</span>`}
      ${years && (() => {
        const yr = years.counties.find((c) => c.fips === hot);
        const c = yr && yearCell(years, yr, yearView.peek().year);
        return c && html`<span class="map-hover-value">${yearView.peek().year}${c.projected ? " (baseline, projected)" : ""}: ${c.units}${c.imputed ? " (estimated)" : ""}</span>
          <span class="map-hover-value">Per 1,000 homes: ${c.rate}</span>`;
      })()}
      ${measure === "per_1000" && row.per_1000?.shown && html`<span class="map-hover-value">Per 1,000 homes: ${row.per_1000.shown.p50}</span>`}
      ${n?.shown && html`<span class="map-hover-value">${any ? "Homes added" : "Baseline homes"}: ${n.shown.p10} to ${n.shown.p90}</span>`}
      ${any && row.no_base && html`<span class="map-hover-value">${row.no_base.text}</span>`}
      ${n?.shown && html`<span class="map-hover-bar"><${RangeBar} label=${row.name} n=${n} lo=${lo} hi=${hi} /></span>`}
      <span class="map-hover-hint">Click to centre the county</span>
    </div>`}
  </div>`;
}

/** Zoom in, zoom out, the whole state, and the basemap switch: the bottom middle of the map. */
function MapControls({ state, counties, onFind, sc }) {
  const tilted = view3d.value && Math.abs(bearing.value) > 3;
  return html`<div class="map-controls" role="group" aria-label="Map controls" data-zone="controls">
    ${districtView.value.on ? html`<${DistrictFind} sc=${sc} />` : html`<${MapFind} counties=${counties} onFind=${onFind} />`}
    <button type="button" class="btn btn-quiet btn-xs btn-icon" onClick=${() => camera.zoomOut()} title="Zoom out">
      <${Icon} name="minimise" size=${14} /><span class="visually-hidden">Zoom out</span></button>
    <button type="button" class="btn btn-quiet btn-xs btn-icon" onClick=${() => camera.zoomIn()} title="Zoom in">
      <${Icon} name="plus" size=${14} /><span class="visually-hidden">Zoom in</span></button>
    <button type="button" class="btn btn-quiet btn-xs" onClick=${() => camera.home()} title="Fit the view to the open county or the whole state">
      <${Icon} name="target" size=${14} /><span>Fit view</span></button>
    <${BasemapToggle} />
    <${DistrictToggle} state=${state} />
    ${tilted &&
    html`<button type="button" class="btn btn-quiet btn-xs map-north" onClick=${() => camera.north()} title="Turn the map so north is up">
      <${Icon} name="north" size=${14} /><span>North up</span></button>`}
  </div>`;
}

/**
 * "Find a county": a search box over the county names (press / to jump to it). Picking a
 * name opens the county, as a click on the map does.
 */
function MapFind({ counties = [], onFind }) {
  const box = useRef(null);
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "/" || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target?.closest?.("input, select, textarea, [contenteditable]")) return;
      e.preventDefault();
      box.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const go = (text) => {
    const want = String(text || "").trim().toLowerCase();
    const hit = want && counties.find((c) => c.name.toLowerCase() === want || c.fips === want);
    if (hit) {
      onFind(hit.fips);
      if (box.current) box.current.value = "";
    }
  };
  return html`<label class="map-find">
    <${Icon} name="search" size=${14} />
    <span class="visually-hidden">Find a county (press / to jump here)</span>
    <input ref=${box} type="search" class="map-find-input" list="map-find-list" placeholder="Find a county  /"
      onChange=${(e) => go(e.currentTarget.value)}
      onKeyDown=${(e) => {
        if (e.key === "Enter") go(e.currentTarget.value);
        if (e.key === "Escape") e.currentTarget.blur();
      }} />
    <datalist id="map-find-list">${counties.map((c) => html`<option value=${c.name} />`)}</datalist>
  </label>`;
}

/**
 * The 2D / 3D switch and, in 3D, "Show range" (the glass caps from P50 to P90). 2D is the
 * default; the choice is per visit.
 */
function ViewSwitch({ years = false }) {
  const on = view3d.value;
  return html`<div class="key-view" role="group" aria-label="Map view">
    <div class="seg-pair" role="group" aria-label="2D or 3D">
      <button type="button" class="seg seg-xs" aria-pressed=${on ? "false" : "true"} onClick=${() => (view3d.value = false)}>2D</button>
      <button type="button" class="seg seg-xs" aria-pressed=${on ? "true" : "false"} disabled=${years}
        title=${years ? "The 3D view shows the scenario; switch the year slider off first" : "Columns: homes added per county and tract"}
        onClick=${() => {
          yearView.value = { ...yearView.peek(), on: false };
          view3d.value = true;
        }}><${Icon} name="cube" size=${13} /> 3D</button>
    </div>
    ${on &&
    html`<button type="button" class="btn btn-quiet btn-xs" aria-pressed=${showRange.value ? "true" : "false"}
        onClick=${() => (showRange.value = !showRange.peek())} title="Show each column's likely range as a glass cap (P50 to P90)">Show range</button>`}
    <${YearToggle} />
  </div>`;
}

/**
 * Step through the counties by rank with Left / Right while the map has focus (the hover card
 * follows); Enter opens the county; Esc lets go.
 */
function useRankKeys(map, pick, say) {
  useEffect(() => {
    const onKey = (e) => {
      if (!e.target?.closest?.(".map-canvas") || e.altKey || e.metaKey || e.ctrlKey || e.shiftKey) return;
      const rows = map?.counties || [];
      if (!rows.length) return;
      const any = (map.reforms?.length || 0) > 0;
      const ranked = [...rows].sort((a, b) => ((any ? b.added?.p50 : b.baseline?.p50) ?? -1) - ((any ? a.added?.p50 : a.baseline?.p50) ?? -1));
      const at = ranked.findIndex((c) => c.fips === hoveredCounty.peek());
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        e.stopPropagation();
        const next = e.key === "ArrowRight" ? (at + 1) % ranked.length : at <= 0 ? ranked.length - 1 : at - 1;
        const row = ranked[next];
        hoveredCounty.value = row.fips;
        // Say the county reached (the hover card is drawing only): its name and the engine's
        // likely range, once per key press.
        const n = any ? row.added : row.baseline;
        say(`${row.name}, ${next + 1} of ${ranked.length}${n?.shown ? `. ${any ? "Homes added" : "Baseline homes"}: ${n.shown.p10} to ${n.shown.p90}` : ""}. Enter opens it.`);
      } else if (e.key === "Enter" && at >= 0) {
        e.preventDefault();
        pick(ranked[at].fips);
      } else if (e.key === "Escape" && hoveredCounty.peek()) {
        hoveredCounty.value = "";
      }
    };
    // Capture: the map's own keys (arrows pan) come after this.
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [map, pick, say]);
}

/**
 * The keyboard path through the floating zones, in reading order: brand, mode switcher, left
 * panel, the map, the map controls, right panel, legend, chat. Tab moves as usual inside a
 * zone; at a zone's last stop it goes to the next zone's first (Shift+Tab: back). The DOM
 * order alone cannot give this, because the map stays mounted outside the panels. Phones
 * keep the DOM order (the panels are one sheet there).
 */
const ZONES = [".hud-left-col", ".hud-top", '[data-zone="left"]', ".map-canvas", ".hud-controls", '[data-zone="right"]', ".hud-legend", ".hud-chat"];
const TABBABLE = 'a[href], button:not(:disabled), input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]';

function tabbables(zone) {
  return [...zone.querySelectorAll(TABBABLE)].filter((el) => {
    if (el.tabIndex < 0 || !el.getClientRects().length || getComputedStyle(el).visibility === "hidden") return false;
    if (el.closest("[inert], [aria-hidden='true']")) return false;
    // A radio group is one stop: its checked button.
    if (el.type === "radio" && !el.checked && el.name && zone.querySelector(`input[type="radio"][name="${CSS.escape(el.name)}"]:checked`)) return false;
    return true;
  });
}

function useZoneTab(on) {
  useEffect(() => {
    if (!on) return undefined;
    const onKey = (e) => {
      if (e.key !== "Tab" || e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const shell = document.querySelector(".shell:not([hidden])");
      if (!shell) return;
      const zones = ZONES.map((sel) => shell.querySelector(sel));
      const at = document.activeElement;
      const back = e.shiftKey;
      const go = (from) => {
        for (let k = from; k >= 0 && k < zones.length; k += back ? -1 : 1) {
          const list = zones[k] ? tabbables(zones[k]) : [];
          if (list.length) {
            e.preventDefault();
            (back ? list[list.length - 1] : list[0]).focus();
            return true;
          }
        }
        return false;
      };
      // From the skip link (before the map in the page) straight to the first zone.
      if (at?.classList?.contains("skip-link")) {
        if (!back) go(0);
        return;
      }
      const zi = zones.findIndex((z) => z && z.contains(at));
      if (zi < 0) return;
      const step = back ? Node.DOCUMENT_POSITION_PRECEDING : Node.DOCUMENT_POSITION_FOLLOWING;
      if (tabbables(zones[zi]).some((el) => el !== at && !el.contains(at) && at.compareDocumentPosition(el) & step)) return;
      if (go(zi + (back ? -1 : 1))) return;
      if (back) {
        const skip = document.querySelector(".skip-link");
        if (skip) {
          e.preventDefault();
          skip.focus();
        }
      }
      // Forward past the last zone (the chat box, last in the page): the browser leaves the page.
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [on]);
}

/**
 * The phone sheet's handle: a tap opens or closes the sheet; a drag moves it with the finger
 * and, let go, it settles open (dragged up) or closed (dragged down). Returns the handle's
 * pointer and click handlers.
 */
function useSheetDrag(hud, sheet, setSheet) {
  const drag = useRef(null);
  const dragged = useRef(false);
  const el = () => hud.current?.querySelector(".hud-sheet");
  return {
    onPointerDown: (e) => {
      if (e.button !== 0 || !el()) return;
      drag.current = { y: e.clientY, h: el().getBoundingClientRect().height, moved: false };
      e.currentTarget.setPointerCapture?.(e.pointerId);
    },
    onPointerMove: (e) => {
      const d = drag.current;
      const box = el();
      if (!d || !box) return;
      const dy = e.clientY - d.y;
      if (Math.abs(dy) > 6) d.moved = true;
      if (!d.moved) return;
      box.style.transition = "none";
      box.style.height = `${Math.round(Math.max(96, Math.min(window.innerHeight - 64, d.h - dy)))}px`;
    },
    onPointerUp: (e) => {
      const d = drag.current;
      drag.current = null;
      if (!d?.moved) return;
      const box = el();
      if (box) {
        box.style.transition = "";
        box.style.height = "";
      }
      dragged.current = true;
      const dy = e.clientY - d.y;
      if (dy < -40) setSheet(true);
      else if (dy > 40) setSheet(false);
    },
    onPointerCancel: () => {
      drag.current = null;
      const box = el();
      if (box) {
        box.style.transition = "";
        box.style.height = "";
      }
    },
    onClick: () => {
      // The click that ends a drag is not a tap.
      if (dragged.current) {
        dragged.current = false;
        return;
      }
      setSheet(!sheet);
    },
  };
}

/**
 * The shell. `modes` maps a route name to its screen component; each is given
 * {map, picked, pick, table, setTable}. `children` is drawn instead of the panels (the slides
 * view hides the shell, which stays mounted so the map is not drawn again).
 */
export function Shell({ modes, hidden = false }) {
  const r = route.value;
  const name = modes[r.name] ? r.name : "state";
  const sc = scenario.value;
  const phone = useMedia("(max-width: 767px)");
  const [picked, setPicked] = useState("");
  const [table, setTable] = useState(false);
  const [sheet, setSheet] = useState(false);
  const [said, say] = useState("");
  const hud = useRef(null);
  useInsets(hud);
  useZoneTab(!phone && !hidden);
  const sheetDrag = useSheetDrag(hud, sheet, setSheet);

  const mapSc = mapScenario(name, r.param, sc);
  const map = useMap(mapSc);
  const outlines = useJson(`outlines:${sc.state}`, () => api.outlines(sc.state));
  // Method & data shades the map by the baseline's back-test error instead.
  const val = useJson(name === "method" ? `validation:${sc.state}` : "", () => api.validation(sc.state));
  const bp = name === "method" ? val.data?.by_place : null;
  // Place: the place in the address is the one picked; a tract eases to its own point.
  const placeId = name === "place" ? r.param : "";
  const points = useJson(placeId.length === 11 ? `tract-points:${sc.state}` : "", () => api.tractPoints(sc.state));
  const focusPoint = (() => {
    const p = points.data;
    if (!p || placeId.length !== 11) return null;
    const i = (p.geoids || []).indexOf(placeId);
    if (i < 0) return null;
    const lon = p.lon?.[i] ?? p.points?.[i]?.[0];
    const lat = p.lat?.[i] ?? p.points?.[i]?.[1];
    return Number.isFinite(lon) && Number.isFinite(lat) ? [lon, lat] : null;
  })();
  const selected = name === "place" ? placeId.slice(0, 5) : picked;
  // The 3D view's tract columns: the points once per state, the values per scenario.
  const three = view3d.value && !bp;
  const pts3 = useJson(three ? `tract-points:${sc.state}` : "", () => api.tractPoints(sc.state));
  const tractsOn = three && (mapSc.reforms?.length || 0) > 0;
  const tr3 = useJson(tractsOn ? `tracts:${sc.state}:${JSON.stringify(mapSc.reforms)}:${JSON.stringify(mapSc.overrides || {})}` : "",
    () => api.stateTracts(sc.state, mapSc.reforms, mapSc.overrides));
  const yearsOn = yearView.value.on && !bp;
  const years = usePermitYears(sc.state, yearsOn);

  useEffect(() => setPicked(""), [sc.state, name]);
  useEffect(() => setTable(false), [name]);
  const any = (map.data?.reforms?.length || 0) > 0;
  useEffect(() => {
    if (!any || name === "method") focusClass.value = "";
  }, [any, name]);
  // Esc closes the open county (a popover or menu that is open takes Esc first).
  useEffect(() => {
    if (!picked) return undefined;
    const onKey = (e) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (e.target?.closest?.("input, select, textarea, [role=menu], .pop-panel")) return;
      setPicked("");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [picked]);
  useEffect(() => () => {
    hoveredCounty.value = "";
    hoverPoint.value = null;
  }, []);
  // A picked county on a phone pulls the sheet up so its panel shows.
  useLayoutEffect(() => {
    if (picked && phone) setSheet(true);
  }, [picked]);
  // The tour on a phone pulls the sheet up too, so the panels it points at are on screen.
  const touring = tourOpen.value && !hidden;
  useLayoutEffect(() => {
    if (touring && phone) setSheet(true);
  }, [touring]);

  const pick = (fips) => {
    if (name === "place") navigate("place", fips);
    else {
      setPicked(fips);
      setTable(false);
    }
  };
  const measure = mapMeasure.value;
  // "What this law changes" (Reform screen, ui/law-changes.js) shades the four answers.
  const lawOn = name === "reform" && lawView.value.on && LAW_REFORMS.includes(r.param) && !bp && !yearsOn && !districtView.value.on && !dialView.value;
  const law = useLawChanges(sc.state, r.param, lawOn);
  const classes = bp
    ? Object.fromEntries(bp.counties.map((c) => [c.county, c.cls]))
    : lawOn
      ? lawClasses(law.data)
    : yearsOn && years.data
      ? yearClasses(years.data, yearView.value.year || years.data.latest)
      : classesFor(map.data, measure === "coverage" || any ? measure : "change");
  const funnelOn = Boolean(mapFunnel.value.reform) && !bp && !yearsOn && !lawOn;
  useRankKeys(map.data, (f) => pick(f), say);
  const tableModes = name === "state" || name === "reform";
  const Mode = modes[name];
  const legend = bp
    ? html`<${MapLegend} title="Back-test: the baseline's typical error by county" classes=${bp.map_classes}
        palette=${ERROR_COLOURS} grey=${[["is-nodata", "No back-test case"]]} />`
    : html`<${MapLegend} map=${map.data} onTable=${tableModes ? () => {
        setTable(!table);
        setPicked("");
        if (phone) setSheet(true);
      } : null} tableOpen=${table} measure=${measure} onMeasure=${(m) => (mapMeasure.value = m)}
        top=${html`<${ViewSwitch} years=${yearsOn} />`}
        grey=${lawOn ? [] : null}
        body=${lawOn ? html`<${LawKey} state=${sc.state} reform=${r.param} />` : dialView.value ? html`<${DialKey} dv=${dialView.value} />` : districtView.value.on && !yearsOn ? html`<${DistrictKey} sc=${mapSc} />` : yearsOn ? html`<${YearKey} state=${sc.state} />` : funnelOn && !view3d.value ? html`<${FunnelKey} state=${sc.state} overrides=${mapSc.overrides} />` : null}
        title=${view3d.value ? (any ? `Homes added per ${mapZoom.value >= TRACT_LEGEND_ZOOM && tr3.data ? "tract" : "county"} (column height; colour: class)` : "Baseline homes per county (column height)") : ""}>
        ${view3d.value && !yearsOn && !districtView.value.on && !dialView.value && html`<${ColumnLegend} map=${map.data} classes=${classes} tracts=${tr3.data} />`}
      <//>`;
  const place = stateName(sc.state);
  // A picked district takes the right panel (ui/district-panel.js); the mode's own right panel waits.
  const dv = districtView.value;
  const districtOpen = !bp && dv.on && Boolean(dv.picked);
  useLayoutEffect(() => {
    if (districtOpen && phone) setSheet(true);
  }, [districtOpen]);
  return html`<div class=${`shell mode-${name}`} hidden=${hidden}>
    <h1 class="visually-hidden">${place}</h1>
    <${MapView} stateName=${place} outlines=${outlines.data} classes=${classes} palette=${MAP_COLOURS}
      selected=${selected} onSelect=${pick} focusPoint=${focusPoint}
      label=${bp ? `Back-test error map of ${place}. The county error table in the right panel has the same values.` : lawOn ? `Map of what this law changes in ${place}. The table in the right panel (open it from the map key) has the same values.` : ""} />
    ${!bp && html`<${HoverCard} map=${map.data} measure=${measure} years=${yearsOn ? years.data : null} />`}
    ${!bp && html`<${MapColumns} state=${sc.state} outlines=${outlines.data} mapReply=${map.data} classes=${classesFor(map.data, measure === "coverage" || any ? measure : "change")}
      tracts=${tractsOn ? tr3.data : null} points=${pts3.data} onPick=${(f) => !districtView.peek().on && pick(f)} frameState=${() => (selected ? null : stateFrame(outlines.data))} />`}
    ${!bp && html`<${MapFunnelLayer} sc=${mapSc} picked=${selected} outlines=${outlines.data} />`}
    ${!bp && html`<${DistrictLayer} sc=${mapSc} />`}
    <main id=${hidden ? undefined : "main"} tabindex="-1" class=${`hud ${sheet ? "sheet-up" : ""} ${districtOpen ? "has-district" : ""}`} ref=${hud}>
      <div class="hud-left-col">
        <${Brand} />
      </div>
      <div class="hud-top">
        <${ModeSwitcher} />
        <${StaticNote} />
        ${name === "state" && !hidden && html`<${TourOffer} />`}
      </div>
      <div class="hud-sheet">
        ${phone &&
        html`<button type="button" class="sheet-handle" aria-expanded=${sheet ? "true" : "false"} ...${sheetDrag}>
          <span class="sheet-grip" aria-hidden="true"></span>
          <span>${sheet ? "Show the map" : "Show all controls"}</span>
        </button>`}
        ${districtOpen && html`<${DistrictPanel} sc=${mapSc} />`}
        <div class="hud-mode" key=${name}>
          <${Mode} map=${map} mapScenario=${mapSc} picked=${picked} pick=${pick} close=${() => setPicked("")}
            table=${table} setTable=${setTable} validation=${val} />
        </div>
      </div>
      <div class="hud-controls"><${MapControls} state=${sc.state} sc=${mapSc} counties=${map.data?.counties || []} onFind=${(f) => {
        hoveredCounty.value = f;
        pick(f);
      }} /></div>
      <div class="hud-legend">${legend}</div>
      <div class="hud-chat"><${AskBox} county=${selected} /></div>
      <p class="visually-hidden" role="status">${said}</p>
      ${touring && html`<${Tour} onClose=${() => { tourOpen.value = false; }} />`}
    </main>
  </div>`;
}

/**
 * The first-visit offer: a small note under the mode switcher, "New here? Take the tour",
 * shown on the State screen until this browser has seen the tour (the tour's own flag,
 * `tourSeen()`). It never opens the tour by itself. "Not now" sets the same flag, so it
 * shows once. Starting from here puts focus on the left panel's "Take the tour" button
 * first, so focus lands there when the tour closes.
 */
function TourOffer() {
  const [gone, setGone] = useState(tourSeen);
  if (gone || tourOpen.value) return null;
  const take = () => {
    document.querySelector('[data-zone="left"] .tour-btn')?.focus({ preventScroll: true });
    setGone(true);
    startTour();
  };
  const dismiss = () => {
    markTourSeen();
    setGone(true);
  };
  return html`<aside class="tour-offer" aria-label="Guided tour">
    <span class="tour-offer-text">New here? A short tour shows how to read the results.</span>
    <button type="button" class="btn btn-primary btn-xs tour-offer-take" onClick=${take}>Take the tour</button>
    <button type="button" class="btn btn-ghost btn-xs tour-offer-close" onClick=${dismiss}>
      <${Icon} name="close" size=${14} /><span class="visually-hidden">Not now</span>
    </button>
  </aside>`;
}

/**
 * The legend's 3D part: the column patch at the current scale for the level in view (tracts
 * from zoom 8.2 when the scenario has tract values) and a note on what the tract level leaves
 * out.
 */
function ColumnLegend({ map, classes, tracts }) {
  const values = levelValues(map, classes || {}, tracts);
  void columnScale.value;
  const hasTracts = Object.keys(values.tract).length > 0;
  const only = tracts?.county_only && Object.keys(tracts.county_only).length > 0;
  const level = hasTracts && mapZoom.value >= TRACT_LEGEND_ZOOM ? "tract" : "county";
  return html`<div class="col-legend" data-level=${level}>
    <${ColumnPatch} mapReply=${map} values=${values} level=${level} />
    ${hasTracts && level === "county" && html`<p class="key-hint">Zoom in past the county columns to see one column per census tract.</p>`}
    ${level === "tract" && html`<p class="key-hint">One column per census tract (the tract-level reforms). Tract middles do not add up to the county's.</p>`}
    ${only && html`<p class="key-hint">Fees and parking work by county: the tract columns show the other reforms only.</p>`}
  </div>`;
}
