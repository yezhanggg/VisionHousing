"""The ``visionhousing`` command line.

One entry point for every task: check the machine (``doctor``), fetch data
(``fetch``), build a state data pack (``build``), run reforms (``run``), write a
report (``report``), write a place profile (``profile``), rebuild a pack's transit
stop tables (``transit-stops``), add the transit walkshed tables to a pack
(``transit-walk``), serve the interface
(``serve``) and save a state's default results as files for the static site
(``export-static``). All work today except ``run``, which says what it will do once built.

It uses the standard library's ``argparse`` so it adds no dependency.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from visionhousing import __version__
from visionhousing.export_static import DEFAULT_TRACT_BUDGET_MB

MIN_FREE_GB = 15.0
MIN_PYTHON = (3, 12)
OPTIONAL_KEYS = ("CENSUS_API_KEY",)


@dataclass(frozen=True)
class Check:
    """The result of one doctor check: its name, pass or fail, and a short detail."""

    name: str
    ok: bool
    detail: str
    required: bool = True


def check_python(version: Sequence[int] = sys.version_info) -> Check:
    """Check that Python is 3.12 or newer, because the engine relies on 3.12 features."""
    found = ".".join(str(v) for v in version[:3])
    ok = tuple(version[:2]) >= MIN_PYTHON
    return Check("python", ok, f"{found} (need {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+)")


def check_uv(which: Callable[[str], str | None] = shutil.which) -> Check:
    """Check that the uv package manager can be found, because setup and runs use it."""
    path = os.environ.get("UV") or which("uv")
    return Check("uv", bool(path), "found" if path else "not found: install uv first")


def check_disk(folder: Path, min_gb: float = MIN_FREE_GB) -> Check:
    """Check free disk space, because data downloads stop below the safety margin."""
    free_gb = shutil.disk_usage(folder).free / 1e9
    ok = free_gb >= min_gb
    return Check("disk", ok, f"{free_gb:.1f} GB free (need {min_gb:.0f} GB)")


def check_keys(env: dict[str, str] | None = None) -> list[Check]:
    """Report whether each optional key is set, without ever showing its value."""
    env = dict(os.environ) if env is None else env
    return [
        Check(
            name.lower(),
            True,
            "present" if env.get(name) else "absent (optional; downloads still work)",
            required=False,
        )
        for name in OPTIONAL_KEYS
    ]


def check_write(folder: Path) -> Check:
    """Check that the data folder can be written, because fetched files are saved there."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".doctor-", delete=True) as fh:
            fh.write(b"ok")
        return Check("write access", True, f"can write to {folder.name}/")
    except OSError as err:
        return Check("write access", False, f"cannot write to {folder.name}/: {err.strerror}")


def run_doctor(checks: list[Check]) -> int:
    """Print the checks as a table and return 1 if any required check failed."""
    width = max(len(c.name) for c in checks)
    print(f"{'check':<{width}}  result  detail")
    for c in checks:
        status = "pass" if c.ok else "FAIL"
        if not c.required:
            status = "info"
        print(f"{c.name:<{width}}  {status:<6}  {c.detail}")
    failed = [c for c in checks if c.required and not c.ok]
    print("doctor: all checks passed" if not failed else f"doctor: {len(failed)} check(s) failed")
    return 1 if failed else 0


def doctor(data_dir: Path) -> int:
    """Run every machine check against ``data_dir`` and print the table."""
    checks = [
        check_python(),
        check_uv(),
        check_disk(data_dir if data_dir.exists() else data_dir.parent),
        *check_keys(),
        check_write(data_dir),
    ]
    return run_doctor(checks)


DATA_COMMANDS = {
    "fetch": "Download every source for a state into the cache (only what is missing)",
    "build": "Build the state data pack from the cache and print its size report",
}


def fetch(state: str, raw: Path) -> int:
    """Download what the state pack needs into ``raw`` and list the cached files.

    Each source keeps only the state's rows; large national files are deleted
    after use. Files already in the cache are reused, so a second run is offline.
    """
    from visionhousing.data import pack

    inputs = pack.load_inputs(state, raw)
    total = 0
    print(f"fetch {inputs.state}: cached inputs in {raw}/")
    for source_id, path in inputs.input_files:
        size = path.stat().st_size
        total += size
        print(f"  {source_id:<24} {path.relative_to(raw).as_posix():<52} {size:>12,}")
    print(f"  {'total':<77} {total:>12,}")
    for note in inputs.notes:
        print(f"  note: {note}")
    return 0


def build(state: str, raw: Path, out: Path) -> int:
    """Build ``out/{STATE}/`` from the cache and print the size report."""
    from visionhousing.data import pack

    report = pack.build_pack(state, raw, out)
    print(pack.size_report(report))
    return 0 if report.total_bytes < pack.PACK_BUDGET_BYTES else 1


STUBS: dict[str, tuple[str, str]] = {}
"""Commands listed but not built yet: name to (summary, what it will do)."""


def run_cmd(state: str, reforms: str, *, seed: int, draws: int | None, tier: int, fmt: str) -> int:
    """Run the engine and print each range (text) or the whole result (json).

    ``tier`` masks every data input above it (``assumptions/data_tiers.yaml``):
    2 is full data, 1 national and state sources, 0 national sources only.
    """
    import json

    from visionhousing.engine import run
    from visionhousing.engine.comparisons.ablation import context_at_tier
    from visionhousing.ledger import DEFAULT_DRAWS

    keys = (
        []
        if reforms.strip().lower() in ("", "none")
        else [k.strip() for k in reforms.split(",") if k.strip()]
    )
    try:
        ctx = context_at_tier(state.upper(), tier)
        result = run(ctx.state, keys, seed=seed, draws=draws or DEFAULT_DRAWS, context=ctx)
    except (FileNotFoundError, KeyError, ValueError) as err:
        print(f"run: {err}", file=sys.stderr)
        return 1
    if fmt == "json":
        print(json.dumps({**result.to_dict(), "data_tier": tier}, allow_nan=False))
        return 0

    def line(name: str, r) -> str:
        return f"  {name:<34} P10 {r.p10:>10,.0f}   P50 {r.p50:>10,.0f}   P90 {r.p90:>10,.0f}"

    print(f"{result.state}, data tier {tier}, seed {seed}, {result.draws:,} draws, 2027-2031")
    print(line("baseline (units authorized)", result.baseline))
    for k, out in result.reforms.items():
        print(line(f"{k} (added units)", out.added))
    if len(result.reforms) > 1:
        print(line("package (added units)", result.package.combined))
    return 0


def comparisons_cmd(
    state: str, parts: str, *, seed: int, draws: int | None, out: Path | None, warm: bool = False
) -> int:
    """Run the comparisons and write them as JSON (to ``out`` or the screen).

    With ``warm``, compute every part (``all`` here includes ``market``) and keep
    each in the disk cache (``api.comparison_cache``) instead of printing them.
    """
    import json

    from visionhousing.engine.comparisons import MARKET_PART, PARTS, comparisons
    from visionhousing.ledger import DEFAULT_DRAWS

    if warm:
        from visionhousing.api.comparison_cache import warm as warm_parts

        chosen = (
            [*PARTS, MARKET_PART]
            if parts == "all"
            else [p.strip() for p in parts.split(",") if p.strip()]
        )
        try:
            status = warm_parts(
                state, chosen, seed=seed, draws=draws,
                progress=lambda m: print(f"comparisons --warm: {m}", flush=True),
            )  # fmt: skip
        except (FileNotFoundError, KeyError, ValueError, RuntimeError) as err:
            print(f"comparisons: {err}", file=sys.stderr)
            return 1
        return 0 if all(v != "not kept" for v in status.values()) else 1
    wanted = list(PARTS) if parts == "all" else [p.strip() for p in parts.split(",") if p.strip()]
    try:
        data = comparisons(state.upper(), wanted, seed=seed, draws=draws or DEFAULT_DRAWS)
    except (FileNotFoundError, KeyError, ValueError) as err:
        print(f"comparisons: {err}", file=sys.stderr)
        return 1
    text = json.dumps(data, allow_nan=False, separators=(",", ":"))
    if out is None:
        print(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"comparisons: wrote {out} ({len(text):,} bytes)")
    return 0


def switch_ranges_cmd(state: str, reforms: str, *, seed: int, draws: int | None) -> int:
    """Work out the switch runs of each reform and keep them in the caches.

    One run of the reforms, then for each reform its top three tornado inputs
    fixed at low / central / high with every other input drawn as in the run
    (``sensitivity.switch_runs``). Each reform's answer goes to the disk cache
    beside the comparisons parts, so ``GET /api/v1/switch/{st}`` serves ranges.
    """
    import resource
    import time

    from visionhousing.engine.core import run
    from visionhousing.ledger import DEFAULT_DRAWS
    from visionhousing.sensitivity.switch_runs import switch_runs

    keys = [k.strip() for k in reforms.split(",") if k.strip()]
    start = time.perf_counter()
    try:
        result = run(state.upper(), keys, seed=seed, draws=draws or DEFAULT_DRAWS)
    except (FileNotFoundError, KeyError, ValueError) as err:
        print(f"switch-ranges: {err}", file=sys.stderr)
        return 1
    print(f"switch-ranges: run of {','.join(keys)} in {time.perf_counter() - start:.1f} s",
          flush=True)  # fmt: skip
    for key in keys:
        t = time.perf_counter()
        runs = switch_runs(result, key)
        print(f"switch-ranges: {key}: {len(runs)} inputs x 3 settings in "
              f"{time.perf_counter() - t:.1f} s ({', '.join(runs)})", flush=True)  # fmt: skip
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / 2**20 if sys.platform == "darwin" else peak / 2**10
    print(f"switch-ranges: done in {time.perf_counter() - start:.1f} s; peak memory "
          f"{peak_mb:,.0f} MB")  # fmt: skip
    return 0


DEFAULT_REPORT_REFORMS = "adu,fees,parking"


def report(
    state: str,
    reforms: str,
    fmt: str,
    out: Path | None,
    *,
    seed: int,
    draws: int | None,
    run_date: str | None,
) -> int:
    """Run the engine and write its brief (md, html, json) or CSV tables; list the files.

    ``reforms`` is a comma-separated list of reform keys, or ``none`` for the
    baseline only. The same arguments give byte-identical files; only the run
    date printed in the stamp can differ, and ``--date`` fixes it.
    """
    from visionhousing.engine import run
    from visionhousing.ledger import DEFAULT_DRAWS
    from visionhousing.report import write_report

    keys = (
        []
        if reforms.strip().lower() in ("", "none")
        else [k.strip() for k in reforms.split(",") if k.strip()]
    )
    try:
        result = run(state.upper(), keys, seed=seed, draws=draws or DEFAULT_DRAWS)
    except (FileNotFoundError, KeyError, ValueError) as err:
        print(f"report: {err}", file=sys.stderr)
        return 1
    for path in write_report(result, fmt, out, run_date=run_date):
        print(f"report: wrote {path}")
    return 0


def profile(state: str, county: str | None, tract: str | None, fmt: str, out: Path | None) -> int:
    """Write a place profile (a county, or a tract with ``--tract``) as md, html, csv or json.

    Without ``--out`` the profile is printed. See ``docs/modules/profile.md``.
    """
    from visionhousing.profile import PlaceNotFoundError, build_profile, render

    if not county and not tract:
        print("profile: give --county (5 digits) or --tract (11 digits)", file=sys.stderr)
        return 2
    if county and tract and not tract.startswith(county):
        print(f"profile: tract {tract} is not in county {county}", file=sys.stderr)
        return 2
    geoid = tract or county or ""
    try:
        place = build_profile(state, geoid)
    except FileNotFoundError as err:
        print(f"profile: {err}", file=sys.stderr)
        return 1
    except PlaceNotFoundError:
        print(f"profile: no county or tract {geoid} in {state.upper()}", file=sys.stderr)
        return 1
    text = render(place, fmt)
    if out is None:
        sys.stdout.write(text)
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"profile: wrote {out}")
    return 0


def _run_date(text: str) -> str | None:
    """Check a ``--date`` value: ``YYYY-MM-DD``, ``today`` or ``none``."""
    import datetime as dt

    if text.lower() == "none":
        return None
    if text.lower() == "today":
        return dt.date.today().isoformat()
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError as err:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD, 'today' or 'none'") from err


def serve(host: str, port: int, timeout: float | None = None) -> int:
    """Start the interface (web shell and JSON endpoints) with uvicorn.

    The server banner header is switched off; the app adds its own safety
    headers. ``timeout`` is how long a request waits for a run, in seconds.
    """
    import uvicorn

    from visionhousing.api import create_app

    app = create_app(run_timeout=timeout)
    print(f"serve: VisionHousing at http://{host}:{port}/  (interface: /api/v1/states)")
    uvicorn.run(app, host=host, port=port, server_header=False, log_level="info")
    return 0


def export_static_cmd(
    state: str,
    out: Path,
    *,
    seed: int,
    draws: int | None,
    tracts: str,
    budget: float,
    explain: str = "auto",
    tract_cache: Path | None = None,
    comparisons: bool = True,
    extras: bool = False,
    law_changes: bool = True,
    budget_grid: bool = True,
    alternatives: bool = True,
) -> int:
    """Save the state's default-scenario results as JSON files for the static site.

    See ``visionhousing.export_static``. Returns 1 if the back end refused a
    request or the folder ends up over the static site's size limit.
    """
    from visionhousing.export_static import ExportError, export_static, print_progress

    try:
        rep = export_static(
            state,
            out,
            seed=seed,
            draws=draws,
            tracts=tracts,
            tract_budget_mb=budget,
            explain=explain,
            tract_cache=tract_cache,
            progress=print_progress,
            comparisons=comparisons,
            extras=extras,
            law_changes=law_changes,
            budget_grid=budget_grid,
            alternatives=alternatives,
        )
    except ExportError as err:
        print(f"export-static: {err}", file=sys.stderr)
        return 1
    print(rep.text())
    return 1 if any("over the" in n and "limit" in n for n in rep.notes) else 0


def permit_timing(raw: Path, out: Path, *, refresh: bool = False) -> int:
    """Fetch Philadelphia permit-stage dates and write the timing calibration table."""
    from visionhousing.data.sources.phl_permit_timing import (
        fetch_timing_extracts,
        load_settings,
    )
    from visionhousing.features.permit_timing import build_table, write_table

    settings = load_settings()
    frames = fetch_timing_extracts(raw, settings, refresh=refresh)
    path = write_table(build_table(frames, settings), out)
    print(f"permit-timing: wrote {path}")
    return 0


def tract_crosswalks(state: str, raw: Path, packs: Path) -> int:
    """Add the tract-to-town (and tract-to-place) tables to a built pack (VT, WY, MA)."""
    from visionhousing.data.pack import add_tract_crosswalks

    folder = Path(packs) / state.upper()
    written = add_tract_crosswalks(folder, Path(raw))
    if not written:
        print(f"tract-crosswalks: {state.upper()} needs no crosswalk; nothing written",
              file=sys.stderr)  # fmt: skip
        return 1
    for name, table in written.items():
        print(f"{name}: {len(table):,} tract pieces, {table['tract'].nunique():,} tracts")
    return 0


def ma_3a_tracts(state: str, raw: Path, packs: Path) -> int:
    """Add the Section 3A district-to-tract links to a built Massachusetts pack."""
    from visionhousing.data.pack import add_3a_district_tracts

    folder = Path(packs) / state.upper()
    table = add_3a_district_tracts(folder, Path(raw))
    if table is None:
        print(f"ma-3a-tracts: {folder} has no 3A district outlines", file=sys.stderr)
        return 1
    many = table.groupby("district_id").size()
    print(
        f"mbta_3a_district_tracts: {len(table):,} links, {len(many):,} districts, "
        f"{int((many > 1).sum()):,} reach more than one tract"
    )
    return 0


def transit_stops(state: str, raw: Path, packs: Path) -> int:
    """Rebuild the transit stop and feed tables of the built pack of ``state``; print counts.

    Run ``transit-walk`` afterwards: the walkshed tables read the stop table.
    """
    from visionhousing.data.pack import add_transit_stops

    folder = Path(packs) / state.upper()
    if not (folder / "pack_manifest.json").is_file():
        print(f"transit-stops: {folder} is not a built pack; build it first", file=sys.stderr)
        return 1
    written = add_transit_stops(folder, Path(raw))
    stops, feeds = written["transit_stops"], written["transit_feeds"]
    counted = int((stops["frequency"] == "counted").sum())
    bus = int((stops["mode"] == "bus").sum())
    print(
        f"transit-stops: {len(stops):,} stops, {bus:,} bus stops ({counted:,} counted), "
        f"{int(stops['major'].sum()):,} major; {len(feeds):,} feed rows"
    )
    return 0


def transit_walk(
    state: str,
    raw: Path,
    packs: Path,
    counties: list[str] | None = None,
    stop_report: Path | None = None,
) -> int:
    """Add the transit walkshed tables to the built pack of ``state``; print sizes and coverage."""
    import time

    from visionhousing.data.pack import add_transit_walk

    folder = Path(packs) / state.upper()
    if not (folder / "transit_stops.csv.gz").is_file():
        print(
            f"transit-walk: {folder} has no transit_stops table; build the pack first",
            file=sys.stderr,
        )
        return 1
    start = time.time()
    written = add_transit_walk(folder, Path(raw), counties=counties, stop_report=stop_report)
    tract = written["transit_walk_tract"]
    for name in written:
        path = folder / f"{name}.csv.gz"
        print(f"{name}: {len(written[name]):,} rows, {path.stat().st_size:,} bytes")
    if len(tract):
        net = tract["network_acres_0_50mi"].sum()
        line = tract["straight_acres_0_50mi"].sum()
        print(
            f"1/2 mile: network {net:,.0f} acres / straight line {line:,.0f} acres "
            f"= {net / line:.3f}"
        )
    print(f"transit-walk: done in {time.time() - start:,.0f} s")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one sub-command per task."""
    parser = argparse.ArgumentParser(
        prog="visionhousing",
        description="Estimate how state housing reforms change five-year housing production.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")
    doc = sub.add_parser("doctor", help="Check this machine is ready (Python, uv, disk, access)")
    doc.add_argument("--data-dir", type=Path, default=Path("data"), help="data folder to check")
    for name, summary in DATA_COMMANDS.items():
        p = sub.add_parser(name, help=summary)
        p.add_argument("--state", default="PA", help="two-letter state code (default PA)")
        p.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
        if name == "build":
            p.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    for name, (summary, _) in STUBS.items():
        sub.add_parser(name, help=f"{summary} (not built yet)")
    rn = sub.add_parser("run", help="Run the baseline and the chosen reforms; print the ranges")
    rn.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    rn.add_argument(
        "--reforms", default="adu,missing_middle,tod,parking,fees",
        help="comma-separated reform keys, or 'none' (default: the five benchmarks)",
    )  # fmt: skip
    rn.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
    rn.add_argument("--draws", type=int, default=None, help="Monte Carlo draws (default 2,000)")
    rn.add_argument(
        "--data-tier", type=int, choices=[0, 1, 2], default=2,
        help="mask data above this tier: 2 full, 1 national + state, 0 national only",
    )  # fmt: skip
    rn.add_argument("--format", dest="fmt", choices=["text", "json"], default="text")
    cmp_ = sub.add_parser(
        "comparisons", help="Interactions, policy dials, matrix, data ablation, ranking (JSON)"
    )
    cmp_.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    cmp_.add_argument(
        "--part", default="all",
        help="all, or comma-separated: interactions,dials,matrix,ablation,ranking",
    )  # fmt: skip
    cmp_.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
    cmp_.add_argument("--draws", type=int, default=None, help="Monte Carlo draws (default 2,000)")
    cmp_.add_argument("--out", type=Path, default=None, help="file to write (default: print)")
    cmp_.add_argument(
        "--warm", action="store_true",
        help="compute every part (with market) and keep each in data/cache/comparisons",
    )  # fmt: skip
    swr = sub.add_parser(
        "switch-ranges",
        help="Likely ranges with each reform's top three inputs fixed (kept in data/cache)",
    )
    swr.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    swr.add_argument(
        "--reforms", default="adu,missing_middle,tod,parking,fees",
        help="comma-separated reform keys (default the five benchmark reforms)",
    )  # fmt: skip
    swr.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
    swr.add_argument("--draws", type=int, default=None, help="Monte Carlo draws (default 2,000)")
    rep = sub.add_parser("report", help="Write a run's brief (md, html), CSV tables or JSON")
    rep.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    rep.add_argument(
        "--reforms",
        default=DEFAULT_REPORT_REFORMS,
        help=f"comma-separated reform keys, or 'none' (default {DEFAULT_REPORT_REFORMS})",
    )
    rep.add_argument(
        "--format", dest="fmt", choices=["md", "html", "csv", "json"], default="md",
        help="output format (default md)",
    )  # fmt: skip
    rep.add_argument(
        "--out", type=Path, default=None,
        help="file to write (a folder for csv; default outputs/reports/)",
    )  # fmt: skip
    rep.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
    rep.add_argument("--draws", type=int, default=None, help="Monte Carlo draws (default 2,000)")
    rep.add_argument(
        "--date", dest="run_date", type=_run_date, default="today",
        help="run date in the stamp: YYYY-MM-DD, 'today' (default) or 'none'",
    )  # fmt: skip
    pro = sub.add_parser("profile", help="Write a place profile for a county or tract")
    pro.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    pro.add_argument("--county", default=None, help="county code, 5 digits (for example 42003)")
    pro.add_argument("--tract", default=None, help="tract code, 11 digits (optional)")
    pro.add_argument(
        "--format", dest="fmt", choices=["md", "html", "csv", "json"], default="md",
        help="output format (default md)",
    )  # fmt: skip
    pro.add_argument("--out", type=Path, default=None, help="file to write (default: print)")
    ldr = sub.add_parser(
        "law-draft", help="Draft zoning facts from a municipality's own ordinance PDF for review"
    )
    ldr.add_argument("--pdf", type=Path, required=True, help="the ordinance PDF, downloaded")
    ldr.add_argument("--url", required=True, help="where it was downloaded from (its own site)")
    ldr.add_argument("--geoid", required=True, help="municipality code, 10 digits")
    ldr.add_argument("--doc-id", default="", help="document id (default: the file name)")
    ldr.add_argument(
        "--out", type=Path, default=Path("outputs/law/review.csv"), help="review CSV to write"
    )
    ltb = sub.add_parser("law-tables", help="Add the law and zoning tables to a built state pack")
    ltb.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    ltb.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    ltb.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    zrl = sub.add_parser(
        "zone-rules", help="Add the zoning-district pilot tables (zone_rule, rule_area_weights)"
    )
    zrl.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    zrl.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    zrl.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    lpr = sub.add_parser(
        "law-priorities", help="Rank unread municipal ordinances by the value of reading them next"
    )
    lpr.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    lpr.add_argument("--packs", type=Path, default=Path("data/packs"), help="packs folder")
    lpr.add_argument(
        "--out", type=Path, default=None,
        help="CSV to write (default data/outputs/law_priorities_<STATE>.csv)",
    )  # fmt: skip
    lpr.add_argument("--top", type=int, default=30, help="rows to print (default 30)")
    pt = sub.add_parser(
        "permit-timing", help="Write Philadelphia's permit-stage timing calibration table"
    )
    pt.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    pt.add_argument(
        "--out", type=Path, default=Path("assumptions/calibration/permit_lag_phl.yaml"),
        help="table to write (default assumptions/calibration/permit_lag_phl.yaml)",
    )  # fmt: skip
    pt.add_argument("--refresh", action="store_true", help="read the City's table again")
    tst = sub.add_parser(
        "transit-stops",
        help="Rebuild a pack's transit stop and feed tables and tract transit columns",
    )
    tst.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    tst.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    tst.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    twk = sub.add_parser(
        "transit-walk",
        help="Add the transit walkshed tables (peak bus rule, network walksheds) to a pack",
    )
    twk.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    twk.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    twk.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    twk.add_argument(
        "--county", action="append", default=None,
        help="only this county code (repeatable; writes a partial table, for checks)",
    )  # fmt: skip
    twk.add_argument(
        "--stop-report", type=Path, default=None,
        help="also write each rail stop's own 1/2-mile walkshed to this CSV (checks by hand)",
    )  # fmt: skip
    txw = sub.add_parser(
        "tract-crosswalks",
        help="Add 2020 tract-to-town and tract-to-place tables to a built pack (VT, WY, MA)",
    )
    txw.add_argument("--state", required=True, help="two-letter state code")
    txw.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    txw.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    m3a = sub.add_parser(
        "ma-3a-tracts",
        help="Add the tracts each Massachusetts Section 3A district reaches to a built pack",
    )
    m3a.add_argument("--state", default="MA", help="two-letter state code (default MA)")
    m3a.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    m3a.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    mcap = sub.add_parser(
        "ma-capacity",
        help="Add Massachusetts 3A modeled capacity and MassDEP estimated sewer to a built pack",
    )
    mcap.add_argument("--state", default="MA", help="two-letter state code (default MA)")
    mcap.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    mcap.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    dst = sub.add_parser(
        "districts", help="Add state legislative district weights and outlines to a built pack"
    )
    dst.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    dst.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    dst.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    aff = sub.add_parser(
        "affordability",
        help="Add the cost and affordability tables (CHAS, income limits, assisted homes)",
    )
    aff.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    aff.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    aff.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    inf = sub.add_parser(
        "infrastructure",
        help="Add the water and sewer readiness tables (official service areas) to a pack",
    )
    inf.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    inf.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    inf.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    haz = sub.add_parser(
        "hazards",
        help="Add the hazards and land screen (flood zones, land cover, slope) to a pack",
    )
    haz.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    haz.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    haz.add_argument("--out", type=Path, default=Path("data/packs"), help="packs folder")
    haz.add_argument(
        "--county", action="append", default=None,
        help="only this county code (repeatable; writes a partial table, for checks)",
    )  # fmt: skip
    haz.add_argument("--workers", type=int, default=2, help="counties read at once (default 2)")
    srv = sub.add_parser("serve", help="Serve the interactive tool on this computer")
    srv.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    srv.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    srv.add_argument(
        "--timeout", type=float, default=None, help="seconds a request may wait for a run"
    )
    exp = sub.add_parser(
        "export-static", help="Save a state's default results as JSON for the static site"
    )
    exp.add_argument("--state", default="PA", help="two-letter state code (default PA)")
    exp.add_argument(
        "--out", type=Path, default=Path("web/public/data"),
        help="data folder (default web/public/data)",
    )  # fmt: skip
    exp.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
    exp.add_argument("--draws", type=int, default=None, help="Monte Carlo draws (default 2,000)")
    exp.add_argument(
        "--tracts", choices=["auto", "yes", "compact", "no"], default="auto",
        help="tract profiles: auto saves them whole under the budget, else compact "
        "(default auto)",
    )  # fmt: skip
    exp.add_argument(
        "--tract-budget-mb", type=float, default=DEFAULT_TRACT_BUDGET_MB,
        help="size budget in MB for tract profiles with --tracts auto "
        f"(default {DEFAULT_TRACT_BUDGET_MB:g})",
    )  # fmt: skip
    exp.add_argument(
        "--explain", choices=["auto", "yes", "no"], default="auto",
        help="save each run's explanation; auto drops it only if over the size budget",
    )  # fmt: skip
    exp.add_argument(
        "--comparisons", choices=["yes", "no"], default="yes",
        help="save the comparisons parts (warm them first with 'comparisons --warm'; default yes)",
    )  # fmt: skip
    exp.add_argument(
        "--extras",
        action="store_true",
        help="also save each extra scenario alone (default: the local app shows them)",
    )
    exp.add_argument(
        "--law-changes", choices=["yes", "no"], default="yes",
        help="save 'What this law changes' per reform (read-next bounds only if cached; "
        "default yes)",
    )  # fmt: skip
    exp.add_argument(
        "--budget-grid", choices=["yes", "no"], default="yes",
        help="save the ADU budget-and-target grid if cached (visionhousing adu-budget-grid; "
        "default yes)",
    )  # fmt: skip
    exp.add_argument(
        "--alternatives", choices=["yes", "no"], default="yes",
        help="save the Alternatives table if cached (python -m visionhousing.api.alternatives; "
        "default yes)",
    )  # fmt: skip
    exp.add_argument(
        "--tract-cache", default="data/cache/tract-profiles",
        help="folder that keeps fetched tract profiles so a stopped export resumes "
        "(default data/cache/tract-profiles; 'none' keeps nothing)",
    )  # fmt: skip
    from visionhousing.evidence.commands import add_parser as add_evidence_states

    add_evidence_states(sub)
    from visionhousing.validation.commands import add_parser as add_validate_california

    add_validate_california(sub)
    from visionhousing.validation.commands import add_donor_pool_parser

    add_donor_pool_parser(sub)
    from visionhousing.validation.commands import add_growth_parser

    add_growth_parser(sub)
    from visionhousing.validation.commands import add_state_holdouts_parser

    add_state_holdouts_parser(sub)
    from visionhousing.baseline.calibrate import add_parser as add_baseline_calibration

    add_baseline_calibration(sub)
    from visionhousing.baseline.reliability import add_parser as add_baseline_reliability

    add_baseline_reliability(sub)
    from visionhousing.baseline.card import add_parser as add_baseline_card

    add_baseline_card(sub)
    from visionhousing.report.hearing import add_parser as add_hearing_brief

    add_hearing_brief(sub)
    from visionhousing.validation.national_state import add_parser as add_national_state

    add_national_state(sub)
    from visionhousing.validation.cards import add_parser as add_validation_cards

    add_validation_cards(sub)
    from visionhousing.validation.nested_selection import add_parser as add_nested_selection

    add_nested_selection(sub)
    from visionhousing.engine.correlated import add_parser as add_uncertainty_structure

    add_uncertainty_structure(sub)
    from visionhousing.evidence.retro import add_parser as add_evidence_retro

    add_evidence_retro(sub)
    from visionhousing.intake.commands import add_parser as add_intake

    add_intake(sub)
    from visionhousing.api.budget_grid import add_parser as add_budget_grid

    add_budget_grid(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run the chosen command; return the exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "doctor":
        return doctor(args.data_dir)
    if args.command == "fetch":
        return fetch(args.state, args.raw)
    if args.command == "build":
        return build(args.state, args.raw, args.out)
    if args.command == "serve":
        return serve(args.host, args.port, args.timeout)
    if args.command == "export-static":
        return export_static_cmd(
            args.state,
            args.out,
            seed=args.seed,
            draws=args.draws,
            tracts=args.tracts,
            budget=args.tract_budget_mb,
            explain=args.explain,
            tract_cache=None if args.tract_cache == "none" else Path(args.tract_cache),
            comparisons=args.comparisons == "yes",
            extras=args.extras,
            law_changes=args.law_changes == "yes",
            budget_grid=args.budget_grid == "yes",
            alternatives=args.alternatives == "yes",
        )
    if args.command == "profile":
        return profile(args.state, args.county, args.tract, args.fmt, args.out)
    if args.command == "law-draft":
        from visionhousing.law.commands import law_draft

        return law_draft(args.pdf, args.url, args.geoid, args.out, args.doc_id)
    if args.command == "law-tables":
        from visionhousing.law.commands import law_tables

        return law_tables(args.state, args.raw, args.out)
    if args.command == "zone-rules":
        from visionhousing.law.commands import zone_rules

        return zone_rules(args.state, args.raw, args.out)
    if args.command == "law-priorities":
        from visionhousing.law.commands import law_priorities

        return law_priorities(args.state, args.packs, args.out, args.top)
    if args.command == "evidence-states":
        from visionhousing.evidence.commands import run as evidence_states

        return evidence_states(args)
    if args.command in ("state-readiness", "state-intake", "reproduce"):
        from visionhousing.intake.commands import run as intake_run

        return intake_run(args)
    if args.command == "evidence-retro":
        from visionhousing.evidence.retro import run as evidence_retro

        return evidence_retro(args)
    if args.command == "validate-california":
        from visionhousing.validation.commands import run as validate_california

        return validate_california(args)
    if args.command == "validate-donor-pool":
        from visionhousing.validation.commands import run_donor_pool

        return run_donor_pool(args)
    if args.command == "validate-growth-coverage":
        from visionhousing.validation.commands import run_growth

        return run_growth(args)
    if args.command == "validate-baseline-reliability":
        from visionhousing.baseline.reliability import command as baseline_reliability

        return baseline_reliability(args)
    if args.command == "hearing-brief":
        from visionhousing.report.hearing import command as hearing_brief

        return hearing_brief(args)
    if args.command == "baseline-card":
        from visionhousing.baseline.card import command as baseline_card

        return baseline_card(args)
    if args.command == "validate-cards":
        from visionhousing.validation.cards import command as validation_cards

        return validation_cards(args)
    if args.command == "validate-nested-selection":
        from visionhousing.validation.nested_selection import command as nested_selection

        return nested_selection(args)
    if args.command == "uncertainty-structure":
        from visionhousing.engine.correlated import command as uncertainty_structure

        return uncertainty_structure(args)
    if args.command == "validate-national-state-calibration":
        from visionhousing.validation.national_state import command as national_state

        return national_state(args)
    if args.command == "validate-baseline-calibration":
        from visionhousing.baseline.calibrate import command as baseline_calibration

        return baseline_calibration(args)
    if args.command == "validate-state-holdouts":
        from visionhousing.validation.commands import run_state_holdouts

        return run_state_holdouts(args)
    if args.command == "permit-timing":
        return permit_timing(args.raw, args.out, refresh=args.refresh)
    if args.command == "districts":
        from visionhousing.districts.commands import districts_cmd

        return districts_cmd(args.state, args.raw, args.out)
    if args.command == "infrastructure":
        from visionhousing.features.infrastructure_build import infrastructure_cmd

        return infrastructure_cmd(args.state, args.raw, args.out)
    if args.command == "hazards":
        from visionhousing.features.hazards_build import hazards_cmd

        return hazards_cmd(args.state, args.raw, args.out, args.county, args.workers)
    if args.command == "tract-crosswalks":
        return tract_crosswalks(args.state, args.raw, args.out)
    if args.command == "ma-3a-tracts":
        return ma_3a_tracts(args.state, args.raw, args.out)
    if args.command == "ma-capacity":
        from visionhousing.features.ma_capacity_build import ma_capacity_cmd

        return ma_capacity_cmd(args.state, args.raw, args.out)
    if args.command == "transit-stops":
        return transit_stops(args.state, args.raw, args.out)
    if args.command == "transit-walk":
        return transit_walk(args.state, args.raw, args.out, args.county, args.stop_report)
    if args.command == "affordability":
        from visionhousing.features.affordability import affordability_cmd

        return affordability_cmd(args.state, args.raw, args.out)
    if args.command == "run":
        return run_cmd(
            args.state, args.reforms, seed=args.seed, draws=args.draws, tier=args.data_tier,
            fmt=args.fmt,
        )  # fmt: skip
    if args.command == "adu-budget-grid":
        from visionhousing.api.budget_grid import command as budget_grid_command

        return budget_grid_command(args)
    if args.command == "comparisons":
        return comparisons_cmd(args.state, args.part, seed=args.seed, draws=args.draws,
                               out=args.out, warm=args.warm)  # fmt: skip
    if args.command == "switch-ranges":
        return switch_ranges_cmd(args.state, args.reforms, seed=args.seed, draws=args.draws)
    if args.command == "report":
        return report(
            args.state,
            args.reforms,
            args.fmt,
            args.out,
            seed=args.seed,
            draws=args.draws,
            run_date=args.run_date,
        )
    _summary, plan = STUBS[args.command]
    state = getattr(args, "state", None)
    where = f" for {state}" if state else ""
    print(f"{args.command}{where}: not built yet. When built, this command {plan}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
