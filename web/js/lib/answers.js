/**
 * The question box's resolver: answers a plain question from the records of the current run.
 *
 * This is the browser twin of src/visionhousing/api/answers.py, function for function, so the
 * same question over the same records gives the same answer (the gold questions in
 * tests/test_answers.py check it). No language model is involved:
 *
 *   1. match  the question is lower-cased and stripped of punctuation; state and reform words
 *             are picked out; each intent in the table (web/js/content/answers.json, written
 *             from assumptions/answers.yaml) scores one point per word it finds; the highest
 *             score wins, the table's order breaks a tie, no point means "unsupported";
 *   2. read   the intent asks a fixed reader for fixed records (the run's explanation numbers,
 *             validation, permit history, law rows, dial targets, report card); anything the
 *             build does not hold raises `UnavailableError` and the answer says so;
 *   3. fill   a template is filled with text copied from those records (shown values, names,
 *             units, period, dates, sources). Nothing is computed here.
 *
 * The module imports only lib/budget.js (itself import-free), so a test can run it in Node. The reader is handed in: the
 * question box (ui/ask.js) builds one over lib/api.js, which reads the live back end or the
 * static site's files. The question itself never leaves the page.
 */

import { cellKey, gridLookup, targetKey } from "./budget.js";

export const BENCHMARKS = ["adu", "missing_middle", "tod", "parking", "fees"];
export const MAX_QUESTION = 500;

/** The build does not hold the records a question needs. */
export class UnavailableError extends Error {
  constructor(what = "") {
    super(what);
    this.name = "UnavailableError";
  }
}

/* ------------------------------------------------------------------ small helpers */

const isObj = (v) => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const obj = (v) => (isObj(v) ? v : {});
const lst = (v) => (Array.isArray(v) ? v : []);
const str = (v) => (typeof v === "string" ? v : "");
const val = (v) => (v === undefined ? null : v);
const size = (v) => Object.keys(obj(v)).length;
const has0 = (o, k) => Object.prototype.hasOwnProperty.call(obj(o), k);

/** Lower case, apostrophes dropped, digit commas joined, other punctuation to spaces. */
export function normalise(text) {
  let t = String(text ?? "").toLowerCase().replaceAll("’", "").replaceAll("'", "");
  t = t.replace(/(\d),(?=\d)/g, "$1");
  t = t.replace(/[^a-z0-9$%]+/g, " ").trim();
  return ` ${t} `;
}

export function has(norm, word) {
  const w = normalise(word).trim();
  return Boolean(w) && norm.includes(` ${w} `);
}

function drop(norm, word) {
  const w = normalise(word).trim();
  while (w && norm.includes(` ${w} `)) norm = norm.split(` ${w} `).join(" ");
  return norm;
}

/** Put each `{name}` of `template` from `values` (a missing name becomes empty). */
export function fill(template, values) {
  return template.replace(/\{(\w+)\}/g, (_m, k) => {
    const v = values[k];
    return v === undefined || v === null ? "" : String(v);
  });
}

/** "baseline", one reform id, "package", "custom" (changed assumptions) or a mix. */
export function runKey(reforms, overrides, pkg) {
  if (size(overrides)) return "custom";
  if (!reforms.length) return "baseline";
  if (reforms.length === 1) return reforms[0];
  if (pkg.length && reforms.length === pkg.length && pkg.every((r) => reforms.includes(r))) return "package";
  return reforms.join("+");
}

/** The link's scenario part, as encodeScenario in state/codec.js writes it. */
export function scenarioQuery(sc) {
  const parts = ["v=1", `s=${sc.state}`];
  if (sc.reforms.length) parts.push(`r=${sc.reforms.join(",")}`);
  const keys = Object.keys(sc.overrides || {}).sort();
  if (keys.length) parts.push(`a=${keys.map((k) => `${k}~${sc.overrides[k]}`).join(",")}`);
  return parts.join("&");
}

/** The run's id: state, run key, the code / data / assumption hashes, seed and draws. */
export function runId(run, key) {
  const h = obj(run.hashes);
  const marks = ["code", "data", "assumptions"].map((k) => String(h[k] || "").slice(0, 12)).join(".");
  return `${run.state}/${key}/${marks}/seed-${run.seed}/draws-${run.draws}`;
}

const MONEY = /\$\s?(\d+)(?:\s+(million|m|billion))?\b|\b(\d+)\s+(million|billion)\b/;
const SCALE = { million: 1e6, m: 1e6, billion: 1e9 };

/** A budget named in the question (`$20 million`, `$5000000`) and the rest of it. */
export function moneyIn(norm) {
  const m = MONEY.exec(norm);
  if (!m) return [null, norm];
  const digits = m[1] || m[3];
  const word = m[2] || m[4] || "";
  return [Number(digits) * (SCALE[word] ?? 1), `${norm.slice(0, m.index)} ${norm.slice(m.index + m[0].length)}`];
}

function firstNumber(norm) {
  const m = /\b(\d+)\b/.exec(norm);
  return m ? Number(m[1]) : null;
}

/** One record of `explanation.numbers` as an answer cites it (its inputs left out). */
export function numberView(run, key) {
  const rec = obj(obj(run.explanation).numbers)[key];
  if (!isObj(rec)) return null;
  const ids = lst(rec.assumptions).filter(isObj).map((a) => val(a.id));
  return {
    id: `run:${key}`,
    kind: "run",
    name: str(rec.name),
    unit: str(rec.unit),
    shown: val(rec.shown),
    p10: val(rec.p10),
    p50: val(rec.p50),
    p90: val(rec.p90),
    formula: str(rec.formula),
    source_ids: lst(rec.source_ids),
    assumption_ids: ids.length ? ids : lst(rec.assumption_ids),
  };
}

function ranged(view) {
  const shown = (view || {}).shown;
  return isObj(shown) && ["p10", "p50", "p90"].every((k) => typeof shown[k] === "string");
}

function pushSources(out, run, ids) {
  const table = obj(obj((run || {}).explanation).sources);
  const seen = new Set(out.map((s) => s.id));
  for (const sid of ids) {
    if (typeof sid !== "string" || seen.has(sid)) continue;
    seen.add(sid);
    const src = obj(table[sid]);
    out.push({ id: sid, citation: str(src.citation) || sid, url: str(src.url) });
  }
}

function pushUrl(out, url, citation) {
  if (typeof url === "string" && url && out.every((s) => s.id !== url)) out.push({ id: url, citation: citation || url, url });
}

const byLength = (words) => [...words].sort((a, b) => b.length - a.length);

/* ------------------------------------------------------------------ the resolver */

/** Pick the state, reforms and intent out of a question (words only, no record read). */
export function parse(question, table) {
  const norm = normalise(question);
  let rest = norm;
  let state = "";
  for (const [code, entry] of Object.entries(table.states)) {
    const words = byLength(entry.words);
    if (!state && words.some((w) => has(norm, w))) state = code;
    for (const w of words) rest = drop(rest, w);
  }
  const pkg = table.package.words.some((w) => has(norm, w));
  for (const w of byLength(table.package.words)) rest = drop(rest, w);
  let reforms = [];
  for (const [rid, entry] of Object.entries(table.reforms)) {
    const words = byLength(entry.words);
    if (words.some((w) => has(norm, w))) reforms.push(rid);
    for (const w of words) rest = drop(rest, w);
  }
  if (pkg) reforms = Object.keys(table.reforms);
  let best = table.fallback;
  let score = 0;
  for (const intent of table.intents) {
    let n = (intent.words || []).filter((w) => has(rest, w)).length;
    n += (intent.patterns || []).filter((p) => new RegExp(p).test(rest)).length;
    if (n > score) {
      best = intent.id;
      score = n;
    }
  }
  return { norm, rest, state, reforms, intent: best };
}

/**
 * Answer `question` from the records `reader` holds for the run in `context`
 * ({state, reforms, overrides, route, param, county}). Returns the same object as the Python
 * `resolve`: intent, status, title, lines, text, records, sources, run_id, period, link,
 * download, offers.
 */
export async function resolve(question, context, reader, table) {
  const t = table;
  const q = parse(String(question ?? "").slice(0, MAX_QUESTION), t);
  const ctxState = String(context.state || "TL").toUpperCase();
  const ctxReforms = lst(context.reforms).filter((r) => typeof r === "string");
  const state = q.state || ctxState;
  const reforms = q.reforms.length ? q.reforms : state === ctxState ? ctxReforms : [];
  const same = state === ctxState && JSON.stringify(reforms) === JSON.stringify(ctxReforms);
  const overrides = same ? { ...obj(context.overrides) } : {};
  const route = same ? String(context.route || "state") : "state";
  const param = same ? String(context.param || "") : "";
  const county = state === ctxState ? String(context.county || "") : "";
  const sc = { state, reforms, overrides };
  const a = new Answer(t, q, sc, reader, route, param, county);
  const handlers = {
    result: () => a.result(), baseline: () => a.baseline(), drivers: () => a.drivers(), law: () => a.law(),
    coverage: () => a.coverage(), target: () => a.target(), address: () => a.address(), share: () => a.share(),
    outside: () => a.outside(),
  };
  try {
    await (handlers[q.intent] || (() => a.unsupported()))();
  } catch (err) {
    if (!(err instanceof UnavailableError)) throw err;
    a.unavailable();
  }
  return a.done();
}

class Answer {
  constructor(t, q, sc, reader, route, param, county) {
    Object.assign(this, { t, q, sc, reader, county });
    this.intent = q.intent;
    this.status = "answered";
    this.lines = [];
    this.records = [];
    this.sources = [];
    this.run = null;
    this.runId = "";
    this.period = "";
    this.download = "";
    this.offers = [];
    const path = param ? `${route}/${param}` : route;
    this.link = `#/${path}?${scenarioQuery(sc)}`;
    const st = obj(t.states[sc.state]);
    this.vals = { state_name: st.name || sc.state, scenario_title: this.titleOf(sc.reforms), link: this.link };
  }

  /* ---------------------------------------------------------- shared steps */

  titleOf(reforms) {
    const names = this.t.reforms;
    const all = Object.keys(names);
    if (!reforms.length) return "The baseline";
    if (reforms.length === all.length && all.every((r) => reforms.includes(r))) return this.t.package.title;
    return reforms.map((r) => obj(names[r]).title || r).join(" + ");
  }

  say(name, values = {}) {
    this.lines.push(fill(this.t.templates[name], { ...this.vals, ...values }));
  }

  cite(view) {
    if (view && this.records.every((r) => r.id !== view.id)) {
      this.records.push(view);
      pushSources(this.sources, this.run, lst(view.source_ids));
    }
  }

  /** Cite a reform's transit status view (the run's `status_views`) as a record. */
  citeStatus(key, v) {
    this.cite({
      id: `run:status_views.${key}`, kind: "status", name: str(v.title),
      unit: "", shown: str(v.text), label: str(v.label),
      note: str(v.package_note), formula: str(v.rule),
      source_ids: lst(v.source_ids), assumption_ids: lst(v.ledger_ids),
    });
  }

  async loadRun(sc = this.sc, drivers = false) {
    const run = await this.reader.run(sc, drivers);
    this.run = run;
    this.runId = runId(run, runKey(sc.reforms, sc.overrides, BENCHMARKS));
    this.period = str(obj(run.contract).window);
    this.vals.period = this.period;
    const name = str(obj(obj(run.explanation).stamp).state_name);
    if (name && !has0(this.t.states, this.sc.state)) this.vals.state_name = name;
    if (sc.reforms.length === 1 && !has0(this.t.reforms, sc.reforms[0])) {
      this.vals.scenario_title = str(obj(obj(run.reform_results)[sc.reforms[0]]).title) || sc.reforms[0];
    }
    return run;
  }

  async tryRun() {
    try {
      return await this.loadRun();
    } catch (err) {
      if (err instanceof UnavailableError) return null;
      throw err;
    }
  }

  async maybe(ask) {
    try {
      return await ask();
    } catch (err) {
      if (err instanceof UnavailableError) return null;
      throw err;
    }
  }

  headlineKeys() {
    const r = this.sc.reforms;
    if (r.length === 1) return [`reform_results.${r[0]}.added`, `reform_results.${r[0]}.pct_change`];
    return ["package.combined", "package.pct_change"];
  }

  /* ---------------------------------------------------------- intents */

  async result() {
    const run = await this.loadRun();
    const base = numberView(run, "baseline");
    if (!ranged(base)) throw new UnavailableError("baseline");
    const b = base.shown;
    if (!this.sc.reforms.length) {
      this.say("result_baseline_only", { b10: b.p10, b50: b.p50, b90: b.p90 });
      this.cite(base);
      return;
    }
    const [addedKey, shareKey] = this.headlineKeys();
    const added = numberView(run, addedKey);
    if (!ranged(added)) throw new UnavailableError(addedKey);
    const views = obj(run.status_views);
    const alone = this.sc.reforms.length === 1 ? obj(views[this.sc.reforms[0]]) : {};
    if (alone.shown === false) {
      // The statewide range is hidden (transit status): the status text, never a range.
      this.say("result_status", { status_text: str(alone.text) });
      this.citeStatus(this.sc.reforms[0], alone);
      return;
    }
    if (added.p10 === 0 && added.p90 === 0 && this.gapNotes(run).length) {
      await this.coverage(true);
      return;
    }
    const s = added.shown;
    this.say("result", { p10: s.p10, p50: s.p50, p90: s.p90, b10: b.p10, b90: b.p90 });
    this.cite(added);
    const share = numberView(run, shareKey);
    if (ranged(share)) {
      this.say("result_share", { s10: share.shown.p10, s90: share.shown.p90 });
      this.cite(share);
    }
    for (const r of this.sc.reforms) {
      const v = obj(views[r]);
      if (v.package_note && this.sc.reforms.length > 1) {
        this.say("result_status_note", { note: str(v.package_note) });
        this.citeStatus(r, v);
      } else if (v.shown && v.partial) {
        this.say("result_partial", { title: str(v.title), label: str(v.label) });
        this.citeStatus(r, v);
      }
    }
    this.cite(base);
  }

  async baseline() {
    const run = await this.loadRun({ ...this.sc, reforms: [] });
    this.link = `#/state?${scenarioQuery({ ...this.sc, reforms: [] })}`;
    this.vals.link = this.link;
    const base = numberView(run, "baseline");
    if (!ranged(base)) throw new UnavailableError("baseline");
    const st = this.sc.state;
    const v = obj(await this.maybe(() => this.reader.validation(st)));
    const years = obj(await this.maybe(() => this.reader.permitYears(st)));
    const view = obj(v.contest_view);
    const rule = str(view.used_name) || str(v.chosen_rule) || "see the record";
    const b = base.shown;
    this.say("baseline", { b10: b.p10, b50: b.p50, b90: b.p90, rule_name: rule });
    this.cite(base);
    const card = obj(v.baseline_card);
    if (size(card)) {
      this.say("baseline_rule", { rule_text: str(card.rule_text) });
      if (str(card.unit_fields_label)) this.say("baseline_classes", { unit_fields: card.unit_fields_label });
      this.cite({
        id: `baseline-card:${st}`, kind: "baseline-card",
        name: str(card.rule_text), unit: "units",
        shown: str(card.unit_fields_label),
        formula: str(card.formula),
        years: obj(card.historical_years),
        source_ids: lst(card.sources),
        assumption_ids: lst(card.assumption_ids),
      });
    }
    let past = lst(years.state_past).filter(isObj);
    const wanted = lst(obj(card.historical_years).level_years);
    if (wanted.length === 2) past = past.filter((p) => wanted[0] <= (p.year ?? 0) && (p.year ?? 0) <= wanted[1]);
    else past = past.slice(-5);
    if (past.length) {
      this.say("baseline_past", { past: past.map((p) => `${p.year}: ${str(p.shown)}`).join("; ") });
      this.cite({
        id: `permit-years:${st}:state_past`, kind: "permit-years",
        name: str(years.title) || "homes permitted by year",
        unit: "units", shown: past.map((p) => ({ year: val(p.year), shown: str(p.shown) })),
        formula: str(years.formula),
        source_ids: lst(years.source_ids), assumption_ids: [],
      });
    }
    const stateRange = obj(obj(v.reliability).state_range);
    if (str(stateRange.sentence)) {
      this.say("baseline_reliability", { sentence: stateRange.sentence });
      this.cite({
        id: `validation:${st}:reliability.state_range`, kind: "validation",
        name: str(stateRange.label), unit: "cases",
        shown: stateRange.sentence,
        cases: val(stateRange.cases), covered: val(stateRange.covered),
        formula: str(obj(v.reliability).source),
        source_ids: [], assumption_ids: [],
      });
    }
    const diags = obj(card.diagnostics);
    for (const did of Object.keys(diags).sort()) {
      const d = obj(diags[did]);
      this.say("baseline_offer", { label: str(d.label), id: did, status: str(d.status) });
      this.cite({
        id: `baseline-card:${did}`, kind: "diagnostic", name: str(d.label),
        unit: "units", shown: str(d.formula), formula: str(d.formula),
        use: str(d.use), source_ids: lst(d.sources),
        assumption_ids: lst(d.assumption_ids),
      });
    }
  }

  async drivers() {
    const reforms = this.sc.reforms;
    if (reforms.length !== 1) {
      this.status = "unsupported";
      this.say("drivers_package");
      return;
    }
    const run = await this.loadRun(this.sc, true);
    const d = obj(obj(obj(run.explanation).drivers)[reforms[0]]);
    const items = lst(d.items).filter(isObj).slice(0, 3);
    if (!items.length) {
      this.status = "unknown";
      this.say("drivers_none");
      return;
    }
    const lead = str(obj(d.lead).text);
    if (lead) this.say("drivers_lead", { lead });
    for (const item of items) {
      const text = obj(item.text);
      this.say("drivers_item", { text: str(text.text) });
      this.cite({
        id: `run:${str(item.ref)}`, kind: "driver",
        name: str(item.label), unit: str(item.unit),
        shown: lst(text.cites).filter(isObj).map((c) => str(c.shown)),
        parameter: str(item.parameter),
        low: val(item.low), central: val(item.central), high: val(item.high),
        total_low: val(item.total_low), total_central: val(item.total_central),
        total_high: val(item.total_high),
        formula: lead, source_ids: [], assumption_ids: [str(item.parameter)],
      });
    }
  }

  async lawRows(reform) {
    const impl = obj(await this.reader.implementation(this.sc.state, reform, this.county));
    return [impl, lst(impl.rows).filter(isObj)];
  }

  citeRow(row) {
    const legal = obj(row.legal);
    const rec = obj(legal.record);
    const view = {
      id: `law:${str(rec.record_id) || str(row.provision_id)}`,
      kind: "law", name: str(row.citation), unit: "",
      shown: str(row.line), status: str(legal.state),
      level: str(row.level), jurisdiction: str(row.jurisdiction),
      scope: str(row.scope), start: val(rec.start), end: val(rec.end),
      verified_on: val(rec.verified_on), agency: str(rec.agency),
      note: str(rec.note), formula: "",
      source_ids: [], assumption_ids: [], urls: lst(rec.sources),
    };
    this.cite(view);
    for (const url of view.urls) pushUrl(this.sources, url, view.agency || view.name);
    return view;
  }

  async law() {
    const reforms = this.sc.reforms;
    if (reforms.length !== 1) {
      this.status = "unsupported";
      this.say("law_package");
      return;
    }
    await this.tryRun();
    const [impl, rows] = await this.lawRows(reforms[0]);
    if (!rows.length) {
      this.status = "unknown";
      this.say("law_none", { none_text: str(impl.none_text) });
      this.cite({
        id: `law:${this.sc.state}:${reforms[0]}:none`, kind: "law",
        name: str(impl.source), unit: "", shown: str(impl.none_text),
        on: val(impl.on), formula: "", source_ids: [], assumption_ids: [],
      });
      return;
    }
    let pending = false;
    for (const row of rows.slice(0, 5)) {
      const view = this.citeRow(row);
      const values = {
        citation: view.name, level: view.level, line: view.shown,
        start: view.start, verified_on: view.verified_on || "not recorded",
        scope: view.scope || "not recorded",
      };
      this.say(view.start ? "law_row" : "law_row_nodate", values);
      pending = pending || Boolean(row.pending);
    }
    if (pending && str(impl.pending_note)) this.say("law_pending", { pending_note: impl.pending_note });
  }

  /** Reform notes that say a result is a data gap: [reform, note index, note]. */
  gapNotes(run) {
    const words = this.t.coverage_note_words.map((w) => w.toLowerCase());
    const out = [];
    for (const r of this.sc.reforms) {
      const notes = lst(obj(obj(obj(run || {}).reform_results)[r]).notes);
      notes.forEach((note, i) => {
        if (typeof note === "string" && words.some((w) => note.toLowerCase().includes(w))) out.push([r, i, note]);
      });
    }
    return out;
  }

  async coverage(zero = false) {
    this.intent = "coverage";
    this.status = "unknown";
    const run = zero ? this.run : await this.tryRun();
    if (zero) this.say("coverage_zero");
    for (const r of this.sc.reforms) {
      const v = obj(obj(obj(run).status_views)[r]);
      if (v.shown === false) {
        this.say("coverage_status", { status_text: str(v.text) });
        this.citeStatus(r, v);
      }
    }
    for (const [rid, i, note] of this.gapNotes(run)) {
      this.say("coverage_note", { note });
      this.cite({
        id: `run:reform_results.${rid}.notes.${i}`, kind: "note",
        name: this.titleOf([rid]), unit: "", shown: note, formula: "",
        source_ids: [], assumption_ids: [],
      });
    }
    const card = obj(await this.maybe(() => this.reader.reportCard(this.sc.state)));
    const want = this.sc.reforms.length ? this.sc.reforms : ["baseline"];
    const bad = [...this.t.not_observed];
    const gaps = lst(card.inputs).filter(
      (i) => isObj(i) && bad.includes(i.value_state) && [...want, "all reforms"].some((r) => lst(i.reforms).includes(r)),
    );
    if (!gaps.length) {
      if (size(card)) this.say("coverage_none");
      return;
    }
    const first = gaps.find((g) => g.value_state === "missing") || gaps[0];
    const ordered = [first, ...gaps.filter((g) => g !== first)].slice(0, 3);
    ordered.forEach((g, n) => {
      let limit = lst(g.limits).find((x) => typeof x === "string") || "";
      limit = limit.replace(/\.+$/, "");
      const values = { label: str(g.label), value_state: str(g.value_state), limit };
      this.say(n === 0 ? "coverage_next" : "coverage_gap", values);
      this.cite({
        id: `report-card:${this.sc.state}:${str(g.id)}`, kind: "report-card",
        name: str(g.label), unit: "", shown: limit,
        status: str(g.status), value_state: str(g.value_state),
        coverage: val(g.coverage), limits: lst(g.limits), formula: "",
        source_ids: lst(g.sources), assumption_ids: [],
      });
    });
  }

  async target() {
    const reforms = this.sc.reforms;
    const budget = this.t.budget_words.some((w) => has(this.q.norm, w));
    const onlyAdu = reforms.length === 0 || (reforms.length === 1 && reforms[0] === "adu");
    if (budget && onlyAdu && (await this.budgetTarget())) return;
    if (reforms.length !== 1) {
      this.status = "unsupported";
      this.say("target_package");
      return;
    }
    const run = await this.tryRun();
    const alone = obj(obj(obj(run).status_views)[reforms[0]]);
    if (alone.shown === false) {
      // No dial level has a statewide range to read (transit status): the status text.
      this.status = "unknown";
      this.say("result_status", { status_text: str(alone.text) });
      this.citeStatus(reforms[0], alone);
      return;
    }
    const dials = obj(await this.reader.dials(this.sc.state));
    const block = obj(dials.dials);
    const chosen = Object.values(obj(block.dials)).map(obj).filter((d) => d.reform === reforms[0]);
    if (!chosen.length) {
      this.status = "unknown";
      this.say("target_none");
      return;
    }
    const rows0 = lst(obj(chosen[0].targets).rows).filter(isObj);
    const targets = rows0.map((r) => str(r.homes_text)).join(", ");
    this.cite({
      id: `dials:${this.sc.state}:${str(chosen[0].key)}.targets`,
      kind: "dials", name: str(chosen[0].title), unit: "homes",
      shown: rows0.map((r) => str(r.homes_text)),
      formula: str(obj(chosen[0].targets).rule),
      seed: val(block.seed), draws: val(block.draws),
      source_ids: [], assumption_ids: lst(obj(chosen[0].targets).assumption_ids),
    });
    const want = firstNumber(this.q.rest);
    if (want === null) {
      this.status = "unknown";
      this.say("target_ask_number", { targets });
      return;
    }
    const hits = [];
    for (const d of chosen) {
      const rows = lst(obj(d.targets).rows).filter(isObj);
      const row = rows.find((r) => r.homes === want);
      if (row !== undefined) hits.push([d, row]);
    }
    if (!hits.length) {
      this.status = "out_of_range";
      this.say("target_rows_only", { targets });
      return;
    }
    let reached = false;
    for (const [d, row] of hits) {
      const p50 = obj(row.p50);
      const p10 = obj(row.p10);
      reached = reached || p50.status !== "out_of_reach";
      this.say("target_row", { dial: str(d.title), p50: str(p50.text), p10: str(p10.text) });
      this.cite({
        id: `dials:${this.sc.state}:${str(d.key)}.targets.${str(row.homes_text)}`,
        kind: "dials", name: str(d.title), unit: str(d.unit),
        shown: { homes: str(row.homes_text), p50: str(p50.text), p10: str(p10.text) },
        status: { p50: str(p50.status), p10: str(p10.status) },
        headline: str(d.headline),
        formula: str(obj(d.targets).rule),
        source_ids: [], assumption_ids: lst(obj(d.targets).assumption_ids),
      });
    }
    if (!reached) this.status = "out_of_range";
    this.say("target_head", { headline: str(hits[0][0].headline) });
    this.say("target_trials", { draws: block.draws, seed: block.seed, rule: str(obj(hits[0][0].targets).rule) });
    if (this.t.budget_words.some((w) => has(this.q.norm, w))) this.say("target_budget");
  }

  /** A target-and-budget question about ADUs, read from the budget grid (budget_target in Python). */
  async budgetTarget() {
    let held;
    try {
      held = obj(await this.reader.budgetGrid(this.sc.state));
    } catch (err) {
      if (err instanceof UnavailableError) return false;
      throw err;
    }
    const grid = obj(held.grid);
    const view = obj(held.view);
    if (!lst(grid.cells).length || !lst(view.cells).length) return false;
    if (!(this.sc.reforms.length === 1 && this.sc.reforms[0] === "adu")) {
      this.sc = { ...this.sc, reforms: ["adu"] };
      this.vals.scenario_title = this.titleOf(["adu"]);
    }
    await this.tryRun();
    const texts = Object.fromEntries(lst(view.cells).filter(isObj).map((c) => [c.key, c]));
    const opts = obj(view.options);
    const ttext = new Map(lst(opts.targets).map((o) => [Number(o.value), o.text]));
    const btext = new Map(lst(opts.budgets).map((o) => [Number(o.value), o.text]));
    const targets = [...ttext.values()].join(", ");
    this.cite({
      id: `budget-grid:${this.sc.state}`, kind: "budget-grid",
      name: str(grid.label), unit: "homes",
      shown: { targets: [...ttext.values()], budgets: [...btext.values()] },
      formula: str(view.scope), seed: val(view.seed),
      draws: val(view.draws), source_ids: [],
      assumption_ids: lst(view.assumption_ids),
    });
    const [money, rest] = moneyIn(this.q.rest);
    const want = firstNumber(rest);
    this.say("budget_head", { scope: str(view.scope) });
    if (want === null) {
      this.status = "unknown";
      this.say("budget_ask_number", { targets });
      return true;
    }
    const q = gridLookup(grid, { target: want, budget: money });
    const flags = lst(q.flags);
    const words = obj(view.flags);
    const loHi = lst(obj(q.brackets).target);
    if (flags.includes("outside_tested_range") && !lst(q.options).length && !loHi.length) {
      this.status = "out_of_range";
      this.say("budget_outside", { targets });
      return true;
    }
    if (loHi.length) {
      this.status = "out_of_range";
      this.say("budget_bracket", { lo: ttext.get(Number(loHi[0])) ?? "", hi: ttext.get(Number(loHi[1])) ?? "" });
      for (const end of loHi) this.budgetLines(grid, texts, ttext, Number(end), money);
    } else {
      this.budgetLines(grid, texts, ttext, Number(want), money);
    }
    for (const f of flags) {
      if (["budget_exceeded", "no_tested_option_meets_both", "outside_tested_range"].includes(f)) {
        this.say("budget_flag", { flag: str(words[f]) });
      }
    }
    if (money !== null && btext.has(Number(money))) this.say("budget_ceiling", { budget: btext.get(Number(money)) });
    else if (money !== null) this.say("budget_ceiling_untested", { budgets: [...btext.values()].join(", ") });
    this.say("budget_trials", { draws: view.draws_text, seed: view.seed });
    this.say("budget_uncapped", { note: str(view.uncapped_note) });
    return true;
  }

  /** The tested options on the cost / share trade-off for one tested target. */
  budgetLines(grid, texts, ttext, target, money) {
    const q = gridLookup(grid, { target, budget: money });
    const tkey = targetKey(target);
    const pareto = lst(q.pareto)
      .map((o, i) => [o, i])
      .sort((x, y) => x[0].net_public_cost_p50 - y[0].net_public_cost_p50 || x[1] - y[1])
      .map(([o]) => o);
    this.say("budget_target", { target: ttext.get(target) ?? "" });
    if (!pareto.length) {
      this.say("budget_no_pareto");
      return;
    }
    for (const o of pareto.length <= 4 ? pareto : [...pareto.slice(0, 2), ...pareto.slice(-2)]) {
      const key = cellKey(o);
      const c = obj(texts[key]);
      const t = obj(obj(c.targets)[tkey]);
      const cost = obj(obj(c.net_cost).shown);
      const added = obj(c.added);
      const shown = {
        share: str(t.share_text), se: str(t.se_text),
        net_cost: str(cost.p50), added: str(added.likely),
        appropriation: str(c.appropriation_text),
        award: str(c.award_text),
      };
      this.say(c.arm === "law_only" ? "budget_option_law" : "budget_option", { arm: str(c.arm_word), ...shown });
      this.cite({
        id: `budget-grid:${this.sc.state}:${key}:${tkey}`, kind: "budget-grid",
        name: str(c.label), unit: "homes", shown,
        formula: str(added.formula), source_ids: [],
        assumption_ids: [],
      });
    }
  }

  async address() {
    this.status = "unsupported";
    await this.tryRun();
    this.say("address");
    if (!this.county) this.say("address_pick");
    let shown = 0;
    for (const r of this.county ? this.sc.reforms : []) {
      const rows = (await this.maybe(async () => (await this.lawRows(r))[1])) || [];
      for (const row of rows) {
        const rec = obj(obj(row.legal).record);
        const urls = lst(rec.sources).filter((u) => typeof u === "string");
        if (shown >= 3 || row.level === "state" || !urls.length) continue;
        const view = this.citeRow(row);
        this.say("address_rule", { citation: view.name, agency: view.agency || view.jurisdiction, url: urls[0] });
        shown += 1;
      }
    }
    this.say("address_ask");
  }

  async share() {
    const run = await this.loadRun();
    this.say("share", { run_id: this.runId });
    const key = this.sc.reforms.length ? this.headlineKeys()[0] : "baseline";
    const view = numberView(run, key);
    if (ranged(view)) {
      const s = view.shown;
      this.say("share_record", { name: view.name, p10: s.p10, p50: s.p50, p90: s.p90 });
      this.cite(view);
    }
    this.download = (await this.reader.download(this.sc)) || "";
    if (this.download) this.say("share_download", { download: this.download });
    else this.say("share_no_download");
  }

  outside() {
    this.status = "unsupported";
    this.say("outside");
    this.offer();
  }

  unsupported() {
    this.intent = "unsupported";
    this.status = "unsupported";
    this.say("unsupported");
    this.offer();
  }

  offer() {
    const s = this.t.suggestions;
    this.offers = [...(s[this.sc.state] || s.default)];
  }

  unavailable() {
    this.status = "unavailable";
    this.lines = [];
    this.records = [];
    this.sources = [];
    this.runId = "";
    this.period = "";
    this.download = "";
    const r = this.sc.reforms;
    const reason = size(this.sc.overrides) ? "custom" : r.length === 1 ? "scenario" : r.length ? "combo" : "missing";
    this.say(`unavailable_${reason}`);
  }

  done() {
    const title = this.t.templates.title;
    const name = this.status === "unavailable" ? "unavailable" : this.intent;
    return {
      intent: this.intent,
      status: this.status,
      state: this.sc.state,
      state_name: this.vals.state_name,
      reforms: [...this.sc.reforms],
      title: fill(title[name] || title.unsupported, this.vals),
      lines: this.lines,
      text: this.lines.join(" "),
      records: this.records,
      sources: this.sources,
      run_id: this.runId,
      period: this.period,
      link: this.link,
      download: this.download,
      offers: this.offers,
    };
  }
}
