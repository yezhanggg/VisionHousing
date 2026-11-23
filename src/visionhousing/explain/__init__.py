"""Plain-language explanations of every result, built only from engine numbers.

Builds the one-sentence summary and three numbers, a verdict word per reform,
each reform's funnel with what every step means, the top drivers from the
sensitivity tornado, the package waterfall, what each estimate cannot tell
you, the "explain this number" payload for every record, and the slide deck
spec. Nothing here calls a model or types a number by hand (decision D-12):
every number in every sentence carries a cite to where it came from, and the
tests check each one. See ``docs/modules/explain.md``.
"""

from visionhousing.explain.ids import record_index, resolve
from visionhousing.explain.limits import Limits, LimitsError, limits_for, load_limits
from visionhousing.explain.numbers import Cite, Text, num, numbers_in, round_sig, show, uncited
from visionhousing.explain.payload import (
    DISCLAIMER,
    VERDICT_WORDS,
    cited_texts,
    explain,
    explain_number,
    funnel,
    headline,
    top_drivers,
    verdict_word,
    verdicts,
    waterfall,
)
from visionhousing.explain.slides import slides

__all__ = [
    "DISCLAIMER",
    "VERDICT_WORDS",
    "Cite",
    "Limits",
    "LimitsError",
    "Text",
    "cited_texts",
    "explain",
    "explain_number",
    "funnel",
    "headline",
    "limits_for",
    "load_limits",
    "num",
    "numbers_in",
    "record_index",
    "resolve",
    "round_sig",
    "show",
    "slides",
    "top_drivers",
    "uncited",
    "verdict_word",
    "verdicts",
    "waterfall",
]
