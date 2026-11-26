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

/** The reform key of a record ref "reform_results.KEY.added", else "". */
function reformOf(ref) {
  const m = /^reform_results\.([^.]+)\.added$/.exec(ref || "");
  return m ? m[1] : "";
}

/**
 * The deck as plain slides: {kind, kicker, title, big, chart, body[], sources[]}.
 * A reform whose statewide range is hidden (the run's `status_views`, transit status) shows
 * the server's status words in place of its range: its own slide's big number becomes the
 * measured contribution, its bar leaves the interval chart, and a package slide carries the
 * server's package note.
 */
export function deckOf(run) {
  const views = run?.status_views || {};
  const hidden = (key) => (views[key] && views[key].shown === false ? views[key] : null);
  const notes = Object.values(views).map((v) => v.package_note).filter(Boolean);
  return (run?.explanation?.slides || []).map((s) => {
    const off = hidden(reformOf(s.big?.ref));
    const chart = s.chart?.type === "interval" && Array.isArray(s.chart.items)
      ? { ...s.chart, items: s.chart.items.filter((it) => !hidden(reformOf(it.ref))) }
      : s.chart || null;
    const dropped = (s.chart?.items || []).map((it) => hidden(reformOf(it.ref))).filter(Boolean);
    const body = (s.body || []).map((b) => b.text);
    return {
      kind: s.kind,
      kicker: s.kicker?.text || "",
      title: s.title?.text || "",
      big: off
        ? { label: off.hidden_word, text: off.measured.text, middle: off.label }
        : s.big
          ? { label: s.big.label?.text || "", text: s.big.text?.text || "", middle: s.big.middle?.text || "" }
          : null,
      chart: off ? null : chart,
      body: off
        ? [off.text, ...body]
        : s.big?.ref === "package.combined"
          ? [...body, ...notes, ...dropped.filter((v) => !v.package_note).map((v) => v.text)]
          : [...body, ...dropped.map((v) => v.text)],
      sources: s.sources || [],
    };
  });
}
