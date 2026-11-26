/**
 * Entry point. Mounts the app: the intro page at the bare address ("#/"), else the shell
 * (one full-screen map with the floating mode switcher, side panels, chat box and legend,
 * ui/shell.js) and the toast. The slides view covers the shell, which stays mounted
 * underneath, so the map is not drawn again when the slides close.
 * Vite bundles this file, everything it imports and the stylesheets below into web/dist/.
 * The stylesheets are imported here, in this order, so the cascade is the same in
 * `npm run dev` and in the build.
 */
import "maplibre-gl/dist/maplibre-gl.css";
import "../css/tokens.css";
import "../css/app.css";
import "../css/slides.css";
import "../css/shell.css";
import { stateName } from "./content/states.js";
import { html, render, useEffect } from "./lib/html.js";
import { CompareMode } from "./screens/compare.js";
import { IntroScreen } from "./screens/intro.js";
import { MethodMode } from "./screens/method.js";
import { PlaceMode } from "./screens/place.js";
import { ReformMode } from "./screens/reform.js";
import { SlidesScreen } from "./screens/slides.js";
import { StateMode } from "./screens/state.js";
import { atIntro, enableIntro, hrefFor, navigate, route, scenario, startRouter, tourOpen } from "./state/store.js";
import { Shell } from "./ui/shell.js";
import { Toast } from "./ui/toast.js";

const MODES = {
  state: StateMode,
  reform: ReformMode,
  compare: CompareMode,
  place: PlaceMode,
  method: MethodMode,
};

const TITLES = { state: "State", reform: "Reform", compare: "Compare", place: "Place", method: "Method & data", slides: "Slides" };

let shellSeen = false;

/** The intro's "Take the tour": open the State screen with ADU on (if nothing is), then the tour. */
function openWithTour() {
  const sc = scenario.value;
  location.hash = hrefFor("state", "", sc.reforms.length ? sc : { ...sc, reforms: ["adu"] });
  // The shell mounts on the hash change; open the tour after it has drawn.
  setTimeout(() => {
    tourOpen.value = true;
  }, 0);
}

function App() {
  const intro = atIntro.value;
  const name = route.value.name;
  const title = intro ? "Welcome" : TITLES[name] || "State";
  const place = stateName(scenario.value.state);
  useEffect(() => {
    document.title = `${title} · ${place} · VisionHousing`;
    document.body.dataset.route = intro ? "intro" : name;
  }, [name, title, place, intro]);
  const slides = !intro && name === "slides";
  // The shell mounts the first time a mode is shown and then stays (hidden under the slides).
  if (!intro && !slides) shellSeen = true;
  return html`<a class="skip-link" href="#main" onClick=${(e) => {
      e.preventDefault();
      document.getElementById("main")?.focus();
    }}>Skip to content</a>
    ${intro && html`<${IntroScreen} onOpen=${() => navigate("state")} onTour=${openWithTour} />`}
    ${!intro && shellSeen && html`<${Shell} modes=${MODES} hidden=${slides} />`}
    ${slides && html`<${SlidesScreen} />`}
    <${Toast} />`;
}

enableIntro();
startRouter();
const root = document.getElementById("app");
root.textContent = ""; // remove the "Loading…" note before the first render
render(html`<${App} />`, root);
