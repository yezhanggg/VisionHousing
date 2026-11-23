/**
 * The slide deck of a scenario, as the engine writes it.
 *
 * The explanation layer builds the deck from one run (`explanation.slides`): a summary, one
 * slide per reform, the top drivers (a reform run alone), the package waterfall (several
 * reforms) and the limits. Each slide carries its title, a big likely range, a chart spec,
 * body sentences and its sources; every number in them is cited to a run record. This module
 * only turns that spec into the plain shape the Slides view draws. It adds words, no numbers.
 */

/** The stamp printed in every slide footer: the run's three fingerprints. */
export function stampOf(run) {
  const s = run?.explanation?.stamp || run?.hashes || {};
  return { code: s.code || "", data: s.data || "", assumptions: s.assumptions || "", disclaimer: s.disclaimer || "" };
}

/** The deck as plain slides: {kind, kicker, title, big, chart, body[], sources[]}. */
export function deckOf(run) {
  return (run?.explanation?.slides || []).map((s) => ({
    kind: s.kind,
    kicker: s.kicker?.text || "",
    title: s.title?.text || "",
    big: s.big
      ? { label: s.big.label?.text || "", text: s.big.text?.text || "", middle: s.big.middle?.text || "" }
      : null,
    chart: s.chart || null,
    body: (s.body || []).map((b) => b.text),
    sources: s.sources || [],
  }));
}
