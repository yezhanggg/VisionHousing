/**
 * The intro page: what the tool is, the questions it answers, a few live figures from the
 * engine, how it works, why the numbers can be trusted, and a button that opens the tool.
 *
 * Sections, top to bottom (each is its own small component below):
 *   Hero       name, promise, one line on what it shows, "Open the tool". Behind it, a field
 *              of columns rising out of the ground, an echo of the 3D county map. The columns
 *              are drawing only: their heights are a fixed pattern, not data.
 *   Figures    three or four headline figures for the state: the baseline range, the homes
 *              all five reforms add, the change from the baseline, and the size of the map.
 *              Every figure is the engine's own text (POST /run's explanation and
 *              GET /states/{st}/summary, through lib/api.js), counted up when the section comes
 *              into view and rolled to the new value when the state changes (ui/motion.js).
 *   Questions  the two questions the tool answers.
 *   How        data, model, ranges.
 *   Trust      ranges not forecasts, every number traceable, no AI inside, open source.
 *   Closing    the button again.
 *
 * Copy lives in the constants at the top, so wording can change without touching layout.
 * Motion: a staggered entrance, a slow rise of the columns, sections that fade in as they
 * scroll into view, and a short exit before the tool opens. All of it is off when the reader
 * asks for reduced motion. Styles: css/intro.css (imported here).
 *
 * `IntroScreen({ onOpen, onTour })`: `onOpen()` opens the tool. Without it the button follows
 * its own link (the State screen). `onTour()` opens the tool with the guided tour running;
 * without it the "Take the tour" button is not shown.
 */
import "../../css/intro.css";
import { BENCHMARK_IDS } from "../content/reforms.js";
import { stateName } from "../content/states.js";
import { api } from "../lib/api.js";
import { useJson, useRun } from "../lib/data.js";
import { html, useEffect, useRef, useState } from "../lib/html.js";
import { hrefFor } from "../state/store.js";
import { Icon, Mark } from "../ui/icons.js";
import { countUp, reducedMotion, Ticker } from "../ui/motion.js";

/* ------------------------------------------------------------------ copy */

/** The state the figures open on. */
const START_STATE = "PA";

const HERO = {
  eyebrow: "VisionHousing",
  title: "See what a housing reform would build",
  accent: "and what is already on the ground.",
  lede:
    "An open tool for state housing policy. Switch on a reform and see the likely range of homes it adds over five years, county by county, next to what today's rules would bring.",
  open: "Open the tool",
  openSub: "County map, five benchmark reforms, likely ranges",
  more: "How it works",
  tour: "Take the tour",
};

const FIGURES = {
  title: "The state at a glance",
  lede: "Live from the engine: today's rules, then all five benchmark reforms switched on together.",
  stateLabel: "State",
  sizeLabel: "Modelled on",
  loading: "Loading the engine's figures…",
  failed: "The figures did not load. The tool itself may still open.",
};

const QUESTIONS = {
  title: "Two questions, answered side by side",
  items: [
    {
      kicker: "Question 1",
      title: "What would a reform build?",
      text:
        "Pick accessory dwelling units, missing middle, transit-oriented density, parking reduction or a fee cut, alone or together. See the extra homes permitted over five years as a likely range, with what drives it and what the model cannot say.",
    },
    {
      kicker: "Question 2",
      title: "What is already on the ground?",
      text:
        "Every result sits on a baseline: the homes the state would likely permit under today's rules and economics. Open any county or census tract for a plain profile of what is there now.",
    },
  ],
};

const HOW = {
  title: "How it works",
  steps: [
    {
      n: "1",
      title: "Data",
      text: "Public sources only: building permits, parcels, transit stops and census tables, fetched by script from where they are published.",
    },
    {
      n: "2",
      title: "Model",
      text: "Simple, written-down rules: which lots a reform reaches, which of them would pencil out, and how many would be permitted. Every assumption sits in one ledger.",
    },
    {
      n: "3",
      title: "Ranges",
      text: "Thousands of seeded draws across the uncertain inputs give a low, middle and high value, so you see how sure the answer is.",
    },
  ],
};

const TRUST = {
  title: "Why you can check every number",
  items: [
    { title: "Ranges, not forecasts", text: "Every result is a likely range with its spread shown. Nothing is a single-point prediction." },
    { title: "Every number traceable", text: "Click a figure to see its formula, its inputs, its sources and the assumptions behind it." },
    { title: "No AI inside", text: "The results come from plain arithmetic on public data. No language model writes or changes a number." },
    { title: "Open source", text: "The code, the assumptions and the data scripts are public under the Apache-2.0 licence." },
  ],
};

const CLOSING = {
  title: "Start with your state",
  text: "The map opens on the baseline. Switch on a reform to see what changes.",
};

/* ------------------------------------------------------------------ helpers */

/**
 * "67,000 units" and "180,000 units" -> "67,000–180,000 units": the engine's two texts with
 * the shared unit said once. If the texts do not share a unit, both are kept whole.
 */
function rangeText(shown) {
  if (!shown || shown.p10 === undefined || shown.p90 === undefined) return "";
  const lo = String(shown.p10);
  const hi = String(shown.p90);
  const unit = (s) => s.replace(/^[\d,.\s-]+/, "");
  const u = unit(hi);
  if (u && unit(lo) === u) return `${lo.slice(0, lo.length - u.length).trim()}–${hi}`;
  return `${lo} to ${hi}`;
}

/** Add `is-in` to `.intro-reveal` elements inside `root` the first time each comes into view. */
function useScrollReveal(root) {
  useEffect(() => {
    const el = root.current;
    if (!el) return undefined;
    const items = [...el.querySelectorAll(".intro-reveal")];
    if (reducedMotion() || typeof IntersectionObserver === "undefined") {
      items.forEach((n) => n.classList.add("is-in"));
      return undefined;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          e.target.classList.add("is-in");
          io.unobserve(e.target);
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -8% 0px" },
    );
    items.forEach((n) => io.observe(n));
    return () => io.disconnect();
  }, []);
}

/* ------------------------------------------------------------------ hero backdrop */

const GRID = 11; // columns per side of the field
const TILE_W = 64; // width of one tile's diamond
const TILE_H = 32; // height of one tile's diamond
const MAX_RISE = 190; // tallest column, in drawing units
const RAMP = 6; // steps of the map's colour ramp (tokens --seq-1 .. --seq-6)

/** A fixed, made-up skyline: two soft peaks plus a little seeded noise. Drawing only. */
function skyline() {
  let seed = 7;
  const rand = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  const peak = (i, j, ci, cj, r) => Math.exp(-((i - ci) ** 2 + (j - cj) ** 2) / (2 * r * r));
  const cols = [];
  for (let i = 0; i < GRID; i += 1) {
    for (let j = 0; j < GRID; j += 1) {
      const k = Math.min(1, 0.08 + 0.85 * peak(i, j, 4, 5, 2.2) + 0.5 * peak(i, j, 8, 2, 1.6) + 0.18 * rand());
      if (k < 0.12) continue;
      cols.push({ i, j, k });
    }
  }
  // Painter's order: back to front.
  return cols.sort((a, b) => a.i + a.j - (b.i + b.j) || a.i - b.i);
}

const COLUMNS = skyline();

function Column({ i, j, k, n }) {
  const x = (i - j) * (TILE_W / 2);
  const y = (i + j) * (TILE_H / 2);
  const h = Math.round(MAX_RISE * k);
  const hw = TILE_W / 2 - 3;
  const hh = TILE_H / 2 - 1.5;
  // Corners of the column's footprint on the ground (top, right, bottom, left) and its roof.
  const top = (dy) => `${x},${y - hh - dy} ${x + hw},${y - dy} ${x},${y + hh - dy} ${x - hw},${y - dy}`;
  const left = `${x - hw},${y} ${x},${y + hh} ${x},${y + hh - h} ${x - hw},${y - h}`;
  const right = `${x + hw},${y} ${x},${y + hh} ${x},${y + hh - h} ${x + hw},${y - h}`;
  const silhouette = `${x},${y - hh - h} ${x + hw},${y - h} ${x + hw},${y} ${x},${y + hh} ${x - hw},${y} ${x - hw},${y - h}`;
  const tone = Math.min(RAMP, 1 + Math.floor(k * RAMP));
  const dist = Math.hypot(i - 4, j - 5);
  return html`<g class=${`intro-col tone-${tone}`} clip-path=${`url(#intro-clip-${n})`}
    style=${`--rise:${h};--delay:${Math.round(260 + dist * 70)}ms;--sway:${(n % 7) * 0.9}s`}>
    <clipPath id=${`intro-clip-${n}`}><polygon points=${silhouette} /></clipPath>
    <g class="intro-col-body">
      <polygon class="face-left" points=${left} />
      <polygon class="face-right" points=${right} />
      <polygon class="face-top" points=${top(h)} />
    </g>
  </g>`;
}

/** The field of columns behind the hero. Decorative: hidden from assistive technology. */
function Backdrop() {
  const w = GRID * TILE_W;
  const h = GRID * TILE_H + MAX_RISE + 40;
  return html`<div class="intro-stage" aria-hidden="true">
    <svg class="intro-field" viewBox=${`${-w / 2} ${-MAX_RISE - 30} ${w} ${h}`} preserveAspectRatio="xMidYMid slice"
      focusable="false">
      <g class="intro-ground">
        ${Array.from({ length: GRID + 1 }, (_, a) => {
          const s = a - 0.5;
          const e = GRID - 0.5;
          const p = (i, j) => `${(i - j) * (TILE_W / 2)},${(i + j) * (TILE_H / 2)}`;
          return html`<polyline points=${`${p(s, -0.5)} ${p(s, e)}`} /><polyline points=${`${p(-0.5, s)} ${p(e, s)}`} />`;
        })}
      </g>
      ${COLUMNS.map((c, n) => html`<${Column} key=${n} ...${c} n=${n} />`)}
    </svg>
    <div class="intro-stage-fade"></div>
  </div>`;
}

/* ------------------------------------------------------------------ open button */

/**
 * The link that opens the tool. A plain link to the State screen (it works in a new tab and
 * with no script hooks); a normal click plays the exit, then calls `onOpen`.
 */
function OpenLink({ onOpen, onLeave, big = false, sub = "" }) {
  const href = hrefFor("state");
  const click = (e) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    onLeave(() => (onOpen ? onOpen() : (location.hash = href)));
  };
  return html`<a class=${`intro-open ${big ? "is-big" : ""}`} href=${href} onClick=${click}>
    <span class="intro-open-text">
      <span class="intro-open-title">${HERO.open}</span>
      ${sub && html`<span class="intro-open-sub">${sub}</span>`}
    </span>
    <span class="intro-open-arrow"><${Icon} name="chevronRight" size=${big ? 26 : 20} /></span>
  </a>`;
}

/* ------------------------------------------------------------------ sections */

function Hero({ onOpen, onLeave, onMore, onTour }) {
  return html`<section class="intro-hero" aria-labelledby="intro-title">
    <${Backdrop} />
    <div class="intro-hero-copy">
      <p class="intro-eyebrow intro-in" style="--i:0"><${Mark} /> <span>${HERO.eyebrow}</span></p>
      <h1 class="intro-title" id="intro-title">
        <span class="intro-in" style="--i:1">${HERO.title}</span>
        <span class="intro-title-accent intro-in" style="--i:2">— ${HERO.accent}</span>
      </h1>
      <p class="intro-lede intro-in" style="--i:3">${HERO.lede}</p>
      <div class="intro-actions intro-in" style="--i:4">
        <${OpenLink} big onOpen=${onOpen} onLeave=${onLeave} sub=${HERO.openSub} />
        <button type="button" class="intro-more" onClick=${onMore}>
          ${HERO.more} <${Icon} name="chevronDown" size=${16} />
        </button>
        ${onTour && html`<button type="button" class="intro-more intro-tour" onClick=${() => onLeave(onTour)}>${HERO.tour}</button>`}
      </div>
    </div>
  </section>`;
}

/** One headline figure: the engine's label and its text, with a line under it. */
function Figure({ label, text, sub, i }) {
  return html`<div class="intro-fig" style=${`--i:${i}`}>
    <dt class="intro-fig-label">${label}</dt>
    <dd class="intro-fig-value"><${Ticker} text=${text} live=${i === 0} /></dd>
    ${sub && html`<dd class="intro-fig-sub">${sub}</dd>`}
  </div>`;
}

function StateSwitch({ value, onChange }) {
  const list = useJson("states", () => api.states());
  const states = (list.data?.states || []).filter((s) => !s.synthetic);
  if (states.length < 2) return null;
  return html`<div class="intro-states" role="group" aria-label=${FIGURES.stateLabel}>
    ${states.map(
      (s) => html`<button type="button" class="intro-state" aria-pressed=${s.code === value ? "true" : "false"}
        onClick=${() => onChange(s.code)}>${s.name || stateName(s.code)}</button>`,
    )}
  </div>`;
}

function Figures() {
  const [st, setSt] = useState(START_STATE);
  const run = useRun({ state: st, reforms: BENCHMARK_IDS, overrides: {} });
  const summary = useJson(`summary:${st}`, () => api.summary(st));
  const box = useRef(null);
  const counted = useRef(false);
  const ex = run.data?.explanation;
  const numbers = ex?.headline?.numbers || [];
  const ready = numbers.length > 0;

  const figs = numbers.map((n) => ({
    key: n.key,
    label: n.label?.text || "",
    text: n.shown?.p10 !== undefined ? rangeText(n.shown) : String(n.shown?.value ?? ""),
    sub: n.shown?.p50 !== undefined ? `middle ${n.shown.p50}` : "",
  }));
  const s = summary.data;
  if (s && Number.isFinite(s.county_count)) {
    figs.push({
      key: "size",
      label: FIGURES.sizeLabel,
      text: `${s.county_count.toLocaleString("en-US")} counties`,
      sub: Number.isFinite(s.tracts) ? `${s.tracts.toLocaleString("en-US")} census tracts` : "",
    });
  }

  // Count the figures up the first time they are on screen with data.
  useEffect(() => {
    const el = box.current;
    if (!el || !ready || counted.current) return undefined;
    const go = () => {
      counted.current = true;
      el.querySelectorAll(".intro-fig-value .tick").forEach((n) => countUp(n));
    };
    if (reducedMotion() || typeof IntersectionObserver === "undefined") {
      counted.current = true;
      return undefined;
    }
    const io = new IntersectionObserver((e) => {
      if (e.some((x) => x.isIntersecting)) {
        io.disconnect();
        go();
      }
    }, { threshold: 0.3 });
    io.observe(el);
    return () => io.disconnect();
  }, [ready]);

  const where = ex?.stamp?.state_name || stateName(st);
  return html`<section class="intro-section intro-figures" aria-labelledby="intro-figures-title">
    <div class="intro-wrap intro-reveal">
      <div class="intro-figures-head">
        <div>
          <h2 class="intro-h2" id="intro-figures-title">${FIGURES.title}</h2>
          <p class="intro-p">${FIGURES.lede}</p>
        </div>
        <${StateSwitch} value=${st} onChange=${setSt} />
      </div>
      <div class="intro-board" ref=${box} aria-busy=${run.loading ? "true" : "false"}>
        <p class="intro-board-head">
          <strong>${where}</strong>${ex?.span && html`<span>${ex.span}</span>`}
        </p>
        ${ready
          ? html`<dl class="intro-figs">${figs.map((f, i) => html`<${Figure} key=${f.key} ...${f} i=${i} />`)}</dl>`
          : html`<p class="intro-board-note" role="status">${run.error ? FIGURES.failed : FIGURES.loading}</p>`}
        ${ex?.headline?.sentence?.text && html`<p class="intro-sentence">${ex.headline.sentence.text}</p>`}
        ${ex?.stamp?.disclaimer && html`<p class="intro-stamp">${ex.stamp.disclaimer}</p>`}
      </div>
    </div>
  </section>`;
}

function Questions() {
  return html`<section class="intro-section" aria-labelledby="intro-q-title">
    <div class="intro-wrap">
      <h2 class="intro-h2 intro-reveal" id="intro-q-title">${QUESTIONS.title}</h2>
      <div class="intro-questions">
        ${QUESTIONS.items.map(
          (q, i) => html`<article class="intro-q intro-reveal" style=${`--i:${i}`}>
            <p class="intro-kicker">${q.kicker}</p>
            <h3 class="intro-h3">${q.title}</h3>
            <p class="intro-p">${q.text}</p>
          </article>`,
        )}
      </div>
    </div>
  </section>`;
}

function How({ anchor }) {
  return html`<section class="intro-section" aria-labelledby="intro-how-title" ref=${anchor} tabindex="-1">
    <div class="intro-wrap">
      <h2 class="intro-h2 intro-reveal" id="intro-how-title">${HOW.title}</h2>
      <ol class="intro-steps intro-reveal">
        ${HOW.steps.map(
          (s, i) => html`<li class="intro-step" style=${`--i:${i}`}>
            <span class="intro-step-n" aria-hidden="true">${s.n}</span>
            <h3 class="intro-h3">${s.title}</h3>
            <p class="intro-p">${s.text}</p>
          </li>`,
        )}
      </ol>
    </div>
  </section>`;
}

function Trust() {
  return html`<section class="intro-section intro-trust" aria-labelledby="intro-trust-title">
    <div class="intro-wrap">
      <h2 class="intro-h2 intro-reveal" id="intro-trust-title">${TRUST.title}</h2>
      <ul class="intro-trust-list">
        ${TRUST.items.map(
          (t, i) => html`<li class="intro-trust-item intro-reveal" style=${`--i:${i}`}>
            <h3 class="intro-h3">${t.title}</h3>
            <p class="intro-p">${t.text}</p>
          </li>`,
        )}
      </ul>
    </div>
  </section>`;
}

function Closing({ onOpen, onLeave }) {
  return html`<section class="intro-section intro-closing" aria-labelledby="intro-closing-title">
    <div class="intro-wrap intro-closing-box intro-reveal">
      <div>
        <h2 class="intro-h2" id="intro-closing-title">${CLOSING.title}</h2>
        <p class="intro-p">${CLOSING.text}</p>
      </div>
      <${OpenLink} onOpen=${onOpen} onLeave=${onLeave} />
    </div>
  </section>`;
}

/* ------------------------------------------------------------------ page */

const LEAVE_MS = 360;

export function IntroScreen({ onOpen, onTour } = {}) {
  const root = useRef(null);
  const how = useRef(null);
  const leaving = useRef(false);
  useScrollReveal(root);

  useEffect(() => {
    document.body.classList.add("is-intro");
    return () => document.body.classList.remove("is-intro");
  }, []);

  /** Play the exit, then open the tool (inside a view transition where the browser has one). */
  const leave = (open) => {
    if (leaving.current) return;
    leaving.current = true;
    if (reducedMotion()) {
      open();
      return;
    }
    root.current?.classList.add("is-leaving");
    setTimeout(() => {
      if (document.startViewTransition) document.startViewTransition(() => open());
      else open();
      leaving.current = false;
    }, LEAVE_MS);
  };

  const more = () => {
    const el = how.current;
    if (!el) return;
    el.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
    el.focus({ preventScroll: true });
  };

  return html`<main id="main" class="screen intro" tabindex="-1" ref=${root}>
    <${Hero} onOpen=${onOpen} onLeave=${leave} onMore=${more} onTour=${onTour} />
    <${Figures} />
    <${Questions} />
    <${How} anchor=${how} />
    <${Trust} />
    <${Closing} onOpen=${onOpen} onLeave=${leave} />
  </main>`;
}
