"""Named variants of the benchmark reforms (``assumptions/variants.yaml``).

Plain-language summary
----------------------
The five benchmark presets stay exactly as the Challenge Handbook defines
them. A **variant** is a named alternative run of one reform that answers one
question, for example "what if builders keep every parking space?" or "what if
homeowners can borrow less?". A variant changes only two things:

- **Ledger swaps.** A target ledger entry takes the range of another ledger
  entry (``overrides: {target: source}``). Every number stays in
  ``assumptions/parameters.yaml`` with its id, range, kind, source and
  rationale. The target keeps its own random stream, so a variant run is
  paired with the benchmark run draw by draw.
- **Engine options.** For example the parking response rule or the TOD walk
  distance (``options``).

A reform is asked for a variant with the option ``{"variant": "<name>"}``.
The adapter merges the variant's options under the caller's own options (the
caller wins) and applies the ledger swaps after the scenario's overrides and
before the user's overrides, so a user can still move any entry.

Which ledger a variant run sees, in order: base ledger, the scenario's
overrides, the variant's swaps, the user's overrides, package changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from visionhousing.ledger import Ledger, LedgerError
from visionhousing.ledger.loader import ROOT, read_yaml

__all__ = [
    "OPTION_VARIANT",
    "VARIANTS_PATH",
    "Variant",
    "expand_options",
    "load_variants",
    "parse_variants",
    "tod_decomposition",
    "variant_overrides",
    "variants_for",
]

VARIANTS_PATH = ROOT / "assumptions" / "variants.yaml"
"""The variant file."""

OPTION_VARIANT = "variant"
"""Reform option that names a variant."""

FAMILIES = (
    "adu",
    "missing_middle",
    "parking",
    "fees",
    "tod",
    "transit_zoning_funded_infrastructure",
    "commercial_land_housing",
    "adaptive_reuse",
    "starter_home_lots",
    "fee_deferral_with_affordability",
)
"""Reform families a variant may belong to."""

COPIED_FIELDS = ("central", "low", "high", "shape", "kind", "source", "status", "revisit")
"""Fields a target entry takes from its source entry."""


@dataclass(frozen=True)
class Variant:
    """One named variant.

    Attributes:
        name: Variant id (the key in the file).
        reform: Reform family it belongs to.
        title: Plain name.
        question: The question it answers.
        overrides: Target ledger id to source ledger id.
        options: Engine options it sets.
    """

    name: str
    reform: str
    title: str
    question: str
    overrides: Mapping[str, str] = field(default_factory=dict)
    options: Mapping[str, Any] = field(default_factory=dict)


def parse_variants(data: Any, ledger: Ledger | None = None) -> dict[str, Variant]:
    """Check parsed variant data and build the variants.

    Args:
        data: The parsed YAML.
        ledger: When given, every target and source id must be in it.

    Returns:
        Variant name to variant.

    Raises:
        LedgerError: A field is missing, a family is unknown or an id is not in the ledger.
    """
    if not isinstance(data, dict) or not isinstance(data.get("variants"), dict):
        raise LedgerError("variants.yaml: expected a 'variants' mapping")
    out: dict[str, Variant] = {}
    problems: list[str] = []
    for name, raw in data["variants"].items():
        if not isinstance(raw, dict):
            problems.append(f"{name}: not a mapping")
            continue
        reform = str(raw.get("reform", ""))
        if reform not in FAMILIES:
            problems.append(f"{name}: reform must be one of {FAMILIES}")
        for key in ("title", "question"):
            if not str(raw.get(key, "")).strip():
                problems.append(f"{name}: needs a {key}")
        overrides = {str(k): str(v) for k, v in (raw.get("overrides") or {}).items()}
        if ledger is not None:
            for target, source in overrides.items():
                for pid in (target, source):
                    if pid not in ledger:
                        problems.append(f"{name}: '{pid}' is not in the ledger")
        options = dict(raw.get("options") or {})
        if OPTION_VARIANT in options:
            problems.append(f"{name}: a variant cannot name another variant")
        if not overrides and not options:
            problems.append(f"{name}: changes nothing (no overrides and no options)")
        out[str(name)] = Variant(
            name=str(name),
            reform=reform,
            title=" ".join(str(raw.get("title", "")).split()),
            question=" ".join(str(raw.get("question", "")).split()),
            overrides=overrides,
            options=options,
        )
    if problems:
        raise LedgerError("variants.yaml: " + " | ".join(problems))
    return out


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Variant]:
    return parse_variants(read_yaml(Path(path)))


def load_variants(path: Path | str | None = None) -> dict[str, Variant]:
    """Load ``assumptions/variants.yaml`` (or another file); empty when the file is absent."""
    p = Path(path) if path is not None else VARIANTS_PATH
    if not p.is_file():
        return {}
    return dict(_load(str(p)))


def variants_for(family: str, path: Path | str | None = None) -> dict[str, Variant]:
    """The variants of one reform family, by name."""
    return {k: v for k, v in load_variants(path).items() if v.reform == family}


def _variant(name: str, family: str) -> Variant:
    found = load_variants()
    if name not in found:
        raise KeyError(f"unknown variant '{name}'; known: {sorted(found)}")
    v = found[name]
    if v.reform != family:
        raise ValueError(f"variant '{name}' belongs to reform '{v.reform}', not '{family}'")
    return v


def expand_options(family: str, options: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a named variant's options under the caller's options.

    Args:
        family: The reform family running.
        options: The reform options; ``variant`` names the variant (optional).

    Returns:
        The options to use (the caller's own keys win over the variant's).

    Raises:
        KeyError: Unknown variant.
        ValueError: The variant belongs to another reform family.
    """
    name = options.get(OPTION_VARIANT)
    if not name:
        return dict(options)
    v = _variant(str(name), family)
    return {**dict(v.options), **dict(options)}


def variant_overrides(family: str, options: Mapping[str, Any], ledger: Ledger) -> dict[str, Any]:
    """The ledger changes a named variant makes (target takes the source's range).

    Args:
        family: The reform family running.
        options: The reform options (``variant`` names the variant, optional).
        ledger: The ledger the reform sees so far.

    Returns:
        Target id to the fields to replace; empty when no variant is named.
    """
    name = options.get(OPTION_VARIANT)
    if not name:
        return {}
    v = _variant(str(name), family)
    out: dict[str, Any] = {}
    for target, source in v.overrides.items():
        src = ledger[source]
        entry = {f: getattr(src, f) for f in COPIED_FIELDS}
        entry["source"] = [s.model_dump(exclude_none=True) for s in src.source]
        entry["rationale"] = f"Variant '{v.name}': takes the range of {source}. {src.rationale}"
        if not entry["revisit"]:
            entry["revisit"] = src.revisit or "See the source entry."
        out[target] = entry
    return out


DECOMPOSITION_ID = "variants.decomposition.tod"
"""Id of the TOD decomposition settings in ``assumptions/variants.yaml``."""


def tod_decomposition(path: Path | str | None = None) -> dict[str, Any]:
    """The TOD zero-decomposition settings: the low-rise variant and the wider distance.

    Read from ``assumptions/variants.yaml`` (``decomposition.tod``).

    Returns:
        ``{"id", "lowrise_variant", "wider_distance"}``.

    Raises:
        LedgerError: The block is missing, or names a variant that is not a TOD variant.
    """
    p = Path(path) if path is not None else VARIANTS_PATH
    data = read_yaml(p) if p.is_file() else {}
    raw = ((data or {}).get("decomposition") or {}).get("tod")
    if not isinstance(raw, dict) or "lowrise_variant" not in raw or "wider_distance" not in raw:
        raise LedgerError(
            "variants.yaml: decomposition.tod needs lowrise_variant and wider_distance"
        )
    name = str(raw["lowrise_variant"])
    found = parse_variants(data)
    if name not in found or found[name].reform != "tod":
        raise LedgerError(f"variants.yaml: decomposition.tod: '{name}' is not a TOD variant")
    return {
        "id": str(raw.get("id", DECOMPOSITION_ID)),
        "lowrise_variant": name,
        "wider_distance": float(raw["wider_distance"]),
    }
