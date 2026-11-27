# Module: Report writer (`src/visionhousing/report/`)

**What it does.** Writes one run as a brief a legislator can read (Markdown, or a print-ready HTML page), as CSV tables an analyst can check, or as the full JSON explanation.

**Why.** The Handbook asks for results people can take away. The brief and the screens must say the same thing, so the brief is drawn from the same explanation the interface reads (`visionhousing.explain`), and every number in it is cited to the engine.

## Command

```
visionhousing report --state PA [--reforms adu,fees,parking] [--format md|html|csv|json]
                     [--out PATH] [--seed 0] [--draws 2000] [--date YYYY-MM-DD|today|none]
```

- `--reforms`: comma-separated reform keys, or `none` for the baseline only. Default `adu,fees,parking`.
- `--out`: the file to write; for `csv`, the folder. Default `outputs/reports/<STATE>-<reforms>.<format>` (git-ignored).
- `--date`: the run date printed in the stamp. Default today; `none` leaves it out.

The same arguments give byte-identical files. Only the run date can differ, and `--date` fixes it.

## What the brief holds

1. Title and the banner "Decision support, not a forecast. Not an official state document."
2. **In one sentence**: the summary sentence and the three numbers (baseline, homes added, percent change) as likely ranges with the middle estimate.
3. **Each reform on its own**: verdict word, homes added, percent change and why the verdict, plus interval bars (HTML).
4. **All reforms together**: the waterfall (sum of parts, overlap removed, interaction added, combined).
5. One section per reform: verdict, the funnel table with what each step means, the top drivers, and what it cannot tell you.
6. **Where the added homes would be**: the ten counties with the most added homes (every county is in the CSV).
7. **What these estimates cannot tell you**, **How the ranges are made**, **Assumptions used** (every ledger entry behind the numbers, with low, central, high, kind and status), **Sources**, **How the headline numbers are worked out** (the engine's formulas), **Technical notes from the model**.
8. The stamp: code, data and assumption hashes, seed, draws, run date, the command that rebuilds it, and the disclaimer.

## Files

| File | What it does |
|---|---|
| `brief.py` | `brief_blocks(result, explanation, run_date)`: the brief as data, a list of blocks (title, banner, heading, paragraph, list, table, chart, stamp). Every text is a cited `Text` |
| `render.py` | `to_markdown(blocks)`, `to_html(blocks)`. The page has inline CSS, system fonts, no scripts and no outside requests; charts are inline SVG with an `aria-label` listing every value; print rules set US Letter pages and keep rows and charts whole |
| `tables.py` | `csv_tables(result, explanation, run_date)`: `summary`, `counties`, `funnels`, `drivers`, `assumptions`, `numbers`, `run`, at full precision. RFC 4180 quoting, CRLF line ends, UTF-8 byte-order mark |
| `writer.py` | `render(result, format, run_date)` and `write_report(result, format, out, run_date)` |

## Example

```python
from visionhousing.engine import run
from visionhousing.report import write_report

result = run("PA", ["adu", "fees", "parking"], seed=0)
write_report(result, "html", run_date="2026-11-22")  # outputs/reports/PA-adu-fees-parking.html
```

Open the HTML file in a browser and print it to PDF.

## How it is tested (`tests/test_report.py`)

- Golden opening of the Testland brief, and every expected section.
- The stamp holds the three hashes, seed, draws and date; with `--date none` there is no date.
- The must-cite check over every text in the brief; no number in the Markdown or the visible HTML text is outside the cited numbers (chart labels are checked against their records too).
- Every format is byte-identical for the same run; only the date line changes with the date.
- The HTML parses with the standard library's `html.parser` with every tag closed in order, one title, no scripts, links, images, imports or web fonts, and labelled SVG charts.
- The CSV tables are RFC 4180 with the byte-order mark and full-precision values equal to the engine's.
- The command line writes each format, writes all seven CSV tables, handles `--reforms none`, and fails cleanly on an unknown reform or a bad date.

## The two-page hearing brief (`hearing.py`)

For an analyst preparing a hearing memo: one state and one reform (or the package) on two US
Letter pages, written from the same run records the screens read.

```
visionhousing hearing-brief --state PA --reforms adu [--format html|md] [--out PATH]
                            [--seed 0] [--draws 2000] [--date YYYY-MM-DD]
visionhousing hearing-brief --state PA --reforms adu --static web/public/data
visionhousing hearing-brief --state PA,MA,VT,WY --refresh-static web/public/data
```

`--reforms` takes reform ids, `package` (all five benchmarks) or `none`. The first form runs the
engine; the second reads a saved static export (its run file and `baseline-card.json`), so no
state pack is needed; the third writes every saved run's brief and the baseline card into an
export and records `schema_version`, `baseline_card` and `hearing_briefs` in its `index.json`.
The API serves the same page at `GET /export/hearing?s=PA&r=adu`. The file the CLI writes keeps
its style rules inline (self-contained, opened from disk); the web copies (the API and the static
site) link them instead (`GET /export/hearing.css`, static `data/hearing.css`), because the
site's Content Security Policy allows styles only from the site itself.

Page one: **the question** (how many more homes the state would likely authorize by building
permit over the five projected years); **the answer** (the engine's sentence and the likely
ranges of the baseline, homes added and change, as the headline's `shown` text) with **what the
headline counts** (outcome accounting: the scope, e.g. ADU "all work types", and the
Census-comparable range beside it); **the baseline** (the baseline card rows from
`api/baseline_view.py`: years, permit columns, release and date, cutoff, projected years and the
2026 gap, the 2026 nowcast, the PA window sensitivity, the reconciliation).

Page two: **what the law changes** (only when the "what this law changes" payload exists for the
reform: the statewide share in each answer, the provision, the unresolved share); **main drivers**
(the top inputs moved one at a time; for a package, the waterfall); **what is unknown** (where the
uncertainty comes from, the outcome-accounting statuses, unresolved local rules); **what the
model cannot tell you**; and the **reproduce** block: run id (state, run key, the three hashes,
seed, draws), the full hashes, the command that writes the brief again and the link that
reopens the view.

Appendix (one benchmark reform): **alternatives at a glance**, the reform's named alternatives
with their likely range, the paired change from the benchmark and the status
(`api.alternatives.hearing_rows`). It is added only when the table is already on disk (the API
and the CLI read the comparisons disk cache, never run it) or in the export
(`ST/alternatives.json`, read by `--refresh-static`); otherwise the brief has no appendix.

Nothing is typed by hand: every number is a `shown` text or a card row from the records.
`tests/test_hearing_brief.py` checks that every number in the brief is a number of the run
reply, that the answer table and scope equal the records, that the HTML is self-contained with
two-page print rules, that `GET /export/hearing` equals the CLI, and that the committed static
briefs hold their run's id; `tests/e2e/test_hearing_links.py` prints it and counts two pages.
