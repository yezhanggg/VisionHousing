# Module: Explanations (`src/visionhousing/explain/`)

**What it does.** Turns one engine run into everything a reader sees in words: the one-sentence summary and three numbers, a verdict word per reform, each reform's funnel with what every step means, the top drivers, the package waterfall, what each estimate cannot tell you, the "explain this number" payload for every record, and the slide deck spec.

**Why.** Transparency is the largest part of the score, and every screen, export and document must agree. Explanations are plain text built from the engine's own numbers. There is no AI in the product (decision D-12): no model writes or rewrites any sentence.

## The one rule: every number is cited

Every sentence is a **Text**: the words plus a list of **cites**. A cite says where a number came from (`ref`, a path into the result such as `reform_results.adu.added`), which field (`p10`), the exact value there, and how it was written (`8,900`).

The tests check, for every Text in the explanation, the slides and the brief:

1. every number written in the words is covered by a cite (a hand-typed number fails);
2. every cite's value is found at its `ref` in the engine result itself;
3. the written form is that value rounded the way this module rounds.

Only digits count as numbers. Words such as "five-year" or "nine in ten" are fixed wording.

## How numbers are written (`numbers.py`)

| Where | Rule | Example |
|---|---|---|
| Sentences, big numbers, slides | two significant figures | 8,010.5 → 8,000; 6.203 → 6.2 |
| Tables | three significant figures; homes and lots as whole numbers | 41,903.9 → 41,900; 34.2 → 34 |
| Percent of baseline | two significant figures | 0.804 → 0.8% |
| Shares | as a percent | 0.0987 → 9.9% |
| Money | dollars with the unit | $25,000/unit |

The engine keeps full precision; the CSV export does too.

## Record ids (`ids.py`)

A record's id is its path in `Result.to_dict()`, with dots: `baseline`, `baseline_counties.42003`, `reform_results.adu.added`, `reform_results.adu.pct_change`, `reform_results.adu.steps.2`, `reform_results.adu.records.per_100k`, `reform_results.adu.counties.42003`, `package.combined` (also `sum_of_parts`, `overlap`, `interaction`, `pct_change`), `package.interactions.fees -> adu`, `package.counties.42003`, `total`. `resolve(data, id)` follows an id into the JSON, so anyone holding the JSON can find the number without this code.

Cites can also point at the run's ledger (`ledger:<reform>/<parameter id>`, an empty reform meaning the run's own ledger), at a sensitivity row (`drivers.<reform>.<rank - 1>`), at a source (`source:<id>`) or at a county name (`county:<geoid>`).

## What `explain(result)` returns (`payload.py`)

| Key | Contents |
|---|---|
| `headline` | The one sentence and three numbers: baseline five-year homes, homes added, percent change, each P10 / P50 / P90 |
| `verdicts` | Per reform: the word, the middle-estimate percent, and a sentence saying which rule picked it |
| `funnels` | Per reform: each step's label, plain meaning, range (or central value), rounded form, formula and tier |
| `drivers` | Per reform: the top five tornado inputs, with values at low and high and one sentence each |
| `waterfall` | Sum of parts, overlap removed, interaction added, combined; each reform's part; each interaction |
| `limits` | "What this cannot tell you": general, baseline, package and each reform, from `assumptions/limits.yaml` |
| `numbers` | "Explain this number" for every record id: formula, inputs, assumption ids with labels, source ids, range, tier and tier label |
| `assumptions`, `sources` | The ledger entries and sources those payloads point to |
| `slides` | The slide deck spec (below) |
| `stamp` | Code, data and assumption hashes, seed, draws, and the disclaimer |

### The sentence

> Over {years}, {state} would likely permit between {P10} and {P90} more homes ({pct P10}–{pct P90}%) with {reforms}, on top of a baseline of {baseline P10}–{baseline P90} homes expected under today's rules.

The reforms read as "accessory dwelling units (ADUs) allowed", "development fees cut in half", "parking minimums removed", "two to six homes allowed on single-family lots"; a bill reads as "Pennsylvania HB 2186 passed".

### Verdict words

From the reform's middle estimate (P50) of the percent change against the baseline, run alone. The thresholds are ledger entries of kind *judgement* (fixed values, status draft):

| Word | Rule | Ledger id |
|---|---|---|
| Adds a lot | P50 at least 5% | `explain.verdict_a_lot_pct` |
| Adds some | P50 from 1% to under 5% | `explain.verdict_some_pct` |
| Little effect | P50 under 1% | — |
| Overlaps | In a package, at least half of the reform's own P50 is removed because another reform uses the same lots | `explain.verdict_overlap_share` |

Only missing middle can overlap today: when ADUs and missing middle are both on, the ADU reform keeps its lots and missing middle loses them (see `engine.packages`).

### Funnels

The ADU and missing-middle reforms report each step as a range. The fee and parking reforms work one central-value chain and sample the whole chain separately, so their steps are central values and the funnel adds a last row with the likely range. What each step means is written in `wording.py` (no figures).

## Slides (`slides.py`, decision D-22)

An ordered list, the same JSON the web `#/slides` route draws:

1. **summary**: the sentence; big number = homes added; interval bars for baseline and with-reforms totals.
2. **compare** (two or more reforms): interval bars of each reform alone; verdict lines.
3. Per reform, **reform**: big number = homes added by it; funnel chart of the steps counted in lots or homes; the verdict sentence. Then **drivers**: tornado chart of the top five.
4. **package** (two or more reforms): waterfall.
5. **limits**: what these estimates cannot tell you, and the disclaimer. Text only (`chart` is `null`).

Every slide carries `title`, `kicker`, `big` (P10 / P50 / P90 with written forms), `chart` (`type` plus items that each name their record `ref`), `body`, `sources` (with citations) and `stamp` (the three hashes, seed and draws).

## Limits (`limits.py`, `assumptions/limits.yaml`)

Plain lines per reform, plus `general`, `baseline` and `package`. An entry may `extend` another (the Pennsylvania bills repeat the ADU lines). The file must hold no digits; loading fails if it does.

## Example

```python
from visionhousing.engine import run
from visionhousing.explain import explain

e = explain(run("TL", ["adu", "fees", "parking"], seed=0))
print(e["headline"]["sentence"]["text"])
for key, v in e["verdicts"].items():
    print(key, v["word"], "-", v["why"]["text"])
```

## How it is tested (`tests/test_explain.py`)

- Golden sentence and verdicts on Testland.
- The must-cite check over every Text in the explanation and the slides, with a resolver that reads the engine result directly; it is also shown to fail on a hand-typed number and on a wrong cite.
- Every slide's big number and chart items equal the records they name; every slide has the three hashes.
- Every record id has an "explain this number" payload whose formula, range, tier and assumption labels match the engine and the ledger.
- Verdict thresholds (pure function) and that they are read from the ledger (an override changes the word).
- Funnels follow the engine's steps; every known step has a meaning; drivers equal the sensitivity module's top five; the waterfall equals the package records.
- The limits file loads, extends, and refuses figures and cycles.
- The same run gives the same explanation, byte for byte.
