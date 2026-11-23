"""Profile section ``data_quality``: how good the state's data are (the state data report card).

Plain-language summary
----------------------
The state data report card (:mod:`visionhousing.data.report_card`) lists every
model input and says whether the state's pack has it, from which sources, of
which edition, and how complete it is. Its **tier** says how widely the data
exist: 0 = national files for every state, 1 = published for many states (for
example transit schedules), 2 = local data only.

This section shows one fact per input that has a source: its tier, with its
status and coverage as text. Inputs the model takes from the Assumption Ledger
(no state data) are named in the limits. The card is for the whole state, so
every fact is measured at the state level, for a county and a tract alike.

The synthetic test state's card names its sources "synthetic"; there each input
cites the source a real pack takes that table from (for example the permit
table cites the Building Permits Survey), and every vintage says "synthetic".
"""

from __future__ import annotations

from visionhousing.data.pack import TABLES
from visionhousing.data.report_card import INPUTS
from visionhousing.profile.context import MissingDataError, ProfileContext
from visionhousing.profile.model import Fact, Section
from visionhousing.profile.sections._common import finish

_TABLE_OF = {i.id: i.table for i in INPUTS}

KEY = "data_quality"
TITLE = "Data quality"


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``data_quality`` section (the same for every place in a state)."""
    ctx.place(geoid)  # check the place exists
    try:
        card = ctx.report_card()
    except FileNotFoundError as err:
        raise MissingDataError(str(err)) from None
    facts: list[Fact] = []
    assumed: list[str] = []
    for row in card["inputs"]:
        sources = [s for s in row["sources"] if s in ctx.registry]
        table = _TABLE_OF.get(row["id"], "")
        if not sources and row["status"] in ("used", "available") and table in TABLES:
            sources = [str(s) for s in TABLES[table]["sources"]][:1]  # type: ignore[index]
        if not sources:
            assumed.append(row["label"])
            continue
        coverage = "" if row["coverage"] is None else f", {row['coverage']:.0%} filled"
        facts.append(
            Fact(
                id=f"{KEY}.{row['id']}",
                label=row["label"],
                value=int(row["tier"]),
                unit="tier",
                text=f"Tier {row['tier']} · {row['status']}{coverage}",
                source=sources[0],
                vintage=ctx.vintage(sources[0]),
                year=str(row["vintage"]),
                measured_at="state",
                note="; ".join(row["limits"][:2]),
            )
        )
    c = card["counts"]
    summary = (
        f"Of {len(card['inputs'])} model inputs for {ctx.state_name}, {c['used']} come from "
        f"state data, {c['available']} are context only, {c['assumed']} are assumed and "
        f"{c['missing']} are missing."
    )
    limits = "The report card describes the whole state's data, not this place alone."
    if ctx.synthetic:
        limits += " This is the synthetic test state: none of its data are real."
    if assumed:
        limits += " Taken from the Assumption Ledger, not state data: " + "; ".join(assumed) + "."
    return finish(KEY, TITLE, summary, facts, limits)
