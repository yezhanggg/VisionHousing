/**
 * Signals the map's extra layers share with the panels and the legend: the 2D / 3D view, the
 * range caps, the tract pointed at, the map funnel step and the year slider.
 *
 * They are per visit (never in the address) and make no numbers: every value the layers draw
 * comes from an API reply (`dialView` carries a dial level's county values as the server sent
 * them). The main map itself is in `mainMap` once it has loaded, so the
 * layer controllers (ui/columns.js, ui/map-funnel.js, ui/years.js) can add their sources to it
 * without the map view drawing again.
 */
import { signal } from "@preact/signals";

/** The main MapLibre map once it has loaded (null before, and on the compact maps). */
export const mainMap = signal(null);

/** True in the 3D view (columns), false in the flat 2D map (the default). */
export const view3d = signal(false);
/** Whether the columns show their likely range as a glass cap (P50 to P90). Default on. */
export const showRange = signal(true);
/** The map's bearing in degrees (for the "North up" button). */
export const bearing = signal(0);
/** The map's zoom when it last came to rest (the 3D legend shows the level in view). */
export const mapZoom = signal(0);

/**
 * The column or tract pointed at in 3D or on the map funnel: {level: "county" | "tract", id,
 * county} or null. The hover card reads it; the county lists still use `hoveredCounty`.
 */
export const hoveredTract = signal(null);
/** Where the 3D hover card sits (the column's top on screen): {x, y} or null. */
export const columnPoint = signal(null);

/**
 * The map funnel: which reform's stages the map shows and which stage. `step` is an index
 * into that reform's tract-funnel steps; `preview` (or -1) is a stage pointed at in the
 * Reform screen's funnel card, shown on the map while the pointer stays. `reform` is "" when
 * the map funnel is off.
 */
export const mapFunnel = signal({ reform: "", step: 0, preview: -1 });

/** The year slider: off, or {on: true, year} (a past year or a projected one). */
export const yearView = signal({ on: false, year: 0 });

/**
 * The district view (ui/districts-layer.js): whether the district layer is on, which chamber
 * ("lower" = state house, "upper" = state senate) and the district picked ("" for none; it
 * opens the district panel). While it is on, the county layer does not answer the pointer.
 */
export const districtView = signal({ on: false, chamber: "lower", picked: "" });

/**
 * A policy dial's level shown on the map (ui/columns.js, "Dial levels on the map"), or null
 * for the scenario. Set when the reader moves a dial (the `vh:dial-level` event); cleared by
 * "Back to the scenario", by leaving the Reform screen (the dial unmounts) and by a new
 * scenario. {state, reform, reformName, dial, level, label, counties: {fips: {p10, p50, p90}},
 * top: {fips: {v90}} (each county's highest P90 over the dial's levels, for one height scale),
 * scale2d (the highest county P50 over the dial's levels, for one 2D colour scale)}. Every
 * value is the engine's own, from the comparisons reply.
 */
export const dialView = signal(null);
