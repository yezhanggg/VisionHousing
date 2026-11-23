"""Read ``assumptions/limits.yaml``: what each estimate cannot tell you.

Plain-language summary
----------------------
Each reform has a short list of plain statements of what the estimate leaves
out, kept in ``assumptions/limits.yaml`` so writers can change them without
touching code. An entry may ``extend`` another (the Pennsylvania bills are ADU
reforms, so they repeat the ADU lines first). The file must hold no digits:
every number a reader sees comes from the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from visionhousing.ledger.loader import ROOT

__all__ = ["DEFAULT_PATH", "Limits", "LimitsError", "limits_for", "load_limits"]

DEFAULT_PATH = ROOT / "assumptions" / "limits.yaml"


class LimitsError(ValueError):
    """The limits file is malformed; the message names the entry."""


@dataclass(frozen=True)
class Limits:
    """The limits of one estimate.

    Attributes:
        key: Reform key (or ``general``, ``baseline``, ``package``).
        title: Heading.
        items: The statements, inherited ones first.
    """

    key: str
    title: str
    items: tuple[str, ...]


def _parse(data: object) -> dict[str, Limits]:
    if not isinstance(data, dict) or not isinstance(data.get("limits"), dict):
        raise LimitsError("the file needs a top-level 'limits' mapping")
    raw = data["limits"]
    out: dict[str, Limits] = {}

    def build(key: str, seen: tuple[str, ...]) -> Limits:
        if key in out:
            return out[key]
        if key in seen:
            raise LimitsError(f"'{key}': 'extends' goes round in a circle")
        entry = raw.get(key)
        if not isinstance(entry, dict):
            raise LimitsError(f"'{key}': missing or not a mapping")
        unknown = set(entry) - {"title", "items", "extends"}
        if unknown:
            raise LimitsError(f"'{key}': unknown fields {sorted(unknown)}")
        title, items = entry.get("title"), entry.get("items")
        if not isinstance(title, str) or not title.strip():
            raise LimitsError(f"'{key}': needs a title")
        if not isinstance(items, list) or not all(isinstance(i, str) and i for i in items):
            raise LimitsError(f"'{key}': items must be a list of sentences")
        for text in (title, *items):
            if any(ch.isdigit() for ch in text):
                raise LimitsError(f"'{key}': no figures allowed, found one in {text!r}")
        inherited: tuple[str, ...] = ()
        if "extends" in entry:
            inherited = build(str(entry["extends"]), (*seen, key)).items
        out[key] = Limits(key=key, title=title.strip(), items=(*inherited, *items))
        return out[key]

    for key in raw:
        build(str(key), ())
    return out


@lru_cache(maxsize=4)
def _load(path: str, mtime: int) -> dict[str, Limits]:
    with open(path, encoding="utf-8") as fh:
        return _parse(yaml.safe_load(fh))


def load_limits(path: Path = DEFAULT_PATH) -> dict[str, Limits]:
    """Load and check the limits file.

    Args:
        path: File to read.

    Returns:
        Key to its limits, with inherited lines included.

    Raises:
        LimitsError: A malformed entry, a cycle in ``extends``, or a digit.
    """
    return _load(str(path), path.stat().st_mtime_ns)


def limits_for(key: str, path: Path = DEFAULT_PATH) -> Limits:
    """The limits of one reform; an empty entry if the file has none for it."""
    return load_limits(path).get(key, Limits(key=key, title=key, items=()))
