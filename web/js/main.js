/**
 * Entry point. Mounts the app: the top bar, the screen for the current route and the toast.
 * Vite bundles this file, everything it imports and the stylesheets below into web/dist/.
 * The stylesheets are imported here, in this order, so the cascade is the same in
 * `npm run dev` and in the build.
 */
import "maplibre-gl/dist/maplibre-gl.css";
import "../css/tokens.css";
import "../css/app.css";
import "../css/slides.css";
import { stateName } from "./content/states.js";
import { html, render, useEffect } from "./lib/html.js";
import { CompareScreen } from "./screens/compare.js";
import { MethodScreen } from "./screens/method.js";
import { PlaceScreen } from "./screens/place.js";
import { ReformScreen } from "./screens/reform.js";
import { SlidesScreen } from "./screens/slides.js";
import { StateScreen } from "./screens/state.js";
import { route, scenario, startRouter } from "./state/store.js";
import { StaticNote } from "./ui/static-note.js";
import { Toast } from "./ui/toast.js";
import { TopBar } from "./ui/topbar.js";

const SCREENS = {
  state: [StateScreen, "State"],
  reform: [ReformScreen, "Reform"],
  compare: [CompareScreen, "Compare"],
  place: [PlaceScreen, "Place"],
  method: [MethodScreen, "Method & data"],
  slides: [SlidesScreen, "Slides"],
};

function App() {
  const name = route.value.name;
  const [Screen, title] = SCREENS[name] || SCREENS.state;
  const place = stateName(scenario.value.state);
  useEffect(() => {
    document.title = `${title} · ${place} · VisionHousing`;
    document.body.dataset.route = name;
  }, [name, title, place]);
  return html`<a class="skip-link" href="#main" onClick=${(e) => {
      e.preventDefault();
      document.getElementById("main")?.focus();
    }}>Skip to content</a>
    <${TopBar} />
    <${StaticNote} />
    <${Screen} key=${name} />
    <${Toast} />`;
}

startRouter();
const root = document.getElementById("app");
root.textContent = ""; // remove the "Loading…" note before the first render
render(html`<${App} />`, root);
