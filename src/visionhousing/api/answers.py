"""The question box: answers a plain question from the records of the current run.

Plain-language summary
----------------------
A reader types a question ("Why does the Pennsylvania baseline look high?", "Is the
Massachusetts ADU reform already in place?"). This module answers it **without any language
model**. It works in three fixed steps:

1. **Match.** The question is lower-cased and stripped of punctuation. Words that name a state
   or a reform are picked out first. Each *intent* (share, address, law, target, coverage,
   drivers, baseline, result, outside) has a list of words in ``assumptions/answers.yaml``;
   the intent whose words appear most often wins (the file's order breaks a tie). No word
   at all means "unsupported", and the answer says so and lists what the box can answer.
2. **Read.** The intent names a fixed set of read-only records: the run's own
   ``explanation.numbers`` (the same records every screen shows), the baseline validation
   reply, the permit history, the implementation-status (law) rows, the precomputed policy
   dial targets and the data report card. Nothing is computed: a missing record makes the
   answer say "not in this run", never a guess.
3. **Fill.** A template from ``answers.yaml`` is filled with text copied from those records:
   their shown values (already rounded by the engine), names, units, the period, dates and
   sources. Each answer lists the records it cites (with their formulas and source ids), the
   run id (state, run key, the three hashes, seed and draws) and a canonical link.

The same resolver runs in the browser (``web/js/lib/answers.js``) over the same records,
read from the live interface or the static site's files. The browser reads its intent table
from ``web/js/content/answers.json``, which :func:`write_shared_table` writes from the YAML
(``uv run python -m visionhousing.api.answers``); a test checks the two match, and the gold
questions in ``tests/test_answers.py`` check that Python and the browser give the same answer.

The browser never sends a question anywhere: it answers in the page. ``POST
/api/v1/answer`` is for scripts and tests; it reads the same records through the app's own
addresses (in memory) and keeps nothing.

Readers
-------
A *reader* hands the resolver records and raises :class:`UnavailableError` for anything the
build does not hold. :class:`StaticReader` reads the static site's files exactly as
``web/js/lib/api.js`` does in static mode; :class:`LiveReader` asks the interface's own
addresses (the same ones the browser asks in live mode).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import yaml

from visionhousing.ledger.loader import ROOT

TABLE_PATH = ROOT / "assumptions" / "answers.yaml"
SHARED_PATH = ROOT / "web" / "js" / "content" / "answers.json"
BENCHMARKS = ["adu", "missing_middle", "tod", "parking", "fees"]
MAX_QUESTION = 500


# ------------------------------------------------------------------ the table


@lru_cache(maxsize=4)
def _read(path: str, mtime: int) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_table(path: Path = TABLE_PATH) -> dict[str, Any]:
    """Read ``assumptions/answers.yaml`` (cached until the file changes)."""
    p = Path(path)
    return _read(str(p), p.stat().st_mtime_ns)


def shared_table_text(table: Mapping[str, Any] | None = None) -> str:
    """The intent table as the JSON text the browser reads (``web/js/content/answers.json``)."""
    return json.dumps(table or load_table(), indent=1, ensure_ascii=False) + "\n"


def write_shared_table(path: Path = SHARED_PATH) -> Path:
    """Write the browser's copy of the intent table; returns its path."""
    path = Path(path)
    path.write_text(shared_table_text(), encoding="utf-8")
    return path


# ------------------------------------------------------------------ readers


class UnavailableError(Exception):
    """The build does not hold the records a question needs."""


class StaticReader:
    """Read the static site's files (``web/public/data`` or a copy) as the browser does.

    Args:
        load: Path inside the data folder (``"PA/index.json"``) to its JSON, packing undone.
            :meth:`from_folder` makes one from a folder.
        base: How the download link starts (the static site serves the folder at ``data/``).
    """

    def __init__(self, load: Callable[[str], Any], base: str = "data"):
        self._load = load
        self._base = base
        self._index: dict[str, Any] = {}

    @classmethod
    def from_folder(cls, folder: Path) -> StaticReader:
        """A reader over a data folder on disk (``web/public/data`` or an export)."""
        from visionhousing.export_static import read_saved

        root = Path(folder)

        def load(rel: str) -> Any:
            path = root / rel
            if not path.is_file():
                raise UnavailableError(rel)
            return read_saved(path)

        return cls(load)

    def _file(self, rel: str) -> Any:
        try:
            return self._load(rel)
        except UnavailableError:
            raise
        except Exception as exc:  # a missing or unreadable file: not in this build
            raise UnavailableError(rel) from exc

    def index(self, st: str) -> dict[str, Any]:
        """The state's ``index.json``: which runs and cards the build holds (read once)."""
        if st not in self._index:
            self._index[st] = self._file(f"{st}/index.json")
        return self._index[st]

    def _key(self, sc: Mapping[str, Any]) -> str:
        idx = self.index(sc["state"])
        key = run_key(sc["reforms"], sc["overrides"], idx.get("package") or [])
        saved = [*(idx.get("runs") or []), *(idx.get("extra_runs") or [])]
        if key in ("", "custom") or key not in saved:
            raise UnavailableError("run")
        return key

    def run(self, sc: Mapping[str, Any], drivers: bool = False) -> dict[str, Any]:
        """The saved run of a scenario (drivers are in the saved file when exported)."""
        idx = self.index(sc["state"])
        if idx.get("seed") != 0:
            raise UnavailableError("run")
        return self._file(f"{sc['state']}/runs/{self._key(sc)}.json")

    def validation(self, st: str) -> dict[str, Any]:
        """The baseline validation reply."""
        return self._file(f"{st}/validation.json")

    def permit_years(self, st: str) -> dict[str, Any]:
        """The permits by year (past and projected)."""
        if not self.index(st).get("permit_years"):
            raise UnavailableError("permit-years")
        return self._file(f"{st}/permit-years.json")

    def implementation(self, st: str, reform: str, geoid: str = "") -> dict[str, Any]:
        """The law rows of one reform covering a place (all places with no ``geoid``)."""
        if not self.index(st).get("implementation"):
            raise UnavailableError("implementation")
        every = self._file(f"{st}/implementation.json")
        rows = [
            r for r in every.get("rows") or []
            if r.get("reform") == reform and _row_covers(r, geoid)
        ]  # fmt: skip
        return {**every, "geoid": geoid or None, "reforms": [reform], "rows": rows}

    def dials(self, st: str) -> dict[str, Any]:
        """The policy dials, with their precomputed target rows."""
        if not self.index(st).get("comparisons"):
            raise UnavailableError("comparisons")
        return self._file(f"{st}/comparisons/dials.json")

    def report_card(self, st: str) -> dict[str, Any]:
        """The data report card."""
        return self._file(f"{st}/report-card.json")

    def budget_grid(self, st: str) -> dict[str, Any]:
        """The ADU budget-and-target grid with its text (``{grid, view}``), when exported."""
        if not self.index(st).get("adu_budget_grid"):
            raise UnavailableError("adu-budget-grid")
        return self._file(f"{st}/adu-budget-grid.json")

    def download(self, sc: Mapping[str, Any]) -> str:
        """The address of the run's CSV table, or ``""`` when the build has none."""
        try:
            idx = self.index(sc["state"])
            key = run_key(sc["reforms"], sc["overrides"], idx.get("package") or [])
        except UnavailableError:
            return ""
        if key in ("", "custom") or key not in (idx.get("exports") or []):
            return ""
        return f"{self._base}/{sc['state']}/exports/{key}.csv"


def _row_covers(row: Mapping[str, Any], geoid: str) -> bool:
    """As ``rowCovers`` in web/js/lib/api.js: a state row covers every place."""
    j = str(row.get("jurisdiction") or "")
    if not geoid or re.fullmatch(r"[A-Z]{2}", j):
        return True
    return geoid.startswith(j) or j.startswith(geoid)


class LiveReader:
    """Ask the interface's own addresses, the ones the browser asks in live mode.

    Args:
        call: ``call(method, path, body)`` returns ``(status, body bytes)``; the app's
            in-memory caller (:func:`app_caller`) or any HTTP client.
        api: The address prefix.
    """

    def __init__(self, call: Callable[[str, str, Any], tuple[int, bytes]], api: str = "/api/v1"):
        self._call = call
        self._api = api

    def _get(self, path: str, body: Any = None) -> Any:
        status, raw = self._call("POST" if body is not None else "GET", f"{self._api}{path}", body)
        if status != 200:
            raise UnavailableError(path)
        return json.loads(raw)

    def run(self, sc: Mapping[str, Any], drivers: bool = False) -> dict[str, Any]:
        """``POST /run`` for the scenario (seed 0, default draws)."""
        overrides = {
            k: (v if v in ("l", "c", "h") else float(v)) for k, v in (sc["overrides"] or {}).items()
        }
        body = {"state": sc["state"], "reforms": list(sc["reforms"]), "overrides": overrides,
                "seed": 0, "drivers": bool(drivers)}  # fmt: skip
        return self._get("/run", body)

    def validation(self, st: str) -> dict[str, Any]:
        """``GET /validation``."""
        return self._get(f"/validation?state={st}")

    def permit_years(self, st: str) -> dict[str, Any]:
        """``GET /states/ST/permit-years``."""
        return self._get(f"/states/{st}/permit-years")

    def implementation(self, st: str, reform: str, geoid: str = "") -> dict[str, Any]:
        """``GET /implementation/ST?r=&geoid=``."""
        qs = {"r": reform, **({"geoid": geoid} if geoid else {})}
        return self._get(f"/implementation/{st}?{urlencode(qs)}")

    def dials(self, st: str) -> dict[str, Any]:
        """``GET /states/ST/comparisons?part=dials``."""
        return self._get(f"/states/{st}/comparisons?part=dials")

    def report_card(self, st: str) -> dict[str, Any]:
        """``GET /report-card``."""
        return self._get(f"/report-card?state={st}")

    def budget_grid(self, st: str) -> dict[str, Any]:
        """``GET /states/ST/adu-budget-grid``: the grid and its text (``{grid, view}``)."""
        body = self._get(f"/states/{st}/adu-budget-grid")
        return {"grid": body.get("grid"), "view": body.get("view")}

    def download(self, sc: Mapping[str, Any]) -> str:
        """The CSV export address of the scenario."""
        return f"{self._api}/export/csv?{scenario_query(sc)}"


def app_caller(app: Any) -> Callable[[str, str, Any], tuple[int, bytes]]:
    """Call an ASGI app in memory: ``call(method, path, body) -> (status, body)``."""

    async def call(method: str, path: str, body: Any) -> tuple[int, bytes]:
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        target, _, query = path.partition("?")
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": target, "raw_path": target.encode(),
            "query_string": query.encode(), "root_path": "",
            "headers": [(b"host", b"answers"), (b"content-type", b"application/json"),
                        (b"content-length", str(len(raw)).encode())],
            "client": ("127.0.0.1", 0), "server": ("answers", 80),
        }  # fmt: skip
        sent = False
        status = 0
        chunks: list[bytes] = []

        async def receive() -> dict[str, Any]:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await app(scope, receive, send)
        return status, b"".join(chunks)

    def sync(method: str, path: str, body: Any = None) -> tuple[int, bytes]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(call(method, path, body))
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, call(method, path, body)).result()

    return sync


# ------------------------------------------------------------------ small helpers
# Each one has a twin of the same name in web/js/lib/answers.js.


def normalise(text: str) -> str:
    """Lower case, apostrophes dropped, digit commas joined, other punctuation to spaces."""
    t = str(text or "").lower().replace("\u2019", "").replace("'", "")
    t = re.sub(r"(\d),(?=\d)", r"\1", t)
    t = re.sub(r"[^a-z0-9$%]+", " ", t).strip()
    return f" {t} "


def has(norm: str, word: str) -> bool:
    """True when the normalised ``word`` stands as whole words in ``norm``."""
    w = normalise(word).strip()
    return bool(w) and f" {w} " in norm


def drop(norm: str, word: str) -> str:
    """``norm`` with every whole-word ``word`` taken out."""
    w = normalise(word).strip()
    while w and f" {w} " in norm:
        norm = norm.replace(f" {w} ", " ")
    return norm


def fill(template: str, values: Mapping[str, Any]) -> str:
    """Put each ``{name}`` of ``template`` from ``values`` (a missing name becomes empty)."""

    def put(m: re.Match[str]) -> str:
        v = values.get(m.group(1))
        return "" if v is None else str(v)

    return re.sub(r"\{(\w+)\}", put, template)


def run_key(reforms: list[str], overrides: Mapping[str, Any], package: list[str]) -> str:
    """``baseline``, one reform id, ``package``, ``custom`` (changed assumptions) or a mix."""
    if overrides:
        return "custom"
    if not reforms:
        return "baseline"
    if len(reforms) == 1:
        return reforms[0]
    if package and len(reforms) == len(package) and all(r in reforms for r in package):
        return "package"
    return "+".join(reforms)


def scenario_query(sc: Mapping[str, Any]) -> str:
    """The link's scenario part, as ``encodeScenario`` in web/js/state/codec.js writes it."""
    parts = ["v=1", f"s={sc['state']}"]
    if sc["reforms"]:
        parts.append(f"r={','.join(sc['reforms'])}")
    keys = sorted(sc["overrides"] or {})
    if keys:
        parts.append("a=" + ",".join(f"{k}~{sc['overrides'][k]}" for k in keys))
    return "&".join(parts)


def run_id(run: Mapping[str, Any], key: str) -> str:
    """The run's id: state, run key, the code / data / assumption hashes, seed and draws."""
    h = run.get("hashes") or {}
    marks = ".".join(str(h.get(k) or "")[:12] for k in ("code", "data", "assumptions"))
    return f"{run.get('state')}/{key}/{marks}/seed-{run.get('seed')}/draws-{run.get('draws')}"


def first_number(norm: str) -> int | None:
    """The first whole number in the question (the target a reader names), or ``None``."""
    m = re.search(r"\b(\d+)\b", norm)
    return int(m.group(1)) if m else None


MONEY = re.compile(r"\$\s?(\d+)(?:\s+(million|m|billion))?\b|\b(\d+)\s+(million|billion)\b")
SCALE = {"million": 1e6, "m": 1e6, "billion": 1e9}


def money_in(norm: str) -> tuple[float | None, str]:
    """A budget named in the question (``$20 million``, ``$5000000``) and the rest of it."""
    m = MONEY.search(norm)
    if not m:
        return None, norm
    digits = m.group(1) or m.group(3)
    word = m.group(2) or m.group(4) or ""
    return float(digits) * SCALE.get(word, 1.0), f"{norm[: m.start()]} {norm[m.end() :]}"


def _lst(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def number_view(run: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    """One record of ``explanation.numbers`` as an answer cites it (its inputs left out)."""
    rec = _obj(_obj(run.get("explanation")).get("numbers")).get(key)
    if not isinstance(rec, dict):
        return None
    ids = [a.get("id") for a in _lst(rec.get("assumptions")) if isinstance(a, dict)]
    return {
        "id": f"run:{key}",
        "kind": "run",
        "name": _str(rec.get("name")),
        "unit": _str(rec.get("unit")),
        "shown": rec.get("shown"),
        "p10": rec.get("p10"),
        "p50": rec.get("p50"),
        "p90": rec.get("p90"),
        "formula": _str(rec.get("formula")),
        "source_ids": _lst(rec.get("source_ids")),
        "assumption_ids": ids or _lst(rec.get("assumption_ids")),
    }


def ranged(view: Mapping[str, Any] | None) -> bool:
    """True when a record view has shown P10, P50 and P90 text."""
    shown = (view or {}).get("shown")
    return isinstance(shown, dict) and all(
        isinstance(shown.get(k), str) for k in ("p10", "p50", "p90")
    )


def _push_sources(out: list[dict[str, Any]], run: Mapping[str, Any] | None, ids: list[Any]) -> None:
    table = _obj(_obj((run or {}).get("explanation")).get("sources"))
    seen = {s["id"] for s in out}
    for sid in ids:
        if not isinstance(sid, str) or sid in seen:
            continue
        seen.add(sid)
        src = _obj(table.get(sid))
        out.append(
            {"id": sid, "citation": _str(src.get("citation")) or sid, "url": _str(src.get("url"))}
        )


def _push_url(out: list[dict[str, Any]], url: Any, citation: str) -> None:
    if isinstance(url, str) and url and all(s["id"] != url for s in out):
        out.append({"id": url, "citation": citation or url, "url": url})


# ------------------------------------------------------------------ the resolver


def parse(question: str, table: Mapping[str, Any]) -> dict[str, Any]:
    """Pick the state, reforms and intent out of a question (words only, no record read)."""
    norm = normalise(question)
    rest = norm
    state = ""
    for code, entry in table["states"].items():
        words = sorted(entry["words"], key=lambda w: -len(w))
        if not state and any(has(norm, w) for w in words):
            state = code
        for w in words:
            rest = drop(rest, w)
    package = any(has(norm, w) for w in table["package"]["words"])
    for w in sorted(table["package"]["words"], key=lambda w: -len(w)):
        rest = drop(rest, w)
    reforms: list[str] = []
    for rid, entry in table["reforms"].items():
        words = sorted(entry["words"], key=lambda w: -len(w))
        if any(has(norm, w) for w in words):
            reforms.append(rid)
        for w in words:
            rest = drop(rest, w)
    if package:
        reforms = list(table["reforms"].keys())
    best, score = table["fallback"], 0
    for intent in table["intents"]:
        n = sum(1 for w in intent.get("words") or [] if has(rest, w))
        n += sum(1 for p in intent.get("patterns") or [] if re.search(p, rest))
        if n > score:
            best, score = intent["id"], n
    return {"norm": norm, "rest": rest, "state": state, "reforms": reforms, "intent": best}


def resolve(
    question: str,
    context: Mapping[str, Any],
    reader: Any,
    table: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Answer ``question`` from the records ``reader`` holds for the run in ``context``.

    Args:
        question: The reader's question (at most 500 characters are read).
        context: The view on screen: ``state``, ``reforms``, ``overrides``, ``route``,
            ``param`` (a reform id or place code) and ``county`` (a picked county).
        reader: A :class:`StaticReader` or :class:`LiveReader` (or any object with the same
            methods).
        table: The intent table (default ``assumptions/answers.yaml``).

    Returns:
        The answer: ``intent``, ``status`` (answered / unknown / out_of_range / unavailable /
        unsupported), ``title``, ``lines`` and ``text``, the cited ``records`` and
        ``sources``, ``run_id``, ``period``, ``link``, ``download`` and ``offers``
        (suggested questions).
    """
    t = table or load_table()
    q = parse(str(question or "")[:MAX_QUESTION], t)
    ctx_state = str(context.get("state") or "TL").upper()
    ctx_reforms = [r for r in _lst(context.get("reforms")) if isinstance(r, str)]
    state = q["state"] or ctx_state
    reforms = q["reforms"] or (ctx_reforms if state == ctx_state else [])
    same = state == ctx_state and reforms == ctx_reforms
    overrides = dict(_obj(context.get("overrides"))) if same else {}
    route = str(context.get("route") or "state") if same else "state"
    param = str(context.get("param") or "") if same else ""
    county = str(context.get("county") or "") if state == ctx_state else ""
    sc = {"state": state, "reforms": reforms, "overrides": overrides}
    a = _Answer(t, q, sc, reader, route, param, county)
    handler = {
        "result": a.result, "baseline": a.baseline, "drivers": a.drivers, "law": a.law,
        "coverage": a.coverage, "target": a.target, "address": a.address, "share": a.share,
        "outside": a.outside,
    }.get(q["intent"], a.unsupported)  # fmt: skip
    try:
        handler()
    except UnavailableError:
        a.unavailable()
    return a.done()


class _Answer:
    """One answer being built; each intent is one method (twins in web/js/lib/answers.js)."""

    def __init__(self, t, q, sc, reader, route, param, county):
        self.t, self.q, self.sc, self.reader = t, q, sc, reader
        self.county = county
        self.intent = q["intent"]
        self.status = "answered"
        self.lines: list[str] = []
        self.records: list[dict[str, Any]] = []
        self.sources: list[dict[str, Any]] = []
        self.run: dict[str, Any] | None = None
        self.key = run_key(sc["reforms"], sc["overrides"], BENCHMARKS)
        self.run_id = ""
        self.period = ""
        self.download = ""
        self.offers: list[str] = []
        path = f"{route}/{param}" if param else route
        self.link = f"#/{path}?{scenario_query(sc)}"
        st = t["states"].get(sc["state"]) or {}
        self.vals: dict[str, Any] = {
            "state_name": st.get("name") or sc["state"],
            "scenario_title": self.title_of(sc["reforms"]),
            "link": self.link,
        }

    # ---------------------------------------------------------- shared steps

    def title_of(self, reforms: list[str]) -> str:
        names = self.t["reforms"]
        if not reforms:
            return "The baseline"
        if len(reforms) == len(names) and all(r in reforms for r in names):
            return self.t["package"]["title"]
        return " + ".join((names.get(r) or {}).get("title") or r for r in reforms)

    def say(self, name: str, values: Mapping[str, Any] | None = None) -> None:
        self.lines.append(fill(self.t["templates"][name], {**self.vals, **(values or {})}))

    def cite(self, view: dict[str, Any] | None) -> None:
        if view and all(r["id"] != view["id"] for r in self.records):
            self.records.append(view)
            _push_sources(self.sources, self.run, _lst(view.get("source_ids")))

    def cite_status(self, key: str, v: Mapping[str, Any]) -> None:
        """Cite a reform's transit status view (the run's ``status_views``) as a record."""
        self.cite({
            "id": f"run:status_views.{key}", "kind": "status", "name": _str(v.get("title")),
            "unit": "", "shown": _str(v.get("text")), "label": _str(v.get("label")),
            "note": _str(v.get("package_note")), "formula": _str(v.get("rule")),
            "source_ids": _lst(v.get("source_ids")), "assumption_ids": _lst(v.get("ledger_ids")),
        })  # fmt: skip

    def load_run(
        self, sc: Mapping[str, Any] | None = None, drivers: bool = False
    ) -> dict[str, Any]:
        sc = sc or self.sc
        run = self.reader.run(sc, drivers)
        self.run = run
        key = run_key(sc["reforms"], sc["overrides"], BENCHMARKS)
        self.run_id = run_id(run, key)
        self.period = _str(_obj(run.get("contract")).get("window"))
        self.vals["period"] = self.period
        name = _str(_obj(_obj(run.get("explanation")).get("stamp")).get("state_name"))
        if name and self.sc["state"] not in self.t["states"]:
            self.vals["state_name"] = name
        if len(sc["reforms"]) == 1 and sc["reforms"][0] not in self.t["reforms"]:
            title = _str(_obj(_obj(run.get("reform_results")).get(sc["reforms"][0])).get("title"))
            self.vals["scenario_title"] = title or sc["reforms"][0]
        return run

    def try_run(self) -> dict[str, Any] | None:
        try:
            return self.load_run()
        except UnavailableError:
            return None

    def maybe(self, ask: Callable[[], Any]) -> Any:
        try:
            return ask()
        except UnavailableError:
            return None

    def headline_keys(self) -> tuple[str, str]:
        r = self.sc["reforms"]
        if len(r) == 1:
            return f"reform_results.{r[0]}.added", f"reform_results.{r[0]}.pct_change"
        return "package.combined", "package.pct_change"

    # ---------------------------------------------------------- intents

    def result(self) -> None:
        run = self.load_run()
        base = number_view(run, "baseline")
        if not ranged(base):
            raise UnavailableError("baseline")
        b = base["shown"]
        if not self.sc["reforms"]:
            self.say("result_baseline_only", {"b10": b["p10"], "b50": b["p50"], "b90": b["p90"]})
            self.cite(base)
            return
        added_key, share_key = self.headline_keys()
        added = number_view(run, added_key)
        if not ranged(added):
            raise UnavailableError(added_key)
        views = _obj(run.get("status_views"))
        alone = _obj(views.get(self.sc["reforms"][0])) if len(self.sc["reforms"]) == 1 else {}
        if alone and alone.get("shown") is False:
            # The statewide range is hidden (transit status): the status text, never a range.
            self.say("result_status", {"status_text": _str(alone.get("text"))})
            self.cite_status(self.sc["reforms"][0], alone)
            return
        if added["p10"] == 0 and added["p90"] == 0 and self.gap_notes(run):
            self.coverage(zero=True)
            return
        s = added["shown"]
        self.say("result", {"p10": s["p10"], "p50": s["p50"], "p90": s["p90"],
                            "b10": b["p10"], "b90": b["p90"]})  # fmt: skip
        self.cite(added)
        share = number_view(run, share_key)
        if ranged(share):
            self.say("result_share", {"s10": share["shown"]["p10"], "s90": share["shown"]["p90"]})
            self.cite(share)
        for r in self.sc["reforms"]:
            v = _obj(views.get(r))
            if v.get("package_note") and len(self.sc["reforms"]) > 1:
                self.say("result_status_note", {"note": _str(v.get("package_note"))})
                self.cite_status(r, v)
            elif v.get("shown") and v.get("partial"):
                self.say("result_partial", {"title": _str(v.get("title")),
                                            "label": _str(v.get("label"))})  # fmt: skip
                self.cite_status(r, v)
        self.cite(base)

    def baseline(self) -> None:
        run = self.load_run({**self.sc, "reforms": []})
        self.link = f"#/state?{scenario_query({**self.sc, 'reforms': []})}"
        self.vals["link"] = self.link
        base = number_view(run, "baseline")
        if not ranged(base):
            raise UnavailableError("baseline")
        st = self.sc["state"]
        val = _obj(self.maybe(lambda: self.reader.validation(st)))
        years = _obj(self.maybe(lambda: self.reader.permit_years(st)))
        view = _obj(val.get("contest_view"))
        rule = _str(view.get("used_name")) or _str(val.get("chosen_rule")) or "see the record"
        b = base["shown"]
        self.say("baseline", {"b10": b["p10"], "b50": b["p50"], "b90": b["p90"], "rule_name": rule})
        self.cite(base)
        card = _obj(val.get("baseline_card"))
        if card:
            self.say("baseline_rule", {"rule_text": _str(card.get("rule_text"))})
            if _str(card.get("unit_fields_label")):
                self.say("baseline_classes", {"unit_fields": card["unit_fields_label"]})
            self.cite({
                "id": f"baseline-card:{st}", "kind": "baseline-card",
                "name": _str(card.get("rule_text")), "unit": "units",
                "shown": _str(card.get("unit_fields_label")),
                "formula": _str(card.get("formula")),
                "years": _obj(card.get("historical_years")),
                "source_ids": _lst(card.get("sources")),
                "assumption_ids": _lst(card.get("assumption_ids")),
            })  # fmt: skip
        past = [p for p in _lst(years.get("state_past")) if isinstance(p, dict)]
        wanted = [y for y in _lst(_obj(card.get("historical_years")).get("level_years"))]
        if len(wanted) == 2:
            past = [p for p in past if wanted[0] <= p.get("year", 0) <= wanted[1]]
        else:
            past = past[-5:]
        if past:
            self.say(
                "baseline_past",
                {"past": "; ".join(f"{p.get('year')}: {_str(p.get('shown'))}" for p in past)},
            )
            self.cite({
                "id": f"permit-years:{st}:state_past", "kind": "permit-years",
                "name": _str(years.get("title")) or "homes permitted by year",
                "unit": "units",
                "shown": [{"year": p.get("year"), "shown": _str(p.get("shown"))} for p in past],
                "formula": _str(years.get("formula")),
                "source_ids": _lst(years.get("source_ids")), "assumption_ids": [],
            })  # fmt: skip
        state_range = _obj(_obj(val.get("reliability")).get("state_range"))
        if _str(state_range.get("sentence")):
            self.say("baseline_reliability", {"sentence": state_range["sentence"]})
            self.cite({
                "id": f"validation:{st}:reliability.state_range", "kind": "validation",
                "name": _str(state_range.get("label")), "unit": "cases",
                "shown": state_range["sentence"],
                "cases": state_range.get("cases"), "covered": state_range.get("covered"),
                "formula": _str(_obj(val.get("reliability")).get("source")),
                "source_ids": [], "assumption_ids": [],
            })  # fmt: skip
        diags = _obj(card.get("diagnostics"))
        for did in sorted(diags):
            d = _obj(diags[did])
            self.say("baseline_offer", {"label": _str(d.get("label")), "id": did,
                                        "status": _str(d.get("status"))})  # fmt: skip
            self.cite({
                "id": f"baseline-card:{did}", "kind": "diagnostic", "name": _str(d.get("label")),
                "unit": "units", "shown": _str(d.get("formula")), "formula": _str(d.get("formula")),
                "use": _str(d.get("use")), "source_ids": _lst(d.get("sources")),
                "assumption_ids": _lst(d.get("assumption_ids")),
            })  # fmt: skip

    def drivers(self) -> None:
        reforms = self.sc["reforms"]
        if len(reforms) != 1:
            self.status = "unsupported"
            self.say("drivers_package")
            return
        run = self.load_run(drivers=True)
        d = _obj(_obj(_obj(run.get("explanation")).get("drivers")).get(reforms[0]))
        items = [i for i in _lst(d.get("items")) if isinstance(i, dict)][:3]
        if not items:
            self.status = "unknown"
            self.say("drivers_none")
            return
        lead = _str(_obj(d.get("lead")).get("text"))
        if lead:
            self.say("drivers_lead", {"lead": lead})
        for item in items:
            text = _obj(item.get("text"))
            self.say("drivers_item", {"text": _str(text.get("text"))})
            self.cite({
                "id": f"run:{_str(item.get('ref'))}", "kind": "driver",
                "name": _str(item.get("label")), "unit": _str(item.get("unit")),
                "shown": [
                    _str(c.get("shown")) for c in _lst(text.get("cites")) if isinstance(c, dict)
                ],
                "parameter": _str(item.get("parameter")),
                "low": item.get("low"), "central": item.get("central"), "high": item.get("high"),
                "total_low": item.get("total_low"), "total_central": item.get("total_central"),
                "total_high": item.get("total_high"),
                "formula": lead, "source_ids": [], "assumption_ids": [_str(item.get("parameter"))],
            })  # fmt: skip

    def law_rows(self, reform: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        impl = _obj(self.reader.implementation(self.sc["state"], reform, self.county))
        return impl, [r for r in _lst(impl.get("rows")) if isinstance(r, dict)]

    def cite_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        legal = _obj(row.get("legal"))
        rec = _obj(legal.get("record"))
        view = {
            "id": f"law:{_str(rec.get('record_id')) or _str(row.get('provision_id'))}",
            "kind": "law", "name": _str(row.get("citation")), "unit": "",
            "shown": _str(row.get("line")), "status": _str(legal.get("state")),
            "level": _str(row.get("level")), "jurisdiction": _str(row.get("jurisdiction")),
            "scope": _str(row.get("scope")), "start": rec.get("start"), "end": rec.get("end"),
            "verified_on": rec.get("verified_on"), "agency": _str(rec.get("agency")),
            "note": _str(rec.get("note")), "formula": "",
            "source_ids": [], "assumption_ids": [], "urls": _lst(rec.get("sources")),
        }  # fmt: skip
        self.cite(view)
        for url in view["urls"]:
            _push_url(self.sources, url, view["agency"] or view["name"])
        return view

    def law(self) -> None:
        reforms = self.sc["reforms"]
        if len(reforms) != 1:
            self.status = "unsupported"
            self.say("law_package")
            return
        self.try_run()
        impl, rows = self.law_rows(reforms[0])
        if not rows:
            self.status = "unknown"
            self.say("law_none", {"none_text": _str(impl.get("none_text"))})
            self.cite({
                "id": f"law:{self.sc['state']}:{reforms[0]}:none", "kind": "law",
                "name": _str(impl.get("source")), "unit": "", "shown": _str(impl.get("none_text")),
                "on": impl.get("on"), "formula": "", "source_ids": [], "assumption_ids": [],
            })  # fmt: skip
            return
        pending = False
        for row in rows[:5]:
            view = self.cite_row(row)
            values = {"citation": view["name"], "level": view["level"], "line": view["shown"],
                      "start": view["start"], "verified_on": view["verified_on"] or "not recorded",
                      "scope": view["scope"] or "not recorded"}  # fmt: skip
            self.say("law_row" if view["start"] else "law_row_nodate", values)
            pending = pending or bool(row.get("pending"))
        if pending and _str(impl.get("pending_note")):
            self.say("law_pending", {"pending_note": impl["pending_note"]})

    def gap_notes(self, run: Mapping[str, Any] | None) -> list[tuple[str, int, str]]:
        """Reform notes that say a result is a data gap: ``(reform, note index, note)``."""
        words = [w.lower() for w in self.t["coverage_note_words"]]
        out: list[tuple[str, int, str]] = []
        for r in self.sc["reforms"]:
            notes = _lst(_obj(_obj(_obj(run or {}).get("reform_results")).get(r)).get("notes"))
            for i, note in enumerate(notes):
                if isinstance(note, str) and any(w in note.lower() for w in words):
                    out.append((r, i, note))
        return out

    def coverage(self, zero: bool = False) -> None:
        self.intent = "coverage"
        self.status = "unknown"
        run = self.run if zero else self.try_run()
        if zero:
            self.say("coverage_zero")
        for r in self.sc["reforms"]:
            v = _obj(_obj(_obj(run or {}).get("status_views")).get(r))
            if v.get("shown") is False:
                self.say("coverage_status", {"status_text": _str(v.get("text"))})
                self.cite_status(r, v)
        for rid, i, note in self.gap_notes(run):
            self.say("coverage_note", {"note": note})
            self.cite({
                "id": f"run:reform_results.{rid}.notes.{i}", "kind": "note",
                "name": self.title_of([rid]), "unit": "", "shown": note, "formula": "",
                "source_ids": [], "assumption_ids": [],
            })  # fmt: skip
        card = _obj(self.maybe(lambda: self.reader.report_card(self.sc["state"])))
        want = self.sc["reforms"] or ["baseline"]
        bad = [w for w in self.t["not_observed"]]
        gaps = [
            i for i in _lst(card.get("inputs"))
            if isinstance(i, dict) and i.get("value_state") in bad
            and any(r in _lst(i.get("reforms")) for r in [*want, "all reforms"])
        ]  # fmt: skip
        if not gaps:
            if card:
                self.say("coverage_none")
            return
        first = next((g for g in gaps if g.get("value_state") == "missing"), gaps[0])
        ordered = [first, *[g for g in gaps if g is not first]][:3]
        for n, g in enumerate(ordered):
            limit = next((x for x in _lst(g.get("limits")) if isinstance(x, str)), "")
            limit = re.sub(r"\.+$", "", limit)
            values = {"label": _str(g.get("label")), "value_state": _str(g.get("value_state")),
                      "limit": limit}  # fmt: skip
            self.say("coverage_next" if n == 0 else "coverage_gap", values)
            self.cite({
                "id": f"report-card:{self.sc['state']}:{_str(g.get('id'))}", "kind": "report-card",
                "name": _str(g.get("label")), "unit": "", "shown": limit,
                "status": _str(g.get("status")), "value_state": _str(g.get("value_state")),
                "coverage": g.get("coverage"), "limits": _lst(g.get("limits")), "formula": "",
                "source_ids": _lst(g.get("sources")), "assumption_ids": [],
            })  # fmt: skip

    def target(self) -> None:
        reforms = self.sc["reforms"]
        budget = any(has(self.q["norm"], w) for w in self.t["budget_words"])
        if budget and reforms in ([], ["adu"]) and self.budget_target():
            return
        if len(reforms) != 1:
            self.status = "unsupported"
            self.say("target_package")
            return
        run = self.try_run()
        alone = _obj(_obj(_obj(run or {}).get("status_views")).get(reforms[0]))
        if alone.get("shown") is False:
            # No dial level has a statewide range to read (transit status): the status text.
            self.status = "unknown"
            self.say("result_status", {"status_text": _str(alone.get("text"))})
            self.cite_status(reforms[0], alone)
            return
        dials = _obj(self.reader.dials(self.sc["state"]))
        block = _obj(dials.get("dials"))
        chosen = [
            _obj(d) for d in _obj(block.get("dials")).values()
            if _obj(d).get("reform") == reforms[0]
        ]  # fmt: skip
        if not chosen:
            self.status = "unknown"
            self.say("target_none")
            return
        rows0 = [r for r in _lst(_obj(chosen[0].get("targets")).get("rows")) if isinstance(r, dict)]
        targets = ", ".join(_str(r.get("homes_text")) for r in rows0)
        self.cite({
            "id": f"dials:{self.sc['state']}:{_str(chosen[0].get('key'))}.targets",
            "kind": "dials", "name": _str(chosen[0].get("title")), "unit": "homes",
            "shown": [_str(r.get("homes_text")) for r in rows0],
            "formula": _str(_obj(chosen[0].get("targets")).get("rule")),
            "seed": block.get("seed"), "draws": block.get("draws"),
            "source_ids": [],
            "assumption_ids": _lst(_obj(chosen[0].get("targets")).get("assumption_ids")),
        })  # fmt: skip
        want = first_number(self.q["rest"])
        if want is None:
            self.status = "unknown"
            self.say("target_ask_number", {"targets": targets})
            return
        hits = []
        for d in chosen:
            rows = [r for r in _lst(_obj(d.get("targets")).get("rows")) if isinstance(r, dict)]
            row = next((r for r in rows if r.get("homes") == want), None)
            if row is not None:
                hits.append((d, row))
        if not hits:
            self.status = "out_of_range"
            self.say("target_rows_only", {"targets": targets})
            return
        reached = False
        for d, row in hits:
            p50, p10 = _obj(row.get("p50")), _obj(row.get("p10"))
            reached = reached or p50.get("status") != "out_of_reach"
            self.say("target_row", {"dial": _str(d.get("title")), "p50": _str(p50.get("text")),
                                    "p10": _str(p10.get("text"))})  # fmt: skip
            self.cite({
                "id": f"dials:{self.sc['state']}:{_str(d.get('key'))}.targets."
                f"{_str(row.get('homes_text'))}",
                "kind": "dials", "name": _str(d.get("title")), "unit": _str(d.get("unit")),
                "shown": {"homes": _str(row.get("homes_text")), "p50": _str(p50.get("text")),
                          "p10": _str(p10.get("text"))},
                "status": {"p50": _str(p50.get("status")), "p10": _str(p10.get("status"))},
                "headline": _str(d.get("headline")),
                "formula": _str(_obj(d.get("targets")).get("rule")),
                "source_ids": [],
                "assumption_ids": _lst(_obj(d.get("targets")).get("assumption_ids")),
            })  # fmt: skip
        if not reached:
            self.status = "out_of_range"
        self.say("target_head", {"headline": _str(hits[0][0].get("headline"))})
        self.say("target_trials", {"draws": block.get("draws"), "seed": block.get("seed"),
                                   "rule": _str(_obj(hits[0][0].get("targets")).get("rule")),
                                   })  # fmt: skip
        if any(has(self.q["norm"], w) for w in self.t["budget_words"]):
            self.say("target_budget")

    def budget_target(self) -> bool:
        """A target-and-budget question about ADUs, read from the budget grid's cells.

        Returns ``False`` (and says nothing) when the build has no grid, so the dial
        targets answer instead. Nothing is interpolated (``grid_lookup``).
        """
        from visionhousing.scenarios.adu_budget import grid_lookup

        try:
            held = _obj(self.reader.budget_grid(self.sc["state"]))
        except UnavailableError:
            return False
        grid, view = _obj(held.get("grid")), _obj(held.get("view"))
        if not grid.get("cells") or not view.get("cells"):
            return False
        if self.sc["reforms"] != ["adu"]:
            self.sc = {**self.sc, "reforms": ["adu"]}
            self.vals["scenario_title"] = self.title_of(["adu"])
        self.try_run()
        texts = {c["key"]: c for c in _lst(view.get("cells")) if isinstance(c, dict)}
        opts = _obj(view.get("options"))
        ttext = {float(o["value"]): o["text"] for o in _lst(opts.get("targets"))}
        btext = {float(o["value"]): o["text"] for o in _lst(opts.get("budgets"))}
        targets = ", ".join(ttext.values())
        self.cite({
            "id": f"budget-grid:{self.sc['state']}", "kind": "budget-grid",
            "name": _str(grid.get("label")), "unit": "homes",
            "shown": {"targets": list(ttext.values()), "budgets": list(btext.values())},
            "formula": _str(view.get("scope")), "seed": view.get("seed"),
            "draws": view.get("draws"), "source_ids": [],
            "assumption_ids": _lst(view.get("assumption_ids")),
        })  # fmt: skip
        money, rest = money_in(self.q["rest"])
        want = first_number(rest)
        self.say("budget_head", {"scope": _str(view.get("scope"))})
        if want is None:
            self.status = "unknown"
            self.say("budget_ask_number", {"targets": targets})
            return True
        q = grid_lookup(grid, target=want, budget=money)
        flags = _lst(q.get("flags"))
        words = _obj(view.get("flags"))
        lo_hi = _lst(_obj(q.get("brackets")).get("target"))
        if "outside_tested_range" in flags and not q.get("options") and not lo_hi:
            self.status = "out_of_range"
            self.say("budget_outside", {"targets": targets})
            return True
        if lo_hi:
            self.status = "out_of_range"
            self.say("budget_bracket", {"lo": ttext.get(float(lo_hi[0]), ""),
                                        "hi": ttext.get(float(lo_hi[1]), "")})  # fmt: skip
            for end in lo_hi:
                self.budget_lines(grid, texts, ttext, float(end), money)
        else:
            self.budget_lines(grid, texts, ttext, float(want), money)
        for f in flags:
            if f in ("budget_exceeded", "no_tested_option_meets_both", "outside_tested_range"):
                self.say("budget_flag", {"flag": _str(words.get(f))})
        if money is not None and float(money) in btext:
            self.say("budget_ceiling", {"budget": btext[float(money)]})
        elif money is not None:
            self.say("budget_ceiling_untested", {"budgets": ", ".join(btext.values())})
        self.say("budget_trials", {"draws": view.get("draws_text"), "seed": view.get("seed")})
        self.say("budget_uncapped", {"note": _str(view.get("uncapped_note"))})
        return True

    def budget_lines(
        self,
        grid: Mapping[str, Any],
        texts: Mapping[str, Any],
        ttext: Mapping[float, str],
        target: float,
        money: float | None,
    ) -> None:
        """The tested options on the cost / share trade-off for one tested target."""
        from visionhousing.api.budget_view import cell_key
        from visionhousing.scenarios.adu_budget import grid_lookup

        q = grid_lookup(grid, target=target, budget=money)
        tkey = f"{target:g}"
        pareto = sorted(_lst(q.get("pareto")), key=lambda o: o["net_public_cost_p50"])
        self.say("budget_target", {"target": ttext.get(target, "")})
        if not pareto:
            self.say("budget_no_pareto")
            return
        for o in pareto if len(pareto) <= 4 else [*pareto[:2], *pareto[-2:]]:
            key = cell_key(o)
            c = _obj(texts.get(key))
            t = _obj(_obj(c.get("targets")).get(tkey))
            cost = _obj(_obj(c.get("net_cost")).get("shown"))
            added = _obj(c.get("added"))
            shown = {
                "share": _str(t.get("share_text")), "se": _str(t.get("se_text")),
                "net_cost": _str(cost.get("p50")), "added": _str(added.get("likely")),
                "appropriation": _str(c.get("appropriation_text")),
                "award": _str(c.get("award_text")),
            }  # fmt: skip
            name = "budget_option_law" if c.get("arm") == "law_only" else "budget_option"
            self.say(name, {"arm": _str(c.get("arm_word")), **shown})
            self.cite({
                "id": f"budget-grid:{self.sc['state']}:{key}:{tkey}", "kind": "budget-grid",
                "name": _str(c.get("label")), "unit": "homes", "shown": shown,
                "formula": _str(added.get("formula")), "source_ids": [],
                "assumption_ids": [],
            })  # fmt: skip

    def address(self) -> None:
        self.status = "unsupported"
        self.try_run()
        self.say("address")
        if not self.county:
            self.say("address_pick")
        shown = 0
        for r in self.sc["reforms"] if self.county else []:
            rows = self.maybe(lambda r=r: self.law_rows(r)[1]) or []
            for row in rows:
                rec = _obj(_obj(row.get("legal")).get("record"))
                urls = [u for u in _lst(rec.get("sources")) if isinstance(u, str)]
                if shown >= 3 or row.get("level") == "state" or not urls:
                    continue
                view = self.cite_row(row)
                self.say("address_rule", {"citation": view["name"],
                                          "agency": view["agency"] or view["jurisdiction"],
                                          "url": urls[0]})  # fmt: skip
                shown += 1
        self.say("address_ask")

    def share(self) -> None:
        run = self.load_run()
        self.say("share", {"run_id": self.run_id})
        key = self.headline_keys()[0] if self.sc["reforms"] else "baseline"
        view = number_view(run, key)
        if ranged(view):
            s = view["shown"]
            self.say("share_record", {"name": view["name"], "p10": s["p10"], "p50": s["p50"],
                                      "p90": s["p90"]})  # fmt: skip
            self.cite(view)
        self.download = self.reader.download(self.sc) or ""
        if self.download:
            self.say("share_download", {"download": self.download})
        else:
            self.say("share_no_download")

    def outside(self) -> None:
        self.status = "unsupported"
        self.say("outside")
        self.offer()

    def unsupported(self) -> None:
        self.intent = "unsupported"
        self.status = "unsupported"
        self.say("unsupported")
        self.offer()

    def offer(self) -> None:
        s = self.t["suggestions"]
        self.offers = list(s.get(self.sc["state"]) or s["default"])

    def unavailable(self) -> None:
        self.status = "unavailable"
        self.lines, self.records, self.sources = [], [], []
        self.run_id, self.period, self.download = "", "", ""
        r = self.sc["reforms"]
        reason = (
            "custom"
            if self.sc["overrides"]
            else ("scenario" if len(r) == 1 else ("combo" if r else "missing"))
        )
        self.say(f"unavailable_{reason}")

    def done(self) -> dict[str, Any]:
        title = self.t["templates"]["title"]
        name = "unavailable" if self.status == "unavailable" else self.intent
        return {
            "intent": self.intent,
            "status": self.status,
            "state": self.sc["state"],
            "state_name": self.vals["state_name"],
            "reforms": list(self.sc["reforms"]),
            "title": fill(title.get(name) or title["unsupported"], self.vals),
            "lines": self.lines,
            "text": " ".join(self.lines),
            "records": self.records,
            "sources": self.sources,
            "run_id": self.run_id,
            "period": self.period,
            "link": self.link,
            "download": self.download,
            "offers": self.offers,
        }


# ------------------------------------------------------------------ the address


def register(app: Any, *, api: str, error: Callable[[int, str, str], Exception]) -> None:
    """Add ``POST {api}/answer`` to ``app`` (before its catch-all ``/api/v1/...`` route).

    The body is ``{"question": text, "context": {state, reforms, overrides, route, param,
    county}}``. The reply is :func:`resolve`'s answer, read through the app's own addresses
    in memory. The question is not logged or kept.
    """
    from fastapi import Body
    from fastapi.responses import Response

    from visionhousing.api import service

    call = app_caller(app)

    @app.post(f"{api}/answer")
    def answer(payload: dict = Body(...)) -> Response:  # noqa: B008
        question = payload.get("question")
        context = payload.get("context")
        context = {} if context is None else context
        if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION:
            raise error(400, "invalid_request", "question must be text of 1 to 500 characters")
        if not isinstance(context, dict):
            raise error(400, "invalid_request", "context must be an object")
        reforms = context.get("reforms") or []
        overrides = context.get("overrides") or {}
        if (
            not isinstance(reforms, list) or len(reforms) > 8
            or not all(isinstance(r, str) and re.fullmatch(r"[a-z0-9_]{1,40}", r) for r in reforms)
            or not isinstance(overrides, dict) or len(overrides) > 40
            or not re.fullmatch(r"[A-Za-z]{2}", str(context.get("state") or "TL"))
        ):  # fmt: skip
            raise error(400, "invalid_request", "context has a malformed state, reform or override")
        ctx = {
            "state": str(context.get("state") or "TL").upper(),
            "reforms": reforms,
            "overrides": {str(k): str(v) for k, v in overrides.items()},
            "route": str(context.get("route") or "state")[:16],
            "param": str(context.get("param") or "")[:32],
            "county": str(context.get("county") or "")[:11],
        }
        reply = resolve(question, ctx, LiveReader(call, api))
        return Response(service.dumps(reply), media_type="application/json")


if __name__ == "__main__":
    print(write_shared_table())
