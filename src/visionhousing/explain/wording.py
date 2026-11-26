"""Fixed words: state names, what each funnel step means, list joining, tier labels.

Plain-language summary
----------------------
The explanation layer writes sentences from engine numbers. The words around
those numbers live here so they can be read and improved in one place. Nothing
here is a number the model uses; text in this module contains no figures (a
test checks the step meanings).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

__all__ = [
    "REFORM_PHRASES",
    "STATE_NAMES",
    "STEP_MEANINGS",
    "TIER_LABELS",
    "join_words",
    "reform_phrase",
    "state_name",
    "step_label",
    "step_meaning",
    "step_scope",
]

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "the District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "TL": "Testland",
}  # fmt: skip
"""Two-letter code to the name used in sentences (``TL`` is the synthetic test state)."""

TIER_LABELS = {
    0: "Tier 0: national public data only",
    1: "Tier 1: adds statewide layers",
    2: "Tier 2: adds local parcel or zoning data",
}
"""Data tier to its plain label (see the glossary)."""

# Funnel and step names as the reform modules write them (the part before any
# bracket), to one plain sentence on what the step means. Matched by prefix.
STEP_MEANINGS: tuple[tuple[str, str], ...] = (
    # ADU reforms
    (
        "eligible lots",
        "Lots the rule reaches: existing homes of the building types in scope, counted as lots.",
    ),
    (
        "lots with room for an ADU",
        "Of those, lots with enough yard for a backyard home of the required size.",
    ),
    (
        "lots where an ADU is feasible",
        "Of those, lots where rent would pay for building one, or where the owner would "
        "build one for family anyway.",
    ),
    (
        "ADUs authorized",
        "Of those, the ones expected to get a building permit over the five years, with a "
        "slow start while owners learn about the new right.",
    ),
    # Missing middle
    (
        "eligible single-family lots",
        "Single-family lots in zones that allow only one home today.",
    ),
    (
        "lots large enough",
        "Of those, lots big enough for a small apartment building or townhouses.",
    ),
    (
        "lots where redevelopment pencils",
        "Of those, lots where a new small building would be worth more than the house "
        "and the cost of building it.",
    ),
    ("lots redeveloped", "Of those, the ones expected to be rebuilt over the five years."),
    ("new units authorized in", "Homes in the new buildings on those lots, each one a permit."),
    # Transit-oriented density
    ("land within", "Land near a major transit stop, within the chosen walking distance."),
    ("land that could become", "Of that land, the share that could realistically be rebuilt."),
    (
        "land where a mid-rise building pencils",
        "Of that, land where a mid-rise building would be worth more than the homes on it.",
    ),
    (
        "added homes the upzoning allows",
        "Homes the extra height or floor area allows there, beyond what would be permitted "
        "on those sites anyway.",
    ),
    (
        "mid-rise homes permitted",
        "Of those, the ones expected to get a permit over the five years, within what the "
        "region's apartment market can absorb.",
    ),
    # Shared headline and net steps
    (
        "added units authorized",
        "Permits the reform adds: what is left after taking out permits that would have "
        "been issued anyway, on the same sites or elsewhere. The headline.",
    ),
    (
        "net added homes after demolition",
        "The change in the number of homes: permits added, minus the existing homes torn "
        "down that would not have been torn down anyway.",
    ),
    (
        "net added units",
        "What is left after taking out homes that would have been built anyway: the "
        "reform's own addition.",
    ),
    # Fee and parking reforms
    ("fee saving per unit", "How much the fee cut saves a builder on each new home."),
    (
        "baseline units",
        "Homes the baseline expects to be permitted in the building types this reform reaches.",
    ),
    (
        "value per new unit",
        "What a typical new home is worth, so the saving can be compared with it.",
    ),
    (
        "saving as a share of value",
        "The saving as a share of a new home's value: the size of the push to build.",
    ),
    (
        "Medium-run supply response",
        "How strongly builders respond when building becomes more profitable.",
    ),
    (
        "Share of a fee cut",
        "How much of the fee cut reaches the decision to build, rather than land prices.",
    ),
    (
        "Share of a cost saving",
        "How much of the saving reaches the decision to build, rather than land prices.",
    ),
    ("added units from the fee cut", "Extra homes from the fee cut, at central values."),
    ("transit-rich tracts", "Neighbourhoods with frequent transit nearby."),
    (
        "spaces required per unit",
        "Parking spaces the rules require for each new home today.",
    ),
    (
        "spaces still built per unit",
        "Parking spaces builders would still choose to build without a rule.",
    ),
    ("spaces avoided per unit", "Spaces no longer built for each new home."),
    ("cost per space avoided", "What each space not built would have cost."),
    ("parking saving per unit", "The saving on each new home from building fewer spaces."),
    (
        "added units from the parking cost saving",
        "Extra homes because each home costs less to build, at central values.",
    ),
    (
        "added units from site yield",
        "Extra homes because land once kept for parking can hold homes, at central values.",
    ),
    ("added units from parking reform", "Both parking effects together, at central values."),
)

_SCOPE = re.compile(r"\s*\(([^()]*)\)\s*$")


def state_name(code: str) -> str:
    """The name of a state for sentences (``PA`` -> ``Pennsylvania``)."""
    return STATE_NAMES.get(code.upper(), code.upper())


def step_label(name: str) -> str:
    """A step's name without its bracketed scope.

    ``eligible lots (adu: sf_detached)`` -> ``eligible lots``.
    """
    return _SCOPE.sub("", name).strip()


def step_scope(name: str) -> str:
    """The bracketed scope of a step name, or ``""``."""
    m = _SCOPE.search(name)
    return m.group(1) if m else ""


def step_meaning(name: str) -> str:
    """Plain words for what a funnel step means; a pointer to the formula if unknown."""
    label = step_label(name)
    for prefix, meaning in STEP_MEANINGS:
        if label.startswith(prefix):
            return meaning
    return "See the formula for how this step is worked out."


def join_words(items: Sequence[str]) -> str:
    """``["a", "b", "c"]`` -> ``"a, b and c"``."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


REFORM_PHRASES = {
    "adu": "accessory dwelling units (ADUs) allowed",
    "missing_middle": "two to six homes allowed on single-family lots",
    "tod": "more homes allowed near major transit stops",
    "parking": "parking minimums removed",
    "fees": "development fees cut in half",
}
"""Registry key to the phrase that follows "with" in the one-sentence summary."""


def reform_phrase(key: str, title: str) -> str:
    """A reform as it reads after "with" in a sentence.

    ``adu`` -> ``accessory dwelling units (ADUs) allowed``; a reform without a
    phrase uses its title up to any colon, followed by "passed" for a bill
    (``Pennsylvania HB 2186 passed``) or in lower case otherwise.
    """
    if key in REFORM_PHRASES:
        return REFORM_PHRASES[key]
    head = title.split(":", 1)[0].strip() or title
    if re.search(r"\b(HB|SB)\s*\d", head):
        return f"{head} passed"
    first = head.split(" ", 1)[0]
    if len(first) > 1 and first[1:].islower():
        return head[0].lower() + head[1:]
    return head


ADU_FAMILY = frozenset({"adu", "pa_hb2186", "pa_sb1346"})
"""Reforms whose added homes are accessory dwelling units."""

ADU_MEASUREMENT = "of which some may not appear in permit statistics"
"""Said wherever ADU homes added are shown: permit statistics do not count ADUs
consistently (some towns permit them as alterations, not as new homes)."""

FREQUENCY_LEAD = "In eight of ten simulated futures"
"""Frequency wording of a P10-P90 range: eight in ten draws fall between P10 and P90."""


def added_label(keys: Sequence[str] | str, base: str = "Homes added") -> str:
    """The label for homes added; ADU reforms carry the permit-statistics caveat.

    ``added_label("adu")`` -> ``Homes added, of which some may not appear in
    permit statistics``; a package with an ADU reform says the ADUs among them may not.
    """
    keys = [keys] if isinstance(keys, str) else list(keys)
    adu = [k for k in keys if k in ADU_FAMILY]
    if not adu:
        return base
    if len(keys) == 1:
        return f"{base}, {ADU_MEASUREMENT}"
    return f"{base}, of which some ADUs may not appear in permit statistics"
