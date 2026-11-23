/**
 * The map area and its key.
 *
 * The map is drawn by MapLibre GL (BSD-3) from web/vendor/. It has no basemap and calls
 * no tile server: the county shapes will come from the state data pack as simplified
 * GeoJSON. Until that pack exists the map is an empty canvas, and the page says so.
 * If the browser cannot draw WebGL, the page says that too, in plain words.
 */
import { html, useEffect, useRef, useState } from "../lib/html.js";

const MESSAGES = {
  loading: "Loading the map…",
  ready: "",
  missing: "The map library is missing. Run make setup, then reload.",
  nogl: "This browser cannot draw the map. The same results will be in the table on Compare.",
};

export function MapView({ stateName }) {
  const box = useRef(null);
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    let map = null;
    let gone = false;
    import("maplibre-gl")
      .then(({ Map }) => {
        if (gone || !box.current) return;
        try {
          map = new Map({
            container: box.current,
            style: {
              version: 8,
              sources: {},
              layers: [
                { id: "background", type: "background", paint: { "background-opacity": 0 } },
              ],
            },
            center: [-77.6, 40.9],
            zoom: 6,
            attributionControl: false,
            dragRotate: false,
            pitchWithRotate: false,
          });
          map.on("error", () => setStatus("nogl"));
          map.on("load", () => setStatus("ready"));
        } catch {
          setStatus("nogl");
        }
      })
      .catch(() => setStatus("missing"));
    return () => {
      gone = true;
      if (map) map.remove();
    };
  }, []);

  return html`<div class="map-area">
    <div class="map-canvas" ref=${box} role="region" aria-label="County map"></div>
    <div class="map-empty" role="note">
      <p class="map-empty-title">${MESSAGES[status] || `County map for ${stateName}`}</p>
      <p class="map-empty-text">
        Counties will be shaded by added units per 1,000 existing homes once the state data pack
        is built.
      </p>
    </div>
  </div>`;
}

const SWATCHES = ["--seq-1", "--seq-2", "--seq-3", "--seq-4", "--seq-5", "--seq-6"];

/**
 * The map key. Class breaks are fixed per state by the engine (they do not move when the
 * scenario changes), so their labels come with the results. Until then each says "—".
 */
export function MapKey() {
  return html`<section class="map-key card" aria-labelledby="map-key-title">
    <h2 class="key-title" id="map-key-title">Added units per 1,000 existing homes</h2>
    <ol class="key-ramp" aria-label="Six classes, lightest is fewest">
      ${SWATCHES.map(
        (v, i) => html`<li class="key-step">
          <span class="key-swatch" style=${`background: var(${v})`}></span>
          <span class="key-label" aria-label=${`Class ${i + 1}: break not available yet`}>—</span>
        </li>`,
      )}
    </ol>
    <ul class="key-grey">
      <li><span class="key-swatch is-nodata"></span>No data</li>
      <li><span class="key-swatch is-notmodelled"></span>Not modelled</li>
    </ul>
  </section>`;
}
