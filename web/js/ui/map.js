/**
 * The county map and its key.
 *
 * The map is drawn by MapLibre GL (BSD-3), bundled by Vite and loaded only when the map is
 * first shown. MapLibre draws in a Web Worker; its worker file is copied into the build as is
 * and named with `setWorkerUrl`, so it loads from this site. The map has no basemap and calls
 * no tile server: the county outlines come from the state pack (GET /states/{st}/outlines).
 *
 * Each county is filled by its class from the engine's map reply (GET /states/{st}/map):
 * "Little effect", "Adds some" or "Adds a lot", the verdict rule of the Assumption Ledger
 * applied to the county's added homes as a share of its own baseline. The breaks are fixed,
 * so colours mean the same thing whatever is switched on. Grey is "no data".
 * Clicking a county (or pressing Enter on its row in the table) opens the county panel.
 * If the browser cannot draw WebGL, the page says so and the table still works.
 */
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { html, useEffect, useRef, useState } from "../lib/html.js";

const MESSAGES = {
  loading: "Loading the map…",
  missing: "The map library did not load. The county table below has the same values.",
  nogl: "This browser cannot draw the map. The county table has the same values.",
  noshapes: "No county outlines for this state. The county table has the values.",
};

/** Class id to the colour token it is filled with. */
export const CLASS_COLOURS = {
  none: "--not-modelled",
  nodata: "--no-data",
  little: "--seq-2",
  some: "--seq-4",
  a_lot: "--seq-6",
};

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#cccccc";
}

/** The bounding box of a feature collection, for fitting the view. */
function bounds(fc) {
  let w = 180;
  let s = 90;
  let e = -180;
  let n = -90;
  const walk = (c) => {
    if (typeof c[0] === "number") {
      w = Math.min(w, c[0]);
      e = Math.max(e, c[0]);
      s = Math.min(s, c[1]);
      n = Math.max(n, c[1]);
    } else c.forEach(walk);
  };
  fc.features.forEach((f) => f.geometry && walk(f.geometry.coordinates));
  return w <= e ? [[w, s], [e, n]] : null;
}

/** The outlines with each county's class and name joined in (no numbers are added). */
function joined(outlines, classes) {
  return {
    type: "FeatureCollection",
    features: (outlines?.features || []).map((f) => ({
      ...f,
      properties: {
        ...f.properties,
        cls: classes[String(f.properties?.geoid)] || "nodata",
      },
    })),
  };
}

export function MapView({ stateName, outlines, classes = {}, selected = "", onSelect }) {
  const box = useRef(null);
  const mapRef = useRef(null);
  const pick = useRef(onSelect);
  pick.current = onSelect;
  const [status, setStatus] = useState("loading");
  const hasShapes = Boolean(outlines?.features?.length);

  useEffect(() => {
    let map = null;
    let gone = false;
    import("maplibre-gl")
      .then(({ Map, setWorkerUrl }) => {
        if (gone || !box.current) return;
        setWorkerUrl(workerUrl);
        try {
          map = new Map({
            container: box.current,
            style: {
              version: 8,
              sources: {},
              layers: [{ id: "background", type: "background", paint: { "background-opacity": 0 } }],
            },
            center: [-77.6, 40.9],
            zoom: 6,
            attributionControl: false,
            dragRotate: false,
            pitchWithRotate: false,
          });
          map.on("error", () => setStatus("nogl"));
          map.on("load", () => {
            const fill = ["match", ["get", "cls"]];
            Object.entries(CLASS_COLOURS).forEach(([k, v]) => fill.push(k, token(v)));
            fill.push(token("--no-data"));
            map.addSource("counties", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
            map.addLayer({ id: "fill", type: "fill", source: "counties", paint: { "fill-color": fill, "fill-opacity": 0.92 } });
            map.addLayer({
              id: "line",
              type: "line",
              source: "counties",
              paint: { "line-color": token("--surface"), "line-width": 1 },
            });
            map.addLayer({
              id: "picked",
              type: "line",
              source: "counties",
              filter: ["==", ["get", "geoid"], ""],
              paint: { "line-color": token("--text"), "line-width": 2.5 },
            });
            map.on("click", "fill", (e) => {
              const id = e.features?.[0]?.properties?.geoid;
              if (id) pick.current?.(String(id));
            });
            map.on("mouseenter", "fill", () => {
              map.getCanvas().style.cursor = "pointer";
            });
            map.on("mouseleave", "fill", () => {
              map.getCanvas().style.cursor = "";
            });
            mapRef.current = map;
            setStatus("ready");
          });
        } catch {
          setStatus("nogl");
        }
      })
      .catch(() => setStatus("missing"));
    return () => {
      gone = true;
      mapRef.current = null;
      if (map) map.remove();
    };
  }, []);

  // New outlines: fit the view to them.
  useEffect(() => {
    const map = mapRef.current;
    if (status !== "ready" || !map || !hasShapes) return;
    const b = bounds(outlines);
    // Keep the state clear of the floating cards (wide screens) or the bottom sheet (phones).
    const wide = window.innerWidth >= 768;
    const side = wide ? Math.min(400, window.innerWidth * 0.28) : 16;
    const padding = wide
      ? { top: 40, bottom: 170, left: side, right: side }
      : { top: 24, bottom: Math.round(window.innerHeight * 0.55), left: side, right: side };
    if (b) map.fitBounds(b, { padding, animate: false });
  }, [status, outlines]);

  // New classes: redraw the fills.
  useEffect(() => {
    const map = mapRef.current;
    if (status !== "ready" || !map) return;
    map.getSource("counties")?.setData(joined(outlines, classes));
  }, [status, outlines, classes]);

  useEffect(() => {
    const map = mapRef.current;
    if (status !== "ready" || !map) return;
    map.setFilter("picked", ["==", ["get", "geoid"], selected || ""]);
  }, [status, selected]);

  const shown = status === "ready" && !hasShapes ? "noshapes" : status;
  const message = shown === "ready" ? "" : MESSAGES[shown];
  return html`<div class="map-area" data-map-status=${shown}>
    <div class="map-canvas" ref=${box} role="region"
      aria-label=${`County map of ${stateName}. The county table has the same values.`}></div>
    ${message &&
    html`<div class="map-empty" role="note"><p class="map-empty-title">${message}</p></div>`}
  </div>`;
}

/**
 * The map key: the three classes with the engine's labels, then grey for "no data".
 * With no reform switched on there is nothing to shade, and the key says so.
 */
export function MapKey({ map, onTable, tableOpen }) {
  const any = map?.reforms?.length > 0;
  return html`<section class="map-key card" aria-labelledby="map-key-title">
    <h2 class="key-title" id="map-key-title">
      ${any ? "Added homes as a share of each county's baseline (middle estimate)" : "Switch a reform on to shade the counties"}
    </h2>
    ${any &&
    html`<ol class="key-ramp key-ramp-3">
      ${map.classes.map(
        (c) => html`<li class="key-step">
          <span class="key-swatch" style=${`background: var(${CLASS_COLOURS[c.id]})`}></span>
          <span class="key-label"><strong>${c.word}</strong> ${c.label}</span>
        </li>`,
      )}
    </ol>`}
    <div class="key-foot">
      <ul class="key-grey">
        <li><span class="key-swatch is-nodata"></span>No data</li>
        ${!any && html`<li><span class="key-swatch is-notmodelled"></span>No reform on</li>`}
      </ul>
      <button type="button" class="btn btn-quiet btn-xs" aria-pressed=${tableOpen ? "true" : "false"}
        onClick=${onTable}>${tableOpen ? "Hide table" : "County table"}</button>
    </div>
  </section>`;
}
