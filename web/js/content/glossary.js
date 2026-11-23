/**
 * Plain-language definitions. Each technical word on screen links here through the
 * glossary tooltip (ui/popover.js). Words only: no figures.
 */
export const GLOSSARY = {
  unit: {
    term: "Unit",
    text: "One home: a house, an apartment or an accessory dwelling. Tables say units; sentences may say homes.",
  },
  permits: {
    term: "Units authorized by permits",
    text: "New housing units that local governments approve through building permits. This is the outcome the tool measures.",
  },
  baseline: {
    term: "Baseline",
    text: "What the model expects over the next five years if today's rules and economics stay the same.",
  },
  range: {
    term: "Likely range",
    text: "The span a result is likely to fall in. The low and high ends come from re-running the model across the sourced range of every assumption.",
  },
  funnel: {
    term: "Funnel",
    text: "The four steps from sites a reform makes eligible to homes actually built in five years. Each step keeps only part of the one before.",
  },
  feasible: {
    term: "Financially feasible",
    text: "A project whose expected value covers its land, building, fee and financing costs, so a builder would likely go ahead.",
  },
  takeup: {
    term: "Take-up",
    text: "The share of feasible sites where owners actually build within the five years.",
  },
  overlap: {
    term: "Overlap",
    text: "When two reforms open up the same sites, their combined effect is smaller than the sum of each one alone.",
  },
  tier: {
    term: "Data tier",
    text: "How complete a state's data is. The tier says which inputs are measured and which are assumed.",
  },
  adu: {
    term: "Accessory dwelling unit (ADU)",
    text: "A small second home on a lot that already has a house. It can be attached, detached or inside the house.",
  },
  ledger: {
    term: "Assumption Ledger",
    text: "The one list of every assumption the model uses, each with a low, central and high value, a source and a reason.",
  },
};

/** The glossary entries in alphabetical order, for the Method & data screen. */
export function glossaryList() {
  return Object.entries(GLOSSARY)
    .map(([id, e]) => ({ id, ...e }))
    .sort((a, b) => a.term.localeCompare(b.term));
}
