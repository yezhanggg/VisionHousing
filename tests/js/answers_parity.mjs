/**
 * Runs the browser's question-box resolver (web/js/lib/answers.js) in Node over a static-site
 * data folder, for tests/test_answers.py: the same gold questions must give the same answers
 * as the Python resolver over the same files.
 *
 * Input (stdin, JSON): {"data": folder, "table": answers.json path, "cases": [{question, context}]}.
 * Output (stdout, JSON): the list of answers, in order.
 *
 * The reader below mirrors StaticReader in src/visionhousing/api/answers.py and the static
 * client in web/js/lib/api.js (packing undone with that file's own makePool / unpack).
 */
import { readFile } from "node:fs/promises";
import path from "node:path";
import { makePool, unpack } from "../../web/js/lib/api.js";
import { resolve, runKey, UnavailableError, BENCHMARKS } from "../../web/js/lib/answers.js";

function staticReader(root) {
  const pools = {};
  const indexes = {};
  async function file(rel) {
    let raw;
    try {
      raw = JSON.parse(await readFile(path.join(root, rel), "utf8"));
    } catch {
      throw new UnavailableError(rel);
    }
    if (raw && raw.$shared) {
      const st = rel.split("/")[0];
      pools[st] ||= makePool(JSON.parse(await readFile(path.join(root, st, "pool.json"), "utf8")));
      return unpack(raw, pools[st]);
    }
    return unpack(raw);
  }
  const index = async (st) => (indexes[st] ||= await file(`${st}/index.json`));
  const rowCovers = (row, geoid) => {
    const j = String(row.jurisdiction || "");
    if (!geoid || /^[A-Z]{2}$/.test(j)) return true;
    return geoid.startsWith(j) || j.startsWith(geoid);
  };
  return {
    async run(sc) {
      const idx = await index(sc.state);
      if (idx.seed !== 0) throw new UnavailableError("run");
      const key = runKey(sc.reforms, sc.overrides, idx.package || []);
      const saved = [...(idx.runs || []), ...(idx.extra_runs || [])];
      if (!key || key === "custom" || !saved.includes(key)) throw new UnavailableError("run");
      return file(`${sc.state}/runs/${key}.json`);
    },
    validation: (st) => file(`${st}/validation.json`),
    async permitYears(st) {
      if (!(await index(st)).permit_years) throw new UnavailableError("permit-years");
      return file(`${st}/permit-years.json`);
    },
    async implementation(st, reform, geoid = "") {
      if (!(await index(st)).implementation) throw new UnavailableError("implementation");
      const all = await file(`${st}/implementation.json`);
      const rows = (all.rows || []).filter((r) => r.reform === reform && rowCovers(r, geoid));
      return { ...all, geoid: geoid || null, reforms: [reform], rows };
    },
    async dials(st) {
      if (!(await index(st)).comparisons) throw new UnavailableError("comparisons");
      return file(`${st}/comparisons/dials.json`);
    },
    reportCard: (st) => file(`${st}/report-card.json`),
    async budgetGrid(st) {
      if (!(await index(st)).adu_budget_grid) throw new UnavailableError("adu-budget-grid");
      return file(`${st}/adu-budget-grid.json`);
    },
    async download(sc) {
      let idx;
      try {
        idx = await index(sc.state);
      } catch {
        return "";
      }
      const key = runKey(sc.reforms, sc.overrides, idx.package || []);
      if (!key || key === "custom" || !(idx.exports || []).includes(key)) return "";
      return `data/${sc.state}/exports/${key}.csv`;
    },
  };
}

const chunks = [];
for await (const c of process.stdin) chunks.push(c);
const input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const table = JSON.parse(await readFile(input.table, "utf8"));
const reader = staticReader(input.data);
const out = [];
for (const c of input.cases) out.push(await resolve(c.question, c.context, reader, table));
process.stdout.write(JSON.stringify(out));
void BENCHMARKS;
