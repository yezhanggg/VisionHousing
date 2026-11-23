/**
 * Entry point. Mounts the app: the top bar, the screen for the current route and the toast.
 * No build step: the browser loads these ES modules directly, and the import map in
 * index.html points library names at web/vendor/.
 */
import { stateName } from "./content/states.js";
import { html, render, useEffect } from "./lib/html.js";
import { CompareScreen } from "./screens/compare.js";
import { MethodScreen } from "./screens/method.js";
import { PlaceScreen } from "./screens/place.js";
import { ReformScreen } from "./screens/reform.js";
import { SlidesScreen } from "./screens/slides.js";
import { StateScreen } from "./screens/state.js";
import { route, scenario, startRouter } from "./state/store.js";
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
    <${Screen} key=${name} />
    <${Toast} />`;
}

startRouter();
const root = document.getElementById("app");
root.textContent = ""; // remove the "Loading…" note before the first render
render(html`<${App} />`, root);
