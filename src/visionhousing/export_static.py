"""Export a state's results as plain JSON files, so the interface can run with no server.

Plain-language summary
----------------------
The interface normally asks the FastAPI back end for every number. A static web host
(such as Vercel) has no Python, so this module asks the back end once, ahead of time,
and saves each reply as a file. The front end, built with ``VITE_DATA_MODE=static``,
then reads those files instead (``web/js/lib/api.js``).

It calls the app in memory (no network, no open port), so every file holds exactly
the bytes the live interface would send. It saves only the **default** scenarios:
default assumptions, seed 0 and the default number of draws. Anything else (a custom
assumption, another seed) still needs the local app; the static site says so.

Usage::

    visionhousing export-static --state PA --out web/public/data

Files written under ``--out`` (``ST`` is the state code)::

    states.json                       the exported states (merged with earlier exports)
    reforms.json, reforms/{key}.json  every reform's definition and its ledger parameters
    ledger.json, provenance.json      the Assumption Ledger and the source list
    ST/index.json                     what this export holds: runs, counties, sizes
    ST/summary.json                   the state's facts and baseline
    ST/runs/{key}.json                POST /run for "baseline", each benchmark reform
                                      alone (with its top drivers), and "package" (all
                                      five together); for PA (and Testland) also each
                                      Pennsylvania bill alone ("extra_runs" in the index)
    ST/compare/{key}.json             POST /compare for the same scenarios
    ST/maps/{key}.json                GET /states/ST/map for every saved run
    ST/exports/{key}.csv, .html       GET /export/csv and /export/html (the brief) for
                                      every saved run
    ST/outlines.json                  the county outlines for the map
    ST/report-card.json               the state's data report card
    evidence.json                     the evidence library
    ST/counties/{fips}.json           one county: baseline, each reform and the package
    ST/ledger.json, ST/provenance.json, ST/validation.json
    ST/places/counties.json           the county search list
    ST/places/tracts-{fips}.json      the tract list of one county (only with tract profiles)
    ST/profiles/{geoid}.json          one place profile per county (and per tract, if small)

Tract profiles are kept only while their total stays under ``--tract-budget-mb``
(default 4 MB); otherwise they are left out and ``index.json`` says so. The whole
folder must stay under 30 MB for the static site (``tests/test_web_package.py``).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

__all__ = [
    "BENCHMARK_PACKAGE",
    "SIZE_LIMIT_BYTES",
    "ExportReport",
    "export_static",
    "folder_size",
]

API = "/api/v1"
BENCHMARK_PACKAGE = ("adu", "missing_middle", "tod", "parking", "fees")
"""The five benchmark reforms, in the engine's keys. Together they are the "package"."""
STATE_BILLS = {"PA": ("pa_hb2186", "pa_sb1346"), "TL": ("pa_hb2186", "pa_sb1346")}
"""State bills saved as single runs. Each reaches the same lots as the ADU reform, so it is
never part of the package. Testland carries Pennsylvania's bills so the tests can check them."""
SIZE_LIMIT_BYTES = 30 * 1024 * 1024
"""The static site's data budget (the whole ``--out`` folder), measured uncompressed.

The files are plain JSON and CSV, so git and the static host store and send them compressed
(about one tenth of this size). The budget leaves room for every run's explanation, which
keeps "Explain this number" and the drivers working on the static site."""
DEFAULT_TRACT_BUDGET_MB = 4.0


class ExportError(RuntimeError):
    """The back end refused a request during the export."""


@dataclass
class ExportReport:
    """What one export wrote: file counts and byte sizes per kind, and the notes."""

    state: str
    out: Path
    sizes: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    tract_profiles: bool = False

    def add(self, kind: str, size: int, count: int = 1) -> None:
        """Count one file of ``kind`` with ``size`` bytes (negative values take it away)."""
        self.sizes[kind] = self.sizes.get(kind, 0) + size
        self.counts[kind] = self.counts.get(kind, 0) + count
        if self.counts[kind] <= 0:
            self.sizes.pop(kind)
            self.counts.pop(kind)

    @property
    def total(self) -> int:
        """Bytes written by this export."""
        return sum(self.sizes.values())

    def text(self) -> str:
        """A short size table for the terminal."""
        lines = [f"export-static {self.state}: wrote to {self.out}/"]
        for kind in sorted(self.sizes):
            lines.append(f"  {kind:<18} {self.counts[kind]:>6} files {self.sizes[kind]:>12,} bytes")
        lines.append(f"  {'this export':<18} {sum(self.counts.values()):>6} files "
                     f"{self.total:>12,} bytes")  # fmt: skip
        whole = folder_size(self.out)
        lines.append(f"  {'whole folder':<31} {whole:>12,} bytes (limit {SIZE_LIMIT_BYTES:,})")
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


def folder_size(folder: Path) -> int:
    """Total bytes of every file under ``folder`` (0 if it does not exist)."""
    if not folder.is_dir():
        return 0
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


class _Caller:
    """Call the ASGI app in memory, the way a web server would, and return the reply."""

    def __init__(self, app: Any):
        self.app = app

    def __call__(self, method: str, path: str, body: Any = None) -> tuple[int, bytes]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._call(method, path, body))
        # Called from code that already runs an event loop (a browser test, a notebook):
        # use a short-lived thread with its own loop.
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self._call(method, path, body)).result()

    async def _call(self, method: str, path: str, body: Any) -> tuple[int, bytes]:
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        target, _, query = path.partition("?")
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": target,
            "raw_path": target.encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": [
                (b"host", b"export"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
            ],
            "client": ("127.0.0.1", 0),
            "server": ("export", 80),
        }
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

        await self.app(scope, receive, send)
        return status, b"".join(chunks)


class _Writer:
    """Fetch an address from the app and save the reply under ``out``."""

    def __init__(self, call: _Caller, out: Path, report: ExportReport):
        self.call, self.out, self.report = call, out, report
        self.written: set[str] = set()

    def fetch(self, method: str, path: str, body: Any = None) -> bytes:
        status, data = self.call(method, path, body)
        if status != 200:
            try:
                message = json.loads(data).get("message", "")
            except ValueError:
                message = data[:200].decode("utf-8", "replace")
            raise ExportError(f"{method} {path} answered {status}: {message}")
        return data

    def save(self, rel: str, data: bytes, kind: str) -> None:
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel in self.written:  # an overwrite in this export replaces the earlier count
            self.report.add(kind, -path.stat().st_size, -1)
        path.write_bytes(data)
        self.written.add(rel)
        self.report.add(kind, len(data))

    def drop(self, rel: str, kind: str) -> None:
        path = self.out / rel
        if path.is_file():
            if rel in self.written:
                self.report.add(kind, -path.stat().st_size, -1)
                self.written.discard(rel)
            path.unlink()

    def get(self, rel: str, path: str, kind: str) -> Any:
        data = self.fetch("GET", path)
        self.save(rel, data, kind)
        return json.loads(data)

    def post(self, rel: str, path: str, body: Any, kind: str) -> Any:
        data = self.fetch("POST", path, body)
        self.save(rel, data, kind)
        return json.loads(data)


def _scenarios() -> dict[str, list[str]]:
    """The exported runs: the baseline, each benchmark reform alone, and the package."""
    out: dict[str, list[str]] = {"baseline": []}
    out.update({key: [key] for key in BENCHMARK_PACKAGE})
    out["package"] = list(BENCHMARK_PACKAGE)
    return out


def _clear_state_folder(folder: Path) -> None:
    """Remove an earlier export of this state, but only a folder this module wrote."""
    if folder.is_dir():
        if not (folder / "index.json").is_file():
            raise ExportError(
                f"{folder} exists but is not an earlier export (no index.json); "
                "move it away or pick another --out"
            )
        shutil.rmtree(folder)


def export_static(
    state: str,
    out: Path,
    *,
    packs_dir: Path | None = None,
    seed: int = 0,
    draws: int | None = None,
    tracts: str = "auto",
    tract_budget_mb: float = DEFAULT_TRACT_BUDGET_MB,
    explain: str = "auto",
) -> ExportReport:
    """Write the static-site files for ``state`` into ``out``.

    Args:
        state: Two-letter code (``TL`` for Testland).
        out: The data folder, normally ``web/public/data``.
        packs_dir: Built state packs (default ``data/packs``).
        seed: Random seed of the saved runs (the interface's default is 0).
        draws: Monte Carlo draws (default: the ledger default, as the interface uses).
        tracts: ``auto`` keeps tract profiles while they fit the budget, ``yes``
            always keeps them, ``no`` leaves them out.
        tract_budget_mb: The budget for ``auto``.
        explain: ``yes`` saves each run with its explanation (formulas, inputs and
            sources for every number), as the interface asks for it; ``no`` saves
            the runs without it (much smaller); ``auto`` (default) keeps it unless
            the folder would be over the size limit even without tract profiles.

    Returns:
        The report: files and bytes per kind, and notes.

    Raises:
        ExportError: The back end refused a request (for example, no pack for the state).
    """
    from visionhousing.api import create_app
    from visionhousing.ledger import DEFAULT_DRAWS

    code = state.upper()
    draws = draws or DEFAULT_DRAWS
    out = Path(out)
    report = ExportReport(code, out)
    app = create_app(packs_dir=packs_dir, web_dir=out / ".no-web", workers=1)
    w = _Writer(_Caller(app), out, report)
    base = {"state": code, "seed": seed, "draws": draws}

    # Check the state exists before anything is removed.
    w.fetch("GET", f"{API}/states/{code}/summary")
    _clear_state_folder(out / code)
    out.mkdir(parents=True, exist_ok=True)

    # Shared files: reforms, the ledger, the source list.
    reforms = w.get("reforms.json", f"{API}/reforms", "reforms")
    for r in reforms["reforms"]:
        w.get(f"reforms/{r['key']}.json", f"{API}/reforms/{r['key']}", "reforms")
    w.get("ledger.json", f"{API}/ledger", "ledger")
    w.get("provenance.json", f"{API}/provenance", "provenance")
    w.get("evidence.json", f"{API}/evidence", "evidence")

    # This state.
    w.get(f"{code}/summary.json", f"{API}/states/{code}/summary", "summary")
    w.get(f"{code}/ledger.json", f"{API}/ledger?state={code}", "ledger")
    w.get(f"{code}/provenance.json", f"{API}/provenance?state={code}", "provenance")
    w.get(f"{code}/validation.json", f"{API}/validation?state={code}", "validation")
    w.get(f"{code}/report-card.json", f"{API}/report-card?state={code}", "report card")
    w.get(f"{code}/outlines.json", f"{API}/states/{code}/outlines", "outlines")
    runs = _scenarios()
    extra = {key: [key] for key in STATE_BILLS.get(code, ())}
    every = {**runs, **extra}

    def save_runs(with_explanation: bool) -> None:
        for key, keys in every.items():
            # A reform alone also carries its top drivers (the Reform screen shows them).
            drivers = with_explanation and len(keys) == 1
            body = {**base, "reforms": keys, "explain": with_explanation, "drivers": drivers}
            w.post(f"{code}/runs/{key}.json", f"{API}/run", body, "runs")

    save_runs(explain != "no")
    for key, keys in runs.items():
        w.post(f"{code}/compare/{key}.json", f"{API}/compare", {**base, "reforms": keys},
               "compare")  # fmt: skip
    for key, keys in every.items():
        query = urlencode({"s": code, "r": ",".join(keys), "seed": seed, "draws": draws})
        w.get(f"{code}/maps/{key}.json", f"{API}/states/{code}/map?{query}", "maps")
        for fmt in ("csv", "html"):
            w.save(f"{code}/exports/{key}.{fmt}", w.fetch("GET", f"{API}/export/{fmt}?{query}"),
                   "exports")  # fmt: skip

    # Counties: one panel each (baseline, every reform and the package), and a profile.
    places = w.get(
        f"{code}/places/counties.json", f"{API}/places/{code}?level=county&limit=500", "places"
    )
    counties = [p["geoid"] for p in places["places"]]
    query = urlencode({"r": ",".join(BENCHMARK_PACKAGE), "seed": seed, "draws": draws})
    for fips in counties:
        w.get(f"{code}/counties/{fips}.json", f"{API}/states/{code}/counties/{fips}?{query}",
              "counties")  # fmt: skip
        w.get(f"{code}/profiles/{fips}.json", f"{API}/profile/{code}/{fips}", "profiles (county)")

    tract_files = _export_tracts(w, code, counties, tracts, tract_budget_mb)
    report.tract_profiles = tract_files is not None
    explained = explain != "no"

    # Over the limit: first drop tract profiles (auto), then run explanations (auto).
    if folder_size(out) > SIZE_LIMIT_BYTES and tracts == "auto" and tract_files:
        for rel, kind in tract_files:
            w.drop(rel, kind)
        report.tract_profiles = False
        report.notes.append("tract profiles left out to stay under the size limit")
    if folder_size(out) > SIZE_LIMIT_BYTES and explain == "auto":
        save_runs(False)
        explained = False
        report.notes.append(
            "runs saved without their explanations to stay under the size limit; "
            "the local app (make app) shows them"
        )

    index = {
        "state": code,
        "seed": seed,
        "draws": draws,
        "package": list(BENCHMARK_PACKAGE),
        "runs": list(runs),
        "extra_runs": list(extra),
        "compare": list(runs),
        "maps": list(every),
        "exports": list(every),
        "explanations": explained,
        "drivers": explained,
        "counties": counties,
        "tract_profiles": report.tract_profiles,
        "note": "Default assumptions only. Changing assumptions needs the local app (make app).",
    }
    w.save(f"{code}/index.json", json.dumps(index, indent=1).encode("utf-8"), "index")
    _merge_states(w, code)
    if folder_size(out) > SIZE_LIMIT_BYTES:
        report.notes.append(
            f"the folder is over the {SIZE_LIMIT_BYTES // (1024 * 1024)} MB static-site limit; "
            "export with --tracts no --explain no, or fewer states"
        )
    return report


def _export_tracts(
    w: _Writer, code: str, counties: list[str], mode: str, budget_mb: float
) -> list[tuple[str, str]] | None:
    """Save tract lists and tract profiles; ``None`` if left out (``no``, or over budget).

    Returns the saved files as ``(path, kind)`` so they can be dropped later.
    """
    if mode == "no":
        w.report.notes.append("tract profiles left out (--tracts no)")
        return None
    budget = int(budget_mb * 1024 * 1024)
    pending: list[tuple[str, bytes, str]] = []
    used = 0
    for fips in counties:
        path = f"{API}/places/{code}?level=tract&q={fips}&limit=500"
        data = w.fetch("GET", path)
        pending.append((f"{code}/places/tracts-{fips}.json", data, "places (tracts)"))
        for p in json.loads(data)["places"]:
            if not p["geoid"].startswith(fips):
                continue
            body = w.fetch("GET", f"{API}/profile/{code}/{p['geoid']}")
            pending.append((f"{code}/profiles/{p['geoid']}.json", body, "profiles (tract)"))
            used += len(body)
            if mode == "auto" and used > budget:
                w.report.notes.append(
                    f"tract profiles left out: over the {budget_mb:g} MB budget "
                    "(use --tract-budget-mb or --tracts yes to keep them)"
                )
                return None
    for rel, data, kind in pending:
        w.save(rel, data, kind)
    return [(rel, kind) for rel, _data, kind in pending]


def _merge_states(w: _Writer, code: str) -> None:
    """Write ``states.json``: every state exported into this folder, this one included."""
    live = json.loads(w.fetch("GET", f"{API}/states"))["states"]
    entry = next((s for s in live if s["code"] == code), {"code": code, "name": code})
    path = w.out / "states.json"
    kept: list[dict[str, Any]] = []
    if path.is_file():
        try:
            kept = json.loads(path.read_text(encoding="utf-8")).get("states", [])
        except ValueError:
            kept = []
    kept = [
        s
        for s in kept
        if s.get("code") != code and (w.out / s.get("code", "") / "index.json").is_file()
    ]
    states = sorted([*kept, entry], key=lambda s: s["code"])
    w.save("states.json", json.dumps({"states": states}).encode("utf-8"), "states")
