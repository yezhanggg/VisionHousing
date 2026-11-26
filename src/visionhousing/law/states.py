"""State law files for any state: provisions, dated records and the already-legal calibration.

Plain-language summary
----------------------
Pennsylvania's state law reaches the pack as a table (``state_law_provisions``,
:mod:`visionhousing.law.state`). Other states keep theirs in plain files:

- ``assumptions/law/states/<st>.yaml``: the state's provisions (citation,
  reform, status, a summary in our own words) and, under ``records``, dated
  statuses in the timeline layout (:mod:`visionhousing.law.timeline`);
- ``assumptions/law/timeline.yaml``: dated records for every state (Vermont's
  Acts are only here);
- ``assumptions/calibration/already_legal_<st>.yaml``: the share of lots where
  a benchmark reform is already legal under state law, with the statute read.

This module only reads those files. :func:`state_records` joins the state
file's records and the shipped timeline's records for one state (a record id
seen twice is kept once, the state file's copy first). :func:`source_for`
picks the source-registry entry a provision is shown under.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from visionhousing.law.timeline import TIMELINE_PATH, TimelineRecord, load_timeline
from visionhousing.paths import safe_child

__all__ = [
    "CALIBRATION_DIR",
    "STATES_DIR",
    "calibration_files",
    "load_state_file",
    "source_for",
    "state_records",
]

ROOT = Path(__file__).resolve().parents[3]
STATES_DIR = ROOT / "assumptions" / "law" / "states"
CALIBRATION_DIR = ROOT / "assumptions" / "calibration"


def load_state_file(state: str, folder: Path = STATES_DIR) -> dict[str, Any] | None:
    """``assumptions/law/states/<st>.yaml`` as a dict, or ``None`` when the state has none."""
    path = safe_child(Path(folder), f"{state.lower()}.yaml")
    if not path.is_file():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def state_records(
    state: str,
    state_fips: str = "",
    *,
    folder: Path = STATES_DIR,
    timeline: Path = TIMELINE_PATH,
) -> list[TimelineRecord]:
    """Every timeline record for one state: its state file's ``records``, then ``timeline.yaml``.

    A record belongs to the state when its jurisdiction is the state's letters,
    its two-digit code, or a county or municipality code that starts with it.
    """
    code = state.upper()
    out: list[TimelineRecord] = []
    seen: set[str] = set()
    paths = [safe_child(Path(folder), f"{code.lower()}.yaml"), Path(timeline)]
    for path in paths:
        if not path.is_file():
            continue
        for r in load_timeline(path):
            j = r.jurisdiction
            mine = j == code or (bool(state_fips) and j.isdigit() and j.startswith(state_fips))
            if mine and r.record_id not in seen:
                seen.add(r.record_id)
                out.append(r)
    return out


def calibration_files(state: str, folder: Path = CALIBRATION_DIR) -> list[dict[str, Any]]:
    """The ``already_legal_<st>.yaml`` calibration tables of a state (empty when none)."""
    path = safe_child(Path(folder), f"already_legal_{state.lower()}.yaml")
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [data] if str(data.get("state", "")).upper() == state.upper() else []


def _prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


def source_for(
    registry: Mapping[str, Any],
    *,
    source_id: str = "",
    urls: Iterable[str] = (),
    fallbacks: Iterable[str] = (),
) -> str:
    """The registry id a provision is shown under.

    In order: its own ``source_id``; the registry entry whose address
    (``url_pattern`` up to its first ``{``) shares the longest start with one
    of the provision's addresses on the same site; the first fallback id (the
    state file's fetched documents, then the calibration's sources) that is in
    the registry. Empty when nothing fits.
    """
    if source_id and source_id in registry:
        return source_id
    best, best_len = "", 0
    for url in urls:
        host = urlparse(url).netloc
        if not host:
            continue
        for sid, entry in registry.items():
            pattern = str(getattr(entry, "url_pattern", "") or "").split("{")[0]
            if urlparse(pattern).netloc != host:
                continue
            n = _prefix(pattern, url)
            if n > best_len:
                best, best_len = sid, n
    if best:
        return best
    return next((f for f in fallbacks if f and f in registry), "")
