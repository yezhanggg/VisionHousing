"""The ``visionhousing validate-california`` command.

Plain-language summary
----------------------
Downloads (once) California's APR Tables A and A2 and the Census lot counts,
reads them in chunks, and writes three generated files:
``assumptions/evidence/california_apr.yaml``,
``assumptions/calibration/apr_timing_donor_ca.yaml`` and
``assumptions/calibration/adu_rate_plausibility.yaml``. The two tables are
processed one after the other, so only one large file is being read at a time.

Also ``visionhousing validate-donor-pool``: builds the national county panel of
the Building Permits Survey and runs the hold-out-state test of borrowed
baseline errors (:mod:`visionhousing.validation.donor_pool`), writing
``assumptions/calibration/donor_pool_errors.yaml``.

Also ``visionhousing validate-growth-coverage``: coverage of the baseline's state
range by growth class, and the pre-registered growth-aware widening test
(:mod:`visionhousing.validation.growth_coverage`), writing
``assumptions/calibration/growth_coverage.yaml``.

Also ``visionhousing validate-state-holdouts``: the pre-registered state
confidence check, held-out coverage of the current baseline range for named
states (:mod:`visionhousing.validation.state_holdouts`), writing
``assumptions/calibration/state_holdouts.yaml``.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

__all__ = [
    "add_donor_pool_parser",
    "add_growth_parser",
    "add_parser",
    "add_state_holdouts_parser",
    "run",
    "run_donor_pool",
    "run_growth",
    "run_state_holdouts",
]

OUT_EVIDENCE = Path("assumptions/evidence/california_apr.yaml")
OUT_DONOR = Path("assumptions/calibration/apr_timing_donor_ca.yaml")
OUT_PLAUSIBILITY = Path("assumptions/calibration/adu_rate_plausibility.yaml")


def add_parser(sub: argparse._SubParsersAction) -> None:
    """Register the command and its options."""
    p = sub.add_parser(
        "validate-california",
        help="California APR study: stage attrition, timing, ADU rates, plausibility table",
    )
    p.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    p.add_argument("--refresh", action="store_true", help="download the APR tables again")
    p.add_argument("--evidence", type=Path, default=OUT_EVIDENCE, help="evidence file to write")
    p.add_argument("--donor", type=Path, default=OUT_DONOR, help="donor-range file to write")
    p.add_argument(
        "--plausibility", type=Path, default=OUT_PLAUSIBILITY, help="plausibility table to write"
    )


def run(args: argparse.Namespace) -> int:
    """Fetch, compute and write; prints the files written."""
    from visionhousing.validation import apr_data
    from visionhousing.validation.california import (
        HEADERS,
        build_documents,
        pa_adu_rate,
        write_yaml,
    )

    s = apr_data.load_settings()
    paths = apr_data.fetch_apr(args.raw, s, refresh=args.refresh)
    acs = apr_data.fetch_lots(args.raw, s, refresh=args.refresh)
    lots = apr_data.parse_place_lots(acs["table"], acs["geos"], s["acs_lots_cell"])
    a = apr_data.read_table_a(paths["table_a"], s)
    a2 = apr_data.read_table_a2(paths["table_a2"], s)
    as_of = date.fromtimestamp(paths["table_a2"].stat().st_mtime).isoformat()
    docs = build_documents(a, a2, lots, s, as_of=as_of, plausibility=pa_adu_rate(s))
    for key, out in (
        ("evidence", args.evidence),
        ("donor", args.donor),
        ("plausibility", args.plausibility),
    ):
        if docs[key] is not None:
            print(f"validate-california: wrote {write_yaml(docs[key], out, HEADERS[key])}")
    return 0


# --------------------------------------------------------------------------- donor pool


def add_donor_pool_parser(sub: argparse._SubParsersAction) -> None:
    """Register ``validate-donor-pool``: the hold-out-state test of borrowed baseline errors."""
    p = sub.add_parser(
        "validate-donor-pool",
        help="hold-out-state back-test: donor-pool vs Pennsylvania errors for sparse states",
    )
    p.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    p.add_argument("--refresh", action="store_true", help="download the county files again")
    p.add_argument(
        "--out",
        type=Path,
        default=Path("assumptions/calibration/donor_pool_errors.yaml"),
        help="results file to write",
    )


def run_donor_pool(args: argparse.Namespace) -> int:
    """Build the national county panel, run the study, write the results; prints the outcome."""
    from visionhousing.validation import donor_pool as dp

    s = dp.load_settings()
    paths = dp.fetch_national(
        args.raw, range(s["first_year"], s["last_year"] + 1), refresh=args.refresh
    )
    cache = Path(args.raw) / "bps_national" / dp.PANEL_FILE
    if args.refresh and cache.exists():
        cache.unlink()
    panel = dp.build_national_panel(paths, s, cache)
    rule, bsettings = dp.rule_and_settings()
    as_of = date.fromtimestamp(max(p.stat().st_mtime for p in paths)).isoformat()
    doc = dp.run_study(panel, s, bsettings, rule, as_of=as_of)
    print(f"validate-donor-pool: wrote {dp.write_document(doc, args.out)}")
    print(
        f"validate-donor-pool: decision {doc['decision']['outcome']}: {doc['decision']['reason']}"
    )
    return 0


# --------------------------------------------------------------------------- growth coverage


def add_growth_parser(sub: argparse._SubParsersAction) -> None:
    """Register ``validate-growth-coverage``: coverage by growth class and the widening test."""
    p = sub.add_parser(
        "validate-growth-coverage",
        help="coverage of the baseline's state range by growth class; growth-aware widening test",
    )
    p.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    p.add_argument(
        "--out",
        type=Path,
        default=Path("assumptions/calibration/growth_coverage.yaml"),
        help="results file to write",
    )


def run_growth(args: argparse.Namespace) -> int:
    """Build (or reuse) the national county panel, run the study, write the results."""
    from visionhousing.validation import donor_pool as dp
    from visionhousing.validation import growth_coverage as gc

    s = dp.load_settings()
    paths = dp.fetch_national(args.raw, range(s["first_year"], s["last_year"] + 1))
    panel = dp.build_national_panel(paths, s, Path(args.raw) / "bps_national" / dp.PANEL_FILE)
    rule, bsettings = dp.rule_and_settings()
    as_of = date.fromtimestamp(max(p.stat().st_mtime for p in paths)).isoformat()
    doc = gc.run_growth_study(panel, s, bsettings, rule, as_of=as_of)
    print(f"validate-growth-coverage: wrote {gc.write_document(doc, args.out)}")
    d = doc["decision"]
    print(f"validate-growth-coverage: decision {d['outcome']}: {d['reason']}")
    return 0


# --------------------------------------------------------------------------- state hold-outs


def add_state_holdouts_parser(sub: argparse._SubParsersAction) -> None:
    """Register ``validate-state-holdouts``: held-out coverage of the current range by state."""
    p = sub.add_parser(
        "validate-state-holdouts",
        help="state confidence check: out-of-time coverage of the current baseline range",
    )
    p.add_argument("--raw", type=Path, default=Path("data/raw"), help="download cache folder")
    p.add_argument(
        "--out",
        type=Path,
        default=Path("assumptions/calibration/state_holdouts.yaml"),
        help="results file to write",
    )


def run_state_holdouts(args: argparse.Namespace) -> int:
    """Build (or reuse) the national county panel, score the listed states, write the results."""
    from visionhousing.validation import donor_pool as dp
    from visionhousing.validation import state_holdouts as sh

    s = dp.load_settings()
    paths = dp.fetch_national(args.raw, range(s["first_year"], s["last_year"] + 1))
    panel = dp.build_national_panel(paths, s, Path(args.raw) / "bps_national" / dp.PANEL_FILE)
    rule, bsettings = dp.rule_and_settings()
    as_of = date.fromtimestamp(max(p.stat().st_mtime for p in paths)).isoformat()
    doc = sh.run_check(panel, s, sh.load_check(), bsettings, rule, as_of=as_of)
    print(f"validate-state-holdouts: wrote {sh.write_document(doc, args.out)}")
    for st, row in doc["states"].items():
        cov = row.get("coverage")
        text = "no case scored" if cov is None else f"{cov:.1%} of {row['cases']} cases"
        print(f"validate-state-holdouts: {st} {text}")
    return 0
