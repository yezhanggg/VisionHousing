"""Draft a municipality's zoning facts from its ordinance, by rule, for a person to check.

Plain-language summary
----------------------
This reader does what a careful person does first with a long ordinance: find
the sections that talk about each topic, then look for the sentence that
answers the question. It uses only keyword lists and text patterns from
``assumptions/law/parser.yaml``; there is no AI step and no guessing.

1. **Rank sections.** For each topic (ADUs, lot sizes, parking, residential
   districts, impact fees) every section scores one point per keyword in its
   text and ``heading_weight`` points per keyword in its heading
   (:func:`rank_sections`).
2. **Pull values.**

   - ADU rule: after the first ADU keyword in the best ADU section, the status
     phrase that appears first near it (by right, conditional use, special exception,
     prohibited) gives the rule; when that section has none (often the
     definitions), the next-best sections are tried, up to
     ``adu_sections_tried``. ADU keywords found but no status phrase leave
     the rule unknown. An ordinance with no ADU keyword at all is
     drafted as ``not_addressed``. Detached and owner-occupancy are read from
     phrases in the same section.
   - Smallest minimum lot: every "minimum lot area ... N square feet" (or acres)
     in a residential section; the smallest wins.
   - Parking: "N spaces per dwelling unit" lines; a line naming apartments or
     multi-family homes is the multi-family minimum, one naming single-family
     homes the single-family minimum.
   - Housing types permitted: the type words found in residential sections.
   - Impact fee ordinance: ``True`` only when the text names a transportation
     impact fee; otherwise unknown (the fee ordinance is usually separate).
3. **Keep the evidence.** Every value carries a quote of at most
   ``quote_chars`` characters, its character offset in the whole text and its
   section number, so a reviewer can find it in seconds.

:func:`draft_rule` turns the facts into a ``muni_zoning_rules`` row with
``method: rule`` and ``status: draft``. It cannot make a verified row: the
schema refuses one that is not a hand reading. :func:`write_review_csv` writes
draft rows with their evidence for review.

:func:`check_source` refuses documents from the commercial code hosts in
``blocked_hosts``: their terms forbid automated use, so only ordinances a
municipality publishes on its own site are read.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from visionhousing.law.schema import Evidence, MuniRule, OrdinanceFacts
from visionhousing.law.text import DocText, SectionText, load_parser_rules, split_sections

__all__ = [
    "REVIEW_COLUMNS",
    "BlockedSourceError",
    "check_source",
    "draft_rule",
    "extract_facts",
    "rank_sections",
    "write_review_csv",
]


class BlockedSourceError(ValueError):
    """The document is hosted where automated reading is not allowed."""


def check_source(url: str, rules: dict[str, Any] | None = None) -> str:
    """Return the host of ``url``, or raise :class:`BlockedSourceError` for a blocked host."""
    rules = rules or load_parser_rules()
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise BlockedSourceError(f"not a web address: {url!r}")
    for blocked in rules["blocked_hosts"]:
        if host == blocked or host.endswith("." + blocked):
            raise BlockedSourceError(
                f"{host} is a commercial code host whose terms forbid automated use; "
                "keep a deep link and read it by hand"
            )
    return host


def _hits(text: str, words: Iterable[str]) -> int:
    low = text.lower()
    return sum(low.count(w.lower()) for w in words)


def rank_sections(
    sections: list[SectionText], topic: str, rules: dict[str, Any] | None = None
) -> list[tuple[int, SectionText]]:
    """Sections that mention ``topic``, best first, with their scores (ties keep text order)."""
    rules = rules or load_parser_rules()
    words = rules["topics"][topic]
    weight = int(rules["heading_weight"])
    scored = []
    for i, s in enumerate(sections):
        score = _hits(s.heading, words) * weight + _hits(s.text, words)
        if score > 0:
            scored.append((score, i, s))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [(score, s) for score, _, s in scored]


def _evidence(text: str, start: int, end: int, section: SectionText, chars: int) -> Evidence:
    """A quote around ``text[start:end]`` (at most ``chars`` long) and its offset."""
    pad = max((chars - (end - start)) // 2, 0)
    lo = max(start - pad, section.start)
    hi = min(max(end + pad, lo + 1), section.end, lo + chars)
    quote = re.sub(r"\s+", " ", text[lo:hi]).strip()[:chars]
    return Evidence(quote=quote, offset=lo, section=section.cite)


def _find_first(low: str, phrases: Iterable[str], start: int, stop: int) -> tuple[int, str] | None:
    best: tuple[int, str] | None = None
    for p in phrases:
        k = low.find(p.lower(), start, stop)
        if k >= 0 and (best is None or k < best[0]):
            best = (k, p)
    return best


def _adu(doc: str, sections: list[SectionText], rules: dict[str, Any], out: dict) -> set[str]:
    """ADU rule, detached, owner occupancy. Returns the fields read from a top-ranked section."""
    chars = int(rules["quote_chars"])
    low = doc.lower()
    ranked = rank_sections(sections, "adu", rules)
    if not ranked:
        out["adu_status"] = "not_addressed"
        return set()
    read: set[str] = set()
    sec = ranked[0][1]
    for rank, (_, cand) in enumerate(ranked[: int(rules["adu_sections_tried"])]):
        kw = _find_first(low, rules["topics"]["adu"], cand.start, cand.end)
        if kw is None:
            continue
        window_start = max(kw[0] - int(rules["adu_window_before"]), cand.start)
        window_end = min(kw[0] + int(rules["adu_window"]), cand.end)
        best: tuple[int, int, str] | None = None
        for status, phrases in rules["adu_status_phrases"].items():
            hit = _find_first(low, phrases, window_start, window_end)
            if hit and (best is None or hit[0] < best[0]):
                best = (hit[0], hit[0] + len(hit[1]), status)
        if best is not None:
            sec = cand
            out["adu_status"] = best[2]
            out["evidence"]["adu_status"] = _evidence(doc, best[0], best[1], sec, chars)
            if rank == 0:
                read.add("adu_status")
            break
    yes = _find_first(low, rules["adu_detached_yes"], sec.start, sec.end)
    no = _find_first(low, rules["adu_detached_no"], sec.start, sec.end)
    if yes or no:
        hit = yes or no
        out["adu_detached"] = yes is not None
        out["evidence"]["adu_detached"] = _evidence(doc, hit[0], hit[0] + len(hit[1]), sec, chars)
        read.add("adu_detached")
    occ = _find_first(low, rules["adu_owner_occupancy"], sec.start, sec.end)
    if occ:
        out["adu_owner_occupancy"] = True
        out["evidence"]["adu_owner_occupancy"] = _evidence(
            doc, occ[0], occ[0] + len(occ[1]), sec, chars
        )
        read.add("adu_owner_occupancy")
    return read


_LOT = re.compile(
    r"minimum\s+lot\s+(?:area|size)[^0-9\n]{0,60}?([\d][\d,]*(?:\.\d+)?)\s*"
    r"(square\s+feet|sq\.?\s*ft\.?|s\.?f\.?|acres?)",
    re.IGNORECASE,
)
_PARKING = re.compile(
    r"(\d+(?:\.\d+)?|one and one-half|one-half|one|two|three|four)\s+"
    r"(?:off-street\s+)?(?:parking\s+)?spaces?\s+(?:shall be required\s+)?(?:per|for each)\s+"
    r"(?:\w+\s+){0,3}?(?:dwelling\s+)?unit",
    re.IGNORECASE,
)
_MF_WORDS = ("multi-family", "multifamily", "apartment", "multiple-family")
_SF_WORDS = ("single-family", "single family", "detached dwelling")


def _district(section: SectionText) -> str:
    m = re.search(r"\b([A-Z]{1,3}-?\d{1,2}[A-Z]?)\b", section.heading)
    return m.group(1) if m else section.heading[:60]


def _lots(doc: str, sections: list[SectionText], rules: dict[str, Any], out: dict) -> set[str]:
    chars = int(rules["quote_chars"])
    acre = float(rules["sqft_per_acre"])
    words = [w.lower() for w in rules["topics"]["residential_district"]]
    best: tuple[float, re.Match[str], SectionText] | None = None
    for s in sections:
        if not any(w in s.text.lower() for w in words):
            continue
        for m in _LOT.finditer(doc, s.start, s.end):
            value = float(m.group(1).replace(",", ""))
            if m.group(2).lower().startswith("acre"):
                value *= acre
            if value > 0 and (best is None or value < best[0]):
                best = (value, m, s)
    if best is None:
        return set()
    value, m, s = best
    out["min_lot_sf_smallest_res_district"] = value
    out["smallest_res_district"] = _district(s)
    out["evidence"]["min_lot_sf_smallest_res_district"] = _evidence(
        doc, m.start(), m.end(), s, chars
    )
    top = rank_sections(sections, "lot", rules)
    return {"min_lot_sf_smallest_res_district"} if top and top[0][1] is s else set()


def _number(text: str, rules: dict[str, Any]) -> float:
    words = {str(k).lower(): float(v) for k, v in rules["number_words"].items()}
    t = text.lower()
    return words[t] if t in words else float(t)


def _parking(doc: str, sections: list[SectionText], rules: dict[str, Any], out: dict) -> set[str]:
    chars = int(rules["quote_chars"])
    ranked = rank_sections(sections, "parking", rules)
    read: set[str] = set()
    for rank, (_, s) in enumerate(ranked):
        for m in _PARKING.finditer(doc, s.start, s.end):
            line_start = doc.rfind("\n", s.start, m.start()) + 1
            line = doc[line_start : m.end()].lower()
            if any(w in line for w in _MF_WORDS):
                field_name = "parking_min_per_unit_mf"
            elif any(w in line for w in _SF_WORDS):
                field_name = "parking_min_per_unit_sf"
            else:
                continue
            if field_name in out:
                continue
            out[field_name] = _number(m.group(1), rules)
            out["evidence"][field_name] = _evidence(doc, line_start, m.end(), s, chars)
            if rank == 0:
                read.add(field_name)
    return read


def _types(doc: str, sections: list[SectionText], rules: dict[str, Any], out: dict) -> None:
    words = [w.lower() for w in rules["topics"]["residential_district"]]
    found: list[str] = []
    text = " ".join(s.text.lower() for s in sections if any(w in s.text.lower() for w in words))
    for name, marks in rules["housing_types"].items():
        if any(m.lower() in text for m in marks):
            found.append(name)
    if found:
        out["permitted_types"] = tuple(found)


def _max_units(sections: list[SectionText], rules: dict[str, Any], out: dict) -> None:
    ranked = rank_sections(sections, "residential_district", rules)
    if not ranked:
        return
    text = ranked[0][1].text.lower()
    if not any(w in text for w in ("permitted use", "uses permitted", "permitted by right")):
        return
    if any(w in text for w in rules["housing_types"]["multifamily"]):
        return
    two = any(w in text for w in rules["housing_types"]["two_family"])
    one = any(w in text for w in rules["housing_types"]["single_family_detached"])
    if two:
        out["max_units_by_right_sf_district"] = 2
    elif one:
        out["max_units_by_right_sf_district"] = 1


def _fees(doc: str, sections: list[SectionText], rules: dict[str, Any], out: dict) -> None:
    low = doc.lower()
    k = low.find("transportation impact fee")
    if k < 0:
        return
    sec = next(s for s in sections if s.start <= k < s.end)
    out["transport_impact_fee_ordinance"] = True
    out["evidence"]["transport_impact_fee_ordinance"] = _evidence(
        doc, k, k + len("transportation impact fee"), sec, int(rules["quote_chars"])
    )


def extract_facts(doc: DocText | str, rules: dict[str, Any] | None = None) -> OrdinanceFacts:
    """Read an ordinance by rule and return what it says (unknown where nothing was found)."""
    rules = rules or load_parser_rules()
    text = doc.text if isinstance(doc, DocText) else doc
    sections = split_sections(text, rules)
    out: dict[str, Any] = {"evidence": {}}
    top: set[str] = set()
    top |= _adu(text, sections, rules, out)
    top |= _lots(text, sections, rules, out)
    top |= _parking(text, sections, rules, out)
    _types(text, sections, rules, out)
    _max_units(sections, rules, out)
    _fees(text, sections, rules, out)
    out["top_ranked"] = tuple(sorted(top))
    return OrdinanceFacts(**out)


def _confidence(facts: OrdinanceFacts, rules: dict[str, Any]) -> str:
    known = [k for k in facts.known if k in facts.evidence]
    if not known:
        return "low"
    top = set(facts.top_ranked)
    share = sum(k in top for k in known) / len(known)
    if share == 1.0:
        return "high"
    return "medium" if share >= float(rules["confidence_medium_share"]) else "low"


def draft_rule(
    facts: OrdinanceFacts,
    *,
    geoid: str,
    doc_id: str,
    dced_id: str = "",
    name: str = "",
    zoning_regime: str = "",
    rules: dict[str, Any] | None = None,
) -> MuniRule:
    """A draft ``muni_zoning_rules`` row from the reader's facts (never verified)."""
    rules = rules or load_parser_rules()
    main = facts.evidence.get("adu_status") or next(iter(facts.evidence.values()), None)
    return MuniRule(
        geoid=geoid,
        dced_id=dced_id,
        name=name,
        zoning_regime=zoning_regime,
        doc_id=doc_id,
        section_cite=main.section if main else "",
        adu_status=facts.adu_status,
        adu_detached=facts.adu_detached,
        adu_owner_occupancy=facts.adu_owner_occupancy,
        min_lot_sf_smallest_res_district=facts.min_lot_sf_smallest_res_district,
        max_units_by_right_sf_district=facts.max_units_by_right_sf_district,
        parking_min_per_unit_sf=facts.parking_min_per_unit_sf,
        parking_min_per_unit_mf=facts.parking_min_per_unit_mf,
        permitted_types=facts.permitted_types,
        transport_impact_fee_ordinance=facts.transport_impact_fee_ordinance,
        evidence_quote=main.quote if main else "",
        evidence_offset=main.offset if main else None,
        method="rule",
        confidence=_confidence(facts, rules),
        status="draft",
    )


REVIEW_COLUMNS = (
    *MuniRule.model_fields,
    "field",
    "field_value",
    "field_quote",
    "field_offset",
    "field_section",
)
"""Columns of the review CSV: the draft row, then one evidence item per line."""


def write_review_csv(items: Iterable[tuple[MuniRule, OrdinanceFacts]], path: Path | str) -> Path:
    """Write draft rows for review: one line per (row, field read), with its evidence.

    A row with no evidence at all still gets one line, so nothing is dropped.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(REVIEW_COLUMNS))
        w.writeheader()
        for row, facts in items:
            base = row.row()
            if not facts.evidence:
                w.writerow({**base, "field": "", "field_value": ""})
            for name, ev in facts.evidence.items():
                value = getattr(facts, name)
                if isinstance(value, tuple):
                    value = ";".join(value)
                w.writerow(
                    {
                        **base,
                        "field": name,
                        "field_value": value,
                        "field_quote": ev.quote,
                        "field_offset": ev.offset,
                        "field_section": ev.section,
                    }
                )
    return path
