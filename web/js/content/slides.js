/**
 * The slide spec: an ordered list of slides, each a plain object.
 *
 *   { layout: "title" | "headline" | "bullets", kicker, title, subtitle?, body?,
 *     stats?: [{ label, value }], bullets?: [text], sources?: [text] }
 *
 * The engine will build this list from one run, with every value and source attached.
 * Until it is connected, `placeholderDeck` returns three slides that hold words only: every
 * value is `null` and is drawn as "—".
 */
import { reformById } from "./reforms.js";
import { stateName } from "./states.js";

export function placeholderDeck(scenario) {
  const reforms = scenario.reforms.map((id) => reformById(id)?.name).filter(Boolean);
  const which = reforms.length ? reforms.join(" · ") : "Baseline only: no reform switched on";
  return [
    {
      layout: "title",
      kicker: "VisionHousing",
      title: "How state housing reforms could change homes built over five years",
      subtitle: `${stateName(scenario.state)} · ${which}`,
      body: "Decision support, not a forecast. Not an official state document.",
    },
    {
      layout: "headline",
      kicker: "Headline",
      title: "What the selected reforms add",
      stats: [
        { label: "Baseline, five-year units", value: null },
        { label: "Added units", value: null },
        { label: "Change from baseline", value: null },
      ],
      body: "Results appear here once the engine is connected. Each number will be a likely range.",
    },
    {
      layout: "bullets",
      kicker: "Limits",
      title: "What this cannot tell you",
      bullets: [
        "Where any single home will be built. Results are reported for counties and the state.",
        "One exact number. Every result is a likely range that depends on stated assumptions.",
        "The effect of rules this model does not cover. The full list comes with the results.",
      ],
    },
  ];
}
